"""
main_window.py — The top-level orchestrator.

Responsibilities:
* Compose the three-pane layout: [Outline TOC] | [PDF Canvas] | [Terminal]
* Own a single ViewerEngine (shared across features)
* Wire all features into the command parser via `register(...)`
* Forward UI events (drag-drop, TOC clicks, canvas changes) into the engine
"""
from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QSplitter,
    QToolBar,
    QStatusBar,
    QMessageBox,
    QFileDialog,
)

# ---- Feature imports (each feature is self-contained) -------------------
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

# ---- Shared utils --------------------------------------------------------
from shared.utils.color_utils import parse_color
from shared.utils.path_solver import resolve_user_path, is_pdf_file


class TermiPDFWindow(QMainWindow):
    """The single main window of the application."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TermiPDF  —  Smart PDF Editor with Hacker Console")
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        # ---- Core services ------------------------------------------------
        self.engine = ViewerEngine()
        self.annot = AnnotationEngine(self.engine)
        self.editor = TextEditor(self.engine)
        self.qr = QRLogic(self.engine)
        self.parser = CommandParser()

        # ---- UI -----------------------------------------------------------
        self._build_toolbar()
        self._build_statusbar()
        self._build_central()
        self._register_commands()
        self._wire_signals()

    # ====================================================================
    # UI construction
    # ====================================================================
    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        open_act = QAction("📂 Open PDF…", self)
        open_act.setShortcut(QKeySequence("Ctrl+O"))
        open_act.triggered.connect(self._open_file_dialog)
        tb.addAction(open_act)

        tb.addSeparator()

        toggle_toc = QAction("📑 Toggle TOC", self)
        toggle_toc.setShortcut(QKeySequence("Ctrl+B"))
        toggle_toc.triggered.connect(self._toggle_toc)
        tb.addAction(toggle_toc)

        toggle_term = QAction("💻 Toggle Terminal", self)
        toggle_term.setShortcut(QKeySequence("Ctrl+`"))
        toggle_term.triggered.connect(self.toggle_terminal)
        tb.addAction(toggle_term)

        tb.addSeparator()

        save_act = QAction("💾 Save PDF", self)
        save_act.setShortcut(QKeySequence("Ctrl+S"))
        save_act.triggered.connect(self._save_pdf)
        tb.addAction(save_act)

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        sb.showMessage("Ready. Type 'help' in the terminal or drop a PDF to start.")

    def _build_central(self):
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # Outer splitter: TOC | (PDF canvas + Terminal inner splitter)
        self.outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.outer_splitter)

        # ----- Left: TOC --------------------------------------------------
        self.toc = TOCUI()
        self.toc.setMinimumWidth(220)
        self.toc.setMaximumWidth(420)
        self.outer_splitter.addWidget(self.toc)

        # ----- Right: vertical splitter (PDF | Terminal) -------------------
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.pdf_viewer = PDFViewerUI()
        self.pdf_viewer.attach_engine(self.engine)
        self.terminal = TerminalUI()

        self.right_splitter.addWidget(self.pdf_viewer)
        self.right_splitter.addWidget(self.terminal)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 1)
        self.outer_splitter.addWidget(self.right_splitter)

        self.outer_splitter.setStretchFactor(0, 0)
        self.outer_splitter.setStretchFactor(1, 1)
        self.outer_splitter.setSizes([260, 1020])

        self.setCentralWidget(central)

    def _wire_signals(self):
        # Terminal ─→ main window
        self.terminal.command_entered.connect(self._on_command)
        self.terminal.close_requested.connect(self.toggle_terminal)

        # Canvas → annotate
        self._router = CanvasEventRouter(self.engine, self.annot, self.pdf_viewer)

        # TOC → navigate
        self.toc.navigate_requested.connect(self._on_toc_navigate)

        # Page render → status bar
        self.pdf_viewer.page_rendered.connect(self._on_page_rendered)

        # Ctrl+L focuses the terminal input
        QShortcut(QKeySequence("Ctrl+L"), self,
                  activated=self.terminal.input.setFocus)

    # ====================================================================
    # Command registration
    # ====================================================================
    def _register_commands(self):
        # ---- General (overrides built-ins if we want different behavior)
        self.parser.register("help", self._cmd_help)

        # ---- Viewer
        self.parser.register("open", self._cmd_open)
        self.parser.register("close", self._cmd_close)
        self.parser.register("next", self._cmd_next)
        self.parser.register("prev", self._cmd_prev)
        self.parser.register("goto", self._cmd_goto)
        self.parser.register("zoom", self._cmd_zoom)
        self.parser.register("fit", self._cmd_fit)
        self.parser.register("toc", self._cmd_toc)

        # ---- Annotator
        self.parser.register("mode", self._cmd_mode)
        self.parser.register("highlight", self._cmd_highlight_text)
        self.parser.register("save", self._cmd_save)

        # ---- Editor
        self.parser.register("addtext", self._cmd_addtext)
        self.parser.register("extract", self._cmd_extract)
        self.parser.register("merge", self._cmd_merge)
        self.parser.register("delete", self._cmd_delete)
        self.parser.register("rotate", self._cmd_rotate)

        # ---- QR
        self.parser.register("qr", self._cmd_qr)

    # ====================================================================
    # Command handlers — return CommandResult
    # ====================================================================
    def _cmd_help(self, _args):
        return CommandResult.print(self.parser.help_text())

    # --- viewer ---------------------------------------------------------
    def _cmd_open(self, args):
        positional, _ = self.parser.extract_flags(args)
        if not positional:
            return CommandResult.error("Usage: open <path-to-pdf>")
        path = resolve_user_path(" ".join(positional))
        return self._do_open(path)

    def _cmd_close(self, _args):
        self.engine.close()
        self.pdf_viewer._set_placeholder()
        self.toc.clear_outline()
        self._set_status("Closed current PDF.")
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
            return CommandResult.error("Usage: zoom <factor> | zoom in | zoom out")
        if args[0] == "in":
            ok, msg = self.engine.zoom_in()
        elif args[0] == "out":
            ok, msg = self.engine.zoom_out()
        else:
            try:
                z = float(args[0])
                ok, msg = self.engine.set_zoom(z)
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

    # --- annotator -----------------------------------------------------
    def _cmd_mode(self, args):
        if not args:
            return CommandResult.error("Usage: mode view | draw | highlight | erase")
        mode = args[0].lower()
        if mode == "view":
            self.pdf_viewer.set_mode(CanvasMode.VIEW)
            self.terminal.set_mode_label("view")
            return CommandResult.print("Mode → view.")
        if mode == "draw":
            _, flags = self.parser.extract_flags(args)
            color_str = flags.get("color", "red")
            try:
                color_rgb = parse_color(color_str)
            except ValueError as exc:
                return CommandResult.error(str(exc))
            thickness = float(flags.get("thickness", 2))
            self.pdf_viewer.set_mode(CanvasMode.DRAW)
            self.pdf_viewer.set_active_ink(color_rgb, thickness)
            self.terminal.set_mode_label(f"draw ({color_str}, t={thickness})")
            return CommandResult.print(
                f"Mode → draw (color={color_str}, thickness={thickness}). "
                "Click & drag on the PDF to draw."
            )
        if mode == "highlight":
            self.pdf_viewer.set_mode(CanvasMode.HIGHLIGHT)
            self.terminal.set_mode_label("highlight")
            return CommandResult.print(
                "Mode → highlight. Click & drag on the PDF to highlight."
            )
        if mode == "erase":
            self.pdf_viewer.set_mode(CanvasMode.ERASE)
            self.terminal.set_mode_label("erase")
            return CommandResult.print(
                "Mode → erase. Click any annotation to delete it."
            )
        return CommandResult.error(f"Unknown mode: {mode}")

    def _cmd_highlight_text(self, args):
        # The whole thing after "highlight" is the text to search
        # (re-join because tokenizer stripped quotes)
        if not args:
            return CommandResult.error('Usage: highlight "text to highlight"')
        raw = " ".join(args)
        ok, msg = self.annot.highlight_text(raw)
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    def _cmd_save(self, _args):
        ok, msg = self.engine.save()
        return self._as_result(ok, msg)

    # --- editor --------------------------------------------------------
    def _cmd_addtext(self, args):
        if not args:
            return CommandResult.error(
                'Usage: addtext "your text" --page 1 --x 100 --y 200 '
                '--size 14 --color black [--font Kalpurush.ttf] '
                '[--width 200 --height 80]'
            )
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
        ok, msg = self.editor.add_text(
            text, page, x, y, size, color_rgb,
            font_file=font_file, width=width, height=height,
        )
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
        if len(args) < 3:
            return CommandResult.error("Usage: merge <f1> <f2> <out.pdf>")
        out = resolve_user_path(args[-1])
        inputs = [resolve_user_path(p) for p in args[:-1]]
        ok, msg = PDFManipulator.merge_pdfs(inputs, out)
        return self._as_result(ok, msg)

    def _cmd_delete(self, args):
        if not args:
            return CommandResult.error("Usage: delete <page>")
        try:
            p = int(args[0])
        except ValueError:
            return CommandResult.error("page must be a number.")
        ok, msg = PDFManipulator.delete_page(self.engine.path, p)
        if ok:
            # Reload to reflect change
            self._do_open(self.engine.path)
        return self._as_result(ok, msg)

    def _cmd_rotate(self, args):
        if len(args) < 2:
            return CommandResult.error("Usage: rotate <page> <angle>")
        try:
            p = int(args[0])
            a = int(args[1])
        except ValueError:
            return CommandResult.error("page and angle must be integers.")
        ok, msg = PDFManipulator.rotate_page(self.engine.path, p, a)
        if ok:
            self._do_open(self.engine.path)
        return self._as_result(ok, msg)

    # --- QR ------------------------------------------------------------
    def _cmd_qr(self, args):
        positional, flags = self.parser.extract_flags(args)
        if not positional:
            return CommandResult.error('Usage: qr "text" --page 1 --x 50 --y 50 --size 100')
        text = " ".join(positional)
        try:
            page = int(flags.get("page", self.engine.current_page + 1))
            x = float(flags.get("x", 50))
            y = float(flags.get("y", 50))
            size = float(flags.get("size", 100))
        except (ValueError, TypeError) as exc:
            return CommandResult.error(f"Bad flag value: {exc}")
        ok, msg = self.qr.stamp_on_page(text, page=page, x=x, y=y, size_pt=size)
        if ok:
            self.pdf_viewer.refresh()
        return self._as_result(ok, msg)

    # ====================================================================
    # Helpers
    # ====================================================================
    def _as_result(self, ok: bool, msg: str) -> CommandResult:
        if ok:
            return CommandResult.print(msg)
        return CommandResult.error(msg)

    def _do_open(self, path: str) -> CommandResult:
        if not is_pdf_file(path):
            return CommandResult.error(f"Not a PDF file: {path}")
        ok, msg = self.engine.open(path)
        if not ok:
            return CommandResult.error(msg)
        self.pdf_viewer.refresh()
        self.toc.load_outline(self.engine.get_outline())
        self._set_status(f"Loaded {os.path.basename(path)}  "
                         f"({self.engine.page_count} pages)")
        return CommandResult.print(
            f"Opened '{os.path.basename(path)}' — {self.engine.page_count} pages."
        )

    def _toggle_toc(self):
        self.toc.setVisible(not self.toc.isVisible())

    def toggle_terminal(self):
        self.terminal.setVisible(not self.terminal.isVisible())

    def _on_toc_navigate(self, page_1based: int):
        ok, msg = self.engine.goto(page_1based - 1)
        if ok:
            self.pdf_viewer.refresh()
            self._set_status(f"Jumped to page {page_1based} via TOC.")

    def _on_page_rendered(self, page_1based: int):
        self._set_status(
            f"Page {page_1based}/{self.engine.page_count}  •  "
            f"Zoom {self.engine.zoom:.2f}x"
        )

    def _on_command(self, raw: str):
        result = self.parser.execute(raw)
        self._render_result(result)

    def _render_result(self, result: CommandResult):
        action = result.action
        if action == "print":
            self.terminal.append_output(
                f"<span style='color:#a6e3a1;'>{self._escape(result.data.get('text',''))}</span>"
            )
        elif action == "error":
            self.terminal.append_output(
                f"<span style='color:#f38ba8;'>✗ {self._escape(result.data.get('text',''))}</span>"
            )
        elif action == "clear":
            self.terminal.clear_output()
        elif action == "exit":
            self.close()
        elif action == "open":
            self.terminal.append_output(
                f"<span style='color:#a6e3a1;'>✓ {self._escape(result.data.get('text',''))}</span>"
            )

    @staticmethod
    def _escape(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _set_status(self, text: str):
        self.statusBar().showMessage(text)

    # ---- toolbar shortcuts --------------------------------------------
    def _open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf)"
        )
        if path:
            result = self._do_open(path)
            self._render_result(result)

    def _save_pdf(self):
        result = self._cmd_save([])
        self._render_result(result)

    # ====================================================================
    # Drag & Drop
    # ====================================================================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0].toLocalFile().lower()
            if url.endswith(".pdf"):
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            return
        path = event.mimeData().urls()[0].toLocalFile()
        self.terminal.append_output(
            f"<span style='color:#89b4fa;'>» [drag & drop] open \"{path}\"</span>"
        )
        result = self._do_open(path)
        self._render_result(result)

    # ====================================================================
    # Misc
    # ====================================================================
    def closeEvent(self, event):
        if self.engine.is_open:
            self.engine.close()
        super().closeEvent(event)