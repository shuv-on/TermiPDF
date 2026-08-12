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

    def close(self):
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self.path = None
        self.current_page = 0

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
        self.current_page = page_index
        return True, f"On page {page_index + 1}/{self.page_count}."

    def next_page(self) -> Tuple[bool, str]:
        return self.goto(self.current_page + 1)

    def prev_page(self) -> Tuple[bool, str]:
        return self.goto(self.current_page - 1)

    def set_zoom(self, zoom: float) -> Tuple[bool, str]:
        zoom = max(0.25, min(zoom, 8.0))
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
