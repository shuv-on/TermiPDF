# TermiPDF

> A modern PDF editor that combines a sleek GUI with an embedded hacker-style terminal. Edit, annotate, highlight, and stamp QR codes — all from a single command line.

![TermiPDF](https://img.shields.io/badge/Python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey)

---

## ✨ Features

- 📄 **Open & View** any PDF with smooth zoom, pan, and navigation
- 📑 **Table of Contents** sidebar (MS Edge–style outline tree)
- 🖊️ **Freehand Drawing** with customizable color and thickness
- 🟨 **Highlighting** — drag a box or auto-highlight any word across the document
- ✏️ **Unicode Text Insertion** with full Bangla support
- 🔳 **QR Code Generator** stamped directly onto your PDF
- 💻 **Embedded Terminal** — drive everything with a few keystrokes
- 💾 **Saves annotations** into the PDF itself (works in Chrome, Edge, Adobe)

---

## 🚀 Quick Start

### 1. Requirements
- Python **3.10 or newer**
- A desktop environment (Linux, Windows, or macOS with a display)

### 2. Install
```bash
# Clone or download this project, then:
cd TermiPDF

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .\.venv\Scripts\activate         # Windows (PowerShell)

# Install dependencies
pip install -r requirements.txt
```

### 3. Run
```bash
python src/main.py
```

A window will open with three panels:

```
┌──────────┬─────────────────────┐
│  Outline │   PDF Canvas        │
│  (TOC)   │                     │
│          │                     │
├──────────┴─────────────────────┤
│  💻 Terminal                    │
└─────────────────────────────────┘
```

### 4. Open a PDF
**Option A:** Drag any `.pdf` file from your file manager onto the window.

**Option B:** Type this in the terminal:
```
open /path/to/your/file.pdf
```

---

## ⌨️ The Terminal — Your Command Center

Click the input box at the bottom (or press `Ctrl+L`) and start typing. Try `help` to see every command.

### Navigation
| Command | What it does |
|---|---|
| `open <path>` | Open a PDF |
| `close` | Close the current PDF |
| `next` / `prev` | Go to next / previous page |
| `goto 5` | Jump to page 5 |
| `zoom in` / `zoom out` | Zoom in or out |
| `zoom 1.5` | Set zoom to 1.5× |
| `fit` | Fit page to window |
| `toc` | Show or hide the outline sidebar |

### Annotations
| Command | What it does |
|---|---|
| `mode view` | Return to normal scrolling mode |
| `mode draw --color red --thickness 3` | Start drawing in red, 3pt thick |
| `mode highlight` | Drag a rectangle on the PDF to highlight |
| `highlight "search text"` | Highlight every occurrence of "search text" |
| `mode erase` | Click any annotation to delete it |
| `save` | Write annotations into the PDF file |

**Tip:** Drawing is interactive — type `mode draw --color blue --thickness 2`, then click and drag on the PDF like you would in MS Paint.

### Editing
| Command | What it does |
|---|---|
| `addtext "Hello World" --page 1 --x 100 --y 200 --size 14` | Insert text |
| `addtext "বাংলা টেক্সট" --page 1 --x 100 --y 300 --size 16` | Insert Bangla text |
| `extract 1 3 out.pdf` | Extract pages 1–3 to a new file |
| `merge a.pdf b.pdf merged.pdf` | Combine two PDFs |
| `delete 2` | Delete page 2 |
| `rotate 1 90` | Rotate page 1 by 90° |

### QR Codes
```
qr "https://example.com" --page 1 --x 50 --y 50 --size 100
```
Stamps a QR code (100pt × 100pt) at position (50, 50) on page 1.

### General
| Command | What it does |
|---|---|
| `help` | Show all commands |
| `clear` | Clear the terminal output |
| `exit` | Quit TermiPDF |

### Keyboard Shortcuts
| Shortcut | Action |
|---|---|
| `Ctrl+O` | Open a PDF file |
| `Ctrl+S` | Save the current PDF |
| `Ctrl+B` | Toggle the outline sidebar |
| `Ctrl+\`` | Toggle the terminal |
| `Ctrl+L` | Focus the terminal input |
| `↑` / `↓` | Cycle through command history |

---

## 🌏 Bangla / Unicode Text

To render Bangla (বাংলা) text correctly in the PDF:

1. Place a Unicode Bangla TTF font in `src/shared/assets/`. Suggested fonts:
   - `Kalpurush.ttf`
   - `NotoSansBengali.ttf`
2. TermiPDF auto-detects the font and embeds it into the PDF.
3. Insert with:
   ```
   addtext "আমার সোনার বাংলা" --page 1 --x 100 --y 500 --size 18
   ```

Without a TTF, Bangla characters may appear as boxes — the font is essential.

---

## 💾 Saving & Cross-Reader Compatibility

When you type `save` (or press `Ctrl+S`), TermiPDF writes the annotations **directly into the PDF file**. The resulting PDF will display all your drawings, highlights, text, and QR codes when opened in:

- ✅ Google Chrome
- ✅ Microsoft Edge
- ✅ Adobe Acrobat Reader
- ✅ Mozilla Firefox (built-in PDF viewer)
- ✅ macOS Preview

This works because TermiPDF uses **standard PDF annotation types** (Ink, Highlight, FreeText) rather than overlay images.

---

## 🎨 The Theme

The interface uses a **Dracula × Nord** hybrid dark theme with a hacker-terminal accent palette:

- Background: `#1e1e2e`
- Foreground: `#cdd6f4`
- Accent: `#cba6f7` (purple)
- Success: `#a6e3a1` (green)
- Error: `#f38ba8` (pink)

You can tweak the theme by editing `src/shared/styles/modern_theme.qss`.

---

## ❓ Troubleshooting

**Q: The window doesn't open.**
A: Make sure you ran `source .venv/bin/activate` before launching. Then check that PyQt6 installed correctly: `python -c "import PyQt6; print('ok')"`.

**Q: Bangla shows as boxes.**
A: Drop a Bangla-capable TTF font into `src/shared/assets/` (see the Bangla section above).

**Q: I drew something but it disappeared.**
A: You forgot to type `save`. Annotations are kept in memory until you save.

**Q: `save` says "permission denied".**
A: The PDF file is read-only or open in another program. Close it elsewhere, or save to a new path: `save` will use the same file; you can also use `extract` / `merge` to create new files.

**Q: Where are my command history entries?**
A: Use the ↑ and ↓ arrow keys in the terminal input box. The last 200 commands are remembered for the session.

---

## 🧪 Sanity Check

To verify everything works, run the test suite:
```bash
source .venv/bin/activate
python tests/smoke_test.py
```
You should see `ALL 37 CHECKS PASSED ✓` at the bottom.

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

## 🤝 Contributing

Bug reports and pull requests are welcome! Please open an issue first to discuss what you'd like to change.
