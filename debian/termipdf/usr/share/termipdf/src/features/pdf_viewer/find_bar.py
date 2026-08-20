"""
find_bar.py — Ctrl+F search bar that slides in over the PDF canvas.

Provides:
* Search input with placeholder "Find in document"
* Prev / Next arrows + match counter "3 / 17"
* Close button (Esc)
* Emits ``search_requested(text)`` on Enter / arrow presses

The main window is responsible for running the actual search against
``ViewerEngine.find_all(text)`` and drawing the highlights.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolButton,
)


class FindBar(QFrame):
    """Slide-down search bar (Edge-style)."""

    search_requested = pyqtSignal(str)              # any text change
    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("findBar")
        self.setFixedHeight(36)
        self._build_ui()
        self.hide()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(6)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Find in document…")
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.returnPressed.connect(self.next_requested.emit)
        root.addWidget(self.input, 1)

        self.match_label = QLabel("—")
        self.match_label.setObjectName("findMatchLabel")
        self.match_label.setMinimumWidth(60)
        self.match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.match_label)

        prev_btn = QToolButton()
        prev_btn.setText("▲")
        prev_btn.setToolTip("Previous match (Shift+Enter)")
        prev_btn.clicked.connect(self.prev_requested.emit)
        root.addWidget(prev_btn)

        next_btn = QToolButton()
        next_btn.setText("▼")
        next_btn.setToolTip("Next match (Enter)")
        next_btn.clicked.connect(self.next_requested.emit)
        root.addWidget(next_btn)

        close_btn = QToolButton()
        close_btn.setText("✖")
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.closed.emit)
        root.addWidget(close_btn)

    # ---------------------------------------------------- public API
    def set_text(self, text: str) -> None:
        self.input.setText(text)

    def text(self) -> str:
        return self.input.text()

    def show_bar(self) -> None:
        self.show()
        self.input.setFocus()
        self.input.selectAll()

    def hide_bar(self) -> None:
        self.hide()

    def set_match_count(self, current: int, total: int) -> None:
        if total <= 0:
            self.match_label.setText("No matches")
        else:
            self.match_label.setText(f"{current} / {total}")

    # --------------------------------------------------------- handlers
    def _on_text_changed(self, text: str):
        self.search_requested.emit(text)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        if event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.prev_requested.emit()
            return
        super().keyPressEvent(event)