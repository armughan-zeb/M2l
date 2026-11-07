"""
Transition Library — xfade + custom FFmpeg transitions.

Each transition returns (xfade_filter, needs_extra_inputs=False).
Some transitions (paper_tear, glitch, light_leak) need extra overlay tracks.
"""

XFADE_MAP = {
    "fade": "fade",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "wipeup": "wipeup",
    "wipedown": "wipedown",
    "wipetl": "wipetl",
    "wipetr": "wipetr",
    "wipebl": "wipebl",
    "wipebr": "wipebr",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "slideup": "slideup",
    "slidedown": "slidedown",
    "smoothleft": "smoothleft",
    "smoothright": "smoothright",
    "smoothup": "smoothup",
    "smoothdown": "smoothdown",
    "circlecrop": "circlecrop",
    "circleopen": "circleopen",
    "circleclose": "circleclose",
    "rectcrop": "rectcrop",
    "distance": "distance",
    "dissolve": "dissolve",
    "hblur": "hblur",
    "fadegrays": "fadegrays",
    "pixelize": "pixelize",
    "radial": "radial",
    "zoomin": "zoomin",
    "zoomout": "zoomout",
    "diagtl": "diagtl",
    "diagtr": "diagtr",
    "diagbl": "diagbl",
    "diagbr": "diagbr",
    "squeezev": "squeezev",
    "squeezeh": "squeezeh",
    "hlslice": "hlslice",
    "hrslice": "hrslice",
    "vuslice": "vuslice",
    "vdslice": "vdslice",
}


