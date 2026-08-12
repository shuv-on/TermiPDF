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

    # ---------------------------------------------------------- reorder
    @staticmethod
    def reorder_pages(src_path: str, new_order_1based: list,
                      out_path: Optional[str] = None) -> tuple[bool, str]:
        """Rewrite the PDF so the pages appear in ``new_order_1based``.

        ``new_order_1based`` is a permutation of 1..N where N is the
        current page count. In-place when ``out_path`` is None (same
        temp-file + atomic-replace pattern as the other in-place ops).
        Returns (ok, message).
        """
        if not os.path.isfile(src_path):
            return False, f"PDF not found: {src_path}"
        try:
            doc = fitz.open(src_path)
            n = len(doc)
            if not new_order_1based or len(new_order_1based) != n:
                doc.close()
                return False, (
                    f"new_order must contain exactly {n} page numbers "
                    f"(got {len(new_order_1based) if new_order_1based else 0})")
            # Validate: every page in 1..N must appear exactly once.
            if sorted(int(p) for p in new_order_1based) != list(range(1, n + 1)):
                doc.close()
                return False, (
                    f"new_order must be a permutation of 1..{n}; got "
                    f"{new_order_1based}")
            new_doc = fitz.open()
            for p in new_order_1based:
                new_doc.insert_pdf(doc, from_page=int(p) - 1,
                                   to_page=int(p) - 1)
            if out_path is None:
                tmp = src_path + ".tmp.pdf"
                new_doc.save(tmp, garbage=4, deflate=True)
                new_doc.close()
                doc.close()
                os.replace(tmp, src_path)
            else:
                new_doc.save(out_path, garbage=4, deflate=True)
                new_doc.close()
                doc.close()
            return True, f"Reordered {n} pages in {src_path}"
        except Exception as exc:
            return False, f"Reorder failed: {exc}"

    # -------------------------------------------------------------- move
    @staticmethod
    def move_page(src_path: str, src_page: int, target_position: int,
                  out_path: Optional[str] = None) -> tuple[bool, str]:
        """Move ``src_page`` (1-based) to ``target_position`` (1-based) and
        shift everything between to fill the gap. Simpler API than
        ``reorder_pages`` — handy for drag-and-drop where the user picks
        a single page and a single target slot.

        Returns (ok, message). No-op (and success) when src==target.
        """
        if not os.path.isfile(src_path):
            return False, f"PDF not found: {src_path}"
        try:
            doc = fitz.open(src_path)
            n = len(doc)
            if src_page < 1 or src_page > n:
                doc.close()
                return False, f"Invalid src_page {src_page} (1..{n})"
            if target_position < 1 or target_position > n:
                doc.close()
                return False, f"Invalid target_position {target_position} (1..{n})"
            if src_page == target_position:
                doc.close()
                return True, "Page already at target position."
            # Build the new order: remove src_page, then re-insert at
            # target_position (1-based after removal).
            order = list(range(1, n + 1))
            order.remove(src_page)
            # After removal the list has n-1 entries. The new position
            # is target_position in the post-removal indexing; if the
            # user dragged below their original slot we clamp.
            target_position = max(1, min(target_position, n))
            order.insert(target_position - 1, src_page)
            doc.close()
            return PDFManipulator.reorder_pages(src_path, order, out_path)
        except Exception as exc:
            return False, f"Move failed: {exc}"

    # -------------------------------------------------------------- swap
    @staticmethod
    def swap_pages(src_path: str, page_a: int, page_b: int,
                   out_path: Optional[str] = None) -> tuple[bool, str]:
        """Exchange pages ``page_a`` and ``page_b`` (1-based) in-place.

        Unlike ``move_page`` (which inserts the source at the target and
        shifts the intervening pages), swap keeps the total page count
        unchanged and preserves the relative order of every other page —
        the user asks for "swap two pages", not "shuffle the doc".

        If ``page_a == page_b`` this is a no-op (returns success) so the
        GUI can call it unconditionally on every drop.
        """
        if not os.path.isfile(src_path):
            return False, f"PDF not found: {src_path}"
        try:
            doc = fitz.open(src_path)
            n = len(doc)
            if page_a < 1 or page_a > n:
                doc.close()
                return False, f"Invalid page_a {page_a} (1..{n})"
            if page_b < 1 or page_b > n:
                doc.close()
                return False, f"Invalid page_b {page_b} (1..{n})"
            if page_a == page_b:
                doc.close()
                return True, f"Pages {page_a} and {page_b} are identical; no-op."
            # Build the new order: identity permutation, then exchange
            # the two slots. Falls back to reorder_pages for the actual
            # write so we get the same atomic-replace, in-place behavior.
            order = list(range(1, n + 1))
            order[page_a - 1], order[page_b - 1] = order[page_b - 1], order[page_a - 1]
            doc.close()
            return PDFManipulator.reorder_pages(src_path, order, out_path)
        except Exception as exc:
            return False, f"Swap failed: {exc}"