import copy

import pytest

from server.services.semantic_edit_engine import (
    SemanticManifestError,
    build_semantic_edit_manifest,
    choose_theme,
    get_template_manifest,
    plan_sound_cues,
    align_directed_motion_to_speech,
    plan_directed_sound_cues,
    rescore_revised_soundtrack,
    _anchor_sound_cue_to_whisper,
    _sanitize_sound_cue_timeline,
    stable_seed,
    validate_semantic_manifest,
)
from server.services.editing_profile import deterministic_profile
from server.services.edit_variation_audit import audit_edit_variation


def test_theme_selection_and_override_are_deterministic():
    assert choose_theme("RMS Titanic history")['id'] == "history_archive"
    assert choose_theme("market analysis", requested="finance_terminal")['id'] == "finance_terminal"
    first = build_semantic_edit_manifest(["The ship departed in 1912."], [3], topic="RMS Titanic")
    second = build_semantic_edit_manifest(["The ship departed in 1912."], [3], topic="RMS Titanic")
    assert first == second
    assert first['theme']['id'] == 'history_archive'


def test_grounded_cue_audit_does_not_demand_invented_card_coverage():
    scenes = []
    treatments = ("editorial_crop", "panoramic", "focus_push")
    transitions = ("cut", "crossfade", "cut", "wipe")
    for index in range(12):
        scenes.append({
            "graphic": {"template_id": "full-bleed"},
            "transition": {"template_id": transitions[index % len(transitions)]},
            "design": {
                "image_treatment": treatments[index % len(treatments)],
                "visual_structure": "media",
            },
            "atmosphere": {"layers": ["grain"]},
            "sound_cues": [],
        })

    audit = audit_edit_variation(scenes, grounded_cue_mode=True)

    assert not any(
        warning.startswith("graphic_coverage:") for warning in audit["warnings"]
    )
    assert audit["graphic_density_policy"] == "grounded_cues"


def test_manifest_preserves_exact_narration_and_scene_timing():
    texts = ["The ship departed in 1912.", "A route crossed the North Atlantic."]
    manifest = build_semantic_edit_manifest(texts, [2.2, 3.8], topic="RMS Titanic", editorial_beats=[{"role": "evidence", "treatment": "date"}, {"role": "location", "emphasis": "North Atlantic"}])
    assert [scene['narration'] for scene in manifest['scenes']] == texts
    assert manifest['scenes'][1]['start'] == 2.2
    assert manifest['duration_in_frames'] == 180
    assert manifest['scenes'][0]['graphic']['template_id'] in {'date-card', 'calendar-rip', 'timeline-trace'}
    assert manifest['scenes'][1]['graphic']['template_id'] in {'map-card', 'map-route', 'route-progress', 'field-caption', 'timeline-trace'}


def test_non_transition_sound_effects_snap_to_meaningful_whisper_words():
    scene = {
        "start": 20.0,
        "duration": 5.0,
        "word_cues": [{
            "timing_quality": "whisper_word_aligned",
            "timed_words": [
                {"index": 0, "text": "In", "start": 0.0, "end": 0.15, "absolute_start": 20.0, "absolute_end": 20.15},
                {"index": 1, "text": "1969,", "start": 0.2, "end": 0.48, "absolute_start": 20.2, "absolute_end": 20.48},
                {"index": 2, "text": "Apollo", "start": 0.55, "end": 0.92, "absolute_start": 20.55, "absolute_end": 20.92},
                {"index": 3, "text": "reached", "start": 1.0, "end": 1.3, "absolute_start": 21.0, "absolute_end": 21.3},
                {"index": 4, "text": "the", "start": 1.4, "end": 1.52, "absolute_start": 21.4, "absolute_end": 21.52},
                {"index": 5, "text": "Moon.", "start": 1.6, "end": 1.95, "absolute_start": 21.6, "absolute_end": 21.95},
            ],
        }],
    }
    cue = {
        "kind": "map_ping",
        "start": 20.0,
        "time_local": 0.0,
        "sound_intent": "location_ping",
        "reason": "map arrival",
    }

    _anchor_sound_cue_to_whisper(scene, cue)

    assert cue["sound_anchor"] == "Apollo"
    assert cue["timing_quality"] == "whisper_word_aligned"
    assert cue["start"] == 20.55
    assert cue["sound_anchor_edge"] == "start"


