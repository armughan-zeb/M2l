import pytest

from server.services.edit_variation_audit import audit_edit_variation
from server.services.editing_libraries import library_manifest
from server.services.editing_profile import classify_niche, deterministic_profile, normalize_profile
from server.services.editorial_director.contract import ALLOWED_GENRES
from server.services.editorial_director.visual_dna import detect_genre, generate_visual_dna
from server.services.editorial_director.visual_critic import audit_manifest
from server.services.semantic_edit_engine import build_semantic_edit_manifest
from server.services.remotion_renderer import assert_semantic_edit_quality, semantic_render_required
from server.services.sound_library import _commercial_sound_license
from server.services import native_render_policy


def test_niche_profiles_are_reproducible_and_visually_distinct():
    history_a = deterministic_profile("RMS Titanic history", script="An archive records the voyage.", variation_seed=8)
    history_b = deterministic_profile("RMS Titanic history", script="An archive records the voyage.", variation_seed=8)
    finance = deterministic_profile("The stock market crash", script="Shares fell rapidly.", variation_seed=8)
    assert history_a == history_b
    assert history_a["niche"] == "history"
    assert finance["niche"] == "finance"
    assert history_a["palette"] != finance["palette"]
    assert history_a["typography"] != finance["typography"]


def test_script_niches_have_distinct_visual_grammars_and_genre_presets():
    crime = deterministic_profile("Court evidence revisits a missing-person case", category="crime", variation_seed=4)
    sports = deterministic_profile("Championship final tactics", category="sports", variation_seed=4)
    gaming = deterministic_profile("A new open-world game", category="gaming", variation_seed=4)

    assert crime["niche"] == "crime"
    assert sports["niche"] == "sports"
    assert gaming["niche"] == "gaming"
    assert crime["visual_grammar"] != sports["visual_grammar"]
    assert sports["visual_grammar"] != gaming["visual_grammar"]
    assert detect_genre("Championship final", "sports") == "sports-documentary"
    assert detect_genre("A new open-world game", "gaming") == "gaming-blockbuster"
    assert classify_niche("A landmark sports car", script="The engine reshaped automotive design.") == "automotive"
    assert detect_genre("A landmark sports car", script="The engine reshaped automotive design.") == "automotive-documentary"

    for genre in (
        "sports-documentary", "gaming-blockbuster", "true-crime", "travelogue",
        "politics-explainer", "automotive-documentary", "biographical-essay",
        "cultural-chronicle", "health-science",
    ):
        dna = generate_visual_dna("Subject", "general", "A grounded documentary beat.", genre_override=genre)
        assert dna.to_dict()["genre"] == genre
        assert genre in ALLOWED_GENRES


def test_explicit_niche_wins_over_conflicting_raw_profile():
    fallback = deterministic_profile("Championship final", category="sports", variation_seed=2)
    normalized = normalize_profile({"niche": "gaming"}, fallback)

    assert normalized["niche"] == "sports"
    assert normalized["visual_grammar"] == fallback["visual_grammar"]


def test_super_bowl_script_is_sports_and_inferred_general_profile_cannot_mask_it():
    script = (
        "Super Bowl XLIX has 26 seconds left. Seattle trails New England 28–24. "
        "The Seahawks are at the Patriots' one-yard line: second-and-goal."
    )
    assert classify_niche("Super Bowl XLIX", script=script) == "sports"
    fallback = deterministic_profile("Super Bowl XLIX", script=script, variation_seed=19)
    normalized = normalize_profile(
        {"niche": "general", "niche_locked": False, "niche_source": "script_classification"},
        fallback,
    )
    assert normalized["niche"] == "sports"
    assert normalized["visual_grammar"]["editorial_logic"].startswith("Follow the contest")


def test_pipeline_profile_seed_is_local_and_shared_before_llm_direction():
    from server.app import _run_editing_profile

    profile = _run_editing_profile(
        "A championship changes the team's season",
        "sports",
        "The match turns on a late defensive change.",
        {"semantic_variation_seed": 23},
    )
    assert profile["niche"] == "sports"
    assert profile["seed"]
    assert profile["visual_grammar"]["editorial_logic"].startswith("Follow the contest")


def test_remotion_director_timeline_audit_checks_scene_shots_and_motion_cues():
    manifest = {
        "edit_plan_schema_version": 4,
        "timeline_contract_version": 1,
        "fps": 30,
        "scenes": [
            {
                "start": 0.0, "duration": 2.0,
                "narration": "The first launch changes the race.",
                "primary_visual": {"type": "SmartVideo", "variant": "full_bleed"},
                "motion_cues": [{"element": "image", "action": "reveal", "anchor_text": "first launch", "time_local": 0.2, "duration_seconds": 0.6}],
                "media": {"shots": [{"shot_start": 0.0, "shot_end": 2.0, "src": "launch.mp4"}]},
            },
            {
                "start": 2.0, "duration": 3.0,
                "narration": "The team responds with a new formation.",
                "primary_visual": {"type": "SmartImage", "variant": "full_bleed_cinematic"},
                "motion_cues": [],
            },
        ],
    }
    good = audit_manifest(manifest)
    assert good["status"] == "pass"
    assert good["timeline"]["duration_seconds"] == 5.0
    assert good["timeline"]["shots_checked"] == 1
    assert good["timeline"]["motion_cues_checked"] == 1

    manifest["scenes"][1]["start"] = 1.5
    manifest["scenes"][0]["motion_cues"][0]["time_local"] = 1.7
    manifest["scenes"][0]["motion_cues"][0]["duration_seconds"] = 0.6
    bad = audit_manifest(manifest)
    bad_codes = {finding["code"] for finding in bad["findings"]}
    assert bad["status"] == "fail"
    assert "timeline_scene_overlap" in bad_codes
    assert "timeline_motion_cue_out_of_bounds" in bad_codes

    missing_start = {
        "timeline_contract_version": 1,
        "fps": 30,
        "scenes": [{
            "duration": 2.0,
            "narration": "A final scene lands.",
            "primary_visual": {"type": "SmartImage", "variant": "full_bleed_cinematic"},
        }],
    }
    missing = audit_manifest(missing_start)
    assert missing["status"] == "fail"
    assert any(item["code"] == "timeline_absolute_timing_missing" for item in missing["findings"])


