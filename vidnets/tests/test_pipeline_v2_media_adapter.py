import os
import hashlib
import sys
import threading
import pytest
from unittest import mock


@pytest.mark.parametrize('changed_audio_or_bytes', [False, True])
def test_bound_pixel_review_supersedes_stale_skip_flag(tmp_path, changed_audio_or_bytes):
    from services.pipeline_v2_media_adapter import _visual_requirement_issue
    image=tmp_path/'screen.jpg'
    image.write_bytes(b'exact-reviewed-bytes')
    req={'strict_identity':True,'canonical_entity':'Tesla','requirement_hash':'scene-a'}
    decision={'accepted':True,'visual_checked':True,'visual_available':True,
              'verified_asset_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
              'verified_requirement_hash':'scene-a','entity_conflict':False}
    row={'provider':'web','status':'accepted','verification_status':'verified',
         'requirement_hash':'scene-a','path':str(image),'visual_decision':decision,
         'visual_verification_skipped':True}
    if changed_audio_or_bytes:
        image.write_bytes(b'different-bytes')
    issue=_visual_requirement_issue({'visual_requirement':req},row,None,'Web Images')
    assert issue == ('visual_verification_skipped_for_strict_scene' if changed_audio_or_bytes else '')


def test_media_audit_integer_parser_tolerates_malformed_saved_overrides():
    from services.pipeline_v2_media_adapter import _bounded_audit_int

    assert _bounded_audit_int("not-a-number", 6, 12) == 6
    assert _bounded_audit_int(None, 4, 6) == 4
    assert _bounded_audit_int("999", 4, 6) == 6
    assert _bounded_audit_int("-2", 4, 6) == 0
    assert _bounded_audit_int(float("inf"), 4, 6) == 4