def test_sound_mix_limits_density_and_avoids_repeating_effect_families(monkeypatch):
    for key in (
        "VIDZEO_SFX_MAX_PER_MINUTE",
        "VIDZEO_SFX_REPEAT_GAP_SECONDS",
        "VIDZEO_SFX_CATEGORY_GAP_SECONDS",
        "VIDZEO_SFX_MIN_EFFECT_GAP_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    scenes = [
        {
            "start": float(index * 15),
            "duration": 15.0,
            "narration": "Apollo reached the Moon.",
            "semantic_beat": {"source": "deterministic", "sound_intent": "reveal"},
            "word_cues": [],
            "sound_cues": [{
                "kind": kind,
                "src": "local.wav",
                "start": float(index * 15),
                "time_local": 0.0,
                "duration": 0.3,
                "sound_intent": "reveal",
            }],
        }
        for index, kind in enumerate(("whoosh_soft", "edit_whoosh_sucked", "map_ping", "impact_soft"))
    ]

    audit = _sanitize_sound_cue_timeline(scenes, {})

    assert audit["max_effect_cues"] == 10
    assert audit["after"] == 4
    assert audit["category_gap_seconds"] == 2.5
    assert audit["minimum_effect_gap_seconds"] == 1.35


def test_final_director_sound_follows_spoken_visual_events_and_keeps_plain_media_quiet():
    library = {key: {"src": f"{key}.wav", "license": "generated"} for key in
               ("cash_counter", "data_tick", "edit_zoom_whirr", "map_draw", "route_ping", "technical_tick", "whoosh_soft")}
    chart = {
        "start": 4.0, "duration": 4.0, "primary_visual": {"type": "StatCard"},
        "graphic": {"template_id": "bar-chart"}, "camera": {"entrance": "cut"},
        "semantic_beat": {"source": "openrouter", "sound_intent": "none"},
        "motion_cues": [{"action": "count", "element": "data", "anchor_text": "twenty five billion", "time_local": 2.5}],
        "word_cues": [{"timing_quality": "whisper_word_aligned", "timed_words": [
            {"index": 0, "text": "twenty", "start": .6},
            {"index": 1, "text": "five", "start": .85},
            {"index": 2, "text": "billion", "start": 1.1},
        ]}],
    }
    align_directed_motion_to_speech(chart)
    assert chart["motion_cues"][0]["time_local"] == .6
    chart["sound_cues"] = plan_directed_sound_cues(chart, 1, library)
    assert [(cue["kind"], cue["start"]) for cue in chart["sound_cues"]] == [("cash_counter", 4.6)]
    assert _sanitize_sound_cue_timeline([chart], library)["after"] == 1

    photo = {
        "start": 8.0, "duration": 4.0, "primary_visual": {"type": "SmartImage"},
        "graphic": {"template_id": "full-bleed"}, "camera": {"entrance": "fade"},
        "semantic_beat": {"source": "openrouter", "sound_intent": "none"},
        "motion_cues": [{"action": "reveal", "element": "steps", "time_local": 1.0}],
    }
    assert plan_directed_sound_cues(photo, 2, library) == []

    zoom = {**photo, "camera": {"entrance": "scale_pop"}, "motion_cues": []}
    assert plan_directed_sound_cues(zoom, 2, library)[0]["kind"] == "edit_zoom_whirr"

    map_scene = {
        **photo, "primary_visual": {"type": "AnimatedMap"},
        "graphic": {"template_id": "map-route"},
        "motion_cues": [{"action": "trace", "element": "route", "time_local": 1.2}],
    }
    assert [(cue["kind"], cue["start"]) for cue in plan_directed_sound_cues(map_scene, 2, library)] == [
        ("map_draw", 9.2)
    ]

    chapter = {
        **photo, "primary_visual": {"type": "ChapterTitle"},
        "transition_out": "wipe_left", "narration": "A turning point.", "motion_cues": [],
    }
    assert [cue["kind"] for cue in plan_directed_sound_cues(chapter, 0, library)] == ["whoosh_soft"]
    assert plan_directed_sound_cues({**photo, "transition_out": "wipe_left", "narration": "A plain shot."}, 1, library) == []


def test_adjacent_scenes_do_not_repeat_graphic_or_motion_variants():
    manifest = build_semantic_edit_manifest(["A fact appears."] * 6, [2] * 6, topic="A history documentary", variation_seed=7)
    templates = [scene['graphic']['template_id'] for scene in manifest['scenes']]
    motions = [scene['camera']['motion'] for scene in manifest['scenes']]
    designed = [template for template in templates if template != 'full-bleed']
    assert all(left != right for left, right in zip(designed, designed[1:]))
    assert all(left != right for left, right in zip(motions, motions[1:]))


