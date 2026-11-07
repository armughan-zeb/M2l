from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_shot_plan import build_media_shot_plan


def test_trimmed_youtube_cut_never_seeks_to_its_source_timestamp(tmp_path):
    from unittest import mock
    from services.media_shot_plan import _make_shots

    path = _asset(tmp_path, "short-youtube-cut.mp4")
    candidate = {
        "path": path,
        "media_type": "video",
        "video_start": 7.0,
        "video_end": 11.0,
        "render_clip_start": 7.0,
    }
    with mock.patch("services.media_shot_plan._local_video_duration", return_value=1.77):
        shots = _make_shots([candidate], 4.0)

    assert shots[0]["clip_start"] == 7.0  # original YouTube-source provenance
    assert shots[0]["render_clip_start"] == 0.0  # local downloaded-cut clock
    assert shots[0]["timebase_repair"]["reason"] == "source_timestamp_exceeds_local_cut_timebase"


def test_full_source_keeps_valid_local_seek_and_missing_seek_defaults_to_zero(tmp_path):
    from unittest import mock
    from services.media_shot_plan import _make_shots

    path = _asset(tmp_path, "full-source.mp4")
    with mock.patch("services.media_shot_plan._local_video_duration", return_value=30.0):
        sought = _make_shots([{
            "path": path, "media_type": "video", "video_start": 7.0,
            "render_clip_start": 7.0,
        }], 4.0)
        defaulted = _make_shots([{
            "path": path, "media_type": "video", "video_start": 7.0,
        }], 4.0)

    assert sought[0]["render_clip_start"] == 7.0
    assert defaulted[0]["render_clip_start"] == 0.0


def test_same_downloaded_video_is_not_unique_just_because_source_time_changes(tmp_path):
    repeated = _asset(tmp_path, "same-local-cut.mp4")
    alternate = _asset(tmp_path, "different-local-cut.mp4")
    fingerprint = "same-exact-video-bytes"
    plans, summary = build_media_shot_plan(
        [
            {
                "path": repeated, "type": "video", "source": "youtube",
                "asset_fingerprint": fingerprint, "video_start": 0.0,
                "verification_status": "verified",
            },
            {
                "path": repeated, "type": "video", "source": "youtube",
                "asset_fingerprint": fingerprint, "video_start": 180.0,
                "verification_status": "verified",
                "resolution": {"alternates": [{
                    "path": alternate, "type": "video", "source": "youtube",
                    "asset_fingerprint": "different-exact-video-bytes",
                    "video_start": 190.0, "verified": True,
                    "verification_status": "verified",
                }]},
            },
        ],
        [2.0, 2.0],
        config={"intro_media_duration_seconds": 0},
    )

    assert plans[0][0]["path"] == repeated
    assert plans[1][0]["path"] == alternate
    assert summary["source_reuse_cooldown"]["cooldown_scenes"] == 1
    assert summary["repeated_source_shot_count"] == 0


def _asset(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"verified-real-media-fixture")
    return str(path)


def test_new_alternate_does_not_inherit_primary_fingerprint(tmp_path):
    from services.media_shot_plan import _candidate_rows
    first = _asset(tmp_path, 'first.jpg')
    second = _asset(tmp_path, 'second.jpg')
    rows = _candidate_rows({'path': first, 'asset_fingerprint':'primary-digest',
                            'alternates':[{'path':second,'verified':True}]})
    assert len(rows)==2
    assert 'asset_fingerprint' not in rows[1]


def test_long_scenes_use_verified_alternates_and_preserve_scene_coverage(tmp_path):
    primary = _asset(tmp_path, "jaguar-e-type-01.jpg")
    alternate_one = _asset(tmp_path, "jaguar-e-type-02.jpg")
    alternate_two = _asset(tmp_path, "jaguar-e-type-03.jpg")
    plans, summary = build_media_shot_plan(
        [{
            "scene_index": 0,
            "path": primary,
            "type": "image",
            "source": "web",
            "candidate_id": "web:primary",
            "resolution": {
                "status": "accepted",
                "verification_status": "verified",
                "alternates": [
                    {"path": alternate_one, "candidate_id": "web:alternate:1", "verified": True},
                    {"path": alternate_two, "candidate_id": "web:alternate:2", "verified": True},
                ],
            },
        }],
        [9.0],
        config={"max_still_hold_seconds": 3.5, "max_visual_beat_seconds": 3.5},
    )

    assert len(plans[0]) == 3
    assert [shot["path"] for shot in plans[0]] == [primary, alternate_one, alternate_two]
    assert plans[0][0]["shot_start"] == 0.0
    assert plans[0][-1]["shot_end"] == 9.0
    assert summary["unresolved_scene_indices"] == []


