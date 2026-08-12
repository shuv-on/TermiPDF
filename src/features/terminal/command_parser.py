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
        """Rebuilt as a <table> so the description column stays aligned
        even when the terminal wraps long command lines."""
        rows = [
            ("General", [
                ("help",                       "Show this help screen"),
                ("clear",                      "Clear terminal output"),
                ("history",                    "Show history hint"),
                ("exit",                       "Close TermiPDF"),
            ]),
            ("Viewer / Navigation", [
                ("open &lt;path&gt;",          "Open a PDF file"),
                ("close",                      "Close the current PDF"),
                ("next | prev",                "Next / previous page"),
                ("goto &lt;n&gt;",             "Jump to page n"),
                ("zoom in | out | &lt;float&gt;", "Zoom controls"),
                ("fit",                        "Fit page to window"),
                ("toc",                        "Toggle the outline sidebar"),
                ("thumbs",                     "Toggle the thumbnail sidebar"),
                ("find &lt;text&gt;",          "Search &amp; highlight all matches"),
            ]),
            ("Annotate (Edge-like)", [
                ("mode view",                  "Return to normal scrolling"),
                ("mode draw --color &lt;c&gt; --thickness &lt;n&gt;",
                 "Freehand ink (multi-color)"),
                ("mode highlight --color &lt;c&gt;",
                 "Drag to highlight (multi-color)"),
                ("mode erase",                 "Click annotation to delete"),
                ("mode rect | ellipse | arrow","Shape tools"),
                ("mode note",                  "Drop a sticky note"),
                ("mode signature",             "Draw &amp; save a signature"),
                ("mode text | edit-text",      "Insert / replace text"),
                ("highlight \"text\" --color", "Auto-highlight all occurrences"),
                ("undo | redo",                "Undo / redo last change"),
                ("save",                       "Save annotations into the PDF"),
            ]),
            ("Edit", [
                ("addtext \"txt\" --page 1 --x 100 --y 200 --size 14",
                 "Insert Unicode text (supports Bangla)"),
                ("edit-text &lt;page&gt; &lt;x&gt; &lt;y&gt; \"new\"",
                 "Replace existing text at a point"),
                ("extract &lt;from&gt; &lt;to&gt; &lt;out.pdf&gt;",
                 "Extract pages to a new PDF"),
                ("merge &lt;f1&gt; &lt;f2&gt; &lt;out.pdf&gt;",
                 "Merge two PDFs"),
                ("merge p-&lt;from&gt; p-&lt;to&gt; &lt;out.pdf&gt;",
                 "Extract a page range from the open PDF into a new file"),
                ("gen npdf p-1,2,3 &lt;out.pdf&gt;",
                 "Generate new PDF with the given pages"),
                ("split &lt;page&gt; &lt;left.pdf&gt; &lt;right.pdf&gt;",
                 "Split the open PDF into two files at <page>"),
                ("delete &lt;page&gt;",        "Delete a page"),
                ("rotate &lt;page&gt; &lt;angle&gt;",
                 "Rotate a page"),
                ("swap &lt;page-A&gt; &lt;page-B&gt;",
                 "Exchange two pages (positions unchanged elsewhere)"),
            ]),
            ("Tools", [
                ("qr \"text\" --page 1 --x … --y … --size 100",
                 "Stamp a QR code on a page"),
                ("stamp-capture &lt;name&gt;", "Save current selection as a stamp"),
                ("stamp &lt;name&gt; --page n --x … --y …",
                 "Paste a saved stamp"),
            ]),
            ("UI", [
                ("theme dark | light",         "Switch theme"),
                ("fullscreen",                 "Toggle fullscreen (F11)"),
                ("dock bottom | left | right | top | float",
                 "Move the terminal dock"),
                ("print",                      "Print the current PDF"),
            ]),
        ]

        parts = [
            "<div style='font-family:\"JetBrains Mono\",monospace;'>",
            "<div style='color:#cba6f7;font-weight:bold;font-size:15px;"
            "margin-bottom:6px'>═══ TermiPDF Command Reference ═══</div>",
        ]
        for section, items in rows:
            parts.append(
                f"<div style='color:#89b4fa;font-weight:bold;"
                f"margin:6px 0 2px 0'>— {section} —</div>"
            )
            parts.append(
                "<table cellspacing='0' cellpadding='2' width='100%'>"
            )
            for cmd, desc in items:
                parts.append(
                    "<tr>"
                    f"<td width='260' style='color:#a6e3a1;"
                    f"white-space:nowrap;vertical-align:top'>{cmd}</td>"
                    f"<td style='color:#cdd6f4;vertical-align:top'>{desc}</td>"
                    "</tr>"
                )
            parts.append("</table>")
        parts.append("</div>")
        return "".join(parts)
