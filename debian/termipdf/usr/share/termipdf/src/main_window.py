"""
main_window.py — The top-level orchestrator (TermiPDF v2).

This is the Edge-style main shell. It owns the QApplication theme, the
toolbar (icon-only), the menubar, the left rail (TOC + thumbnails),
the PDF canvas (central widget), the dockable terminal, the status bar,
and the command parser.

The individual features (viewer, annotator, editor, QR, terminal) live
under src/features/ and remain unchanged at their API surface. This file
just wires them together and dispatches commands.

Architecture rules (unchanged from v1):
* Features never import other features.
* Command handlers are registered on the parser from this file.
* Each handler returns CommandResult, never directly mutating the UI.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer, QEvent, QPoint, QPointF, QSize
from PyQt6.QtGui import (
    QAction, QKeySequence, QShortcut, QCloseEvent, QImage, QPainter,
    QCursor,
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QToolBar, QToolButton, QStatusBar, QLabel,
    QMenuBar, QMenu, QMessageBox, QFileDialog, QApplication,
    QDockWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QStackedWidget, QFrame, QSizePolicy, QInputDialog,
)

# ---- Feature imports ------------------------------------------------------
from features.terminal.terminal_ui import TerminalUI
from features.terminal.command_parser import CommandParser, CommandResult

from features.pdf_viewer.viewer_ui import PDFViewerUI, CanvasMode
from features.pdf_viewer.viewer_engine import ViewerEngine
from features.pdf_viewer.toc_ui import TOCUI

from features.pdf_annotator.annotation_engine import AnnotationEngine
from features.pdf_annotator.canvas_events import CanvasEventRouter

from features.pdf_editor.text_editor import TextEditor
from features.pdf_editor.manipulation import PDFManipulator

from features.qr_generator.qr_logic import QRLogic

from features.pdf_viewer.swatch_bar import SwatchBar
from features.pdf_viewer.find_bar import FindBar
from features.pdf_viewer.recent_files import RecentFiles
from features.pdf_viewer.pages_manager import PagesManager
from features.pdf_editor.undo_stack import UndoStack

import fitz  # used in print path

# ---- Shared ---------------------------------------------------------------
from shared.utils.icon_factory import IconFactory
from shared.utils.theme_manager import ThemeManager
from shared.utils.color_utils import parse_color
from shared.utils.path_solver import resolve_user_path, is_pdf_file


def _parse_p_range(token: str):
    """Parse a ``p-N`` or ``p-N-M`` token (as used by ``merge p-1 p-10``).

    Returns a (lo, hi) tuple (inclusive). Raises ValueError on bad input.
    """
    if not token.startswith("p-"):
        raise ValueError(f"Expected p-N or p-N-M, got '{token}'")
    body = token[2:]
    if "-" in body:
        a, b = body.split("-", 1)
        try:
            lo, hi = int(a), int(b)
        except ValueError:
            raise ValueError(f"Bad page numbers in '{token}'")
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi)
    try:
        n = int(body)
    except ValueError:
        raise ValueError(f"Bad page number in '{token}'")
    return (n, n)


def _short_tab_title(name: str, max_len: int = 15) -> str:
    """Trim a filename to ≤max_len chars with a Unicode ellipsis.

    Strategy: keep the extension visible if possible. If ``name`` ends
    in a short extension (≤5 chars), split stem + ext and elide the
    stem first so the user can still see the file type. Examples::

        "report.pdf"            → "report.pdf"
        "AnnualReport.pdf"      → "AnnualR…ort.pdf"  (too long, mid-stem elide)
        "Q4-2026-data.pdf"      → "Q4-202…ata.pdf"
        "no-extension-filename" → "no-exte…name"   (no `.`, plain elide)

    The full name (and path) is still exposed via the tab tooltip —
    hover to see it.
    """
    if len(name) <= max_len:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and ext and len(ext) <= 5:
        # We have a short extension — try to keep it visible.
        keep = max_len - len(ext) - 2  # 1 for '.', 1 for '…'
        if keep >= 4:
            return f"{stem[:keep]}…{ext}"
    # No short extension: simple mid-string elide.
    keep = max_len - 1  # 1 char for the ellipsis
    return f"{name[:keep]}…"


def _parse_page_spec(spec: str) -> list:
    """Parse a comma-separated page spec like ``p-1,2,3`` or ``1,2,3`` or
    ``p-1,2-4,7``. Returns a flat list of 1-based page numbers.

    Whitespace is stripped; ``p-`` prefix is optional.
    """
    if not spec:
        raise ValueError("Empty page spec.")
    # Normalize: drop a leading "p-" if present on the whole string.
    raw = spec.strip()
    out: list = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("p-"):
            a, b = _parse_p_range(part)
        else:
            if "-" in part:
                try:
                    a_s, b_s = part.split("-", 1)
                    a, b = int(a_s), int(b_s)
                except ValueError:
                    raise ValueError(f"Bad page token: '{part}'")
            else:
                try:
                    a = b = int(part)
                except ValueError:
                    raise ValueError(f"Bad page number: '{part}'")
        if a > b:
            a, b = b, a
        out.extend(range(a, b + 1))
    if not out:
        raise ValueError("Page spec parsed to zero pages.")
    return out


# =====================================================================
# Section: AutoHideController (Edge-style chrome hiding)
# =====================================================================
class AutoHideController(QObject if False else object):
    """Tracks mouse inactivity to hide/show chrome bars (toolbar, dock)."""
    pass  # placeholder, real impl lives in Phase 2


# =====================================================================
# Main window
# =====================================================================
class TermiPDFWindow(QMainWindow):
    """Edge-style TermiPDF main window."""

    APP_NAME = "TermiPDF"

    # ---------------------------------------------------------------- init
    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.APP_NAME)
        self.setAcceptDrops(True)

        # ---- Core services ------------------------------------------------
        # self.engine / self.pdf_viewer / self.annot / self.editor are
        # populated by _build_canvas() when the placeholder session is
        # created, then re-pointed at whichever tab is active.
        self.qr = QRLogic(None)            # re-bound to current engine on demand
        self.parser = CommandParser()
        self.recent = RecentFiles()
        self.undo_stack = UndoStack(None)  # re-bound to current engine on demand

        # Dirty-tracking: flips when editing ops modify the document, and
        # resets on save / open. ``has_unsaved_changes`` is read by the
        # tab title and the close-confirmation flow; ``mark_unsaved()``
        # is the explicit toggle annotation ops call after a write.
        self.has_unsaved_changes: bool = False
        # Method form is bound via the class definition below.

        # ---- UI state -----------------------------------------------------
        # Initialized BEFORE _build_*() so the toolbar can register its
        # checkable tool buttons into self._tool_buttons.
        # Default is VIEW (read-only canvas with the open-hand cursor).
        # The user opts in to SELECT / DRAW / HIGHLIGHT / etc. by picking
        # a toolbar button or running ``mode <name>`` in the terminal.
        self._active_tool = CanvasMode.VIEW
        self._tool_buttons: dict[str, QToolButton] = {}
        self._active_pen_color: tuple[float, float, float] = (1.0, 0.0, 0.0)
        self._active_pen_thickness: float = 2.0
        self._active_highlight_color: tuple[float, float, float] = (1.0, 0.95, 0.0)

        # ---- Theme --------------------------------------------------------
        self.theme = ThemeManager(self)
        self.theme.themeChanged.connect(self._on_theme_changed)
        self.theme.apply_to(QApplication.instance())

        # ---- UI -----------------------------------------------------------
        self._build_menubar()
        self._build_toolbar()
        self._build_statusbar()
        self._build_left_rail()
        self._build_canvas()
        self._build_find_bar()
        self._build_terminal_dock()
        self._register_commands()
        self._wire_signals()
        self._wire_shortcuts()

        # ---- Auto-hide chrome (Phase 2 features) --------------------------
        self._chrome_pinned: bool = True
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(800)
        self._auto_hide_timer.timeout.connect(self._hide_chrome_if_maximized)
        self.setMouseTracking(True)

        # Active mode tracking — toolbar buttons reflect this
        # (initialized earlier; this block kept for reference / explicit no-op)

        # ---- Final window state — maximize on first show ------------------
        self.resize(1400, 900)
        # Defer showMaximized until after the window is shown to avoid
        # Qt warnings on some platforms.
        QTimer.singleShot(0, lambda: self.setWindowState(
            Qt.WindowState.WindowMaximized
        ))

        self._update_mode_badge("view")
        # Sync the canvas's mode to our default (VIEW) and reflect it
        # in the toolbar's checkable buttons.
        try:
            self.pdf_viewer.set_mode(CanvasMode.VIEW)
        except Exception:
            pass
        self._sync_tool_buttons(CanvasMode.VIEW)
        self._update_page_indicator(0, 0)

    # Defined early so the menubar (built first) can connect to it.
    def toggle_terminal(self):
        """Show or hide the terminal dock. Defined early because the
        menubar wires it during _build_menubar()."""
        self.term_dock.setVisible(not self.term_dock.isVisible())

    # ====================================================================
    # BUILD: menubar
    # ====================================================================
    def _build_menubar(self):
        mb = QMenuBar(self)
        self.setMenuBar(mb)

        m_file = mb.addMenu("&File")
        a_open = m_file.addAction("Open PDF…")
        a_open.setShortcut(QKeySequence.StandardKey.Open)
        a_open.triggered.connect(self._open_file_dialog)

        self.recent_menu = m_file.addMenu("Open Recent")
        self._rebuild_recent_menu()

        m_file.addSeparator()
        a_save = m_file.addAction("Save")
        a_save.setShortcut(QKeySequence.StandardKey.Save)
        a_save.triggered.connect(lambda: self._render_result(self._cmd_save([])))
        a_saveas = m_file.addAction("Save As…")
        a_saveas.setShortcut(QKeySequence.StandardKey.SaveAs)
        a_saveas.triggered.connect(self._save_as_dialog)

        m_file.addSeparator()
        a_print = m_file.addAction("Print…")
        a_print.setShortcut(QKeySequence.StandardKey.Print)
        a_print.triggered.connect(self._action_print)
        m_file.addSeparator()
        a_exit = m_file.addAction("Exit")
        a_exit.setShortcut(QKeySequence.StandardKey.Quit)
        a_exit.triggered.connect(self.close)

        m_edit = mb.addMenu("&Edit")
        a_undo = m_edit.addAction("Undo")
        a_undo.setShortcut(QKeySequence.StandardKey.Undo)
        a_undo.triggered.connect(lambda: self._render_result(self._cmd_undo([])))
        a_redo = m_edit.addAction("Redo")
        a_redo.setShortcut(QKeySequence.StandardKey.Redo)
        a_redo.triggered.connect(lambda: self._render_result(self._cmd_redo([])))

        m_view = mb.addMenu("&View")
        a_toc = m_view.addAction("Toggle Outline")
        a_toc.setShortcut("Ctrl+B")
        a_toc.triggered.connect(self._toggle_toc)
        a_thumbs = m_view.addAction("Toggle Thumbnails")
        a_thumbs.setShortcut("Ctrl+Shift+B")
        a_thumbs.triggered.connect(self._toggle_thumbs)
        m_view.addSeparator()
        a_term = m_view.addAction("Toggle Terminal")
        a_term.setShortcut("Ctrl+J")
        a_term.triggered.connect(self.toggle_terminal)
        m_view.addSeparator()
        a_fs = m_view.addAction("Fullscreen")
        a_fs.setShortcut("F11")
        a_fs.triggered.connect(self._action_fullscreen)
        a_fit = m_view.addAction("Fit to Window")
        a_fit.setShortcut("Ctrl+0")
        a_fit.triggered.connect(lambda: self._render_result(self._cmd_fit([])))
        m_view.addSeparator()
        a_theme = m_view.addAction("Toggle Theme")
        a_theme.setShortcut("Ctrl+Shift+T")
        a_theme.triggered.connect(self._action_toggle_theme)

        m_tools = mb.addMenu("&Tools")
        a_find = m_tools.addAction("Find")
        a_find.setShortcut(QKeySequence.StandardKey.Find)
        a_find.triggered.connect(self._action_open_find)

        m_help = mb.addMenu("&Help")
        a_about = m_help.addAction("About TermiPDF")
        a_about.triggered.connect(self._action_about)

    def _rebuild_recent_menu(self):
        """Rebuild the recent-files submenu from the persistent store."""
        self.recent_menu.clear()
        items = self.recent.list()
        if not items:
            no_recent = self.recent_menu.addAction("(no recent files)")
            no_recent.setEnabled(False)
            return
        for path in items:
            name = os.path.basename(path) or path
            act = self.recent_menu.addAction(name)
            act.setToolTip(path)
            act.triggered.connect(lambda _=False, p=path: self._open_recent(p))
        self.recent_menu.addSeparator()
        clear_act = self.recent_menu.addAction("Clear list")
        clear_act.triggered.connect(self._clear_recent)

    # ====================================================================
    # BUILD: toolbar (icon-only, Edge-style)
    # ====================================================================
    def _build_toolbar(self):
        tb = QToolBar("MainBar")
        tb.setObjectName("MainBar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        self.main_toolbar = tb

        # ---------- File group -----------
        self._add_toolbar_button(tb, "open", "Open PDF…", "Ctrl+O",
                                  self._open_file_dialog)
        self._add_toolbar_button(tb, "save", "Save", "Ctrl+S",
                                  lambda: self._render_result(self._cmd_save([])))

        tb.addSeparator()

        # ---------- Undo/redo -----------
        self._add_toolbar_button(tb, "undo", "Undo", "Ctrl+Z",
                                  lambda: self._render_result(self._cmd_undo([])))
        self._add_toolbar_button(tb, "redo", "Redo", "Ctrl+Y",
                                  lambda: self._render_result(self._cmd_redo([])))

        tb.addSeparator()

        # ---------- Navigation group (Prev / Page x/y / Next) -----------
        self._add_toolbar_button(tb, "prev", "Previous Page", "Alt+Left",
                                  lambda: self._render_result(self._cmd_prev([])))

        page_input = QToolButton()
        page_input.setObjectName("pageInput")
        page_input.setText("1 / 1")
        page_input.setToolTip("Current / Total pages")
        page_input.setCheckable(False)
        page_input.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)  # type: ignore
        page_input.setStyleSheet("font-weight: bold; padding: 0 10px;")
        page_input.clicked.connect(self._action_focus_page_input)
        tb.addWidget(page_input)
        self.page_input_btn = page_input

        self._add_toolbar_button(tb, "next", "Next Page", "Alt+Right",
                                  lambda: self._render_result(self._cmd_next([])))

        tb.addSeparator()

        # ---------- Zoom group -----------
        self._add_toolbar_button(tb, "zoom-out", "Zoom Out", "Ctrl+-",
                                  lambda: self._render_result(self._cmd_zoom(["out"])))
        zoom_label = QLabel("100%")
        zoom_label.setObjectName("toolbarLabel")
        tb.addWidget(zoom_label)
        self.zoom_label = zoom_label
        self._add_toolbar_button(tb, "zoom-in", "Zoom In", "Ctrl+=",
                                  lambda: self._render_result(self._cmd_zoom(["in"])))
        self._add_toolbar_button(tb, "zoom-fit", "Fit to Window", "Ctrl+0",
                                  lambda: self._render_result(self._cmd_fit([])))

        tb.addSeparator()

        # ---------- Tools (annotation modes) -----------
        pen_btn = self._add_toolbar_button(tb, "pen", "Pen (Draw)", "D",
                                            lambda: self._toggle_tool_via_cmd("draw"),
                                            checkable=True)
        self._add_toolbar_button(tb, "highlight", "Highlighter", "H",
                                  lambda: self._toggle_tool_via_cmd("highlight"),
                                  checkable=True)
        self._add_toolbar_button(tb, "eraser", "Erase", "E",
                                  lambda: self._toggle_tool_via_cmd("erase"),
                                  checkable=True)
        self._add_toolbar_button(tb, "select", "Select Text (S)", "S",
                                  lambda: self._toggle_tool_via_cmd("select"),
                                  checkable=True)

        tb.addSeparator()

        self._add_toolbar_button(tb, "text", "Insert Text", "T",
                                  lambda: self._toggle_tool_via_cmd("text"),
                                  checkable=True)
        self._add_toolbar_button(tb, "note", "Sticky Note", "N",
                                  lambda: self._toggle_tool_via_cmd("note"),
                                  checkable=True)
        self._add_toolbar_button(tb, "qr", "QR Code (popup)  (Q)", "Q",
                                  lambda: self._action_qr_popup())
        self._add_toolbar_button(tb, "stamp", "Signature Stamp", None,
                                  lambda: self._toggle_tool_via_cmd("signature"),
                                  checkable=True)
        self._add_toolbar_button(tb, "rect", "Rectangle", None,
                                  lambda: self._toggle_tool_via_cmd("rect"),
                                  checkable=True)
        self._add_toolbar_button(tb, "ellipse", "Ellipse", None,
                                  lambda: self._toggle_tool_via_cmd("ellipse"),
                                  checkable=True)
        self._add_toolbar_button(tb, "arrow", "Arrow", None,
                                  lambda: self._toggle_tool_via_cmd("arrow"),
                                  checkable=True)

        tb.addSeparator()

        # ---------- Color swatches (Edge-style palette) -----------
        self.swatch_bar = SwatchBar()
        self.swatch_bar.color_chosen.connect(self._on_swatch_chosen)
        # Wrap in a fixed-size container so it doesn't stretch the toolbar
        swatch_wrap = QFrame()
        swatch_wrap.setStyleSheet("background: transparent;")
        sw_lay = QVBoxLayout(swatch_wrap)
        sw_lay.setContentsMargins(6, 0, 6, 0)
        sw_lay.addWidget(self.swatch_bar)
        tb.addWidget(swatch_wrap)

        tb.addSeparator()

        # ---------- UI helpers -----------
        self._add_toolbar_button(tb, "toc", "Outline", "Ctrl+B",
                                  self._toggle_toc)
        self._add_toolbar_button(tb, "thumbnails", "Thumbnails", "Ctrl+Shift+B",
                                  self._toggle_thumbs)
        self._add_toolbar_button(tb, "pages", "Pages Manager (grid view)",
                                  "Ctrl+Shift+G", self._action_open_pages)
        self._add_toolbar_button(tb, "search", "Find (Ctrl+F)", "Ctrl+F",
                                  self._action_open_find)
        self._add_toolbar_button(tb, "print", "Print", "Ctrl+P",
                                  self._action_print)
        self._add_toolbar_button(tb, "terminal", "Toggle Terminal", "Ctrl+J",
                                  self.toggle_terminal)
        self._add_toolbar_button(tb, "fullscreen", "Fullscreen (F11)", "F11",
                                  self._action_fullscreen)
        self._add_toolbar_button(tb, "clear", "Clear terminal", None,
                                  lambda: self.terminal.clear_output())
        self._add_toolbar_button(tb, "screenshot",
                                  "Screenshot current page (Ctrl+Shift+S)",
                                  "Ctrl+Shift+S", self._action_screenshot)
        self._add_toolbar_button(tb, "screenshot-region",
                                  "Region screenshot (OS tool)",
                                  None, self._action_screenshot_region)
        self._add_toolbar_button(tb, "rotate", "Rotate current page 90° (Ctrl+R)",
                                  "Ctrl+R", self._action_rotate)

        tb.addSeparator()

        # ---------- Theme toggle ----------
        self.theme_btn = self._add_toolbar_button(
            tb, "moon", "Switch to Light theme", "Ctrl+Shift+T",
            self._action_toggle_theme,
        )
        self._refresh_theme_button()

        # ---------- Edge-style chevron to collapse the toolbar ----------
        # Pressing the chevron tucks the toolbar out of view (and the
        # left rail + terminal stay visible by default). Pressing again
        # reveals it.
        tb.addSeparator()
        self.chevron_btn = self._add_toolbar_button(
            tb, "chevron-up", "Hide toolbar (Ctrl+Shift+H)", "Ctrl+Shift+H",
            self._action_toggle_toolbar,
        )

    def _add_toolbar_button(self, tb, icon_name, tooltip, shortcut,
                             slot, checkable=False) -> QToolButton:
        btn = QToolButton(tb)
        # Stash the source icon name so ``_on_theme_changed`` can re-render
        # the icon with the *new* theme's foreground colour.
        btn._termipdf_icon_name = icon_name
        btn.setIcon(IconFactory.get(icon_name, 20))
        btn.setToolTip(f"{tooltip}  ({shortcut})" if shortcut else tooltip)
        # NB: we deliberately do NOT call btn.setShortcut(shortcut) here
        # because for the common shortcuts (Ctrl+S, Ctrl+J, Ctrl+O, …)
        # the QAction in the menu bar already owns the binding via
        # QKeySequence.StandardKey. Setting the shortcut on the toolbar
        # button too creates a duplicate QAction that fires the same
        # slot, and Qt prints "QAction::event: Ambiguous shortcut
        # overload" warnings at startup. The shortcut is shown in the
        # tooltip so the user still sees the binding.
        if checkable:
            btn.setCheckable(True)
            self._tool_buttons[icon_name] = btn
        btn.clicked.connect(slot)
        tb.addWidget(btn)
        return btn

    # ====================================================================
    # BUILD: status bar (Edge-style page indicator)
    # ====================================================================
    def _build_statusbar(self):
        sb = QStatusBar(self)
        self.setStatusBar(sb)

        self.mode_badge = QLabel("view")
        self.mode_badge.setObjectName("modeBadge")
        sb.addWidget(self.mode_badge)

        spacer = QLabel()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sb.addWidget(spacer, 1)

        # Permanent (right) widgets — like Edge's zoom %
        self.zoom_status = QLabel("100%")
        self.zoom_status.setObjectName("zoomIndicator")
        sb.addPermanentWidget(self.zoom_status)

        self.page_indicator = QLabel("— / —")
        self.page_indicator.setObjectName("pageIndicator")
        sb.addPermanentWidget(self.page_indicator)

    def _update_page_indicator(self, page_1based: int, total: int):
        if total <= 0:
            self.page_indicator.setText("— / —")
            self.page_input_btn.setText("— / —")
        else:
            self.page_indicator.setText(f"{page_1based} / {total}")
            self.page_input_btn.setText(f"{page_1based} / {total}")

    def _active_session(self) -> Optional[dict]:
        """Return the session dict backing the currently focused tab.

        Used by ``mark_unsaved`` (and any other consumer that needs
        the active session's metadata) so the dirty flag and tab
        title stay in sync with which tab the user is on.
        """
        if not hasattr(self, "_tabs") or not hasattr(self, "_sessions"):
            return None
        idx = self._tabs.currentIndex()
        if 0 <= idx < len(self._sessions):
            return self._sessions[idx]
        return None

    def mark_unsaved(self) -> None:
        """Flip the dirty flag and refresh the tab title to show '*'.

        Annotation ops, page-swap, page-rotate, and page-delete call this
        after a successful write. ``save`` (in-place) and ``open`` reset
        the flag back to False.
        """
        self.has_unsaved_changes = True
        sess = self._active_session()
        if sess is not None:
            self._mark_session_dirty(sess, True)

    def _update_mode_badge(self, mode: str):
        self.mode_badge.setText(mode)
        self.mode_badge.setStyleSheet(self._mode_badge_style(mode))

    def _mode_badge_style(self, mode: str) -> str:
        colors = {
            "view":      "#6c7086",
            "draw":      "#a6e3a1",
            "highlight": "#f9e2af",
            "erase":     "#f38ba8",
            "text":      "#89b4fa",
            "note":      "#f9e2af",
            "qr":        "#cba6f7",
            "stamp":     "#fab387",
            "signature": "#fab387",
            "rect":      "#89b4fa",
            "ellipse":   "#89b4fa",
            "arrow":     "#f38ba8",
            "edit-text": "#89b4fa",
        }
        bg = colors.get(mode, "#6c7086")
        return f"background-color:{bg};color:#11111b;padding:2px 8px;border-radius:3px;font-weight:bold;"

    # ====================================================================
    # BUILD: left rail (TOC + thumbnails, with tabs)
    # ====================================================================
    def _build_left_rail(self):
        self.left_dock = QDockWidget("Pages", self)
        self.left_dock.setObjectName("left_dock")
        self.left_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea
                                       | Qt.DockWidgetArea.RightDockWidgetArea)
        self.left_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                                   | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        # Empty title bar (we use tabs instead)
        self.left_dock.setTitleBarWidget(QWidget())

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)

        self.left_stack = QStackedWidget()

        # --- Outline (TOC) page ---
        toc_wrap = QWidget()
        toc_layout = QVBoxLayout(toc_wrap)
        toc_layout.setContentsMargins(4, 4, 4, 4)
        self.toc = TOCUI()
        toc_layout.addWidget(self.toc)
        self.left_stack.addWidget(toc_wrap)

        # --- Thumbnails page (Phase 2: filled in later) ---
        from features.pdf_viewer.page_thumbnails import PageThumbnailsUI  # imported lazily
        self.thumbs = PageThumbnailsUI()
        self.left_stack.addWidget(self.thumbs)

        v.addWidget(self.left_stack)
        self.left_dock.setWidget(container)
        self.left_dock.resize(240, 600)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.left_dock)

    def _toggle_toc(self):
        self.left_stack.setCurrentIndex(0)
        self.left_dock.setVisible(not self.left_dock.isVisible())

    def _toggle_thumbs(self):
        self.left_stack.setCurrentIndex(1)
        self.left_dock.setVisible(not self.left_dock.isVisible())

    # ====================================================================
    # BUILD: PDF canvas (central widget)
    # ====================================================================
    def _build_canvas(self):
        # Build the tab widget as the central widget. Each tab owns its
        # own (ViewerEngine, PDFViewerUI, AnnotationEngine) so the user
        # can have multiple PDFs open like MS Edge.
        from PyQt6.QtWidgets import QTabWidget
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        # Initially the active "session" is a placeholder tab (no PDF).
        self._sessions: list[dict] = []   # [{path, engine, pdf_viewer, annot, editor, dirty}]
        # The "current" attributes used everywhere in main_window point
        # at the active tab's resources — set up the placeholder so
        # the rest of the wiring works.
        self._build_placeholder_session()
        self.setCentralWidget(self._tabs)

    def _build_placeholder_session(self):
        """Create the initial empty session (no PDF loaded)."""
        engine = ViewerEngine()
        pdf_viewer = PDFViewerUI()
        pdf_viewer.attach_engine(engine)
        annot = AnnotationEngine(engine)
        editor = TextEditor(engine)
        session = {
            "path": None,
            "engine": engine,
            "pdf_viewer": pdf_viewer,
            "annot": annot,
            "editor": editor,
            "dirty": False,
        }
        self._sessions.append(session)
        idx = self._tabs.addTab(pdf_viewer, "No document")
        self._tabs.setTabToolTip(idx, "No PDF loaded — drag a PDF here or use Ctrl+O")
        # Wire the active tab's canvas into the shared signals + router.
        self._bind_session_signals(session)
        self._set_active_session(session)

    def _bind_session_signals(self, session: dict) -> None:
        """Connect a session's pdf_viewer to main_window handlers."""
        v = session["pdf_viewer"]
        v.page_rendered.connect(self._on_page_rendered)
        v.page_advance_requested.connect(self._on_page_advance)
        v.page_advance_committed.connect(self._on_page_advance_committed)
        v.context_menu_requested.connect(self._on_canvas_context_menu)
        v.annotations_changed.connect(self._update_window_title)
        v.annotations_changed.connect(
            lambda: self._mark_session_dirty(session, True))
        # Keyboard-scrolling requires the canvas to have focus. Make sure
        # the canvas itself receives keyboard events.
        v.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _set_active_session(self, session: dict) -> None:
        """Point self.engine / self.pdf_viewer / self.annot / self.editor
        at the given session. Existing handlers read these attributes,
        so swapping them is enough to make the active tab the one the
        UI operates on."""
        # Avoid re-binding if it's already the active session.
        if getattr(self, "engine", None) is session["engine"]:
            return
        self.engine = session["engine"]
        self.pdf_viewer = session["pdf_viewer"]
        # Wire image drag-drop → image-to-PDF conversion.
        self.pdf_viewer.set_image_drop_handler(self._handle_image_drop)
        self.annot = session["annot"]
        self.editor = session["editor"]
        # Re-bind the shared undo stack to the active viewer so undo/redo
        # operate on the right document.
        if hasattr(self, "undo_stack"):
            try:
                self.undo_stack.set_viewer(self.engine)
            except Exception:
                pass
        # Re-wire the router (it depends on engine+annot+pdf_viewer).
        if hasattr(self, "_router"):
            try:
                self._router.disconnect()
            except Exception:
                pass
        self._router = CanvasEventRouter(
            self.engine, self.annot, self.pdf_viewer,
            undo_stack=self.undo_stack,
            editor=self.editor)
        # Re-attach engine to the active viewer.
        self.pdf_viewer.attach_engine(self.engine)
        # Refresh TOC + thumbs to reflect the active engine.
        self.toc.load_outline(self.engine.get_outline())
        try:
            self.thumbs.load_document(self.engine)
        except Exception:
            pass
        if self.engine.is_open:
            self._update_page_indicator(1, self.engine.page_count)
            self.zoom_status.setText(f"{int(self.engine.zoom * 100)}%")
        self._update_window_title()

    def _mark_session_dirty(self, session: dict, dirty: bool) -> None:
        """Flag a session as having unsaved changes and refresh the tab
        title. The asterisk prefix is the standard 'unsaved' indicator."""
        session["dirty"] = dirty
        self._refresh_tab_title(session)

    def _refresh_tab_title(self, session: dict) -> None:
        for i in range(self._tabs.count()):
            if self._tabs.widget(i) is session["pdf_viewer"]:
                if session["path"] is None:
                    name = "No document"
                else:
                    name = _short_tab_title(os.path.basename(session["path"]),
                                             max_len=15)
                if session["dirty"]:
                    name = "* " + name
                self._tabs.setTabText(i, name)
                break

    def _on_tab_close_requested(self, index: int) -> None:
        session = self._sessions[index]
        if session["dirty"]:
            # Don't silently lose unsaved changes.
            from PyQt6.QtWidgets import QMessageBox
            ans = QMessageBox.question(
                self, "Unsaved changes",
                f"{os.path.basename(session['path'] or 'document')} has "
                f"unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Save and session["path"]:
                self.engine = session["engine"]
                self.pdf_viewer = session["pdf_viewer"]
                ok, msg = session["engine"].save(session["path"])
                if not ok:
                    self._render_result(CommandResult.error(msg))
                    return
        # Close the tab.
        self._tabs.removeTab(index)
        self._sessions.pop(index)
        if not self._sessions:
            # Always keep at least one empty session open.
            self._build_placeholder_session()
        else:
            # Make sure self.engine / self.pdf_viewer reflect the now-active tab.
            cur = self._tabs.currentIndex()
            self._set_active_session(self._sessions[cur])

    def _on_tab_changed(self, index: int) -> None:
        if 0 <= index < len(self._sessions):
            self._set_active_session(self._sessions[index])

    def _cycle_tab(self):
        """Move to the next tab (wrapping)."""
        n = self._tabs.count()
        if n <= 1:
            return
        self._tabs.setCurrentIndex((self._tabs.currentIndex() + 1) % n)

    def _cycle_tab_back(self):
        """Move to the previous tab (wrapping)."""
        n = self._tabs.count()
        if n <= 1:
            return
        self._tabs.setCurrentIndex((self._tabs.currentIndex() - 1) % n)

    # ====================================================================
    # BUILD: find bar (overlay over canvas, hidden by default)
    # ====================================================================
    def _build_find_bar(self):
        # Wrap the tab widget + find bar overlay so the find bar floats
        # above whichever tab is active.
        wrapper = QWidget()
        wrapper.setObjectName("canvasWrapper")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tabs)

        self.find_bar = FindBar(wrapper)
        self.find_bar.closed.connect(self.find_bar.hide_bar)
        self.find_bar.search_requested.connect(self._do_find)
        self.find_bar.next_requested.connect(lambda: self._do_find_step(1))
        self.find_bar.prev_requested.connect(lambda: self._do_find_step(-1))

        # Position the find bar floating at top-right via overlay geometry.
        wrapper.resizeEvent = self._on_wrapper_resized
        wrapper.resize(self._tabs.size())
        self.setCentralWidget(wrapper)

        # Track current matches for prev/next
        self._find_matches: list[tuple[int, "fitz.Rect"]] = []
        self._find_idx: int = -1
        self._find_highlight_ids: list[tuple[int, "fitz.Annot"]] = []

    def _on_wrapper_resized(self, event):
        # Keep the find bar anchored to top-right when shown
        if hasattr(self, "find_bar") and self.find_bar is not None:
            w = self.find_bar.parent().width()
            self.find_bar.setGeometry(max(0, w - 460), 8, 450, 36)

    # ====================================================================
    # BUILD: dockable terminal
    # ====================================================================
    def _build_terminal_dock(self):
        self.terminal = TerminalUI()
        self.term_dock = DockableTerminal(self.terminal, self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.term_dock)
        self.resizeDocks([self.term_dock], [260], Qt.Orientation.Vertical)
        # Default dock size
        self.terminal.set_mode_label("select")

    # ====================================================================
    # Wire signals & shortcuts
    # ====================================================================
    def _wire_signals(self):
        # Terminal → main
        self.terminal.command_entered.connect(self._on_command)
        self.terminal.close_requested.connect(self.toggle_terminal)

        # Canvas ↔ annotator (router is rebuilt on tab switch)
        self._router = CanvasEventRouter(self.engine, self.annot, self.pdf_viewer,
                                         undo_stack=self.undo_stack,
                                         editor=self.editor)

        # TOC → navigation
        self.toc.navigate_requested.connect(self._on_toc_navigate)
        # Thumbnails → navigation
        self.thumbs.navigate_requested.connect(self._on_toc_navigate)

        # Per-canvas signals (page render, scroll, context menu, etc.)
        # are bound per-session in _bind_session_signals so they
        # automatically target whichever tab is active.

        # Canvas annotation activity → status bar (optional)
        self.pdf_viewer.annotations_changed.connect(
            lambda: self.statusBar().showMessage("Annotation added", 1500)
        )
        # Also update the window title (shows a * when there are unsaved
        # changes). The undo stack sets _dirty=True on every push.
        self.pdf_viewer.annotations_changed.connect(self._update_window_title)

        # Dock visibility → auto-hide countdown
        self.term_dock.visibilityChanged.connect(self._on_chrome_visibility_change)
        self.left_dock.visibilityChanged.connect(self._on_chrome_visibility_change)
        self.main_toolbar.visibilityChanged.connect(self._on_chrome_visibility_change)

        # When the user drags the splitter between the terminal dock and
        # the PDF canvas (e.g. grows the terminal panel), the canvas
        # shrinks. Trigger a refresh so any cached fit-to-viewport layout
        # stays correct. The dock widget resizes the central widget
        # automatically; we just make sure the page refits if needed.
        try:
            self.term_dock.topLevelChanged.connect(self._on_dock_resized)
            self.term_dock.dockLocationChanged.connect(self._on_dock_resized)
        except Exception:
            pass

    def _wire_shortcuts(self):
        """Bind Ctrl+J / Ctrl+T / F11 / Ctrl+0 etc."""
        # Ctrl+J → toggle terminal (already in menu; we don't bind
        # another QShortcut here because the menu's QAction already
        # owns the binding — registering a second one causes Qt's
        # "Ambiguous shortcut overload" warning at startup).
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.toggle_terminal)
        QShortcut(QKeySequence("Ctrl+`"), self, activated=self.toggle_terminal)
        QShortcut(QKeySequence("F11"), self, activated=self._action_fullscreen)
        QShortcut(QKeySequence("Ctrl+L"), self,
                  activated=lambda: self.terminal.input.setFocus())
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self._toggle_chrome_pinned)
        QShortcut(QKeySequence("PageDown"), self, activated=self._on_pgdn)
        QShortcut(QKeySequence("PageUp"), self, activated=self._on_pgup)
        # Arrow keys: Right/Left for next/prev page, Up/Down for the
        # same (so users with keyboards that have no dedicated
        # PageUp/PageDown can still navigate). All four are guarded by
        # "no terminal focus" in _on_pgdn/_on_pgup so the user can
        # type arrow keys into the command line without jumping pages.
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=self._on_pgdn)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=self._on_pgup)
        QShortcut(QKeySequence(Qt.Key.Key_Down), self,
                  activated=self._on_pgdn)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self,
                  activated=self._on_pgup)
        QShortcut(QKeySequence("Home"), self,
                  activated=lambda: self._render_result(self._cmd_goto(["1"])))
        QShortcut(QKeySequence("End"), self,
                  activated=lambda: self._render_result(
                      self._cmd_goto([str(self.engine.page_count)])))
        # Ctrl+Z / Ctrl+Y → undo / redo (matching the toolbar buttons).
        # Bound at window level so they work regardless of focus — but
        # only when the terminal input doesn't have focus (terminal
        # intercepts its own Ctrl+Z for "undo last command").
        QShortcut(QKeySequence("Ctrl+Z"), self,
                  activated=self._shortcut_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self,
                  activated=self._shortcut_redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self,
                  activated=self._shortcut_redo)
        # Ctrl+R → rotate current page 90° clockwise.
        # (Ctrl+Shift+R is left free for the future "rotate counter-clockwise".)
        QShortcut(QKeySequence("Ctrl+R"), self,
                  activated=self._action_rotate)
        # Ctrl+Tab / Ctrl+Shift+Tab → cycle through open PDF tabs.
        QShortcut(QKeySequence("Ctrl+Tab"), self,
                  activated=self._cycle_tab)
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self,
                  activated=self._cycle_tab_back)
        # Ctrl+W → close the current tab.
        QShortcut(QKeySequence("Ctrl+W"), self,
                  activated=lambda: self._on_tab_close_requested(
                      self._tabs.currentIndex()))

    # ====================================================================
    # Auto-hide chrome logic (Phase 2 — minimal implementation here)
    # ====================================================================
    def _toggle_chrome_pinned(self):
        """Pin or unpin chrome. When unpinned, auto-hides when maximized."""
        self._chrome_pinned = not self._chrome_pinned
        if self._chrome_pinned:
            self.main_toolbar.show()
            self.left_dock.show()
            self.term_dock.show()
            self.statusBar().showMessage("Chrome pinned (auto-hide off)", 1500)
        else:
            self.statusBar().showMessage(
                "Chrome un-pinned (hover edge to reveal)", 3000)

    def _hide_chrome_if_maximized(self):
        # When the user has unpinned the chrome (Ctrl+H), hide the panels
        # so the canvas gets full focus. Pinned = always visible.
        if self._chrome_pinned:
            return
        if self.windowState() & (Qt.WindowState.WindowMaximized
                                  | Qt.WindowState.WindowFullScreen):
            for w in (self.main_toolbar, self.left_dock, self.term_dock):
                if w.isVisible() and not getattr(w, "_pinned", False):
                    w.hide()

    def _on_chrome_visibility_change(self, visible: bool):
        # When the user manually shows a panel, restart the hide countdown
        # so the auto-hide kicks in after inactivity.
        if visible and not self._chrome_pinned:
            self._auto_hide_timer.start()

    def mouseMoveEvent(self, event):
        # Reveal chrome on cursor movement when auto-hidden
        if (not self.main_toolbar.isVisible()
                and (self.windowState() & Qt.WindowState.WindowMaximized)):
            self.main_toolbar.show()
            self.left_dock.show()
            self.term_dock.show()
        # Only restart the hide timer when auto-hide is on (Ctrl+H to toggle)
        if not self._chrome_pinned:
            self._auto_hide_timer.start()
        super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        """Keep the toolbar-reveal arrow anchored to the top-right corner."""
        super().resizeEvent(event)
        btn = getattr(self, "_toolbar_reveal_btn", None)
        if btn is not None and btn.isVisible():
            # Position just under the toolbar area, top-right of central widget
            btn.move(self.width() - btn.width() - 8, 4)
            btn.raise_()

    # ====================================================================
    # Command registration
    # ====================================================================
    def _register_commands(self):
        # General
        self.parser.register("help", self._cmd_help)
        self.parser.register("history", self._cmd_history)
        # Viewer
        self.parser.register("open", self._cmd_open)
        self.parser.register("close", self._cmd_close)
        self.parser.register("next", self._cmd_next)
        self.parser.register("prev", self._cmd_prev)
        self.parser.register("goto", self._cmd_goto)
        self.parser.register("zoom", self._cmd_zoom)
        self.parser.register("fit", self._cmd_fit)
        self.parser.register("toc", self._cmd_toc)
        self.parser.register("thumbs", self._cmd_thumbs)
        # Annotator
        self.parser.register("mode", self._cmd_mode)
        self.parser.register("highlight", self._cmd_highlight_text)
        self.parser.register("save", self._cmd_save)
        self.parser.register("undo", self._cmd_undo)
        self.parser.register("redo", self._cmd_redo)
        # Editor
        self.parser.register("addtext", self._cmd_addtext)
        self.parser.register("edit-text", self._cmd_edit_text)
        self.parser.register("extract", self._cmd_extract)
        self.parser.register("merge", self._cmd_merge)
        self.parser.register("gen", self._cmd_gen)
        self.parser.register("split", self._cmd_split)
        self.parser.register("delete", self._cmd_delete)
        self.parser.register("rotate", self._cmd_rotate)
        self.parser.register("swap", self._cmd_swap)
        # Image→PDF conversion: `image2pdf <paths...>` or `img2pdf <paths...>`.
        self.parser.register("image2pdf", self._cmd_image2pdf)
        self.parser.register("img2pdf", self._cmd_image2pdf)
        # QR / stamps
        self.parser.register("qr", self._cmd_qr)
        self.parser.register("stamp-capture", self._cmd_stamp_capture)
        self.parser.register("stamp", self._cmd_stamp_paste)
        # Screenshots: `screenshot page` (current page → PNG), `screenshot
        # region` (invokes OS-native screenshot tool).
        self.parser.register("screenshot", self._cmd_screenshot)
        # UI
        self.parser.register("theme", self._cmd_theme)
        self.parser.register("fullscreen", self._cmd_fullscreen)
        self.parser.register("dock", self._cmd_dock)
        self.parser.register("print", self._cmd_print)
        self.parser.register("find", self._cmd_find)
        self.parser.register("view", self._cmd_view)

    # ====================================================================
    # Command handlers
    # ====================================================================
    def _cmd_help(self, _args):
        return CommandResult.print(self.parser.help_text())

    def _cmd_history(self, _args):
        return CommandResult.print(
            "Use ↑ / ↓ keys in the terminal input to cycle history.")

    def _cmd_view(self, args):
        """Switch the active session between single-page and continuous view.

        The session dict carries ``view_mode`` ("single" | "continuous") and
        a ``continuous_view`` widget reference. The terminal command lets
        the user toggle which view the active tab is hosting; the tab's
        central widget is swapped to the matching viewer.
        """
        if not args:
            return CommandResult.error("Usage: view <single|continuous>")
        mode = args[0].lower()
        if mode not in ("single", "continuous"):
            return CommandResult.error(
                f"Unknown view mode: {args[0]!r}. Use 'single' or 'continuous'.")
        sess = self._active_session()
        if sess is None:
            return CommandResult.error("No active session.")
        if sess["view_mode"] == mode:
            return CommandResult.print(f"Already in {mode} view.")
        idx = self._tabs.currentIndex()
        if mode == "continuous":
            # Lazily create the continuous-view widget on first use so
            # the tab can swap back to single without re-instantiating.
            if sess["continuous_view"] is None:
                from features.pdf_viewer.continuous_view import ContinuousView
                cv = ContinuousView()
                cv.attach_engine(sess["engine"])
                sess["continuous_view"] = cv
            self._tabs.insertTab(idx, sess["continuous_view"],
                                 os.path.basename(sess["path"] or "Tab"))
            self._tabs.removeTab(idx + 1)
            self._tabs.setCurrentIndex(idx)
        else:
            pdf_viewer = sess["pdf_viewer"]
            self._tabs.insertTab(idx, pdf_viewer,
                                 os.path.basename(sess["path"] or "Tab"))
            self._tabs.removeTab(idx + 1)
            self._tabs.setCurrentIndex(idx)
        sess["view_mode"] = mode
        return CommandResult.print(f"Switched to {mode} view.")

    # ----- viewer --------------------------------------------------------
    def _cmd_open(self, args):
        positional, _ = self.parser.extract_flags(args)
        if not positional:
            return CommandResult.error("Usage: open <path-to-pdf>")
        return self._do_open(resolve_user_path(" ".join(positional)))

    # ------------------------------------------------- image → PDF
    def _handle_image_drop(self, paths: List[str]) -> None:
        """Drag-drop handler injected into PDFViewerUI.

        Prompts for a save location, builds a multi-page PDF from the
        dropped images (one per page, fit-to-page), saves it, and
        auto-opens the new PDF in TermiPDF so the user gets the full
        editor / annotator / Pages Manager immediately.
        """
        if not paths:
            return
        # Suggest a default filename based on the first image.
        first = os.path.basename(paths[0])
        stem = os.path.splitext(first)[0] or "images"
        suggested = os.path.join(os.path.expanduser("~"),
                                 f"{stem}.pdf")
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Save images as PDF",
            suggested,
            "PDF files (*.pdf)")
        if not out_path:
            return  # user cancelled
        if not out_path.lower().endswith(".pdf"):
            out_path += ".pdf"
        ok, msg = PDFManipulator.images_to_pdf(paths, out_path)
        if not ok:
            QMessageBox.warning(self, "Image → PDF failed", msg)
            self._render_result(CommandResult.error(msg))
            return
        self._render_result(CommandResult.print(msg))
        # Auto-open the new PDF — gives the user the full editor +
        # annotator + Pages Manager flow on the result.
        result = self._do_open(out_path)
        if result and result.action == "error":
            QMessageBox.warning(self, "Could not open new PDF",
                                result.message)

    def _cmd_image2pdf(self, args):
        """Convert one or more image files to a single multi-page PDF.

        Usage:
            image2pdf <img1> <img2> ...
            img2pdf <img1> <img2> ...      (alias)

        Each image becomes one PDF page (fit-to-page with margins).
        Prompts for a save location.
        """
        if not args:
            return CommandResult.error(
                "Usage: image2pdf <image1> [image2 ...]")
        # Resolve every argument; support comma-separated lists too.
        paths: List[str] = []
        for tok in args:
            for piece in tok.split(","):
                p = resolve_user_path(piece.strip())
                if p and os.path.isfile(p):
                    paths.append(os.path.abspath(p))
        if not paths:
            return CommandResult.error(
                "None of the supplied paths point to an existing file.")
        self._handle_image_drop(paths)
        return CommandResult.print(
            f"Converted {len(paths)} image(s) to PDF.")

    def _cmd_close(self, _args):
        self.engine.close()
        self.pdf_viewer._set_placeholder()
        self.toc.clear_outline()
        self._update_page_indicator(0, 0)
        try:
            self.undo_stack.reset()
        except Exception:
            pass
        self._update_window_title()
        return CommandResult.print("Closed current PDF.")

    def _cmd_next(self, _args):
        ok, msg = self.engine.next_page()
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_prev(self, _args):
        ok, msg = self.engine.prev_page()
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_goto(self, args):
        if not args:
            return CommandResult.error("Usage: goto <page>")
        try:
            n = int(args[0])
        except ValueError:
            return CommandResult.error(f"Invalid page number: {args[0]}")
        ok, msg = self.engine.goto(n - 1)
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_zoom(self, args):
        if not args:
            return CommandResult.error("Usage: zoom in | out | <factor>")
        if args[0] == "in":
            ok, msg = self.engine.zoom_in()
        elif args[0] == "out":
            ok, msg = self.engine.zoom_out()
        else:
            try:
                ok, msg = self.engine.set_zoom(float(args[0]))
            except ValueError:
                return CommandResult.error(f"Invalid zoom factor: {args[0]}")
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_fit(self, _args):
        self.pdf_viewer.fit_to_viewport()
        return CommandResult.print("Fitted page to viewport.")

    def _cmd_toc(self, _args):
        self._toggle_toc()
        return CommandResult.print("TOC toggled.")

    def _cmd_thumbs(self, _args):
        self._toggle_thumbs()
        return CommandResult.print("Thumbnails toggled.")

    # ----- annotator -----------------------------------------------------
    def _cmd_mode(self, args):
        if not args:
            return CommandResult.error(
                "Usage: mode view | draw | highlight | erase | text | note | "
                "rect | ellipse | arrow | signature | edit-text")
        mode = args[0].lower()
        _, flags = self.parser.extract_flags(args)

        # Common color & thickness parsing
        color_str = flags.get("color")
        try:
            if color_str is not None:
                color_rgb = parse_color(color_str)
            else:
                color_rgb = None
        except ValueError as exc:
            return CommandResult.error(str(exc))

        thickness = float(flags.get("thickness", self._active_pen_thickness))
        if thickness <= 0:
            return CommandResult.error("thickness must be positive")

        if mode == "view":
            self._set_tool_mode(CanvasMode.VIEW)
            return CommandResult.print("Mode → view.")
        if mode == "draw":
            if color_rgb is None:
                color_rgb = self._active_pen_color
            else:
                self._active_pen_color = color_rgb
            self._active_pen_thickness = thickness
            self.pdf_viewer.set_active_ink(color_rgb, thickness)
            self._set_tool_mode(CanvasMode.DRAW)
            return CommandResult.print(
                f"Mode → draw (color={color_str or 'red'}, thickness={thickness}). "
                "Drag on the PDF to draw.")
        if mode == "highlight":
            if color_rgb is None:
                color_rgb = self._active_highlight_color
            else:
                self._active_highlight_color = color_rgb
            self.pdf_viewer.set_active_highlight(color_rgb)
            self.annot.set_highlight_color(color_rgb)
            self._set_tool_mode(CanvasMode.HIGHLIGHT)
            return CommandResult.print(
                f"Mode → highlight (color={color_str or 'yellow'}). "
                "Drag on the PDF to highlight.")
        if mode == "erase":
            self._set_tool_mode(CanvasMode.ERASE)
            return CommandResult.print("Mode → erase. Click any annotation to delete it.")
        if mode == "text":
            self._set_tool_mode(CanvasMode.TEXT)
            return CommandResult.print("Mode → text. Click the PDF to insert text.")
        if mode == "edit-text":
            self._set_tool_mode(CanvasMode.EDIT_TEXT)
            return CommandResult.print(
                "Mode → edit-text. Click existing text to replace it.")
        if mode == "note":
            self._set_tool_mode(CanvasMode.NOTE)
            return CommandResult.print("Mode → note. Click to drop a sticky note.")
        if mode == "rect":
            self._set_tool_mode(CanvasMode.RECT)
            if color_rgb:
                self.annot.set_shape_color(color_rgb)
                self.pdf_viewer.set_active_shape(color_rgb, thickness)
            return CommandResult.print("Mode → rectangle. Drag to draw a rect.")
        if mode == "ellipse":
            self._set_tool_mode(CanvasMode.ELLIPSE)
            if color_rgb:
                self.annot.set_shape_color(color_rgb)
                self.pdf_viewer.set_active_shape(color_rgb, thickness)
            return CommandResult.print("Mode → ellipse. Drag to draw.")
        if mode == "arrow":
            self._set_tool_mode(CanvasMode.ARROW)
            if color_rgb:
                self.annot.set_shape_color(color_rgb)
                self.pdf_viewer.set_active_shape(color_rgb, thickness)
            return CommandResult.print("Mode → arrow. Drag to draw an arrow.")
        if mode == "signature":
            self._set_tool_mode(CanvasMode.SIGNATURE)
            return CommandResult.print(
                "Mode → signature. Click the PDF, then draw your signature.")
        if mode == "select":
            self._set_tool_mode(CanvasMode.SELECT)
            return CommandResult.print(
                "Mode → select. Click to copy a word/line; drag to copy a "
                "block.  (OCR is used automatically on scanned pages when "
                "pytesseract is installed.)")
        return CommandResult.error(f"Unknown mode: {mode}")

    def _set_tool_mode(self, mode: CanvasMode):
        self.pdf_viewer.set_mode(mode)
        self._active_tool = mode
        self._update_mode_badge(mode.value)
        self.terminal.set_mode_label(mode.value)
        self._sync_tool_buttons(mode)

    def _sync_tool_buttons(self, mode: CanvasMode):
        """Reflect the current canvas mode in the toolbar's checkable icons.

        Mapping from mode → toolbar button key. When mode is VIEW, every
        tool button is unchecked. When a non-view mode is active, only its
        matching button is checked.
        """
        # Mode -> button icon-name mapping (matches the names used in
        # _add_toolbar_button for each tool).
        mode_to_key = {
            CanvasMode.VIEW:       None,
            CanvasMode.DRAW:       "pen",
            CanvasMode.HIGHLIGHT:  "highlight",
            CanvasMode.ERASE:      "eraser",
            CanvasMode.TEXT:       "text",
            CanvasMode.NOTE:       "note",
            CanvasMode.EDIT_TEXT:  "text",   # shares with text tool
            CanvasMode.RECT:       "rect",
            CanvasMode.ELLIPSE:    "ellipse",
            CanvasMode.ARROW:      "arrow",
            CanvasMode.SIGNATURE:  "stamp",
            CanvasMode.SELECT:     "select",
        }
        active_key = mode_to_key.get(mode)
        for key, btn in self._tool_buttons.items():
            try:
                btn.setChecked(active_key is not None and key == active_key)
            except Exception:
                pass

    def _toggle_tool_button(self, mode: CanvasMode):
        """Called when the user clicks a checkable tool button.

        If the tool is already active, switch back to VIEW (deselect).
        If a different tool is active, switch to the new one. This is the
        Edge-style behavior: clicking an active tool again toggles it off.
        """
        if self._active_tool == mode:
            self._set_tool_mode(CanvasMode.VIEW)
        else:
            self._set_tool_mode(mode)

    def _toggle_tool_via_cmd(self, mode_name: str):
        """Toolbar-button click handler that runs through the command parser
        (so it works the same whether the user clicks the icon or types a
        command), but ALSO deselects the tool back to VIEW if the same tool
        is already active — fixing the "stuck mode" UX bug.
        """
        name_to_mode = {
            "view":       CanvasMode.VIEW,
            "draw":       CanvasMode.DRAW,
            "highlight":  CanvasMode.HIGHLIGHT,
            "erase":      CanvasMode.ERASE,
            "text":       CanvasMode.TEXT,
            "edit-text":  CanvasMode.EDIT_TEXT,
            "note":       CanvasMode.NOTE,
            "rect":       CanvasMode.RECT,
            "ellipse":    CanvasMode.ELLIPSE,
            "arrow":      CanvasMode.ARROW,
            "signature":  CanvasMode.SIGNATURE,
            "select":     CanvasMode.SELECT,
        }
        target = name_to_mode.get(mode_name)
        if target is None:
            # Fall back to the regular command path
            self._render_result(self._cmd_mode([mode_name]))
            return
        if self._active_tool == target:
            # Already active → deselect back to VIEW.
            self._set_tool_mode(CanvasMode.VIEW)
            self._render_result(CommandResult.print(
                f"Mode → view (deselected {mode_name})."))
        else:
            self._render_result(self._cmd_mode([mode_name]))

    def _cmd_highlight_text(self, args):
        if not args:
            return CommandResult.error('Usage: highlight "text" [--color c]')
        raw = " ".join(args)
        _, flags = self.parser.extract_flags(args)
        color_str = flags.get("color")
        rgb = parse_color(color_str) if color_str else None
        ok, msg = self.annot.highlight_text(raw, color_rgb=rgb)
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_save(self, _args):
        ok, msg = self.engine.save()
        if ok:
            # After a successful save the document is no longer dirty.
            try:
                self.undo_stack.mark_clean()
            except Exception:
                pass
            # Mark the active session clean and refresh its tab title.
            self.has_unsaved_changes = False
            sess = self._active_session()
            if sess is not None:
                self._mark_session_dirty(sess, False)
            self._update_window_title()
        return self._as_result(ok, msg)

    def _cmd_undo(self, _args):
        ok, msg = self.undo_stack.undo()
        if ok:
            self._refresh_after_undo_redo()
        return self._as_result(ok, msg)

    def _cmd_redo(self, _args):
        ok, msg = self.undo_stack.redo()
        if ok:
            self._refresh_after_undo_redo()
        return self._as_result(ok, msg)

    # -------------------------------------------- page-op orchestration
    def _refresh_after_page_op(self) -> None:
        """Reload the viewer after an on-disk page mutation, without
        touching the undo stack. Used after terminal/PM-initiated
        swap / reorder / move / rotate / delete."""
        if not self.engine or not self.engine.path:
            return
        try:
            self.engine.reload_from_disk()
        except Exception:
            pass
        try:
            self.pdf_viewer.refresh()
        except Exception:
            pass
        # Pages Manager — if it's open, repopulate so thumbnails + labels
        # match the new state. ``_populate`` is private but stable.
        pm = getattr(self, "_pages_manager", None)
        if pm is not None and pm.isVisible():
            try:
                pm._populate()
            except Exception:
                pass
        try:
            self._update_window_title()
        except Exception:
            pass

    def _refresh_after_undo_redo(self) -> None:
        """Refresh viewer + Pages Manager after the undo stack applied
        an inverse or forward. The stack already called
        ``engine.reload_from_disk()`` so we just need to repaint and
        update the PM grid + window title."""
        try:
            self.pdf_viewer.refresh()
        except Exception:
            pass
        pm = getattr(self, "_pages_manager", None)
        if pm is not None and pm.isVisible():
            try:
                pm._populate()
            except Exception:
                pass
        try:
            self._update_window_title()
        except Exception:
            pass

    # ----- editor --------------------------------------------------------
    def _cmd_addtext(self, args):
        if not args:
            return CommandResult.error(
                'Usage: addtext "text" --page 1 --x 100 --y 200 --size 14')
        positional, flags = self.parser.extract_flags(args)
        if not positional:
            return CommandResult.error("addtext requires text as first argument.")
        text = " ".join(positional)
        try:
            page = int(flags.get("page", 1))
            x = float(flags.get("x", 50))
            y = float(flags.get("y", 50))
            size = float(flags.get("size", 14))
            color_rgb = parse_color(flags.get("color", "black"))
            width = float(flags["width"]) if "width" in flags else None
            height = float(flags["height"]) if "height" in flags else None
        except (ValueError, TypeError) as exc:
            return CommandResult.error(f"Bad flag value: {exc}")
        font_file = flags.get("font")
        ok, msg = self.editor.add_text(text, page, x, y, size, color_rgb,
                                        font_file=font_file, width=width, height=height)
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_edit_text(self, args):
        # Replaces text near the given page + (x, y) coordinates.
        if len(args) < 3:
            return CommandResult.error("Usage: edit-text <page> <x> <y> [\"new text\"]")
        try:
            page = int(args[0])
            x = float(args[1])
            y = float(args[2])
        except ValueError:
            return CommandResult.error("page, x, y must be numbers")
        new_text = " ".join(args[3:]).strip('"') if len(args) > 3 else None
        if new_text is None:
            new_text, ok = QInputDialog.getText(
                self, "Replace text", "New text:")
            if not ok or not new_text:
                return CommandResult.print("Edit cancelled.")
        ok, msg = self.editor.whiteout_then_insert(
            page, x, y, new_text, viewer=self.pdf_viewer)
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_extract(self, args):
        if len(args) < 3:
            return CommandResult.error("Usage: extract <from> <to> <out.pdf>")
        try:
            f, t = int(args[0]), int(args[1])
        except ValueError:
            return CommandResult.error("from/to must be page numbers.")
        out = resolve_user_path(args[2])
        ok, msg = PDFManipulator.extract_pages(self.engine.path, f, t, out)
        return self._as_result(ok, msg)

    def _cmd_merge(self, args):
        """Two forms:
        merge <f1> <f2> <out.pdf>  — merge PDF files
        merge p-<from> p-<to> <out.pdf>  — extract page range from the
                                           currently open PDF into a new
                                           file (the requested syntax).
        """
        if len(args) < 2:
            return CommandResult.error(
                "Usage: merge <f1> <f2> <out.pdf>\n"
                "   or: merge p-<from> p-<to> <out.pdf>")
        # Detect the page-range shorthand "p-N" / "p-X-Y" used to mean
        # "pages from the currently open PDF".
        is_p_range = (len(args) >= 3
                      and args[0].startswith("p-")
                      and args[1].startswith("p-"))
        if is_p_range:
            try:
                # First arg may be "p-3" (single page) or "p-1-5" (range).
                a, b = _parse_p_range(args[0]), _parse_p_range(args[1])
            except ValueError as exc:
                return CommandResult.error(str(exc))
            out = resolve_user_path(args[2])
            # Combine the two p-ranges into a single range [min..max] so
            # ``merge p-1 p-10`` merges pages 1..10 from the current PDF.
            lo, hi = min(a[0], b[0]), max(a[1], b[1])
            ok, msg = PDFManipulator.extract_pages(self.engine.path,
                                                   lo, hi, out)
            return self._as_result(ok, msg)
        # Standard multi-file merge
        if len(args) < 3:
            return CommandResult.error("Usage: merge <f1> <f2> <out.pdf>")
        out = resolve_user_path(args[-1])
        inputs = [resolve_user_path(p) for p in args[:-1]]
        ok, msg = PDFManipulator.merge_pdfs(inputs, out)
        return self._as_result(ok, msg)

    def _cmd_gen(self, args):
        """Generate a new PDF from a list of pages from the open PDF.

        Usage:
          gen npdf p-1,2,3 <out.pdf>     → write to <out.pdf>
          gen npdf 1,2,3 <out.pdf>       → write to <out.pdf>
          gen npdf p-1,2,3               → opens a file-save dialog

        When the output path is missing we pop a QFileDialog so the
        user can pick where to save the new PDF.
        """
        if len(args) < 2:
            return CommandResult.error(
                'Usage: gen npdf p-1,2,3 <out.pdf>\n'
                '   or: gen npdf 1,2,3 <out.pdf>')
        if args[0] != "npdf":
            return CommandResult.error(
                'Unknown gen subcommand. Use: gen npdf p-1,2,3 <out.pdf>')
        if len(args) < 3:
            # No output path → pop a save-file dialog.
            return self._cmd_gen_save_dialog(args[1])
        page_spec = args[1]
        out = resolve_user_path(args[2])
        return self._gen_npdf_write(page_spec, out)

    def _cmd_gen_save_dialog(self, page_spec: str) -> CommandResult:
        """``gen npdf <spec>`` with no output — pop a file-save dialog."""
        if not self.engine.is_open:
            return CommandResult.error("No PDF is open.")
        try:
            from PyQt6.QtWidgets import QFileDialog
            # Suggest a sensible filename next to the current document.
            base_dir = ""
            if self.engine.is_open and self.engine.path:
                p = Path(self.engine.path)
                base_dir = str(p.parent)
            # Suggest "pages_1-3.pdf" or similar.
            suggested = f"pages_{page_spec.replace('p-', '').replace(',', '-')}.pdf"
            path, _ = QFileDialog.getSaveFileName(
                self, "Save new PDF as…",
                str(Path(base_dir) / suggested) if base_dir else suggested,
                "PDF files (*.pdf)")
            if not path:
                return CommandResult.print("Cancelled.")
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            return self._gen_npdf_write(page_spec, path)
        except Exception as exc:
            return CommandResult.error(f"gen npdf failed: {exc}")

    def _gen_npdf_write(self, page_spec: str, out: str) -> CommandResult:
        if not self.engine.is_open:
            return CommandResult.error("No PDF is open.")
        try:
            pages = _parse_page_spec(page_spec)
        except ValueError as exc:
            return CommandResult.error(str(exc))
        try:
            import fitz as _fitz
            doc = _fitz.open()
            src = _fitz.open(self.engine.path)
            n = len(src)
            for p in pages:
                if p < 1 or p > n:
                    src.close()
                    doc.close()
                    return CommandResult.error(
                        f"Page {p} out of range (1..{n}).")
                doc.insert_pdf(src, from_page=p - 1, to_page=p - 1)
            doc.save(out, garbage=4, deflate=True)
            doc.close()
            src.close()
            return self._as_result(True,
                f"Saved {len(pages)} page(s) → {out}")
        except Exception as exc:
            return CommandResult.error(f"gen npdf failed: {exc}")

    def _cmd_split(self, args):
        """Split the open PDF into two new files at the given page.

        Usage: split <page> <left.pdf> <right.pdf>
        """
        if len(args) < 3:
            return CommandResult.error(
                "Usage: split <page> <left.pdf> <right.pdf>")
        try:
            split_at = int(args[0])
        except ValueError:
            return CommandResult.error("split <page> must be an integer.")
        left_out = resolve_user_path(args[1])
        right_out = resolve_user_path(args[2])
        n = self.engine.page_count
        if split_at < 1 or split_at >= n:
            return CommandResult.error(
                f"split page must be 1..{n - 1} (the last page of the LEFT part).")
        ok1, m1 = PDFManipulator.extract_pages(self.engine.path, 1, split_at, left_out)
        if not ok1:
            return self._as_result(ok1, m1)
        ok2, m2 = PDFManipulator.extract_pages(self.engine.path, split_at + 1, n, right_out)
        if not ok2:
            return self._as_result(ok2, m2)
        return self._as_result(True,
            f"Split into {left_out} (1..{split_at}) and {right_out} ({split_at + 1}..{n})")

    def _cmd_delete(self, args):
        if not args:
            return CommandResult.error("Usage: delete <page>")
        try:
            p = int(args[0])
        except ValueError:
            return CommandResult.error("page must be a number.")
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return CommandResult.error("No PDF open.")
        n = self.engine.page_count
        if p < 1 or p > n:
            return CommandResult.error(f"Invalid page {p} (1..{n}).")
        if n <= 1:
            return CommandResult.error("Cannot delete the only remaining page.")
        # Cache the deleted page's content so undo can restore it.
        ok_cache, msg_cache, cache_pdf = UndoStack.cache_deleted_page(
            self.engine.path, p)
        if not ok_cache:
            return CommandResult.error(f"Cannot cache page for undo: {msg_cache}")
        ok, msg = PDFManipulator.delete_page(self.engine.path, p)
        if ok:
            # Push an undo entry — undo will re-merge ``cache_pdf`` at slot p.
            self.undo_stack.push_page_op("delete", page=p,
                                          deleted_page_pdf=cache_pdf)
            self._refresh_after_page_op()
        else:
            # Clean up the cache so we don't leak it.
            try:
                os.remove(cache_pdf)
            except Exception:
                pass
        return self._as_result(ok, msg)

    def _cmd_rotate(self, args):
        if len(args) < 2:
            return CommandResult.error("Usage: rotate <page> <angle>")
        try:
            p = int(args[0])
            a = int(args[1])
        except ValueError:
            return CommandResult.error("page and angle must be integers.")
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return CommandResult.error("No PDF open.")
        n = self.engine.page_count
        if p < 1 or p > n:
            return CommandResult.error(f"Invalid page {p} (1..{n}).")
        # Snapshot the page order so undo restores it (rotation doesn't
        # change order, but storing it keeps the entry uniform and
        # protects against batched ops that might come later).
        ok, msg = PDFManipulator.rotate_page(self.engine.path, p, a)
        if ok:
            self.undo_stack.push_page_op("rotate", page=p, angle=a)
            self._refresh_after_page_op()
        return self._as_result(ok, msg)

    def _cmd_swap(self, args):
        """Swap two pages in the open document.

        Usage:
            swap p-1 p-3
            swap 1 3
            swap 1,3 2,5        (multi-page pairings treated as a single swap)

        Unlike ``move`` (which inserts and shifts), ``swap`` exchanges
        the two positions and keeps total page count unchanged.

        Each page argument accepts either a single page (``p-3`` / ``3``)
        or a comma-separated list (``1,3,5``). When both sides are
        lists of equal length they are paired up position-by-position.
        """
        if not self.engine or not self.engine.is_open or not self.engine.path:
            return CommandResult.error("No PDF open.")
        if len(args) < 2:
            return CommandResult.error("Usage: swap <p-A> <p-B>  (e.g. swap 1 3)")

        def _parse(token: str) -> List[int]:
            """Parse a 'p-1,2,3' or '1,2,3' style token into 1-based ints."""
            body = token[2:] if token.startswith("p-") else token
            parts = [p.strip() for p in body.split(",") if p.strip()]
            if not parts:
                raise ValueError(f"empty page list in '{token}'")
            return [int(p) for p in parts]

        try:
            left = _parse(args[0])
            right = _parse(args[1])
        except ValueError as exc:
            return CommandResult.error(f"Invalid page token: {exc}")

        # If one side is a singleton and the other is a list, broadcast
        # the singleton across the list. If both are lists they must
        # have the same length and we pair them up one-for-one.
        n = self.engine.page_count
        if len(left) == 1 and len(right) > 1:
            pairs = [(left[0], p) for p in right]
        elif len(right) == 1 and len(left) > 1:
            pairs = [(p, right[0]) for p in left]
        elif len(left) == len(right):
            pairs = list(zip(left, right))
        else:
            return CommandResult.error(
                f"swap: list lengths must match (got {len(left)} vs {len(right)})")

        # Apply swaps in DESCENDING order of slot index so earlier
        # swaps don't shift the indices of later ones. With a true
        # swap the order doesn't matter mathematically, but doing them
        # right-to-left is the safest mental model when partial state
        # ends up on disk.
        # (reorder_pages is all-or-nothing, so we batch via docostring
        # pattern: swap each pair separately. The engine reloads at
        # the end.)
        results: List[str] = []
        ok_all = True
        any_swap_applied = False
        for a, b in sorted(pairs, key=lambda p: -max(p[0], p[1])):
            if a < 1 or a > n or b < 1 or b > n:
                ok_all = False
                results.append(f"  ({a},{b}): out of range 1..{n}")
                continue
            if a == b:
                results.append(f"  ({a},{b}): no-op (same page)")
                continue
            ok, msg = PDFManipulator.swap_pages(self.engine.path, a, b)
            if not ok:
                ok_all = False
                results.append(f"  ({a},{b}): {msg}")
            else:
                any_swap_applied = True
                # Push one undo entry per applied swap. Each entry
                # carries (page_a, page_b); undo re-swaps the same
                # pair (swap is its own inverse). Doing one entry per
                # pair means a multi-pair `swap 1,3 2,5` undo reverses
                # the pairs in reverse order, matching the user's
                # mental model.
                self.undo_stack.push_page_op("swap", page_a=a, page_b=b)
                results.append(f"  ({a},{b}): {msg}")

        # If at least one swap landed, the entries above are already on
        # the undo stack. Refresh the viewer / Pages Manager without
        # wiping the stack — do NOT call _do_open here.
        if ok_all and any_swap_applied:
            try:
                self.engine.reload_from_disk()
            except Exception:
                pass
            try:
                self.pdf_viewer.refresh()
            except Exception:
                pass
            # If the Pages Manager is open, let it observe the swap so
            # it can run its animation / repopulate / emit the
            # pages_swapped + pages_reordered signals. Pass
            # ``apply_on_disk=False`` because we just wrote the swap
            # above; the PM must NOT re-write or the file is touched
            # twice per pair.
            pm = getattr(self, "_pages_manager", None)
            if pm is not None and pm.isVisible():
                for a, b in sorted(pairs, key=lambda p: -max(p[0], p[1])):
                    if a == b:
                        continue
                    try:
                        pm.animate_terminal_swap(a, b, apply_on_disk=False)
                    except Exception:
                        pass
            try:
                self._update_window_title()
            except Exception:
                pass
        elif ok_all:
            # All swaps were no-ops — still refresh so the viewer
            # reflects the (unchanged) state.
            try:
                self.engine.reload_from_disk()
            except Exception:
                pass
            try:
                self.pdf_viewer.refresh()
            except Exception:
                pass

        msg = "Swap complete." if ok_all else "Swap finished with errors."
        msg += "\n" + "\n".join(results)
        return self._as_result(ok_all, msg)

    # ----- QR / stamps --------------------------------------------------
    def _cmd_qr(self, args):
        """Open a QR-share popup for the given text.

        Usage: qr "text or URL"
        Same flags as before (--page, --x, --y, --size) are accepted but
        the QR is now shown in a popup instead of being stamped onto the
        page — matches the toolbar button behavior.
        """
        positional, flags = self.parser.extract_flags(args)
        if not positional:
            return CommandResult.error('Usage: qr "text or URL"')
        text = " ".join(positional)
        # Use the same popup flow as the toolbar button / right-click
        # share — non-modal, scannable, with a copy button.
        self._qr_share_text(text)
        return CommandResult.print(f"QR popup opened for {len(text)} chars.")

    def _cmd_stamp_capture(self, args):
        if not args:
            return CommandResult.error("Usage: stamp-capture <name>")
        return CommandResult.print(
            f"Saved '{args[0]}' as a reusable stamp (Phase 4 implementation).")

    def _cmd_stamp_paste(self, args):
        return CommandResult.print(
            "Stamp paste (Phase 4 implementation).")

    def _cmd_screenshot(self, args):
        """Terminal entry-point for screenshots.

        ``screenshot page``   → save the current page as PNG (with
                                annotations baked in) and copy to clipboard.
        ``screenshot region`` → invoke the OS-native screenshot tool so
                                the user can pick which segment of the UI
                                to capture.
        """
        sub = (args[0].lower() if args else "page")
        if sub == "page":
            self._action_screenshot()
            return CommandResult.print("Page screenshot complete.")
        if sub == "region":
            self._action_screenshot_region()
            return CommandResult.print("Region screenshot tool invoked.")
        return CommandResult.error(
            "Usage: screenshot page | screenshot region")

    # ----- UI ------------------------------------------------------------
    def _cmd_theme(self, args):
        if not args:
            return CommandResult.error("Usage: theme dark | light")
        if args[0] not in ("dark", "light"):
            return CommandResult.error("Theme must be 'dark' or 'light'.")
        self.theme.set(args[0])
        return CommandResult.print(f"Theme → {args[0]}.")

    def _cmd_fullscreen(self, _args):
        self._action_fullscreen()
        return CommandResult.print("Fullscreen toggled.")

    def _cmd_dock(self, args):
        if not args or args[0] not in ("bottom", "left", "right", "top", "float"):
            return CommandResult.error("Usage: dock bottom | left | right | top | float")
        self.term_dock.dock_to(args[0])
        return CommandResult.print(f"Terminal docked → {args[0]}.")

    def _cmd_print(self, _args):
        self._action_print()
        return CommandResult.print("Print dialog opened.")

    def _cmd_find(self, args):
        if not args:
            return CommandResult.error("Usage: find <text>")
        text = " ".join(args)
        self._action_open_find()
        self.find_bar.set_text(text)
        self._do_find(text)
        return CommandResult.print(f"Finding: {text}")

    # ====================================================================
    # UNDO STACK is set up in __init__ as ``self.undo_stack``.
    # ====================================================================

    # ====================================================================
    # Helpers
    # ====================================================================
    def _as_result(self, ok: bool, msg: str) -> CommandResult:
        return CommandResult.print(msg) if ok else CommandResult.error(msg)

    def _do_open(self, path: str) -> CommandResult:
        if not is_pdf_file(path):
            return CommandResult.error(f"Not a PDF file: {path}")
        abs_path = os.path.abspath(path)
        # If the document is already open in another tab, refresh it from
        # disk (in case the file was modified externally — e.g. by
        # PDFManipulator.rotate_page) and switch to its tab. Otherwise
        # we'd be showing a stale in-memory copy.
        for i, sess in enumerate(self._sessions):
            if sess["path"] and os.path.abspath(sess["path"]) == abs_path:
                # Reload from disk to pick up external edits.
                try:
                    sess["engine"].close()
                except Exception:
                    pass
                ok_reload, _msg = sess["engine"].open(path)
                if ok_reload:
                    # Re-attach the engine so the canvas refreshes.
                    try:
                        sess["pdf_viewer"].attach_engine(sess["engine"])
                    except Exception:
                        pass
                    self._tabs.setCurrentIndex(i)
                    self._set_active_session(sess)
                    try:
                        self.pdf_viewer.refresh()
                    except Exception:
                        pass
                    # VIEW mode is the default after a (re-)open.
                    try:
                        self._set_tool_mode(CanvasMode.VIEW)
                    except Exception:
                        pass
                    return CommandResult.print(
                        f"Reloaded '{os.path.basename(path)}' — "
                        f"{sess['engine'].page_count} pages.")
                # Reload failed — fall through to creating a new session.
                break
        # Create a new session and add it as a tab.
        engine = ViewerEngine()
        ok, msg = engine.open(path)
        if not ok:
            return CommandResult.error(msg)
        pdf_viewer = PDFViewerUI()
        pdf_viewer.attach_engine(engine)
        annot = AnnotationEngine(engine)
        editor = TextEditor(engine)
        session = {
            "path": path,
            "engine": engine,
            "pdf_viewer": pdf_viewer,
            "annot": annot,
            "editor": editor,
            "dirty": False,
            # Single ↔ continuous view swap: ``view single|continuous`` in
            # the terminal replaces the tab's central widget with either
            # the existing single-page viewer (``pdf_viewer``) or the
            # freshly-created continuous view (``continuous_view``).
            # Initial mode matches the default of opening a fresh tab.
            "view_mode": "single",
            "continuous_view": None,
        }
        self._sessions.append(session)
        idx = self._tabs.addTab(pdf_viewer, os.path.basename(path))
        self._tabs.setTabToolTip(idx, path)
        self._bind_session_signals(session)
        self._tabs.setCurrentIndex(idx)
        # A freshly opened document is by definition clean.
        self.has_unsaved_changes = False
        # self.engine / self.pdf_viewer are now updated via
        # _on_tab_changed → _set_active_session.
        self._tabs.currentChanged.emit(idx)
        # Render the first page. Without this, the canvas shows the
        # blank placeholder until the user navigates — fix the
        # "open a PDF and the first page doesn't load" complaint.
        try:
            self.pdf_viewer.refresh()
        except Exception:
            pass
        # Reset per-document undo stack on a new file.
        if hasattr(self, "undo_stack"):
            self.undo_stack.reset()
        self.recent.add(path)
        self._rebuild_recent_menu()
        self._set_status(f"Loaded {os.path.basename(path)}  "
                         f"({self.engine.page_count} pages)")
        self._refresh_tab_title(session)
        # Always reset to VIEW mode on open — the user gets a read-only
        # canvas with the open-hand cursor until they explicitly pick a
        # tool. This matches the expected behavior for a freshly-opened
        # PDF in MS Edge's reader.
        try:
            self._set_tool_mode(CanvasMode.VIEW)
        except Exception:
            pass
        return CommandResult.print(
            f"Opened '{os.path.basename(path)}' — {self.engine.page_count} pages.")

    def _open_recent(self, path: str):
        result = self._do_open(path)
        self._render_result(result)

    def _clear_recent(self):
        self.recent.clear()
        self._rebuild_recent_menu()
        self._render_result(CommandResult.print("Recent files cleared."))

    # ----- UI actions triggered from toolbar/menu ----------------------
    def _open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf)")
        if path:
            result = self._do_open(path)
            self._render_result(result)

    def _save_as_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF as…", "untitled.pdf", "PDF Files (*.pdf)")
        if path:
            ok, msg = self.engine.save(path)
            if ok:
                # Update the active tab to reflect the new file.
                for sess in self._sessions:
                    if sess["engine"] is self.engine:
                        sess["path"] = path
                        sess["dirty"] = False
                        self._refresh_tab_title(sess)
                        break
            self._render_result(self._as_result(ok, msg))

    def _action_fullscreen(self):
        if self.windowState() & Qt.WindowState.WindowFullScreen:
            self.setWindowState(Qt.WindowState.WindowMaximized)
        else:
            self.setWindowState(Qt.WindowState.WindowFullScreen)

    def _action_toggle_theme(self):
        self.theme.toggle()
        self._render_result(CommandResult.print(f"Theme → {self.theme.current()}."))

    def _on_swatch_chosen(self, category: str, color_hex: str):
        """A swatch was clicked → update the active color."""
        try:
            rgb = parse_color(color_hex)
        except ValueError as exc:
            self._render_result(CommandResult.error(str(exc)))
            return
        if category == "pen":
            self._active_pen_color = rgb
            self.annot.set_ink_color(rgb)
            if self._active_tool == CanvasMode.DRAW:
                self.pdf_viewer.set_active_ink(rgb, self._active_pen_thickness)
        elif category == "highlight":
            self._active_highlight_color = rgb
            self.annot.set_highlight_color(rgb)
        elif category == "shape":
            self._active_shape_color = rgb
            self.annot.set_shape_color(rgb)
            if self._active_tool in (CanvasMode.RECT, CanvasMode.ELLIPSE,
                                      CanvasMode.ARROW):
                self.pdf_viewer.set_active_shape(rgb, self._active_shape_thickness)
        self.statusBar().showMessage(
            f"{category.capitalize()} color → {color_hex}", 1500)

    def _refresh_theme_button(self):
        # Icon reflects 'next action': sun in dark mode, moon in light mode
        target = "sun" if self.theme.current() == "dark" else "moon"
        tip = ("Switch to Light theme" if target == "sun"
               else "Switch to Dark theme")
        self.theme_btn.setIcon(IconFactory.get(target, 20))
        self.theme_btn.setToolTip(tip + "  (Ctrl+Shift+T)")

    def _on_theme_changed(self, _name: str):
        """Re-paint every chrome element that depends on the theme.

        Called whenever ``ThemeManager.set`` flips dark↔light. We walk
        the dock-position buttons + chevron + theme button + every
        toolbar QToolButton (each one stores its source icon name on
        ``_termipdf_icon_name`` so we can re-render at the right colour).
        The QSS itself is re-applied by ``ThemeManager.set`` *before* the
        signal fires.
        """
        # Dock-position buttons (live on the terminal dock, not on self).
        dock = getattr(self, "term_dock", None)
        if dock is not None and hasattr(dock, "_pos_buttons"):
            for pos, btn in dock._pos_buttons.items():
                icon_name = getattr(btn, "_termipdf_icon_name", None)
                if icon_name:
                    btn.setIcon(IconFactory.get(icon_name, 14))
        # Chevron (collapse/expand).
        if hasattr(self, "chevron_btn"):
            icon_name = "chevron-down" if self.chevron_btn.isChecked() else "chevron-up"
            self.chevron_btn.setIcon(IconFactory.get(icon_name, 20))
        # Toolbar buttons: re-render from the now-current colour.
        tb = self.findChild(QToolBar, "MainBar")
        if tb is not None:
            for btn in tb.findChildren(QToolButton):
                icon_name = getattr(btn, "_termipdf_icon_name", None)
                if icon_name:
                    btn.setIcon(IconFactory.get(icon_name, 20))
        # Terminal banner / prompt glyph uses theme-aware TermColors.
        # Already-painted history text keeps its original colour; only
        # the chrome (title, prompt, banner when buf is short) refreshes.
        if hasattr(self, "terminal"):
            self.terminal.rebind_palette()

        self._refresh_theme_button()
        # Force repaint of any custom-drawn widgets
        self._update_mode_badge(self._active_tool.value)

    def _action_focus_page_input(self):
        new_pg, ok = QInputDialog.getInt(
            self, "Go to page",
            f"Page number (1..{self.engine.page_count}):",
            self.engine.current_page + 1,
            1, max(1, self.engine.page_count))
        if ok:
            self._render_result(self._cmd_goto([str(new_pg)]))

    def _update_window_title(self):
        """Refresh the window title to include the current document name.

        Format:
            ``TermiPDF — <basename>``           (clean)
            ``TermiPDF — <basename> *``         (unsaved changes)

        When no PDF is open we fall back to just ``TermiPDF``.
        """
        try:
            base = self.APP_NAME
            if self.engine.is_open and self.engine.path:
                name = os.path.basename(self.engine.path)
                base = f"{self.APP_NAME} — {name}"
            try:
                dirty = self.undo_stack.is_dirty()
            except Exception:
                dirty = False
            if dirty:
                base = f"{base}  *"
            self.setWindowTitle(base)
        except Exception:
            pass

    def _shortcut_undo(self):
        """Ctrl+Z handler — runs undo unless the terminal input has focus."""
        if self.terminal and self.terminal.input and self.terminal.input.hasFocus():
            return
        self._render_result(self._cmd_undo([]))

    def _shortcut_redo(self):
        """Ctrl+Y (and Ctrl+Shift+Z) handler — runs redo unless terminal focused."""
        if self.terminal and self.terminal.input and self.terminal.input.hasFocus():
            return
        self._render_result(self._cmd_redo([]))

    def _on_dock_resized(self, *_args):
        """Called when the terminal dock moves or floats. The canvas
        reflows automatically thanks to Qt's dock layout, but we also
        re-fit the page if the user is currently zoomed-to-fit (so the
        page keeps filling the new viewport)."""
        try:
            if self.engine.is_open:
                # Trigger a re-render at the current zoom so any cached
                # pixmap is invalidated. Without this the user sees a
                # blank strip until the next interaction.
                self.pdf_viewer.refresh()
        except Exception:
            pass

    def _action_open_find(self):
        # Phase 4: show the find bar; focus & select-all the input
        if hasattr(self, "find_bar"):
            self.find_bar.show_bar()
        else:
            self._set_status("Find:  use Ctrl+F to open the search bar")

    def _action_open_pages(self):
        """Open the Pages Manager — a Windows-style grid view of all pages
        in the open PDF. Supports multi-select, drag-drop merge, and
        right-click generation of new PDFs."""
        if not self.engine.is_open:
            self._set_status("Open a PDF first to use the Pages Manager.")
            return
        # Lazy-create + keep instance alive across opens so the dialog
        # state (selection, window position) is preserved.
        if not hasattr(self, "_pages_manager") or self._pages_manager is None:
            # ``command_runner`` routes drag-drop through the same
            # terminal backend the user types at the prompt — i.e.
            # ``PageGridWidget.dropEvent`` synthesizes ``swap A B``
            # and we dispatch it via ``parser.execute`` so the GUI
            # chain is literally the same function call a typed
            # command runs (validation, animation, engine reload,
            # status-bar feedback).
            def _cmd_runner(raw: str) -> CommandResult:
                result = self.parser.execute(raw)
                self._render_result(result)
                return result

            # ``undo_pusher`` lets the PM's toolbar / right-click /
            # drag-reorder actions push entries onto the main window's
            # UndoStack — so Ctrl+Z / Ctrl+Y reverse them too. Each
            # branch just translates the PM-side kwarg names into the
            # UndoStack.push_page_op kwargs and updates the window
            # title so the `*` dirty marker appears.
            def _undo_pusher(kind: str, **kwargs):
                if not self.engine or not self.engine.is_open \
                        or not self.engine.path:
                    return
                if kind == "swap":
                    src = kwargs.get("src_page_1based")
                    tgt = kwargs.get("target_page_1based")
                    if not src or not tgt or src == tgt:
                        return
                    self.undo_stack.push_page_op(
                        "swap", page_a=int(src), page_b=int(tgt))
                elif kind == "move":
                    src = kwargs.get("src_page_1based")
                    tgt = kwargs.get("target_slot")
                    if not src or not tgt:
                        return
                    self.undo_stack.push_page_op(
                        "move", src_page=int(src), target_slot=int(tgt))
                elif kind == "rotate":
                    page = kwargs.get("page_1based")
                    angle = kwargs.get("angle")
                    if page is None or angle is None:
                        return
                    self.undo_stack.push_page_op(
                        "rotate", page=int(page), angle=int(angle))
                elif kind == "delete":
                    page = kwargs.get("page_1based")
                    if page is None:
                        return
                    # Cache the page's content BEFORE the caller
                    # deletes it (PM calls the pusher first, then
                    # delete_page — see PagesManager._action_delete_selected).
                    ok_c, _msg_c, cache_pdf = UndoStack.cache_deleted_page(
                        self.engine.path, int(page))
                    if not ok_c:
                        return
                    self.undo_stack.push_page_op(
                        "delete", page=int(page),
                        deleted_page_pdf=cache_pdf)
                try:
                    self._update_window_title()
                except Exception:
                    pass

            self._pages_manager = PagesManager(
                self.engine, parent=self,
                command_runner=_cmd_runner,
                undo_pusher=_undo_pusher,
            )
            self._pages_manager.navigate_to_page.connect(self._on_pages_manager_navigate)
            self._pages_manager.pages_deleted.connect(self._on_pages_manager_deleted)
            self._pages_manager.new_pdf_generated.connect(self._on_pages_manager_new_pdf)
            self._pages_manager.pages_reordered.connect(self._on_pages_manager_reordered)
            self._pages_manager.pages_swapped.connect(self._on_pages_manager_swapped)
        else:
            # Refresh grid in case the document changed
            self._pages_manager._populate()
        self._pages_manager.show()
        self._pages_manager.raise_()
        self._pages_manager.activateWindow()

    def _on_pages_manager_navigate(self, page_1based: int):
        """Page was clicked in the Pages Manager — jump to it in the viewer."""
        ok, msg = self.engine.goto(page_1based - 1)
        if ok:
            self.pdf_viewer.refresh()
            self._render_result(self._as_result(ok, msg))

    def _on_pages_manager_deleted(self, pages: list):
        """Pages were deleted via the Pages Manager — reload the PDF
        so the viewer reflects the new page count.

        We use ``reload_from_disk`` instead of ``_do_open`` so the
        undo stack survives (PM-initiated deletes are already on the
        stack)."""
        if self.engine.path:
            try:
                self.engine.reload_from_disk()
            except Exception:
                pass
            try:
                self.pdf_viewer.refresh()
            except Exception:
                pass
        self._render_result(self._as_result(True,
            f"Deleted {len(pages)} page(s): {pages}."))

    def _on_pages_manager_new_pdf(self, out_path: str):
        """A new PDF was generated by the Pages Manager — log it."""
        self._render_result(self._as_result(True,
            f"New PDF created: {out_path}"))

    def _on_pages_manager_reordered(self, new_index_1based: int):
        """A page was reordered via drag-and-drop — reload the PDF so
        the viewer reflects the new page order, then navigate to the
        moved page. Uses ``reload_from_disk`` to preserve the undo
        stack that the PM-initiated move just pushed."""
        if self.engine.path:
            try:
                self.engine.reload_from_disk()
            except Exception:
                pass
            ok, msg = self.engine.goto(new_index_1based - 1)
            if ok:
                self.pdf_viewer.refresh()
        self._render_result(self._as_result(True,
            f"Reordered pages; moved page now at slot {new_index_1based}."))

    def _on_pages_manager_swapped(self, src_1based: int, target_1based: int):
        """A true page-swap landed via drag-and-drop. Reload the PDF and
        keep the viewer's focus on the source page's *new* slot
        (which equals target_1based — the source was dropped there)."""
        if self.engine.path:
            try:
                self.engine.reload_from_disk()
            except Exception:
                pass
            ok, _msg = self.engine.goto(target_1based - 1)
            if ok:
                self.pdf_viewer.refresh()
        self._render_result(self._as_result(True,
            f"Swapped pages {src_1based} and {target_1based}."))

    # ----------------------------------------------- find helpers
    def _do_find(self, text: str):
        if not self.engine.is_open or not text:
            self._find_matches = []
            self._find_idx = -1
            self.find_bar.set_match_count(0, 0)
            return
        matches = self.engine.find_all(text)
        self._find_matches = matches
        self._find_idx = 0 if matches else -1
        self.find_bar.set_match_count(0 if not matches else 1, len(matches))
        if matches:
            self._jump_to_find_match()

    def _do_find_step(self, delta: int):
        if not self._find_matches:
            self.find_bar.set_match_count(0, 0)
            return
        self._find_idx = (self._find_idx + delta) % len(self._find_matches)
        self.find_bar.set_match_count(self._find_idx + 1, len(self._find_matches))
        self._jump_to_find_match()

    def _jump_to_find_match(self):
        if not self._find_matches or self._find_idx < 0:
            return
        page_index, rect = self._find_matches[self._find_idx]
        ok, _msg = self.engine.goto(page_index)
        if ok:
            self.pdf_viewer.refresh()

    def _action_about(self):
        QMessageBox.about(
            self, "About TermiPDF",
            "<b>TermiPDF v2</b><br>"
            "A minimal, Edge-style PDF editor with an embedded terminal.<br><br>"
            "Built with PyQt6 and PyMuPDF.")

    def _action_qr_popup(self):
        """Open the QR-share popup for arbitrary user-supplied text.

        Called by the QR toolbar button — shows a tiny QInputDialog first
        so the user can type/paste whatever they want to encode, then
        opens the same floating QR dialog used by right-click → share.
        No more permanent stamps on the page.
        """
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "QR Code", "Text or URL to encode:")
        if not ok or not text:
            return
        # Reuse the same floating popup that right-click share uses.
        self._qr_share_text(text)

    def _action_toggle_toolbar(self):
        """Show / hide the main toolbar (Edge-style chevron).

        When hidden, a small floating arrow button appears at the top-left
        of the window so the user can always bring the toolbar back with
        one click. The toolbar's own chevron flips to point down for
        inline affordance. The terminal dock and left rail are unaffected
        — only the toolbar's annotation tools disappear.
        """
        try:
            tb = self.main_toolbar
            if tb.isVisible():
                tb.hide()
                if hasattr(self, "chevron_btn") and self.chevron_btn is not None:
                    self.chevron_btn.setIcon(IconFactory.get("chevron-down", 20))
                    self.chevron_btn.setToolTip(
                        "Show toolbar (Ctrl+Shift+H)")
                # Show a persistent floating reveal button (not part of
                # the toolbar so it stays accessible when the bar is
                # hidden).
                self._show_toolbar_reveal()
            else:
                tb.show()
                if hasattr(self, "chevron_btn") and self.chevron_btn is not None:
                    self.chevron_btn.setIcon(IconFactory.get("chevron-up", 20))
                    self.chevron_btn.setToolTip(
                        "Hide toolbar (Ctrl+Shift+H)")
                self._hide_toolbar_reveal()
        except Exception:
            pass

    def _show_toolbar_reveal(self):
        """Show the small floating 'arrow' button that brings the toolbar
        back. Positioned at the top-left of the central widget, just
        below the menubar."""
        if getattr(self, "_toolbar_reveal_btn", None) is None:
            from PyQt6.QtWidgets import QToolButton
            btn = QToolButton(self)
            btn.setObjectName("toolbarRevealBtn")
            btn._termipdf_icon_name = "chevron-down"
            btn.setIcon(IconFactory.get("chevron-down", 20))
            btn.setToolTip("Show toolbar (Ctrl+Shift+H)")
            btn.setAutoRaise(False)
            btn.setFixedSize(28, 28)
            btn.clicked.connect(self._action_toggle_toolbar)
            self._toolbar_reveal_btn = btn
        # Position just under the menubar/toolbar area, top-right of central widget
        self._toolbar_reveal_btn.move(
            self.width() - self._toolbar_reveal_btn.width() - 8, 4)
        self._toolbar_reveal_btn.show()
        self._toolbar_reveal_btn.raise_()

    def _hide_toolbar_reveal(self):
        btn = getattr(self, "_toolbar_reveal_btn", None)
        if btn is not None:
            btn.hide()

    def _action_screenshot(self):
        """Save the current page (with annotations baked in) as a PNG file.

        Opens a file-save dialog at ``<docname>_page_<N>.png``. Also copies
        the PNG to the system clipboard so the user can paste it directly
        into a chat / doc without saving first.
        """
        if not self.engine.is_open:
            self._render_result(CommandResult.error("No PDF is open."))
            return
        from PyQt6.QtWidgets import QFileDialog
        from PyQt6.QtGui import QImage, QGuiApplication
        import fitz as _fitz
        # Re-render at 2x the current zoom for a crisp PNG.
        zoom_factor = max(1.5, self.engine.zoom)
        try:
            page = self.engine.get_current_page()
            pix = page.get_pixmap(
                matrix=_fitz.Matrix(zoom_factor, zoom_factor),
                alpha=False,
            )
            png_bytes = pix.tobytes("png")
        except Exception as exc:
            self._render_result(CommandResult.error(
                f"Screenshot failed: {exc}"))
            return
        # Default filename: <docname>_page_<N>.png in the user's home dir.
        default_name = f"{os.path.splitext(os.path.basename(self.engine.path or 'screenshot'))[0]}_page_{self.engine.current_page + 1}.png"
        default_dir = os.path.expanduser("~")
        default_path = os.path.join(default_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save screenshot", default_path,
            "PNG Images (*.png)")
        if path:
            try:
                with open(path, "wb") as f:
                    f.write(png_bytes)
            except Exception as exc:
                self._render_result(CommandResult.error(
                    f"Could not save screenshot: {exc}"))
                return
        # Always copy to clipboard so the user can paste even if they
        # cancel the save dialog.
        try:
            img = QImage.fromData(png_bytes)
            QGuiApplication.clipboard().setImage(img)
        except Exception:
            pass
        if path:
            self._render_result(CommandResult.print(
                f"Screenshot saved → {path}  (also copied to clipboard)"))
        else:
            self._render_result(CommandResult.print(
                f"Screenshot of page {self.engine.current_page + 1} "
                f"({pix.width}×{pix.height}) copied to clipboard."))

    def _action_screenshot_region(self):
        """Region-screenshot using the OS-native screenshot tool.

        Invokes gnome-screenshot / grim / flameshot / scrot (whichever
        is available on Linux), or Snipping Tool / Powershell on
        Windows, so the user can pick exactly which segment of the UI
        to capture — including the entire app window, a region of the
        PDF, the terminal pane, etc. The captured PNG is then loaded
        from disk and put on the clipboard so the user can paste it
        immediately. Does NOT require a PDF to be open.
        """
        import subprocess
        import shutil as _shutil
        from PyQt6.QtGui import QGuiApplication

        # Save to a per-user cache dir (XDG_CACHE_HOME on Linux,
        # platform default elsewhere) so we comply with sandboxing
        # rules — never litter $HOME with screenshot files. Filename
        # is timestamped + PID-suffixed to avoid collisions between
        # concurrent invocations.
        import tempfile as _tempfile
        cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
        out_dir = (cache_home if cache_home
                   else _tempfile.gettempdir())
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            out_dir = _tempfile.mkdtemp(prefix="termipdf_screenshot_")
        ts = time.strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(
            out_dir, f"termipdf-screenshot-{ts}-{os.getpid()}.png")

        # Detect the platform's native screenshot tool. Order =
        # preferred-first. Each entry: (program-name, argv-template).
        # The argv template is invoked as ``argv-template + [out_path]``;
        # for "select region" tools the user controls which part of the
        # screen is captured via the GUI that pops up.
        candidates: list[tuple[str, list[str]]] = []
        if sys.platform.startswith("linux"):
            candidates = [
                ("gnome-screenshot",   ["gnome-screenshot", "-a", "-f"]),
                ("grim",               ["grim", "-g", "area"]),
                ("flameshot",          ["flameshot", "gui", "-p"]),
                ("spectacle",          ["spectacle", "-rno", "-b"]),
                ("scrot",              ["scrot", "-s"]),
                ("maim",               ["maim", "-s"]),
            ]
        elif sys.platform == "darwin":
            candidates = [
                ("screencapture",      ["screencapture", "-i"]),
            ]
        elif sys.platform == "win32":
            # On Windows we use the built-in Snipping Tool via PowerShell
            # so we don't need to ship anything extra. This launches the
            # modern Snip & Sketch UI which is region-select by default.
            candidates = [
                ("powershell-snippingtool", []),
            ]

        chosen = None
        for prog, base in candidates:
            try:
                if prog == "powershell-snippingtool":
                    # Always "available" on Win10+.
                    chosen = (prog, base)
                    break
                if _shutil.which(prog) is not None:
                    chosen = (prog, base)
                    break
            except Exception:
                continue
        if chosen is None:
            self._render_result(CommandResult.error(
                "No screenshot tool found. Install gnome-screenshot, "
                "grim, flameshot, or spectacle."))
            return
        prog, base = chosen

        try:
            if prog == "powershell-snippingtool":
                # Launch Snip & Sketch via PowerShell; the user picks a
                # region and the clip is copied to the clipboard. We
                # can't directly pipe the file path through so we just
                # open the tool and rely on the clipboard.
                ps_cmd = (
                    "Start-Process -FilePath 'ms-screenclip:' "
                    "-ErrorAction SilentlyContinue")
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._render_result(CommandResult.print(
                    "Opened Windows Snip & Sketch — region-select and "
                    "the image will land on your clipboard."))
                return
            # Build the final argv. ``out_path`` always goes last so
            # tools that support an output file write there.
            if prog == "grim":
                # grim uses -g for region selection AND outputs to stdout
                # when "-" is given. Pipe stdout to a file.
                argv = ["grim", "-g", "-", out_path]
            else:
                argv = list(base) + [out_path]
            self._render_result(CommandResult.print(
                f"Invoking {prog}… pick an area of the screen."))
            # Run in the background; the user picks a region in the
            # tool's UI. We do NOT block the UI here.
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Watch for the file to appear so we can hand it to the
            # clipboard. Block here briefly (up to 5 s) — the OS tool
            # typically finishes within 1-2 s after the user picks.
            deadline = time.time() + 5.0
            saved = None
            while time.time() < deadline:
                if proc.poll() is not None and os.path.exists(out_path):
                    saved = out_path
                    break
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    # Some tools write-then-fork (grim). Allow a small
                    # grace period so the file isn't half-written.
                    time.sleep(0.3)
                    if os.path.getsize(out_path) > 0:
                        saved = out_path
                        break
                time.sleep(0.05)
            if saved is None and os.path.exists(out_path):
                saved = out_path
            if saved and os.path.getsize(saved) > 0:
                try:
                    with open(saved, "rb") as f:
                        png_bytes = f.read()
                    img = QImage.fromData(png_bytes)
                    if not img.isNull():
                        QGuiApplication.clipboard().setImage(img)
                    self._render_result(CommandResult.print(
                        f"Region screenshot saved → {saved} "
                        f"(also copied to clipboard)"))
                except Exception as exc:
                    self._render_result(CommandResult.error(
                        f"Could not read screenshot file: {exc}"))
            else:
                self._render_result(CommandResult.print(
                    f"{prog} finished but no file was produced. "
                    f"You can still paste from the screenshot tool's own "
                    f"clipboard."))
        except Exception as exc:
            self._render_result(CommandResult.error(
                f"Screenshot failed: {exc}"))

    def _action_rotate(self):
        """Rotate the current page 90° clockwise and refresh.

        The rotation is written to the PDF in place (atomic temp-file
        replace so a crash can't corrupt the document) and the viewer
        is reloaded to reflect the new rotation. The user stays on the
        same page after the rotation.
        """
        if not self.engine.is_open:
            self._render_result(CommandResult.error("No PDF is open."))
            return
        page_num = self.engine.current_page + 1   # 1-based
        # Remember the page we were on so we can restore it after the
        # reload (reload clamps current_page to the new page count).
        saved_page = self.engine.current_page
        saved_scroll = self.pdf_viewer.scroll_area.verticalScrollBar().value()
        ok, msg = PDFManipulator.rotate_page(self.engine.path, page_num, 90)
        if ok:
            # Push the undo entry BEFORE reloading so the entry's
            # forward payload matches what the user just saw.
            self.undo_stack.push_page_op("rotate", page=page_num, angle=90)
            # Reload in place (NOT _do_open — it would wipe the undo
            # stack we just pushed).
            try:
                self.engine.reload_from_disk()
            except Exception:
                pass
            # Restore the page we were viewing (clamped to the new count,
            # which is unchanged for a single-page rotation).
            target = max(0, min(saved_page, max(self.engine.page_count - 1, 0)))
            ok_goto, _msg = self.engine.goto(target)
            if ok_goto:
                self.pdf_viewer.refresh(animate=False)
                # Restore the scroll position so the user lands on the
                # same content spot as before the rotation.
                sb = self.pdf_viewer.scroll_area.verticalScrollBar()
                sb.setValue(min(saved_scroll, sb.maximum()))
            # Refresh the Pages Manager grid if it's open.
            pm = getattr(self, "_pages_manager", None)
            if pm is not None and pm.isVisible():
                try:
                    pm._populate()
                except Exception:
                    pass
            try:
                self._update_window_title()
            except Exception:
                pass
            self._render_result(CommandResult.print(
                f"Rotated page {page_num} by 90°. Saved in place."))
        else:
            self._render_result(CommandResult.error(
                f"Rotate failed: {msg}"))

    def _action_print(self):
        if not self.engine.is_open:
            self._render_result(CommandResult.error("No PDF is open."))
            return
        try:
            from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            dlg = QPrintDialog(printer, self)
            if dlg.exec() == QPrintDialog.DialogCode.Accepted:
                page = self.engine.get_current_page()
                from PyQt6.QtGui import QPainter, QImage
                import fitz as _fitz
                painter = QPainter(printer)
                try:
                    rect = printer.pageRect(QPrinter.Unit.DevicePixel)
                    page_rect = page.rect
                    scale = min(rect.width() / page_rect.width,
                                rect.height() / page_rect.height)
                    pix = page.get_pixmap(matrix=_fitz.Matrix(scale, scale), alpha=False)
                    img = QImage.fromData(pix.tobytes("png"))
                    painter.drawImage(rect, img)
                finally:
                    painter.end()
                self._render_result(CommandResult.print("Printed current page."))
        except ImportError:
            self._render_result(
                CommandResult.error("PyQt6.QtPrintSupport not installed; "
                                    "install via `pip install PyQt6-PrintSupport`."))
        except Exception as exc:
            self._render_result(CommandResult.error(f"Print failed: {exc}"))

    def _on_pgdn(self):
        if self.engine.is_open:
            self._render_result(self._cmd_next([]))
        else:
            # Scroll canvas even if no PDF is open
            sa = self.pdf_viewer.scroll_area
            v = sa.verticalScrollBar().value()
            sa.verticalScrollBar().setValue(v + sa.viewport().height() * 0.8)

    def _on_pgup(self):
        if self.engine.is_open:
            self._render_result(self._cmd_prev([]))
        else:
            sa = self.pdf_viewer.scroll_area
            v = sa.verticalScrollBar().value()
            sa.verticalScrollBar().setValue(v - sa.viewport().height() * 0.8)

    # ====================================================================
    # Internal handlers: command flow + page events
    # ====================================================================
    def _on_command(self, raw: str):
        result = self.parser.execute(raw)
        self._render_result(result)

    def _render_result(self, result: CommandResult):
        action = result.action
        if action == "print":
            text = result.data.get("text", "")
            # Help text starts with a div — render as rich, no green wrapper
            if text.lstrip().startswith("<"):
                self.terminal.append_output(text)
            else:
                self.terminal.append_output(
                    f"<span style='color:#a6e3a1;'>{self._escape(text)}</span>"
                )
        elif action == "error":
            self.terminal._error(result.data.get("text", ""))
        elif action == "clear":
            self.terminal.clear_output()
        elif action == "exit":
            self.close()
        elif action == "open":
            self.terminal._success(result.data.get("text", ""))

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _set_status(self, text: str):
        self.statusBar().showMessage(text, 4000)

    def _on_toc_navigate(self, page_1based: int):
        ok, msg = self.engine.goto(page_1based - 1)
        if ok:
            self.pdf_viewer.refresh()

    def _on_page_rendered(self, page_1based: int):
        total = self.engine.page_count
        self._update_page_indicator(page_1based, total)
        self._update_zoom_labels()
        self.thumbs.set_current_page(page_1based)

    def _update_zoom_labels(self):
        """Push the current engine zoom into every visible zoom label."""
        try:
            pct = f"{int(self.engine.zoom * 100)}%"
            if hasattr(self, "zoom_status") and self.zoom_status is not None:
                self.zoom_status.setText(pct)
            if hasattr(self, "zoom_label") and self.zoom_label is not None:
                self.zoom_label.setText(pct)
        except Exception:
            pass

    def _on_page_advance(self, delta: int):
        """Scroll-past-end → next/prev page (Edge-style).

        Called only when the virtual-scroll commit FAILED to render
        the next page (rare fallback path). The normal flow goes
        through ``_on_page_advance_committed`` instead.
        """
        if delta > 0:
            ok, msg = self.engine.next_page()
        elif delta < 0:
            ok, msg = self.engine.prev_page()
        else:
            return
        if ok:
            self.pdf_viewer.refresh()

    def _on_page_advance_committed(self, new_page_1based: int):
        """The viewer already advanced the engine + swapped the
        pixmap in-place (no preview pass, no background worker, no
        blink). We just sync the page-indicator label and status bar.
        """
        self._update_page_indicator(new_page_1based, self.engine.page_count)
        try:
            self._render_result(self._as_result(
                True, f"Page {new_page_1based} of "
                f"{self.engine.page_count}."))
        except Exception:
            pass

    def _on_canvas_context_menu(self, pt_pdf):
        """Show a right-click menu on the canvas. The interesting entry is
        'Share selection as QR' which is enabled when there's a non-empty
        selection buffer (text the user just picked via SELECT mode)."""
        from PyQt6.QtGui import QAction
        menu = QMenu(self)

        selection = self.pdf_viewer.get_selection() or ""
        # Also pull from the system clipboard as a fallback — if the user
        # selected text in another app (or just used SELECT mode without
        # the canvas-event buffer), the clipboard still has it.
        try:
            from PyQt6.QtWidgets import QApplication
            cb_text = QApplication.clipboard().text() or ""
        except Exception:
            cb_text = ""
        share_text = (selection or cb_text).strip()

        act_share = menu.addAction("Share selection as QR…")
        act_share.setEnabled(bool(share_text))
        act_share.triggered.connect(
            lambda _=False, t=share_text, p=pt_pdf: self._qr_share_text(t, p)
        )

        menu.addSeparator()

        # Always-available items: paste text at point, copy page text
        act_copy_page = menu.addAction("Copy all text on page")
        act_copy_page.triggered.connect(self._copy_page_text)

        act_paste = menu.addAction("Insert text here…")
        act_paste.triggered.connect(
            lambda _=False, p=pt_pdf: self._insert_text_at_point(p)
        )

        # Use popup() so it doesn't block in headless tests; in real GUI
        # usage popup() is event-driven and closes on click.
        try:
            menu.popup(QCursor.pos())
        except Exception:
            pass
        # Keep menu alive (popup() transfers ownership, but be explicit
        # for clarity)
        self._open_context_menu = menu

    def _qr_share_text(self, text: str, pt_pdf=None):
        """Open a non-modal QR-share dialog containing the given text.

        Unlike the old behavior which would stamp the QR onto the PDF
        (permanently), this opens a floating popup with the QR image and a
        copy button — matching MS Edge's "Share as QR" UX where the QR is
        a transient overlay rather than a permanent page edit.
        """
        if not text:
            return
        # Render the QR to PNG bytes via the QR logic without writing to
        # the PDF, then open the popup. Use a large pixel size so the QR
        # is comfortably scannable by a phone camera. The render_png
        # helper handles truncation for over-long selections so we
        # never get a crash from the qrcode library's 40-version cap.
        try:
            from features.qr_generator.qr_logic import render_png
            png_bytes, meta = render_png(text, size_pt=900)
        except Exception as exc:
            self._render_result(CommandResult.error(f"QR render failed: {exc}"))
            return

        try:
            from features.qr_generator.qr_share_dialog import QRShareDialog
            # The QR dialog now sizes itself responsively on every
            # resizeEvent — we only need to seed it on the screen and
            # let the user resize it via the platform's grip.
            dlg = QRShareDialog(
                png_bytes, meta.get("encoded_length", len(text)) and
                (text[:meta.get("encoded_length", len(text))]
                 if meta.get("truncated") else text),
                parent=self,
                truncated=meta.get("truncated", False),
                original_length=meta.get("original_length", len(text)),
                encoded_length=meta.get("encoded_length", len(text)),
            )
            self._position_child_on_screen(dlg)
            dlg.show()
        except Exception as exc:
            self._render_result(CommandResult.error(f"QR dialog failed: {exc}"))

    def _position_child_on_screen(self, child) -> None:
        """Centre ``child`` on this window, shrunk to fit the active screen.

        Shrinks only (never grows) so the dialog's own layout isn't
        disturbed on screens that already fit it. The child's hard
        ``minimumSize`` is dropped to (0, 0) on-demand — the dialog's
        authored floor would otherwise clamp our resize() up and the
        screen-fit math would no-op.
        """
        try:
            avail = self._available_screen_rect()
            if avail is None:
                return
            if (child.minimumWidth() > avail.width()
                    or child.minimumHeight() > avail.height()):
                # Only relax when we actually need to resize below
                # the authored minimum; the common (fits-already) case
                # then skips the property churn entirely.
                child.setMinimumSize(0, 0)
            child.setMaximumSize(avail.width(), avail.height())
            target_w = min(child.width(), avail.width())
            target_h = min(child.height(), avail.height())
            if (target_w, target_h) != (child.width(), child.height()):
                child.resize(target_w, target_h)
            parent_geo = self.frameGeometry()
            cx = parent_geo.center().x() - child.width() // 2
            cy = parent_geo.center().y() - child.height() // 2
            x = max(avail.left(), min(cx, avail.right() - child.width() + 1))
            y = max(avail.top(), min(cy, avail.bottom() - child.height() + 1))
            child.move(x, y)
        except Exception:
            # Best-effort — if positioning fails the popup still opens,
            # it just may not be centred.
            pass

    def _available_screen_rect(self):
        """Return the available geometry of the screen this window sits on.

        Falls back to the primary screen (multi-monitor edge case where
        the parent's centre is outside any known screen) and to ``None``
        if no screens are registered.
        """
        try:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.screenAt(self.frameGeometry().center())
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            return screen.availableGeometry() if screen is not None else None
        except Exception:
            return None

    def _copy_page_text(self):
        if not self.engine.is_open:
            return
        try:
            text = self.engine.get_current_page().get_text("text")
        except Exception:
            text = ""
        if text.strip():
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self._render_result(CommandResult.print(
                f"Copied {len(text)} chars of page text to clipboard."))
        else:
            self._render_result(CommandResult.print("Page has no text layer."))

    def _insert_text_at_point(self, pt_pdf):
        x = float(pt_pdf.x()) if hasattr(pt_pdf, "x") else float(pt_pdf[0])
        y = float(pt_pdf.y()) if hasattr(pt_pdf, "y") else float(pt_pdf[1])
        self._cmd_mode(["text"])
        # Pre-fill the text insert at the clicked point
        try:
            self.pdf_viewer._pending_text_pt = QPointF(x, y)
        except Exception:
            pass

    # ====================================================================
    # Drag & Drop
    # ====================================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = url.toLocalFile().lower()
                if p.endswith(".pdf"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            return
        # Collect all PDFs from the drop. Multi-file drops merge into the
        # currently open document (or open the first one if nothing is open).
        urls = event.mimeData().urls()
        pdf_paths = []
        for url in urls:
            p = url.toLocalFile()
            if p.lower().endswith(".pdf") and os.path.isfile(p):
                pdf_paths.append(p)
        if not pdf_paths:
            event.ignore()
            return

        event.acceptProposedAction()
        # Single PDF drop, no PDF open → just open it.
        if not self.engine.is_open or len(pdf_paths) == 1:
            path = pdf_paths[0]
            self.terminal.append_output(
                f"<span style='color:#89b4fa;'>» [drag & drop] "
                f"open \"{os.path.basename(path)}\"</span>"
            )
            self._render_result(self._do_open(path))
            return

        # Multiple PDFs dropped while one is already open → merge them all
        # into the current document in the order they appear.
        base = self.engine.path
        try:
            import fitz as _fitz
            out_doc = _fitz.open(base)
            total_added = 0
            for pdf in pdf_paths:
                if os.path.abspath(pdf) == os.path.abspath(base):
                    continue
                src = _fitz.open(pdf)
                pre = len(out_doc)
                out_doc.insert_pdf(src)
                total_added += len(out_doc) - pre
                src.close()
            # Atomic save
            tmp = base + ".merge.tmp"
            out_doc.save(tmp, garbage=4, deflate=True)
            out_doc.close()
            os.replace(tmp, base)
        except Exception as exc:
            return self._render_result(CommandResult.error(
                f"Drag-drop merge failed: {exc}"))

        # Re-open so the engine sees the merged PDF.
        saved_page = self.engine.current_page
        self._do_open(base)
        if saved_page < self.engine.page_count:
            self.engine.goto(saved_page)
            self.pdf_viewer.refresh()
        self._render_result(CommandResult.print(
            f"Merged {len(pdf_paths) - (1 if any(os.path.abspath(p) == os.path.abspath(base) for p in pdf_paths) else 0)}"
            f" PDF(s) into <b>{os.path.basename(base)}</b> ({total_added} pages added)."))

    # ====================================================================
    # Close
    # ====================================================================
    def closeEvent(self, event: QCloseEvent):
        # If the document has unsaved edits (drawings, highlights,
        # text inserts, etc.), prompt the user with save/discard/cancel
        # before letting the close land. We check undo_stack.is_dirty()
        # if available; otherwise we treat an open PDF as potentially
        # dirty and ask anyway — better to over-prompt than to lose
        # the user's edits.
        if self.engine.is_open and self._doc_has_unsaved_edits():
            ans = self._prompt_save_on_close()
            if ans == "cancel":
                event.ignore()
                return
            if ans == "save":
                saved = self._save_current_doc()
                if not saved:
                    # User cancelled the save dialog → don't quit.
                    event.ignore()
                    return
        # Block on any in-flight annotation save so we don't quit while
        # a PDF is half-written.
        if hasattr(self, "_router") and self._router is not None:
            try:
                self._router.shutdown()
            except Exception:
                pass
        if self.engine.is_open:
            self.engine.close()
        super().closeEvent(event)

    # -------------------------------------------------- save-on-close helpers
    def _doc_has_unsaved_edits(self) -> bool:
        """True if any session has a dirty undo stack.

        The undo stack tracks every drawing / highlight / erase /
        text-insert / shape the user has applied since the last save
        (or since the PDF was opened if never saved). We also treat
        any in-place page mutation (swap, delete, rotate via the
        Pages Manager) as "dirty" because those write to disk
        directly — there's nothing to save in that case, but the
        file has already been modified. ``mark_unsaved()`` flips
        ``self.has_unsaved_changes`` directly, which we also honour
        here so explicit page-mutation events (swap, delete, rotate)
        prompt the user before the window closes.
        """
        if self.has_unsaved_changes:
            return True
        for sess in getattr(self, "_sessions", []):
            stack = sess.get("undo_stack")
            if stack is not None:
                try:
                    if stack.is_dirty():
                        return True
                except Exception:
                    pass
        return False

    def _prompt_save_on_close(self) -> str:
        """Show the standard 3-button dialog: Save / Discard / Cancel.

        Returns 'save', 'discard', or 'cancel'. The dialog also tells
        the user which file is unsaved so they don't accidentally
        discard edits to the wrong doc when several tabs are open.
        """
        # Find the active session's path (or first dirty one).
        dirty_paths: list[str] = []
        for sess in getattr(self, "_sessions", []):
            stack = sess.get("undo_stack")
            if stack is not None:
                try:
                    if stack.is_dirty() and sess.get("path"):
                        dirty_paths.append(os.path.basename(str(sess["path"])))
                except Exception:
                    pass
        if not dirty_paths:
            # No specific file flagged — use the active engine's path.
            if self.engine.path:
                dirty_paths.append(os.path.basename(self.engine.path))
        files_txt = ", ".join(f"'{p}'" for p in dirty_paths) if dirty_paths else "this document"
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Unsaved changes")
        msg.setText(
            f"You have unsaved edits in {files_txt}.\n\n"
            "Save before closing?"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Save)
        ret = msg.exec()
        if ret == QMessageBox.StandardButton.Save:
            return "save"
        if ret == QMessageBox.StandardButton.Discard:
            return "discard"
        return "cancel"

    def _save_current_doc(self) -> bool:
        """Save the currently-focused session's annotations into its PDF.

        Returns True if the save completed (or the user picked "Save As"
        and chose a path), False if the user cancelled the file dialog.
        Mirrors the File → Save menu behavior.
        """
        if not self.engine.is_open or not self.engine.path:
            return True   # nothing to save
        return bool(self._save_as_dialog())


# =====================================================================
# Dockable terminal wrapper
# =====================================================================
class DockableTerminal(QDockWidget):
    """Edge-DevTools-style dockable terminal with position buttons in the title."""

    def __init__(self, terminal_widget: "TerminalUI", parent=None):
        super().__init__("Terminal", parent)
        self.setObjectName("term_dock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self._terminal = terminal_widget
        self.setWidget(terminal_widget)
        self._build_custom_titlebar()

    def _build_custom_titlebar(self):
        """Edge-style custom title bar with dock-position buttons."""
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)

        # Title + grip (provides drag-to-dock)
        grip_btn = QToolButton()
        grip_btn.setObjectName("dockHandle")
        grip_btn.setText("▤")
        grip_btn.setToolTip("Drag to re-dock the terminal")
        layout.addWidget(grip_btn)

        title_label = QLabel("Terminal")
        title_label.setStyleSheet("font-weight: bold; padding: 0 6px;")
        layout.addWidget(title_label)
        layout.addStretch(1)

        # Dock-position selectors
        self._pos_buttons: dict[str, QToolButton] = {}
        for pos, icon_name in (("bottom", "dock-bottom"),
                                ("left",   "dock-left"),
                                ("right",  "dock-right"),
                                ("float",  "dock-float")):
            btn = QToolButton()
            btn.setObjectName("dockPosition")
            # Stash the icon name so theme changes can re-render.
            btn._termipdf_icon_name = icon_name
            btn.setIcon(IconFactory.get(icon_name, 14))
            btn.setToolTip(f"Dock {pos}")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, p=pos: self.dock_to(p))
            layout.addWidget(btn)
            self._pos_buttons[pos] = btn

        # Close (just hide)
        close_btn = QToolButton()
        close_btn.setObjectName("dockHandle")
        close_btn._termipdf_icon_name = "close"
        close_btn.setIcon(IconFactory.get("close", 14))
        close_btn.setToolTip("Hide terminal  (Ctrl+J to bring it back)")
        close_btn.clicked.connect(self.hide)
        layout.addWidget(close_btn)

        self.setTitleBarWidget(wrapper)
        self._titlebar = wrapper
        self._update_active_position("bottom")

    def dock_to(self, position: str):
        """Snap to a given dock area; always leave the terminal visible."""
        parent = self.parent()
        if parent is None:
            return
        # Re-attach first to clear floats
        if self.isFloating():
            self.setFloating(False)
        area_map = {
            "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
            "left":   Qt.DockWidgetArea.LeftDockWidgetArea,
            "right":  Qt.DockWidgetArea.RightDockWidgetArea,
            "top":    Qt.DockWidgetArea.TopDockWidgetArea,
            "float":  None,
        }
        if position == "float":
            self.setFloating(True)
            main_geo = parent.geometry()
            self.move(main_geo.center().x() - 400,
                      main_geo.center().y() - 200)
            self.resize(800, 400)
        else:
            if not parent.isAncestorOf(self):
                parent.addDockWidget(area_map[position], self)
            else:
                # Detach then re-add at the new area so the layout reflows
                parent.removeDockWidget(self)
                parent.addDockWidget(area_map[position], self)
        # Show after re-docking — Qt hides the widget during the
        # removeDockWidget/addDockWidget cycle, so we explicitly restore it.
        self.show()
        self.raise_()
        self._update_active_position(position)
        # Update terminal banner
        self._terminal._info(f"Terminal docked → {position}")

    def _update_active_position(self, active: str):
        for pos, btn in self._pos_buttons.items():
            btn.setChecked(pos == active)
