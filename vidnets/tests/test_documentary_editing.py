from server.services.documentary_editing import (
    build_documentary_edit_plan,
    ffmpeg_scene_filters,
    finalize_documentary_edit_plan,
    normalize_documentary_settings,
    _measure_text_width,
    _legible_ink_for,
)


def test_documentary_layer_is_opt_in_and_normalizes_style():
    disabled = normalize_documentary_settings({})
    enabled = normalize_documentary_settings({
        "documentary_editing_enabled": True,
        "documentary_style": "archival",
    })

    assert disabled["enabled"] is False
    assert enabled["enabled"] is True
    assert enabled["style"] == "archival"


def test_plan_detects_dates_money_and_container_boundaries():
    plan = build_documentary_edit_plan(
        [
            "On March 14, 2024, the investigation began.",
            "Apple became the first company valued at $3 billion.",
            "Banana exports changed the market forever.",
        ],
        [4.0, 5.0, 3.0],
        {"documentary_editing_enabled": True},
        ["Apple", "Apple", "Banana"],
    )

    assert plan["enabled"] is True
    assert plan["duration"] == 12.0
    assert plan["scenes"][0]["date_card"] == "March 14, 2024"
    assert plan["scenes"][1]["money_counter"]["value"] == 3_000_000_000
    assert plan["scenes"][1]["money_counter"]["suffix"] == "B"
    assert plan["scenes"][2]["container_boundary"] is True
    assert plan["scenes"][2]["transition_in"] in {
        "dip_to_black", "crossfade", "quick_zoom_in", "quick_zoom_out",
        "slide_left", "slide_right", "wipe",
    }
    assert len(plan["tracks"]["transitions"]) == 2


def test_ffmpeg_filters_include_grade_transition_and_animated_card():
    filters = ffmpeg_scene_filters(
        {
            "duration": 4.0,
            "date_card": "March 14, 2024",
            "key_highlight": "THE CASE CHANGED FOREVER",
            "color_grade": "investigative",
            "transition_in": "dip_to_black",
            "transition_out": "dip_to_black",
        },
        1920,
        1080,
    )
    joined = ",".join(filters)

    assert "eq=contrast=" in joined
    assert "fade=t=in" in joined
    assert "fade=t=out" in joined
    assert "drawtext=" in joined
    assert "fontsize='" in joined
    assert "alpha='" in joined
    assert "shadowcolor=black@0.96:shadowx=5:shadowy=5" in joined


def test_editor_export_renders_multiple_independently_positioned_text_blocks():
    filters = ffmpeg_scene_filters(
        {
            "duration": 6.0,
            "editor_text_blocks": [
                {
                    "role": "overlay", "label": "FIRST BLOCK", "start": 0.2,
                    "duration": 2.0, "x": 20, "y": 25, "size": 58,
                    "font_color": "#00d4ff", "background": "edge",
                },
                {
                    "role": "highlight", "label": "SECOND BLOCK", "start": 2.4,
                    "duration": 2.2, "x": 75, "y": 70, "size": 76,
                    "font_color": "#ffffff", "background": "underline",
                },
            ],
        },
        1920,
        1080,
    )
    joined = ",".join(filters)

    assert joined.count("drawtext=") == 2
    assert "FIRST BLOCK" in joined
    assert "SECOND BLOCK" in joined
    assert "(w-text_w)*0.2000" in joined
    assert "(w-text_w)*0.7500" in joined


def test_money_filter_uses_a_numeric_count_up_expression():
    filters = ffmpeg_scene_filters(
        {
            "duration": 3.0,
            "money_counter": {
                "label": "$3 billion",
                "prefix": "$",
                "display_value": 3.0,
                "suffix": "B",
            },
            "color_grade": "none",
        },
        1920,
        1080,
    )

    assert any("%{eif\\:min(3\\,3*max(0\\,(t-0.000))/0.8)\\:d}B" in item for item in filters)


