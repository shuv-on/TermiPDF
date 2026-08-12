"""
text_editor.py — Insert Unicode text into a PDF (with full Bangla support).

Implementation notes
--------------------
PyMuPDF's `insert_text` and `add_freetext_annot` honor a TrueType font (TTF)
that has glyph coverage for the target script. We accept --font <path> so the
user can point at Kalpurush.ttf, NotoSansBengali.ttf, etc. If no font is
supplied we auto-detect the first TTF in src/shared/assets/.

Bangla rendering pitfall: PyMuPDF uses HarfBuzz for shaping when given a TTF
that has GSUB tables, so joining letters are drawn correctly. We use
`insert_textbox` for multi-line text support and to auto-wrap.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

import fitz

from features.pdf_viewer.viewer_engine import ViewerEngine
from shared.utils.path_solver import font_path, asset_path


# Map of bundled font filenames (we ship these by default).
_BUNDLED_FONTS = [
    "Kalpurush.ttf",
    "NotoSansBengali.ttf",
    "NotoSans-Regular.ttf",
    "DejaVuSans.ttf",
]


def _resolve_font_path(explicit: Optional[str]) -> Optional[str]:
    """Return an absolute path to a usable TTF, or None."""
    if explicit:
        if os.path.isfile(explicit):
            return explicit
        # Maybe user gave just the filename — try shared/assets/
        bundled = asset_path(explicit)
        if os.path.isfile(bundled):
            return bundled
        return None
    for fname in _BUNDLED_FONTS:
        p = font_path(fname)
        if os.path.isfile(p):
            return p
    return None


class TextEditor:
    """Insert styled Unicode text into a PDF."""

    def __init__(self, viewer: ViewerEngine):
        self.viewer = viewer
        self._cached_font: Optional[str] = None

    # ------------------------------------------------------------- font
    def available_font(self, explicit: Optional[str] = None) -> Optional[str]:
        if explicit:
            return _resolve_font_path(explicit)
        if self._cached_font and os.path.isfile(self._cached_font):
            return self._cached_font
        p = _resolve_font_path(None)
        self._cached_font = p
        return p

    # ----------------------------------------------------------- insert
    def add_text(
        self,
        text: str,
        page: int,
        x: float,
        y: float,
        font_size: float = 14.0,
        color: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        fontname: str = "helv",
        font_file: Optional[str] = None,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Insert text at the given PDF coordinates on the given 1-based page.

        If a TTF font file is available the text is rendered with that font,
        otherwise PyMuPDF falls back to its built-in Helvetica which may
        render Bangla as boxes.
        """
        if not self.viewer.is_open:
            return False, "No PDF is open."
        if not text:
            return False, "Empty text."
        if page < 1 or page > self.viewer.page_count:
            return False, f"Invalid page {page} (1..{self.viewer.page_count})."

        page_obj = self.viewer.get_page(page - 1)
        page_rect = page_obj.rect

        # Resolve font -----------------------------------------------------
        resolved_fontfile = self.available_font(font_file)
        fontname_to_use = fontname
        if resolved_fontfile:
            # Embed TTF into the page
            try:
                page_obj.insert_font(
                    fontname="termipdf-ttf",
                    fontfile=resolved_fontfile,
                )
                fontname_to_use = "termipdf-ttf"
            except Exception:
                # Fall back to helv if TTF embedding failed
                fontname_to_use = "helv"
        else:
            fontname_to_use = "helv"

        # Color string -----------------------------------------------------
        hex_color = "#%02x%02x%02x" % (
            int(color[0] * 255),
            int(color[1] * 255),
            int(color[2] * 255),
        )

        # If width/height given, use insert_textbox (multi-line capable)
        if width and height:
            rect = fitz.Rect(x, y, x + width, y + height)
            try:
                rc = page_obj.insert_textbox(
                    rect,
                    text,
                    fontname=fontname_to_use,
                    fontfile=resolved_fontfile,
                    fontsize=font_size,
                    color=fitz.utils.getColor(hex_color),
                    align=fitz.TEXT_ALIGN_LEFT,
                )
                if rc < 0:
                    return False, "Text did not fit in the provided box."
                return True, (
                    f"Inserted text on page {page} using "
                    f"{'TTF font' if resolved_fontfile else 'default font'}."
                )
            except Exception as exc:
                return False, f"insert_textbox failed: {exc}"

        # Otherwise insert a single line
        try:
            rc = page_obj.insert_text(
                (x, y),
                text,
                fontname=fontname_to_use,
                fontfile=resolved_fontfile,
                fontsize=font_size,
                color=fitz.utils.getColor(hex_color),
            )
            if rc < 0:
                return False, "Failed to insert text (font glyphs missing?)."
            return True, (
                f"Inserted text on page {page} using "
                f"{'TTF font' if resolved_fontfile else 'default font'}."
            )
        except Exception as exc:
            return False, f"insert_text failed: {exc}"
