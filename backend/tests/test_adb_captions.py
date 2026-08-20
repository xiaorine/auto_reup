import time
import os
import sys
import logging
from app.db.session import SessionLocal
from app.models.history import VideoHistory
from app.models.social_account import SocialAccount
from app.services.uploader.adb_engine import ADBUploader
from app.services.uploader.adb_automator import ADBAutomator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = SessionLocal()
video = db.query(VideoHistory).filter(VideoHistory.final_video_path.isnot(None)).order_by(VideoHistory.id.desc()).first()
account = db.query(SocialAccount).filter(SocialAccount.username.like('%Zhiu%Nika%')).first()

if not video:
    print("Không tìm thấy video nào đã xử lý.")
    sys.exit(1)
if not account:
    print("Không tìm thấy tài khoản.")
    sys.exit(1)

video_path = video.final_video_path

print(f"Bắt đầu test với video: {video_path}")
print(f"Thiết bị: {account.device_id}")

uploader = ADBUploader({
    "auth_data": account.device_id,
    "device_id": account.device_id,
    "platform": "tiktok"
})

# Dọn dẹp video cũ
uploader._run_adb_cmd(["shell", "rm", "-f", "/sdcard/DCIM/Camera/reup_*.mp4"])

# Đẩy video
remote_path = f"/sdcard/DCIM/Camera/reup_{int(time.time())}.mp4"
uploader._run_adb_cmd(["shell", "mkdir", "-p", "/sdcard/DCIM/Camera"])
uploader._run_adb_cmd(["push", video_path, remote_path], timeout=120)
uploader._run_adb_cmd(["shell", "touch", remote_path])
uploader._run_adb_cmd(["shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote_path}"])
time.sleep(5)

automator = ADBAutomator(uploader.adb_ip)
automator.check_adb_keyboard()

# logger.info("[ADB] Khởi động Tiktok Mobile App...")
# uploader._run_adb_cmd(["shell", "am", "force-stop", "com.zhiliaoapp.musically"])
# time.sleep(2)
# uploader._run_adb_cmd(["shell", "monkey", "-p", "com.zhiliaoapp.musically", "-c", "android.intent.category.LAUNCHER", "1"])
# uploader._run_adb_cmd(["shell", "monkey", "-p", "com.ss.android.ugc.trill", "-c", "android.intent.category.LAUNCHER", "1"])

app_opened = False
for i in range(12): 
    time.sleep(5)
    if automator.find_element(texts=["Trang chủ", "Home", "Hồ sơ", "Profile", "Tạo", "Create", "Hộp thư", "Inbox"]):
        app_opened = True
        break

if not app_opened:
    print("Tiktok tải quá chậm.")
    sys.exit(1)

# 1. Bấm nút + (Tạo mới) ở giữa cạnh dưới
logger.info("[ADB] Bấm nút + (Tạo mới) ở giữa cạnh dưới màn hình...")
automator.click_percentage(0.5, 0.92)
time.sleep(3)
automator.handle_permission_popups()

# 2. Upload
logger.info("[ADB] Bấm nút Tải lên (Upload)...")
if not automator.click_element(texts=["Tải lên", "Upload", "Album", "Gallery"], wait=3):
    logger.info("[ADB] Không tìm thấy nút Tải lên bằng chữ, dùng thuật toán dò mìn (Phải -> Trái)...")
    automator.click_dynamic_bottom_right()
    automator.handle_permission_popups()
    
    # Check for Video tab
    if not automator.find_element(texts=["Video", "Videos", "视频"]):
        logger.info("[ADB] Bấm bên phải không ra Thư viện. Hủy thao tác và thử bấm bên Trái...")
        uploader._run_adb_cmd(["shell", "input", "keyevent", "4"]) # Bấm Back
        time.sleep(2)
        automator.click_dynamic_bottom_left()
automator.handle_permission_popups()

# 3. Chuyển tab Video
if automator.click_element(texts_contains=["Video", "Videos", "视频"], content_descs=["Video", "Videos", "视频"], wait=2):
    pass
else:
    logger.info("[ADB] Không tìm thấy tab Video, tiếp tục lấy file đầu tiên...")

try:
    # 4. Chọn video
    automator.click_percentage(0.25, 0.3)
    time.sleep(2)
    
    logger.info("[ADB] Bấm Tiếp (Màn hình chọn video)")
    if not automator.click_element(texts=["Tiếp", "Next", "Tiếp theo", "Continue"], wait=4):
        automator.click_percentage(0.85, 0.95)
        time.sleep(2)
        
    logger.info("[ADB] Bấm Tiếp (Màn hình chỉnh sửa video)")
    if not automator.click_element(texts=["Tiếp", "Next", "Tiếp theo", "Continue"], wait=4):
        automator.click_percentage(0.85, 0.95)
        time.sleep(2)

    # 7. Gõ caption (Hỗ trợ tiếng Việt đầy đủ, Emoji, ngắt dòng)
    text = "🔥 Test caption tự động từ Cáo: Tiếng Việt chuẩn 100% & Emoji siêu đỉnh! #xuhuong #reup #fyp "
    if not automator.click_element(texts_contains=["Mô tả", "Thêm mô tả", "Add description", "Describe your post"], wait=2):
        automator.click_percentage(0.3, 0.2)
    time.sleep(1)
    
    # Gửi text qua ADB Keyboard (Base64)
    automator.input_text(text)
    time.sleep(3) # Chờ 3s để Tiktok xử lý chuỗi và render hashtag (nếu có)
    
    # Bấm phím Back 1 lần để đóng bảng gợi ý hashtag của Tiktok và thoát trạng thái Focus của ô nhập liệu
    logger.info("[ADB] Ẩn bảng gợi ý và thoát focus nhập liệu...")
    uploader._run_adb_cmd(["shell", "input", "keyevent", "4"])
    time.sleep(1)

except Exception as e:
    logger.error(f"Lỗi trong quá trình chạy: {e}")
finally:
    # Force stop tiktok on exit
    logger.info("[ADB] Dọn dẹp: Đóng hoàn toàn Tiktok...")
    uploader._run_adb_cmd(["shell", "am", "force-stop", "com.zhiliaoapp.musically"])
