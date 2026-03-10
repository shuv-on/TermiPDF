from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QPushButton, QTextEdit, QLineEdit
from core.command_engine import CommandEngine

# Main window class
class TermiPDFWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # App Title
        self.setWindowTitle("TermiPDF - The Smart PDF Editor")
        
        # Window size and position: setGeometry(x, y, width, height)
        self.setGeometry(100, 100, 800, 600)
        
        # create commandEngine object
        self.engine = CommandEngine()
        
        # 01. Central widget & Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        
        # 02. Terminal Output Area
        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        # Terminal style
        self.terminal_output.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas; font-size: 14px;")
        self.terminal_output.append(">>> TermiPDF OS v1.0 Initialized...")
        self.terminal_output.append(">>> Type 'help' to see available commands.\n")
        
        # 03. Termianl Input Box
        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("Enter your command here...(e.g., help)")
        self.command_input.setStyleSheet("font-family: Consolas; font-size: 14px; padding: 5px")
        
        # 04. Set Layout Widget
        layout.addWidget(self.terminal_output)
        layout.addWidget(self.command_input)
        central_widget.setLayout(layout)
        
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
        # Empty command box
        self.command_input.clear()