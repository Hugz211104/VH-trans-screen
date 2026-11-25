from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QWidget

from app.config import AppConfig
from app.ocr_engine import OcrBlock
from app.win_utils import exclude_from_capture, make_overlay_clickthrough


def physical_rect_to_logical(rect: tuple[int, int, int, int]) -> QRect:
    """Convert physical screen rect (x, y, w, h) to Qt logical QRect."""
    px, py, pw, ph = rect
    cx = px + pw // 2
    cy = py + ph // 2
    for screen in QGuiApplication.screens():
        dpr = screen.devicePixelRatio()
        sg = screen.geometry()
        sx = int(round(sg.x() * dpr))
        sy = int(round(sg.y() * dpr))
        sw = int(round(sg.width() * dpr))
        sh = int(round(sg.height() * dpr))
        if sx <= cx <= sx + sw and sy <= cy <= sy + sh:
            return QRect(
                sg.x() + int(round((px - sx) / dpr)),
                sg.y() + int(round((py - sy) / dpr)),
                max(1, int(round(pw / dpr))),
                max(1, int(round(ph / dpr))),
            )

    primary = QGuiApplication.primaryScreen()
    dpr = primary.devicePixelRatio() if primary else 1.0
    return QRect(
        int(round(px / dpr)),
        int(round(py / dpr)),
        max(1, int(round(pw / dpr))),
        max(1, int(round(ph / dpr))),
    )


