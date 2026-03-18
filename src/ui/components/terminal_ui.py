from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLineEdit, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal

class TerminalUI(QWidget):
    # custom signal: signal send to main window
    command_entered = pyqtSignal(str) 
    close_requested = pyqtSignal()    

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)

        # Close Button
        self.close_btn = QPushButton("✖ Close Terminal")
        self.close_btn.setStyleSheet("background-color: #ff4444; color: white; font-weight: bold; border: none; padding: 5px; border-radius: 3px;")
        self.close_btn.clicked.connect(self.close_requested.emit)

        # Output Box
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas; font-size: 14px;")
        self.output.append(">>> TermiPDF OS v1.0 Initialized...")
        self.output.append(">>> Type 'help' to see available commands.\n")

        # Input Box
        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter your command here...(e.g., help)")
        self.input.setStyleSheet("font-family: Consolas; font-size: 14px; padding: 5px")
        self.input.returnPressed.connect(self._on_enter)

        layout.addWidget(self.close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.output)
        layout.addWidget(self.input)
        self.setLayout(layout)

    def _on_enter(self):
        text = self.input.text().strip()
        if text:
            self.command_entered.emit(text) # send text outside usign signal
        self.input.clear()

    # show output main window
    def append_output(self, html_text):
        self.output.append(html_text)

    def clear_output(self):
        self.output.clear()