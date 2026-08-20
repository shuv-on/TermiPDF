"""
terminal_ui.py — Hacker-style CLI widget with syntax highlighting.

Provides:
- A read-only colored output area.
- A command input line (with up/down history).
- A close button to hide the terminal.
- Emits a signal with the entered command.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import (
    QFont,
    QFontDatabase,
    QKeyEvent,
    QTextCursor,
    QTextCharFormat,
    QColor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QLineEdit,
    QPushButton,
    QLabel,
)


# Theme-aware palette. The colours are picked at first read (and every
# time ``rebind_palette`` is called) from the running QApplication's
# palette + named accent colours that work in both themes.
class _TermColorsMeta(type):
    """Metaclass so ``TermColors.ACCENT`` works at *class* access time.

    A normal ``__getattr__`` classmethod is only invoked for *instance*
    attribute misses; class-level attribute access skips it entirely. The
    metaclass intercepts ``TermColors.<name>`` and returns the colour from
    the current theme table on every read.
    """

    def __getattr__(cls, name: str):
        # Skip dunder / private lookups — never shadow real class machinery.
        if name.startswith("_") or name in (
            "is_dark", "_table", "_DARK", "_LIGHT",
        ):
            raise AttributeError(name)
        table = cls._DARK if cls.is_dark() else cls._LIGHT
        if name in table:
            return table[name]
        raise AttributeError(f"TermColors has no {name!r}")


class TermColors(metaclass=_TermColorsMeta):
    """Hacker-style terminal palette that adapts to the active theme.

    We expose hex strings because QTextEdit's HTML formatter wants raw
    colour names. The QSS may have just refreshed in another module, so
    every read goes back to QApplication to pick up the latest palette.
    """

    # Theme-specific accents that aren't in the palette. These keep the
    # "hacker terminal" vibe while staying legible on either background.
    _DARK = {
        "PROMPT":  "#a6e3a1",
        "COMMAND": "#cdd6f4",
        "INFO":    "#89b4fa",
        "SUCCESS": "#a6e3a1",
        "WARNING": "#f9e2af",
        "ERROR":   "#f38ba8",
        "ACCENT":  "#cba6f7",
        "MUTED":   "#6c7086",
    }
    _LIGHT = {
        # Light theme: deeper, saturated accent colours so they pop on
        # white. The "muted" and "command" tones come from the palette
        # via ``_fg`` / ``_muted`` below for a consistent feel.
        "PROMPT":  "#2e8b3e",   # green ~700
        "COMMAND": "#1f1f1f",   # text
        "INFO":    "#0067c0",   # link / focus blue
        "SUCCESS": "#2e8b3e",
        "WARNING": "#a45a00",   # amber
        "ERROR":   "#c42b1c",   # danger red (matches QPushButton#danger)
        "ACCENT":  "#7c3aed",   # purple 600 — readable on white
        "MUTED":   "#6c7086",   # slate
    }

    @classmethod
    def is_dark(cls) -> bool:
        app = QApplication.instance()
        if app is None:
            return True
        try:
            from PyQt6.QtGui import QPalette
            bg = app.palette().color(QPalette.ColorRole.Window)
            h, s, l, _ = bg.getHslF()
            return l <= 0.55
        except Exception:
            return True


class TerminalUI(QWidget):
    """Hacker-style embedded terminal widget."""

    command_entered = pyqtSignal(str)
    close_requested = pyqtSignal()

    HISTORY_LIMIT = 200

    def __init__(self, parent=None):
        super().__init__(parent)

        self._history: list[str] = []
        self._history_idx: int = -1  # -1 == "current line"

        self._build_ui()
        self._print_banner()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Header row ------------------------------------------------------
        header = QHBoxLayout()
        title = QLabel("● termipdf ~ hacker-console")
        title.setStyleSheet(f"color: {TermColors.ACCENT}; font-weight: bold;")
        self._title_label = title
        header.addWidget(title)
        header.addStretch(1)

        self.mode_label = QLabel("mode: view")
        self.mode_label.setObjectName("modeLabel")
        header.addWidget(self.mode_label)

        self.clear_btn = QPushButton("clear")
        self.clear_btn.clicked.connect(self.clear_output)
        header.addWidget(self.clear_btn)

        self.close_btn = QPushButton("✖ close")
        self.close_btn.setObjectName("danger")
        self.close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(self.close_btn)

        root.addLayout(header)

        # Output ----------------------------------------------------------
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("JetBrains Mono", 11))
        self.output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.output, 1)

        # Input row -------------------------------------------------------
        input_row = QHBoxLayout()
        prompt_label = QLabel("➜")
        prompt_label.setStyleSheet(
            f"color: {TermColors.PROMPT}; font-weight: bold; font-size: 16px;"
        )
        self._prompt_label = prompt_label
        prompt_label.setFixedWidth(20)
        input_row.addWidget(prompt_label)

        self.input = QLineEdit()
        self.input.setPlaceholderText("type a command and press Enter  (try: help)")
        self.input.returnPressed.connect(self._on_enter)
        input_row.addWidget(self.input, 1)

        root.addLayout(input_row)

    def _print_banner(self):
        # ASCII banner — kept short and bold; no logo icon (icons are toolbar-only).
        # Colours come from TermColors so the banner adapts to the active theme
        # (and re-prints cleanly when the theme flips, via rebind_palette()).
        self.output.append(
            f"<pre style='color:{TermColors.ACCENT};font-weight:bold;line-height:1.0'>"
            " ▀█▀ ▄▀█ █▀ █ █▀▀ █▀▀ █▄░█ █▀▀ █▀█\n"
            " █░█ █▀█ ▄█ █ ██▄ ██▄ █░▀█ ██▄ █▀▄\n"
            f"  v2  · hacker console  · type <b style='color:{TermColors.SUCCESS}'>help</b> to start"
            "</pre>"
        )
        self._info("TermiPDF OS v2.0 initialized. Type <b>help</b> to list commands.")

    # ------------------------------------------------------------ Output
    def _append_html(self, html: str):
        self.output.append(html)
        # Auto-scroll to bottom
        sb = self.output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def append_output(self, html_text: str):
        """Append raw HTML (used by main window)."""
        self._append_html(html_text)

    def _info(self, text: str):
        self._append_html(f"<span style='color:{TermColors.INFO}'>» {text}</span>")

    def _success(self, text: str):
        self._append_html(f"<span style='color:{TermColors.SUCCESS}'>✓ {text}</span>")

    def _warn(self, text: str):
        self._append_html(f"<span style='color:{TermColors.WARNING}'>⚠ {text}</span>")

    def _error(self, text: str):
        self._append_html(f"<span style='color:{TermColors.ERROR}'>✗ {text}</span>")

    def _muted(self, text: str):
        self._append_html(f"<span style='color:{TermColors.MUTED}'>{text}</span>")

    def print_user_command(self, text: str):
        """Echo a user-typed command in the output."""
        self._append_html(
            f"<span style='color:{TermColors.PROMPT}'>➜ </span>"
            f"<span style='color:{TermColors.COMMAND}'>{self._escape(text)}</span>"
        )

    def clear_output(self):
        self.output.clear()
        self._print_banner()

    # ----------------------------------------------------------- Theming
    def rebind_palette(self) -> None:
        """Re-pick the theme-aware colours and refresh chrome that is
        ``setStyleSheet``-driven (the title label, prompt glyph, banner).

        Called from ``main_window`` when the theme flips. Output text
        already uses ``TermColors.<name>`` at append time, so any text
        produced *after* the flip automatically picks up the new colour.
        """
        # 1. Title label ("● termipdf ~ hacker-console")
        if hasattr(self, "_title_label"):
            self._title_label.setStyleSheet(
                f"color: {TermColors.ACCENT}; font-weight: bold;"
            )
        # 2. Prompt glyph (the "›" before the input box)
        if hasattr(self, "_prompt_label"):
            self._prompt_label.setStyleSheet(
                f"color: {TermColors.PROMPT}; font-weight: bold; font-size: 16px;"
            )
        # 3. Re-emit the banner so the big purple ASCII art picks up the
        #    new accent colour. The history/buffer above isn't touched
        #    (we don't rewrite the past — only future chrome updates).
        if hasattr(self, "output") and self.output.document().blockCount() <= 4:
            # Output is essentially empty (just the banner) → safe to
            # refresh so the user sees the accent change immediately.
            self.output.clear()
            self._print_banner()

    # ----------------------------------------------------------- Helpers
    @staticmethod
    def _escape(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def set_mode_label(self, mode_text: str):
        self.mode_label.setText(f"mode: {mode_text}")

    # ----------------------------------------------------- Input handling
    def _on_enter(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._history_idx = -1

        # Add to history (de-dup consecutive dupes)
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        if len(self._history) > self.HISTORY_LIMIT:
            self._history = self._history[-self.HISTORY_LIMIT :]

        self.print_user_command(text)
        self.command_entered.emit(text)

    def keyPressEvent(self, event: QKeyEvent):
        # History navigation with Up/Down arrows
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if not self._history:
                return super().keyPressEvent(event)
            if event.key() == Qt.Key.Key_Up:
                if self._history_idx == -1:
                    self._history_idx = len(self._history) - 1
                elif self._history_idx > 0:
                    self._history_idx -= 1
            else:  # Down
                if self._history_idx == -1:
                    return super().keyPressEvent(event)
                if self._history_idx < len(self._history) - 1:
                    self._history_idx += 1
                else:
                    self._history_idx = -1
                    self.input.clear()
                    return
            self.input.setText(self._history[self._history_idx])
            return
        super().keyPressEvent(event)
