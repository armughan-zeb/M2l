"""Approved text-card slots copied from the placement guide."""

from __future__ import annotations

from typing import Any, Dict


# The guide image is 472 x 263 pixels. Keep the source rectangles explicit so
# every renderer can derive the same normalized anchors instead of inventing
# its own safe-area percentages.
GUIDE_WIDTH = 472
GUIDE_HEIGHT = 264

GUIDE_TEXT_CARD_RECTS: Dict[str, Dict[str, int]] = {
    "top_left": {"left": 10, "top": 10, "right": 120, "bottom": 40},
    "top_right": {"left": 347, "top": 10, "right": 457, "bottom": 40},
    "center": {"left": 178, "top": 103, "right": 300, "bottom": 145},
    "bottom_left": {"left": 10, "top": 225, "right": 120, "bottom": 255},
    "bottom_right": {"left": 347, "top": 230, "right": 457, "bottom": 260},
}

ALLOWED_TEXT_POSITIONS = tuple(GUIDE_TEXT_CARD_RECTS)


def normalize_text_position(value: Any, default: str = "center") -> str:
    """Return a canonical guide slot; never return a forbidden/unknown slot."""
    candidate = str(value or "").strip().lower().replace("-", "_")
    if candidate in GUIDE_TEXT_CARD_RECTS:
        return candidate
    fallback = str(default or "").strip().lower().replace("-", "_")
    return fallback if fallback in GUIDE_TEXT_CARD_RECTS else "center"


def guide_anchor(value: Any, default: str = "center") -> Dict[str, Any]:
    """Return exact normalized anchor and pixel boundaries for a green slot."""
    position = normalize_text_position(value, default)
    rect = GUIDE_TEXT_CARD_RECTS[position]
    
    # Exact center of green box
    cx = ((rect["left"] + rect["right"]) / 2.0) / GUIDE_WIDTH
    cy = ((rect["top"] + rect["bottom"]) / 2.0) / GUIDE_HEIGHT
    
    # Normalized box width and height
    bw = (rect["right"] - rect["left"]) / GUIDE_WIDTH
    bh = (rect["bottom"] - rect["top"]) / GUIDE_HEIGHT
    
    horizontal = "center" if position == "center" else ("right" if position.endswith("_right") else "left")
    vertical = "center" if position == "center" else ("bottom" if position.startswith("bottom_") else "top")
    
    return {
        "position": position,
        "x": round(cx, 6),
        "y": round(cy, 6),
        "cx": round(cx, 6),
        "cy": round(cy, 6),
        "norm_w": round(bw, 6),
        "norm_h": round(bh, 6),
        "horizontal": horizontal,
        "vertical": vertical,
        "rect": dict(rect),
    }