def test_unverified_alternates_never_enter_the_rendered_shot_plan(tmp_path):
    primary = _asset(tmp_path, "verified-primary.jpg")
    unverified = _asset(tmp_path, "unverified-alternate.jpg")
    plans, _summary = build_media_shot_plan(
        [{
            "scene_index": 0,
            "path": primary,
            "type": "image",
            "source": "web",
            "candidate_id": "web:primary",
            "verification_status": "verified",
            "resolution": {
                "status": "accepted",
                "verification_status": "verified",
                "alternates": [{
                    "path": unverified,
                    "candidate_id": "web:unverified",
                    # Deliberately omit verification metadata.
                }],
            },
        }],
        [2.0],
        config={"intro_media_duration_seconds": 0},
    )

    assert [shot["path"] for shot in plans[0]] == [primary]


def test_intro_director_targets_six_to_eight_real_shots(tmp_path):
    media = []
    for scene_index in range(2):
        paths = [_asset(tmp_path, f"intro-{scene_index}-{part}.jpg") for part in range(3)]
        media.append({
            "scene_index": scene_index,
            "path": paths[0],
            "type": "image",
            "source": "web",
            "candidate_id": f"web:{scene_index}:primary",
            "resolution": {
                "status": "accepted",
                "verification_status": "verified",
                "alternates": [
                    {"path": path, "candidate_id": f"web:{scene_index}:{part}", "verified": True}
                    for part, path in enumerate(paths[1:], start=1)
                ],
            },
        })
    plans, summary = build_media_shot_plan(
        media,
        [8.0, 8.0],
        config={
            "intro_media_duration_seconds": 15,
            "intro_media_min_shots": 6,
            "intro_media_max_shots": 8,
        },
    )

    intro_shots = [shot for plan in plans for shot in plan if shot.get("intro_shot")]
    assert 6 <= len(intro_shots) <= 8
    assert summary["intro_shortfall"] is False
    assert all(shot["verification"] in {"verified", "verified_reuse"} for shot in intro_shots)
    assert len({shot["path"] for shot in intro_shots}) == len(intro_shots)
    assert max(float(shot["duration"]) for shot in intro_shots) <= 3.5


def test_intro_keeps_a_single_still_as_one_continuous_moving_shot(tmp_path):
    still = _asset(tmp_path, "jaguar-e-type-intro.jpg")
    plans, summary = build_media_shot_plan(
        [{
            "scene_index": 0,
            "path": still,
            "type": "image",
            "source": "web",
            "candidate_id": "web:intro",
            "resolution": {"status": "accepted", "verification_status": "verified"},
        }],
        [5.2],
        config={
            "intro_media_duration_seconds": 15,
            "intro_media_min_shots": 1,
            "intro_media_max_shots": 8,
            "intro_max_shot_seconds": 2.5,
        },
    )

    assert len(plans[0]) == 1
    assert plans[0][0]["shot_start"] == 0.0
    assert plans[0][0]["shot_end"] == 5.2
    assert plans[0][0]["continuous_motion"] is True
    assert summary["intro_shortfall"] is False
    assert summary["motion_segment_count"] == 0


def test_sparse_intro_still_marks_fast_real_media_shots(tmp_path):
    still = _asset(tmp_path, "sparse-intro-jaguar.jpg")
    plans, summary = build_media_shot_plan(
        [{
            "scene_index": 0,
            "path": still,
            "type": "image",
            "source": "web",
            "candidate_id": "web:sparse-intro",
            "resolution": {"status": "accepted", "verification_status": "verified"},
        }],
        [8.0],
        config={
            "intro_media_duration_seconds": 8,
            "intro_media_min_shots": 6,
            "intro_media_max_shots": 8,
            "intro_max_shot_seconds": 2.5,
        },
    )

    intro_shots = [shot for shot in plans[0] if shot.get("intro_shot")]
    assert len(intro_shots) == 1
    assert summary["intro_shortfall"] is True
    assert intro_shots[0]["continuous_motion"] is True
    assert intro_shots[0]["duration"] == 8.0
    assert summary["motion_segment_count"] == 0


