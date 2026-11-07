"""Quality contract for the script-driven Remotion visual system."""

from __future__ import annotations

import re
from pathlib import Path

from server.services.editorial_cue_planner import plan_editorial_cues
from server.services.semantic_edit_engine import (
    NATIVE_TEMPLATE_MAP,
    TEMPLATE_REGISTRY,
    _graphic_props,
    build_semantic_edit_manifest,
)
from server.services.editorial_director.manager import apply_manager_corrections, manager_review
from server.services.editorial_director.visual_critic import _frame_metrics, audit_frames, audit_manifest
from server.services.editorial_director.media_director import (
    annotate_media_compositions,
    audit_media_compositions,
    plan_media_composition,
)
from server.services.editorial_director.media_first import audit_editorial_contracts
from server.services.editorial_director.swarm import run_documentary_swarm
from server.services.highlight_selector import highlight_tokens_for, select_grounded_highlight
from server.services.editorial_director.director import _build_stat_card_props, _extract_numbers_from_text
from server.services.editorial_director.script_analyzer import analyze_sentence_triggers


MODERN_FONT_FILES = {
    "Inter-SemiBold.ttf",
    "DMSans-Bold.ttf",
    "PlusJakartaSans-Bold.ttf",
    "Oswald-SemiBold.ttf",
    "ArchivoBlack-Regular.ttf",
    "JetBrainsMono-Bold.ttf",
    "Lora-Bold.ttf",
}


def _words(value: str) -> set[str]:
    return {
        token.lower().strip(".,!?;:'\"()[]")
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9,'/-]*", str(value or ""))
        if token
    }


def test_south_america_does_not_trigger_a_false_united_states_map():
    triggers = analyze_sentence_triggers(
        "At Panama’s narrow crossing, ships once faced a detour around South America.",
        is_first_scene=True,
    )

    assert triggers.countries == ["PAN"]


def test_stat_card_keeps_script_grounded_ratio_denominator_and_subject():
    narration = "The thieves emptied 123 of the center’s 160 safes."
    props = _build_stat_card_props(
        "stat_with_context",
        narration,
        _extract_numbers_from_text(narration),
        None,
    )

    assert props["value"] == 123
    assert props["label"] == "OF 160 SAFES"

    unrelated_count = _build_stat_card_props(
        "stat_with_context",
        "123 safes were emptied; the center had 160 in total.",
        _extract_numbers_from_text("123 safes were emptied; the center had 160 in total."),
        None,
    )
    assert unrelated_count["label"] == "KEY METRIC"


def test_low_information_frame_failure_has_actionable_finding(tmp_path):
    from PIL import Image

    blank = tmp_path / "blank.jpg"
    Image.new("RGB", (320, 180), (0, 0, 0)).save(blank)

    report = audit_frames([blank])

    assert report["status"] == "fail"
    finding = next(item for item in report["findings"] if item["code"] == "low_information_frames")
    assert finding["severity"] == "error"
    assert finding["frame_indices"] == [1]


def test_detailed_night_frame_is_not_rejected_as_blank(tmp_path):
    from PIL import Image, ImageDraw

    path = tmp_path / "lit-structure.png"
    image = Image.new("RGB", (960, 540), (0, 0, 20))
    draw = ImageDraw.Draw(image)
    for x in range(120, 840, 48):
        draw.line((x, 40, x + 70, 520), fill=(225, 160, 35), width=9)
    image.save(path)

    metrics = _frame_metrics(path)
    assert metrics["mean_luma"] < 0.16
    assert metrics["dark_fraction"] > 0.58
    assert metrics["excessively_dark"] is False


def test_every_registered_motion_graphic_path_has_a_native_adapter_and_props():
    sample = "On April 15, 1912, London received a warning about 1,500 people and two routes."
    beat = {"role": "evidence", "treatment": "document", "emphasis": "London"}

    for item in TEMPLATE_REGISTRY:
        template_id = item["id"]
        props = _graphic_props(template_id, sample, beat, "Titanic", focal_bbox=[.1, .1, .4, .4])
        assert isinstance(props, dict), template_id
        if template_id != "full-bleed":
            assert template_id in NATIVE_TEMPLATE_MAP, template_id


