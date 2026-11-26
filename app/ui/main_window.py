from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.hotkeys import GlobalHotkeyManager
from app.ocr_engine import OcrBlock, get_available_ocr_languages
from app.translator import MultiApiTranslator, TranslationCache, list_gemini_models_async
from app.ui.icons import create_app_icon, get_icon
from app.ui.mini_hud import MiniHudBar
from app.ui.overlay import TranslationOverlay
from app.ui.region_selector import RegionSelector
from app.ui.tokens import (
    ACCENT,
    ACCENT_BORDER,
    ACCENT_DIM,
    BG_HOVER,
    BG_PAGE,
    BG_RAISED,
    BG_SIDEBAR,
    BG_SURFACE,
    BORDER_HAIRLINE,
    BORDER_SUBTLE,
    CTA_BG,
    CTA_BG_HOVER,
    CTA_TEXT,
    FONT_BODY,
    FONT_DISPLAY,
    FONT_MONO,
    NAV_ROW_H,
    RADIUS_CONTROL,
    RADIUS_INSET,
    RADIUS_SURFACE,
    RADIUS_TIGHT,
    SEMANTIC_DANGER,
    SEMANTIC_DANGER_HOVER,
    SEMANTIC_OK,
    SIDEBAR_W,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from app.ui.widgets import EngineOptionRow, LiveOverlayPreview, ToggleSwitch
from app.win_utils import list_visible_windows
from app.worker import TranslationWorker

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG_PAGE};
    color: {TEXT_PRIMARY};
    font-family: "{FONT_BODY}", "Segoe UI";
    font-size: 13px;
    font-weight: 400;
}}
QLabel {{
    background-color: transparent;
}}
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_SUBTLE};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #484852;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* Sidebar */
QFrame#sidebar {{
    background-color: {BG_SIDEBAR};
    border-right: 1px solid {BORDER_HAIRLINE};
}}
QPushButton.navBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS_CONTROL}px;
    padding: 0px 12px;
    text-align: left;
    color: {TEXT_SECONDARY};
    font-size: 13px;
    font-weight: 400;
}}
QPushButton.navBtn:hover {{
    background-color: #18181b;
    color: {TEXT_PRIMARY};
}}
QPushButton.navBtn:checked {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER_HAIRLINE};
    color: {TEXT_PRIMARY};
    font-weight: 500;
}}

/* Surface cards (12px outer -> 8px inset/controls) */
QFrame.card {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: {RADIUS_SURFACE}px;
}}
QFrame.insetCard {{
    background-color: {BG_PAGE};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: {RADIUS_INSET}px;
}}
QFrame#telemetryInset {{
    background-color: {BG_PAGE};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: {RADIUS_INSET}px;
}}

/* Typography roles */
QLabel#pageTitle {{
    font-family: "{FONT_DISPLAY}", "Segoe UI";
    font-size: 20px;
    font-weight: 500;
    color: {TEXT_PRIMARY};
}}
QLabel#statusLine {{
    font-size: 12px;
    font-weight: 400;
    color: {TEXT_SECONDARY};
}}
QLabel.sectionTitle {{
    font-family: "{FONT_DISPLAY}", "Segoe UI";
    font-size: 14px;
    font-weight: 500;
    color: {TEXT_PRIMARY};
}}
QLabel.caption {{
    font-size: 12px;
    font-weight: 400;
    color: {TEXT_MUTED};
}}
QLabel.monoVal {{
    font-family: "{FONT_MONO}", "Consolas";
    font-size: 12px;
    font-weight: 500;
    color: {TEXT_PRIMARY};
}}
QLabel.monoAccent {{
    font-family: "{FONT_MONO}", "Consolas";
    font-size: 12px;
    font-weight: 500;
    color: {ACCENT};
}}
QLabel.keycap {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER_SUBTLE};
    border-bottom: 2px solid #121215;
    border-radius: {RADIUS_TIGHT}px;
    padding: 3px 8px;
    color: {TEXT_PRIMARY};
    font-family: "{FONT_MONO}", "Consolas";
    font-size: 12px;
    font-weight: 500;
}}

/* Inputs */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
    background-color: {BG_PAGE};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px 12px;
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_DIM};
    selection-color: {TEXT_PRIMARY};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_SUBTLE};
    selection-background-color: {BG_RAISED};
    selection-color: {TEXT_PRIMARY};
    color: {TEXT_PRIMARY};
    outline: none;
    padding: 4px;
}}

/* Buttons */
QPushButton {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER_SUBTLE};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px 14px;
    color: {TEXT_PRIMARY};
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: #42424a;
}}
QPushButton:pressed {{
    background-color: #1e1e22;
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {BG_SURFACE};
    border-color: {BORDER_HAIRLINE};
}}

/* Primary CTA on dark: solid off-white + dark graphite ink */
QPushButton#primaryBtn {{
    background-color: {CTA_BG};
    border: 1px solid {CTA_BG};
    color: {CTA_TEXT};
    font-weight: 600;
    padding: 0px 16px;
    border-radius: {RADIUS_CONTROL}px;
}}
QPushButton#primaryBtn:hover {{
    background-color: {CTA_BG_HOVER};
    border-color: {CTA_BG_HOVER};
}}
QPushButton#primaryBtn:pressed {{
    background-color: #d6d6da;
}}

/* Active Stop CTA: restrained semantic red */
QPushButton#stopBtn {{
    background-color: {SEMANTIC_DANGER};
    border: 1px solid {SEMANTIC_DANGER};
    color: #ffffff;
    font-weight: 600;
    padding: 0px 16px;
    border-radius: {RADIUS_CONTROL}px;
}}
QPushButton#stopBtn:hover {{
    background-color: {SEMANTIC_DANGER_HOVER};
    border-color: {SEMANTIC_DANGER_HOVER};
}}

/* Quiet icon-only toolbar buttons */
QPushButton.iconBtn {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: {RADIUS_CONTROL}px;
    padding: 0px;
}}
QPushButton.iconBtn:hover {{
    background-color: {BG_RAISED};
    border-color: {BORDER_SUBTLE};
}}

/* Segmented options */
QPushButton.segBtn {{
    background-color: {BG_PAGE};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: {RADIUS_CONTROL}px;
    padding: 7px 12px;
    color: {TEXT_SECONDARY};
    font-size: 13px;
    font-weight: 400;
}}
QPushButton.segBtn:hover {{
    background-color: #1c1c20;
    color: {TEXT_PRIMARY};
}}
QPushButton.segBtn:checked {{
    background-color: {ACCENT_DIM};
    border: 1px solid {ACCENT_BORDER};
    color: {ACCENT};
    font-weight: 500;
}}

/* Compact preset chips (6px tight radius) */
QPushButton.chipBtn {{
    background-color: {BG_PAGE};
    border: 1px solid {BORDER_HAIRLINE};
    border-radius: {RADIUS_TIGHT}px;
    padding: 4px 10px;
    color: {TEXT_SECONDARY};
    font-size: 12px;
    font-weight: 400;
}}
QPushButton.chipBtn:hover {{
    background-color: {BG_RAISED};
    border-color: {BORDER_SUBTLE};
    color: {TEXT_PRIMARY};
}}