def test_long_non_intro_still_uses_one_camera_move_without_duplicate_shots(tmp_path):
    still = _asset(tmp_path, "long-documentary-still.jpg")
    plans, summary = build_media_shot_plan(
        [{
            "scene_index": 0,
            "path": still,
            "type": "image",
            "source": "web",
            "candidate_id": "web:long-still",
            "verification_status": "verified",
        }],
        [12.0],
        config={"intro_media_duration_seconds": 0, "max_still_hold_seconds": 3.5},
    )

    assert len(plans[0]) == 1
    assert plans[0][0]["shot_start"] == 0.0
    assert plans[0][0]["shot_end"] == 12.0
    assert plans[0][0]["continuous_motion"] is True
    assert summary["motion_segment_count"] == 0
    assert summary["continuous_still_motion_count"] == 1


def test_normal_long_scene_uses_same_identity_candidates_before_repeating_a_still(tmp_path):
    first = _asset(tmp_path, "e-type-first.jpg")
    second = _asset(tmp_path, "e-type-second.jpg")
    third = _asset(tmp_path, "e-type-third.jpg")
    fourth = _asset(tmp_path, "e-type-fourth.jpg")
    requirement = {
        "canonical_entity": "Jaguar",
        "domain": "automotive",
        "subject_type": "automobile",
        "required_attributes": {
            "model": "e type",
            "year": "1961",
            "era": "classic_historical",
        },
    }
    plans, summary = build_media_shot_plan(
        [
            {
                "scene_index": 0,
                "path": first,
                "type": "image",
                "candidate_id": "web:first",
                "visual_requirement": requirement,
                "resolution": {
                    "alternates": [{
                        "path": third,
                        "candidate_id": "web:third",
                        "verified": True,
                    }, {
                        "path": fourth,
                        "candidate_id": "web:fourth",
                        "verified": True,
                    }],
                },
            },
            {
                "scene_index": 1,
                "path": second,
                "type": "image",
                "candidate_id": "web:second",
                "visual_requirement": requirement,
            },
        ],
        [2.0, 10.0],
        config={
            "intro_media_duration_seconds": 0,
            "max_still_hold_seconds": 3.5,
            "max_visual_beat_seconds": 3.5,
        },
    )

    assert len(plans[1]) == 3
    assert len({shot["path"] for shot in plans[1]}) == 3
    assert any(shot["fallback_layer"] == "same_identity_cross_scene" for shot in plans[1])
    assert summary["cross_scene_fallback_shot_count"] >= 1


def test_adjacent_cross_scene_reuse_uses_one_verified_alternate_continuously(tmp_path):
    shared = _asset(tmp_path, "shared-crew.jpg")
    alternate = _asset(tmp_path, "alternate-crew.jpg")
    requirement = {
        "canonical_entity": "Train Crew",
        "domain": "railway",
        "subject_type": "railway crew",
        "required_attributes": {"era": "historical"},
    }
    plans, summary = build_media_shot_plan(
        [
            {
                "scene_index": 0,
                "path": shared,
                "type": "image",
                "candidate_id": "web:shared",
                "visual_requirement": requirement,
            },
            {
                "scene_index": 1,
                "path": shared,
                "type": "image",
                "candidate_id": "web:shared-again",
                "visual_requirement": requirement,
                "resolution": {
                    "alternates": [{
                        "path": alternate,
                        "candidate_id": "web:alternate",
                        "verified": True,
                    }],
                },
            },
        ],
        [2.0, 4.0],
        config={"intro_media_duration_seconds": 0, "max_still_hold_seconds": 3.5},
    )

    assert plans[0][-1]["path"] != plans[1][0]["path"]
    assert plans[1][0]["path"] == alternate
    assert summary["adjacent_reuse_repairs"] == 1
    assert plans[1][0]["shot_start"] == 0.0
    assert len(plans[1]) == 1
    assert plans[1][0]["shot_end"] == 4.0
    assert plans[1][0]["continuous_motion"] is True


