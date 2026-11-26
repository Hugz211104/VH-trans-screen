# VH Translator

> **Dịch màn hình game thời gian thực theo phong cách WindowTranslator cho Windows 10/11** — *made by Hưng*

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-0078D6?style=flat-square)](#tải-về-nhanh)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square)](#chạy-từ-mã-nguồn)
[![Release](https://img.shields.io/badge/Download-v1.1.0-E5A93C?style=flat-square)](https://github.com/Hugz211104/VH-trans-screen/releases/latest)

**VH Translator** đọc chữ trực tiếp trên cửa sổ game (hoặc vùng phụ đề cố định) bằng OCR cục bộ, dịch sang tiếng Việt độ trễ cực thấp và vẽ lớp phủ đè gọn gàng ngay lên vị trí chữ gốc — click xuyên qua hoàn toàn, không cướp tiêu điểm game và không bị lặp chữ.

---

## Tải về nhanh

1. Mở trang **[Releases mới nhất](https://github.com/Hugz211104/VH-trans-screen/releases/latest)**.
2. Tải file **`VHGameTranslator.exe`** (bản chạy độc lập, đã tích hợp sẵn Python, Visual C++ runtime, `rapidfuzz` và 2 model đa font `PP-OCRv5` — **không cần cài Python**).
3. Đặt file vào một thư mục riêng và mở trực tiếp. File cấu hình `settings.json` và bộ nhớ đệm `translation_cache.json` sẽ tự lưu ngay cạnh file `.exe`.

---

## Tính năng nổi bật (v1.1.0)

- **Kiến trúc giảm độ trễ tối đa**:
  - **Chia 2 luồng dịch (Tiered Routing)**: nút bấm / nhãn menu ngắn (1–4 từ) đi thẳng luồng HTTP/2 nhanh (~45 ms), chỉ gửi hội thoại dài cần ngữ cảnh lên AI.
  - **SSE Streaming & Prompt cố định (< 75 token)**: đổ chữ trực tiếp từng phần lên lớp phủ cho cả **Gemini** (`streamGenerateContent?alt=sse` + `systemInstruction`) và **OpenAI / Groq / Cerebras / DeepSeek / Ollama / llama.cpp** (`stream: true`, thân thiện KV-Cache, tự bỏ thẻ `<think>`).
  - **Cache 2 tầng với `rapidfuzz`**: Tầng 1 hash chính xác (~0.01 ms) + Tầng 2 so mờ lõi C++ (≥ 93%, ~0.3 ms trên 10.000 câu) bắt gọn câu bị OCR đọc lệch 1–2 ký tự.
  - **Hàng đợi 1 phần tử (Drop-Oldest) + Hủy ngang (Preemptive Cancel)**: luôn xử lý khung hình mới nhất để không bao giờ dồn lag; bấm qua thoại mới tự hủy ngay request cũ.
  - **Trigger thông minh cho chữ chạy (Typewriter)**: kết thúc `. ? !` bắn dịch ngay (`0 ms`), đang chạy dở giữa câu chỉ đợi ổn định `130 ms`.
  - **Mặt nạ chữ chống nền 3D động (`< 0.2 ms`)**: lọc nét chữ 1/4-scale + giãn 3×3 khử rung viền anti-aliasing, bỏ qua chuyển động sóng, mây, ánh sáng nền 3D mà vẫn bắt chuẩn cả phụ đề sáng lẫn chữ mực tối trên giấy da.
- **3 chế độ OCR cục bộ (hoạt động offline)**:
  - **PP-OCRv5 Đa font** (`rapidocr` + `onnxruntime`, giới hạn cạnh dài `640 / 960 / 1280 px`, `use_cls=False`): đọc chuẩn font nghệ thuật, font hải tặc, chữ viết tay, nền giấy da.
  - **Windows + Tăng nét**: Windows OCR kèm tăng nét và tương phản (~39 ms).
  - **Windows Nhanh**: Windows Native OCR (`Windows.Media.Ocr`, ~7–20 ms) cho font hiện đại.
- **Lớp phủ trực tiếp & Giám sát 4 khâu**:
  - Click xuyên qua hoàn toàn, ẩn khỏi ảnh chụp màn hình bằng `WDA_EXCLUDEFROMCAPTURE`, chống chồng hộp 2 chiều + tự co cỡ chữ, nền thích ứng và **Mini HUD** gọn nhẹ.
  - Hiển thị trực tiếp thời gian từng khâu **Chụp / So**, **OCR**, **Dịch (TTFT / tổng)** và **Bộ nhớ đệm** ngay trên thanh bên.
- **Phím tắt toàn cục chuẩn Windows API (`RegisterHotKey`)**: không dùng hook bàn phím nên không gây trễ thao tác khi chơi game.

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

File thực thi độc lập sau khi build sẽ nằm tại `dist\VHGameTranslator.exe`.

---

## Lưu ý khi sử dụng

1. Đặt game ở chế độ **Borderless Windowed (Không viền)** hoặc **Windowed** để Windows cho phép vẽ lớp phủ lên trên game.
2. Thu hẹp vùng chụp về khung phụ đề (**Phụ đề dưới** / **Hội thoại giữa-dưới** hoặc **Vùng cố định**) và chọn giới hạn OCR `640–960 px` để đạt tốc độ nhanh nhất.
3. Nếu nhập API key cá nhân trong ứng dụng, **không commit** file `settings.json` có chứa khóa cá nhân lên kho lưu trữ công khai.
4. Với game tiếng Nhật, Trung hoặc Hàn dùng Windows OCR, cài thêm gói ngôn ngữ tương ứng tại **Windows Settings → Time & Language → Language & Region**.
