from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QPushButton, QTextEdit

# Main window class
class TemriPDFWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # App Title
        self.setWindowTitle("TermiPDF - The Smart PDF Editor")
        
        # Window size and position: setGeometry(x, y, width, height)
        self.setGeometry(100, 100, 800, 600)
        
        # 01. Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 02. Layout
        layout = QVBoxLayout()
        
        # 03. Widgets generate
        self.text_area = QTextEdit() # Show message terminal
        self.text_area.setReadOnly(True) # The user should not delete what they type here.
        self.text_area.append(">>> Welcome to TermiPDF. System Initialized...")
        
        self.test_button = QPushButton("Click Me - Test Button")
        
        # 04. Set widget in Layout
        layout.addWidget(self.text_area)
        layout.addWidget(self.test_button)
        
        # 05. Telling the Central Widget, "This will be your layout"
        central_widget.setLayout(layout)
        