def test_money_parser_accepts_pound_symbol_without_encoding_drift():
    plan = build_documentary_edit_plan(
        ["The acquisition cost \u00a35 million."],
        [2.0],
        {"documentary_editing_enabled": True},
        ["Acquisition"],
    )

    counter = plan["scenes"][0]["money_counter"]
    assert counter["prefix"] == "\u00a3"
    assert counter["value"] == 5_000_000


def test_container_boundary_never_leaks_internal_subject_as_visible_text():
    plan = build_documentary_edit_plan(
        ["The opening establishes the investigation.", "The banana section begins with ordinary context."],
        [3.0, 3.0],
        {"documentary_editing_enabled": True},
        ["Apple", "Banana"],
    )

    assert plan["scenes"][0]["key_highlight"] == ""
    assert plan["scenes"][1]["container_boundary"] is True
    assert plan["scenes"][1]["overlay_type"] == ""
    assert plan["scenes"][1]["overlay_card"] == ""


def test_statistic_and_cta_cues_are_explicit_timeline_overlays():
    plan = build_documentary_edit_plan(
        ["Only 27 percent survived the first trial.", "Subscribe for the next investigation."],
        [3.0, 3.0],
        {"documentary_editing_enabled": True},
        ["Trial", "Trial"],
    )

    assert plan["scenes"][0]["overlay_type"] == "statistic"
    assert plan["scenes"][0]["overlay_card"] == "27 percent"
    assert plan["scenes"][1]["overlay_type"] == "cta"
    assert plan["scenes"][1]["overlay_card"] == "SUBSCRIBE"


def test_cues_are_timed_near_their_spoken_phrase_and_not_all_at_scene_start():
    plan = build_documentary_edit_plan(
        ["The investigation continued for months before the company paid $12 million."],
        [8.0],
        {"documentary_editing_enabled": True},
        ["Company"],
    )

    scene = plan["scenes"][0]
    assert scene["overlay_start"] > 4.0
    assert plan["tracks"]["overlays"][0]["start"] == scene["overlay_start"]


def test_finalizer_records_parallel_seed_reconciliation():
    provisional = build_documentary_edit_plan(
        ["In 2024 the case opened."], [4.0], {"documentary_editing_enabled": True}, []
    )
    final = finalize_documentary_edit_plan(
        provisional,
        ["In 2024 the case opened.", "The inquiry continued."],
        [3.0, 3.0],
        {"documentary_editing_enabled": True},
        ["Case", "Case"],
    )

    assert final["planning"]["reconciled"] is True
    assert final["planning"]["seed_scene_count"] == 1
    assert final["planning"]["final_scene_count"] == 2


def test_whisper_words_place_text_on_the_spoken_phrase():
    words = [
        {"word": "The", "start": 0.1, "end": 0.3},
        {"word": "critical", "start": 1.25, "end": 1.62},
        {"word": "evidence", "start": 1.64, "end": 2.02},
        {"word": "survived", "start": 2.1, "end": 2.5},
    ]
    plan = build_documentary_edit_plan(
        ["The critical evidence survived."],
        [3.0],
        {"documentary_editing_enabled": True},
        ["Case"],
        editorial_beats=[{
            "role": "evidence", "emphasis": "critical evidence", "treatment": "keyword",
            "animation": "scale_pop", "background": "none", "transition": "cut", "energy": "tense",
        }],
        whisper_words=words,
    )

    scene = plan["scenes"][0]
    assert scene["highlight_timing_source"] == "whisper"
    assert 1.55 <= scene["highlight_start"] <= 1.75
    assert 1.0 <= scene["highlight_duration"] <= 1.7


