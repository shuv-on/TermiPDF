"""Color parsing and HTML helpers used across features."""
from __future__ import annotations

import re
from typing import Tuple

# CSS named colors (subset - the most common ones)
_NAMED_COLORS = {
    "red":        (1.0, 0.0, 0.0),
    "green":      (0.0, 1.0, 0.0),
    "blue":       (0.0, 0.0, 1.0),
    "yellow":     (1.0, 1.0, 0.0),
    "cyan":       (0.0, 1.0, 1.0),
    "magenta":    (1.0, 0.0, 1.0),
    "black":      (0.0, 0.0, 0.0),
    "white":      (1.0, 1.0, 1.0),
    "gray":       (0.5, 0.5, 0.5),
    "grey":       (0.5, 0.5, 0.5),
    "orange":     (1.0, 0.5, 0.0),
    "pink":       (1.0, 0.75, 0.8),
    "purple":     (0.5, 0.0, 0.5),
    "brown":      (0.6, 0.3, 0.1),
    "lime":       (0.0, 1.0, 0.5),
    "navy":       (0.0, 0.0, 0.5),
    "teal":       (0.0, 0.5, 0.5),
    "olive":      (0.5, 0.5, 0.0),
    "silver":     (0.75, 0.75, 0.75),
    "gold":       (1.0, 0.84, 0.0),
}


def parse_color(value: str) -> Tuple[float, float, float]:
    """
    Parse a color string into a normalized (r, g, b) tuple in [0, 1].
    Accepts hex (#RRGGBB or #RGB), or a named CSS color.
    Raises ValueError on bad input.
    """
    if not value:
        raise ValueError("Empty color value")
    s = value.strip().lower()

    # Named color?
    if s in _NAMED_COLORS:
        return _NAMED_COLORS[s]

    # Hex (#RGB or #RRGGBB)
    if s.startswith("#"):
        hexpart = s[1:]
        if len(hexpart) == 3:
            hexpart = "".join(ch * 2 for ch in hexpart)
        if len(hexpart) != 6 or not re.fullmatch(r"[0-9a-f]{6}", hexpart):
            raise ValueError(f"Invalid hex color: {value}")
        r = int(hexpart[0:2], 16) / 255.0
        g = int(hexpart[2:4], 16) / 255.0
        b = int(hexpart[4:6], 16) / 255.0
        return (r, g, b)

    raise ValueError(f"Unsupported color format: {value}")


def html_color(value: str) -> str:
    """Return a CSS-friendly #RRGGBB string for any input we accept."""
    r, g, b = parse_color(value)
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"