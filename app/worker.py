from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from app.capture import ScreenCapturer, apply_window_crop_preset
from app.config import APP_DIR, AppConfig
from app.ocr_engine import OcrBlock, UnifiedOcrEngine
from app.translator import MultiApiTranslator, TranslationCache
from app.win_utils import (
    find_window_by_title,
    get_foreground_window,
    get_window_client_rect_screen,
    is_window_minimized,
    is_window_valid,
)


def _is_same_scene(
    new_texts: tuple[str, ...], old_texts: Optional[tuple[str, ...]], threshold: float = 0.90
) -> bool:
    """Fast normalized + fuzzy check to ignore minor OCR character jitter."""
    if old_texts is None:
        return False
    if new_texts == old_texts:
        return True
    if len(new_texts) != len(old_texts):
        return False
    for a, b in zip(new_texts, old_texts):
        if a == b:
            continue
        # Typewriter dialogue growing -> genuine change
        if len(a) > len(b) + 2:
            return False
        la, lb = a.lower(), b.lower()
        if la == lb:
            continue
        if SequenceMatcher(None, la, lb, autojunk=False).ratio() < threshold:
            return False
    return True


class TranslationWorker(QThread):
    """Low-latency async pipeline: Cropped BGRA Capture -> Strided Diff -> OCR -> HTTP/2 + Background AI."""

    blocks_ready = pyqtSignal(object, object, int)
    overlay_geometry_changed = pyqtSignal(object, bool)
    status_changed = pyqtSignal(str)
    metrics_updated = pyqtSignal(int, int, int, int, str)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        config: AppConfig,
        cache: TranslationCache,
        target_hwnd: Optional[int] = None,
        one_shot: bool = False,
    ) -> None:
        super().__init__()
        self._config = replace(config)
        self._cache = cache
        self._target_hwnd = target_hwnd
        self._one_shot = one_shot
        self._running = True
        self._force_rescan = True

    def update_config(self, config: AppConfig, target_hwnd: Optional[int] = None) -> None:
        self._config = replace(config)
        if target_hwnd is not None:
            self._target_hwnd = target_hwnd
        self._force_rescan = True

    def trigger_rescan(self) -> None:
        self._force_rescan = True

    def stop(self) -> None:
        self._running = False

    def _resolve_capture_rect(self) -> tuple[Optional[tuple[int, int, int, int]], bool]:
        cfg = self._config
        if cfg.capture_mode == "region":
            x, y, w, h = cfg.region
            return ((int(x), int(y), int(w), int(h)), True) if w > 10 and h > 10 else (None, False)

        hwnd = self._target_hwnd
        if not hwnd or not is_window_valid(hwnd):
            hwnd = find_window_by_title(cfg.target_window_title)
            self._target_hwnd = hwnd

        if not hwnd or not is_window_valid(hwnd) or is_window_minimized(hwnd):
            return None, False

        rect = get_window_client_rect_screen(hwnd)
        if rect is None:
            return None, False

        rect = apply_window_crop_preset(rect, cfg.window_crop_preset)
        if cfg.only_when_focused and get_foreground_window() != hwnd:
            return rect, False
        return rect, True

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            try:
                tb = traceback.format_exc()
                with (APP_DIR / "crash.log").open("a", encoding="utf-8") as fh:
                    fh.write(f"\n--- Worker error ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n{tb}\n")
            except Exception:
                pass
            self.error_occurred.emit(f"Lỗi luồng dịch: {exc}")

    async def _run_async(self) -> None:
        capturer = ScreenCapturer()
        ocr = UnifiedOcrEngine(self._config.ocr_language)
        translator = MultiApiTranslator(self._cache)
        # Pre-warm HTTP/2 TLS asynchronously while OCR initializes on first frame (zero lock contention)
        warmup_task = asyncio.create_task(translator.warmup_async(self._config))
        if self._config.ocr_engine == "rapidocr" and not ocr.is_rapid_ready():
            self.status_changed.emit("Đang nạp mô hình OCR đa font (chỉ 1 lần)...")

        last_rect: Optional[tuple[int, int, int, int]] = None
        last_visible: Optional[bool] = None
        last_texts_sig: Optional[tuple[str, ...]] = None
        empty_streak = 0
        scene_seq = 0
        refine_task: Optional[asyncio.Task] = None

        async def _refine_with_llm(
            expected_seq: int,
            rect: tuple[int, int, int, int],
            blocks_snapshot: list[OcrBlock],
            cfg_snapshot: AppConfig,
            ocr_ms: int,
        ) -> None:
            try:
                t_ai = time.perf_counter()
                translated = await translator.translate_batch_async(
                    [b.text for b in blocks_snapshot], cfg_snapshot
                )
                if not self._running or expected_seq != scene_seq:
                    return
                for b, tr in zip(blocks_snapshot, translated):
                    b.translated = tr
                ai_ms = int((time.perf_counter() - t_ai) * 1000)
                self.blocks_ready.emit(rect, blocks_snapshot, ai_ms)
                model_tag = translator.last_used_model or cfg_snapshot.provider
                self.metrics_updated.emit(ocr_ms, ai_ms, ocr_ms + ai_ms, len(self._cache), f"AI / {model_tag}")
                self.status_changed.emit(
                    f"AI ({model_tag}) đã cập nhật {len(blocks_snapshot)} cụm ({ai_ms} ms)"
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self._running and expected_seq == scene_seq:
                    self.status_changed.emit(f"Giữ bản dịch nhanh (AI bận: {exc})")

        try:
            while self._running:
                t0 = time.perf_counter()
                cfg = self._config

                rect, visible = self._resolve_capture_rect()
                if rect != last_rect or visible != last_visible:
                    self.overlay_geometry_changed.emit(rect, visible)
                    last_rect, last_visible = rect, visible
                    capturer.reset_diff()

                if rect is None or not visible:
                    if self._one_shot:
                        self.error_occurred.emit(
                            "Không tìm thấy cửa sổ game hoặc vùng chọn hợp lệ."
                        )
                        break
                    await asyncio.sleep(0.15)
                    continue

                frame = capturer.grab_raw(rect)
                if frame is None:
                    if self._one_shot:
                        break
                    await asyncio.sleep(0.15)
                    continue

                if not self._force_rescan and not self._one_shot and not capturer.has_changed(
                    frame, rect, threshold=cfg.change_threshold
                ):
                    await self._sleep_remaining(t0, cfg.scan_interval_ms)
                    continue

                self._force_rescan = False

                t_ocr_start = time.perf_counter()
                try:
                    blocks = await ocr.recognize_bgra_async(
                        frame.bgra, frame.width, frame.height, cfg
                    )
                except Exception as exc:
                    self.error_occurred.emit(f"Lỗi OCR: {exc}")
                    if self._one_shot:
                        break
                    await asyncio.sleep(0.3)
                    continue
                ocr_ms = int((time.perf_counter() - t_ocr_start) * 1000)

                texts = [b.text for b in blocks]
                texts_sig = tuple(texts)

                if not self._one_shot and _is_same_scene(texts_sig, last_texts_sig):
                    await self._sleep_remaining(t0, cfg.scan_interval_ms)
                    continue

                if not blocks:
                    empty_streak += 1
                    # Require 2 consecutive empty scans before clearing active subtitles (prevents flash blink)
                    if not self._one_shot and last_texts_sig and empty_streak < 2:
                        await self._sleep_remaining(t0, cfg.scan_interval_ms)
                        continue
                    scene_seq += 1
                    if refine_task is not None and not refine_task.done():
                        refine_task.cancel()
                        refine_task = None
                    last_texts_sig = ()
                    total_ms = int((time.perf_counter() - t0) * 1000)
                    self.blocks_ready.emit(rect, [], total_ms)
                    self.metrics_updated.emit(ocr_ms, 0, total_ms, len(self._cache), "Chờ chữ")
                    self.status_changed.emit("Đang quét (không thấy chữ)")
                    if self._one_shot:
                        break
                    await self._sleep_remaining(t0, cfg.scan_interval_ms)
                    continue

                empty_streak = 0
                scene_seq += 1
                if refine_task is not None and not refine_task.done():
                    refine_task.cancel()
                    refine_task = None

                last_texts_sig = texts_sig

                cached_res, missing_map = translator.check_cache(texts, cfg)
                if not missing_map:
                    for b, tr in zip(blocks, cached_res):
                        b.translated = tr or b.text
                    total_ms = int((time.perf_counter() - t0) * 1000)
                    self.blocks_ready.emit(rect, blocks, total_ms)
                    self.metrics_updated.emit(ocr_ms, 0, total_ms, len(self._cache), "Cache")
                    self.status_changed.emit(f"Đọc từ bộ nhớ đệm: {len(blocks)} cụm ({total_ms} ms)")
                elif cfg.provider == "google_free":
                    t_tr = time.perf_counter()
                    try:
                        translated = await translator.translate_batch_async(texts, cfg)
                    except Exception as exc:
                        self.error_occurred.emit(f"Lỗi Google Translate: {exc}")
                        if self._one_shot:
                            break
                        await asyncio.sleep(0.4)
                        continue
                    tr_ms = int((time.perf_counter() - t_tr) * 1000)
                    for b, tr in zip(blocks, translated):
                        b.translated = tr
                    total_ms = int((time.perf_counter() - t0) * 1000)
                    self.blocks_ready.emit(rect, blocks, total_ms)
                    self.metrics_updated.emit(ocr_ms, tr_ms, total_ms, len(self._cache), "HTTP/2")
                    self.status_changed.emit(f"Đã dịch {len(blocks)} cụm — OCR {ocr_ms} ms, dịch {tr_ms} ms")
                elif cfg.hybrid_fast_preview:
                    t_tr = time.perf_counter()
                    try:
                        fast_tr = await translator.translate_fast_google_async(texts, cfg)
                        tr_ms = int((time.perf_counter() - t_tr) * 1000)
                        for b, tr in zip(blocks, fast_tr):
                            b.translated = tr
                        total_ms = int((time.perf_counter() - t0) * 1000)
                        self.blocks_ready.emit(rect, blocks, total_ms)
                        self.metrics_updated.emit(ocr_ms, tr_ms, total_ms, len(self._cache), "Hybrid")
                        self.status_changed.emit(
                            f"Đã hiện {len(blocks)} cụm ({total_ms} ms), đang cập nhật bằng AI"
                        )
                    except Exception:
                        pass

                    blocks_copy = [replace(b) for b in blocks]
                    cfg_copy = replace(cfg)
                    if self._one_shot:
                        await _refine_with_llm(scene_seq, rect, blocks_copy, cfg_copy, ocr_ms)
                    else:
                        refine_task = asyncio.create_task(
                            _refine_with_llm(scene_seq, rect, blocks_copy, cfg_copy, ocr_ms)
                        )
                else:
                    if any(r is not None for r in cached_res):
                        partial = [replace(b, translated=(tr or b.text)) for b, tr in zip(blocks, cached_res)]
                        self.blocks_ready.emit(rect, partial, 0)
                    t_tr = time.perf_counter()
                    try:
                        translated = await translator.translate_batch_async(texts, cfg)
                    except Exception as exc:
                        self.error_occurred.emit(f"Lỗi dịch ({cfg.provider}): {exc}")
                        if self._one_shot:
                            break
                        await asyncio.sleep(0.6)
                        continue
                    tr_ms = int((time.perf_counter() - t_tr) * 1000)
                    for b, tr in zip(blocks, translated):
                        b.translated = tr
                    total_ms = int((time.perf_counter() - t0) * 1000)
                    self.blocks_ready.emit(rect, blocks, total_ms)
                    model_tag = translator.last_used_model or cfg.provider
                    self.metrics_updated.emit(ocr_ms, tr_ms, total_ms, len(self._cache), f"AI / {model_tag}")
                    self.status_changed.emit(f"AI đã dịch {len(blocks)} cụm — OCR {ocr_ms} ms, AI {tr_ms} ms")

                if self._one_shot:
                    break
                await self._sleep_remaining(t0, cfg.scan_interval_ms)
        finally:
            pending_tasks = [
                t for t in (refine_task, warmup_task) if t is not None and not t.done()
            ]
            for t in pending_tasks:
                t.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            capturer.close()
            await translator.aclose()

    async def _sleep_remaining(self, t0: float, interval_ms: int) -> None:
        remain_s = max(0.02, (interval_ms - (time.perf_counter() - t0) * 1000.0) / 1000.0)
        end_t = time.perf_counter() + remain_s
        while self._running:
            left = end_t - time.perf_counter()
            if left <= 0:
                break
            await asyncio.sleep(min(0.05, left))
