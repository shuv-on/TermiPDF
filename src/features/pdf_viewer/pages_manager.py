"""
pages_manager.py — Pages Manager (Windows-style grid view).

A modal dialog that shows every page in the open PDF as a thumbnail tile
in a responsive grid. Supports:

* Click a tile → navigate to that page in the main viewer.
* Multi-select with Ctrl+Click / Shift+Click.
* Drag-and-drop tiles onto other tiles → reorder the document (move
  the dragged page to the target's position).
* Right-click on selected tile(s) → context menu:
    - Generate new PDF from these pages (saves to a user-chosen path)
    - Delete selected pages
    - Rotate selected pages

Thumbnails are rendered on a background QThread so the dialog opens
instantly — placeholder grey tiles appear immediately, then the real
thumbnails stream in as they finish (one worker thread per populate).

The dialog is opened from the main window's "Pages" toolbar button. It
is also used by the terminal commands ``gen npdf p-1,2,3`` (which calls
``generate_new_pdf(pages, out_path)`` programmatically).
"""
from __future__ import annotations

import os
from typing import List, Optional, Sequence

import fitz

from PyQt6.QtCore import (
    Qt, pyqtSignal, QSize, QPoint, QThread, QObject, QPropertyAnimation,
    QEasingCurve, QAbstractAnimation, QRect, QTimer,
    QParallelAnimationGroup,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QAction, QIcon, QColor, QBrush, QPen, QPainter,
)
from PyQt6.QtWidgets import (
    QDialog, QListWidget, QListWidgetItem, QAbstractItemView, QStyle,
    QMenu, QFileDialog, QMessageBox, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QApplication, QSpinBox, QButtonGroup,
    QRadioButton, QDialogButtonBox,
)

from features.pdf_viewer.viewer_engine import ViewerEngine
from features.pdf_editor.manipulation import PDFManipulator


# Tile size used by the grid. The grid is responsive — the column count
# is computed from the dialog width.
TILE_W = 160
TILE_H = 220
GRID_COLUMNS_MIN = 2
GRID_COLUMNS_MAX = 8


# Swap-animation palette. The ghost overlay uses a translucent amber
# fill so the moving tile stands out against the dark grid background
# without obscuring the destination slot's preview.
_SWAP_FILL     = "rgba(255, 235, 59, 0.85)"
_SWAP_BORDER   = "#f59e0b"
_SWAP_HIGHLIGHT = QColor(255, 235, 59, 90)
_SWAP_DURATION_MS = 420


