"""
signature_dialog.py — A small dialog where the user draws a signature.

The dialog shows a white drawing surface with a pen; once the user clicks
"Save" we render the surface into a transparent PNG.

Used by ``canvas_events._on_request_signature``.
"""
from __future__ import annotations

from typing import List

from PyQt6.QtCore import Qt, QPoint, QPointF, QRect
from PyQt6.QtGui import QPainter, QPen, QColor, QImage, QMouseEvent, QPaintEvent
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QWidget,
)


class _SignatureSurface(QLabel):
    """A QLabel that captures mouse input to build up a list of strokes."""

    def __init__(self, parent: "SignatureDialog"):
        super().__init__(parent)
        self._owner = parent
        self.setFixedSize(420, 140)
        self.setStyleSheet("background-color: #ffffff; border: 1px solid #888;")
        self._strokes: List[List[QPoint]] = []
        self._current: List[QPoint] = []
        self._drawing = False

    def paintEvent(self, event: QPaintEvent):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            pen = QPen(QColor("#1f1f1f"))
            pen.setWidth(2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            for stroke in self._strokes:
                for a, b in zip(stroke, stroke[1:]):
                    painter.drawLine(a, b)
            if self._current:
                for a, b in zip(self._current, self._current[1:]):
                    painter.drawLine(a, b)
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drawing = True
            self._current = [event.position().toPoint()]
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drawing:
            self._current.append(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._drawing = False
            if len(self._current) > 1:
                self._strokes.append(self._current)
            self._current = []
            self.update()

    def has_content(self) -> bool:
        return any(len(s) > 1 for s in self._strokes)

    def render_to_png(self) -> bytes:
        """Render the current strokes into a transparent PNG."""
        if not self.has_content():
            return b""
        img = QImage(self.size(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        try:
            pen = QPen(QColor("#1f1f1f"))
            pen.setWidth(2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(pen)
            for stroke in self._strokes:
                for a, b in zip(stroke, stroke[1:]):
                    painter.drawLine(a, b)
        finally:
            painter.end()
        buf = QByteArray()  # type: ignore
        # QImage.save takes a QBuffer; we'll just convert via bytes
        from PyQt6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buffer, "PNG")
        buffer.close()
        return bytes(buffer.data())


# Late import to avoid an unnecessary QByteArray namespace error above
from PyQt6.QtCore import QByteArray  # noqa: E402


class SignatureDialog(QDialog):
    """Modal dialog that captures a freehand signature."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Capture Signature")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        hint = QLabel("Draw your signature below:")
        root.addWidget(hint)

        self.surface = _SignatureSurface(self)
        root.addWidget(self.surface)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

        self._png_bytes: bytes = b""

    def _on_clear(self):
        self.surface._strokes.clear()
        self.surface.update()

    def _on_save(self):
        if not self.surface.has_content():
            return
        self._png_bytes = self.surface.render_to_png()
        self.accept()

    def get_png_bytes(self) -> bytes:
        return self._png_bytes