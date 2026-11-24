"""Render tweaks (Nov 18 2026): pre-scaled 1080p media preference.

Advance preparation writes `<stem>.scaled-1080p.<ext>` next to an original
image or video. The render-facing media plan must prefer the scaled sibling
when it exists and use the original untouched when it does not, while the
provenance rows keep the originally retrieved asset.
"""

import pytest

from services.media_resolution_coordinator import SceneResolution
from services.pipeline_v2_media_adapter import (
    _scaled_1080p_path,
    prefer_scaled_1080p,
)


def test_scaled_1080p_path_naming():
    assert _scaled_1080p_path("/a/b/photo.jpg") == "/a/b/photo.scaled-1080p.jpg"
    assert _scaled_1080p_path("/a/b/clip.mp4") == "/a/b/clip.scaled-1080p.mp4"
    assert _scaled_1080p_path("/a/b/my.clip.webm") == "/a/b/my.clip.scaled-1080p.webm"
    # Audio, fonts, and non-media values are never rewritten.
    assert _scaled_1080p_path("/a/b/voice.mp3") == "/a/b/voice.mp3"
    assert _scaled_1080p_path("/a/b/font.ttf") == "/a/b/font.ttf"
    assert _scaled_1080p_path("") == ""
    assert _scaled_1080p_path(None) is None


def test_prefer_scaled_1080p_uses_sibling_when_present(tmp_path):
    original = tmp_path / "photo.jpg"
    scaled = tmp_path / "photo.scaled-1080p.jpg"
    original.write_bytes(b"orig")
    scaled.write_bytes(b"scaled")
    assert prefer_scaled_1080p(str(original)) == str(scaled)


def test_prefer_scaled_1080p_video_clip(tmp_path):
    clip = tmp_path / "scene.mp4"
    scaled = tmp_path / "scene.scaled-1080p.mp4"
    clip.write_bytes(b"clip")
    scaled.write_bytes(b"scaled")
    assert prefer_scaled_1080p(str(clip)) == str(scaled)


def test_prefer_scaled_1080p_falls_back_to_original_when_absent(tmp_path):
    original = tmp_path / "photo.jpg"
    original.write_bytes(b"orig")
    assert prefer_scaled_1080p(str(original)) == str(original)


def test_prefer_scaled_1080p_never_swaps_audio_even_when_sibling_exists(tmp_path):
    audio = tmp_path / "voice.mp3"
    scaled = tmp_path / "voice.scaled-1080p.mp3"
    audio.write_bytes(b"a")
    scaled.write_bytes(b"s")
    assert prefer_scaled_1080p(str(audio)) == str(audio)


def test_prefer_scaled_1080p_tolerates_empty_and_missing_values():
    assert prefer_scaled_1080p("") == ""
    assert prefer_scaled_1080p(None) is None
    assert prefer_scaled_1080p("/no/such/file.jpg") == "/no/such/file.jpg"


def test_media_plan_prefers_scaled_sibling_and_keeps_provenance(tmp_path, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    original = tmp_path / "still.jpg"
    scaled = tmp_path / "still.scaled-1080p.jpg"
    original.write_bytes(b"orig")
    scaled.write_bytes(b"scaled")
    unscaled = tmp_path / "other.png"
    unscaled.write_bytes(b"other")

    resolutions = {
        0: SceneResolution(
            scene_index=0, selected_path=str(original),
            provider="web", status="accepted", media_type="image",
        ),
        1: SceneResolution(
            scene_index=1, selected_path=str(unscaled),
            provider="web", status="accepted", media_type="image",
        ),
    }
    monkeypatch.setattr(
        adapter, "resolve_storyboard_media_v2", lambda *args, **kwargs: resolutions
    )
    scenes = [
        {"text": "scene zero", "order": 0, "scene_id": "s0",
         "container_id": "c0", "start_seconds": 0, "end_seconds": 4},
        {"text": "scene one", "order": 1, "scene_id": "s1",
         "container_id": "c1", "start_seconds": 4, "end_seconds": 8},
    ]
    plan, _ = adapter.resolve_and_build_media_plan(
        scenes,
        youtube_lane=lambda *args, **kwargs: None,
        web_lane=lambda *args, **kwargs: None,
        stock_lane=None,
        visual_verifier=None,
        neutral_factory=lambda scene: None,
    )

    # Render-facing paths use the scaled sibling where one exists.
    assert plan["media_paths"][0] == str(scaled)
    assert plan["media_paths"][1] == str(unscaled)
    # Provenance rows keep the originally retrieved asset.
    rows = plan["metadata"]["media_resolution_v2"]
    assert rows[0]["selected_path"] == str(original)
    assert rows[1]["selected_path"] == str(unscaled)
    assert plan["metadata"]["global_media_guarantee"]["no_blank_assets"] is True