def test_unmatched_text_is_dropped_instead_of_using_estimated_timing():
    plan = build_documentary_edit_plan(
        ["The warning changed everything."],
        [2.5],
        {"documentary_editing_enabled": True},
        ["Internal container label"],
        editorial_beats=[{
            "role": "chapter", "emphasis": "Titanic sinking", "treatment": "chapter",
            "animation": "fade", "background": "none", "transition": "cut", "energy": "normal",
        }],
        whisper_words=[
            {"word": "The", "start": 0.1, "end": 0.2},
            {"word": "warning", "start": 0.3, "end": 0.7},
            {"word": "changed", "start": 0.8, "end": 1.1},
            {"word": "everything", "start": 1.2, "end": 1.7},
        ],
    )

    scene = plan["scenes"][0]
    assert scene["overlay_card"] == ""
    assert scene["overlay_timing_source"] == "rejected_no_whisper_match"
    assert plan["tracks"]["overlays"] == []


def test_llm_date_and_location_use_exact_whisper_phrase_when_scene_label_omits_it():
    words = [
        {"word": "Everything", "start": 0.1, "end": 0.4},
        {"word": "changed", "start": 0.5, "end": 0.8},
        {"word": "April", "start": 1.0, "end": 1.3},
        {"word": "14", "start": 1.32, "end": 1.55},
        {"word": "The", "start": 3.1, "end": 3.25},
        {"word": "ship", "start": 3.3, "end": 3.5},
        {"word": "left", "start": 3.55, "end": 3.75},
        {"word": "Southampton", "start": 4.0, "end": 4.55},
    ]
    beats = [
        {
            "role": "reveal", "emphasis": "April 14", "treatment": "date",
            "highlight_kind": "milestone", "selected_for_text": True,
            "animation": "fade", "background": "none", "transition": "cut", "energy": "tense",
        },
        {
            "role": "context", "emphasis": "Southampton", "treatment": "location",
            "highlight_kind": "location", "selected_for_text": True,
            "animation": "slide_left", "background": "edge", "transition": "cut", "energy": "normal",
        },
    ]
    plan = build_documentary_edit_plan(
        ["Everything changed that night.", "The voyage began at the port."],
        [3.0, 3.0],
        {"documentary_editing_enabled": True},
        ["Disaster", "Departure"],
        editorial_beats=beats,
        whisper_words=words,
    )

    assert plan["scenes"][0]["date_card"] == "April 14"
    assert plan["scenes"][0]["overlay_timing_source"] == "whisper"
    assert plan["scenes"][1]["overlay_card"] == "Southampton"
    assert plan["scenes"][1]["overlay_type"] == "location"
    assert plan["scenes"][1]["overlay_timing_source"] == "whisper"


def test_modern_selected_text_uses_template_motion_when_brain_requests_none():
    plan = build_documentary_edit_plan(
        ["The critical warning changed everything."],
        [3.0],
        {"documentary_editing_enabled": True, "documentary_style": "modern"},
        ["Investigation"],
        editorial_beats=[{
            "role": "reveal", "emphasis": "critical warning", "treatment": "keyword",
            "highlight_kind": "warning", "selected_for_text": True,
            "animation": "none", "background": "none", "transition": "cut", "energy": "tense",
        }],
    )

    scene = plan["scenes"][0]
    assert scene["key_highlight"] == "warning"
    assert scene["text_animation"] != "none"


def test_modern_highlight_never_restores_legacy_blue_card_background():
    plan = build_documentary_edit_plan(
        ["The critical warning changed everything."],
        [3.0],
        {"documentary_editing_enabled": True, "documentary_style": "modern"},
        ["Investigation"],
        editorial_beats=[{
            "role": "reveal", "emphasis": "critical warning", "treatment": "keyword",
            "highlight_kind": "warning", "selected_for_text": True,
            "animation": "scale_pop", "background": "edge", "transition": "cut", "energy": "tense",
        }],
    )

    scene = plan["scenes"][0]
    assert scene["text_background"] == "none"
    rendered = ",".join(ffmpeg_scene_filters(scene, 1920, 1080))
    assert "color=0x09111f" not in rendered
    assert "boxcolor=0x09111f" not in rendered


