"""
undo_stack.py — Per-document annotation + page-level undo/redo.

Each action is represented as a (action_type, payload) triple. The stack
stores both the action and the matching inverse so that ``undo()`` can
rewind without us having to record every byte that was written.

Storage:
* annotation actions (``page_index`` 0-based, used to find the right page):
  - ``"add"`` payload is the JSON-like dict returned by ``Page.add_*_annot``
    (``info``, ``rect``, ``vertices``). On undo we delete by walking the page
    annotations and matching these fields.
  - ``"delete"`` payload is the annotation snapshot we removed. On undo we
    re-insert it (kind + rect + vertices + colors).
  - ``"edit_text"`` payload is the new text + rect; the inverse restores the
    original snapshot.
* page-level actions (``page_index`` unused; ``forward`` / ``inverse`` carry
  document-level state):
  - ``"page_op"`` payload has ``"kind"`` in {``"reorder"``, ``"swap"``,
    ``"move"``, ``"rotate"``, ``"delete"``}:
      * ``reorder | swap | move``: ``"order"`` is the 1-based page order to
        apply on undo/redo. ``inverse.order`` is the pre-op order, so undo
        restores it; ``forward.order`` is the post-op order so redo re-applies.
      * ``rotate``: ``"page"`` (1-based) + ``"angle"``. ``inverse.angle`` is
        the negative of the forward angle so undo rotates back.
      * ``delete``: ``"page"`` (1-based slot where the page used to live) +
        ``"cache_pdf"`` (path to a sidecar one-page PDF holding the deleted
        page's content — needed to restore on undo). Redo simply re-deletes
        that page.

This module intentionally avoids touching the undo stack from inside
``AnnotationEngine`` or ``PDFManipulator``; ``main_window`` is the
orchestrator that calls ``push_added(...)`` / ``push_deleted(...)`` /
``push_page_op(...)`` with the matching snapshot.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import fitz

from features.pdf_viewer.viewer_engine import ViewerEngine


MAX_DEPTH = 50


@dataclass
class UndoEntry:
    action: str                  # "add" | "delete" | "edit_text" | "page_op"
    page_index: int              # 0-based (unused for "page_op")
    forward: Dict[str, Any] = field(default_factory=dict)
    inverse: Dict[str, Any] = field(default_factory=dict)


class UndoStack:
    """Bounded undo/redo stack with snapshot-based inversion."""

    def __init__(self, viewer: ViewerEngine):
        self.viewer = viewer
        self._undo: List[UndoEntry] = []
        self._redo: List[UndoEntry] = []
        # "Dirty" flag — True when there are unsaved annotation changes
        # in the current document (i.e. ``_undo`` non-empty and not the
        # default empty stack). The window title shows a star when this
        # is set; ``reset()`` and ``save()`` clear it.
        self._dirty: bool = False

    def set_viewer(self, viewer: ViewerEngine) -> None:
        """Swap the bound viewer engine (used when tabbing between PDFs).

        The undo stack is reset on swap so each document keeps its own
        history.
        """
        self.viewer = viewer
        self.reset()

    # ------------------------------------------------------------- public
    def reset(self):
        self._undo.clear()
        self._redo.clear()
        self._dirty = False

    def mark_clean(self):
        """Called by main_window after a successful save."""
        self._dirty = False

    def is_dirty(self) -> bool:
        return self._dirty

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, entry: UndoEntry) -> None:
        self._undo.append(entry)
        if len(self._undo) > MAX_DEPTH:
            self._undo.pop(0)
        # Pushing a new action invalidates the redo path AND marks the
        # document dirty (so the window title shows a `*`).
        self._redo.clear()
        self._dirty = True

    def undo(self) -> Tuple[bool, str]:
        if not self._undo:
            return False, "Nothing to undo."
        if not self.viewer.is_open:
            return False, "No PDF is open."
        entry = self._undo.pop()
        ok, msg = self._apply_inverse(entry)
        if ok:
            self._redo.append(entry)
        else:
            # Put the entry back if we couldn't undo it cleanly
            self._undo.append(entry)
            return False, f"Undo failed: {msg}"
        # Going back through the stack may have emptied it — clear
        # dirty when there's nothing to redo AND nothing left to undo.
        self._dirty = bool(self._undo)
        return True, f"Undid {entry.action}."

    def redo(self) -> Tuple[bool, str]:
        if not self._redo:
            return False, "Nothing to redo."
        if not self.viewer.is_open:
            return False, "No PDF is open."
        entry = self._redo.pop()
        ok, msg = self._apply_forward(entry)
        if ok:
            self._undo.append(entry)
        else:
            self._redo.append(entry)
            return False, f"Redo failed: {msg}"
        # Re-applying a redo'd action returns the document to dirty.
        self._dirty = True
        return True, f"Redid {entry.action}."

    # ---------------------------------------------------- snapshot helpers
    def snapshot_annot(self, annot: fitz.Annot) -> Dict[str, Any]:
        """Capture the bits needed to re-create an annotation later."""
        try:
            verts = annot.vertices
        except Exception:
            verts = []
        try:
            colors = annot.colors
        except Exception:
            colors = {}
        try:
            info = annot.info
        except Exception:
            info = {}
        return {
            "type": int(annot.type[0]),
            "rect": [annot.rect.x0, annot.rect.y0,
                     annot.rect.x1, annot.rect.y1],
            "vertices": [list(v) for v in (verts or [])],
            "colors": colors,
            "info": dict(info) if info else {},
        }

    def push_added(self, page_index: int, annot: fitz.Annot) -> None:
        snap = self.snapshot_annot(annot)
        # Inverse = delete this annotation by matching snapshot fields.
        entry = UndoEntry(action="add", page_index=page_index,
                          forward=snap, inverse={"snapshot": snap})
        self.push(entry)

    def push_deleted(self, page_index: int, annot_or_snap) -> None:
        """Record a delete. Accepts either a live ``fitz.Annot`` (we snapshot
        it now) or a pre-computed snapshot dict (used when the annotation is
        about to be deleted and is no longer safe to read)."""
        if isinstance(annot_or_snap, dict):
            snap = annot_or_snap
        else:
            snap = self.snapshot_annot(annot_or_snap)
        entry = UndoEntry(action="delete", page_index=page_index,
                          forward={"snapshot": snap}, inverse=snap)
        self.push(entry)

    def push_edit_text(self, page_index: int, old_snap: Dict[str, Any],
                        new_snap: Dict[str, Any]) -> None:
        entry = UndoEntry(action="edit_text", page_index=page_index,
                          forward=new_snap, inverse=old_snap)
        self.push(entry)

    # ----------------------------------------------------- page-level ops
    # Cache directory for one-page PDFs holding deleted-page content
    # (needed so delete can be undone). Lives next to the source PDF so
    # cleanup is straightforward; key is the source PDF's absolute path.
    _page_cache_dirs: Dict[str, str] = {}

    @classmethod
    def _page_cache_dir(cls, src_path: str) -> str:
        """Return (and lazily create) a sidecar cache directory for
        one-page snapshots of deleted pages."""
        d = cls._page_cache_dirs.get(src_path)
        if d is None:
            base = os.path.dirname(os.path.abspath(src_path))
            d = os.path.join(base, ".undo_cache")
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                # Fall back to a tempfile-private dir if the user's
                # directory is read-only.
                d = tempfile.mkdtemp(prefix="termipdf_undo_cache_")
            cls._page_cache_dirs[src_path] = d
        return d

    def push_page_op(self, kind: str, *,
                     page_a: Optional[int] = None,
                     page_b: Optional[int] = None,
                     src_page: Optional[int] = None,
                     target_slot: Optional[int] = None,
                     page: Optional[int] = None,
                     angle: Optional[int] = None,
                     deleted_page_pdf: Optional[str] = None) -> bool:
        """Push a page-level undo entry.

        ``kind`` selects the inverse/forward strategy:
            * ``"swap"`` — provide ``page_a`` and ``page_b`` (1-based).
              Undo re-swaps; redo re-swaps.
            * ``"move"`` — provide ``src_page`` and ``target_slot``
              (1-based, post-removal). Undo re-moves; redo re-moves.
            * ``"rotate"`` — provide ``page`` (1-based) and ``angle``.
              Undo rotates by ``-angle``.
            * ``"delete"`` — provide ``page`` (1-based slot the deleted
              page used to occupy) and ``deleted_page_pdf`` (path to a
              one-page PDF holding the deleted page's content). Undo
              re-merges that PDF at ``page``.

        Returns True on success. Returns False (without pushing) if the
        inputs don't make sense for ``kind``.
        """
        if not self.viewer or not self.viewer.path:
            return False
        kind = (kind or "").lower()
        if kind == "swap":
            if page_a is None or page_b is None:
                return False
            entry = UndoEntry(
                action="page_op", page_index=0,
                forward={"kind": "swap", "page_a": int(page_a),
                         "page_b": int(page_b)},
                inverse={"kind": "swap", "page_a": int(page_a),
                         "page_b": int(page_b)},
            )
        elif kind == "move":
            if src_page is None or target_slot is None:
                return False
            # move_page(src, tgt) is NOT self-inverse: redo from a
            # restored pre-state needs the same call (src, tgt); undo
            # from the post-state needs (current_post_slot, src). We
            # store both pairs so the apply function picks the right
            # one based on ``forward``.
            entry = UndoEntry(
                action="page_op", page_index=0,
                forward={"kind": "move",
                         "do_src": int(src_page),
                         "do_tgt": int(target_slot)},
                inverse={"kind": "move",
                         # In the post-state, src_page's content is at
                         # ``target_slot`` (move_page puts the moved
                         # page there). To undo, move from
                         # target_slot back to src_page.
                         "do_src": int(target_slot),
                         "do_tgt": int(src_page)},
            )
        elif kind == "rotate":
            if page is None or angle is None:
                return False
            entry = UndoEntry(
                action="page_op", page_index=0,
                forward={"kind": "rotate", "page": int(page),
                         "angle": int(angle)},
                inverse={"kind": "rotate", "page": int(page),
                         "angle": -int(angle)},
            )
        elif kind == "delete":
            if page is None or not deleted_page_pdf:
                return False
            entry = UndoEntry(
                action="page_op", page_index=0,
                forward={"kind": "delete", "page": int(page)},
                inverse={"kind": "delete", "page": int(page),
                         "cache_pdf": str(deleted_page_pdf)},
            )
        else:
            return False
        self.push(entry)
        return True

    @staticmethod
    def cache_deleted_page(src_path: str, page_1based: int) -> Tuple[bool, str, Optional[str]]:
        """Extract the given page from ``src_path`` into a sidecar
        one-page PDF and return its path. Used to build the payload for
        ``push_page_op(kind="delete", ...)``.

        Returns ``(ok, msg, cache_path)``. ``cache_path`` is ``None`` on
        failure.
        """
        from features.pdf_editor.manipulation import PDFManipulator
        cache_dir = UndoStack._page_cache_dir(src_path)
        # Stable name per (path, page) — consecutive deletes of the
        # same slot overwrite, keeping the cache small.
        out_path = os.path.join(cache_dir, f"page_{page_1based}.pdf")
        ok, msg = PDFManipulator.extract_pages(src_path, page_1based,
                                               page_1based, out_path)
        if not ok:
            return False, msg, None
        return True, msg, out_path

    # ---------------------------------------------------- apply inverse/fwd
    def _apply_inverse(self, entry: UndoEntry) -> Tuple[bool, str]:
        if entry.action == "page_op":
            return self._apply_page_op(entry.inverse, forward=False)
        page = self.viewer.get_page(entry.page_index)
        if entry.action == "add":
            # Forward was "add" — remove the matching annotation.
            self._delete_matching(page, entry.inverse.get("snapshot", {}))
            return True, "ok"
        if entry.action == "delete":
            return self._recreate_annot(page, entry.inverse)
        if entry.action == "edit_text":
            # Restore the old annotation by deleting the new and re-adding old.
            self._delete_matching(page, entry.forward)
            return self._recreate_annot(page, entry.inverse)
        return False, f"Unknown action: {entry.action}"

    def _apply_forward(self, entry: UndoEntry) -> Tuple[bool, str]:
        if entry.action == "page_op":
            return self._apply_page_op(entry.forward, forward=True)
        page = self.viewer.get_page(entry.page_index)
        if entry.action == "add":
            return self._recreate_annot(page, entry.forward)
        if entry.action == "delete":
            self._delete_matching(page, entry.forward.get("snapshot", {}))
            return True, "ok"
        if entry.action == "edit_text":
            self._delete_matching(page, entry.inverse)
            return self._recreate_annot(page, entry.forward)
        return False, f"Unknown action: {entry.action}"

    def _apply_page_op(self, payload: Dict[str, Any], *,
                        forward: bool) -> Tuple[bool, str]:
        """Dispatch a single page-op payload to the right PDFManipulator
        call. Used by both ``_apply_inverse`` (with ``forward=False``)
        and ``_apply_forward`` (with ``forward=True``).

        After the on-disk write the engine is reloaded so the viewer
        reflects the change immediately.
        """
        from features.pdf_editor.manipulation import PDFManipulator
        path = self.viewer.path
        kind = payload.get("kind")
        try:
            if kind == "swap":
                # swap is its own inverse — just re-swap the same pair.
                a = payload.get("page_a")
                b = payload.get("page_b")
                if a is None or b is None or a == b:
                    return False, "page_op swap: missing or equal pages"
                ok, msg = PDFManipulator.swap_pages(path, int(a), int(b))
                if not ok:
                    return False, msg
            elif kind == "move":
                # ``forward`` payload carries ``do_src`` + ``do_tgt``
                # representing the move to perform right now
                # (different for undo vs redo).
                src = payload.get("do_src")
                tgt = payload.get("do_tgt")
                if src is None or tgt is None:
                    return False, "page_op move: missing do_src/do_tgt"
                if int(src) == int(tgt):
                    # No-op (e.g. undoing a move that landed back on
                    # itself — shouldn't happen but be safe).
                    pass
                else:
                    ok, msg = PDFManipulator.move_page(
                        path, int(src), int(tgt))
                    if not ok:
                        return False, msg
            elif kind == "rotate":
                page = payload.get("page")
                angle = payload.get("angle")
                if page is None or angle is None:
                    return False, "page_op rotate: missing page/angle"
                ok, msg = PDFManipulator.rotate_page(path, int(page),
                                                     int(angle))
                if not ok:
                    return False, msg
            elif kind == "delete":
                page = payload.get("page")
                if page is None:
                    return False, "page_op delete: missing page"
                if forward:
                    # Redo a delete — just call delete_page again.
                    ok, msg = PDFManipulator.delete_page(path, int(page))
                    if not ok:
                        return False, msg
                else:
                    # Undo a delete — re-merge the cached one-page PDF
                    # back into the document. The cached PDF holds the
                    # content of the deleted page; we merge it as a new
                    # last page, then call move_page to slide it back
                    # to its original slot.
                    cache_pdf = payload.get("cache_pdf")
                    if not cache_pdf or not os.path.isfile(cache_pdf):
                        return False, "page_op delete: cache PDF missing"
                    tmp = path + ".undodelete.tmp.pdf"
                    ok, msg = PDFManipulator.merge_pdfs([path, cache_pdf],
                                                        tmp)
                    if not ok:
                        return False, msg
                    try:
                        os.replace(tmp, path)
                    except Exception as exc:
                        return False, f"Replace failed: {exc}"
                    # After merge the restored page is the last page
                    # (slot = n). Use move_page to bring it back to
                    # ``page`` (1-based).
                    n = len(fitz.open(path))
                    current_slot = n
                    target = max(1, min(int(page), n))
                    if current_slot != target:
                        ok, msg = PDFManipulator.move_page(
                            path, current_slot, target)
                        if not ok:
                            return False, msg
            else:
                return False, f"Unknown page_op kind: {kind!r}"
        except Exception as exc:
            return False, f"page_op {kind} failed: {exc}"

        # Reload the engine so the viewer + Pages Manager see the new
        # state. Swallow reload errors — the on-disk write already
        # succeeded, the user just won't see the change until they
        # close and reopen.
        try:
            if hasattr(self.viewer, "reload_from_disk"):
                self.viewer.reload_from_disk()
        except Exception:
            pass
        return True, "ok"

    # ----------------------------------------------------------- helpers
    def _delete_matching(self, page: fitz.Page, snap: Dict[str, Any]) -> bool:
        target_rect = snap.get("rect")
        if not target_rect:
            return False
        target = fitz.Rect(*target_rect)
        for annot in list(page.annots() or []):
            try:
                # Match by rect overlap (within 0.5 pt) and annotation type.
                if int(annot.type[0]) == int(snap.get("type", -999)):
                    if abs(annot.rect.x0 - target.x0) < 0.5 and \
                       abs(annot.rect.y0 - target.y0) < 0.5 and \
                       abs(annot.rect.x1 - target.x1) < 0.5 and \
                       abs(annot.rect.y1 - target.y1) < 0.5:
                        page.delete_annot(annot)
                        return True
            except Exception:
                continue
        return False

    def _recreate_annot(self, page: fitz.Page, snap: Dict[str, Any]) -> Tuple[bool, str]:
        atype = snap.get("type")
        rect_list = snap.get("rect")
        if atype is None or not rect_list:
            return False, "Snapshot missing type or rect."
        r = fitz.Rect(*rect_list)
        try:
            if atype == fitz.PDF_ANNOT_INK:
                verts = snap.get("vertices") or []
                # Vertices is list of lists; each inner list is one stroke
                strokes = [[tuple(p) for p in stroke] for stroke in verts] if verts else []
                if not strokes and rect_list:
                    # Fallback: synthesize a single tiny stroke at the rect
                    strokes = [[(r.x0, r.y0), (r.x1, r.y1)]]
                annot = page.add_ink_annot(strokes)
            elif atype == fitz.PDF_ANNOT_HIGHLIGHT:
                annot = page.add_highlight_annot(r)
            elif atype == fitz.PDF_ANNOT_SQUIGGLY:
                annot = page.add_squiggly_annot(r)
            elif atype == fitz.PDF_ANNOT_UNDERLINE:
                annot = page.add_underline_annot(r)
            elif atype == fitz.PDF_ANNOT_STRIKE_OUT:
                annot = page.add_strikeout_annot(r)
            elif atype == fitz.PDF_ANNOT_SQUARE:
                annot = page.add_rect_annot(r)
            elif atype == fitz.PDF_ANNOT_CIRCLE:
                annot = page.add_circle_annot(r)
            elif atype == fitz.PDF_ANNOT_LINE:
                annot = page.add_line_annot(
                    fitz.Point(r.x0, r.y0), fitz.Point(r.x1, r.y1))
            elif atype == fitz.PDF_ANNOT_TEXT:
                annot = page.add_text_annot(fitz.Point(r.x0, r.y0),
                                            snap.get("info", {}).get("content", ""))
            elif atype == fitz.PDF_ANNOT_FREETEXT:
                annot = page.add_freetext_annot(
                    r, snap.get("info", {}).get("content", ""),
                    fontsize=10)
            else:
                return False, f"Unsupported annotation type {atype}."
        except Exception as exc:
            return False, f"Could not recreate annotation: {exc}"

        # Restore colors if available
        colors = snap.get("colors") or {}
        try:
            if "stroke" in colors and colors["stroke"]:
                annot.set_colors(stroke=colors["stroke"])
            if "fill" in colors and colors["fill"]:
                annot.set_colors(fill=colors["fill"])
        except Exception:
            pass

        # Restore info (title / content) if available
        info = snap.get("info") or {}
        try:
            if info:
                annot.set_info(info)
        except Exception:
            pass

        # Restore line ends (arrows) if applicable
        if atype == fitz.PDF_ANNOT_LINE:
            try:
                le = snap.get("line_ends")
                if le:
                    annot.set_line_ends(le[0], le[1])
            except Exception:
                pass

        try:
            annot.update()
        except Exception:
            pass
        return True, "ok"