def test_same_fingerprint_candidates_are_deduped_before_cross_scene_pool_fill(tmp_path):
    first = _asset(tmp_path, "crew-first.jpg")
    duplicate = _asset(tmp_path, "crew-duplicate.jpg")
    middle = _asset(tmp_path, "crew-middle.jpg")
    duplicate_again = _asset(tmp_path, "crew-duplicate-again.jpg")
    current = _asset(tmp_path, "crew-current.jpg")
    requirement = {
        "canonical_entity": "Train Crew",
        "domain": "railway",
        "subject_type": "railway crew",
        "required_attributes": {"era": "historical"},
    }
    plans, _summary = build_media_shot_plan(
        [
            {
                "scene_index": 0,
                "path": first,
                "type": "image",
                "asset_fingerprint": "same-underlying-still",
                "candidate_id": "web:first",
                "visual_requirement": requirement,
            },
            {
                "scene_index": 1,
                "path": duplicate,
                "type": "image",
                "asset_fingerprint": "same-underlying-still",
                "candidate_id": "web:duplicate",
                "visual_requirement": requirement,
            },
            {
                "scene_index": 2,
                "path": middle,
                "type": "image",
                "asset_fingerprint": "middle-still",
                "candidate_id": "web:middle",
                "visual_requirement": requirement,
            },
            {
                "scene_index": 3,
                "path": duplicate_again,
                "type": "image",
                "asset_fingerprint": "same-underlying-still",
                "candidate_id": "web:duplicate-again",
                "visual_requirement": requirement,
            },
            {
                "scene_index": 4,
                "path": current,
                "type": "image",
                "asset_fingerprint": "current-still",
                "candidate_id": "web:current",
                "visual_requirement": requirement,
            },
        ],
        [2.0, 2.0, 2.0, 2.0, 10.0],
        config={"intro_media_duration_seconds": 0, "max_still_hold_seconds": 3.5},
    )

    fingerprints = [str(shot.get("asset_fingerprint") or "") for shot in plans[4]]
    assert fingerprints.count("same-underlying-still") <= 1


def test_video_scenes_preserve_continuous_footage_without_still_frame_chopping(tmp_path):
    video_one = _asset(tmp_path, "gta-clip-01.mp4")
    video_two = _asset(tmp_path, "gta-clip-02.mp4")
    image_alt = _asset(tmp_path, "gta-still.jpg")

    plans, summary = build_media_shot_plan(
        [
            {
                "scene_index": 0,
                "path": video_one,
                "type": "video",
                "media_type": "video",
                "source": "youtube",
                "candidate_id": "youtube:gta1",
                "verification_status": "verified",
                "alternates": [
                    {"path": image_alt, "type": "image", "verified": True},
                ],
            },
            {
                "scene_index": 1,
                "path": video_two,
                "type": "video",
                "media_type": "video",
                "source": "youtube",
                "candidate_id": "youtube:gta2",
                "verification_status": "verified",
            },
        ],
        [12.0, 14.0],
        config={
            "intro_media_duration_seconds": 15,
            "max_still_hold_seconds": 3.5,
            "max_visual_beat_seconds": 3.5,
        },
    )

    # Scene 0 (12s intro scene with video) must remain 100% video without still frame subdivision
    assert len(plans[0]) == 1
    assert plans[0][0]["media_type"] == "video"
    assert plans[0][0]["path"] == video_one
    assert plans[0][0]["duration"] == 12.0

    # Scene 1 (14s non-intro scene with video) must remain 100% video without still frame subdivision
    assert len(plans[1]) == 1
    assert plans[1][0]["media_type"] == "video"
    assert plans[1][0]["path"] == video_two
    assert plans[1][0]["duration"] == 14.0

    # Summary must report both video shots
    assert summary["video_shot_count"] == 2


def test_partial_intro_tail_reflow_stays_inside_scene_duration(tmp_path):
    durations = [6.12, 5.96, 5.474]
    media = []
    for scene_index in range(len(durations)):
        candidates = [
            _asset(tmp_path, f"partial-intro-{scene_index}-{candidate_index}.jpg")
            for candidate_index in range(5)
        ]
        media.append({
            "scene_index": scene_index,
            "path": candidates[0],
            "type": "image",
            "verification_status": "verified",
            "candidate_id": f"web:primary:{scene_index}",
            "director_media_plan": {"visual_beats": 2, "media_preference": "either"},
            "resolution": {
                "status": "accepted",
                "verification_status": "verified",
                "alternates": [
                    {
                        "path": path,
                        "candidate_id": f"web:{scene_index}:{candidate_index}",
                        "verified": True,
                    }
                    for candidate_index, path in enumerate(candidates[1:], start=1)
                ],
            },
        })

    plans, _summary = build_media_shot_plan(
        media,
        durations,
        config={
            "intro_media_duration_seconds": 15,
            "intro_media_min_shots": 1,
            "intro_media_max_shots": 8,
            "intro_max_shot_seconds": 2.5,
            "max_still_hold_seconds": 3.5,
            "max_visual_beat_seconds": 3.5,
        },
    )

    for scene_index, (plan, duration) in enumerate(zip(plans, durations)):
        assert plan, f"scene {scene_index} should have a verified visual"
        previous_end = 0.0
        for shot in plan:
            start = float(shot["shot_start"])
            end = float(shot["shot_end"])
            assert start >= previous_end - 0.001
            assert end > start
            assert end <= duration + 0.001, f"scene {scene_index} shot escaped its narration window"
            previous_end = end
        assert abs(previous_end - duration) <= 0.001


