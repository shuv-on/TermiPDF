# TermiPDF — Developer Guide

> A technical overview for contributors: architecture, design decisions, extension points, and development workflow.

---

## 🎯 Design Goals

1. **Feature-Based (Vertical Slice) Architecture** — every feature owns its own UI, logic, and command handlers. No cross-feature imports.
2. **Decoupled Routing** — the terminal parser dispatches via *callbacks registered by `main_window`*, never via direct feature-to-feature calls.
3. **Cross-Reader PDF Fidelity** — annotations must be visible in Chrome, Edge, and Adobe. We use standard PDF annotation types (Ink, Highlight, FreeText) rather than overlay images.
4. **Unicode-First** — full support for non-Latin scripts (Bangla, Arabic, CJK) via TTF embedding.

---

## 🧰 Tech Stack

| Layer | Tech | Version |
|---|---|---|
| GUI | PyQt6 | ≥ 6.5 |
| PDF engine | PyMuPDF (fitz) | ≥ 1.23 |
| QR / Image | qrcode + Pillow | ≥ 7.4 / ≥ 10.0 |
| Language | Python | ≥ 3.10 |

---

## 📂 Project Layout

```
TermiPDF/
├── requirements.txt
├── README.md                    # End-user guide
├── DEVELOPER.md                 # ← you are here
├── tests/
│   ├── smoke_test.py            # Headless feature tests
│   └── gui_integration_test.py  # Off-screen GUI tests
└── src/
    ├── main.py                  # Entry point — QApplication + QSS load
    ├── main_window.py           # Orchestrator — wires features together
    ├── shared/
    │   ├── styles/modern_theme.qss
    │   ├── utils/path_solver.py # project_root(), asset_path(), resolve_user_path()
    │   └── utils/color_utils.py # parse_color(), html_color()
    └── features/
        ├── terminal/            # CLI engine
        │   ├── terminal_ui.py   # QWidget: output + input + history
        │   └── command_parser.py# Tokenizer + dispatch + built-in commands
        ├── pdf_viewer/          # Canvas + outline + navigation
        │   ├── viewer_engine.py # PyMuPDF wrapper (open/render/outline/save)
        │   ├── viewer_ui.py     # QLabel-based PDF surface w/ mouse events
        │   └── toc_ui.py        # MS Edge-style QTreeWidget outline
        ├── pdf_annotator/       # MS Edge-like annotation tools
        │   ├── annotation_engine.py # PyMuPDF ops: ink, highlight, erase
        │   └── canvas_events.py # Bridges QMouseEvent ↔ AnnotationEngine
        ├── pdf_editor/          # Page-level editing
        │   ├── text_editor.py   # Unicode text insertion (Bangla-aware)
        │   └── manipulation.py  # extract / merge / delete / rotate
        └── qr_generator/
            ├── qr_logic.py      # Generate image + stamp on PDF
            └── qr_ui.py         # Optional preview widget (not in main layout)
```

---

## 🏛️ Architectural Rules (Strict)

### 1. Feature Isolation
A feature folder **must not** import from another feature folder. The chain is always:

```
terminal.command_parser ← main_window ← features.*
```

`main_window.py` is the only file allowed to import across features. If you find yourself adding `from features.X import Y` inside `features/Z/`, **stop** — register the operation through `main_window` instead.

### 2. Command Registration (Not Hardcoded Dispatch)
The parser doesn't know any feature-specific command. Instead, `main_window._register_commands()` calls:
```python
self.parser.register("open", self._cmd_open)
self.parser.register("mode", self._cmd_mode)
# … etc
```
Each handler returns a `CommandResult` dataclass (`print | clear | exit | error`).

### 3. Qt Point ↔ PDF Point Conversion
The canvas always speaks in **PDF user units** at the layer above (`AnnotationEngine`, `TextEditor`). The widget layer (`PDFViewerUI`) handles scaling. Use the helpers:
```python
pt_pdf  = self.pdf_viewer.widget_to_pdf(widget_pos)
pt_wid  = self.pdf_viewer.pdf_to_widget(pdf_pt)
```

### 4. Save Strategy
**In-place saves use `os.replace(tmp, original)`** rather than PyMuPDF's `incremental=True`. This works around two PyMuPDF restrictions:
- `incremental=True` cannot be combined with `garbage=4`
- Structural edits (`delete_page`, `set_rotation`) reject incremental writes

Annotation-only saves *can* in theory use incremental, but for consistency we use tmp-and-replace everywhere.

---

## 🔌 Extension Points

### Adding a New Command
1. Implement a handler in `main_window.py`:
   ```python
   def _cmd_my_thing(self, args):
       positional, flags = self.parser.extract_flags(args)
       if not positional:
           return CommandResult.error("Usage: my-thing <arg>")
       # … do the work
       return CommandResult.print("Done!")
   ```
2. Register it:
   ```python
   def _register_commands(self):
       # … existing registrations …
       self.parser.register("my-thing", self._cmd_my_thing)
   ```
3. Add it to the `help_text()` output inside `command_parser.py`.

### Adding a New Feature Folder
1. Create `src/features/my_feature/__init__.py` and your modules.
2. From `main_window.py`:
   ```python
   from features.my_feature import my_widget
   self.my_widget = my_widget.MyUI()
   # add to layout
   ```
3. Register any commands via `self.parser.register(...)` if needed.

### Supporting a New Script (e.g. Arabic, CJK)
1. Add the TTF to `src/shared/assets/`.
2. Add the filename to `_BUNDLED_FONTS` in `text_editor.py` (so auto-detection finds it).
3. No other code changes required — `insert_font()` + HarfBuzz shaping handle the rest.

