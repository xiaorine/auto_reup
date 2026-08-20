import base64
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from unittest.mock import MagicMock, patch
from app.services.uploader.adb_automator import ADBAutomator

def test_adb_automator_input_text_unicode():
    automator = ADBAutomator("127.0.0.1:5555")
    
    # Test cases với các chuỗi Unicode tiếng Việt, emoji, ký tự đặc biệt, xuống dòng
    test_strings = [
        "Chào mừng bạn đến với kênh của tôi! Trải nghiệm video cực đã.",
        "🔥 Video review siêu đỉnh nè! #xuhuong #fyp\nLink mua ở comment nha cả nhà! 🇻🇳",
        "Nội dung 'đặc biệt' & \"tuyệt vời\" (giá $100) -> 100% OK! #douyin",
        "测试中文字符输入是否正常 🚀 #tiktok #viral"
    ]
    
    with patch.object(automator, "check_adb_keyboard", return_value=True), \
         patch.object(automator, "_run_adb", return_value="Broadcast completed: result=0") as mock_run_adb:
        
        for text in test_strings:
            success = automator.input_text(text)
            assert success is True, f"Failed to input text: {text}"
            
            # Kiểm tra mock_run_adb được gọi đúng định dạng
            expected_b64 = base64.b64encode(text.encode('utf-8')).decode('ascii')
            mock_run_adb.assert_called_with([
                "shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", expected_b64
            ])
            
            # Kiểm tra giải mã ngược lại từ Base64 có khớp 100% không
            decoded = base64.b64decode(expected_b64.encode('ascii')).decode('utf-8')
            assert decoded == text, f"Decoded text '{decoded}' does not match original '{text}'"

def test_adb_automator_empty_text():
    automator = ADBAutomator("127.0.0.1:5555")
    with patch.object(automator, "_run_adb") as mock_run_adb:
        assert automator.input_text("") is True
        mock_run_adb.assert_not_called()

def test_adb_automator_clear_text():
    automator = ADBAutomator("127.0.0.1:5555")
    with patch.object(automator, "_run_adb") as mock_run_adb:
        automator.clear_text()
        mock_run_adb.assert_called_with(["shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"])

if __name__ == "__main__":
    test_adb_automator_input_text_unicode()
    test_adb_automator_empty_text()
    test_adb_automator_clear_text()
    print("All ADB Unicode tests passed successfully!")