def test_intro_borrowed_source_is_removed_when_its_native_scene_arrives(tmp_path):
    unrelated = _asset(tmp_path, "intro-unrelated.jpg")
    native_first = _asset(tmp_path, "intro-native-first.jpg")
    native_later = _asset(tmp_path, "intro-native-later.jpg")
    identity = {
        "canonical_entity": "Apollo 11",
        "domain": "spaceflight",
        "subject_type": "space mission",
        "required_attributes": {"era": "1960s"},
    }
    media = [
        {
            "scene_index": 0,
            "path": unrelated,
            "type": "image",
            "candidate_id": "web:unrelated",
            "verification_status": "verified",
        },
        {
            "scene_index": 1,
            "path": native_first,
            "type": "image",
            "candidate_id": "web:native-first",
            "visual_requirement": identity,
            "verification_status": "verified",
        },
        {
            "scene_index": 2,
            "path": native_later,
            "type": "image",
            "candidate_id": "web:native-later",
            "visual_requirement": identity,
            "verification_status": "verified",
        },
    ]

    plans, summary = build_media_shot_plan(
        media,
        [2.0, 6.0, 4.0],
        config={
            "intro_media_duration_seconds": 12,
            "intro_media_min_shots": 1,
            "intro_media_max_shots": 8,
            "intro_max_shot_seconds": 2.5,
        },
    )

    selected_native_later = [shot for plan in plans for shot in plan if shot["path"] == native_later]
    assert len(selected_native_later) == 1
    assert all(shot["path"] != native_later for shot in plans[1])
    assert selected_native_later[0]["reuse_kind"] == "unique_candidate"
    assert summary["source_reuse_cooldown"]["removed_borrowed_shots"] >= 1
    assert summary["repeated_source_shot_count"] == 0
    assert all(abs(float(plan[-1]["shot_end"]) - duration) <= 0.001 for plan, duration in zip(plans, [2.0, 6.0, 4.0]))


def test_shot_plan_preserves_rights_evidence_and_reports_clearance(tmp_path):
    image = _asset(tmp_path, "licensed-retrieval.jpg")
    plans, summary = build_media_shot_plan(
        [{
            "scene_index": 0,
            "path": image,
            "type": "image",
            "provider": "wikimedia",
            "candidate_id": "web:licensed",
            "source_url": "https://commons.wikimedia.org/example",
            "license": "Creative Commons Attribution-ShareAlike 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attribution": "Archive photographer",
            "creator": "Archive photographer",
            "verification_status": "verified",
        }],
        [3.0],
        config={"intro_media_duration_seconds": 0},
    )

    shot = plans[0][0]
    rights = summary["asset_rights_audit"]
    assert shot["rights_status"] == "reported_open_license"
    assert shot["license_url"].endswith("/by-sa/4.0/")
    assert shot["attribution"] == "Archive photographer"
    assert shot["rights_review_required"] is True
    assert rights["source_asset_count"] == 1
    assert rights["review_required_asset_count"] == 1
    assert rights["release_clearance_required"] is True



def test_opening_still_uses_verified_alternates_even_when_director_requests_one(tmp_path):
    paths = [_asset(tmp_path, f"opening-{i}.jpg") for i in range(3)]
    plans, _ = build_media_shot_plan([{
        "path": paths[0], "type": "image", "candidate_id": "web:opening0",
        "verification_status": "verified", "director_primary_type": "ChapterTitle",
        "director_media_plan": {"visual_beats": 1, "media_preference": "images"},
        "resolution": {"status": "accepted", "verification_status": "verified", "alternates": [
            {"path": p, "candidate_id": f"web:opening{i}", "verified": True}
            for i,p in enumerate(paths[1:],start=1)
        ]},
    }], [6], config={"intro_media_duration_seconds":15,"intro_media_min_shots":1,"intro_media_max_shots":8,"intro_max_shot_seconds":2.5})
    assert len(plans[0]) == 3
    assert len({shot["path"] for shot in plans[0]}) == 3
    assert max(shot["duration"] for shot in plans[0]) <= 2.5