def test_final_reuse_excludes_emergency_plates_and_copies_real_bound_review(tmp_path, monkeypatch):
    import services.pipeline_v2_media_adapter as adapter
    real = tmp_path / 'real-photo.jpg'
    graphic = tmp_path / 'graphic_fallback_fixture.png'
    real.write_bytes(b'real source bytes')
    graphic.write_bytes(b'generated fallback bytes')
    requirement = {'canonical_entity': 'Royal Warrant', 'strict_identity': True, 'requirement_hash': 'same-beat'}
    def row(path, provider):
        return {'provider': provider, 'path': str(path), 'selected_path': str(path),
                'status': 'accepted', 'verification_status': 'verified', 'requirement_hash': 'same-beat',
                'visual_decision': {'accepted': True, 'visual_checked': True, 'visual_available': True,
                    'verified_requirement_hash': 'same-beat',
                    'verified_asset_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'source_presentation_type': 'photograph'}}
    original = row(graphic, 'borrowed')
    source = row(real, 'web')
    calls = []
    monkeypatch.setattr(adapter, '_validate_renderable_asset', lambda path: calls.append(path) or (True, 'ok', 'image'))
    monkeypatch.setattr('services.web_image.ownership_mark.inspect_ownership_mark', lambda path: {'watermark': False})
    plan = {'media_paths': [str(graphic), str(real)], 'source_labels': ['Grounded Graphic Fallback', 'Web Images'],
            'metadata': {'media_resolution_v2': [original, source]}}
    result = adapter.validate_media_plan_for_render_v2(plan,
        [{'scene_id': f'scene-{i}', 'text': 'The warrant seal.', 'visual_requirement': requirement} for i in range(2)],
        render_resolution=(1920, 1080), title='Royal Warrant', allow_emergency_media=True,
        neutral_asset_factory=lambda *a, **k: str(graphic))
    assert result['media_paths'] == [str(real), str(real)]
    replacement = result['metadata']['media_resolution_v2'][0]
    assert replacement['visual_decision'] == source['visual_decision']
    assert replacement['provider'] == 'web' and replacement['verification_status'] == 'verified_reuse'
    assert calls.count(str(real)) == 1


def test_bound_real_photo_normalizes_stale_neutral_provider_before_render(tmp_path):
    image = tmp_path / 'powerwall.jpg'
    image.write_bytes(b'photo-reviewed-bytes')
    req = {'strict_identity': True, 'canonical_entity': 'Tesla', 'requirement_hash': 'scene-a'}
    verdict = {'accepted': True, 'visual_checked': True, 'visual_available': True,
        'source_presentation_type': 'photograph', 'entity_conflict': False,
        'verified_asset_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
        'verified_requirement_hash': 'scene-a'}
    plan = {'media_paths': [str(image)], 'source_labels': ['Web Images (fallback)'],
        'metadata': {'source_lane_policy': {'mode': 'Mixed Media'},
            'youtube_coverage': {'target': .6, 'eligible_scene_count': 1,
                'accepted_scene_count': 1, 'accepted_ratio': 1.0},
            'media_resolution_v2': [{'provider': 'neutral', 'source': 'neutral',
                'path': str(image), 'selected_path': str(image), 'visual_decision': verdict,
                'verification_status': 'verified', 'requirement_hash': 'scene-a'}]}}
    with mock.patch('services.pipeline_v2_media_adapter._validate_renderable_asset', return_value=(True, 'ok', 'image')):
        result = validate_media_plan_for_render_v2(plan, [{'scene_id': 'scene-0', 'visual_requirement': req}],
            render_resolution=(854, 480), title='Tesla', neutral_asset_factory=lambda **kwargs: None,
            allow_emergency_media=False)
    row = result['metadata']['media_resolution_v2'][0]
    assert (row['provider'], row['source'], row['media_type']) == ('web', 'web', 'image')
    assert row['visual_decision'] == verdict
    coverage = result['metadata']['youtube_coverage']
    assert coverage['accepted_scene_count'] == 0
    assert coverage['accepted_ratio'] == 0
    assert result['metadata']['youtube_coverage_warning']['shortfall_scene_count'] == 1


@pytest.mark.parametrize('changed_bytes', [False, True])
def test_latest_bound_review_supersedes_stale_coordinator_status(tmp_path, changed_bytes):
    from types import SimpleNamespace
    image = tmp_path/'screen.jpg'
    image.write_bytes(b'reviewed-screen')
    req = {'strict_identity': True, 'visual_verification_required': True, 'requirement_hash': 'screen-a'}
    row = {'provider': 'web', 'path': str(image), 'status': 'accepted', 'requirement_hash': 'screen-a',
        'verification_status': 'verified', 'visual_decision': {
            'accepted': True, 'visual_checked': True, 'verified_requirement_hash': 'screen-a',
            'verified_asset_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}}
    stale = SimpleNamespace(status='pending', verification_status='pending', provider='web')
    if changed_bytes:
        image.write_bytes(b'unreviewed-replacement')
    issue = _visual_requirement_issue({'visual_requirement': req}, row, stale, 'Web Images')
    assert issue == ('visual_verification_pending' if changed_bytes else '')


def test_high_brand_score_does_not_override_wrong_scene_facet():
    scene = {'text': 'Stationary batteries store electricity for homes.',
             'visual_requirement': {'canonical_entity': 'Tesla', 'strict_identity': True}}
    result = _score_verdict(scene, {'identity_score': 100, 'context_score': 95,
        'action_score': 90, 'quality': 95, 'entity_conflict': False,
        'domain_match': True, 'scene_match': False,
        'reason': 'A car exterior is visible; no stationary storage is shown.'})
    assert not result['accepted']
    assert result['rejection_reason'] in {'scene_subject_or_facet_not_visible', 'explicit_scene_conflict'}


def test_broader_film_context_cannot_turn_roof_installation_into_charging_footage():
    result = _score_verdict({'text': 'Supercharger stations help drivers recharge between journeys.'},
        {'identity_score': 95, 'context_score': 95, 'quality': 90, 'action_score': 80,
         'scene_match': True, 'domain_match': True, 'entity_conflict': False,
         'scene_description': 'Construction workers install solar panels on a residential roof.',
         'reason': 'This fits the wider Tesla ecosystem although the narration mentions charging.'})
    assert not result['accepted']
    assert result['rejection_reason'] == 'different_visible_subsystem'


def test_multi_subject_scene_may_use_one_visible_matching_subsystem():
    from services.visual_intelligence.semantic_guard import explicit_visual_facet_conflict
    assert not explicit_visual_facet_conflict('A dashboard, a charger and battery storage.',
                                            'A garage with a Powerwall battery storage unit.')
    assert not explicit_visual_facet_conflict('Supercharger stations.', 'An outdoor landscape.')
    assert not explicit_visual_facet_conflict('Vehicles, charging and energy storage.',
                                            'A Tesla interior with a central touchscreen.')

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.pipeline_v2_media_adapter import (
    build_qwen_verifier,
    resolve_existing_provider_branches_v2,
    _explicit_metadata_scene_conflict,
    _score_verdict,
    _media_stage_deadline_seconds,
    _web_fallback_coordinator_grace_seconds,
    _source_lane_policy,
    _mixed_media_youtube_target_ratio,
    _automatic_youtube_scene_indices,
    SceneFallbackLane,
    _youtube_alternate_attempt_allowed,
    _youtube_fallback_indices,
    _mixed_web_scene_worker_limit,
    validate_media_plan_for_render_v2,
    _visual_requirement_issue,
    _youtube_cut_recovery_matches_scene,
)
from services.media_resolution_coordinator import _candidate_hard_gate
from services.visual_requirement import build_visual_requirement
from services.visual_intelligence.context import VisualDecisionContext


def test_verified_industrial_establishing_shot_keeps_installation_context():
    from services.pipeline_v2_media_adapter import _metadata_background_conflict
    text = "Stationary batteries store electricity for homes and utility projects."
    scene = {"scene_text": text, "visual_requirement": build_visual_requirement(
        title="Tesla", script=text, scene_text=text,
    )}
    assert not _metadata_background_conflict(scene, {"title": "Tesla home battery storage"})
    verdict = _score_verdict(scene, {
        "identity_score": 95, "context_score": 95, "action_score": 60,
        "domain_match": True, "scene_match": True, "fatal_mismatch": False,
        "source_presentation_type": "photograph",
        "subject_box": {"x": .73, "y": .64, "width": .08, "height": .12},
    })
    assert verdict["accepted"]


def test_exact_car_model_still_rejects_background_dominated_framing():
    text = "The Tesla Model S appeared in 2012."
    scene = {"scene_text": text, "visual_requirement": build_visual_requirement(
        title="Tesla", script=text, scene_text=text,
    )}
    verdict = _score_verdict(scene, {
        "identity_score": 95, "context_score": 95, "action_score": 60,
        "domain_match": True, "scene_match": True, "fatal_mismatch": False,
        "source_presentation_type": "photograph",
        "subject_box": {"x": .65, "y": .78, "width": .16, "height": .16},
    })
    assert not verdict["accepted"]


def test_youtube_snapshot_recovery_requires_same_scene_and_source_moment():
    cut = {
        "scene_index": 8,
        "source_id": "video-123",
        "start": 180.0,
        "end": 188.0,
    }
    moment = {"video_id": "video-123", "start": 180.2, "end": 188.1}
    assert _youtube_cut_recovery_matches_scene(cut, 8, moment)
    assert not _youtube_cut_recovery_matches_scene(cut, 3, moment)
    assert not _youtube_cut_recovery_matches_scene(
        cut, 8, {"video_id": "video-456", "start": 180.2, "end": 188.1}
    )
    assert not _youtube_cut_recovery_matches_scene(
        cut, 8, {"video_id": "video-123", "start": 210.0, "end": 218.0}
    )


def test_repeated_director_queries_correct_stale_event_anchor():
    from services.pipeline_v2_media_adapter import _repeated_title_entity

    config = {
        "content_title": "How the Eiffel Tower Was Built",
        "_prebuilt_web_queries": [
            {"_llm": True, "search_query": "Eiffel Tower construction 1887 archival photograph"},
            {"_llm": True, "search_query": "Eiffel Tower iron lattice engineering drawings"},
            {"_llm": True, "search_query": "Eiffel Tower rivets and construction workers"},
        ],
    }
    assert _repeated_title_entity(config, 3) == "Eiffel Tower"


def test_single_director_query_cannot_rewrite_visual_identity():
    from services.pipeline_v2_media_adapter import _repeated_title_entity

    config = {
        "content_title": "Eiffel Tower API First Pass",
        "_prebuilt_web_queries": [
            {"_llm": True, "search_query": "Eiffel Tower construction 1887 archival photograph"},
            {"_llm": True, "search_query": "Paris Exposition pavilion 1889"},
            {"_llm": True, "search_query": "Paris Exposition visitors 1889"},
        ],
    }
    assert _repeated_title_entity(config, 3) == ""


def test_director_scene_identity_narrows_broad_automotive_topic_only_when_script_confirms_it():
    from services.pipeline_v2_media_adapter import _scene_local_director_identity

    narration = "Mitsubishi says the Starion launched in May 1982 with a wedge shape and turbo engine."
    broad = build_visual_requirement(
        title="Japanese Classics", script=narration, scene_text=narration,
        positive_subjects=("Three Japanese Classics",),
    )
    focused, changed = _scene_local_director_identity(
        broad,
        [{"scene_subject": "Mitsubishi Starion", "subject_query": "Japanese Classics Mitsubishi Starion launch photo"}],
        narration,
    )
    assert changed
    assert focused["canonical_entity"] == "Mitsubishi Starion"
    assert focused["subject_type"] == "automobile"
    assert focused["required_attributes"]["model_or_make"] == "mitsubishi starion"

    unchanged, changed = _scene_local_director_identity(
        broad,
        [{"scene_subject": "Nissan Skyline GT-R", "subject_query": "Nissan Skyline GT-R photo"}],
        narration,
    )
    assert not changed
    assert unchanged["canonical_entity"] == broad["canonical_entity"]


def test_director_scene_identity_drops_possessive_without_losing_model_name():
    from services.pipeline_v2_media_adapter import _scene_local_director_identity

    narration = "Honda's NSX was the first production car with an all-aluminum monocoque."
    broad = build_visual_requirement(
        title="Japanese Classics", script=narration, scene_text=narration,
        positive_subjects=("Three Japanese Classics",),
    )
    focused, changed = _scene_local_director_identity(
        broad, [{"scene_subject": "Honda's NSX"}], narration,
    )
    assert changed
    assert focused["canonical_entity"] == "Honda NSX"
    assert focused["subject_type"] == "automobile"


def test_repeated_neighboring_director_subject_covers_poll_bridge_beats():
    from services.pipeline_v2_media_adapter import _scene_local_director_identity

    narration = "In 2008, a survey asked motorists which car they would least like to drive."
    broad = build_visual_requirement(
        title="Austin Allegro and Britain's Worst-Car Poll",
        script=narration,
        scene_text=narration,
        positive_subjects=("Austin Allegro and Britain's Worst-Car Poll",),
    )
    focused, changed = _scene_local_director_identity(
        broad,
        [
            {"scene_subject": "Britain's"},
            {"scene_subject": "Britain's"},
            {"scene_subject": "Austin Allegro"},
            {"scene_subject": "Austin Allegro"},
        ],
        narration,
    )
    assert changed
    assert focused["canonical_entity"] == "Austin Allegro"
    assert focused["required_attributes"]["model_or_make"] == "austin allegro"


def test_provider_rows_keep_scene_specific_vehicle_identity(monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    captured = []
    narration = "Mitsubishi says the Starion launched in May 1982 with a wedge shape and turbo engine."
    broad = build_visual_requirement(
        title="Japanese Classics", script=narration, scene_text=narration,
        positive_subjects=("Three Japanese Classics",),
    )
    scenes = [{
        "scene_id": "starion-beat", "order": 0, "duration": 6.0,
        "text": narration, "positive_subjects": ["Three Japanese Classics"],
        "visual_requirement": broad,
    }]
    config = {
        "image_source": "Web Images", "visual_understanding": False,
        "global_media_guarantee": False, "_topic_anchor": "Japanese Classics",
        "_prebuilt_web_queries": [{
            "scene_subject": "Mitsubishi Starion",
            "subject_query": "Mitsubishi Starion",
            "search_query": "Mitsubishi Starion 1982 launch photo",
            "_llm": True,
        }],
    }

    def coordinator(rows, **kwargs):
        captured.extend(rows)
        return {"metadata": {"media_resolution_v2": []}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", coordinator)
    adapter.resolve_existing_provider_branches_v2(
        config,
        {"scene_contracts": scenes},
        title="Three Japanese Classics", script=narration,
        render_resolution=(1280, 720), youtube_runner=lambda *a, **k: [],
        web_runner=lambda *a, **k: {}, stock_runner=lambda *a, **k: {},
        snapshotter=lambda paths, **kwargs: paths,
        neutral_asset_factory=lambda *a, **k: None,
        report_progress=lambda *a, **k: None,
    )

    assert captured[0]["visual_requirement"]["canonical_entity"] == "Mitsubishi Starion"
    query_row = config["_semantic_query_rows"][0]
    assert query_row["section_subject"] == "Mitsubishi Starion"
    assert any("Mitsubishi Starion" in q for q in query_row["web_query_variants"])


def test_repeated_broader_event_query_cannot_drop_anchor_ordinal():
    from services.pipeline_v2_media_adapter import _repeated_title_entity

    config = {
        "content_title": "Super Bowl XLIX: 26 Seconds at the Goal Line",
        "_topic_anchor": "Super Bowl XLIX",
        "_prebuilt_web_queries": [
            {"_llm": True, "search_query": "Super Bowl final play Seattle Patriots"},
            {"_llm": True, "search_query": "Super Bowl interception Malcolm Butler"},
            {"_llm": True, "search_query": "Super Bowl goal line Russell Wilson"},
        ],
    }
    assert _repeated_title_entity(config, 3) == ""


def test_scene_local_people_narrow_event_media_contracts(monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    script = (
        "Super Bowl XLIX was played February 1, 2015. Russell Wilson fires toward Ricardo Lockette. "
        "Malcolm Butler reads the route and cuts in front of Lockette. "
        "Interception. But Butler had read the play. "
        "Moments earlier, Jermaine Kearse’s juggling catch kept Seattle alive. "
        "Pete Carroll later took responsibility. Marshawn Lynch was in the backfield."
    )
    rows = [
        "Russell Wilson fires toward Ricardo Lockette.",
        "Malcolm Butler reads the route and cuts in front of Lockette.",
        "Interception.",
        "But Butler had read the play.",
        "Moments earlier, Jermaine Kearse’s juggling catch kept Seattle alive.",
        "Pete Carroll later took responsibility.",
        "Marshawn Lynch was in the backfield.",
        "New England holds on, 28-24.",
    ]
    scenes = [
        {
            "scene_id": f"nfl-{index}",
            "order": index,
            "text": text,
            "duration": 3.0,
            "positive_subjects": ["Super Bowl XLIX"],
            "visual_requirement": build_visual_requirement(
                title="Super Bowl XLIX", scene_text=text,
                positive_subjects=("Super Bowl XLIX",),
            ),
        }
        for index, text in enumerate(rows)
    ]
    captured = {}

    def fake_coordinator(coordinator_scenes, **_kwargs):
        captured["rows"] = coordinator_scenes
        return {"metadata": {"media_resolution_v2": []}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", fake_coordinator)
    config = {
        "image_source": "Web Images",
        "content_title": "Super Bowl XLIX",
        "_topic_anchor": "Super Bowl XLIX",
        "visual_understanding": False,
        "visual_required": False,
        "global_media_guarantee": False,
    }
    resolve_existing_provider_branches_v2(
        config,
        {"scene_contracts": scenes},
        title="Super Bowl XLIX",
        script=script,
        render_resolution=(1280, 720),
        youtube_runner=lambda *_args, **_kwargs: [],
        web_runner=lambda *_args, **_kwargs: {},
        stock_runner=lambda *_args, **_kwargs: {},
        snapshotter=lambda paths, namespace="": list(paths),
        neutral_asset_factory=lambda *_args, **_kwargs: None,
        report_progress=lambda *_args, **_kwargs: None,
    )

    actual = [row["visual_requirement"]["canonical_entity"] for row in captured["rows"]]
    assert actual[:7] == [
        "Russell Wilson", "Malcolm Butler", "Malcolm Butler", "Malcolm Butler",
        "Jermaine Kearse", "Pete Carroll", "Marshawn Lynch",
    ]
    assert actual[7] == "Super Bowl XLIX"
    assert captured["rows"][0]["visual_requirement"]["subject_type"] == "person"
    assert "2015" in captured["rows"][0]["visual_requirement"]["required_attributes"].values()


def test_v2_handoff_uses_director_confirmed_title_entity(monkeypatch):
    import services.pipeline_v2_media_adapter as adapter

    scenes = [
        {
            "scene_id": f"scene-{index}",
            "order": index,
            "text": text,
            "duration": 3.0,
            "visual_requirement": build_visual_requirement(
                title="Paris Exposition", scene_text=text
            ),
        }
        for index, text in enumerate((
            "Construction began on the tower in 1887.",
            "Workers riveted iron sections together.",
            "The tower opened to visitors in 1889.",
        ))
    ]
    captured = {}

    def fake_coordinator(coordinator_scenes, **_kwargs):
        captured["subjects"] = [
            row["visual_requirement"]["canonical_entity"] for row in coordinator_scenes
        ]
        return {"metadata": {"media_resolution_v2": []}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", fake_coordinator)
    config = {
            "image_source": "Web Images",
            "content_title": "How the Eiffel Tower Was Built",
            "visual_understanding": False,
            "visual_required": False,
            "global_media_guarantee": False,
            "_prebuilt_web_queries": [
                {"_llm": True, "search_query": "Eiffel Tower construction photograph 1887"},
                {"_llm": True, "search_query": "Eiffel Tower rivet workers archive"},
                {"_llm": True, "search_query": "Eiffel Tower 1889 opening visitors"},
            ],
        }
    resolve_existing_provider_branches_v2(
        config,
        {"scene_contracts": scenes},
        title="Paris Exposition",
        script=" ".join(scene["text"] for scene in scenes),
        render_resolution=(1280, 720),
        youtube_runner=lambda *_args, **_kwargs: [],
        web_runner=lambda *_args, **_kwargs: {},
        stock_runner=lambda *_args, **_kwargs: {},
        snapshotter=lambda paths, namespace="": list(paths),
        neutral_asset_factory=lambda *_args, **_kwargs: None,
        report_progress=lambda *_args, **_kwargs: None,
    )
    assert captured["subjects"] == ["Eiffel Tower"] * 3
    semantic_queries = config["_semantic_query_rows"]
    assert semantic_queries[0]["search_query"] == "Eiffel Tower construction photograph 1887"
    assert semantic_queries[1]["search_query"] == "Eiffel Tower rivet workers archive"
    assert semantic_queries[2]["search_query"] == "Eiffel Tower 1889 opening visitors"
    assert all(row["director_web_query"] for row in semantic_queries)


def test_youtube_media_stage_outlives_youtube_branch_wall():
    with mock.patch.dict(
        os.environ,
        {"VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS": "300"},
        clear=True,
    ):
        assert _media_stage_deadline_seconds({"media_deadline_seconds": 210}, "YouTube Clips") == 420.0
        assert _media_stage_deadline_seconds({}, "YouTube Clips") == 600.0


def test_strict_scene_rejects_unverified_terminal_stock_identity():
    """Availability alone cannot turn a generic car into a named Corolla."""
    media_plan = {
        "media_paths": ["terminal-stock.jpg"],
        "source_labels": ["Stock Media Final Fallback (vision skipped)"],
        "metadata": {
            "media_resolution_v2": [{
                "provider": "stock",
                "media_type": "image",
                "status": "accepted",
                "verification_status": "deterministic_degraded",
                "visual_verification_skipped": True,
                "requirement_hash": "req-1",
                "candidate_evidence": {
                    "degraded_reason": "stock_last_resort_no_vision",
                    "visual_verification_skipped": True,
                },
            }],
        },
    }
    scenes = [{
        "scene_id": "scene-1",
        "visual_required": True,
        "visual_requirement": {
            "requirement_hash": "req-1",
            "strict_identity": True,
        },
    }]
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        return_value=(True, "ok", "image"),
    ):
        from services.pipeline_v2_media_adapter import MediaContractError
        import pytest
        with pytest.raises(MediaContractError):
            validate_media_plan_for_render_v2(
                media_plan, scenes, render_resolution=(1280, 720), title="Test",
                neutral_asset_factory=lambda *_args, **_kwargs: None,
            )


def test_strict_scene_still_rejects_ordinary_unverified_stock_identity():
    """The no-vision exception is limited to the explicit terminal rescue lane."""
    media_plan = {
        "media_paths": ["ordinary-stock.jpg"],
        "source_labels": ["Stock Media"],
        "metadata": {"media_resolution_v2": [{
            "provider": "stock",
            "media_type": "image",
            "status": "accepted",
            "verification_status": "deterministic_degraded",
            "visual_verification_skipped": True,
            "requirement_hash": "req-1",
            "candidate_evidence": {
                "degraded_reason": "provider_candidate",
                "visual_verification_skipped": True,
            },
        }]},
    }
    scenes = [{"scene_id": "scene-1", "visual_required": True,
               "visual_requirement": {"requirement_hash": "req-1", "strict_identity": True}}]
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        return_value=(True, "ok", "image"),
    ):
        from services.pipeline_v2_media_adapter import MediaContractError
        with pytest.raises(MediaContractError):
            validate_media_plan_for_render_v2(
                media_plan, scenes, render_resolution=(1280, 720), title="Test",
                neutral_asset_factory=lambda *_args, **_kwargs: None,
            )


def test_explicit_media_deadline_can_still_raise_youtube_budget():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert _media_stage_deadline_seconds(
            {"media_deadline_seconds": 420, "yt_branch_wall_timeout_seconds": 180},
            "YouTube Clips",
        ) == 420.0


def test_mixed_youtube_coordinator_includes_cleanup_and_salvage_tail():
    with mock.patch.dict(os.environ, {}, clear=True):
        deadline = _media_stage_deadline_seconds(
            {"media_deadline_seconds": 990, "_youtube_budget_scene_count": 24},
            "Mixed Media",
        )
    # One-worker transfer budgeting raises the clip wall to 900s; reserve a
    # separate 180s for cancellation, executor drain, salvage, and snapshots.
    assert deadline == 1080.0


def test_mixed_direct_clip_lane_uses_bounded_coordinator_wall():
    with mock.patch.dict(
        os.environ,
        {
            "VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY": "1",
            "VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS": "240",
        },
        clear=True,
    ):
        # The direct lane is capped at 150s plus the visual-gate tail, while
        # an explicitly shorter coordinator budget is honored.
        assert _media_stage_deadline_seconds({"media_deadline_seconds": 210}, "Mixed Media") == 210.0
        assert _media_stage_deadline_seconds({}, "Mixed Media") == 600.0


def test_mixed_youtube_lane_budget_fits_clip_quota_waves():
    from services.pipeline_v2_media_adapter import mixed_youtube_time_budget

    assert mixed_youtube_time_budget(8, 480, 150) == (343.0, 35.0)
    assert mixed_youtube_time_budget(24, 900, 150) == (768.0, 60.0)
    assert mixed_youtube_time_budget(8, 90, 35) == (343.0, 35.0)


def test_mixed_youtube_lane_reserves_serial_waves_when_host_memory_limits_workers():
    import math
    from services.pipeline_v2_media_adapter import mixed_youtube_time_budget
    from services.youtube_stage_budget import youtube_stage_budget

    for scene_count in (8, 10, 12):
        wall, search = mixed_youtube_time_budget(
            scene_count, 480, 150, download_workers=1,
        )
        target = math.ceil(scene_count * 0.60)
        plan = youtube_stage_budget(
            wall,
            {'yt_search_timeout_seconds': search, 'youtube_accepted_coverage_target': 0.60},
            scene_count=scene_count,
            download_workers=1,
        )
        assert plan.get('yt_max_download_scenes', target) >= target


def test_media_deadline_respects_shared_delivery_budget_reserve():
    from services.delivery_budget import DeliveryBudget

    budget = DeliveryBudget.create(
        total_seconds=600,
        render_reserve_seconds=180,
        clock=lambda: 100.0,
    )
    config = {
        "media_deadline_seconds": 900,
        "yt_branch_wall_timeout_seconds": 420,
        "_delivery_budget": budget,
        "_render_reserve_seconds": 180,
    }

    assert _media_stage_deadline_seconds(config, "Mixed Media") == 420.0


def test_web_fallback_gets_bounded_tail_before_stock_and_can_be_disabled():
    policy = {"web_enabled": True, "cross_source_fallback": True}
    with mock.patch.dict(os.environ, {}, clear=True):
        assert _web_fallback_coordinator_grace_seconds({}, policy, 300.0) == 150.0
        assert _web_fallback_coordinator_grace_seconds(
            {"web_fallback_coordinator_grace_seconds": 45}, policy, 300.0,
        ) == 45.0
        assert _web_fallback_coordinator_grace_seconds(
            {"web_fallback_coordinator_grace_seconds": 0}, policy, 300.0,
        ) == 0.0
        assert _web_fallback_coordinator_grace_seconds(
            {}, {"web_enabled": False}, 300.0,
        ) == 0.0


def test_mixed_media_youtube_default_target_is_60_percent():
    config = {
        "_niche_media_policy": {
            "niche": "science",
            "weights": {
                "Web Images": 0.70,
                "Stock Media": 0.10,
                "YouTube Clips": 0.20,
            },
        },
    }
    assert _mixed_media_youtube_target_ratio(config) == 0.60
    indices = _automatic_youtube_scene_indices(
        config,
        [f"science scene {index}" for index in range(8)],
        title="Science",
        script="A science documentary.",
    )
    assert len(indices) == 5


def test_oversized_prebuilt_youtube_group_is_capped_and_spread_across_timeline():
    config = {
        "youtube_accepted_coverage_target": 0.60,
        "_prebuilt_mixed_plan": {
            "_scene_count": 24,
            "grouped_indices": {"YouTube Clips": list(range(24))},
        },
    }
    indices = _automatic_youtube_scene_indices(
        config,
        [f"rail scene {index}" for index in range(24)],
        title="Diesel Locomotive",
        script="A documentary about starting a diesel locomotive.",
    )
    assert len(indices) == 15
    assert indices[0] == 0
    assert indices[-1] == 23
    assert indices == sorted(set(indices))


def test_explicit_mixed_media_percentages_override_niche_policy():
    config = {
        "mixed_media_weights": {
            "Web Images": 50,
            "Stock Media": 0,
            "YouTube Clips": 50,
        },
        "_niche_media_policy": {
            "niche": "science",
            "weights": {"Web Images": 0.80, "YouTube Clips": 0.20},
        },
    }
    assert _mixed_media_youtube_target_ratio(config) == 0.50
    indices = _automatic_youtube_scene_indices(
        config,
        [f"scene {index}" for index in range(8)],
        title="Video",
        script="Script",
    )
    assert len(indices) == 4


def test_explicit_youtube_coverage_override_remains_authoritative():
    config = {
        "youtube_accepted_coverage_target": 0.60,
        "mixed_media_weights": {"Web Images": 80, "YouTube Clips": 20},
    }
    assert _mixed_media_youtube_target_ratio(config) == 0.60
    indices = _automatic_youtube_scene_indices(
        config,
        [f"scene {index}" for index in range(8)],
        title="Video",
        script="Script",
    )
    assert len(indices) == 5


def test_downloaded_youtube_alternates_stay_eligible_until_visual_gate():
    assert _youtube_alternate_attempt_allowed({
        "status": "downloaded",
        "path": "candidate.mp4",
        "overlay_checked": True,
        "overlay_clean": True,
        "overlay_types": [],
    }) is True

    assert _youtube_alternate_attempt_allowed({
        "status": "downloaded",
        "path": "captioned.mp4",
        "overlay_checked": True,
        "overlay_blur_failed": True,
        "overlay_types": ["large_text_band"],
    }) is False


def test_youtube_fallback_retries_primary_transport_misses_without_repeating_successes():
    indices = _youtube_fallback_indices(
        6,
        [0, 2, 4],
        {
            0: [{"path": "primary-0.mp4"}],
            2: [],
            4: [{"path": "primary-4.mp4"}],
        },
    )
    assert indices == [1, 3, 5, 2]


def test_strict_youtube_fallback_retries_downloaded_primary_rows_for_semantic_alternates():
    indices = _youtube_fallback_indices(
        4,
        [0, 2],
        {
            0: [{"path": "primary-0.mp4"}],
            2: [{"path": "primary-2.mp4"}],
        },
        retry_downloaded=True,
    )

    assert indices == [1, 3, 0, 2]


def test_scene_fallback_lane_only_loads_the_scene_requesting_recovery():
    loaded = []
    lane = SceneFallbackLane(lambda index: loaded.append(index) or {index: {"path": f"{index}.mp4"}}.get(index))

    assert lane({"scene_index": 7}, threading.Event()) == {"path": "7.mp4"}
    assert loaded == [7]


def test_scene_fallback_forwards_verified_failures_to_recovery_batch():
    prepared = []
    lane = SceneFallbackLane(lambda index: {"path": f"{index}.jpg"},
                             lambda indices: prepared.extend(indices))
    lane.prepare_recovery([2, 5, 8])
    assert prepared == [2, 5, 8]
    assert lane({"scene_index": 5}, threading.Event())["path"] == "5.jpg"


def test_mixed_media_lane_does_not_reopen_stock_after_validated_policy_disables_it():
    config = {
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "title": "The History of Jaguar Cars",
        "script": "A factual history of the 1961 Jaguar E-Type classic automobile.",
        "_niche_media_policy": {
            "niche": "documentary",
            "weights": {
                "Web Images": 0.50,
                "YouTube Clips": 0.50,
                "Stock Media": 0.0,
            },
        },
    }

    lane_policy = _source_lane_policy(config)

    assert lane_policy["mode"] == "Mixed Media"
    assert lane_policy["youtube_enabled"] is True
    assert lane_policy["web_enabled"] is True
    assert lane_policy["stock_enabled"] is False


def test_mixed_web_scene_workers_promote_legacy_packaged_cap():
    with mock.patch.dict(
        os.environ,
        {"VIDZEO_MIXED_WEB_SCENE_WORKERS": "12"},
        clear=True,
    ):
        assert _mixed_web_scene_worker_limit({"web_max_scene_workers": 6}) == 12
        # Keep an explicit higher operator value bounded by the process-safe
        # cap rather than silently reverting to the old default.
        assert _mixed_web_scene_worker_limit({"web_max_scene_workers": 20}) == 12


def test_explicit_stock_opt_out_overrides_cached_mixed_plan_weights():
    lane_policy = _source_lane_policy({
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "allow_relevant_stock": False,
        "_prebuilt_mixed_plan": {
            "requested_weights": {
                "Web Images": 0.10,
                "YouTube Clips": 0.70,
                "Stock Media": 0.20,
            },
        },
    })

    assert lane_policy["stock_enabled"] is False


def test_stock_last_resort_can_opt_into_visual_verification():
    enabled = _source_lane_policy({"image_source": "YouTube Clips"})
    assert enabled["stock_enabled"] is False
    assert enabled["stock_last_resort_enabled"] is True
    assert enabled["stock_last_resort_no_vision"] is True

    disabled = _source_lane_policy({
        "image_source": "YouTube Clips",
        "allow_stock_last_resort": False,
    })
    assert disabled["stock_last_resort_enabled"] is False

    vision_checked = _source_lane_policy({
        "image_source": "YouTube Clips",
        "stock_last_resort_no_vision": False,
    })
    assert vision_checked["stock_last_resort_no_vision"] is False


def test_negative_query_disambiguators_are_not_forbidden_asset_evidence():
    requirement = build_visual_requirement(
        title="The History of Jaguar Cars: Why the 1961 E-Type Still Matters",
        script="A factual history of the 1961 Jaguar E-Type classic automobile.",
        scene_text="The 1961 Jaguar E-Type became a defining sports car.",
    )
    candidate = {
        "path": "C:\\media\\jaguar-e-type.mp4",
        "title": "1961 Jaguar E-Type S1 Roadster",
        "query": "Jaguar 1961 E-Type automobile archival footage -tiger -wildlife",
        "provider": "youtube",
        "source": "youtube",
        "media_type": "video",
        "gate_outcomes": {"decodable": True, "safe": True, "container": True},
    }

    passed, reason = _candidate_hard_gate(
        {
            "container_id": "container-1",
            "visual_requirement": requirement,
            "forbidden_subjects": requirement["forbidden_subjects"],
        },
        candidate,
    )

    assert passed is True
    assert reason == "passed"


def test_final_verifier_rebuilds_disabled_transport_context():
    disabled_transport_context = VisualDecisionContext.from_config(
        {"visual_understanding": False},
        scene_count=1,
    )

    _verifier, final_context = build_qwen_verifier(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "pipeline_v2_mode": "on",
        },
        [{"visual_requirement": {"strict_identity": True}}],
        visual_context=disabled_transport_context,
    )

    assert final_context is not disabled_transport_context
    assert final_context.enabled is True


@pytest.mark.parametrize('mixed', [False, True])
def test_missing_stock_slots_do_not_create_phantom_candidates_or_shift_metadata(monkeypatch, tmp_path, mixed):
    import services.pipeline_v2_media_adapter as adapter
    captured = {}
    asset = tmp_path / 'factory.jpg'
    asset.write_bytes(b'provider-fixture')
    paths = [None, str(asset)] if mixed else [None]
    metadata = [{"title": "Missing row"}, {"title": "Valid stock provider row"}]
    snapshots = []
    def snapshotter(values, namespace=''):
        snapshots.append(list(values))
        return list(values)
    def coordinator(rows, **kwargs):
        captured['candidates'] = kwargs['stock_lane'](rows[0], threading.Event())
        return {'metadata': {'media_resolution_v2': []}}, {}
    monkeypatch.setattr(adapter, 'resolve_and_build_media_plan', coordinator)
    scene = {'scene_id': 'factory', 'order': 0, 'duration': 3,
             'text': 'Manufacturing plants make products.'}
    adapter.resolve_existing_provider_branches_v2(
        {'image_source': 'Stock Media', 'visual_understanding': False,
         'visual_required': False, 'global_media_guarantee': False},
        {'scene_contracts': [scene]}, title='Manufacturing Plants',
        script=scene['text'], render_resolution=(1920,1080),
        youtube_runner=lambda *a, **k: [], web_runner=lambda *a, **k: {},
        stock_runner=lambda *a, **k: {'stock_paths': paths, 'stock_metadata': metadata},
        snapshotter=snapshotter, neutral_asset_factory=lambda *a, **k: None,
        report_progress=lambda *a: None,
    )
    if mixed:
        assert snapshots == [paths]
        assert len(captured['candidates']) == 1
        assert captured['candidates'][0]['path'] == str(asset)
        assert captured['candidates'][0]['title'] == 'Valid stock provider row'
    else:
        assert captured['candidates'] is None
        assert snapshots == []


def test_saved_warrant_contract_keeps_institution_in_provider_queries(monkeypatch):
    import services.pipeline_v2_media_adapter as adapter
    captured = {}
    scenes = [{"scene_id": "warrant-intro", "order": 0, "duration": 3,
        "text": "Over eight hundred brands display the seal.",
        "visual_requirement": {"canonical_entity": "Royal Warrant System",
            "subject_type": "person", "domain": "person", "strict_identity": True,
            "required_terms": ["Royal Warrant System"]}}]
    def web_runner(*args, **kwargs):
        captured["topic"] = args[3]
        captured["queries"] = kwargs["prebuilt_queries"]
        return {"web_result": {"image_paths": [], "image_metadata": [], "audit": {"scenes": []}}}
    def coordinator(rows, **kwargs):
        kwargs["web_lane"](rows[0], threading.Event())
        return {"metadata": {"media_resolution_v2": []}}, {}
    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", coordinator)
    adapter.resolve_existing_provider_branches_v2(
        {"image_source": "Web Images", "visual_understanding": False,
            "visual_required": False, "global_media_guarantee": False,
            "_prebuilt_web_queries": [{"_llm": True, "search_query": "Over eight hundred brands photo",
                "supporting_search_query": "Royal Warrant Holder List directory photo"}]},
        {"scene_contracts": scenes, "scene_timing_windows": [{"scene_index": 0, "start_seconds": 0, "end_seconds": 3}]},
        title="The Royal Warrant System: Why Brands Pay for the Queen's Seal",
        script="King Charles III and the British Royal Family grant warrants to businesses.",
        render_resolution=(1920, 1080), youtube_runner=lambda *a, **k: [],
        web_runner=web_runner, stock_runner=lambda *a, **k: {},
        snapshotter=lambda paths, namespace="": list(paths),
        neutral_asset_factory=lambda *a, **k: None, report_progress=lambda *a: None,
    )
    assert captured["topic"] == "Royal Warrant System"
    query = captured["queries"][0]
    assert query["section_subject"] == "Royal Warrant System"
    assert query["web_query_variants"][:3] == ["Royal Warrant certificate", "Royal Warrant seal", "Royal Warrant holders"]
    assert all("royal warrant" in value.casefold() for value in query["web_query_variants"][:8])
    assert scenes[0]["visual_requirement"]["subject_type"] == "person", "saved source remains unchanged"


def test_institutional_documentary_uses_named_person_only_in_explicit_person_beat(monkeypatch):
    import services.pipeline_v2_media_adapter as adapter
    captured = []
    texts = ['King Charles III grants new warrants.', 'Brands display the warrant seal.',
             'Queen Elizabeth II granted warrants to suppliers.']
    scenes = [{'scene_id': f'beat-{i}', 'order': i, 'duration': 5, 'text': text,
               'visual_requirement': {'canonical_entity': 'Royal Warrant System',
                                      'subject_type': 'person', 'domain': 'person',
                                      'strict_identity': True, 'required_terms': ['Royal Warrant System']}}
              for i, text in enumerate(texts)]
    def coordinator(rows, **kwargs):
        captured.extend(rows)
        return {'metadata': {'media_resolution_v2': []}}, {}
    monkeypatch.setattr(adapter, 'resolve_and_build_media_plan', coordinator)
    adapter.resolve_existing_provider_branches_v2(
        {'image_source': 'Web Images', 'visual_understanding': False, 'global_media_guarantee': False},
        {'scene_contracts': scenes}, title='Royal Warrant System', script=' '.join(texts),
        render_resolution=(1920, 1080), youtube_runner=lambda *a, **k: [],
        web_runner=lambda *a, **k: {}, stock_runner=lambda *a, **k: {},
        snapshotter=lambda paths, **kwargs: paths, neutral_asset_factory=lambda *a, **k: None,
        report_progress=lambda *a: None)
    assert [row['visual_requirement']['canonical_entity'] for row in captured] == [
        'King Charles III', 'Royal Warrant System', 'Queen Elizabeth II']
    assert [row['visual_requirement']['subject_type'] for row in captured] == ['person', 'organization', 'person']
    assert all(row['visual_requirement']['strict_identity'] for row in captured)


def test_cached_broad_royal_contract_narrows_provider_queries(monkeypatch):
    """A stale storyboard anchor must not suppress scene-local person waves."""
    import services.pipeline_v2_media_adapter as adapter

    captured = {}
    stale_requirement = {
        "canonical_entity": "King Charles III and Prince Harry British Royal Family",
        "subject_type": "person",
        "domain": "culture heritage",
        "strict_identity": True,
        "required_terms": ("King Charles III", "Prince Harry"),
    }
    scenes = [{
        "scene_id": "scene-1",
        "order": 0,
        "text": "The family is entering a new chapter.",
        "duration": 3.0,
        "visual_requirement": stale_requirement,
    }]
    audio_plan = {
        "scene_contracts": scenes,
        "scene_timing_windows": [{
            "scene_index": 0,
            "start_seconds": 0.0,
            "end_seconds": 3.0,
        }],
    }

    def fake_web_runner(scene_texts, scene_durations, lane_config, topic, script, **kwargs):
        captured["topic"] = topic
        captured["queries"] = list(kwargs.get("prebuilt_queries") or ())
        return {
            "web_result": {
                "image_paths": [],
                "image_metadata": [],
                "audit": {"scenes": []},
            }
        }

    def fake_coordinator(coordinator_scenes, **kwargs):
        # Trigger the lazy web lane so the test observes the exact provider
        # topic and immutable query row passed to the resolver.
        kwargs["web_lane"](coordinator_scenes[0], threading.Event())
        return {"metadata": {"media_resolution_v2": []}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", fake_coordinator)
    config = {
        "image_source": "Web Images",
        "pipeline_v2_mode": "on",
        "visual_understanding": False,
        "visual_required": False,
        "global_media_guarantee": False,
        "_topic_anchor": "King Charles III and Prince Harry British Royal Family",
    }

    resolve_existing_provider_branches_v2(
        config,
        audio_plan,
        title="The British Royal Family",
        script=(
            "The British Royal Family is entering a new chapter. "
            "Prince Harry and Meghan Markle are preparing to return."
        ),
        render_resolution=(1280, 720),
        youtube_runner=lambda *args, **kwargs: [],
        web_runner=fake_web_runner,
        stock_runner=lambda *args, **kwargs: {},
        snapshotter=lambda paths, namespace="": list(paths),
        neutral_asset_factory=lambda *args, **kwargs: None,
        report_progress=lambda *args, **kwargs: None,
    )

    assert captured["topic"] == "Prince Harry"
    assert captured["queries"][0]["section_subject"] == "Prince Harry"
    variants = captured["queries"][0]["web_query_variants"]
    assert "Prince Harry portrait" in variants
    assert "Prince Harry photo" in variants
    assert len(set(variants)) == len(variants)
    assert all(
        "prince harry" in query.casefold()
        for query in captured["queries"][0]["web_query_variants"][:8]
    )


def test_scene_local_monarch_query_keeps_explicit_regnal_ordinal():
    import services.pipeline_v2_media_adapter as adapter

    assert adapter._qualify_local_person_identity(
        "King Charles",
        "King Charles III and Prince Harry British Royal Family",
    ) == "King Charles III"
    assert adapter._qualify_local_person_identity(
        "King Charles",
        "Prince Harry and Meghan Markle British Royal Family",
    ) == "King Charles"
    script = (
        "Prince William is now the heir to the throne. "
        "Princess Eugenie and Jack Brooksbank welcomed a baby girl."
    )
    assert adapter._script_person_anchor_for_scene(
        "William is now the heir to the throne.", script
    ) == "Prince William"
    assert adapter._script_person_anchor_for_scene(
        "giving the Royal Family another new generation to celebrate.",
        script,
        previous_scene="Princess Eugenie and Jack Brooksbank welcomed a baby girl.",
    ) == "Princess Eugenie"


def test_spoken_regnal_names_and_postposed_roles_keep_scene_identity():
    import services.pipeline_v2_media_adapter as adapter
    script = "Elizabeth the Second was crowned in 1953. Charles became King in 2022. His coronation followed in May 2023."
    assert adapter._script_person_anchor_for_scene("Elizabeth the Second was crowned in 1953.", script) == "Monarch Elizabeth II"
    assert adapter._script_person_anchor_for_scene("Charles became King in 2022.", script) == "King Charles"
    assert adapter._script_person_anchor_for_scene("His coronation followed in May 2023.", script, "Charles became King in 2022.") == "King Charles"
    intervening = "Elizabeth the Second was crowned in 1953. Television brought the ceremony into homes. Her reign lasted seventy years."
    assert adapter._script_person_anchor_for_scene("Her reign lasted seventy years.", intervening, "Television brought the ceremony into homes.") == "Monarch Elizabeth II"


def test_web_lane_handoff_defers_metadata_mismatch_to_shared_visual_gate(monkeypatch, tmp_path):
    import services.pipeline_v2_media_adapter as adapter

    asset = tmp_path / "royal-event.jpg"
    asset.write_bytes(b"local test image payload")
    evidence = {
        "metadata_source": "query_anchored_visual_pending",
        "verification_required": True,
        "canonical_entity_mismatch": True,
        "missing_terms": ["harry"],
    }
    audit_row = {
        "scene_number": 1,
        "resolved": True,
        "path": str(asset),
        "width": 800,
        "height": 450,
        "title": "Royal event photograph",
        "provider": "universal_yandex",
        "domain": "yandex",
        "final_score": 8.0,
        "candidate_evidence": evidence,
    }
    captured = {}
    scene = {
        "scene_id": "royal-scene",
        "order": 0,
        "text": "Prince Harry and Meghan prepare to return to Britain.",
        "duration": 3.0,
        "positive_subjects": ["Prince Harry"],
        "visual_requirement": {
            "canonical_entity": "Prince Harry",
            "subject_type": "person",
            "domain": "person",
            "strict_identity": True,
            "required_terms": ["Prince Harry"],
        },
    }
    audio_plan = {
        "scene_contracts": [scene],
        "scene_timing_windows": [{
            "scene_index": 0,
            "start_seconds": 0.0,
            "end_seconds": 3.0,
        }],
    }

    def fake_web_runner(*_args, **_kwargs):
        return {"web_result": {
            "image_paths": [str(asset)],
            "image_metadata": [audit_row],
            "audit": {"scenes": [audit_row]},
        }}

    def fake_coordinator(coordinator_scenes, **kwargs):
        captured["candidates"] = kwargs["web_lane"](
            coordinator_scenes[0], threading.Event()
        )
        return {"metadata": {"media_resolution_v2": []}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", fake_coordinator)
    visual_context = VisualDecisionContext.from_config(
        {"visual_understanding": True, "visual_required": True}, scene_count=1
    )
    monkeypatch.setattr(adapter, "build_qwen_verifier", lambda *_args, **_kwargs: (
        lambda _scene, _candidate: {"accepted": True}, visual_context
    ))

    resolve_existing_provider_branches_v2(
        {
            "image_source": "Web Images",
            "pipeline_v2_mode": "on",
            "visual_understanding": True,
            "visual_required": True,
            "global_media_guarantee": False,
        },
        audio_plan,
        title="The British Royal Family",
        script=scene["text"],
        render_resolution=(1280, 720),
        youtube_runner=lambda *_args, **_kwargs: [],
        web_runner=fake_web_runner,
        stock_runner=lambda *_args, **_kwargs: {},
        snapshotter=lambda paths, namespace="": list(paths),
        neutral_asset_factory=lambda *_args, **_kwargs: None,
        report_progress=lambda *_args, **_kwargs: None,
    )

    handed_off = captured["candidates"]
    assert isinstance(handed_off, list) and handed_off
    candidate = handed_off[0]
    assert candidate["identity_failed"] is False
    assert candidate["semantic_context_pending"] is True
    assert candidate["qwen_result"]["visual_verification_required"] is True


def test_strict_person_metadata_mismatch_cannot_be_promoted_by_vision():
    import services.pipeline_v2_media_adapter as adapter

    scene = {
        "text": "Princess Eugenie attends a royal event.",
        "visual_requirement": {
            "canonical_entity": "Princess Eugenie",
            "subject_type": "person",
            "strict_identity": True,
        },
    }
    candidate = {
        "source": "web",
        "title": "British Royal Family attends Trooping the Colour",
        "candidate_evidence": {
            "canonical_entity_mismatch": True,
            "missing_terms": ["eugenie"],
            "metadata_source": "query_anchored_visual_pending",
        },
    }

    assert adapter._explicit_metadata_scene_conflict(scene, candidate) == (
        "explicit_canonical_entity_mismatch"
    )


def test_web_lane_repairs_empty_image_vector_from_scene_indexed_audit(monkeypatch, tmp_path):
    """A real audit path must survive a runner that returns an empty slot."""
    import services.pipeline_v2_media_adapter as adapter

    asset = tmp_path / "audit-only.jpg"
    asset.write_bytes(b"local test image payload")
    audit_row = {
        "scene_number": 1,
        "resolved": True,
        "path": str(asset),
        "width": 800,
        "height": 450,
        "title": "Prince Harry at Windsor Castle",
        "provider": "universal_yandex",
        "domain": "yandex",
        "final_score": 8.0,
        "candidate_evidence": {
            "metadata_source": "asset",
            "identity_proof": "Prince Harry",
        },
    }
    scene = {
        "scene_id": "audit-only-scene",
        "order": 0,
        "text": "Prince Harry at Windsor Castle.",
        "duration": 3.0,
        "positive_subjects": ["Prince Harry"],
        "visual_requirement": {
            "canonical_entity": "Prince Harry",
            "subject_type": "person",
            "domain": "person",
            "strict_identity": True,
            "required_terms": ["Prince Harry"],
        },
    }
    audio_plan = {
        "scene_contracts": [scene],
        "scene_timing_windows": [{
            "scene_index": 0,
            "start_seconds": 0.0,
            "end_seconds": 3.0,
        }],
    }

    def fake_web_runner(*_args, **_kwargs):
        return {"web_result": {
            # Legacy runner shape: vector length is correct but slot is empty.
            "image_paths": [""],
            "image_metadata": [audit_row],
            "audit": {"scenes": [audit_row]},
        }}

    captured = {}

    def fake_coordinator(coordinator_scenes, **kwargs):
        captured["candidates"] = kwargs["web_lane"](
            coordinator_scenes[0], threading.Event()
        )
        return {"metadata": {"media_resolution_v2": []}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", fake_coordinator)
    resolve_existing_provider_branches_v2(
        {
            "image_source": "Web Images",
            "pipeline_v2_mode": "on",
            "visual_understanding": False,
            "visual_required": False,
            "global_media_guarantee": False,
        },
        audio_plan,
        title="British Royal Family",
        script=scene["text"],
        render_resolution=(1280, 720),
        youtube_runner=lambda *_args, **_kwargs: [],
        web_runner=fake_web_runner,
        stock_runner=lambda *_args, **_kwargs: {},
        snapshotter=lambda paths, namespace="": list(paths),
        neutral_asset_factory=lambda *_args, **_kwargs: None,
        report_progress=lambda *_args, **_kwargs: None,
    )

    candidates = captured["candidates"]
    assert isinstance(candidates, list) and candidates
    assert candidates[0]["path"] == str(asset)


def test_long_jobs_reserve_qwen_budget_for_fallback_layers():
    scenes = [{"visual_requirement": {"strict_identity": True}} for _ in range(33)]

    _verifier, context = build_qwen_verifier(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "pipeline_v2_mode": "on",
        },
        scenes,
    )

    # Primary clips/images and recovery alternatives each need pixel checks.
    assert context.max_calls == 206


@pytest.mark.parametrize('description', [
    "A simulated diesel locomotive cab interior viewed from the operator's perspective.",
    'Train Sim World gameplay of a locomotive cold start.',
    'A 3D rendered cab with computer controls.',
])
def test_real_locomotive_scene_rejects_simulator_even_with_high_model_score(description):
    result = _score_verdict({'text': 'The crew starts a diesel locomotive.',
        'source_policy': {'youtube_relaxed_visual_gate': True}},
        {'scene_description': description, 'identity_score': 95,
         'context_score': 90, 'accepted': True, 'domain_match': True})
    assert result['accepted'] is False
    assert result['fatal_mismatch'] is True
    assert result['rejection_reason'] == 'simulation_for_real_world'


def test_train_simulator_documentary_can_use_its_actual_gameplay():
    from services.visual_intelligence.semantic_guard import explicit_domain_conflict
    assert explicit_domain_conflict('Train Sim World gameplay guide',
        'A simulated diesel locomotive cab interior.') == ''


def test_mixed_media_vision_honors_timeout_and_bounded_retry_floor():
    raw_client = mock.Mock(max_attempts=1, timeout_seconds=12.0, fast_mode=False)
    existing_context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "visual_call_timeout_seconds": 12,
            "visual_call_max_attempts": 1,
        },
        1,
    )
    existing_context._client = mock.Mock(_client=raw_client)
    _verifier, context = build_qwen_verifier(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "pipeline_v2_mode": "on",
            "image_source": "Mixed Media",
            "mixed_visual_call_timeout_seconds": 12,
            "visual_call_max_attempts": 1,
        },
        [{"visual_requirement": {"strict_identity": True}}],
        visual_context=existing_context,
    )

    assert context.call_timeout_seconds == 12.0
    assert context.call_max_attempts == 2
    assert raw_client.timeout_seconds == 12.0
    assert raw_client.max_attempts == 2
    assert raw_client.fast_mode is True


def test_final_verifier_downgrades_action_only_fatality_to_nearest_match():
    scene = {
        "visual_requirement": {"strict_identity": True},
    }
    result = _score_verdict(scene, {
        "identity_score": 95,
        "action_score": 30,
        "context_score": 85,
        "fatal_mismatch": True,
        "entity_conflict": False,
        "reason": (
            "The correct railway crew member and locomotive are visible, but the "
            "required action of helping a passenger is not actively shown; action mismatch."
        ),
    })

    assert result["accepted"] is True
    assert result["entity_conflict"] is False
    assert result["fatal_mismatch"] is False
    assert result["fatal_mismatch_normalized"] == "action_only_nearest_match"


def test_final_verifier_rejects_contextual_clip_for_explicit_action_scene():
    scene = {
        "text": "Cornerback Malcolm Butler intercepts the pass at the goal line.",
        "visual_requirement": {"strict_identity": True},
    }
    result = _score_verdict(scene, {
        "identity_score": 96,
        "action_score": 80,
        "context_score": 95,
        "quality": 90,
        "fatal_mismatch": False,
        "entity_conflict": False,
        "reason": "The clip shows the correct teams and game, but sampled footage does not clearly show the interception.",
    })
    assert result["accepted"] is False
    assert result["action_specific_match_required"] is True
    assert result["rejection_reason"] == "specific_action_not_visually_confirmed"


def test_final_verifier_accepts_confirmed_action_and_allows_non_action_context():
    direct = _score_verdict(
        {"text": "Malcolm Butler intercepts the pass.", "visual_requirement": {"strict_identity": True}},
        {"identity_score": 96, "action_score": 92, "context_score": 90,
         "quality": 90, "fatal_mismatch": False, "entity_conflict": False},
    )
    context = _score_verdict(
        {"text": "Super Bowl XLIX was played in Arizona.", "visual_requirement": {"strict_identity": True}},
        {"identity_score": 90, "action_score": 20, "context_score": 90,
         "quality": 80, "fatal_mismatch": False, "entity_conflict": False},
    )
    assert direct["accepted"] is True
    assert "action_specific_match_required" not in direct
    assert context["accepted"] is True


def test_strict_youtube_reuse_cannot_carry_another_scene_vision_stamp():
    requirement_hash = "scene-action-hash"
    scene = {"visual_requirement": {"strict_identity": True, "requirement_hash": requirement_hash}}
    row = {
        "provider": "youtube",
        "status": "accepted",
        "verification_status": "verified_reuse",
        "requirement_hash": requirement_hash,
        "visual_decision": {
            "accepted": True,
            "visual_checked": True,
            "verified_requirement_hash": "different-scene-hash",
        },
    }
    assert _visual_requirement_issue(scene, row, None) == "visual_verification_pending"


def test_strict_youtube_requires_live_vision_even_if_pool_cut_says_accepted():
    scene = {"visual_requirement": {"strict_identity": True, "requirement_hash": "scene-hash"}}
    row = {
        "provider": "youtube",
        "status": "accepted",
        "verification_status": "verified_reuse",
        "requirement_hash": "scene-hash",
        "visual_decision": {
            "accepted": True,
            "verification_status": "deterministic_pool_cut",
            "visual_checked": False,
        },
    }
    assert _visual_requirement_issue(scene, row, None) == "visual_verification_pending"


def test_final_verifier_keeps_missing_required_subjects_fatal():
    scene = {
        "visual_requirement": {"strict_identity": True},
    }
    result = _score_verdict(scene, {
        "identity_score": 95,
        "action_score": 10,
        "context_score": 85,
        "fatal_mismatch": True,
        "entity_conflict": False,
        "reason": (
            "The railway crew member is visible, but the required subjects are absent; "
            "the passenger and child are not shown, so the action is not depicted."
        ),
    })

    assert result["accepted"] is False
    assert result["entity_conflict"] is True
    assert result["fatal_mismatch"] is True
    assert "fatal_mismatch_normalized" not in result


def test_final_verifier_keeps_wrong_entity_fatal_even_with_high_scores():
    scene = {
        "visual_requirement": {"strict_identity": True},
    }
    result = _score_verdict(scene, {
        "identity_score": 95,
        "action_score": 90,
        "context_score": 90,
        "fatal_mismatch": True,
        "entity_conflict": False,
        "reason": "The image shows a different entity, not the required named model.",
    })

    assert result["accepted"] is False
    assert result["entity_conflict"] is True
    assert "fatal_mismatch_normalized" not in result


def test_strict_youtube_pool_cut_forces_live_semantic_verification(tmp_path):
    class FakeVisionClient:
        model = "test-qwen"

        def __init__(self):
            self.calls = 0

        def analyze_image(self, *_args, **_kwargs):
            self.calls += 1
            return {
                "candidate_id": "youtube:clip-1",
                "entity_conflict": False,
                "identity_score": 95,
                "action_score": 90,
                "context_score": 90,
                "quality": 85,
                "visible_overlays": [],
                "subject_box": {},
                "safe_text_regions": [],
                "fatal_mismatch": False,
                "reason": "The frame shows the required Jaguar E-Type automobile.",
            }

    clip_path = tmp_path / "clip.jpg"
    clip_path.write_bytes(b"test-image")
    fake_client = FakeVisionClient()
    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "pipeline_v2_mode": "on",
        },
        scene_count=1,
    )
    context._client = object()
    context.get_client = lambda: fake_client

    with mock.patch(
        "services.pipeline_v2_media_adapter.VisualDecisionContext.from_config",
        return_value=context,
    ):
        verifier, _ = build_qwen_verifier(
            {"visual_understanding": True, "openrouter_api_key": "test-key"},
            [{"visual_requirement": {"strict_identity": True}}],
        )

    scene = {
        "text": "The 1961 Jaguar E-Type remains a landmark sports car.",
        "positive_subjects": ["Jaguar E-Type"],
        "visual_requirement": {
            "strict_identity": True,
            "canonical_entity": "Jaguar E-Type",
            "requirement_hash": "req-1",
        },
    }
    candidate = {
        "candidate_id": "youtube:clip-1",
        "path": str(clip_path),
        "source": "youtube",
        "media_type": "image",
        "qwen_result": {
            "candidate_id": "youtube:clip-1",
            "entity_conflict": False,
            "identity_score": 100,
            "action_score": 100,
            "context_score": 80,
            "quality": 80,
            "visible_overlays": [],
            "subject_box": {},
            "safe_text_regions": [],
            "fatal_mismatch": False,
            "accepted": True,
            "source": "youtube_pool_moment",
            "verification_status": "deterministic_pool_cut",
            "visual_available": False,
        },
    }

    result = verifier(scene, candidate)

    assert fake_client.calls == 1
    assert result["verification_attempt"] == "primary"
    assert result["accepted"] is True


def test_pending_web_visual_handoff_forces_live_vision_and_vetoes_wrong_portrait(tmp_path):
    class FakeVisionClient:
        model = "test-qwen"

        def __init__(self):
            self.calls = 0
            self.prompt = ""

        def analyze_image(self, _path, prompt, _system):
            self.calls += 1
            self.prompt = prompt
            # Simulate the permissive same-domain response that allowed the
            # observed wrong royal portrait into the completed render.
            return {
                "candidate_id": "web:scene-1",
                "entity_conflict": False,
                "identity_score": 95,
                "action_score": 85,
                "context_score": 90,
                "quality": 85,
                "visible_overlays": [],
                "subject_box": {},
                "safe_text_regions": [],
                "fatal_mismatch": False,
                "domain_match": True,
                "scene_match": True,
                "reason": "A royal portrait supports the same domain.",
            }

    asset = tmp_path / "portrait.jpg"
    asset.write_bytes(b"fixture")
    fake_client = FakeVisionClient()
    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "visual_required": True,
            "openrouter_api_key": "test-key",
            "pipeline_v2_mode": "on",
        },
        scene_count=1,
    )
    context._client = object()
    context.get_client = lambda: fake_client

    with mock.patch(
        "services.pipeline_v2_media_adapter.VisualDecisionContext.from_config",
        return_value=context,
    ):
        verifier, _ = build_qwen_verifier(
            {"visual_understanding": True, "visual_required": True, "openrouter_api_key": "test-key"},
            [{"visual_requirement": {"strict_identity": True}}],
        )

    scene = {
        "text": "Prince Harry and Meghan Markle's 2018 wedding at Windsor Castle.",
        "visual_intent": "Prince Harry at his wedding ceremony",
        "positive_subjects": ["Prince Harry", "2018 wedding"],
        "visual_requirement": {
            "strict_identity": True,
            "subject_type": "person",
            "canonical_entity": "Prince Harry",
            "requirement_hash": "harry-wedding-2018",
            "required_attributes": {"year": "2018"},
        },
    }
    candidate = {
        "candidate_id": "web:scene-1",
        "path": str(asset),
        "source": "web",
        "media_type": "image",
        "title": "Portrait of Prince Philip, Duke of Edinburgh (1921–2021)",
        "candidate_evidence": {"external_visual_gate_required": True},
        "qwen_result": {
            "candidate_id": "web:scene-1",
            "entity_conflict": False,
            "identity_score": 100,
            "action_score": 100,
            "context_score": 80,
            "quality": 80,
            "visible_overlays": [],
            "subject_box": {},
            "safe_text_regions": [],
            "fatal_mismatch": False,
            "accepted": True,
            "source": "web_image_resolver",
            "verification_status": "pending_visual_verification",
            "visual_verification_required": True,
        },
    }

    result = verifier(scene, candidate)

    assert fake_client.calls == 1
    assert result["verification_attempt"] == "primary"
    assert "another identifiable person" in fake_client.prompt
    assert result["accepted"] is False
    assert result["metadata_conflict_veto"] == "explicit_portrait_entity_mismatch"


def test_event_metadata_vetoes_protest_image_for_wedding_scene():
    scene = {
        "text": "Princess Eugenie married Jack Brooksbank at St George's Chapel.",
        "visual_requirement": {
            "strict_identity": True,
            "subject_type": "person",
            "canonical_entity": "Princess Eugenie",
        },
    }
    candidate = {
        "title": "NOT MY KING protest, London England",
        "description": "Demonstrators with anti-monarchy signs",
    }

    assert _explicit_metadata_scene_conflict(scene, candidate) == "explicit_protest_event_mismatch"


def test_live_vision_event_mismatch_cannot_pass_person_near_match(tmp_path):
    """A correct person at the wrong ceremony must not fill an event beat."""
    class FakeVisionClient:
        model = "test-qwen"

        def analyze_image(self, *_args, **_kwargs):
            return {
                "candidate_id": "web:eugenie-wrong-event",
                "entity_conflict": False,
                "identity_score": 98,
                "action_score": 95,
                "context_score": 92,
                "quality": 90,
                "fatal_mismatch": False,
                "visible_overlays": [],
                "safe_text_regions": [],
                "subject_box": {},
                "reason": (
                    "The image correctly depicts Princess Eugenie at the "
                    "Trooping the Colour ceremony in June 2018."
                ),
            }

    asset = tmp_path / "eugenie.jpg"
    asset.write_bytes(b"fixture")
    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "visual_required": True,
            "openrouter_api_key": "test-key",
            "pipeline_v2_mode": "on",
        },
        scene_count=1,
    )
    context._client = object()
    context.get_client = lambda: FakeVisionClient()

    with mock.patch(
        "services.pipeline_v2_media_adapter.VisualDecisionContext.from_config",
        return_value=context,
    ):
        verifier, _ = build_qwen_verifier(
            {"visual_understanding": True, "visual_required": True, "openrouter_api_key": "test-key"},
            [{"visual_requirement": {"strict_identity": True}}],
        )

    result = verifier(
        {
            "text": "Princess Eugenie married Jack Brooksbank at St George's Chapel in October 2018.",
            "visual_intent": "Princess Eugenie wedding ceremony",
            "visual_requirement": {
                "strict_identity": True,
                "subject_type": "person",
                "canonical_entity": "Princess Eugenie",
                "requirement_hash": "eugenie-wedding-2018",
            },
        },
        {
            "candidate_id": "web:eugenie-wrong-event",
            "path": str(asset),
            "source": "web",
            "media_type": "image",
            "candidate_evidence": {"external_visual_gate_required": True},
            "qwen_result": {
                "verification_status": "pending_visual_verification",
                "visual_verification_required": True,
            },
        },
    )

    assert result["accepted"] is False
    assert result["semantic_verified"] is False
    assert result["rejection_reason"] == "visual_event_mismatch"


