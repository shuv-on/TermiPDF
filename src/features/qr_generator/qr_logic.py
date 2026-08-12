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
    def generate_image(text: str, box_size: int = 10, border: int = 6,
                       fill: str = "black", back: str = "white") -> Image.Image:
        """Render a QR PNG.

        ``border`` is the quiet-zone width in MODULES (not pixels). The
        QR spec requires 4 modules of quiet zone; we use 6 to give the
        scanner plenty of clean white around the corner finder patterns
        — phones with mediocre cameras tend to miss the corner finders
        when the quiet zone is right at the spec minimum. The user
        reported "main corner is not finding on the frame of qr" — that
        symptom is the scanner failing to detect the three corner
        finder patterns because they sit too close to the image edge.
        """
        qr = qrcode.QRCode(
            version=None,
            # Q (25% recovery) is much more forgiving when scanning from
            # a phone camera at arm's length — modules can be partially
            # obscured by glare / angle and the code still decodes.
            error_correction=qrcode.constants.ERROR_CORRECT_Q,
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


# ----------------------------------------------------------------- API
def render_png(text: str, size_pt: int = 900) -> Tuple[bytes, dict]:
    """Render a QR code for ``text`` as PNG bytes — no PDF involved.

    Used by the floating "share as QR" dialog so the QR can be shown in
    a popup without being stamped onto the page (per MS Edge's UX).
    The default ``size_pt=900`` yields a ~900 px QR with Q-level error
    correction — comfortably scannable by any modern phone camera from
    ~30 cm away (and much more forgiving than the previous 600 px
    M-level build, which the user reported was hard to scan).

    QR codes max out at version 40 (~2953 byte capacity with Q
    error-correction). When the input text exceeds that, we return a
    QR that encodes a short "too long" message plus a JSON manifest
    (or we return a clear error). To keep the UX simple we cap the
    encoded payload to QRv40 capacity and emit a graceful failure if
    even that overflows.

    Returns (png_bytes, meta_dict) where meta contains box_size and
    ``truncated`` flag (True when the input was too long and the QR
    encodes a shortened message).
    """
    box_size = max(6, min(20, size_pt // 50))
    # Capacity check — QRv40 at Q-error holds ~2361 alphanumeric or
    # ~1273 byte (binary) bytes. Anything longer overflows and the
    # qrcode lib raises ValueError. We truncate to a safe limit and
    # flag it in the metadata so the caller can warn the user.
    MAX_QR_BYTES = 1200   # safely under the Q-error limit
    truncated = False
    encoded = text
    if len(text.encode("utf-8")) > MAX_QR_BYTES:
        # Truncate on a UTF-8 char boundary.
        b = text.encode("utf-8")[:MAX_QR_BYTES]
        # Walk back to avoid splitting a multibyte char.
        while b and (b[-1] & 0xC0) == 0x80:
            b = b[:-1]
        encoded = b.decode("utf-8", errors="ignore")
        truncated = True
        # Long QRs are huge modules × box_size — shrink the box so
        # the resulting PNG is still scannable at a sensible size.
        box_size = 6
    try:
        img = QRLogic.generate_image(encoded, box_size=box_size, border=6)
    except Exception:
        # Defensive: if even the capped payload overflows (shouldn't
        # happen given MAX_QR_BYTES), fall back to a one-line notice
        # so the dialog still has *something* to show.
        encoded = "[Text too long for QR — copy text manually]"
        img = QRLogic.generate_image(encoded, box_size=box_size, border=6)
        truncated = True
    return _img_to_bytes(img), {
        "box_size": box_size,
        "width": img.width,
        "height": img.height,
        "truncated": truncated,
        "encoded_length": len(encoded),
        "original_length": len(text),
    }
