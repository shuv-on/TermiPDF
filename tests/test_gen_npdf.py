"""
test_gen_npdf.py — Unit tests for the new ``gen npdf`` range/exclusion grammar.

These exercise the pure parser helpers + ``PDFManipulator.generate_new_pdf``
without launching a GUI. Run from the project root with:

    python tests/test_gen_npdf.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import fitz  # noqa: E402

# The helper functions live in main_window.py which is normally imported
# only by the GUI; pull just the bits we need here.
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "main_window_helpers", PROJECT_ROOT / "src" / "main_window.py")
# We can't actually import main_window.py (it needs a display), so read
# just the parser helpers out of the source via a tiny wrapper module.
# Instead we use exec on the helper definitions directly.
_helpers_src = (PROJECT_ROOT / "src" / "main_window.py").read_text(
    encoding="utf-8", errors="replace")
# Cut off at the first "class TermiPDFWindow" line — everything before
# that is pure helpers we can safely import.
_helpers_src = _helpers_src.split("class TermiPDFWindow", 1)[0]
_ns: dict = {"__name__": "_main_window_helpers"}
try:
    exec(compile(_helpers_src, "<main_window_helpers>", "exec"), _ns)
except Exception as _exc:
    print(f"Failed to extract helpers: {_exc}")
    raise

# The exec'd source may have silently lost the helper if PyQt imports
# failed inside the exec'd namespace. Sanity-check the critical ones.
assert "_rewrite_to_dash" in _ns, "_rewrite_to_dash missing from exec'd namespace"
assert callable(_ns["_rewrite_to_dash"]), "_rewrite_to_dash not callable"

_rewrite_to_dash = _ns["_rewrite_to_dash"]
_parse_p_range_strict = _ns["_parse_p_range_strict"]
_parse_inclusion_strict = _ns["_parse_inclusion_strict"]
_parse_gen_npdf_args = _ns["_parse_gen_npdf_args"]
_looks_like_output_path = _ns["_looks_like_output_path"]

from features.pdf_editor.manipulation import PDFManipulator  # noqa: E402

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
CHECK = "\u2713"
CROSS = "\u2717"
passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        print(f"  {GREEN}{CHECK}{RESET} {name}"
              + (f"  ({detail})" if detail else ""))
        passed += 1
    else:
        print(f"  {RED}{CROSS}{RESET} {name}"
              + (f"  -- {detail}" if detail else ""))
        failed += 1


def section(title: str):
    print(f"\n=== {title} ===")


def _make_pdf(path: str, n_pages: int = 20) -> None:
    """Create a throwaway N-page PDF at ``path``."""
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page()
    doc.save(path)
    doc.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="termipdf_gen_npdf_"))
    src_pdf = str(tmp / "src.pdf")
    _make_pdf(src_pdf, n_pages=20)

    # ----------------------------------------------------- parser helpers
    section("Parser helpers — _rewrite_to_dash")
    _r1 = _rewrite_to_dash("p-1 to p-10")
    check("p-1 to p-10 becomes p-1-10",
          _r1 == "p-1-10", f"got={_r1!r}")
    _r2 = _rewrite_to_dash("p-3 to p-7 to p-9")
    check("two 'to' occurrences both rewritten",
          _r2 == "p-3-7-9", f"got={_r2!r}")
    _r3 = _rewrite_to_dash("p-2 TO p-9")
    check("case-insensitive: TO, To, to all match",
          _r3 == "p-2-9", f"got={_r3!r}")
    check("no 'to' leaves string untouched",
          _rewrite_to_dash("p-1,2,3") == "p-1,2,3")

    section("Parser helpers — _parse_p_range_strict")
    check("p-5 → (5,5)", _parse_p_range_strict("p-5") == (5, 5))
    check("p-1-5 → (1,5)", _parse_p_range_strict("p-1-5") == (1, 5))
    try:
        _parse_p_range_strict("p-10-1")
        check("p-10-1 raises ValueError", False, "no exception raised")
    except ValueError as exc:
        check("p-10-1 raises ValueError",
              "greater than end" in str(exc), str(exc))

    section("Parser helpers — _parse_inclusion_strict")
    # The caller is expected to pre-rewrite ``to`` → ``-`` via
    # ``_rewrite_to_dash``; here we pass the already-rewritten form.
    check("p-1-3 → [1,2,3]",
          _parse_inclusion_strict("p-1-3") == [1, 2, 3])
    check("p-1,3,5 → [1,3,5]",
          _parse_inclusion_strict("p-1,3,5") == [1, 3, 5])
    check("p-2-5,8 → [2,3,4,5,8]",
          _parse_inclusion_strict("p-2-5,8") == [2, 3, 4, 5, 8])
    # And the realistic end-to-end path: rewrite then parse.
    rewritten = _rewrite_to_dash("p-1 to p-3")
    check("rewrite+parse: p-1 to p-3 → [1,2,3]",
          _parse_inclusion_strict(rewritten) == [1, 2, 3])
    try:
        _parse_inclusion_strict("p-7-3")
        check("p-7-3 raises ValueError", False, "no exception raised")
    except ValueError as exc:
        check("p-7-3 raises ValueError",
              "greater than end" in str(exc), str(exc))

    section("Parser helpers — _looks_like_output_path")
    check("'output.pdf' is a path", _looks_like_output_path("output.pdf"))
    check("'~/foo.pdf' is a path",
          _looks_like_output_path("~/foo.pdf"))
    check("'./rel.pdf' is a path",
          _looks_like_output_path("./rel.pdf"))
    check("'p-1,2,3' is NOT a path",
          not _looks_like_output_path("p-1,2,3"))
    check("'p-1' is NOT a path",
          not _looks_like_output_path("p-1"))
    check("'5' is NOT a path",
          not _looks_like_output_path("5"))
    check("'w' is NOT a path",
          not _looks_like_output_path("w"))
    check("'to' is NOT a path",
          not _looks_like_output_path("to"))

    # ----------------------------------------------- end-to-end args
    section("_parse_gen_npdf_args — full grammar")

    inc, exc, out = _parse_gen_npdf_args(
        ["p-1", "to", "p-10", "/tmp/out.pdf"])
    check("range alone → ([1..10], [], /tmp/out.pdf)",
          inc == list(range(1, 11)) and exc == [] and out == "/tmp/out.pdf",
          f"inc={inc[:3]}... exc={exc} out={out}")

    inc, exc, out = _parse_gen_npdf_args(
        ["p-1", "to", "p-10", "w", "p-5"])
    check("range minus single page → ([1..10], [5], None)",
          inc == list(range(1, 11)) and exc == [5] and out is None,
          f"exc={exc} out={out}")

    inc, exc, out = _parse_gen_npdf_args(
        ["p-1", "to", "p-20", "w", "p-3,5,8", "out.pdf"])
    check("range minus multi → ([1..20], [3,5,8], out.pdf)",
          inc == list(range(1, 21)) and exc == [3, 5, 8]
          and out.endswith("out.pdf"),
          f"exc={exc} out={out}")

    inc, exc, out = _parse_gen_npdf_args(
        ["p-1", "to", "p-3", "w", "p-3", "to", "p-7", "/tmp/x.pdf"])
    check("range minus sub-range → ([1,2,3], [3..7], /tmp/x.pdf)",
          inc == [1, 2, 3] and exc == [3, 4, 5, 6, 7],
          f"inc={inc} exc={exc}")

    inc, exc, out = _parse_gen_npdf_args(["p-1", "to", "p-3"])
    check("no exclusion, no output → ([1,2,3], [], None)",
          inc == [1, 2, 3] and exc == [] and out is None)

    # Error cases
    def _expect_value_error(args, must_contain: str):
        try:
            _parse_gen_npdf_args(args)
            check(f"{args} raises ValueError", False, "no exception")
        except ValueError as exc:
            check(f"{args} raises ValueError",
                  must_contain in str(exc), str(exc))

    section("_parse_gen_npdf_args — errors")
    _expect_value_error(["w", "p-5", "x.pdf"], "Missing page range")
    _expect_value_error(["p-1", "to", "p-10", "w"], "requires an exclusion")
    _expect_value_error(["p-10", "to", "p-1", "x.pdf"], "greater than end")

    # ------------------------------------------------------ writer
    section("PDFManipulator.generate_new_pdf")
    out_pdf = str(tmp / "out_basic.pdf")
    ok, msg = PDFManipulator.generate_new_pdf(
        src_pdf, [1, 2, 3], out_pdf)
    check("writes 3-page PDF",
          ok and os.path.isfile(out_pdf), msg)
    if os.path.isfile(out_pdf):
        check("output has exactly 3 pages",
              len(fitz.open(out_pdf)) == 3,
              f"got {len(fitz.open(out_pdf))}")

    out_pdf2 = str(tmp / "out_excluded.pdf")
    pages = [p for p in range(1, 11) if p != 5]
    ok, msg = PDFManipulator.generate_new_pdf(
        src_pdf, pages, out_pdf2)
    check("writes 9-page PDF after exclusion",
          ok and os.path.isfile(out_pdf2), msg)
    if os.path.isfile(out_pdf2):
        check("output has exactly 9 pages",
              len(fitz.open(out_pdf2)) == 9,
              f"got {len(fitz.open(out_pdf2))}")

    # Out-of-range page → error, no partial write.
    out_pdf3 = str(tmp / "out_bad.pdf")
    ok, msg = PDFManipulator.generate_new_pdf(
        src_pdf, [1, 999], out_pdf3)
    check("page out of range → error",
          not ok and "out of range" in msg and not os.path.isfile(out_pdf3),
          msg)

    # Empty pages → error.
    out_pdf4 = str(tmp / "out_empty.pdf")
    ok, msg = PDFManipulator.generate_new_pdf(src_pdf, [], out_pdf4)
    check("empty pages → error",
          not ok and "No pages" in msg, msg)

    # Source missing → error.
    ok, msg = PDFManipulator.generate_new_pdf(
        "/nonexistent/foo.pdf", [1], str(tmp / "out_missing.pdf"))
    check("missing source → error",
          not ok and "not found" in msg.lower(), msg)

    print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())