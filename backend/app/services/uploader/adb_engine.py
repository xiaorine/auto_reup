import logging
import os
import subprocess
import time
import random
import re
from datetime import datetime
from typing import Dict, Any
from .base_engine import BaseUploaderEngine, TaskAbortedByUser
from .adb_automator import ADBAutomator

logger = logging.getLogger(__name__)



class ADBUploader(BaseUploaderEngine):
    """
    Engine upload sử dụng ADB (Android Debug Bridge).
    Chuyên trị: Các App nội địa Trung Quốc như Douyin, Xiaohongshu hoặc TikTok Mobile.
    Cần cấu hình: IP của máy ảo/điện thoại, port (VD: 192.168.1.10:5555).
    """
    
    def __init__(self, account_data: Dict[str, Any], schedule_id: int = None):
        super().__init__(account_data, schedule_id=schedule_id)
        # Auth data có thể chứa "adb_ip" thay vì cookie, nhưng ưu tiên device_id nếu có
        self.adb_ip = self.account_data.get("device_id")
        if not self.adb_ip:
            self.adb_ip = self.account_data.get("auth_data")
        
        self.adb_ip = self.adb_ip.strip() if self.adb_ip else ""

    def _smart_sleep(self, seconds: float):
        """
        Replacement for time.sleep() that polls Redis for pause/stop signals.
        Checks every 0.5s so the task can react quickly to user commands.
        """
        if not self.schedule_id:
            time.sleep(seconds)
            return
        
        try:
            from app.core.redis_pool import get_sync_redis
            r = get_sync_redis(decode_responses=True)
            redis_key = f"task_control:{self.schedule_id}"
        except Exception:
            # If Redis is unavailable, fall back to normal sleep
            time.sleep(seconds)
            return
        
        elapsed = 0.0
        interval = 0.5
        while elapsed < seconds:
            # Check control signal
            signal = r.get(redis_key)
            
            if signal == "stop":
                logger.warning(f"[ADB] Nhận tín hiệu STOP từ người dùng cho schedule #{self.schedule_id}!")
                # Clean up: send device back to home screen
                self._run_adb_cmd(["shell", "input", "keyevent", "3"])
                raise TaskAbortedByUser(f"Bị hủy bởi người dùng (schedule_id={self.schedule_id})")
            
            if signal == "pause":
                logger.info(f"[ADB] Nhận tín hiệu PAUSE cho schedule #{self.schedule_id}. Đang chờ Resume...")
                while True:
                    time.sleep(1)
                    signal = r.get(redis_key)
                    if signal == "stop":
                        logger.warning(f"[ADB] Chuyển từ PAUSE sang STOP cho schedule #{self.schedule_id}!")
                        self._run_adb_cmd(["shell", "input", "keyevent", "3"])
                        raise TaskAbortedByUser(f"Bị hủy bởi người dùng (schedule_id={self.schedule_id})")
                    if signal != "pause":
                        logger.info(f"[ADB] Đã RESUME cho schedule #{self.schedule_id}. Tiếp tục tiến trình...")
                        break
            
            time.sleep(interval)
            elapsed += interval

    def _run_adb_cmd(self, args: list, timeout: int = 60) -> str:
        """Chạy lệnh adb tới thiết bị cụ thể"""
        cmd = ["adb", "-s", self.adb_ip] + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace')
            if result.returncode != 0:
                logger.error(f"[ADB] Lỗi khi chạy lệnh {cmd}: {result.stderr}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.error(f"[ADB] Timeout khi chạy lệnh {cmd}")
            return ""
        except Exception as e:
            logger.error(f"[ADB] Lỗi không xác định: {e}")
            return ""

    def connect(self) -> bool:
        """Kết nối tới máy ảo Android hoặc điện thoại qua mạng"""
        if not self.adb_ip:
            logger.error("[ADB] Lỗi: Chưa cấu hình Device ID (adb_ip) cho tài khoản này.")
            return False

        # Nếu thiết bị là cổng local USB thì thường auth_data là mã thiết bị (vd: emulator-5554)
        if ":" in self.adb_ip:
            logger.info(f"[ADB] Đang connect tới {self.adb_ip}...")
            subprocess.run(["adb", "connect", self.adb_ip], capture_output=True)
            
        # Kiểm tra trạng thái
        devices = subprocess.run(["adb", "devices"], capture_output=True, text=True, encoding='utf-8', errors='replace').stdout
        for line in devices.splitlines():
            if self.adb_ip in line:
                if "device" in line and "offline" not in line and "unauthorized" not in line:
                    return True
                elif "unauthorized" in line:
                    logger.error(f"[ADB] Thiết bị {self.adb_ip} đang ở trạng thái 'unauthorized'. Vui lòng mở khóa điện thoại và chọn 'Allow USB debugging'.")
                    return False
                elif "offline" in line:
                    logger.error(f"[ADB] Thiết bị {self.adb_ip} đang ở trạng thái 'offline'.")
                    return False
        
        logger.error(f"[ADB] Không tìm thấy thiết bị {self.adb_ip} trong danh sách adb devices.")
        return False

    def upload(self, video_path: str, caption: str, hashtags: str) -> str:
        logger.info(f"[ADB] Bắt đầu đẩy video sang điện thoại {self.adb_ip}: {video_path}")
        
        if not self.connect():
            raise Exception(f"Không thể kết nối ADB tới thiết bị: {self.adb_ip}")
            
        if not os.path.exists(video_path):
            raise Exception(f"File video không tồn tại: {video_path}")

        # 1. Dọn dẹp các video reup cũ trên điện thoại để tránh đầy bộ nhớ và chọn nhầm
        self._run_adb_cmd(["shell", "rm", "-f", "/sdcard/DCIM/Camera/Camerep_*.mp4"])
        
        # 1.5 Cập nhật Metadata Creation Time của Video thành hiện tại để đảm bảo Tiktok xếp nó lên đầu tiên
        logger.info("[ADB] Cập nhật Metadata Creation Time cho video...")
        new_video_path = video_path.replace(".mp4", f"_{int(time.time())}.mp4")
        from datetime import timezone
        current_time_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", video_path, 
                "-c", "copy", 
                "-metadata", f"creation_time={current_time_iso}", 
                new_video_path
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            video_path = new_video_path
        except Exception as e:
            logger.warning(f"[ADB] Không thể cập nhật metadata bằng ffmpeg, bỏ qua: {e}")
            
        # 2. Push video sang bộ nhớ điện thoại (Thư mục Camera để Tiktok dễ nhận diện nhất)
        self._run_adb_cmd(["shell", "mkdir", "-p", "/sdcard/DCIM/Camera"])
        remote_path = f"/sdcard/DCIM/Camera/Camerep_{int(time.time())}.mp4"
        logger.info(f"[ADB] Pushing video to {remote_path}...")
        self._run_adb_cmd(["push", video_path, remote_path], timeout=120)
        
        # Cập nhật timestamp của file thành hiện tại để luôn đứng đầu thư viện
        self._run_adb_cmd(["shell", "touch", remote_path])
        
        # 2. Bắn Broadcast để hệ điều hành quét lại thư viện media (để app thấy video)
        logger.info("[ADB] Cập nhật thư viện Media...")
        self._run_adb_cmd(["shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote_path}"])
        self._smart_sleep(5) # Đợi lâu hơn một chút để Android kịp index video mới

        # Phân loại nền tảng
        platform = self.account_data.get("platform", "douyin").lower()
        full_caption = f"{caption} {hashtags}"
        
        # Khởi tạo Automator
        automator = ADBAutomator(self.adb_ip)
        automator.check_adb_keyboard()
        
        try:
            if platform == "douyin":
                return self._upload_douyin(remote_path, full_caption, automator)
            elif platform == "tiktok":
                return self._upload_tiktok_mobile(remote_path, full_caption, automator)
            else:
                raise Exception(f"Nền tảng App {platform} chưa được hỗ trợ qua ADB.")
        finally:
            logger.info("[ADB] Dọn dẹp: Đóng hoàn toàn các App sau quá trình upload...")
            self._run_adb_cmd(["shell", "am", "force-stop", "com.zhiliaoapp.musically"])
            self._run_adb_cmd(["shell", "am", "force-stop", "com.ss.android.ugc.trill"])
            self._run_adb_cmd(["shell", "am", "force-stop", "com.ss.android.ugc.aweme"])

    def _upload_douyin(self, remote_video_path: str, post_caption: str, automator: ADBAutomator) -> str:
        logger.info("[ADB] Khởi động Douyin App...")
        # Ép buộc đóng app cũ để xóa cache thư viện
        self._run_adb_cmd(["shell", "am", "force-stop", "com.ss.android.ugc.aweme"])
        self._smart_sleep(2)
        # Lệnh mở Douyin (Package: com.ss.android.ugc.aweme)
        self._run_adb_cmd(["shell", "monkey", "-p", "com.ss.android.ugc.aweme", "-c", "android.intent.category.LAUNCHER", "1"])
        
        logger.info("[ADB] Đang chờ Douyin tải trang chủ...")
        app_opened = False
        for i in range(6): # Chờ tối đa 30 giây (6 * 5s)
            self._smart_sleep(5)
            # Kiểm tra xem trang chủ đã load chưa (có chữ Home, Tải lên, Hồ sơ...)
            if automator.find_element(texts=["首页", "朋友", "消息", "我", "Home", "Profile"]):
                app_opened = True
                break
        
        if not app_opened:
            raise Exception("Lỗi: Douyin tải quá chậm hoặc bị treo, không thể vào trang chủ!")
        
        logger.info("[ADB] Douyin đã mở thành công.")
        
        # 1. Bấm nút + (Tạo mới)
        if not automator.click_element(texts=["发布", "拍摄", "相册"], wait=3):
            automator.click_percentage(0.5, 0.92)
            self._smart_sleep(3)
            
        automator.handle_permission_popups()
            
        # 2. Bấm nút Tải lên (Upload)
        logger.info("[ADB] Bấm nút Tải lên (Upload) Douyin...")
        if not automator.click_element(texts=["相册", "上传", "上传视频"], content_descs=["相册", "上传", "上传视频"], wait=3):
            logger.info("[ADB] Không tìm thấy nút Tải lên bằng chữ hoặc mô tả, dùng thuật toán dò mìn (Phải -> Trái)...")
            automator.click_dynamic_bottom_right()
            
            # Douyin check tab Video
            if not automator.find_element(texts=["Video", "Videos", "视频"]):
                logger.info("[ADB] Bấm bên phải không ra Thư viện. Hủy thao tác và thử bấm bên Trái...")
                self._run_adb_cmd(["shell", "input", "keyevent", "4"]) # Bấm Back
                self._smart_sleep(2)
                automator.click_dynamic_bottom_left()
            
        automator.handle_permission_popups()
            
        # 3. Chuyển sang tab Video (để chắc chắn không chọn nhầm ảnh)
        if automator.click_element(texts_contains=["Video", "Videos", "视频"], content_descs=["Video", "Videos", "视频"], wait=2):
            logger.info("[ADB] Đã chuyển sang tab Video.")
        else:
            logger.info("[ADB] Không tìm thấy tab Video, tiếp tục lấy file đầu tiên...")
            
        # 4. Chọn video đầu tiên
        automator.click_percentage(0.25, 0.3)
        self._smart_sleep(2)
        
        # 4. Bấm Tiếp / Next
        automator.click_element(texts=["下一步", "完成"], wait=4)
        
        # 5. Bấm Tiếp / Next (Màn hình edit)
        automator.click_element(texts=["下一步"], wait=3)
        
        # 7. Gõ caption
        logger.info("[ADB] Nhập caption Unicode qua ADBKeyboard (Base64)...")
        if not post_caption.endswith(" "):
            post_caption += " "
            
        if not automator.click_element(
            texts_contains=["添加描述", "分享你的"], 
            classes=["android.widget.EditText"],
            wait=2
        ):
            automator.click_percentage(0.3, 0.2)
        self._smart_sleep(1.5)
        
        # Nhập text qua Base64 để hỗ trợ 100% Unicode (tiếng Trung, tiếng Việt, emoji, xuống dòng)
        automator.input_text(post_caption)
        self._smart_sleep(3)
        
        # Bấm Nút Back (Trở về) của Android để đảm bảo mọi Popup, bảng gợi ý hashtag và Bàn phím đều bị thu gọn
        logger.info("[ADB] Gửi lệnh Back để đóng toàn bộ Popup và thoát focus...")
        self._run_adb_cmd(["shell", "input", "keyevent", "4"])
        self._smart_sleep(2)
        
        # 8. Bấm Đăng / Post
        automator.click_element(texts=["发布", "日常"], wait=5)
        
        # 8. Xử lý popup xác nhận nếu có
        automator.click_element(texts=["立即发布", "确认", "发布"], retries=1, wait=3)
        
        logger.info("[ADB] Upload Douyin App hoàn tất!")
        return "https://www.douyin.com/"
        
    def _get_tiktok_package(self) -> str:
        """Tự động phát hiện phiên bản TikTok đang cài trên máy"""
        output = self._run_adb_cmd(["shell", "pm", "list", "packages"])
        if output:
            if "package:com.ss.android.ugc.trill" in output:
                return "com.ss.android.ugc.trill" # TikTok Asia
            if "package:com.zhiliaoapp.musically" in output:
                return "com.zhiliaoapp.musically" # TikTok Global
        return "com.zhiliaoapp.musically" # Default fallback
        
    def _upload_tiktok_mobile(self, remote_video_path: str, post_caption: str, automator: ADBAutomator) -> str:
        logger.info("[ADB] Khởi động Tiktok Mobile App...")
        
        # Tự động dò tìm Package name
        tiktok_pkg = self._get_tiktok_package()
        logger.info(f"[ADB] Đã phát hiện phiên bản TikTok: {tiktok_pkg}")
        
        # Ép buộc đóng app cũ để xóa cache thư viện
        self._run_adb_cmd(["shell", "am", "force-stop", tiktok_pkg])
        self._smart_sleep(2)
        
        # Lệnh mở Tiktok
        self._run_adb_cmd(["shell", "monkey", "-p", tiktok_pkg, "-c", "android.intent.category.LAUNCHER", "1"])
        
        logger.info("[ADB] Đang chờ Tiktok tải trang chủ...")
        app_opened = automator.wait_for_app_foreground([tiktok_pkg], timeout=60)
        
        if not app_opened:
            raise Exception("Lỗi: Tiktok tải quá chậm hoặc bị treo, không thể vào trang chủ!")
        
        # Process đã lên Foreground, nhưng UI bên trong có thể chưa load xong
        # Chờ thêm 8s rồi xác nhận giao diện thật sự đã hiển thị
        logger.info("[ADB] TikTok đã lên Foreground, đang chờ giao diện tải xong...")
        self._smart_sleep(5)
        
        # Xác nhận giao diện trang chủ đã render (có thanh menu dưới cùng)
        ui_ready = False
        for attempt in range(5):
            if automator.find_element(
                texts=["Hồ sơ", "Profile", "Hộp thư", "Inbox", "Trang chủ", "Home", "Đề xuất", "For You"],
                content_descs=["Hồ sơ", "Profile", "Hộp thư", "Inbox"]
            ):
                ui_ready = True
                break
            logger.info(f"[ADB] Giao diện TikTok chưa sẵn sàng, chờ thêm... (lần {attempt + 1}/5)")
            self._smart_sleep(3)
        
        if not ui_ready:
            logger.warning("[ADB] Không xác nhận được giao diện TikTok, tiếp tục thận trọng...")
            self._smart_sleep(5)  # Chờ thêm 5s fallback
            
        logger.info("[ADB] Tiktok đã mở thành công.")
        
        # Helper: Lướt video và tìm trang chủ an toàn (Không Live, không Quảng cáo)
        def _swipe_to_safe_home():
            swipes = random.randint(1, 2)
            logger.info(f"[ADB] Lướt {swipes} video để tăng độ trust trước khi đăng...")
            
            # Tính toán kích thước màn hình
            res_wm = subprocess.run(f"adb -s {self.adb_ip} shell wm size", shell=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
            width, height = 720, 1280
            if res_wm.stdout:
                try:
                    # Ưu tiên tìm "Override size" trước vì đây là độ phân giải đang hiển thị thật
                    if "Override size:" in res_wm.stdout:
                        match = re.search(r"Override size: (\d+)x(\d+)", res_wm.stdout)
                    else:
                        # Nếu không có Override thì lấy "Physical size"
                        match = re.search(r"Physical size: (\d+)x(\d+)", res_wm.stdout)
                        
                    if match:
                        width = int(match.group(1))
                        height = int(match.group(2))
                        logger.info(f"[ADB] Kích thước màn hình: {width}x{height}")
                except Exception as e:
                    logger.error(f"Lỗi khi lấy kích thước màn hình: {e}")

            # Tiếp tục tính toán tọa độ
            start_x, start_y = int(width * 0.5), int(height * 0.8)
            end_x, end_y = int(width * 0.5), int(height * 0.2)
            
            # Lướt 1-2 video ban đầu
            for _ in range(swipes):
                subprocess.run(["adb", "-s", self.adb_ip, "shell", "input", "swipe", str(start_x), str(start_y), str(end_x), str(end_y), "300"])
                self._smart_sleep(random.randint(3, 5))
                
                # Thả tim ngẫu nhiên
                if random.random() < 0.15:
                    logger.info("[ADB] Thả tim ngẫu nhiên...")
                    if automator.click_element(content_descs=['Thích', 'Like']):
                        self._smart_sleep(1)
                
                # Yêu thích ngẫu nhiên
                if random.random() < 0.15:
                    logger.info("[ADB] Lưu yêu thích ngẫu nhiên...")
                    if automator.click_element(content_descs=['Thêm vào mục Yêu thích', 'Add to Favorites', 'Lưu', 'Save']):
                        self._smart_sleep(1)
                
            # Đảm bảo đang ở trang chủ (có thanh menu dưới cùng) và không kẹt ở Live
            max_retries = 5
            for _ in range(max_retries):
                if automator.find_element(
                    texts=["Hồ sơ", "Profile", "Hộp thư", "Inbox", "Trang chủ", "Home"],
                    content_descs=["Hồ sơ", "Profile", "Hộp thư", "Inbox", "Trang chủ", "Home"]
                ):
                    logger.info("[ADB] Đã xác nhận trang chủ an toàn, sẵn sàng đăng bài.")
                    return True
                logger.info("[ADB] Đang ở Live hoặc Quảng cáo che mất thanh menu, lướt tiếp...")
                subprocess.run(["adb", "-s", self.adb_ip, "shell", "input", "swipe", str(start_x), str(start_y), str(end_x), str(end_y), "300"])
                self._smart_sleep(4)
                
            return False

        _swipe_to_safe_home()
        
        # 1. Bấm nút dừng video (tránh video chuyển động chèn mất nút +)
        logger.info("[ADB] Bấm nút dừng video...")
        automator.click_percentage(0.5, 0.5)
        self._smart_sleep(1.5)

        logger.info("[ADB] Bấm nút + (Tạo mới) ở giữa cạnh dưới màn hình (Dùng XML để định vị các nút xung quanh)...")
        w, h = automator.get_screen_size()
        segment = int(w / 5)
        clicked = False
        
        # Lấy XML một lần để tối ưu tốc độ
        root = automator.dump_ui()
        if root is not None:
            # 1. Thử tìm nút + bằng mô tả trực tiếp
            create_node = None
            for node in root.iter('node'):
                desc = node.get('content-desc', '').lower()
                if desc in ["tạo", "create", "post", "camera"]:
                    create_node = node
                    break
            
            if create_node and create_node.get('bounds') != '[0,0][0,0]':
                coords = automator._get_center_from_bounds(create_node.get('bounds'))
                if coords:
                    logger.info(f"[ADB] Đã tìm thấy nút + bằng mô tả XML tại {coords}.")
                    automator._run_adb(["shell", "input", "tap", str(coords[0]), str(coords[1])])
                    clicked = True
            
            # 2. Nếu không tìm thấy mô tả, tìm các nút neo xung quanh (Hộp thư, Cửa hàng...)
            if not clicked:
                anchor_coords = None
                shift = 0
                for node in root.iter('node'):
                    node_text = node.get('text', '').lower()
                    bounds = node.get('bounds', '')
                    if not bounds or bounds == '[0,0][0,0]':
                        continue
                    
                    if node_text in ["Hộp thư", "inbox", "tin nhắn", "message", "消息"]:
                        anchor_coords = automator._get_center_from_bounds(bounds)
                        shift = -segment
                        logger.info(f"[ADB] Tìm thấy nút Hộp thư, dịch trái {segment}px")
                        break
                    elif node_text in ["Cửa hàng", "shop",]:
                        anchor_coords = automator._get_center_from_bounds(bounds)
                        shift = segment
                        logger.info(f"[ADB] Tìm thấy nút Cửa hàng, dịch phải {segment}px")
                        break
                    elif node_text in ["hồ sơ", "profile", "tôi", "me", "我"]:
                        anchor_coords = automator._get_center_from_bounds(bounds)
                        shift = -(segment * 2)
                        logger.info(f"[ADB] Tìm thấy nút Hồ sơ, dịch trái {segment * 2}px")
                        break
                    elif node_text in ["trang chủ", "home", "首页"]:
                        anchor_coords = automator._get_center_from_bounds(bounds)
                        shift = segment * 2
                        logger.info(f"[ADB] Tìm thấy nút Trang chủ, dịch phải {segment * 2}px")
                        break
                
                if anchor_coords:
                    target_x = int(anchor_coords[0] + shift)
                    target_y = anchor_coords[1]
                    automator._run_adb(["shell", "input", "tap", str(target_x), str(target_y)])
                    clicked = True

        self._smart_sleep(3)
        
        # Cách 2 (Dự phòng): Tính tọa độ động (Height - 80px)
        if not clicked:
            logger.info("[ADB] Cách 1 (XML) không tìm thấy tọa độ nút +, dùng cách 2: tính tọa độ động...")
            automator._run_adb(["shell", "input", "tap", str(int(w/2)), str(h - 80)])
            self._smart_sleep(3)
            
        automator.handle_permission_popups()
        
        # Thêm độ dừng để camera có thể load mở lên được
        logger.info("[ADB] Chờ camera load...")
        self._smart_sleep(8)
            
        # 2. Bấm nút Tải lên (Upload)
        logger.info("[ADB] Bấm nút Tải lên (Upload)...")
        if not automator.click_element(
            texts=["Tải lên", "Upload", "Album", "Gallery"], 
            content_descs=["Tải lên", "Upload", "Album", "Gallery", "Thư viện"], 
            resource_ids=["com.ss.android.ugc.trill:id/upload_btn", "com.zhiliaoapp.musically:id/upload_btn", "com.ss.android.ugc.trill:id/upload_hot_area", "com.zhiliaoapp.musically:id/upload_hot_area"],
            wait=3
        ):
            logger.info("[ADB] Không tìm thấy nút Tải lên bằng chữ hoặc mô tả, dùng thuật toán dò mìn...")
            
            # Lấy tọa độ nút Quay/Đăng (Nút trắng to nhất) bằng OpenCV hoặc XML
            x_record, y_record = automator.get_dynamic_pos()
            logger.info(f"[ADB] Tọa độ tâm nút Quay (trắng to nhất): X={x_record:.3f}, Y={y_record:.3f}")
            
            # Tìm Y của thanh dưới cùng (nút Đăng/Tạo)
            w_scr, h_scr = automator.get_screen_size()
            bottom_btn = automator.find_element(texts=["Đăng", "Tạo", "Live", "Trực tiếp", "Post", "Create", "Story"])
            if bottom_btn:
                y_bottom_bar = bottom_btn[1] / h_scr
                logger.info(f"[ADB] Đã tìm thấy nút Đăng/Tạo bằng XML tại Y={y_bottom_bar:.3f}")
            else:
                y_bottom_bar = (h_scr - 80) / h_scr
                logger.info(f"[ADB] Không thấy nút Đăng/Tạo qua XML, dùng mặc định Y={y_bottom_bar:.3f}")
            
            found_gallery = False
            
            # Ưu tiên dò mìn bên TRÁI trước (2%, 4%, 6%) (Cùng hàng với nút Đăng/Tạo - thanh dưới cùng)
            for pct in [0.02, 0.04, 0.06]:
                logger.info(f"[ADB] Đang dò nút Thư viện ở mép TRÁI (X={pct*100}%, Y={y_bottom_bar:.2f})...")
                automator.click_percentage(pct, y_bottom_bar)
                self._smart_sleep(2)
                if automator.find_element(texts=["Video", "Videos", "视频"]):
                    logger.info(f"[ADB] Đã vào được Thư viện thành công tại mép TRÁI (X = {pct*100}%)")
                    found_gallery = True
                    break
                else:
                    logger.info(f"[ADB] Bấm mép TRÁI X = {pct*100}% không thành công, thử nhích thêm...")
            
            # Nếu bên trái xịt, chuyển sang dò bên PHẢI bằng cách dịch phải chiều rộng / 4 từ nút Quay
            if not found_gallery:
                logger.info("[ADB] Bấm bên trái không ra Thư viện. Hủy thao tác và thử bấm bên PHẢI (Cùng hàng nút Quay)...")
                self._run_adb_cmd(["shell", "input", "keyevent", "4"]) # Bấm Back để đóng panel vừa mở nhầm
                self._smart_sleep(2)
                
                right_x = x_record + 0.25
                logger.info(f"[ADB] Đang dò nút Thư viện ở mép PHẢI (X={right_x*100:.1f}%, Y={y_record:.2f})...")
                automator.click_percentage(right_x, y_record)
                self._smart_sleep(2)
                if automator.find_element(texts=["Video", "Videos", "视频"]):
                    logger.info(f"[ADB] Đã vào được Thư viện thành công tại mép PHẢI (X = {right_x*100:.1f}%)")
            
        automator.handle_permission_popups()
            
        # 3. Chuyển sang tab Video (để chắc chắn không chọn nhầm ảnh)
        if automator.click_element(texts_contains=["Video", "Videos", "视频"], content_descs=["Video", "Videos", "视频"], wait=2):
            logger.info("[ADB] Đã chuyển sang tab Video.")
        else:
            logger.info("[ADB] Không tìm thấy tab Video, tiếp tục lấy file đầu tiên...")
            
        # 4. Chọn video đầu tiên (video mới nhất vừa tải vào máy)
        logger.info("[ADB] Tìm điểm neo 'Tất cả' hoặc 'Gần đây' để chọn video đầu tiên...")
        anchor = automator.find_element(texts_contains=["Tất cả", "All", "Gần đây", "Recent"])
        if anchor:
            ax, ay = anchor
            w, h = automator.get_screen_size()
            target_x = int(w * 0.22) # Cột đầu tiên (khoảng 22% chiều rộng)
            target_y = ay + int(h * 0.12) # Dịch xuống dưới chữ "Tất cả" khoảng 12% màn hình để trúng tâm thumbnail
            logger.info(f"[ADB] Đã tìm thấy điểm neo tại Y={ay}. Bấm video đầu tiên tại ({target_x}, {target_y})")
            automator._run_adb(["shell", "input", "tap", str(target_x), str(target_y)])
        else:
            logger.info("[ADB] Không tìm thấy điểm neo, dùng vị trí dự phòng (0.25, 0.3)...")
            automator.click_percentage(0.25, 0.3)
            
        self._smart_sleep(2)
        
        # 4. Bấm Tiếp / Next (Màn hình chọn video)
        logger.info("[ADB] Bấm Tiếp (Màn hình chọn video)")
        if not automator.click_element(texts_contains=["Tiếp", "Next", "Continue"], wait=4):
            automator.click_percentage(0.85, 0.95)
            self._smart_sleep(2)
        
        # 5. Bấm Tiếp / Next (Màn hình edit video)
        logger.info("[ADB] Bấm Tiếp (Màn hình chỉnh sửa video)")
        if not automator.click_element(texts_contains=["Tiếp", "Next", "Continue"], wait=3):
            automator.click_percentage(0.85, 0.95)
            self._smart_sleep(2)
        
        # 7. Gõ caption
        logger.info("[ADB] Nhập caption Unicode qua ADBKeyboard (Base64)...")
        if not post_caption.endswith(" "):
            post_caption += " "
            
        if not automator.click_element(
            texts_contains=[
                "Mô tả", "Thêm mô tả", "Add description", "Describe your post",
                "nói gì về video", "Viết", "nội dung", "Write"
            ],
            classes=["android.widget.EditText"],
            wait=2
        ):
            automator.click_percentage(0.3, 0.2)
        self._smart_sleep(1.5)
        
        # Nhập text qua Base64 để hỗ trợ 100% Unicode (tiếng Việt có dấu, emoji, xuống dòng, ký tự đặc biệt)
        automator.input_text(post_caption)
        self._smart_sleep(3)
        
        # Lời khuyên của user: Thay vì bấm mù vào 1 vị trí cố định, hãy tìm element "Đăng" hoặc "Chia sẻ với",
        # sau đó bấm dịch xuống dưới 1 đoạn để thoát focus, hệ thống sẽ chắc chắn hơn.
        logger.info("[ADB] Tìm điểm neo để thu gọn bàn phím và bảng hashtag...")
        anchor = automator.find_element(texts=["Đăng", "Post"], texts_contains=["Chia sẻ với", "Share to", "Tự động chia sẻ", "Share"])
        
        if anchor:
            ax, ay = anchor
            logger.info(f"[ADB] Đã tìm thấy điểm neo tại ({ax}, {ay}). Chạm dịch xuống (Y+80) để thoát focus an toàn...")
            automator._run_adb(["shell", "input", "tap", str(ax), str(ay + 80)])
        else:
            logger.info("[ADB] Không thấy điểm neo, dùng vị trí dự phòng (0.5, 0.15)...")
            automator.click_percentage(0.5, 0.15)
            
        self._smart_sleep(2)
        
        # 8. Bấm Đăng / Post
        automator.click_element(texts=["Đăng", "Post"], wait=5)
        
        # 8. Xử lý bảng hỏi xác nhận "Đăng video công khai?" (Nếu có)
        automator.click_element(texts=["Đăng ngay", "Post now", "Xác nhận", "Confirm", "OK", "Đồng ý"], retries=1, wait=3)
        
        # 9. Lướt thêm vài video sau khi đăng để ngụy trang
        logger.info("[ADB] Đang chờ Tiktok quay lại trang chính để lướt thêm video ngụy trang...")
        self._smart_sleep(5)
        # Bấm về Trang chủ TikTok nếu chưa ở đó
        automator.click_element(texts=["Trang chủ", "Home"], wait=2)
        self._smart_sleep(1)
        
        # Yêu cầu 23.3: Chuyển sang tab Đề xuất (For You) để tăng độ trust
        logger.info("[ADB] Chuyển sang tab Đề xuất (For You) để tránh kẹt ở tab Bạn bè...")
        # Bước 1: Bấm dừng video để ổn định XML
        w, h = automator.get_screen_size()
        automator._run_adb(["shell", "input", "tap", str(int(w/2)), str(int(h/2))])
        self._smart_sleep(1.5)
        
        # Bước 2: Tìm "Đã follow" làm neo, sau đó ưu tiên tìm "Đề xuất" / "Dành cho bạn"
        for_you_coords = automator.find_element(texts=["Đề xuất", "Dành cho bạn", "For You"])
        if for_you_coords:
            logger.info(f"[ADB] Đã tìm thấy tab 'Đề xuất/Dành cho bạn' tại {for_you_coords}, tiến hành click...")
            automator._run_adb(["shell", "input", "tap", str(for_you_coords[0]), str(for_you_coords[1])])
        else:
            anchor_coords = automator.find_element(texts=["Đã follow", "Following", "Bạn bè", "Friends"])
            if anchor_coords:
                x_anchor, y_anchor = anchor_coords
                logger.info(f"[ADB] Dùng điểm neo 'Đã follow' tại ({x_anchor}, {y_anchor}). Click sang bên phải 250px...")
                automator._run_adb(["shell", "input", "tap", str(x_anchor + 250), str(y_anchor)])
            else:
                logger.info("[ADB] Không tìm thấy điểm neo, dùng Fallback vuốt tương đối giữa màn hình...")
                automator._run_adb(["shell", "input", "swipe", str(int(w*0.8)), str(int(h*0.5)), str(int(w*0.2)), str(int(h*0.5)), "300"])
        
        self._smart_sleep(2)
        # Bấm Play lại video
        automator._run_adb(["shell", "input", "tap", str(int(w/2)), str(int(h/2))])
        self._smart_sleep(2)
        
        # 10. Lướt video vài lần để tăng trust, bất chấp quảng cáo hay Live
        logger.info("[ADB] Lướt video ngẫu nhiên để tăng trust (bất chấp Live/QC)...")
        for _ in range(3):
            automator._run_adb(["shell", "input", "swipe", str(int(w/2)), str(int(h*0.8)), str(int(w/2)), str(int(h*0.2)), "300"])
            self._smart_sleep(random.randint(3, 6))
            
            # Thả tim ngẫu nhiên
            if random.random() < 0.15:
                logger.info("[ADB] Thả tim ngẫu nhiên...")
                if automator.click_element(content_descs=['Thích', 'Like']):
                    self._smart_sleep(1)
            
            # Yêu thích ngẫu nhiên
            if random.random() < 0.15:
                logger.info("[ADB] Lưu yêu thích ngẫu nhiên...")
                if automator.click_element(content_descs=['Thêm vào mục Yêu thích', 'Add to Favorites', 'Lưu', 'Save']):
                    self._smart_sleep(1)
        
        # Gửi máy về màn hình chính của Android
        logger.info("[ADB] Trở về màn hình chính Android...")
        self._run_adb_cmd(["shell", "input", "keyevent", "3"])
        
        logger.info("[ADB] Upload Tiktok Mobile hoàn tất!")
        return "https://www.tiktok.com/@tiktok"

    def check_status(self) -> bool:
        """Kiểm tra thiết bị có online và nhận lệnh không"""
        return self.connect()