/* Sliders */
QSlider::groove:horizontal {{
    height: 4px;
    background: {BG_RAISED};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT_PRIMARY};
    border: 1px solid {BORDER_SUBTLE};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    border-color: {ACCENT};
}}
"""


def _make_card(title: str = "", subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("class", "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(20, 20, 20, 20)
    lay.setSpacing(12)
    if title:
        lbl_t = QLabel(title)
        lbl_t.setProperty("class", "sectionTitle")
        lay.addWidget(lbl_t)
    if subtitle:
        lbl_s = QLabel(subtitle)
        lbl_s.setProperty("class", "caption")
        lbl_s.setWordWrap(True)
        lay.addWidget(lbl_s)
    return frame, lay


class GeminiModelsThread(QThread):
    finished_signal = pyqtSignal(bool, object)

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key

    def run(self) -> None:
        try:
            models = asyncio.run(list_gemini_models_async(self._api_key))
            self.finished_signal.emit(True, models)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class ApiTestThread(QThread):
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        async def _run() -> tuple[str, str, int]:
            translator = MultiApiTranslator(TranslationCache())
            try:
                sample = ["Press Start to begin your adventure", "Inventory"]
                test_cfg = replace(self._config, use_cache=False)
                t0 = time.perf_counter()
                result = await translator.translate_batch_async(sample, test_cfg)
                ms = int((time.perf_counter() - t0) * 1000)
                return result[0], translator.last_used_model, ms
            finally:
                await translator.aclose()

        try:
            translated_0, used_model, ms = asyncio.run(_run())
            meta = f"Độ trễ: {ms} ms"
            if self._config.provider == "gemini" and used_model:
                if used_model != self._config.gemini_model:
                    meta += f" — {self._config.gemini_model} đang bận, tự chuyển sang {used_model}"
                else:
                    meta += f" — {used_model}"
            elif self._config.provider == "openai" and used_model:
                meta += f" — {used_model}"
            self.finished_signal.emit(
                True,
                f'"Press Start to begin your adventure" → "{translated_0}"\n{meta}',
            )
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class MainWindow(QMainWindow):
    _PAGE_TITLES = [
        "Điều khiển và tốc độ",
        "API và mô hình dịch",
        "Giao diện lớp phủ",
        "Bộ lọc và phím tắt",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = AppConfig.load()
        self.cache = TranslationCache()
        self.overlay = TranslationOverlay(self.config)
        self.mini_hud = MiniHudBar()
        self.worker: Optional[TranslationWorker] = None
        self.oneshot_worker: Optional[TranslationWorker] = None
        self.region_selector: Optional[RegionSelector] = None
        self._api_test_thread: Optional[ApiTestThread] = None
        self._gemini_models_thread: Optional[GeminiModelsThread] = None
        self._retiring_threads: list[QThread] = []
        self._windows_list: list[tuple[int, str]] = []
        self._hotkey_listener: Optional[GlobalHotkeyManager] = None
        self._quitting = False
        self._loading_ui = True
        self._selected_provider = self.config.provider
        self._nav_meta: list[tuple[QPushButton, str]] = []

        self.setWindowTitle("VH Translator")
        self.setWindowIcon(create_app_icon(64))
        self.setMinimumSize(880, 600)
        self.resize(960, 680)
        self.setStyleSheet(STYLESHEET)

        self._wire_mini_hud()
        self._build_ui()
        self._load_config_to_ui()
        self._loading_ui = False
        self._refresh_windows()
        self._setup_tray()
        self._setup_hotkeys()

    def _wire_mini_hud(self) -> None:
        self.mini_hud.toggle_run_clicked.connect(self.toggle_translation)
        self.mini_hud.oneshot_clicked.connect(self.run_one_shot)
        self.mini_hud.toggle_overlay_clicked.connect(self.toggle_overlay_visibility)
        self.mini_hud.restore_clicked.connect(self._restore_from_tray)

    # ------------------------------------------------------------------ Shell
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._build_sidebar())

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(24, 20, 24, 20)
        right_lay.setSpacing(16)

        # Quiet header: page title on left, 1 primary + 1 secondary + compact tools on right
        header = QHBoxLayout()
        header.setSpacing(10)

        self.lbl_page_title = QLabel(self._PAGE_TITLES[0])
        self.lbl_page_title.setObjectName("pageTitle")
        header.addWidget(self.lbl_page_title, 1)

        self.btn_mini_hud = QPushButton()
        self.btn_mini_hud.setProperty("class", "iconBtn")
        self.btn_mini_hud.setFixedSize(36, 36)
        self.btn_mini_hud.setIcon(get_icon("pin", TEXT_SECONDARY, 16))
        self.btn_mini_hud.setIconSize(QSize(16, 16))
        self.btn_mini_hud.setToolTip("Bật thanh nổi gọn trên màn hình game")
        self.btn_mini_hud.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mini_hud.clicked.connect(self._toggle_mini_hud)
        header.addWidget(self.btn_mini_hud)

        self.btn_toggle_overlay = QPushButton()
        self.btn_toggle_overlay.setProperty("class", "iconBtn")
        self.btn_toggle_overlay.setFixedSize(36, 36)
        self.btn_toggle_overlay.setIcon(get_icon("eye", TEXT_SECONDARY, 16))
        self.btn_toggle_overlay.setIconSize(QSize(16, 16))
        self.btn_toggle_overlay.setToolTip("Ẩn hoặc hiện lớp phủ (Alt+H)")
        self.btn_toggle_overlay.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_toggle_overlay.clicked.connect(self.toggle_overlay_visibility)
        header.addWidget(self.btn_toggle_overlay)

        self.btn_oneshot = QPushButton("  Dịch một lần")
        self.btn_oneshot.setFixedHeight(36)
        self.btn_oneshot.setIcon(get_icon("zap", TEXT_SECONDARY, 16))
        self.btn_oneshot.setIconSize(QSize(16, 16))
        self.btn_oneshot.setToolTip("Quét và dịch tức thì một lần (Alt+S)")
        self.btn_oneshot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_oneshot.clicked.connect(self.run_one_shot)
        header.addWidget(self.btn_oneshot)

        self.btn_start = QPushButton("  Bắt đầu dịch")
        self.btn_start.setObjectName("primaryBtn")
        self.btn_start.setFixedHeight(36)
        self.btn_start.setIcon(get_icon("play", CTA_TEXT, 15))
        self.btn_start.setIconSize(QSize(15, 15))
        self.btn_start.setToolTip("Bắt đầu hoặc dừng dịch tự động (Alt+T)")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.toggle_translation)
        header.addWidget(self.btn_start)

        right_lay.addLayout(header)

        # Full-width quiet status line (never clips beside action buttons)
        self.lbl_status = QLabel("Chọn cửa sổ game chế độ Borderless hoặc Windowed để bắt đầu")
        self.lbl_status.setObjectName("statusLine")
        self.lbl_status.setWordWrap(True)
        right_lay.addWidget(self.lbl_status)

        # Quiet hairline divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {BORDER_HAIRLINE};")
        right_lay.addWidget(divider)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._wrap_scroll(self._build_speed_page()))
        self.pages.addWidget(self._wrap_scroll(self._build_api_page()))
        self.pages.addWidget(self._wrap_scroll(self._build_overlay_page()))
        self.pages.addWidget(self._wrap_scroll(self._build_system_page()))
        right_lay.addWidget(self.pages, 1)

        shell.addWidget(right, 1)

    def _wrap_scroll(self, content: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(content)
        return area

    def _build_sidebar(self) -> QFrame:
        sb = QFrame()
        sb.setObjectName("sidebar")
        sb.setFixedWidth(SIDEBAR_W)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(16, 20, 16, 16)
        lay.setSpacing(4)

        # Brand lockup: flat vector mark + wordmark in display face (zero gradient, zero hype badge)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 0, 4, 0)
        brand_row.setSpacing(10)

        mark = QLabel()
        mark.setPixmap(create_app_icon(28).pixmap(28, 28))
        brand_row.addWidget(mark)

        lbl_brand = QLabel("VH Translator")
        lbl_brand.setStyleSheet(
            f"font-family: '{FONT_DISPLAY}', 'Segoe UI'; font-size: 16px; "
            f"font-weight: 600; color: {TEXT_PRIMARY};"
        )
        brand_row.addWidget(lbl_brand)
        brand_row.addStretch()
        lay.addLayout(brand_row)

        lay.addSpacing(24)

        # Navigation rail (strict 36px rows, 4px gap, 16px vector icons in one aligned column)
        nav_label = QLabel("Không gian làm việc")
        nav_label.setStyleSheet(
            f"font-size: 12px; color: {TEXT_MUTED}; padding: 0px 8px 4px 8px;"
        )
        lay.addWidget(nav_label)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        nav_items = [
            ("Điều khiển và tốc độ", "gauge", 0),
            ("API và mô hình", "key", 1),
            ("Giao diện lớp phủ", "layers", 2),
            ("Bộ lọc và phím tắt", "shield", 3),
        ]
        for label, icon_name, idx in nav_items:
            btn = QPushButton(f"  {label}")
            btn.setCheckable(True)
            btn.setFixedHeight(NAV_ROW_H)
            btn.setProperty("class", "navBtn")
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked, i=idx: self._switch_page(i))
            self._nav_group.addButton(btn, idx)
            self._nav_meta.append((btn, icon_name))
            lay.addWidget(btn)

        self._nav_group.button(0).setChecked(True)
        self._sync_nav_icons(0)

        lay.addStretch()

        # Calibrated Telemetry Inset (tabular mono figures, single live indicator)
        tele = QFrame()
        tele.setObjectName("telemetryInset")
        t_lay = QVBoxLayout(tele)
        t_lay.setContentsMargins(12, 12, 12, 12)
        t_lay.setSpacing(8)

        st_row = QHBoxLayout()
        st_row.setSpacing(8)
        self.lbl_state_dot = QLabel()
        self.lbl_state_dot.setFixedSize(8, 8)
        self.lbl_state_dot.setStyleSheet(f"background-color: {TEXT_MUTED}; border-radius: 4px;")
        self.lbl_state_txt = QLabel("Đang nghỉ")
        self.lbl_state_txt.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {TEXT_PRIMARY};")
        self.lbl_m_mode = QLabel("Chưa chạy")
        self.lbl_m_mode.setStyleSheet(f"font-family: '{FONT_MONO}'; font-size: 11px; color: {TEXT_MUTED};")
        st_row.addWidget(self.lbl_state_dot)
        st_row.addWidget(self.lbl_state_txt)
        st_row.addStretch()
        st_row.addWidget(self.lbl_m_mode)
        t_lay.addLayout(st_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.lbl_m_cap = QLabel("-- ms")
        self.lbl_m_ocr = QLabel("-- ms")
        self.lbl_m_tr = QLabel("-- ms")
        self.lbl_m_cache = QLabel(str(len(self.cache)))
        self.lbl_m_cap.setProperty("class", "monoVal")
        self.lbl_m_ocr.setProperty("class", "monoVal")
        self.lbl_m_tr.setProperty("class", "monoAccent")
        self.lbl_m_cache.setProperty("class", "monoVal")
        for row_i, (k_text, val_lbl) in enumerate([
            ("Chụp / So", self.lbl_m_cap),
            ("OCR", self.lbl_m_ocr),
            ("Dịch", self.lbl_m_tr),
            ("Bộ nhớ đệm", self.lbl_m_cache),
        ]):
            kl = QLabel(k_text)
            kl.setProperty("class", "caption")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(kl, row_i, 0)
            grid.addWidget(val_lbl, row_i, 1)
        t_lay.addLayout(grid)

        lay.addWidget(tele)

        lay.addSpacing(4)
        lbl_credit = QLabel("made by Hưng")
        lbl_credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_credit.setStyleSheet(
            f"font-family: '{FONT_BODY}', 'Segoe UI'; font-size: 11px; "
            f"font-weight: 400; color: {TEXT_MUTED}; padding: 2px 0px;"
        )
        lay.addWidget(lbl_credit)
        return sb

    def _switch_page(self, idx: int) -> None:
        self.pages.setCurrentIndex(idx)
        self.lbl_page_title.setText(self._PAGE_TITLES[idx])
        self._sync_nav_icons(idx)

    def _sync_nav_icons(self, active_idx: int) -> None:
        for i, (btn, icon_name) in enumerate(self._nav_meta):
            color = ACCENT if i == active_idx else TEXT_MUTED
            btn.setIcon(get_icon(icon_name, color, 16))

    # ------------------------------------------------------------------ Page 0: Control & Speed
    def _build_speed_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 4, 8)
        lay.setSpacing(16)

        # Card 1: Capture Target
        card_t, t_lay = _make_card(
            "Vùng chụp màn hình",
            "Chọn cửa sổ game hoặc khoanh một vùng cố định trên màn hình",
        )

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self._mode_group = QButtonGroup(self)
        self.btn_mode_window = QPushButton("  Theo cửa sổ game")
        self.btn_mode_window.setIcon(get_icon("monitor", TEXT_SECONDARY, 16))
        self.btn_mode_region = QPushButton("  Vùng cố định")
        self.btn_mode_region.setIcon(get_icon("crop", TEXT_SECONDARY, 16))
        for btn in (self.btn_mode_window, self.btn_mode_region):
            btn.setCheckable(True)
            btn.setFixedHeight(36)
            btn.setIconSize(QSize(16, 16))
            btn.setProperty("class", "segBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._mode_group.addButton(btn)
            mode_row.addWidget(btn)
        self.btn_mode_window.toggled.connect(self._on_mode_changed)
        t_lay.addLayout(mode_row)

        self.win_section = QWidget()
        self.win_section.setStyleSheet("background: transparent;")
        w_lay = QVBoxLayout(self.win_section)
        w_lay.setContentsMargins(0, 4, 0, 0)
        w_lay.setSpacing(12)

        win_pick = QHBoxLayout()
        win_pick.setSpacing(8)
        self.cmb_windows = QComboBox()
        self.cmb_windows.setFixedHeight(36)
        self.cmb_windows.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.btn_refresh_win = QPushButton("  Làm mới")
        self.btn_refresh_win.setFixedHeight(36)
        self.btn_refresh_win.setIcon(get_icon("refresh", TEXT_SECONDARY, 15))
        self.btn_refresh_win.setIconSize(QSize(15, 15))
        self.btn_refresh_win.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh_win.clicked.connect(self._refresh_windows)
        win_pick.addWidget(self.cmb_windows, 1)
        win_pick.addWidget(self.btn_refresh_win)
        w_lay.addLayout(win_pick)

        crop_lbl = QLabel("Vùng cắt trong cửa sổ (thu hẹp về vùng phụ đề giảm thời gian OCR còn 15–20 ms)")
        crop_lbl.setProperty("class", "caption")
        w_lay.addWidget(crop_lbl)

        crop_row = QHBoxLayout()
        crop_row.setSpacing(8)
        self._crop_group = QButtonGroup(self)
        self.btn_crop_full = QPushButton("Toàn cửa sổ")
        self.btn_crop_bottom = QPushButton("Phụ đề dưới (38%)")
        self.btn_crop_center = QPushButton("Hội thoại giữa-dưới (44%)")
        for btn, key in [
            (self.btn_crop_full, "full"),
            (self.btn_crop_bottom, "bottom"),
            (self.btn_crop_center, "center_bottom"),
        ]:
            btn.setCheckable(True)
            btn.setFixedHeight(34)
            btn.setProperty("class", "segBtn")
            btn.setProperty("cropKey", key)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._on_quick_setting_changed)
            self._crop_group.addButton(btn)
            crop_row.addWidget(btn)
        w_lay.addLayout(crop_row)

        self.chk_only_focused = ToggleSwitch("Chỉ hiện lớp phủ khi cửa sổ game đang được chọn")
        w_lay.addWidget(self.chk_only_focused)
        t_lay.addWidget(self.win_section)

        self.region_section = QWidget()
        self.region_section.setStyleSheet("background: transparent;")
        r_lay = QHBoxLayout(self.region_section)
        r_lay.setContentsMargins(0, 4, 0, 0)
        r_lay.setSpacing(12)
        self.btn_select_region = QPushButton("  Kéo chọn vùng trên màn hình")
        self.btn_select_region.setFixedHeight(36)
        self.btn_select_region.setIcon(get_icon("crop", TEXT_SECONDARY, 16))
        self.btn_select_region.setIconSize(QSize(16, 16))
        self.btn_select_region.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_region.clicked.connect(self._open_region_selector)
        self.lbl_region = QLabel("100, 100  —  1280 × 720")
        self.lbl_region.setProperty("class", "monoVal")
        r_lay.addWidget(self.btn_select_region)
        r_lay.addWidget(self.lbl_region, 1)
        t_lay.addWidget(self.region_section)

        lay.addWidget(card_t)

        # Card 2: OCR & Speed
        card_s, s_lay = _make_card(
            "Nhận diện chữ (OCR) và chu kỳ quét",
            "Chọn PP-OCRv5 Đa font cho game dùng font riêng như Sea of Thieves; chọn Windows OCR cho font cơ bản cần tốc độ tối đa",
        )

        # OCR Engine 3-option segmented selector
        ocr_eng_row = QHBoxLayout()
        ocr_eng_row.setSpacing(8)
        self._ocr_engine_group = QButtonGroup(self)
        self.btn_ocr_rapid = QPushButton("PP-OCRv5 Đa font")
        self.btn_ocr_win_enh = QPushButton("Windows + Tăng nét")
        self.btn_ocr_win_fast = QPushButton("Windows Nhanh")
        for btn, key in [
            (self.btn_ocr_rapid, "rapidocr"),
            (self.btn_ocr_win_enh, "windows_enhanced"),
            (self.btn_ocr_win_fast, "windows"),
        ]:
            btn.setCheckable(True)
            btn.setFixedHeight(34)
            btn.setProperty("class", "segBtn")
            btn.setProperty("ocrEngineKey", key)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._on_ocr_engine_clicked)
            self._ocr_engine_group.addButton(btn)
            ocr_eng_row.addWidget(btn)
        s_lay.addLayout(ocr_eng_row)

        self.lbl_ocr_engine_hint = QLabel("")
        self.lbl_ocr_engine_hint.setProperty("class", "caption")
        s_lay.addWidget(self.lbl_ocr_engine_hint)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        lbl_p = QLabel("Mức thiết lập nhanh")
        lbl_p.setProperty("class", "caption")
        preset_row.addWidget(lbl_p)
        for text, ms, max_w in [
            ("Siêu tốc — 60 ms / 640 px", 60, 640),
            ("Cân bằng — 80 ms / 960 px", 80, 960),
            ("Chữ nhỏ — 120 ms / 1280 px", 120, 1280),
        ]:
            chip = QPushButton(text)
            chip.setProperty("class", "chipBtn")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _c, m=ms, w=max_w: self._apply_speed_preset(m, w))
            preset_row.addWidget(chip)
        preset_row.addStretch()
        s_lay.addLayout(preset_row)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)

        self.cmb_ocr_lang = QComboBox()
        self.cmb_ocr_lang.setFixedHeight(36)
        langs = get_available_ocr_languages()
        if langs:
            for tag, name in langs:
                self.cmb_ocr_lang.addItem(f"{name} ({tag})", tag)
        else:
            self.cmb_ocr_lang.addItem("English (en-US)", "en-US")
        form.addRow("Ngôn ngữ trong game", self.cmb_ocr_lang)

        self.cmb_ocr_res = QComboBox()
        self.cmb_ocr_res.setFixedHeight(36)
        self.cmb_ocr_res.addItem("Giới hạn 640 px (nhanh nhất cho vùng phụ đề cắt gọn)", 640)
        self.cmb_ocr_res.addItem("Giới hạn 960 px (cân bằng tốc độ và độ nét)", 960)
        self.cmb_ocr_res.addItem("Giới hạn 1280 px (nét hơn cho toàn cửa sổ 1080p–2K)", 1280)
        self.cmb_ocr_res.addItem("Giới hạn 1600 px (màn hình 4K)", 1600)
        self.cmb_ocr_res.addItem("Độ phân giải gốc (dùng khi chữ quá nhỏ)", 0)
        form.addRow("Độ phân giải OCR", self.cmb_ocr_res)

        interval_row = QHBoxLayout()
        interval_row.setSpacing(12)
        self.sld_interval = QSlider(Qt.Orientation.Horizontal)
        self.sld_interval.setRange(50, 1200)
        self.sld_interval.setSingleStep(10)
        self.lbl_interval = QLabel("80 ms")
        self.lbl_interval.setProperty("class", "monoAccent")
        self.lbl_interval.setFixedWidth(56)
        self.lbl_interval.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sld_interval.valueChanged.connect(lambda v: self.lbl_interval.setText(f"{v} ms"))
        interval_row.addWidget(self.sld_interval, 1)
        interval_row.addWidget(self.lbl_interval)
        form.addRow("Chu kỳ quét", interval_row)

        s_lay.addLayout(form)

        self.chk_merge_lines = ToggleSwitch("Gộp nhiều dòng gần nhau thành một đoạn hội thoại")
        s_lay.addWidget(self.chk_merge_lines)

        lay.addWidget(card_s)
        lay.addStretch()
        return page

    # ------------------------------------------------------------------ Page 1: API & Models
    def _build_api_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 4, 8)
        lay.setSpacing(16)

        # Stacked Engine Selector (no symmetric 3-column template grid)
        card_e, e_lay = _make_card(
            "Bộ máy dịch thuật",
            "Chọn một dịch vụ dịch chính. Các bản dịch đã xử lý được lưu đệm để tái sử dụng tức thì",
        )

        self.row_google = EngineOptionRow(
            "google_free",
            "Google Translate (HTTP/2)",
            "Không cần API key, phản hồi nhanh cho game hành động",
            "~45 ms",
        )
        self.row_gemini = EngineOptionRow(
            "gemini",
            "Google Gemini API",
            "Hiểu ngữ cảnh cốt truyện, tự chuyển mô hình dự phòng khi quá tải",
            "AI ngữ cảnh",
        )
        self.row_openai = EngineOptionRow(
            "openai",
            "Groq / OpenAI-Compatible",
            "Hỗ trợ Groq tốc độ cao, DeepSeek, OpenAI, OpenRouter và Ollama nội bộ",
            "~200 ms",
        )
        self._engine_rows = {
            "google_free": self.row_google,
            "gemini": self.row_gemini,
            "openai": self.row_openai,
        }
        for row in self._engine_rows.values():
            row.clicked.connect(self._select_provider)
            e_lay.addWidget(row)

        # Quiet inset for Hybrid, Tiered Routing & Streaming inside the engine card
        self.hybrid_inset = QFrame()
        self.hybrid_inset.setProperty("class", "insetCard")
        h_lay = QVBoxLayout(self.hybrid_inset)
        h_lay.setContentsMargins(16, 12, 16, 12)
        h_lay.setSpacing(8)
        self.chk_tiered_routing = ToggleSwitch(
            "Chia 2 luồng dịch (nút bấm 1–4 từ dịch nhanh ~45 ms, hội thoại dài dùng AI)"
        )
        self.chk_stream_llm = ToggleSwitch(
            "Đổ chữ trực tiếp từng phần khi AI trả lời (Streaming giảm TTFT)"
        )
        self.chk_hybrid_fast = ToggleSwitch(
            "Hiện bản dịch nhanh trước (~45 ms), AI cập nhật ngầm sau"
        )
        h_desc = QLabel(
            "Khi bạn bấm qua thoại mới, yêu cầu AI cũ sẽ tự hủy ngay để không nghẽn băng thông và không đè chữ cũ"
        )
        h_desc.setProperty("class", "caption")
        h_desc.setWordWrap(True)
        h_lay.addWidget(self.chk_tiered_routing)
        h_lay.addWidget(self.chk_stream_llm)
        h_lay.addWidget(self.chk_hybrid_fast)
        h_lay.addWidget(h_desc)
        e_lay.addWidget(self.hybrid_inset)

        lay.addWidget(card_e)

        # Gemini config card
        self.gemini_card, g_lay = _make_card("Cấu hình Google Gemini")
        g_form = QFormLayout()
        g_form.setHorizontalSpacing(16)
        g_form.setVerticalSpacing(12)

        g_key_row = QHBoxLayout()
        g_key_row.setSpacing(8)
        self.txt_gemini_key = QLineEdit()
        self.txt_gemini_key.setFixedHeight(36)
        self.txt_gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_gemini_key.setPlaceholderText("Khóa API AIza hoặc AQ")
        self.btn_g_eye = QPushButton()
        self.btn_g_eye.setFixedSize(36, 36)
        self.btn_g_eye.setCheckable(True)
        self.btn_g_eye.setIcon(get_icon("eye", TEXT_SECONDARY, 16))
        self.btn_g_eye.setIconSize(QSize(16, 16))
        self.btn_g_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_g_eye.toggled.connect(
            lambda on: (
                self.txt_gemini_key.setEchoMode(
                    QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
                ),
                self.btn_g_eye.setIcon(get_icon("eye_off" if on else "eye", TEXT_SECONDARY, 16)),
            )
        )
        g_key_row.addWidget(self.txt_gemini_key, 1)
        g_key_row.addWidget(self.btn_g_eye)
        g_form.addRow("API Key", g_key_row)

        gm_row = QHBoxLayout()
        gm_row.setSpacing(8)
        self.cmb_gemini_model = QComboBox()
        self.cmb_gemini_model.setFixedHeight(36)
        self.cmb_gemini_model.setEditable(True)
        self.cmb_gemini_model.addItems([
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash",
            "gemini-3.5-flash",
        ])
        self.btn_load_gemini_models = QPushButton("  Tải danh sách")
        self.btn_load_gemini_models.setFixedHeight(36)
        self.btn_load_gemini_models.setIcon(get_icon("refresh", TEXT_SECONDARY, 15))
        self.btn_load_gemini_models.setIconSize(QSize(15, 15))
        self.btn_load_gemini_models.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_load_gemini_models.clicked.connect(self._load_gemini_models)
        gm_row.addWidget(self.cmb_gemini_model, 1)
        gm_row.addWidget(self.btn_load_gemini_models)
        g_form.addRow("Mô hình", gm_row)
        g_lay.addLayout(g_form)
        lay.addWidget(self.gemini_card)

        # OpenAI / Groq config card
        self.openai_card, o_lay = _make_card(
            "Cấu hình Groq và OpenAI-Compatible",
            "Chọn nhanh máy chủ mẫu bên dưới hoặc tự nhập địa chỉ endpoint",
        )
        preset_o = QHBoxLayout()
        preset_o.setSpacing(8)
        for label, url, model in [
            ("Cerebras", "https://api.cerebras.ai/v1", "qwen-3-32b"),
            ("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
            ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
            ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("OpenRouter", "https://openrouter.ai/api/v1", "google/gemini-2.5-flash-lite"),
            ("Ollama nội bộ", "http://localhost:11434/v1", "qwen2.5:7b"),
        ]:
            chip = QPushButton(label)
            chip.setProperty("class", "chipBtn")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _c, u=url, m=model: self._apply_openai_preset(u, m))
            preset_o.addWidget(chip)
        preset_o.addStretch()
        o_lay.addLayout(preset_o)

        o_form = QFormLayout()
        o_form.setHorizontalSpacing(16)
        o_form.setVerticalSpacing(12)

        self.txt_openai_url = QLineEdit()
        self.txt_openai_url.setFixedHeight(36)
        self.txt_openai_url.setPlaceholderText("https://api.groq.com/openai/v1")

        o_key_row = QHBoxLayout()
        o_key_row.setSpacing(8)
        self.txt_openai_key = QLineEdit()
        self.txt_openai_key.setFixedHeight(36)
        self.txt_openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_openai_key.setPlaceholderText("Khóa API gsk_ hoặc sk-")
        self.btn_o_eye = QPushButton()
        self.btn_o_eye.setFixedSize(36, 36)
        self.btn_o_eye.setCheckable(True)
        self.btn_o_eye.setIcon(get_icon("eye", TEXT_SECONDARY, 16))
        self.btn_o_eye.setIconSize(QSize(16, 16))
        self.btn_o_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_o_eye.toggled.connect(
            lambda on: (
                self.txt_openai_key.setEchoMode(
                    QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
                ),
                self.btn_o_eye.setIcon(get_icon("eye_off" if on else "eye", TEXT_SECONDARY, 16)),
            )
        )
        o_key_row.addWidget(self.txt_openai_key, 1)
        o_key_row.addWidget(self.btn_o_eye)

        self.txt_openai_model = QLineEdit()
        self.txt_openai_model.setFixedHeight(36)
        self.txt_openai_model.setPlaceholderText("llama-3.3-70b-versatile")
        o_form.addRow("Base URL", self.txt_openai_url)
        o_form.addRow("API Key", o_key_row)
        o_form.addRow("Mô hình", self.txt_openai_model)
        o_lay.addLayout(o_form)
        lay.addWidget(self.openai_card)

        # Prompt & Test Card
        card_ctx, ctx_lay = _make_card("Ngôn ngữ đích và ngữ cảnh dịch")
        top_ctx = QHBoxLayout()
        top_ctx.setSpacing(12)
        lbl_tl = QLabel("Mã ngôn ngữ")
        self.txt_target_lang = QLineEdit()
        self.txt_target_lang.setFixedSize(88, 36)
        self.txt_target_lang.setPlaceholderText("vi")
        top_ctx.addWidget(lbl_tl)
        top_ctx.addWidget(self.txt_target_lang)
        top_ctx.addStretch()

        self.btn_test_api = QPushButton("  Kiểm tra API")
        self.btn_test_api.setFixedHeight(36)
        self.btn_test_api.setIcon(get_icon("check", TEXT_SECONDARY, 16))
        self.btn_test_api.setIconSize(QSize(16, 16))
        self.btn_test_api.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test_api.clicked.connect(self._test_api)
        top_ctx.addWidget(self.btn_test_api)
        ctx_lay.addLayout(top_ctx)

        self.txt_game_context = QPlainTextEdit()
        self.txt_game_context.setMaximumHeight(76)
        ctx_lay.addWidget(self.txt_game_context)
        lay.addWidget(card_ctx)

        lay.addStretch()
        return page

    # ------------------------------------------------------------------ Page 2: Overlay Style
    def _build_overlay_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 4, 8)
        lay.setSpacing(16)

        # Outer card wrapping the inset viewport moment (12px surface -> 8px inset)
        card_prev, p_lay = _make_card()
        p_lay.setContentsMargins(16, 16, 16, 16)
        self.live_preview = LiveOverlayPreview(self.config)
        p_lay.addWidget(self.live_preview)
        lay.addWidget(card_prev)

        card_o, o_lay = _make_card("Hiển thị hộp chữ đè lên game")

        sw_row = QHBoxLayout()
        sw_row.setSpacing(20)
        self.chk_adaptive_bg = ToggleSwitch("Nền thích ứng theo game")
        self.chk_text_shadow = ToggleSwitch("Đổ bóng chữ")
        self.chk_border = ToggleSwitch("Viền khung mảnh")
        for sw in (self.chk_adaptive_bg, self.chk_text_shadow, self.chk_border):
            sw.toggled.connect(self._on_overlay_style_live)
            sw_row.addWidget(sw)
        sw_row.addStretch()
        o_lay.addLayout(sw_row)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)

        op_row = QHBoxLayout()
        op_row.setSpacing(12)
        self.sld_opacity = QSlider(Qt.Orientation.Horizontal)
        self.sld_opacity.setRange(90, 255)
        self.lbl_opacity = QLabel("215")
        self.lbl_opacity.setProperty("class", "monoVal")
        self.lbl_opacity.setFixedWidth(36)
        self.lbl_opacity.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sld_opacity.valueChanged.connect(self._on_opacity_changed)
        op_row.addWidget(self.sld_opacity, 1)
        op_row.addWidget(self.lbl_opacity)
        form.addRow("Độ đậm nền", op_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        self.btn_bg_color = QPushButton("Màu nền cố định")
        self.btn_bg_color.setFixedHeight(34)
        self.btn_bg_color.clicked.connect(self._pick_bg_color)
        self.btn_text_color = QPushButton("Màu chữ cố định")
        self.btn_text_color.setFixedHeight(34)
        self.btn_text_color.clicked.connect(self._pick_text_color)
        color_row.addWidget(self.btn_bg_color)
        color_row.addWidget(self.btn_text_color)
        form.addRow("Màu sắc thủ công", color_row)

        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        self.txt_font_family = QLineEdit()
        self.txt_font_family.setFixedHeight(34)
        self.txt_font_family.textChanged.connect(self._on_overlay_style_live)
        self.spn_min_font = QSpinBox()
        self.spn_min_font.setFixedHeight(34)
        self.spn_min_font.setRange(7, 24)
        self.spn_min_font.valueChanged.connect(self._on_overlay_style_live)
        self.spn_max_font = QSpinBox()
        self.spn_max_font.setFixedHeight(34)
        self.spn_max_font.setRange(10, 48)
        self.spn_max_font.valueChanged.connect(self._on_overlay_style_live)
        font_row.addWidget(self.txt_font_family, 2)
        font_row.addWidget(QLabel("Nhỏ nhất"))
        font_row.addWidget(self.spn_min_font)
        font_row.addWidget(QLabel("Lớn nhất"))
        font_row.addWidget(self.spn_max_font)
        form.addRow("Phông và cỡ chữ", font_row)

        box_row = QHBoxLayout()
        box_row.setSpacing(8)
        self.spn_padding = QSpinBox()
        self.spn_padding.setFixedHeight(34)
        self.spn_padding.setRange(0, 8)
        self.spn_padding.valueChanged.connect(self._on_overlay_style_live)
        self.spn_radius = QSpinBox()
        self.spn_radius.setFixedHeight(34)
        self.spn_radius.setRange(0, 10)
        self.spn_radius.valueChanged.connect(self._on_overlay_style_live)
        box_row.addWidget(QLabel("Đệm lề"))
        box_row.addWidget(self.spn_padding)
        box_row.addWidget(QLabel("Bo góc"))
        box_row.addWidget(self.spn_radius)
        box_row.addStretch()
        form.addRow("Kích thước hộp", box_row)

        o_lay.addLayout(form)

        act_row = QHBoxLayout()
        act_row.setSpacing(8)
        self.btn_preview = QPushButton("  Xem thử toàn màn hình (4 giây)")
        self.btn_preview.setFixedHeight(36)
        self.btn_preview.setIcon(get_icon("frame", TEXT_SECONDARY, 16))
        self.btn_preview.setIconSize(QSize(16, 16))
        self.btn_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_preview.clicked.connect(self._show_preview)
        self.btn_save = QPushButton("  Lưu cài đặt")
        self.btn_save.setFixedHeight(36)
        self.btn_save.setIcon(get_icon("save", TEXT_SECONDARY, 16))
        self.btn_save.setIconSize(QSize(16, 16))
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._save_ui_to_config)
        act_row.addWidget(self.btn_preview)
        act_row.addWidget(self.btn_save)
        act_row.addStretch()
        o_lay.addLayout(act_row)

        lay.addWidget(card_o)
        lay.addStretch()
        return page

    # ------------------------------------------------------------------ Page 3: Filters & Shortcuts
    def _build_system_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 4, 8)
        lay.setSpacing(16)

        card_f, f_lay = _make_card(
            "Bộ lọc cảnh game 3D",
            "Tránh dịch nhầm chỉ số hệ thống và giảm tải cho bộ máy dịch khi cảnh chuyển động liên tục",
        )
        self.chk_filter_hud = ToggleSwitch("Bỏ qua chữ HUD như FPS, la bàn, khoảng cách và đồng hồ")
        self.chk_text_mask_diff = ToggleSwitch(
            "Lọc mặt nạ chữ khi so khung hình (bỏ qua chuyển động sóng, mây, nền 3D)"
        )
        self.chk_show_mini_hud = ToggleSwitch("Tự mở thanh nổi ở góc màn hình khi bắt đầu dịch")
        f_lay.addWidget(self.chk_filter_hud)
        f_lay.addWidget(self.chk_text_mask_diff)
        f_lay.addWidget(self.chk_show_mini_hud)

        diff_row = QHBoxLayout()
        diff_row.setSpacing(12)
        diff_row.addWidget(QLabel("Ngưỡng phát hiện đổi khung hình"))
        self.sld_diff = QSlider(Qt.Orientation.Horizontal)
        self.sld_diff.setRange(5, 80)
        self.lbl_diff = QLabel("1.6")
        self.lbl_diff.setProperty("class", "monoVal")
        self.lbl_diff.setFixedWidth(36)
        self.lbl_diff.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sld_diff.valueChanged.connect(lambda v: self.lbl_diff.setText(f"{v / 10.0:.1f}"))
        diff_row.addWidget(self.sld_diff, 1)
        diff_row.addWidget(self.lbl_diff)
        f_lay.addLayout(diff_row)
        lay.addWidget(card_f)

        card_c, c_lay = _make_card(
            "Bộ nhớ đệm bản dịch",
            "Các đoạn thoại và menu đã gặp được lưu tại chỗ để hiển thị lại không tốn lượt gọi API",
        )
        c_row = QHBoxLayout()
        c_row.setSpacing(12)
        self.chk_use_cache = ToggleSwitch("Tự động lưu bộ nhớ đệm")
        self.btn_clear_cache = QPushButton(f"  Xóa bộ nhớ đệm ({len(self.cache)})")
        self.btn_clear_cache.setFixedHeight(36)
        self.btn_clear_cache.setIcon(get_icon("trash", TEXT_SECONDARY, 15))
        self.btn_clear_cache.setIconSize(QSize(15, 15))
        self.btn_clear_cache.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        c_row.addWidget(self.chk_use_cache, 1)
        c_row.addWidget(self.btn_clear_cache)
        c_lay.addLayout(c_row)
        lay.addWidget(card_c)

        card_k, k_lay = _make_card(
            "Phím tắt toàn cục",
            "Đăng ký trực tiếp qua Windows API, không chắn thao tác bàn phím khi chơi game",
        )
        hk_grid = QGridLayout()
        hk_grid.setHorizontalSpacing(16)
        hk_grid.setVerticalSpacing(8)
        for i, (combo, desc) in enumerate([
            ("Alt + T", "Bắt đầu hoặc dừng dịch tự động"),
            ("Alt + S", "Quét và dịch một lần ngay lập tức"),
            ("Alt + H", "Ẩn hoặc hiện nhanh lớp phủ tiếng Việt"),
        ]):
            cap = QLabel(combo)
            cap.setProperty("class", "keycap")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setFixedWidth(84)
            dl = QLabel(desc)
            dl.setStyleSheet(f"color: {TEXT_SECONDARY};")
            hk_grid.addWidget(cap, i, 0)
            hk_grid.addWidget(dl, i, 1)
        hk_grid.setColumnStretch(1, 1)
        k_lay.addLayout(hk_grid)

        tip = QLabel(
            "Đặt game ở chế độ Borderless Windowed hoặc Windowed để Windows cho phép vẽ lớp phủ. "
            "Nếu chơi game tiếng Nhật, Trung hoặc Hàn, cài thêm gói ngôn ngữ tương ứng trong Windows Settings → Language & Region."
        )
        tip.setProperty("class", "caption")
        tip.setWordWrap(True)
        k_lay.addWidget(tip)
        lay.addWidget(card_k)

        lay.addStretch()
        return page

    # ------------------------------------------------------------------ Config Sync
    def _load_config_to_ui(self) -> None:
        c = self.config
        self.btn_mode_window.setChecked(c.capture_mode == "window")
        self.btn_mode_region.setChecked(c.capture_mode == "region")
        self._on_mode_changed()

        for btn in self._crop_group.buttons():
            if btn.property("cropKey") == c.window_crop_preset:
                btn.setChecked(True)
                break
        else:
            self.btn_crop_full.setChecked(True)

        x, y, w, h = c.region
        self.lbl_region.setText(f"{x}, {y}  —  {w} × {h}")
        self.chk_only_focused.setChecked(c.only_when_focused)

        idx = self.cmb_ocr_lang.findData(c.ocr_language)
        if idx >= 0:
            self.cmb_ocr_lang.setCurrentIndex(idx)
        for btn in self._ocr_engine_group.buttons():
            if btn.property("ocrEngineKey") == c.ocr_engine:
                btn.setChecked(True)
                break
        else:
            self.btn_ocr_rapid.setChecked(True)
        self._update_ocr_engine_hint()
        r_idx = self.cmb_ocr_res.findData(c.ocr_max_width)
        if r_idx >= 0:
            self.cmb_ocr_res.setCurrentIndex(r_idx)

        self.sld_interval.setValue(max(50, min(1200, c.scan_interval_ms)))
        self.lbl_interval.setText(f"{self.sld_interval.value()} ms")
        self.chk_merge_lines.setChecked(c.merge_lines)
        self.chk_filter_hud.setChecked(c.filter_hud_noise)
        self.chk_text_mask_diff.setChecked(c.text_mask_diff)
        self.chk_show_mini_hud.setChecked(c.show_mini_hud)
        self.sld_diff.setValue(max(5, min(80, int(round(c.change_threshold * 10)))))
        self.lbl_diff.setText(f"{self.sld_diff.value() / 10.0:.1f}")

        self._select_provider(c.provider)
        self.chk_tiered_routing.setChecked(c.tiered_routing)
        self.chk_stream_llm.setChecked(c.stream_llm)
        self.chk_hybrid_fast.setChecked(c.hybrid_fast_preview)
        self.txt_target_lang.setText(c.target_language)
        self.txt_gemini_key.setText(c.gemini_api_key)
        self.cmb_gemini_model.setCurrentText(c.gemini_model)
        self.txt_openai_url.setText(c.openai_base_url)
        self.txt_openai_key.setText(c.openai_api_key)
        self.txt_openai_model.setText(c.openai_model)
        self.txt_game_context.setPlainText(c.game_context)
        self.chk_use_cache.setChecked(c.use_cache)

        self.chk_adaptive_bg.setChecked(c.adaptive_bg)
        self.chk_text_shadow.setChecked(c.text_shadow)
        self.chk_border.setChecked(c.show_border)
        self.sld_opacity.setValue(max(90, min(255, c.bg_opacity)))
        self.lbl_opacity.setText(str(self.sld_opacity.value()))
        self.txt_font_family.setText(c.font_family)
        self.spn_min_font.setValue(c.min_font_size)
        self.spn_max_font.setValue(c.max_font_size)
        self.spn_padding.setValue(c.padding)
        self.spn_radius.setValue(c.border_radius)
        self._update_color_buttons()
        self.live_preview.set_config(c)

    def _save_ui_to_config(self) -> AppConfig:
        c = self.config
        c.capture_mode = "region" if self.btn_mode_region.isChecked() else "window"
        if self.cmb_windows.currentIndex() >= 0:
            c.target_window_title = self.cmb_windows.currentText()

        checked_crop = self._crop_group.checkedButton()
        c.window_crop_preset = str(checked_crop.property("cropKey")) if checked_crop else "full"
        c.only_when_focused = self.chk_only_focused.isChecked()

        checked_ocr = self._ocr_engine_group.checkedButton()
        c.ocr_engine = str(checked_ocr.property("ocrEngineKey")) if checked_ocr else "rapidocr"

        ocr_tag = self.cmb_ocr_lang.currentData()
        if ocr_tag:
            c.ocr_language = str(ocr_tag)
        ocr_max_w = self.cmb_ocr_res.currentData()
        if ocr_max_w is not None:
            c.ocr_max_width = int(ocr_max_w)
        c.scan_interval_ms = self.sld_interval.value()
        c.merge_lines = self.chk_merge_lines.isChecked()
        c.filter_hud_noise = self.chk_filter_hud.isChecked()
        c.text_mask_diff = self.chk_text_mask_diff.isChecked()
        c.show_mini_hud = self.chk_show_mini_hud.isChecked()
        c.change_threshold = self.sld_diff.value() / 10.0

        c.provider = self._selected_provider
        c.tiered_routing = self.chk_tiered_routing.isChecked()
        c.stream_llm = self.chk_stream_llm.isChecked()
        c.hybrid_fast_preview = self.chk_hybrid_fast.isChecked()
        c.target_language = self.txt_target_lang.text().strip() or "vi"
        c.gemini_api_key = self.txt_gemini_key.text().strip()
        c.gemini_model = self.cmb_gemini_model.currentText().strip() or "gemini-3.5-flash-lite"
        c.openai_base_url = self.txt_openai_url.text().strip() or "https://api.openai.com/v1"
        c.openai_api_key = self.txt_openai_key.text().strip()
        c.openai_model = self.txt_openai_model.text().strip() or "gpt-4o-mini"
        c.game_context = self.txt_game_context.toPlainText().strip()
        c.use_cache = self.chk_use_cache.isChecked()

        c.adaptive_bg = self.chk_adaptive_bg.isChecked()
        c.text_shadow = self.chk_text_shadow.isChecked()
        c.show_border = self.chk_border.isChecked()
        c.bg_opacity = self.sld_opacity.value()
        c.font_family = self.txt_font_family.text().strip() or "Segoe UI"
        c.min_font_size = self.spn_min_font.value()
        c.max_font_size = max(c.min_font_size, self.spn_max_font.value())
        c.padding = self.spn_padding.value()
        c.border_radius = self.spn_radius.value()

        c.save()
        snap = replace(c)
        self.overlay.update_config(snap)
        self.live_preview.set_config(snap)
        if self.worker and self.worker.isRunning():
            self.worker.update_config(snap, self._selected_hwnd())
        self.lbl_status.setText("Đã lưu cấu hình")
        return snap

    # ------------------------------------------------------------------ Handlers
    def _on_mode_changed(self) -> None:
        is_win = self.btn_mode_window.isChecked()
        self.win_section.setVisible(is_win)
        self.region_section.setVisible(not is_win)
        self.btn_mode_window.setIcon(get_icon("monitor", ACCENT if is_win else TEXT_MUTED, 16))
        self.btn_mode_region.setIcon(get_icon("crop", ACCENT if not is_win else TEXT_MUTED, 16))

    def _on_quick_setting_changed(self) -> None:
        if not self._loading_ui and self.worker and self.worker.isRunning():
            self._save_ui_to_config()

    def _on_ocr_engine_clicked(self) -> None:
        self._update_ocr_engine_hint()
        self._on_quick_setting_changed()

    def _update_ocr_engine_hint(self) -> None:
        checked = self._ocr_engine_group.checkedButton()
        key = str(checked.property("ocrEngineKey")) if checked else "rapidocr"
        hints = {
            "rapidocr": "Đọc chính xác font hải tặc, chữ viết tay, giấy da và chữ có viền bóng; lọc nhiễu bằng điểm tin cậy",
            "windows_enhanced": "Windows OCR kèm tăng nét và tương phản (~50 ms); hợp chữ serif có viền nhẹ",
            "windows": "Windows OCR nguyên bản (~20 ms); nhanh nhất cho game dùng font sans-serif rõ ràng",
        }
        self.lbl_ocr_engine_hint.setText(hints.get(key, ""))

    def _apply_speed_preset(self, interval_ms: int, max_width: int) -> None:
        self.sld_interval.setValue(interval_ms)
        idx = self.cmb_ocr_res.findData(max_width)
        if idx >= 0:
            self.cmb_ocr_res.setCurrentIndex(idx)
        self._save_ui_to_config()

    def _select_provider(self, provider_id: str) -> None:
        if provider_id not in self._engine_rows:
            provider_id = "google_free"
        self._selected_provider = provider_id
        for pid, row in self._engine_rows.items():
            row.set_selected(pid == provider_id)
        self.hybrid_inset.setVisible(provider_id in ("gemini", "openai"))
        self.gemini_card.setVisible(provider_id == "gemini")
        self.openai_card.setVisible(provider_id == "openai")

    def _apply_openai_preset(self, base_url: str, model: str) -> None:
        self.txt_openai_url.setText(base_url)
        self.txt_openai_model.setText(model)
        self.lbl_status.setText(f"Đã chọn máy chủ {base_url} ({model})")

    def _on_overlay_style_live(self) -> None:
        if self._loading_ui:
            return
        c = self.config
        c.adaptive_bg = self.chk_adaptive_bg.isChecked()
        c.text_shadow = self.chk_text_shadow.isChecked()
        c.show_border = self.chk_border.isChecked()
        c.font_family = self.txt_font_family.text().strip() or "Segoe UI"
        c.min_font_size = self.spn_min_font.value()
        c.max_font_size = max(c.min_font_size, self.spn_max_font.value())
        c.padding = self.spn_padding.value()
        c.border_radius = self.spn_radius.value()
        self._update_color_buttons()
        self.live_preview.set_config(c)
        self.overlay.update_config(c)

    def _on_opacity_changed(self, value: int) -> None:
        self.lbl_opacity.setText(str(value))
        if not self._loading_ui:
            self.config.bg_opacity = value
            self.live_preview.set_config(self.config)
            self.overlay.update_config(self.config)

    def _update_color_buttons(self) -> None:
        adaptive = self.chk_adaptive_bg.isChecked()
        self.btn_bg_color.setEnabled(not adaptive)
        self.btn_text_color.setEnabled(not adaptive)
        if adaptive:
            self.btn_bg_color.setStyleSheet("")
            self.btn_text_color.setStyleSheet("")
        else:
            self.btn_bg_color.setStyleSheet(
                f"background-color: {self.config.bg_color}; color: #ededef; border: 1px solid {BORDER_SUBTLE};"
            )
            self.btn_text_color.setStyleSheet(
                f"background-color: {BG_PAGE}; color: {self.config.text_color}; border: 1px solid {self.config.text_color};"
            )

    def _pick_bg_color(self) -> None:
        col = QColorDialog.getColor(QColor(self.config.bg_color), self, "Chọn màu nền lớp phủ")
        if col.isValid():
            self.config.bg_color = col.name()
            self._update_color_buttons()
            self.live_preview.set_config(self.config)
            self.overlay.update_config(self.config)

    def _pick_text_color(self) -> None:
        col = QColorDialog.getColor(QColor(self.config.text_color), self, "Chọn màu chữ lớp phủ")
        if col.isValid():
            self.config.text_color = col.name()
            self._update_color_buttons()
            self.live_preview.set_config(self.config)
            self.overlay.update_config(self.config)

    def _refresh_windows(self) -> None:
        own_hwnds = {int(self.winId()), int(self.overlay.winId()), int(self.mini_hud.winId())}
        self._windows_list = list_visible_windows(exclude_hwnds=own_hwnds)
        current_title = self.cmb_windows.currentText() or self.config.target_window_title

        self.cmb_windows.clear()
        selected_idx = -1
        for i, (hwnd, title) in enumerate(self._windows_list):
            self.cmb_windows.addItem(title, hwnd)
            if title == current_title:
                selected_idx = i
        if selected_idx >= 0:
            self.cmb_windows.setCurrentIndex(selected_idx)

    def _selected_hwnd(self) -> Optional[int]:
        if self.btn_mode_region.isChecked():
            return None
        data = self.cmb_windows.currentData()
        return int(data) if data else None

    def _open_region_selector(self) -> None:
        self.hide()
        QTimer.singleShot(180, self._launch_region_selector)

    def _launch_region_selector(self) -> None:
        self.region_selector = RegionSelector()
        self.region_selector.region_selected.connect(self._on_region_selected)
        self.region_selector.cancelled.connect(self.showNormal)
        self.region_selector.show()

    def _on_region_selected(self, x: int, y: int, w: int, h: int) -> None:
        self.config.region = [x, y, w, h]
        self.btn_mode_region.setChecked(True)
        self.lbl_region.setText(f"{x}, {y}  —  {w} × {h}")
        self._save_ui_to_config()
        self.showNormal()
        self.activateWindow()

    def _clear_cache(self) -> None:
        self.cache.clear()
        self.btn_clear_cache.setText("  Xóa bộ nhớ đệm (0)")
        self.lbl_m_cache.setText("0")
        self.lbl_status.setText("Đã xóa toàn bộ bộ nhớ đệm")

    def _load_gemini_models(self) -> None:
        key = self.txt_gemini_key.text().strip()
        if not key:
            QMessageBox.warning(self, "Thiếu API Key", "Nhập Gemini API Key trước khi tải danh sách mô hình.")
            return
        self.btn_load_gemini_models.setEnabled(False)
        self.btn_load_gemini_models.setText("Đang tải...")
        self._gemini_models_thread = GeminiModelsThread(key)
        self._gemini_models_thread.finished_signal.connect(self._on_gemini_models_loaded)
        self._gemini_models_thread.start()

    def _on_gemini_models_loaded(self, ok: bool, payload) -> None:
        self.btn_load_gemini_models.setEnabled(True)
        self.btn_load_gemini_models.setText("  Tải danh sách")
        if ok and isinstance(payload, list) and payload:
            current = self.cmb_gemini_model.currentText()
            self.cmb_gemini_model.clear()
            self.cmb_gemini_model.addItems(payload)
            if current in payload:
                self.cmb_gemini_model.setCurrentText(current)
            self.lbl_status.setText(f"Đã tải {len(payload)} mô hình Gemini khả dụng")
        else:
            QMessageBox.warning(self, "Không tải được danh sách", str(payload))

    def _test_api(self) -> None:
        cfg = self._save_ui_to_config()
        self.btn_test_api.setEnabled(False)
        self.btn_test_api.setText("Đang kiểm tra...")
        self._api_test_thread = ApiTestThread(cfg)
        self._api_test_thread.finished_signal.connect(self._on_api_test_done)
        self._api_test_thread.start()

    def _on_api_test_done(self, ok: bool, msg: str) -> None:
        self.btn_test_api.setEnabled(True)
        self.btn_test_api.setText("  Kiểm tra API")
        if ok:
            QMessageBox.information(self, "Kết nối API thành công", msg)
        else:
            QMessageBox.warning(self, "Lỗi kết nối API", msg)

    def _show_preview(self) -> None:
        self._save_ui_to_config()
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        sg = screen.geometry() if screen else self.geometry()
        rect = (
            int(round(sg.x() * dpr)),
            int(round(sg.y() * dpr)),
            int(round(sg.width() * dpr)),
            int(round(sg.height() * dpr)),
        )
        demo_blocks = [
            OcrBlock(
                text="Press Start to begin",
                translated="Nhấn Bắt Đầu để tiếp tục hành trình",
                nx=0.26,
                ny=0.72,
                nw=0.48,
                nh=0.065,
                bg_rgb=(18, 22, 30),
                fg_rgb=(237, 237, 239),
                line_count=1,
            ),
            OcrBlock(
                text="Quest Updated: Defeat the Ancient Dragon",
                translated="Cập nhật nhiệm vụ: Tiêu diệt Rồng Cổ Đại tại Đỉnh Mây Mù để nhận Thánh Kiếm",
                nx=0.26,
                ny=0.80,
                nw=0.48,
                nh=0.095,
                bg_rgb=(22, 20, 17),
                fg_rgb=(245, 240, 228),
                line_count=2,
            ),
        ]
        self.overlay.set_user_hidden(False)
        self.overlay.set_blocks(rect, demo_blocks)
        self.lbl_status.setText("Đang hiển thị bản xem trước toàn màn hình trong 4 giây")
        QTimer.singleShot(4000, self._clear_preview_if_idle)

    def _clear_preview_if_idle(self) -> None:
        if not (self.worker and self.worker.isRunning()):
            self.overlay.clear_blocks()
            self.overlay.hide()
            self.lbl_status.setText("Đã tắt bản xem trước")

    def _toggle_mini_hud(self) -> None:
        if self.mini_hud.isVisible():
            self.mini_hud.hide()
            self.btn_mini_hud.setIcon(get_icon("pin", TEXT_SECONDARY, 16))
        else:
            self.mini_hud.show()
            self.btn_mini_hud.setIcon(get_icon("pin", ACCENT, 16))

    # ------------------------------------------------------------------ Pipeline Control
    def _retire_thread(self, thread: Optional[QThread], timeout_ms: int = 2000) -> None:
        """Stop a QThread safely without ever GC-destroying a still-running thread."""
        if thread is None:
            return
        if isinstance(thread, TranslationWorker):
            thread.stop()
            for sig in (
                thread.blocks_ready,
                thread.overlay_geometry_changed,
                thread.status_changed,
                thread.metrics_updated,
                thread.stage_profiled,
                thread.error_occurred,
                thread.finished,
            ):
                try:
                    sig.disconnect()
                except Exception:
                    pass
        if thread.isRunning():
            thread.wait(timeout_ms)
        if thread.isRunning():
            # Keep alive until OS thread finishes so PyQt6 never aborts via ~QThread()
            self._retiring_threads.append(thread)
            thread.finished.connect(
                lambda t=thread: self._retiring_threads.remove(t)
                if t in self._retiring_threads
                else None
            )

    def toggle_translation(self) -> None:
        if self.worker and self.worker.isRunning():
            self.stop_translation()
        else:
            self.start_translation()

    def start_translation(self) -> None:
        if self.worker is not None:
            self._retire_thread(self.worker, timeout_ms=1500)
            self.worker = None

        cfg = self._save_ui_to_config()
        hwnd = self._selected_hwnd()
        if cfg.capture_mode == "window" and not hwnd and not cfg.target_window_title:
            QMessageBox.warning(
                self,
                "Chưa chọn cửa sổ game",
                "Chọn một cửa sổ game trong danh sách hoặc chuyển sang chế độ vùng cố định.",
            )
            return

        self.overlay.set_user_hidden(False)
        self.btn_toggle_overlay.setIcon(get_icon("eye", TEXT_SECONDARY, 16))
        self.mini_hud.set_overlay_hidden(False)

        self.worker = TranslationWorker(cfg, self.cache, target_hwnd=hwnd, one_shot=False)
        self.worker.blocks_ready.connect(self._on_blocks_ready)
        self.worker.overlay_geometry_changed.connect(self.overlay.set_physical_geometry)
        self.worker.status_changed.connect(self._on_status_changed)
        self.worker.metrics_updated.connect(self._on_metrics_updated)
        self.worker.stage_profiled.connect(self._on_stage_profiled)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

        self.btn_start.setText("  Dừng dịch")
        self.btn_start.setIcon(get_icon("stop", "#ffffff", 15))
        self.btn_start.setObjectName("stopBtn")
        self.btn_start.style().unpolish(self.btn_start)
        self.btn_start.style().polish(self.btn_start)

        self.lbl_state_dot.setStyleSheet(f"background-color: {SEMANTIC_OK}; border-radius: 4px;")
        self.lbl_state_txt.setText("Đang dịch")
        self.mini_hud.set_running(True)
        if cfg.show_mini_hud:
            self.mini_hud.show()
            self.btn_mini_hud.setIcon(get_icon("pin", ACCENT, 16))
        self.lbl_status.setText("Đang quét màn hình game")

    def _reset_stopped_ui(self, status_msg: Optional[str] = "Đã dừng dịch") -> None:
        self.overlay.clear_blocks()
        self.overlay.hide()

        self.btn_start.setText("  Bắt đầu dịch")
        self.btn_start.setIcon(get_icon("play", CTA_TEXT, 15))
        self.btn_start.setObjectName("primaryBtn")
        self.btn_start.style().unpolish(self.btn_start)
        self.btn_start.style().polish(self.btn_start)

        self.lbl_state_dot.setStyleSheet(f"background-color: {TEXT_MUTED}; border-radius: 4px;")
        self.lbl_state_txt.setText("Đang nghỉ")
        self.lbl_m_mode.setText("Đã dừng")
        self.mini_hud.set_running(False)
        if status_msg:
            self.lbl_status.setText(status_msg)

    def _on_worker_finished(self) -> None:
        if self.worker is not None and not self.worker.isRunning():
            self.worker = None
            if not self._quitting:
                self._reset_stopped_ui(status_msg=None)

    def stop_translation(self) -> None:
        if self.worker is not None:
            worker = self.worker
            self.worker = None
            self._retire_thread(worker, timeout_ms=2500)
        self._reset_stopped_ui("Đã dừng dịch")

    def _stop_oneshot_worker(self) -> None:
        if self.oneshot_worker is not None:
            w = self.oneshot_worker
            self.oneshot_worker = None
            self._retire_thread(w, timeout_ms=1500)

    def run_one_shot(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.trigger_rescan()
            self.lbl_status.setText("Đang quét lại ngay")
            return
        self._stop_oneshot_worker()
        cfg = self._save_ui_to_config()
        hwnd = self._selected_hwnd()
        self.overlay.set_user_hidden(False)
        self.oneshot_worker = TranslationWorker(cfg, self.cache, target_hwnd=hwnd, one_shot=True)
        self.oneshot_worker.blocks_ready.connect(self._on_blocks_ready)
        self.oneshot_worker.overlay_geometry_changed.connect(self.overlay.set_physical_geometry)
        self.oneshot_worker.status_changed.connect(self._on_status_changed)
        self.oneshot_worker.metrics_updated.connect(self._on_metrics_updated)
        self.oneshot_worker.stage_profiled.connect(self._on_stage_profiled)
        self.oneshot_worker.error_occurred.connect(self._on_error)
        self.oneshot_worker.start()
        self.lbl_status.setText("Đang quét và dịch một lần")

    def toggle_overlay_visibility(self) -> None:
        hidden = not self.overlay.is_user_hidden
        self.overlay.set_user_hidden(hidden)
        self.mini_hud.set_overlay_hidden(hidden)
        self.btn_toggle_overlay.setIcon(
            get_icon("eye_off" if hidden else "eye", ACCENT if hidden else TEXT_SECONDARY, 16)
        )

    def _on_blocks_ready(self, rect, blocks, _latency_ms: int) -> None:
        self.overlay.set_blocks(rect, blocks)
        self.btn_clear_cache.setText(f"  Xóa bộ nhớ đệm ({len(self.cache)})")
        self.lbl_m_cache.setText(str(len(self.cache)))

    def _on_metrics_updated(
        self, ocr_ms: int, tr_ms: int, total_ms: int, cache_count: int, mode_label: str
    ) -> None:
        self.lbl_m_ocr.setText(f"{ocr_ms} ms")
        self.lbl_m_tr.setText(f"{tr_ms} ms")
        self.lbl_m_cache.setText(str(cache_count))
        self.lbl_m_mode.setText(mode_label)
        if self.mini_hud.isVisible():
            self.mini_hud.update_latency(total_ms, mode_label)

    def _on_stage_profiled(
        self, cap_ms: float, diff_ms: float, _ocr_ms: int, ttft_ms: int, total_tr_ms: int
    ) -> None:
        self.lbl_m_cap.setText(f"{cap_ms:.1f} / {diff_ms:.1f} ms")
        if 0 < ttft_ms < total_tr_ms - 40:
            self.lbl_m_tr.setText(f"{ttft_ms}/{total_tr_ms} ms")

    def _on_status_changed(self, text: str) -> None:
        self.lbl_status.setText(text)

    def _on_error(self, msg: str) -> None:
        self.lbl_status.setText(msg)
        self.lbl_state_dot.setStyleSheet(f"background-color: {ACCENT}; border-radius: 4px;")

    # ------------------------------------------------------------------ Hotkeys & Tray
    def _setup_hotkeys(self) -> None:
        actions = {
            "toggle_run": self.toggle_translation,
            "toggle_overlay": self.toggle_overlay_visibility,
            "one_shot": self.run_one_shot,
        }
        self._hotkey_listener = GlobalHotkeyManager(
            {
                "toggle_run": self.config.hotkey_toggle_run,
                "toggle_overlay": self.config.hotkey_toggle_overlay,
                "one_shot": self.config.hotkey_oneshot,
            }
        )
        self._hotkey_listener.triggered.connect(lambda name: actions[name]())
        self._hotkey_listener.start()
        if self._hotkey_listener.failed:
            self.lbl_status.setText(
                "Không đăng ký được phím tắt (đang bị ứng dụng khác dùng): "
                + ", ".join(self._hotkey_listener.failed)
            )

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(create_app_icon(64), self)
        menu = QMenu()
        act_show = QAction("Mở bảng điều khiển", self)
        act_show.triggered.connect(self._restore_from_tray)
        act_hud = QAction("Bật hoặc tắt thanh nổi", self)
        act_hud.triggered.connect(self._toggle_mini_hud)
        act_toggle = QAction("Bắt đầu hoặc dừng dịch (Alt+T)", self)
        act_toggle.triggered.connect(self.toggle_translation)
        act_overlay = QAction("Ẩn hoặc hiện lớp phủ (Alt+H)", self)
        act_overlay.triggered.connect(self.toggle_overlay_visibility)
        act_quit = QAction("Thoát", self)
        act_quit.triggered.connect(self._quit_app)

        menu.addAction(act_show)
        menu.addAction(act_hud)
        menu.addAction(act_toggle)
        menu.addAction(act_overlay)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()

    def _quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.stop_translation()
        self._stop_oneshot_worker()
        for t in (self._api_test_thread, self._gemini_models_thread, *list(self._retiring_threads)):
            if t is not None and t.isRunning():
                t.wait(1500)
        if self._hotkey_listener:
            self._hotkey_listener.stop()
        self.mini_hud.close()
        self.overlay.close()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        self._quit_app()
        event.accept()
