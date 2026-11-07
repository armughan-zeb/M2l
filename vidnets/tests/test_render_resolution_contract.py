import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import app


def test_short_landscape_request_is_not_overridden_by_shorts_preset():
    assert app._resolve_render_resolution({
        "type": "short",
        "resolution": "1080p",
        "aspect_ratio": "16:9",
    }) == (1920, 1080)


def test_short_default_stays_vertical_without_explicit_aspect():
    assert app._resolve_render_resolution({
        "type": "short",
        "resolution": "1080x1920 (TikTok/Shorts)",
    }) == (1080, 1920)


def test_explicit_render_tuple_wins_over_preset_and_aspect():
    assert app._resolve_render_resolution({
        "type": "short",
        "resolution": "1080x1920 (TikTok/Shorts)",
        "aspect_ratio": "16:9",
        "render_resolution": (1280, 720),
    }) == (1280, 720)
