from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.config import AppConfig
from app.ui.tokens import (
    ACCENT,
    BG_RAISED,
    BORDER_HAIRLINE,
    BORDER_SUBTLE,
    FONT_BODY,
    FONT_MONO,
    RADIUS_CONTROL,
    RADIUS_INSET,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class ToggleSwitch(QCheckBox):
    """Tactile pill toggle switch built on the graphite + warm amber token ramp."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._track_w = 36
        self._track_h = 20

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        text_w = self.fontMetrics().horizontalAdvance(self.text()) + 12 if self.text() else 0
        return QSize(self._track_w + text_w + 4, max(self._track_h + 4, base.height()))

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tw, th = self._track_w, self._track_h
        y_off = (self.height() - th) / 2.0
        track_rect = QRectF(0.5, y_off + 0.5, tw - 1.0, th - 1.0)

        if not self.isEnabled():
            bg = QColor("#1c1c20")
            border = QColor("#26262b")
            knob = QColor("#4d4d55")
        elif self.isChecked():
            bg = QColor(ACCENT)
            border = QColor(ACCENT)
            knob = QColor("#111113")
        else:
            bg = QColor(BG_RAISED)
            border = QColor(BORDER_SUBTLE)
            knob = QColor("#8c8c96")

        p.setPen(QPen(border, 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(track_rect, (th - 1.0) / 2.0, (th - 1.0) / 2.0)

        r = (th - 6.0) / 2.0
        cx = (tw - r - 3.5) if self.isChecked() else (r + 3.5)
        cy = y_off + th / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(knob)
        p.drawEllipse(QPointF(cx, cy), r, r)

        if self.text():
            p.setPen(QColor(TEXT_PRIMARY) if self.isEnabled() else QColor(TEXT_MUTED))
            p.setFont(self.font())
            text_rect = QRectF(tw + 12, 0, self.width() - tw - 12, self.height())
            p.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )

        p.end()


class EngineOptionRow(QFrame):
    """Quiet stacked row for choosing the translation engine (replaces template 3-col cards)."""

    clicked = pyqtSignal(str)

    def __init__(
        self,
        provider_id: str,
        title: str,
        description: str,
        meta_right: str,
    ) -> None:
        super().__init__()
        self.provider_id = provider_id
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("engineRow")
        self.setFixedHeight(52)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(12)

        # Selection indicator dot (amber when active, quiet ring when inactive)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        row.addWidget(self._dot)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self._lbl_title = QLabel(title)
        self._lbl_title.setStyleSheet(
            f"font-family: '{FONT_BODY}'; font-size: 13px; font-weight: 500; color: {TEXT_PRIMARY};"
        )
        self._lbl_desc = QLabel(description)
        self._lbl_desc.setStyleSheet(
            f"font-family: '{FONT_BODY}'; font-size: 12px; font-weight: 400; color: {TEXT_MUTED};"
        )
        text_col.addWidget(self._lbl_title)
        text_col.addWidget(self._lbl_desc)
        row.addLayout(text_col, 1)

        self._lbl_meta = QLabel(meta_right)
        self._lbl_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_meta.setStyleSheet(
            f"font-family: '{FONT_MONO}'; font-size: 12px; font-weight: 400; color: {TEXT_SECONDARY};"
        )
        row.addWidget(self._lbl_meta)

        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                f"QFrame#engineRow {{ background-color: {BG_RAISED}; "
                f"border: 1px solid {BORDER_SUBTLE}; border-radius: {RADIUS_CONTROL}px; }}"
            )
            self._dot.setStyleSheet(
                f"background-color: {ACCENT}; border-radius: 4px; border: none;"
            )
            self._lbl_meta.setStyleSheet(
                f"font-family: '{FONT_MONO}'; font-size: 12px; font-weight: 500; color: {ACCENT};"
            )
        else:
            self.setStyleSheet(
                f"QFrame#engineRow {{ background-color: transparent; "
                f"border: 1px solid transparent; border-radius: {RADIUS_CONTROL}px; }}"
                f"QFrame#engineRow:hover {{ background-color: #1f1f23; border-color: {BORDER_HAIRLINE}; }}"
            )
            self._dot.setStyleSheet(
                f"background-color: transparent; border: 1px solid {TEXT_MUTED}; border-radius: 4px;"
            )
            self._lbl_meta.setStyleSheet(
                f"font-family: '{FONT_MONO}'; font-size: 12px; font-weight: 400; color: {TEXT_MUTED};"
            )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.provider_id)
        super().mousePressEvent(event)


class LiveOverlayPreview(QWidget):
    """The primary crafted visual moment: a nocturnal game viewport showing real-time overlay styling."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setFixedHeight(176)

    def set_config(self, config: AppConfig) -> None:
        self._config = config
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        w, h = float(self.width()), float(self.height())
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), float(RADIUS_INSET), float(RADIUS_INSET))
        p.setClipPath(clip)

        # Atmospheric twilight sea & ship lantern horizon (rich moment inside quiet graphite chrome)
        sky = QLinearGradient(0, 0, 0, h)
        sky.setColorAt(0.0, QColor("#111722"))
        sky.setColorAt(0.54, QColor("#1c2938"))
        sky.setColorAt(0.56, QColor("#131d27"))
        sky.setColorAt(1.0, QColor("#0d1218"))
        p.fillRect(self.rect(), sky)

        # Warm lantern glow on distant horizon
        lantern = QRadialGradient(QPointF(w * 0.78, h * 0.44), 120.0)
        lantern.setColorAt(0.0, QColor(229, 169, 60, 72))
        lantern.setColorAt(0.5, QColor(229, 169, 60, 22))
        lantern.setColorAt(1.0, QColor(229, 169, 60, 0))
        p.fillRect(self.rect(), lantern)

        # Subtle horizon line and distant silhouette ridge
        p.setPen(QPen(QColor(237, 237, 239, 18), 1.0))
        p.drawLine(QPointF(0, h * 0.55), QPointF(w, h * 0.55))

        ridge = QPainterPath()
        ridge.moveTo(0, h * 0.55)
        ridge.lineTo(w * 0.14, h * 0.45)
        ridge.lineTo(w * 0.28, h * 0.52)
        ridge.lineTo(w * 0.42, h * 0.48)
        ridge.lineTo(w * 0.56, h * 0.55)
        ridge.closeSubpath()
        p.fillPath(ridge, QColor(12, 16, 22, 160))

        # Signature detail: crisp calibrated optical viewfinder corner ticks
        tick_pen = QPen(QColor(229, 169, 60, 140), 1.2)
        p.setPen(tick_pen)
        m, t = 10.0, 10.0
        p.drawLine(QPointF(m, m), QPointF(m + t, m))
        p.drawLine(QPointF(m, m), QPointF(m, m + t))
        p.drawLine(QPointF(w - m, m), QPointF(w - m - t, m))
        p.drawLine(QPointF(w - m, m), QPointF(w - m, m + t))
        p.drawLine(QPointF(m, h - m), QPointF(m + t, h - m))
        p.drawLine(QPointF(m, h - m), QPointF(m, h - m - t))
        p.drawLine(QPointF(w - m, h - m), QPointF(w - m - t, h - m))
        p.drawLine(QPointF(w - m, h - m), QPointF(w - m, h - m - t))

        # Quiet sentence-case viewport caption (no uppercase tracked label)
        cap_font = QFont(FONT_BODY, 9)
        p.setFont(cap_font)
        p.setPen(QColor(237, 237, 239, 145))
        p.drawText(QRectF(24, 12, w - 48, 18), Qt.AlignmentFlag.AlignLeft, "Xem trước khung game")

        meta_font = QFont(FONT_MONO, 8)
        p.setFont(meta_font)
        p.setPen(QColor(237, 237, 239, 100))
        p.drawText(QRectF(24, 12, w - 48, 18), Qt.AlignmentFlag.AlignRight, "1920 × 1080")

        cfg = self._config
        pad = float(cfg.padding)
        radius = float(cfg.border_radius)
        custom_bg = QColor(cfg.bg_color)
        custom_fg = QColor(cfg.text_color)

        samples = [
            (
                QRectF(24, 44, min(w - 48, 288.0), 32),
                "Mục tiêu: Giương buồm hướng Đông",
                (20, 27, 36),
                (237, 237, 239),
            ),
            (
                QRectF(24, 88, w - 48, 60),
                "Thuyền trưởng Flameheart: Kho báu bị nguyền rủa đang chờ phía sau Màn Sương Ác Quỷ",
                (16, 18, 23),
                (237, 237, 239),
            ),
        ]

        font_pt = max(cfg.min_font_size, min(cfg.max_font_size, 11))
        font = QFont(cfg.font_family, font_pt)
        font.setWeight(QFont.Weight.Medium)
        flags = int(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.TextFlag.TextWordWrap
        )

        for box, text, adaptive_rgb, adaptive_fg in samples:
            if cfg.adaptive_bg:
                bg = QColor(*adaptive_rgb, cfg.bg_opacity)
                fg = QColor(*adaptive_fg, 255)
            else:
                bg = QColor(custom_bg.red(), custom_bg.green(), custom_bg.blue(), cfg.bg_opacity)
                fg = custom_fg

            path = QPainterPath()
            path.addRoundedRect(box, radius, radius)
            p.fillPath(path, bg)
            if cfg.show_border:
                p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 46), 1.0))
                p.drawPath(path)

            p.setFont(font)
            text_rect = box.adjusted(pad + 4, pad * 0.4, -(pad + 4), -pad * 0.4)
            if cfg.text_shadow:
                sh = QColor(0, 0, 0, 170) if fg.lightness() > 120 else QColor(255, 255, 255, 110)
                p.setPen(sh)
                p.drawText(text_rect.translated(1.0, 1.0), flags, text)
            p.setPen(fg)
            p.drawText(text_rect, flags, text)

        p.setClipping(False)
        p.setPen(QPen(QColor(BORDER_HAIRLINE), 1.0))
        p.drawRoundedRect(
            QRectF(0.5, 0.5, w - 1.0, h - 1.0),
            float(RADIUS_INSET),
            float(RADIUS_INSET),
        )
        p.end()
