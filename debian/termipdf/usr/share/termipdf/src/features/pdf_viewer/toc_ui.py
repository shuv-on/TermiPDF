"""
toc_ui.py — MS Edge-style collapsible Table of Contents sidebar.

Built on QTreeWidget for simplicity (no model/view plumbing needed for this
scale). Each top-level item is an OutlineNode; clicking jumps the viewer.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QLabel,
    QLineEdit,
    QPushButton,
)

from .viewer_engine import OutlineNode, ViewerEngine


class TOCUI(QWidget):
    """Left sidebar showing the PDF outline."""

    # Emitted when user clicks an entry → main_window asks the engine to navigate.
    navigate_requested = pyqtSignal(int)  # 1-based page number

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Header
        header = QHBoxLayout()
        title = QLabel("📑 Outline")
        title.setStyleSheet("font-weight: bold; color: #f9e2af;")
        header.addWidget(title)
        header.addStretch(1)

        self.collapse_btn = QPushButton("⊟ collapse all")
        self.collapse_btn.clicked.connect(self.collapse_all)
        header.addWidget(self.collapse_btn)

        root.addLayout(header)

        # Filter
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.filter_edit)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Title", "Page"])
        self.tree.setColumnWidth(0, 220)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.tree.itemClicked.connect(self._on_item_activated)
        root.addWidget(self.tree, 1)

        # Empty-state hint
        self._empty_label = QLabel("No PDF loaded.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #6c7086; padding: 20px;")
        root.addWidget(self._empty_label)
        self.tree.hide()

    # ----------------------------------------------------------- public API
    def load_outline(self, nodes: List[OutlineNode]):
        self.tree.clear()
        if not nodes:
            self.tree.hide()
            self._empty_label.show()
            self._empty_label.setText("This PDF has no outline.")
            return
        self._empty_label.hide()
        self.tree.show()

        def add(item_parent: Optional[QTreeWidgetItem], node: OutlineNode):
            tree_item = QTreeWidgetItem([node.title, str(node.page)])
            tree_item.setData(0, Qt.ItemDataRole.UserRole, node.page)
            tree_item.setToolTip(0, node.title)
            if item_parent is None:
                self.tree.addTopLevelItem(tree_item)
            else:
                item_parent.addChild(tree_item)
            for child in node.children:
                add(tree_item, child)
            tree_item.setExpanded(True)

        for top in nodes:
            add(None, top)

    def clear_outline(self):
        self.tree.clear()
        self.tree.hide()
        self._empty_label.show()
        self._empty_label.setText("No PDF loaded.")

    def collapse_all(self):
        self.tree.collapseAll()

    def expand_all(self):
        self.tree.expandAll()

    # ---------------------------------------------------------- internals
    def _on_item_activated(self, item: QTreeWidgetItem, _col: int):
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page:
            self.navigate_requested.emit(int(page))

    def _apply_filter(self, text: str):
        text = text.strip().lower()
        # Walk the tree and hide items that don't match (keeping parents of matches)
        def filter_recursive(item: QTreeWidgetItem) -> bool:
            title_match = text in item.text(0).lower()
            child_any = False
            for i in range(item.childCount()):
                child_any |= filter_recursive(item.child(i))
            visible = title_match or child_any or not text
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            filter_recursive(self.tree.topLevelItem(i))