TRANSITIONS = {
    # ── Simple xfade transitions (fastest) ──
    "cut": {"xfade": None, "cost": 0, "tags": ["fast", "sharp"]},
    "dissolve": {"xfade": "fade", "cost": 1, "tags": ["smooth", "gentle"]},
    "dip_black": {"xfade": "fadeblack", "cost": 1, "tags": ["dramatic", "chapter"]},
    "dip_white": {"xfade": "fadewhite", "cost": 1, "tags": ["flash", "dream"]},
    "flash_cut": {"xfade": "fadeblack", "cost": 1, "tags": ["fast", "energetic"]},

    # ── Directional wipes ──
    "wipe_right": {"xfade": "wiperight", "cost": 1, "tags": ["clean", "dynamic"]},
    "wipe_left": {"xfade": "wipeleft", "cost": 1, "tags": ["clean", "dynamic"]},
    "wipe_up": {"xfade": "wipeup", "cost": 1, "tags": ["clean", "dynamic"]},
    "wipe_down": {"xfade": "wipedown", "cost": 1, "tags": ["clean", "dynamic"]},

    # ── Slides ──
    "slide_left": {"xfade": "slideleft", "cost": 2, "tags": ["movement", "energetic"]},
    "slide_right": {"xfade": "slideright", "cost": 2, "tags": ["movement", "energetic"]},
    "slide_up": {"xfade": "slideup", "cost": 2, "tags": ["movement", "energetic"]},
    "slide_down": {"xfade": "slidedown", "cost": 2, "tags": ["movement", "energetic"]},

    # ── Smooth (eased slides, CapCut-style) ──
    "smooth": {"xfade": "smoothleft", "cost": 2, "tags": ["smooth", "elegant"]},
    "smooth_left": {"xfade": "smoothleft", "cost": 2, "tags": ["smooth", "elegant"]},
    "smooth_right": {"xfade": "smoothright", "cost": 2, "tags": ["smooth", "elegant"]},
    "smooth_up": {"xfade": "smoothup", "cost": 2, "tags": ["smooth", "elegant"]},
    "smooth_down": {"xfade": "smoothdown", "cost": 2, "tags": ["smooth", "elegant"]},

    # ── Corner wipes ──
    "wipe_tl": {"xfade": "wipetl", "cost": 2, "tags": ["clean", "dynamic"]},
    "wipe_tr": {"xfade": "wipetr", "cost": 2, "tags": ["clean", "dynamic"]},
    "wipe_bl": {"xfade": "wipebl", "cost": 2, "tags": ["clean", "dynamic"]},
    "wipe_br": {"xfade": "wipebr", "cost": 2, "tags": ["clean", "dynamic"]},

    # ── Zoom ──
    "zoom_in": {"xfade": "zoomin", "cost": 2, "tags": ["impact", "focus"]},
    "zoom_out": {"xfade": "zoomout", "cost": 2, "tags": ["impact", "reveal"]},
    "zoom_burst_soft": {"xfade": "zoomin", "cost": 2, "tags": ["impact", "energetic"]},

    # ── Diagonal ──
    "diagonal_tl": {"xfade": "diagtl", "cost": 2, "tags": ["dynamic", "split"]},
    "diagonal_tr": {"xfade": "diagtr", "cost": 2, "tags": ["dynamic", "split"]},
    "diagonal_bl": {"xfade": "diagbl", "cost": 2, "tags": ["dynamic", "split"]},
    "diagonal_br": {"xfade": "diagbr", "cost": 2, "tags": ["dynamic", "split"]},

    # ── Creative ──
    "circle_reveal": {"xfade": "circlecrop", "cost": 2, "tags": ["creative", "focus"]},
    "circle_open": {"xfade": "circleopen", "cost": 2, "tags": ["creative", "reveal"]},
    "circle_close": {"xfade": "circleclose", "cost": 2, "tags": ["creative", "focus"]},
    "pixelate": {"xfade": "pixelize", "cost": 2, "tags": ["tech", "glitch"]},
    "radial_blur": {"xfade": "radial", "cost": 2, "tags": ["dreamy", "cinematic"]},
    "soft_blur": {"xfade": "hblur", "cost": 3, "tags": ["dreamy", "soft"]},
    "fade_grays": {"xfade": "fadegrays", "cost": 2, "tags": ["classic", "film"]},
    "squeeze_h": {"xfade": "squeezeh", "cost": 2, "tags": ["dynamic", "impact"]},
    "squeeze_v": {"xfade": "squeezev", "cost": 2, "tags": ["dynamic", "impact"]},

    # ── Glitch / Tech ──
    "glitch_slice_h": {"xfade": "hlslice", "cost": 3, "tags": ["tech", "glitch", "intense"]},
    "glitch_slice_v": {"xfade": "vuslice", "cost": 3, "tags": ["tech", "glitch", "intense"]},

    # ── Custom (needs extra filter chains) ──
    "paper_tear": {
        "xfade": None,
        "cost": 3,
        "tags": ["artistic", "document", "archive"],
        "custom": "paper_tear",
    },
    "light_leak": {
        "xfade": None,
        "cost": 2,
        "tags": ["cinematic", "warm", "vintage"],
        "custom": "light_leak_overlay",
    },
    "zoom_burst": {
        "xfade": None,
        "cost": 3,
        "tags": ["impact", "energetic", "action"],
        "custom": "zoom_burst",
    },
    "glitch_rgb": {
        "xfade": None,
        "cost": 4,
        "tags": ["tech", "intense", "crime"],
        "custom": "glitch_rgb_split",
    },
}


def get_xfade(transition_id: str, duration: float = 0.5, offset: float = 0.0) -> str:
    """Get xfade filter string for a transition."""
    t = TRANSITIONS.get(transition_id, TRANSITIONS["dissolve"])
    xf = t.get("xfade")
    if not xf:
        return None
    return f"xfade=transition={xf}:duration={duration:.3f}:offset={offset:.3f}"


def get_transition_cost(transition_id: str) -> int:
    """Get performance cost for a transition."""
    return TRANSITIONS.get(transition_id, {}).get("cost", 1)


def get_transition_tags(transition_id: str) -> list:
    """Get style tags for a transition."""
    return TRANSITIONS.get(transition_id, {}).get("tags", [])
