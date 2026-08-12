"""
qr_ui.py — (optional) small preview widget for the QR generator.

The QR generator feature is primarily CLI-driven (`qr "text"`). This file
ships a small QWidget that, if added to a panel, lets users preview a QR
before stamping it. The CLI path does NOT require this widget.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QFrame,
)

from .qr_logic import QRLogic


class QRPreviewUI(QWidget):
    """Tiny preview widget (used only if embedded in a panel)."""

    stamp_requested = pyqtSignal(str, int, int, int)  # text, page, x, y, size

    def __init__(self, qr_logic: QRLogic, parent=None):
        super().__init__(parent)
        self.qr = qr_logic
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        title = QLabel("🔳 QR Generator")
        title.setStyleSheet("font-weight: bold; color: #cba6f7;")
        root.addWidget(title)

        self.input = QLineEdit()
        self.input.setPlaceholderText('enter text or URL … e.g. qr "https://…"')
        root.addWidget(self.input)

        row = QHBoxLayout()
        row.addWidget(QLabel("Size:"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(40, 500)
        self.size_spin.setValue(120)
        self.size_spin.setSuffix(" pt")
        row.addWidget(self.size_spin)
        root.addLayout(row)

        btn_row = QHBoxLayout()
        self.preview_btn = QPushButton("preview")
        self.preview_btn.clicked.connect(self._on_preview)
        btn_row.addWidget(self.preview_btn)

        self.stamp_btn = QPushButton("stamp on PDF")
        self.stamp_btn.clicked.connect(self._on_stamp)
        btn_row.addWidget(self.stamp_btn)
        root.addLayout(btn_row)

        self.preview_label = QLabel("(preview)")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_label.setMinimumHeight(160)
        root.addWidget(self.preview_label, 1)

    # ------------------------------------------------------- slots
    def _on_preview(self):
        text = self.input.text().strip()
        if not text:
            return
        img = self.qr.generate_image(text)
        qimg = QImage(
            img.tobytes("raw", "RGB"),
            img.size[0], img.size[1],
            img.size[0] * 3,
            QImage.Format.Format_RGB888,
        )
        self.preview_label.setPixmap(QPixmap.fromImage(qimg))

    def _on_stamp(self):
        text = self.input.text().strip()
        if not text:
            return
        self.stamp_requested.emit(text, 0, 50, 50)  # page=x=y=0 ⇒ handled by main_window
