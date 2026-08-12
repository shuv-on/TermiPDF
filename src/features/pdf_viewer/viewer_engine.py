"""
viewer_engine.py — PyMuPDF rendering & outline helpers.

Owns the fitz.Document and is the single source of truth for:
* Opening / closing the document
* Rasterizing pages at a given zoom matrix
* Extracting the outline (Table of Contents) as a tree
* Returning a page reference + its rect for coordinate mapping
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import fitz  # PyMuPDF

from PyQt6.QtCore import QThread, pyqtSignal, Qt


@dataclass
class OutlineNode:
    title: str
    page: int                # 1-based page number
    level: int               # depth (0 = top)
    children: List["OutlineNode"]


@dataclass
class RenderResult:
    """PNG bytes + page dimensions + the source fitz.Page for callers."""
    png_bytes: bytes
    page_width_pt: float     # PDF user units
    page_height_pt: float
    page_index: int          # 0-based


class ViewerEngine:
    """Thin wrapper around fitz.Document used by the viewer & other features."""

    def __init__(self):
        self.doc: Optional[fitz.Document] = None
        self.path: Optional[str] = None
        self.zoom: float = 1.5          # default zoom factor
        self.current_page: int = 0      # 0-based index
        # ----- render cache ----------------------------------------------
        # Caches the most recent full-resolution render so next/prev
        # navigation and undo/redo (which re-renders the same page at
        # the same zoom) is instant. The cache is invalidated on any
        # document-editing operation (annotation add/erase, page ops).
        self._render_cache_key: Optional[Tuple[int, float]] = None
        self._render_cache_bytes: Optional[bytes] = None
        self._render_cache_dims: Tuple[float, float] = (0.0, 0.0)
        # The current background render worker, if any. Replaced every
        # time ``request_full_render`` is called. Held as an attribute
        # (not a local) so it survives until the thread emits finished_ok.
        self._render_worker = None

    # ---------------------------------------------------------------- open
    def open(self, path: str) -> Tuple[bool, str]:
        try:
            self.doc = fitz.open(path)
            self.path = path
            self.current_page = 0
            return True, f"Opened '{path}' ({len(self.doc)} pages)."
        except Exception as exc:
            self.doc = None
            self.path = None
            return False, f"Failed to open PDF: {exc}"

    def reload_from_disk(self) -> Tuple[bool, str]:
        """Re-open the currently-bound path from disk.

        Used after an in-place page reorder / external merge so the
        live document reflects the new on-disk state. Caches are
        cleared; current_page is clamped to the new page count.
        """
        if not self.path:
            return False, "No path to reload."
        prev_page = self.current_page
        self.close()
        ok, msg = self.open(self.path)
        if not ok:
            return False, msg
        if self.page_count > 0:
            self.current_page = max(0, min(prev_page, self.page_count - 1))
        return True, f"Reloaded '{self.path}' ({self.page_count} pages)."

    def close(self):
        # Stop any background render first so it doesn't outlive the doc.
        self._cancel_pending_render()
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        # NOTE: we intentionally KEEP self.path here so reload_from_disk()
        # can re-open the same file after an external mutation (e.g. a
        # PDFManipulator.swap_pages/rotate/delete round-trip from the
        # Pages Manager). Previously close() set path=None which made
        # reload_from_disk return 'No path to reload' and left the
        # engine empty — the viewer would then show the old pages even
        # though the file on disk had changed.
        self.current_page = 0
        self.invalidate_render_cache()

    @property
    def is_open(self) -> bool:
        return self.doc is not None and not self.doc.is_closed

    @property
    def page_count(self) -> int:
        return len(self.doc) if self.is_open else 0

    # -------------------------------------------------------- navigation
    def goto(self, page_index: int) -> Tuple[bool, str]:
        if not self.is_open:
            return False, "No PDF is open."
        if page_index < 0 or page_index >= self.page_count:
            return False, f"Page {page_index + 1} out of range (1..{self.page_count})."
        if page_index != self.current_page:
            self._cancel_pending_render()
            self.invalidate_render_cache()
        self.current_page = page_index
        return True, f"On page {page_index + 1}/{self.page_count}."

    def next_page(self) -> Tuple[bool, str]:
        return self.goto(self.current_page + 1)

    def prev_page(self) -> Tuple[bool, str]:
        return self.goto(self.current_page - 1)

    def set_zoom(self, zoom: float) -> Tuple[bool, str]:
        zoom = max(0.25, min(zoom, 8.0))
        if abs(zoom - self.zoom) > 1e-6:
            self._cancel_pending_render()
            self.invalidate_render_cache()
        self.zoom = zoom
        return True, f"Zoom set to {zoom:.2f}x"

    def zoom_in(self) -> Tuple[bool, str]:
        return self.set_zoom(self.zoom * 1.25)

    def zoom_out(self) -> Tuple[bool, str]:
        return self.set_zoom(self.zoom / 1.25)

    def fit_to(self, width_pt: float, height_pt: float, viewport_w: int, viewport_h: int):
        """Compute zoom so the page fits the viewport."""
        if width_pt <= 0 or height_pt <= 0:
            return
        sx = viewport_w / width_pt
        sy = viewport_h / height_pt
        self.zoom = max(0.25, min(min(sx, sy), 4.0))

    # -------------------------------------------------------------- render
    def render_current(self) -> RenderResult:
        if not self.is_open:
            raise RuntimeError("No PDF is open.")
        page = self.doc.load_page(self.current_page)
        matrix = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return RenderResult(
            png_bytes=pix.tobytes("png"),
            page_width_pt=page.rect.width,
            page_height_pt=page.rect.height,
            page_index=self.current_page,
        )

    def get_current_page(self) -> "fitz.Page":
        if not self.is_open:
            raise RuntimeError("No PDF is open.")
        return self.doc.load_page(self.current_page)

    def get_page(self, index: int) -> "fitz.Page":
        if not self.is_open:
            raise RuntimeError("No PDF is open.")
        return self.doc.load_page(index)

    # ---------------------------------------------------- search / find
    def find_all(self, text: str) -> List[Tuple[int, "fitz.Rect"]]:
        """Return (page_index_0based, fitz.Rect) for every match.

        Cached per (text, page_count) to make repeated Ctrl+F usage snappy.
        Empty text returns an empty list.
        """
        if not self.is_open or not text:
            return []
        cache_key = ("find", text, self.page_count)
        if getattr(self, "_find_cache_key", None) == cache_key:
            return self._find_cache or []
        results: List[Tuple[int, "fitz.Rect"]] = []
        for i in range(self.page_count):
            try:
                page = self.doc.load_page(i)
                rects = page.search_for(text)
            except Exception:
                rects = []
            for r in rects:
                results.append((i, r))
        self._find_cache_key = cache_key
        self._find_cache = results
        return results

    # --------------------------------------------------- thumbnail render
    def render_thumbnail(self, index: int, width_pt: Optional[float] = None,
                         height_pt: Optional[float] = None) -> Optional[bytes]:
        """Render a single page as PNG bytes for the thumbnail sidebar.

        If width_pt / height_pt are given the result is scaled to match.
        Returns None if the page can't be rendered.
        """
        if not self.is_open:
            return None
        try:
            page = self.doc.load_page(index)
            # Default: a small zoom so the thumbnail is readable
            scale = 0.4
            if width_pt and page.rect.width:
                scale = max(0.15, min(width_pt / page.rect.width, 2.0))
            matrix = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            return pix.tobytes("png")
        except Exception:
            return None

    def invalidate_thumbnail_cache(self):
        """Called after annotation activity so thumbs re-render on next show."""
        # Placeholder hook for future explicit invalidation.
        pass

    # ----------------------------------------------------------- outline
    def get_outline(self) -> List[OutlineNode]:
        """Return the PDF outline (TOC) as a tree of OutlineNode."""
        if not self.is_open:
            return []
        raw = self.doc.get_toc(simple=False) or []
        nodes: List[OutlineNode] = []
        stack: List[Tuple[int, OutlineNode]] = []  # (level, node)

        for entry in raw:
            # entry shape: [level, title, page, dest?]
            level, title, page = entry[0], entry[1], entry[2]
            node = OutlineNode(title=title, page=page, level=level, children=[])

            # Pop the stack until we find a parent with a smaller level
            while stack and stack[-1][0] >= level:
                stack.pop()

            if stack:
                stack[-1][1].children.append(node)
            else:
                nodes.append(node)
            stack.append((level, node))

        return nodes

    # ------------------------------------------------------ annotation ops
    def save(self, path: Optional[str] = None) -> Tuple[bool, str]:
        """Save the open document to disk.

        In-place save (overwriting the originally opened file) is performed
        via a temporary file + atomic replace so that garbage collection is
        always available (PyMuPDF forbids garbage with incremental writes,
        and many structural edits reject incremental altogether).

        Saves to a new path use a plain full rewrite with garbage=4.
        """
        if not self.is_open:
            return False, "No PDF is open."
        out = path or self.path
        if not out:
            return False, "No destination path."
        try:
            in_place = (out == self.path) and (path is None)
            if in_place:
                tmp = out + ".tmp.pdf"
                self.doc.save(tmp, garbage=4, deflate=True)
                os.replace(tmp, out)
            else:
                self.doc.save(out, garbage=4, deflate=True)
            self.path = out
            return True, f"Saved to '{out}'."
        except Exception as exc:
            return False, f"Save failed: {exc}"

    # -------------------------------------------------- render cache
    def invalidate_render_cache(self) -> None:
        """Drop any cached render so the next refresh re-renders from scratch.

        Called after any document-editing operation (annotation add /
        erase, rotate, etc.) so the user doesn't see a stale pixmap.
        """
        self._render_cache_key = None
        self._render_cache_bytes = None
        self._render_cache_dims = (0.0, 0.0)

    def render_current_preview(self, zoom: float = 0.5) -> RenderResult:
        """Synchronous low-zoom preview render.

        Used by ``PDFViewerUI.refresh`` to show *something* on screen
        immediately while the full-resolution render runs on a worker
        thread. The preview is fast even on large PDFs because we're
        rendering at a fraction of the target zoom.
        """
        if not self.is_open:
            raise RuntimeError("No PDF is open.")
        page = self.doc.load_page(self.current_page)
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return RenderResult(
            png_bytes=pix.tobytes("png"),
            page_width_pt=page.rect.width,
            page_height_pt=page.rect.height,
            page_index=self.current_page,
        )

    def request_full_render(self, callback):
        """Render the current page at full zoom on a worker thread.

        ``callback(png_bytes, width_pt, height_pt)`` is invoked on the
        main thread (via ``Qt.ConnectionType.QueuedConnection``) when
        the worker finishes. If we already have a cached render for
        this (page, zoom), we deliver it synchronously instead.
        """
        if not self.is_open:
            return
        cache_key = (self.current_page, self.zoom)
        if (cache_key == self._render_cache_key
                and self._render_cache_bytes is not None):
            # Cache hit — deliver immediately.
            try:
                callback(self._render_cache_bytes,
                         *self._render_cache_dims)
            except Exception:
                pass
            return
        # Cancel any in-flight worker before starting a new one.
        self._cancel_pending_render()
        worker = _RenderWorker(self.doc, self.current_page, self.zoom)
        # Bound the worker to this engine so we can find it later if
        # the engine is closed mid-render.
        worker.finished_ok.connect(
            lambda png, w, h, k=cache_key:
                self._on_render_finished(callback, k, png, w, h),
            Qt.ConnectionType.QueuedConnection,
        )
        self._render_worker = worker
        worker.start()

    def _on_render_finished(self, callback, key, png, w, h):
        """Worker delivered PNG bytes — store in cache and dispatch."""
        try:
            self._render_cache_key = key
            self._render_cache_bytes = png
            self._render_cache_dims = (w, h)
        except Exception:
            pass
        try:
            callback(png, w, h)
        except Exception:
            pass

    def _cancel_pending_render(self):
        """Best-effort stop of the in-flight worker, if any."""
        w = getattr(self, "_render_worker", None)
        if w is None:
            return
        if w.isRunning():
            try:
                w.requestInterruption()
                w.wait(50)  # ms — short so the UI doesn't stall
            except Exception:
                pass
        self._render_worker = None


class _RenderWorker(QThread):
    """Background thread that rasterizes a single page at full zoom.

    Emits ``finished_ok(png_bytes, width_pt, height_pt)`` when done.
    Errors are silent — the UI keeps showing whatever it has.
    """
    finished_ok = pyqtSignal(bytes, float, float)

    def __init__(self, doc, page_index: int, zoom: float):
        super().__init__()
        self._doc = doc
        self._page_index = page_index
        self._zoom = zoom

    def run(self):
        try:
            page = self._doc.load_page(self._page_index)
            matrix = fitz.Matrix(self._zoom, self._zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            self.finished_ok.emit(
                pix.tobytes("png"),
                float(page.rect.width),
                float(page.rect.height),
            )
        except Exception:
            # Silent: caller keeps the preview pixmap if full-res fails.
            pass
