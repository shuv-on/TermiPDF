"""
swatch_bar.py — A horizontal color palette (Edge-style).

Shows two rows: pen colors (8) and highlighter colors (6).
Click a swatch → emits color_chosen(category, color_hex).
"""
from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel,
)


# Default palettes
PEN_COLORS = [
    "#1f1f1f",  # black
    "#f38ba8",  # red
    "#fab387",  # orange
    "#f9e2af",  # yellow
    "#a6e3a1",  # green
    "#89b4fa",  # blue
    "#cba6f7",  # purple
    "#ffffff",  # white
]
HIGHLIGHT_COLORS = [
    "#f9e2af",  # yellow (default)
    "#a6e3a1",  # green
    "#89b4fa",  # blue
    "#cba6f7",  # purple
    "#fab387",  # orange
    "#f38ba8",  # pink
]
# Shape (rect / ellipse / arrow) outline colors. The first swatch is the
# default; any of these can be selected via the toolbar.
SHAPE_COLORS = [
    "#89b4fa",  # blue (default)
    "#f38ba8",  # red
    "#a6e3a1",  # green
    "#f9e2af",  # yellow
    "#cba6f7",  # purple
    "#1f1f1f",  # black
]


class Swatch(QFrame):
    """Single color swatch button. Emits clicked() on press."""

    clicked = pyqtSignal()

    def __init__(self, color: str, category: str, parent=None):
        super().__init__(parent)
        self.setObjectName("swatch")
        self.color = color
        self.category = category
        self.setFixedSize(20, 20)
        self.setToolTip(color)
        self.setStyleSheet(self._stylesheet(active=False))
        self.setProperty("category", category)

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(self._stylesheet(active=active))

    def _stylesheet(self, *, active: bool) -> str:
        border = "#1f1f1f" if active else "transparent"
        if self.category == "highlight":
            inner = self.color
        else:
            inner = self.color
        return (f"background-color: {inner};"
                f"border: 2px solid {border};"
                f"border-radius: 4px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()


class SwatchBar(QFrame):
    """Two-row swatch palette: pen colors (top) + highlighter (bottom)."""

    color_chosen = pyqtSignal(str, str)   # category ("pen"|"highlight"), color_hex

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_pen: Optional[Swatch] = None
        self._active_highlight: Optional[Swatch] = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        # Pen row
        pen_row = QHBoxLayout()
        pen_label = QLabel("✎")
        pen_label.setToolTip("Pen color")
        pen_label.setObjectName("toolbarLabel")
        pen_row.addWidget(pen_label)
        self._pen_swatches: list[Swatch] = []
        for c in PEN_COLORS:
            sw = Swatch(c, "pen")
            sw.clicked.connect(lambda _=False, color=c: self._pick("pen", color))
            pen_row.addWidget(sw)
            self._pen_swatches.append(sw)
        pen_row.addStretch(1)
        root.addLayout(pen_row)

        # Highlight row
        h_row = QHBoxLayout()
        h_label = QLabel("▮")
        h_label.setToolTip("Highlighter color")
        h_label.setObjectName("toolbarLabel")
        h_row.addWidget(h_label)
        self._h_swatches: list[Swatch] = []
        for c in HIGHLIGHT_COLORS:
            sw = Swatch(c, "highlight")
            sw.clicked.connect(lambda _=False, color=c: self._pick("highlight", color))
            h_row.addWidget(sw)
            self._h_swatches.append(sw)
        h_row.addStretch(1)
        root.addLayout(h_row)

        # Default active selections
        self._pick("pen", "#f38ba8", emit=False)
        self._pick("highlight", "#f9e2af", emit=False)

    def set_active_pen(self, color_hex: str) -> None:
        self._pick("pen", color_hex, emit=True)

    def set_active_highlight(self, color_hex: str) -> None:
        self._pick("highlight", color_hex, emit=True)

    def _pick(self, category: str, color_hex: str, *, emit: bool = True) -> None:
        items = self._pen_swatches if category == "pen" else self._h_swatches
        active_field = "_active_pen" if category == "pen" else "_active_highlight"
        prev = getattr(self, active_field, None)
        if prev is not None:
            prev.set_active(False)
        for sw in items:
            if sw.color.lower() == color_hex.lower():
                sw.set_active(True)
                setattr(self, active_field, sw)
                break
        if emit:
            self.color_chosen.emit(category, color_hex)
