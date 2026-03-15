import fitz # PyMuPDF libray for read PDF
import os
class CommandEngine:
    def __init__(self):
        self.current_pdf_doc = None

    def execute(self, command):
        cmd_parts = command.strip().split()
        if not cmd_parts:
            return "none", ""
        
        cmd = cmd_parts[0].lower()
        
        if cmd == "help":
            
            message = (">>> Available Commands:<br>"
                       "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- <b>open &lt;filename&gt;</b> : Open a PDF file<br>"
                       "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- <b>help</b>   : Show this message<br>"
                       "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- <b>clear</b>  : Clear the terminal<br>"
                       "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- <b>exit</b>   : Close the application")
            return "print", message
            
        elif cmd == "clear":
            return "clear", ">>> TermiPDF OS v1.0 Initialized...<br>"
            
        elif cmd == "exit":
            return "exit", ""
        elif cmd == "open":
            if len(cmd_parts) < 2:
                return "error", "<span style='color: red;'>>>> Error: Please provide a filename. Example: open test.pdf</span>"
            filename = cmd_parts[1]
            
            if not os.path.exists(filename):
                return "error", f"<span style='color: red;'>>>> Error: File '{filename}' not found!</span>"
            try:
                # 01. Open file
                self.current_pdf_doc = fitz.open(filename)
                
                # 02. PDF page 0
                page = self.current_pdf_doc[0]
                
                # 03. Convert page to high resolution pic
                pix = page.get_pixmap(matrix=fitz.Matrix(2,2))
                
                # 04. Send to ui as png
                image_data = pix.tobytes("png")
                
                return "open", {"image_bytes": image_data, "msg": f">>> Successfully opened '{filename}'. Total Pages: {len(self.current_pdf_doc)}"}
            except Exception as e:
                return "error", f"<span style='color: red;'>>>> Error opening PDF: {str(e)}</span>"
                
            
        else:
            return "error", f"<span style='color: red;'>>>> Error: Command '{command}' not found.</span>"
        