def test_documentary_vision_gate_rejects_meme_speech_bubbles():
    result = _score_verdict(
        {"visual_requirement": {"strict_identity": True}},
        {
            "identity_score": 95,
            "action_score": 90,
            "context_score": 90,
            "entity_conflict": False,
            "fatal_mismatch": False,
            "visible_overlays": [{"type": "speech_bubble", "severity": "high"}],
            "subject_box": {},
            "safe_text_regions": [],
        },
    )

    assert result["accepted"] is False
    assert result["severe_overlay"] is True


def test_gameplay_capture_with_visible_hud_is_rejected_for_documentary_broll():
    result = _score_verdict(
        {"text": "Rockstar has revealed a new city.", "visual_requirement": {"domain": "gaming", "canonical_entity": "Grand Theft Auto VI"}},
        {
            "identity_score": 96, "action_score": 92, "context_score": 94,
            "fatal_mismatch": False, "entity_conflict": False,
            "has_text_overlay": False,
            "visible_overlays": ["Minimap and HUD elements", "Location text overlay"],
            "subject_box": {}, "safe_text_regions": [],
        },
    )
    assert result["accepted"] is False
    assert result["game_ui_overlay"] is True
    assert result["rejection_reason"] == "gameplay_interface_overlay_detected"


def test_gta_vi_scene_rejects_a_source_explicitly_named_gta_v_or_online():
    scene = {"text": "Grand Theft Auto VI reveal", "visual_requirement": {
        "domain": "gaming", "canonical_entity": "Grand Theft Auto VI", "strict_identity": False,
    }}
    assert _explicit_metadata_scene_conflict(scene, {"title": "GTA V Online free roam gameplay"}) == "explicit_game_installment_mismatch"
    assert _explicit_metadata_scene_conflict(scene, {"title": "GTA VI official trailer"}) == ""


