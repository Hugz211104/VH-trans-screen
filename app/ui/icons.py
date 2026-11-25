from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from app.ui.tokens import ACCENT, BG_RAISED, BORDER_SUBTLE, TEXT_PRIMARY, TEXT_SECONDARY

# Cached rendered icons by (name, color, size)
_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


def create_app_icon(size: int = 64) -> QIcon:
    """Flat, crisp optical-frame brand mark (no gradient tile, no glow, no emoji)."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    s = float(size)
    # Matte graphite square plate with hairline border
    p.setBrush(QColor(BG_RAISED))
    p.setPen(QPen(QColor(BORDER_SUBTLE), max(1.0, s * 0.03)))
    p.drawRoundedRect(QRectF(s * 0.06, s * 0.06, s * 0.88, s * 0.88), s * 0.22, s * 0.22)

    # Four viewfinder corner brackets (top-left & bottom-right in warm amber accent)
    pad = s * 0.22
    arm = s * 0.16
    stroke = max(1.8, s * 0.065)

    def _draw_corner(x: float, y: float, dx: float, dy: float, color: str) -> None:
        pen = QPen(QColor(color), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(x, y), QPointF(x + dx, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + dy))

    _draw_corner(pad, pad, arm, arm, ACCENT)
    _draw_corner(s - pad, pad, -arm, arm, TEXT_PRIMARY)
    _draw_corner(pad, s - pad, arm, -arm, TEXT_PRIMARY)
    _draw_corner(s - pad, s - pad, -arm, -arm, ACCENT)

    # Two centered subtitle bars inside the viewfinder frame
    bar_pen = QPen(QColor(TEXT_PRIMARY), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(bar_pen)
    p.drawLine(QPointF(s * 0.34, s * 0.46), QPointF(s * 0.66, s * 0.46))
    p.setPen(QPen(QColor(ACCENT), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.38, s * 0.59), QPointF(s * 0.62, s * 0.59))

    p.end()
    return QIcon(pix)


def get_icon(name: str, color: str = TEXT_SECONDARY, size: int = 16) -> QIcon:
    """Render a uniform Lucide-style vector line icon (1.5px stroke, 2x retina)."""
    key = (name, color, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    dpr = 2
    px = size * dpr
    pix = QPixmap(px, px)
    pix.fill(Qt.GlobalColor.transparent)
    pix.setDevicePixelRatio(dpr)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # Normalize coordinate space to 24x24 (exact Lucide viewBox)
    scale = size / 24.0
    p.scale(scale, scale)

    pen = QPen(
        QColor(color),
        1.75,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    _draw_glyph(p, name)
    p.end()

    icon = QIcon(pix)
    _ICON_CACHE[key] = icon
    return icon


def _draw_glyph(p: QPainter, name: str) -> None:
    if name == "gauge":
        # Speedometer / scan control
        path = QPainterPath()
        path.arcMoveTo(QRectF(3, 4, 18, 18), 210)
        path.arcTo(QRectF(3, 4, 18, 18), 210, -240)
        p.drawPath(path)
        p.drawLine(QPointF(12, 13), QPointF(16.5, 8.5))
        p.drawEllipse(QPointF(12, 13), 1.4, 1.4)

    elif name == "key":
        # Specific icon for API keys & models
        p.drawEllipse(QPointF(7.5, 14.5), 4.0, 4.0)
        p.drawLine(QPointF(10.5, 11.5), QPointF(19.5, 4.5))
        p.drawLine(QPointF(16.5, 6.8), QPointF(18.8, 9.1))
        p.drawLine(QPointF(19.5, 4.5), QPointF(21.5, 6.5))

    elif name == "layers":
        # Overlay layers
        top = QPainterPath()
        top.moveTo(12, 3)
        top.lineTo(21, 8)
        top.lineTo(12, 13)
        top.lineTo(3, 8)
        top.closeSubpath()
        p.drawPath(top)
        mid = QPainterPath()
        mid.moveTo(3, 12.5)
        mid.lineTo(12, 17.5)
        mid.lineTo(21, 12.5)
        p.drawPath(mid)
        bot = QPainterPath()
        bot.moveTo(3, 16.5)
        bot.lineTo(12, 21.5)
        bot.lineTo(21, 16.5)
        p.drawPath(bot)

    elif name == "shield":
        # Filter & system controls
        path = QPainterPath()
        path.moveTo(12, 3)
        path.lineTo(19, 6)
        path.lineTo(19, 12)
        path.quadTo(19, 17.5, 12, 21)
        path.quadTo(5, 17.5, 5, 12)
        path.lineTo(5, 6)
        path.closeSubpath()
        p.drawPath(path)

    elif name == "play":
        path = QPainterPath()
        path.moveTo(6.5, 4.5)
        path.lineTo(19.5, 12)
        path.lineTo(6.5, 19.5)
        path.closeSubpath()
        p.drawPath(path)

    elif name == "stop":
        p.drawRoundedRect(QRectF(5.5, 5.5, 13, 13), 2, 2)

    elif name == "zap":
        path = QPainterPath()
        path.moveTo(13, 2.5)
        path.lineTo(4.5, 13.5)
        path.lineTo(11.5, 13.5)
        path.lineTo(10.5, 21.5)
        path.lineTo(19.5, 10.5)
        path.lineTo(12.5, 10.5)
        path.closeSubpath()
        p.drawPath(path)

    elif name == "eye":
        path = QPainterPath()
        path.moveTo(2.5, 12)
        path.quadTo(12, 3.5, 21.5, 12)
        path.quadTo(12, 20.5, 2.5, 12)
        p.drawPath(path)
        p.drawEllipse(QPointF(12, 12), 2.8, 2.8)

    elif name == "eye_off":
        path = QPainterPath()
        path.moveTo(2.5, 12)
        path.quadTo(12, 4.5, 21.5, 12)
        p.drawPath(path)
        p.drawLine(QPointF(4, 4), QPointF(20, 20))

    elif name == "pin":
        p.drawLine(QPointF(12, 16), QPointF(12, 21))
        path = QPainterPath()
        path.moveTo(6.5, 16)
        path.lineTo(17.5, 16)
        path.lineTo(15.5, 11)
        path.lineTo(15.5, 5.5)
        path.lineTo(17, 4)
        path.lineTo(7, 4)
        path.lineTo(8.5, 5.5)
        path.lineTo(8.5, 11)
        path.closeSubpath()
        p.drawPath(path)

    elif name == "monitor":
        p.drawRoundedRect(QRectF(3, 4, 18, 12), 2, 2)
        p.drawLine(QPointF(8, 20), QPointF(16, 20))
        p.drawLine(QPointF(12, 16), QPointF(12, 20))

    elif name == "crop":
        p.drawLine(QPointF(7, 3), QPointF(7, 17))
        p.drawLine(QPointF(7, 17), QPointF(21, 17))
        p.drawLine(QPointF(3, 7), QPointF(17, 7))
        p.drawLine(QPointF(17, 7), QPointF(17, 21))

    elif name == "refresh":
        path = QPainterPath()
        path.arcMoveTo(QRectF(4, 4, 16, 16), 35)
        path.arcTo(QRectF(4, 4, 16, 16), 35, 290)
        p.drawPath(path)
        p.drawLine(QPointF(18.5, 4.5), QPointF(18.5, 9.5))
        p.drawLine(QPointF(18.5, 9.5), QPointF(13.5, 9.5))

    elif name == "trash":
        p.drawLine(QPointF(4, 6), QPointF(20, 6))
        p.drawLine(QPointF(9, 6), QPointF(9, 4))
        p.drawLine(QPointF(9, 4), QPointF(15, 4))
        p.drawLine(QPointF(15, 4), QPointF(15, 6))
        p.drawRoundedRect(QRectF(6, 6, 12, 14), 1.5, 1.5)
        p.drawLine(QPointF(10, 10), QPointF(10, 16))
        p.drawLine(QPointF(14, 10), QPointF(14, 16))

    elif name == "check":
        p.drawLine(QPointF(4.5, 12.5), QPointF(9.5, 17.5))
        p.drawLine(QPointF(9.5, 17.5), QPointF(19.5, 6.5))

    elif name == "save":
        p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
        p.drawRect(QRectF(8, 4, 7, 5))
        p.drawRect(QRectF(7, 13, 10, 7))

    elif name == "sliders":
        p.drawLine(QPointF(4, 7), QPointF(20, 7))
        p.drawLine(QPointF(4, 12), QPointF(20, 12))
        p.drawLine(QPointF(4, 17), QPointF(20, 17))
        p.setBrush(QColor(BG_RAISED))
        p.drawEllipse(QPointF(9, 7), 2.0, 2.0)
        p.drawEllipse(QPointF(15, 12), 2.0, 2.0)
        p.drawEllipse(QPointF(10, 17), 2.0, 2.0)

    elif name == "frame":
        # Viewfinder / full-screen preview icon
        p.drawLine(QPointF(4, 8), QPointF(4, 4))
        p.drawLine(QPointF(4, 4), QPointF(8, 4))
        p.drawLine(QPointF(16, 4), QPointF(20, 4))
        p.drawLine(QPointF(20, 4), QPointF(20, 8))
        p.drawLine(QPointF(4, 16), QPointF(4, 20))
        p.drawLine(QPointF(4, 20), QPointF(8, 20))
        p.drawLine(QPointF(16, 20), QPointF(20, 20))
        p.drawLine(QPointF(20, 20), QPointF(20, 16))