def test_script_beats_produce_grounded_text_modern_fonts_timing_and_variety():
    texts = [
        "In 1912, the ship left Southampton.",
        "The route connected Southampton to New York across the North Atlantic.",
        "More than 1,500 people were aboard.",
        "Radio operators received two warnings.",
        "A decision caused the response to arrive too late.",
        "A witness said the sea was calm until the final minute.",
        "The archive revealed the missing message in 1985.",
        "The route crossed the North Atlantic again.",
    ]
    beats = [
        {"role": "evidence", "treatment": "date"},
        {"role": "location", "treatment": "map"},
        {"role": "evidence", "treatment": "counter"},
        {"role": "evidence", "treatment": "signal"},
        {"role": "comparison", "treatment": "decision"},
        {"role": "quote", "treatment": "quote"},
        {"role": "evidence", "treatment": "document"},
        {"role": "location", "treatment": "map"},
    ]
    manifest = build_semantic_edit_manifest(
        texts,
        [3.0] * len(texts),
        editorial_beats=beats,
        topic="Titanic history",
        category="history",
        config={"graphic_density": 0.9},
        variation_seed=19,
    )

    assert manifest["editing_profile"]["typography"]["body"] in {"inter", "dm_sans", "plus_jakarta"}
    assert manifest["metrics"]["variation_audit"]["status"] == "pass"
    assert manifest["metrics"]["variation_audit"]["unique_visual_grammars"] >= 6

    graphic_motion = []
    graphic_templates = []
    for index, scene in enumerate(manifest["scenes"]):
        graphic = scene["graphic"]
        scene_start = float(scene.get("start", 0.0))
        scene_end = scene_start + float(scene["duration"])
        assert scene_start <= float(graphic.get("start", scene_start)) <= float(graphic.get("end", scene_end))
        assert float(graphic.get("end", scene_end)) <= scene_end + 0.01
        if graphic["template_id"] == "full-bleed":
            continue
        graphic_templates.append(graphic["template_id"])
        props = graphic.get("props") or {}
        contract_text = props.get("_contract_text")
        if contract_text:
            assert _words(contract_text) & _words(texts[index]), (index, contract_text, texts[index])
        if props.get("_contract_font"):
            assert props["_contract_font"] in MODERN_FONT_FILES
        if props.get("_position"):
            assert props["_position"] in {"top_left", "top_right", "center", "bottom_left", "bottom_right"}
        if props.get("_entry_motion"):
            graphic_motion.append(props["_entry_motion"])

    assert len(set(graphic_templates)) >= 5
    assert all(left != right for left, right in zip(graphic_motion, graphic_motion[1:]))
    assert "1912" in " ".join(str(scene["graphic"].get("props", {}).get("_contract_text", "")) for scene in manifest["scenes"])
    assert "1,500" in " ".join(str(scene["graphic"].get("props", {}).get("_contract_text", "")) for scene in manifest["scenes"])
    timeline_visuals = [
        scene["primary_visual"]
        for scene in manifest["scenes"]
        if scene.get("primary_visual", {}).get("type") == "Timeline"
        and scene.get("primary_visual", {}).get("variant") == "date_flip_calendar"
    ]
    assert timeline_visuals
    assert all(visual["props"].get("year") or visual["props"].get("date") for visual in timeline_visuals)


def test_timed_cues_use_exact_script_text_and_safe_non_overlapping_placement():
    text = "Number 1: Tower Climber employs 2,200 workers in London."
    tokens = text.split()
    words = [
        {"index": index, "text": token, "start_seconds": 0.3 + index * 0.25, "end_seconds": 0.48 + index * 0.25, "confidence": 0.99}
        for index, token in enumerate(tokens)
    ]
    scene = {
        "scene_id": "scene-1",
        "container_id": "item-1",
        "text": text,
        "char_start": 0,
        "char_end": len(text),
        "token_start": 0,
        "token_end": len(tokens),
        "start_seconds": 0.0,
        "end_seconds": 6.0,
        "word_start_index": 0,
        "word_end_index": len(tokens),
        "timing_quality": "word_aligned",
        "alignment_confidence": 0.99,
    }
    cues = plan_editorial_cues(
        [scene],
        words,
        editorial_beats=[{"role": "evidence", "treatment": "listicle"}],
        media=[{
            "visual_decision": {
                "subject_bbox": [0.04, 0.35, 0.54, 0.72],
                "safe_text_regions": [[0.62, 0.06, 0.92, 0.31]],
            }
        }],
        containers=[{
            "container_id": "item-1",
            "kind": "listicle_item",
            "ordinal": 1,
            "canonical_subject": "Tower Climber",
        }],
        captions_enabled=True,
        variation_seed=5,
    )

    assert cues
    cue = cues[0]
    assert cue.text == "Number 1: Tower Climber"
    assert text[cue.char_start:cue.char_end] == cue.text
    assert 0 <= cue.scene_local_start_seconds < cue.scene_local_end_seconds <= 6.0
    assert cue.placement["position"] in {"top_left", "top_right", "center", "bottom_left", "bottom_right"}
    assert list(cue.placement["subject_bbox"]) == [0.04, 0.35, 0.54, 0.72]
    assert list(cue.placement["caption_band"]) == [0.04, 0.76, 0.96, 0.98]


