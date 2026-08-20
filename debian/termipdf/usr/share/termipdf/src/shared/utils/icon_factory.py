"""
icon_factory.py — Vector icon renderer for TermiPDF.

Draws icons directly with QPainter (no asset files needed). Every icon is
a stroke + optional fill drawn into a QRectF using QPainterPath. All icons
share the same stroke width proportional to the requested size, so they
look consistent across 16px, 24px, and 32px renderings.

Usage:
    icon = IconFactory.get("pen", size=24, color="#a6e3a1")
    button.setIcon(icon)
"""
from __future__ import annotations

from typing import Callable, Dict

from PyQt6.QtCore import QRectF, Qt, QPointF, QSize
from PyQt6.QtGui import (
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QColor,
    QPixmap,
    QBrush,
    QPolygonF,
)


# Default stroke widths (proportional to size)
_STROKE_RATIO = 0.12   # icon stroke width = size * this ratio
_FILL_RATIO   = 0.20   # small fill thickness for accent shapes

# Pen palette used inside icons
class IconPalette:
    STROKE = "#cdd6f4"
    FILL   = "#89b4fa"
    ACCENT = "#cba6f7"
    MUTED  = "#6c7086"
    RED    = "#f38ba8"
    GREEN  = "#a6e3a1"
    YELLOW = "#f9e2af"


PainterFn = Callable[[QPainter, QRectF, QColor], None]


