"""
viewer_ui.py — The PDF canvas.

Responsibilities:
* Render the current page from ViewerEngine into a QLabel on a QScrollArea.
* Map pixel coordinates ↔ PDF user units, taking zoom into account.
* Forward mouse events to the annotator when an annotation mode is active.
* Emit signals when the page changes, when a user requests drag-pan, etc.
"""
from __future__ import annotations

import os
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
    QHBoxLayout,
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
    EDIT_TEXT = "edit-text"
    NOTE = "note"
    RECT = "rect"
    ELLIPSE = "ellipse"
    ARROW = "arrow"
    SIGNATURE = "signature"
    SELECT = "select"


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
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Local floating strokes drawn over the page while user is drawing
        self._live_strokes: List[CanvasStroke] = []
        # Active in-progress shape overlay (rect / ellipse / arrow / highlight
        # rect / select rect). Tracked separately from strokes because the
        # user expects an outline to follow the cursor during a drag.
        self._live_rect: Optional[Tuple[QPointF, QPointF]] = None
        self._live_rect_mode: str = ""         # "rect" | "ellipse" | "highlight" | "select"
        self._live_rect_color: Tuple[float, float, float] = (1.0, 0.95, 0.0)
        self._live_rect_thickness: float = 2.0
        self._live_arrow: Optional[Tuple[QPointF, QPointF]] = None
        self._live_arrow_color: Tuple[float, float, float] = (1.0, 0.0, 0.0)

    def add_live_stroke(self, stroke: CanvasStroke):
        self._live_strokes.append(stroke)
        self.update()

    def live_strokes(self) -> List[CanvasStroke]:
        return self._live_strokes

    def set_live_strokes(self, strokes: List[CanvasStroke]):
        self._live_strokes = list(strokes)

    def clear_live_strokes(self):
        self._live_strokes.clear()
        self._live_rect = None
        self._live_arrow = None
        self._live_rect_mode = ""
        self.update()

    def set_live_rect(self, start_pt: QPointF, end_pt: QPointF,
                       mode: str, color_rgb: Tuple[float, float, float],
                       thickness: float = 2.0):
        self._live_rect = (start_pt, end_pt)
        self._live_rect_mode = mode
        self._live_rect_color = color_rgb
        self._live_rect_thickness = thickness
        self.update()

    def clear_live_rect(self):
        self._live_rect = None
        self._live_rect_mode = ""
        self.update()

    def set_live_arrow(self, start_pt: QPointF, end_pt: QPointF,
                        color_rgb: Tuple[float, float, float]):
        self._live_arrow = (start_pt, end_pt)
        self._live_arrow_color = color_rgb
        self.update()

    def clear_live_arrow(self):
        self._live_arrow = None
        self.update()

    def paintEvent(self, event: QPaintEvent):
        super().paintEvent(event)
        # Nothing to overlay (and no virtual-scroll next-page peeking)
        if (not self._live_strokes and self._live_rect is None
                and self._live_arrow is None
                and self._owner._next_page_pixmap is None):
            return
        painter = QPainter(self)
        try:
            scale = self._owner.zoom
            # If we're in virtual-scroll mode, paint the neighbouring
            # page's pixmap just below the current page so the user
            # can see it as they scroll past the edge (Edge-style
            # continuous-scroll page advance).
            next_pm = self._owner._next_page_pixmap
            if next_pm is not None:
                cur_pm = self._owner._current_pixmap
                if cur_pm is not None:
                    painter.drawPixmap(0, cur_pm.height(), next_pm)

            # --- strokes (pen ink) ---
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
                pen.setWidthF(max(1.0, stroke.thickness * scale))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                pts = [QPointF(p.x() * scale, p.y() * scale) for p in stroke.points]
                for a, b in zip(pts, pts[1:]):
                    painter.drawLine(a, b)

            # --- in-progress rect / ellipse / highlight / select ---
            if self._live_rect is not None:
                start, end = self._live_rect
                x0 = min(start.x(), end.x()) * scale
                y0 = min(start.y(), end.y()) * scale
                x1 = max(start.x(), end.x()) * scale
                y1 = max(start.y(), end.y()) * scale
                if x1 > x0 and y1 > y0:
                    r = QRectF(x0, y0, x1 - x0, y1 - y0)
                    color = QColor(
                        int(self._live_rect_color[0] * 255),
                        int(self._live_rect_color[1] * 255),
                        int(self._live_rect_color[2] * 255),
                    )
                    if self._live_rect_mode == "highlight":
                        # Translucent fill for the highlighter overlay
                        fill = QColor(color)
                        fill.setAlpha(80)
                        painter.fillRect(r, fill)
                        pen = QPen(color)
                        pen.setWidthF(1.0)
                        painter.setPen(pen)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRect(r)
                    elif self._live_rect_mode == "select":
                        # MS Edge-style text selection: solid translucent
                        # blue fill (no dashed border) so the user sees
                        # exactly the area they're selecting. The color
                        # matches Edge's blue selection overlay.
                        edge_blue = QColor(0, 120, 215, 90)  # rgba
                        painter.fillRect(r, edge_blue)
                        # Thin solid border on top so the rect is visible
                        # even on white pages.
                        border = QColor(0, 120, 215, 200)
                        pen = QPen(border)
                        pen.setWidthF(1.0)
                        painter.setPen(pen)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRect(r)
                    elif self._live_rect_mode == "ellipse":
                        pen = QPen(color)
                        pen.setWidthF(max(1.0, self._live_rect_thickness * scale))
                        painter.setPen(pen)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawEllipse(r)
                    else:  # "rect"
                        pen = QPen(color)
                        pen.setWidthF(max(1.0, self._live_rect_thickness * scale))
                        painter.setPen(pen)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRect(r)

            # --- in-progress arrow (line + arrow head) ---
            if self._live_arrow is not None:
                start, end = self._live_arrow
                p1 = QPointF(start.x() * scale, start.y() * scale)
                p2 = QPointF(end.x() * scale, end.y() * scale)
                color = QColor(
                    int(self._live_arrow_color[0] * 255),
                    int(self._live_arrow_color[1] * 255),
                    int(self._live_arrow_color[2] * 255),
                )
                pen = QPen(color)
                pen.setWidthF(max(1.0, 2.0 * scale))
                painter.setPen(pen)
                painter.setBrush(color)
                painter.drawLine(p1, p2)
                # Arrow head triangle
                from PyQt6.QtGui import QPolygonF
                dx = p2.x() - p1.x()
                dy = p2.y() - p1.y()
                length = (dx * dx + dy * dy) ** 0.5
                if length > 1:
                    ux, uy = dx / length, dy / length
                    head_len = max(8.0, 10.0 * scale)
                    head_w = max(6.0, 7.0 * scale)
                    # base point
                    bx = p2.x() - ux * head_len
                    by = p2.y() - uy * head_len
                    # perpendicular for the wide base
                    px, py = -uy, ux
                    a = QPointF(bx + px * head_w / 2, by + py * head_w / 2)
                    b = QPointF(bx - px * head_w / 2, by - py * head_w / 2)
                    painter.drawPolygon(QPolygonF([p2, a, b]))
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

    def wheelEvent(self, event):
        # Forward to the viewer so zoom / scroll-to-next-page can take over.
        self._owner.wheelEvent(event)

    def contextMenuEvent(self, event):
        # Right-click on the canvas → context menu (e.g. QR-share selection)
        self._owner.on_canvas_context_menu(event)