def test_untrusted_llm_profile_values_are_normalized_to_registry():
    fallback = deterministic_profile("Historic archive", variation_seed=3)
    result = normalize_profile({
        "overlay_pack": "javascript:alert(1)",
        "transition_pack": "random-web-transition",
        "typography": {"display": "missing-font", "body": "poppins"},
        "palette": {"accent": "not-a-color", "background": "#010203"},
        "sound_density": 9,
        "source": "groq",
    }, fallback)
    assert result["overlay_pack"] == fallback["overlay_pack"]
    assert result["transition_pack"] == fallback["transition_pack"]
    assert result["typography"]["display"] == fallback["typography"]["display"]
    assert result["typography"]["body"] == fallback["typography"]["body"]
    assert result["palette"]["accent"] == fallback["palette"]["accent"]
    assert result["palette"]["background"] == "#010203"
    assert result["sound_density"] == 1


def test_manifest_carries_editing_dna_and_reuses_vision_hints():
    manifest = build_semantic_edit_manifest(
        ["Notice the warning in the wireless log.", "The share rose to 73 percent."],
        [3, 3],
        topic="RMS Titanic history",
        media=[
            {"path": "C:/one.jpg", "visual_decision": {
                "subject_bbox": [.55, .18, .75, .4],
                "safe_text_regions": [[.05, .1, .35, .4]],
                "status": "accepted",
                "confidence": 95,
            }},
            {"path": "C:/two.jpg"},
        ],
        editorial_beats=[
            {"role": "evidence", "emphasis": "warning"},
            {"role": "evidence", "treatment": "statistic", "emphasis": "73 percent"},
        ],
        variation_seed=11,
    )
    assert manifest["editing_profile"]["niche"] == "history"
    assert manifest["metrics"]["design_fingerprint"]
    assert manifest["scenes"][0]["design"]["vision_reused"] is True
    assert manifest["scenes"][0]["design"]["focal_bbox"] == [.55, .18, .75, .4]
    assert manifest["scenes"][0]["design"]["safe_text_regions"]
    assert manifest["scenes"][1]["graphic"]["template_id"] in {
        "donut-chart", "pictogram-stat", "stat-counter",
    }


def test_library_is_deep_enough_for_project_level_variation():
    libraries = library_manifest()
    assert len(libraries["fonts"]) >= 15
    assert len(libraries["overlays"]) >= 8
    assert len(libraries["transition_packs"]) >= 7
    assert len(libraries["image_treatments"]) >= 12
    assert len(libraries["text_styles"]) >= 12
    assert len(libraries["graphs"]) >= 9
    assert sum(len(pack) for pack in libraries["sfx_packs"].values()) >= 60


def test_variation_audit_detects_channel_collision_and_repetitive_design():
    scenes = [{
        "graphic": {"template_id": "keyword-reveal", "variant": 0},
        "transition": {"template_id": "dissolve"},
        "design": {"image_treatment": "hold"},
        "atmosphere": {"layers": ["grain"]},
        "sound_cues": [{"kind": "impact"}],
    } for _ in range(10)]
    result = audit_edit_variation(scenes, design_fingerprint="same", history_fingerprints=["same"])
    assert result["status"] == "needs_regeneration"
    assert result["channel_fingerprint_collision"] is True
    assert "adjacent_template_repeat" in result["warnings"]


def test_semantic_render_is_required_and_repetitive_manifest_is_blocked(monkeypatch):
    monkeypatch.setenv("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
    monkeypatch.setenv("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
    assert semantic_render_required({"semantic_render_enabled": True}) is True
    repetitive = build_semantic_edit_manifest(
        [f"Scene {index}" for index in range(10)],
        [2] * 10,
        topic="Test",
    )
    repetitive["metrics"]["variation_audit"] = {
        "status": "needs_regeneration",
        "warnings": ["image_treatment_concentration:hold"],
    }
    try:
        assert_semantic_edit_quality(repetitive, {'semantic_variation_required': True})
    except RuntimeError as exc:
        assert "variation gate" in str(exc)
    else:
        raise AssertionError("Repetitive semantic edit should not render")


def test_required_remotion_render_fails_closed_instead_of_downgrading(monkeypatch, tmp_path):
    def fail(**_kwargs):
        raise RuntimeError("synthetic remotion failure")

    monkeypatch.setattr(native_render_policy, "render_semantic_documentary_remotion", fail)
    from server.services import render_resource_manager
    monkeypatch.setattr(render_resource_manager, 'prepare_render_resources', lambda **kwargs: {'recommended_workers':1})
    with pytest.raises(RuntimeError, match="legacy fallback is disabled"):
        native_render_policy.render_semantic_documentary(
            {"scenes": []},
            tmp_path / "should-not-render.mp4",
            {"render_backend": "remotion", "semantic_render_required": True},
        )


def test_freesound_license_gate_rejects_noncommercial_and_unknown():
    assert _commercial_sound_license("Creative Commons 0")
    assert _commercial_sound_license("Attribution 4.0")
    assert not _commercial_sound_license("Attribution NonCommercial 4.0")
    assert not _commercial_sound_license("Unknown")