# --------------------------------------------------------------------- helpers
def _setup_painter(p: QPainter, rect: QRectF, color: QColor, *, fill=None):
    """Configure common painter state. Returns the pen."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    side = min(rect.width(), rect.height())
    pen = QPen(color)
    pen.setWidthF(max(1.2, side * _STROKE_RATIO))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    if fill is not None:
        p.setBrush(QBrush(fill))
    else:
        p.setBrush(Qt.BrushStyle.NoBrush)
    return pen


def _path_polyline(points, closed=False):
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    for pt in points[1:]:
        path.lineTo(pt)
    if closed:
        path.closeSubpath()
    return path


# =====================================================================
# Per-icon painters
# =====================================================================

def _p_open(p, r, c):
    _setup_painter(p, r, c)
    # Folder outline
    p.drawRoundedRect(QRectF(r.left(), r.top() + r.height() * 0.2,
                             r.width(), r.height() * 0.75), 2, 2)
    # Folder tab
    p.drawLine(QPointF(r.left() + r.width() * 0.05, r.top() + r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.05, r.top() + r.height() * 0.1))
    p.drawLine(QPointF(r.left() + r.width() * 0.05, r.top() + r.height() * 0.1),
               QPointF(r.left() + r.width() * 0.45, r.top() + r.height() * 0.1))
    p.drawLine(QPointF(r.left() + r.width() * 0.45, r.top() + r.height() * 0.1),
               QPointF(r.left() + r.width() * 0.55, r.top() + r.height() * 0.2))


def _p_save(p, r, c):
    _setup_painter(p, r, c)
    # Floppy disk body
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.1, r.top() + r.height() * 0.15,
                             r.width() * 0.8, r.height() * 0.75), 2, 2)
    # Slider notch (top)
    p.drawRect(QRectF(r.left() + r.width() * 0.25, r.top() + r.height() * 0.05,
                      r.width() * 0.5, r.height() * 0.2))
    # Label area (bottom)
    p.drawRect(QRectF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.55,
                      r.width() * 0.6, r.height() * 0.3))


def _p_undo(p, r, c):
    _setup_painter(p, r, c)
    # Curved arrow looping left
    arrow_size = r.height() * 0.25
    path = QPainterPath()
    cx, cy = r.center().x(), r.center().y()
    path.moveTo(cx + r.width() * 0.3, cy - r.height() * 0.05)
    path.cubicTo(cx - r.width() * 0.1, cy - r.height() * 0.35,
                 cx - r.width() * 0.35, cy + r.height() * 0.1,
                 cx - r.width() * 0.1, cy + r.height() * 0.3)
    p.drawPath(path)
    # Arrow head
    head = QPolygonF([
        QPointF(cx - r.width() * 0.1 - arrow_size, cy + r.height() * 0.2),
        QPointF(cx - r.width() * 0.1, cy + r.height() * 0.3),
        QPointF(cx - r.width() * 0.05, cy + r.height() * 0.1),
    ])
    p.drawPolygon(head)


def _p_redo(p, r, c):
    _setup_painter(p, r, c)
    arrow_size = r.height() * 0.25
    path = QPainterPath()
    cx, cy = r.center().x(), r.center().y()
    path.moveTo(cx - r.width() * 0.3, cy - r.height() * 0.05)
    path.cubicTo(cx + r.width() * 0.1, cy - r.height() * 0.35,
                 cx + r.width() * 0.35, cy + r.height() * 0.1,
                 cx + r.width() * 0.1, cy + r.height() * 0.3)
    p.drawPath(path)
    head = QPolygonF([
        QPointF(cx + r.width() * 0.1 + arrow_size, cy + r.height() * 0.2),
        QPointF(cx + r.width() * 0.1, cy + r.height() * 0.3),
        QPointF(cx + r.width() * 0.05, cy + r.height() * 0.1),
    ])
    p.drawPolygon(head)


def _p_prev(p, r, c):
    _setup_painter(p, r, c)
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.18
    tri = QPolygonF([
        QPointF(cx + s, cy - s),
        QPointF(cx + s, cy + s),
        QPointF(cx - s * 0.5, cy),
    ])
    bar = QRectF(cx - s * 1.1, cy - s * 0.7, s * 0.4, s * 1.4)
    p.drawPolygon(tri)
    p.drawRect(bar)


def _p_next(p, r, c):
    _setup_painter(p, r, c)
    cx, cy = r.center().x(), r.center().y()
    s = r.width() * 0.18
    tri = QPolygonF([
        QPointF(cx - s, cy - s),
        QPointF(cx - s, cy + s),
        QPointF(cx + s * 0.5, cy),
    ])
    bar = QRectF(cx + s * 0.7, cy - s * 0.7, s * 0.4, s * 1.4)
    p.drawPolygon(tri)
    p.drawRect(bar)


def _p_zoom_in(p, r, c):
    _setup_painter(p, r, c)
    cx, cy = r.left() + r.width() * 0.4, r.top() + r.height() * 0.4
    rad = r.width() * 0.28
    p.drawEllipse(QPointF(cx, cy), rad, rad)
    # +
    p.drawLine(QPointF(cx - rad * 0.5, cy), QPointF(cx + rad * 0.5, cy))
    p.drawLine(QPointF(cx, cy - rad * 0.5), QPointF(cx, cy + rad * 0.5))
    # handle
    p.drawLine(QPointF(cx + rad * 0.7, cy + rad * 0.7),
               QPointF(r.right() - 2, r.bottom() - 2))


def _p_zoom_out(p, r, c):
    _setup_painter(p, r, c)
    cx, cy = r.left() + r.width() * 0.4, r.top() + r.height() * 0.4
    rad = r.width() * 0.28
    p.drawEllipse(QPointF(cx, cy), rad, rad)
    # −
    p.drawLine(QPointF(cx - rad * 0.5, cy), QPointF(cx + rad * 0.5, cy))
    p.drawLine(QPointF(cx + rad * 0.7, cy + rad * 0.7),
               QPointF(r.right() - 2, r.bottom() - 2))


def _p_zoom_fit(p, r, c):
    _setup_painter(p, r, c)
    # Four corner brackets
    m = 0.15
    l = 0.30
    corners = [
        (r.left() + r.width() * m, r.top() + r.height() * m, 1, 1),
        (r.right() - r.width() * m, r.top() + r.height() * m, -1, 1),
        (r.left() + r.width() * m, r.bottom() - r.height() * m, 1, -1),
        (r.right() - r.width() * m, r.bottom() - r.height() * m, -1, -1),
    ]
    for x, y, sx, sy in corners:
        p.drawLine(QPointF(x, y), QPointF(x + sx * r.width() * l, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + sy * r.height() * l))


def _p_pen(p, r, c):
    _setup_painter(p, r, c)
    # Diagonal pen body
    path = QPainterPath()
    x1, y1 = r.left() + r.width() * 0.20, r.bottom() - r.height() * 0.20
    x2, y2 = r.right() - r.width() * 0.15, r.top() + r.height() * 0.15
    path.moveTo(x1, y1)
    path.lineTo(x1 + r.width() * 0.10, y1 - r.height() * 0.10)
    path.lineTo(x2 + r.width() * 0.10, y2 - r.height() * 0.10)
    path.lineTo(x2, y2)
    path.closeSubpath()
    p.drawPath(path)
    # Tip line
    p.drawLine(QPointF(x2 + r.width() * 0.10, y2 - r.height() * 0.10),
               QPointF(r.right() - 2, r.top() + 2))


def _p_eraser(p, r, c):
    """Diagonal-tipped rubber eraser (recognizable pink/blue eraser shape).

    Two halves: the tip (lighter) and the body (darker), with a metal
    band in the middle. Drawn at 45° so the tip points up-left, like
    the classic office-eraser icon. Much clearer than just a rectangle
    — the user previously couldn't tell which toolbar button was the
    eraser because the rect-with-divider silhouette looked like a
    generic shape.
    """
    _setup_painter(p, r, c)
    # Two rectangles: the tip (lighter stroked) and the body (filled)
    # arranged diagonally. We compute corners from r.
    pad = 0.12
    body = QRectF(r.left() + r.width() * pad,
                  r.top() + r.height() * (0.5 - pad),
                  r.width() * (1 - 2 * pad),
                  r.height() * (1 - 2 * pad))
    # Body outline (the rubber part we hold)
    p.drawRoundedRect(body, 2, 2)
    # A short cross-line so the rectangle reads as erasable (not generic)
    p.drawLine(QPointF(body.left() + body.width() * 0.25,
                       body.bottom() - body.height() * 0.2),
               QPointF(body.left() + body.width() * 0.25,
                       body.bottom() - body.height() * 0.05))
    p.drawLine(QPointF(body.left() + body.width() * 0.25,
                       body.bottom() - body.height() * 0.05),
               QPointF(body.left() + body.width() * 0.75,
                       body.bottom() - body.height() * 0.05))
    # Eraser mark — small arc trailing off the tip to show "rubbed"
    mark = QPainterPath()
    mark.moveTo(body.left() + 2, body.top() - 2)
    mark.cubicTo(body.left() - 3, body.top() - 4,
                 body.left() - 5, body.top() - 1,
                 body.left() - 4, body.top() + 2)
    p.drawPath(mark)


def _p_highlight(p, r, c):
    # Filled yellow bar with darker outline
    fill = QColor("#f9e2af")
    fill.setAlpha(180)
    _setup_painter(p, r, QColor("#f9e2af"), fill=fill)
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.1,
                              r.top() + r.height() * 0.3,
                              r.width() * 0.8, r.height() * 0.4), 3, 3)


def _p_text(p, r, c):
    _setup_painter(p, r, c)
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.3),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.3))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.55),
               QPointF(r.right() - r.width() * 0.4, r.top() + r.height() * 0.55))
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.8),
               QPointF(r.right() - r.width() * 0.3, r.top() + r.height() * 0.8))


def _p_note(p, r, c):
    fill = QColor("#f9e2af")
    _setup_painter(p, r, QColor("#f9e2af"), fill=fill)
    # Note rectangle
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.15,
                              r.top() + r.height() * 0.15,
                              r.width() * 0.7, r.height() * 0.7), 3, 3)
    # Lines inside
    pen = p.pen()
    p.setPen(QPen(QColor("#11111b"), max(1.0, r.width() * 0.06)))
    p.drawLine(QPointF(r.left() + r.width() * 0.25, r.top() + r.height() * 0.4),
               QPointF(r.right() - r.width() * 0.25, r.top() + r.height() * 0.4))
    p.drawLine(QPointF(r.left() + r.width() * 0.25, r.top() + r.height() * 0.6),
               QPointF(r.right() - r.width() * 0.25, r.top() + r.height() * 0.6))


def _p_qr(p, r, c):
    _setup_painter(p, r, c)
    s = r.width() * 0.25
    p.drawRect(QRectF(r.left() + r.width() * 0.05, r.top() + r.height() * 0.05, s, s))
    p.drawRect(QRectF(r.right() - r.width() * 0.05 - s, r.top() + r.height() * 0.05, s, s))
    p.drawRect(QRectF(r.left() + r.width() * 0.05, r.bottom() - r.height() * 0.05 - s, s, s))
    p.drawLine(QPointF(r.left() + r.width() * 0.4, r.top() + r.height() * 0.5),
               QPointF(r.right() - r.width() * 0.15, r.top() + r.height() * 0.5))
    p.drawLine(QPointF(r.left() + r.width() * 0.4, r.top() + r.height() * 0.7),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.7))


def _p_stamp(p, r, c):
    _setup_painter(p, r, c)
    # Stamp body (square + handle on top)
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.2,
                              r.top() + r.height() * 0.35,
                              r.width() * 0.6, r.height() * 0.55), 3, 3)
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.4,
                              r.top() + r.height() * 0.1,
                              r.width() * 0.2, r.height() * 0.3), 2, 2)


def _p_rect(p, r, c):
    _setup_painter(p, r, c)
    p.drawRect(QRectF(r.left() + r.width() * 0.1,
                      r.top() + r.height() * 0.2,
                      r.width() * 0.8, r.height() * 0.6))


def _p_ellipse(p, r, c):
    _setup_painter(p, r, c)
    p.drawEllipse(QRectF(r.left() + r.width() * 0.1,
                          r.top() + r.height() * 0.2,
                          r.width() * 0.8, r.height() * 0.6))


def _p_arrow(p, r, c):
    _setup_painter(p, r, c)
    # Diagonal arrow line
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.2),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2))
    # Arrow head at top-right
    s = r.width() * 0.12
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2),
               QPointF(r.right() - r.width() * 0.2 - s, r.top() + r.height() * 0.2))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2),
               QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2 + s))


def _p_signature(p, r, c):
    _setup_painter(p, r, c)
    # Curly signature path
    path = QPainterPath()
    path.moveTo(r.left() + r.width() * 0.1, r.top() + r.height() * 0.65)
    path.cubicTo(r.left() + r.width() * 0.2, r.top() + r.height() * 0.2,
                 r.left() + r.width() * 0.4, r.top() + r.height() * 0.9,
                 r.left() + r.width() * 0.55, r.top() + r.height() * 0.5)
    path.cubicTo(r.left() + r.width() * 0.65, r.top() + r.height() * 0.3,
                 r.left() + r.width() * 0.75, r.top() + r.height() * 0.7,
                 r.left() + r.width() * 0.9, r.top() + r.height() * 0.4)
    p.drawPath(path)


def _p_search(p, r, c):
    _setup_painter(p, r, c)
    cx, cy = r.left() + r.width() * 0.4, r.top() + r.height() * 0.4
    rad = r.width() * 0.25
    p.drawEllipse(QPointF(cx, cy), rad, rad)
    p.drawLine(QPointF(cx + rad * 0.7, cy + rad * 0.7),
               QPointF(r.right() - 2, r.bottom() - 2))


def _p_print(p, r, c):
    _setup_painter(p, r, c)
    p.drawRect(QRectF(r.left() + r.width() * 0.2,
                      r.top() + r.height() * 0.3,
                      r.width() * 0.6, r.height() * 0.45))
    p.drawRect(QRectF(r.left() + r.width() * 0.3,
                      r.top() + r.height() * 0.5,
                      r.width() * 0.4, r.height() * 0.25))
    p.drawLine(QPointF(r.left() + r.width() * 0.1, r.top() + r.height() * 0.35),
               QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.35))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.35),
               QPointF(r.right() - r.width() * 0.1, r.top() + r.height() * 0.35))


def _p_rotate(p, r, c):
    """Rotate-clockwise icon — circular arrow with a chevron tip."""
    import math
    _setup_painter(p, r, c)
    cx, cy = r.center().x(), r.center().y()
    # Ring that goes 270° around the center (3/4 of a full circle)
    radius = r.width() * 0.30
    # Use QPainterPath so we can leave a gap for the arrowhead.
    path = QPainterPath()
    # Start angle: 30° from top, sweep to -60° (i.e. 3/4 turn)
    start_a = math.radians(60)    # bottom-right
    span = math.radians(270)      # 3/4 turn
    end_a = start_a + span
    # Convert to Qt's coordinate system (y goes down)
    x0 = cx + radius * math.cos(start_a)
    y0 = cy + radius * math.sin(start_a)
    x1 = cx + radius * math.cos(end_a)
    y1 = cy + radius * math.sin(end_a)
    path.moveTo(x0, y0)
    # Qt's arcTo takes angles in 1/16th-degree units; sweep counter-clockwise
    # gives the long way around. Use drawArc instead for clarity.
    from PyQt6.QtCore import QRectF as _QRectF, Qt as _Qt
    arc_rect = _QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
    # 60 * 16 = 960 start, 270 * 16 = 4320 span
    p.drawArc(arc_rect, int(start_a * 180 / math.pi * 16),
              int(span * 180 / math.pi * 16))
    # Arrowhead at the end of the arc, pointing in the direction of motion.
    # The tangent at end_a points along the arc — roughly outward.
    tx = -math.sin(end_a)
    ty = math.cos(end_a)
    head_len = r.width() * 0.14
    head_w = r.width() * 0.10
    tip = QPointF(x1 + tx * head_len, y1 + ty * head_len)
    # base of arrow head, perpendicular to tangent
    perp_x, perp_y = -ty, tx
    base_cx = x1
    base_cy = y1
    a = QPointF(base_cx + perp_x * head_w / 2,
                base_cy + perp_y * head_w / 2)
    b = QPointF(base_cx - perp_x * head_w / 2,
                base_cy - perp_y * head_w / 2)
    poly = QPolygonF([tip, a, b])
    # Fill the arrowhead so it's visible
    p.setBrush(c)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPolygon(poly)
    # Reset brush back to NoBrush so subsequent draws are stroke-only
    p.setBrush(Qt.BrushStyle.NoBrush)


def _p_sun(p, r, c):
    _setup_painter(p, r, c)
    cx, cy = r.center().x(), r.center().y()
    rad = r.width() * 0.18
    p.drawEllipse(QPointF(cx, cy), rad, rad)
    for i in range(8):
        angle = i * 3.14159 / 4
        x1 = cx + (rad + r.width() * 0.08) * __import__("math").cos(angle)
        y1 = cy + (rad + r.width() * 0.08) * __import__("math").sin(angle)
        x2 = cx + (rad + r.width() * 0.20) * __import__("math").cos(angle)
        y2 = cy + (rad + r.width() * 0.20) * __import__("math").sin(angle)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _p_moon(p, r, c):
    _setup_painter(p, r, c)
    cx, cy = r.center().x(), r.center().y()
    rad = r.width() * 0.30
    path = QPainterPath()
    path.moveTo(cx - rad * 0.4, cy - rad)
    path.cubicTo(cx + rad * 0.6, cy - rad,
                 cx + rad * 0.6, cy + rad,
                 cx - rad * 0.4, cy + rad)
    path.cubicTo(cx - rad * 0.2, cy + rad * 0.4,
                 cx - rad * 0.2, cy - rad * 0.4,
                 cx - rad * 0.4, cy - rad)
    p.drawPath(path)


def _p_fullscreen(p, r, c):
    _setup_painter(p, r, c)
    m = 0.10
    l = 0.35
    corners = [
        (r.left() + r.width() * m, r.top() + r.height() * m, 1, 1),
        (r.right() - r.width() * m, r.top() + r.height() * m, -1, 1),
        (r.left() + r.width() * m, r.bottom() - r.height() * m, 1, -1),
        (r.right() - r.width() * m, r.bottom() - r.height() * m, -1, -1),
    ]
    for x, y, sx, sy in corners:
        p.drawLine(QPointF(x, y), QPointF(x + sx * r.width() * l, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + sy * r.height() * l))


def _p_toc(p, r, c):
    _setup_painter(p, r, c)
    p.drawRect(QRectF(r.left() + r.width() * 0.1,
                      r.top() + r.height() * 0.1,
                      r.width() * 0.8, r.height() * 0.8))
    # Indented rows
    for i, (y_frac, indent) in enumerate([(0.3, 0.15), (0.5, 0.30), (0.7, 0.15)]):
        x_start = r.left() + r.width() * indent
        x_end = r.right() - r.width() * (0.20 if i == 1 else 0.10)
        y = r.top() + r.height() * y_frac
        p.drawLine(QPointF(x_start, y), QPointF(x_end, y))


def _p_terminal(p, r, c):
    _setup_painter(p, r, c)
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.1,
                              r.top() + r.height() * 0.1,
                              r.width() * 0.8, r.height() * 0.8), 3, 3)
    # Prompt arrow
    p.drawLine(QPointF(r.left() + r.width() * 0.25, r.top() + r.height() * 0.4),
               QPointF(r.left() + r.width() * 0.20, r.top() + r.height() * 0.5))
    p.drawLine(QPointF(r.left() + r.width() * 0.20, r.top() + r.height() * 0.5),
               QPointF(r.left() + r.width() * 0.25, r.top() + r.height() * 0.6))
    # Cursor
    p.drawLine(QPointF(r.left() + r.width() * 0.40, r.top() + r.height() * 0.4),
               QPointF(r.left() + r.width() * 0.40, r.top() + r.height() * 0.6))


def _p_close(p, r, c):
    _setup_painter(p, r, c)
    p.drawLine(QPointF(r.left() + r.width() * 0.2, r.top() + r.height() * 0.2),
               QPointF(r.right() - r.width() * 0.2, r.bottom() - r.height() * 0.2))
    p.drawLine(QPointF(r.right() - r.width() * 0.2, r.top() + r.height() * 0.2),
               QPointF(r.left() + r.width() * 0.2, r.bottom() - r.height() * 0.2))


def _p_clear(p, r, c):
    _setup_painter(p, r, c)
    # Eraser/broom: rectangle with diagonal
    p.drawRoundedRect(QRectF(r.left() + r.width() * 0.15,
                              r.top() + r.height() * 0.3,
                              r.width() * 0.7, r.height() * 0.45), 2, 2)
    p.drawLine(QPointF(r.left() + r.width() * 0.3, r.bottom() - r.height() * 0.25),
               QPointF(r.right() - r.width() * 0.3, r.top() + r.height() * 0.3))


def _p_dock_bottom(p, r, c):
    _setup_painter(p, r, c)
    # Square with bar at bottom
    p.drawRect(QRectF(r.left() + r.width() * 0.1,
                      r.top() + r.height() * 0.1,
                      r.width() * 0.8, r.height() * 0.55))
    p.drawLine(QPointF(r.left() + r.width() * 0.1, r.bottom() - r.height() * 0.35),
               QPointF(r.right() - r.width() * 0.1, r.bottom() - r.height() * 0.35))
    p.drawLine(QPointF(r.left() + r.width() * 0.3, r.bottom() - r.height() * 0.10),
               QPointF(r.right() - r.width() * 0.3, r.bottom() - r.height() * 0.10))


def _p_dock_left(p, r, c):
    _setup_painter(p, r, c)
    p.drawRect(QRectF(r.left() + r.width() * 0.1,
                      r.top() + r.height() * 0.1,
                      r.width() * 0.55, r.height() * 0.8))
    p.drawLine(QPointF(r.left() + r.width() * 0.35, r.top() + r.height() * 0.1),
               QPointF(r.left() + r.width() * 0.35, r.bottom() - r.height() * 0.1))
    p.drawLine(QPointF(r.right() - r.width() * 0.10, r.top() + r.height() * 0.3),
               QPointF(r.right() - r.width() * 0.10, r.bottom() - r.height() * 0.3))


def _p_dock_right(p, r, c):
    _setup_painter(p, r, c)
    p.drawRect(QRectF(r.left() + r.width() * 0.35,
                      r.top() + r.height() * 0.1,
                      r.width() * 0.55, r.height() * 0.8))
    p.drawLine(QPointF(r.right() - r.width() * 0.35, r.top() + r.height() * 0.1),
               QPointF(r.right() - r.width() * 0.35, r.bottom() - r.height() * 0.1))
    p.drawLine(QPointF(r.left() + r.width() * 0.10, r.top() + r.height() * 0.3),
               QPointF(r.left() + r.width() * 0.10, r.bottom() - r.height() * 0.3))


def _p_pin(p, r, c):
    _setup_painter(p, r, c)
    # Pin / push-pin shape
    p.drawLine(QPointF(r.center().x(), r.top() + r.height() * 0.1),
               QPointF(r.center().x(), r.bottom() - r.height() * 0.1))
    p.drawEllipse(QPointF(r.center().x(), r.top() + r.height() * 0.25),
                  r.width() * 0.12, r.height() * 0.10)


def _p_thumbs(p, r, c):
    _setup_painter(p, r, c)
    # 4 thumbnails in a 2x2 grid
    for i, (x_frac, y_frac) in enumerate([(0.10, 0.10), (0.55, 0.10),
                                          (0.10, 0.55), (0.55, 0.55)]):
        x = r.left() + r.width() * x_frac
        y = r.top() + r.height() * y_frac
        p.drawRect(QRectF(x, y, r.width() * 0.35, r.height() * 0.35))


def _p_dock_float(p, r, c):
    _setup_painter(p, r, c)
    p.drawRect(QRectF(r.left() + r.width() * 0.15,
                      r.top() + r.height() * 0.25,
                      r.width() * 0.7, r.height() * 0.5))
    p.drawLine(QPointF(r.left() + r.width() * 0.3, r.top() + r.height() * 0.05),
               QPointF(r.right() - r.width() * 0.3, r.top() + r.height() * 0.05))


def _p_select(p, r, c):
    """Cursor + highlighted text — 'Select text' icon."""
    _setup_painter(p, r, c)
    # I-beam
    p.drawLine(QPointF(r.left() + r.width() * 0.35, r.top() + r.height() * 0.10),
               QPointF(r.left() + r.width() * 0.35, r.top() + r.height() * 0.70))
    p.drawLine(QPointF(r.left() + r.width() * 0.20, r.top() + r.height() * 0.20),
               QPointF(r.left() + r.width() * 0.50, r.top() + r.height() * 0.20))
    p.drawLine(QPointF(r.left() + r.width() * 0.20, r.top() + r.height() * 0.60),
               QPointF(r.left() + r.width() * 0.50, r.top() + r.height() * 0.60))
    # Highlighted text bars to the right
    for y_frac in (0.30, 0.50, 0.70):
        bar_w = r.width() * (0.45 if y_frac != 0.50 else 0.30)
        p.drawLine(QPointF(r.left() + r.width() * 0.55, r.top() + r.height() * y_frac),
                   QPointF(r.left() + r.width() * 0.55 + bar_w, r.top() + r.height() * y_frac))


def _p_screenshot(p, r, c):
    """Camera-with-bracket icon for 'Take screenshot'."""
    _setup_painter(p, r, c)
    # Four corner brackets (the screenshot / crop tool affordance)
    bracket = 0.25  # fraction of the icon taken by each bracket arm
    # Top-left
    p.drawLine(QPointF(r.left(), r.top() + r.height() * bracket),
               QPointF(r.left(), r.top()))
    p.drawLine(QPointF(r.left(), r.top()),
               QPointF(r.left() + r.width() * bracket, r.top()))
    # Top-right
    p.drawLine(QPointF(r.right() - r.width() * bracket, r.top()),
               QPointF(r.right(), r.top()))
    p.drawLine(QPointF(r.right(), r.top()),
               QPointF(r.right(), r.top() + r.height() * bracket))
    # Bottom-left
    p.drawLine(QPointF(r.left(), r.bottom() - r.height() * bracket),
               QPointF(r.left(), r.bottom()))
    p.drawLine(QPointF(r.left(), r.bottom()),
               QPointF(r.left() + r.width() * bracket, r.bottom()))
    # Bottom-right
    p.drawLine(QPointF(r.right() - r.width() * bracket, r.bottom()),
               QPointF(r.right(), r.bottom()))
    p.drawLine(QPointF(r.right(), r.bottom()),
               QPointF(r.right(), r.bottom() - r.height() * bracket))
    # Camera dot in the middle
    cx = r.left() + r.width() * 0.5
    cy = r.top() + r.height() * 0.5
    p.drawEllipse(QPointF(cx, cy), r.width() * 0.10, r.height() * 0.10)


def _p_screenshot_region(p, r, c):
    """Region-screenshot icon: dashed crop brackets with a hand-cursor
    arrow, so it's clearly distinct from the page-screenshot icon."""
    _setup_painter(p, r, c)
    # Dashed-corner brackets (smaller than the page icon)
    bracket = 0.18
    pen = p.pen()
    pen.setStyle(Qt.PenStyle.DashLine)
    p.setPen(pen)
    # Top-left
    p.drawLine(QPointF(r.left(), r.top() + r.height() * bracket),
               QPointF(r.left(), r.top()))
    p.drawLine(QPointF(r.left(), r.top()),
               QPointF(r.left() + r.width() * bracket, r.top()))
    # Top-right
    p.drawLine(QPointF(r.right() - r.width() * bracket, r.top()),
               QPointF(r.right(), r.top()))
    p.drawLine(QPointF(r.right(), r.top()),
               QPointF(r.right(), r.top() + r.height() * bracket))
    # Bottom-left
    p.drawLine(QPointF(r.left(), r.bottom() - r.height() * bracket),
               QPointF(r.left(), r.bottom()))
    p.drawLine(QPointF(r.left(), r.bottom()),
               QPointF(r.left() + r.width() * bracket, r.bottom()))
    # Bottom-right
    p.drawLine(QPointF(r.right() - r.width() * bracket, r.bottom()),
               QPointF(r.right(), r.bottom()))
    p.drawLine(QPointF(r.right(), r.bottom()),
               QPointF(r.right(), r.bottom() - r.height() * bracket))
    # Reset to solid pen
    pen.setStyle(Qt.PenStyle.SolidLine)
    p.setPen(pen)
    # Arrow head in the middle (suggests "drag a region")
    cx = r.left() + r.width() * 0.5
    cy = r.top() + r.height() * 0.5
    p.drawLine(QPointF(cx - r.width() * 0.10, cy - r.height() * 0.10),
               QPointF(cx, cy))
    p.drawLine(QPointF(cx, cy),
               QPointF(cx + r.width() * 0.10, cy + r.height() * 0.10))