def test_approved_copy_over_real_media_has_its_own_reveal_sound():
    library = {'marker_scratch': {'src':'marker.wav','license':'generated'}}
    scene = {'start':8,'duration':4,'narration':'A quiet clue emerges.',
        'primary_visual':{'type':'SmartImage'},'graphic':{'template_id':'full-bleed'},
        'semantic_beat':{'source':'openrouter','sound_intent':'none'},
        'editorial_contract':{'version':4,'source':'llm','approved':True,'display_text':'quiet clue','source_span':'quiet clue'},
        'text':{'position':'top_right'},
        'motion_cues':[{'element':'copy','action':'wipe','anchor_text':'quiet clue','time_local':1.2}]}
    cues = plan_directed_sound_cues(scene,2,library)
    assert [(cue['kind'],cue['visual_event_time_local']) for cue in cues] == [('marker_scratch',1.2)]
    assert plan_directed_sound_cues({**scene,'editorial_contract':{}},2,library)==[]


def test_transition_sound_follows_director_transition_object_and_sentence_boundary():
    library = {'whoosh_soft':{'src':'whoosh.wav','license':'generated'}}
    scene = {'start':4,'duration':4,'narration':'The turning point.',
        'primary_visual':{'type':'ChapterTitle'},'transition':{'template_id':'wipe-left'},
        'semantic_beat':{'source':'openrouter','sound_intent':'none'}}
    assert [cue['reason'] for cue in plan_directed_sound_cues(scene,1,library)]==['visual:transition']
    assert plan_directed_sound_cues({**scene,'narration':'The unfinished sentence'},1,library)==[]


def test_directed_date_count_uses_a_time_sound_with_the_same_event_clock():
    library = {kind: {'src': f'{kind}.wav', 'license': 'generated'}
               for kind in ('cash_counter', 'clock_tick', 'data_tick')}
    scene = {'start': 14, 'duration': 6, 'primary_visual': {'type': 'Timeline'},
             'semantic_beat': {'source': 'openrouter', 'sound_intent': 'none'},
             'motion_cues': [{'action': 'count', 'element': 'data',
                              'anchor_text': '6 February 1952', 'time_local': 3.8}]}
    cues = plan_directed_sound_cues(scene, 3, library)
    assert [(cue['kind'], cue['visual_event_time_local'], cue['start']) for cue in cues] == [
        ('clock_tick', 3.8, 17.8)]
    del library['clock_tick']
    assert plan_directed_sound_cues(scene, 3, library)[0]['kind'] == 'data_tick'
    count = copy.deepcopy(scene)
    count['primary_visual']['type'] = 'StatCard'
    count['motion_cues'][0]['anchor_text'] = '1952 people'
    assert plan_directed_sound_cues(count, 3, library)[0]['kind'] == 'cash_counter'


def test_visual_repair_rescores_layer_reveals_and_updates_export_sound_tracks():
    library = {'technical_tick':{'src':'tick.wav','license':'generated'}}
    scene = {'start':0,'duration':4,'narration':'The Moon blocks sunlight.',
        'primary_visual':{'type':'Diagram'},'graphic':{'template_id':'generated-recipe'},
        'semantic_beat':{'source':'openrouter','sound_intent':'none'},
        'template_recipe':{'layers':[{'id':'moon','kind':'sphere','semantic_actor':'Moon','motion':'scale','start_frame':30}]},
        'sound_cues':[{'kind':'old_photo_sound','src':'old.wav','time_local':0,'duration':.3}]}
    other={'start':4,'duration':4,'narration':'A quiet hold.','sound_cues':[]}
    manifest={'fps':30,'scenes':[scene,copy.deepcopy(other)],'audio':{'effects':[]}}
    rescore_revised_soundtrack(manifest,[0],library)
    assert [(cue['kind'],cue['visual_event_time_local']) for cue in scene['sound_cues']]==[('technical_tick',1.0)]
    assert manifest['audio']['effects']==scene['sound_cues']
    assert manifest['metrics']['sound_cue_count']==1
    assert manifest['scenes'][1]==other
    before=copy.deepcopy(manifest)
    manifest['editor_timeline']={'tracks':{'sfx':{'clips':[]}}}
    rescore_revised_soundtrack(manifest,[0],{})
    assert manifest['audio']==before['audio']


