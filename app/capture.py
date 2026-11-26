from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import mss
import numpy as np


@dataclass(slots=True)
class RawFrame:
    """Zero-copy raw BGRA frame from mss for ultra-fast OCR and diffing."""

    bgra: bytes
    width: int
    height: int


def apply_window_crop_preset(
    rect: tuple[int, int, int, int], preset: str
) -> tuple[int, int, int, int]:
    """Crop a window client rect before BitBlt so capture & OCR only touch dialogue zones."""
    x, y, w, h = rect
    if preset == "bottom":
        ch = max(40, int(h * 0.38))
        return (x, y + (h - ch), w, ch)
    if preset == "center_bottom":
        cw = max(80, int(w * 0.76))
        ch = max(40, int(h * 0.44))
        return (x + (w - cw) // 2, y + (h - ch), cw, ch)
    return rect


class ScreenCapturer:
    """Cropped ROI physical-pixel screen capturer with <0.4ms 3D-resistant Text-Mask Diff."""

    def __init__(self) -> None:
        self._sct: Optional[mss.MSS] = None
        self._last_thumb: Optional[np.ndarray] = None
        self._last_mask: Optional[np.ndarray] = None
        self._last_rect: Optional[tuple[int, int, int, int]] = None
        self._last_mode: Optional[bool] = None

    def _ensure_sct(self) -> mss.MSS:
        if self._sct is None:
            cls = getattr(mss, "MSS", mss.mss)
            self._sct = cls()
        return self._sct

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None

    def reset_diff(self) -> None:
        self._last_thumb = None
        self._last_mask = None
        self._last_rect = None
        self._last_mode = None

    def grab_raw(self, rect: tuple[int, int, int, int]) -> Optional[RawFrame]:
        """Capture ONLY the cropped ROI physical rect (never full screen) directly as BGRA bytes."""
        x, y, w, h = rect
        if w <= 2 or h <= 2:
            return None
        sct = self._ensure_sct()
        monitor = {"left": int(x), "top": int(y), "width": int(w), "height": int(h)}
        try:
            raw = sct.grab(monitor)
        except Exception:
            # Reset GDI DC handle on UAC / resolution switch / fullscreen transition so next tick recovers
            self.close()
            return None
        bgra = raw.bgra if isinstance(raw.bgra, bytes) else bytes(raw.bgra)
        return RawFrame(bgra=bgra, width=raw.width, height=raw.height)

    @staticmethod
    def _extract_text_mask(arr: np.ndarray) -> np.ndarray:
        """Build 1/4-scale binary text-stroke mask (<0.2ms), invariant to moving 3D backgrounds."""
        h, w = arr.shape[:2]
        sy = 4 if h >= 120 else (2 if h >= 40 else 1)
        sx = 4 if w >= 240 else (2 if w >= 80 else 1)
        # Green channel (index 1 in BGRA) carries ~59% of perceived luminance (uint8, zero int16 alloc)
        g = arr[::sy, ::sx, 1]
        if g.shape[0] < 3 or g.shape[1] < 3:
            return g >= 180
        c = g[1:-1, 1:-1]
        up, dn, lt, rt = g[:-2, 1:-1], g[2:, 1:-1], g[1:-1, :-2], g[1:-1, 2:]
        local_min = np.minimum(np.minimum(up, dn), np.minimum(lt, rt))
        local_max = np.maximum(np.maximum(up, dn), np.maximum(lt, rt))
        # Light subtitles on dark/3D bg (ignores smooth bright sky/sun glare and dark ocean waves)
        light_text = (c >= 178) & (local_min <= 142)
        # Dark ink on bright parchment/UI panels (ignores moving dark shadows over 3D ocean)
        dark_text = (c <= 82) & (local_max >= 148)
        return light_text | dark_text

    def has_changed(
        self,
        frame: RawFrame,
        rect: tuple[int, int, int, int],
        threshold: float = 1.6,
        use_text_mask: bool = True,
    ) -> bool:
        """Detect real text changes while ignoring 3D background motion (<0.4ms vectorized NumPy)."""
        arr = np.frombuffer(frame.bgra, dtype=np.uint8).reshape(
            (frame.height, frame.width, 4)
        )
        if self._last_mode != use_text_mask or self._last_rect != rect:
            self._last_thumb = None
            self._last_mask = None
            self._last_mode = use_text_mask
            self._last_rect = rect

        if use_text_mask:
            mask = self._extract_text_mask(arr)
            if self._last_mask is None or self._last_mask.shape != mask.shape:
                self._last_mask = mask
                return True
            # Separable 3x3 dilated symmetric diff absorbs 8-neighbor anti-aliased fringe shimmer
            # over moving 3D backgrounds while instantly catching any new/removed glyph strokes
            prev = self._last_mask
            if mask.shape[0] >= 3 and mask.shape[1] >= 3:
                ph = prev[:, :-2] | prev[:, 1:-1] | prev[:, 2:]
                prev_dil = ph[:-2, :] | ph[1:-1, :] | ph[2:, :]
                ch = mask[:, :-2] | mask[:, 1:-1] | mask[:, 2:]
                curr_dil = ch[:-2, :] | ch[1:-1, :] | ch[2:, :]
                added = int(np.count_nonzero(mask[1:-1, 1:-1] & ~prev_dil))
                removed = int(np.count_nonzero(prev[1:-1, 1:-1] & ~curr_dil))
                diff_px = added + removed
            else:
                diff_px = int(np.count_nonzero(mask ^ prev))
            min_px = max(6, int(mask.size * max(0.3, threshold) * 0.0003))
            if diff_px >= min_px:
                self._last_mask = mask
                return True
            return False

        sy = max(1, frame.height // 45)
        sx = max(1, frame.width // 80)
        thumb = arr[::sy, ::sx, 1].astype(np.int16)
        if self._last_thumb is None or self._last_thumb.shape != thumb.shape:
            self._last_thumb = thumb
            return True
        mean_diff = float(np.abs(thumb - self._last_thumb).mean())
        if mean_diff >= threshold:
            self._last_thumb = thumb
            return True
        return False
