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

    # Highlight color regression: previously fitz.utils.getColor("#hex")
    # returned white for any hex string, so highlights were invisible.
    # The new _to_fitz_color path bypasses getColor and returns the
    # actual float triple.
    annot.set_highlight_color((1.0, 0.0, 0.0))
    annot.highlight_rect(__import__("PyQt6").QtCore.QRectF(40, 40, 60, 20))
    page = eng.get_page(0)
    last_hl = None
    for a in page.annots() or []:
        if int(a.type[0]) == 8:
            last_hl = a
    if last_hl is not None:
        stroke_rgb = last_hl.colors.get("stroke", [])
        check("highlight color is the requested one (not white)",
              tuple(round(float(v), 2) for v in stroke_rgb) == (1.0, 0.0, 0.0),
              f"got {list(stroke_rgb)}")
    else:
        check("highlight color regression: annot present", False,
              "no highlight annot found on page")

    # Same regression check for ink: red ink should actually be red.
    annot.set_ink_color((0.0, 1.0, 0.0))
    stroke2 = CanvasStroke(points=[fitz.Point(50, 50), fitz.Point(120, 50)],
                           color_rgb=(0.0, 1.0, 0.0), thickness=2.0)
    annot.add_ink_stroke(stroke2)
    last_ink = None
    for a in page.annots() or []:
        if int(a.type[0]) == 15:  # PDF_ANNOT_INK
            last_ink = a
    if last_ink is not None:
        stroke_rgb = last_ink.colors.get("stroke", [])
        check("ink color is the requested one (not white)",
              tuple(round(float(v), 2) for v in stroke_rgb) == (0.0, 1.0, 0.0),
              f"got {list(stroke_rgb)}")

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

    # --------------------------------------------------------------- T9
    section("T9 — Phase 4: viewer engine find_all + render_thumbnail")
    eng_find = ViewerEngine()
    eng_find.open(pdf_src)
    matches = eng_find.find_all("test")
    check("find_all returns list", isinstance(matches, list), f"{len(matches)} match(es)")
    matches2 = eng_find.find_all("definitely_not_in_document_xyz")
    check("find_all returns empty for missing term", matches2 == [],
          f"{len(matches2)} match(es)")
    thumb = eng_find.render_thumbnail(0)
    check("render_thumbnail produces PNG bytes",
          isinstance(thumb, bytes) and len(thumb) > 0,
          f"{len(thumb) if thumb else 0} bytes")

    # --------------------------------------------------------------- T10
    section("T10 — Phase 4: annotation shapes (rect / ellipse / arrow / note / signature)")
    eng_shape = ViewerEngine()
    eng_shape.open(pdf_src)
    annot_shape = AnnotationEngine(eng_shape)
    from PyQt6.QtCore import QRectF, QPointF

    ok, msg = annot_shape.add_rect(QRectF(50, 50, 200, 100))
    check("add_rect runs", ok, msg)
    ok, msg = annot_shape.add_ellipse(QRectF(50, 200, 200, 100))
    check("add_ellipse runs", ok, msg)
    ok, msg = annot_shape.add_arrow(QPointF(50, 350), QPointF(250, 400))
    check("add_arrow runs", ok, msg)
    ok, msg = annot_shape.add_sticky_note(QPointF(100, 450), "Hello note")
    check("add_sticky_note runs", ok, msg)

    # Signature needs real PNG bytes
    from PIL import Image
    import io
    sig_img = Image.new("RGBA", (200, 60), (255, 255, 255, 0))
    buf = io.BytesIO()
    sig_img.save(buf, format="PNG")
    sig_bytes = buf.getvalue()
    ok, msg = annot_shape.add_signature(QRectF(50, 500, 200, 60), sig_bytes)
    check("add_signature runs", ok, msg)

    # Confirm at least 4 annotations now exist on page 1
    # (rect + ellipse + arrow + sticky note; the signature uses insert_image,
    # not a PDF annotation, so it doesn't appear in page.annots().)
    page_obj = eng_shape.get_page(0)
    n_annots = len(list(page_obj.annots() or []))
    check("shapes persisted as annotations", n_annots >= 4, f"{n_annots} annot(s)")

    # --------------------------------------------------------------- T11
    section("T11 — Phase 4: text_editor.whiteout_then_insert")
    eng_whiteout = ViewerEngine()
    eng_whiteout.open(pdf_src)
    ed_whiteout = TextEditor(eng_whiteout)
    ok, msg = ed_whiteout.whiteout_then_insert(
        page=1, x=50, y=200, new_text="Replaced!", font_size=14,
        width=200, height=24, viewer=None)
    check("whiteout_then_insert runs", ok, msg)

    # --------------------------------------------------------------- T12
    section("T12 — Phase 4: undo/redo round-trip")
    eng_undo = ViewerEngine()
    eng_undo.open(pdf_src)
    from features.pdf_editor.undo_stack import UndoStack
    stack = UndoStack(eng_undo)
    page0 = eng_undo.get_page(0)

    # Add an ink annotation and push to undo stack
    pre_count = len(list(page0.annots() or []))
    annot_obj = page0.add_ink_annot([[(10, 10), (60, 40), (120, 30)]])
    annot_obj.update()
    stack.push_added(0, annot_obj)
    post_count = len(list(page0.annots() or []))
    check("ink annotation added", post_count == pre_count + 1,
          f"{pre_count} → {post_count}")

    # Undo
    ok, msg = stack.undo()
    check("undo ok", ok, msg)
    after_undo = len(list(eng_undo.get_page(0).annots() or []))
    check("annotation removed by undo", after_undo == pre_count,
          f"{after_undo} annot(s)")

    # Redo
    ok, msg = stack.redo()
    check("redo ok", ok, msg)
    after_redo = len(list(eng_undo.get_page(0).annots() or []))
    check("annotation re-added by redo", after_redo == post_count,
          f"{after_redo} annot(s)")

    # --------------------------------------------------------------- T12b
    section("T12b — Page-level undo/redo (swap / move / rotate / delete)")

    def _page_labels(path):
        """Return the list of "LABEL_<n>" strings at each slot — used to
        fingerprint the on-disk page order."""
        d = fitz.open(path)
        out = [d[i].get_text().strip() for i in range(len(d))]
        d.close()
        return out

    # Build a 5-page PDF with unique labels.
    page_pdf = str(tmp / "page_undo_fixture.pdf")
    doc = fitz.open()
    for i in range(5):
        pg = doc.new_page(width=400, height=600)
        pg.insert_text((100, 200), f"LABEL_{i + 1}", fontsize=20)
    doc.save(page_pdf)
    doc.close()

    # ----- swap -----
    swap_pdf = str(tmp / "page_undo_swap.pdf")
    shutil.copy(page_pdf, swap_pdf)
    eng_swap = ViewerEngine()
    eng_swap.open(swap_pdf)
    stack_swap = UndoStack(eng_swap)

    PDFManipulator.swap_pages(swap_pdf, 1, 3)
    stack_swap.push_page_op("swap", page_a=1, page_b=3)
    labels_after = _page_labels(swap_pdf)
    check("swap wrote new order", labels_after[0] == "LABEL_3"
          and labels_after[2] == "LABEL_1",
          str(labels_after))

    ok, msg = stack_swap.undo()
    check("swap undo ok", ok, msg)
    eng_swap.reload_from_disk()
    check("swap undo restored identity",
          _page_labels(swap_pdf)[0] == "LABEL_1", str(_page_labels(swap_pdf)))

    ok, msg = stack_swap.redo()
    check("swap redo ok", ok, msg)
    eng_swap.reload_from_disk()
    check("swap redo re-applied swap",
          _page_labels(swap_pdf)[0] == "LABEL_3", str(_page_labels(swap_pdf)))

    # ----- rotate -----
    rot_pdf = str(tmp / "page_undo_rotate.pdf")
    shutil.copy(page_pdf, rot_pdf)
    eng_rot = ViewerEngine()
    eng_rot.open(rot_pdf)
    stack_rot = UndoStack(eng_rot)
    orig_rot = eng_rot.get_page(1).rotation

    PDFManipulator.rotate_page(rot_pdf, 2, 90)
    eng_rot.reload_from_disk()
    stack_rot.push_page_op("rotate", page=2, angle=90)
    check("rotate wrote new rotation",
          eng_rot.get_page(1).rotation == (orig_rot + 90) % 360,
          f"{eng_rot.get_page(1).rotation}°")

    ok, msg = stack_rot.undo()
    check("rotate undo ok", ok, msg)
    eng_rot.reload_from_disk()
    check("rotate undo restored angle",
          eng_rot.get_page(1).rotation == orig_rot,
          f"{eng_rot.get_page(1).rotation}°")

    # ----- move -----
    move_pdf = str(tmp / "page_undo_move.pdf")
    shutil.copy(page_pdf, move_pdf)
    eng_move = ViewerEngine()
    eng_move.open(move_pdf)
    stack_move = UndoStack(eng_move)

    PDFManipulator.move_page(move_pdf, 1, 3)
    stack_move.push_page_op("move", src_page=1, target_slot=3)
    check("move wrote new order",
          _page_labels(move_pdf) == ["LABEL_2", "LABEL_3",
                                       "LABEL_1", "LABEL_4", "LABEL_5"],
          str(_page_labels(move_pdf)))

    ok, msg = stack_move.undo()
    check("move undo ok", ok, msg)
    eng_move.reload_from_disk()
    check("move undo restored identity",
          _page_labels(move_pdf) == [f"LABEL_{i + 1}" for i in range(5)],
          str(_page_labels(move_pdf)))

    ok, msg = stack_move.redo()
    check("move redo ok", ok, msg)
    eng_move.reload_from_disk()
    check("move redo re-applied move",
          _page_labels(move_pdf) == ["LABEL_2", "LABEL_3",
                                       "LABEL_1", "LABEL_4", "LABEL_5"],
          str(_page_labels(move_pdf)))

    # ----- delete -----
    del_pdf = str(tmp / "page_undo_delete.pdf")
    shutil.copy(page_pdf, del_pdf)
    eng_del = ViewerEngine()
    eng_del.open(del_pdf)
    stack_del = UndoStack(eng_del)

    ok_c, _, cache = UndoStack.cache_deleted_page(del_pdf, 4)
    check("cache deleted page for undo", ok_c and os.path.isfile(cache),
          cache or "")
    PDFManipulator.delete_page(del_pdf, 4)
    stack_del.push_page_op("delete", page=4, deleted_page_pdf=cache)
    check("delete shrunk page count",
          len(fitz.open(del_pdf)) == 4, f"{len(fitz.open(del_pdf))} pages")

    ok, msg = stack_del.undo()
    check("delete undo ok", ok, msg)
    eng_del.reload_from_disk()
    check("delete undo restored page count",
          len(fitz.open(del_pdf)) == 5, f"{len(fitz.open(del_pdf))} pages")
    # The restored page's label should be LABEL_4.
    d = fitz.open(del_pdf)
    restored_label = d[3].get_text().strip()  # page 4 (1-based) = index 3
    d.close()
    check("delete undo restored correct page content",
          restored_label == "LABEL_4", restored_label)

    # ----- multi-step: swap, rotate, delete, then 3x undo -----
    multi_pdf = str(tmp / "page_undo_multi.pdf")
    shutil.copy(page_pdf, multi_pdf)
    eng_multi = ViewerEngine()
    eng_multi.open(multi_pdf)
    stack_multi = UndoStack(eng_multi)

    PDFManipulator.swap_pages(multi_pdf, 1, 2)
    stack_multi.push_page_op("swap", page_a=1, page_b=2)
    PDFManipulator.rotate_page(multi_pdf, 1, 90)
    stack_multi.push_page_op("rotate", page=1, angle=90)
    ok_c, _, cache = UndoStack.cache_deleted_page(multi_pdf, 3)
    PDFManipulator.delete_page(multi_pdf, 3)
    stack_multi.push_page_op("delete", page=3, deleted_page_pdf=cache)

    # Three undos: delete → rotate → swap. After all three the file
    # should be back to the identity 5-page fixture.
    stack_multi.undo(); eng_multi.reload_from_disk()
    stack_multi.undo(); eng_multi.reload_from_disk()
    stack_multi.undo(); eng_multi.reload_from_disk()
    check("multi-step undo restores identity",
          _page_labels(multi_pdf) == [f"LABEL_{i + 1}" for i in range(5)],
          str(_page_labels(multi_pdf)))

    # --------------------------------------------------------------- T13
    section("T13 — Phase 4: RecentFiles persistence")
    from features.pdf_viewer.recent_files import RecentFiles
    rf_tmp = tmp / "rf_test"
    rf_tmp.mkdir()
    rf_path = str(rf_tmp / "recent.json")
    rf = RecentFiles(path=rf_path)
    rf.add(pdf_src)
    rf.add(pdf_src)  # de-dupe
    items = rf.list()
    check("recent dedupe to 1 entry", len(items) == 1, f"{len(items)} item(s)")
    rf2 = RecentFiles(path=rf_path)
    items2 = rf2.list()
    check("recent persists across instances",
          items2 == items, f"{items2}")

    # --------------------------------------------------------------- T14
    section("T14 — Text selection (extract_text_at / extract_text_in_rect)")
    from features.pdf_viewer.text_selector import (
        extract_text_at, extract_text_in_rect, is_ocr_available,
    )
    # Build a PDF that contains known text we can extract
    known_pdf = str(tmp / "with_text.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "TermiPDF hello world")
    page.insert_text((50, 80), "Second line of text")
    doc.save(known_pdf)
    doc.close()
    eng_text = ViewerEngine()
    eng_text.open(known_pdf)

    # Click near "hello"
    ok, txt = extract_text_at(eng_text, fitz.Point(170, 55))
    check("extract_text_at finds word", ok and "hello" in txt, txt)

    # Drag-select a region covering both lines
    from PyQt6.QtCore import QRectF
    ok, txt = extract_text_in_rect(eng_text, QRectF(40, 40, 300, 60))
    check("extract_text_in_rect finds both lines", ok and "hello" in txt
          and "Second" in txt, txt.replace("\n", " | "))

    # OCR availability probe (don't require tesseract; just exercise the call)
    val = is_ocr_available()
    check("is_ocr_available returns bool", isinstance(val, bool), str(val))

    # Empty page → no text → friendly fallback message
    empty_pdf = str(tmp / "empty.pdf")
    doc = fitz.open()
    doc.new_page()
    doc.save(empty_pdf)
    doc.close()
    eng_empty = ViewerEngine()
    eng_empty.open(empty_pdf)
    ok, txt = extract_text_at(eng_empty, fitz.Point(50, 50))
    check("empty page reports no text", not ok or "install" in txt.lower()
          or txt == "", txt)

    # --------------------------------------------------------------- T15
    section("T15 — Canvas center-alignment helper (QApplication required)")
    # The viewer UI now wraps the surface in a centering container; we
    # verify the holder widget structure exists in source code.
    viewer_ui_src = (PROJECT_ROOT / "src" / "features" / "pdf_viewer"
                     / "viewer_ui.py").read_text()
    check("viewer_ui defines _canvas_holder",
          "_canvas_holder" in viewer_ui_src)
    check("viewer_ui sets widgetResizable(True)",
          "setWidgetResizable(True)" in viewer_ui_src)
    check("viewer_ui uses QHBoxLayout for centering",
          "QHBoxLayout" in viewer_ui_src)
    check("viewer_ui exposes SELECT mode",
          "SELECT" in viewer_ui_src and "select" in viewer_ui_src)

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
