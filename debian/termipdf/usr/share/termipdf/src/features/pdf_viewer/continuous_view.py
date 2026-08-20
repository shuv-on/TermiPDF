"""
continuous_view.py — Continuous vertical page view mode.

Stacks every page of the open PDF into a single scrollable viewport
with rasterization-on-demand so memory cost stays proportional to
what's on screen rather than the full document.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import fitz
from PyQt6.QtCore import (
    Qt, QSize, QTimer, pyqtSignal,
    QPropertyAnimation, QEasingCurve, QAbstractAnimation, QPoint,
)
from PyQt6.QtGui import QImage, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QAbstractScrollArea,
)


GAP_PX = 12
PREFETCH_PX = 400
# How many pixels of accumulated wheel scroll trigger one kinetic
# momentum animation. Lower = more aggressive momentum; higher = the
# animation only kicks in for fast flicks.
MOMENTUM_THRESHOLD_PX = 60
# Cap on a single momentum animation's target pixel delta so a
# pathological high-resolution mouse can't fling the view by 100k px.
MOMENTUM_MAX_PX = 2400
# Cap on the duration (ms) of one momentum animation — short enough
# that the curve still feels like a single deceleration, long enough
# that the OutCubic ease is visible.
MOMENTUM_MAX_MS = 700
# Standard mouse-wheel notch delta in 1/8-degree units (120° per notch).
WHEEL_NOTCH_DEG = 120
# How many pixels a single notch moves when no pixelDelta is supplied
# (some mice / Linux X11 don't provide pixelDelta).
WHEEL_NOTCH_PX = 100
PLACEHOLDER_INDEX = -1
MAX_WORKERS = 2


class _PageCell(QLabel):
    """A single page cell inside the continuous view."""

    def __init__(self, page_index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.page_index = page_index
        self._pixmap: Optional[QPixmap] = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background-color: #ffffff;"
            "border: 1px solid #cdd6f4;"
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_placeholder_size(self, size: QSize) -> None:
        self._pixmap = None
        self.setFixedSize(size)
        self.setText("")

    def set_pixmap(self, pm: QPixmap) -> None:
        self._pixmap = pm
        self.setFixedSize(pm.size())
        super().setPixmap(pm)

    def has_pixmap(self) -> bool:
        return self._pixmap is not None


class ContinuousView(QWidget):
    """Stacked vertical scrolling view of every page in the open PDF."""

    visible_page_changed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._engine: Optional[object] = None
        self._zoom: float = 1.0
        self._cells: List[_PageCell] = []
        # Cumulative top-Y of each cell; len(_offsets) == len(_cells) + 1
        # so offsets[-1] is the total content height.
        self._offsets: List[int] = []
        self._render_futures: Dict[int, object] = {}
        self._render_results: Dict[int, QPixmap] = {}
        self._render_lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._shutting_down = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("background-color: #313244;")
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll)

        self._container = QWidget()
        self._container.setStyleSheet("background-color: #313244;")
        self._scroll.setWidget(self._container)

        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(8, 8, 8, 8)
        self._vbox.setSpacing(GAP_PX)

        self._add_placeholder_cell()
        # Event-filter on the scroll area itself (not the viewport)
        # so we intercept one well-defined point where QScrollArea
        # forwards wheel events; the viewport path can be missing
        # some events depending on platform.
        self._scroll.installEventFilter(self)
        self._scroll.verticalScrollBar().valueChanged.connect(
            self._on_scroll_changed)

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(40)
        self._repaint_timer.timeout.connect(
            self._render_visible_pages_and_prefetch)

        self._kinetic_v = 0.0
        # Momentum animation: a single QPropertyAnimation targeting the
        # vertical scrollbar's value and driven by OutCubic easing so
        # the page decelerates smoothly over the kinetic tail. We
        # re-create this animation on every new wheel event so the
        # previous deceleration is interrupted cleanly (the spec
        # requires "instant and responsive" input).
        self._momentum_anim: Optional[QPropertyAnimation] = None
        # Accumulated wheel delta in pixels between two animation
        # starts; once it crosses MOMENTUM_THRESHOLD_PX we kick off a
        # momentum animation pointing at the current scroll position
        # plus the accumulated delta.
        self._momentum_pending_px = 0
        # WheelEvent object on which we're currently applying a
        # movement direct from pixelDelta (no animation) so we can
        # break the loop if the user keeps wheeling.
        self._wheel_event_lock = False

    # ---- wheel handling (spec: pixelDelta priority, OutCubic momentum) -
    def _handle_wheel(self, event: QWheelEvent) -> bool:
        """Process one QWheelEvent through our custom curve.

        Returns ``True`` iff the event was consumed and we accepted it
        — the ``eventFilter`` uses the return value to decide whether
        to also let QScrollArea consume the event.
        """
        # Interrupt any in-flight momentum so the new input is
        # immediate (the spec is explicit: "Interrupt ongoing
        # scrolling animations immediately if a new wheelEvent or
        # click-drag gesture occurs").
        self._stop_momentum()
        # ---- pick the pixel delta -----------------------------------
        # ``pixelDelta`` is the spec's preferred source (precision
        # touchpads deliver sub-pixel deltas). Fall back to
        # ``angleDelta`` when the OS doesn't provide pixel-level
        # events (most desktop mice under X11).
        pix = event.pixelDelta()
        if pix.y() != 0:
            dy = int(pix.y())
        else:
            # angleDelta is in 1/8° units; 120 == one notch (positive
            # means "wheel rotated forward / away from user" → content
            # should move UP → bar should DECREASE). The pixel branch
            # above preserves the sign of pixelDelta.y() directly, so
            # for consistency this branch must also preserve the sign
            # of angleDelta.y() (no negation).
            notch = event.angleDelta().y()
            if notch == 0:
                return False  # nothing useful — let Qt handle it.
            # Convert notch -> pixels using WHEEL_NOTCH_PX. We
            # deliberately round towards zero so a single notch always
            # scrolls at least one pixel — Qt's default 3-line jump
            # is what produces the "force step jumps" feel the spec
            # is fixing.
            full_notches = notch // WHEEL_NOTCH_DEG
            remainder = (notch % WHEEL_NOTCH_DEG) * WHEEL_NOTCH_PX // WHEEL_NOTCH_DEG
            dy = full_notches * WHEEL_NOTCH_PX + remainder
        # ---- apply the immediate shift ----------------------------
        bar = self._scroll.verticalScrollBar()
        new_value = max(bar.minimum(),
                        min(bar.maximum(), bar.value() - dy))
        bar.setValue(new_value)
        # ---- accumulate for momentum ------------------------------
        # Weighted by the magnitude so a hard flick produces a longer
        # glide than a single careful notch.
        self._momentum_pending_px += abs(dy)
        if self._momentum_pending_px >= MOMENTUM_THRESHOLD_PX:
            # dy < 0 means scroll-down (content moves down → bar
            # value INCREASES). dy > 0 means scroll-up (content moves
            # up → bar value DECREASES). The momentum target is
            # ``bar + sign * magnitude`` so the sign convention must
            # match the bar's direction.
            self._start_momentum(self._momentum_pending_px,
                                 sign=+1 if dy < 0 else -1)
            self._momentum_pending_px = 0
        event.accept()
        return True




    def _add_placeholder_cell(self) -> None:
        cell = _PageCell(PLACEHOLDER_INDEX)
        cell.set_placeholder_size(QSize(800, 1000))
        self._vbox.addWidget(cell, 0, Qt.AlignmentFlag.AlignHCenter)
        self._cells.append(cell)

    def _rebuild_offsets(self) -> None:
        # First offset is the top margin (matches _vbox contentsMargins.top()).
        self._offsets = [8]
        for cell in self._cells:
            self._offsets.append(
                self._offsets[-1] + cell.height() + GAP_PX)

    def attach_engine(self, engine) -> None:
        self._engine = engine
        self._zoom = getattr(engine, "zoom", 1.0) or 1.0
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the page stack from the current engine state."""
        self._shutting_down = True
        with self._render_lock:
            self._render_futures.clear()
            self._render_results.clear()
        self._shutting_down = False

        for cell in self._cells:
            cell.setParent(None)
            cell.deleteLater()
        self._cells.clear()
        self._offsets.clear()

        if (not self._engine
                or not getattr(self._engine, "is_open", False)):
            self._add_placeholder_cell()
            self._rebuild_offsets()
            return

        n = self._engine.page_count
        for i in range(n):
            try:
                rect = self._engine.get_page(i).rect
                w = max(1, int(rect.width * self._zoom))
                h = max(1, int(rect.height * self._zoom))
            except Exception:
                w, h = 800, 1000
            cell = _PageCell(i)
            cell.set_placeholder_size(QSize(w, h))
            self._vbox.addWidget(cell, 0, Qt.AlignmentFlag.AlignHCenter)
            self._cells.append(cell)
        self._rebuild_offsets()

        self._ensure_executor()
        self._repaint_timer.start()

    def _ensure_executor(self) -> None:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=MAX_WORKERS,
                thread_name_prefix="termipdf-cv")

    def set_zoom(self, f: float) -> None:
        self._zoom = max(0.1, f)
        self.refresh()

    def zoom_in(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def set_visible_page(self, page_1based: int) -> None:
        """Scroll so the requested page is visible at the top."""
        if not self._engine or not self._engine.is_open:
            return
        if page_1based < 1 or page_1based > self._engine.page_count:
            return
        # _offsets[i] = top of cell i; clamp to len(_cells).
        idx = min(page_1based - 1, len(self._cells) - 1)
        if idx < 0 or idx >= len(self._offsets) - 1:
            return
        self._scroll.verticalScrollBar().setValue(self._offsets[idx])

    def shutdown(self) -> None:
        """Cancel pending work and drain render state.

        Daemon-thread workers won't block process exit, but the
        QTimer.singleShot callbacks they schedule would otherwise
        fire into a deleted widget. Clear both pending futures and
        any already-completed results so no post-shutdown GUI
        mutation happens.
        """
        self._shutting_down = True
        with self._render_lock:
            self._render_futures.clear()
            self._render_results.clear()
        self._stop_momentum()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def eventFilter(self, obj, event):
        # Intercept wheel events on the scroll area itself so we can
        # apply our custom pixel-aware + momentum-animated scroll curve.
        # We register on the *scroll area* (not its viewport) because
        # QScrollArea forwards wheel events to itself before they reach
        # the viewport — capturing them at this level gives a single,
        # dependable interception point and works on all platforms.
        if obj is self._scroll and event.type() == event.type().Wheel:
            if self._handle_wheel(event):
                return True
        return super().eventFilter(obj, event)

    def _stop_momentum(self) -> None:
        """Cancel any in-flight momentum animation.

        Called from ``wheelEvent`` (so a new wheel cancels the
        previous glide) and from ``shutdown`` (so the dialog stops
        animating when its parent is being torn down).
        """
        self._momentum_pending_px = 0
        if self._momentum_anim is not None:
            try:
                self._momentum_anim.stop()
            except Exception:
                pass
            # deleteLater keeps the animation alive long enough for
            # any in-flight ``finished`` signal to fire without
            # referencing a half-torn-down widget.
            self._momentum_anim.deleteLater()
            self._momentum_anim = None

    def _start_momentum(self, accumulated_px: int, *, sign: int) -> None:
        """Kick off an OutCubic momentum glide for ``accumulated_px``
        pixels in the direction given by ``sign`` (-1 / +1).

        ``QPropertyAnimation`` is used (per the spec) so the value is
        driven by an easing curve rather than a manual timer tick —
        this gives a clean, single ``OutCubic`` deceleration curve
        visible to the user as one smooth motion.
        """
        bar = self._scroll.verticalScrollBar()
        if bar.minimum() == bar.maximum():
            return  # nothing to scroll
        # Cap so a giant trackpad flick doesn't fling by 50k pixels.
        magnitude = min(int(accumulated_px), MOMENTUM_MAX_PX)
        target = bar.value() + sign * magnitude
        target = max(bar.minimum(), min(bar.maximum(), target))
        if target == bar.value():
            return  # already at the requested end
        # Duration scales with the magnitude (so a longer flick gets
        # more glide time) but is bounded so the curve never lingers.
        duration = min(MOMENTUM_MAX_MS,
                       max(150, int(magnitude * 0.25)))
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Cleanup: every time we replace the animation we delete the
        # old one; we keep the new one alive while it runs.
        old = self._momentum_anim
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass
            old.deleteLater()
        self._momentum_anim = anim
        anim.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _on_scroll_changed(self, _value: int) -> None:
        self._repaint_timer.start()

    def _render_visible_pages_and_prefetch(self) -> None:
        if not self._engine or not self._engine.is_open:
            return
        bar = self._scroll.verticalScrollBar()
        view_top = bar.value()
        view_h = self._scroll.viewport().height()
        view_bottom = view_top + view_h
        band_top = max(0, view_top - PREFETCH_PX)
        band_bottom = view_bottom + PREFETCH_PX
        center = view_top + view_h // 2

        centered = PLACEHOLDER_INDEX
        with self._render_lock:
            busy = set(self._render_futures.keys())

        # Single pass over cells; collects which to rasterize + the
        # centered page (one walk, not two).
        for i, cell in enumerate(self._cells):
            if i >= len(self._offsets) - 1:
                break
            top = self._offsets[i]
            bottom = top + cell.height()
            if top <= center <= bottom:
                centered = cell.page_index
            if bottom < band_top or top > band_bottom:
                continue
            if (not cell.has_pixmap()
                    and cell.page_index >= 0
                    and cell.page_index not in busy):
                self._kick_render(cell.page_index)
                busy.add(cell.page_index)

        if centered >= 0:
            self.visible_page_changed.emit(centered)

    def _kick_render(self, page_index: int) -> None:
        if self._shutting_down or not self._engine or self._executor is None:
            return
        engine = self._engine
        zoom = self._zoom

        def _worker():
            try:
                page = engine.get_page(page_index)
                matrix = fitz.Matrix(zoom, zoom)
                png = page.get_pixmap(matrix=matrix, alpha=False).tobytes("png")
                pm = QPixmap.fromImage(QImage.fromData(png))
            except Exception:
                return
            if self._shutting_down:
                return
            with self._render_lock:
                self._render_results[page_index] = pm
            QTimer.singleShot(0, lambda: self._apply_rendered(page_index))

        future = self._executor.submit(_worker)
        with self._render_lock:
            self._render_futures[page_index] = future

    def _apply_rendered(self, page_index: int) -> None:
        if self._shutting_down:
            return
        with self._render_lock:
            pm = self._render_results.pop(page_index, None)
            self._render_futures.pop(page_index, None)
        if pm is None:
            return
        # Defend against refresh() rebuilding cells while a worker
        # was in flight: cell count or identity may have changed.
        if not (0 <= page_index < len(self._cells)):
            return
        cell = self._cells[page_index]
        if cell.page_index != page_index:
            return
        cell.set_pixmap(pm)

    # NOTE: the previous implementation used a 16 ms ``QTimer`` and a
    # ``_kinetic_v`` decay field for momentum. That implementation was
    # replaced by a ``QPropertyAnimation`` driven by an ``OutCubic``
    # easing curve (see ``_start_momentum``). The manual timer-based
    # decay produced visible ``stair-step`` motion at low frame rates
    # — the easing-curve approach produces a single smooth glide.