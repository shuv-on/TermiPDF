class CommandEngine:
    def __init__(self):
        pass

    def execute(self, command):
        cmd = command.lower()
        
        if cmd == "help":
            
            message = (">>> Available Commands:<br>"
                       "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- <b>help</b>   : Show this message<br>"
                       "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- <b>clear</b>  : Clear the terminal<br>"
                       "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;- <b>exit</b>   : Close the application")
            return "print", message
            
        elif cmd == "clear":
            return "clear", ">>> TermiPDF OS v1.0 Initialized...<br>"
            
        elif cmd == "exit":
            return "exit", ""
            
        else:
            return "error", f"<span style='color: red;'>>>> Error: Command '{command}' not found.</span>"