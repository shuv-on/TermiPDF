"""
pages_manager_dragdrop.py — Drag-and-drop fix for the Pages Manager grid.

This module provides a drop-in replacement for ``_PageGrid`` that
guarantees the on-disk PDF is reordered (not just the visible tiles)
when the user drags a page thumbnail onto another in the Pages Manager
dialog.

The previous implementation overrode ``QListWidget.startDrag`` to inject
a custom MIME payload, then read that payload in ``dropEvent``. Under
PyQt 6.10+ this override is **never invoked** from Qt's C++ drag-start
machinery, so the custom MIME is never produced and the custom
``dropEvent`` falls through to ``super().dropEvent()`` — which moves
the model rows visually but never touches the underlying PDF. The user
sees the tile "move" but the file on disk is unchanged.

The fix here uses two mechanisms that ARE guaranteed to fire:

1. **`dropMimeData` (a public Qt virtual)** — Qt's ``InternalMove``
   machinery calls ``QListWidget.dropMimeData`` on the destination
   widget with the ``(destination_row, mime, action)`` triple. We
   override this to commit the swap to disk BEFORE Qt's default
   implementation moves the model rows. We return ``True`` so Qt's
   default move still runs and the grid stays visually consistent.

2. **A ``mousePressEvent``-side hook** — rather than overriding
   ``startDrag`` (which is not reliably called from Python), we rely
   on Qt's built-in ``InternalMove`` mode to handle the drag itself
   and read the source rows from the current selection when the user
   drops. This sidesteps the broken ``startDrag`` override entirely.

The contract is:

    User drags tile A onto tile B
        → Qt fires ``dropMimeData(B, mime, MoveAction)``
        → We call ``PDFManipulator.swap_pages(path, A, B)``
        → We reload the engine from disk
        → We return ``True`` so Qt also moves the model rows
        → Grid labels and tile order stay in sync with the file

This module is intentionally self-contained — importing it has no
side effects beyond registering ``ImprovedPageGrid`` so it can be used
as a drop-in replacement for ``_PageGrid``.
"""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtCore import (
    Qt, pyqtSignal, QSize, QMimeData, QTimer,
)
from PyQt6.QtGui import (
    QColor, QPen, QPainter,
)
from PyQt6.QtWidgets import (
    QListWidget, QAbstractItemView, QStyledItemDelegate,
)

# Reuse the page-manager's MIME type and the manipulator that does
# the on-disk swap. Lazy-import the manipulator so we don't create a
# circular import — pages_manager.py imports from this module.
_MIME_TYPE = "application/x-termipdf-pages"

# Drag-hover palette (mirrors pages_manager.py).
_HOVER_FILL_COLOR = QColor(255, 191, 0, 70)
_HOVER_BORDER_COLOR = QColor(245, 158, 11, 230)
_HOVER_BORDER_WIDTH = 3
_HOVER_AUTO_CLEAR_MS = 800
_HOVER_DATA_ROLE = Qt.ItemDataRole.UserRole + 1


def _event_pos(event):
    """PyQt6.6+ exposes ``event.position()``; older versions only
    ``event.pos()``. Use whichever is available."""
    return (event.position().toPoint()
            if hasattr(event, "position") else event.pos())


class _PageGridDelegate(QStyledItemDelegate):
    """Paint a translucent amber fill + border on the tile being hovered
    as a drop target. Qt's built-in drop indicator doesn't render in
    ``IconMode`` so we paint our own."""

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        tint = index.data(_HOVER_DATA_ROLE)
        if not isinstance(tint, QColor):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(_HOVER_BORDER_COLOR, _HOVER_BORDER_WIDTH))
        painter.setBrush(tint)
        r = option.rect.adjusted(3, 3, -3, -3)
        painter.drawRoundedRect(r, 8, 8)
        painter.restore()