def test_still_motion_guard_survives_interleaved_video_scene():
    manifest = build_semantic_edit_manifest(
        ["A train crew prepares.", "The locomotive leaves the yard.", "The crew checks the signals."],
        [2, 2, 2],
        topic="Train crew documentary",
        media=[
            {"path": "C:/train-crew-1.jpg", "type": "image"},
            {"path": "C:/train-departure.mp4", "type": "video"},
            {"path": "C:/train-crew-2.jpg", "type": "image"},
        ],
        variation_seed=19,
    )
    still_motions = [
        scene["camera"]["motion"]
        for scene in manifest["scenes"]
        if str(scene["media"].get("type")).lower() == "image"
    ]
    assert all(left != right for left, right in zip(still_motions, still_motions[1:]))


def test_long_manifest_varies_treatments_transitions_and_passes_quality_audit():
    manifest = build_semantic_edit_manifest(
        [f"China's industrial transformation advances through scene {index}." for index in range(24)],
        [2.5] * 24,
        topic="How China became a global superpower",
        media=[{"path": f"C:/scene-{index}.jpg"} for index in range(24)],
        editorial_beats=[{"role": "context", "transition": "cut"} for _ in range(24)],
        variation_seed=118,
    )
    assert manifest["metrics"]["variation_audit"]["status"] == "pass"
    assert len(manifest["metrics"]["image_treatment_counts"]) >= 4
    assert len({scene["transition"]["template_id"] for scene in manifest["scenes"]}) >= 3
    treatments = [scene["design"]["image_treatment"] for scene in manifest["scenes"]]
    assert all(
        left != right or left == 'hold'
        for left, right in zip(treatments, treatments[1:])
    )


def test_animation_flow_changes_camera_and_transition_families_between_scenes():
    manifest = build_semantic_edit_manifest(
        [f"A different car detail anchors scene {index}." for index in range(14)],
        [2.5] * 14,
        topic="Automotive documentary",
        media=[{"path": f"C:/car-scene-{index}.jpg"} for index in range(14)],
        variation_seed=23,
    )
    motion_families = [scene["camera"]["motion_family"] for scene in manifest["scenes"]]
    transition_ids = [scene["transition"]["template_id"] for scene in manifest["scenes"][1:]]
    assert all(left != right for left, right in zip(motion_families, motion_families[1:]))
    assert len(set(transition_ids)) >= 3
    assert all(
        manifest["scenes"][index]["camera"]["motion_family"]
        != manifest["scenes"][index - 1]["camera"]["motion_family"]
        for index in range(1, len(manifest["scenes"]))
    )


def test_variation_seed_changes_design_not_semantics():
    texts = ["The estimated loss was 12 million dollars.", "A quote changed the public record."]
    first = build_semantic_edit_manifest(texts, [3, 3], topic="Documentary", variation_seed=1)
    second = build_semantic_edit_manifest(texts, [3, 3], topic="Documentary", variation_seed=2)
    assert [scene['narration'] for scene in first['scenes']] == [scene['narration'] for scene in second['scenes']]
    assert [scene['camera']['seed'] for scene in first['scenes']] != [scene['camera']['seed'] for scene in second['scenes']]


def test_whisper_anchored_sound_cue_preserves_asset_rights_and_timing():
    scene = {
        "scene_id": "scene-business-total",
        "start": 8.0,
        "duration": 3.0,
        "semantic_beat": {
            "source": "openrouter",
            "sound_intent": "impact",
            "sound_anchor": "12 million",
            "sound_reason": "A restrained accent on the narrated figure.",
        },
        "word_cues": [{
            "timing_quality": "whisper_word_aligned",
            "timed_words": [
                {"text": "12", "index": 4, "start": 1.2, "end": 1.5},
                {"text": "million", "index": 5, "start": 1.5, "end": 1.9},
            ],
        }],
    }
    cues = plan_sound_cues(
        scene,
        0,
        {"impact": {
            "src": "C:/licensed/impact.wav",
            "license": "CC0",
            "source_url": "https://example.test/impact",
        }},
    )
    assert len(cues) == 1
    cue = cues[0]
    assert cue['src'].endswith('impact.wav')
    assert cue['rights_status'] == 'CC0'
    assert cue['duck_voice'] > 0
    assert cue['sound_anchor'] == '12 million'
    assert cue['timing_quality'] == 'whisper_word_aligned'
    assert cue['start'] == 9.2