def _placeholder_tile(w: int, h: int, text: str = "…",
                      index: int = 0) -> QPixmap:
    """Render the placeholder thumbnail shown while a real thumbnail is
    being rasterized in the worker thread.

    Pure-PyQt draw (no fitz) so it's effectively free. The result
    looks like a faded "loading…" tile so the user understands the
    grid is alive even before thumbnails have arrived.
    """
    pm = QPixmap(w, h)
    pm.fill(QColor("#2a2a36"))
    from PyQt6.QtGui import QPainter
    p = QPainter(pm)
    try:
        from PyQt6.QtGui import QPen
        p.setPen(QPen(QColor("#5a5a70")))
        p.drawRect(0, 0, w - 1, h - 1)
        p.setPen(QColor("#a0a0b5"))
        # Show the page number so the user has a stable label even
        # before the thumbnail arrives.
        f = p.font()
        f.setPointSize(max(10, min(18, w // 8)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
    finally:
        p.end()
    return pm


class MoveToDialog(QDialog):
    """Small "Move Page To…" dialog launched from the Pages Manager.

    Lets the user pick a 1-based target page number and choose whether
    to land ``before`` it or ``after`` it. The Apply button is disabled
    until the input parses as an integer in [1, page_count] (with the
    additional restriction that ``target == source`` is forbidden, but
    that's enforced by the caller since this dialog is reusable).

    The dialog is intentionally compact (fixed size, no resize grip)
    so it feels like a pop-over rather than a modal window, matching
    the lightweight "explicit repositioning" workflow in the spec.
    """

    # Emitted when the user clicks Apply: (src_page_1based, target_page_1based, position)
    # where position is "before" or "after". The dialog closes itself
    # before emitting so the receiver sees a hidden dialog.
    move_requested = pyqtSignal(int, int, str)

    def __init__(self, parent: QDialog, src_page_1based: int,
                 page_count: int):
        super().__init__(parent)
        self._src_page = src_page_1based
        self._page_count = page_count
        self.setWindowTitle("Move Page To…")
        # Non-resizable pop-over feel — fixed size, no help button.
        self.setWindowFlags(self.windowFlags()
                            & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setModal(True)
        self.setMinimumWidth(320)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        intro = QLabel(
            f"Move <b>Page {self._src_page}</b> to a new position in the "
            f"document. Pick a target page and choose whether Page "
            f"{self._src_page} should land <i>before</i> it or <i>after</i> it.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # --- Target page input -----------------------------------------
        row = QHBoxLayout()
        row.addWidget(QLabel("Target page:"))
        self._spin = QSpinBox(self)
        self._spin.setRange(1, self._page_count)
        # Default to the next page (or the last page if we are at the
        # end of the doc), which is the natural follow-up move.
        default = min(self._src_page + 1, self._page_count)
        if default == self._src_page and self._src_page > 1:
            default = self._src_page - 1
        self._spin.setValue(default)
        self._spin.setObjectName("moveToTargetSpin")
        row.addWidget(self._spin, 1)
        row.addWidget(QLabel(f"/ {self._page_count}"))
        root.addLayout(row)

        # --- Position toggle (Before / After) --------------------------
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Position:"))
        self._btn_group = QButtonGroup(self)
        self._rb_before = QRadioButton("Before target")
        self._rb_after = QRadioButton("After target")
        self._btn_group.addButton(self._rb_before, 0)
        self._btn_group.addButton(self._rb_after, 1)
        # Default to "Before" — matching the spec's example syntax
        # ("reorder p-5 b p-2").
        self._rb_before.setChecked(True)
        pos_row.addWidget(self._rb_before)
        pos_row.addWidget(self._rb_after)
        pos_row.addStretch(1)
        root.addLayout(pos_row)

        # --- Apply / Cancel --------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        # Wire the standard Apply/Cancel to our own handlers so the
        # dialog closes cleanly with the conventional Accept/Reject
        # return values (in addition to emitting move_requested on
        # success).
        self._apply_btn = buttons.button(
            QDialogButtonBox.StandardButton.Apply)
        self._apply_btn.setObjectName("moveToApplyBtn")
        self._apply_btn.setText("Apply")
        self._apply_btn.setDefault(True)
        # BUG FIX: ``QDialogButtonBox.accepted`` only fires for OK/
        # Yes buttons; the Apply button is a third category and
        # silently does NOTHING on click if you rely on `accepted`
        # alone — meaning the grid/document never update because
        # ``move_requested`` is never emitted. Wire Apply's own
        # ``clicked`` signal directly so this is impossible to
        # accidentally regress.
        self._apply_btn.clicked.connect(self._on_apply)
        # Cancel is fine via the standard ``rejected`` because it maps
        # to the same slot regardless of which button (Close/Cancel/
        # Cancel-all) the user activates.
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_apply(self):
        """Validate, emit move_requested, and close the dialog.

        Connected to the Apply button's ``clicked`` signal — NOT to
        ``QDialogButtonBox.accepted`` — so the grid + document always
        update when the user submits the dialog (the original code
        relied on ``accepted`` which silently never fires for the
        Apply button on several Qt versions, leaving the document
        state stale).
        """
        target = int(self._spin.value())
        if target == self._src_page:
            QMessageBox.warning(
                self, "Same page",
                f"Page {self._src_page} is already at that position; "
                f"choose a different target page.")
            return
        position = ("before" if self._btn_group.checkedId() == 0
                    else "after")
        # Emit FIRST (so the parent can react with animation), then
        # close with Accepted so the dialog properly tears down.
        self.move_requested.emit(self._src_page, target, position)
        self.accept()


# ------------------------------------------------- background thumbnailer
class _ThumbnailWorker(QObject):
    """Rasterize pages in a worker thread, emitting per-page PNG bytes.

    The PagesManager hands off a (path, count, zoom, w, h) tuple and
    listens for ``thumbnail_ready(index, png_bytes)``. Emits one signal
    per page; the consumer applies each one to its list item via
    ``QListWidgetItem.setIcon``.
    """
    thumbnail_ready = pyqtSignal(int, bytes)   # 0-based index, PNG bytes
    finished = pyqtSignal()

    def __init__(self, path: str, count: int, zoom: float,
                 target_w: int, target_h: int):
        super().__init__()
        self._path = path
        self._count = count
        self._zoom = zoom
        self._w = target_w
        self._h = target_h
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            doc = fitz.open(self._path)
            try:
                for i in range(self._count):
                    if self._cancelled:
                        return
                    try:
                        page = doc.load_page(i)
                        pix = page.get_pixmap(
                            matrix=fitz.Matrix(self._zoom, self._zoom),
                            alpha=False)
                        self.thumbnail_ready.emit(i, pix.tobytes("png"))
                    except Exception:
                        # Don't abort the whole batch on one bad page.
                        continue
            finally:
                doc.close()
        except Exception:
            pass
        finally:
            self.finished.emit()


class PageGridWidget(QListWidget):
    """QListWidget whose dropEvent runs the same backend as the
    terminal ``swap`` command.

    Configuring and populating this grid is the parent's job; this
    class only wires drag-drop so the user can tile-rearrange via the
    same code path the terminal uses. The drag-drop machinery itself
    is treated as a transparent pass-through: the only logic that
    lives here is "translate the user's drag into a `swap A B`
    command string and hand it to the registered command runner".

    The contract is:

        User drags tile A onto tile B
            → ``dropEvent`` resolves ``src_row`` via ``currentRow()``
            → ``dropEvent`` resolves ``tgt_row`` via
              ``self.row(self.itemAt(event.position().toPoint()))``
            → We dispatch ``swap <src> <tgt>`` to the parent's
              ``_command_runner`` callable
            → The terminal backend handles engine reload, animation,
              repopulation, and signal emission — exactly once.

    We deliberately route through the terminal backend (instead of
    calling ``PDFManipulator.swap_pages`` directly here) so the GUI
    chain is *literally the same function call* a typed terminal
    command runs — i.e. validation, status-bar feedback, animation in
    the open PM, disk write, engine reload, and signal emission all
    happen exactly once, in the same order, regardless of how the
    swap was triggered.

    The ``command_runner`` callable is held on the parent ``PagesManager``
    so we read it through ``self.parent()`` at drop time — that way
    the parent can swap runners (e.g. for tests) without re-creating
    the widget, and we never carry a stale reference.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # The command runner is read from the parent (``PagesManager``)
        # at drop time so we always see the parent's most-recent
        # reference — tests can swap the runner in-place by mutating
        # ``pm._command_runner`` and we don't need to be re-wired.
        self._apply_drag_drop_config()

    def _apply_drag_drop_config(self) -> None:
        """Set every flag the drag/drop machinery needs.

        Called from ``__init__`` and re-issued after the parent flips
        ``setViewMode(IconMode)`` + ``setMovement(Static)`` because Qt
        silently downgrades ``dragDropMode`` back to ``DropOnly`` in
        that combination. The parent calls ``setViewMode`` first,
        then ``finalize_view_mode`` (this method) so the config is
        the single source of truth.
        """
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setUniformItemSizes(True)

    def _command_runner(self, raw: str):
        """Resolve the runner at drop time so we always see the
        parent's latest reference, never a stale one captured at
        construction."""
        parent = self.parent()
        if parent is None:
            raise RuntimeError(
                "PageGridWidget has no parent — cannot resolve "
                "command runner.")
        return parent._command_runner(raw)

    def dropEvent(self, event):
        # Source index (0-based): the row currently selected. The
        # ``InternalMove`` machinery keeps the source tile flagged as
        # ``current`` until the drop fires, so ``currentRow()`` is the
        # exact row the user started dragging.
        src_row = self.currentRow()
        # Target index (0-based): the row under the cursor at the
        # moment the drop fires. ``event.position()`` is the PyQt6.6+
        # API; fall back to ``event.pos()`` for older bindings. If the
        # cursor lands on the gap between tiles, ``itemAt`` returns
        # ``None`` and we treat the drop as "append to end".
        pos = (event.position().toPoint()
               if hasattr(event, "position") else event.pos())
        tgt_item = self.itemAt(pos)
        tgt_row = (self.row(tgt_item) if tgt_item is not None
                   else self.count())

        if src_row < 0 or tgt_row < 0 or src_row == tgt_row:
            # No-op drag (src==tgt) or degenerate: ignore.
            event.ignore()
            return

        # 0-based rows → 1-based page numbers, matching the terminal's
        # ``swap`` command syntax.
        from_page = src_row + 1
        to_page = tgt_row + 1

        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

        # Hand the swap to the terminal backend. The string is the
        # same one the user would type at the prompt, so we get all
        # the validation + animation + engine reload + status-bar
        # feedback for free. The backend is responsible for repopulating
        # the grid when the swap finalizes (via the animation pipeline
        # or, in the silent fallback, immediately).
        try:
            self._command_runner(f"swap {from_page} {to_page}")
        except Exception as exc:
            QMessageBox.warning(
                self, "Drag-drop failed", f"Swap failed: {exc}")

    # ``dragEnterEvent`` / ``dragMoveEvent`` keep Qt's standard
    # "accept MoveAction" behaviour so the dropEvent actually fires
    # under PyQt6 6.10+ (where ``startDrag`` overrides aren't
    # reliably invoked). Without these acceptors Qt would silently
    # reject the drop and the user would see a dead cursor.
    #
    # Real QListWidget-internal drags use Qt's own private mime type
    # (``application/x-qt-windows-mime;type="application/x-qt-item"``
    # on X11, similar internal names elsewhere). We accept any
    # payload that carries at least one MIME format — text, urls,
    # or Qt's internal drag mime — so the user's actual tile-on-tile
    # drag lands. An empty ``QMimeData`` (no formats) is rejected
    # so dropping a bare event from a non-application source
    # doesn't trigger our command runner.
    def _accept_drag(self, event) -> None:
        mime = event.mimeData()
        if (mime.hasText() or mime.hasUrls()
                or any(mime.hasFormat(f) for f in mime.formats())):
            event.acceptProposedAction()

    def dragEnterEvent(self, event):
        self._accept_drag(event)

    def dragMoveEvent(self, event):
        self._accept_drag(event)


class PagesManager(QDialog):
    """Grid view of all pages with multi-select, drag-drop, and context menu."""

    navigate_to_page = pyqtSignal(int)      # 1-based page index
    pages_deleted = pyqtSignal(list)       # list[int] 1-based pages that were removed
    new_pdf_generated = pyqtSignal(str)     # absolute output path
    # Emitted after an in-place page reorder completes. Carries the
    # new 1-based page number of the page that was moved (so the main
    # viewer can keep the cursor on it after the rebuild).
    pages_reordered = pyqtSignal(int)
    # Emitted after a true swap (drag A onto B). Carries the two page
    # numbers post-swap: (1-based page-A, 1-based page-B) — both pages
    # are still in the doc, just in each other's old slots.
    pages_swapped = pyqtSignal(int, int)

    def __init__(self, engine: ViewerEngine, parent=None,
                 command_runner=None):
        super().__init__(parent)
        self.engine = engine
        # ``command_runner`` is injected by main_window so drag-drop
        # routes through the same terminal backend (``parser.execute``)
        # that the user types at the prompt. When not supplied (e.g.
        # stand-alone PM tests) we fall back to a silent in-process
        # swap so the dialog still works without the main window.
        self._command_runner = (command_runner
                                if command_runner is not None
                                else self._silent_command_runner)
        self.setWindowTitle("Pages Manager")
        self.setMinimumSize(640, 480)
        self.resize(960, 640)
        self.setModal(False)
        # Thumbnail rendering runs on a worker thread so the dialog
        # opens instantly for big PDFs (was: synchronous, blocking
        # open until every thumbnail rendered).
        self._thumb_thread: Optional[QThread] = None
        self._thumb_worker: Optional[_ThumbnailWorker] = None
        self._build_ui()
        self._populate()

    def _silent_command_runner(self, raw: str) -> None:
        """Fallback command runner used when no terminal backend is
        injected (e.g. dialog used in isolation for tests).

        Only ``swap`` is meaningful here — every other command needs
        the surrounding main_window state. We log a silent no-op for
        everything else so the dialog still functions for tile drag
        in tests.
        """
        if not raw or not self.engine or not self.engine.path:
            return
        parts = raw.split()
        if len(parts) >= 3 and parts[0] == "swap":
            try:
                a, b = int(parts[1]), int(parts[2])
            except ValueError:
                return
            # Delegate to ``_finalize_swap`` so the on-disk write,
            # engine reload, repopulate, and signal emission all live
            # in one place. Keeping the fallback in lock-step with the
            # animated path means tests exercise the same code the
            # real terminal ``swap`` command does.
            self._finalize_swap(a, b)

    def closeEvent(self, event):
        """Cancel any in-flight thumbnail worker so we don't outlive the
        dialog (was producing 'QThread destroyed while running' warnings
        on close)."""
        self._cancel_thumbnail_worker()
        super().closeEvent(event)

    # ---------------------------------------------------------- UI setup
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Toolbar row: buttons for the most common actions.
        bar = QHBoxLayout()
        bar.setSpacing(6)

        title = QLabel("Pages")
        title.setObjectName("pagesManagerTitle")
        title.setStyleSheet("font-weight: bold; font-size: 14px; padding: 4px;")
        bar.addWidget(title)

        bar.addStretch(1)

        self._btn_new_pdf = QPushButton("New PDF from selected")
        self._btn_new_pdf.setToolTip("Save the selected pages into a new PDF file.")
        self._btn_new_pdf.clicked.connect(self._action_new_pdf)
        bar.addWidget(self._btn_new_pdf)

        self._btn_delete = QPushButton("Delete selected")
        self._btn_delete.clicked.connect(self._action_delete_selected)
        bar.addWidget(self._btn_delete)

        self._btn_rotate = QPushButton("Rotate 90°")
        self._btn_rotate.clicked.connect(lambda: self._action_rotate_selected(90))
        bar.addWidget(self._btn_rotate)

        self._btn_reorder = QPushButton("Reorder Page…")
        self._btn_reorder.setToolTip(
            "Move a selected page to a relative position before/after "
            "another page (same as the terminal 'reorder' command).")
        self._btn_reorder.clicked.connect(self._action_reorder_selected)
        bar.addWidget(self._btn_reorder)

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setToolTip("Re-render the grid (e.g. after editing).")
        self._btn_refresh.clicked.connect(self._populate)
        bar.addWidget(self._btn_refresh)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.close)
        bar.addWidget(self._btn_close)

        root.addLayout(bar)

        # Grid of thumbnails. Drag-drop routes through the same
        # terminal backend as the typed ``swap`` command — i.e. the
        # ``PageGridWidget`` reads ``currentRow()`` and
        # ``self.row(self.itemAt(...))`` at drop time, then dispatches
        # ``swap A B`` to ``self._command_runner``. No duplicate swap
        # logic lives in the widget.
        self.list = PageGridWidget(parent=self)
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(TILE_W - 24, TILE_H - 50))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSpacing(8)
        # Qt's IconMode + Static movement silently downgrades
        # ``dragDropMode`` back to ``DropOnly``; the widget re-applies
        # its config so we don't lose drag-drop in the grid.
        self.list._apply_drag_drop_config()
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.list.itemActivated.connect(self._on_item_activated)
        self.list.itemClicked.connect(self._on_item_clicked)
        # Recompute the column count on resize.
        self.list.resizeEvent = self._wrap_resize_event(self.list.resizeEvent)
        root.addWidget(self.list, 1)

        # Hint label
        hint = QLabel(
            "Tip: drag a page onto another to move it to that position. "
            "Drag PDF files from your file manager onto the grid to merge "
            "them into the open document. Ctrl/Shift+click to multi-select. "
            "Right-click for more actions."
        )
        hint.setStyleSheet("color: #6c7086; padding: 4px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

    def _wrap_resize_event(self, orig):
        """Wrap QListWidget.resizeEvent so we can re-tile on every resize."""
        def inner(event):
            orig(event)
            self._recompute_grid()
        return inner

    def _recompute_grid(self):
        """Adjust grid size so columns expand/contract with dialog width."""
        viewport_w = self.list.viewport().width()
        if viewport_w <= 0:
            return
        cols = max(GRID_COLUMNS_MIN,
                   min(GRID_COLUMNS_MAX, viewport_w // (TILE_W + 8)))
        self.list.setGridSize(QSize(viewport_w // cols, TILE_H))

    # ------------------------------------------------------ population
    def _populate(self):
        """Build the grid skeleton immediately; render thumbnails in the
        background so the dialog opens instantly even for big PDFs.

        Replaces the previous synchronous loop that blocked the UI
        until every page was rasterized. Now: 1 placeholder tile per
        page is added up front (grey, with the page number rendered),
        then a worker thread emits per-page PNGs which we apply to the
        matching tile as they arrive.
        """
        # Tear down any in-flight thumbnail worker from a previous populate
        # so the new render takes over.
        self._cancel_thumbnail_worker()

        self.list.clear()
        if not self.engine or not self.engine.is_open:
            placeholder = QListWidgetItem("No PDF open")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(placeholder)
            return

        n = self.engine.page_count
        # ---- Phase 1: synchronous skeleton (instant) -----------------
        placeholder_pm = _placeholder_tile(TILE_W - 24, TILE_H - 50,
                                           text=f"…", index=0)
        for i in range(n):
            item = QListWidgetItem(f"Page {i + 1}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter
                                  | Qt.AlignmentFlag.AlignBottom)
            item.setFlags(item.flags()
                          | Qt.ItemFlag.ItemIsSelectable
                          | Qt.ItemFlag.ItemIsDragEnabled
                          | Qt.ItemFlag.ItemIsDropEnabled
                          | Qt.ItemFlag.ItemIsEnabled)
            item.setSizeHint(QSize(TILE_W, TILE_H))
            # Give every tile a placeholder so the grid is immediately
            # visible — the real PNG swaps in when the worker emits.
            item.setIcon(QIcon(placeholder_pm))
            self.list.addItem(item)
        self._recompute_grid()
        # Auto-scroll to the current page so the user doesn't have to.
        try:
            cur = max(0, min(self.engine.current_page, n - 1))
            if self.list.item(cur):
                self.list.setCurrentItem(self.list.item(cur))
                self.list.scrollToItem(
                    self.list.item(cur),
                    QAbstractItemView.ScrollHint.PositionAtCenter)
        except Exception:
            pass

        # ---- Phase 2: background render ----------------------------
        # For very small documents (≤ 4 pages) the synchronous path is
        # actually faster — spinning up a thread costs more than the
        # handful of thumbnails we'll render.
        if n <= 4:
            self._render_thumbnails_sync(n)
            return
        if not self.engine.path:
            self._render_thumbnails_sync(n)
            return

        self._start_thumbnail_worker(n)

    def _render_thumbnails_sync(self, n: int):
        """Render every thumbnail on the calling thread (small docs)."""
        target_w = TILE_W - 24
        target_h = TILE_H - 50
        zoom = 0.40
        try:
            for i in range(n):
                page = self.engine.get_page(i)
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(zoom, zoom), alpha=False)
                self._apply_thumbnail(i, pix.tobytes("png"))
        except Exception:
            pass

    def _start_thumbnail_worker(self, n: int):
        """Spin up a QThread that rasterizes pages and emits PNG bytes."""
        if not self.engine.path:
            return
        # Parent the QThread to the dialog so its lifetime tracks the
        # dialog — closing the dialog triggers the destructor which
        # properly joins the thread. (Previously the thread had no
        # parent and was being GC'd while still running.)
        thread = QThread(self)
        worker = _ThumbnailWorker(
            self.engine.path, n,
            zoom=0.40,
            target_w=TILE_W - 24,
            target_h=TILE_H - 50,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.thumbnail_ready.connect(self._apply_thumbnail)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thumb_thread = thread
        self._thumb_worker = worker
        thread.start()

    def _cancel_thumbnail_worker(self):
        """Cancel any in-flight thumbnail worker.

        Order matters: signal the worker to bail out FIRST so the
        thread's run() loop exits naturally, THEN wait briefly, then
        drop our references. Setting the refs to None before the
        thread actually quits was producing 'QThread destroyed while
        thread is still running' warnings on dialog close.
        """
        worker = self._thumb_worker
        thread = self._thumb_thread
        # Clear refs first so a re-entrant _populate() doesn't see the
        # dying worker.
        self._thumb_worker = None
        self._thumb_thread = None
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
        if thread is not None:
            try:
                if thread.isRunning():
                    # The worker checks _cancelled between pages and
                    # bails out promptly, so 2 s is plenty.
                    thread.quit()
                    if not thread.wait(2000):
                        # Last resort: terminate. Shouldn't happen.
                        try:
                            thread.terminate()
                            thread.wait(500)
                        except Exception:
                            pass
            except Exception:
                pass

    def _apply_thumbnail(self, page_index: int, png_bytes: bytes):
        """Apply a freshly-rendered thumbnail to its list item."""
        try:
            item = self.list.item(page_index)
        except Exception:
            return
        if item is None:
            return
        try:
            qimg = QImage.fromData(png_bytes)
            if qimg.isNull():
                return
            qpm = QPixmap.fromImage(qimg).scaled(
                TILE_W - 24, TILE_H - 50,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            item.setIcon(QIcon(qpm))
        except Exception:
            pass

    # ------------------------------------------------------ public API
    def get_selected_pages(self) -> List[int]:
        """Return 1-based page numbers for the currently-selected tiles."""
        return self.list.selected_pages_1based()

    def select_pages(self, pages_1based: Sequence[int]) -> None:
        """Programmatically select a list of 1-based pages (used by terminal)."""
        self.list.clearSelection()
        for p in pages_1based:
            row = p - 1
            item = self.list.item(row)
            if item is not None:
                item.setSelected(True)

    def generate_new_pdf(self, pages_1based: Sequence[int],
                         out_path: str) -> tuple[bool, str]:
        """Public entry point: save ``pages_1based`` (1-based, may repeat)
        to a new PDF at ``out_path``. Returns (ok, message)."""
        return self._generate_new_pdf(list(pages_1based), out_path)

    # ------------------------------------------------------ user actions
    def _on_item_clicked(self, item: QListWidgetItem):
        if not (QApplication.keyboardModifiers()
                & (Qt.KeyboardModifier.ControlModifier
                   | Qt.KeyboardModifier.ShiftModifier)):
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                self.navigate_to_page.emit(int(idx) + 1)

    def _on_item_activated(self, item: QListWidgetItem):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is not None:
            self.navigate_to_page.emit(int(idx) + 1)

    def _on_pages_dropped(self, target_page_1based: int, src_pages: List[int]):
        """A drag-drop landed on `target_page_1based` with src_pages."""
        pages = ([target_page_1based]
                 + [p for p in src_pages if p != target_page_1based])
        suggested = "merged.pdf"
        if self.engine.path:
            stem = os.path.splitext(os.path.basename(self.engine.path))[0]
            suggested = f"{stem}-merged.pdf"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save merged PDF", suggested, "PDF files (*.pdf)")
        if not out_path:
            return
        if not out_path.lower().endswith(".pdf"):
            out_path += ".pdf"
        ok, msg = self._generate_new_pdf(pages, out_path)
        if ok:
            QMessageBox.information(
                self, "Merged",
                f"Created new PDF with pages {pages}:\n{out_path}")
            self.new_pdf_generated.emit(out_path)
        else:
            QMessageBox.warning(self, "Merge failed", msg)

    def _animate_swap_then_apply(self, src_page_1based: int,
                                 target_page_1based: int):
        """Run a brief cross-fade overlay, then commit the swap on disk
        and repopulate the grid.

        Two translucent "ghost" labels carrying each tile's pixmap
        float over the grid and animate to the *other* tile's slot,
        so the user sees the swap happening in real time. After the
        animation finishes we tear down the ghosts, run the on-disk
        swap, reload the engine, repopulate, and emit the usual
        pages_swapped / pages_reordered signals.
        """
        src_idx = src_page_1based - 1
        tgt_idx = target_page_1based - 1
        src_item = self.list.item(src_idx)
        tgt_item = self.list.item(tgt_idx)
        if src_item is None or tgt_item is None:
            self._finalize_swap(src_page_1based, target_page_1based)
            return
        # visualItemRect returns coordinates in the *viewport* frame;
        # child widgets of self.list sit in the list widget's frame, so
        # translate by the viewport's position inside the list.
        vp_origin = self.list.viewport().mapTo(self.list, QPoint(0, 0))
        src_rect = QRect(self.list.visualItemRect(src_item).topLeft() + vp_origin,
                         self.list.visualItemRect(src_item).size())
        tgt_rect = QRect(self.list.visualItemRect(tgt_item).topLeft() + vp_origin,
                         self.list.visualItemRect(tgt_item).size())

        ghost_src = self._make_swap_ghost(src_item, src_rect)
        ghost_tgt = self._make_swap_ghost(tgt_item, tgt_rect)
        if ghost_src is None or ghost_tgt is None:
            self._finalize_swap(src_page_1based, target_page_1based)
            return

        self._start_swap_animation(
            src_rect, tgt_rect, ghost_src, ghost_tgt,
            lambda: self._finalize_swap(src_page_1based, target_page_1based))

    def _animate_repopulate_only(self, src_page_1based: int,
                                 target_page_1based: int):
        """Same visual overlay as ``_animate_swap_then_apply`` but the
        on-disk swap is presumed already-committed by the caller.

        Used by the terminal ``swap`` command path in main_window,
        which writes the swap to disk before invoking us so we must
        NOT write again. We still animate + repopulate + emit signals
        so the grid reflects the new state.
        """
        src_idx = src_page_1based - 1
        tgt_idx = target_page_1based - 1
        src_item = self.list.item(src_idx)
        tgt_item = self.list.item(tgt_idx)
        if src_item is None or tgt_item is None:
            self._repopulate_after_swap(
                src_page_1based, target_page_1based)
            return
        vp_origin = self.list.viewport().mapTo(self.list, QPoint(0, 0))
        src_rect = QRect(self.list.visualItemRect(src_item).topLeft() + vp_origin,
                         self.list.visualItemRect(src_item).size())
        tgt_rect = QRect(self.list.visualItemRect(tgt_item).topLeft() + vp_origin,
                         self.list.visualItemRect(tgt_item).size())

        ghost_src = self._make_swap_ghost(src_item, src_rect)
        ghost_tgt = self._make_swap_ghost(tgt_item, tgt_rect)
        if ghost_src is None or ghost_tgt is None:
            self._repopulate_after_swap(
                src_page_1based, target_page_1based)
            return

        self._start_swap_animation(
            src_rect, tgt_rect, ghost_src, ghost_tgt,
            lambda: self._repopulate_after_swap(
                src_page_1based, target_page_1based))

    def _start_swap_animation(self, src_rect, tgt_rect,
                              ghost_src, ghost_tgt, on_finish):
        """Build + start the swap cross-fade group and bind teardown +
        finish-callback. Shared by ``_animate_swap_then_apply`` and
        ``_animate_repopulate_only`` so the visual timing lives in one
        place.
        """
        # Each ghost moves to the OTHER tile's slot while fading out,
        # so the underlying list item at the destination becomes
        # visible underneath. Group the animations so the cleanup
        # callback fires exactly once regardless of which anim finishes
        # first (or if any is interrupted).
        def _geom(target, start, end):
            a = QPropertyAnimation(target, b"geometry", self)
            a.setDuration(_SWAP_DURATION_MS)
            a.setStartValue(start)
            a.setEndValue(end)
            a.setEasingCurve(QEasingCurve.Type.InOutCubic)
            return a

        def _fade(target):
            a = QPropertyAnimation(target, b"windowOpacity", self)
            a.setDuration(_SWAP_DURATION_MS)
            a.setStartValue(1.0)
            a.setEndValue(0.0)
            return a

        group = QParallelAnimationGroup(self)
        group.addAnimation(_geom(ghost_src, src_rect,
                                 QRect(tgt_rect.topLeft(), src_rect.size())))
        group.addAnimation(_geom(ghost_tgt, tgt_rect,
                                 QRect(src_rect.topLeft(), tgt_rect.size())))
        group.addAnimation(_fade(ghost_src))
        group.addAnimation(_fade(ghost_tgt))
        ghosts = (ghost_src, ghost_tgt)
        group.finished.connect(
            lambda: self._teardown_swap_ghosts(ghosts))
        group.finished.connect(on_finish)
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _make_swap_ghost(self, item: QListWidgetItem, at_rect: QRect) -> Optional[QLabel]:
        """Build a translucent floating label carrying ``item``'s pixmap."""
        pix = item.icon().pixmap(TILE_W - 24, TILE_H - 50)
        lbl = QLabel(self.list)
        lbl.setPixmap(pix)
        lbl.setFixedSize(pix.size() if not pix.isNull()
                         else QSize(TILE_W - 24, TILE_H - 50))
        lbl.setStyleSheet(
            f"background: {_SWAP_FILL};"
            f"border: 2px solid {_SWAP_BORDER}; border-radius: 8px;")
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lbl.move(at_rect.topLeft())
        lbl.show()
        lbl.raise_()
        return lbl

    def _teardown_swap_ghosts(self, ghosts) -> None:
        """Dispose of the swap-animation overlay labels."""
        for g in ghosts:
            try:
                g.deleteLater()
            except Exception:
                pass

    def _finalize_swap(self, src_page_1based: int, target_page_1based: int):
        """Commit the swap on disk and repopulate the grid.

        Called after the swap animation finishes (drag-and-drop path)
        or directly when the swap is invoked from the terminal and no
        animation is desired / possible.
        """
        if not self._apply_swap_on_disk(
                src_page_1based, target_page_1based):
            return
        self._repopulate_after_swap(src_page_1based, target_page_1based)

    def _apply_swap_on_disk(self, src_page_1based: int,
                            target_page_1based: int) -> bool:
        """Write the page swap to disk and reload the engine.

        Returns True if the swap committed; False on any error (the
        caller is responsible for warning the user). Does NOT
        repopulate the grid or emit signals — that's the job of
        ``_repopulate_after_swap``. Splitting the two lets the
        terminal ``swap`` command (main_window._cmd_swap) skip the
        on-disk write entirely: it has already written, it just needs
        the visual / signal side effect.
        """
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return False
        ok, msg = PDFManipulator.swap_pages(
            self.engine.path, src_page_1based, target_page_1based)
        if not ok:
            QMessageBox.warning(self, "Swap failed", msg)
            return False
        try:
            self.engine.reload_from_disk()
        except Exception as exc:
            QMessageBox.warning(
                self, "Reload failed",
                f"Swap succeeded but reload failed: {exc}")
            return False
        return True

    def _repopulate_after_swap(self, src_page_1based: int,
                               target_page_1based: int) -> None:
        """Rebuild the grid (so labels reflect the new sequence) and emit
        the swap signals.

        Called once per swap, regardless of whether the on-disk write
        happened in the PM (``_finalize_swap``) or was performed by
        the caller (terminal ``swap`` command via ``_cmd_swap``).
        """
        # Repopulate the grid; this re-creates every item with a
        # fresh "Page N" label that matches the new sequence position
        # (because labels are derived from the item's row index in
        # ``_populate``).
        self._populate()
        # Post-swap, the page that WAS at src is now at target, and
        # vice-versa. Emit both so the main window can decide what to
        # focus on (defaults to src's new slot = target_page_1based).
        self.pages_swapped.emit(src_page_1based, target_page_1based)
        # Keep the old signal firing too for compatibility with
        # downstream consumers that listen for any reorder; the "new
        # index" is target_page_1based for the source page.
        self.pages_reordered.emit(target_page_1based)

    # ----------------------------------------------- terminal-driven swap
    def animate_terminal_swap(self, src_page_1based: int,
                              target_page_1based: int,
                              apply_on_disk: bool = True):
        """Public entry-point used by the terminal ``swap`` command.

        Triggers a live animation in the Page Manager UI (when this
        dialog is open) so the user sees *which* pages are being
        swapped in real time, then commits the swap on disk and
        repopulates the grid. If the dialog is hidden / closed the
        caller falls back to ``_finalize_swap`` directly so the
        on-disk swap still goes through.

        ``apply_on_disk`` is False when the caller (typically
        ``main_window._cmd_swap``) has *already* written the swap to
        disk — in that case the PM only needs to repopulate + emit
        signals; double-writing would corrupt the file.
        """
        if src_page_1based == target_page_1based:
            # No-op swap — still let the caller know we're done.
            self.pages_swapped.emit(src_page_1based, target_page_1based)
            return
        if not self.isVisible():
            if apply_on_disk:
                self._finalize_swap(src_page_1based, target_page_1based)
            else:
                self._repopulate_after_swap(
                    src_page_1based, target_page_1based)
            return
        self._highlight_tiles([src_page_1based, target_page_1based])
        if apply_on_disk:
            self._animate_swap_then_apply(
                src_page_1based, target_page_1based)
        else:
            self._animate_repopulate_only(
                src_page_1based, target_page_1based)

    # ----------------------------------------------- terminal-driven move
    def animate_terminal_move(self, src_page_1based: int,
                              position: str,
                              tgt_page_1based: int):
        """Public entry-point used by the terminal ``reorder`` command.

        ``position`` is either ``"before"`` or ``"after"``. The dialog
        commits the move on disk (via ``PDFManipulator.move_page``),
        reloads the engine, repopulates the grid, and emits
        ``pages_reordered`` so the main viewer can follow the page to
        its new slot. The Pages Manager dialog is itself already on
        screen so the user sees the tile fly into its new slot as the
        grid is rebuilt.
        """
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return
        if src_page_1based == tgt_page_1based:
            # No-op — still let the caller know we're done.
            self.pages_reordered.emit(src_page_1based)
            return
        if not self.isVisible():
            # Caller (main window) falls back to the silent path.
            self._finalize_move(src_page_1based, position, tgt_page_1based)
            return
        # Highlight the two involved tiles so the user can see which
        # page is moving and where it is going.
        self._highlight_tiles([src_page_1based, tgt_page_1based])
        self._animate_move_then_apply(src_page_1based, position, tgt_page_1based)

    def _animate_move_then_apply(self, src_page_1based: int,
                                 position: str,
                                 tgt_page_1based: int):
        """Slide the source tile to its destination slot, then commit
        the move on disk and repopulate.

        Visually: we build a translucent floating ghost carrying the
        source tile's pixmap and animate it from the source's slot to
        either the target's slot (``before``) or one slot past it
        (``after``). When the animation finishes we tear down the
        ghost, run ``PDFManipulator.move_page``, reload the engine,
        repopulate the grid, and emit ``pages_reordered`` carrying the
        source page's NEW slot.
        """
        src_idx = src_page_1based - 1
        tgt_idx = tgt_page_1based - 1
        src_item = self.list.item(src_idx)
        tgt_item = self.list.item(tgt_idx)
        if src_item is None or tgt_item is None:
            self._finalize_move(src_page_1based, position, tgt_page_1based)
            return
        vp_origin = self.list.viewport().mapTo(self.list, QPoint(0, 0))
        src_rect = QRect(self.list.visualItemRect(src_item).topLeft() + vp_origin,
                         self.list.visualItemRect(src_item).size())
        tgt_rect = QRect(self.list.visualItemRect(tgt_item).topLeft() + vp_origin,
                         self.list.visualItemRect(tgt_item).size())
        ghost = self._make_swap_ghost(src_item, src_rect)
        if ghost is None:
            self._finalize_move(src_page_1based, position, tgt_page_1based)
            return

        # For "before" the ghost ends at the target's slot. For "after"
        # we offset the end position by one tile-width to land just
        # past the target. We can't easily read the next item's rect
        # for "after" (it may have been removed from the live tree if
        # the source was above the target), so we approximate by
        # offsetting tgt_rect by a tile's column stride. The grid is
        # left-to-right top-to-bottom; the next slot after tgt is
        # either the next column to the right or the start of the next
        # row — both land in roughly the same horizontal pixel as
        # tgt_rect so a +TILE_W offset reads as "just past".
        if position == "after":
            end_rect = QRect(tgt_rect.topLeft() + QPoint(TILE_W, 0),
                             src_rect.size())
        else:
            end_rect = QRect(tgt_rect.topLeft(), src_rect.size())

        # Slide the ghost into place while fading it out so the
        # underlying list item at the destination becomes visible
        # underneath.
        geom = QPropertyAnimation(ghost, b"geometry", self)
        geom.setDuration(_SWAP_DURATION_MS)
        geom.setStartValue(src_rect)
        geom.setEndValue(end_rect)
        geom.setEasingCurve(QEasingCurve.Type.InOutCubic)

        fade = QPropertyAnimation(ghost, b"windowOpacity", self)
        fade.setDuration(_SWAP_DURATION_MS)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)

        group = QParallelAnimationGroup(self)
        group.addAnimation(geom)
        group.addAnimation(fade)
        # Capture the ghost in the closure so the teardown callback
        # can dispose of it without keeping state on self.
        ghosts = (ghost,)
        group.finished.connect(
            lambda: self._teardown_swap_ghosts(ghosts))
        group.finished.connect(
            lambda: self._finalize_move(src_page_1based, position,
                                        tgt_page_1based))
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finalize_move(self, src_page_1based: int,
                       position: str,
                       tgt_page_1based: int):
        """Commit the move on disk and repopulate the grid.

        Called after the move animation finishes (terminal ``reorder``
        path) or directly when no animation is desired / possible.
        Emits ``pages_reordered`` carrying the source page's new
        1-based slot so the main viewer can follow it.
        """
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return
        n = self.engine.page_count
        # Compute the post-removal slot (move_page() expects 1-based,
        # post-removal indexing). See _cmd_reorder for the rationale.
        target_slot = tgt_page_1based
        if src_page_1based < tgt_page_1based:
            target_slot = tgt_page_1based - 1
        if position == "after":
            target_slot += 1
        target_slot = max(1, min(target_slot, n))
        ok, msg = PDFManipulator.move_page(
            self.engine.path, src_page_1based, target_slot)
        if not ok:
            QMessageBox.warning(self, "Reorder failed", msg)
            return
        try:
            self.engine.reload_from_disk()
        except Exception as exc:
            QMessageBox.warning(
                self, "Reload failed",
                f"Reorder succeeded but reload failed: {exc}")
            return
        # Repopulate so the grid labels and tile order match the new
        # sequence (labels are derived from row index in _populate()).
        self._populate()
        # Emit pages_reordered with the source's NEW 1-based slot.
        self.pages_reordered.emit(target_slot)

    def _highlight_tiles(self, pages_1based: List[int]):
        """Flash a coloured background on the given tiles so the user can
        see which pages are involved in a swap.

        The flash is restored after a short delay. If the swap's
        animation triggers a repopulate() before the timer fires the
        setBackground(None) calls become harmless no-ops on the new
        (different) item objects.
        """
        for p in pages_1based:
            it = self.list.item(p - 1)
            if it is None:
                continue
            it.setBackground(QBrush(_SWAP_HIGHLIGHT))

        def _restore():
            for p in pages_1based:
                it = self.list.item(p - 1)
                if it is None:
                    continue
                it.setData(Qt.ItemDataRole.BackgroundRole, None)

        QTimer.singleShot(700, _restore)

    def _on_external_pdfs_dropped(self, paths: List[str]):
        """User dropped external PDF files (from OS file manager) onto the grid.

        The drop arrives as ``paths`` — a list of absolute PDF paths. We
        pop a save dialog, then use ``PDFManipulator.merge_pdfs`` to
        concatenate the current document with each dropped file into one
        combined PDF.
        """
        if not self.engine or not self.engine.is_open or not self.engine.path:
            QMessageBox.warning(
                self, "No document",
                "Open a PDF first to merge external documents into it.")
            return
        # Filter out the current document itself (would still work but
        # produces a duplicate-paged PDF which is rarely what the user
        # wants).
        current = os.path.abspath(self.engine.path)
        external = [p for p in paths
                    if os.path.abspath(p) != current
                    and p.lower().endswith(".pdf")
                    and os.path.isfile(p)]
        if not external:
            QMessageBox.information(
                self, "Nothing to merge",
                "No external PDF files were dropped.")
            return
        suggested = "merged.pdf"
        if self.engine.path:
            stem = os.path.splitext(os.path.basename(self.engine.path))[0]
            suggested = f"{stem}-merged.pdf"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save merged PDF", suggested, "PDF files (*.pdf)")
        if not out_path:
            return
        if not out_path.lower().endswith(".pdf"):
            out_path += ".pdf"
        # Build the merge list: current doc first, then each external file
        # in the order the user dropped them.
        inputs = [self.engine.path] + external
        ok, msg = PDFManipulator.merge_pdfs(inputs, out_path)
        if not ok:
            QMessageBox.warning(self, "Merge failed", msg)
            return
        ans = QMessageBox.question(
            self, "Merged",
            f"Created merged PDF with {len(external)} external document(s):\n"
            f"{out_path}\n\nOpen it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ans == QMessageBox.StandardButton.Yes:
            self.new_pdf_generated.emit(out_path)

    def _on_context_menu(self, pos: QPoint):
        """Right-click menu — actions apply to current selection.

        For the per-page actions ("Move To…") the *target* of the
        operation is the page that was right-clicked on, even if more
        tiles were selected previously. This matches the way every
        desktop file manager handles "move to…" — the operation pivots
        on the right-clicked item.
        """
        item = self.list.itemAt(pos)
        right_clicked_page = None
        if item is not None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is not None:
                right_clicked_page = int(data) + 1
            if not item.isSelected():
                # If nothing was selected, or the right-clicked tile
                # wasn't part of the selection, treat just this tile
                # as the selection so per-page actions are predictable.
                self.list.clearSelection()
                item.setSelected(True)

        menu = QMenu(self)
        has_sel = bool(self.get_selected_pages())
        a_new = menu.addAction("New PDF from selected pages…")
        a_new.triggered.connect(self._action_new_pdf)
        a_new.setEnabled(has_sel)
        menu.addSeparator()
        a_move = menu.addAction(
            f"Move Page {right_clicked_page} To…"
            if right_clicked_page else "Move To…")
        a_move.triggered.connect(
            lambda: self._action_move_to(right_clicked_page))
        a_move.setEnabled(right_clicked_page is not None)
        menu.addSeparator()
        a_del = menu.addAction("Delete selected page(s)")
        a_del.triggered.connect(self._action_delete_selected)
        a_del.setEnabled(has_sel)
        a_rot = menu.addAction("Rotate selected 90°")
        a_rot.triggered.connect(lambda: self._action_rotate_selected(90))
        a_rot.setEnabled(has_sel)
        menu.exec(self.list.mapToGlobal(pos))

    def _action_new_pdf(self):
        pages = self.get_selected_pages()
        if not pages:
            QMessageBox.information(self, "New PDF",
                                    "Select one or more pages first.")
            return
        suggested = "pages.pdf"
        if self.engine.path:
            stem = os.path.splitext(os.path.basename(self.engine.path))[0]
            suggested = f"{stem}-pages.pdf"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save new PDF", suggested, "PDF files (*.pdf)")
        if not out_path:
            return
        if not out_path.lower().endswith(".pdf"):
            out_path += ".pdf"
        ok, msg = self._generate_new_pdf(pages, out_path)
        if ok:
            QMessageBox.information(self, "New PDF created",
                                    f"Saved {len(pages)} page(s) to:\n{out_path}")
            self.new_pdf_generated.emit(out_path)
        else:
            QMessageBox.warning(self, "New PDF failed", msg)

    def _action_delete_selected(self):
        pages = self.get_selected_pages()
        if not pages:
            return
        confirm = QMessageBox.question(
            self, "Delete pages",
            f"Delete {len(pages)} page(s) from the current PDF?\n\n"
            f"Pages: {', '.join(str(p) for p in pages)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        # Delete from highest to lowest so indices stay valid.
        for p in sorted(pages, reverse=True):
            ok, msg = PDFManipulator.delete_page(self.engine.path, p)
            if not ok:
                QMessageBox.warning(self, "Delete failed",
                                    f"Failed on page {p}: {msg}")
                return
        self.pages_deleted.emit(pages)
        self._populate()

    def _action_rotate_selected(self, angle: int):
        pages = self.get_selected_pages()
        if not pages:
            return
        for p in pages:
            ok, _msg = PDFManipulator.rotate_page(self.engine.path, p, angle)
            if not ok:
                QMessageBox.warning(self, "Rotate failed",
                                    f"Failed to rotate page {p}.")
                return
        self._populate()

    def _action_reorder_selected(self):
        """Toolbar 'Reorder Page…' button — opens the MoveToDialog for
        the first selected page (or the current page if none selected).
        """
        if not self.engine or not self.engine.is_open:
            return
        pages = self.get_selected_pages()
        if pages:
            src_page = pages[0]
        else:
            # Fall back to the engine's current page if the user hasn't
            # selected anything; this matches the behavior of the
            # other toolbar buttons that operate on the current page.
            src_page = (self.engine.current_page + 1
                        if self.engine.current_page is not None else 1)
        src_page = max(1, min(src_page, self.engine.page_count))
        self._action_move_to(src_page)

    def _action_move_to(self, src_page_1based):
        """Shared entry point for the right-click 'Move To…' action and
        the toolbar 'Reorder Page…' button. Pops the MoveToDialog and
        routes the user-supplied (target, position) values through the
        same animated path the terminal ``reorder`` command uses.
        """
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return
        if src_page_1based is None:
            return
        dlg = MoveToDialog(self, src_page_1based, self.engine.page_count)
        # ``move_requested`` carries (src, target, position). Reuse the
        # terminal-driven animation entry-point so the user sees the
        # same live transition whether they triggered the move from
        # the widget or the CLI.
        dlg.move_requested.connect(self._on_move_to_dialog_apply)
        dlg.exec()

    def _on_move_to_dialog_apply(self, src_page_1based: int,
                                 target_page_1based: int,
                                 position: str):
        """MoveToDialog.Apply -> animated move + repopulate."""
        self.animate_terminal_move(src_page_1based, position,
                                   target_page_1based)

    def _generate_new_pdf(self, pages_1based: List[int],
                          out_path: str) -> tuple[bool, str]:
        """Internal: actually build the new PDF."""
        if not pages_1based:
            return False, "No pages specified."
        try:
            doc = fitz.open()
            src = fitz.open(self.engine.path)
            for p in pages_1based:
                if p < 1 or p > len(src):
                    src.close()
                    doc.close()
                    return False, f"Page {p} out of range (1..{len(src)})."
                doc.insert_pdf(src, from_page=p - 1, to_page=p - 1)
            doc.save(out_path, garbage=4, deflate=True)
            doc.close()
            src.close()
            return True, f"Saved {len(pages_1based)} page(s) → {out_path}"
        except Exception as exc:
            return False, f"Could not write PDF: {exc}"
