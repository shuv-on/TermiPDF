"""
main.py — TermiPDF entry point.

Run with:
    source .venv/bin/activate
    python src/main.py

This file is intentionally minimal: it only constructs the QApplication,
applies the active theme, and shows the main window. All feature logic
lives under src/features/.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

# Make 'src.' imports work whether the user runs `python src/main.py` from
# the project root OR `python -m src.main`.
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from shared.utils.theme_manager import ThemeManager  # noqa: E402
from main_window import TermiPDFWindow               # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TermiPDF")
    app.setOrganizationName("TermiPDF")

    # Theme is applied inside TermiPDFWindow so it can react to toggle
    # events from the toolbar. We just construct the window here.
    window = TermiPDFWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())