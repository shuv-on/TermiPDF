"""
command_parser.py — Token-based command parser and dispatcher.

Design notes
------------
The terminal feature is the "nervous system" of TermiPDF. To keep features
decoupled (no feature imports another feature), command handlers are
*registered* against this parser by the main window at startup. The parser
only knows about:

* Its own tiny commands (help / clear / exit / history)
* The generic token grammar (with quoted string support)
* A small set of action tags returned from each handler

Each handler returns a dict:
    {
        "action": "print" | "clear" | "exit" | "open" | "render"
                  | "toc_toggle" | "mode" | "edit" | "notify" | "error",
        "data":  <whatever the consumer needs>
    }
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# -------------------------------------------------------------- Data types
@dataclass
class CommandResult:
    action: str
    data: dict = field(default_factory=dict)

    @classmethod
    def print(cls, text: str) -> "CommandResult":
        return cls("print", {"text": text})

    @classmethod
    def error(cls, text: str) -> "CommandResult":
        return cls("error", {"text": text})

    @classmethod
    def clear(cls) -> "CommandResult":
        return cls("clear")

    @classmethod
    def exit(cls) -> "CommandResult":
        return cls("exit")


Handler = Callable[[List[str]], CommandResult]


# -------------------------------------------------------------- Parser core
class CommandParser:
    """Token-based command dispatcher."""

    def __init__(self):
        self._handlers: Dict[str, Handler] = {}
        self._register_builtin_commands()

    # --- registration ----------------------------------------------------
    def register(self, name: str, handler: Handler):
        self._handlers[name.lower()] = handler

    def unregister(self, name: str):
        self._handlers.pop(name.lower(), None)

    # --- parsing ---------------------------------------------------------
    @staticmethod
    def tokenize(raw: str) -> List[str]:
        """Split a command line, supporting double-quoted strings.

        Examples:
            >>> tokenize('addtext "hello world" --x 10')
            ['addtext', 'hello world', '--x', '10']
        """
        if not raw or not raw.strip():
            return []
        try:
            return shlex.split(raw, posix=True)
        except ValueError:
            # Unbalanced quotes — fall back to a permissive split
            return raw.split()

    @staticmethod
    def extract_flags(tokens: List[str]) -> Tuple[List[str], Dict[str, str]]:
        """Split positional args from --key value flags."""
        positional: List[str] = []
        flags: Dict[str, str] = {}
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("--"):
                key = tok[2:].lower()
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                    flags[key] = tokens[i + 1]
                    i += 2
                else:
                    flags[key] = ""  # boolean flag
                    i += 1
            else:
                positional.append(tok)
                i += 1
        return positional, flags

    # --- dispatch --------------------------------------------------------
    def execute(self, raw: str) -> CommandResult:
        tokens = self.tokenize(raw)
        if not tokens:
            return CommandResult.print("")

        cmd = tokens[0].lower()
        args = tokens[1:]

        handler = self._handlers.get(cmd)
        if handler is None:
            return CommandResult.error(
                f"Unknown command: <b>{cmd}</b>. Type <b>help</b> for a list."
            )
        try:
            return handler(args)
        except Exception as exc:  # never crash the terminal
            return CommandResult.error(f"'{cmd}' crashed: {exc}")

    # --- built-in commands ----------------------------------------------
    def _register_builtin_commands(self):
        self.register("help", self._cmd_help)
        self.register("clear", lambda _a: CommandResult.clear())
        self.register("exit", lambda _a: CommandResult.exit())
        self.register("history", self._cmd_history)

    # ------------------------------------------------------------- handlers
    def _cmd_help(self, _args: List[str]) -> CommandResult:
        text = self._help_text()
        return CommandResult.print(text)

    def _cmd_history(self, _args: List[str]) -> CommandResult:
        # The terminal UI owns the history. The terminal will print its own
        # history via its built-in commands if needed; we just acknowledge.
        return CommandResult.print(
            "Use the ↑ / ↓ arrow keys in the input to cycle through history."
        )

    # ---- public helper used by main_window on first 'help' call --------
    def help_text(self) -> str:
        return self._help_text()

    def _help_text(self) -> str:
        lines = [
            "<b style='color:#cba6f7;'>═══ TermiPDF Command Reference ═══</b>",
            "<br><b style='color:#89b4fa;'>— General —</b>",
            "  <b>help</b>                       Show this help screen",
            "  <b>clear</b>                      Clear terminal output",
            "  <b>exit</b>                       Close TermiPDF",
            "",
            "<b style='color:#89b4fa;'>— Viewer / Navigation —</b>",
            '  <b>open &lt;path&gt;</b>            Open a PDF file',
            "  <b>close</b>                      Close the current PDF",
            "  <b>next</b> / <b>prev</b>           Go to next / previous page",
            "  <b>goto &lt;n&gt;</b>               Jump to page n",
            "  <b>zoom in</b> / <b>zoom out</b>    Adjust zoom level",
            "  <b>zoom &lt;float&gt;</b>           Set zoom level (e.g. zoom 1.5)",
            "  <b>fit</b>                        Fit page to window",
            "  <b>toc</b>                        Toggle the Table of Contents sidebar",
            "",
            "<b style='color:#89b4fa;'>— Annotator (Edge-like) —</b>",
            "  <b>mode view</b>                  Return to normal scrolling mode",
            '  <b>mode draw</b> --color &lt;c&gt; --thickness &lt;n&gt;  Freehand ink',
            "  <b>mode highlight</b>             Click-and-drag or auto-highlight",
            '  <b>highlight "text"</b>           Auto-highlight all occurrences of "text"',
            "  <b>mode erase</b>                 Click any annotation to delete it",
            "  <b>save</b>                       Save annotations into the PDF file",
            "",
            "<b style='color:#89b4fa;'>— Editor —</b>",
            '  <b>addtext "txt"</b> --page 1 --x 100 --y 200 --size 14 --color black',
            "       Insert Unicode text (supports Bangla) — needs --font if no Kalpurush.ttf",
            "  <b>extract &lt;from&gt; &lt;to&gt; &lt;out.pdf&gt;</b>",
            "  <b>merge &lt;f1&gt; &lt;f2&gt; &lt;out.pdf&gt;</b>",
            "  <b>delete &lt;page&gt;</b>",
            "  <b>rotate &lt;page&gt; &lt;angle&gt;</b>",
            "",
            "<b style='color:#89b4fa;'>— QR Generator —</b>",
            '  <b>qr "text"</b> --x 100 --y 100 --size 100   Stamp a QR code on the current page',
        ]
        return "<br>".join(lines)
