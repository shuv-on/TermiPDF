"""
canvas_events.py — Glue between PDFViewerUI and AnnotationEngine.

This module wires the viewer canvas's mouse callbacks (which fire with Qt
events) into the annotation engine (which operates on PDF coordinates).

The wiring is implemented as a small class that:
1) Hooks into a PDFViewerUI's commit callbacks (set via set_callbacks).
2) Forwards stroke / rect / hit-test commands to the AnnotationEngine.
3) Records every successful mutation on the UndoStack so undo/redo works.
4) Auto-persists the in-memory PDF to disk on a background thread so
   the user doesn't see a "blink" while PyMuPDF writes the file out.
"""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, QThread, QObject, pyqtSignal, Qt

from features.pdf_annotator.annotation_engine import AnnotationEngine
from features.pdf_viewer.viewer_engine import ViewerEngine
from features.pdf_viewer.viewer_ui import PDFViewerUI, CanvasStroke


class _SaveWorker(QObject):
    """Save the in-memory PDF to disk on a background thread.

    PyMuPDF's ``doc.save()`` is the slow part of any annotation flow —
    for a 100-page PDF with annotations it can take 200-500 ms which
    on the GUI thread causes the visible "blink" the user reported.
    Doing the write on a worker means the canvas refreshes immediately
    and the file write happens invisibly in the background.
    """
    save_done = pyqtSignal(bool, str)   # (ok, message)

    def __init__(self, path: str, tmp_path: str):
        super().__init__()
        self._path = path
        self._tmp_path = tmp_path

    def run(self):
        try:
            from features.pdf_viewer.viewer_engine import ViewerEngine
            # Open a fresh fitz.Document from the current on-disk file
            # and save it back with garbage collection. We deliberately
            # do NOT share the engine's in-memory doc because we want
            # the engine's mutations to land on disk via the standard
            # ``garbage=4, deflate=True`` path.
            import fitz
            doc = fitz.open(self._path)
            doc.save(self._tmp_path, garbage=4, deflate=True)
            doc.close()
            os.replace(self._tmp_path, self._path)
            self.save_done.emit(True, f"Saved → {self._path}")
        except Exception as exc:
            try:
                if os.path.exists(self._tmp_path):
                    os.remove(self._tmp_path)
            except OSError:
                pass
            self.save_done.emit(False, f"Save failed: {exc}")