### Adding a PDF Source Op (e.g. Crop, Watermark)
1. Add a static method to `features/pdf_editor/manipulation.py`:
   ```python
   @staticmethod
   def crop_page(src, page, x0, y0, x1, y1, out=None) -> tuple[bool, str]:
       # follow the same in-place pattern as delete_page
   ```
2. Wire a `_cmd_crop` in `main_window.py` and `register("crop", ...)`.

---

## 🧪 Testing

### Quick Sanity (no display required)
```bash
source .venv/bin/activate
python tests/smoke_test.py
```
Output: `ALL 37 CHECKS PASSED ✓` — exercises every feature against a generated 2-page PDF.

### GUI Integration Test (offscreen)
```bash
QT_QPA_PLATFORM=offscreen python tests/gui_integration_test.py
```
Output: `ALL 19 GUI CHECKS PASSED ✓` — boots the actual `TermiPDFWindow` and runs a sequence of commands through the parser.

### Adding New Tests
- Headless: append a `section(...)` block in `smoke_test.py`. Keep tests independent — use `tempfile.mkdtemp()` for any files written.
- GUI: append a check after `window._do_open(...)` in `gui_integration_test.py`. Drive the window through `_cmd_*` methods, not by simulating Qt events.

---

## 🐛 Known PyMuPDF Quirks (and how we work around them)

| Issue | Workaround |
|---|---|
| `fitz.Point` has attribute `.x` / `.y` (not methods) | Helper `_xx(p)` / `_yy(p)` in `annotation_engine.py` works for both attribute-style and QPointF method-style access. |
| `add_ink_annot` needs `[[(x1,y1), (x2,y2), …]]` (seq-of-seq-of-float-pairs) | We pass `[stroke_path]` not `stroke_path`. |
| `add_ink_annot(...)` then `.set_border_width(...)` is gone in 1.27 | Use `.set_border(width)` with fallback. |
| `incremental=True` rejects garbage / structural edits | In-place saves write to `*.tmp.pdf` then `os.replace()`. |
| `delete_page` on the only page produces a 0-page doc | `manipulation.delete_page` rejects when `n <= 1`. |
| `highlight_text` returns 0 matches silently | That's not an error — display "Highlighted 0 occurrence(s)". |

---

## 🔧 Development Workflow

### Code Style
- **PEP 8** with 4-space indent.
- Type hints on every public function.
- Private helpers prefixed with `_`.
- Docstrings on every module + every public class.

### Branching
- `main` — stable, tested
- `feature/<name>` — short-lived branches
- Commit messages: imperative mood (`Add QR generator`, not `Added`)

### Pre-commit Checklist
1. `python tests/smoke_test.py` → all green
2. `python tests/gui_integration_test.py` → all green
3. `python -m py_compile src/**/*.py` → no syntax errors
4. Manual test in GUI: open a PDF, draw, save, reopen in Chrome to confirm cross-reader rendering.

---

## 🗺️ Class / Module Cheat-Sheet

| Symbol | Where | Purpose |
|---|---|---|
| `QApplication`, `main()` | `src/main.py` | Bootstrapping + QSS load |
| `TermiPDFWindow` | `src/main_window.py` | Top-level QMainWindow; owns engine + parser |
| `CommandParser` | `features/terminal/command_parser.py` | Tokenize + dispatch to registered handlers |
| `CommandResult` | `features/terminal/command_parser.py` | Standard return dataclass |
| `TerminalUI` | `features/terminal/terminal_ui.py` | QWidget with output, input, history |
| `ViewerEngine` | `features/pdf_viewer/viewer_engine.py` | fitz.Document wrapper; opens, renders, saves |
| `PDFViewerUI` | `features/pdf_viewer/viewer_ui.py` | QScrollArea + QLabel canvas with mouse events |
| `CanvasMode` | `features/pdf_viewer/viewer_ui.py` | Enum: VIEW, DRAW, HIGHLIGHT, ERASE, TEXT |
| `_CanvasSurface` | `features/pdf_viewer/viewer_ui.py` | Internal QLabel subclass for live-stroke overlay |
| `TOCUI` | `features/pdf_viewer/toc_ui.py` | QTreeWidget outline with filter & navigate |
| `AnnotationEngine` | `features/pdf_annotator/annotation_engine.py` | ink, highlight, highlight_text, erase_at |
| `CanvasEventRouter` | `features/pdf_annotator/canvas_events.py` | Hooks PDFViewerUI callbacks to AnnotationEngine |
| `TextEditor` | `features/pdf_editor/text_editor.py` | Unicode text insert (TTF auto-detect) |
| `PDFManipulator` | `features/pdf_editor/manipulation.py` | extract / merge / delete / rotate |
| `QRLogic` | `features/qr_generator/qr_logic.py` | generate_image + stamp_on_page |
| `parse_color` | `shared/utils/color_utils.py` | Hex / named → (r,g,b) tuple |
| `resolve_user_path` | `shared/utils/path_solver.py` | `~`, env vars, normalize |

---

## 🚧 Future Roadmap

- [ ] Plugins: drop a `.py` file into `src/features/` and `main_window` auto-discovers it.
- [ ] Annotations sidebar (list of all annotations on a page).
- [ ] Search across the PDF with regex.
- [ ] Form filling (AcroForm).
- [ ] Optional async rendering for huge PDFs.
- [ ] i18n: translate the terminal banner / help text.

---

## 📜 License

MIT — see [LICENSE](LICENSE).
