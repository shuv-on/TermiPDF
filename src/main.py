"""
main.py — TermiPDF entry point.

Run with:
    source .venv/bin/activate
    python src/main.py

This file is intentionally minimal: it only constructs the QApplication,
loads the QSS theme, and shows the main window. All feature logic lives
under src/features/.
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

from shared.utils.path_solver import style_path  # noqa: E402
from main_window import TermiPDFWindow             # noqa: E402


def _load_stylesheet(app: QApplication) -> None:
    qss_path = style_path("modern_theme.qss")
    try:
        with open(qss_path, "r", encoding="utf-8") as fh:
            app.setStyleSheet(fh.read())
    except FileNotFoundError:
        # Without the QSS the app still works (falls back to defaults).
        print(f"[TermiPDF] Warning: theme file not found at {qss_path}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TermiPDF")
    app.setOrganizationName("TermiPDF")

    _load_stylesheet(app)

    window = TermiPDFWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())