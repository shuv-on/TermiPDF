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
    Qt, pyqtSignal, QSize, QPoint, QMimeData, QByteArray, QDataStream,
    QIODevice, QThread, QObject,
)
from PyQt6.QtGui import QPixmap, QImage, QDrag, QAction, QIcon, QColor, QBrush
from PyQt6.QtWidgets import (
    QDialog, QListWidget, QListWidgetItem, QAbstractItemView,
    QMenu, QFileDialog, QMessageBox, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QApplication,
)

from features.pdf_viewer.viewer_engine import ViewerEngine
from features.pdf_editor.manipulation import PDFManipulator


# Tile size used by the grid. The grid is responsive — the column count
# is computed from the dialog width.
TILE_W = 160
TILE_H = 220
GRID_COLUMNS_MIN = 2
GRID_COLUMNS_MAX = 8


# Drag-and-drop payload — we encode the source page indices as a byte
# array so the receiver can re-create the page list.
_MIME_TYPE = "application/x-termipdf-pages"


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


class _PageGrid(QListWidget):
    """QListWidget with custom drag-drop so we can drop a tile onto
    another tile to MOVE that page to the drop target's position in
    the document, OR drop external PDF files from the OS file
    manager to merge them into the current document. Standard
    QListWidget's internal drag moves items within the same widget,
    which isn't what we want for PDF pages."""

    # (target_1based, src_1based_list) — emitted for the "merge into a
    # new PDF" flow, kept around in case future code wants it. Currently
    # only the single-page reorder (page_moved) is wired up.
    pages_dropped_on_target = pyqtSignal(int, list)
    # Emitted when external .pdf files (from OS file manager) are
    # dropped onto the grid. Carries a list of absolute file paths.
    external_pdfs_dropped = pyqtSignal(list)
    # Emitted for the simple single-page reorder: drop src_page_1based
    # onto target_page_1based → src_page moves to target's position.
    page_moved = pyqtSignal(int, int)  # (src_1based, target_1based)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def selected_pages_1based(self) -> List[int]:
        pages: List[int] = []
        for item in self.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                pages.append(int(idx) + 1)
        return sorted(set(pages))

    def startDrag(self, supportedActions):
        indices = self.selected_pages_1based()
        if not indices:
            return
        data = QByteArray()
        stream = QDataStream(data, QIODevice.OpenModeFlag.WriteOnly)
        stream.writeUInt32(len(indices))
        for p in indices:
            stream.writeUInt32(p)
        mime = QMimeData()
        mime.setData(_MIME_TYPE, data)
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Visual drag feedback: show the source tile's pixmap (or the
        # first selected tile's pixmap) at 0.6× opacity so the user
        # sees what they're dragging. Without this the default
        # platform drag cursor looks empty on a static grid.
        src_items = [self.item(p - 1) for p in indices if self.item(p - 1)]
        if src_items and not src_items[0].icon().isNull():
            pm = src_items[0].icon().pixmap(TILE_W - 24, TILE_H - 50)
            if not pm.isNull():
                drag.setPixmap(pm)
                drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        # Accept either our internal pages-payload OR external file URLs.
        if md.hasFormat(_MIME_TYPE) or md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if md.hasFormat(_MIME_TYPE) or md.hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def _external_pdf_paths(self, mime) -> List[str]:
        """Filter the drop's URLs down to local .pdf files that exist."""
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

    def _target_page_from_pos(self, pos) -> Optional[int]:
        """Resolve the 1-based target page for a drop at ``pos``.

        If the cursor is over a tile, use that tile's page. If the
        cursor is on the empty grid area (not on a tile), pick the
        nearest tile by center distance — so the user can drop into
        empty space between tiles and the page still moves to a
        sensible position.
        """
        item = self.itemAt(pos)
        if item is not None:
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is not None:
                return int(data) + 1
        # Empty space: snap to the nearest tile by center distance.
        items = [self.item(i) for i in range(self.count())]
        items = [it for it in items if it is not None]
        if not items:
            return None
        best = None
        best_d = float("inf")
        for it in items:
            r = self.visualItemRect(it)
            cx = r.center().x()
            cy = r.center().y()
            d = (cx - pos.x()) ** 2 + (cy - pos.y()) ** 2
            if d < best_d:
                best_d = d
                best = it
        if best is None:
            return None
        data = best.data(Qt.ItemDataRole.UserRole)
        return int(data) + 1 if data is not None else None

    def dropEvent(self, event):
        md = event.mimeData()
        # External file drop (from OS file manager) → defer to the
        # dialog, which knows about the engine + dialog state. We only
        # act when the drop contains local PDF files.
        if md.hasUrls() and not md.hasFormat(_MIME_TYPE):
            paths = self._external_pdf_paths(md)
            if paths:
                event.acceptProposedAction()
                self.external_pdfs_dropped.emit(paths)
                return
            event.ignore()
            return
        # Internal tile-drop → page reorder. Dragging page A onto page B
        # (or into the empty space near B) means "move page A to where
        # page B sits now."
        if not md.hasFormat(_MIME_TYPE):
            super().dropEvent(event)
            return
        target_page_1based = self._target_page_from_pos(event.position().toPoint())
        if target_page_1based is None:
            event.ignore()
            return
        data = md.data(_MIME_TYPE)
        stream = QDataStream(data, QIODevice.OpenModeFlag.ReadOnly)
        count = stream.readUInt32()
        src = [stream.readUInt32() for _ in range(count)]
        event.acceptProposedAction()
        if not src:
            return
        # Single-source case: emit page_moved so the dialog can reorder
        # the document inline. Multi-source case is rare and the merge
        # flow handles it via pages_dropped_on_target.
        if len(src) == 1:
            self.page_moved.emit(src[0], target_page_1based)
        else:
            self.pages_dropped_on_target.emit(target_page_1based, src)


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

    def __init__(self, engine: ViewerEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
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

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.setToolTip("Re-render the grid (e.g. after editing).")
        self._btn_refresh.clicked.connect(self._populate)
        bar.addWidget(self._btn_refresh)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.close)
        bar.addWidget(self._btn_close)

        root.addLayout(bar)

        # Grid of thumbnails via our custom QListWidget subclass.
        self.list = _PageGrid()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(TILE_W - 24, TILE_H - 50))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSpacing(8)
        self.list.setUniformItemSizes(True)
        # IMPORTANT: setMovement(Static) silently downgrades the grid's
        # drag-drop mode from DragDrop to DropOnly, killing the
        # drag-from-tile flow. Re-apply after the view-mode/movement
        # setters so the grid actually starts a QDrag on mousedown.
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        self.list.itemActivated.connect(self._on_item_activated)
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.pages_dropped_on_target.connect(self._on_pages_dropped)
        self.list.external_pdfs_dropped.connect(self._on_external_pdfs_dropped)
        self.list.page_moved.connect(self._on_page_moved)
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

    def _on_page_moved(self, src_page_1based: int, target_page_1based: int):
        """User dragged a tile onto another tile — SWAP the two pages.

        The user explicitly wants the source and target pages to
        *exchange positions* (no shift of intervening pages, total page
        count unchanged). We use ``PDFManipulator.swap_pages`` for that,
        then reload + repopulate + emit ``pages_swapped`` so the main
        window can refresh the viewer.
        """
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return
        if src_page_1based == target_page_1based:
            return  # no-op — the user dropped a tile onto itself
        ok, msg = PDFManipulator.swap_pages(
            self.engine.path, src_page_1based, target_page_1based)
        if not ok:
            QMessageBox.warning(self, "Swap failed", msg)
            return
        # Reload the engine from disk so the live document reflects the
        # new page order. The caller (main window) is responsible for
        # re-pointing its pdf_viewer at the rebuilt engine.
        try:
            self.engine.reload_from_disk()
        except Exception as exc:
            QMessageBox.warning(
                self, "Reload failed",
                f"Swap succeeded but reload failed: {exc}")
            return
        self._populate()
        # Post-swap, the page that WAS at src is now at target, and
        # vice-versa. Emit both so the main window can decide what to
        # focus on (defaults to src's new slot = target_page_1based).
        self.pages_swapped.emit(src_page_1based, target_page_1based)
        # Keep the old signal firing too for compatibility with
        # downstream consumers that listen for any reorder; the "new
        # index" is target_page_1based for the source page.
        self.pages_reordered.emit(target_page_1based)

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
        """Right-click menu — actions apply to current selection."""
        item = self.list.itemAt(pos)
        if item is not None and not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)

        menu = QMenu(self)
        has_sel = bool(self.get_selected_pages())
        a_new = menu.addAction("New PDF from selected pages…")
        a_new.triggered.connect(self._action_new_pdf)
        a_new.setEnabled(has_sel)
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
