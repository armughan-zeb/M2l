from copy import deepcopy
from pathlib import Path

from server.services.rendered_graphics_audit import audit_rendered_graphics, update_graphic_audit
from server.services.delivery_quality import delivery_quality_summary


def scene(index=0, kind="Diagram", variant="cause_and_effect_flow", duration=10, props=None):
    return {"scene_index": index, "start": index * duration, "duration": duration,
            "primary_visual": {"type": kind, "variant": variant, "props": props or {"steps": [{"title": "battery"}, {"title": "motor"}]}},
            "editor_graphic_window": {"start_frame": 30, "duration_frames": 60},
            "graphic": {"template_id": "cause-effect"}}


def manifest(scenes):
    return {"fps": 30, "duration": sum(row["duration"] for row in scenes), "scenes": scenes,
            "metrics": {"graphic_coverage_target": .30}}


def sample(m, graphics, frame=60):
    return {"frame": frame, "graphics_audit_version": 1,
            "graphics_manifest_signature": audit_rendered_graphics(m)["review_signature"], "graphics": graphics}


def test_graphics_window_not_entire_scene_and_overlaps_are_unioned():
    m = manifest([scene(), scene(1)])
    # Simultaneous scenes/graphics do not double-count runtime.
    m["scenes"][1]["start"] = 0
    audit = audit_rendered_graphics(m)
    assert audit["timeline_explanatory_seconds"] == 2
    assert audit["timeline_explanatory_ratio"] == .1
    assert "explanatory_graphic_coverage_below_target" in audit["warnings"]


def test_caption_title_decorative_recipe_does_not_count():
    row = scene(kind="KineticText", props={"text": "November 24"})
    row["graphic"] = {"template_id": "kinetic-statement"}
    row["template_id"] = "generated-recipe"
    row["template_recipe"] = {"layers": [{"kind": "text", "text": "November 24"}, {"kind": "line"}, {"kind": "circle"}]}
    row["caption_words"] = [{"text": "November"}]
    audit = audit_rendered_graphics(manifest([row]))
    assert audit["timeline_explanatory_seconds"] == 0
    assert audit["graphic_events"] == []


def test_grounded_recipe_requires_actor_relationship_not_random_lines():
    row = scene(kind="SmartVideo", props={})
    row["graphic"] = {"template_id": "generated-recipe"}
    row["narration"] = "A battery drives the motor."
    row["template_recipe"] = {"layers": [{"kind": "rect", "semantic_actor": "battery"},
        {"kind": "circle", "semantic_actor": "motor"}, {"kind": "arrow", "semantic_relation": "drives"}]}
    assert audit_rendered_graphics(manifest([row]))["timeline_explanatory_seconds"] == 2
    row["template_recipe"]["layers"][-1]["kind"] = "line"
    assert audit_rendered_graphics(manifest([row]))["timeline_explanatory_seconds"] == 0


def test_empty_chart_or_disabled_graphics_not_counted():
    row = scene(kind="Chart", props={"title": "Battery"})
    row["graphic"] = {"template_id": "bar-chart"}
    assert not audit_rendered_graphics(manifest([row]))["graphic_events"]
    row = scene(); row["editor_track_visibility"] = {"graphics": False}
    assert not audit_rendered_graphics(manifest([row]))["graphic_events"]


def test_layout_fingerprint_ignores_copy_colors_seed_but_honors_variant():
    rows = [scene(index) for index in range(3)]
    for index, row in enumerate(rows):
        row["graphic"]["seed"] = index
        row["primary_visual"]["props"]["color"] = f"#{index:06d}"
        row["primary_visual"]["props"]["steps"][0]["title"] = f"Different label {index}"
    m = manifest(rows)
    assert "same_visible_graphic_treatment" in audit_rendered_graphics(m)["warnings"]
    m["scenes"][1]["primary_visual"]["variant"] = "hierarchical_system_tree"
    assert audit_rendered_graphics(m)["visible_treatment_unique"] == 2


def test_continuous_reveal_and_breathing_shot_not_false_adjacency():
    m = manifest([scene(), scene(1)])
    m["scenes"][1]["continuous_reveal"] = True
    assert "adjacent_visible_graphic_repeat" not in audit_rendered_graphics(m)["warnings"]
    m = manifest([scene(), scene(1, kind="SmartVideo", props={"x": 1}), scene(2)])
    m["scenes"][1]["graphic"] = {"template_id": "full-bleed"}
    assert "adjacent_visible_graphic_repeat" not in audit_rendered_graphics(m)["warnings"]


