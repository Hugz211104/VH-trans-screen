from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def get_app_dir() -> Path:
    """Return directory alongside the .exe when frozen, or project root in dev mode."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = get_app_dir()
CONFIG_PATH = APP_DIR / "settings.json"
CACHE_PATH = APP_DIR / "translation_cache.json"


@dataclass
class AppConfig:
    # Capture target
    capture_mode: str = "window"  # "window" or "region"
    target_window_title: str = ""
    window_crop_preset: str = "full"  # "full", "bottom" (38%), "center_bottom" (45% center)
    region: list[int] = field(default_factory=lambda: [100, 100, 1280, 720])  # physical [x, y, w, h]

    # OCR & Speed settings
    ocr_engine: str = "rapidocr"  # "rapidocr" (PP-OCRv5 multi-font), "windows_enhanced", "windows"
    ocr_language: str = "en-US"  # Language tag (e.g., en-US, ja, zh-Hans-CN, ko)
    ocr_min_confidence: float = 0.55
    scan_interval_ms: int = 150
    ocr_max_width: int = 1280  # 0 = native, 1280 = fast sweet-spot, 1600 = sharp
    merge_lines: bool = True
    filter_hud_noise: bool = True  # Skip FPS/timers/compass/3D noise artifacts
    min_text_length: int = 2
    change_threshold: float = 1.6  # Frame diff threshold (0-255)

    # Translation API settings
    provider: str = "google_free"  # "google_free", "gemini", "openai"
    hybrid_fast_preview: bool = True  # Show ~45ms HTTP/2 preview immediately while AI refines in background
    target_language: str = "vi"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    game_context: str = (
        "Đây là văn bản trong game (hội thoại, giao diện, kỹ năng, vật phẩm). "
        "Hãy dịch sang tiếng Việt tự nhiên, ngắn gọn, đúng văn phong game thủ, "
        "giữ nguyên tên riêng nếu cần và không giải thích thêm."
    )
    use_cache: bool = True

    # Overlay appearance
    adaptive_bg: bool = True
    bg_color: str = "#101218"
    bg_opacity: int = 215  # 0-255
    text_color: str = "#FFFFFF"
    font_family: str = "Segoe UI"
    min_font_size: int = 8
    max_font_size: int = 24
    padding: int = 3
    border_radius: int = 4
    show_border: bool = True
    text_shadow: bool = True
    only_when_focused: bool = False  # Only show overlay when target window is active
    show_mini_hud: bool = False  # Compact floating HUD bar when active

    # Hotkeys
    hotkey_toggle_run: str = "<alt>+t"
    hotkey_toggle_overlay: str = "<alt>+h"
    hotkey_oneshot: str = "<alt>+s"

    def save(self, path: Optional[Path] = None) -> None:
        target = path or CONFIG_PATH
        tmp = target.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(target)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        target = path or CONFIG_PATH
        if not target.exists():
            cfg = cls()
            cfg.save(target)
            return cfg
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            return cls(**filtered)
        except Exception:
            return cls()
