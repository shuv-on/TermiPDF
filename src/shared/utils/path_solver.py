"""Global path / resource helpers for TermiPDF."""
from __future__ import annotations

import os
import sys
from pathlib import Path


# ----- Project Root resolution ----------------------------------------------
def project_root() -> Path:
    """Return the TermiPDF project root directory."""
    # src/shared/utils/path_solver.py -> up 3 levels
    return Path(__file__).resolve().parents[3]


def src_root() -> Path:
    """Return the src/ directory."""
    return project_root() / "src"


def asset_path(*parts: str) -> str:
    """Resolve a path inside src/shared/assets/."""
    return str(src_root() / "shared" / "assets" / Path(*parts))


def style_path(*parts: str) -> str:
    """Resolve a path inside src/shared/styles/."""
    return str(src_root() / "shared" / "styles" / Path(*parts))


def font_path(filename: str = "Kalpurush.ttf") -> str:
    """Return absolute path to a font file in shared/assets/."""
    return asset_path(filename)


def resolve_user_path(raw: str) -> str:
    """Expand ~, vars, and normalize a user-supplied path."""
    if not raw:
        return raw
    expanded = os.path.expandvars(os.path.expanduser(raw.strip().strip('"').strip("'")))
    return os.path.normpath(expanded)


def is_pdf_file(path: str) -> bool:
    """Quick check for a PDF file."""
    return path.lower().endswith(".pdf") and os.path.isfile(path)