def test_template_manifest_exposes_real_template_contract():
    payload = get_template_manifest()
    assert payload['version'] == 1
    ids = {template['id'] for template in payload['templates']}
    assert {'map-route', 'bar-chart', 'paper-explainer', 'money-counter', 'end-card'} <= ids
    dossier = next(template for template in payload['templates'] if template['id'] == 'profile-dossier')
    assert dossier['performanceClass'] == 'medium'
    assert dossier['supportedAspectRatios'] == ['16:9', '9:16', '1:1']


def test_motion_router_uses_documentary_grammar_without_date_name_false_positive():
    texts = [
        'In April 1912, Titanic left Southampton.',
        'Captain Edward Smith took command.',
        'Radio operators received ice warnings in a log.',
        'The route crossed the North Atlantic.',
        'More than 1500 people died because flooding spread.',
    ]
    beats = [
        {'role': 'evidence', 'treatment': 'date'},
        {'role': 'person', 'emphasis': 'Captain Edward Smith', 'selected_for_text': True, 'importance': .95},
        {'role': 'evidence', 'treatment': 'document'}, {'role': 'location', 'emphasis': 'North Atlantic'},
        {'role': 'evidence', 'treatment': 'counter'},
    ]
    # Authored copy needs the measured word clock; a role hint alone must not
    # manufacture a person card without its text/timing contract.
    words = [{"word": word, "start": scene * 3 + token * 3 / len(text.split()),
              "end": scene * 3 + (token + 1) * 3 / len(text.split())}
             for scene, text in enumerate(texts) for token, word in enumerate(text.split())]
    manifest = build_semantic_edit_manifest(texts, [3] * len(texts), topic='RMS Titanic', editorial_beats=beats,
                                            word_timings=words)
    templates = [scene['graphic']['template_id'] for scene in manifest['scenes']]
    assert templates[0] in {'date-card', 'calendar-rip', 'timeline-trace'}
    assert templates[1] in {'lower-third', 'exhibit-label', 'field-caption'}
    assert templates[2] == 'full-bleed'
    assert templates[3] in {'full-bleed', 'map-card', 'map-route', 'route-progress', 'field-caption', 'timeline-trace'}
    assert templates[4] in {'stat-card', 'stat-counter', 'pictogram-stat'}
    assert manifest['scenes'][1]['graphic']['props']['title'] == 'Captain Edward Smith'


def test_four_digit_quantities_are_not_calendar_years():
    from services.editorial_cue_planner import _extract_candidates
    for text in ('1500 people died.', 'The mission cost 2000 dollars.', 'They traveled 1912 miles.'):
        candidates = _extract_candidates(text, {}, {}, {}, opening_scene=False)
        assert any(row.cue_type == 'statistic' for row in candidates)
        assert not any(row.cue_type == 'date' for row in candidates)
    candidates = _extract_candidates('In 1912 the voyage began.', {}, {}, {}, opening_scene=False)
    assert any(row.cue_type == 'date' and row.text == '1912' for row in candidates)


def test_vision_arrow_requires_explicit_normalized_target_box():
    accepted = build_semantic_edit_manifest(
        ['Notice the signal in the log.'], [3], topic='Test',
        media=[{
            'path': 'C:/fake.jpg',
            'visual_decision': {
                'subject_bbox': [.6, .2, .75, .35],
                'status': 'accepted',
                'confidence': 94,
            },
        }],
        editorial_beats=[{'role': 'evidence', 'emphasis': 'signal'}],
    )
    assert any(overlay['template_id'] == 'callout-arrow' for overlay in accepted['scenes'][0]['overlays'])
    rejected = build_semantic_edit_manifest(
        ['Notice the signal in the log.'], [3], topic='Test',
        media=[{
            'path': 'C:/fake.jpg',
            'visual_decision': {
                'subject_bbox': [0, 0, 1, 1],
                'status': 'accepted',
                'confidence': 94,
            },
        }],
        editorial_beats=[{'role': 'evidence', 'emphasis': 'signal'}],
    )
    assert not any(overlay['template_id'] == 'callout-arrow' for overlay in rejected['scenes'][0]['overlays'])


