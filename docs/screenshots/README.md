# Screenshot gallery

These images are the per-feature screenshots embedded in the root
[`README.md`](../../README.md#-screenshots). Each one is hand-composited
via `tests/capture_screenshots.py`, using `tests/make_demo_pdf.py` as the
shared fixture (5-page demo PDF saved to [`_assets/demo.pdf`](_assets/demo.pdf)).

| File | Feature |
|------|---------|
| `01-viewer.png`        | PDF viewer — demo page 1 with toolbar & status bar |
| `02-toc.png`           | Outline (TOC) sidebar — chapter navigation |
| `03-thumbs.png`        | Thumbnail rail — one thumb per page |
| `04-draw.png`          | Freehand ink — multi-colour strokes on a page |
| `05-highlight.png`     | Highlight mode — auto-highlight every match |
| `06-text-annot.png`    | `addtext` — Unicode & Bangla text insertion |
| `07-pages-manager.png` | Pages Manager — drag-drop reorder, swap, delete, rotate |
| `08-terminal-help.png` | Embedded terminal — full `help` reference |
| `09-qr.png`            | QR-share popup — real scannable QR rendered live |
| `10-theme-light.png`   | Light theme variant |
| `11-image2pdf.png`     | Image → PDF pipeline (drag-drop or `image2pdf`) |
| `12-undo-redo.png`     | Dirty-marker reminder + undo/redo hint |

## How to regenerate

```bash
source .venv/bin/activate
python tests/make_demo_pdf.py        # builds _assets/demo.pdf
python tests/capture_screenshots.py  # writes the 12 PNGs above
```

Both scripts are headless — they run under PyQt's `offscreen` platform
plugin, so they work in CI / Docker / WSL without a display.