def test_advanced_documentary_manager_accepts_diverse_grounded_grammars():
    manifest = {
        "scenes": [
            {"scene_index": 0, "narration": "The archive opened in 1985.", "primary_visual": {"type": "Timeline", "variant": "date_flip_calendar", "props": {"year": "1985"}}},
            {"scene_index": 1, "narration": "The route connected A to B.", "primary_visual": {"type": "AnimatedMap", "variant": "route_flight_arc", "props": {"from": "A", "to": "B"}}},
            {"scene_index": 2, "narration": "More than 1,500 people were aboard.", "primary_visual": {"type": "StatCard", "variant": "hero_counter_large", "props": {"value": "1,500"}}},
            {"scene_index": 3, "narration": "A witness described the final minute.", "primary_visual": {"type": "QuoteCard", "variant": "editorial_large_quote", "props": {"text": "The sea was calm."}}},
        ],
    }
    report = audit_manifest(manifest)
    assert report["status"] == "pass"
    assert report["unique_grammars"] == 4
    review = manager_review(manifest)
    assert review["ready_for_long_render"] is True


def test_media_first_chronology_audit_reads_the_rendered_date_marker():
    scene = {
        "scene_index": 0,
        "narration": "On a February weekend in 2003, the investigation began.",
        "media_composition": {"overlay_grammar": "chronology_strip"},
        "primary_visual": {
            "type": "ChapterTitle",
            "variant": "bold_editorial_kicker",
            "props": {"kicker": "ACT 01", "title": "ON A FEBRUARY WEEKEND IN 2003,"},
        },
        "graphic": {"template_id": "date-card", "props": {"date": "2003", "label": "2003"}},
    }
    assert audit_manifest({"scenes": [scene]})["status"] == "pass"

    scene_without_date = {
        **scene,
        "narration": "The investigation began.",
        "primary_visual": {"type": "ChapterTitle", "variant": "bold_editorial_kicker", "props": {"title": "THE INVESTIGATION"}},
        "graphic": {"template_id": "date-card", "props": {}},
    }
    report = audit_manifest({"scenes": [scene_without_date]})
    assert report["status"] == "fail"
    assert any(item["code"] == "chronology_without_date" for item in report["findings"])


def test_visual_critic_only_treats_geographic_diagrams_as_spatial_atlases():
    system_diagram = {
        "scene_index": 0,
        "narration": "The breach depended on how the vault was opened.",
        "primary_visual": {
            "type": "Diagram",
            "variant": "hierarchical_system_tree",
            "props": {"title": "SYSTEM PROCESS"},
        },
    }
    assert not any(
        item["code"] == "spatial_visual_is_abstract"
        for item in audit_manifest({"scenes": [system_diagram]})["findings"]
    )

    map_scene = {
        "scene_index": 0,
        "narration": "The route crossed the region.",
        "primary_visual": {"type": "AnimatedMap", "variant": "country_spotlight", "props": {}},
    }
    assert any(
        item["code"] == "spatial_visual_is_abstract"
        for item in audit_manifest({"scenes": [map_scene]})["findings"]
    )


def test_visual_critic_recognizes_grounded_paired_score_as_data_payload():
    scene = {
        'scene_index': 0,
        'narration': 'At Super Bowl XLIX, the Seahawks trail the Patriots, 28–24.',
        'primary_visual': {'type': 'Comparison', 'props': {
            'leftLabel': 'Seahawks', 'leftValue': '24',
            'rightLabel': 'Patriots', 'rightValue': '28',
        }},
        'media_composition': {'overlay_grammar': 'chart_center'},
    }
    report = audit_manifest({'scenes': [scene]})
    assert not any(item['code'] == 'data_without_payload' for item in report['findings'])