def test_vision_subject_box_object_is_normalized_into_focal_bbox():
    manifest = build_semantic_edit_manifest(
        ['Notice the signal in the log.'], [3], topic='Test',
        media=[{
            'path': 'C:/fake.jpg',
            'visual_decision': {
                'subject_box': {'x': .6, 'y': .2, 'width': .15, 'height': .15},
                'status': 'accepted',
                'confidence': 94,
            },
        }],
        editorial_beats=[{'role': 'evidence', 'emphasis': 'signal'}],
    )
    scene = manifest['scenes'][0]
    assert scene['design']['focal_bbox'] == [.6, .2, .75, .35]
    assert any(overlay['template_id'] == 'callout-arrow' for overlay in scene['overlays'])


def test_router_has_richer_documentary_treatments_without_default_zoom_pan_or_frame():
    texts = [
        'The engine design relied on watertight compartments.',
        'The decision was to wait or act immediately.',
        'A newspaper reported the warning on the front page.',
        'Forty minutes remained before it was too late.',
        'The hidden detail was later revealed in the archive.',
    ]
    manifest = build_semantic_edit_manifest(
        texts, [3] * len(texts), topic='RMS Titanic',
        editorial_beats=[
            {'role': 'context', 'treatment': 'blueprint'}, {'role': 'turn', 'treatment': 'decision'},
            {'role': 'evidence', 'treatment': 'newspaper'}, {'role': 'turn', 'treatment': 'countdown'},
            {'role': 'evidence', 'treatment': 'redaction'},
        ],
    )
    templates = [scene['graphic']['template_id'] for scene in manifest['scenes']]
    assert templates[0] in {'blueprint-reveal', 'cause-effect', 'decision-tree'}
    assert templates[1] in {'full-bleed', 'decision-tree', 'split-screen', 'cause-effect'}
    assert templates[2] in {'full-bleed', 'source-highlight', 'index-card', 'paper-explainer'}
    assert templates[3] in {'full-bleed', 'countdown-clock', 'calendar-rip', 'word-cascade', 'timeline-trace'}
    assert templates[4] in {'full-bleed', 'redaction-reveal', 'case-file', 'evidence-board', 'kinetic-statement'}
    motions = [scene['camera']['motion'] for scene in manifest['scenes']]
    assert not any('zoom' in motion or 'pan' in motion or 'push' in motion for motion in motions)
    assert not any(overlay['template_id'] == 'archival-frame' for scene in manifest['scenes'] for overlay in scene['overlays'])


def test_high_energy_scene_uses_one_distinct_whisper_anchored_effect():
    scene = {
        "scene_id": "scene-radio-signal",
        "start": 12.0,
        "duration": 4.0,
        "semantic_beat": {
            "source": "openrouter",
            "sound_intent": "signal",
            "sound_anchor": "radio signal",
            "sound_reason": "A brief radio accent on the spoken transmission.",
        },
        "word_cues": [{
            "timing_quality": "whisper_word_aligned",
            "timed_words": [
                {"text": "radio", "index": 20, "start": 1.3, "end": 1.6},
                {"text": "signal", "index": 21, "start": 1.6, "end": 2.0},
            ],
        }],
    }
    cues = plan_sound_cues(
        scene,
        0,
        {
            'radio_static': {'src': 'C:/sfx/radio.wav'},
            'riser': {'src': 'C:/sfx/riser.wav'},
            'reverse_swell': {'src': 'C:/sfx/swell.wav'},
        },
    )
    assert [cue['kind'] for cue in cues] == ['radio_static']
    assert cues[0]['start'] == 13.3
    assert cues[0]['sound_anchor'] == 'radio signal'


def test_ordinary_media_stays_silent_without_a_directed_word_anchor():
    manifest = build_semantic_edit_manifest(
        ['The story begins.', 'A quiet archival image holds on screen.'], [3, 3], topic='RMS Titanic',
        config={'sound_library': {
            'photo_slide': {'src': 'C:/sfx/slide.wav'}, 'camera_shutter': {'src': 'C:/sfx/camera.wav'},
            'paper_rustle': {'src': 'C:/sfx/paper.wav'}, 'room_tone': {'src': 'C:/sfx/room.wav'},
        }},
    )
    cues = manifest['scenes'][1]['sound_cues']
    assert cues == []


