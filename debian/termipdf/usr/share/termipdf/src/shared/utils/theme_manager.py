"""
theme_manager.py — Dark/light theme switching with QSettings persistence.

Detects the OS preference on first launch, lets the user toggle via a
toolbar button, and persists the choice across sessions via QSettings.
The QSS for both themes lives in src/shared/styles/.

Usage:
    tm = ThemeManager()
    tm.apply_to(app)              # at startup, after QApplication exists
    tm.toggle()                   # user clicks the sun/moon button
    tm.themeChanged.connect(...)
"""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication

from shared.utils.path_solver import style_path


VALID_THEMES = ("dark", "light")
ORG_NAME = "TermiPDF"
APP_NAME = "TermiPDF"
SETTING_KEY = "ui/theme"
AUTO_VALUE = "auto"


class ThemeManager(QObject):
    """Manage the active theme + persist user choice."""

    themeChanged = pyqtSignal(str)   # "dark" | "light"

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._settings = QSettings(ORG_NAME, APP_NAME)
        self._current: str = ""

    # --------------------------------------------------------------- API
    def current(self) -> str:
        """Return the active theme name (after detection if 'auto')."""
        if not self._current:
            self._current = self._resolve()
        return self._current

    def stored(self) -> str:
        """Return the raw stored value: 'dark' | 'light' | 'auto'."""
        v = self._settings.value(SETTING_KEY, AUTO_VALUE)
        if isinstance(v, str) and v in VALID_THEMES:
            return v
        return AUTO_VALUE

    def set(self, theme: str) -> None:
        """Set + persist theme. Accepts 'dark' or 'light'.

        Re-applies the active QSS + palette to the live QApplication so
        the visible UI updates immediately. Without this re-apply the
        ``themeChanged`` signal only refreshes non-QSS items (icon, mode
        badge) and the chrome stays in the old theme — which is what the
        user sees when they click the toolbar toggle and "nothing
        happens".
        """
        theme = theme.lower()
        if theme not in VALID_THEMES:
            raise ValueError(f"Unknown theme: {theme!r}")
        if self._settings.value(SETTING_KEY, AUTO_VALUE) == theme and self._current == theme:
            return
        self._settings.setValue(SETTING_KEY, theme)
        self._current = theme
        # Re-apply the QSS to the live app BEFORE emitting the signal so
        # any handler that reads ``self.current()`` and re-paints
        # already sees the new palette.
        app = QApplication.instance()
        if app is not None:
            self.apply_to(app)
        self.themeChanged.emit(theme)

    def toggle(self) -> None:
        """Toggle between dark and light, respecting auto-detect on first call."""
        self.set("light" if self.current() == "dark" else "dark")

    def follow_system(self) -> None:
        """Clear override; will detect OS preference on next read."""
        self._settings.setValue(SETTING_KEY, AUTO_VALUE)
        self._current = self._resolve()
        self.themeChanged.emit(self._current)

    # ------------------------------------------------------- application
    def apply_to(self, app: QApplication) -> None:
        """Load QSS + palette for the current theme. Idempotent."""
        theme = self.current()
        qss_path = style_path(f"theme_{theme}.qss")
        qss = ""
        try:
            with open(qss_path, "r", encoding="utf-8") as fh:
                qss = fh.read()
        except FileNotFoundError:
            # Fallback to the legacy single theme if split files are missing
            legacy = style_path("modern_theme.qss")
            try:
                with open(legacy, "r", encoding="utf-8") as fh:
                    qss = fh.read()
            except FileNotFoundError:
                qss = ""

        app.setStyleSheet(qss)
        app.setPalette(self._build_palette(theme))

    # -------------------------------------------------------- internals
    def _resolve(self) -> str:
        stored = self.stored()
        if stored in VALID_THEMES:
            return stored
        return self.detect_os_preference()

    @staticmethod
    def detect_os_preference() -> str:
        """Inspect the application palette to choose a theme."""
        app = QApplication.instance()
        if app is None:
            return "dark"
        try:
            bg = app.palette().color(QPalette.ColorRole.Window)
            # Lightness heuristic — Qt gives us an HSL value via getHsl
            h, s, l, _ = bg.getHslF()
            return "light" if l > 0.55 else "dark"
        except Exception:
            return "dark"

    @staticmethod
    def _build_palette(theme: str) -> QPalette:
        """Construct a QPalette matching the chosen theme."""
        pal = QPalette()
        if theme == "light":
            # Edge-light values
            pal.setColor(QPalette.ColorRole.Window,         QColor("#fafafa"))
            pal.setColor(QPalette.ColorRole.WindowText,     QColor("#1f1f1f"))
            pal.setColor(QPalette.ColorRole.Base,            QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.AlternateBase,   QColor("#f0f0f0"))
            pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.ToolTipText,     QColor("#1f1f1f"))
            pal.setColor(QPalette.ColorRole.Text,            QColor("#1f1f1f"))
            pal.setColor(QPalette.ColorRole.Button,          QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.ButtonText,      QColor("#1f1f1f"))
            pal.setColor(QPalette.ColorRole.Highlight,       QColor("#0067c0"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#888888"))
            pal.setColor(QPalette.ColorRole.Link,            QColor("#0067c0"))
        else:
            # Dracula/Nord dark
            pal.setColor(QPalette.ColorRole.Window,         QColor("#1e1e2e"))
            pal.setColor(QPalette.ColorRole.WindowText,     QColor("#cdd6f4"))
            pal.setColor(QPalette.ColorRole.Base,            QColor("#11111b"))
            pal.setColor(QPalette.ColorRole.AlternateBase,   QColor("#1e1e2e"))
            pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#1e1e2e"))
            pal.setColor(QPalette.ColorRole.ToolTipText,     QColor("#cdd6f4"))
            pal.setColor(QPalette.ColorRole.Text,            QColor("#cdd6f4"))
            pal.setColor(QPalette.ColorRole.Button,          QColor("#313244"))
            pal.setColor(QPalette.ColorRole.ButtonText,      QColor("#cdd6f4"))
            pal.setColor(QPalette.ColorRole.Highlight,       QColor("#89b4fa"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#11111b"))
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6c7086"))
            pal.setColor(QPalette.ColorRole.Link,            QColor("#89b4fa"))
        return pal
