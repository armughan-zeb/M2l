"""Integration smoke test: the media coordinator honors a debugger scene
advance (services/job_debugger.py + media_resolution_coordinator.py).

A scene whose only downloaded candidate was vision-rejected as a partial
match (real BNSF footage, wrong viewpoint -- the BNSF cab-scene situation)
resolves to that on-disk file when the advance flag is set, and falls back
to a graphic when it is not. This is the tier-1 rule of the stall policy:
reuse already-downloaded, vision-checked media instead of hunting further.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.job_debugger import (  # noqa: E402
    clear_scene_advances,
    consume_scene_advance,
    is_scene_advance_requested,
    set_scene_advance,
)
from services.media_resolution_coordinator import (  # noqa: E402
    resolve_storyboard_media_v2,
)


@pytest.fixture(autouse=True)
def _clean_advance_registry():
    clear_scene_advances()
    yield
    clear_scene_advances()


def _setup(tmp_path, *, set_flag_in_verifier=False):
    clip = tmp_path / "bnsf-frame.mp4"
    clip.write_bytes(b"x" * 4096)
    graphic = tmp_path / "graphic.mp4"
    graphic.write_bytes(b"x" * 4096)
    scene = {
        "text": "A BNSF freight crosses in a whiteout, shown from the cab",
        "scene_index": 0,
        "scene_id": "scene_0000",
        "container_id": "global",
        # Relaxed YouTube soft-pass disabled: a soft vision rejection must
        # leave the scene unresolved (like a strict scene), so the advance
        # is what resolves it -- otherwise the normal path would accept the
        # clip and the test could not prove the branch did the work.
        "source_policy": {"youtube_relaxed_visual_gate": False},
    }
    candidate = {
        "path": str(clip),
        "source": "youtube",
        "provider": "youtube",
        "title": "BNSF freight in snow",
        "final_score": 58.0,
        "score": 58.0,
        "candidate_id": "KtdyKC3GTnk-frame3",
    }

    def youtube_lane(scene_row, event):
        return [dict(candidate)]

    def web_lane(scene_row, event):
        return []

    def fake_verifier(scene_row, cand):
        # Vision saw it: real BNSF, wrong viewpoint -- partial match, not a
        # fatal mismatch. The normal gate rejects it; the advance reuses it.
        # The flag is set here (during the gate) to mirror production, where
        # the watchdog flags the scene mid-run, after discovery has already
        # populated the on-disk candidates the advance reuses.
        if set_flag_in_verifier:
            set_scene_advance("smoke-job", 1, "smoke test")
        return {
            "accepted": False,
            "status": "rejected",
            "reason": "partial_match",
            "final_score": 58.0,
            "identity_score": 60.0,
            "scene_match": False,
            "fatal_mismatch": False,
            "visual_checked": True,
            "visual_available": True,
        }

    def neutral_factory(scene_row):
        return {"path": str(graphic)}

    return scene, clip, graphic, youtube_lane, web_lane, fake_verifier, neutral_factory


def _resolve(scene, youtube_lane, web_lane, fake_verifier, neutral_factory):
    return resolve_storyboard_media_v2(
        [scene],
        youtube_lane=youtube_lane,
        web_lane=web_lane,
        youtube_fallback_lane=None,
        web_fallback_lane=None,
        stock_lane=None,
        visual_verifier=fake_verifier,
        neutral_factory=neutral_factory,
        progress_callback=None,
        deadline_seconds=30.0,
        web_fallback_grace_seconds=0.0,
        max_workers=2,
    )


def test_advance_flag_resolves_from_ondisk_media(tmp_path):
    scene, clip, graphic, youtube_lane, web_lane, fake_verifier, neutral_factory = _setup(tmp_path, set_flag_in_verifier=True)
    resolutions = _resolve(scene, youtube_lane, web_lane, fake_verifier, neutral_factory)
    resolution = resolutions[0]
    assert resolution.selected_path == str(clip)
    assert resolution.status == "accepted"
    assert resolution.resolution_kind == "debugger_advance_ondisk_reuse"
    assert resolution.fallback_reason == "debugger_advance:scene_stuck"
    # The flag is consumed once applied -- it cannot leak into a later scene.
    assert is_scene_advance_requested(0) is False
    assert consume_scene_advance(0) is None


def test_without_flag_same_scene_falls_back_to_graphic(tmp_path):
    scene, clip, graphic, youtube_lane, web_lane, fake_verifier, neutral_factory = _setup(tmp_path)
    assert is_scene_advance_requested(0) is False
    resolutions = _resolve(scene, youtube_lane, web_lane, fake_verifier, neutral_factory)
    resolution = resolutions[0]
    assert resolution.selected_path != str(clip)
    assert resolution.resolution_kind in {"graphic_fallback", "neutral_no_claim"}
