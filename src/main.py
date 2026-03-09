import sys
from PyQt6.QtWidgets import QApplication

# import window class from ui folder
from ui.main_window import TemriPDFWindow
        
# 02. Main function (App start from here)
def main():
    # An object QApplication is need every PyQt Application
    app = QApplication(sys.argv) # Terminal command control likes: for run main.py --> python src/main.py 
    
    # Create class object 
    window = TemriPDFWindow()
    
    # Show window to screen
    window.show()
    
    # Event loop: App run while user doesn't exit
    sys.exit(app.exec())
# 03. Python standard entry Point
if __name__ == "__main__":
    main()

          