def _p_stamp_qr(p, r, c):
    """QR code on a paper — 'Stamp QR' icon (with popup affordance)."""
    _setup_painter(p, r, c, fill=QColor("#89b4fa"))
    # Outer "paper" square (representing the page)
    p.drawRoundedRect(
        QRectF(r.left() + r.width() * 0.10,
               r.top() + r.height() * 0.10,
               r.width() * 0.80, r.height() * 0.80),
        2, 2,
    )
    # Three QR-style squares (corners) inside the paper
    s = r.width() * 0.16
    # Top-left
    p.drawRect(QRectF(r.left() + r.width() * 0.22,
                      r.top() + r.height() * 0.22, s, s))
    # Top-right
    p.drawRect(QRectF(r.right() - r.width() * 0.22 - s,
                      r.top() + r.height() * 0.22, s, s))
    # Bottom-left
    p.drawRect(QRectF(r.left() + r.width() * 0.22,
                      r.bottom() - r.height() * 0.22 - s, s, s))


def _p_chevron_up(p, r, c):
    """Up-pointing chevron — used for collapse-toolbar / hide-bar."""
    _setup_painter(p, r, c)
    # ^ shape pointing up
    p.drawLine(QPointF(r.left() + r.width() * 0.20, r.top() + r.height() * 0.55),
               QPointF(r.left() + r.width() * 0.50, r.top() + r.height() * 0.25))
    p.drawLine(QPointF(r.left() + r.width() * 0.50, r.top() + r.height() * 0.25),
               QPointF(r.right() - r.width() * 0.20, r.top() + r.height() * 0.55))