def test_long_director_cut_flags_earned_opportunities_left_as_generic_media():
    scenes = [
        {'scene_index': i, 'director_treatment_enabled': True,
         'text': {'frame_mode': 'media_only'},
             'narration': (
                 'The archive opened in 1985.' if i == 1 else
                 'The engine failed, the alarms sounded, which led to the crew turning back.' if i == 5 else
             f'A quiet visual beat number {i}.'
         ),
         'primary_visual': {'type': 'SmartVideo', 'variant': 'full_bleed'},
         'media': {'src': f'clip-{i}.mp4', 'type': 'video'}}
        for i in range(8)
    ]
    report = audit_manifest({'scenes': scenes})
    assert report['status'] == 'fail'
    assert any(item['code'] == 'director_grammar_underdiversity' for item in report['findings'])
    review = manager_review({'scenes': scenes})
    assert any('grounded date/process/data/place' in text for text in review['revision_instructions'])


def test_grounded_director_opportunity_repair_adds_date_and_causal_diagram_only():
    from services.editorial_director.contract import PrimaryVisual, SceneDecision
    from services.editorial_director.director import _repair_director_visual_opportunities

    narrations = [
        'A quiet opening image introduces the subject.',
        'The archive opened in 1985.',
        'The first object is shown in the collection.',
        'A quiet visual beat gives the viewer time to think.',
        'The engine failed, the alarms sounded, which led to the crew turning back.',
        'A source photograph shows the final object.',
        'The room is empty after the event.',
        'The final image closes the story.',
    ]
    scenes = [SceneDecision(
        scene_id=f'scene-{i}', scene_index=i, start=i * 4, end=(i + 1) * 4,
        duration=4, narration=text, visual_intent='media led',
        primary_visual=PrimaryVisual(type='SmartVideo', variant='full_bleed', props={}),
    ) for i, text in enumerate(narrations)]
    repairs = _repair_director_visual_opportunities(scenes, [
        {'source': 'graphic', 'is_graphic_fallback': True} if i == 1 else {'source': 'pexels'}
        for i in range(len(scenes))
    ])
    assert {row['type'] for row in repairs} == {'Timeline', 'Diagram'}
    assert scenes[1].primary_visual.props['events'][0]['year'] == '1985'
    assert scenes[1].text['frame_mode'] == 'data'
    assert scenes[4].text['frame_mode'] == 'diagram'
    assert len(scenes[4].primary_visual.props['steps']) >= 2
    assert scenes[0].primary_visual.type == 'SmartVideo'
    assert scenes[7].primary_visual.type == 'SmartVideo'


def test_atmosphere_only_director_cut_is_not_forced_into_specialist_graphics():
    from services.editorial_director.contract import PrimaryVisual, SceneDecision
    from services.editorial_director.director import _repair_director_visual_opportunities

    scenes = [SceneDecision(
        scene_id=f'atmosphere-{i}', scene_index=i, start=i * 3, end=(i + 1) * 3,
        duration=3, narration='The empty corridor falls quiet.', visual_intent='hold',
        primary_visual=PrimaryVisual(type='SmartImage', variant='full_bleed_cinematic', props={}),
    ) for i in range(8)]
    assert _repair_director_visual_opportunities(scenes, []) == []
    assert all(scene.primary_visual.type == 'SmartImage' for scene in scenes)


def test_visual_opportunity_repair_keeps_finding_facts_after_other_graphic_families_exist():
    from services.editorial_director.contract import PrimaryVisual, SceneDecision
    from services.editorial_director.director import _repair_director_visual_opportunities

    narrations = [
        'The opening image establishes the people involved.',
        'A quiet frame holds on the room.',
        'The archive opened in 1985.',
        'The next image introduces the route.',
        'A quiet visual beat gives the viewer time to think.',
        'The engine failed, the alarms sounded, which led to the crew turning back.',
        'The final image closes the story.',
        'One last moment remains.',
    ]
    scenes = [SceneDecision(
        scene_id=f'mixed-{i}', scene_index=i, start=i * 4, end=(i + 1) * 4,
        duration=4, narration=text, visual_intent='media led',
        primary_visual=PrimaryVisual(
            type='Chart' if i == 0 else 'StatCard' if i == 7 else 'SmartVideo',
            variant='existing' if i in {0, 7} else 'full_bleed', props={},
        ),
    ) for i, text in enumerate(narrations)]
    repairs = _repair_director_visual_opportunities(scenes, [])
    assert {repair['type'] for repair in repairs} == {'Timeline', 'Diagram'}
    assert scenes[2].primary_visual.type == 'Timeline'
    assert scenes[5].primary_visual.type == 'Diagram'


