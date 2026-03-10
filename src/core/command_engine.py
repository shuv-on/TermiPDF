class CommandEngine:
    def __init__(self):
        pass
    def execute(self, command):
        # this method take input command and decesion
        cmd = command.lower()
        
        if cmd == "help":
            message = (">>> Available Commands:\n"
                       "       - <b>help</b>   : Show this message\n"
                       "       - <b>clear</b>  : Clear the terminal\n"
                       "       - <b>exit</b>   : Close the application")
            return "print", message
            
        elif cmd == "clear":
            return "clear", ">>> TermiPDF OS v1.0 Initialized...\n"
            
        elif cmd == "exit":
            return "exit", ""
            
        else:
            return "error", f"<span style='color: red;'>>>> Error: Command '{command}' not found.</span>"