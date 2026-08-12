"""
viewer_ui.py — The PDF canvas.

Responsibilities:
* Render the current page from ViewerEngine into a QLabel on a QScrollArea.
* Map pixel coordinates ↔ PDF user units, taking zoom into account.
* Forward mouse events to the annotator when an annotation mode is active.
* Emit signals when the page changes, when a user requests drag-pan, etc.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF, QRectF, QTimer
from PyQt6.QtGui import (
    QPixmap,
    QImage,
    QPainter,
    QPen,
    QColor,
    QMouseEvent,
    QPaintEvent,
    QResizeEvent,
    QCursor,
)
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
)


# ----------------------------------------------------------------- Modes
class CanvasMode(str, Enum):
    VIEW = "view"
    DRAW = "draw"
    HIGHLIGHT = "highlight"
    ERASE = "erase"
    TEXT = "text"


# ---------------------------------------------------------------- Data
@dataclass
class CanvasStroke:
    points: List[QPointF]   # in PDF user units
    color_rgb: Tuple[float, float, float]
    thickness: float


class _CanvasSurface(QLabel):
    """Subclass of QLabel that owns mouse paint events for ink highlighting."""

    def __init__(self, parent: "PDFViewerUI"):
        super().__init__(parent)
        self._owner = parent
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #ffffff;")
        # Local floating strokes drawn over the page while user is drawing
        self._live_strokes: List[CanvasStroke] = []

    def add_live_stroke(self, stroke: CanvasStroke):
        self._live_strokes.append(stroke)
        self.update()

    def live_strokes(self) -> List[CanvasStroke]:
        return self._live_strokes

    def set_live_strokes(self, strokes: List[CanvasStroke]):
        self._live_strokes = list(strokes)

    def clear_live_strokes(self):
        self._live_strokes.clear()
        self.update()

    def paintEvent(self, event: QPaintEvent):
        super().paintEvent(event)
        if not self._live_strokes:
            return
        painter = QPainter(self)
        try:
            for stroke in self._live_strokes:
                if len(stroke.points) < 2:
                    continue
                pen = QPen(
                    QColor(
                        int(stroke.color_rgb[0] * 255),
                        int(stroke.color_rgb[1] * 255),
                        int(stroke.color_rgb[2] * 255),
                    )
                )
                pen.setWidthF(max(1.0, stroke.thickness * self._owner.zoom))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                # Convert PDF coordinates → widget coordinates
                scale = self._owner.zoom
                pts = [QPointF(p.x() * scale, p.y() * scale) for p in stroke.points]
                for a, b in zip(pts, pts[1:]):
                    painter.drawLine(a, b)
        finally:
            painter.end()

    # ---------------- mouse events (forwarded to owner) ----------------
    def mousePressEvent(self, event: QMouseEvent):
        self._owner.on_canvas_mouse_press(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        self._owner.on_canvas_mouse_move(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._owner.on_canvas_mouse_release(event)

    def leaveEvent(self, event):
        self._owner.on_canvas_leave(event)


class PDFViewerUI(QWidget):
    """The main PDF canvas widget."""

    page_rendered = pyqtSignal(int)            # 1-based page index when rendering finishes
    annotations_changed = pyqtSignal()         # any local annotation activity

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom: float = 1.5
        self._mode: CanvasMode = CanvasMode.VIEW
        self._engine_ref: Optional[object] = None
        self._current_pixmap: Optional[QPixmap] = None
        self._setup_ui()
        self._apply_panning_when_view = True

    # ---------------------------------------------------------------- UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setStyleSheet("background-color: #313244;")

        self.surface = _CanvasSurface(self)
        self.surface.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.scroll_area.setWidget(self.surface)
        root.addWidget(self.scroll_area)

        # Friendly placeholder
        self._set_placeholder()
        self.mode = CanvasMode.VIEW  # sets cursor

    def _set_placeholder(self):
        pm = QPixmap(800, 1000)
        pm.fill(QColor("#ffffff"))
        self.surface.setPixmap(pm)
        self.surface.setFixedSize(800, 1000)

    # ----------------------------------------------------------- public
    def attach_engine(self, engine):
        """Bind the engine so the canvas knows about pages and zoom."""
        self._engine_ref = engine
        self.zoom = engine.zoom

    def set_mode(self, mode: CanvasMode):
        self._mode = mode
        if mode == CanvasMode.VIEW:
            self.surface.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        elif mode in (CanvasMode.DRAW, CanvasMode.HIGHLIGHT):
            self.surface.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif mode == CanvasMode.ERASE:
            self.surface.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        else:
            self.surface.setCursor(QCursor(Qt.CursorShape.IBeamCursor))

    @property
    def mode(self) -> CanvasMode:
        return self._mode

    @mode.setter
    def mode(self, value: CanvasMode):
        self.set_mode(value)

    # ---------------------------------------------------- rendering
    def render(self, png_bytes: bytes, width_pt: float, height_pt: float,
               zoom: float):
        """Display a new rasterized page."""
        self.zoom = zoom
        img = QImage.fromData(png_bytes)
        pix = QPixmap.fromImage(img)
        self._current_pixmap = pix
        self.surface.setPixmap(pix)
        self.surface.setFixedSize(pix.size())
        self.surface.clear_live_strokes()
        # Reset scroll to top
        self.scroll_area.verticalScrollBar().setValue(0)
        self.scroll_area.horizontalScrollBar().setValue(0)

    def fit_to_viewport(self):
        if not self._engine_ref or not self._engine_ref.is_open:
            return
        if self._current_pixmap is None:
            return
        vp = self.scroll_area.viewport().size()
        if self._current_pixmap.width() <= 0 or self._current_pixmap.height() <= 0:
            return
        sx = vp.width() / self._current_pixmap.width()
        sy = vp.height() / self._current_pixmap.height()
        new_zoom = max(0.25, min(min(sx, sy), 4.0)) * self.zoom
        self._engine_ref.set_zoom(new_zoom)
        # Trigger a fresh render at the new zoom
        self.refresh()

    def refresh(self):
        """Re-render the current page using the engine's state."""
        if not self._engine_ref or not self._engine_ref.is_open:
            return
        try:
            result = self._engine_ref.render_current()
            self.render(result.png_bytes, result.page_width_pt, result.page_height_pt,
                        self._engine_ref.zoom)
            self.page_rendered.emit(result.page_index + 1)
        except Exception:
            pass

    # --------------------------------------------- coordinate utilities
    def widget_to_pdf(self, pos: QPoint) -> QPointF:
        """Widget pixel → PDF user units."""
        return QPointF(pos.x() / self.zoom, pos.y() / self.zoom)

    def pdf_to_widget(self, pt: QPointF) -> QPoint:
        return QPoint(int(pt.x() * self.zoom), int(pt.y() * self.zoom))

    # ---------------------------------------------- mouse event forwarders
    def on_canvas_mouse_press(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Dispatch by mode
        if self._mode == CanvasMode.DRAW:
            self._live_stroke = CanvasStroke(
                points=[self.widget_to_pdf(event.position().toPoint())],
                color_rgb=self._active_color,
                thickness=self._active_thickness,
            )
        elif self._mode == CanvasMode.HIGHLIGHT:
            self._live_rect = (self.widget_to_pdf(event.position().toPoint()), None)
        elif self._mode == CanvasMode.ERASE:
            self._request_erase_at(self.widget_to_pdf(event.position().toPoint()))

    def on_canvas_mouse_move(self, event: QMouseEvent):
        if self._mode == CanvasMode.DRAW and hasattr(self, "_live_stroke"):
            self._live_stroke.points.append(self.widget_to_pdf(event.position().toPoint()))
            self.surface.add_live_stroke(self._live_stroke)
        elif self._mode == CanvasMode.HIGHLIGHT and hasattr(self, "_live_rect"):
            self._live_rect = (self._live_rect[0], self.widget_to_pdf(event.position().toPoint()))

    def on_canvas_mouse_release(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._mode == CanvasMode.DRAW and hasattr(self, "_live_stroke"):
            stroke = self._live_stroke
            self._commit_stroke(stroke)
            del self._live_stroke
        elif self._mode == CanvasMode.HIGHLIGHT and hasattr(self, "_live_rect"):
            start, end = self._live_rect
            del self._live_rect
            if start and end:
                rect = QRectF(
                    min(start.x(), end.x()),
                    min(start.y(), end.y()),
                    abs(end.x() - start.x()),
                    abs(end.y() - start.y()),
                )
                self._commit_highlight_rect(rect)

    def on_canvas_leave(self, event):
        pass

    # -------------------------------- setters used by annotator/handlers
    def set_active_ink(self, color_rgb: Tuple[float, float, float], thickness: float):
        self._active_color = color_rgb
        self._active_thickness = thickness

    # ------------------------------------------------------- callbacks
    # These are overridden/set by main_window after the annotator is wired.
    _commit_stroke = lambda self, _stroke: None
    _commit_highlight_rect = lambda self, _rect: None
    _request_erase_at = lambda self, _pt: None
