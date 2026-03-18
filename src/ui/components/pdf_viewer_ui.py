from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QScrollArea
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

class PDFViewerUI(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0) # Rmv spaces

        self.label = QLabel("No PDF Loaded. Drag & Drop a file here.")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("background-color: #2b2b2b; color: #888888; font-size: 16px;")

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.label)
        self.scroll_area.setWidgetResizable(True)

        layout.addWidget(self.scroll_area)
        self.setLayout(layout)

    # send pic using this method
    def set_image(self, image_bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(image_bytes)
        self.label.setPixmap(pixmap)
        self.label.setScaledContents(True)