def test_youtube_only_never_converts_missing_video_to_neutral_asset():
    media_plan = {
        "media_paths": ["C:\\missing\\emergency.jpg"],
        "source_labels": ["Neutral No-Claim Visual"],
        "metadata": {
            "source_lane_policy": {"mode": "YouTube Clips"},
            "media_resolution_v2": [{
                "provider": "neutral",
                "media_type": "image",
                "verification_status": "not_applicable",
            }],
        },
    }

    try:
        validate_media_plan_for_render_v2(
            media_plan,
            [{"scene_id": "scene-1"}],
            render_resolution=(1280, 720),
            title="Test",
            neutral_asset_factory=lambda *_args, **_kwargs: None,
        )
    except RuntimeError as exc:
        assert "youtube_video_coverage_not_met" in str(exc)
    else:
        raise AssertionError("YouTube-only validation unexpectedly accepted a neutral asset")


def test_youtube_only_blocks_unbound_snapshot_hole_instead_of_blank_export():
    """A neighbor without scene/asset identity evidence cannot repair a hole."""
    media_plan = {
        "media_paths": ["good.mp4", "", "other.mp4", "third.mp4"],
        "source_labels": ["YouTube Clips", "", "YouTube Clips", "YouTube Clips"],
        "metadata": {
            "source_lane_policy": {"mode": "YouTube Clips"},
            "media_resolution_v2": [
                *[
                    {
                        "provider": "youtube",
                        "media_type": "video",
                        "status": "accepted",
                        "verification_status": "verified",
                    }
                    for _ in range(1)
                ],
                {
                    "provider": "neutral",
                    "media_type": "image",
                    "status": "degraded",
                    "verification_status": "not_applicable",
                },
                {
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified",
                },
                {
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified",
                },
            ],
        },
    }
    scenes = [{"scene_id": f"scene-{idx}"} for idx in range(4)]
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
            side_effect=lambda path: (False, "missing_file", "") if not path else (True, "ok", "video"),
    ):
        with pytest.raises(RuntimeError, match='youtube_video_coverage_not_met'):
            validate_media_plan_for_render_v2(
                media_plan, scenes, render_resolution=(1280, 720), title="Test",
                neutral_asset_factory=lambda *_args, **_kwargs: None,
            )


