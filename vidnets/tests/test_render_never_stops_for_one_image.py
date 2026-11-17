"""Render resilience: one bad image must never hold the whole render.

Regression case: Berlin Wall job 55e43a67, scene 2. The top web candidate
(Schabowski press conference photo, sha acd3897a...) was vision-verified,
then rejected at a late gate for an ownership watermark ("© AFP via Getty
Images"). The ranked alternates were already gone, so the plan fell to a
terminal grounded graphic -- but the shot kept the photo's verified digest.
The render gate then hard-failed the whole job with
``verified_shot_changed_after_review`` because the digest did not match the
emergency frame file on disk.

The fix under test, in three places:
  1. ``production_contract.repair_media_defects`` -- a single-scene media
     defect swaps in the next ranked alternate when one exists, else
     converts the shot to a clean graphic re-stamped to the file's real
     hash, counted against a small per-video graphic allowance. The render
     continues; only a scene with no usable media at all, 0 real-media
     scenes overall, or a non-media defect still fails it.
  2. ``pipeline_v2_media_adapter.validate_media_plan_for_render_v2`` -- a
     late-gate rejection tries the scene's next-best ranked alternate
     before borrowing or falling to a graphic, and the terminal-graphic
     branch no longer leaves the rejected photo's digest on the row.
  3. ``media_resolution_coordinator`` pre-stock web rescue -- the ranked
     candidates are walked best-first and the first ownership-clean one is
     taken, instead of blindly taking [0].
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from services.editorial_director.production_contract import (
    audit_production_requirements,
    compile_production_timeline,
    repair_media_defects,
)
from services.media_delivery_gate import validate_documentary_coverage
from services.pipeline_contracts import MediaContractError

# Saved evidence from the Berlin Wall regression (job 55e43a67). The .orig
# plan is the edit plan as it stood when the render gate failed it.
BERLIN_ORIG = Path(
    "/home/hatch/workspace/vidnets-runtime/output"
    "/Berlin_Wall_Fall_5Min_20261115_141725_b53e995c"
    "/Berlin_Wall_Fall_5Min_remotion_edit_plan.json.orig"
)
BERLIN_PHOTO_SHA = "acd3897a53fc284d9bec0d80ce429c5cb6912cb0d07374f5ce49c0151b0de8d8"
BERLIN_GRAPHIC_SHA = "3a66097ef582b005e78e3bb234cfe2ebb48a5157ad34d1fa0589a07b5e650e84"


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _healthy_scene(path: Path, *, narration: str = "The wall stood for decades."):
    sha = _sha(path)
    return {
        "start": 0,
        "duration": 3,
        "narration": narration,
        "primary_visual": {"type": "SmartImage"},
        "media": {
            "src": str(path),
            "type": "image",
            "shots": [{
                "path": str(path),
                "asset_path": str(path),
                "media_type": "image",
                "type": "image",
                "verification": "verified",
                "verification_status": "verified",
                "verified_asset_sha256": sha,
                "asset_sha256": sha,
                "asset_fingerprint": sha,
                "visual_decision": {
                    "accepted": True,
                    "visual_checked": True,
                    "verified_asset_sha256": sha,
                },
            }],
        },
    }


def _defective_scene(path: Path, *, photo_sha: str, alternates=None,
                     narration: str = "Schabowski reads the new travel rules."):
    """A Berlin Wall scene-2 shaped shot: graphic file, photo digest."""
    file_sha = _sha(path)
    scene = {
        "start": 0,
        "duration": 3,
        "narration": narration,
        "primary_visual": {"type": "SmartImage"},
        "media": {
            "src": str(path),
            "type": "graphic",
            "is_graphic_fallback": True,
            "shots": [{
                "path": str(path),
                "asset_path": str(path),
                "media_type": "image",
                "type": "image",
                "source": "graphic",
                "candidate_id": "graphic:grounded_terminal:2:scn_a218307e3f46242c8531",
                "fallback_layer": "terminal_grounded_graphic_after_real_media_exhausted:ownership_watermark_detected",
                "verification": "grounded_graphic_fallback",
                "verification_status": "grounded_graphic_fallback",
                "verified_asset_sha256": photo_sha,
                "asset_sha256": file_sha,
                "asset_fingerprint": file_sha,
                "visual_decision": {
                    "candidate_id": "web:scn_a218307e3f46242c8531",
                    "accepted": True,
                    "visual_checked": True,
                    "semantic_verified": True,
                    "verified_asset_sha256": photo_sha,
                },
            }],
        },
    }
    if alternates is not None:
        scene["primary_visual"]["asset"] = {"alternates": alternates}
    return scene


# ---------------------------------------------------------------------------
# 1. Render-gate repair (production_contract)
# ---------------------------------------------------------------------------

def test_berlin_wall_scene2_real_plan_repairs_and_renders():
    """The real saved scene 2: digest mismatch must repair, not fail."""
    if not BERLIN_ORIG.is_file():
        pytest.skip("Berlin Wall job output not on this machine")
    plan = json.loads(BERLIN_ORIG.read_text(encoding="utf-8"))
    scene2 = copy.deepcopy(plan["scenes"][2])
    # The defect is really there before the repair.
    before = audit_production_requirements({"scenes": [scene2]})
    assert before["status"] == "fail"
    assert any(
        error.get("code") == "verified_shot_changed_after_review"
        for error in before["errors"]
    )
    manifest = {"fps": 30, "scenes": [scene2]}
    result = repair_media_defects(manifest)
    assert result["audit"]["status"] == "pass"
    assert result["audit"]["errors"] == []
    assert result["substitutions"] == [{
        "scene_index": 0,
        "code": "verified_shot_changed_after_review",
        "from": str(scene2["media"]["shots"][0]["path"]),
        "to": "graphic",
        "path": str(scene2["media"]["shots"][0]["path"]),
    }]
    assert result["graphic_scenes"] == 1
    assert result["allowance_exceeded"] is False
    # The stale photo digest is gone: the shot now verifies against the
    # emergency frame file that is actually there.
    shot = manifest["scenes"][0]["media"]["shots"][0]
    assert shot["verified_asset_sha256"] == BERLIN_GRAPHIC_SHA
    assert shot["visual_decision"]["verified_asset_sha256"] == BERLIN_GRAPHIC_SHA


def test_berlin_wall_scene2_compiles_to_passing_audit():
    """End to end: compile stores a passing audit for the repaired scene."""
    if not BERLIN_ORIG.is_file():
        pytest.skip("Berlin Wall job output not on this machine")
    plan = json.loads(BERLIN_ORIG.read_text(encoding="utf-8"))
    scene2 = copy.deepcopy(plan["scenes"][2])
    manifest = compile_production_timeline({"fps": 30, "scenes": [scene2]})
    assert manifest["production_audit"]["status"] == "pass"
    assert manifest["media_substitutions"]
    assert manifest["graphic_allowance"]["graphic_scenes"] == 1


def test_single_scene_digest_mismatch_substitutes_alternate_and_continues(tmp_path):
    graphic = tmp_path / "emergency.jpg"
    graphic.write_bytes(b"emergency graphic bytes")
    alternate = tmp_path / "bundesarchiv.jpg"
    alternate.write_bytes(b"watermark-free bundesarchiv photo bytes")
    alt_sha = _sha(alternate)
    scene = _defective_scene(
        graphic,
        photo_sha="deadbeef" * 8,
        alternates=[{
            "path": str(alternate),
            "candidate_id": "web:bundesarchiv-1",
            "visual_decision": {
                "accepted": True,
                "visual_checked": True,
                "verified_asset_sha256": alt_sha,
            },
        }],
    )
    healthy = _healthy_scene(_write(tmp_path, "good.jpg", b"good photo bytes"))
    manifest = {"scenes": [healthy, scene]}
    result = repair_media_defects(manifest)
    assert result["audit"]["status"] == "pass"
    assert result["substitutions"][0]["to"] == "alternate"
    assert result["substitutions"][0]["path"] == str(alternate)
    repaired_shot = manifest["scenes"][1]["media"]["shots"][0]
    assert repaired_shot["path"] == str(alternate)
    assert repaired_shot["verified_asset_sha256"] == alt_sha
    assert repaired_shot["visual_decision"]["verified_asset_sha256"] == alt_sha
    assert result["graphic_scenes"] == 0


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_single_scene_digest_mismatch_falls_to_graphic_and_continues(tmp_path):
    graphic = _write(tmp_path, "emergency.jpg", b"emergency graphic bytes")
    healthy = _healthy_scene(_write(tmp_path, "good.jpg", b"good photo bytes"))
    scene = _defective_scene(graphic, photo_sha="deadbeef" * 8)
    manifest = {"scenes": [healthy, scene]}
    result = repair_media_defects(manifest)
    assert result["audit"]["status"] == "pass"
    assert result["substitutions"][0]["to"] == "graphic"
    shot = manifest["scenes"][1]["media"]["shots"][0]
    assert shot["verified_asset_sha256"] == _sha(graphic)
    assert shot["visual_decision"]["verified_asset_sha256"] == _sha(graphic)
    assert shot["verification_status"] == "graphic_fallback"
    assert result["graphic_scenes"] == 1


def test_few_graphics_allowed_render_completes(tmp_path):
    scenes = [_healthy_scene(_write(tmp_path, "good.jpg", b"good photo bytes"))]
    for i in range(3):
        graphic = _write(tmp_path, f"emergency-{i}.jpg", f"emergency graphic {i}".encode())
        scenes.append(_defective_scene(graphic, photo_sha="deadbeef" * 8))
    manifest = {"scenes": scenes}
    result = repair_media_defects(manifest)
    assert result["audit"]["status"] == "pass"
    assert len(result["substitutions"]) == 3
    assert result["graphic_scenes"] == 3
    assert result["allowance_exceeded"] is False
    assert not any(
        warning.get("code") == "graphic_allowance_exceeded"
        for warning in result["audit"]["warnings"]
    )


def test_graphic_allowance_exceeded_still_renders_with_warning(tmp_path):
    scenes = [_healthy_scene(_write(tmp_path, "good.jpg", b"good photo bytes"))]
    for i in range(6):
        graphic = _write(tmp_path, f"emergency-{i}.jpg", f"emergency graphic {i}".encode())
        scenes.append(_defective_scene(graphic, photo_sha="deadbeef" * 8))
    manifest = {"scenes": scenes}
    result = repair_media_defects(manifest)
    # Beyond the allowance the video still renders -- flagged, not failed.
    assert result["audit"]["status"] == "pass"
    assert result["graphic_scenes"] == 6
    assert result["allowance_exceeded"] is True
    assert any(
        warning.get("code") == "graphic_allowance_exceeded"
        for warning in result["audit"]["warnings"]
    )


def test_non_media_defect_still_fails(tmp_path):
    """A map the narration demands but no scene routes must still fail."""
    healthy = _healthy_scene(_write(tmp_path, "good.jpg", b"good photo bytes"))
    map_scene = _healthy_scene(
        _write(tmp_path, "map.jpg", b"map photo bytes"),
        narration="Watch the route unfold on the map below.",
    )
    manifest = {"scenes": [healthy, map_scene]}
    result = repair_media_defects(manifest)
    assert result["audit"]["status"] == "fail"
    assert any(
        error.get("code") == "required_map_not_routed"
        for error in result["audit"]["errors"]
    )
    assert result["substitutions"] == []


def test_zero_real_media_scenes_still_hard_fails(tmp_path):
    """The 0-real rule is untouched: all-graphics still hard-fails."""
    g1 = _write(tmp_path, "g1.jpg", b"graphic one")
    g2 = _write(tmp_path, "g2.jpg", b"graphic two")
    plan = {
        "media_paths": [str(g1), str(g2)],
        "source_labels": ["Grounded Graphic Fallback", "Grounded Graphic Fallback"],
        "metadata": {"media_resolution_v2": [
            {"provider": "graphic", "resolution_kind": "grounded_graphic_fallback"},
            {"provider": "graphic", "resolution_kind": "grounded_graphic_fallback"},
        ]},
    }
    with pytest.raises(MediaContractError):
        validate_documentary_coverage(plan, durations=[3.0, 3.0])


def test_partial_real_media_delivers_with_advisory(tmp_path):
    """Partial-real still delivers (advisory only) -- the b6fb05d rule."""
    real = _write(tmp_path, "real.jpg", b"real photo bytes")
    graphic = _write(tmp_path, "g.jpg", b"graphic bytes")
    plan = {
        "media_paths": [str(real), str(graphic)],
        "source_labels": ["Web Images", "Grounded Graphic Fallback"],
        "metadata": {"media_resolution_v2": [
            {"provider": "web"},
            {"provider": "graphic", "resolution_kind": "grounded_graphic_fallback"},
        ]},
    }
    summary = validate_documentary_coverage(plan, durations=[3.0, 3.0])
    assert summary["passed"] is True
    assert summary["emergency_scene_indices"] == [1]
    assert summary["warnings"]


# ---------------------------------------------------------------------------
# 2. Late-gate alternates in the final-handoff adapter
# ---------------------------------------------------------------------------

def _adapter_row(path: Path, *, requirement_hash: str, alternates=None):
    decision = {
        "accepted": True,
        "visual_checked": True,
        "visual_available": True,
        "verified_requirement_hash": requirement_hash,
        "verified_asset_sha256": _sha(path),
        "source_presentation_type": "photograph",
    }
    row = {
        "provider": "web",
        "path": str(path),
        "selected_path": str(path),
        "status": "accepted",
        "verification_status": "verified",
        "requirement_hash": requirement_hash,
        "visual_decision": decision,
    }
    if alternates is not None:
        row["alternates"] = alternates
    return row


def test_watermarked_top_candidate_falls_to_next_ranked_alternate(tmp_path, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    watermarked = _write(tmp_path, "photo.jpg", b"watermarked photo bytes")
    alternate = _write(tmp_path, "alt.jpg", b"clean alternate bytes")
    graphic = _write(tmp_path, "graphic.png", b"generated fallback bytes")
    alt_sha = _sha(alternate)
    requirement = {"canonical_entity": "Berlin Wall", "strict_identity": False,
                   "requirement_hash": "scene-b"}
    row = _adapter_row(watermarked, requirement_hash="scene-b", alternates=[{
        "path": str(alternate),
        "candidate_id": "web:alt-1",
        "visual_decision": {
            "accepted": True,
            "visual_checked": True,
            "verified_asset_sha256": alt_sha,
            "verified_requirement_hash": "scene-b",
            "source_presentation_type": "photograph",
        },
    }])
    monkeypatch.setattr(
        adapter, "_validate_renderable_asset", lambda path: (True, "ok", "image"))
    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": str(path) == str(watermarked)})
    plan = {"media_paths": [str(watermarked)], "source_labels": ["Web Images"],
            "metadata": {"media_resolution_v2": [row]}}
    result = adapter.validate_media_plan_for_render_v2(
        plan,
        [{"scene_id": "scene-0", "text": "The Berlin Wall fell.",
          "visual_requirement": requirement}],
        render_resolution=(1920, 1080), title="Berlin Wall",
        allow_emergency_media=True,
        allow_grounded_graphics=True,
        neutral_asset_factory=lambda *a, **k: str(graphic),
    )
    assert result["media_paths"] == [str(alternate)]
    assert result["source_labels"] == ["Web Images"]
    new_row = result["metadata"]["media_resolution_v2"][0]
    assert new_row["resolution_kind"] == "late_gate_alternate"
    assert new_row["visual_decision"]["verified_asset_sha256"] == alt_sha
    assert result["metadata"]["late_gate_substitutions"][0]["to"] == str(alternate)


def test_terminal_graphic_cleans_stale_photo_digest(tmp_path, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    watermarked = _write(tmp_path, "photo.jpg", b"watermarked photo bytes")
    graphic = _write(tmp_path, "graphic.png", b"generated fallback bytes")
    graphic_sha = _sha(graphic)
    requirement = {"canonical_entity": "Berlin Wall", "strict_identity": False,
                   "requirement_hash": "scene-b"}
    row = _adapter_row(watermarked, requirement_hash="scene-b")
    monkeypatch.setattr(
        adapter, "_validate_renderable_asset", lambda path: (True, "ok", "image"))
    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": str(path) == str(watermarked)})
    plan = {"media_paths": [str(watermarked)], "source_labels": ["Web Images"],
            "metadata": {"media_resolution_v2": [row]}}
    result = adapter.validate_media_plan_for_render_v2(
        plan,
        [{"scene_id": "scene-0", "text": "The Berlin Wall fell.",
          "visual_requirement": requirement}],
        render_resolution=(1920, 1080), title="Berlin Wall",
        allow_emergency_media=True,
        allow_grounded_graphics=True,
        neutral_asset_factory=lambda *a, **k: str(graphic),
    )
    # The graphic is used -- and the rejected photo's digest is gone.
    assert result["media_paths"] == [str(graphic)]
    new_row = result["metadata"]["media_resolution_v2"][0]
    assert new_row["provider"] == "graphic"
    assert new_row["asset_fingerprint"] == graphic_sha
    assert new_row["verified_asset_sha256"] == graphic_sha
    assert new_row["visual_decision"]["verified_asset_sha256"] == graphic_sha
    assert new_row["visual_decision"]["verification_status"] == "graphic_fallback"


# ---------------------------------------------------------------------------
# 3. Pre-stock web rescue skips the watermarked top candidate
# ---------------------------------------------------------------------------

def test_pre_stock_rescue_skips_watermarked_top_candidate(tmp_path, monkeypatch):
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    top = _write(tmp_path, "top.jpg", b"top watermarked photo")
    second = _write(tmp_path, "second.jpg", b"clean second photo")

    def verify(_scene, _candidate):
        # The main gate rejects everything, so the scene degrades and the
        # pre-stock rescue runs.
        return {"accepted": False, "status": "visual_rejected", "reason": "wrong subject"}

    def web_lane(_scene, _cancel_event):
        return [
            {"path": str(top), "source": "web", "title": "Schabowski press conference",
             "score": 95, "metadata_score": 95, "final_score": 95},
            {"path": str(second), "source": "web", "title": "Schabowski press conference",
             "score": 90, "metadata_score": 90, "final_score": 90},
        ]

    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": str(path) == str(top)})

    result = resolve_storyboard_media_v2(
        [{"scene_index": 0, "scene_id": "berlin-2",
          "text": "Schabowski announces the new travel rules."}],
        youtube_lane=lambda *_args: [],
        web_lane=web_lane,
        visual_verifier=verify,
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
    )
    resolution = result[0]
    assert resolution.selected_path == str(second)
    assert resolution.resolution_kind == "pre_stock_web_rescue"
    assert any(
        attempt.get("stage") == "pre_stock_web_rescue"
        and attempt.get("status") == "ownership_watermark_detected"
        for attempt in resolution.attempts
    )