def test_media_first_contract_audit_handles_a_single_graphic_after_media_hold():
    report = audit_editorial_contracts({
        "scenes": [
            {
                "scene_index": 0,
                "duration": 5.0,
                "editorial_contract": {"enabled": False, "family": "media_hold"},
            },
            {
                "scene_index": 1,
                "duration": 5.0,
                "editorial_contract": {
                    "enabled": True,
                    "family": "keyword_emphasis",
                    "display_text": "diamond centre",
                    "motion": "text_reveal",
                    "composition_fingerprint": "keyword_emphasis:corner:text_reveal",
                    "timing": {"enabled": True, "hold_seconds": 3.0, "total_seconds": 3.62},
                },
            },
        ],
    })
    assert report["status"] == "pass"
    assert report["family_sequence"] == ["keyword_emphasis"]


def test_manager_blocks_ungrounded_data_and_repetitive_wallpaper():
    manifest = {
        "scenes": [
            {"scene_index": 0, "narration": "A claim.", "primary_visual": {"type": "Chart", "variant": "editorial_bar", "props": {}}},
            {"scene_index": 1, "narration": "Another claim.", "primary_visual": {"type": "Chart", "variant": "editorial_bar", "props": {}}},
        ],
    }
    review = manager_review(manifest)
    assert review["ready_for_long_render"] is False
    assert any("grounded payload" in instruction.lower() or "grounded text" in instruction.lower() for instruction in review["revision_instructions"])


def test_advanced_showcase_covers_all_documentary_render_grammars():
    systems = [
        ("Timeline", "date_flip_calendar", {"year": "1912"}),
        ("TextCard", "field-caption", {"text": "A grounded place"}),
        ("StatCard", "hero_counter_large", {"value": "1,500"}),
        ("Diagram", "code_console", {"message": "TWO WARNINGS"}),
        ("Headline", "newspaper_front_page", {"title": "THE ARCHIVE"}),
        ("DocumentHighlight", "yellow_marker_highlight", {"date": "1985", "highlight": "missing message"}),
        ("AnimatedMap", "route_flight_arc", {"from": "A", "to": "B"}),
        ("Chart", "trend_line_with_markers", {"data": [{"label": "A", "value": "1"}, {"label": "B", "value": "2"}]}),
        ("Comparison", "split_slider_before_after", {"left": "BEFORE", "right": "AFTER"}),
        ("Collage", "three_panel_asymmetric", {"title": "Three fragments"}),
    ]
    manifest = {
        "qa_showcase": True,
        "scenes": [
            {"scene_index": index, "narration": f"Grounded scene {index}.", "primary_visual": {"type": kind, "variant": variant, "props": props}}
            for index, (kind, variant, props) in enumerate(systems)
        ],
    }
    report = audit_manifest(manifest)
    assert report["status"] == "pass"
    assert report["unique_grammars"] >= 5
    assert report["warnings"] == 0


def test_manager_corrections_replace_ungrounded_facts_without_inventing_data():
    manifest = {
        "scenes": [
            {"scene_index": 0, "narration": "The report raised a question.", "primary_visual": {"type": "Chart", "variant": "editorial_bar", "props": {}}},
            {"scene_index": 1, "narration": "The report raised another question.", "primary_visual": {"type": "Chart", "variant": "editorial_bar", "props": {}}},
        ],
    }
    corrected = apply_manager_corrections(manifest)
    assert corrected["scenes"][0]["primary_visual"]["type"] == "SmartImage"
    assert corrected["scenes"][1]["primary_visual"]["type"] == "SmartImage"
    assert corrected["scenes"][0]["text"]["overlay_policy"] == "none"
    assert len(corrected["manager_corrections"]) == 2
    assert manifest["scenes"][0]["primary_visual"]["type"] == "Chart"


def test_media_first_plan_keeps_image_and_video_hero_and_avoids_subject():
    for media_type in ("image", "video"):
        scene = {
            "scene_index": 0,
            "narration": "A verified source shows the subject on the right.",
            "media": {
                "src": f"C:/media/source.{ 'jpg' if media_type == 'image' else 'mp4' }",
                "type": media_type,
                "visual_decision": {
                    "subject_bbox": [0.58, 0.18, 0.95, 0.82],
                    "safe_text_regions": [[0.04, 0.08, 0.42, 0.28]],
                },
            },
            "primary_visual": {
                "type": "StatCard",
                "variant": "hero_counter_large",
                "props": {"value": "1,500", "label": "PEOPLE"},
            },
        }
        plan = plan_media_composition(scene)
        assert plan["hero_layer"] == "source_media"
        assert plan["render_mode"] == "media_first_overlay"
        assert plan["media_visibility"] >= 0.78
        assert plan["overlay_grammar"] == "data_inset"
        assert plan["safe_region"] == [0.04, 0.08, 0.42, 0.28]
        assert plan["subject_bbox"] == [0.58, 0.18, 0.95, 0.82]