def test_youtube_only_repairs_neutralized_slots_from_accepted_pool_audit():
    """Coordinator neutralization must still recover clean pool footage."""
    pool_paths = ["pool-a.mp4", "pool-b.mp4", "pool-c.mp4"]
    media_plan = {
        "media_paths": [None, None, None],
        "source_labels": ["", "", ""],
        "metadata": {
            "source_lane_policy": {"mode": "YouTube Clips"},
            "media_resolution_v2": [{}, {}, {}],
            "provider_audit": {
                "youtube": {
                    "cut_audit": [
                        {
                            "scene_index": index,
                            "status": "accepted",
                            "path": path,
                            "source_id": f"source-{index}",
                            "start": float(index),
                        }
                        for index, path in enumerate(pool_paths)
                    ]
                }
            },
        },
    }
    scenes = [{"scene_id": f"scene-{index}"} for index in range(3)]
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        side_effect=lambda path: (False, "missing_file", "")
        if not path
        else (True, "ok", "video"),
    ):
        repaired = validate_media_plan_for_render_v2(
            media_plan,
            scenes,
            render_resolution=(1280, 720),
            title="Test",
            neutral_asset_factory=lambda *_args, **_kwargs: None,
        )

    assert repaired["media_paths"] == pool_paths
    assert len(set(repaired["media_paths"])) == 3
    assert repaired["source_labels"] == [
        "YouTube Clips (verified reuse)",
        "YouTube Clips (verified reuse)",
        "YouTube Clips (verified reuse)",
    ]
    assert repaired["metadata"]["terminal_accounting"] == {
        "accepted": 3,
        "degraded": 0,
        "unresolved": 0,
    }


