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
        self.highlight_color: Tuple[float, float, float] = (1.0, 0.95, 0.0)
        self.shape_color: Tuple[float, float, float] = (0.5, 0.6, 1.0)
        self.shape_fill: bool = False
        self.shape_border: float = 2.0

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
        annot.set_colors(stroke=self._to_fitz_color(stroke.color_rgb))
        # PyMuPDF: set_border takes (width, style, dashes, width rounding)
        try:
            annot.set_border(max(0.5, stroke.thickness))
        except Exception:
            annot.set_border_width(max(0.5, stroke.thickness))
        annot.update()
        return True, f"Stroke added on page {idx + 1}."

    # ------------------------------------------------------------- highlight
    def highlight_rect(self, rect: QRectF, page_index: Optional[int] = None,
                         color_rgb: Optional[Tuple[float, float, float]] = None
                         ) -> Tuple[bool, str]:
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
        rgb = color_rgb or self.highlight_color
        annot = page.add_highlight_annot(r)
        annot.set_colors(stroke=self._to_fitz_color(rgb))
        annot.update()
        return True, f"Highlight added on page {idx + 1}."

    def highlight_text(self, text: str, color_rgb: Optional[Tuple[float, float, float]] = None
                        ) -> Tuple[bool, str]:
        """Find and highlight all occurrences of `text` across the document."""
        if not self.viewer.is_open:
            return False, "No PDF is open."
        if not text:
            return False, "Empty text."

        rgb = color_rgb or self.highlight_color
        total = 0
        for page_index in range(self.viewer.page_count):
            page = self.viewer.get_page(page_index)
            rects = page.search_for(text)
            for r in rects:
                annot = page.add_highlight_annot(r)
                annot.set_colors(stroke=self._to_fitz_color(rgb))
                annot.update()
                total += 1
        return True, f"Highlighted {total} occurrence(s) of '{text}'."

    # ----------------------------------------------------------- color setters
    def set_highlight_color(self, rgb: Tuple[float, float, float]) -> None:
        self.highlight_color = tuple(float(x) for x in rgb)

    def set_shape_color(self, rgb: Tuple[float, float, float]) -> None:
        self.shape_color = tuple(float(x) for x in rgb)

    def set_ink_color(self, rgb: Tuple[float, float, float]) -> None:
        self.ink_color = tuple(float(x) for x in rgb)

    @staticmethod
    def _rgb_to_hex(rgb) -> str:
        r, g, b = rgb
        return "#%02x%02x%02x" % (
            max(0, min(255, int(r * 255))),
            max(0, min(255, int(g * 255))),
            max(0, min(255, int(b * 255))),
        )

    @staticmethod
    def _to_fitz_color(rgb) -> Tuple[float, float, float]:
        """Convert an (r, g, b) tuple in 0..1 floats to a PyMuPDF color.

        We CANNOT pass a "#RRGGBB" hex to ``fitz.utils.getColor`` — it only
        accepts the small CSS color-name dictionary. Anything else returns
        white, which is why every highlight drawn with a hex color comes
        out invisible on a white page. We compute the float tuple
        directly so any color works.
        """
        try:
            r, g, b = rgb
            return (float(r), float(g), float(b))
        except Exception:
            return (0.0, 0.0, 0.0)

    @staticmethod
    def _parse_color(color) -> Tuple[float, float, float]:
        """Parse either an ``(r, g, b)`` float tuple, a hex string, or a CSS
        color name into a PyMuPDF-friendly float triple.

        Used wherever callers can supply colors (draw, highlight, shape,
        note). Centralized so every code path uses the same logic.
        """
        # Already a tuple/list of three numbers?
        if isinstance(color, (tuple, list)) and len(color) == 3:
            try:
                r, g, b = color
                if all(isinstance(v, (int, float)) for v in (r, g, b)):
                    # Normalize 0..255 ints → 0..1 floats
                    def _n(v):
                        v = float(v)
                        return v / 255.0 if v > 1.0 else v
                    return (_n(r), _n(g), _n(b))
            except Exception:
                pass
        if not isinstance(color, str):
            return (1.0, 0.95, 0.0)
        s = color.strip().lower()
        # Hex string
        if s.startswith("#") and len(s) in (4, 7):
            try:
                if len(s) == 4:
                    r = int(s[1]*2, 16) / 255.0
                    g = int(s[2]*2, 16) / 255.0
                    b = int(s[3]*2, 16) / 255.0
                else:
                    r = int(s[1:3], 16) / 255.0
                    g = int(s[3:5], 16) / 255.0
                    b = int(s[5:7], 16) / 255.0
                return (r, g, b)
            except Exception:
                return (1.0, 0.95, 0.0)
        # Named color — fall back to fitz.utils.getColor, then a small
        # built-in fallback table for cases where getColor returns the
        # wrong thing (e.g. hex pass-through).
        try:
            triple = fitz.utils.getColor(s)
            if triple != (1.0, 1.0, 1.0) or s in ("white", "#fff", "#ffffff"):
                return tuple(triple)  # type: ignore[return-value]
        except Exception:
            pass
        # Custom fallback for common colors that fitz mis-routes to white.
        named = {
            "yellow": (1.0, 0.95, 0.0),
            "red": (1.0, 0.0, 0.0),
            "green": (0.0, 0.7, 0.0),
            "blue": (0.0, 0.0, 1.0),
            "orange": (1.0, 0.5, 0.0),
            "pink": (1.0, 0.4, 0.7),
            "purple": (0.6, 0.2, 0.8),
            "cyan": (0.0, 1.0, 1.0),
            "magenta": (1.0, 0.0, 1.0),
        }
        if s in named:
            return named[s]
        return (1.0, 0.95, 0.0)

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
                                     fitz.PDF_ANNOT_FREE_TEXT,
                                     fitz.PDF_ANNOT_SQUIGGLY,
                                     fitz.PDF_ANNOT_STRIKE_OUT,
                                     fitz.PDF_ANNOT_UNDERLINE,
                                     fitz.PDF_ANNOT_SQUARE,
                                     fitz.PDF_ANNOT_CIRCLE,
                                     fitz.PDF_ANNOT_LINE,
                                     fitz.PDF_ANNOT_TEXT):
                    if annot.rect.contains(point):
                        page.delete_annot(annot)
                        removed += 1
            except Exception:
                continue

        if removed:
            return True, f"Removed {removed} annotation(s) on page {idx + 1}."
        return False, "No annotation hit at that location."

    # ----------------------------------------------------------- shapes
    def add_rect(self, rect: QRectF, page_index: Optional[int] = None,
                  color_rgb: Optional[Tuple[float, float, float]] = None,
                  fill: bool = False, border: Optional[float] = None) -> Tuple[bool, str]:
        if not self.viewer.is_open:
            return False, "No PDF is open."
        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)
        page_rect = page.rect
        x = max(page_rect.x0, rect.x())
        y = max(page_rect.y0, rect.y())
        w = min(page_rect.x1, rect.x() + rect.width()) - x
        h = min(page_rect.y1, rect.y() + rect.height()) - y
        if w <= 0 or h <= 0:
            return False, "Empty rect."
        r = fitz.Rect(x, y, x + w, y + h)
        rgb = color_rgb or self.shape_color
        annot = page.add_rect_annot(r)
        annot.set_colors(stroke=self._to_fitz_color(rgb))
        if fill:
            annot.set_colors(fill=self._to_fitz_color(rgb))
        try:
            annot.set_border(border if border else self.shape_border)
        except Exception:
            try:
                annot.set_border_width(border if border else self.shape_border)
            except Exception:
                pass
        annot.update()
        return True, f"Rectangle added on page {idx + 1}."

    def add_ellipse(self, rect: QRectF, page_index: Optional[int] = None,
                     color_rgb: Optional[Tuple[float, float, float]] = None,
                     fill: bool = False, border: Optional[float] = None) -> Tuple[bool, str]:
        if not self.viewer.is_open:
            return False, "No PDF is open."
        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)
        page_rect = page.rect
        x = max(page_rect.x0, rect.x())
        y = max(page_rect.y0, rect.y())
        w = min(page_rect.x1, rect.x() + rect.width()) - x
        h = min(page_rect.y1, rect.y() + rect.height()) - y
        if w <= 0 or h <= 0:
            return False, "Empty ellipse."
        r = fitz.Rect(x, y, x + w, y + h)
        rgb = color_rgb or self.shape_color
        annot = page.add_circle_annot(r)  # circle w/ arbitrary bounding rect = ellipse
        annot.set_colors(stroke=self._to_fitz_color(rgb))
        if fill:
            annot.set_colors(fill=self._to_fitz_color(rgb))
        try:
            annot.set_border(border if border else self.shape_border)
        except Exception:
            pass
        annot.update()
        return True, f"Ellipse added on page {idx + 1}."

    def add_arrow(self, start_pt: QPointF, end_pt: QPointF,
                   page_index: Optional[int] = None,
                   color_rgb: Optional[Tuple[float, float, float]] = None,
                   border: Optional[float] = None) -> Tuple[bool, str]:
        if not self.viewer.is_open:
            return False, "No PDF is open."
        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)
        rgb = color_rgb or self.shape_color

        def _x(p):
            return p.x() if callable(getattr(p, "x", None)) else p.x
        def _y(p):
            return p.y() if callable(getattr(p, "y", None)) else p.y
        p1 = fitz.Point(_x(start_pt), _y(start_pt))
        p2 = fitz.Point(_x(end_pt), _y(end_pt))
        annot = page.add_line_annot(p1, p2)
        annot.set_colors(stroke=self._to_fitz_color(rgb))
        try:
            # Closed arrow head at end point
            annot.set_line_ends(fitz.PDF_ANNOT_LINE_END_NONE,
                                fitz.PDF_ANNOT_LINE_END_CLOSED_ARROW)
            annot.set_border(border if border else self.shape_border)
        except Exception:
            pass
        annot.update()
        return True, f"Arrow added on page {idx + 1}."

    # -------------------------------------------------------- sticky note
    def add_sticky_note(self, pt: QPointF, text: str,
                         page_index: Optional[int] = None,
                         color: str = "yellow") -> Tuple[bool, str]:
        if not self.viewer.is_open:
            return False, "No PDF is open."
        if not text:
            return False, "Empty note."
        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)

        def _x(p):
            return p.x() if callable(getattr(p, "x", None)) else p.x
        def _y(p):
            return p.y() if callable(getattr(p, "y", None)) else p.y
        point = fitz.Point(_x(pt), _y(pt))
        annot = page.add_text_annot(point, text)
        try:
            annot.set_info(title="TermiPDF", content=text)
            annot.set_colors(stroke=self._parse_color(color),
                              fill=self._parse_color("#fff8b0"))
        except Exception:
            pass
        annot.update()
        return True, f"Note added on page {idx + 1}."

    # -------------------------------------------------------- signature
    def add_signature(self, rect: QRectF, png_bytes: bytes,
                       page_index: Optional[int] = None) -> Tuple[bool, str]:
        """Stamp a saved signature PNG onto the PDF at the given rect."""
        if not self.viewer.is_open:
            return False, "No PDF is open."
        idx = page_index if page_index is not None else self.viewer.current_page
        page = self.viewer.get_page(idx)
        page_rect = page.rect
        x = max(page_rect.x0, rect.x())
        y = max(page_rect.y0, rect.y())
        w = min(page_rect.x1, rect.x() + rect.width()) - x
        h = min(page_rect.y1, rect.y() + rect.height()) - y
        if w <= 0 or h <= 0:
            return False, "Empty signature rect."
        try:
            pix = fitz.Pixmap(png_bytes)
            target = fitz.Rect(x, y, x + w, y + h)
            page.insert_image(target, pixmap=pix)
            return True, f"Signature stamped on page {idx + 1}."
        except Exception as exc:
            return False, f"Signature failed: {exc}"
