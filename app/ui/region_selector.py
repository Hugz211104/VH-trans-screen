from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from app.ui.tokens import ACCENT, FONT_BODY, FONT_MONO, TEXT_PRIMARY


class RegionSelector(QWidget):
    """Fullscreen dimmed overlay for dragging a screen region; emits physical (x, y, w, h)."""

    region_selected = pyqtSignal(int, int, int, int)
    cancelled = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None)
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._dragging = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        virtual_rect = QRect()
        for screen in QGuiApplication.screens():
            virtual_rect = virtual_rect.united(screen.geometry())
        self.setGeometry(virtual_rect)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.pos()
            self._end = event.pos()
            self._dragging = True
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            self.close()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._end = event.pos()
            rect = QRect(self._start, self._end).normalized()
            if rect.width() >= 20 and rect.height() >= 20:
                global_tl = self.mapToGlobal(rect.topLeft())
                screen = QGuiApplication.screenAt(global_tl) or QGuiApplication.primaryScreen()
                dpr = screen.devicePixelRatio() if screen else 1.0
                sg = screen.geometry() if screen else QRect()
                px = int(round(sg.x() * dpr + (global_tl.x() - sg.x()) * dpr))
                py = int(round(sg.y() * dpr + (global_tl.y() - sg.y()) * dpr))
                pw = int(round(rect.width() * dpr))
                ph = int(round(rect.height() * dpr))
                self.region_selected.emit(px, py, pw, ph)
            else:
                self.cancelled.emit()
            self.close()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(10, 10, 12, 130))

        painter.setFont(QFont(FONT_BODY, 12, QFont.Weight.Medium))
        painter.setPen(QColor(TEXT_PRIMARY))
        painter.drawText(
            self.rect().adjusted(0, 28, 0, 0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            "Kéo chuột để chọn vùng dịch trên màn hình  —  ESC hoặc chuột phải để hủy",
        )

        if self._start and self._end:
            rect = QRect(self._start, self._end).normalized()
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

            accent = QColor(ACCENT)
            painter.fillRect(rect, QColor(accent.red(), accent.green(), accent.blue(), 22))
            painter.setPen(QPen(accent, 1.5))
            painter.drawRect(rect)

            label = f"{rect.width()} × {rect.height()}"
            painter.setFont(QFont(FONT_MONO, 10, QFont.Weight.Medium))
            badge_y = max(10, rect.top() - 24)
            painter.setPen(accent)
            painter.drawText(rect.left() + 4, badge_y + 16, label)

        painter.end()
