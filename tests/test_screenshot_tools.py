"""
test_screenshot_tools.py — Unit tests for screenshot tool selection.

Exercises the pure helpers ``_pick_screenshot_tool`` and
``_describe_no_screenshot_tool`` extracted from main_window.py without
launching a GUI. Run from the project root with:

    python tests/test_screenshot_tools.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Pull the pure helpers out of main_window.py via exec on the prefix
# before "class TermiPDFWindow".
import importlib.util  # noqa: E402

_helpers_src = (PROJECT_ROOT / "src" / "main_window.py").read_text(
    encoding="utf-8", errors="replace")
_helpers_src = _helpers_src.split("class TermiPDFWindow", 1)[0]
_ns: dict = {"__name__": "_main_window_helpers"}
try:
    exec(compile(_helpers_src, "<main_window_helpers>", "exec"), _ns)
except Exception as _exc:
    print(f"Failed to extract helpers: {_exc}")
    raise

_pick_screenshot_tool = _ns["_pick_screenshot_tool"]
_build_screenshot_candidates = _ns["_build_screenshot_candidates"]
_describe_no_screenshot_tool = _ns["_describe_no_screenshot_tool"]

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


def _make_which(*installed):
    """Build a fake ``which`` function that only resolves ``installed``."""
    installed_set = set(installed)

    def _which(name):
        return f"/usr/bin/{name}" if name in installed_set else None

    return _which


def main() -> int:
    candidates = _build_screenshot_candidates("linux", "/tmp/x.png")
    check("linux candidate list is non-empty",
          len(candidates) >= 5, f"count={len(candidates)}")
    check("candidate list is a list of 3-tuples",
          all(isinstance(c, tuple) and len(c) == 3 for c in candidates))
    check("every candidate carries a valid session tag",
          all(c[2] in {"any", "wayland", "x11"} for c in candidates))

    darwin_candidates = _build_screenshot_candidates("darwin", "/tmp/x.png")
    check("darwin returns screencapture",
          len(darwin_candidates) == 1
          and darwin_candidates[0][0] == "screencapture")

    win_candidates = _build_screenshot_candidates("win32", "/tmp/x.png")
    check("win32 returns powershell-snippingtool",
          len(win_candidates) == 1
          and win_candidates[0][0] == "powershell-snippingtool")

    unknown = _build_screenshot_candidates("plan9", "/tmp/x.png")
    check("unknown platform → empty list", unknown == [])

    # ----------------------------------------------------- selection logic
    section("_pick_screenshot_tool — basic")

    # Only grim on PATH, Wayland session → grim chosen.
    chosen = _pick_screenshot_tool(
        candidates, _make_which("grim"),
        {"WAYLAND_DISPLAY": "wayland-0"})
    check("only grim on PATH, Wayland → grim",
          chosen is not None and chosen[0] == "grim",
          f"got={chosen}")

    # Only grim on PATH, X11 session → grim skipped (wayland-only).
    chosen = _pick_screenshot_tool(
        candidates, _make_which("grim"),
        {"DISPLAY": ":0"})
    check("grim on PATH, X11 only → skipped",
          chosen is None, f"got={chosen}")

    # Only scrot on PATH, X11 session → scrot chosen.
    chosen = _pick_screenshot_tool(
        candidates, _make_which("scrot"),
        {"DISPLAY": ":0"})
    check("scrot on PATH, X11 → scrot",
          chosen is not None and chosen[0] == "scrot")

    # Only scrot on PATH, Wayland session → scrot skipped (x11-only).
    chosen = _pick_screenshot_tool(
        candidates, _make_which("scrot"),
        {"WAYLAND_DISPLAY": "wayland-0"})
    check("scrot on PATH, Wayland only → skipped",
          chosen is None, f"got={chosen}")

    # "any" tools work on either session.
    chosen = _pick_screenshot_tool(
        candidates, _make_which("flameshot"),
        {"WAYLAND_DISPLAY": "wayland-0"})
    check("flameshot works on Wayland",
          chosen is not None and chosen[0] == "flameshot")

    chosen = _pick_screenshot_tool(
        candidates, _make_which("flameshot"),
        {"DISPLAY": ":0"})
    check("flameshot works on X11",
          chosen is not None and chosen[0] == "flameshot")

    # No display at all → only "any" candidates survive.
    chosen = _pick_screenshot_tool(
        candidates, _make_which("grim", "scrot", "flameshot"), {})
    check("headless session → only 'any' tools considered",
          chosen is not None and chosen[0] == "flameshot")

    # First-match wins.
    chosen = _pick_screenshot_tool(
        candidates, _make_which("gnome-screenshot", "flameshot"),
        {"WAYLAND_DISPLAY": "wayland-0"})
    check("first 'any' candidate wins over later ones",
          chosen is not None and chosen[0] == "gnome-screenshot")

    section("_pick_screenshot_tool — slurp+grim gating")

    # Only grim installed, slurp missing → standalone grim wins (since it
    # appears earlier in the candidate list). The test here is that we DO
    # get a usable result, not None, because grim alone is functional.
    chosen = _pick_screenshot_tool(
        candidates, _make_which("grim"),
        {"WAYLAND_DISPLAY": "wayland-0"})
    check("only grim installed → standalone grim chosen",
          chosen is not None and chosen[0] == "grim",
          f"got={chosen}")

    # Both installed → still grim wins (earlier in list).
    chosen = _pick_screenshot_tool(
        candidates, _make_which("slurp", "grim"),
        {"WAYLAND_DISPLAY": "wayland-0"})
    check("slurp+grim installed but grim standalone wins (earlier)",
          chosen is not None and chosen[0] == "grim",
          f"got={chosen}")

    # Only slurp installed (no standalone grim) → no usable Wayland tool.
    chosen = _pick_screenshot_tool(
        candidates, _make_which("slurp"),
        {"WAYLAND_DISPLAY": "wayland-0"})
    check("slurp alone with no grim → None",
          chosen is None, f"got={chosen}")

    section("_pick_screenshot_tool — nothing usable")

    chosen = _pick_screenshot_tool(candidates, _make_which(), {})
    check("nothing installed, headless → None",
          chosen is None, f"got={chosen}")

    chosen = _pick_screenshot_tool(
        candidates, _make_which("does-not-exist"),
        {"DISPLAY": ":0"})
    check("fake tool on PATH ignored → None",
          chosen is None, f"got={chosen}")

    section("_describe_no_screenshot_tool — error message")

    msg = _describe_no_screenshot_tool(
        candidates, {"WAYLAND_DISPLAY": "wayland-0"})
    check("error mentions Wayland",
          "Wayland" in msg, f"got: {msg[:60]}...")
    check("error lists candidate names",
          all(name in msg for name in
              ("gnome-screenshot", "grim", "flameshot", "spectacle")),
          f"msg contains missing candidate(s)")
    check("error suggests installing flameshot",
          "flameshot" in msg and "apt install" in msg)

    msg = _describe_no_screenshot_tool(candidates, {"DISPLAY": ":0"})
    check("error mentions X11 when DISPLAY set",
          "X11" in msg, f"got: {msg[:60]}...")

    msg = _describe_no_screenshot_tool(candidates, {})
    check("error notes headless when no display",
          "headless" in msg or "no display" in msg,
          f"got: {msg[:60]}...")

    # Hybrid session
    msg = _describe_no_screenshot_tool(
        candidates, {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"})
    check("hybrid session detected",
          "hybrid" in msg or "Wayland" in msg and "X11" in msg,
          f"got: {msg[:60]}...")

    print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())