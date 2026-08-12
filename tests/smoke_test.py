"""
smoke_test.py — End-to-end test of every TermiPDF feature.

Run from the project root with:
    source .venv/bin/activate
    python tests/smoke_test.py

Exits with code 0 on success, 1 on any failure.
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
from pathlib import Path

# Make `src` importable
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import fitz  # noqa: E402

from features.pdf_viewer.viewer_engine import ViewerEngine, OutlineNode  # noqa: E402
from features.pdf_viewer.viewer_ui import CanvasStroke, CanvasMode  # noqa: E402
from features.terminal.command_parser import CommandParser  # noqa: E402
from features.pdf_annotator.annotation_engine import AnnotationEngine  # noqa: E402
from features.pdf_editor.text_editor import TextEditor  # noqa: E402
from features.pdf_editor.manipulation import PDFManipulator  # noqa: E402
from features.qr_generator.qr_logic import QRLogic  # noqa: E402


GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
CHECK = "✓"
CROSS = "✗"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        print(f"  {GREEN}{CHECK}{RESET} {name}" + (f"  ({detail})" if detail else ""))
        passed += 1
    else:
        print(f"  {RED}{CROSS}{RESET} {name}" + (f"  -- {detail}" if detail else ""))
        failed += 1


def section(title: str):
    print(f"\n=== {title} ===")


def main() -> int:
    pdf_src = str(PROJECT_ROOT / "src" / "test.pdf")
    assert os.path.isfile(pdf_src), f"Missing test fixture: {pdf_src}"

    tmp = Path(tempfile.mkdtemp(prefix="termipdf_test_"))
    print(f"Using temp dir: {tmp}")

    # --------------------------------------------------------------- T1
    section("T1 — ViewerEngine: open / render / outline")
    eng = ViewerEngine()
    ok, msg = eng.open(pdf_src)
    check("open PDF", ok, msg)
    check("page_count > 0", eng.page_count > 0, f"{eng.page_count} page(s)")
    result = eng.render_current()
    check("render produces PNG bytes", len(result.png_bytes) > 0,
          f"{len(result.png_bytes)} bytes")
    check("page rect width reported", result.page_width_pt > 0,
          f"{result.page_width_pt:.1f} pt")

    # --------------------------------------------------------------- T2
    section("T2 — CommandParser: tokenize / flags / dispatch")
    parser = CommandParser()
    tokens = parser.tokenize('addtext "hello world" --page 1 --x 100 --y 200 --size 14')
    pos, flags = parser.extract_flags(tokens)
    check("tokenize quoted string", pos == ["addtext", "hello world"], str(pos))
    check("extract --flags", flags == {"page": "1", "x": "100", "y": "200", "size": "14"},
          str(flags))

    res = parser.execute("definitely_not_a_command")
    check("unknown command returns error", res.action == "error")
    res = parser.execute("help")
    check("help command returns print", res.action == "print")
    res = parser.execute("clear")
    check("clear returns clear action", res.action == "clear")
    res = parser.execute("exit")
    check("exit returns exit action", res.action == "exit")

    # --------------------------------------------------------------- T3
    section("T3 — AnnotationEngine: ink + highlight + erase")
    annot = AnnotationEngine(eng)
    stroke = CanvasStroke(
        points=[fitz.Point(50, 50), fitz.Point(150, 80), fitz.Point(250, 60)],
        color_rgb=(1.0, 0.0, 0.0),
        thickness=2.0,
    )
    ok, msg = annot.add_ink_stroke(stroke)
    check("add ink stroke", ok, msg)

    ok, msg = annot.highlight_text("test")
    # "test" may or may not be in this PDF; both outcomes are valid
    check("highlight_text runs without error", isinstance(ok, bool), msg)

    ok, msg = annot.highlight_rect(__import__("PyQt6").QtCore.QRectF(20, 20, 80, 20))
    check("highlight_rect runs", isinstance(ok, bool), msg)

    # --------------------------------------------------------------- T4
    section("T4 — TextEditor: English + Bangla (Unicode) text insertion")
    editor = TextEditor(eng)
    ok, msg = editor.add_text("Hello TermiPDF", page=1, x=50, y=50, font_size=18)
    check("insert ASCII text", ok, msg)

    ok, msg = editor.add_text("আমার সোনার বাংলা", page=1, x=50, y=100, font_size=18)
    check("insert Bangla text", ok, msg)
    check("Bangla text uses TTF when available",
          "TTF font" in msg or "default font" in msg, msg)

    # --------------------------------------------------------------- T5
    section("T5 — Manipulator: delete / rotate / merge / extract")
    # Build a 2-page test PDF from the 1-page fixture (so delete-page-1 is valid)
    two_page_pdf = str(tmp / "two_page.pdf")
    src_doc = fitz.open(pdf_src)
    dest_doc = fitz.open()
    dest_doc.insert_pdf(src_doc)
    dest_doc.insert_pdf(src_doc)  # duplicate page → 2 pages total
    dest_doc.save(two_page_pdf)
    dest_doc.close()
    src_doc.close()

    work_pdf = str(tmp / "work.pdf")
    shutil.copy(two_page_pdf, work_pdf)

    ok, msg = PDFManipulator.delete_page(work_pdf, 1)
    check("delete page 1 (from 2-page PDF)", ok, msg)
    eng2 = ViewerEngine()
    eng2.open(work_pdf)
    check("remaining pages after delete = 1", eng2.page_count == 1,
          f"{eng2.page_count} page(s)")

    # rotate
    shutil.copy(two_page_pdf, work_pdf)
    ok, msg = PDFManipulator.rotate_page(work_pdf, 1, 90)
    check("rotate page 1 by 90°", ok, msg)
    eng_rot = ViewerEngine()
    eng_rot.open(work_pdf)
    check("rotation actually applied",
          eng_rot.get_page(0).rotation == 90, f"{eng_rot.get_page(0).rotation}°")

    # merge
    merged = str(tmp / "merged.pdf")
    ok, msg = PDFManipulator.merge_pdfs([two_page_pdf, two_page_pdf], merged)
    check("merge two PDFs", ok, msg)
    eng3 = ViewerEngine()
    eng3.open(merged)
    check("merged has 4 pages", eng3.page_count == 4, f"{eng3.page_count} page(s)")

    # extract
    extracted = str(tmp / "extracted.pdf")
    ok, msg = PDFManipulator.extract_pages(two_page_pdf, 1, 1, extracted)
    check("extract page 1", ok, msg)
    eng_ex = ViewerEngine()
    eng_ex.open(extracted)
    check("extracted has 1 page", eng_ex.page_count == 1, f"{eng_ex.page_count} page(s)")

    # --------------------------------------------------------------- T6
    section("T6 — QRLogic: generate image + stamp on PDF")
    qr = QRLogic(eng)
    img = qr.generate_image("https://termipdf.example")
    check("QR image is non-empty", img.size[0] > 0, f"{img.size}")

    ok, msg = qr.stamp_on_page("hello", page=1, x=50, y=50, size_pt=100)
    check("stamp QR on PDF", ok, msg)

    # --------------------------------------------------------------- T7
    section("T7 — Save annotated PDF and re-open")
    annotated = str(tmp / "annotated.pdf")
    ok, msg = eng.save(annotated)
    check("save with annotations", ok, msg)
    check("saved file exists & non-empty",
          os.path.isfile(annotated) and os.path.getsize(annotated) > 0,
          f"{os.path.getsize(annotated) if os.path.isfile(annotated) else 0} bytes")

    # In-place save (overwrites the original file used by the engine)
    inplace_src = str(tmp / "inplace.pdf")
    shutil.copy(pdf_src, inplace_src)
    eng5 = ViewerEngine()
    ok5, _ = eng5.open(inplace_src)
    assert ok5
    ok5, msg5 = eng5.save()  # no path → save in place
    check("in-place save (overwrite original)", ok5, msg5)
    check("in-place file still exists",
          os.path.isfile(inplace_src) and os.path.getsize(inplace_src) > 0,
          f"{os.path.getsize(inplace_src)} bytes")

    eng4 = ViewerEngine()
    ok, _ = eng4.open(annotated)
    check("re-open annotated PDF", ok)
    page = eng4.get_page(0)
    annots = list(page.annots() or [])
    check("annotations persisted in file", len(annots) > 0,
          f"{len(annots)} annot(s) on page 1")

    # --------------------------------------------------------------- T8
    section("T8 — Color parser")
    from shared.utils.color_utils import parse_color, html_color
    check("hex #FF0000 → red", parse_color("#ff0000") == (1.0, 0.0, 0.0))
    check("named 'red'", parse_color("red") == (1.0, 0.0, 0.0))
    # #f0a expands to #ff00aa → (1.0, 0.0, 170/255)
    expected_short = (0xff / 255.0, 0x00 / 255.0, 0xaa / 255.0)
    check("short hex #f0a → #ff00aa",
          parse_color("#f0a") == expected_short,
          str(parse_color("#f0a")))
    check("html_color hex", html_color("red") == "#ff0000")
    try:
        parse_color("nope")
        check("invalid color raises", False, "did not raise")
    except ValueError:
        check("invalid color raises ValueError", True)

    # --------------------------------------------------------------- Summary
    print()
    print("=" * 50)
    if failed == 0:
        print(f"{GREEN}ALL {passed} CHECKS PASSED ✓{RESET}")
        return 0
    print(f"{RED}{failed} check(s) failed, {passed} passed{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