class PDFViewerUI(QWidget):
    """The main PDF canvas widget."""

    page_rendered = pyqtSignal(int)            # 1-based page index when rendering finishes
    annotations_changed = pyqtSignal()         # any local annotation activity
    # Emitted AFTER a virtual-scroll commit has done the page change
    # in-place (no engine next/prev needed from the receiver). Carries
    # the new 1-based page index so the main window can update its
    # page indicator label.
    page_advance_committed = pyqtSignal(int)
    page_advance_requested = pyqtSignal(int)   # +1 = next, -1 = prev (continuous scroll)
    context_menu_requested = pyqtSignal(object)  # QPointF in PDF coords — right-click point

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom: float = 1.5
        # Default to VIEW mode so the user gets a read-only canvas
        # immediately on open. Text-selection (SELECT) is still
        # available via the toolbar/terminal — the user just has to
        # opt-in to it. This matches the user-visible behaviour of MS
        # Edge's PDF reader where opening a document doesn't pre-arm
        # any tool.
        self._mode: CanvasMode = CanvasMode.VIEW
        self._engine_ref: Optional[object] = None
        self._current_pixmap: Optional[QPixmap] = None
        # Active ink / highlight / shape colors (set by main_window
        # when the user picks a swatch or runs ``mode ... --color``).
        self._active_color: Tuple[float, float, float] = (1.0, 0.0, 0.0)
        self._active_thickness: float = 2.0
        self._active_highlight_color: Tuple[float, float, float] = (1.0, 0.95, 0.0)
        self._active_shape_color: Tuple[float, float, float] = (0.5, 0.6, 1.0)
        self._active_shape_thickness: float = 2.0
        # Momentum / inertia state (set up after the scroll area is
        # built below; init here so attribute-lookup never fails
        # before first use).
        self._momentum_v: float = 0.0
        # QTextEdit-style kinetic scrolling:
        #   * The wheel handler updates the scrollbar synchronously
        #     (no per-event animation) — this is what gives the canvas
        #     the same instant, smooth feel as the in-app terminal.
        #   * Page advance on edge overscroll is debounced (180 ms) so a
        #     single accidental overscroll never causes an instant
        #     page flip — same debounce the terminal scroll has when
        #     you scroll past its content area.
        self._edge_pending_dir: int = 0       # 0=none, +1=down-wants-next, -1=up-wants-prev
        self._edge_pending_timer = QTimer(self)
        self._edge_pending_timer.setSingleShot(True)
        self._edge_pending_timer.setInterval(180)
        self._edge_pending_timer.timeout.connect(self._fire_pending_edge)
        # ----- Edge-style continuous-scroll page advance -----------------
        # When the user scrolls past the bottom (or top) of the current
        # page, instead of an instant page cut, we enter virtual-scroll
        # mode: the next page is rendered just below the current page
        # on the same surface so the user can scroll *through* the
        # boundary visually (like MS Edge's PDF reader). When the user
        # stops spinning for ``VIRTUAL_SNAP_MS`` we commit the page
        # change via ``page_advance_requested``.
        self._next_page_pixmap: Optional[QPixmap] = None
        self._virtual_direction: int = 0   # +1 = commit-next, -1 = commit-prev
        self._VIRTUAL_SNAP_MS = 180
        self._VIRTUAL_PEEK_GAP = 600      # px — peek range past the edge
        self._virtual_snap_timer = QTimer(self)
        self._virtual_snap_timer.setSingleShot(True)
        self._virtual_snap_timer.setInterval(self._VIRTUAL_SNAP_MS)
        self._virtual_snap_timer.timeout.connect(self._commit_virtual_to_next_page)
        # Momentum / inertia ticker: per-tick velocity is applied to
        # the scrollbar and decays exponentially. The timer is only
        # active while ``_momentum_v > 0``; it stops itself the moment
        # the velocity falls below the threshold.
        self._momentum_timer = QTimer(self)
        self._momentum_timer.setInterval(16)        # ~60 Hz
        self._momentum_timer.timeout.connect(self._tick_momentum)
        self._setup_ui()
        self._apply_panning_when_view = True

    # ---------------------------------------------------------------- UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        # setWidgetResizable(True) so the scroll area's widget grows to fill
        # the viewport, which lets the centering container center the surface.
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background-color: #313244;")
        # Strong focus policy so keyboard scrolling works without click first
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Inner centering container — centers the surface inside the scroll
        # area's viewport both horizontally and vertically.
        self._canvas_holder = QWidget()
        self._canvas_holder.setStyleSheet("background-color: #313244;")
        holder_layout = QHBoxLayout(self._canvas_holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addStretch(1)
        self._canvas_surface_anchor = QWidget()
        holder_layout.addWidget(self._canvas_surface_anchor)
        holder_layout.addStretch(1)
        v_anchor_layout = QVBoxLayout(self._canvas_surface_anchor)
        v_anchor_layout.setContentsMargins(0, 0, 0, 0)
        v_anchor_layout.addStretch(1)

        self.surface = _CanvasSurface(self)
        self.surface.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        v_anchor_layout.addWidget(self.surface, 0, Qt.AlignmentFlag.AlignHCenter)
        v_anchor_layout.addStretch(1)

        self.scroll_area.setWidget(self._canvas_holder)
        root.addWidget(self.scroll_area)

        # Friendly placeholder
        self._set_placeholder()
        self.mode = CanvasMode.VIEW  # sets cursor

        # ----- Image drag-drop ---------------------------------------------
        # The viewer accepts drops of one-or-more image files; main_window
        # builds a multi-page PDF from them and auto-opens it. We only
        # accept drops here — the actual conversion lives in
        # MainWindow._handle_image_drop.
        self.setAcceptDrops(True)
        self._image_drop_handler = None  # injected by main_window

    def set_image_drop_handler(self, callable_):
        """Inject the function main_window wants invoked on image drop.

        The handler signature is
        ``handler(absolute_paths: list[str]) -> None``.
        """
        self._image_drop_handler = callable_

    # ----- Image drag-drop events -----------------------------------------
    # PyQt6's drag-and-drop events are dispatched to the widget that
    # has ``setAcceptDrops(True)`` — that's us. We accept the drop only
    # when every URL is a local file with a recognized image
    # extension; non-image drops fall through (so a stray PDF drop is
    # still handled by main_window's main drop handler if any).
    _IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif",
                   ".tiff", ".webp", ".avif", ".heic", ".heif", ".ppm",
                   ".pgm", ".pbm")

    def dragEnterEvent(self, event):
        from PyQt6.QtCore import QUrl
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        # Accept only if every URL is a local file with an image ext.
        if not paths or not all(p and os.path.isfile(p)
                               and p.lower().endswith(self._IMAGE_EXTS)
                               for p in paths):
            event.ignore()
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        # Required so the drop is accepted (Qt only fires drop on the
        # widget that accepts the move too).
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        from PyQt6.QtCore import QUrl
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = []
        for u in event.mimeData().urls():
            local = u.toLocalFile()
            if local and local.lower().endswith(self._IMAGE_EXTS):
                paths.append(os.path.abspath(local))
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        handler = getattr(self, "_image_drop_handler", None)
        if handler is not None:
            try:
                handler(paths)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error(
                    "image drop handler raised: %s", exc)

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
        # VIEW mode = read-only document. We use the open-hand cursor
        # (drag-to-pan) so the canvas feels like a static page until
        # the user opts in to selection / annotation. SELECT is the
        # text-select mode (IBeam) — only when the user explicitly
        # switches to it. Annotation tools use their own cursors.
        if mode == CanvasMode.VIEW:
            self.surface.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        elif mode == CanvasMode.SELECT:
            self.surface.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
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

    # ---------------------------------------------------- selection buffer
    def set_selection(self, text: str) -> None:
        """Remember the most recently selected text (for right-click QR-share)."""
        self._last_selection = text

    def get_selection(self) -> str:
        """Return the last selection text (empty string if none)."""
        return getattr(self, "_last_selection", "")

    def on_canvas_context_menu(self, event) -> None:
        """Right-click handler — surface forwards its contextMenuEvent here.

        We re-use Qt's normal right-button mouse flow but instead of popping
        a menu ourselves, we just emit a signal with the click point so the
        main window can build the menu (it has access to all the QR + cmd
        machinery).
        """
        # QContextMenuEvent.pos() returns the local position in widget coords.
        try:
            if hasattr(event, "pos"):
                pt = event.pos()
            elif hasattr(event, "position"):
                pt = event.position().toPoint()
            else:
                pt = QPoint(0, 0)
        except Exception:
            pt = QPoint(0, 0)
        pt_pdf = self.widget_to_pdf(pt)
        self.context_menu_requested.emit(pt_pdf)

    # ---------------------------------------------------- rendering
    def render(self, png_bytes: bytes, width_pt: float, height_pt: float,
               zoom: float, *, animate: bool = False):
        """Display a new rasterized page.

        The scroll position is preserved when re-rendering the same page
        (zoom changes) — but reset when rendering a *different* page
        (page navigation), so the user always sees the top of the new page
        exactly like Edge / Chrome does.

        ``animate`` is accepted for API compatibility but currently has no
        effect — the user requested no animations on scroll/page-advance
        (instant cuts, like the embedded QTextEdit terminal).
        """
        self.zoom = zoom
        img = QImage.fromData(png_bytes)
        pix = QPixmap.fromImage(img)
        same_size = (self._current_pixmap is not None and
                     self._current_pixmap.size() == pix.size())
        self._current_pixmap = pix
        self.surface.setPixmap(pix)
        self.surface.setFixedSize(pix.size())
        self.surface.clear_live_strokes()
        if not same_size:
            # Different page (or different aspect ratio after zoom) — reset
            # scroll to top so the user sees the page header.
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

    def refresh(self, *, animate: bool = False, immediate_full_res: bool = False):
        """Re-render the current page using the engine's state.

        Strategy (no blink):
          1. **Keep the current pixmap visible** — the user sees the
             last-rendered page until the worker finishes. There is
             intentionally NO low-resolution preview pass anymore; the
             previous "preview at 0.5× → full-res swap" pattern caused
             a visible blink (low-res flash, then full-res swap) that
             the user reported as "screen blinks for a few ms and looks
             like loading."
          2. **Background full-res.** A worker thread rasterizes the
             page at the engine's current zoom and emits a callback.
             We swap in the high-resolution pixmap when it's ready.

        Pass ``immediate_full_res=True`` to render synchronously on the
        GUI thread (used by code paths that have already pre-rendered
        the pixmap, like virtual-scroll commit).

        If a cached render exists for this page+zoom, we paste it
        immediately and skip the worker entirely.
        """
        if not self._engine_ref or not self._engine_ref.is_open:
            return
        engine = self._engine_ref

        # Fast path: cache hit → paste and skip the worker. This is
        # what handles page advance when the next page was pre-rendered
        # during virtual-scroll mode.
        cache_key = (engine.current_page, engine.zoom)
        if (engine._render_cache_key == cache_key
                and engine._render_cache_bytes is not None
                and not getattr(self, "_next_page_pixmap", None)):
            try:
                self._show_pixmap(engine._render_cache_bytes,
                                  *engine._render_cache_dims,
                                  zoom=engine.zoom, animate=False)
                self.page_rendered.emit(engine.current_page + 1)
                return
            except Exception:
                pass

        # Synchronous full-res path — used when the caller has already
        # pre-rendered (e.g. virtual-scroll commit, where the next-page
        # pixmap is in hand). NO preview pass.
        if immediate_full_res:
            try:
                result = engine.render_current()
                self._show_pixmap(result.png_bytes, result.page_width_pt,
                                  result.page_height_pt,
                                  zoom=engine.zoom, animate=animate)
                self.page_rendered.emit(result.page_index + 1)
                return
            except Exception:
                pass

        # Default path: keep the current pixmap visible (no preview
        # swap) and let the background worker swap in full-res when
        # ready. If the worker fails we fall back to a sync render.
        try:
            engine.request_full_render(
                lambda png, w, h: self._on_full_render_ready(
                    png, w, h, animate))
        except Exception:
            # Fallback: synchronous render so we still show something.
            try:
                result = engine.render_current()
                self._show_pixmap(result.png_bytes, result.page_width_pt,
                                  result.page_height_pt,
                                  zoom=engine.zoom, animate=animate)
                self.page_rendered.emit(result.page_index + 1)
            except Exception:
                pass

    def _on_full_render_ready(self, png, w, h, animate: bool):
        """Worker delivered full-resolution PNG bytes — swap it in."""
        try:
            self._show_pixmap(png, w, h,
                              zoom=self._engine_ref.zoom if self._engine_ref else 1.5,
                              animate=animate)
            if self._engine_ref:
                self.page_rendered.emit(self._engine_ref.current_page + 1)
        except Exception:
            pass

    def _show_pixmap(self, png, w, h, zoom: float, animate: bool = False):
        """Display a new pixmap without going through the engine cache."""
        try:
            img = QImage.fromData(png)
            pix = QPixmap.fromImage(img)
        except Exception:
            return
        self.zoom = zoom
        # Preserve the user's scroll position proportionally when the
        # surface size changes (e.g. preview → full-res swap). Without
        # this the user sees the preview at scroll-bottom, then the
        # full-res lands mid-page when the scrollbar range shifts.
        #
        # Special case: if the user was already pinned at the edge
        # (top or bottom) before the swap, keep them pinned to the
        # same edge after the swap — otherwise the preview → full-res
        # race resets them to a mid-page position.
        sb_v = self.scroll_area.verticalScrollBar()
        prev_had_scroll = sb_v.maximum() > 0
        prev_at_top = prev_had_scroll and sb_v.value() <= sb_v.minimum()
        prev_at_bottom = prev_had_scroll and sb_v.value() >= sb_v.maximum()
        prev_ratio = 0.0
        if prev_had_scroll and not prev_at_top and not prev_at_bottom:
            prev_ratio = sb_v.value() / float(sb_v.maximum())
        prev_h = self._current_pixmap.height() if self._current_pixmap is not None else 0
        self._current_pixmap = pix
        self.surface.setPixmap(pix)
        self.surface.setFixedSize(pix.size())
        self.surface.clear_live_strokes()
        # Force layout to run so the scrollbar's maximum() reflects the
        # new surface size before we try to set the value — otherwise
        # setValue() reads the OLD maximum and the user lands mid-page
        # after the preview → full-res swap.
        try:
            from PyQt6.QtWidgets import QApplication
            self.scroll_area.widget().adjustSize()
            QApplication.processEvents()
        except Exception:
            pass
        # Adjust scrollbar after the surface resize so the user's
        # visual position is preserved across the preview → full-res
        # swap (and across zoom changes).
        try:
            if prev_h and prev_h != pix.size().height() and sb_v.maximum() > 0:
                if prev_at_bottom:
                    sb_v.setValue(sb_v.maximum())
                elif prev_at_top:
                    sb_v.setValue(sb_v.minimum())
                else:
                    new_val = int(prev_ratio * sb_v.maximum())
                    sb_v.setValue(max(sb_v.minimum(),
                                      min(sb_v.maximum(), new_val)))
        except Exception:
            pass

    # --------------------------------------------- coordinate utilities
    def widget_to_pdf(self, pos: QPoint) -> QPointF:
        """Widget pixel → PDF user units."""
        return QPointF(pos.x() / self.zoom, pos.y() / self.zoom)

    def pdf_to_widget(self, pt: QPointF) -> QPoint:
        return QPoint(int(pt.x() * self.zoom), int(pt.y() * self.zoom))

    # ---------------------------------------------------------- wheel/keys
    def wheelEvent(self, event):
        """Wheel handling — exactly like the embedded QTextEdit terminal.

        Why we don't reuse QTextEdit directly: the canvas needs a custom
        paint overlay for live annotations, selection feedback, and
        shape previews, which QTextEdit doesn't support. So we replicate
        QTextEdit's scroll feel here:

          * Each angleDelta unit moves the scrollbar by exactly the
            same per-pixel amount Qt uses internally for QTextEdit
            (~3 px per wheel step of 120).
          * The scrollbar is updated directly via ``setValue()`` —
            no animation, no momentum. This is the same "kinetic" feel
            the terminal has: smooth, immediate, with the document
            tracking the wheel 1:1.
          * Wheel-down (delta < 0) at the bottom edge advances to the
            next page; wheel-up at the top goes back. A small debounce
            prevents a single accidental overscroll from flipping the
            page instantly.
          * Ctrl+wheel = zoom in/out.
        """
        delta = event.angleDelta().y()
        # Ctrl + wheel = zoom (Edge-style)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if delta > 0 and self._engine_ref:
                self._engine_ref.zoom_in()
            elif delta < 0 and self._engine_ref:
                self._engine_ref.zoom_out()
            self.refresh()
            event.accept()
            return
        if delta == 0:
            event.accept()
            return

        sb = self.scroll_area.verticalScrollBar()

        # Per-pixel scroll (matches QTextEdit per-pixel mode): each
        # "wheel unit" (120) maps to ~20 px of movement so the page
        # glides noticeably under each notch. The user bumped this
        # from 10 → 20 px so scrolling covers a full screen in ~20
        # notches, matching Edge/Chrome's default. The direct
        # setValue() — no animation — is what gives QTextEdit its
        # buttery, real-time feel.
        per_pixel_step = -delta / 120.0 * 20.0
        new_val = sb.value() + per_pixel_step
        # Clamp into valid range
        if new_val > sb.maximum():
            new_val = sb.maximum()
        elif new_val < sb.minimum():
            new_val = sb.minimum()

        # Detect whether the user is *pushing past the edge* (wheel
        # direction would move the scrollbar outside its range).
        at_bottom = sb.value() >= sb.maximum()
        at_top = sb.value() <= sb.minimum()

        # ---- Edge-style continuous-scroll page advance -----------------
        # When the user is pinned at the bottom (or top) and keeps
        # spinning the wheel, render the neighbouring page on the
        # opposite side of the current one and let the scrollbar
        # travel into the peek region. On idle, commit the page
        # change via _commit_virtual_to_next_page().
        if new_val == sb.value() and sb.maximum() > 0:
            if at_bottom and delta < 0:
                # Wheel-down past bottom: peek next page below.
                self._enter_virtual_scroll_mode(direction=+1)
                self._virtual_snap_timer.start()
                # Let the scrollbar travel into the peek gap.
                sb.setValue(int(sb.value() + per_pixel_step))
                event.accept()
                return
            if at_top and delta > 0:
                # Wheel-up past top: peek prev page above.
                self._enter_virtual_scroll_mode(direction=-1)
                self._virtual_snap_timer.start()
                sb.setValue(int(sb.value() + per_pixel_step))
                event.accept()
                return
        # If we're already in virtual mode and the user keeps spinning
        # in the same direction, just keep advancing the scrollbar.
        if self._next_page_pixmap is not None:
            if (self._virtual_direction == +1 and delta < 0):
                self._virtual_snap_timer.start()
                sb.setValue(int(sb.value() + per_pixel_step))
                event.accept()
                return
            if (self._virtual_direction == -1 and delta > 0):
                self._virtual_snap_timer.start()
                sb.setValue(int(sb.value() + per_pixel_step))
                event.accept()
                return
            # User reversed direction → leave virtual mode.
            self._exit_virtual_scroll_mode()

        # Pinned AND no scroll range at all (single-screen page): every
        # wheel event is a potential page turn.
        if sb.maximum() <= 0:
            if delta < 0:
                self._request_edge_advance(1)
            elif delta > 0:
                self._request_edge_advance(-1)
            event.accept()
            return

        # Apply the per-pixel scroll directly. No animation, no
        # momentum — this matches the QTextEdit feel exactly.
        sb.setValue(int(new_val))
        # If the scrollbar just landed on the edge in this event, the
        # next wheel event can advance pages. Reset the debounce so a
        # stray overscroll doesn't fire prematurely.
        if sb.value() not in (sb.minimum(), sb.maximum()):
            self._edge_pending_timer.stop()
            self._edge_pending_dir = 0
        event.accept()

    # ------------------------------------------------------------------
    # Helpers used by the scroll system (QTextEdit-style)
    # ------------------------------------------------------------------
    def _reset_momentum(self) -> None:
        """Stop any pending page-advance debounce.

        Kept as a no-op-style helper because some call sites (keyboard
        handlers, tests) still invoke it for compatibility. There is no
        per-event animation to stop anymore — we update the scrollbar
        synchronously, just like QTextEdit does.
        """
        self._edge_pending_timer.stop()
        self._edge_pending_dir = 0

    # ----- momentum / inertia (Phase 2 scroll feel) -------------------
    # The scrollbar now uses a per-pixel, fast-feel wheel step (20px)
    # plus a momentum tail that keeps the page sliding for a beat after
    # the user lifts their finger. ``_momentum_v`` is the per-tick
    # velocity; ``_tick_momentum`` advances the scrollbar by that
    # amount and decays the velocity; the timer self-stops once the
    # velocity falls below the threshold.
    def _feed_momentum(self, v: float) -> None:
        self._momentum_v = max(self._momentum_v, v)
        if self._momentum_v > 0 and not self._momentum_timer.isActive():
            self._momentum_timer.start()

    def _tick_momentum(self) -> None:
        v = float(self._momentum_v)
        if v <= 0.0:
            self._momentum_timer.stop()
            return
        # Apply the velocity to the scrollbar (positive = down).
        sb = self.scroll_area.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.value() + int(round(v)))
        # Exponential decay: ~12% per tick.
        self._momentum_v = v * 0.88
        if self._momentum_v < 0.5:
            self._momentum_v = 0.0
            self._momentum_timer.stop()

    # ---- debounced edge → page advance -----------------------------
    def _request_edge_advance(self, direction: int) -> None:
        """Request ``direction`` page advance, debounced.

        The user must keep spinning the wheel against the edge for
        EDGE_DEBOUNCE ms before the page actually flips — same feel as
        the QTextEdit widget on a single-line terminal buffer.
        """
        is_active = self._edge_pending_timer.isActive()
        if self._edge_pending_dir == direction and is_active:
            # Already armed in this direction — don't restart.
            return
        self._edge_pending_dir = direction
        self._edge_pending_timer.start()

    def _fire_pending_edge(self) -> None:
        """Timer callback — emit the page advance request."""
        direction = self._edge_pending_dir
        self._edge_pending_dir = 0
        if direction == 0:
            return
        self.page_advance_requested.emit(direction)

    # ---- virtual-scroll (Edge-style continuous page advance) ---------
    def _enter_virtual_scroll_mode(self, direction: int) -> None:
        """Render the next/prev page so the user can scroll past the edge.

        Called when the user is pinned at the bottom (direction=+1) or
        top (direction=-1) and keeps spinning the wheel. We synchronously
        render the neighbouring page at the engine's current zoom and
        grow the surface so the scrollbar's maximum() now extends
        ``_VIRTUAL_PEEK_GAP`` pixels past the actual page edge. The
        user can then scroll *through* the boundary visually until the
        snap timer fires.
        """
        if not self._engine_ref or not self._engine_ref.is_open:
            return
        engine = self._engine_ref
        target = engine.current_page + direction
        if target < 0 or target >= engine.page_count:
            return  # at a bookend — nothing to peek
        # If we're already peeking in the same direction, no need to
        # re-render. (Direction is sticky within one mode.)
        if (self._next_page_pixmap is not None
                and self._virtual_direction == direction):
            return
        # Render the neighbour at the engine's current zoom.
        try:
            import fitz as _fitz
            page = engine.get_page(target)
            matrix = _fitz.Matrix(engine.zoom, engine.zoom)
            png = page.get_pixmap(matrix=matrix, alpha=False).tobytes("png")
            img = QImage.fromData(png)
            self._next_page_pixmap = QPixmap.fromImage(img)
        except Exception:
            self._next_page_pixmap = None
            return
        self._virtual_direction = direction
        # Grow the surface so the scrollbar can travel into the peek
        # region. We set height to pixmap height + _VIRTUAL_PEEK_GAP.
        cur = self._current_pixmap
        if cur is not None:
            self.surface.setFixedSize(
                cur.width(),
                cur.height() + self._VIRTUAL_PEEK_GAP,
            )
            # Trigger a repaint so the next-page peeks below.
            self.surface.update()
        # Force layout to settle so the new sb.maximum() takes effect
        # before the next wheel event.
        from PyQt6.QtWidgets import QApplication
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _exit_virtual_scroll_mode(self) -> None:
        """Reverse-scroll: leave virtual mode without committing."""
        if self._next_page_pixmap is None:
            return
        self._virtual_snap_timer.stop()
        self._next_page_pixmap = None
        self._virtual_direction = 0
        # Restore single-page surface height.
        cur = self._current_pixmap
        if cur is not None:
            self.surface.setFixedSize(cur.size())
            self.surface.update()

    def _commit_virtual_to_next_page(self) -> None:
        """Snap: do the page advance in-place and reset to single-page
        layout — no preview/worker round-trip, no blink.

        Called when the user stops spinning in virtual mode for
        ``_VIRTUAL_SNAP_MS``. We:
          1. Advance the engine ourselves (next_page / prev_page).
          2. Promote the already-rendered ``_next_page_pixmap`` (which
             was rasterized during virtual-scroll mode) to the current
             pixmap so the user sees the new page at full resolution
             with no preview pass and no background worker.
          3. Still emit ``page_advance_requested`` so the main window
             can update the page-indicator label, but the visual
             transition is already done.
        """
        direction = self._virtual_direction or +1
        next_pm = self._next_page_pixmap
        # Clear state FIRST so _show_pixmap doesn't see stale data.
        self._next_page_pixmap = None
        self._virtual_direction = 0
        if not self._engine_ref or not self._engine_ref.is_open:
            self.page_advance_requested.emit(direction)
            return
        engine = self._engine_ref
        if direction > 0:
            ok, _msg = engine.next_page()
        else:
            ok, _msg = engine.prev_page()
        if not ok:
            self.page_advance_requested.emit(direction)
            return
        # Promote the next-page pixmap to current. We set the cache so
        # any subsequent refresh hits the cache (no re-render). This
        # eliminates the preview → full-res blink that the user reported.
        if next_pm is not None and not next_pm.isNull():
            try:
                # Stash into the engine cache so refresh() finds it.
                import io as _io
                buf = _io.BytesIO()
                next_pm.save(buf, "PNG")
                png_bytes = buf.getvalue()
                w_pt = (next_pm.width() / engine.zoom) if engine.zoom else 0.0
                h_pt = (next_pm.height() / engine.zoom) if engine.zoom else 0.0
                engine._render_cache_key = (engine.current_page, engine.zoom)
                engine._render_cache_bytes = png_bytes
                engine._render_cache_dims = (w_pt, h_pt)
            except Exception:
                pass
            # Reset surface to single-page size and paste the new pixmap.
            self._current_pixmap = next_pm
            self.surface.setPixmap(next_pm)
            self.surface.setFixedSize(next_pm.size())
            try:
                self.scroll_area.verticalScrollBar().setValue(0)
                self.scroll_area.horizontalScrollBar().setValue(0)
            except Exception:
                pass
            self.page_rendered.emit(engine.current_page + 1)
            # Tell the main window about the change so the page-
            # indicator updates, but DON'T re-render — the cache is
            # already populated. page_advance_committed tells the main
            # window the engine already advanced, so it must NOT call
            # next_page()/prev_page() again.
            self.page_advance_committed.emit(engine.current_page + 1)
            return
        # No pre-rendered next page (rare path) — fall back to the
        # standard signal which triggers a normal refresh.
        self.page_advance_requested.emit(direction)

    def keyPressEvent(self, event):
        """Arrow / PageUp / PageDown / Home / End / Space → scroll canvas.

        Direct scrollbar updates — same as QTextEdit. Keyboard scroll
        feels instant and natural instead of having to wait for an
        animation.
        """
        sb_v = self.scroll_area.verticalScrollBar()
        sb_h = self.scroll_area.horizontalScrollBar()
        vp_h = self.scroll_area.viewport().height()
        step = 60

        # Cancel any debounced edge advance (no longer applicable but
        # we keep this for safety).
        self._reset_momentum()

        def jump(delta: int):
            target = sb_v.value() + delta
            target = max(sb_v.minimum(), min(sb_v.maximum(), target))
            if target != sb_v.value():
                sb_v.setValue(target)

        k = event.key()
        if k == Qt.Key.Key_Down:
            jump(step)
        elif k == Qt.Key.Key_Up:
            jump(-step)
        elif k == Qt.Key.Key_PageDown:
            jump(int(vp_h * 0.8))
        elif k == Qt.Key.Key_PageUp:
            jump(-int(vp_h * 0.8))
        elif k == Qt.Key.Key_Home:
            sb_v.setValue(sb_v.minimum())
        elif k == Qt.Key.Key_End:
            sb_v.setValue(sb_v.maximum())
        elif k == Qt.Key.Key_Left:
            sb_h.setValue(sb_h.value() - step)
        elif k == Qt.Key.Key_Right:
            sb_h.setValue(sb_h.value() + step)
        elif k == Qt.Key.Key_Space:
            jump(int(vp_h * 0.8))
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # ---------------------------------------------- mouse event forwarders
    def on_canvas_mouse_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            # Right-click → selection-mode "share via QR". Even outside
            # SELECT mode we still surface a context menu because it's
            # useful on any tool.
            self.on_canvas_context_menu(event)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pt_pdf = self.widget_to_pdf(event.position().toPoint())
        # In VIEW mode (no annotation tool active), dragging with the
        # left mouse button should still let the user select text — this
        # is the MS Edge / Chrome PDF reader behavior. The user doesn't
        # need to remember to switch modes.
        if self._mode == CanvasMode.VIEW:
            self._live_rect = (pt_pdf, pt_pdf)
            self.surface.set_live_rect(
                pt_pdf, pt_pdf, "select",
                color_rgb=(0.0, 120/255.0, 215/255.0),
            )
            return
        # Dispatch by mode (only annotation tools are listed here)
        if self._mode == CanvasMode.DRAW:
            self._live_stroke = CanvasStroke(
                points=[pt_pdf],
                color_rgb=self._active_color,
                thickness=self._active_thickness,
            )
        elif self._mode == CanvasMode.HIGHLIGHT:
            self._live_rect = (pt_pdf, pt_pdf)
            self.surface.set_live_rect(
                pt_pdf, pt_pdf, "highlight",
                color_rgb=getattr(self, "_active_highlight_color",
                                  self.annot_highlight_color
                                  if hasattr(self, "annot_highlight_color")
                                  else (1.0, 0.95, 0.0)),
            )
        elif self._mode == CanvasMode.ERASE:
            self._request_erase_at(pt_pdf)
        elif self._mode in (CanvasMode.RECT, CanvasMode.ELLIPSE):
            self._live_rect = (pt_pdf, pt_pdf)
            shape = "ellipse" if self._mode == CanvasMode.ELLIPSE else "rect"
            self.surface.set_live_rect(
                pt_pdf, pt_pdf, shape,
                color_rgb=self._active_shape_color,
                thickness=self._active_shape_thickness,
            )
        elif self._mode == CanvasMode.ARROW:
            self._live_arrow = (pt_pdf, pt_pdf)
            self.surface.set_live_arrow(pt_pdf, pt_pdf, self._active_shape_color)
        elif self._mode == CanvasMode.NOTE:
            self._request_note(pt_pdf)
        elif self._mode == CanvasMode.TEXT:
            self._request_text_insert(pt_pdf)
        elif self._mode == CanvasMode.EDIT_TEXT:
            self._request_edit_text(pt_pdf)
        elif self._mode == CanvasMode.SIGNATURE:
            self._request_signature(pt_pdf)
        elif self._mode == CanvasMode.SELECT:
            # Single click = copy text at point; drag = select range
            self._live_rect = (pt_pdf, pt_pdf)
            self.surface.set_live_rect(
                pt_pdf, pt_pdf, "select",
                color_rgb=(0.4, 0.7, 1.0),
            )

    def on_canvas_mouse_move(self, event: QMouseEvent):
        pt_pdf = self.widget_to_pdf(event.position().toPoint())
        if self._mode == CanvasMode.DRAW and hasattr(self, "_live_stroke"):
            self._live_stroke.points.append(pt_pdf)
            self.surface.add_live_stroke(self._live_stroke)
        elif self._mode == CanvasMode.HIGHLIGHT and hasattr(self, "_live_rect"):
            self._live_rect = (self._live_rect[0], pt_pdf)
            self.surface.set_live_rect(
                self._live_rect[0], pt_pdf, "highlight",
                color_rgb=self.surface._live_rect_color,
            )
        elif self._mode in (CanvasMode.RECT, CanvasMode.ELLIPSE) and hasattr(self, "_live_rect"):
            self._live_rect = (self._live_rect[0], pt_pdf)
            shape = "ellipse" if self._mode == CanvasMode.ELLIPSE else "rect"
            self.surface.set_live_rect(
                self._live_rect[0], pt_pdf, shape,
                color_rgb=self.surface._live_rect_color,
                thickness=self.surface._live_rect_thickness,
            )
        elif self._mode == CanvasMode.ARROW and hasattr(self, "_live_arrow"):
            self._live_arrow = (self._live_arrow[0], pt_pdf)
            self.surface.set_live_arrow(
                self._live_arrow[0], pt_pdf, self.surface._live_arrow_color)
        elif (self._mode in (CanvasMode.SELECT, CanvasMode.VIEW)
              and hasattr(self, "_live_rect")):
            self._live_rect = (self._live_rect[0], pt_pdf)
            self.surface.set_live_rect(
                self._live_rect[0], pt_pdf, "select",
                color_rgb=self.surface._live_rect_color,
            )

    def on_canvas_mouse_release(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Always clear the live overlays before committing so a tiny rect
        # (single click) doesn't keep the dashed outline on screen.
        # VIEW mode reuses the SELECT drag flow so the user can highlight
        # text without ever switching modes.
        if (self._mode in (CanvasMode.SELECT, CanvasMode.VIEW)
                and hasattr(self, "_live_rect")):
            start, end = self._live_rect
            del self._live_rect
            self.surface.clear_live_rect()
            if start and end:
                rect = self._normalize_rect(start, end)
                if rect.width() >= 1 and rect.height() >= 1:
                    self._commit_select_rect(rect)
        elif self._mode == CanvasMode.DRAW and hasattr(self, "_live_stroke"):
            stroke = self._live_stroke
            self.surface.clear_live_strokes()
            del self._live_stroke
            self._commit_stroke(stroke)
        elif self._mode == CanvasMode.HIGHLIGHT and hasattr(self, "_live_rect"):
            start, end = self._live_rect
            del self._live_rect
            self.surface.clear_live_rect()
            if start and end:
                rect = self._normalize_rect(start, end)
                if rect.width() >= 1 and rect.height() >= 1:
                    self._commit_highlight_rect(rect)
        elif self._mode == CanvasMode.RECT and hasattr(self, "_live_rect"):
            start, end = self._live_rect
            del self._live_rect
            self.surface.clear_live_rect()
            if start and end:
                rect = self._normalize_rect(start, end)
                if rect.width() >= 1 and rect.height() >= 1:
                    self._commit_rect(rect)
        elif self._mode == CanvasMode.ELLIPSE and hasattr(self, "_live_rect"):
            start, end = self._live_rect
            del self._live_rect
            self.surface.clear_live_rect()
            if start and end:
                rect = self._normalize_rect(start, end)
                if rect.width() >= 1 and rect.height() >= 1:
                    self._commit_ellipse(rect)
        elif self._mode == CanvasMode.ARROW and hasattr(self, "_live_arrow"):
            start, end = self._live_arrow
            del self._live_arrow
            self.surface.clear_live_arrow()
            if start and end:
                if (end.x() - start.x()) ** 2 + (end.y() - start.y()) ** 2 >= 4:
                    self._commit_arrow(start, end)
        elif self._mode == CanvasMode.SELECT and hasattr(self, "_live_rect"):
            start, end = self._live_rect
            del self._live_rect
            self.surface.clear_live_rect()
            if start and end:
                rect = self._normalize_rect(start, end)
                # Tiny rect (~ <2pt) = single-point pick: copy the word at
                # that location rather than the whole range.
                if rect.width() < 2 and rect.height() < 2:
                    self._commit_select_point(start)
                else:
                    self._commit_select_rect(rect)

    def on_canvas_leave(self, event):
        pass

    @staticmethod
    def _normalize_rect(start, end) -> QRectF:
        return QRectF(
            min(start.x(), end.x()),
            min(start.y(), end.y()),
            abs(end.x() - start.x()),
            abs(end.y() - start.y()),
        )

    # -------------------------------- setters used by annotator/handlers
    def set_active_ink(self, color_rgb: Tuple[float, float, float], thickness: float):
        self._active_color = color_rgb
        self._active_thickness = thickness

    def set_active_highlight(self, color_rgb: Tuple[float, float, float]):
        self._active_highlight_color = color_rgb

    def set_active_shape(self, color_rgb: Tuple[float, float, float],
                          thickness: float = 2.0):
        self._active_shape_color = color_rgb
        self._active_shape_thickness = thickness

    # ------------------------------------------------------- callbacks
    # These are overridden/set by main_window after the annotator is wired.
    _commit_stroke = lambda self, _stroke: None
    _commit_highlight_rect = lambda self, _rect: None
    _commit_rect = lambda self, _rect: None
    _commit_ellipse = lambda self, _rect: None
    _commit_arrow = lambda self, _start, _end: None
    _request_erase_at = lambda self, _pt: None
    _request_note = lambda self, _pt: None
    _request_text_insert = lambda self, _pt: None
    _request_edit_text = lambda self, _pt: None
    _request_signature = lambda self, _pt: None
    _commit_select_point = lambda self, _pt: None
    _commit_select_rect = lambda self, _rect: None
