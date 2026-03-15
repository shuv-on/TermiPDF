from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QTextEdit, QLineEdit, QLabel, QScrollArea) 
from PyQt6.QtGui import QPixmap
from core.command_engine import CommandEngine

# Main window class
class TermiPDFWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # App Title
        self.setWindowTitle("TermiPDF - The Smart PDF Editor")
        
        # Window size and position: setGeometry(x, y, width, height)
        self.setGeometry(100, 100, 1000, 600)
        
        # create commandEngine object
        self.engine = CommandEngine()
        
        # 01. Central widget & Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout()
        
        # ======================================
        # 02. Left Panle Layout: PDF Viewer
        # ======================================
        self.pdf_viewer_label = QLabel("No PDF Loaded. Use termianl to open a file.")
        self.pdf_viewer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pdf_viewer_label.setStyleSheet("background-color: #2b2b2b; color: #888888; font-size: 16px;")
        
        # Scrolling area for mouse scroll
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.pdf_viewer_label)
        self.scroll_area.setWidgetResizable(True)
        
        # =============================================
        # 03. Right Panel Layout: Terminal Output Area
        # =============================================
        right_panel_layout = QVBoxLayout()
        
        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        # Terminal style
        self.terminal_output.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas; font-size: 14px;")
        self.terminal_output.append(">>> TermiPDF OS v1.0 Initialized...")
        self.terminal_output.append(">>> Type 'help' to see available commands.\n")
        
        # Termianl Input Box
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Enter your command here...(e.g., help)")
        self.command_input.setStyleSheet("font-family: Consolas; font-size: 14px; padding: 5px")
        
        
        right_panel_layout.addWidget(self.terminal_output)
        right_panel_layout.addWidget(self.command_input) 
        
        # ===========================
        # 04. Assemble Panels
        # ===========================
        main_layout.addWidget(self.scroll_area, stretch=6)
        main_layout.addLayout(right_panel_layout, stretch=4)
        
        central_widget.setLayout(main_layout)
        
        # ===========================================
        # 05. Signal and Slots
        # ===========================================
        # if user write command and press enter then process_command method will run
        self.command_input.returnPressed.connect(self.process_command)
        
        
    # ===============================================
    # 06. Command Processing Engine
    # ===============================================
    def process_command(self):
        user_text = self.command_input.text().strip() # .strip() -> delete space left and right
        if not user_text:
            return # if no text input and press enter nothing to do
        # show user command to screen
        self.terminal_output.append(f"<span style='color: white;'>$ {user_text} </span>")
        
        # Command processing
        action, response = self.engine.execute(user_text)
        
        # if else control flow for command
        if action == "print":
            self.terminal_output.append(response)
        elif action == "clear":
            self.terminal_output.clear()
            self.terminal_output.append(response)
        elif action == "exit":
            self.close()
        elif action == "error":
            self.terminal_output.append(response)
        elif action == "open":
            # 1. Show success message
            self.terminal_output.append(response["msg"])
            
            # 2. Change png to PDF
            pixmap = QPixmap()
            pixmap.loadFromData(response["image_bytes"])
            
            # 3. set pdf viewer
            self.pdf_viewer_label.setPixmap(pixmap)
            
            # 4. fit with label size
            self.pdf_viewer_label.setScaledContents(True)
        # Empty command box
        self.command_input.clear()