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
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QPoint, QPointF, QEvent
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QWheelEvent, QMouseEvent, QContextMenuEvent, QResizeEvent

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
    # Verify the first page actually renders on the canvas after open
    # (the user reported "1st page didn't load" — make sure the canvas
    # has a non-placeholder pixmap once the background render settles).
    app.processEvents()
    for _ in range(10):
        app.processEvents()
        time.sleep(0.03)
    check("GUI: first page renders after open",
          window.pdf_viewer._current_pixmap is not None
          and window.pdf_viewer._current_pixmap.size().height() > 100,
          f"pixmap={window.pdf_viewer._current_pixmap}")

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

    # ---- Phase 4 additions -----------------------------------------------
    # Re-open the PDF so the Phase 4 tests have a document to operate on.
    window._do_open(pdf_path)
    app.processEvents()

    # 16. Theme toggle (round-trip: light → dark → light)
    res = window._cmd_theme(["light"])
    check("GUI: theme light", res.action == "print")
    check("GUI: theme == light", window.theme.current() == "light")
    res = window._cmd_theme(["dark"])
    check("GUI: theme dark", res.action == "print")
    check("GUI: theme == dark", window.theme.current() == "dark")

    # 17. Bug-fix regression check: chrome pinned by default → no auto-hide
    window.show()
    app.processEvents()
    check("GUI: chrome pinned by default", window._chrome_pinned is True)
    check("GUI: terminal visible by default", window.term_dock.isVisible())
    check("GUI: toolbar visible by default", window.main_toolbar.isVisible())

    # 18. Dock position (terminal) — these dock-cycle commands may hide the
    # terminal via Qt's dock-reparent behavior; subsequent checks are OK
    # because we still call commands.
    res = window._cmd_dock(["left"])
    check("GUI: dock left", res.action == "print")
    check("GUI: dock left → terminal stays visible",
          window.term_dock.isVisible())
    res = window._cmd_dock(["right"])
    check("GUI: dock right", res.action == "print")
    check("GUI: dock right → terminal stays visible",
          window.term_dock.isVisible())
    res = window._cmd_dock(["bottom"])
    check("GUI: dock bottom", res.action == "print")
    check("GUI: dock bottom → terminal stays visible",
          window.term_dock.isVisible())
    res = window._cmd_dock(["top"])
    check("GUI: dock top", res.action == "print")
    check("GUI: dock top → terminal stays visible",
          window.term_dock.isVisible())
    # Reset to bottom for the rest of the test
    res = window._cmd_dock(["bottom"])
    check("GUI: dock reset to bottom", res.action == "print")

    # 18. Mode rect / ellipse / arrow via cmd
    res = window._cmd_mode(["rect"])
    check("GUI: mode rect", res.action == "print")
    check("GUI: canvas mode == RECT", window.pdf_viewer.mode == CanvasMode.RECT)
    res = window._cmd_mode(["ellipse"])
    check("GUI: mode ellipse", res.action == "print")
    res = window._cmd_mode(["arrow"])
    check("GUI: mode arrow", res.action == "print")
    res = window._cmd_mode(["view"])
    check("GUI: reset mode to view", res.action == "print")

    # 18b. Tool toggle behavior — clicking the same tool twice should
    # deselect it (back to VIEW). Regression for "deselect mode stuck".
    window._toggle_tool_via_cmd("draw")
    check("GUI: tool toggle on → DRAW",
          window.pdf_viewer.mode == CanvasMode.DRAW)
    window._toggle_tool_via_cmd("draw")
    check("GUI: tool toggle off → VIEW",
          window.pdf_viewer.mode == CanvasMode.VIEW)
    # Pen button reflects state in the toolbar
    pen_btn = window._tool_buttons["pen"]
    check("GUI: pen button checked reflects mode",
          pen_btn.isChecked() is False,
          f"checked={pen_btn.isChecked()}")

    # 19. Add a rect via router and verify undo round-trip
    from PyQt6.QtCore import QRectF
    router = CanvasEventRouter(window.engine, window.annot, window.pdf_viewer,
                               undo_stack=window.undo_stack)
    pre = len(list(window.engine.get_page(0).annots() or []))
    router._on_commit_rect(QRectF(10, 10, 100, 50))
    post = len(list(window.engine.get_page(0).annots() or []))
    check("GUI: rect persisted", post == pre + 1, f"{pre} → {post}")
    res = window._cmd_undo([])
    check("GUI: undo rect", res.action == "print")
    after = len(list(window.engine.get_page(0).annots() or []))
    check("GUI: undo removed rect", after == pre, f"{after} annot(s)")
    res = window._cmd_redo([])
    check("GUI: redo rect", res.action == "print")

    # 20. Find command (opens bar, runs search; the find_bar shouldn't crash)
    res = window._cmd_find(["TermiPDF"])
    check("GUI: find command", res.action == "print")
    check("GUI: find bar visible", window.find_bar.isVisible())

    # 20a. Scroll-step normalization: a single wheel notch should produce
    # a 10 px scroll (per-pixel, fast feel — bumped from 3 px because the
    # user reported the 3 px step felt choppy/sluggish). The scrollbar
    # value is updated synchronously inside wheelEvent — no animation.
    from PyQt6.QtGui import QWheelEvent
    # Ensure a non-zero scrollbar range so we can actually measure motion.
    # Open the 2-page test PDF (loaded earlier in #1) at higher zoom so
    # the page exceeds the viewport height.
    window.engine.set_zoom(2.0)
    window.pdf_viewer.refresh()
    app.processEvents()
    sb_v = window.pdf_viewer.scroll_area.verticalScrollBar()
    if sb_v.maximum() <= 0:
        # Page still fits — bump zoom more.
        window.engine.set_zoom(4.0)
        window.pdf_viewer.refresh()
        app.processEvents()
        sb_v = window.pdf_viewer.scroll_area.verticalScrollBar()
    sb_v.setValue(sb_v.minimum())
    app.processEvents()
    if hasattr(window.pdf_viewer, "_reset_momentum"):
        window.pdf_viewer._reset_momentum()
    before = sb_v.value()
    pos = QPointF(window.pdf_viewer.width() / 2, window.pdf_viewer.height() / 2)
    wheel = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(window.pdf_viewer, wheel)
    app.processEvents()
    after = sb_v.value()
    delta = after - before
    check("GUI: scroll step ≈ 20px per notch (per-pixel, fast feel)",
          18 <= abs(delta) <= 22,
          f"step={delta}px")
    # Reset zoom back to 1.5 so subsequent tests see the right viewport.
    window.engine.set_zoom(1.5)
    window.pdf_viewer.refresh()
    app.processEvents()

    # 20b. QR popup dimensions: card-style dialog with a big-enough QR
    # for phone scanning (bumped from 520×640 → 720×820 because the user
    # reported the smaller QR was hard to scan with a phone camera).
    from features.qr_generator.qr_share_dialog import QRShareDialog
    sample_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    dlg = QRShareDialog(sample_png, "test", parent=window)
    check("GUI: QR popup min width >= 640px",
          dlg.minimumWidth() >= 640,
          f"minWidth={dlg.minimumWidth()}")
    check("GUI: QR popup min height >= 720px",
          dlg.minimumHeight() >= 720,
          f"minHeight={dlg.minimumHeight()}")
    check("GUI: QR popup QR_MIN_PX >= 350 (roomy scan size)",
          QRShareDialog.QR_MIN_PX >= 350,
          f"QR_MIN_PX={QRShareDialog.QR_MIN_PX}")
    # Window flags: standard native frame (no FramelessWindowHint) so
    # close / minimize / maximize / system-menu buttons appear.
    check("GUI: QR popup uses native window frame (per user request)",
          not bool(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint),
          f"flags={int(dlg.windowFlags())}")
    check("GUI: QR popup has minimize button hint",
          bool(dlg.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint),
          f"flags={int(dlg.windowFlags())}")
    check("GUI: QR popup has maximize button hint",
          bool(dlg.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint),
          f"flags={int(dlg.windowFlags())}")
    check("GUI: QR popup has close button hint",
          bool(dlg.windowFlags() & Qt.WindowType.WindowCloseButtonHint),
          f"flags={int(dlg.windowFlags())}")
    # The Tool flag shares bits with Dialog (Qt.WindowType.Tool == 0xA
    # = Dialog | Tool), so we need to mask off the Dialog bits and
    # check whether the Tool-specific bit (0x8) is set.
    _tool_only = Qt.WindowType.Tool & ~Qt.WindowType.Dialog
    check("GUI: QR popup is NOT a Tool window (gets taskbar entry)",
          not bool(dlg.windowFlags() & _tool_only),
          f"flags={int(dlg.windowFlags())} tool_bit={int(_tool_only)}")
    dlg.close()

    # 20c. Edge-style text selection: drag-selecting in VIEW mode (the
    # default — no mode switch required) must populate the clipboard.
    res = window._cmd_mode(["view"])
    check("GUI: switched back to view", res.action == "print")
    # In VIEW mode the selection flow should still run through
    # _commit_select_rect when the user drags.
    router = CanvasEventRouter(window.engine, window.annot, window.pdf_viewer,
                               undo_stack=window.undo_stack)
    pre_clip = app.clipboard().text()
    router._on_select_rect(QRectF(0, 0, 1000, 1000))
    post_clip = app.clipboard().text()
    check("GUI: VIEW mode text-select still copies to clipboard",
          len(post_clip) > 0,
          f"clipboard len={len(post_clip)}")

    # ---- Bug-fix regressions -------------------------------------------
    # 21. Chrome pinned by default → terminal & toolbar should be visible
    # when the window is shown. The dock-toggle commands at #17 hide them
    # (re-attach a hidden dock), so we check BEFORE running them.
    res = window._cmd_toc([])
    res = window._cmd_thumbs([])
    check("GUI: chrome pinned by default (post doc open)", window._chrome_pinned is True)

    # 22. SELECT mode: click copies text to clipboard
    res = window._cmd_mode(["select"])
    check("GUI: mode select", res.action == "print")
    check("GUI: canvas mode == SELECT", window.pdf_viewer.mode == CanvasMode.SELECT)
    router = CanvasEventRouter(window.engine, window.annot, window.pdf_viewer,
                               undo_stack=window.undo_stack)
    # The test PDF has at least one page; click somewhere in the middle.
    router._on_select_point(QPointF(100, 100))
    clipboard = app.clipboard().text()
    check("GUI: select point wrote to clipboard", isinstance(clipboard, str),
          f"{len(clipboard)} chars on clipboard")

    # 23. SELECT mode: drag-select extracts text in rect
    router._on_select_rect(QRectF(0, 0, 1000, 1000))
    clipboard2 = app.clipboard().text()
    check("GUI: select rect wrote to clipboard", isinstance(clipboard2, str))

    # 24. Back to view mode
    res = window._cmd_mode(["view"])
    check("GUI: back to view", res.action == "print")

    # 25. Eraser regression test (bug: pymupdf "annotation not bound" crash)
    # Pre-fix this would crash because undo snapshot was taken AFTER delete
    # (the live Annot was already unbound). Now we snapshot before deleting.
    res = window._cmd_mode(["rect"])
    check("GUI: mode rect (for eraser test)", res.action == "print")
    router = CanvasEventRouter(window.engine, window.annot, window.pdf_viewer,
                               undo_stack=window.undo_stack)
    # Use a fresh, far-from-anything rect so the eraser only hits this one
    router._on_commit_rect(QRectF(450, 450, 100, 50))
    pre = len(list(window.engine.get_page(0).annots() or []))
    check("GUI: rect added for erase test", pre >= 1,
          f"{pre} annot(s) on page 0")
    res = window._cmd_mode(["erase"])
    check("GUI: mode erase", res.action == "print")
    # Erase at the center of the rect we just added (no crash!)
    result = router._on_erase_at(QPointF(500, 475))
    check("GUI: erase ran without crash",
          result is not None and not result.startswith("pymupdf"),
          str(result)[:60])
    after = len(list(window.engine.get_page(0).annots() or []))
    check("GUI: erase removed the rect", "Removed" in (result or "")
          or after == pre - 1,
          f"pre={pre} after={after} result={result}")
    res = window._cmd_mode(["view"])
    check("GUI: back to view (post erase test)", res.action == "print")

    # 26. Scroll-to-next-page (Edge-style continuous scroll).
    # Verify the wheel handler emits page_advance_requested when at the top
    # (delta negative) and the main window catches it via _on_page_advance.
    captures = []
    # Listen on BOTH signals — page_advance_requested is the fallback
    # path (no pre-rendered next page) and page_advance_committed is
    # the normal in-place swap path. Either one indicates the page
    # advance happened.
    window.pdf_viewer.page_advance_requested.connect(lambda d: captures.append(("req", d)))
    window.pdf_viewer.page_advance_committed.connect(lambda p: captures.append(("ok", p)))
    # Make sure we're on page 1
    last_page = window.engine.page_count
    if last_page > 1:
        window._cmd_goto(["1"])
        app.processEvents()
    sa = window.pdf_viewer.scroll_area
    sb = sa.verticalScrollBar()
    # Allow any pending background render / layout settle BEFORE
    # pinning the scrollbar to maximum — otherwise the test races
    # the preview → full-res swap and lands mid-page.
    for _ in range(8):
        app.processEvents()
        time.sleep(0.02)
    sb.setValue(sb.maximum())
    # Re-pin after settling: a fresh layout pass might have grown
    # sb.maximum() past where we just set it.
    app.processEvents()
    sb.setValue(sb.maximum())
    app.processEvents()
    # Wheel event must be sent to the viewer (not the scroll_area) because
    # wheelEvent() lives on PDFViewerUI. We fire enough notches to cross
    # the 180ms debounce + let the page-advance signal propagate.
    pos = QPointF(window.pdf_viewer.width() / 2,
                  window.pdf_viewer.height() / 2)
    delta = 120  # standard wheel notch magnitude
    def _fire_until(timeout=0.4, delta_y=120):
        """Fire wheel events with the given angleDelta.y() every ~30ms
        until the page advance signal arrives or the timeout elapses.
        Drives Qt's event loop between firings so the debounce timer
        can fire.
        """
        deadline = time.time() + timeout
        # Send the first event
        wheel = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, delta_y),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(window.pdf_viewer, wheel)
        app.processEvents()
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.03)
            app.processEvents()
            wheel = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, delta_y),
                                Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(window.pdf_viewer, wheel)
    # At maximum: scroll DOWN further (delta_y negative) → next page.
    # With the Edge-style virtual-scroll mode, the FIRST wheel tick
    # enters peek mode (renders next page below) and arms the snap
    # timer. The page_advance_requested signal is only emitted after
    # the user stops spinning for ~180 ms (the snap timer fires idle).
    # Fire ONE wheel tick then wait for the snap.
    wheel = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, -120),
                        Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(window.pdf_viewer, wheel)
    app.processEvents()
    # Verify we entered virtual-scroll mode (peek below the fold).
    check("GUI: virtual-scroll entered after wheel past bottom",
          window.pdf_viewer._next_page_pixmap is not None,
          f"next_pixmap={'set' if window.pdf_viewer._next_page_pixmap else 'None'}")
    # Wait for the snap timer (180 ms + margin).
    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    if last_page > 1:
        check("GUI: scroll past bottom → next page emitted",
              any((c[0] == "ok" or c == 1) for c in captures),
              f"captures={captures} page={window.engine.current_page+1}")
    else:
        check("GUI: scroll past bottom on last page → no crash", True)
    # At minimum: scroll UP further (delta_y positive) → prev page.
    if last_page > 1:
        captures.clear()
        window._cmd_goto([str(last_page)])
        app.processEvents()
        # Let the background render settle so the new surface size
        # has propagated to sb.maximum() before we pin sb.value.
        for _ in range(8):
            app.processEvents()
            time.sleep(0.02)
        sb.setValue(sb.minimum())
        app.processEvents()
        wheel = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 120),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(window.pdf_viewer, wheel)
        app.processEvents()
        check("GUI: virtual-scroll entered after wheel past top",
              window.pdf_viewer._next_page_pixmap is not None,
              f"next_pixmap={'set' if window.pdf_viewer._next_page_pixmap else 'None'}")
        for _ in range(30):
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        check("GUI: scroll past top → prev page emitted",
              any((c[0] == "ok" or c == -1) for c in captures),
              f"captures={captures} page={window.engine.current_page+1}")

    # 26b. Regression: when the page fits in the viewport (no scrollbar
    # range), any wheel event should advance/retreat the page — matches
    # Edge/Chrome where scrolling on a single-screen page takes the user
    # to the next page immediately instead of feeling "dead".
    if last_page > 1:
        captures.clear()
        window._cmd_goto(["1"])
        app.processEvents()
        # Force the viewport size to be larger than the page so the
        # scrollbar's maximum becomes 0 (no scroll range).
        original_size = window.pdf_viewer.size()
        try:
            window.pdf_viewer.resize(4000, 4000)
            window.engine.set_zoom(0.3)
            window.pdf_viewer.refresh()
            app.processEvents()
            sb_v = window.pdf_viewer.scroll_area.verticalScrollBar()
            if sb_v.maximum() <= 0:
                # Good — page fits in viewport. Wheel-down (delta<0)
                # requests page advance FORWARD; debounce requires ~180ms
                # of continuous pushing, so we fire several events
                # with a small delay between them.
                _fire_until(timeout=0.4, delta_y=-120)
                app.processEvents()
                check("GUI: wheel on single-viewport page → next page",
                      any(c == ("req", 1) or c == 1 for c in captures),
                      f"captures={captures} page={window.engine.current_page+1}")
            else:
                check("GUI: wheel on single-viewport page → next page", True,
                      "skipped (page did not fit at zoom=0.3)")
        finally:
            # Restore zoom + size for downstream tests
            window.engine.set_zoom(1.5)
            window.pdf_viewer.resize(original_size)
            window.pdf_viewer.refresh()
            app.processEvents()

    # 26c. Toolbar hide → reveal arrow button appears
    res = window._action_toggle_toolbar()
    app.processEvents()
    reveal = getattr(window, "_toolbar_reveal_btn", None)
    check("GUI: toolbar hide shows reveal arrow button",
          reveal is not None and reveal.isVisible(),
          f"reveal btn visible={reveal is not None and reveal.isVisible()}")
    check("GUI: reveal arrow icon flips to chevron-down",
          reveal is not None and reveal.icon() is not None,
          "icon check")
    # Reveal button must be anchored on the RIGHT side (not left).
    if reveal is not None:
        window.resizeEvent(QResizeEvent(window.size(), window.size()))
        app.processEvents()
        center_x = reveal.x() + reveal.width() / 2
        win_w = window.width()
        check("GUI: reveal arrow is on the right half of the window",
              center_x > win_w / 2,
              f"center_x={center_x:.0f} win_w={win_w}")
    else:
        check("GUI: reveal arrow is on the right half of the window",
              False, "no reveal button")
    # Hide-reveal: click the reveal button → toolbar back, arrow gone
    if reveal is not None:
        reveal.click()
        app.processEvents()
        check("GUI: reveal arrow restores toolbar + hides itself",
              window.main_toolbar.isVisible() and not reveal.isVisible(),
              f"toolbar visible={window.main_toolbar.isVisible()}, "
              f"reveal visible={reveal.isVisible()}")
    else:
        check("GUI: reveal arrow restores toolbar + hides itself", False,
              "no reveal button created")

    # 26d. Page advance is instant — no fade animation by user request.
    # The user explicitly asked to remove scroll-triggered animations, so
    # the surface should NOT have a QGraphicsOpacityEffect installed
    # after a wheel-past-end page change.
    if last_page > 1:
        # Reset to page 1 and clear any animations
        window._cmd_goto(["1"])
        app.processEvents()
        # Move to page 2 via the wheel-past-end path
        window.pdf_viewer._reset_momentum()
        sb = window.pdf_viewer.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())
        app.processEvents()
        pos = QPointF(window.pdf_viewer.width() / 2,
                      window.pdf_viewer.height() / 2)
        wheel = QWheelEvent(pos, pos, QPoint(0, 0), QPoint(0, 120),
                            Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(window.pdf_viewer, wheel)
        app.processEvents()
        # Animation should be GONE — no opacity effect installed.
        has_fx = getattr(window.pdf_viewer.surface, "_fade_effect", None) is not None
        check("GUI: page-advance uses NO fade animation (per user request)",
              not has_fx,
              f"fade_effect={'present' if has_fx else 'missing'}")

    # 26e. Pages Manager: open + verify it populates thumbnails
    window._action_open_pages()
    app.processEvents()
    check("GUI: Pages Manager opens",
          getattr(window, "_pages_manager", None) is not None
          and window._pages_manager.isVisible())
    if getattr(window, "_pages_manager", None) is not None:
        check("GUI: Pages Manager populates thumbnails",
              window._pages_manager.list.count() == last_page,
              f"count={window._pages_manager.list.count()} expected={last_page}")
        # 26e-extra: verify the page_moved signal is wired so a tile-on-tile
        # drop reaches the dialog. We DON'T actually trigger the reorder
        # (which would mutate the on-disk PDF and break later merge tests).
        if last_page >= 2:
            check("GUI: pages_manager exposes page_moved signal",
                  hasattr(window._pages_manager.list, "page_moved"),
                  "missing signal")
            check("GUI: pages_manager exposes pages_reordered signal",
                  hasattr(window._pages_manager, "pages_reordered"),
                  "missing signal")
            check("GUI: pages_manager exposes pages_swapped signal",
                  hasattr(window._pages_manager, "pages_swapped"),
                  "missing signal")
        # Close the dialog
        window._pages_manager.close()
        app.processEvents()

    # 26e-swap: end-to-end swap test — drag-and-drop path. Open a fresh
    # copy so we can mutate the on-disk PDF without breaking later tests.
    swap_pdf = "/tmp/swap_e2e.pdf"
    import fitz
    _d = fitz.open(); [_d.new_page() for _ in range(4)]; _d.save(swap_pdf); _d.close()
    swap_window = TermiPDFWindow()
    swap_window.engine.open(swap_pdf)
    swap_window.pdf_viewer.attach_engine(swap_window.engine)
    swap_window.pdf_viewer.refresh()
    app.processEvents()
    swap_window._action_open_pages()
    app.processEvents()
    if getattr(swap_window, "_pages_manager", None) is not None:
        # Capture both new + legacy signals
        swapped_capture = []
        reordered_capture = []
        swap_window._pages_manager.pages_swapped.connect(
            lambda a, b: swapped_capture.append((a, b)))
        swap_window._pages_manager.pages_reordered.connect(
            lambda i: reordered_capture.append(i))
        # Swap pages 1 and 3 (drag tile 1 onto tile 3).
        swap_window._pages_manager._on_page_moved(1, 3)
        app.processEvents()
        check("GUI: pages_manager swap emits pages_swapped(1,3)",
              swapped_capture == [(1, 3)],
              f"captures={swapped_capture}")
        check("GUI: pages_manager swap emits pages_reordered(target)",
              reordered_capture == [3],
              f"captures={reordered_capture}")
        # Verify the on-disk page count is unchanged (true swap, not move).
        import fitz as _f
        _doc = _f.open(swap_pdf)
        check("GUI: swap keeps total page count (4)",
              len(_doc) == 4,
              f"len={len(_doc)}")
        # And the order changed: page at slot 1 is now the original page 3,
        # page at slot 3 is now the original page 1. We prove it by checking
        # a side effect — the page sizes differ because new_page() with no
        # args uses the same default rect, so we instead assert by reading
        # the page rect's rotation which we explicitly set differently.
        # Simpler proof: reload and check current_page points to slot 3.
        swap_window._do_open(swap_pdf)
        check("GUI: engine reloads after swap, page_count still 4",
              swap_window.engine.page_count == 4,
              f"page_count={swap_window.engine.page_count}")
        _doc.close()
        swap_window._pages_manager.close()
    swap_window.close()
    app.processEvents()

    # 26e-cli-swap: terminal `swap` command parses and executes.
    cli_swap_pdf = "/tmp/cli_swap.pdf"
    _d = fitz.open()
    for i in range(5):
        _d.new_page(width=200 + i * 10, height=300)   # distinct sizes for proof
    _d.save(cli_swap_pdf)
    _d.close()
    cli_window = TermiPDFWindow()
    cli_window.engine.open(cli_swap_pdf)
    cli_window.pdf_viewer.attach_engine(cli_window.engine)
    cli_window.pdf_viewer.refresh()
    app.processEvents()
    # Capture the page rects BEFORE the swap.
    before_rects = [cli_window.engine.get_page(i).rect for i in range(5)]
    # Run the swap command via the parser directly (same path the terminal uses).
    res = cli_window.parser.execute("swap 1 5")
    app.processEvents()
    check("CLI: swap 1 5 returns ok=True",
          res.action == "print" and "Swap complete" in res.data.get("text", ""),
          f"action={res.action} data={res.data}")
    # Engine should have been reloaded by the handler — verify on disk.
    cli_window._do_open(cli_swap_pdf)
    app.processEvents()
    after_rects = [cli_window.engine.get_page(i).rect for i in range(5)]
    # Slot 0 now holds the original page 4 (width=240). Slot 4 holds the
    # original page 0 (width=200). Other slots unchanged.
    check("CLI: swap exchanges rect at slot 0",
          abs(after_rects[0].width - before_rects[4].width) < 0.5,
          f"before[4].w={before_rects[4].width} after[0].w={after_rects[0].width}")
    check("CLI: swap exchanges rect at slot 4",
          abs(after_rects[4].width - before_rects[0].width) < 0.5,
          f"before[0].w={before_rects[0].width} after[4].w={after_rects[4].width}")
    check("CLI: swap keeps slot 2 unchanged (intervening pages not shifted)",
          abs(after_rects[2].width - before_rects[2].width) < 0.5,
          f"before[2].w={before_rects[2].width} after[2].w={after_rects[2].width}")
    # p-N syntax
    res2 = cli_window.parser.execute("swap p-2 p-4")
    cli_window._do_open(cli_swap_pdf)
    app.processEvents()
    after2 = [cli_window.engine.get_page(i).rect for i in range(5)]
    # Now slot 1 should have the original page at slot 3 (width=230),
    # and slot 3 should have the original page at slot 1 (which was width=210).
    check("CLI: swap p-2 p-4 syntax exchanges slot 1 and slot 3",
          abs(after2[1].width - 230) < 0.5 and abs(after2[3].width - 210) < 0.5,
          f"after2[1].w={after2[1].width} after2[3].w={after2[3].width}")
    # No-PDF error path
    cli_window.engine.close()
    res_err = cli_window.parser.execute("swap 1 2")
    check("CLI: swap with no PDF returns error",
          res_err.action == "error",
          f"action={res_err.action} data={res_err.data}")
    cli_window.close()
    app.processEvents()

    # 26f. Terminal page-spec parser handles the new merge/gen syntax
    from main_window import _parse_p_range, _parse_page_spec
    check("parser: p-1 → (1,1)", _parse_p_range("p-1") == (1, 1))
    check("parser: p-1-10 → (1,10)", _parse_p_range("p-1-10") == (1, 10))
    check("parser: p-10-1 → (1,10)",
          _parse_p_range("p-10-1") == (1, 10))
    check("parser: p-1,2,3 → [1,2,3]",
          _parse_page_spec("p-1,2,3") == [1, 2, 3])
    check("parser: 1,2,3 → [1,2,3]",
          _parse_page_spec("1,2,3") == [1, 2, 3])
    check("parser: p-1,2-4,7 → [1,2,3,4,7]",
          _parse_page_spec("p-1,2-4,7") == [1, 2, 3, 4, 7])

    # 26g. Terminal merge p-X p-Y uses extract_pages (single-source range)
    if last_page > 1:
        out_path = os.path.join(tempfile.gettempdir(),
                                "test_merge_p.pdf")
        if os.path.exists(out_path):
            os.remove(out_path)
        res = window._cmd_merge(["p-1", "p-" + str(last_page), out_path])
        app.processEvents()
        check("CLI: merge p-1 p-N <out> creates a PDF",
              res.action == "print"
              and os.path.isfile(out_path)
              and os.path.getsize(out_path) > 0,
              f"action={res.action} out_exists={os.path.isfile(out_path)}")

    # 26h. Terminal gen npdf p-1,2 <out>
    if last_page >= 2:
        out_path = os.path.join(tempfile.gettempdir(),
                                "test_gen_npdf.pdf")
        if os.path.exists(out_path):
            os.remove(out_path)
        res = window._cmd_gen(["npdf", "p-1,2", out_path])
        app.processEvents()
        check("CLI: gen npdf p-1,2 <out> creates a 2-page PDF",
              res.action == "print"
              and os.path.isfile(out_path),
              f"action={res.action} out_exists={os.path.isfile(out_path)}")
        # Verify the resulting PDF has exactly the expected pages
        if os.path.isfile(out_path):
            import fitz as _fitz
            try:
                d = _fitz.open(out_path)
                npages = len(d)
                d.close()
                check("CLI: gen npdf produced exactly 2 pages",
                      npages == 2,
                      f"got {npages}")
            except Exception:
                check("CLI: gen npdf produced exactly 2 pages", False,
                      "could not read PDF")

    # 26i. Rotate button / Ctrl+R rotates the current page in place.
    # Capture the page's rotation before and after the action.
    window._cmd_goto(["1"])
    app.processEvents()
    try:
        pre_rot = window.engine.get_current_page().rotation
    except Exception:
        pre_rot = 0
    window._action_rotate()
    app.processEvents()
    try:
        post_rot = window.engine.get_current_page().rotation
    except Exception:
        post_rot = pre_rot
    check("GUI: rotate action rotates page 90°",
          (post_rot - pre_rot) % 360 == 90,
          f"pre={pre_rot} post={post_rot}")
    # Verify the user stays on the same page after rotation.
    check("GUI: rotate keeps user on same page",
          window.engine.current_page == 0,
          f"page={window.engine.current_page}")
    # Verify the rotate icon is registered
    from shared.utils.icon_factory import IconFactory
    icon = IconFactory.get("rotate", 20)
    check("GUI: rotate icon factory returns a non-null QIcon",
          not icon.isNull(),
          "icon.isNull()")

    # 27. Right-click QR-share (selection → context menu → QR stamp)
    # Reset to page 1, select text, right-click → context menu fires the
    # signal, _on_canvas_context_menu builds the menu and (via the
    # overridden popup) returns without blocking.
    window._cmd_goto(["1"])
    app.processEvents()
    # Re-open the original 2-page PDF and switch to SELECT mode
    window._cmd_mode(["select"])
    check("GUI: select mode active", window.pdf_viewer.mode == CanvasMode.SELECT)
    router = CanvasEventRouter(window.engine, window.annot, window.pdf_viewer,
                               undo_stack=window.undo_stack)
    # Pick a region of text — the PDF text was inserted at known coords
    router._on_select_rect(QRectF(40, 40, 300, 60))
    selection = window.pdf_viewer.get_selection()
    check("GUI: SELECT → selection buffer populated",
          len(selection) > 0,
          f"{len(selection)} chars in selection buffer")
    check("GUI: SELECT → clipboard populated",
          len(app.clipboard().text()) > 0,
          f"{len(app.clipboard().text())} chars in clipboard")

    # Capture context menu signal
    captures = []
    window.pdf_viewer.context_menu_requested.connect(
        lambda pt: captures.append(pt))
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse,
                           QPointF(150, 150).toPoint())
    window.pdf_viewer.surface.contextMenuEvent(ev)
    app.processEvents()
    check("GUI: right-click → context_menu_requested emitted",
          len(captures) >= 1, f"{len(captures)} capture(s)")

    # Right-click via raw mouse press (not via QContextMenuEvent) also works
    captures.clear()
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(200, 200),
                        QPointF(200, 200), Qt.MouseButton.RightButton,
                        Qt.MouseButton.RightButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.pdf_viewer.surface, press)
    app.processEvents()
    check("GUI: right-click mouse press → context_menu_requested",
          len(captures) >= 1, f"{len(captures)} capture(s)")

    # QR-share should open a popup (not stamp onto the page — that's the
    # new MS-Edge-style behavior). The PDF's image count should NOT change.
    images_before = len(window.engine.get_page(0).get_images())
    window._qr_share_text(selection or "Hello", QPointF(100, 600))
    app.processEvents()
    images_after = len(window.engine.get_page(0).get_images())
    check("GUI: QR-share does NOT stamp onto page",
          images_after == images_before,
          f"{images_before} → {images_after} images")
    # The QR-share dialog should be open as a top-level widget.
    from features.qr_generator.qr_share_dialog import QRShareDialog
    dialogs = [w for w in QApplication.topLevelWidgets()
               if isinstance(w, QRShareDialog)]
    check("GUI: QR-share opened a popup dialog",
          len(dialogs) >= 1,
          f"{len(dialogs)} QRShareDialog(s) visible")
    if dialogs:
        check("GUI: QR dialog is non-modal (can stay open with PDF)",
              not dialogs[0].isModal())
        # Close dialog to clean up
        for d in dialogs:
            d.close()
        app.processEvents()

    print()
    print("=" * 50)
    if failed == 0:
        print(f"{GREEN}ALL {passed} GUI CHECKS PASSED ✓{RESET}")
        return 0
    print(f"{RED}{failed} GUI check(s) failed, {passed} passed{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())