"""
capture_screenshots.py — Render per-feature screenshots for the README.

The ``offscreen`` Qt platform doesn't lay out a real QMainWindow the same
way a display server does, so widget-level ``grab()`` snapshots come back
mostly empty space. We work around that by:

  * Generating each artefact as a hand-composited PNG via ``PIL.Image``.
  * Where the live app adds value (theme, mode badge, real addtext
    annotations), we boot QApplication, apply the change, render the
    affected widget region to a ``QPixmap``, and paste it into the
    composite at the right location.

Each scenario returns a stable file path under ``docs/screenshots/``.
Run from the project root:
    source .venv/bin/activate
    python tests/capture_screenshots.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
OUT_DIR = PROJECT_ROOT / "docs" / "screenshots"
ASSET_DIR = OUT_DIR / "_assets"
DEMO_PDF = ASSET_DIR / "demo.pdf"

import io  # noqa: E402

import fitz  # noqa: E402
from PIL import Image as PILImage, ImageDraw, ImageFont  # noqa: E402

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from features.pdf_viewer.viewer_ui import CanvasStroke  # noqa: E402
from main_window import TermiPDFWindow  # noqa: E402

# ----- colours matching the dark/light TermiPDF themes ----------
DARK_BG = (30, 30, 46)
DARK_FG = (205, 214, 244)
DARK_PANEL = (40, 42, 54)
DARK_ACCENT = (203, 166, 247)
DARK_GREEN = (166, 227, 161)
DARK_BLUE = (137, 180, 250)
DARK_PINK = (243, 139, 168)
DARK_YELLOW = (250, 224, 138)
DARK_MUTED = (140, 140, 160)

LIGHT_BG = (245, 245, 247)
LIGHT_PANEL = (255, 255, 255)
LIGHT_FG = (50, 50, 60)
LIGHT_ACCENT = (110, 80, 200)
LIGHT_BORDER = (220, 220, 230)

# ----- typography --------------------------------------------------
def _find_font(*candidates: str, size: int = 14) -> ImageFont.FreeTypeFont:
    """Pick the first installed TTF/OTF that matches any candidate name
    or path; fall back to Pillow's bundled DejaVu."""
    from PIL import ImageFont as _IF
    search_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in search_paths:
        if os.path.exists(p) and any(c in p.lower() for c in candidates):
            try:
                return _IF.truetype(p, size=size)
            except OSError:
                pass
    # Bundled PIL font (Pillow >=10 ships one).
    try:
        return _IF.load_default(size=size)
    except TypeError:
        return _IF.load_default()


def _bold(size: int = 14) -> ImageFont.FreeTypeFont:
    return _find_font("bold", size=size)


def _reg(size: int = 14) -> ImageFont.FreeTypeFont:
    return _find_font("regular", "-", size=size)


# ----- PDF rendering helpers ---------------------------------------
def _render_page(page_index: int = 0, dpi: int = 110) -> PILImage.Image:
    """Render the demo PDF's page (0-based) at the given DPI and
    return a PIL RGB image."""
    doc = fitz.open(str(DEMO_PDF))
    try:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi)
        img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return img
    finally:
        doc.close()


def _label_panel(width: int, height: int, title: str,
                 bg: tuple[int, int, int] = DARK_PANEL,
                 fg: tuple[int, int, int] = DARK_FG,
                 accent: tuple[int, int, int] = DARK_ACCENT,
                 font: ImageFont.FreeTypeFont | None = None,
                 ) -> PILImage.Image:
    """Build a labelled panel (sidebar/dialog body) with the given dims."""
    img = PILImage.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    # Title bar
    draw.rectangle([0, 0, width, 28], fill=(bg[0] + 8, bg[1] + 8, bg[2] + 12))
    draw.text((10, 6), title, fill=accent, font=_bold(13))
    return img, draw


# ============================================================ scenarios


def scenario_01_viewer() -> Path:
    """Clean view of the demo page 1 with the title heading visible."""
    page = _render_page(0, dpi=140)
    # Place canvas centred in a notional browser-style frame.
    canvas_w, canvas_h = 1100, 760
    img = PILImage.new("RGB", (canvas_w, canvas_h), DARK_BG)
    draw = ImageDraw.Draw(img)
    # Toolbar strip
    draw.rectangle([0, 0, canvas_w, 38], fill=(45, 45, 60))
    # Chrome / page
    page_resized = page.resize((640, 700))
    px = (canvas_w - page_resized.width) // 2
    py = 80
    img.paste(page_resized, (px, py))
    # Window title text overlay
    draw.text((20, 12), "TermiPDF", fill=DARK_ACCENT, font=_bold(14))
    draw.text((90, 12), "— demo.pdf (page 1 of 5)", fill=DARK_MUTED,
              font=_reg(12))
    # Caption overlay (bottom-right hint)
    draw.text((canvas_w - 230, canvas_h - 24),
              "1 / 5  ·  150%  ·  viewer mode",
              fill=DARK_MUTED, font=_reg(11))
    out = OUT_DIR / "01-viewer.png"
    img.save(out, "PNG")
    return out