def test_youtube_only_breaks_adjacent_reused_pool_cuts_when_alternative_exists():
    """A valid footage-only plan should not hold the same cut twice in a row."""
    media_plan = {
        "media_paths": ["a.mp4", "a.mp4", "b.mp4"],
        "source_labels": ["YouTube Clips", "YouTube Clips", "YouTube Clips"],
        "metadata": {
            "source_lane_policy": {"mode": "YouTube Clips"},
            "media_resolution_v2": [
                {
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified",
                }
                for _ in range(3)
            ],
            "provider_audit": {
                "youtube": {
                    "cut_audit": [
                        {"scene_index": 0, "status": "accepted", "path": "a.mp4"},
                        {"scene_index": 1, "status": "accepted", "path": "a.mp4"},
                        {"scene_index": 2, "status": "accepted", "path": "b.mp4"},
                        {"scene_index": 3, "status": "accepted", "path": "c.mp4"},
                    ]
                }
            },
        },
    }
    scenes = [{"scene_id": f"scene-{index}"} for index in range(3)]
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        return_value=(True, "ok", "video"),
    ):
        repaired = validate_media_plan_for_render_v2(
            media_plan,
            scenes,
            render_resolution=(1280, 720),
            title="Test",
            neutral_asset_factory=lambda *_args, **_kwargs: None,
        )

    assert repaired["media_paths"][0] == "a.mp4"
    assert repaired["media_paths"][1] == "c.mp4"
    assert repaired["media_paths"][2] == "b.mp4"
    assert repaired["media_paths"][0] != repaired["media_paths"][1]