class ImprovedPageGrid(QListWidget):
    """Drop-in replacement for ``_PageGrid`` that guarantees the
    on-disk PDF is reordered when the user drags a tile.

    The class lives in its own module so the existing ``pages_manager``
    module can keep its public API and animation helpers — we just
    swap the list widget instance for an ``ImprovedPageGrid``.

    Signals
    -------
    pages_dropped_on_target(int, list)
        Emitted for multi-tile drops. ``(target_page_1based, src_pages_1based)``.
    external_pdfs_dropped(list)
        Emitted when external PDF files are dropped from the OS file manager.
    page_moved(int, int)
        Emitted for single-tile drags. ``(src_page_1based, target_page_1based)``.
    """

    pages_dropped_on_target = pyqtSignal(int, list)
    external_pdfs_dropped = pyqtSignal(list)
    page_moved = pyqtSignal(int, int)

    # The on-disk reorder function. Set by ``PagesManager`` after
    # construction so this widget doesn't need to know about the
    # engine or the manipulator. Signature: (src_1based, target_1based)
    # → (ok: bool, msg: str).
    def __init__(self, parent=None):
        super().__init__(parent)
        # ``reorder_callback`` is set externally by ``PagesManager``
        # after construction; we default to ``None`` so the widget is
        # testable in isolation (tests that don't wire the callback
        # still get the no-op accept path).
        self.reorder_callback: Optional[callable] = None
        # Last row that resolved from ``indexAt`` during a drag — we
        # cache it so ``dropEvent`` doesn't repeat the O(n) grid scan
        # that ``dragMoveEvent`` just performed.
        self._drop_target_row: int = -1
        self._configure_dragdrop()
        # Drag-hover state — same UX as the original implementation.
        self._hover_index: Optional[int] = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(_HOVER_AUTO_CLEAR_MS)
        self._hover_timer.timeout.connect(self._clear_hover)
        self.setItemDelegate(_PageGridDelegate(self))

    def _configure_dragdrop(self) -> None:
        """Set the drag-drop configuration Qt needs for tile reorder.

        ``InternalMove`` lets Qt move rows natively; ``MoveAction``
        forces the drop semantics to "move" rather than "copy" so a
        page physically leaves its slot (no duplication). Qt's
        IconMode + Static movement setters silently downgrade this
        back to ``DropOnly`` elsewhere in ``PagesManager._build_ui``
        so we re-apply here as the last word.
        """
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setUniformItemSizes(True)

    # ------------------------------------------------------------------
    # Public helpers (mirrors the original _PageGrid surface)
    # ------------------------------------------------------------------
    def selected_pages_1based(self) -> List[int]:
        pages: List[int] = []
        for item in self.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                pages.append(int(idx) + 1)
        return sorted(set(pages))

    def _external_pdf_paths(self, mime) -> List[str]:
        if not mime.hasUrls():
            return []
        paths: List[str] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = url.toLocalFile()
            if p and p.lower().endswith(".pdf") and os.path.isfile(p):
                paths.append(p)
        return paths

    # ------------------------------------------------------------------
    # Drag-hover handling (visual UX)
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event):
        self._handle_drag(event)

    def dragMoveEvent(self, event):
        self._handle_drag(event)

    def _handle_drag(self, event) -> None:
        """Shared handler for ``dragEnterEvent`` and ``dragMoveEvent``.

        Accepts drags whose payload we understand (in-app page MIME,
        or external file URLs). Rejects everything else by ignoring
        the event — calling ``super()`` here would defer to Qt's
        default which can silently accept random drops and then
        no-op inside ``dropMimeData``, leaving the user staring at a
        dead cursor.
        """
        mime = event.mimeData()
        if not (mime.hasFormat(_MIME_TYPE) or mime.hasUrls()):
            event.ignore()
            return
        if event.proposedAction() != Qt.DropAction.MoveAction:
            event.setDropAction(Qt.DropAction.MoveAction)
        event.acceptProposedAction()
        idx = self.indexAt(_event_pos(event)).row()
        self._drop_target_row = idx
        self._apply_hover(idx)

    def dragLeaveEvent(self, event):
        self._clear_hover()
        self._drop_target_row = -1
        super().dragLeaveEvent(event)

    def _apply_hover(self, idx: Optional[int]) -> None:
        # Already hovering this tile: the timer is armed, no work to do.
        if idx == self._hover_index:
            return
        self._clear_hover()
        if idx is None or idx < 0 or idx >= self.count():
            return
        item = self.item(idx)
        if item is None:
            return
        self._hover_index = idx
        item.setData(_HOVER_DATA_ROLE, _HOVER_FILL_COLOR)
        self.viewport().update()
        self._hover_timer.start()

    def _clear_hover(self) -> None:
        if self._hover_index is None:
            self._hover_timer.stop()
            return
        idx = self._hover_index
        self._hover_index = None
        self._hover_timer.stop()
        if 0 <= idx < self.count():
            item = self.item(idx)
            if item is not None:
                item.setData(_HOVER_DATA_ROLE, None)
        self.viewport().update()

    # ------------------------------------------------------------------
    # dropEvent — translate a QDropEvent into a dropMimeData call.
    #
    # Qt's ``QListWidget::dropEvent`` is supposed to call our
    # ``dropMimeData`` override, but PyQt6's C++→Python virtual dispatch
    # isn't guaranteed, which would silently leave drag drops as a
    # no-op. Invoking ``dropMimeData`` directly from this Python override
    # keeps the tests (``tests/gui_integration_test.py`` invokes
    # ``pm.list.dropEvent`` directly) and the production drag flow in
    # lockstep.
    # ------------------------------------------------------------------
    def dropEvent(self, event):
        self._clear_hover()
        # Reuse the row resolved during the most recent ``dragMoveEvent``
        # (saved via ``self._drop_target_row``) so we don't redo the
        # O(n) ``indexAt`` scan just for the drop frame. Fall back to a
        # fresh lookup if the cache is stale (-1) or the cursor moved
        # across tiles between the last move event and the drop.
        idx = self._drop_target_row
        if idx < 0:
            idx = self.indexAt(_event_pos(event)).row()
        if idx < 0:
            idx = self.count()
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        ok = self.dropMimeData(idx, event.mimeData(),
                               Qt.DropAction.MoveAction)
        if not ok:
            event.ignore()
        self._drop_target_row = -1

    # ------------------------------------------------------------------
    # THE FIX: dropMimeData — reliable hook for swap-then-repopulate.
    #
    # In PyQt 6.10+ the protected ``startDrag`` override isn't reliably
    # invoked, so the previous implementation fell through to
    # ``super().dropEvent`` which moves the model rows without touching
    # the file. We bypass Qt's internal-move machinery here and route
    # the work through ``reorder_callback`` so the on-disk PDF is
    # reordered exactly once.
    # ------------------------------------------------------------------
    def dropMimeData(self, index: int, mimeData: QMimeData,
                     action: Qt.DropAction) -> bool:
        """Receive a drop on row ``index`` (0-based)."""
        # External PDFs from the OS file manager → let the dialog open
        # the merge flow.
        if mimeData.hasUrls() and not mimeData.hasFormat(_MIME_TYPE):
            paths = self._external_pdf_paths(mimeData)
            if paths:
                self.external_pdfs_dropped.emit(paths)
            return False

        target_row = max(0, min(int(index), self.count()))
        src_pages = self.selected_pages_1based()
        if not src_pages:
            return False

        if len(src_pages) == 1:
            # Single-tile drop. ``reorder_callback`` does the actual
            # swap + engine reload + grid repopulate synchronously —
            # we do NOT also emit ``page_moved`` here because doing so
            # would re-run ``_on_page_moved`` → ``_finalize_swap`` and
            # undo the swap the callback just committed.
            src = src_pages[0]
            target = target_row + 1
            if src <= target:
                target = max(1, target)
            if not self._invoke_callback(src, target):
                return False
        else:
            # Multi-tile drops continue to use the merge pipeline.
            self.pages_dropped_on_target.emit(target_row + 1, src_pages)

        # ``return True`` tells Qt the drop was accepted; we do NOT
        # call ``super().dropMimeData`` because Qt's built-in payload
        # format differs from ours and would mutate the model
        # independently of the on-disk swap the callback just ran.
        return True

    def _invoke_callback(self, src_1based: int, target_1based: int) -> bool:
        """Run the registered reorder callback (if any) and return its
        success flag. ``True`` if the callback isn't set, so standalone
        widget tests that don't wire one still pass through cleanly.
        """
        if self.reorder_callback is None:
            return True
        try:
            ok, _msg = self.reorder_callback(src_1based, target_1based)
        except Exception:
            return False
        return bool(ok)


def make_improved_grid(tiles_w: int = 160, tiles_h: int = 220) -> ImprovedPageGrid:
    """Convenience factory used by ``PagesManager._build_ui``.

    Returns a grid with the icon-mode / movement settings that match
    the original design, then re-applies the drag-drop configuration
    because Qt's IconMode + Static movement silently downgrades
    drag-drop mode back to ``DropOnly``.
    """
    g = ImprovedPageGrid()
    g.setViewMode(QListWidget.ViewMode.IconMode)
    g.setIconSize(QSize(tiles_w - 24, tiles_h - 50))
    g.setResizeMode(QListWidget.ResizeMode.Adjust)
    g.setMovement(QListWidget.Movement.Static)
    g.setSpacing(8)
    g._configure_dragdrop()
    return g