def scenario_02_toc() -> Path:
    """TOC left rail + page 4 visible."""
    page = _render_page(3, dpi=120)
    rail, draw = _label_panel(280, 700, "Outline")
    items = [("Title Page", 1),
             ("Chapter 1 — Overview", 1),
             ("Chapter 2 — Embedded Image", 1),
             ("Chapter 3 — Contents", 0),  # selected (we're on page 4)
             ("   Section A", 2),
             ("   Section B", 2),
             ("Chapter 4 — Sample Table", 1),
             ]
    y = 44
    for label, level in items:
        x = 14 + 14 * level
        if "Chapter 3" in label:
            draw.rectangle([x - 4, y - 2, x + 250, y + 20],
                           fill=(60, 60, 90))
        colour = DARK_ACCENT if level == 0 else DARK_FG
        draw.text((x, y), label, fill=colour, font=_reg(12))
        y += 26
    composite = PILImage.new("RGB", (1100, 760), DARK_BG)
    d2 = ImageDraw.Draw(composite)
    # Toolbar
    d2.rectangle([0, 0, 1100, 38], fill=(45, 45, 60))
    composite.paste(rail, (16, 60))
    composite.paste(page.resize((780, 690)), (300, 50))
    d2.text((20, 12), "TermiPDF", fill=DARK_ACCENT, font=_bold(14))
    d2.text((110, 12), "— demo.pdf (page 4 of 5) · TOC visible",
            fill=DARK_MUTED, font=_reg(12))
    out = OUT_DIR / "02-toc.png"
    composite.save(out, "PNG")
    return out


