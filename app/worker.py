from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from app.capture import RawFrame, ScreenCapturer, apply_window_crop_preset
from app.config import APP_DIR, AppConfig
from app.ocr_engine import OcrBlock, UnifiedOcrEngine
from app.translator import MultiApiTranslator, TranslationCache, is_short_ui_text
from app.win_utils import (
    find_window_by_title,
    get_foreground_window,
    get_window_client_rect_screen,
    is_window_minimized,
    is_window_valid,
)

try:
    from rapidfuzz import fuzz as _rf_fuzz
except Exception:
    _rf_fuzz = None  # type: ignore

_SENTENCE_ENDINGS = (".", "?", "!", "…", "。", "？", "！", '"', "”", "'", ")")


def _text_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if _rf_fuzz is not None:
        return _rf_fuzz.ratio(a, b) / 100.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _is_same_scene(
    new_texts: tuple[str, ...], old_texts: Optional[tuple[str, ...]], threshold: float = 0.91
) -> bool:
    """Fast C++ rapidfuzz check to ignore minor 1-2 char OCR jitter."""
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
        if _text_similarity(la, lb) < threshold:
            return False
    return True


def _is_typewriter_growing(
    new_texts: tuple[str, ...], prev_texts: Optional[tuple[str, ...]]
) -> bool:
    """True when dialogue is actively typing out (prefix growth without full scene switch)."""
    if not prev_texts or len(new_texts) != len(prev_texts):
        return False
    grew = False
    for a, b in zip(new_texts, prev_texts):
        if a == b:
            continue
        la, lb = a.lower().strip(), b.lower().strip()
        if len(la) > len(lb) + 1:
            # Allow 2-char fuzzy tail tolerance for OCR jitter at the typing cursor
            prefix = lb[:-2] if len(lb) > 4 else lb
            if prefix and la.startswith(prefix):
                grew = True
                continue
        return False
    return grew


def _ends_with_sentence_punct(texts: tuple[str, ...]) -> bool:
    for t in reversed(texts):
        s = t.strip()
        if s:
            return s.endswith(_SENTENCE_ENDINGS)
    return False


class _LatestFrameSlot:
    """1-slot Drop-Oldest buffer: producer always overwrites stale frames so lag never queues up."""

    def __init__(self) -> None:
        self._item: Optional[tuple[tuple[int, int, int, int], RawFrame, float, float]] = None
        self._ready = asyncio.Event()
        self._closed = False

    def put(self, item: tuple[tuple[int, int, int, int], RawFrame, float, float]) -> None:
        self._item = item
        self._ready.set()

    def close(self) -> None:
        self._closed = True
        self._ready.set()

    async def get(self) -> Optional[tuple[tuple[int, int, int, int], RawFrame, float, float]]:
        while True:
            if self._item is not None:
                item = self._item
                self._item = None
                self._ready.clear()
                return item
            if self._closed:
                return None
            await self._ready.wait()


