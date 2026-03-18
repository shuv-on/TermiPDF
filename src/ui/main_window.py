from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QSplitter, QToolBar
from PyQt6.QtCore import Qt
from core.command_engine import CommandEngine
from ui.components.pdf_viewer_ui import PDFViewerUI
from ui.components.terminal_ui import TerminalUI

# Main window class
class TermiPDFWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # App Title
        self.setWindowTitle("TermiPDF - The Smart PDF Editor")
        
        # Window size and position: setGeometry(x, y, width, height)
        self.setGeometry(100, 100, 1000, 600)
        
        # Drags & Drops Permission
        self.setAcceptDrops(True)
        
        # create commandEngine object
        self.engine = CommandEngine()

        # ==========================================
        # ToolBar (Toggle Terminal Button)
        # ==========================================
        toolbar = self.addToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.toggle_term_action = toolbar.addAction("💻 Toggle Terminal")
        self.toggle_term_action.triggered.connect(self.toggle_terminal)

        # ==========================================
        # 01. Central widget & Layout
        # ==========================================
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # ======================================
        # 02. Left Panel & 03. Right Panel Components
        # ======================================
        self.pdf_viewer = PDFViewerUI()
        self.terminal = TerminalUI()

        # ===========================
        # 04. Assemble Panels
        # ===========================
        self.splitter.addWidget(self.pdf_viewer)
        self.splitter.addWidget(self.terminal)
        self.splitter.setSizes([600, 400])

        main_layout.addWidget(self.splitter)
        central_widget.setLayout(main_layout)

        # ===========================================
        # 05. Signal and Slots
        # ===========================================
        # if user write command and press enter then process_command method will run
        self.terminal.command_entered.connect(self.process_command)
        # hide terminal when close button is clicked
        self.terminal.close_requested.connect(self.terminal.hide)

    def toggle_terminal(self):
        if self.terminal.isHidden():
            self.terminal.show()
        else:
            self.terminal.hide()

    # ===============================================
    # 06. Command Processing Engine
    # ===============================================
    def process_command(self, user_text):
        # show user command to screen
        self.terminal.append_output(f"<span style='color: white;'>$ {user_text} </span>")
        
        # Command processing
        action, response = self.engine.execute(user_text)

        # if else control flow for command
        if action == "print":
            self.terminal.append_output(response)
        elif action == "clear":
            self.terminal.clear_output()
            self.terminal.append_output(response)
        elif action == "exit":
            self.close()
        elif action == "error":
            self.terminal.append_output(response)
        elif action == "open":
            # 1. Show success message
            self.terminal.append_output(response["msg"])
            
            # 2. Change png to PDF
            # 3. set pdf viewer
            # 4. fit with label size
            # (Note: Step 2, 3 and 4 are now handled inside pdf_viewer_ui.py)
            self.pdf_viewer.set_image(response["image_bytes"]) 

    # ===============================================
    # 07. Drag & Drop Events
    # ===============================================
    def dragEnterEvent(self, event):
        # to check drag file is .pdf or not
        if event.mimeData().hasUrls():
            file_url = event.mimeData().urls()[0].toLocalFile()
            if file_url.lower().endswith('.pdf'):
                event.accept() # if pdf give acces
            else:
                event.ignore() # else don't give access
        else:
            event.ignore()

    def dropEvent(self, event):
        # Mouse leave
        file_path = event.mimeData().urls()[0].toLocalFile()
        
        # show opening command to terminal
        auto_command = f'open "{file_path}"'
        self.terminal.append_output(f"<span style='color: #00ffff;'>$ [Drag & Drop] {auto_command} </span>")
        
        # send command 
        action, response = self.engine.execute(auto_command)
        
        # set image 
        if action == "open":
            self.terminal.append_output(response["msg"])
            self.pdf_viewer.set_image(response["image_bytes"])
        elif action == "error":
            self.terminal.append_output(response)