"""
undo_stack.py — Per-document annotation undo/redo.

Each annotation action is represented as a (page_index, action_type, payload)
triple. The stack stores both the action and the matching inverse so that
``undo()`` can rewind without us having to record every byte that was written.

Storage:
* action types we care about:
  - ``"add"`` payload is the JSON-like dict returned by ``Page.add_*_annot``
    (``info``, ``rect``, ``vertices``). On undo we delete by walking the page
    annotations and matching these fields.
  - ``"delete"`` payload is the annotation snapshot we removed. On undo we
    re-insert it (kind + rect + vertices + colors).
  - ``"edit_text"`` payload is the new text + rect; the inverse restores the
    original snapshot.

This module intentionally avoids touching the undo stack from inside
``AnnotationEngine``; ``main_window`` is the orchestrator that calls
``push_add(...)`` / ``push_delete(...)`` etc. with the matching snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import fitz

from features.pdf_viewer.viewer_engine import ViewerEngine


MAX_DEPTH = 50


@dataclass
class UndoEntry:
    action: str                  # "add" | "delete" | "edit_text"
    page_index: int              # 0-based
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

    # ---------------------------------------------------- apply inverse/fwd
    def _apply_inverse(self, entry: UndoEntry) -> Tuple[bool, str]:
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