def test_media_first_manifest_audit_rejects_full_frame_graphics_over_media():
    manifest = {
        "scenes": [{
            "scene_index": 0,
            "narration": "A source clip carries the scene.",
            "media": {"src": "C:/media/clip.mp4", "type": "video"},
            "primary_visual": {"type": "TextCard", "variant": "field-caption", "props": {"text": "SOURCE"}},
            "media_composition": {
                "enabled": True,
                "hero_layer": "graphic",
                "render_mode": "graphic_only",
                "allow_full_frame_graphic": True,
                "media_visibility": 0.4,
            },
        }],
    }
    report = audit_media_compositions(manifest)
    assert report["status"] == "fail"
    assert report["ready_for_long_render"] is False


def test_media_first_annotation_is_used_by_manager_for_mixed_media_manifest():
    manifest = annotate_media_compositions({
        "scenes": [{
            "scene_index": 0,
            "narration": "A field image carries the evidence.",
            "media": {"src": "C:/media/source.jpg", "type": "image"},
            "primary_visual": {"type": "TextCard", "variant": "field-caption", "props": {"text": "EVIDENCE"}},
        }],
    })
    assert manifest["scenes"][0]["media_composition"]["hero_layer"] == "source_media"
    review = manager_review(manifest)
    assert review["media_critic"]["status"] == "pass"
    assert review["ready_for_long_render"] is True


def test_media_overlay_is_open_and_content_sized_not_a_box_panel():
    source = (Path(__file__).resolve().parents[1] / "src" / "remotion" / "components" / "MediaOverlayCanvas.jsx").read_text(encoding="utf-8")
    assert "function OpenCluster" in source
    assert "width: 'fit-content'" in source
    assert "const signatures = ['trace', 'slide', 'pin', 'scan'];" in source
    assert "backdropFilter" not in source
    assert "background: `linear-gradient(90deg, ${rgba(colors.bg, .78)" not in source


def test_documentary_swarm_assigns_reference_grammars_and_diverse_motion():
    scenes = [
        {"scene_index": 0, "narration": "Espoo, Finland, February 2011.", "media": {"src": "C:/media/a.jpg", "type": "image"}, "primary_visual": {"type": "ChapterTitle", "variant": "chapter-slate", "props": {"title": "THE BURNING PLATFORM", "kicker": "CHAPTER 01"}}},
        {"scene_index": 1, "narration": "The memo described a company standing on a burning platform.", "media": {"src": "C:/media/b.mp4", "type": "video"}, "primary_visual": {"type": "QuoteCard", "variant": "source-quote", "props": {"quote": "We are standing on a burning platform.", "attribution": "STEPHEN ELOP"}}},
        {"scene_index": 2, "narration": "The stock fell fourteen percent.", "media": {"src": "C:/media/c.jpg", "type": "image"}, "primary_visual": {"type": "StatCard", "variant": "editorial-stat", "props": {"value": "-14%", "label": "HELSINKI EXCHANGE"}}},
        {"scene_index": 3, "narration": "The company shipped phones across the world.", "media": {"src": "C:/media/d.jpg", "type": "image"}, "primary_visual": {"type": "Collage", "variant": "archive-contact-sheet", "props": {"items": [{"label": "2110"}, {"label": "Communicator"}, {"label": "N-Gage"}]}}},
        {"scene_index": 4, "narration": "Stephen Elop became the new chief executive.", "media": {"src": "C:/media/e.jpg", "type": "image"}, "primary_visual": {"type": "PersonProfile", "variant": "portrait-id", "props": {"name": "STEPHEN ELOP", "role": "CHIEF EXECUTIVE"}}},
        {"scene_index": 5, "narration": "The story began in Finland.", "media": {"src": "C:/media/f.jpg", "type": "image"}, "primary_visual": {"type": "AnimatedMap", "variant": "map-locator", "props": {"from": "FINLAND", "to": "ESPOO"}}},
    ]
    result = run_documentary_swarm({"scenes": scenes, "visual_system": {"render_mode": "advanced-doc-v1"}})
    grammars = [item["graphic"]["grammar"] for item in result["swarm_plan"]["scenes"]]
    assert grammars == ["chapter_slate", "quote_pull", "data_inset", "archive_contact_sheet", "portrait_id", "locator_inset"]
    assert result["swarm_plan"]["sequence_audit"]["status"] == "pass"
    assert result["swarm_plan"]["sequence_audit"]["unique_grammars"] == 6
    assert result["swarm_plan"]["sequence_audit"]["unique_motion_families"] >= 3
    assert all(scene["media_composition"]["hero_layer"] == ("graphic" if scene['primary_visual']['type'] == 'AnimatedMap' else "source_media") for scene in result["scenes"])


