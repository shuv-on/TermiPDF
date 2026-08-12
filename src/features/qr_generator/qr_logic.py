"""
qr_logic.py — Generate QR code images and stamp them onto a PDF page.
"""
from __future__ import annotations

import io
import os
from typing import Optional, Tuple

import fitz
import qrcode
from PIL import Image

from features.pdf_viewer.viewer_engine import ViewerEngine


class QRLogic:
    """QR generation + stamping helpers."""

    def __init__(self, viewer: ViewerEngine):
        self.viewer = viewer

    # --------------------------------------------------------- generate
    @staticmethod
    def generate_image(text: str, box_size: int = 6, border: int = 2,
                       fill: str = "black", back: str = "white") -> Image.Image:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box_size,
            border=border,
        )
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color=fill, back_color=back).convert("RGB")
        return img

    # ------------------------------------------------------------- stamp
    def stamp_on_page(
        self,
        text: str,
        page: Optional[int] = None,
        x: float = 50.0,
        y: float = 50.0,
        size_pt: float = 100.0,
    ) -> Tuple[bool, str]:
        """Embed a QR image into the current (or given) page of the open PDF."""
        if not self.viewer.is_open:
            return False, "No PDF is open."
        idx = (page - 1) if page else self.viewer.current_page
        if idx < 0 or idx >= self.viewer.page_count:
            return False, f"Invalid page {idx + 1}"

        page_obj = self.viewer.get_page(idx)

        # Render QR at high resolution; insert as a fitted rectangle in PDF units.
        img = self.generate_image(text)
        rect = fitz.Rect(x, y, x + size_pt, y + size_pt)

        try:
            page_obj.insert_image(rect, pixmap=fitz.Pixmap(_img_to_bytes(img)))
            return True, (
                f"QR stamped on page {idx + 1} at ({x:.1f}, {y:.1f}) "
                f"size {size_pt:.0f}pt."
            )
        except Exception as exc:
            # Fallback: insert via a temporary file (works on more readers)
            tmp = _img_to_tempfile(img)
            try:
                page_obj.insert_image(rect, filename=tmp)
                return True, (
                    f"QR stamped on page {idx + 1} at ({x:.1f}, {y:.1f}) "
                    f"size {size_pt:.0f}pt. (fallback: {exc})"
                )
            except Exception as exc2:
                return False, f"QR stamping failed: {exc2}"
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass


# ----------------------------------------------------------------- helpers
def _img_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _img_to_tempfile(img: Image.Image) -> str:
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(path, format="PNG")
    return path
