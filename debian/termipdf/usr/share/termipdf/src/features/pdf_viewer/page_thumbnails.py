"""
page_thumbnails.py — Vertical thumbnail sidebar (Edge-style).

Lazy-renders small previews of each page in a QListWidget and emits
`navigate_requested(int)` (1-based page number) on click.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QWidget, QVBoxLayout, QLabel,
    QHBoxLayout, QLineEdit,
)

from .viewer_engine import ViewerEngine


THUMB_W = 130
THUMB_H = 180


class PageThumbnailsUI(QWidget):
    """Thumbnail rail."""

    navigate_requested = pyqtSignal(int)   # 1-based page number

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine: Optional[ViewerEngine] = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # Page jump
        top = QHBoxLayout()
        top.addWidget(QLabel("Page:"))
        self.page_input = QLineEdit()
        self.page_input.setPlaceholderText("#")
        self.page_input.setMaximumWidth(60)
        self.page_input.returnPressed.connect(self._jump_to_text)
        top.addWidget(self.page_input)
        top.addStretch(1)
        root.addLayout(top)

        # Thumbnail list
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSpacing(6)
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(self._on_item)
        self.list.itemClicked.connect(self._on_item)
        root.addWidget(self.list, 1)

        # Empty-state hint
        self._empty = QLabel("No PDF open")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color: #6c7086; padding: 20px;")
        root.addWidget(self._empty)
        self.list.hide()

    # -------------------------------------------------------------- public
    def load_document(self, engine: ViewerEngine):
        self._engine = engine
        self.list.clear()
        if engine is None or not engine.is_open:
            self.list.hide()
            self._empty.show()
            self._empty.setText("No PDF open")
            return
        self._empty.hide()
        self.list.show()
        # Populate with placeholders; thumbnails rendered lazily below.
        for i in range(engine.page_count):
            item = QListWidgetItem(f"Page {i + 1}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list.addItem(item)
        # Kick off lazy rendering: render the first page immediately,
        # rest on demand when they scroll into view (simplified: render
        # all on a single-shot timer to keep the code straightforward).
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._render_all)

    def set_current_page(self, page_1based: int):
        """Highlight the active thumbnail (called from main window)."""
        if not self.list.count():
            return
        idx = page_1based - 1
        if 0 <= idx < self.list.count():
            self.list.setCurrentRow(idx)
            self.list.scrollToItem(self.list.item(idx))

    # ----------------------------------------------------------- internals
    def _render_all(self):
        if not self._engine or not self._engine.is_open:
            return
        for i in range(self.list.count()):
            self._render_one(i)
            # Allow the UI to breathe between renders
            if i % 4 == 3:
                from PyQt6.QtCore import QCoreApplication
                QCoreApplication.processEvents()

    def _render_one(self, row: int):
        if not self._engine:
            return
        try:
            page = self._engine.get_page(row)
            import fitz
            from PyQt6.QtGui import QIcon
            matrix = fitz.Matrix(0.25, 0.25)   # ~72 dpi → small previews
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            qimg = QImage.fromData(pix.tobytes("png"))
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(THUMB_W, THUMB_H,
                                   Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            item = self.list.item(row)
            if item is not None:
                # QListWidgetItem.setIcon expects a QIcon, not a QPixmap.
                item.setIcon(QIcon(scaled))
        except Exception:
            pass

    def _on_item(self, item: QListWidgetItem):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is not None:
            self.navigate_requested.emit(int(idx) + 1)

    def _jump_to_text(self):
        if not self._engine or not self._engine.is_open:
            return
        try:
            n = int(self.page_input.text())
        except ValueError:
            return
        if 1 <= n <= self._engine.page_count:
            self.navigate_requested.emit(n)