class TranslationWorker(QThread):
    """Low-latency non-blocking pipeline:
    Cropped ROI Capture -> Text-Mask Diff -> 1-Slot Drop-Oldest -> OCR
    -> Smart Typewriter Trigger -> Preemptible Tiered & Streaming Translation.
    """

    blocks_ready = pyqtSignal(object, object, int)
    overlay_geometry_changed = pyqtSignal(object, bool)
    status_changed = pyqtSignal(str)
    metrics_updated = pyqtSignal(int, int, int, int, str)
    # Per-stage profiler: capture_ms, diff_ms, ocr_ms, ttft_ms, total_tr_ms
    stage_profiled = pyqtSignal(float, float, int, int, int)
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
        # Pre-warm HTTP/2 TLS connections asynchronously while OCR initializes on first frame
        warmup_task = asyncio.create_task(translator.warmup_async(self._config))
        if self._config.ocr_engine == "rapidocr" and not ocr.is_rapid_ready():
            self.status_changed.emit("Đang nạp mô hình OCR đa font (chỉ 1 lần)...")

        slot = _LatestFrameSlot()
        scene_seq = 0
        active_translate_task: Optional[asyncio.Task] = None
        debounce_task: Optional[asyncio.Task] = None

        async def _translate_scene(
            expected_seq: int,
            rect: tuple[int, int, int, int],
            blocks: list[OcrBlock],
            cfg: AppConfig,
            cap_ms: float,
            diff_ms: float,
            ocr_ms: int,
        ) -> None:
            texts = [b.text for b in blocks]
            t_start = time.perf_counter()
            try:
                cached_res, missing_map = translator.check_cache(texts, cfg)
                if not missing_map:
                    if not self._running or expected_seq != scene_seq:
                        return
                    for b, tr in zip(blocks, cached_res):
                        b.translated = tr or b.text
                    total_ms = int(cap_ms + diff_ms) + ocr_ms
                    self.blocks_ready.emit(rect, blocks, total_ms)
                    self.stage_profiled.emit(cap_ms, diff_ms, ocr_ms, 0, 0)
                    self.metrics_updated.emit(ocr_ms, 0, total_ms, len(self._cache), "Cache")
                    self.status_changed.emit(
                        f"Cache: {len(blocks)} cụm — chụp {cap_ms:.1f}ms, OCR {ocr_ms}ms"
                    )
                    return

                if cfg.provider == "google_free":
                    t_tr = time.perf_counter()
                    translated = await translator.translate_batch_async(texts, cfg)
                    if not self._running or expected_seq != scene_seq:
                        return
                    tr_ms = int((time.perf_counter() - t_tr) * 1000)
                    for b, tr in zip(blocks, translated):
                        b.translated = tr
                    total_ms = int(cap_ms + diff_ms) + ocr_ms + tr_ms
                    self.blocks_ready.emit(rect, blocks, total_ms)
                    self.stage_profiled.emit(cap_ms, diff_ms, ocr_ms, tr_ms, tr_ms)
                    self.metrics_updated.emit(ocr_ms, tr_ms, total_ms, len(self._cache), "HTTP/2")
                    self.status_changed.emit(
                        f"Đã dịch {len(blocks)} cụm — OCR {ocr_ms}ms, dịch {tr_ms}ms"
                    )
                    return

                # Check if Tiered Routing handles all uncached items on Tier-1 fast HTTP/2
                has_long_llm = (
                    not cfg.tiered_routing
                    or any(not is_short_ui_text(k) for k in missing_map.keys())
                )

                # Hybrid fast preview (~45ms) before LLM finishes, only when LLM is actually needed
                if cfg.hybrid_fast_preview and has_long_llm:
                    t_fast = time.perf_counter()
                    try:
                        fast_tr = await translator.translate_fast_google_async(texts, cfg)
                        if not self._running or expected_seq != scene_seq:
                            return
                        fast_ms = int((time.perf_counter() - t_fast) * 1000)
                        preview_blocks = [replace(b, translated=tr) for b, tr in zip(blocks, fast_tr)]
                        total_fast = int(cap_ms + diff_ms) + ocr_ms + fast_ms
                        self.blocks_ready.emit(rect, preview_blocks, total_fast)
                        self.stage_profiled.emit(cap_ms, diff_ms, ocr_ms, fast_ms, fast_ms)
                        self.metrics_updated.emit(ocr_ms, fast_ms, total_fast, len(self._cache), "Hybrid")
                        self.status_changed.emit(
                            f"Hiện nhanh {len(blocks)} cụm ({fast_ms}ms), AI đang cập nhật..."
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                elif any(r is not None for r in cached_res):
                    partial_cached = [
                        replace(b, translated=(tr or b.text)) for b, tr in zip(blocks, cached_res)
                    ]
                    self.blocks_ready.emit(rect, partial_cached, 0)

                # Stream LLM tokens live onto overlay (TTFT ~150-300ms) + Tier-1 fast UI
                stream_ttft_reported = [False]

                def _on_partial(partial_texts: list[str], ttft_ms: int) -> None:
                    if not self._running or expected_seq != scene_seq:
                        return
                    stream_blocks = [
                        replace(b, translated=tr) for b, tr in zip(blocks, partial_texts)
                    ]
                    self.blocks_ready.emit(rect, stream_blocks, ttft_ms)
                    if ttft_ms > 0 and not stream_ttft_reported[0]:
                        stream_ttft_reported[0] = True
                        self.stage_profiled.emit(cap_ms, diff_ms, ocr_ms, ttft_ms, ttft_ms)
                        self.metrics_updated.emit(
                            ocr_ms, ttft_ms, ocr_ms + ttft_ms, len(self._cache), "Stream"
                        )

                t_ai = time.perf_counter()
                translated = await translator.translate_batch_async(
                    texts,
                    cfg,
                    on_partial=_on_partial if cfg.stream_llm else None,
                )
                if not self._running or expected_seq != scene_seq:
                    return
                ai_ms = int((time.perf_counter() - t_ai) * 1000)
                ttft_ms = translator.last_ttft_ms or ai_ms
                final_blocks = [replace(b, translated=tr) for b, tr in zip(blocks, translated)]
                total_ms = int(cap_ms + diff_ms) + ocr_ms + ai_ms
                self.blocks_ready.emit(rect, final_blocks, ai_ms)
                model_tag = translator.last_used_model or cfg.provider
                self.stage_profiled.emit(cap_ms, diff_ms, ocr_ms, ttft_ms, ai_ms)
                self.metrics_updated.emit(
                    ocr_ms, ai_ms, total_ms, len(self._cache), f"AI / {model_tag}"
                )
                if ttft_ms < ai_ms - 40:
                    self.status_changed.emit(
                        f"AI ({model_tag}): {len(blocks)} cụm — TTFT {ttft_ms}ms / tổng {ai_ms}ms"
                    )
                else:
                    self.status_changed.emit(
                        f"AI ({model_tag}): {len(blocks)} cụm — OCR {ocr_ms}ms, dịch {ai_ms}ms"
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self._running and expected_seq == scene_seq:
                    if cfg.hybrid_fast_preview and cfg.provider != "google_free":
                        self.status_changed.emit(f"Giữ bản dịch nhanh (AI bận: {exc})")
                    else:
                        self.error_occurred.emit(f"Lỗi dịch ({cfg.provider}): {exc}")

        def _cancel_active_translation() -> None:
            nonlocal active_translate_task
            if active_translate_task is not None and not active_translate_task.done():
                active_translate_task.cancel()
                active_translate_task = None

        def _dispatch_translation(
            rect: tuple[int, int, int, int],
            blocks: list[OcrBlock],
            cfg: AppConfig,
            cap_ms: float,
            diff_ms: float,
            ocr_ms: int,
        ) -> asyncio.Task:
            nonlocal scene_seq, active_translate_task
            scene_seq += 1
            _cancel_active_translation()
            blocks_snap = [replace(b) for b in blocks]
            cfg_snap = replace(cfg)
            active_translate_task = asyncio.create_task(
                _translate_scene(scene_seq, rect, blocks_snap, cfg_snap, cap_ms, diff_ms, ocr_ms)
            )
            return active_translate_task

        # One-shot mode: capture 1 frame -> OCR -> translate -> done
        if self._one_shot:
            try:
                cfg = self._config
                rect, visible = self._resolve_capture_rect()
                self.overlay_geometry_changed.emit(rect, visible)
                if rect is None or not visible:
                    self.error_occurred.emit("Không tìm thấy cửa sổ game hoặc vùng chọn hợp lệ.")
                    return
                t_c = time.perf_counter()
                frame = capturer.grab_raw(rect)
                cap_ms = (time.perf_counter() - t_c) * 1000.0
                if frame is None:
                    return
                t_o = time.perf_counter()
                blocks = await ocr.recognize_bgra_async(frame.bgra, frame.width, frame.height, cfg)
                ocr_ms = int((time.perf_counter() - t_o) * 1000)
                if not blocks:
                    self.blocks_ready.emit(rect, [], ocr_ms)
                    self.status_changed.emit("Không phát hiện chữ trong vùng chọn")
                    return
                task = _dispatch_translation(rect, blocks, cfg, cap_ms, 0.0, ocr_ms)
                await task
            finally:
                if not warmup_task.done():
                    warmup_task.cancel()
                    await asyncio.gather(warmup_task, return_exceptions=True)
                capturer.close()
                await translator.aclose()
            return

        # Continuous mode: decoupled producer (Drop-Oldest 1-slot) + consumer (OCR + Typewriter + Preemptible AI)
        async def _capture_loop() -> None:
            last_rect: Optional[tuple[int, int, int, int]] = None
            last_visible: Optional[bool] = None
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
                        await self._sleep_ms(140)
                        continue

                    t_cap = time.perf_counter()
                    frame = capturer.grab_raw(rect)
                    cap_ms = (time.perf_counter() - t_cap) * 1000.0
                    if frame is None:
                        await self._sleep_ms(140)
                        continue

                    t_diff = time.perf_counter()
                    changed = self._force_rescan or capturer.has_changed(
                        frame,
                        rect,
                        threshold=cfg.change_threshold,
                        use_text_mask=cfg.text_mask_diff,
                    )
                    diff_ms = (time.perf_counter() - t_diff) * 1000.0
                    if changed:
                        self._force_rescan = False
                        slot.put((rect, frame, cap_ms, diff_ms))

                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    remain_ms = max(15.0, cfg.scan_interval_ms - elapsed_ms)
                    await self._sleep_ms(remain_ms)
            finally:
                slot.close()

        async def _process_loop() -> None:
            nonlocal scene_seq, debounce_task
            last_committed_sig: Optional[tuple[str, ...]] = None
            last_seen_sig: Optional[tuple[str, ...]] = None
            typewriter_started_at = 0.0
            empty_streak = 0

            def _cancel_debounce() -> None:
                nonlocal debounce_task
                if debounce_task is not None and not debounce_task.done():
                    debounce_task.cancel()
                    debounce_task = None

            while self._running:
                item = await slot.get()
                if item is None or not self._running:
                    break
                rect, frame, cap_ms, diff_ms = item
                cfg = self._config

                t_ocr = time.perf_counter()
                try:
                    blocks = await ocr.recognize_bgra_async(
                        frame.bgra, frame.width, frame.height, cfg
                    )
                except Exception as exc:
                    self.error_occurred.emit(f"Lỗi OCR: {exc}")
                    await self._sleep_ms(250)
                    continue
                ocr_ms = int((time.perf_counter() - t_ocr) * 1000)

                texts_sig = tuple(b.text for b in blocks)
                if _is_same_scene(texts_sig, last_committed_sig):
                    last_seen_sig = texts_sig
                    continue

                if not blocks:
                    empty_streak += 1
                    # 2 consecutive empty frames required to clear subtitles (anti-blink hysteresis)
                    if last_committed_sig and empty_streak < 2:
                        continue
                    _cancel_debounce()
                    _cancel_active_translation()
                    scene_seq += 1
                    last_committed_sig = ()
                    last_seen_sig = ()
                    typewriter_started_at = 0.0
                    total_ms = int(cap_ms + diff_ms) + ocr_ms
                    self.blocks_ready.emit(rect, [], total_ms)
                    self.stage_profiled.emit(cap_ms, diff_ms, ocr_ms, 0, 0)
                    self.metrics_updated.emit(ocr_ms, 0, total_ms, len(self._cache), "Chờ chữ")
                    self.status_changed.emit("Đang quét (không thấy chữ)")
                    continue

                empty_streak = 0
                now = time.perf_counter()

                # Smart Typewriter Trigger:
                # - Ends with . ? ! -> fire immediately (0ms wait)
                # - Actively growing mid-sentence -> short 130ms stability debounce (max cap 420ms)
                # - Full new scene / menu -> fire immediately + preemptively cancel stale request
                growing = _is_typewriter_growing(texts_sig, last_seen_sig)
                last_seen_sig = texts_sig

                if growing and not _ends_with_sentence_punct(texts_sig):
                    if typewriter_started_at == 0.0:
                        typewriter_started_at = now
                    if (now - typewriter_started_at) < 0.42:
                        _cancel_debounce()
                        pending_rect = rect
                        pending_blocks = blocks
                        pending_cfg = replace(cfg)
                        pending_sig = texts_sig
                        pending_cap, pending_diff, pending_ocr = cap_ms, diff_ms, ocr_ms

                        async def _debounced_fire() -> None:
                            nonlocal last_committed_sig, typewriter_started_at
                            try:
                                await asyncio.sleep(0.13)
                                if not self._running:
                                    return
                                last_committed_sig = pending_sig
                                typewriter_started_at = 0.0
                                _dispatch_translation(
                                    pending_rect,
                                    pending_blocks,
                                    pending_cfg,
                                    pending_cap,
                                    pending_diff,
                                    pending_ocr,
                                )
                            except asyncio.CancelledError:
                                return

                        debounce_task = asyncio.create_task(_debounced_fire())
                        continue

                _cancel_debounce()
                typewriter_started_at = 0.0
                last_committed_sig = texts_sig
                _dispatch_translation(rect, blocks, cfg, cap_ms, diff_ms, ocr_ms)

        cap_task = asyncio.create_task(_capture_loop())
        proc_task = asyncio.create_task(_process_loop())
        try:
            await asyncio.gather(cap_task, proc_task)
        finally:
            slot.close()
            pending = [
                t
                for t in (cap_task, proc_task, debounce_task, active_translate_task, warmup_task)
                if t is not None and not t.done()
            ]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            capturer.close()
            await translator.aclose()

    async def _sleep_ms(self, duration_ms: float) -> None:
        end_t = time.perf_counter() + max(0.01, duration_ms / 1000.0)
        while self._running:
            left = end_t - time.perf_counter()
            if left <= 0:
                break
            await asyncio.sleep(min(0.025, left))