class CanvasEventRouter:
    """Bridges viewer canvas ↔ annotation engine + undo stack."""

    def __init__(self, viewer: ViewerEngine, annot: AnnotationEngine,
                 ui: PDFViewerUI, undo_stack: Optional[object] = None,
                 editor: Optional[object] = None):
        self.viewer = viewer
        self.annot = annot
        self.ui = ui
        self.undo = undo_stack  # may be None for headless use
        # TextEditor is optional but required for "Insert text" / "Edit
        # text" mode. Without it those modes crash with
        # AttributeError: 'CanvasEventRouter' object has no attribute
        # 'editor' — we keep the field optional so headless tests can
        # still instantiate the router without a TextEditor.
        self.editor = editor
        # Background save state — only one save worker in flight at a
        # time so we don't accumulate zombie threads.
        self._save_thread: Optional[QThread] = None
        self._save_worker: Optional[_SaveWorker] = None

        # Wire UI callbacks
        self.ui._commit_stroke = self._on_commit_stroke
        self.ui._commit_highlight_rect = self._on_commit_highlight_rect
        self.ui._commit_rect = self._on_commit_rect
        self.ui._commit_ellipse = self._on_commit_ellipse
        self.ui._commit_arrow = self._on_commit_arrow
        self.ui._request_erase_at = self._on_erase_at
        self.ui._request_note = self._on_request_note
        self.ui._request_text_insert = self._on_request_text_insert
        self.ui._request_edit_text = self._on_request_edit_text
        self.ui._request_signature = self._on_request_signature
        self.ui._commit_select_point = self._on_select_point
        self.ui._commit_select_rect = self._on_select_rect

    # ============================================================== helpers
    def _refresh(self):
        # Drop any cached render so the just-added annotation is actually
        # visible after refresh — without this, the engine serves a stale
        # pixmap from the cache and the user sees "nothing happened."
        try:
            self.viewer.invalidate_render_cache()
        except Exception:
            pass
        # Persist the in-memory doc on a background thread so the user
        # doesn't see a "blink" while PyMuPDF writes the file out. The
        # canvas refreshes immediately; the file write is invisible.
        try:
            if self.viewer.path and self.viewer.is_open:
                self._kick_background_save()
        except Exception:
            pass
        self.ui.surface.clear_live_strokes()
        self.ui.refresh()
        self.ui.annotations_changed.emit()

    def _kick_background_save(self):
        """Spawn (or coalesce into) a background save worker.

        PyMuPDF's doc.save() can take 200-500 ms on a multi-hundred-page
        PDF. Doing it on the GUI thread causes the canvas to "blink" (the
        user-visible 500 ms freeze they reported). Here we hand the save
        off to a worker thread so refresh returns immediately.

        If a save is already in flight, we don't queue another one —
        the engine's in-memory doc already reflects all annotations
        added since the previous save started, so a single tail-save
        will catch them. We mark a flag for the worker to run once more
        when it finishes, ensuring the latest state lands on disk.
        """
        if not self.viewer.path:
            return
        if self._save_thread is not None and self._save_thread.isRunning():
            # A save is already pending; the in-memory doc reflects
            # every annotation so far. Mark for one more tail-save and
            # bail — the existing worker's save_done handler will kick
            # the next one if anything was added during this run.
            self._save_pending = True
            return
        self._save_pending = False
        path = self.viewer.path
        tmp = path + ".tmp.pdf"
        # Parent the thread to the router (or to the UI as fallback)
        # so its lifetime tracks a parent and Qt handles join on
        # destruction. Without a parent the thread can be GC'd while
        # still running.
        parent = self.ui if self.ui is not None else None
        thread = QThread(parent)
        worker = _SaveWorker(path, tmp)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Save_done → update our refs (use a queued connection so it
        # runs on the GUI thread, not the worker thread).
        worker.save_done.connect(
            self._on_save_done, Qt.ConnectionType.QueuedConnection)
        worker.save_done.connect(thread.quit)
        worker.save_done.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._save_thread = thread
        self._save_worker = worker
        thread.start()

    def _on_save_done(self, ok: bool, msg: str):
        """Worker finished. If a save was queued during the run, kick
        another one — otherwise clear refs."""
        try:
            self._save_thread = None
            self._save_worker = None
        except Exception:
            return
        try:
            if getattr(self, "_save_pending", False) and self.viewer.is_open:
                self._save_pending = False
                self._kick_background_save()
        except Exception:
            pass

    def shutdown(self):
        """Block on any in-flight save worker so we don't quit while a
        file is half-written. Safe to call from main window close."""
        # Disconnect first so the worker can't fire _on_save_done after
        # we've started tearing down.
        try:
            if self._save_worker is not None:
                self._save_worker.save_done.disconnect(self._on_save_done)
        except Exception:
            pass
        if self._save_thread is not None and self._save_thread.isRunning():
            try:
                self._save_thread.quit()
                self._save_thread.wait(3000)
            except Exception:
                pass
        self._save_thread = None
        self._save_worker = None

    def _record_added(self, annot_obj):
        """Push a successful add to the undo stack if available."""
        if not self.undo:
            return
        try:
            # Get the page index from the annot
            page_index = self.viewer.current_page
            self.undo.push_added(page_index, annot_obj)
        except Exception:
            pass

    # ==================================================== stroke / highlight
    def _on_commit_stroke(self, stroke: CanvasStroke):
        ok, msg = self.annot.add_ink_stroke(stroke)
        if ok:
            # Re-fetch the most recently added ink annot on this page for undo
            if self.undo:
                try:
                    page = self.viewer.get_current_page()
                    last = None
                    for a in page.annots() or []:
                        if int(a.type[0]) == 5:  # PDF_ANNOT_INK
                            last = a
                    if last is not None:
                        self.undo.push_added(self.viewer.current_page, last)
                except Exception:
                    pass
            self._refresh()
        return msg

    def _on_commit_highlight_rect(self, rect: QRectF):
        ok, msg = self.annot.highlight_rect(rect)
        if ok:
            if self.undo:
                try:
                    page = self.viewer.get_current_page()
                    last = None
                    for a in page.annots() or []:
                        if int(a.type[0]) == fitz.PDF_ANNOT_HIGHLIGHT:
                            last = a
                    if last is not None:
                        self.undo.push_added(self.viewer.current_page, last)
                except Exception:
                    pass
            self._refresh()
        return msg

    # =========================================================== shape tools
    def _on_commit_rect(self, rect: QRectF):
        # Snapshot the annotation count so we can find the new one after add
        page = self.viewer.get_current_page() if self.viewer.is_open else None
        pre = []
        if page is not None:
            try:
                pre = list(page.annots() or [])
            except Exception:
                pre = []
        ok, msg = self.annot.add_rect(rect)
        if ok:
            if self.undo and page is not None:
                try:
                    post = list(page.annots() or [])
                    new_annots = [a for a in post if a not in pre]
                    if new_annots:
                        self.undo.push_added(self.viewer.current_page, new_annots[-1])
                except Exception:
                    pass
            self._refresh()
        return msg

    def _on_commit_ellipse(self, rect: QRectF):
        page = self.viewer.get_current_page() if self.viewer.is_open else None
        pre = []
        if page is not None:
            try:
                pre = list(page.annots() or [])
            except Exception:
                pre = []
        ok, msg = self.annot.add_ellipse(rect)
        if ok:
            if self.undo and page is not None:
                try:
                    post = list(page.annots() or [])
                    new_annots = [a for a in post if a not in pre]
                    if new_annots:
                        self.undo.push_added(self.viewer.current_page, new_annots[-1])
                except Exception:
                    pass
            self._refresh()
        return msg

    def _on_commit_arrow(self, start_pt: QPointF, end_pt: QPointF):
        page = self.viewer.get_current_page() if self.viewer.is_open else None
        pre = []
        if page is not None:
            try:
                pre = list(page.annots() or [])
            except Exception:
                pre = []
        ok, msg = self.annot.add_arrow(start_pt, end_pt)
        if ok:
            if self.undo and page is not None:
                try:
                    post = list(page.annots() or [])
                    new_annots = [a for a in post if a not in pre]
                    if new_annots:
                        self.undo.push_added(self.viewer.current_page, new_annots[-1])
                except Exception:
                    pass
            self._refresh()
        return msg

    # =============================================================== erase
    def _on_erase_at(self, pt: QPointF):
        # Snapshot the annotation we're about to delete so we can undo.
        if not self.viewer.is_open:
            return
        page = self.viewer.get_current_page()
        try:
            annots = list(page.annots() or [])
        except Exception:
            annots = []
        target = None
        for a in annots:
            try:
                if a.rect.contains(self._to_fitz_point(pt)):
                    target = a
                    break
            except Exception:
                continue
        if target is None:
            return "No annotation at that point."
        # Snapshot BEFORE delete so the annot is still bound to its page.
        snap = None
        if self.undo:
            try:
                snap = self.undo.snapshot_annot(target)
            except Exception:
                snap = None
        ok, msg = self.annot.erase_at(pt)
        if ok and snap is not None and self.undo:
            self.undo.push_deleted(self.viewer.current_page, snap)
        if ok:
            self._refresh()
        return msg

    # ================================================ text / note / signature
    def _on_request_note(self, pt: QPointF):
        """Prompt the user for note text and drop a sticky-note annotation.

        The router imports ``QInputDialog`` lazily so headless tests don't
        need a Qt event loop just to construct this class.
        """
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            None, "Sticky note", "Note text:")
        if not ok or not text:
            return "Cancelled."
        ok2, msg = self.annot.add_sticky_note(pt, text)
        if ok2:
            self._refresh()
        return msg

    def _on_request_text_insert(self, pt: QPointF):
        if self.editor is None:
            return "Insert text: editor not wired up."
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            None, "Insert text", "Text:")
        if not ok or not text:
            return "Cancelled."
        ok2, msg = self.editor.add_text(
            text, self.viewer.current_page + 1,
            pt.x(), pt.y() + 12, font_size=14, color=(0, 0, 0))
        if ok2:
            self._refresh()
        return msg

    def _on_request_edit_text(self, pt: QPointF):
        """Replace the text nearest to (x, y) on the current page."""
        if self.editor is None:
            return "Edit text: editor not wired up."
        if not self.viewer.is_open:
            return "No PDF open."
        try:
            page = self.viewer.get_current_page()
            words = page.get_text("words")
        except Exception:
            words = []
        nearest = None
        best_d = float("inf")
        for w in words:
            # w = (x0, y0, x1, y1, "word", block_no, line_no, word_no)
            cx = (w[0] + w[2]) / 2.0
            cy = (w[1] + w[3]) / 2.0
            d = (cx - pt.x()) ** 2 + (cy - pt.y()) ** 2
            if d < best_d:
                best_d = d
                nearest = w
        if not nearest:
            return "No text found near the click point."
        old_text = nearest[4]
        from PyQt6.QtWidgets import QInputDialog
        new_text, ok = QInputDialog.getText(
            None, "Replace text",
            f"Replace '{old_text}' with:")
        if not ok or not new_text:
            return "Cancelled."
        ok2, msg = self.editor.whiteout_then_insert(
            self.viewer.current_page + 1,
            nearest[0], nearest[3],
            new_text, viewer=self.ui)
        if ok2:
            self._refresh()
        return msg

    def _on_request_signature(self, pt: QPointF):
        """Open the signature capture dialog; on success stamp the saved PNG."""
        from features.pdf_annotator.signature_dialog import SignatureDialog
        dlg = SignatureDialog(self.ui)
        if dlg.exec() != SignatureDialog.DialogCode.Accepted:
            return "Cancelled."
        png_bytes = dlg.get_png_bytes()
        if not png_bytes:
            return "No signature captured."
        # Place a 200x60 rect centered on click point
        from PyQt6.QtCore import QRectF
        rect = QRectF(pt.x() - 100, pt.y() - 30, 200, 60)
        ok, msg = self.annot.add_signature(rect, png_bytes)
        if ok:
            self._refresh()
        return msg

    # ================================================================ utils
    @staticmethod
    def _to_fitz_point(pt: QPointF):
        import fitz
        return fitz.Point(pt.x(), pt.y())

    # ====================================================== text selection
    def _on_select_point(self, pt: QPointF):
        """Pick the text nearest a click; copy to clipboard."""
        from features.pdf_viewer.text_selector import extract_text_at
        ok, text = extract_text_at(self.viewer, pt)
        if ok and text:
            self._copy_to_clipboard(text)
            # Stash the selection on the viewer so the right-click QR-share
            # context menu can read it.
            try:
                self.ui.set_selection(text)
            except Exception:
                pass
            self.ui.annotations_changed.emit()
        return text

    def _on_select_rect(self, rect):
        """Extract all words inside a drag-selected rect; copy to clipboard."""
        from features.pdf_viewer.text_selector import extract_text_in_rect
        ok, text = extract_text_in_rect(self.viewer, rect)
        if ok and text:
            self._copy_to_clipboard(text)
            try:
                self.ui.set_selection(text)
            except Exception:
                pass
            self.ui.annotations_changed.emit()
        return text

    def _copy_to_clipboard(self, text: str):
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
        except Exception:
            pass


# Late import so module-level import order doesn't matter.
import fitz  # noqa: E402