def test_generic_scene_scheduler_never_fakes_a_three_image_collage_or_overuses_a_template():
    manifest = build_semantic_edit_manifest(
        [f'An archival part of the story continues {index}.' for index in range(54)], [2.5] * 54,
        topic='RMS Titanic', media=[{'path': f'C:/single-source-{index}.jpg'} for index in range(54)],
    )
    counts = manifest['metrics']['template_counts']
    assert counts.get('photo-pinboard', 0) == 0
    assert counts.get('gallery-stack', 0) == 0
    exempt = {'full-bleed', 'lower-third', 'archival-frame'}
    assert all(count <= 5 for template_id, count in counts.items() if template_id not in exempt)
    # The product's default is roughly one graphic per three beats (~30%),
    # with opening/boundary rounding. This fixture supplies no authored
    # high-density instruction; the old 47–63% expectation was obsolete.
    assert 0.28 <= manifest['metrics']['graphic_scene_ratio'] <= 0.37
    assert manifest['metrics']['atmosphere_scene_count'] == len(manifest['scenes'])


def test_spotlight_is_never_selected_without_a_verified_bbox():
    without_box = build_semantic_edit_manifest(
        ['This detail changed the entire decision.'] * 12,
        [2.5] * 12,
        topic='Documentary',
        editorial_beats=[{'role': 'evidence', 'treatment': 'focus'}] * 12,
    )
    assert all(scene['graphic']['template_id'] != 'spotlight-lens' for scene in without_box['scenes'])
    with_box = build_semantic_edit_manifest(
        ['Notice this verified object in the frame.'] * 9,
        [3] * 9,
        topic='Documentary',
        media=[{'path': f'C:/verified-{index}.jpg', 'visual_decision': {
            'subject_bbox': [.2, .25, .42, .55],
            'status': 'accepted',
            'confidence': 96,
        }} for index in range(9)],
        editorial_beats=[{'role': 'evidence', 'treatment': 'focus'}] * 9,
    )
    spotlights = [
        scene for scene in with_box['scenes']
        if scene['graphic']['template_id'] == 'spotlight-lens'
    ]
    assert spotlights
    assert all(scene['graphic']['props']['bbox'] == [.2, .25, .42, .55] for scene in spotlights)


def test_world_trade_is_not_misclassified_as_a_person():
    manifest = build_semantic_edit_manifest(
        ['China joined the World Trade Organization in 2001.'],
        [3],
        topic='China',
    )
    scene = manifest['scenes'][0]
    assert scene['graphic']['template_id'] != 'profile-dossier'
    assert not any(
        overlay['template_id'] == 'lower-third'
        and overlay['props']['title'] == 'WORLD TRADE'
        for overlay in scene['overlays']
    )


def test_sound_history_obeys_sparse_global_budget_and_avoids_adjacent_loops():
    manifest = build_semantic_edit_manifest(
        [f'A different archival scene {index}.' for index in range(12)], [2.5] * 12, topic='History',
    )
    primary = [scene['sound_cues'][0]['kind'] for scene in manifest['scenes'] if scene['sound_cues']]
    assert len(primary) <= 2
    assert all(left != right for left, right in zip(primary, primary[1:]))
    assert manifest['metrics']['sound_cue_count'] == len(primary)


def test_chart_request_without_narrated_data_never_fabricates_a_graph():
    manifest = build_semantic_edit_manifest(
        [f'The policy changed the balance of power during phase {chr(65 + index)}.' for index in range(18)],
        [2.5] * 18,
        topic='Global strategy',
        editorial_beats=[{'role': 'comparison', 'treatment': 'chart'} for _ in range(18)],
        variation_seed=41,
    )
    graph_ids = {'bar-chart', 'line-chart', 'donut-chart', 'area-chart', 'slope-chart', 'ranked-bars'}
    assert not any(scene['graphic']['template_id'] in graph_ids for scene in manifest['scenes'])
    assert manifest['metrics']['quantitative_chart_count'] == 0
    assert all(
        not scene['design']['semantic_visual_intent']['quantitative']
        for scene in manifest['scenes']
    )


def test_real_data_graphs_are_budgeted_and_separated_by_other_visual_grammars():
    texts = [
        f'Exports rose from {10 + index} percent to {20 + index} percent during the measured period.'
        for index in range(30)
    ]
    manifest = build_semantic_edit_manifest(
        texts,
        [2.5] * len(texts),
        topic='Industrial growth',
        editorial_beats=[{'role': 'evidence', 'treatment': 'chart'} for _ in texts],
        variation_seed=92,
    )
    chart_indices = [
        index for index, scene in enumerate(manifest['scenes'])
        if scene['design']['visual_structure'] == 'quantitative_chart'
    ]
    assert chart_indices
    assert len(chart_indices) <= 3
    assert all(right - left >= 6 for left, right in zip(chart_indices, chart_indices[1:]))
    assert manifest['metrics']['variation_audit']['status'] == 'pass'