def _p_chevron_down(p, r, c):
    """Down-pointing chevron — used for expand-toolbar."""
    _setup_painter(p, r, c)
    p.drawLine(QPointF(r.left() + r.width() * 0.20, r.top() + r.height() * 0.45),
               QPointF(r.left() + r.width() * 0.50, r.top() + r.height() * 0.75))
    p.drawLine(QPointF(r.left() + r.width() * 0.50, r.top() + r.height() * 0.75),
               QPointF(r.right() - r.width() * 0.20, r.top() + r.height() * 0.45))


def _p_pages(p, r, c):
    """Pages grid icon — used for the Pages Manager (Windows-style grid view)."""
    _setup_painter(p, r, c)
    # Four small page tiles arranged in a 2x2 grid
    cell_w = r.width() * 0.40
    cell_h = r.height() * 0.40
    gap = r.width() * 0.05
    x0 = r.left() + (r.width() - (cell_w * 2 + gap)) / 2
    y0 = r.top() + (r.height() - (cell_h * 2 + gap)) / 2
    for row in range(2):
        for col in range(2):
            x = x0 + col * (cell_w + gap)
            y = y0 + row * (cell_h + gap)
            tile = QRectF(x, y, cell_w, cell_h)
            p.drawRoundedRect(tile, 1.5, 1.5)
            # Two text lines inside the tile for "page" feel
            line_h = cell_h * 0.10
            line_y1 = y + cell_h * 0.30
            line_y2 = y + cell_h * 0.55
            p.drawLine(QPointF(x + cell_w * 0.20, line_y1),
                       QPointF(x + cell_w * 0.80, line_y1))
            p.drawLine(QPointF(x + cell_w * 0.20, line_y2),
                       QPointF(x + cell_w * 0.70, line_y2))