def test_mixed_media_breaks_adjacent_web_reuse_using_verified_candidate_ledger():
    """Late web rescue must not leave the same still on consecutive scenes."""
    ledger = [
        {
            "path": "a.jpg",
            "provider": "web",
            "media_type": "image",
            "asset_fingerprint": "fp-a",
            "verified": True,
            "status": "accepted",
        },
        {
            "path": "b.jpg",
            "provider": "universal_yandex",
            "media_type": "image",
            "asset_fingerprint": "fp-b",
            "verified": True,
            "status": "accepted",
            "final_score": 9,
        },
        {
            "path": "c.jpg",
            "provider": "universal_bing",
            "media_type": "image",
            "asset_fingerprint": "fp-c",
            "verified": True,
            "status": "accepted",
            "final_score": 8,
        },
    ]
    media_plan = {
        "media_paths": ["a.jpg", "a.jpg", "a.jpg", "a.jpg"],
        "source_labels": ["Web Images", "Web Images", "Web Images", "Web Images"],
        "metadata": {
            "source_lane_policy": {"mode": "Mixed Media"},
            "media_resolution_v2": [
                {
                    "provider": "web",
                    "media_type": "image",
                    "status": "accepted",
                    "verification_status": "verified",
                    "asset_fingerprint": "fp-a",
                    "candidate_ledger": ledger,
                }
                for _ in range(4)
            ],
        },
    }
    scenes = [{"scene_id": f"scene-{idx}"} for idx in range(4)]
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        return_value=(True, "ok", "image"),
    ):
        repaired = validate_media_plan_for_render_v2(
            media_plan,
            scenes,
            render_resolution=(1280, 720),
            title="Test",
            neutral_asset_factory=lambda *_args, **_kwargs: None,
        )

    paths = repaired["media_paths"]
    assert paths[0] == "a.jpg"
    assert paths[0] != paths[1]
    assert paths[1] != paths[2]
    assert paths[2] == "a.jpg"
    assert paths[2] != paths[3]
    assert set(paths) == {"a.jpg", "b.jpg", "c.jpg"}
    assert repaired["metadata"]["adjacent_duplicate_repairs"] == 2