def test_swarm_promotes_spaced_high_salience_headline_to_center_without_boxing_media():
    result = run_documentary_swarm({
        "scenes": [
            {
                "scene_index": 0,
                "narration": "The pressure system changes every escape across Vice City.",
                "media": {"src": "C:/media/source.jpg", "type": "image"},
                "primary_visual": {"type": "TextCard", "variant": "field-caption", "props": {"text": "VICE CITY"}},
            }
        ]
    })
    scene = result["scenes"][0]
    assert scene["media_composition"]["placement"] == "center"
    assert scene["media_composition"]["overlay_scale"] == "hero"
    assert scene["media_composition"]["overlay_background"] == "transparent"
    assert result["swarm_audit"]["investigators"]["status"] == "pass"


def test_swarm_overlay_routes_are_explicit_and_no_box_geometry_is_allowed():
    source = (Path(__file__).resolve().parents[1] / "src" / "remotion" / "components" / "MediaOverlayCanvas.jsx").read_text(encoding="utf-8")
    for grammar in ("quote_pull", "chapter_slate", "archive_contact_sheet", "portrait_id"):
        assert grammar in source
    assert '"background": "transparent"' in (Path(__file__).resolve().parents[1] / "server" / "services" / "editorial_director" / "swarm.py").read_text(encoding="utf-8")
    assert "filled_panel" in (Path(__file__).resolve().parents[1] / "server" / "services" / "editorial_director" / "swarm.py").read_text(encoding="utf-8")


def test_swarm_lets_the_script_hold_media_without_decorative_graphics():
    result = run_documentary_swarm({
        "scenes": [{
            "scene_index": 0,
            "narration": "The source image carries this beat.",
            "media": {"src": "C:/media/source.jpg", "type": "image"},
            "primary_visual": {"type": "SmartImage", "props": {}},
        }],
    })
    scene = result["scenes"][0]
    assert scene["swarm_plan"]["graphic"]["grammar"] == "none"
    assert scene["media_composition"]["overlay_grammar"] == "none"
    assert result["swarm_audit"]["investigators"]["status"] == "pass"
    assert result["swarm_audit"]["investigators"]["media_hold_count"] == 1


def test_map_and_chart_director_paths_are_grounded_and_spatially_distinct():
    result = run_documentary_swarm({
        "scenes": [
            {
                "scene_index": 0,
                "narration": "The route moved from London to Georgetown.",
                "media": {"src": "C:/media/map.jpg", "type": "image"},
                "primary_visual": {"type": "AnimatedMap", "props": {"from": "LONDON", "to": "GEORGETOWN, GUYANA"}},
            },
            {
                "scene_index": 1,
                "narration": "The series rose across four verified points.",
                "media": {"src": "C:/media/chart.jpg", "type": "image"},
                "primary_visual": {"type": "Chart", "props": {"title": "VERIFIED SERIES", "data": [{"label": "Q1", "value": 12}, {"label": "Q2", "value": 18}, {"label": "Q3", "value": 27}]}},
            },
        ],
    })
    map_scene, chart_scene = result["scenes"]
    assert map_scene["media_composition"]["render_mode"] == "explanatory_canvas"
    assert map_scene["media_composition"]["placement"] == "full_canvas"
    assert chart_scene["media_composition"]["render_mode"] == "media_first_overlay"
    assert chart_scene["media_composition"]["placement"] == "center"
    assert chart_scene["media_composition"]["hero_layer"] == "source_media"
    assert chart_scene["media_composition"]["blur_media_underlay"] is False
    assert result["swarm_audit"]["investigators"]["status"] == "pass"


def test_map_and_chart_compositor_paths_are_present_in_remotion_source():
    source = (Path(__file__).resolve().parents[1] / "src" / "remotion" / "components" / "MediaOverlayCanvas.jsx").read_text(encoding="utf-8")
    composition = (Path(__file__).resolve().parents[1] / "src" / "remotion" / "DocumentaryComposition.jsx").read_text(encoding="utf-8")
    assert "const LOCATION_POINTS" in source
    assert "function ChartCenter" in source
    assert "function LocatorInset" in source
    assert "strokeDashoffset={1 - motion.enter}" in source
    assert "blur(5px)" not in composition
    assert "brightness(.92)" in composition
    assert "centerDataVisual" in composition