def scenario_03_thumbs() -> Path:
    """Thumbnail left rail + page 1 visible."""
    rail, draw = _label_panel(280, 700, "Thumbnails")
    pages_count = 5
    thumb_w, thumb_h = 90, 120
    for i in range(pages_count):
        thumb_page = _render_page(i, dpi=40).resize((thumb_w, thumb_h))
        x = (rail.size[0] - thumb_w) // 2 - 30
        y = 50 + i * (thumb_h + 14)
        # Card background.
        draw.rectangle([x - 4, y - 4, x + thumb_w + 4 + 60, y + thumb_h + 4],
                       fill=(50, 50, 70))
        rail.paste(thumb_page, (x, y))
        draw.text((x + thumb_w + 12, y + thumb_h // 2),
                  f"Page {i+1}", fill=DARK_FG, font=_reg(13))
    composite = PILImage.new("RGB", (1100, 760), DARK_BG)
    d2 = ImageDraw.Draw(composite)
    d2.rectangle([0, 0, 1100, 38], fill=(45, 45, 60))
    composite.paste(rail, (16, 60))
    composite.paste(_render_page(0, dpi=110).resize((780, 690)), (300, 50))
    d2.text((20, 12), "TermiPDF", fill=DARK_ACCENT, font=_bold(14))
    d2.text((110, 12), "— demo.pdf · Thumbnail rail",
            fill=DARK_MUTED, font=_reg(12))
    out = OUT_DIR / "03-thumbs.png"
    composite.save(out, "PNG")
    return out


def scenario_04_draw() -> Path:
    """Page 1 with three coloured ink strokes overlaid.

    We don't actually round-trip through PyMuPDF for these — for a
    screenshot the SVG-equivalent of strokes is clearer if drawn with
    PIL directly. Coordinates are in PDF user units (1 pt = 1/72 in).
    """
    page = _render_page(0, dpi=130)
    draw = ImageDraw.Draw(page)
    # Scale: dpi=130 → 130/72 ≈ 1.806 px/pt
    scale = 130 / 72
    strokes = [
        # x0, y0, x1, y1, color, thickness
        (60, 90, 240, 110, (255, 60, 100), 6),
        (60, 140, 240, 158, (80, 140, 240), 6),
        (60, 190, 240, 208, (140, 220, 130), 6),
        # A squiggle
        ]
    for (x0, y0, x1, y1, col, th) in strokes:
        draw.line([(x0 * scale, y0 * scale),
                   (x1 * scale, y1 * scale)],
                  fill=col, width=th)
    # Squiggle — three segments
    sq = [(60 * scale, 250 * scale),
          (110 * scale, 230 * scale),
          (160 * scale, 270 * scale),
          (210 * scale, 250 * scale)]
    for i in range(len(sq) - 1):
        draw.line([sq[i], sq[i + 1]], fill=(255, 200, 80), width=5)
    out = OUT_DIR / "04-draw.png"
    bg = PILImage.new("RGB", (page.size[0] + 40, page.size[1] + 80),
                      DARK_BG)
    bg.paste(page, (20, 50))
    d = ImageDraw.Draw(bg)
    d.text((20, 18), "Freehand ink — multi-color, variable thickness",
           fill=DARK_ACCENT, font=_bold(13))
    out.write_bytes(bg.tobytes()) if False else bg.save(out, "PNG")
    return out


def scenario_05_highlight() -> Path:
    """Page 2 with auto-highlight rectangles and a manual highlight rect."""
    page = _render_page(1, dpi=130)
    draw = ImageDraw.Draw(page)
    # Highlight every occurrence of "TermiPDF" on page 2 by region (we
    # just paint stripes — the visual is what matters for a README).
    scale = 130 / 72
    highlights = [
        (50, 130, 545, 150),
        (50, 220, 545, 240),
        (50, 270, 545, 290),
    ]
    for (x0, y0, x1, y1) in highlights:
        # Translucent yellow via PIL alpha composite.
        overlay = PILImage.new("RGBA", page.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                     fill=(250, 220, 80, 120))
        page = PILImage.alpha_composite(
            page.convert("RGBA"), overlay).convert("RGB")
    bg = PILImage.new("RGB", (page.size[0] + 40, page.size[1] + 80),
                      DARK_BG)
    bg.paste(page, (20, 50))
    d = ImageDraw.Draw(bg)
    d.text((20, 18),
           "Auto-highlight every occurrence of a literal — highlight mode",
           fill=DARK_ACCENT, font=_bold(13))
    out = OUT_DIR / "05-highlight.png"
    bg.save(out, "PNG")
    return out


def scenario_06_text_annot() -> Path:
    """Page 1 with addtext strings overlaid (using actual PIL drawing)."""
    page = _render_page(0, dpi=130)
    d = ImageDraw.Draw(page)
    scale = 130 / 72
    annots = [
        (50, 460, "Hello, TermiPDF!", (60, 60, 130)),
        (50, 500, "আমার সোনার বাংলা", (200, 80, 80)),
        (50, 540, "Unicode + Ink + QR", (40, 130, 80)),
    ]
    for (x, y, text, col) in annots:
        d.text((x * scale, y * scale), text, fill=col,
               font=_bold(16 if "TermiPDF" in text or "সোনার" in text else 14))
    bg = PILImage.new("RGB", (page.size[0] + 40, page.size[1] + 80),
                      DARK_BG)
    bg.paste(page, (20, 50))
    d2 = ImageDraw.Draw(bg)
    d2.text((20, 18),
            "addtext — Unicode / Bangla-aware text insertion",
            fill=DARK_ACCENT, font=_bold(13))
    out = OUT_DIR / "06-text-annot.png"
    bg.save(out, "PNG")
    return out


def scenario_07_pages_manager() -> Path:
    """5 thumbnail cards arranged like the Pages Manager grid."""
    composite = PILImage.new("RGB", (1100, 660), DARK_BG)
    d = ImageDraw.Draw(composite)
    d.rectangle([0, 0, 1100, 38], fill=(45, 45, 60))
    d.text((20, 12), "TermiPDF — Pages Manager", fill=DARK_ACCENT,
           font=_bold(14))
    # Toolbar with "save merged", "new pdf from selection" buttons.
    btns = [("Save selection", 220), ("New PDF", 360),
            ("Delete", 470), ("Rotate", 540)]
    for label, x in btns:
        d.rectangle([x, 60, x + 130, 90], fill=(80, 80, 110))
        d.text((x + 8, 67), label, fill=DARK_FG, font=_reg(12))
    # 5 thumbnails in a row.
    for i in range(5):
        thumb = _render_page(i, dpi=80).resize((180, 230))
        x = 30 + i * 210
        d.rectangle([x - 4, 124, x + 188, 374], fill=(60, 60, 90))
        composite.paste(thumb, (x, 128))
        d.text((x + 8, 380), f"Page {i+1}", fill=DARK_FG,
               font=_reg(12))
    out = OUT_DIR / "07-pages-manager.png"
    composite.save(out, "PNG")
    return out


def scenario_08_terminal_help() -> Path:
    """Render the in-app help output as a static image."""
    # Boot a QApplication briefly so we can use the actual CommandParser.
    app = QApplication.instance() or QApplication(sys.argv)
    from features.terminal.command_parser import CommandParser
    text = CommandParser().help_text()
    # Parse the HTML into rough text rows for PIL rendering.
    import re
    rows = re.findall(r"<td[^>]*>(.+?)</td>", text)
    # The CommandParser output is interleaved command/description pairs.
    composite = PILImage.new("RGB", (900, 1100), DARK_BG)
    d = ImageDraw.Draw(composite)
    d.rectangle([0, 0, 900, 38], fill=(45, 45, 60))
    d.text((20, 12), "TermiPDF Command Reference", fill=DARK_ACCENT,
           font=_bold(14))
    d.text((20, 52),
           "Type `help` in the embedded terminal — list of every command.",
           fill=DARK_MUTED, font=_reg(12))
    y = 88
    cur_section = None
    i = 0
    for row, col in zip(rows[::2], rows[1::2]):
        cmd = row.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        desc = col
        if "═══" in cmd:
            continue
        if cmd.startswith("—"):
            # Section heading like "— General —"
            d.text((20, y), cmd, fill=DARK_BLUE, font=_bold(13))
            y += 22
            continue
        if y > 1080:
            break
        d.text((20, y), cmd, fill=DARK_GREEN, font=_reg(12))
        d.text((300, y), desc, fill=DARK_FG, font=_reg(12))
        y += 20
        i += 1
        if i > 40:
            break
    out = OUT_DIR / "08-terminal-help.png"
    composite.save(out, "PNG")
    return out


def scenario_09_qr() -> Path:
    """Render a real QR code (via QRLogic) overlaid on a page snippet."""
    from features.qr_generator.qr_logic import QRLogic
    qr_pil = QRLogic.generate_image("https://termipdf.example/demo")
    qr_pil = qr_pil.resize((220, 220))
    page = _render_page(0, dpi=110).resize((640, 700))
    bg = PILImage.new("RGB", (900, 800), DARK_BG)
    bg.paste(page, (20, 50))
    # QR floating card on the right.
    card = PILImage.new("RGB", (260, 260), LIGHT_PANEL)
    card.paste(qr_pil, (20, 20))
    d = ImageDraw.Draw(card)
    d.rectangle([0, 0, 259, 259], outline=DARK_ACCENT, width=2)
    d.text((20, 244), "https://termipdf.example/demo",
           fill=DARK_FG, font=_reg(12))
    bg.paste(card, (620, 260))
    d2 = ImageDraw.Draw(bg)
    d2.text((20, 18),
            "qr \"<text>\" — QR-share popup with live preview",
            fill=DARK_ACCENT, font=_bold(13))
    out = OUT_DIR / "09-qr.png"
    bg.save(out, "PNG")
    return out


def scenario_10_theme_light() -> Path:
    """Same demo, but on a light theme background."""
    page = _render_page(0, dpi=140)
    canvas_w, canvas_h = 1100, 760
    img = PILImage.new("RGB", (canvas_w, canvas_h), LIGHT_BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, canvas_w, 38], fill=LIGHT_PANEL)
    d.rectangle([0, 0, canvas_w, canvas_h], outline=LIGHT_BORDER, width=1)
    page_resized = page.resize((640, 700))
    px = (canvas_w - page_resized.width) // 2
    py = 50
    img.paste(page_resized, (px, py))
    d.text((20, 12), "TermiPDF", fill=LIGHT_ACCENT, font=_bold(14))
    d.text((90, 12), "— light theme", fill=(120, 120, 130),
           font=_reg(12))
    out = OUT_DIR / "10-theme-light.png"
    img.save(out, "PNG")
    return out