def _p_doc_plus(p, r, c):
    """Document with plus sign — 'New PDF from these pages' icon."""
    _setup_painter(p, r, c)
    # Document rectangle with folded corner
    doc = QRectF(r.left() + r.width() * 0.10,
                 r.top() + r.height() * 0.15,
                 r.width() * 0.65, r.height() * 0.70)
    p.drawRoundedRect(doc, 2, 2)
    # Plus sign on the right side
    plus_cx = r.left() + r.width() * 0.78
    plus_cy = r.top() + r.height() * 0.50
    plus_w = r.width() * 0.08
    p.drawLine(QPointF(plus_cx - plus_w, plus_cy), QPointF(plus_cx + plus_w, plus_cy))
    p.drawLine(QPointF(plus_cx, plus_cy - plus_w), QPointF(plus_cx, plus_cy + plus_w))


# Registry -----------------------------------------------------------------
_PAINTERS: Dict[str, PainterFn] = {
    "open":         _p_open,
    "save":         _p_save,
    "undo":         _p_undo,
    "redo":         _p_redo,
    "prev":         _p_prev,
    "next":         _p_next,
    "zoom-in":      _p_zoom_in,
    "zoom-out":     _p_zoom_out,
    "zoom-fit":     _p_zoom_fit,
    "pen":          _p_pen,
    "eraser":       _p_eraser,
    "highlight":    _p_highlight,
    "text":         _p_text,
    "note":         _p_note,
    "qr":           _p_qr,
    "stamp":        _p_stamp,
    "rect":         _p_rect,
    "ellipse":      _p_ellipse,
    "arrow":        _p_arrow,
    "signature":    _p_signature,
    "search":       _p_search,
    "print":        _p_print,
    "sun":          _p_sun,
    "moon":         _p_moon,
    "fullscreen":   _p_fullscreen,
    "toc":          _p_toc,
    "thumbnails":   _p_thumbs,
    "terminal":     _p_terminal,
    "close":        _p_close,
    "clear":        _p_clear,
    "pin":          _p_pin,
    "dock-bottom":  _p_dock_bottom,
    "dock-left":    _p_dock_left,
    "dock-right":   _p_dock_right,
    "dock-float":   _p_dock_float,
    "select":       _p_select,
    "screenshot":   _p_screenshot,
    "screenshot-region": _p_screenshot_region,
    "stamp-qr":     _p_stamp_qr,
    "chevron-up":   _p_chevron_up,
    "chevron-down": _p_chevron_down,
    "pages":        _p_pages,
    "doc-plus":     _p_doc_plus,
    "rotate":       _p_rotate,
}