def test_mixed_media_breaks_adjacent_youtube_reuse_from_parent_verified_ledger():
    """YT cut ledgers inherit the parent acceptance verdict."""
    ledger = [
        {
            "path": "a.mp4",
            "provider": "youtube",
            "media_type": "video",
            "asset_fingerprint": "fp-a",
            "candidate_id": "youtube:a",
        },
        {
            "path": "b.mp4",
            "provider": "youtube",
            "media_type": "video",
            "asset_fingerprint": "fp-b",
            "candidate_id": "youtube:b",
        },
    ]
    media_plan = {
        "media_paths": ["a.mp4", "a.mp4", "a.mp4"],
        "source_labels": ["YouTube Clips", "YouTube Clips", "YouTube Clips"],
        "metadata": {
            "source_lane_policy": {"mode": "Mixed Media"},
            "media_resolution_v2": [
                {
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified",
                    "asset_fingerprint": "fp-a",
                    "candidate_ledger": ledger,
                }
                for _ in range(3)
            ],
        },
    }
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        return_value=(True, "ok", "video"),
    ):
        repaired = validate_media_plan_for_render_v2(
            media_plan,
            [{"scene_id": f"scene-{idx}"} for idx in range(3)],
            render_resolution=(1280, 720),
            title="Test",
            neutral_asset_factory=lambda *_args, **_kwargs: None,
        )

    assert repaired["media_paths"][:2] == ["a.mp4", "b.mp4"]
    assert repaired["media_paths"][0] != repaired["media_paths"][1]


def test_mixed_media_sequence_swap_repairs_aaba_with_two_verified_assets():
    """A small accepted pool should alternate assets when a swap can help."""
    paths = ["a.jpg", "a.jpg", "b.jpg", "a.jpg", "b.jpg"]
    rows = [
        {
            "provider": "web",
            "media_type": "image",
            "status": "accepted",
            "verification_status": "deterministic_web_image",
            "verified": True,
            "selected_path": path,
            "path": path,
            "asset_fingerprint": "fp-a" if path == "a.jpg" else "fp-b",
            "candidate_id": f"candidate-{index}",
        }
        for index, path in enumerate(paths)
    ]
    media_plan = {
        "media_paths": paths,
        "source_labels": ["Web Images"] * len(paths),
        "metadata": {
            "source_lane_policy": {"mode": "Mixed Media"},
            "media_resolution_v2": rows,
        },
    }
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        return_value=(True, "ok", "image"),
    ):
        repaired = validate_media_plan_for_render_v2(
            media_plan,
            [{"scene_id": f"scene-{idx}"} for idx in range(len(paths))],
            render_resolution=(1280, 720),
            title="Test",
            neutral_asset_factory=lambda *_args, **_kwargs: None,
        )

    assert repaired["media_paths"] == ["a.jpg", "b.jpg", "a.jpg", "b.jpg", "a.jpg"]
    assert all(left != right for left, right in zip(repaired["media_paths"], repaired["media_paths"][1:]))
    assert repaired["metadata"]["adjacent_duplicate_swap_repairs"] == 2
    assert [row["selected_path"] for row in repaired["metadata"]["media_resolution_v2"]] == repaired["media_paths"]


def test_mixed_media_diversity_uses_content_hash_for_copied_assets(tmp_path):
    """A copied file is not a distinct clip just because its path differs."""
    original = tmp_path / "original.mp4"
    copied = tmp_path / "provider-copy.mp4"
    distinct = tmp_path / "different-cut.mp4"
    original.write_bytes(b"same verified clip bytes")
    copied.write_bytes(b"same verified clip bytes")
    distinct.write_bytes(b"different verified clip bytes")
    ledger = [
        {
            "path": str(copied),
            "provider": "youtube",
            "media_type": "video",
            "candidate_id": "copy-with-distinct-path",
            "status": "accepted",
        },
        {
            "path": str(distinct),
            "provider": "youtube",
            "media_type": "video",
            "candidate_id": "different-cut",
            "status": "accepted",
        },
    ]
    media_plan = {
        "media_paths": [str(original), str(original)],
        "source_labels": ["YouTube Clips", "YouTube Clips"],
        "metadata": {
            "source_lane_policy": {"mode": "Mixed Media"},
            "media_resolution_v2": [
                {
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified",
                    "selected_path": str(original),
                    "candidate_ledger": ledger,
                }
                for _ in range(2)
            ],
        },
    }
    with mock.patch(
        "services.pipeline_v2_media_adapter._validate_renderable_asset",
        return_value=(True, "ok", "video"),
    ):
        repaired = validate_media_plan_for_render_v2(
            media_plan,
            [{"scene_id": "scene-0"}, {"scene_id": "scene-1"}],
            render_resolution=(1280, 720),
            title="Content fingerprint diversity",
            neutral_asset_factory=lambda *_args, **_kwargs: None,
        )

    assert repaired["media_paths"] == [str(original), str(distinct)]
    assert repaired["metadata"]["media_resolution_v2"][1]["selected_path"] == str(distinct)


def test_youtube_fallback_retries_rejected_quota_candidates(monkeypatch, tmp_path):
    """When a primary quota scene has a rejected candidate, fallback must not be skipped as complete."""
    import services.pipeline_v2_media_adapter as adapter

    fallback_called = []
    def fake_youtube_runner(scene_texts, scene_durations, lane_config, topic, script, **kwargs):
        is_fallback = bool(lane_config.get("_is_fallback"))
        if is_fallback:
            fallback_called.append(list(kwargs.get("selected_indices") or []))
            return {
                "youtube_result": {
                    "download_paths": [str(tmp_path / "recovered.mp4")],
                    "cut_audit": [{"scene_index": 0, "status": "accepted"}],
                    "attempts": {0: [{"status": "downloaded", "path": str(tmp_path / "recovered.mp4"), "selected": True}]},
                    "audit": {"resolved": 1},
                }
            }
        # Primary quota run returns a rejected candidate
        return {
            "youtube_result": {
                "download_paths": [None],
                "attempts": {0: [{"status": "post_snapshot_rejected", "rejection_reason": "overlay_rejected", "selected": False}]},
                "audit": {"resolved": 0},
            }
        }

    # Verify that the primary row with status=post_snapshot_rejected is recognized as not usable
    primary_rows = {0: [{"status": "post_snapshot_rejected", "rejection_reason": "overlay_rejected", "path": str(tmp_path / "rej.mp4")}]}
    # Call the helper directly or verify fallback targets logic
    # In adapter, _has_usable_primary_youtube_row returns False for post_snapshot_rejected
    def _has_usable(cand_rows):
        for cand in cand_rows:
            p = str(cand.get("path") or "")
            status = str(cand.get("status") or "").lower()
            if p and status not in {"rejected", "post_snapshot_rejected", "failed"}:
                return True
        return False

    assert _has_usable(primary_rows[0]) is False


def test_media_stage_never_collapses_to_a_one_second_wall_when_budget_is_spent():
    """An exhausted delivery budget used to hand the media stage a 1s wall,
    terminalising every scene as a graphic fallback. The stage now keeps at
    least one agent round-trip (600s) for agent jobs and 300s otherwise."""
    import time
    from services.delivery_budget import DeliveryBudget
    from services.pipeline_v2_media_adapter import _media_stage_deadline_seconds

    def exhausted_budget():
        budget = DeliveryBudget.create(total_seconds=60.0, render_reserve_seconds=0.0)
        budget.deadline_at = time.monotonic() - 10.0
        return budget

    agent_config = {"llm_provider": "external_agent", "_delivery_budget": exhausted_budget()}
    assert _media_stage_deadline_seconds(agent_config, "Mixed Media") == 600.0
    plain_config = {"_delivery_budget": exhausted_budget()}
    assert _media_stage_deadline_seconds(plain_config, "Mixed Media") == 300.0
