"""
recent_files.py — Persistent list of recently opened PDFs.

Stored as JSON at the platform-appropriate config dir (Flatpak/Snap
sandbox-friendly via ``$XDG_CONFIG_HOME`` / ``QStandardPaths``).

Keeps a maximum of 8 entries. Paths are stored absolute; existing entries
are de-duplicated by absolute path. Files that no longer exist are filtered
out on read.
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from PyQt6.QtCore import QStandardPaths

_log = logging.getLogger(__name__)

MAX_ENTRIES = 8


def _config_dir() -> str:
    """Return the platform-appropriate config directory for TermiPDF.

    Resolution order:
      1. ``$XDG_CONFIG_HOME`` if set (Flathub / Snap / Linux userland).
      2. ``QStandardPaths.AppConfigLocation`` (Qt's per-OS default,
         e.g. ``~/Library/Application Support`` on macOS).
      3. ``~/.config/TermiPDF`` as a last-ditch fallback.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        base = os.path.join(xdg, "TermiPDF")
    else:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppConfigLocation
        )
        if not base:
            base = os.path.join(os.path.expanduser("~"),
                               ".config", "TermiPDF")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError as exc:
        _log.warning("recent_files: cannot create config dir %s: %s",
                     base, exc)
        # Fall back to a per-process temp dir so the app still works
        # in read-only environments (Flatpak strict mode, locked
        # down snap, etc.).
        import tempfile
        base = tempfile.mkdtemp(prefix="termipdf_recent_")
    return base


class RecentFiles:
    """JSON-backed recent-files store."""

    def __init__(self, filename: str = "recent.json", path: Optional[str] = None):
        if path is not None:
            self.path = path
        else:
            self.path = os.path.join(_config_dir(), filename)
        self._items: List[str] = []
        self._load()

    # -------------------------------------------------------------- public
    def add(self, file_path: str) -> None:
        """Add a file to the list (de-duped, most-recent first)."""
        if not file_path:
            return
        abs_path = os.path.abspath(file_path)
        if abs_path in self._items:
            self._items.remove(abs_path)
        self._items.insert(0, abs_path)
        if len(self._items) > MAX_ENTRIES:
            self._items = self._items[:MAX_ENTRIES]
        self._save()

    def list(self) -> List[str]:
        """Return the current list, dropping entries whose files vanished."""
        existing = [p for p in self._items if os.path.isfile(p)]
        if existing != self._items:
            self._items = existing
            self._save()
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._save()

    # ----------------------------------------------------------- internal
    def _load(self) -> None:
        if not os.path.isfile(self.path):
            self._items = []
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self._items = [str(p) for p in data]
            else:
                self._items = []
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            _log.warning("recent_files: failed to load %s: %s",
                         self.path, exc)
            self._items = []

    def _save(self) -> None:
        try:
            # Atomic write: write to .tmp then rename. Prevents a
            # half-written JSON file if the process is killed mid-save.
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._items, f, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            # Surface the failure — silently dropping recent-files
            # writes is a data-loss footgun.
            _log.error("recent_files: failed to save %s: %s",
                       self.path, exc)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass