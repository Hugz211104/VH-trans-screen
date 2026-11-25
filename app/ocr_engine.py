from __future__ import annotations

import asyncio
import logging
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

from app.config import AppConfig

# Common 3D game HUD counters / compass / timers / noise that shouldn't trigger translation
_HUD_NOISE_PATTERNS = (
    re.compile(r"^[\d\s.,:/%+\-×x°()\[\]]+$", re.UNICODE),
    re.compile(r"^(?:\d+\s*fps|fps\s*\d+|ping\s*\d+\s*ms|\d+\s*ms)$", re.IGNORECASE),
    re.compile(r"^(?:[NSEW]{1,3}|NE|NW|SE|SW|\d{1,3}°)$"),
    re.compile(r"^\d+(?:\.\d+)?\s*(?:m|km|ft|yd|s|ms|hp|mp|xp|lv|lvl)$", re.IGNORECASE),
)
_HUD_TOKEN_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?(?:fps|ms|m|km|ft|%|°|s)?|fps|ping|[NSEW]{1,2}|[0-9:/.,+-]+)$",
    re.IGNORECASE,
)


def is_hud_or_noise_text(text: str, box_h: float, is_cjk: bool) -> bool:
    """Filter out 3D background artifacts, FPS counters, compasses, and pure numeric HUD."""
    stripped = text.strip()
    if not stripped or box_h < 10.0:
        return True
    for pat in _HUD_NOISE_PATTERNS:
        if pat.match(stripped):
            return True
    if not is_cjk:
        tokens = stripped.split()
        if tokens and all(_HUD_TOKEN_RE.match(tok) for tok in tokens):
            return True
        if sum(1 for ch in stripped if ch.isalpha()) < 2:
            return True
    return False


@dataclass(slots=True)
class OcrBlock:
    """Detected text block (line or merged paragraph) with normalized DPI-independent bounds."""

    text: str
    nx: float
    ny: float
    nw: float
    nh: float
    bg_rgb: tuple[int, int, int] = (16, 18, 24)
    fg_rgb: tuple[int, int, int] = (255, 255, 255)
    translated: str = ""
    line_count: int = 1


@dataclass(slots=True)
class _RawLine:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cy(self) -> float:
        return self.y + self.h * 0.5


def get_available_ocr_languages() -> list[tuple[str, str]]:
    """Return installed Windows OCR languages plus core PP-OCRv5 game languages."""
    seen: set[str] = set()
    langs: list[tuple[str, str]] = []
    try:
        for lang in OcrEngine.available_recognizer_languages:
            langs.append((lang.language_tag, lang.display_name))
            seen.add(lang.language_tag.lower())
    except Exception:
        pass
    for tag, name in [
        ("en-US", "English (United States)"),
        ("ja", "Japanese (PP-OCRv5)"),
        ("zh-Hans-CN", "Chinese Simplified (PP-OCRv5)"),
        ("ko", "Korean (PP-OCRv5)"),
    ]:
        prefix = tag.split("-")[0].lower()
        if not any(s.startswith(prefix) for s in seen):
            langs.append((tag, name))
            seen.add(tag.lower())
    return langs


