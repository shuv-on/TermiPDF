"""
annotation_engine.py — PyMuPDF logic for ink, highlight, and erase.

This module owns the higher-level annotation operations:
* Ink drawing (add_ink_annot)
* Rectangular highlight (add_highlight_annot)
* Text-based highlight (search + highlight)
* Erasing an annotation at a point (by hit-testing)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import fitz
from PyQt6.QtCore import QPointF, QRectF

from features.pdf_viewer.viewer_engine import ViewerEngine
from features.pdf_viewer.viewer_ui import CanvasStroke


@dataclass
class HighlightMatch:
    page_index: int
    rects: List[fitz.Rect]


class AnnotationEngine:
    """Coordinates annotation creation & deletion on a ViewerEngine."""

    def __init__(self, viewer: ViewerEngine):
        self.viewer = viewer
        # Default ink style (overridable per command)
        self.ink_color: Tuple[float, float, float] = (1.0, 0.0, 0.0)
        self.ink_thickness: float = 2.0

    # ---------------------------------------------------------------- ink
    def add_ink_stroke(self, stroke: CanvasStroke, page_index: Optional[int] = None):
        """Commit a freehand stroke into the PDF."""
        if not self.viewer.is_open:
            return False, "No PDF is open."
        if len(stroke.points) < 2:
            return False, "Stroke too short."

        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)

        # Convert point list to a list of fitz.Point. Support both attribute-
        # style (PyMuPDF Point) and method-style (QPointF) accessors so the
        # same list works whether it comes from QPointF widgets or fitz.Points.
        def _xx(p):
            return p.x() if callable(getattr(p, "x", None)) else p.x
        def _yy(p):
            return p.y() if callable(getattr(p, "y", None)) else p.y

        # add_ink_annot expects a list of stroke paths; each path is a
        # sequence of (x, y) float pairs (NOT fitz.Point objects).
        stroke_path = [(_xx(p), _yy(p)) for p in stroke.points]
        annot = page.add_ink_annot([stroke_path])
        annot.set_colors(stroke=fitz.utils.getColor(
            "#%02x%02x%02x" % (
                int(stroke.color_rgb[0] * 255),
                int(stroke.color_rgb[1] * 255),
                int(stroke.color_rgb[2] * 255),
            )
        ))
        # PyMuPDF: set_border takes (width, style, dashes, width rounding)
        try:
            annot.set_border(max(0.5, stroke.thickness))
        except Exception:
            annot.set_border_width(max(0.5, stroke.thickness))
        annot.update()
        return True, f"Stroke added on page {idx + 1}."

    # ------------------------------------------------------------- highlight
    def highlight_rect(self, rect: QRectF, page_index: Optional[int] = None) -> Tuple[bool, str]:
        if not self.viewer.is_open:
            return False, "No PDF is open."
        if rect.width() < 2 or rect.height() < 2:
            return False, "Selection too small."

        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)

        # Ensure the rect is within page bounds
        page_rect = page.rect
        # QRectF exposes x()/y()/width()/height() as methods
        r = fitz.Rect(
            max(page_rect.x0, rect.x()),
            max(page_rect.y0, rect.y()),
            min(page_rect.x1, rect.x() + rect.width()),
            min(page_rect.y1, rect.y() + rect.height()),
        )
        annot = page.add_highlight_annot(r)
        annot.set_colors(stroke=fitz.utils.getColor("yellow"))
        annot.update()
        return True, f"Highlight added on page {idx + 1}."

    def highlight_text(self, text: str) -> Tuple[bool, str]:
        """Find and highlight all occurrences of `text` across the document."""
        if not self.viewer.is_open:
            return False, "No PDF is open."
        if not text:
            return False, "Empty text."

        total = 0
        for page_index in range(self.viewer.page_count):
            page = self.viewer.get_page(page_index)
            rects = page.search_for(text)
            for r in rects:
                annot = page.add_highlight_annot(r)
                annot.set_colors(stroke=fitz.utils.getColor("yellow"))
                annot.update()
                total += 1
        return True, f"Highlighted {total} occurrence(s) of '{text}'."

    # ---------------------------------------------------------------- erase
    def erase_at(self, pt: QPointF, page_index: Optional[int] = None) -> Tuple[bool, str]:
        """Delete any annotation that contains the given PDF point."""
        if not self.viewer.is_open:
            return False, "No PDF is open."

        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)
        # QPointF.x()/y() are methods → call them; fitz.Point.x/.y are
        # attributes → read directly.
        px = pt.x() if callable(getattr(pt, "x", None)) else pt.x
        py = pt.y() if callable(getattr(pt, "y", None)) else pt.y
        point = fitz.Point(px, py)

        # Iterate every annotation on the page and remove the first that hits
        annots = list(page.annots() or [])
        if not annots:
            return False, "No annotations on this page."

        removed = 0
        for annot in annots:
            try:
                if annot.type[0] in (fitz.PDF_ANNOT_INK,
                                     fitz.PDF_ANNOT_HIGHLIGHT,
                                     fitz.PDF_ANNOT_FREETEXT,
                                     fitz.PDF_ANNOT_SQUIGGLY,
                                     fitz.PDF_ANNOT_STRIKEOut,
                                     fitz.PDF_ANNOT_UNDERLINE):
                    if annot.rect.contains(point):
                        page.delete_annot(annot)
                        removed += 1
            except Exception:
                continue

        if removed:
            return True, f"Removed {removed} annotation(s) on page {idx + 1}."
        return False, "No annotation hit at that location."
