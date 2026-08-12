"""
text_selector.py — Extract text from a PDF page (born-digital or scanned).

For born-digital PDFs we use ``page.get_text("words")`` which returns a list
of word rectangles; we can then pick the word(s) that intersect a query
rect or fall near a click point.

For scanned / image-only PDFs (no text layer) we fall back to ``pytesseract``
if it's installed. If not, we return a friendly message instructing the user
to install ``pytesseract`` (and a system ``tesseract-ocr`` binary).

The router (``canvas_events._on_select_point`` / ``_on_select_rect``) uses
``extract_text_at`` for single-point picks and ``extract_text_in_rect`` for
drag-selections. Both copy the result onto the system clipboard.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import fitz

from features.pdf_viewer.viewer_engine import ViewerEngine


# A rough OCR availability probe. ``pytesseract`` is an optional dep.
try:
    import pytesseract  # type: ignore
    _OCR_AVAILABLE = True
except Exception:
    pytesseract = None  # type: ignore
    _OCR_AVAILABLE = False


def is_ocr_available() -> bool:
    """True when pytesseract + a system tesseract binary are usable."""
    if not _OCR_AVAILABLE:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _to_fitz_point(qp):
    """Coerce a QPointF / fitz.Point / (x, y) tuple into a fitz.Point."""
    if isinstance(qp, tuple):
        return fitz.Point(qp[0], qp[1])
    # PyMuPDF 1.27+ exposes .x/.y as attributes; QPointF as methods.
    x = qp.x() if callable(getattr(qp, "x", None)) else qp.x
    y = qp.y() if callable(getattr(qp, "y", None)) else qp.y
    return fitz.Point(x, y)


def _to_fitz_rect(qr):
    """Coerce a QRectF / fitz.Rect / 4-tuple into a fitz.Rect."""
    if isinstance(qr, tuple):
        return fitz.Rect(qr[0], qr[1], qr[2], qr[3])
    x = qr.x() if callable(getattr(qr, "x", None)) else qr.x
    y = qr.y() if callable(getattr(qr, "y", None)) else qr.y
    w = qr.width() if callable(getattr(qr, "width", None)) else qr.width
    h = qr.height() if callable(getattr(qr, "height", None)) else qr.height
    return fitz.Rect(x, y, x + w, y + h)


def _word_rect(w) -> fitz.Rect:
    return fitz.Rect(w[0], w[1], w[2], w[3])


def _words_in_rect(words: List, rect: fitz.Rect) -> List[Tuple[fitz.Rect, str]]:
    """Return [(rect, word), ...] for words that intersect `rect`."""
    out: List[Tuple[fitz.Rect, str]] = []
    for w in words:
        wr = _word_rect(w)
        if wr.intersects(rect):
            ix0 = max(wr.x0, rect.x0)
            ix1 = min(wr.x1, rect.x1)
            iw = max(0.0, ix1 - ix0)
            # Require at least 30% horizontal overlap so partially-overlapping
            # words from neighbouring lines aren't picked up.
            if iw / max(0.1, wr.width) >= 0.3:
                out.append((wr, w[4]))
    return out


def _group_into_lines(items: List[Tuple[fitz.Rect, str]]) -> List[str]:
    """Group words into lines based on y-coordinate overlap."""
    if not items:
        return []
    items.sort(key=lambda t: (round(t[0].y0, 1), t[0].x0))
    lines: List[str] = []
    current_line_words: List[Tuple[fitz.Rect, str]] = [items[0]]
    for it in items[1:]:
        prev = current_line_words[-1]
        prev_center = (prev[0].y0 + prev[0].y1) / 2
        cur_center = (it[0].y0 + it[0].y1) / 2
        prev_h = max(2.0, prev[0].y1 - prev[0].y0)
        if abs(cur_center - prev_center) < prev_h * 0.6:
            current_line_words.append(it)
        else:
            lines.append(" ".join(w for _, w in current_line_words))
            current_line_words = [it]
    if current_line_words:
        lines.append(" ".join(w for _, w in current_line_words))
    return lines


def extract_text_at(viewer: ViewerEngine, pt, *, radius_pt: float = 12.0
                     ) -> Tuple[bool, str]:
    """Extract text near the click point. Single word preferred, then a line."""
    if not viewer.is_open:
        return False, "No PDF is open."
    page = viewer.get_current_page()
    try:
        words = page.get_text("words")
    except Exception as exc:
        return False, f"Could not read text: {exc}"
    if not words:
        # No text layer — try OCR if available
        return _ocr_fallback(page, region=_to_fitz_point(pt), radius=radius_pt)

    point = _to_fitz_point(pt)
    best = None
    best_d = float("inf")
    for w in words:
        wr = _word_rect(w)
        cx = (wr.x0 + wr.x1) / 2
        cy = (wr.y0 + wr.y1) / 2
        d = (cx - point.x) ** 2 + (cy - point.y) ** 2
        if d < best_d:
            best_d = d
            best = (wr, w[4])

    if best is None:
        return False, "No text near the click."

    wr, wtxt = best
    cx = (wr.x0 + wr.x1) / 2
    cy = (wr.y0 + wr.y1) / 2
    if (cx - point.x) ** 2 + (cy - point.y) ** 2 <= radius_pt * radius_pt:
        return True, wtxt

    # Otherwise return the nearest whole line
    line_h = max(8.0, wr.y1 - wr.y0) * 1.4
    line_words = []
    for w in words:
        wr2 = _word_rect(w)
        c2 = (wr2.y0 + wr2.y1) / 2
        if abs(c2 - (wr.y0 + wr.y1) / 2) <= line_h:
            line_words.append((wr2, w[4]))
    line_words.sort(key=lambda t: t[0].x0)
    return True, " ".join(w for _, w in line_words)


def extract_text_in_rect(viewer: ViewerEngine, rect) -> Tuple[bool, str]:
    """Extract all words that intersect the given rectangle."""
    if not viewer.is_open:
        return False, "No PDF is open."
    page = viewer.get_current_page()
    try:
        words = page.get_text("words")
    except Exception as exc:
        return False, f"Could not read text: {exc}"

    qrect = _to_fitz_rect(rect)
    matched = _words_in_rect(words, qrect)
    if not matched:
        return _ocr_fallback(page, region=qrect, radius=None)
    lines = _group_into_lines(matched)
    return True, "\n".join(lines)


def _ocr_fallback(page: "fitz.Page", region: "fitz.Rect",
                   radius: Optional[float] = None) -> Tuple[bool, str]:
    """Run tesseract on a clip of the page when there's no text layer."""
    if not _OCR_AVAILABLE:
        return False, ("No text layer on this page. To extract text from "
                        "scanned PDFs, install `pytesseract` and the "
                        "`tesseract-ocr` system package.")
    if radius is not None:
        cx, cy = region.x, region.y
        clip = fitz.Rect(cx - radius * 3, cy - radius * 3,
                          cx + radius * 3, cy + radius * 3)
    else:
        clip = fitz.Rect(region.x0, region.y0, region.x1, region.y1)

    try:
        matrix = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img).strip()
    except Exception as exc:
        return False, f"OCR failed: {exc}"

    if not text:
        return False, "OCR ran but found no text in that region."
    return True, text