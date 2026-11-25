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
    """Ultra-fast physical-pixel screen capture with strided NumPy frame diff (~0.05 ms)."""

    def __init__(self) -> None:
        self._sct: Optional[mss.MSS] = None
        self._last_thumb: Optional[np.ndarray] = None
        self._last_rect: Optional[tuple[int, int, int, int]] = None

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
        self._last_rect = None

    def grab_raw(self, rect: tuple[int, int, int, int]) -> Optional[RawFrame]:
        """Capture physical screen rectangle (x, y, w, h) directly as BGRA bytes."""
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

    def has_changed(
        self,
        frame: RawFrame,
        rect: tuple[int, int, int, int],
        threshold: float = 1.6,
    ) -> bool:
        """Strided green-channel luma diff directly on BGRA buffer (~0.05 ms, zero PIL copy)."""
        arr = np.frombuffer(frame.bgra, dtype=np.uint8).reshape(
            (frame.height, frame.width, 4)
        )
        sy = max(1, frame.height // 45)
        sx = max(1, frame.width // 80)
        # Green channel (index 1 in BGRA) carries ~59% of perceived luminance -> fast single-channel proxy
        thumb = arr[::sy, ::sx, 1].astype(np.int16)

        if (
            self._last_thumb is None
            or self._last_rect != rect
            or self._last_thumb.shape != thumb.shape
        ):
            self._last_thumb = thumb
            self._last_rect = rect
            return True

        mean_diff = float(np.abs(thumb - self._last_thumb).mean())
        if mean_diff >= threshold:
            self._last_thumb = thumb
            self._last_rect = rect
            return True
        return False
