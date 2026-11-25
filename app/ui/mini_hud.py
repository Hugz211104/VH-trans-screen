from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from app.ui.icons import get_icon
from app.ui.tokens import (
    ACCENT,
    BG_RAISED,
    BORDER_SUBTLE,
    FONT_MONO,
    SEMANTIC_OK,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from app.win_utils import exclude_from_capture


class MiniHudBar(QWidget):
    """Draggable floating graphite HUD pill with vector line icons (excluded from OCR capture)."""

    toggle_run_clicked = pyqtSignal()
    oneshot_clicked = pyqtSignal()
    toggle_overlay_clicked = pyqtSignal()
    restore_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None)
        self._drag_pos: QPoint | None = None
        self._affinity_applied = False
        self._is_running = False
        self._overlay_hidden = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 8, 4)
        layout.setSpacing(6)

        self.lbl_dot = QLabel()
        self.lbl_dot.setFixedSize(8, 8)
        self.lbl_dot.setStyleSheet(f"background-color: {TEXT_MUTED}; border-radius: 4px;")
        layout.addWidget(self.lbl_dot)

        self.lbl_metric = QLabel("Sẵn sàng")
        self.lbl_metric.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-family: '{FONT_MONO}'; font-size: 11px; "
            f"font-weight: 500; background: transparent; padding-right: 6px;"
        )
        layout.addWidget(self.lbl_metric)

        btn_style = (
            f"QPushButton {{ background-color: {BG_RAISED}; border: 1px solid {BORDER_SUBTLE}; "
            f"border-radius: 13px; padding: 0px; }}"
            f"QPushButton:hover {{ background-color: #2f2f35; border-color: #46464f; }}"
        )

        self.btn_run = QPushButton()
        self.btn_run.setFixedSize(26, 26)
        self.btn_run.setIcon(get_icon("play", TEXT_PRIMARY, 14))
        self.btn_run.setIconSize(QSize(14, 14))
        self.btn_run.setToolTip("Bắt đầu hoặc dừng dịch (Alt+T)")
        self.btn_run.setStyleSheet(btn_style)
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.clicked.connect(self.toggle_run_clicked.emit)
        layout.addWidget(self.btn_run)

        self.btn_shot = QPushButton()
        self.btn_shot.setFixedSize(26, 26)
        self.btn_shot.setIcon(get_icon("zap", ACCENT, 14))
        self.btn_shot.setIconSize(QSize(14, 14))
        self.btn_shot.setToolTip("Dịch một lần (Alt+S)")
        self.btn_shot.setStyleSheet(btn_style)
        self.btn_shot.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shot.clicked.connect(self.oneshot_clicked.emit)
        layout.addWidget(self.btn_shot)

        self.btn_eye = QPushButton()
        self.btn_eye.setFixedSize(26, 26)
        self.btn_eye.setIcon(get_icon("eye", TEXT_SECONDARY, 14))
        self.btn_eye.setIconSize(QSize(14, 14))
        self.btn_eye.setToolTip("Ẩn hoặc hiện lớp phủ (Alt+H)")
        self.btn_eye.setStyleSheet(btn_style)
        self.btn_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_eye.clicked.connect(self.toggle_overlay_clicked.emit)
        layout.addWidget(self.btn_eye)

        self.btn_open = QPushButton()
        self.btn_open.setFixedSize(26, 26)
        self.btn_open.setIcon(get_icon("sliders", TEXT_SECONDARY, 14))
        self.btn_open.setIconSize(QSize(14, 14))
        self.btn_open.setToolTip("Mở bảng điều khiển")
        self.btn_open.setStyleSheet(btn_style)
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.clicked.connect(self.restore_clicked.emit)
        layout.addWidget(self.btn_open)

        self.adjustSize()
        self.move(28, 28)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._affinity_applied:
            self._affinity_applied = exclude_from_capture(int(self.winId()))

    def set_running(self, running: bool) -> None:
        self._is_running = running
        color = SEMANTIC_OK if running else TEXT_MUTED
        self.lbl_dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
        self.btn_run.setIcon(
            get_icon("stop" if running else "play", TEXT_PRIMARY, 14)
        )
        if not running:
            self.lbl_metric.setText("Đã dừng")
            self.adjustSize()

    def set_overlay_hidden(self, hidden: bool) -> None:
        self._overlay_hidden = hidden
        self.btn_eye.setIcon(
            get_icon("eye_off" if hidden else "eye", TEXT_SECONDARY, 14)
        )

    def update_latency(self, total_ms: int, mode_label: str) -> None:
        self.lbl_metric.setText(f"{total_ms} ms  {mode_label}")
        self.adjustSize()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0, 20, 20)
        p.fillPath(path, QColor(20, 20, 23, 240))
        p.setPen(QPen(QColor(BORDER_SUBTLE), 1.0))
        p.drawPath(path)
        p.end()