def _sample_box_colors_bgra(
    bgra_arr: np.ndarray, x: float, y: float, w: float, h: float
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Vectorized NumPy median border sampling directly from BGRA view."""
    ih, iw = bgra_arr.shape[:2]
    x0 = max(0, int(x) - 3)
    y0 = max(0, int(y) - 3)
    x1 = min(iw - 1, int(x + w) + 3)
    y1 = min(ih - 1, int(y + h) + 3)

    step_x = max(1, (x1 - x0) // 8)
    step_y = max(1, (y1 - y0) // 4)

    border = np.concatenate(
        (
            bgra_arr[y0, x0 : x1 + 1 : step_x, :3],
            bgra_arr[y1, x0 : x1 + 1 : step_x, :3],
            bgra_arr[y0 : y1 + 1 : step_y, x0, :3],
            bgra_arr[y0 : y1 + 1 : step_y, x1, :3],
        ),
        axis=0,
    )
    if border.size == 0:
        return (16, 18, 24), (255, 255, 255)

    med_bgr = np.median(border, axis=0)
    bg = (int(med_bgr[2]), int(med_bgr[1]), int(med_bgr[0]))
    luminance = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    fg = (18, 18, 22) if luminance > 155 else (255, 255, 255)
    return bg, fg


def _merge_same_baseline_fragments(lines: list[_RawLine], is_cjk: bool) -> list[_RawLine]:
    """Merge horizontal fragments on the same line (handles slight vertical jitter cleanly)."""
    if len(lines) <= 1:
        return lines
    # Sort left-to-right first so horizontal neighbors always attach in reading order
    ordered = sorted(lines, key=lambda l: (l.x, l.cy))
    merged: list[_RawLine] = []
    sep = "" if is_cjk else " "

    for item in ordered:
        placed = False
        for i, cur in enumerate(merged):
            avg_h = (cur.h + item.h) * 0.5
            if abs(cur.cy - item.cy) > avg_h * 0.45:
                continue
            h_ratio = max(cur.h, item.h) / max(1.0, min(cur.h, item.h))
            if h_ratio > 1.65:
                continue
            gap = item.x - cur.right
            if -avg_h * 0.5 <= gap <= avg_h * 2.4:
                x0 = min(cur.x, item.x)
                y0 = min(cur.y, item.y)
                x1 = max(cur.right, item.right)
                y1 = max(cur.bottom, item.bottom)
                merged[i] = _RawLine(
                    text=f"{cur.text}{sep}{item.text}".strip(),
                    x=x0, y=y0, w=x1 - x0, h=y1 - y0,
                )
                placed = True
                break
        if not placed:
            merged.append(item)
    return merged


def _suppress_overlapping_lines(lines: list[_RawLine], iou_thresh: float = 0.6) -> list[_RawLine]:
    """Drop duplicate or heavily nested OCR boxes on the same text region."""
    if len(lines) <= 1:
        return lines
    # Prefer longer text / cleaner boxes first
    ordered = sorted(lines, key=lambda l: (len(l.text), l.w * l.h), reverse=True)
    kept: list[_RawLine] = []
    for cand in ordered:
        c_area = max(1.0, cand.w * cand.h)
        dup = False
        for k in kept:
            ix = min(cand.right, k.right) - max(cand.x, k.x)
            iy = min(cand.bottom, k.bottom) - max(cand.y, k.y)
            if ix > 0 and iy > 0:
                inter = ix * iy
                min_area = min(c_area, max(1.0, k.w * k.h))
                if inter / min_area >= iou_thresh:
                    dup = True
                    break
        if not dup:
            kept.append(cand)
    return kept


def _merge_lines_into_blocks(
    lines: list[_RawLine], is_cjk: bool
) -> list[tuple[str, float, float, float, float, int]]:
    """Group vertically adjacent lines into dialogue paragraphs using O(1) running bounds."""
    if not lines:
        return []

    lines = _suppress_overlapping_lines(lines)
    lines = _merge_same_baseline_fragments(lines, is_cjk)
    sorted_lines = sorted(lines, key=lambda l: (l.y, l.x))
    sep = "" if is_cjk else " "

    # Each group tracks [lines_list, left, top, right, bottom] for O(1) geometry checks
    groups: list[list] = []

    for line in sorted_lines:
        placed = False
        for g in groups:
            g_lines: list[_RawLine] = g[0]
            last = g_lines[-1]
            v_gap = line.y - last.bottom
            avg_h = (line.h + last.h) * 0.5
            # Tighter vertical gap (0.55x) so separate menu buttons don't merge into giant boxes
            if v_gap < -0.25 * avg_h or v_gap > 0.55 * avg_h:
                continue
            h_ratio = max(line.h, last.h) / max(1.0, min(line.h, last.h))
            if h_ratio > 1.45:
                continue
            g_left, g_right = g[1], g[3]
            overlap = min(g_right, line.right) - max(g_left, line.x)
            if overlap <= 0:
                continue
            min_w = max(1.0, min(g_right - g_left, line.w))
            left_aligned = abs(line.x - g_left) <= avg_h * 1.5
            center_aligned = (
                abs((line.x + line.w * 0.5) - (g_left + g_right) * 0.5) <= avg_h * 2.0
            )
            if overlap / min_w >= 0.35 or left_aligned or center_aligned:
                g_lines.append(line)
                if line.x < g[1]:
                    g[1] = line.x
                if line.y < g[2]:
                    g[2] = line.y
                if line.right > g[3]:
                    g[3] = line.right
                if line.bottom > g[4]:
                    g[4] = line.bottom
                placed = True
                break
        if not placed:
            groups.append([[line], line.x, line.y, line.right, line.bottom])

    return [
        (
            sep.join(l.text.strip() for l in g[0] if l.text.strip()),
            g[1],
            g[2],
            g[3] - g[1],
            g[4] - g[2],
            len(g[0]),
        )
        for g in groups
    ]


def _build_blocks(
    raw_lines: list[_RawLine],
    orig_bgra_arr: np.ndarray,
    work_w: int,
    work_h: int,
    is_cjk: bool,
    merge_lines: bool,
    min_text_length: int,
) -> list[OcrBlock]:
    """Shared block-merging and adaptive color sampling stage for both OCR engines."""
    if not raw_lines:
        return []
    raw_lines = _suppress_overlapping_lines(raw_lines)
    grouped = (
        _merge_lines_into_blocks(raw_lines, is_cjk=is_cjk)
        if merge_lines
        else [(l.text, l.x, l.y, l.w, l.h, 1) for l in raw_lines]
    )
    ih, iw = orig_bgra_arr.shape[:2]
    sx, sy = iw / work_w, ih / work_h

    blocks: list[OcrBlock] = []
    for text, x, y, w, h, line_count in grouped:
        if len(text.strip()) < min_text_length:
            continue
        bg_rgb, fg_rgb = _sample_box_colors_bgra(
            orig_bgra_arr, x * sx, y * sy, w * sx, h * sy
        )
        blocks.append(
            OcrBlock(
                text=text,
                nx=x / work_w,
                ny=y / work_h,
                nw=w / work_w,
                nh=h / work_h,
                bg_rgb=bg_rgb,
                fg_rgb=fg_rgb,
                line_count=line_count,
            )
        )
    return blocks


class WindowsOcrEngine:
    """Fast local Windows 10/11 OCR engine with optional font-sharpening enhancer."""

    def __init__(self, language_tag: str = "en-US") -> None:
        self._language_tag = ""
        self._engine: Optional[OcrEngine] = None
        self.set_language(language_tag)

    def set_language(self, language_tag: str) -> None:
        if language_tag == self._language_tag and self._engine is not None:
            return
        self._language_tag = language_tag
        try:
            lang = Language(language_tag)
            if OcrEngine.is_language_supported(lang):
                self._engine = OcrEngine.try_create_from_language(lang)
                if self._engine is not None:
                    return
        except Exception:
            pass
        self._engine = OcrEngine.try_create_from_user_profile_languages()

    async def recognize_bgra_async(
        self,
        bgra: bytes,
        iw: int,
        ih: int,
        merge_lines: bool = True,
        min_text_length: int = 2,
        max_width: int = 1280,
        filter_hud_noise: bool = True,
        enhance_font: bool = False,
    ) -> list[OcrBlock]:
        if self._engine is None or iw <= 0 or ih <= 0:
            return []

        work_bgra = bgra
        work_w, work_h = iw, ih

        if enhance_font:
            view = Image.frombuffer("RGBA", (iw, ih), bgra, "raw", "BGRA", 0, 1).convert("RGB")
            if ih <= 480 and iw <= 1600:
                work_w = int(round(iw * 1.25))
                work_h = int(round(ih * 1.25))
                view = view.resize((work_w, work_h), Image.Resampling.BICUBIC)
            elif max_width > 0 and iw > max(max_width, 1600):
                work_w = 1600
                work_h = max(16, int(round(ih * (1600 / iw))))
                view = view.resize((work_w, work_h), Image.Resampling.BICUBIC)
            view = ImageEnhance.Contrast(view).enhance(1.18)
            view = view.filter(ImageFilter.UnsharpMask(radius=1.2, percent=155, threshold=2))
            work_bgra = view.convert("RGBA").tobytes("raw", "BGRA")
        elif max_width > 0 and iw > max_width:
            import cv2
            work_w = max_width
            work_h = max(16, int(round(ih * (max_width / iw))))
            bgra_view = np.frombuffer(bgra, dtype=np.uint8).reshape((ih, iw, 4))
            work_bgra = cv2.resize(bgra_view, (work_w, work_h), interpolation=cv2.INTER_LINEAR).tobytes()

        writer = DataWriter()
        writer.write_bytes(work_bgra)
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(),
            BitmapPixelFormat.BGRA8,
            work_w,
            work_h,
            BitmapAlphaMode.IGNORE,
        )
        result = await self._engine.recognize_async(bitmap)

        is_cjk = self._language_tag.lower().startswith(("ja", "zh", "ko"))
        sep = "" if is_cjk else " "
        raw_lines: list[_RawLine] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            text = sep.join(w.text for w in words).strip()
            if len(text) < min_text_length:
                continue
            x0 = min(w.bounding_rect.x for w in words)
            y0 = min(w.bounding_rect.y for w in words)
            x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
            y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
            bh = y1 - y0
            if filter_hud_noise and is_hud_or_noise_text(text, bh, is_cjk):
                continue
            raw_lines.append(_RawLine(text=text, x=x0, y=y0, w=x1 - x0, h=bh))

        orig_arr = np.frombuffer(bgra, dtype=np.uint8).reshape((ih, iw, 4))
        return _build_blocks(
            raw_lines, orig_arr, work_w, work_h, is_cjk, merge_lines, min_text_length
        )


def _find_bundled_model(filename: str) -> Optional[str]:
    """Locate pre-bundled PP-OCRv5 ONNX model in frozen exe or dev site-packages."""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "rapidocr_models" / filename)
        candidates.append(Path(meipass) / "rapidocr" / "models" / filename)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "models" / filename)
    try:
        import rapidocr
        candidates.append(Path(rapidocr.__file__).resolve().parent / "models" / filename)
    except Exception:
        pass
    for p in candidates:
        if p.exists():
            return str(p)
    return None


class RapidOcrGameEngine:
    """Neural PP-OCRv5 ONNX engine built for stylized/pirate/fantasy/cursive game fonts."""

    def __init__(self, language_tag: str = "en-US") -> None:
        self._language_tag = language_tag
        self._limit_side = 960
        self._engine = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._engine is not None

    @staticmethod
    def _lang_bucket(language_tag: str) -> str:
        low = language_tag.lower()
        if low.startswith("ja"):
            return "ja"
        if low.startswith("zh"):
            return "zh"
        if low.startswith("ko"):
            return "ko"
        return "latin"

    def _ensure_engine_locked(self, language_tag: str, max_width: int) -> None:
        bucket = self._lang_bucket(language_tag)
        target_side = 1280 if max_width == 0 or max_width >= 1600 else 960
        if (
            self._engine is not None
            and self._lang_bucket(self._language_tag) == bucket
            and self._limit_side == target_side
        ):
            return

        self._language_tag = language_tag
        self._limit_side = target_side
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)

        from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

        rec_lang_map = {
            "latin": LangRec.LATIN,
            "ja": LangRec.JAPAN,
            "zh": LangRec.CH,
            "ko": LangRec.KOREAN,
        }
        params: dict = {
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.lang_type": LangDet.CH,
            "Det.model_type": ModelType.MOBILE,
            "Det.limit_type": "max",
            "Det.limit_side_len": target_side,
            "Det.unclip_ratio": 1.25,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": rec_lang_map[bucket],
            "Rec.model_type": ModelType.MOBILE,
            "Rec.rec_batch_num": 8,
            "Global.use_cls": False,
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": 4,
        }

        det_path = _find_bundled_model("ch_PP-OCRv5_det_mobile.onnx")
        if det_path:
            params["Det.model_path"] = det_path
        if bucket == "latin":
            rec_path = _find_bundled_model("latin_PP-OCRv5_rec_mobile.onnx")
            if rec_path:
                params["Rec.model_path"] = rec_path

        self._engine = RapidOCR(params=params)

    def _ensure_engine(self, language_tag: str, max_width: int) -> None:
        with self._lock:
            self._ensure_engine_locked(language_tag, max_width)

    def _recognize_sync(
        self,
        bgra: bytes,
        iw: int,
        ih: int,
        language_tag: str,
        merge_lines: bool,
        min_text_length: int,
        max_width: int,
        filter_hud_noise: bool,
        min_confidence: float,
    ) -> list[OcrBlock]:
        import cv2

        bgra_arr = np.frombuffer(bgra, dtype=np.uint8).reshape((ih, iw, 4))

        with self._lock:
            self._ensure_engine_locked(language_tag, max_width)
            if self._engine is None:
                return []

            work_arr = bgra_arr
            work_w, work_h = iw, ih
            max_dim = max(iw, ih)
            if max_dim > self._limit_side:
                scale = self._limit_side / max_dim
                work_w = max(16, int(round(iw * scale)))
                work_h = max(16, int(round(ih * scale)))
                work_arr = cv2.resize(bgra_arr, (work_w, work_h), interpolation=cv2.INTER_LINEAR)

            # SIMD-accelerated OpenCV BGRA -> BGR conversion on compact frame
            bgr = cv2.cvtColor(work_arr, cv2.COLOR_BGRA2BGR)
            out = self._engine(bgr)

        if out is None or out.boxes is None or not out.txts:
            return []

        is_cjk = self._lang_bucket(language_tag) in ("ja", "zh", "ko")
        raw_lines: list[_RawLine] = []
        for box, txt, score in zip(out.boxes, out.txts, out.scores):
            if float(score) < min_confidence:
                continue
            text = str(txt).strip()
            if len(text) < min_text_length:
                continue
            xs = box[:, 0]
            ys = box[:, 1]
            x0, x1 = max(0.0, float(xs.min())), min(float(work_w), float(xs.max()))
            y0, y1 = max(0.0, float(ys.min())), min(float(work_h), float(ys.max()))
            bw, bh = x1 - x0, y1 - y0
            if bw <= 2 or bh <= 2:
                continue
            if filter_hud_noise and is_hud_or_noise_text(text, bh, is_cjk):
                continue
            raw_lines.append(_RawLine(text=text, x=x0, y=y0, w=bw, h=bh))

        return _build_blocks(
            raw_lines, bgra_arr, work_w, work_h, is_cjk, merge_lines, min_text_length
        )

    async def recognize_bgra_async(
        self,
        bgra: bytes,
        iw: int,
        ih: int,
        language_tag: str = "en-US",
        merge_lines: bool = True,
        min_text_length: int = 2,
        max_width: int = 1280,
        filter_hud_noise: bool = True,
        min_confidence: float = 0.55,
    ) -> list[OcrBlock]:
        return await asyncio.to_thread(
            self._recognize_sync,
            bgra,
            iw,
            ih,
            language_tag,
            merge_lines,
            min_text_length,
            max_width,
            filter_hud_noise,
            min_confidence,
        )


_SHARED_RAPID_LOCK = threading.Lock()
_SHARED_RAPID_ENGINE: Optional[RapidOcrGameEngine] = None


def _get_shared_rapid(language_tag: str) -> RapidOcrGameEngine:
    """Reuse loaded PP-OCRv5 ONNX session across Start/Stop and one-shot cycles (~0 ms restart)."""
    global _SHARED_RAPID_ENGINE
    with _SHARED_RAPID_LOCK:
        if _SHARED_RAPID_ENGINE is None:
            _SHARED_RAPID_ENGINE = RapidOcrGameEngine(language_tag)
        return _SHARED_RAPID_ENGINE


class UnifiedOcrEngine:
    """Lazy-initialized facade supporting PP-OCRv5, Windows OCR Enhanced, and Windows OCR Fast."""

    def __init__(self, language_tag: str = "en-US") -> None:
        self._default_lang = language_tag
        self._win: Optional[WindowsOcrEngine] = None

    def _get_win(self, language_tag: str) -> WindowsOcrEngine:
        if self._win is None:
            self._win = WindowsOcrEngine(language_tag)
        else:
            self._win.set_language(language_tag)
        return self._win

    def _get_rapid(self, language_tag: str) -> RapidOcrGameEngine:
        return _get_shared_rapid(language_tag)

    def is_rapid_ready(self) -> bool:
        return _SHARED_RAPID_ENGINE is not None and _SHARED_RAPID_ENGINE.is_loaded

    async def warmup_async(self, config: AppConfig) -> None:
        """Pre-load ONNX or WinRT OCR session on worker startup so first subtitle has zero init lag."""
        try:
            if config.ocr_engine == "rapidocr":
                rapid = self._get_rapid(config.ocr_language)
                await asyncio.to_thread(
                    rapid._ensure_engine, config.ocr_language, config.ocr_max_width
                )
            else:
                self._get_win(config.ocr_language)
        except Exception:
            pass

    async def recognize_bgra_async(
        self,
        bgra: bytes,
        iw: int,
        ih: int,
        config: AppConfig,
    ) -> list[OcrBlock]:
        if config.ocr_engine == "rapidocr":
            try:
                return await self._get_rapid(config.ocr_language).recognize_bgra_async(
                    bgra,
                    iw,
                    ih,
                    language_tag=config.ocr_language,
                    merge_lines=config.merge_lines,
                    min_text_length=config.min_text_length,
                    max_width=config.ocr_max_width,
                    filter_hud_noise=config.filter_hud_noise,
                    min_confidence=config.ocr_min_confidence,
                )
            except Exception:
                pass

        return await self._get_win(config.ocr_language).recognize_bgra_async(
            bgra,
            iw,
            ih,
            merge_lines=config.merge_lines,
            min_text_length=config.min_text_length,
            max_width=config.ocr_max_width,
            filter_hud_noise=config.filter_hud_noise,
            enhance_font=(config.ocr_engine == "windows_enhanced"),
        )