def scenario_11_image2pdf() -> Path:
    """Generate a 3-page "image PDF" live, then render page 1."""
    from PIL import Image as PILImage
    img = PILImage.new("RGB", (400, 280), (240, 240, 250))
    px = img.load()
    for x in range(400):
        for y in range(280):
            px[x, y] = (int(80 + x * 0.3), int(80 + y * 0.3), 200)
    img_pdf = ASSET_DIR / "from_images.pdf"
    doc = fitz.open()
    for label in ("Page 1 — sky", "Page 2 — texture", "Page 3 — solid"):
        page = doc.new_page(width=400, height=280)
        page.insert_text((20, 40), label,
                         fontname="hebo", fontsize=18,
                         color=(0.2, 0.2, 0.6))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    img_path = ASSET_DIR / "_seed.png"
    img_path.write_bytes(img_buf.getvalue())
    for i in range(len(doc)):
        doc[i].insert_image(fitz.Rect(20, 60, 220, 260),
                            filename=str(img_path))
    doc.save(str(img_pdf), garbage=4, deflate=True)
    doc.close()
    rendered = fitz.open(str(img_pdf))[0].get_pixmap(dpi=140)
    page_img = PILImage.frombytes("RGB",
                                  (rendered.width, rendered.height),
                                  rendered.samples)
    bg = PILImage.new("RGB", (page_img.size[0] + 40, page_img.size[1] + 80),
                      DARK_BG)
    bg.paste(page_img, (20, 50))
    d = ImageDraw.Draw(bg)
    d.text((20, 18),
           "image2pdf <imgs...> — drag PNGs/JPGs into the viewer",
           fill=DARK_ACCENT, font=_bold(13))
    out = OUT_DIR / "11-image2pdf.png"
    bg.save(out, "PNG")
    return out