class TranslationOverlay(QWidget):
    """WindowTranslator-style transparent click-through overlay with tight 2D collision avoidance."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__(None)
        self._config = config
        self._blocks: list[OcrBlock] = []
        self._blocks_sig: tuple = ()
        self._layout: Optional[list] = None
        # Position-invariant font fit cache: (text, w_bucket, h_bucket, max_w_b, max_h_b, lines) -> (QFont, fit_w, fit_h)
        self._fit_cache: dict[tuple, tuple[QFont, float, float]] = {}
        self._user_hidden = False
        self._affinity_applied = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        make_overlay_clickthrough(hwnd)
        if not self._affinity_applied:
            self._affinity_applied = exclude_from_capture(hwnd)

    def update_config(self, config: AppConfig) -> None:
        self._config = config
        self._layout = None
        self._fit_cache.clear()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout = None

    def set_user_hidden(self, hidden: bool) -> None:
        self._user_hidden = hidden
        if hidden:
            self.hide()
        elif self._blocks:
            self.show()

    @property
    def is_user_hidden(self) -> bool:
        return self._user_hidden

    def set_physical_geometry(
        self, rect: Optional[tuple[int, int, int, int]], visible: bool
    ) -> None:
        if rect is None or not visible or self._user_hidden:
            self.hide()
            return
        logical = physical_rect_to_logical(rect)
        if self.geometry() != logical:
            self.setGeometry(logical)
        if not self.isVisible():
            self.show()

    @staticmethod
    def _blocks_signature(blocks: list[OcrBlock]) -> tuple:
        return tuple(
            (b.translated or b.text, round(b.nx, 3), round(b.ny, 3), round(b.nw, 3), round(b.nh, 3))
            for b in blocks
        )

    def set_blocks(
        self, rect: tuple[int, int, int, int], blocks: list[OcrBlock]
    ) -> None:
        if not self._user_hidden:
            logical = physical_rect_to_logical(rect)
            if self.geometry() != logical:
                self.setGeometry(logical)
            if not self.isVisible():
                self.show()
        sig = self._blocks_signature(blocks)
        if sig == self._blocks_sig:
            return
        self._blocks = blocks
        self._blocks_sig = sig
        self._layout = None
        self.update()

    def clear_blocks(self) -> None:
        self._blocks = []
        self._blocks_sig = ()
        self._layout = None
        self.update()

    def _make_font(self, pt: int) -> QFont:
        font = QFont(self._config.font_family, pt)
        font.setWeight(QFont.Weight.Medium)
        return font

    def _fit_font_and_size(
        self,
        text: str,
        box_w: float,
        box_h: float,
        max_w: float,
        max_h: float,
        pad_x: float,
        pad_y: float,
        line_count: int,
    ) -> tuple[QFont, float, float]:
        """Fit Vietnamese text tightly inside [box_w, box_h] without colliding with neighbors."""
        key = (
            text,
            int(box_w) // 4,
            int(box_h) // 4,
            int(max_w) // 4,
            int(max_h) // 4,
            line_count,
        )
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached

        cfg = self._config
        inner_w = max(20.0, box_w - pad_x * 2)
        inner_h = max(11.0, box_h - pad_y * 2)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap)

        est_line_h = inner_h / max(1, line_count)
        hi = int(max(cfg.min_font_size, min(cfg.max_font_size, est_line_h * 0.76)))
        # Allow auto-shrinking down to 7pt for tight UI labels before expanding the box
        floor_pt = max(6, min(cfg.min_font_size, 7))
        lo = min(hi, floor_pt)

        def fits_in(pt: int, avail_w: float, avail_h: float) -> bool:
            br = QFontMetrics(self._make_font(pt)).boundingRect(
                0, 0, int(avail_w), 2000, flags, text
            )
            return br.height() <= avail_h + 2 and br.width() <= avail_w + 2

        if fits_in(lo, inner_w, inner_h):
            # Binary search largest font that fits STRICTLY inside original tight box
            best = lo
            l_pt, h_pt = lo, hi
            while l_pt <= h_pt:
                mid = (l_pt + h_pt) // 2
                if fits_in(mid, inner_w, inner_h):
                    best = mid
                    l_pt = mid + 1
                else:
                    h_pt = mid - 1
            font = self._make_font(best)
            fit_w, fit_h = box_w, box_h
        else:
            # Try expanding width slightly (only into guaranteed free space before right neighbor!)
            avail_w = max(inner_w, max_w - pad_x * 2)
            avail_h = max(inner_h, max_h - pad_y * 2)
            font = self._make_font(floor_pt)
            fm = QFontMetrics(font)
            br = fm.boundingRect(0, 0, int(avail_w), 2000, flags, text)

            # Tighten width to what the wrapped text actually needs, capped at neighbor-safe max_w
            needed_w = min(max_w, max(box_w, br.width() + pad_x * 2 + 4))
            needed_h = min(max_h, max(box_h, br.height() + pad_y * 2 + 2))
            fit_w, fit_h = needed_w, needed_h

        if len(self._fit_cache) > 600:
            self._fit_cache.clear()
        res = (font, fit_w, fit_h)
        self._fit_cache[key] = res
        return res

    def _compute_neighbor_limits(
        self, raw_rects: list[QRectF], win_w: float, win_h: float, gap: float = 2.0
    ) -> list[tuple[float, float]]:
        """For each raw OCR box, find maximum width/height available before hitting any neighbor."""
        limits: list[tuple[float, float]] = []
        for i, a in enumerate(raw_rects):
            # Cap expansion conservatively so boxes stay sleek and never sprawl across the screen
            max_r = min(win_w, a.left() + a.width() * 1.28 + 20.0)
            max_b = min(win_h, a.top() + a.height() * 1.35 + 10.0)
            for j, b in enumerate(raw_rects):
                if i == j:
                    continue
                # Right neighbor on overlapping vertical band
                v_overlap = min(a.bottom(), b.bottom()) - max(a.top(), b.top())
                if v_overlap > 2.0 and b.left() >= a.left() + 4.0:
                    if b.left() > a.right() - 4.0:
                        max_r = min(max_r, b.left() - gap)
                # Lower neighbor on overlapping horizontal band
                h_overlap = min(max_r, b.right()) - max(a.left(), b.left())
                if h_overlap > 4.0 and b.top() >= a.top() + 4.0:
                    if b.top() > a.bottom() - 4.0:
                        max_b = min(max_b, b.top() - gap)
            max_w = max(a.width(), max_r - a.left())
            max_h = max(a.height(), max_b - a.top())
            limits.append((max_w, max_h))
        return limits

    @staticmethod
    def _resolve_remaining_overlaps(rects: list[QRectF], gap: float = 2.0) -> None:
        """Final safety pass: trim any residual touching edges so two boxes never stack."""
        n = len(rects)
        for i in range(n):
            a = rects[i]
            for j in range(i + 1, n):
                b = rects[j]
                ix = min(a.right(), b.right()) - max(a.left(), b.left())
                iy = min(a.bottom(), b.bottom()) - max(a.top(), b.top())
                if ix <= 0.0 or iy <= 0.0:
                    continue
                # Resolve along the smaller overlap axis so displacement is minimal
                if iy <= ix:
                    # Vertical overlap: upper box trims bottom, lower box trims top
                    upper, lower = (a, b) if a.top() <= b.top() else (b, a)
                    mid = (upper.bottom() + lower.top()) * 0.5
                    new_u_h = max(11.0, mid - gap * 0.5 - upper.top())
                    new_l_top = max(lower.top(), mid + gap * 0.5)
                    new_l_h = max(11.0, lower.bottom() - new_l_top)
                    upper.setHeight(new_u_h)
                    lower.setTop(new_l_top)
                    lower.setHeight(new_l_h)
                else:
                    # Horizontal overlap: left box trims right edge
                    left_b, right_b = (a, b) if a.left() <= b.left() else (b, a)
                    new_w = max(16.0, right_b.left() - gap - left_b.left())
                    if new_w < left_b.width():
                        left_b.setWidth(new_w)

    def _build_layout(self) -> list:
        """Precompute tight, collision-free boxes, pens, colors, fonts, and rects."""
        w = float(self.width())
        h = float(self.height())
        cfg = self._config
        # Keep padding compact vertically so stacked game menu rows never collide
        pad_x = float(min(max(0, cfg.padding), 6))
        pad_y = float(min(max(1.0, cfg.padding * 0.35), 2.5))
        radius = float(min(cfg.border_radius, 8))
        custom_bg = QColor(cfg.bg_color)
        custom_fg = QColor(cfg.text_color)

        items: list[tuple[OcrBlock, str, QRectF]] = []
        for block in self._blocks:
            text = (block.translated or block.text).strip()
            if not text:
                continue
            bx = max(0.0, block.nx * w - pad_x)
            by = max(0.0, block.ny * h - pad_y)
            bw = min(w - bx, max(16.0, block.nw * w + pad_x * 2))
            bh = min(h - by, max(12.0, block.nh * h + pad_y * 2))
            items.append((block, text, QRectF(bx, by, bw, bh)))

        if not items:
            return []

        # Sort top-to-bottom, left-to-right for deterministic neighbor resolution
        items.sort(key=lambda it: (it[2].top(), it[2].left()))
        raw_rects = [it[2] for it in items]
        limits = self._compute_neighbor_limits(raw_rects, w, h, gap=2.0)

        draw_rects: list[QRectF] = []
        fitted_sizes: list[tuple[float, float]] = []
        fonts: list[QFont] = []
        for (block, text, raw_box), (max_w, max_h) in zip(items, limits):
            font, fit_w, fit_h = self._fit_font_and_size(
                text, raw_box.width(), raw_box.height(), max_w, max_h, pad_x, pad_y, block.line_count
            )
            left = min(raw_box.left(), max(0.0, w - fit_w))
            top = min(raw_box.top(), max(0.0, h - fit_h))
            draw_rects.append(QRectF(left, top, fit_w, fit_h))
            fitted_sizes.append((fit_w, fit_h))
            fonts.append(font)

        # Eliminate any remaining pixel overlap between touching boxes
        self._resolve_remaining_overlaps(draw_rects, gap=2.0)

        layout = []
        for (block, text, _raw_box), draw_box, (fit_w, fit_h), font in zip(
            items, draw_rects, fitted_sizes, fonts
        ):
            if cfg.adaptive_bg:
                bg_color = QColor(*block.bg_rgb, cfg.bg_opacity)
                fg_color = QColor(*block.fg_rgb, 255)
            else:
                bg_color = QColor(custom_bg.red(), custom_bg.green(), custom_bg.blue(), cfg.bg_opacity)
                fg_color = custom_fg

            # Re-fit font whenever overlap resolution trimmed width or height below fitted size
            if draw_box.height() < fit_h - 1.0 or draw_box.width() < fit_w - 1.0:
                font, _, _ = self._fit_font_and_size(
                    text, draw_box.width(), draw_box.height(), draw_box.width(), draw_box.height(),
                    pad_x, pad_y, block.line_count,
                )

            eff_radius = min(radius, draw_box.height() * 0.35)
            path = QPainterPath()
            path.addRoundedRect(draw_box, eff_radius, eff_radius)
            border_pen = (
                QPen(QColor(fg_color.red(), fg_color.green(), fg_color.blue(), 60), 1.0)
                if cfg.show_border
                else None
            )
            text_rect = draw_box.adjusted(pad_x, pad_y * 0.5, -pad_x, -pad_y * 0.5)
            shadow_col = (
                (QColor(0, 0, 0, 170) if fg_color.lightness() > 120 else QColor(255, 255, 255, 110))
                if cfg.text_shadow
                else None
            )
            shadow_rect = text_rect.translated(1.0, 1.0) if shadow_col is not None else None
            layout.append((path, bg_color, border_pen, fg_color, font, text_rect, shadow_col, shadow_rect, text))
        return layout

    def paintEvent(self, event) -> None:
        if not self._blocks:
            return
        if self._layout is None:
            self._layout = self._build_layout()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        flags = int(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap
        )

        for path, bg_color, border_pen, fg_color, font, text_rect, shadow_col, shadow_rect, text in self._layout:
            painter.fillPath(path, bg_color)
            if border_pen is not None:
                painter.setPen(border_pen)
                painter.drawPath(path)
            painter.setFont(font)
            if shadow_col is not None and shadow_rect is not None:
                painter.setPen(shadow_col)
                painter.drawText(shadow_rect, flags, text)
            painter.setPen(fg_color)
            painter.drawText(text_rect, flags, text)

        painter.end()