def test_every_clip_boundary_has_a_varied_rendered_transition_with_zoom():
    plan = build_documentary_edit_plan(
        [f"Ordinary scene {index}." for index in range(7)],
        [2.0] * 7,
        {"documentary_editing_enabled": True},
        ["Case"] * 7,
        editorial_beats=[{"role": "context", "treatment": "none", "transition": "cut", "energy": "normal"}] * 7,
    )

    transitions = [scene["transition_in"] for scene in plan["scenes"][1:]]
    assert all(transition != "cut" for transition in transitions)
    assert "quick_zoom_in" in transitions
    assert "quick_zoom_out" in transitions


def test_person_and_place_cues_use_dedicated_template_families():
    plan = build_documentary_edit_plan(
        ["Jason leads the story from Vice City.", "The investigation moved to Berlin."],
        [3.0, 3.0],
        {"documentary_editing_enabled": True},
        ["Jason", "Berlin"],
        editorial_beats=[
            {
                "role": "evidence", "emphasis": "Jason", "treatment": "person",
                "highlight_kind": "key_fact", "selected_for_text": True,
                "animation": "fade", "background": "none", "transition": "cut", "energy": "normal",
            },
            {
                "role": "evidence", "emphasis": "Berlin", "treatment": "location",
                "highlight_kind": "location", "selected_for_text": True,
                "animation": "rise", "background": "edge", "transition": "cut", "energy": "normal",
            },
        ],
    )

    assert plan["scenes"][0]["overlay_type"] == "person"
    assert plan["scenes"][0]["overlay_template"].startswith("person_")
    assert plan["scenes"][1]["overlay_type"] == "location"
    assert plan["scenes"][1]["overlay_template"].startswith("place_")


def test_marker_card_uses_legible_dark_ink_not_white_on_light():
    # The accent marker box is light cyan; white text on it was unreadable.
    filters = ffmpeg_scene_filters(
        {
            "duration": 4.0,
            "key_highlight": "ICEBERG",
            "highlight_background": "marker",
            "highlight_position": "center",
            "color_grade": "none",
        },
        1920,
        1080,
    )
    joined = ",".join(filters)
    assert "fontcolor=0x0d1117" in joined
    assert "boxcolor=0x42e6ff@0.82" in joined


def test_marker_card_preserves_explicit_user_color():
    filters = ffmpeg_scene_filters(
        {
            "duration": 4.0,
            "key_highlight": "ICEBERG",
            "highlight_background": "marker",
            "highlight_font_color": "#ffd000",
            "color_grade": "none",
        },
        1920,
        1080,
    )
    assert any("fontcolor=0xffd000" in item for item in filters)


def test_edge_card_centers_text_on_measured_background():
    filters = ffmpeg_scene_filters(
        {
            "duration": 4.0,
            "key_highlight": "ICEBERG",
            "highlight_background": "edge",
            "highlight_position": "center",
            "color_grade": "none",
        },
        1920,
        1080,
    )
    joined = "\n".join(filters)
    edge = next(item for item in filters if "drawtext" in item)
    # The drawtext must be anchored to the card's geometric center: for a
    # centered edge card the box starts at (1920 - card_w)/2 so text_x must be
    # the box x offset plus half the box width, i.e. centered on the card.
    text_x = edge.split("x='")[1].split("'")[0]
    import re as _re
    box_line = next(item for item in filters if item.startswith("drawbox=x="))
    box_x = int(_re.search(r"x=(\d+)", box_line).group(1))
    box_w = int(_re.search(r"w=(\d+)", box_line).group(1))
    assert text_x == f"({box_x} + ({box_w} - text_w)/2)"
    assert text_x.startswith("(") and "+" in text_x


def test_legible_ink_helper_picks_contrast_appropriately():
    assert _legible_ink_for("#42e6ff") == "0x0d1117"  # light cyan -> dark ink
    assert _legible_ink_for("#09111f") == "white"       # dark navy -> white ink
    assert _measure_text_width("ICEBERG", 82, "Arial") > 200