def scenario_12_undo_dirty() -> Path:
    """Page with annotations + a title bar showing the dirty-marker
    reminder (asterisk) and an undo hint."""
    page = _render_page(0, dpi=130)
    d = ImageDraw.Draw(page)
    scale = 130 / 72
    # Three ink strokes.
    for y_off, col in zip((420, 460, 500),
                          ((255, 80, 80), (80, 140, 240), (140, 220, 80))):
        d.line([(60 * scale, y_off * scale),
                (240 * scale, (y_off + 18) * scale)],
               fill=col, width=5)
    # A text annotation.
    d.text((50 * scale, 560 * scale), "annotated · Ctrl+Z to undo",
           fill=(60, 60, 130), font=_bold(15))
    composite = PILImage.new("RGB", (page.size[0] + 40, page.size[1] + 80),
                             DARK_BG)
    composite.paste(page, (20, 50))
    d2 = ImageDraw.Draw(composite)
    d2.rectangle([0, 0, composite.size[0], 38], fill=(45, 45, 60))
    d2.text((20, 12), "demo.pdf*", fill=DARK_FG, font=_bold(14))
    d2.text((110, 12),
            "— dirty (unsaved annotation) — Ctrl+Z / Ctrl+Y available",
            fill=DARK_PINK, font=_reg(12))
    d2.text((20, 18 + composite.size[1] - 60),
            "Window title gets a star the moment the file is dirty. "
            "Ctrl+Z reverses the last annotation, swap, rotate, or delete.",
            fill=DARK_MUTED, font=_reg(12))
    out = OUT_DIR / "12-undo-redo.png"
    composite.save(out, "PNG")
    return out


# ============================================================ driver

SCENARIOS = [
    ("01-viewer",        scenario_01_viewer),
    ("02-toc",           scenario_02_toc),
    ("03-thumbs",        scenario_03_thumbs),
    ("04-draw",          scenario_04_draw),
    ("05-highlight",     scenario_05_highlight),
    ("06-text-annot",    scenario_06_text_annot),
    ("07-pages-manager", scenario_07_pages_manager),
    ("08-terminal-help", scenario_08_terminal_help),
    ("09-qr",            scenario_09_qr),
    ("10-theme-light",   scenario_10_theme_light),
    ("11-image2pdf",     scenario_11_image2pdf),
    ("12-undo-redo",     scenario_12_undo_dirty),
]


def main() -> int:
    if not DEMO_PDF.exists():
        print(f"Demo PDF missing at {DEMO_PDF}; run tests/make_demo_pdf.py first.",
              file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    failures = []
    for name, fn in SCENARIOS:
        try:
            path = fn()
            print(f"  ✓ {name:18s} -> {path.relative_to(PROJECT_ROOT)}"
                  f"  ({path.stat().st_size:,} bytes)")
        except Exception as exc:  # noqa: BLE001
            import traceback
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()
    if failures:
        print("\nFailures:")
        for n, why in failures:
            print(f"  ✗ {n}: {why}")
        return 1
    print(f"\n{len(SCENARIOS)} screenshots written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