def test_project_seed_changes_template_sequence_without_changing_semantics():
    texts = [f'A documented turning point reshaped the story in chapter {index}.' for index in range(18)]
    beats = [{'role': 'evidence', 'importance': .7} for _ in texts]
    first = build_semantic_edit_manifest(
        texts, [2.5] * len(texts), topic='History', editorial_beats=beats, variation_seed=11,
    )
    second = build_semantic_edit_manifest(
        texts, [2.5] * len(texts), topic='History', editorial_beats=beats, variation_seed=12,
    )
    first_templates = [scene['graphic']['template_id'] for scene in first['scenes']]
    second_templates = [scene['graphic']['template_id'] for scene in second['scenes']]
    assert first_templates != second_templates
    assert [scene['narration'] for scene in first['scenes']] == texts
    assert [scene['narration'] for scene in second['scenes']] == texts
    assert first['metrics']['variation_audit']['status'] == 'pass'
    assert second['metrics']['variation_audit']['status'] == 'pass'


def test_validator_rejects_missing_narration_and_bad_timing():
    manifest = build_semantic_edit_manifest(["Valid scene"], [2], topic="Test")
    broken = copy.deepcopy(manifest)
    broken['scenes'][0]['narration'] = ''
    with pytest.raises(SemanticManifestError):
        validate_semantic_manifest(broken)
    broken = copy.deepcopy(manifest)
    broken['scenes'][0]['duration'] = 0
    with pytest.raises(SemanticManifestError):
        validate_semantic_manifest(broken)


def test_stable_seed_is_content_addressed():
    assert stable_seed('scene', 1) == stable_seed('scene', 1)
    assert stable_seed('scene', 1) != stable_seed('scene', 2)


def test_documentary_contract_prioritizes_dates_lists_and_safe_fonts():
    manifest = build_semantic_edit_manifest(
        [
            'On April 15, 1912, the ship struck the iceberg.',
            'There were three reasons the warning was missed: timing, distance, and silence.',
            'The archive continued without another designed overlay.',
        ],
        [3, 3, 3],
        topic='RMS Titanic history',
    )
    assert manifest['scenes'][0]['graphic']['template_id'] in {'date-card', 'calendar-rip', 'timeline-trace'}
    assert manifest['scenes'][1]['semantic_beat']['treatment'] == 'listicle'
    assert manifest['metrics']['graphic_scene_ratio'] <= 0.67
    typography = deterministic_profile('RMS Titanic history')['typography']
    assert typography['display'] not in {'caveat', 'patrick_hand', 'creepster', 'nosifer'}
    assert typography['body'] in {'inter', 'dm_sans', 'plus_jakarta'}


def test_clean_truncate_strips_auxiliary_verbs_and_broken_fragments():
    from services.semantic_edit_engine import _clean_truncate

    # Trailing auxiliary verb phrase "is still"
    assert _clean_truncate("Grand Theft Auto VI is still", 36) == "Grand Theft Auto VI"

    # Trailing "and this isn"
    assert _clean_truncate("most anticipated game. And this isn", 48) == "most anticipated game"

    # Leading broken contraction "s "
    assert _clean_truncate("s most anticipated game. And this isn", 48) == "most anticipated game"

    # Dangling prepositions
    assert _clean_truncate("from high-speed highway getaways to", 36) == "from high-speed highway getaways"


def test_safe_animated_fit_filter_eval_syntax_clean():
    from services.semantic_ffmpeg_renderer import _safe_animated_fit_filter
    motions = [
        "settle", "zoom_out", "zoom_in", "pan_left", "pan_right",
        "pan_up", "pan_down", "pan_diagonal_bl", "pan_diagonal_tr",
        "rotate_left", "rotate_right",
    ]
    for m in motions:
        vf = _safe_animated_fit_filter(m, 5.0, 1920, 1080)
        assert "+-" not in vf, f"Motion {m} produced invalid '+-' syntax: {vf}"
        assert "--" not in vf, f"Motion {m} produced invalid '--' syntax: {vf}"
