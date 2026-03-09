import sys
from PyQt6.QtWidgets import QApplication, QMainWindow

# 01. Main window class (OOP inheritance)
class TermiPDFWindow(QMainWindow):
    def __init__(self):
        super().__init__() # Call parent class QMainWindows __init__
        
        # Set window Title
        self.setWindowTitle("TermiPDF - The smart PDF Editor")
        
        # Windows size and position: setGeometry(x, y, width, height)
        self.setGeometry(100, 100, 800, 600)
        
# 02. Main function (App start from here)
def main():
    # An object QApplication is need every PyQt Application
    app = QApplication(sys.argv) # Terminal command control likes: for run main.py --> python src/main.py 
    
    # Create class object 
    window = TermiPDFWindow()
    
    # Show window to screen
    window.show()
    
    # Event loop: App run while user doesn't exit
    sys.exit(app.exec())
# 03. Python standard entry Point
if __name__ == "__main__":
    main()

          