def test_prominent_exact_copy_counts_its_actual_window_not_scene():
    m = manifest([scene(kind="KineticText", props={"text": "TWENTY SIX SECONDS"})])
    m["scenes"][0]["graphic"] = {"template_id": "kinetic-statement"}
    prominent = {"category": "emphasis", "family": "script_emphasis", "visible": True,
                 "area_ratio": .1, "start_seconds": 1, "end_seconds": 4}
    audit = audit_rendered_graphics(m, [sample(m, [prominent])])
    assert audit["timeline_explanatory_seconds"] == 0
    assert audit["dom_confirmed_emphasis_window_seconds"] == 3
    assert audit["documentary_graphic_window_ratio"] == .3
    assert "explanatory_graphic_coverage_below_target" not in audit["warnings"]
    tiny = {**prominent, "visible": False, "area_ratio": .001}
    assert audit_rendered_graphics(m, [sample(m, [tiny])])["documentary_graphic_window_ratio"] == 0


def test_samples_bound_to_exact_final_design_no_stale_approval():
    m = manifest([scene()])
    review = sample(m, [{"category": "explanatory", "family": "diagram", "visible": True, "area_ratio": .2}])
    m["scenes"][0]["primary_visual"]["variant"] = "physical_cutaway"
    audit = audit_rendered_graphics(m, [review])
    assert audit["sampled_frame_count"] == 0
    assert "graphic_visibility_samples_stale" in audit["warnings"]


def test_final_delivery_prefers_new_audit_over_old_scene_proxy():
    m = manifest([scene(kind="SmartVideo", props={"x": 1})])
    m["scenes"][0]["graphic"] = {"template_id": "full-bleed"}
    m["metrics"]["graphic_scene_duration_ratio"] = 1
    update_graphic_audit(m)
    result = delivery_quality_summary({"creative_review": {"status": "pass", "llm_review_certified": True}}, m)
    assert not result["ready_to_publish"]
    assert any("below" in warning for warning in result["warnings"])
    assert m["scenes"][0]["_graphic_audit"]["family"] == ""


def test_no_stall_or_api_calls_empty_malformed_inputs():
    assert audit_rendered_graphics({})["video_seconds"] == 0
    m = {"scenes": [{"duration": "bad", "primary_visual": None, "graphic": None}]}
    update_graphic_audit(m)
    assert m["metrics"]["rendered_graphics_audit"]["timeline_explanatory_seconds"] == 0


def test_runtime_mounts_markers_and_loaded_glyph_measurement():
    root = Path(__file__).resolve().parents[1]
    composition = (root / "src/remotion/DocumentaryComposition.jsx").read_text(encoding="utf-8")
    dom = (root / "src/remotion/engine/DocumentaryLayoutAudit.jsx").read_text(encoding="utf-8")
    assert "data-graphic-review-signature" in composition
    assert "{...explanatoryAudit}" in composition
    assert "{...emphasisAudit}" in composition
    assert "document.createRange()" in dom
    assert "largestFont / canvasHeight >= .07" in dom
    assert "areaRatio >= .025" in dom
    assert "graphics_manifest_signature" in dom


def test_recipe_coverage_starts_when_explanatory_layer_is_revealed():
    row = scene(kind="SmartVideo", props={"x": 1})
    row["template_id"] = "generated-recipe"
    row["template_recipe"] = {"layers": [{"kind": "diagram", "start_frame": 30, "data": [{"label": "battery"}, {"label": "motor"}]}]}
    assert audit_rendered_graphics(manifest([row]))["timeline_explanatory_seconds"] == 1


def test_bad_optional_audit_never_blocks_export():
    row = scene(); row["primary_visual"]["type"] = []
    m = manifest([row])
    assert update_graphic_audit(m)["warnings"] == ["graphic_audit_unavailable"]


def test_different_variant_names_cannot_disguise_same_rendered_layout():
    rows = [scene(index, variant=f"variant-{index}") for index in range(3)]
    m = manifest(rows)
    reviews = [sample(m, [{"category": "explanatory", "family": "diagram", "visible": True,
        "area_ratio": .1, "layout_fingerprint": "same-boxes-and-font"}], frame=index*300+60) for index in range(3)]
    audit = audit_rendered_graphics(m, reviews)
    assert audit["visible_treatment_unique"] == 3
    assert audit["sampled_visible_treatment_unique"] == 1
    assert "same_rendered_graphic_layout" in audit["warnings"]


def test_planned_graphics_without_visibility_evidence_cannot_certify_export():
    row = scene(); row["editor_graphic_window"]["duration_frames"] = 180
    m = manifest([row]); update_graphic_audit(m)
    result = delivery_quality_summary({"creative_review": {"status": "pass", "llm_review_certified": True}}, m)
    assert not result["ready_to_publish"]
    assert "graphic_visibility_not_sampled" in m["metrics"]["rendered_graphics_audit"]["warnings"]


def test_component_no_data_recovery_cannot_satisfy_graphic_target():
    row = scene(); row["primary_visual"]["props"]["no_data"] = True
    assert audit_rendered_graphics(manifest([row]))["timeline_explanatory_seconds"] == 0
    row["primary_visual"] = {"type": "Chart", "props": {"no_data": True, "data": [{"label": "A", "value": 1}, {"label": "B", "value": 2}]}}
    row["graphic"] = {"template_id": "bar-chart"}
    assert not audit_rendered_graphics(manifest([row]))["graphic_events"]
