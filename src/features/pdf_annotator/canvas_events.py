"""
canvas_events.py — Glue between PDFViewerUI and AnnotationEngine.

This module wires the viewer canvas's mouse callbacks (which fire with Qt
events) into the annotation engine (which operates on PDF coordinates).

The wiring is implemented as a small class that:
1) Hooks into a PDFViewerUI's commit callbacks (set via set_callbacks).
2) Forwards stroke / rect / hit-test commands to the AnnotationEngine.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF

from features.pdf_annotator.annotation_engine import AnnotationEngine
from features.pdf_viewer.viewer_engine import ViewerEngine
from features.pdf_viewer.viewer_ui import PDFViewerUI, CanvasStroke


class CanvasEventRouter:
    """Bridges viewer canvas ↔ annotation engine."""

    def __init__(self, viewer: ViewerEngine, annot: AnnotationEngine, ui: PDFViewerUI):
        self.viewer = viewer
        self.annot = annot
        self.ui = ui

        # Wire UI callbacks
        self.ui._commit_stroke = self._on_commit_stroke
        self.ui._commit_highlight_rect = self._on_commit_highlight_rect
        self.ui._request_erase_at = self._on_erase_at

    # ------------------------------------------------------- callbacks
    def _on_commit_stroke(self, stroke: CanvasStroke):
        ok, msg = self.annot.add_ink_stroke(stroke)
        if ok:
            # The canvas redraws from the new PDF state on next refresh
            self.ui.surface.clear_live_strokes()
            self.ui.refresh()
            self.ui.annotations_changed.emit()
        return msg

    def _on_commit_highlight_rect(self, rect: QRectF):
        ok, msg = self.annot.highlight_rect(rect)
        if ok:
            self.ui.refresh()
            self.ui.annotations_changed.emit()
        return msg

    def _on_erase_at(self, pt: QPointF):
        ok, msg = self.annot.erase_at(pt)
        if ok:
            self.ui.refresh()
            self.ui.annotations_changed.emit()
        return msg