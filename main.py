from __future__ import annotations

import ctypes
import sys
import threading
import time
import traceback

from PyQt6.QtWidgets import QApplication

from app.config import APP_DIR
from app.win_utils import acquire_single_instance

_INSTANCE_MUTEX: int | None = None


def _log_exception(prefix: str, exc_type, exc_value, exc_tb) -> None:
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log_path = APP_DIR / "crash.log"
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n--- {prefix} ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n{tb}\n")
    except Exception:
        pass


def _install_exception_hooks() -> None:
    """Prevent PyQt6 from silently aborting on non-fatal Python exceptions and log full tracebacks."""
    def _sys_hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        _log_exception("Unhandled Exception", exc_type, exc_value, exc_tb)

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        _log_exception(f"Thread Error ({args.thread})", args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def _close_splash() -> None:
    """Close PyInstaller native bootloader splash screen once GUI is ready."""
    try:
        import pyi_splash  # type: ignore
        pyi_splash.close()
    except Exception:
        pass


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def _report_fatal(exc: BaseException) -> None:
    _close_splash()
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_path = APP_DIR / "crash.log"
    try:
        log_path.write_text(tb, encoding="utf-8")
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Không thể khởi động VH Translator:\n\n{exc}\n\nChi tiết lỗi đã lưu tại:\n{log_path}",
            "VH Translator — Lỗi khởi động",
            0x10,
        )
    except Exception:
        pass


def _run_self_test() -> int:
    """Headless sanity check for frozen .exe builds (PP-OCRv5 + Translation + Overlay)."""
    import asyncio
    from PIL import Image, ImageDraw, ImageFont
    from app.config import AppConfig
    from app.ocr_engine import UnifiedOcrEngine
    from app.translator import MultiApiTranslator, TranslationCache
    from app.ui.main_window import MainWindow

    img = Image.new("RGB", (760, 180), (24, 20, 18))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("palab.ttf", 28)
    except Exception:
        try:
            font = ImageFont.truetype("segoeui.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
    draw.text((30, 60), "Press Start to continue", fill=(246, 236, 206), font=font)
    bgra = img.convert("RGBA").tobytes("raw", "BGRA")

    cfg = AppConfig(provider="google_free", target_language="vi", use_cache=False, ocr_engine="rapidocr")

    async def _run_pipeline():
        ocr = UnifiedOcrEngine("en-US")
        blocks = await ocr.recognize_bgra_async(bgra, img.width, img.height, cfg)
        assert blocks, "PP-OCRv5 returned no blocks"
        tr = MultiApiTranslator(TranslationCache())
        try:
            translated = await tr.translate_batch_async([b.text for b in blocks], cfg)
        finally:
            await tr.aclose()
        assert translated and translated[0] != blocks[0].text, f"Translation failed: {translated}"
        blocks[0].translated = translated[0]
        return blocks

    blocks = asyncio.run(_run_pipeline())

    app = QApplication(sys.argv)
    window = MainWindow()
    window.overlay.set_blocks((100, 100, 760, 180), blocks)
    app.processEvents()
    affinity_ok = window.overlay._affinity_applied

    # Verify live TranslationWorker start -> capture -> OCR -> translate -> overlay -> stop cycle
    from PyQt6.QtCore import QTimer
    from app.capture import RawFrame, ScreenCapturer

    worker_errors: list[str] = []
    worker_blocks: list[int] = []
    orig_grab = ScreenCapturer.grab_raw
    test_frame = RawFrame(bgra=bgra, width=img.width, height=img.height)
    ScreenCapturer.grab_raw = lambda _self, _rect: test_frame  # type: ignore[method-assign]
    window.config.save = lambda *a, **k: None  # type: ignore[method-assign]
    window.btn_mode_region.setChecked(True)
    window.config.capture_mode = "region"
    window.config.region = [0, 0, img.width, img.height]
    window.start_translation()
    if window.worker is not None:
        window.worker.error_occurred.connect(worker_errors.append)
        window.worker.blocks_ready.connect(lambda _r, b, _ms: worker_blocks.append(len(b)))
    QTimer.singleShot(3000, lambda: (window.stop_translation(), window._quit_app()))
    app.exec()
    ScreenCapturer.grab_raw = orig_grab  # type: ignore[method-assign]

    assert not worker_errors, f"Live worker reported errors: {worker_errors}"
    assert worker_blocks and max(worker_blocks) >= 1, f"Live worker emitted no blocks: {worker_blocks}"

    report = (
        f"SELF_TEST_OK\n"
        f"APP_DIR={APP_DIR}\n"
        f"OCR_ENGINE=PP-OCRv5\n"
        f"OCR={blocks[0].text}\n"
        f"TRANSLATED={blocks[0].translated}\n"
        f"WDA_EXCLUDEFROMCAPTURE={affinity_ok}\n"
        f"LIVE_WORKER_OK=True\n"
        f"LIVE_WORKER_BLOCKS={worker_blocks}\n"
    )
    (APP_DIR / "selftest_result.txt").write_text(report, encoding="utf-8")
    return 0


def main() -> int:
    global _INSTANCE_MUTEX
    _enable_dpi_awareness()
    _install_exception_hooks()
    if "--self-test" in sys.argv:
        _close_splash()
        return _run_self_test()

    _INSTANCE_MUTEX = acquire_single_instance()
    if _INSTANCE_MUTEX is None:
        _close_splash()
        return 0

    try:
        from app.ui.main_window import MainWindow

        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        window = MainWindow()
        window.show()
        window.raise_()
        window.activateWindow()
        _close_splash()
        return app.exec()
    except Exception as exc:
        _report_fatal(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
