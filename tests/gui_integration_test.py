"""
gui_integration_test.py — Off-screen integration test of the full GUI.

Boots the QApplication with the offscreen platform plugin, instantiates
the main window, runs a sequence of CLI commands through the parser,
and verifies the engine / annotation / editor state changes correctly.

Run:
    source .venv/bin/activate
    QT_QPA_PLATFORM=offscreen python tests/gui_integration_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from main_window import TermiPDFWindow  # noqa: E402
from features.pdf_viewer.viewer_ui import CanvasMode  # noqa: E402


GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def main() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = ""):
        nonlocal passed, failed
        if ok:
            print(f"  {GREEN}✓{RESET} {name}" + (f"  ({detail})" if detail else ""))
            passed += 1
        else:
            print(f"  {RED}✗{RESET} {name}" + (f"  -- {detail}" if detail else ""))
            failed += 1

    app = QApplication.instance() or QApplication(sys.argv)
    window = TermiPDFWindow()
    window.show()
    app.processEvents()

    # Build a 2-page test PDF
    import fitz
    tmp = Path(tempfile.mkdtemp(prefix="termipdf_gui_"))
    pdf_path = str(tmp / "two_page.pdf")
    src = fitz.open(str(PROJECT_ROOT / "src" / "test.pdf"))
    out = fitz.open()
    out.insert_pdf(src)
    out.insert_pdf(src)
    out.save(pdf_path)
    out.close()
    src.close()

    print("\n=== GUI command flow ===")
    # 1. Open
    res = window._do_open(pdf_path)
    check("GUI: open PDF", res.action == "print",
          "pages=" + str(window.engine.page_count))

    # 2. Next
    res = window._cmd_next([])
    check("GUI: next page", res.action == "print",
          "now page " + str(window.engine.current_page + 1))

    # 3. Goto page 1
    res = window._cmd_goto(["1"])
    check("GUI: goto 1", res.action == "print",
          "now page " + str(window.engine.current_page + 1))

    # 4. Zoom
    res = window._cmd_zoom(["2.0"])
    check("GUI: zoom 2.0", res.action == "print" and abs(window.engine.zoom - 2.0) < 0.01)

    # 5. Mode draw with red color
    res = window._cmd_mode(["draw", "--color", "red", "--thickness", "3"])
    check("GUI: mode draw --color red", res.action == "print")
    check("GUI: canvas mode == DRAW", window.pdf_viewer.mode == CanvasMode.DRAW)

    # 6. Simulate an ink stroke by calling the router directly
    from features.pdf_annotator.canvas_events import CanvasEventRouter
    router = CanvasEventRouter(window.engine, window.annot, window.pdf_viewer)
    stroke_pts = [fitz.Point(40 + i * 5, 40 + (i % 3) * 3) for i in range(20)]
    from features.pdf_viewer.viewer_ui import CanvasStroke
    stroke = CanvasStroke(points=stroke_pts, color_rgb=(1.0, 0.0, 0.0), thickness=3.0)
    router._on_commit_stroke(stroke)
    page = window.engine.get_page(0)
    n_annots = len(list(page.annots() or []))
    check("GUI: ink stroke persisted", n_annots >= 1, f"{n_annots} annot(s)")

    # 7. Mode highlight → use highlight_text command
    res = window._cmd_highlight_text(["anything"])
    check("GUI: highlight_text command", res.action == "print")

    # 8. Mode view
    res = window._cmd_mode(["view"])
    check("GUI: mode view", res.action == "print")
    check("GUI: canvas mode == VIEW", window.pdf_viewer.mode == CanvasMode.VIEW)

    # 9. Add text
    res = window._cmd_addtext(['"Hello GUI"', "--page", "1", "--x", "50", "--y", "700",
                                "--size", "16"])
    check("GUI: addtext", res.action == "print", res.data.get("text", ""))

    # 10. QR
    res = window._cmd_qr(['"https://termipdf.sh"', "--page", "1", "--x", "400", "--y", "100",
                           "--size", "80"])
    check("GUI: qr stamp", res.action == "print", res.data.get("text", ""))

    # 11. Save in-place
    res = window._cmd_save([])
    check("GUI: save (in-place)", res.action == "print")

    # 12. TOC toggle
    was_visible = window.toc.isVisible()
    res = window._cmd_toc([])
    check("GUI: toc toggle", res.action == "print")
    check("GUI: TOC visibility changed",
          window.toc.isVisible() != was_visible)

    # 13. Close
    res = window._cmd_close([])
    check("GUI: close", res.action == "print")
    check("GUI: engine.is_open == False", not window.engine.is_open)

    # 14. Parsed help text non-empty
    txt = window.parser.help_text()
    check("GUI: help text non-empty", len(txt) > 200, f"{len(txt)} chars")

    # 15. Bad command surfaces an error
    res = window.parser.execute("nonsense_command_xyz")
    check("GUI: bad command → error", res.action == "error")

    print()
    print("=" * 50)
    if failed == 0:
        print(f"{GREEN}ALL {passed} GUI CHECKS PASSED ✓{RESET}")
        return 0
    print(f"{RED}{failed} GUI check(s) failed, {passed} passed{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())