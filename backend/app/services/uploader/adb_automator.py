import subprocess
import time
import re
import os
import tempfile
import base64
import xml.etree.ElementTree as ET
import logging
logger = logging.getLogger(__name__)
import urllib.request

class ADBAutomator:
    def __init__(self, adb_ip: str):
        self.adb_ip = adb_ip
        self.local_xml_path = os.path.join(tempfile.gettempdir(), f"window_dump_{self.adb_ip.replace(':', '_')}.xml")

    def _run_adb(self, args: list, timeout: int = 60) -> str:
        cmd = ["adb", "-s", self.adb_ip] + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace')
            return result.stdout
        except Exception as e:
            logger.error(f"[ADBAutomator] Lỗi chạy lệnh {cmd}: {e}")
            return ""

    def wait_for_app_foreground(self, package_names: list, timeout: int = 60) -> bool:
        """Đợi cho đến khi một trong các package_names nằm ở Foreground."""
        start_time = time.time()
        logger.info(f"[ADBAutomator] Đang chờ app {package_names} khởi động lên Foreground...")
        while time.time() - start_time < timeout:
            output = self._run_adb(["shell", "dumpsys", "activity", "activities"])
            # Kiểm tra dòng mCurrentFocus hoặc mFocusedApp hoặc mResumedActivity
            for pkg in package_names:
                if f"mCurrentFocus" in output and pkg in output:
                    logger.info(f"[ADBAutomator] Đã thấy {pkg} ở Foreground (mCurrentFocus) sau {int(time.time() - start_time)}s")
                    return True
                if f"mFocusedApp" in output and pkg in output:
                    logger.info(f"[ADBAutomator] Đã thấy {pkg} ở Foreground (mFocusedApp) sau {int(time.time() - start_time)}s")
                    return True
                if f"mResumedActivity" in output and pkg in output:
                    logger.info(f"[ADBAutomator] Đã thấy {pkg} ở Foreground (mResumedActivity) sau {int(time.time() - start_time)}s")
                    return True
            time.sleep(2)
        logger.error(f"[ADBAutomator] Quá thời gian chờ {timeout}s nhưng không thấy app nào trong {package_names} ở Foreground.")
        return False

    def dump_ui(self) -> ET.Element:
        """Kéo file giao diện XML từ thiết bị về và parse."""
        remote_xml = "/sdcard/window_dump.xml"
        self._run_adb(["shell", "uiautomator", "dump", remote_xml])
        self._run_adb(["pull", remote_xml, self.local_xml_path])
        
        if not os.path.exists(self.local_xml_path):
            return None
            
        try:
            tree = ET.parse(self.local_xml_path)
            return tree.getroot()
        except Exception as e:
            logger.error(f"[ADBAutomator] Lỗi parse XML: {e}")
            return None

    def _get_center_from_bounds(self, bounds_str: str):
        """Chuyển '[x1,y1][x2,y2]' thành tọa độ trung tâm (x, y)"""
        match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
        if match:
            x1, y1, x2, y2 = map(int, match.groups())
            return (x1 + x2) // 2, (y1 + y2) // 2
        return None

    def find_element(self, texts=None, texts_contains=None, content_descs=None, resource_ids=None, classes=None) -> tuple:
        """Tìm tọa độ phần tử dựa trên các thuộc tính. Hỗ trợ đa ngôn ngữ (list)."""
        root = self.dump_ui()
        if root is None:
            return None
            
        texts = texts or []
        texts_contains = texts_contains or []
        content_descs = content_descs or []
        resource_ids = resource_ids or []
        classes = classes or []
        
        for node in root.iter('node'):
            text = node.get('text', '')
            desc = node.get('content-desc', '')
            res_id = node.get('resource-id', '')
            cls = node.get('class', '')
            bounds = node.get('bounds', '')
            
            if not bounds or bounds == '[0,0][0,0]':
                continue
                
            match = False
            if any(t.lower() == text.lower() for t in texts if text): # Exact match
                match = True
            if any(t.lower() in text.lower() for t in texts_contains if text): # Partial match
                match = True
            if any(d.lower() in desc.lower() for d in content_descs if desc):
                match = True
            if any(r.lower() == res_id.lower() for r in resource_ids if res_id):
                match = True
            if any(c.lower() == cls.lower() for c in classes if cls):
                match = True
                
            if match:
                return self._get_center_from_bounds(bounds)
                
        return None

    def click_element(self, texts=None, texts_contains=None, content_descs=None, resource_ids=None, classes=None, retries=3, wait=2) -> bool:
        """Tìm và bấm vào phần tử. Thử lại nhiều lần nếu chưa thấy giao diện cập nhật."""
        for attempt in range(retries):
            coords = self.find_element(texts=texts, texts_contains=texts_contains, content_descs=content_descs, resource_ids=resource_ids, classes=classes)
            if coords:
                x, y = coords
                ident = texts or texts_contains or content_descs or classes
                logger.info(f"[ADBAutomator] Tìm thấy element {ident} tại tọa độ ({x}, {y}). Đang click...")
                self._run_adb(["shell", "input", "tap", str(x), str(y)])
                time.sleep(wait)
                return True
            logger.info(f"[ADBAutomator] Đang đợi màn hình tải để tìm {texts or content_descs} (Lần {attempt+1}/{retries})")
            time.sleep(wait)
            
        logger.error(f"[ADBAutomator] Không tìm thấy phần tử {texts or content_descs} sau {retries} lần thử.")
        return False

    def check_adb_keyboard(self) -> bool:
        """Kiểm tra và cài đặt ADBKeyboard nếu chưa có"""
        packages = self._run_adb(["shell", "pm", "list", "packages", "com.android.adbkeyboard"])
        if "com.android.adbkeyboard" not in packages:
            logger.info("[ADBAutomator] Chưa có ADBKeyboard. Bắt đầu tự động tải và cài đặt...")
            apk_url = "https://github.com/senzhk/ADBKeyBoard/raw/master/ADBKeyboard.apk"
            apk_path = os.path.join(tempfile.gettempdir(), "ADBKeyboard.apk")
            if not os.path.exists(apk_path):
                try:
                    urllib.request.urlretrieve(apk_url, apk_path)
                except Exception as e:
                    logger.error(f"[ADBAutomator] Không thể tải ADBKeyboard.apk: {e}")
                    return False
            
            self._run_adb(["install", "-r", apk_path])
            logger.info("[ADBAutomator] Cài đặt ADBKeyboard thành công.")
            time.sleep(2)
            
        # Kiểm tra và set làm bàn phím mặc định
        current_ime = self._run_adb(["shell", "settings", "get", "secure", "default_input_method"])
        if "com.android.adbkeyboard" not in current_ime:
            logger.info("[ADBAutomator] Đang thiết lập ADBKeyboard làm bàn phím mặc định...")
            self._run_adb(["shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME"])
            self._run_adb(["shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"])
            time.sleep(1)
        return True

    def clear_text(self):
        """Xóa sạch nội dung trong ô nhập liệu đang focus"""
        logger.info("[ADBAutomator] Xóa sạch ô nhập liệu hiện tại qua ADB_CLEAR_TEXT...")
        self._run_adb(["shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"])
        time.sleep(0.5)

    def input_text(self, text: str) -> bool:
        """
        Nhập văn bản vào ô đang focus, hỗ trợ đầy đủ 100% Unicode (tiếng Việt có dấu, tiếng Trung,
        emoji, xuống dòng, ký tự đặc biệt, dấu nháy kép/đơn) qua ADBKeyBoard sử dụng Base64 (ADB_INPUT_B64).
        """
        if not text:
            return True
            
        # Đảm bảo ADBKeyboard đã sẵn sàng
        self.check_adb_keyboard()
        
        try:
            # Mã hóa Base64 chuỗi UTF-8 để truyền an toàn qua ADB
            b64_msg = base64.b64encode(text.encode('utf-8')).decode('ascii')
            logger.info(f"[ADBAutomator] Nhập văn bản Unicode ({len(text)} ký tự) qua ADB_INPUT_B64...")
            out = self._run_adb(["shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b64_msg])
            if "result=0" in out or "Broadcast completed" in out:
                logger.info("[ADBAutomator] Gửi text Unicode qua ADB_INPUT_B64 thành công.")
                return True
            logger.info(f"[ADBAutomator] Broadcast ADB_INPUT_B64 output: {out}")
            return True
        except Exception as e:
            logger.error(f"[ADBAutomator] Lỗi khi gửi text qua ADB_INPUT_B64: {e}")
            return False

    def get_screen_size(self):
        """Lấy kích thước màn hình để bấm theo tọa độ phần trăm nếu cần"""
        output = self._run_adb(["shell", "wm", "size"])
        # Cải tiến từ PR #2: Đọc Override size trước nếu có, vì đây là độ phân giải thực tế đang hiển thị
        width, height = 1080, 1920 
        
        if output:
            try:
                if "Override size:" in output:
                    match = re.search(r"Override size: (\d+)x(\d+)", output)
                else:
                    match = re.search(r"Physical size: (\d+)x(\d+)", output)
                    
                if match:
                    width = int(match.group(1))
                    height = int(match.group(2))
            except Exception as e:
                logger.error(f"Lỗi khi lấy kích thước màn hình: {e}")
        
        return width, height

    def click_percentage(self, pct_x: float, pct_y: float):
        """Bấm theo % chiều rộng và chiều cao màn hình"""
        w, h = self.screen_size if hasattr(self, 'screen_size') else self.get_screen_size()
        self.screen_size = (w, h)
        x = int(w * pct_x)
        y = int(h * pct_y)
        logger.info(f"[ADBAutomator] Click tọa độ tương đối ({pct_x:.2f}, {pct_y:.2f}) -> ({x}, {y})")
        self._run_adb(["shell", "input", "tap", str(x), str(y)])
        time.sleep(1)

    def get_dynamic_pos(self) -> tuple:
        """Tính toán tọa độ X, Y chính xác bằng OpenCV hoặc cào XML tìm cụm nút Quay phim."""
        # 1. Thử dùng OpenCV (Template Matching hoặc Image Processing) trước nếu có cài cv2
        try:
            import cv2
            import numpy as np
            import tempfile
            
            logger.info("[ADBAutomator] Đang sử dụng OpenCV để tìm tọa độ Y của nút Quay (Record)...")
            img_path = os.path.join(tempfile.gettempdir(), "screencap_dynamic.png")
            self._run_adb(["shell", "screencap", "-p", "/sdcard/screencap_dynamic.png"])
            self._run_adb(["pull", "/sdcard/screencap_dynamic.png", img_path])
            
            if os.path.exists(img_path):
                img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
                h, w = img.shape
                # Nút Quay thường là một hình tròn trắng/đỏ rất to nằm ở nửa dưới màn hình
                # Cắt lấy nửa dưới màn hình để tăng tốc độ và tránh nhiễu
                bottom_half = img[int(h/2):, :]
                
                # Dùng thuật toán phát hiện viền hoặc hình tròn lớn nhất ở giữa
                # Đơn giản nhất là tìm vùng sáng lớn nhất ở khoảng X = w/2
                circles = cv2.HoughCircles(bottom_half, cv2.HOUGH_GRADIENT, dp=1.2, minDist=100,
                                           param1=50, param2=30, minRadius=40, maxRadius=150)
                
                if circles is not None:
                    circles = np.round(circles[0, :]).astype("int")
                    # Lấy hình tròn gần tâm X nhất
                    center_x = w / 2
                    best_circle = min(circles, key=lambda c: abs(c[0] - center_x))
                    cx, cy, r = best_circle
                    
                    real_y = int(h/2) + cy
                    logger.info(f"[ADBAutomator] OpenCV tìm thấy nút Quay tại X={cx}, Y={real_y} (Tỷ lệ: {cx/w:.3f}, {real_y/h:.3f})")
                    return cx / w, real_y / h
        except ImportError:
            logger.warning("[ADBAutomator] Chưa cài đặt opencv-python, chuyển sang dùng XML (Fallback)...")
        except Exception as e:
            logger.warning(f"[ADBAutomator] Lỗi khi dùng OpenCV tìm nút Quay: {e}. Chuyển sang XML...")

        # 2. Fallback: Dùng XML
        try:
            import tempfile
            import xml.etree.ElementTree as ET
            import re
            import statistics
            
            xml_path = "/sdcard/window_dump_dynamic.xml"
            self._run_adb(["shell", "uiautomator", "dump", xml_path])
            local_path = os.path.join(tempfile.gettempdir(), "window_dump_dynamic.xml")
            self._run_adb(["pull", xml_path, local_path])
            
            if os.path.exists(local_path):
                tree = ET.parse(local_path)
                w, h = self.screen_size if hasattr(self, 'screen_size') else self.get_screen_size()
                x_center = w / 2
                
                x_candidates = []
                y_candidates = []
                for node in tree.iter():
                    bounds_str = node.get("bounds")
                    if not bounds_str: continue
                    match = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
                    if not match: continue
                    
                    left, top, right, bottom = [int(x) for x in match.groups()]
                    width = right - left
                    height = bottom - top
                    
                    # Nút quay phim thường cắt ngang qua trục dọc giữa màn hình, to trên 80px, nằm ở nửa dưới
                    if left < x_center < right and top > h / 2 and width > 80 and height > 80:
                        x_candidates.append((left + right) / 2)
                        y_candidates.append((top + bottom) / 2)
                
                if y_candidates and x_candidates:
                    median_x = statistics.median(x_candidates)
                    median_y = statistics.median(y_candidates)
                    logger.info(f"[ADBAutomator] Đã tìm thấy cụm nút Camera tại X={median_x}, Y={median_y} (Tỷ lệ: {median_x/w:.3f}, {median_y/h:.3f})")
                    return median_x / w, median_y / h
        except Exception as e:
            logger.warning(f"[ADBAutomator] Lỗi khi tính Dynamic Y tự động, sử dụng fallback. Lỗi: {e}")
            
        w, h = self.screen_size if hasattr(self, 'screen_size') else self.get_screen_size()
        self.screen_size = (w, h)
        ratio = h / w
        if ratio > 2.0:
            return 0.5, 0.80 # Màn hình dài
        return 0.5, 0.85 # Màn hình chuẩn

    def click_dynamic_bottom_right(self):
        """Bấm vào nút Upload mặc định (Thường ở bên Phải)"""
        x, y = self.get_dynamic_pos()
        self.click_percentage(x + 0.25, y)
        time.sleep(2)
        
    def click_dynamic_bottom_left(self):
        """Bấm vào nút Upload (Bên Trái - Cùng hàng với các nút chữ như Đăng/Tạo/Live)"""
        w_scr, h_scr = self.screen_size if hasattr(self, 'screen_size') else self.get_screen_size()
        bottom_btn = self.find_element(texts=["Đăng", "Tạo", "Live", "Trực tiếp", "Post", "Create", "Story", "发布", "拍摄", "相册"])
        if bottom_btn:
            y_bottom_bar = bottom_btn[1] / h_scr
            logger.info(f"[ADBAutomator] Lấy Y của nút Đăng/Tạo (Bottom Bar) = {y_bottom_bar:.3f}")
        else:
            y_bottom_bar = (h_scr - 80) / h_scr
            
        self.click_percentage(0.15, y_bottom_bar)
        time.sleep(2)
        

    def handle_permission_popups(self, max_popups=3):
        """Xử lý các popup cấp quyền (Camera, Mic, Bộ nhớ)"""
        for _ in range(max_popups):
            if self.click_element(texts=["Cho phép", "Allow", "Trong khi dùng ứng dụng", "While using the app", "OK", "Đồng ý", "Agree", "Got it"], retries=1, wait=1):
                logger.info("[ADBAutomator] Đã đồng ý cấp quyền truy cập.")
                time.sleep(1)
            else:
                break