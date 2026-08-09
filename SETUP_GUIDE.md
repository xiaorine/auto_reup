# 🚀 Hướng Dẫn Cài Đặt Hệ Thống (Từ A-Z)

Dự án này sử dụng kiến trúc **Hybrid (Lai)** để tối ưu hóa hiệu năng và dung lượng ổ cứng:
- **Cơ sở hạ tầng nhẹ (Database, Cache, Web UI)**: Chạy qua Docker.
- **Lõi AI & Backend (Xử lý Video/Audio nặng)**: Chạy trực tiếp (Native) trên Windows để tận dụng tối đa sức mạnh Card đồ họa (GPU) và tránh lỗi thắt cổ chai ổ cứng của Docker (VHDX).

Dưới đây là các bước chi tiết để khởi chạy toàn bộ hệ thống trên một máy tính mới.

---

## 🛠️ YÊU CẦU HỆ THỐNG (Prerequisites)

Trước khi bắt đầu, máy tính của bạn CẦN CÓ sẵn các phần mềm sau:
1. **[Git](https://git-scm.com/downloads)**: Để clone mã nguồn.
2. **[Docker Desktop](https://www.docker.com/products/docker-desktop/)**: Đã cài đặt và đang bật.
3. **[Python 3.10](https://www.python.org/downloads/release/python-31011/)**: Bắt buộc là bản 3.10.x. (Nhớ tick vào ô `"Add Python to PATH"` lúc cài đặt).
4. **[FFmpeg](https://ffmpeg.org/download.html)**: Công cụ xử lý video/audio (Đã thêm vào môi trường `PATH`).
5. **[eSpeak-NG](https://github.com/espeak-ng/espeak-ng/releases)**: Thư viện tổng hợp giọng nói (Bắt buộc cho VieNeu-TTS).

*(Mẹo: Bạn có thể dùng lệnh `winget install -e --id Gyan.FFmpeg` và `winget install -e --id eSpeak-NG.eSpeak-NG` trên CMD để cài nhanh FFmpeg và eSpeak).*

---

## 📥 BƯỚC 1: Clone Mã Nguồn & Thiết Lập Biến Môi Trường

1. Clone dự án về máy:
   ```cmd
   git clone <đường-dẫn-repo-github>
   cd auto_reup_tiktok
   ```
2. Cấu hình file `.env` cho Backend:
   - Vào thư mục `backend`, copy file `.env.example` thành `.env` (hoặc tạo file mới).
   - Điền cấu hình cơ bản sau vào `.env`:
     ```env
     DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/autoreup
     CELERY_BROKER_URL=redis://127.0.0.1:6379/0
     CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
     GEMINI_API_KEY="<api-key-cua-ban>"
     ```

---

## 🐳 BƯỚC 2: Khởi Chạy Hạ Tầng Docker (Database & Frontend)

Hệ thống cần Postgres, Redis và Frontend (React) chạy độc lập để quản lý dữ liệu.

1. Tại thư mục gốc `auto_reup_tiktok`, mở CMD/PowerShell và chạy:
   ```cmd
   docker-compose up -d
   ```
2. Đợi một chút để Docker tải các image cần thiết.
3. Kiểm tra xem các container đã chạy chưa bằng lệnh: `docker ps`. Bạn sẽ thấy 3 container (postgres, redis, frontend) đang chạy ở trạng thái `Up`.

---

## 🧠 BƯỚC 3: Cài Đặt Lõi AI & Backend (Chạy Trực Tiếp / Native)

Vì phần Backend chứa các công cụ AI (PyTorch, Whisper, VieNeu) rất nặng, chúng ta sẽ cài đặt chúng trong một Môi Trường Ảo (Virtual Environment) của Python.

1. Di chuyển vào thư mục backend:
   ```cmd
   cd backend
   ```
2. Khởi tạo môi trường ảo (Venv):
   ```cmd
   py -3.10 -m venv .venv
   ```
3. Cài đặt các thư viện lõi AI (PyTorch & CUDA):
   - Chạy lệnh sau để ép hệ thống cài đặt PyTorch phiên bản hỗ trợ GPU NVIDIA (CUDA 12.4).
   - **Lưu ý**: *Sử dụng tham số `--progress-bar off` để tránh lỗi treo Terminal trên Windows khi giải nén các file khổng lồ.*
   ```cmd
   .venv\Scripts\pip.exe install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124 --progress-bar off
   ```

   *(Quá trình tải PyTorch 2.5GB và thư viện CUDA có thể mất 10-15 phút tùy cấu hình mạng).*

4. Cài đặt trình duyệt lõi cho Playwright (Bắt buộc để Crawler và Upload hoạt động):
   ```cmd
   .venv\Scripts\playwright.exe install chromium
   ```

---

## 🚀 BƯỚC 4: Kích Hoạt Hệ Thống

Bây giờ mọi thứ đã sẵn sàng. Bạn cần mở **2 cửa sổ Terminal (CMD)** riêng biệt tại thư mục `backend` để kích hoạt Server và Bot Xử Lý (Celery).

**Terminal 1 (Chạy Backend API Server):**
```cmd
cd backend
.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 (Chạy Celery Worker - Chuyên Xử Lý Tác Vụ Nặng):**
```cmd
cd backend
.venv\Scripts\celery.exe -A app.core.celery_app worker --loglevel=info --pool=solo
```

*(Lưu ý: Trên Windows, bắt buộc phải dùng cờ `--pool=solo` cho Celery để tránh các lỗi đa tiến trình).*

---

## 🎉 BƯỚC 5: Truy Cập Giao Diện

- **Giao diện Web**: Mở trình duyệt và truy cập `http://localhost:5173`
- **Tài liệu API (Swagger UI)**: Truy cập `http://127.0.0.1:8000/docs`

Hệ thống của bạn hiện đã hoàn toàn sẵn sàng để Auto-Reup và xử lý Video/Audio bằng AI với sức mạnh tối đa của GPU! 🎬🤖

---

## 🧹 BƯỚC 6: Quản Lý Dung Lượng (Cache & AppData)

Trong quá trình tải thư viện và chạy các model AI, hệ thống sẽ tự động tải về khá nhiều dữ liệu lưu trữ ngầm (Cache) vào ổ đĩa `C:\`. Để quản lý dung lượng ổ cứng, bạn có thể kiểm tra và xóa các thư mục sau nếu cần giải phóng bộ nhớ:

1. **Bộ nhớ đệm của PIP (Pip Cache)**: `C:\Users\<Tên_User>\AppData\Local\pip\Cache`
   - Nơi lưu trữ các file cài đặt thư viện (`.whl`) mà pip tải về. (Có thể xóa an toàn).
2. **Trình duyệt giả lập Playwright**: `C:\Users\<Tên_User>\AppData\Local\ms-playwright`
   - Chứa trình duyệt Chromium để crawl dữ liệu mạng. Nặng khoảng ~400MB.
3. **Mô hình AI HuggingFace**: `C:\Users\<Tên_User>\.cache\huggingface\hub`
   - Nơi tải về các tệp tin khổng lồ của mô hình *VieNeu-TTS*, *Pyannote Diarization* và các *Transformers*. Dung lượng có thể lên đến **4-6GB**. (Chỉ xóa nếu bạn không còn dùng dự án).
4. **Mô hình Whisper (Dịch thuật)**: `C:\Users\<Tên_User>\.cache\whisper`
   - Nơi tải về các model nhận diện giọng nói (Ví dụ: `base`, `large-v3`). Thường nặng vài GB.
