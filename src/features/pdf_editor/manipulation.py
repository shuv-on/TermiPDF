"""
manipulation.py — Page-level PDF operations: extract, merge, delete, rotate.
"""
from __future__ import annotations

import os
from typing import Optional

import fitz


class PDFManipulator:
    """Stateless helpers for page-level PDF manipulation.

    These functions do NOT mutate the open document; they create new files
    or return fresh fitz.Document instances.
    """

    # --------------------------------------------------------- extract
    @staticmethod
    def extract_pages(src_path: str, from_page: int, to_page: int,
                      out_path: str) -> tuple[bool, str]:
        """Extract pages [from_page..to_page] (1-based, inclusive) into a new PDF."""
        if not os.path.isfile(src_path):
            return False, f"Source PDF not found: {src_path}"
        try:
            doc = fitz.open(src_path)
            n = len(doc)
            if from_page < 1 or to_page > n or from_page > to_page:
                doc.close()
                return False, f"Invalid page range 1..{n}: got {from_page}..{to_page}"
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=from_page - 1, to_page=to_page - 1)
            new_doc.save(out_path, garbage=4, deflate=True)
            new_doc.close()
            doc.close()
            return True, f"Extracted pages {from_page}-{to_page} → {out_path}"
        except Exception as exc:
            return False, f"Extract failed: {exc}"

    # ------------------------------------------------------------- merge
    @staticmethod
    def merge_pdfs(files: list[str], out_path: str) -> tuple[bool, str]:
        """Merge two or more PDFs in order into out_path."""
        if len(files) < 2:
            return False, "merge requires at least 2 input PDFs."
        for f in files:
            if not os.path.isfile(f):
                return False, f"File not found: {f}"
        try:
            result = fitz.open()
            for fp in files:
                src = fitz.open(fp)
                result.insert_pdf(src)
                src.close()
            result.save(out_path, garbage=4, deflate=True)
            result.close()
            return True, f"Merged {len(files)} PDFs → {out_path}"
        except Exception as exc:
            return False, f"Merge failed: {exc}"

    # ------------------------------------------------------------- delete
    @staticmethod
    def delete_page(src_path: str, page_num: int, out_path: Optional[str] = None
                    ) -> tuple[bool, str]:
        """Remove a single page from a PDF and save it (in-place if out_path is None).

        In-place writes that modify the page tree cannot use incremental
        saves in PyMuPDF, so we write to a temp file then move it back.
        """
        if not os.path.isfile(src_path):
            return False, f"PDF not found: {src_path}"
        try:
            doc = fitz.open(src_path)
            n = len(doc)
            if page_num < 1 or page_num > n:
                doc.close()
                return False, f"Invalid page {page_num} (1..{n})"
            if n <= 1:
                doc.close()
                return False, "Cannot delete the only remaining page."
            doc.delete_page(page_num - 1)
            if out_path is None:
                # In-place: write to tmp then atomically replace.
                tmp = src_path + ".tmp.pdf"
                doc.save(tmp, garbage=4, deflate=True)
                doc.close()
                os.replace(tmp, src_path)
            else:
                doc.save(out_path, garbage=4, deflate=True)
                doc.close()
            return True, f"Deleted page {page_num} from {src_path}"
        except Exception as exc:
            return False, f"Delete failed: {exc}"

    # ------------------------------------------------------------- rotate
    @staticmethod
    def rotate_page(src_path: str, page_num: int, angle: int,
                    out_path: Optional[str] = None) -> tuple[bool, str]:
        """Rotate a page by 90/180/270 degrees (in-place when out_path is None)."""
        if not os.path.isfile(src_path):
            return False, f"PDF not found: {src_path}"
        if angle not in (90, 180, 270, -90, -180, -270):
            return False, "Angle must be 90, 180, or 270."
        try:
            doc = fitz.open(src_path)
            n = len(doc)
            if page_num < 1 or page_num > n:
                doc.close()
                return False, f"Invalid page {page_num} (1..{n})"
            page = doc.load_page(page_num - 1)
            page.set_rotation((page.rotation + angle) % 360)
            if out_path is None:
                tmp = src_path + ".tmp.pdf"
                doc.save(tmp, garbage=4, deflate=True)
                doc.close()
                os.replace(tmp, src_path)
            else:
                doc.save(out_path, garbage=4, deflate=True)
                doc.close()
            return True, f"Rotated page {page_num} by {angle}°"
        except Exception as exc:
            return False, f"Rotate failed: {exc}"