def test_specialist_graphics_own_their_cues_without_duplicate_slate_text():
    root = Path(__file__).resolve().parents[1]
    composition = (root / "src" / "remotion" / "DocumentaryComposition.jsx").read_text(encoding="utf-8")
    cue_slate = (root / "src" / "remotion" / "components" / "DirectorMotionGraphics.jsx").read_text(encoding="utf-8")
    assert "activeDirectorGraphicType === 'Comparison'" in composition
    assert "['Diagram', 'Timeline'].includes(activeDirectorGraphicType)" in composition
    assert "suppressedElements={suppressedDirectorCueElements}" in composition
    assert "!suppressed.has('comparison')" in cue_slate
    assert "!suppressed.has('steps')" in cue_slate


def test_remotion_media_layer_uses_explicit_v2_canvas_role_and_keeps_overlays_open():
    root = Path(__file__).resolve().parents[1]
    composition = (root / "src" / "remotion" / "DocumentaryComposition.jsx").read_text(encoding="utf-8")
    assert "visual_role_contract_version" in composition
    assert "scene.visual_role || '').toLowerCase() === 'primary_explanation'" in composition
    assert "mediaComposition.render_mode === 'explanatory_canvas'" in composition
    assert "background: 'rgba(5, 9, 14, .50)'" not in composition
    assert "mediaFirst: directorGraphicType !== 'AnimatedMap'" in composition


def test_director_cue_slates_respect_copy_windows_and_palette_contrast():
    root = Path(__file__).resolve().parents[1]
    composition = (root / "src" / "remotion" / "DocumentaryComposition.jsx").read_text(encoding="utf-8")
    cue_slate = (root / "src" / "remotion" / "components" / "DirectorMotionGraphics.jsx").read_text(encoding="utf-8")
    assert "directorCueBlockedWindows" in composition
    assert "!['SmartImage', 'SmartVideo', 'image', 'video'].includes(String(primaryType))" in composition
    assert "blockedWindows={directorCueBlockedWindows}" in composition
    assert "cueOverlapsWindow(start, end, blockedWindows)" in cue_slate
    assert "function contrastRatio" in cue_slate
    assert "readableColor(palette.primary, '#F7F5EF', panel, 4.5)" in cue_slate
    assert "readableColor(palette.accent, '#E7B84E', panel, 3.2)" in cue_slate


def test_diagram_phase_labels_do_not_shrink_the_narrated_process_to_metadata():
    diagram = (Path(__file__).resolve().parents[1] / "src" / "remotion" / "components" / "Diagram.jsx").read_text(encoding="utf-8")
    assert "title: phaseMatch ? '' : cleanStepTitle" in diagram
    assert "const headline = step.title || step.description" in diagram
    assert "fontSize: step.title ? 17 : 27" in diagram


def test_highlight_selector_returns_script_earned_words_without_stopwords():
    text = "The new Vice City is built around a living world with real time weather."
    phrase = select_grounded_highlight(text, "real time", max_words=3)
    tokens = {token.lower() for token in highlight_tokens_for(text, "real time", max_tokens=3)}
    assert phrase == "real time"
    assert tokens == {"real"}
    assert "the" not in tokens


def test_center_highlights_are_larger_than_corner_highlights_and_modern_fonts_rotate():
    texts = [
        "Vice City becomes the pressure system for every escape.",
        "Jason and Lucia turn trust into the central risk.",
        "Saturated nights create a cinematic storytelling language.",
        "The wetlands route changes who can follow.",
        "The benchmark is cinematic control.",
    ]
    beats = [
        {"role": "highlight", "importance": .9, "emphasis": emphasis}
        for emphasis in ("Vice City", "trust", "saturated nights", "wetlands route", "cinematic control")
    ]
    manifest = build_semantic_edit_manifest(
        texts, [3.0] * len(texts), topic="GTA VI", category="gaming",
        editorial_beats=beats, variation_seed=41,
    )
    contracts = [scene["graphic"].get("props", {}) for scene in manifest["scenes"]]
    centers = [item for item in contracts if item.get("_position") == "center"]
    corners = [item for item in contracts if item.get("_position") != "center" and item.get("_contract_text")]
    assert centers
    assert corners
    assert min(int(item["_contract_size"]) for item in centers) > max(int(item["_contract_size"]) for item in corners)
    assert len({item.get("_contract_font") for item in contracts if item.get("_contract_font")}) >= 2
    assert all(" the " not in f" {str(item.get('_contract_text', '')).lower()} " for item in contracts if item.get("_contract_text") == "THE")