class IconFactory:
    """Render icons via QPainter. Stateless; safe to call from any thread."""

    @staticmethod
    def _default_color() -> str:
        """Return the foreground colour that contrasts with the current
        theme.

        Falls back to a dark-mode legible off-white if no QApplication
        is alive yet (eg the very first icons during `__init__`,
        before any window is shown).
        """
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QPalette
            app = QApplication.instance()
            if app is not None:
                return app.palette().color(QPalette.ColorRole.WindowText).name()
        except Exception:
            pass
        return "#cdd6f4"

    @staticmethod
    def get(name: str, size: int = 24, color: str | None = None) -> QIcon:
        """Return a QIcon for the given name, rendered at `size`x`size`.

        ``color`` defaults to the current theme's ``WindowText`` colour
        so the icon is legible in both dark and light themes. Pass an
        explicit hex (`"#cba6f7"`) to override.
        """
        if name not in _PAINTERS:
            # Unknown icon → render an empty QIcon to avoid crashes
            return QIcon()
        col = color if color else IconFactory._default_color()
        icon = QIcon()
        pm = IconFactory._render(name, size, QColor(col))
        icon.addPixmap(pm)
        return icon

    @staticmethod
    def pixmap(name: str, size: int = 24, color: str | None = None) -> QPixmap:
        if name not in _PAINTERS:
            return QPixmap()
        col = color if color else IconFactory._default_color()
        return IconFactory._render(name, size, QColor(col))

    @staticmethod
    def _render(name: str, size: int, color: QColor) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        try:
            rect = QRectF(1.0, 1.0, size - 2.0, size - 2.0)
            _PAINTERS[name](painter, rect, color)
        finally:
            painter.end()
        return pm

    @staticmethod
    def paint(painter: QPainter, name: str, rect: QRectF,
              color: str | None = None):
        if name in _PAINTERS:
            col = color if color else IconFactory._default_color()
            _PAINTERS[name](painter, rect, QColor(col))

    @staticmethod
    def available() -> list[str]:
        return sorted(_PAINTERS.keys())