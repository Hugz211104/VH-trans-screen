# VH Translator

> **Dịch màn hình game thời gian thực theo phong cách WindowTranslator cho Windows 10/11** — *made by Hưng*

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-0078D6?style=flat-square)](#tải-về-nhanh)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square)](#chạy-từ-mã-nguồn)
[![Release](https://img.shields.io/badge/Download-Latest%20Release-E5A93C?style=flat-square)](https://github.com/Hugz211104/VH-trans-screen/releases/latest)

**VH Translator** đọc chữ trực tiếp trên cửa sổ game (hoặc vùng chọn cố định) bằng OCR cục bộ, dịch sang tiếng Việt và vẽ lớp phủ đè gọn gàng ngay lên vị trí chữ gốc — click xuyên qua hoàn toàn và không làm giật hay lặp chữ.

---

## Tải về nhanh

1. Mở trang **[Releases mới nhất](https://github.com/Hugz211104/VH-trans-screen/releases/latest)**.
2. Tải file **`VHGameTranslator.exe`** (bản chạy độc lập, đã tích hợp sẵn bộ nhận diện đa font PP-OCRv5, không cần cài Python).
3. Đặt file vào một thư mục riêng và mở trực tiếp. File cấu hình `settings.json` và bộ nhớ đệm `translation_cache.json` sẽ tự động lưu ngay cạnh file `.exe`.

---

## Tính năng nổi bật

- **3 chế độ OCR cục bộ (hoạt động offline)**:
  - **PP-OCRv5 Đa font** (`rapidocr` + `onnxruntime`): nhận diện chính xác font nghệ thuật, font hải tặc, chữ viết tay, chữ trên nền giấy da hoặc có viền bóng.
  - **Windows + Tăng nét**: Windows OCR kết hợp tăng độ nét và tương phản (~50 ms), hợp chữ serif có viền nhẹ.
  - **Windows Nhanh**: Windows OCR nguyên bản (~20 ms), tối ưu tốc độ cho game dùng font hiện đại rõ nét.
- **Lớp phủ thông minh đè trực tiếp lên game**:
  - Click xuyên qua hoàn toàn, không cướp tiêu điểm chuột/bàn phím.
  - Tự ẩn khỏi ảnh chụp màn hình bằng `WDA_EXCLUDEFROMCAPTURE` (Windows 10 2004+), tránh vòng lặp OCR và nhấp nháy.
  - Thuật toán chống chồng hộp 2 chiều + tự co cỡ chữ, giữ khung dịch gọn sát chữ gốc; hỗ trợ tự lấy màu nền thích ứng theo hộp thoại game.
  - Tích hợp **Mini HUD** nổi gọn ở góc màn hình để theo dõi độ trễ và bật/tắt nhanh.
- **Đa bộ máy dịch & chế độ Hybrid**:
  - **Google Translate miễn phí (HTTP/2 Keep-Alive)**: không cần API key, phản hồi ~45 ms.
  - **Google Gemini API**: dịch mượt theo ngữ cảnh cốt truyện, tự suy đoán sửa lỗi ký tự OCR, tự chuyển mô hình dự phòng khi quá tải.
  - **Groq / Cerebras / DeepSeek / OpenAI / OpenRouter / Ollama**: hỗ trợ mọi máy chủ chuẩn OpenAI-compatible.
  - **Chế độ Hybrid**: hiện bản dịch nhanh (~45 ms) ngay lập tức, sau đó AI cập nhật mượt ngầm phía sau và tự hủy nếu cảnh thoại đã chuyển.
- **Bộ nhớ đệm LRU (Cache)**: tự lưu các câu thoại và menu đã gặp để hiển thị lại tức thì (0 ms) và tiết kiệm lượt gọi API.
- **Phím tắt toàn cục qua Windows API (`RegisterHotKey`)**: không dùng hook bàn phím toàn cục nên không gây trễ phím khi chơi game.

---

## Phím tắt trong game

| Phím tắt | Chức năng |
| --- | --- |
| `Alt + T` | Bắt đầu / Dừng dịch tự động |
| `Alt + S` | Quét và dịch nhanh một lần (hoặc ép quét lại ngay) |
| `Alt + H` | Ẩn / Hiện nhanh lớp phủ tiếng Việt |

---

## Chạy từ mã nguồn

Yêu cầu: **Windows 10 (2004+) / Windows 11 (64-bit)** và **Python 3.11+**.

### Cách 1: Chạy tự động bằng `run.bat`
Nháy đúp vào `run.bat` — script tự tạo môi trường ảo `.venv`, cài thư viện trong `requirements.txt` và mở ứng dụng.

### Cách 2: Chạy bằng PowerShell
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

---

## Tự đóng gói file `.exe`

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe build_exe.py
```

File thực thi sau khi build sẽ nằm tại `dist\VHGameTranslator.exe`.

---

## Lưu ý khi sử dụng

1. Đặt game ở chế độ **Borderless Windowed (Không viền)** hoặc **Windowed** để Windows cho phép vẽ lớp phủ lên trên cửa sổ game.
2. Thu hẹp vùng chụp về khung phụ đề (**Phụ đề dưới** / **Hội thoại giữa-dưới** hoặc **Vùng cố định**) giúp tăng tốc độ OCR lên gấp nhiều lần.
3. Nếu bạn nhập API key cá nhân trong ứng dụng, **không commit** file `settings.json` có chứa khóa của bạn lên kho lưu trữ công khai.
4. Với game tiếng Nhật, Trung hoặc Hàn dùng Windows OCR, cài thêm gói ngôn ngữ tương ứng tại **Windows Settings → Time & Language → Language & Region**.
