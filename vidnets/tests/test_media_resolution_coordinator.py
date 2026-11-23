import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_resolution_coordinator import (
    MediaResolutionCoordinator,
    SceneResolution,
    _candidate_hard_gate,
    _verification_result,
)
import app as server_app


def test_adaptive_review_starts_while_other_provider_is_searching():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    checked = threading.Event()
    overlap = []

    def slow_youtube(_scene, _cancel):
        overlap.append(checked.wait(2))
        return []

    def verify(_scene, _candidate):
        checked.set()
        return {"accepted": True, "status": "visual_pass", "score": 95,
                "visual_checked": True, "visual_available": True}

    result = resolve_storyboard_media_v2(
        [{"scene_index": 0, "scene_id": "car", "text": "Jaguar E-Type automobile"}],
        youtube_lane=slow_youtube,
        web_lane=lambda *_args: {"path": "car.jpg", "source": "web",
                                 "title": "Jaguar E-Type automobile", "score": 95},
        visual_verifier=verify,
        neutral_factory=lambda _scene: None,
        deadline_seconds=5,
        adaptive_media_review=True,
    )
    assert overlap == [True]
    assert result[0].selected_path == "car.jpg"


def test_external_job_cancellation_interrupts_media_discovery_promptly():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    job_cancel = threading.Event()
    lane_started = threading.Event()
    result_box = {}

    def slow_lane(_scene, cancel_event):
        lane_started.set()
        cancel_event.wait(10)
        return []

    def resolve():
        result_box.update(resolve_storyboard_media_v2(
            [{"scene_index": 0, "scene_id": "cancel-scene", "text": "A test scene"}],
            youtube_lane=slow_lane,
            web_lane=slow_lane,
            neutral_factory=lambda _scene: None,
            deadline_seconds=30,
            external_cancel_event=job_cancel,
        ))

    worker = threading.Thread(target=resolve, daemon=True)
    worker.start()
    assert lane_started.wait(2)
    job_cancel.set()
    worker.join(2)
    assert not worker.is_alive(), "media discovery should observe queue cancellation before its stage deadline"
    assert 0 in result_box


def test_visual_verifier_exception_keeps_sanitized_diagnostic_in_ledger():
    def broken_verifier(_scene, _candidate):
        raise AttributeError(r"missing method at C:\private\asset.png sk-or-v1-abcdefghijklmnopqrstuvwxyz012345")

    accepted, reason, score, verdict = _verification_result(
        {}, {"source": "web"}, broken_verifier
    )

    assert accepted is False
    assert score == 0.0
    assert reason.startswith("visual_verifier_error:AttributeError:")
    assert "missing method" in reason
    assert "[path]" in reason
    assert "[redacted-key]" in reason
    assert "C:\\private" not in reason
    assert "sk-or-v1" not in reason
    assert verdict["error_type"] == "AttributeError"


def test_stock_worker_deadlines_fit_fair_share_of_outer_stock_window():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    seen_deadlines = {}
    lock = threading.Lock()
    scenes = [
        {
            "scene_id": f"stock-scene-{index}",
            "scene_index": index,
            "text": f"A documentary process beat {index}.",
            "source_policy": {"stock_last_resort_allowed": True},
        }
        for index in range(8)
    ]

    def stock_lane(scene, cancel_event):
        with lock:
            seen_deadlines[scene["scene_index"]] = float(cancel_event.deadline_at)
        return []

    with mock.patch.dict(os.environ, {"VIDZEO_STOCK_FINAL_RESERVE_SECONDS": "30"}, clear=False):
        result = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=lambda *_args: [],
            web_lane=lambda *_args: [],
            stock_lane=stock_lane,
            neutral_factory=lambda _scene: None,
            deadline_seconds=1,
        )

    assert set(seen_deadlines) == set(range(8))
    assert all(deadline > 0 for deadline in seen_deadlines.values())
    for scene_index in range(8):
        budget = next(
            attempt
            for attempt in result[scene_index].attempts
            if attempt.get("stage") == "stock_budget"
        )
        assert budget["outer_window_seconds"] == 30
        assert budget["worker_window_seconds"] <= 15
        assert budget["queue_waves"] == 2


class MediaResolutionCoordinatorTests(unittest.TestCase):
    def test_dated_scene_cannot_reuse_undated_topic_verdict(self):
        from services.media_resolution_coordinator import _semantic_reuse_compatible
        target = {'visual_requirement': {'canonical_entity':'Queen Elizabeth II',
            'subject_type':'person','strict_identity':True,'requirement_hash':'1953-scene',
            'required_attributes':{'year':'1953'}}}
        source = SceneResolution(scene_index=0, final_scene_context={'visual_requirement': {
            'canonical_entity':'Queen Elizabeth II','subject_type':'person',
            'strict_identity':True,'requirement_hash':'undated-scene','required_attributes':{}}})
        assert not _semantic_reuse_compatible(target, source)
        source.final_scene_context['visual_requirement']['required_attributes']={'year':'2023'}
        assert not _semantic_reuse_compatible(target, source)
        source.final_scene_context['visual_requirement']['required_attributes']={'year':'1953'}
        assert _semantic_reuse_compatible(target, source)

    def test_nearest_fallback_never_reuses_another_named_persons_media(self):
        from pathlib import Path
        from services.media_resolution_coordinator import (
            _strict_reuse_identity_compatible,
            resolve_storyboard_media_v2,
        )

        target = {
            "visual_requirement": {
                "canonical_entity": "Jermaine Kearse",
                "subject_type": "person",
                "strict_identity": True,
                "requirement_hash": "kearse-scene",
            }
        }
        source = SceneResolution(
            scene_index=0,
            provider="web",
            final_scene_context={"visual_requirement": {
                "canonical_entity": "Malcolm Butler",
                "subject_type": "person",
                "strict_identity": True,
                "requirement_hash": "butler-scene",
            }},
            visual_decision={"accepted": True, "semantic_verified": True},
        )
        assert not _strict_reuse_identity_compatible(target, source)

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "malcolm-butler.jpg"
            image_path.write_bytes(b"x" * 2048)
            scenes = [
                {
                    "scene_index": 0,
                    "scene_id": "butler",
                    "container_id": "super-bowl-xlix",
                    "text": "Malcolm Butler intercepts the pass.",
                    "positive_subjects": ["Malcolm Butler"],
                    "visual_requirement": {
                        "canonical_entity": "Malcolm Butler",
                        "subject_type": "person",
                        "strict_identity": True,
                        "requirement_hash": "butler-scene",
                        "required_terms": ["Malcolm Butler"],
                    },
                    "source_policy": {"visual_verification_required": True},
                },
                {
                    "scene_index": 1,
                    "scene_id": "kearse",
                    "container_id": "super-bowl-xlix",
                    "text": "Jermaine Kearse makes the juggling catch.",
                    "positive_subjects": ["Jermaine Kearse"],
                    "visual_requirement": {
                        "canonical_entity": "Jermaine Kearse",
                        "subject_type": "person",
                        "strict_identity": True,
                        "requirement_hash": "kearse-scene",
                        "required_terms": ["Jermaine Kearse"],
                    },
                    "source_policy": {"visual_verification_required": True},
                },
            ]

            def web_lane(scene, _cancel_event):
                if scene["scene_index"] != 0:
                    return []
                return {
                    "candidate_id": "butler-photo",
                    "path": str(image_path),
                    "provider": "web",
                    "source": "web",
                    "media_type": "image",
                    "container_id": "super-bowl-xlix",
                    "title": "Malcolm Butler interception",
                    "asset_fingerprint": "butler-photo-sha",
                }

            result = resolve_storyboard_media_v2(
                scenes,
                youtube_lane=lambda _scene, _cancel_event: [],
                web_lane=web_lane,
                visual_verifier=lambda _scene, _candidate: {
                    "accepted": True,
                    "status": "accepted",
                    "semantic_verified": True,
                    "visual_available": True,
                    "visual_checked": True,
                    "identity_score": 95,
                    "final_score": 95,
                },
                neutral_factory=lambda _scene: None,
                deadline_seconds=3,
                max_workers=2,
            )

            assert result[0].provider == "web"
            assert result[1].provider != "nearest_fallback"
            assert result[1].selected_path != str(image_path)
            assert any(
                row.get("status") == "rejected_semantic_identity_mismatch"
                for row in result[1].attempts
            )

            same_subject_scenes = [
                dict(scenes[0], scene_index=0, scene_id="butler-first-beat"),
                dict(scenes[0], scene_index=1, scene_id="butler-adjacent-beat",
                     text="Butler is still the focus in the next adjacent beat."),
            ]
            same_subject = resolve_storyboard_media_v2(
                same_subject_scenes,
                youtube_lane=lambda _scene, _cancel_event: [],
                web_lane=web_lane,
                visual_verifier=lambda _scene, _candidate: {
                    "accepted": True,
                    "status": "accepted",
                    "semantic_verified": True,
                    "visual_available": True,
                    "visual_checked": True,
                    "identity_score": 95,
                    "final_score": 95,
                },
                neutral_factory=lambda _scene: None,
                deadline_seconds=3,
                max_workers=2,
            )
            assert same_subject[1].provider != "nearest_fallback"
            assert any(
                row.get("status") == "rejected_adjacent_still_reuse"
                for row in same_subject[1].attempts
            )

    def test_web_asset_identity_prefers_media_url_over_result_page_url(self):
        from services.media_resolution_coordinator import _web_asset_identity

        first = {
            "url": "https://i.pinimg.com/originals/aa/bb/first.jpg?nii=t",
            "source_url": "https://www.pinterest.com/ideas/railway-crew/123/",
            "page_url": "https://www.pinterest.com/ideas/railway-crew/123/",
            "provider": "web",
        }
        second = {
            "url": "https://i.pinimg.com/originals/cc/dd/second.jpg?nii=t",
            "source_url": "https://www.pinterest.com/ideas/railway-crew/123/",
            "page_url": "https://www.pinterest.com/ideas/railway-crew/123/",
            "provider": "web",
        }

        assert _web_asset_identity(first) != _web_asset_identity(second)
        assert _web_asset_identity(first).endswith("/first.jpg")

    def test_mixed_youtube_quota_reuse_is_bounded_per_physical_clip(self):
        from services.media_resolution_coordinator import _mixed_youtube_quota_reuse_cap

        with mock.patch.dict(os.environ, {}, clear=True):
            assert _mixed_youtube_quota_reuse_cap(8) == 2
        with mock.patch.dict(
            os.environ,
            {"VIDZEO_MIXED_YT_QUOTA_REUSE_CAP": "4"},
            clear=True,
        ):
            assert _mixed_youtube_quota_reuse_cap(2) == 2
            assert _mixed_youtube_quota_reuse_cap(8) == 4

    def test_youtube_reuse_cap_defaults_to_two_and_is_configurable(self):
        # Run 9 (job a7f52c58): the single good clip tJbzMqJGH4k was capped
        # at 1 use, so vision-passed frames for other scenes were dropped.
        # One YouTube asset identity may now serve a second scene.
        from services.media_resolution_coordinator import (
            YOUTUBE_REUSE_CAP_DEFAULT,
            _reuse_limit_for_resolution,
            _youtube_reuse_cap,
        )

        assert YOUTUBE_REUSE_CAP_DEFAULT == 2
        scene = {"source_policy": {"mixed_media": True}, "start_seconds": 30}
        youtube = SceneResolution(scene_index=3, provider="youtube")
        web = SceneResolution(scene_index=3, provider="web")
        with mock.patch.dict(os.environ, {}, clear=True):
            assert _youtube_reuse_cap() == 2
            assert _reuse_limit_for_resolution(scene, youtube, non_mixed_limit=4) == 2
            assert _reuse_limit_for_resolution(scene, web, non_mixed_limit=4) == 2
        with mock.patch.dict(
            os.environ, {"VIDZEO_YT_REUSE_CAP": "3"}, clear=True
        ):
            assert _youtube_reuse_cap() == 3
            assert _reuse_limit_for_resolution(scene, youtube, non_mixed_limit=4) == 3
        with mock.patch.dict(
            os.environ, {"VIDZEO_YT_REUSE_CAP": "junk"}, clear=True
        ):
            assert _youtube_reuse_cap() == 2
        # The opening hook still never replays a physical asset.
        intro = {"source_policy": {"mixed_media": True}, "is_intro": True}
        with mock.patch.dict(os.environ, {}, clear=True):
            assert _reuse_limit_for_resolution(intro, youtube, non_mixed_limit=4) == 1

    def test_youtube_quota_cap_default_follows_youtube_reuse_cap(self):
        from services.media_resolution_coordinator import _mixed_youtube_quota_reuse_cap

        with mock.patch.dict(
            os.environ, {"VIDZEO_YT_REUSE_CAP": "3"}, clear=True
        ):
            assert _mixed_youtube_quota_reuse_cap(8) == 3
            assert _mixed_youtube_quota_reuse_cap(2) == 2
        with mock.patch.dict(
            os.environ,
            {
                "VIDZEO_YT_REUSE_CAP": "3",
                "VIDZEO_MIXED_YT_QUOTA_REUSE_CAP": "4",
            },
            clear=True,
        ):
            # The quota-specific override still wins over the shared default.
            assert _mixed_youtube_quota_reuse_cap(8) == 4

    def test_same_youtube_link_reuse_requires_a_distinct_vision_bound_distant_cut(self):
        import hashlib
        from pathlib import Path
        from services.media_resolution_coordinator import (
            _verified_distant_youtube_variant,
            _youtube_video_key,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_path = tmp_path / "source-cut.mp4"
            alternate_path = tmp_path / "distant-cut.mp4"
            source_path.write_bytes(b"first verified cut")
            alternate_bytes = b"different verified cut from later timestamp"
            alternate_path.write_bytes(alternate_bytes)
            requirement_hash = "target-scene-hash"
            source = SceneResolution(
                scene_index=0,
                provider="youtube",
                source_url="https://www.youtube.com/watch?v=abcdefghijk",
                selected_path=str(source_path),
                video_start=20.0,
                video_end=30.0,
                verification_status="verified",
            )
            alternate = {
                "path": str(alternate_path),
                "source_url": "https://youtu.be/abcdefghijk",
                "video_start": 180.0,
                "video_end": 190.0,
                "verified": True,
                "status": "accepted",
                "visual_decision": {
                    "accepted": True,
                    "visual_checked": True,
                    "verified_requirement_hash": requirement_hash,
                    "verified_asset_sha256": hashlib.sha256(alternate_bytes).hexdigest(),
                },
            }
            assert _youtube_video_key(source.source_url) == "abcdefghijk"
            assert _verified_distant_youtube_variant(
                alternate, source, target_requirement_hash=requirement_hash,
                scene_duration=5.0, used_resolutions=[source],
                used_paths={str(source_path.resolve()).casefold()},
            ) is True
            alternate["video_start"] = 34.0
            alternate["video_end"] = 44.0
            assert _verified_distant_youtube_variant(
                alternate, source, target_requirement_hash=requirement_hash,
                scene_duration=5.0, used_resolutions=[source], used_paths=set(),
            ) is False
            alternate["video_start"] = None
            assert _verified_distant_youtube_variant(
                alternate, source, target_requirement_hash=requirement_hash,
                scene_duration=5.0, used_resolutions=[source], used_paths=set(),
            ) is False

    def test_mixed_youtube_quota_does_not_duplicate_adjacent_clip_over_verified_web(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        scenes = [
            {
                "scene_id": f"royal-{index}",
                "scene_index": index,
                "container_id": "royal-family",
                "text": f"Prince Harry royal family scene {index}",
                "positive_subjects": ["Prince Harry"],
                "visual_requirement": {
                    "strict_identity": True,
                    "domain": "person",
                    "subject_type": "person",
                    "canonical_entity": "Prince Harry",
                },
                "source_policy": {
                    "mixed_media": True,
                    "youtube_quota": True,
                    "visual_verification_required": True,
                    "strict_real_media": True,
                },
            }
            for index in range(2)
        ]

        def youtube_lane(scene, _cancel):
            if int(scene.get("scene_index", -1)) != 1:
                return []
            return {
                "path": "shared-harry-clip.mp4",
                "source": "youtube",
                "provider": "youtube",
                "media_type": "video",
                "asset_fingerprint": "same-physical-clip",
                "source_url": "https://youtube.com/watch?v=harry",
                "container_id": "royal-family",
                "candidate_id": "yt-harry-clip",
                "status": "accepted",
                "verification_status": "verified",
                "visual_decision": {
                    "accepted": True,
                    "semantic_verified": True,
                    "visual_available": True,
                    "visual_checked": True,
                },
            }

        def web_lane(scene, _cancel):
            return {
                "path": f"royal-{scene['scene_index']}.jpg",
                "source": "web",
                "provider": "web",
                "media_type": "image",
                "asset_fingerprint": f"web-{scene['scene_index']}",
                "container_id": "royal-family",
                "candidate_id": f"web-{scene['scene_index']}",
                "status": "accepted",
                "verification_status": "verified",
                "candidate_evidence": {"identity_proof": True},
            }

        results = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=youtube_lane,
            web_lane=web_lane,
            visual_verifier=lambda _scene, _candidate: {
                "accepted": True,
                "status": "accepted",
                "semantic_verified": True,
                "visual_available": True,
                "visual_checked": True,
                "identity_score": 90,
                "final_score": 90,
            },
            neutral_factory=lambda _scene: None,
            deadline_seconds=3,
            max_workers=2,
        )

        assert results[0].provider == "web"
        assert results[1].provider == "youtube"
        assert results[0].asset_fingerprint != results[1].asset_fingerprint

    def test_explicit_youtube_quota_prefers_verified_clip_over_web_heuristic(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        scene = {
            "scene_index": 0,
            "scene_id": "quota-scene",
            "container_id": "documentary",
            "text": "A dated historical scene with a named place",
            "visual_requirement": {"canonical_entity": "Windsor", "subject_type": "place"},
            "source_policy": {"mixed_media": True, "youtube_quota": True, "visual_verification_required": False},
        }
        youtube = {"path": "verified-ceremony.mp4", "provider": "youtube", "source": "youtube",
                   "media_type": "video", "score": 90, "candidate_id": "ceremony-clip",
                   "title": "Garter Day procession at Windsor", "query": "Windsor Garter Day ceremony procession",
                   "status": "accepted", "visual_decision": {"accepted": True, "semantic_verified": True}}
        web = {"path": "relevant-photo.jpg", "provider": "web", "source": "web", "media_type": "image",
               "score": 90, "candidate_id": "ceremony-photo", "title": "Garter Day procession at Windsor",
               "query": "Windsor Garter Day ceremony procession", "status": "accepted"}
        with mock.patch("services.mixed_media_planner.automatic_source_preferences", return_value=["Web Images", "YouTube Clips"]):
            result = resolve_storyboard_media_v2(
                [scene], youtube_lane=lambda _scene, _cancel: youtube,
                web_lane=lambda _scene, _cancel: web,
                visual_verifier=lambda _scene, _candidate: {"accepted": True, "status": "accepted", "score": 90},
                neutral_factory=lambda _scene: None, deadline_seconds=2,
            )
        assert result[0].provider == "youtube"
        assert result[0].selected_path == "verified-ceremony.mp4"

    def test_shared_batch_lanes_are_not_serialized_by_container_waves(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        class SharedLane:
            shared_batch = True

            def __init__(self, source):
                self.source = source
                self.calls = 0

            def __call__(self, scene, _cancel_event):
                self.calls += 1
                return {
                    "candidate_id": f"{self.source}:{scene['scene_index']}",
                    "path": f"{self.source}-{scene['scene_index']}.jpg",
                    "source": self.source,
                    "provider": self.source,
                    "container_id": scene["container_id"],
                    "score": 90,
                }

        scenes = [
            {
                "scene_id": f"s{i}", "scene_index": i, "order": i,
                "container_id": "one-container", "text": f"scene {i}",
                "positive_subjects": ["Titanic"], "forbidden_subjects": [],
                "source_policy": {"visual_verification_required": False},
            }
            for i in range(5)
        ]
        youtube = SharedLane("youtube")
        web = SharedLane("web")
        result = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=youtube,
            web_lane=web,
            visual_verifier=lambda _scene, candidate: {"accepted": candidate["source"] == "web", "score": 90},
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
            max_workers=5,
        )

        self.assertEqual(len(result), 5)
        self.assertTrue(all(row.provider == "web" for row in result.values()))
        self.assertEqual(web.calls, 5)

    def test_deadline_polling_records_fast_results_and_cancels_pending_workers(self):
        coordinator = MediaResolutionCoordinator(
            deadline_seconds=1.0,
            max_workers=2,
            drain_seconds=0.25,
        )

        def worker(scene_index, cancel_event):
            if scene_index == 0:
                return SceneResolution(scene_index=scene_index, status="resolved")
            while not cancel_event.is_set():
                time.sleep(0.01)
            return SceneResolution(
                scene_index=scene_index,
                status="unverified",
                fallback_reason="observed_cancel",
            )

        started = time.monotonic()
        ledger = coordinator.resolve([0, 1], worker)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.8)
        self.assertEqual(ledger[0].status, "resolved")
        self.assertIn(ledger[1].fallback_reason, {"observed_cancel", "media_deadline"})
        self.assertTrue(coordinator.cancel_event.is_set())

    def test_deadline_marks_unsubmitted_tail_without_second_pass(self):
        coordinator = MediaResolutionCoordinator(
            deadline_seconds=0.05,
            max_workers=1,
            drain_seconds=0.01,
        )

        def worker(scene_index, cancel_event):
            # Keep the first worker active until the deadline so the tail is
            # never submitted to the executor.
            while not cancel_event.is_set():
                time.sleep(0.005)
            return SceneResolution(scene_index=scene_index, status="unverified")

        ledger = coordinator.resolve([0, 1, 2], worker)
        self.assertEqual(set(ledger), {0, 1, 2})
        self.assertEqual(ledger[2].fallback_reason, "media_deadline_unscheduled")


class WebImagesRescueContractTests(unittest.TestCase):
    @mock.patch("app._run_stock_media_branch")
    def test_web_images_never_enters_targeted_stock_rescue(self, mock_stock_branch):
        config = {"image_source": "Web Images"}
        result = server_app._run_targeted_stock_rescue(
            unresolved_indices=[0, 1],
            scene_texts=["RMS Titanic", "RMS Titanic passengers"],
            scene_durations=[2.0, 2.0],
            final_paths=[None, None],
            config=config,
            title="RMS Titanic",
            script="A documentary about RMS Titanic.",
        )

        self.assertEqual(result, {})
        mock_stock_branch.assert_not_called()
        self.assertEqual(config.get("_stock_rescue_cache"), {0: "", 1: ""})


class CrossContainerReuseContractTests(unittest.TestCase):
    def test_strict_mixed_media_bounds_web_still_reuse_to_two_nonadjacent_appearances(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2
        from services.visual_requirement import build_visual_requirement

        requirement = build_visual_requirement(
            title="Jaguar E-Type",
            script="A factual history of the Jaguar E-Type automobile.",
            scene_text="The Jaguar E-Type remains an iconic classic automobile.",
        )
        scenes = [
            {
                "scene_id": f"jaguar-{index}",
                "scene_index": index,
                "container_id": "jaguar-e-type",
                "text": "The Jaguar E-Type remains an iconic classic automobile.",
                "positive_subjects": ["Jaguar E-Type"],
                "forbidden_subjects": [],
                "visual_requirement": requirement,
                "source_policy": {
                    "mixed_media": True,
                    "strict_real_media": True,
                },
            }
            for index in range(5)
        ]

        def web_lane(scene, _cancel_event):
            if scene["scene_index"] == 0:
                return {
                    "path": "jaguar-one.jpg",
                    "source": "web",
                    "provider": "web",
                    "container_id": "jaguar-e-type",
                    "title": "Jaguar E-Type classic automobile",
                    "score": 90,
                }
            return []

        result = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=lambda _scene, _cancel_event: [],
            web_lane=web_lane,
            visual_verifier=lambda _scene, _candidate: {
                "accepted": True,
                "status": "accepted",
                "score": 90,
            },
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )

        assert result[0].provider == "web"
        assert result[1].provider == "neutral"
        # The production runbook permits two nonadjacent appearances only
        # after fresh real-media candidates are exhausted.
        assert result[2].provider == "web"
        assert result[3].provider == "neutral"
        assert result[4].provider == "neutral"
        assert "adjacent_asset_reuse_blocked" in str(result[1].attempts)

    def test_strict_scene_can_reuse_separately_verified_same_entity_web_alternate(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2
        from services.visual_requirement import build_visual_requirement
        from pathlib import Path
        import hashlib
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        primary = Path(workspace.name) / 'jaguar-primary.jpg'
        alternate = Path(workspace.name) / 'jaguar-alternate.jpg'
        primary.write_bytes(b'primary fixture pixels')
        alternate.write_bytes(b'alternate fixture pixels')

        requirement = build_visual_requirement(
            title="Jaguar E-Type",
            script="A factual history of the Jaguar E-Type automobile.",
            scene_text="The Jaguar E-Type remains an iconic classic automobile.",
        )
        scenes = [
            {
                "scene_id": f"jaguar-{index}",
                "scene_index": index,
                "container_id": "jaguar-e-type",
                "text": "The Jaguar E-Type remains an iconic classic automobile.",
                "positive_subjects": ["Jaguar E-Type"],
                "forbidden_subjects": [],
                "visual_requirement": requirement,
                "source_policy": {
                    "mixed_media": True,
                    "strict_real_media": True,
                },
            }
            for index in range(3)
        ]

        def web_lane(scene, _cancel_event):
            if scene["scene_index"] == 0:
                return [
                    {
                        "candidate_id": "jaguar-primary",
                        "path": str(primary),
                        "source": "web",
                        "provider": "web",
                        "container_id": "jaguar-e-type",
                        "title": "Jaguar E-Type classic automobile primary",
                        "score": 90,
                    },
                    {
                        "candidate_id": "jaguar-alternate",
                        "path": str(alternate),
                        "source": "web",
                        "provider": "web",
                        "container_id": "jaguar-e-type",
                        "title": "Jaguar E-Type classic automobile alternate",
                        "score": 80,
                    },
                ]
            return []

        result = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=lambda _scene, _cancel_event: [],
            web_lane=web_lane,
            visual_verifier=lambda _scene, _candidate: {
                "accepted": True,
                "status": "accepted",
                "score": 90,
                "visual_checked": True, "visual_available": True, "semantic_verified": True,
                "verified_requirement_hash": _scene['visual_requirement']['requirement_hash'],
                "verified_asset_sha256": hashlib.sha256(Path(_candidate['path']).read_bytes()).hexdigest(),
            },
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )

        assert result[0].provider == "web"
        assert result[2].provider == "web"
        assert result[2].resolution_kind == "same_container_reuse"
        assert result[2].verification_status == "verified_reuse"
        assert result[2].selected_path == str(alternate)

    def test_domain_pending_candidate_reaches_visual_gate(self):
        from services.visual_requirement import build_visual_requirement

        scene_text = "The rail maintenance crew prepares the locomotive before departure."
        requirement = build_visual_requirement(
            title="Train Crew: Conductors and Locomotive Engineers",
            script=scene_text,
            scene_text=scene_text,
        )
        scene = {
            "scene_index": 0,
            "container_id": "train-crew",
            "visual_requirement": requirement,
        }
        candidate = {
            "path": "rail-maintenance.mp4",
            "title": "This Rail Maintenance Crew Has Plenty on its Plate",
            "description": "Railway operations and maintenance footage.",
        }
        allowed, reason = _candidate_hard_gate(scene, candidate)
        self.assertTrue(allowed)
        self.assertEqual(reason, "passed")
        self.assertEqual(
            candidate.get("identity_gate_reason"),
            "domain_context_pending_visual_required",
        )
        self.assertTrue(candidate.get("semantic_context_pending"))

    def test_query_anchored_web_candidate_reaches_visual_gate_after_metadata_mismatch(self):
        from services.visual_requirement import build_visual_requirement

        scene_text = "Prince Harry and Meghan prepare to return to Britain."
        requirement = build_visual_requirement(
            title="The British Royal Family",
            script=scene_text,
            scene_text=scene_text,
        )
        scene = {"scene_index": 0, "visual_requirement": requirement}
        candidate = {
            "path": "royal-event.jpg",
            "provider": "web",
            "source": "web",
            "title": "Royal event photograph",
            "candidate_evidence": {
                "metadata_source": "query_anchored_visual_pending",
                "verification_required": True,
                "canonical_entity_mismatch": True,
                "missing_terms": ["harry"],
            },
        }

        allowed, reason = _candidate_hard_gate(scene, candidate)

        self.assertTrue(allowed)
        self.assertEqual(reason, "passed")
        self.assertTrue(candidate.get("semantic_context_pending"))
        self.assertTrue(candidate.get("candidate_evidence", {}).get("verification_required"))

    def test_forbidden_person_confusable_cannot_use_visual_pending_handoff(self):
        from services.visual_requirement import build_visual_requirement

        scene_text = "Prince Harry appears at a royal event."
        requirement = build_visual_requirement(
            title="The British Royal Family",
            script=scene_text,
            scene_text=scene_text,
        )
        scene = {"scene_index": 0, "visual_requirement": requirement}
        candidate = {
            "path": "frog-toy.jpg",
            "provider": "web",
            "source": "web",
            "title": "Prince Harry frog toy",
            "candidate_evidence": {
                "metadata_source": "query_anchored_visual_pending",
                "verification_required": True,
                "canonical_entity_mismatch": True,
            },
        }

        allowed, reason = _candidate_hard_gate(scene, candidate)

        self.assertFalse(allowed)
        self.assertIn(reason, {"person_confusable_entity", "forbidden_confusable_entity"})

    def test_conflicting_regnal_ordinal_cannot_reach_positive_vision_handoff(self):
        from services.visual_requirement import build_visual_requirement

        scene_text = "King Charles III appears more welcoming toward his son."
        requirement = build_visual_requirement(
            title="The British Royal Family",
            script=scene_text,
            scene_text=scene_text,
        )
        scene = {"scene_index": 0, "visual_requirement": requirement}
        candidate = {
            "path": "king-charles-i.jpg",
            "provider": "web",
            "source": "web",
            "title": "An iconic royal portrait of King Charles I on horseback",
            # A stale/overconfident upstream vision verdict must not override
            # deterministic evidence that the asset names a different monarch.
            "visual_decision": {
                "accepted": True,
                "identity_score": 98,
                "reason": "Clearly depicts King Charles III in a formal setting",
            },
        }

        allowed, reason = _candidate_hard_gate(scene, candidate)

        self.assertFalse(allowed)
        self.assertEqual(reason, "conflicting_person_ordinal")

    def test_virtual_avatar_catalog_cannot_pass_for_a_real_royal_person(self):
        from services.visual_requirement import build_visual_requirement

        requirement = build_visual_requirement(
            title="The British Royal Family",
            script="Prince William is now the heir to the throne.",
            scene_text="Prince William is now the heir to the throne.",
        )
        scene = {"scene_index": 0, "visual_requirement": requirement}
        candidate = {
            "path": "virtual-avatar-listing.jpg",
            "provider": "web",
            "source": "web",
            "title": "Prodigy - Male 25",
            "description": "Harry skin, Prince hair, Prodigy eyes. Now at We Love Roleplay.",
            "visual_decision": {"accepted": True, "identity_score": 99},
        }

        allowed, reason = _candidate_hard_gate(scene, candidate)

        self.assertFalse(allowed)
        self.assertEqual(reason, "person_confusable_entity")

    def test_nearest_same_domain_candidate_reaches_visual_gate(self):
        from services.visual_requirement import build_visual_requirement

        requirement = build_visual_requirement(
            title="The History of Jaguar Cars: Why the 1961 E-Type Still Matters",
            script="A factual history of the 1961 Jaguar E-Type classic automobile.",
            scene_text="The sports car's long bonnet changed automotive design.",
        )
        scene = {
            "scene_index": 0,
            "container_id": "jaguar",
            "visual_requirement": requirement,
        }
        candidate = {
            "path": "classic-sports-car.jpg",
            "provider": "web",
            "source": "web",
            "title": "Classic sports car archival road photograph",
        }
        allowed, reason = _candidate_hard_gate(scene, candidate)
        self.assertTrue(allowed)
        self.assertEqual(reason, "passed")
        self.assertEqual(
            candidate.get("identity_gate_reason"),
            "nearest_match_pending_visual_required",
        )
        self.assertTrue(candidate.get("candidate_evidence", {}).get("nearest_match"))

    def test_metadata_free_timeout_salvage_reaches_visual_gate_without_identity_approval(self):
        from services.visual_requirement import build_visual_requirement

        scene_text = "The conductor and locomotive engineer prepare the train for departure."
        requirement = build_visual_requirement(
            title="Train Crew: Conductors and Locomotive Engineers",
            script=scene_text,
            scene_text=scene_text,
        )
        scene = {
            "scene_index": 0,
            "container_id": "train-crew",
            "visual_requirement": requirement,
        }
        candidate = {
            "path": "post-grace-salvage.mp4",
            "provider": "youtube",
            "source": "youtube",
            "media_type": "video",
            "qwen_result": {
                "source": "youtube_pool_moment",
                "verification_status": "deterministic_pool_cut",
                "accepted": True,
            },
        }

        allowed, reason = _candidate_hard_gate(scene, candidate)

        self.assertTrue(allowed)
        self.assertEqual(reason, "passed")
        self.assertEqual(
            candidate.get("identity_gate_reason"),
            "transport_recovery_visual_required",
        )
        self.assertTrue(candidate.get("transport_recovery_pending"))

    def test_unverified_youtube_ranged_alternate_reaches_visual_gate(self):
        scene = {
            "scene_index": 0,
            "visual_requirement": {
                "strict_identity": True,
                "canonical_entity": "Train Crew",
                "domain": "rail_transport",
                "subject_type": "rail_crew",
            },
            "source_policy": {"strict_real_media": True},
        }
        candidate = {
            "path": "ranged-alternate.mp4",
            "provider": "youtube",
            "source": "youtube",
            "visual_decision": {
                "accepted": True,
                "semantic_verified": True,
                "source": "youtube_ranged_alternate",
                "verification_status": "deterministic_ranged_alternate",
                "visual_available": False,
            },
        }
        calls = []

        def verify(_scene, _candidate):
            calls.append(True)
            return {"accepted": False, "status": "visual_rejected", "reason": "wrong subject"}

        accepted, reason, _score, _verdict = _verification_result(scene, candidate, verify)
        self.assertFalse(accepted)
        self.assertEqual(reason, "visual_rejected")
        self.assertEqual(calls, [True])

    def test_cross_source_fallback_works_in_both_directions(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        def scene(index):
            return {
                "scene_id": f"scene-{index}", "scene_index": index,
                "container_id": "jaguar", "text": "Jaguar E-Type archival detail",
                "positive_subjects": ["Jaguar E-Type"], "forbidden_subjects": [],
                "source_policy": {"visual_verification_required": False},
            }

        accepted = lambda _scene, _candidate: {"accepted": True, "status": "accepted", "score": 95}
        web_fallback = lambda _scene, _cancel: {
            "path": "web-jaguar.jpg", "source": "web", "provider": "web", "score": 95,
        }
        youtube_fallback = lambda _scene, _cancel: {
            "path": "yt-jaguar.mp4", "source": "youtube", "provider": "youtube", "media_type": "video", "score": 95,
        }

        youtube_fallback_calls = []
        web_first = resolve_storyboard_media_v2(
            [scene(0)],
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=lambda _scene, _cancel: [],
            youtube_fallback_lane=lambda *_args: youtube_fallback_calls.append(True) or youtube_fallback(*_args),
            web_fallback_lane=web_fallback,
            visual_verifier=accepted,
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )
        youtube_first = resolve_storyboard_media_v2(
            [scene(0)],
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=lambda _scene, _cancel: [],
            youtube_fallback_lane=None,
            web_fallback_lane=web_fallback,
            visual_verifier=accepted,
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )

        # A fast empty primary response remains eligible for the YouTube
        # recovery lane; only slow/exhausted attempts are suppressed.
        self.assertEqual(web_first[0].provider, "youtube")
        self.assertEqual(web_first[0].resolution_kind, "cross_lane_youtube_fallback")
        self.assertEqual(youtube_fallback_calls, [True])
        self.assertEqual(youtube_first[0].provider, "web")
        self.assertEqual(youtube_first[0].resolution_kind, "cross_lane_web_fallback")

        class QuotaYoutubeLane:
            allowed_indices = set()

            def __call__(self, _scene, _cancel):
                return []

        unattempted = resolve_storyboard_media_v2(
            [scene(0)],
            youtube_lane=QuotaYoutubeLane(),
            web_lane=lambda _scene, _cancel: [],
            youtube_fallback_lane=youtube_fallback,
            web_fallback_lane=None,
            visual_verifier=accepted,
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )
        self.assertEqual(unattempted[0].provider, "youtube")
        self.assertEqual(unattempted[0].resolution_kind, "cross_lane_youtube_fallback")

        class AlreadyAttemptedYoutubeLane:
            primary_attempted_indices = {0}

            def __call__(self, _scene, _cancel):
                return []

        repeat_calls = []
        after_bounded_attempt = resolve_storyboard_media_v2(
            [scene(0)],
            youtube_lane=AlreadyAttemptedYoutubeLane(),
            web_lane=lambda _scene, _cancel: [],
            youtube_fallback_lane=lambda *_args: repeat_calls.append(True) or youtube_fallback(*_args),
            web_fallback_lane=web_fallback,
            visual_verifier=accepted,
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )
        self.assertEqual(after_bounded_attempt[0].provider, "web")
        self.assertEqual(repeat_calls, [])

    def test_web_transport_failure_falls_through_to_youtube_only(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        def scene(index):
            return {
                "scene_id": f"scene-{index}", "scene_index": index,
                "container_id": "jaguar", "text": "Jaguar E-Type archival detail",
                "positive_subjects": ["Jaguar E-Type"], "forbidden_subjects": [],
                "source_policy": {"visual_verification_required": False},
            }

        accepted = lambda _scene, _candidate: {"accepted": True, "status": "accepted", "score": 95}
        youtube_fallback = lambda _scene, _cancel: {
            "path": "yt-jaguar.mp4", "source": "youtube", "provider": "youtube", "media_type": "video", "score": 95,
        }
        web_fallback = lambda _scene, _cancel: {
            "path": "web-jaguar.jpg", "source": "web", "provider": "web", "score": 95,
        }

        # Web lane raises (transport failure) with zero candidates: the web
        # recovery lane must not be retried — it would hit the same
        # proxy/hosts — and the scene falls through to YouTube only.
        web_fallback_calls = []
        youtube_fallback_calls = []
        result = resolve_storyboard_media_v2(
            [scene(0)],
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=lambda _scene, _cancel: (_ for _ in ()).throw(RuntimeError("boom")),
            youtube_fallback_lane=lambda *_args: youtube_fallback_calls.append(True) or youtube_fallback(*_args),
            web_fallback_lane=lambda *_args: web_fallback_calls.append(True) or web_fallback(*_args),
            visual_verifier=accepted,
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )
        self.assertEqual(result[0].provider, "youtube")
        self.assertEqual(result[0].resolution_kind, "cross_lane_youtube_fallback")
        self.assertEqual(youtube_fallback_calls, [True])
        self.assertEqual(web_fallback_calls, [])

        # Web lane hangs past the env-tunable web cap: same fast fall-through.
        web_fallback_calls.clear()
        youtube_fallback_calls.clear()

        def hanging_web_lane(_scene, _cancel):
            time.sleep(5)
            return []

        with mock.patch.dict(os.environ, {"VIDZEO_WEB_LANE_TIMEOUT_SECONDS": "0.3"}):
            result = resolve_storyboard_media_v2(
                [scene(0)],
                youtube_lane=lambda _scene, _cancel: [],
                web_lane=hanging_web_lane,
                youtube_fallback_lane=lambda *_args: youtube_fallback_calls.append(True) or youtube_fallback(*_args),
                web_fallback_lane=lambda *_args: web_fallback_calls.append(True) or web_fallback(*_args),
                visual_verifier=accepted,
                neutral_factory=lambda _scene: None,
                deadline_seconds=5,
            )
        self.assertEqual(result[0].provider, "youtube")
        self.assertEqual(result[0].resolution_kind, "cross_lane_youtube_fallback")
        self.assertEqual(youtube_fallback_calls, [True])
        self.assertEqual(web_fallback_calls, [])

    def test_vision_verdict_cache_reuses_candidate_across_rounds(self):
        from services.media_resolution_coordinator import (
            _vision_verdict_cache_key,
            resolve_storyboard_media_v2,
        )

        # Cache key: same clip+timestamp, same URL, same file — and empty
        # when there is nothing to identify the candidate with.
        self.assertEqual(
            _vision_verdict_cache_key({
                "fallback_source_path": "/tmp/clip.mp4", "fallback_frame_index": 2,
            }),
            "yt-frame:/tmp/clip.mp4#2",
        )
        self.assertEqual(
            _vision_verdict_cache_key({"source": "web", "source_url": "https://example.test/a.jpg"}),
            "url:https://example.test/a.jpg",
        )
        self.assertEqual(_vision_verdict_cache_key({}), "")

        def scene(index):
            return {
                "scene_id": f"scene-{index}", "scene_index": index,
                "container_id": "jaguar", "text": "Jaguar E-Type archival detail",
                "positive_subjects": ["Jaguar E-Type"], "forbidden_subjects": [],
                "source_policy": {"visual_verification_required": False},
            }

        def web_candidate(url, path):
            return {
                "path": path, "source": "web", "provider": "web",
                "source_url": url, "score": 90,
            }

        calls = []

        def verifier(_scene, candidate):
            calls.append(candidate.get("source_url"))
            return {
                "accepted": False, "status": "rejected", "final_score": 30,
                "visual_checked": True, "visual_available": True,
                "reason": "not the subject",
            }

        candidate_a = web_candidate("https://example.test/a.jpg", "/tmp/a.jpg")
        candidate_b = web_candidate("https://example.test/b.jpg", "/tmp/b.jpg")
        candidate_c = web_candidate("https://example.test/c.jpg", "/tmp/c.jpg")
        # The recovery round returns candidate A again (same URL) plus a new
        # candidate C. A must reuse its primary-round verdict without a
        # second live vision read; C is verified once. adaptive_media_review
        # is on so the recovery round actually vision-checks (the default
        # fast path accepts recovery candidates on metadata alone).
        result = resolve_storyboard_media_v2(
            [scene(0)],
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=lambda _scene, _cancel: [candidate_a, candidate_b],
            youtube_fallback_lane=None,
            web_fallback_lane=lambda _scene, _cancel: [candidate_a, candidate_c],
            visual_verifier=verifier,
            neutral_factory=lambda _scene: None,
            deadline_seconds=5,
            adaptive_media_review=True,
        )
        self.assertEqual(sorted(calls), [
            "https://example.test/a.jpg",
            "https://example.test/b.jpg",
            "https://example.test/c.jpg",
        ])
        self.assertEqual(len(calls), 3)

    def test_timed_out_vision_is_not_reissued_within_ttl(self):
        # A verifier timeout is a transient outage, not a verdict. The
        # recovery round must not spend a second live vision read on the
        # same candidate while the outage TTL holds; after the TTL it may
        # try again. Genuine verdicts still go through the verdict cache.
        from services.media_resolution_coordinator import (
            resolve_storyboard_media_v2,
        )

        def scene(index):
            return {
                "scene_id": f"scene-{index}", "scene_index": index,
                "container_id": "jaguar", "text": "Jaguar E-Type archival detail",
                "positive_subjects": ["Jaguar E-Type"], "forbidden_subjects": [],
                "source_policy": {"visual_verification_required": False},
            }

        def web_candidate(url, path):
            return {
                "path": path, "source": "web", "provider": "web",
                "source_url": url, "score": 90,
            }

        calls = []

        def verifier(_scene, candidate):
            calls.append(candidate.get("source_url"))
            if candidate.get("source_url") == "https://example.test/a.jpg":
                raise TimeoutError("visual_verifier timed out")
            return {
                "accepted": False, "status": "rejected", "final_score": 30,
                "visual_checked": True, "visual_available": True,
                "reason": "not the subject",
            }

        candidate_a = web_candidate("https://example.test/a.jpg", "/tmp/a.jpg")
        candidate_b = web_candidate("https://example.test/b.jpg", "/tmp/b.jpg")
        candidate_c = web_candidate("https://example.test/c.jpg", "/tmp/c.jpg")
        kwargs = dict(
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=lambda _scene, _cancel: [candidate_a, candidate_b],
            youtube_fallback_lane=None,
            web_fallback_lane=lambda _scene, _cancel: [candidate_a, candidate_c],
            visual_verifier=verifier,
            neutral_factory=lambda _scene: None,
            deadline_seconds=5,
            adaptive_media_review=True,
        )
        with mock.patch.dict(os.environ, {"VIDZEO_VISION_UNAVAILABLE_TTL_SECONDS": "60"}):
            resolve_storyboard_media_v2([scene(0)], **kwargs)
        # A timed out once (primary round); the recovery round reuses the
        # stored outage verdict instead of calling the verifier again.
        self.assertEqual(sorted(calls), [
            "https://example.test/a.jpg",
            "https://example.test/b.jpg",
            "https://example.test/c.jpg",
        ])
        calls.clear()
        with mock.patch.dict(os.environ, {"VIDZEO_VISION_UNAVAILABLE_TTL_SECONDS": "0"}):
            resolve_storyboard_media_v2([scene(0)], **kwargs)
        # With a zero TTL the outage is not suppressed: A is re-issued in
        # the recovery round.
        self.assertEqual(calls.count("https://example.test/a.jpg"), 2)

    def test_scene_budget_resolves_best_so_far_without_new_vision_calls(self):
        # F2: when the per-scene budget expires, the scene resolves with the
        # best hard-gate-passing candidate found so far instead of burning
        # minutes on further vision rounds and then failing bare. A candidate
        # genuinely rejected by vision is never resurrected.
        from services.media_resolution_coordinator import (
            resolve_storyboard_media_v2,
        )

        def scene(index):
            return {
                "scene_id": f"scene-{index}", "scene_index": index,
                "container_id": "jaguar", "text": "Jaguar E-Type archival detail",
                "positive_subjects": ["Jaguar E-Type"], "forbidden_subjects": [],
                "source_policy": {"visual_verification_required": False},
            }

        def web_candidate(url, path, score):
            return {
                "path": path, "source": "web", "provider": "web",
                "source_url": url, "score": score,
            }

        calls = []

        def verifier(_scene, candidate):
            calls.append(candidate.get("source_url"))
            # One live vision read takes 0.6s, so the first primary read
            # alone exhausts the 0.5s test budget; no second read may start.
            time.sleep(0.6)
            return {
                "accepted": False, "status": "rejected", "final_score": 30,
                "visual_checked": True, "visual_available": True,
                "reason": "not the subject",
            }

        candidate_a = web_candidate("https://example.test/a.jpg", "/tmp/a.jpg", 90)
        candidate_b = web_candidate("https://example.test/b.jpg", "/tmp/b.jpg", 80)
        candidate_c = web_candidate("https://example.test/c.jpg", "/tmp/c.jpg", 95)
        result = resolve_storyboard_media_v2(
            [scene(0)],
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=lambda _scene, _cancel: [candidate_a, candidate_b],
            youtube_fallback_lane=None,
            web_fallback_lane=lambda _scene, _cancel: [candidate_a, candidate_c],
            visual_verifier=verifier,
            neutral_factory=lambda _scene: None,
            deadline_seconds=30,
            adaptive_media_review=True,
            scene_budget_seconds=1.0,
        )
        # Only the primary gate's two vision reads happened (1.2s total,
        # past the 1.0s budget); the recovery round spent no further vision
        # call.
        self.assertEqual(
            sorted(calls),
            ["https://example.test/a.jpg", "https://example.test/b.jpg"],
        )
        # The scene still resolved with real media: candidate C, the best
        # hard-gate-passing candidate not rejected by vision.
        self.assertEqual(result[0].selected_path, "/tmp/c.jpg")
        self.assertTrue(
            any(
                attempt.get("status") == "scene_budget_exhausted"
                for attempt in result[0].attempts
            )
        )

    def test_serial_fallbacks_stop_after_coordinator_deadline(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        scene = {
            "scene_id": "deadline-scene",
            "scene_index": 0,
            "container_id": "deadline",
            "text": "Jaguar E-Type archival automobile detail",
            "positive_subjects": ["Jaguar E-Type"],
            "forbidden_subjects": ["wildlife", "animal"],
            "source_policy": {"visual_verification_required": True, "strict_real_media": True},
        }
        fallback_calls = []

        def slow_verifier(_scene, _candidate):
            time.sleep(1.15)
            return {"accepted": False, "status": "visual_rejected", "reason": "wrong subject"}

        result = resolve_storyboard_media_v2(
            [scene],
            youtube_lane=lambda _scene, _cancel: [],
            web_lane=lambda _scene, _cancel: {
                "path": "deadline.jpg", "source": "web", "provider": "web", "score": 90,
            },
            youtube_fallback_lane=lambda _scene, _cancel: fallback_calls.append("youtube") or [],
            web_fallback_lane=lambda _scene, _cancel: fallback_calls.append("web") or [],
            visual_verifier=slow_verifier,
            neutral_factory=lambda _scene: None,
            deadline_seconds=1.0,
        )

        self.assertEqual(fallback_calls, [])
        self.assertEqual(result[0].resolution_kind, "neutral_no_claim")
        self.assertIn("skipped_after_media_deadline", str(result[0].attempts))

    def test_web_recovery_tail_finishes_and_is_vision_checked_before_stock(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        scene = {
            "scene_id": "web-tail-scene",
            "scene_index": 0,
            "container_id": "jaguar",
            "text": "Jaguar E-Type archival automobile detail",
            "positive_subjects": ["Jaguar E-Type"],
            "forbidden_subjects": ["wildlife", "animal"],
            "source_policy": {"visual_verification_required": True, "strict_real_media": True},
        }
        vision_calls = []
        stock_calls = []

        def slow_web_recovery(_scene, _cancel):
            # Finish after both the media wall and its regular vision grace;
            # the web-only recovery and its own visual tail should still apply.
            time.sleep(2.15)
            return {
                "path": "jaguar-web-recovery.jpg",
                "source": "web",
                "provider": "web",
                "title": "Jaguar E-Type classic sports car",
                "score": 95,
            }

        def verifier(_scene, candidate):
            vision_calls.append(candidate["path"])
            return {"accepted": True, "status": "visual_pass", "score": 96}

        with mock.patch.dict(os.environ, {"VIDZEO_MEDIA_VISUAL_GRACE_SECONDS": "2"}):
            result = resolve_storyboard_media_v2(
                [scene],
                youtube_lane=lambda _scene, _cancel: [],
                web_lane=lambda _scene, _cancel: [],
                web_fallback_lane=slow_web_recovery,
                stock_lane=lambda _scene, _cancel: stock_calls.append(True) or [],
                visual_verifier=verifier,
                neutral_factory=lambda _scene: None,
                deadline_seconds=0.1,
                web_fallback_grace_seconds=3.0,
            )

        self.assertEqual(result[0].provider, "web")
        self.assertEqual(result[0].selected_path, "jaguar-web-recovery.jpg")
        self.assertEqual(result[0].verification_status, "verified")
        self.assertEqual(vision_calls, ["jaguar-web-recovery.jpg"])
        self.assertEqual(stock_calls, [])

    def test_primary_finalist_expansion_verifies_retained_lower_ranked_candidate(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        scene = {
            "scene_id": "scene-expansion",
            "scene_index": 0,
            "container_id": "jaguar",
            "text": "Jaguar E-Type archival automobile detail",
            "positive_subjects": ["Jaguar E-Type"],
            "forbidden_subjects": ["wildlife", "animal"],
            "source_policy": {"visual_verification_required": True, "strict_real_media": True},
        }
        candidates = [
            {
                "path": f"candidate-{index}.jpg",
                "source": "web",
                "provider": "web",
                "title": "Jaguar E-Type classic automobile",
                "score": 90 - index,
            }
            for index in range(4)
        ]

        def verify(_scene, candidate):
            return {
                "accepted": candidate["path"] == "candidate-3.jpg",
                "status": "accepted" if candidate["path"] == "candidate-3.jpg" else "rejected",
                "score": 95 if candidate["path"] == "candidate-3.jpg" else 10,
            }

        with mock.patch.dict(os.environ, {"VIDZEO_VISUAL_FINALIST_LIMIT": "3"}, clear=False):
            result = resolve_storyboard_media_v2(
                [scene],
                youtube_lane=lambda _scene, _cancel: [],
                web_lane=lambda _scene, _cancel: candidates,
                visual_verifier=verify,
                neutral_factory=lambda _scene: None,
                deadline_seconds=2,
            )

        self.assertEqual(result[0].selected_path, "candidate-3.jpg")
        self.assertIn("primary_finalist_expansion", str(result[0].attempts))

    def test_duplicate_web_path_does_not_create_adjacent_reuse(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        scenes = [
            {
                "scene_id": f"scene-{index}", "scene_index": index,
                "container_id": "same-subject", "text": "Cristiano Ronaldo scores",
                "positive_subjects": ["Cristiano Ronaldo"], "forbidden_subjects": [],
                "source_policy": {"visual_verification_required": False},
            }
            for index in range(2)
        ]

        def web_lane(_scene, _cancel_event):
            return {
                "path": "duplicate-web.jpg", "source": "web", "provider": "web",
                "container_id": "same-subject", "score": 90,
            }

        result = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=lambda _scene, _cancel_event: [],
            web_lane=web_lane,
            visual_verifier=lambda _scene, _candidate: {"accepted": True, "score": 90},
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )
        assert result[0].selected_path == "duplicate-web.jpg"
        assert result[1].provider == "neutral"
        assert result[1].selected_path == ""
        assert result[1].resolution_kind == "neutral_no_claim"
        assert "web_path_already_selected" in str(result[1].attempts)

    def test_verified_topic_rescue_cannot_leak_between_containers(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        scenes = [
            {
                "scene_id": "ronaldo-1", "scene_index": 0, "container_id": "ronaldo",
                "text": "Cristiano Ronaldo scores", "positive_subjects": ["Cristiano Ronaldo"],
                "forbidden_subjects": [], "source_policy": {"visual_verification_required": False},
            },
            {
                "scene_id": "messi-1", "scene_index": 1, "container_id": "messi",
                "text": "Lionel Messi lifts the cup", "positive_subjects": ["Lionel Messi"],
                "forbidden_subjects": [], "source_policy": {"visual_verification_required": False},
            },
        ]

        def web_lane(scene, _cancel_event):
            if scene["scene_index"] == 0:
                return {
                    "path": "ronaldo.jpg", "source": "web", "provider": "web",
                    "container_id": "ronaldo", "score": 90,
                    "visual_decision": {"accepted": True, "status": "accepted"},
                }
            return []

        result = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=lambda _scene, _cancel_event: [],
            web_lane=web_lane,
            visual_verifier=lambda _scene, _candidate: {"accepted": True, "status": "accepted", "score": 90},
            neutral_factory=lambda _scene: None,
            deadline_seconds=2,
        )
        self.assertEqual(result[0].selected_path, "ronaldo.jpg")
        self.assertEqual(result[1].resolution_kind, "neutral_no_claim")
        self.assertIn("blocked_cross_container", str(result[1].attempts))


if __name__ == "__main__":
    unittest.main()
def test_first_narrative_scene_can_reuse_verified_same_entity_web_media():
    from services.media_resolution_coordinator import _web_reuse_allowed

    assert _web_reuse_allowed({
        "order": 0,
        "scene_index": 0,
        "container_id": "narrative",
        "visual_required": True,
        "named_factual": True,
        "source_policy": {"strict_real_media": True},
    })
    assert not _web_reuse_allowed({
        "order": 1,
        "scene_index": 1,
        "container_id": "narrative",
        "visual_required": True,
        "named_factual": True,
        "source_policy": {"strict_real_media": True},
    })


def test_global_reuse_preserves_attempt_ledger_for_later_stock_scenes(tmp_path, monkeypatch):
    import services.media_resolution_coordinator as coordinator
    image = tmp_path / 'real.jpg'
    image.write_bytes(b'fixture')
    stock = tmp_path / 'stock.mp4'
    stock.write_bytes(b'fixture')
    monkeypatch.setattr(coordinator, '_candidate_hard_gate', lambda *args: (True, 'pass'))
    scenes = [dict(scene_index=i, scene_id=str(i), container_id='other' if i == 8 else 'topic',
                   text='A documentary scene', visual_intent='documentary_context',
                   source_policy={'stock_last_resort_allowed': True}) for i in range(10)]
    def web(scene, _cancel):
        return ([{'path': str(image), 'provider': 'web', 'media_type': 'image',
                  'verification_status': 'verified'}] if scene['scene_index'] == 9 else [])
    def stocks(scene, _cancel):
        return ([{'path': str(stock), 'provider': 'pexels', 'media_type': 'video'}]
                if scene['scene_index'] == 8 else [])
    result = coordinator.resolve_storyboard_media_v2(
        scenes, youtube_lane=lambda *args: [], web_lane=web, stock_lane=stocks,
        visual_verifier=lambda *args: {'accepted': True, 'visual_checked': True,
                                      'visual_available': True, 'final_score': 95},
        deadline_seconds=3)
    assert result[0].resolution_kind == 'verified_global_recovery'
    assert result[8].selected_path == str(stock)
    assert any(a.get('stage') == 'stock_last_resort' for a in result[8].attempts)


def test_quota_retention_never_restores_an_explicit_visual_rejection():
    from services.media_resolution_coordinator import _quota_gate_is_hard_rejection

    assert _quota_gate_is_hard_rejection("explicit_scene_conflict", {"accepted": False})
    assert _quota_gate_is_hard_rejection("visual_rejected", {"accepted": False})
    assert _quota_gate_is_hard_rejection("visual_pass", {"fatal_mismatch": True, "reason": "wrong game"})
    assert not _quota_gate_is_hard_rejection("visual_unavailable", {"accepted": False, "reason": "insufficient pixels"})


def test_quota_retention_upgrades_fallback_stills_to_ready_youtube_cuts(tmp_path):
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_0 = tmp_path / "yt_clip_0.mp4"
    clip_0.write_bytes(b"\x00" * 1024)
    clip_1 = tmp_path / "yt_clip_1.mp4"
    clip_1.write_bytes(b"\x00" * 1024)
    still_0 = tmp_path / "web_still_0.jpg"
    still_0.write_bytes(b"\x00" * 1024)
    still_1 = tmp_path / "web_still_1.jpg"
    still_1.write_bytes(b"\x00" * 1024)

    scenes = [
        {
            "scene_id": f"sc-{i}",
            "scene_index": i,
            "container_id": "game-features",
            "text": f"GTA VI feature detail scene {i}",
            "visual_requirement": {
                "strict_identity": False,
                "domain": "gaming",
                "canonical_entity": "Grand Theft Auto VI",
            },
            "source_policy": {
                "mixed_media": True,
                "youtube_quota": True,
            },
        }
        for i in range(2)
    ]

    class FakeLazyYoutubeLane:
        def __init__(self):
            self._rows = {
                0: {
                    "path": str(clip_0),
                    "source": "youtube",
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified",
                    "visual_decision": {"accepted": True, "source": "youtube_pool_moment", "status": "accepted"},
                },
                1: {
                    "path": str(clip_1),
                    "source": "youtube",
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "accepted",
                    "verification_status": "verified",
                    "visual_decision": {"accepted": True, "source": "youtube_pool_moment", "status": "accepted"},
                },
            }

        def __call__(self, scene, cancel_event):
            # Simulate initial pending / slow worker that returns None during initial discovery
            return None

        def get_row(self, index, timeout=0.0):
            return self._rows.get(index)

    def fake_web_lane(scene, _cancel_event):
        idx = int(scene.get("scene_index", 0))
        path = str(still_0 if idx == 0 else still_1)
        return {
            "path": path,
            "source": "web",
            "provider": "web",
            "media_type": "image",
            "status": "accepted",
            "verification_status": "verified",
            "visual_decision": {"accepted": True},
        }

    yt_lane = FakeLazyYoutubeLane()
    visually_checked_paths = []

    def verify_late_clip(_scene, candidate):
        visually_checked_paths.append(str(candidate.get("path") or ""))
        return {"accepted": True, "status": "accepted", "score": 95}

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=yt_lane,
        web_lane=fake_web_lane,
        visual_verifier=verify_late_clip,
        neutral_factory=lambda _s: None,
        deadline_seconds=5.0,
    )

    assert set(visually_checked_paths) == {str(clip_0), str(clip_1)}
    assert result[0].provider == "youtube"
    assert result[0].media_type == "video"
    assert result[0].selected_path == str(clip_0)
    assert result[1].provider == "youtube"
    assert result[1].media_type == "video"
    assert result[1].selected_path == str(clip_1)


def _quota_clip_budget_scene():
    """One relaxed gaming quota scene (GTA VI shape).

    Strict like the production GTA scenes (``strict_identity`` and
    ``strict_real_media``): the scene must fail closed without vision, so
    the budget-exhausted best-so-far mint never applies to it.
    """
    return {
        "scene_id": "quota-budget-scene",
        "scene_index": 0,
        "container_id": "game-features",
        "text": "GTA VI feature detail scene",
        "visual_requirement": {
            "strict_identity": True,
            "domain": "gaming",
            "canonical_entity": "Grand Theft Auto VI",
        },
        "source_policy": {
            "mixed_media": True,
            "youtube_quota": True,
            "strict_real_media": True,
        },
    }


def test_quota_clip_batched_verdict_survives_expired_scene_budget(tmp_path):
    """Regression: GTA VI tests 14/15 — a quota clip whose batched vision
    verdict arrived after the per-scene budget expired was discarded
    (``not_selected_finalist``) and the scene fell to a still, even though
    the vision round trip was already paid for.

    Non-adaptive review (no discovery-time early checks) so the only way
    the clip can win is the batched verdict. The batch round trip (3s)
    outlasts the per-scene budget (1s, the floor).
    """
    import time

    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "yt_clip.mp4"
    clip.write_bytes(b"\x00" * 1024)
    still = tmp_path / "web_still.jpg"
    still.write_bytes(b"\x00" * 1024)

    def yt_lane(_scene, _cancel):
        return [{
            "path": str(clip), "source": "youtube", "provider": "youtube",
            "media_type": "video", "candidate_id": "youtube:testclip",
            "title": "Grand Theft Auto VI gameplay footage",
            "metadata_score": 0.0,
        }]

    def web_lane(_scene, _cancel):
        return [{
            "path": str(still), "source": "web", "provider": "web",
            "media_type": "image", "candidate_id": "web:teststill",
            "title": "Grand Theft Auto VI official screenshot",
            "final_score": 80.0,
        }]

    class SlowBatchVerifier:
        """One batched vision round trip slower than the scene budget."""

        def verify_batch(self, _scene, candidates):
            time.sleep(3.0)
            out = {}
            for cand in candidates:
                path = str(cand.get("path") or "")
                if path == str(clip):
                    out[path] = {
                        "accepted": True, "status": "accepted",
                        "visual_checked": True, "visual_available": True,
                        "identity_score": 90.0, "action_score": 85.0,
                        "context_score": 88.0, "final_score": 90.0,
                    }
                else:
                    out[path] = {
                        "accepted": False, "status": "rejected",
                        "visual_checked": True, "visual_available": True,
                        "identity_score": 10.0, "action_score": 10.0,
                        "context_score": 10.0, "final_score": 10.0,
                    }
            return out

        def __call__(self, _scene, _candidate):
            return {
                "accepted": True, "status": "accepted",
                "visual_checked": True, "visual_available": True,
                "final_score": 90.0,
            }

    result = resolve_storyboard_media_v2(
        [_quota_clip_budget_scene()],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        visual_verifier=SlowBatchVerifier(),
        neutral_factory=lambda _s: None,
        deadline_seconds=30.0,
        scene_budget_seconds=1.0,
        adaptive_media_review=False,
    )
    assert result[0].provider == "youtube"
    assert result[0].media_type == "video"
    assert result[0].selected_path == str(clip)
    # The paid-for batched verdict is consumed, not discarded on budget.
    assert any(
        a.get("stage") == "visual_gate"
        and a.get("status") == "visual_pass"
        and a.get("candidate_id") == "youtube:testclip"
        for a in result[0].attempts
    )
    assert not any(
        a.get("stage") == "visual_gate"
        and a.get("status") == "not_selected_finalist"
        and a.get("candidate_id") == "youtube:testclip"
        for a in result[0].attempts
    )


def test_quota_clip_recovery_verified_after_scene_budget_expires(tmp_path):
    """Regression: GTA VI test 12 — after the first YouTube clip was
    vision-rejected, the cross-lane second clip was never vision-checked
    (``skipped_after_scene_budget``) and the scene fell to a web still,
    even though a second real clip was on disk.

    The first clip carries a hard explicit-conflict rejection and the
    still a watermark rejection (both production verdict shapes), so the
    scene stays unresolved and must reach recovery. Each vision round
    trip (3s) outlasts the per-scene budget (1s, the floor), so recovery
    runs with the budget already expired.
    """
    import time

    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_1 = tmp_path / "yt_clip_1.mp4"
    clip_1.write_bytes(b"\x00" * 1024)
    clip_2 = tmp_path / "yt_clip_2.mp4"
    clip_2.write_bytes(b"\x00" * 1024)
    still = tmp_path / "web_still.jpg"
    still.write_bytes(b"\x00" * 1024)

    def yt_lane(_scene, _cancel):
        return [{
            "path": str(clip_1), "source": "youtube", "provider": "youtube",
            "media_type": "video", "candidate_id": "youtube:first",
            "title": "Grand Theft Auto VI gameplay footage",
            "metadata_score": 0.0,
        }]

    def web_lane(_scene, _cancel):
        # Finish after the YouTube lane so the discovery-time early check
        # takes the clip, not the still: the clip must be judged (and
        # rejected) in the primary gate for the cross-lane retry to trigger.
        time.sleep(0.5)
        return [{
            "path": str(still), "source": "web", "provider": "web",
            "media_type": "image", "candidate_id": "web:still",
            "title": "Grand Theft Auto VI official screenshot",
            "final_score": 80.0,
        }]

    def yt_fallback_lane(_scene, _cancel):
        return [{
            "path": str(clip_2), "source": "youtube", "provider": "youtube",
            "media_type": "video", "candidate_id": "youtube:second",
            "title": "Grand Theft Auto VI gameplay footage",
            "metadata_score": 0.0,
        }]

    yt_fallback_lane.retry_rejected_primary = True

    verified = []

    def slow_verifier(_scene, candidate):
        # One slow inbox-style round trip per call: the first call alone
        # spends the whole per-scene budget.
        time.sleep(3.0)
        path = str(candidate.get("path") or "")
        verified.append(path)
        if path == str(clip_1):
            # Hard explicit-conflict rejection (production shape): the
            # relaxed gate must not soft-retain this clip.
            return {
                "accepted": False, "status": "rejected",
                "visual_checked": True, "visual_available": True,
                "fatal_mismatch": True,
                "reason": "wrong game: footage shows a different game, not Grand Theft Auto VI",
                "rejection_reason": "explicit_scene_conflict",
                "identity_score": 10.0, "action_score": 10.0,
                "context_score": 10.0, "final_score": 10.0,
            }
        if path == str(still):
            # Hard watermark rejection, as in production test 12 scene 4.
            return {
                "accepted": False, "status": "rejected",
                "visual_checked": True, "visual_available": True,
                "watermark": True,
                "reason": "watermark_detected",
                "identity_score": 10.0, "action_score": 10.0,
                "context_score": 10.0, "final_score": 10.0,
            }
        return {
            "accepted": True, "status": "accepted",
            "visual_checked": True, "visual_available": True,
            "identity_score": 90.0, "action_score": 85.0,
            "context_score": 88.0, "final_score": 90.0,
        }

    result = resolve_storyboard_media_v2(
        [_quota_clip_budget_scene()],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        youtube_fallback_lane=yt_fallback_lane,
        visual_verifier=slow_verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=30.0,
        scene_budget_seconds=1.0,
        adaptive_media_review=True,
    )
    assert str(clip_2) in verified
    assert result[0].provider == "youtube"
    assert result[0].media_type == "video"
    assert result[0].selected_path == str(clip_2)
    assert not any(
        a.get("status") == "skipped_after_scene_budget"
        and a.get("candidate_id") == "youtube:second"
        for a in result[0].attempts
    )


def test_quota_clip_inline_rejection_still_earns_cross_lane_retry(tmp_path):
    """Regression: a quota clip first judged (and rejected) in the inline
    branch — because the primary gate never reached it before the
    per-scene budget expired — must still earn its cross-lane retry.

    The batched round trip fails after outlasting the budget, so the
    primary gate judges nothing; the clip is verified inline and
    rejected there. Without the late-rejection record, the recovery
    specs (computed before selection) would never offer the second clip.
    """
    import time

    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_1 = tmp_path / "yt_clip_1.mp4"
    clip_1.write_bytes(b"\x00" * 1024)
    clip_2 = tmp_path / "yt_clip_2.mp4"
    clip_2.write_bytes(b"\x00" * 1024)
    still = tmp_path / "web_still.jpg"
    still.write_bytes(b"\x00" * 1024)

    def yt_lane(_scene, _cancel):
        # Finish after the web lane so the discovery-time early check
        # takes the still: the clip must stay unjudged until selection.
        time.sleep(0.5)
        return [{
            "path": str(clip_1), "source": "youtube", "provider": "youtube",
            "media_type": "video", "candidate_id": "youtube:first",
            "title": "Grand Theft Auto VI gameplay footage",
            "metadata_score": 0.0,
        }]

    def web_lane(_scene, _cancel):
        return [{
            "path": str(still), "source": "web", "provider": "web",
            "media_type": "image", "candidate_id": "web:still",
            "title": "Grand Theft Auto VI official screenshot",
            "final_score": 80.0,
        }]

    def yt_fallback_lane(_scene, _cancel):
        return [{
            "path": str(clip_2), "source": "youtube", "provider": "youtube",
            "media_type": "video", "candidate_id": "youtube:second",
            "title": "Grand Theft Auto VI gameplay footage",
            "metadata_score": 0.0,
        }]

    yt_fallback_lane.retry_rejected_primary = True

    verified = []

    class FailingBatchVerifier:
        """Batch fails after outlasting the budget; single verifies are slow."""

        def verify_batch(self, _scene, _candidates):
            time.sleep(3.0)
            raise RuntimeError("batch transport failed")

        def __call__(self, _scene, candidate):
            time.sleep(3.0)
            path = str(candidate.get("path") or "")
            verified.append(path)
            if path == str(clip_1):
                return {
                    "accepted": False, "status": "rejected",
                    "visual_checked": True, "visual_available": True,
                    "fatal_mismatch": True,
                    "reason": "wrong game: footage shows a different game, not Grand Theft Auto VI",
                    "rejection_reason": "explicit_scene_conflict",
                    "identity_score": 10.0, "action_score": 10.0,
                    "context_score": 10.0, "final_score": 10.0,
                }
            if path == str(still):
                return {
                    "accepted": False, "status": "rejected",
                    "visual_checked": True, "visual_available": True,
                    "watermark": True,
                    "reason": "watermark_detected",
                    "identity_score": 10.0, "action_score": 10.0,
                    "context_score": 10.0, "final_score": 10.0,
                }
            return {
                "accepted": True, "status": "accepted",
                "visual_checked": True, "visual_available": True,
                "identity_score": 90.0, "action_score": 85.0,
                "context_score": 88.0, "final_score": 90.0,
            }

    result = resolve_storyboard_media_v2(
        [_quota_clip_budget_scene()],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        youtube_fallback_lane=yt_fallback_lane,
        visual_verifier=FailingBatchVerifier(),
        neutral_factory=lambda _s: None,
        deadline_seconds=30.0,
        scene_budget_seconds=1.0,
        adaptive_media_review=True,
    )
    assert str(clip_2) in verified
    assert result[0].provider == "youtube"
    assert result[0].media_type == "video"
    assert result[0].selected_path == str(clip_2)


def test_quota_gate_hard_rejection_not_softened_by_era_prose():
    """The gate's reason code is authoritative, not the model's prose.

    GTA VI tests 12/15: both clips were visual_rejected for severe
    overlays, but ``_quota_gate_is_hard_rejection`` returned False because
    the model's free-text reason contained the substring "era" ("it does
    not literally show the subject" / "the subject, game, era and action
    match") and the spurious-token scan ran before the hard-code check.
    """
    from services.media_resolution_coordinator import _quota_gate_is_hard_rejection

    scene = _quota_clip_budget_scene()
    assert _quota_gate_is_hard_rejection(
        "visual_rejected",
        {
            "accepted": False, "visual_checked": True, "severe_overlay": True,
            "rejection_reason": "severe_overlay_or_subtitle_detected",
            "reason": "Accept: authentic GTA VI gameplay. Approximate: it "
            "shows open-water Leonida, not Vice City streets, and the exact "
            "action of the city opening is not literally visible.",
        },
        scene=scene,
    )
    assert _quota_gate_is_hard_rejection(
        "visual_rejected",
        {
            "accepted": False, "visual_checked": True,
            "reason": "Authentic GTA VI gameplay of a female character on "
            "the run from police in Vice City; it does not literally show "
            "Liberty City, but the subject, game, era and criminal-on-the-run "
            "action match the scene at subject level.",
        },
        scene=scene,
    )
    # A non-hard code is still softened by spurious prose.
    assert not _quota_gate_is_hard_rejection(
        "visual_unavailable",
        {"accepted": False, "reason": "footage from an older era, insufficient pixels"},
        scene=scene,
    )


def test_quota_retention_never_restores_a_live_visual_rejection(tmp_path, capsys):
    """Regression: GTA VI test 12 scene 12 / test 15 scene 4.

    The YouTube clip was visual_rejected at the coordinator's vision gate
    (severe overlay / subtitle), yet quota retention shipped it as
    ``youtube_quota_upgrade``: the hard-rejection check was fooled by the
    substring "era" in the model's prose, and the agent-job / relaxed
    soft-pass then minted ``accepted=True`` over the live rejection.
    The scene must stay on its web still; the rejection is respected.
    """
    from services.job_context import job_scope
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "yt_clip.mp4"
    clip.write_bytes(b"\x00" * 1024)
    still = tmp_path / "web_still.jpg"
    still.write_bytes(b"\x00" * 1024)

    def yt_lane(_scene, _cancel):
        return [{
            "path": str(clip), "source": "youtube", "provider": "youtube",
            "media_type": "video", "candidate_id": "youtube:ryckrUic0dU",
            "title": "Grand Theft Auto VI gameplay footage",
            "metadata_score": 0.0,
        }]

    def web_lane(_scene, _cancel):
        return [{
            "path": str(still), "source": "web", "provider": "web",
            "media_type": "image", "candidate_id": "web:teststill",
            "title": "Grand Theft Auto VI official screenshot",
            "final_score": 80.0,
        }]

    def verifier(_scene, candidate):
        if str(candidate.get("path") or "") == str(clip):
            # Production gate-time verdict for test 12 scene 12
            # (ryckrUic0dU): rejected for severe overlays; the prose
            # contains "literally", which used to defeat the hard check.
            return {
                "accepted": False, "status": "rejected",
                "verification_status": "visual_verification_pending",
                "visual_checked": True, "visual_available": True,
                "semantic_verified": False, "deterministic_only": True,
                "candidate_id": "youtube:ryckrUic0dU",
                "entity_conflict": False, "fatal_mismatch": False,
                "identity_score": 80.0, "action_score": 60.0,
                "context_score": 80.0, "final_score": 76.0,
                "severe_overlay": True, "watermark": True, "subtitle": False,
                "rejection_reason": "severe_overlay_or_subtitle_detected",
                "reason": "Accept: authentic GTA VI gameplay showing "
                "Leonida's open water/Keys environment. Approximate: it "
                "shows open-water Leonida, not Vice City streets, and the "
                "exact action of the city opening is not literally visible.",
                "scene_description": "GTA VI gameplay: a man fishing from "
                "the water in the Leonida Keys/Grassrivers area.",
            }
        return {
            "accepted": True, "status": "accepted",
            "visual_checked": True, "visual_available": True,
            "identity_score": 90.0, "action_score": 85.0,
            "context_score": 88.0, "final_score": 90.0,
        }

    # Production shape: an external-agent job, so the agent-job soft-pass
    # that used to resurrect the clip is live here too.
    with job_scope({"llm_provider": "external_agent"}):
        result = resolve_storyboard_media_v2(
            [_quota_clip_budget_scene()],
            youtube_lane=yt_lane,
            web_lane=web_lane,
            visual_verifier=verifier,
            neutral_factory=lambda _s: None,
            deadline_seconds=30.0,
            adaptive_media_review=False,
        )
    assert result[0].provider == "web"
    assert result[0].selected_path == str(still)
    assert not any(
        a.get("stage") == "quota_retention_own_clip_upgrade"
        and a.get("status") == "upgraded_to_scene_youtube_cut"
        for a in result[0].attempts
    )
    assert any(
        a.get("stage") == "visual_gate"
        and a.get("status") == "visual_rejected"
        and a.get("candidate_id") == "youtube:ryckrUic0dU"
        for a in result[0].attempts
    )
    assert "live vision rejection respected" in capsys.readouterr().out


def _quota_retention_strict_scene(tmp_path, clip_path, still_path, pool_decision_extra=None):
    """One strict quota scene whose YouTube cut arrives late (lazy lane).

    Mirrors ff377408: the clip is downloaded and on disk, its pool decision
    is still ``visual_verification_pending`` (no live vision verdict yet),
    and the scene falls back to a web still in the primary gate.
    """
    clip_path.write_bytes(b"verified rail crew cut bytes")
    still_path.write_bytes(b"web still bytes")
    scene = {
        "scene_id": "quota-strict-scene",
        "scene_index": 0,
        "container_id": "rail-crew",
        "text": "Test rail crew operations documentary scene",
        "visual_requirement": {
            "strict_identity": True,
            "domain": "rail_transport",
            "subject_type": "rail_crew",
            "canonical_entity": "Test Rail Crew",
            "requirement_hash": "quota-strict-requirement-hash",
        },
        "source_policy": {
            "mixed_media": True,
            "youtube_quota": True,
        },
    }

    class FakeLazyYoutubeLane:
        def __init__(self):
            self._calls = 0
            # Deliberately NOT named ``_rows``: the batch promoter falls
            # back to ``lane._rows`` when get_row returns None, which would
            # leak the cut into the primary gate.
            self._late_rows = {
                0: {
                    "path": str(clip_path),
                    "source": "youtube",
                    "provider": "youtube",
                    "media_type": "video",
                    "status": "downloaded",
                    "verification_status": "visual_verification_pending",
                    "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "asset_fingerprint": "quota-strict-clip",
                    "overlay_clean": True,
                    "title": "Test rail crew operations footage",
                    "visual_decision": {
                        "accepted": False,
                        "status": "visual_verification_pending",
                        "source": "youtube_pool_moment",
                        "recovery_kind": "disk_verified_pool_cut",
                        "visual_checked": False,
                        "semantic_verified": False,
                        "visual_available": False,
                        "deterministic_only": True,
                        "requirement_hash": "quota-strict-requirement-hash",
                        **(pool_decision_extra or {}),
                    },
                },
            }

        def __call__(self, scene, cancel_event):
            return None

        def get_row(self, index, timeout=0.0):
            # The cut finishes downloading after the pre-gate batch
            # promotions (two get_row calls) but before quota retention:
            # the production case the quota lane exists for. Returning it
            # earlier would let the primary gate select it directly and
            # the test would not exercise the upgrade construction.
            self._calls += 1
            if self._calls <= 2:
                return None
            return self._late_rows.get(index)

    def fake_web_lane(scene, _cancel_event):
        return {
            "path": str(still_path),
            "source": "web",
            "provider": "web",
            "media_type": "image",
            "status": "accepted",
            "verification_status": "verified",
            "title": "Test rail crew operations photo",
            "visual_decision": {"accepted": True},
        }

    return scene, FakeLazyYoutubeLane(), fake_web_lane


def test_quota_upgrade_stores_gate_approved_vision_verdict(tmp_path):
    """A vision-accepted quota clip survives to the final plan (ff377408).

    The late quota lane gives the downloaded clip its bounded vision pass;
    the upgrade must store that live verdict — accepted + visual_checked +
    bound requirement hash + bound asset sha — so the downstream contract
    gate (_visual_requirement_issue) accepts the row instead of rejecting
    it as 'visual_verdict_rejected'. Before the fix the construction block
    re-read the candidate's raw pending decision, so the row claimed
    accepted while its decision said pending, and 3 of 4 retained clips
    were dropped to graphics.
    """
    import hashlib

    from services.media_resolution_coordinator import resolve_storyboard_media_v2
    from services.pipeline_v2_media_adapter import _visual_requirement_issue

    clip = tmp_path / "quota-cut.mp4"
    still = tmp_path / "quota-still.jpg"
    scene, yt_lane, web_lane = _quota_retention_strict_scene(tmp_path, clip, still)
    clip_sha = hashlib.sha256(clip.read_bytes()).hexdigest()

    def live_verifier(_scene, candidate):
        if str(candidate.get("source") or "") == "youtube":
            return {
                "accepted": True,
                "status": "accepted",
                "visual_checked": True,
                "semantic_verified": True,
                "visual_available": True,
                "identity_score": 90.0,
                "final_score": 90.0,
                "reason": "Authentic rail crew footage",
            }
        return {"accepted": True, "status": "accepted", "visual_checked": True}

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        visual_verifier=live_verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=30.0,
    )

    assert result[0].provider == "youtube"
    assert result[0].selected_path == str(clip)
    decision = dict(result[0].visual_decision or {})
    assert decision.get("accepted") is True
    assert decision.get("visual_checked") is True
    assert decision.get("verified_requirement_hash") == "quota-strict-requirement-hash"
    assert decision.get("verified_asset_sha256") == clip_sha
    assert any(
        a.get("stage") == "quota_retention_own_clip_upgrade"
        and a.get("status") == "upgraded_to_scene_youtube_cut"
        for a in result[0].attempts
    )
    # The downstream gate that dropped ff377408 scenes 1, 2, 6 must accept
    # this row: a live, bound vision verdict is present.
    assert (
        _visual_requirement_issue(scene, result[0].to_dict(), result[0], "YouTube Clips")
        == ""
    )


def test_quota_upgrade_never_mints_acceptance_from_download_alone(tmp_path):
    """A downloaded clip with no live vision accept is not upgraded.

    Fail-closed: the pool already looked at this cut (visual_checked) and
    did not accept it, and the coordinator has no live gated verdict for
    it — the late lane skips already-checked candidates. The only remaining
    evidence is "the file exists and is clean" (the ff377408 shape: the
    clip downloaded, vision never accepted it). Quota retention must leave
    the scene on its web still instead of minting accepted/visual_checked
    from the download and promoting a clip the downstream gate then
    rejects as unverified.
    """
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "quota-cut.mp4"
    still = tmp_path / "quota-still.jpg"
    scene, yt_lane, web_lane = _quota_retention_strict_scene(
        tmp_path, clip, still, pool_decision_extra={"visual_checked": True}
    )

    def unavailable_verifier(_scene, candidate):
        if str(candidate.get("source") or "") == "youtube":
            return {"accepted": False, "status": "vision_unavailable", "visual_available": False}
        return {"accepted": True, "status": "accepted", "visual_checked": True}

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        visual_verifier=unavailable_verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=30.0,
    )

    assert result[0].provider == "web"
    assert result[0].selected_path == str(still)
    assert not any(
        a.get("stage") == "quota_retention_own_clip_upgrade"
        and a.get("status") == "upgraded_to_scene_youtube_cut"
        for a in result[0].attempts
    )


def test_quota_retention_fail_open_still_mints_a_never_judged_clip(tmp_path, monkeypatch):
    """The legitimate fail-open survives the live-rejection fix.

    When vision never judged the clip there is genuinely no verdict for
    it — it arrives via the lane row after discovery, and the late quota
    lane never verifies it — so the retention fail-open mints it as
    ``deterministic_degraded``. Only a LIVE rejection is a veto, never a
    missing verdict. The web still is watermark-rejected so the scene is
    unresolved at retention (retention only fills unresolved quota slots).
    Guards against over-blocking the fix above.
    """
    import services.media_resolution_coordinator as coordinator
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "quota-cut.mp4"
    still = tmp_path / "quota-still.jpg"
    scene, yt_lane, web_lane = _quota_retention_strict_scene(tmp_path, clip, still)

    real_hard_gate = coordinator._candidate_hard_gate

    def hard_gate(scene, candidate):
        # Fixture the "never judged" shape: keep the downloaded cut out
        # of the late quota verification lane, so no gated outcome is
        # recorded for it at all.
        if str(candidate.get("path") or "") == str(clip):
            return False, "fixture_no_vision_verdict"
        return real_hard_gate(scene, candidate)

    monkeypatch.setattr(coordinator, "_candidate_hard_gate", hard_gate)

    def verifier(_scene, candidate):
        if str(candidate.get("source") or "") == "youtube":
            raise AssertionError("a never-judged clip must not reach vision")
        return {
            "accepted": False, "status": "rejected",
            "visual_checked": True, "visual_available": True,
            "watermark": True, "identity_score": 10.0,
            "action_score": 10.0, "context_score": 10.0, "final_score": 10.0,
            "reason": "watermark_detected",
        }

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=yt_lane,
        web_lane=web_lane,
        visual_verifier=verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=30.0,
    )

    assert result[0].provider == "youtube"
    assert result[0].selected_path == str(clip)
    assert any(
        a.get("stage") == "quota_retention_own_clip_upgrade"
        and a.get("status") == "upgraded_to_scene_youtube_cut"
        for a in result[0].attempts
    )
    # The mint is the auditable fail-open, not a live vision pass.
    decision = dict(result[0].visual_decision or {})
    assert decision.get("status") == "deterministic_degraded"


def test_quota_retention_agent_job_fail_open_survives_vision_outage(tmp_path):
    """A vision outage still fail-opens for agent jobs (production shape).

    Strict scene, vision down (``vision_unavailable`` recorded by the
    late quota lane), scene unresolved: the agent-job soft-pass mints the
    downloaded cut. The live-rejection veto must not fire here — there
    is no live verdict, only a transport failure — or the outage path
    that production relies on would fail closed.
    """
    from services.job_context import job_scope
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "quota-cut.mp4"
    still = tmp_path / "quota-still.jpg"
    scene, yt_lane, web_lane = _quota_retention_strict_scene(tmp_path, clip, still)

    def verifier(_scene, candidate):
        if str(candidate.get("source") or "") == "youtube":
            return {"accepted": False, "status": "vision_unavailable",
                    "reason": "vision_unavailable", "visual_checked": False,
                    "visual_available": False}
        return {
            "accepted": False, "status": "rejected",
            "visual_checked": True, "visual_available": True,
            "watermark": True, "identity_score": 10.0,
            "action_score": 10.0, "context_score": 10.0, "final_score": 10.0,
            "reason": "watermark_detected",
        }

    with job_scope({"llm_provider": "external_agent"}):
        result = resolve_storyboard_media_v2(
            [scene],
            youtube_lane=yt_lane,
            web_lane=web_lane,
            visual_verifier=verifier,
            neutral_factory=lambda _s: None,
            deadline_seconds=30.0,
        )

    assert result[0].provider == "youtube"
    assert result[0].selected_path == str(clip)
    assert any(
        a.get("stage") == "quota_retention_own_clip_upgrade"
        and a.get("status") == "upgraded_to_scene_youtube_cut"
        for a in result[0].attempts
    )
    # No live vision behind the mint: the verdict still says so.
    decision = dict(result[0].visual_decision or {})
    assert decision.get("accepted") is True
    assert decision.get("status") == "vision_unavailable"


def test_late_verification_deadline_floors_when_delivery_wall_spent():
    """The late quota vision lane still runs when the delivery wall is spent.

    ff377408: YouTube discovery took 954s of a 1033s media deadline, so the
    delivery deadline was already past when quota retention ran and the old
    min() clamp made the late lane dead on arrival. The floor keeps a
    bounded window (at most the configured late seconds, capped at 60s past
    the wall) for already-downloaded clips only; it never reopens discovery.
    """
    import time

    from services.media_resolution_coordinator import _late_local_verification_deadline

    now = time.monotonic()
    spent = _late_local_verification_deadline(2, delivery_deadline=now - 10.0)
    # +1s tolerance: ``now`` was captured just before the call.
    assert now < spent <= now + 61.0
    future = _late_local_verification_deadline(2, delivery_deadline=now + 30.0)
    assert now < future <= now + 31.0
    assert _late_local_verification_deadline(0, delivery_deadline=now - 10.0) <= now + 1.0


# ---------------------------------------------------------------------------
# Vision + budget (worker D): validator-only vision, game-engine veto fix,
# split discovery/verification clocks with vision reserve, liveness-aware
# discovery clock, reserved frame-fallback allowance.
# ---------------------------------------------------------------------------
import base64 as _b64

_PNG_1X1 = _b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _gta_scene(index=0):
    return {
        "scene_id": f"gta-{index}",
        "scene_index": index,
        "container_id": "gta",
        "text": "Grand Theft Auto VI gameplay in Vice City",
        "domain_category": "gaming",
        "positive_subjects": ["Grand Theft Auto VI"],
        "forbidden_subjects": [],
        "source_policy": {"youtube_quota": True, "visual_verification_required": False},
    }


def _png_file(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(_PNG_1X1)
    return str(path)


def _yt_clip(path, score=80, clip_id="yt:trailer2"):
    return {
        "path": path,
        "source": "youtube",
        "provider": "youtube",
        "candidate_id": clip_id,
        "title": "Grand Theft Auto VI Trailer 2",
        "description": "Official GTA VI trailer footage",
        "score": score,
        "final_score": score,
        "media_type": "video",
    }


def _reject_verdict(reason="not the subject"):
    return {
        "accepted": False, "status": "rejected", "final_score": 20,
        "identity_score": 20, "action_score": 20, "context_score": 20,
        "visual_checked": True, "visual_available": True, "reason": reason,
    }


def _accept_verdict(reason="frame shows GTA VI gameplay in Vice City"):
    return {
        "accepted": True, "status": "accepted", "final_score": 90,
        "identity_score": 90, "action_score": 85, "context_score": 88,
        "visual_checked": True, "visual_available": True, "reason": reason,
    }


class _CountingVerifier:
    """Fake verifier: __call__ is the single-candidate path, verify_batch
    is the batched compare path. Both count their invocations."""

    def __init__(self, verdict_by_path=None, single=None, single_fn=None):
        self.verdict_by_path = dict(verdict_by_path or {})
        self.single = single if single is not None else _reject_verdict()
        self.single_fn = single_fn
        self.single_calls = []
        self.batch_calls = []

    def __call__(self, scene, candidate):
        self.single_calls.append(str(candidate.get("path") or ""))
        if self.single_fn is not None:
            return self.single_fn(scene, candidate)
        return dict(self.single)

    def verify_batch(self, scene, candidates):
        self.batch_calls.append([str(c.get("path") or "") for c in candidates])
        out = {}
        for candidate in candidates:
            path = str(candidate.get("path") or "")
            out[path] = dict(self.verdict_by_path.get(path, self.single))
        return out


def _patch_frame_extraction(monkeypatch, frames_by_clip):
    from services.visual_intelligence import frame_extractor

    def fake_extract(path, out_dir=None, **kwargs):
        return list(frames_by_clip.get(str(path), [])), 7.0

    monkeypatch.setattr(frame_extractor, "extract_clip_frames", fake_extract)


def test_frame_fallbacks_get_reserved_batched_vision_after_scene_budget(tmp_path, monkeypatch):
    """(a) A slow primary verifier eats the whole scene budget; the
    same-video frame fallbacks still receive exactly 1 batched vision call
    and a recorded verdict — never skipped_after_scene_budget."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_path = _png_file(tmp_path, "trailer2.mp4")
    frame1 = _png_file(tmp_path, "frame1.png")
    frame2 = _png_file(tmp_path, "frame2.png")
    _patch_frame_extraction(monkeypatch, {clip_path: [frame1, frame2]})
    winner = _accept_verdict()
    loser = {"accepted": False, "status": "compare_not_selected", "final_score": 5,
             "identity_score": 5, "visual_checked": False, "visual_available": False,
             "reason": "compare_not_selected"}
    def slow_single(scene, candidate):
        time.sleep(1.5)
        return _reject_verdict()

    verifier = _CountingVerifier(
        verdict_by_path={frame1: winner, frame2: loser},
        single_fn=slow_single,
    )

    result = resolve_storyboard_media_v2(
        [_gta_scene(0)],
        youtube_lane=lambda _s, _c: [_yt_clip(clip_path)],
        web_lane=lambda _s, _c: [],
        visual_verifier=verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
        scene_budget_seconds=1,
        vision_reserve_seconds=1,
    )
    assert len(verifier.batch_calls) == 1
    assert sorted(verifier.batch_calls[0]) == sorted([frame1, frame2])
    assert result[0].selected_path == frame1
    # A recorded verdict for the frame (the extracted-frame recovery lane
    # re-labels the reason with its own stamp; the verdict itself — a live
    # vision accept — is what must be present, not a skip).
    decision = result[0].visual_decision or {}
    assert decision.get("accepted") is True
    assert decision.get("visual_checked") is True
    assert str(decision.get("reason") or "").strip()
    assert not any(
        a.get("stage") == "visual_gate" and a.get("status") == "skipped_after_scene_budget"
        for a in result[0].attempts
    )


def test_slow_primary_verifier_resolves_best_so_far_not_unverified_pool(tmp_path, monkeypatch):
    """(b) Fake slow primary verifier consumes the whole scene budget and
    there is no batched path: the scene resolves via scene_budget_best_so_far
    — never via an unverified pool pick."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_path = _png_file(tmp_path, "trailer2.mp4")
    frame1 = _png_file(tmp_path, "frame1.png")
    frame2 = _png_file(tmp_path, "frame2.png")
    _patch_frame_extraction(monkeypatch, {clip_path: [frame1, frame2]})

    def slow_verify(scene, candidate):
        time.sleep(1.5)
        return _reject_verdict()

    result = resolve_storyboard_media_v2(
        [_gta_scene(0)],
        youtube_lane=lambda _s, _c: [_yt_clip(clip_path)],
        web_lane=lambda _s, _c: [],
        visual_verifier=slow_verify,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
        scene_budget_seconds=1,
        vision_reserve_seconds=1,
    )
    assert result[0].selected_path == frame1
    assert any(
        a.get("stage") == "visual_gate" and a.get("status") == "scene_budget_best_so_far"
        for a in result[0].attempts
    )
    # The selected frame resolved via best-so-far, not via a skip: its own
    # visual-gate attempt carries the best-so-far status. (The unselected
    # frame may legitimately record a budget skip — it was never verified.)


def test_full_chain_costs_at_most_two_batched_calls(tmp_path, monkeypatch):
    """(c) Primary 3 + frame fallbacks 4 cost <=2 batched vision calls:
    one compare for the primaries, one for the frames."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    web_paths = [_png_file(tmp_path, f"web{i}.png") for i in range(3)]
    clip1 = _png_file(tmp_path, "clip1.mp4")
    clip2 = _png_file(tmp_path, "clip2.mp4")
    frames1 = [_png_file(tmp_path, "f1a.png"), _png_file(tmp_path, "f1b.png")]
    frames2 = [_png_file(tmp_path, "f2a.png"), _png_file(tmp_path, "f2b.png")]
    _patch_frame_extraction(monkeypatch, {clip1: frames1, clip2: frames2})
    winner = _accept_verdict()
    loser = {"accepted": False, "status": "compare_not_selected", "final_score": 5,
             "identity_score": 5, "visual_checked": False, "visual_available": False,
             "reason": "compare_not_selected"}
    verdicts = {frames1[0]: winner}
    for path in frames1[1:] + frames2:
        verdicts[path] = loser
    verifier = _CountingVerifier(verdict_by_path=verdicts)

    def web_lane(_s, _c):
        # Web stills score BELOW the clips here on purpose in the original
        # variant; the realistic case (web outranking the clips) is covered
        # by test_frame_fallback_filters_to_youtube_before_top_slice below —
        # since the Nov 16 fix the builder filters to YouTube sources
        # BEFORE slicing to the top-2, so a web-dominated pool still yields
        # frames from the downloaded clips.
        return [{
            "path": path, "source": "web", "provider": "web",
            "source_url": f"https://example.test/{i}.jpg",
            "title": "Grand Theft Auto VI key art", "score": 30, "final_score": 30,
        } for i, path in enumerate(web_paths)]

    result = resolve_storyboard_media_v2(
        [_gta_scene(0)],
        youtube_lane=lambda _s, _c: [_yt_clip(clip1, 50, "yt:clip1"), _yt_clip(clip2, 40, "yt:clip2")],
        web_lane=web_lane,
        visual_verifier=verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
    )
    assert len(verifier.batch_calls) == 2
    assert sorted(len(call) for call in verifier.batch_calls) == [3, 4]
    assert result[0].selected_path == frames1[0]


def test_frame_fallback_filters_to_youtube_before_top_slice(tmp_path, monkeypatch):
    """Fix 3 (Nov 16): a web-dominated pool used to fill both top-2 slots
    with stills, the builder then skipped them, yielded [], and the reserved
    frame vision never fired. The builder must filter to YouTube sources
    BEFORE slicing — web stills outranking the clips is the normal case."""
    from services.media_resolution_coordinator import _youtube_frame_fallback_candidates

    clip1 = _png_file(tmp_path, "clip1.mp4")
    clip2 = _png_file(tmp_path, "clip2.mp4")
    frames1 = [_png_file(tmp_path, "f1a.png"), _png_file(tmp_path, "f1b.png")]
    frames2 = [_png_file(tmp_path, "f2a.png"), _png_file(tmp_path, "f2b.png")]
    _patch_frame_extraction(monkeypatch, {clip1: frames1, clip2: frames2})
    web_paths = [_png_file(tmp_path, f"web{i}.png") for i in range(3)]
    candidates = [
        {"path": path, "source": "web", "provider": "web",
         "title": "Grand Theft Auto VI key art", "score": 95 - i, "final_score": 95 - i}
        for i, path in enumerate(web_paths)
    ] + [_yt_clip(clip1, 50, "yt:clip1"), _yt_clip(clip2, 40, "yt:clip2")]
    frames = _youtube_frame_fallback_candidates(_gta_scene(0), candidates, 0)
    assert len(frames) == 4
    assert sorted(f.get("fallback_source_path") for f in frames) == sorted(
        [clip1, clip1, clip2, clip2])
    assert all(f.get("fallback_layer") == "same_video_timestamp_frame" for f in frames)


def test_full_chain_frame_fallback_fires_when_web_outranks_clips(tmp_path, monkeypatch):
    """Same fix at full-chain level: web stills score 90+ while the clips
    score 50/40, yet the frame fallback still builds from the clips and the
    reserved frame vision fires (2 batched calls total, frame selected)."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    web_paths = [_png_file(tmp_path, f"web{i}.png") for i in range(3)]
    clip1 = _png_file(tmp_path, "clip1.mp4")
    clip2 = _png_file(tmp_path, "clip2.mp4")
    frames1 = [_png_file(tmp_path, "f1a.png"), _png_file(tmp_path, "f1b.png")]
    frames2 = [_png_file(tmp_path, "f2a.png"), _png_file(tmp_path, "f2b.png")]
    _patch_frame_extraction(monkeypatch, {clip1: frames1, clip2: frames2})
    winner = _accept_verdict()
    loser = {"accepted": False, "status": "compare_not_selected", "final_score": 5,
             "identity_score": 5, "visual_checked": False, "visual_available": False,
             "reason": "compare_not_selected"}
    verdicts = {frames1[0]: winner}
    for path in frames1[1:] + frames2:
        verdicts[path] = loser
    verifier = _CountingVerifier(verdict_by_path=verdicts)

    def web_lane(_s, _c):
        return [{
            "path": path, "source": "web", "provider": "web",
            "source_url": f"https://example.test/{i}.jpg",
            "title": "Grand Theft Auto VI key art", "score": 95 - i, "final_score": 95 - i,
        } for i, path in enumerate(web_paths)]

    result = resolve_storyboard_media_v2(
        [_gta_scene(0)],
        youtube_lane=lambda _s, _c: [_yt_clip(clip1, 50, "yt:clip1"), _yt_clip(clip2, 40, "yt:clip2")],
        web_lane=web_lane,
        visual_verifier=verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
    )
    assert len(verifier.batch_calls) == 2
    assert sorted(len(call) for call in verifier.batch_calls) == [3, 4]
    assert result[0].selected_path == frames1[0]


def test_skip_records_visual_checked_false_and_reason(tmp_path, monkeypatch):
    """(e) A skip records visual_checked: False + a reason in the ledger,
    never a bare deadline_exceeded."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    paths = [_png_file(tmp_path, f"exp{i}.png") for i in range(8)]
    monkeypatch.setenv("VIDZEO_VISUAL_PRIMARY_EXPANSION_LIMIT", "6")
    monkeypatch.setenv("VIDZEO_VISUAL_FALLBACK_FINALIST_LIMIT", "6")

    def web_lane(_s, _c):
        return [{
            "path": path, "source": "web", "provider": "web",
            "source_url": f"https://example.test/{i}.jpg",
            "title": "Grand Theft Auto VI key art", "score": 95 - i, "final_score": 95 - i,
        } for i, path in enumerate(paths)]

    def verify(_s, _c):
        return _reject_verdict()

    result = resolve_storyboard_media_v2(
        [_gta_scene(0)],
        youtube_lane=lambda _s, _c: [],
        web_lane=web_lane,
        visual_verifier=verify,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
    )
    skipped = [
        a for a in result[0].attempts
        if a.get("stage") == "visual_gate"
        and a.get("status") == "skipped_after_vision_start_budget"
    ]
    assert skipped, "expected a vision-start-budget skip in the ledger"
    ledger = {row.get("path"): row for row in result[0].candidate_ledger}
    recorded = [
        row for row in ledger.values()
        if (row.get("visual_decision") or {}).get("reason") == "skipped_after_vision_start_budget"
    ]
    assert recorded, "expected the skip verdict in the candidate ledger"
    for row in recorded:
        assert row["visual_decision"].get("visual_checked") is False
        assert row["visual_decision"].get("reason")


def test_gaming_scene_game_engine_marker_is_not_a_veto():
    """(f) A verdict whose reason is 'video-game simulation footage' does
    NOT veto a gaming scene — but a wrong-game verdict still does."""
    from services.media_resolution_coordinator import _verification_result
    from services.visual_intelligence.semantic_guard import explicit_scene_conflict

    gaming = {"domain_category": "gaming", "text": "Grand Theft Auto VI gameplay"}
    plain = {"text": "A documentary about the 1969 moon landing"}
    verdict = {
        "accepted": False, "status": "rejected", "fatal_mismatch": True,
        "identity_score": 80, "final_score": 80,
        "visual_checked": True, "visual_available": True,
        "reason": "video-game simulation footage of the game world",
    }
    assert explicit_scene_conflict(verdict, relaxed=True, scene=gaming) is False
    assert explicit_scene_conflict(verdict, relaxed=True, scene=plain) is True
    assert explicit_scene_conflict(verdict, relaxed=False, scene=gaming) is False
    accepted, reason, _score, _verdict = _verification_result(
        gaming, {"path": "/tmp/x.mp4", "source": "youtube", "score": 80},
        lambda _s, _c: dict(verdict),
    )
    assert reason != "explicit_scene_conflict"
    accepted, reason, _score, _verdict = _verification_result(
        plain, {"path": "/tmp/x.mp4", "source": "youtube", "score": 80},
        lambda _s, _c: dict(verdict),
    )
    assert reason == "explicit_scene_conflict"
    # Wrong game still vetoes: in the strict lane the game-engine exemption
    # does not apply (the reason names the wrong game), and a live verdict
    # carrying entity_conflict is still a hard rejection.
    wrong_game = dict(verdict, entity_conflict=True,
                      reason="wrong game: shows GTA V footage, wrong entity")
    assert explicit_scene_conflict(wrong_game, relaxed=False, scene=gaming) is True
    accepted, reason, _score, _verdict = _verification_result(
        gaming, {"path": "/tmp/x.mp4", "source": "youtube", "score": 80},
        lambda _s, _c: dict(wrong_game),
    )
    assert accepted is False
    low_game = dict(verdict, fatal_mismatch=False, identity_score=20, final_score=20,
                    reason="video-game simulation footage, low identity match")
    accepted, reason, _score, _verdict = _verification_result(
        gaming, {"path": "/tmp/x.mp4", "source": "youtube", "score": 80},
        lambda _s, _c: dict(low_game),
    )
    assert reason != "insufficient_visible_subject_match"


def test_discovery_clock_is_liveness_aware(tmp_path, monkeypatch):
    """(g) The discovery clock does not advance while a lane reports
    progress, and does advance during a zero-progress window."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    monkeypatch.setenv("VIDZEO_DISCOVERY_STALL_SECONDS", "10")
    clip0 = _png_file(tmp_path, "w0.mp4")
    clip1 = _png_file(tmp_path, "w1.mp4")

    def accept(_s, _c):
        return _accept_verdict("matches")

    def lanes(first_sleep, second_sleep):
        def youtube(_s, _c):
            time.sleep(first_sleep if _s.get("scene_index") == 0 else second_sleep)
            path = clip0 if _s.get("scene_index") == 0 else clip1
            return [_yt_clip(path)]
        return youtube, lambda _s, _c: []

    # Progress case: wave 1 finishes at ~0.8s (progress extends the wall
    # to the 2s absolute cap), wave 2's 1.0s lane still fits.
    yt, web = lanes(0.8, 1.0)
    result = resolve_storyboard_media_v2(
        [_gta_scene(0), _gta_scene(1)],
        youtube_lane=yt, web_lane=web,
        visual_verifier=accept,
        neutral_factory=lambda _s: None,
        deadline_seconds=2,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
    )
    assert result[1].selected_path == clip1

    # Zero-progress case: wave 1 never completes inside the wall, so wave 2
    # never starts and both scenes stay unresolved.
    yt, web = lanes(3.0, 3.0)
    result = resolve_storyboard_media_v2(
        [_gta_scene(0), _gta_scene(1)],
        youtube_lane=yt, web_lane=web,
        visual_verifier=accept,
        neutral_factory=lambda _s: None,
        deadline_seconds=2,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
    )
    assert result[0].selected_path == ""
    assert result[1].selected_path == ""


def test_vision_reserve_sizes_verification_clock_and_shrinks_discovery(monkeypatch):
    """Budget split: an external-agent job gets a >=300s verification clock
    and a discovery wall of total minus reserve."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    scene = _gta_scene(0)
    seen = {}

    def web_lane(s, _c):
        seen["deadline"] = True
        return []

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda _s, _c: [],
        web_lane=web_lane,
        visual_verifier=lambda _s, _c: _reject_verdict(),
        neutral_factory=lambda _s: None,
        deadline_seconds=600,
        vision_reserve_seconds=300,
    )
    assert result[0].selected_path == ""


# ---------------------------------------------------------------------------
# Worker E (gaming mode + fallback chain) regression tests
# ---------------------------------------------------------------------------


def _padded_clip_file(tmp_path, name, size=2048):
    """A fake clip file big enough for the spare-pool size gate (>1024 B)."""
    path = tmp_path / name
    blob = _PNG_1X1 + b"\x00" * (size - len(_PNG_1X1))
    path.write_bytes(blob)
    return str(path)


def _gaming_scene(index=0, container_id=None, requirement_hash=None):
    scene = _gta_scene(index)
    if container_id is not None:
        scene["container_id"] = container_id
    if requirement_hash is not None:
        scene["requirement_hash"] = requirement_hash
    return scene


def _semantic_accept_verdict(reason="frame shows GTA VI gameplay in Vice City"):
    verdict = _accept_verdict(reason)
    verdict["semantic_verified"] = True
    return verdict


def test_gaming_quota_multi_container_reuse_requires_distinct_verified_sources(tmp_path):
    """(b-part 2) 8 scenes in 8 distinct containers, one verified YouTube
    clip for scene 0 only: the clip stays in its own container. One verified
    source cannot cover 8 containers, so full 90% coverage needs >=7 more
    distinct verified sources (the quota math alone does not create them)."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = _padded_clip_file(tmp_path, "trailer2.mp4")
    scenes = [
        _gaming_scene(i, container_id=f"container-{i}", requirement_hash=f"req-{i}")
        for i in range(8)
    ]
    for scene in scenes:
        scene["source_policy"] = {"youtube_quota": True}

    def yt_lane(scene, _cancel):
        if scene["scene_index"] == 0:
            return [_yt_clip(clip, score=90)]
        return []

    calls = []

    def verify(scene, candidate):
        calls.append((scene["scene_index"], str(candidate.get("path") or "")))
        return _semantic_accept_verdict()

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=yt_lane,
        web_lane=lambda _s, _c: [],
        visual_verifier=verify,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
    )
    assert result[0].selected_path == clip
    assert result[0].provider == "youtube"
    # No other scene replays the single verified source.
    for i in range(1, 8):
        assert result[i].selected_path == "", f"scene {i} replayed the one verified clip"
        assert result[i].provider in {"neutral", "graphic"}


def test_pool_recovery_reverifies_recovered_clip_for_this_scene(tmp_path):
    """(c) Scene 1 has no lanes of its own; scene 0's unselected clipB is
    pool-recovered into it. The result must carry a live vision verdict for
    scene 1 (verified_reuse), never a bare deterministic_degraded stamp
    borrowed from another scene's stored verdict."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_a = _padded_clip_file(tmp_path, "clipA.mp4")
    clip_b = _padded_clip_file(tmp_path, "clipB.mp4")
    scenes = [
        _gaming_scene(0, requirement_hash="req-0"),
        _gaming_scene(1, requirement_hash="req-1"),
    ]

    def yt_lane(scene, _cancel):
        if scene["scene_index"] == 0:
            return [_yt_clip(clip_a, score=95, clip_id="yt:clipA"),
                    _yt_clip(clip_b, score=90, clip_id="yt:clipB")]
        return []

    live_calls = []

    def verify(scene, candidate):
        live_calls.append((scene["scene_index"], str(candidate.get("path") or "")))
        return _semantic_accept_verdict()

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=yt_lane,
        web_lane=lambda _s, _c: [],
        visual_verifier=verify,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
    )
    assert result[0].selected_path == clip_a
    assert result[1].selected_path == clip_b
    # The recovered clip was vision-checked FOR scene 1, not inherited.
    assert (1, clip_b) in live_calls
    assert result[1].verification_status == "verified_reuse"
    decision = result[1].visual_decision or {}
    assert decision.get("visual_checked") is True
    assert decision.get("accepted") is True
    assert str(decision.get("reason") or "").strip()
    assert any(
        a.get("status") == "verified_reuse" for a in result[1].attempts
    )


def test_pool_recovery_vision_unavailable_keeps_explicit_reason(tmp_path):
    """(c, variant) With no verifier at all, pool recovery keeps the
    degraded stamp but must say WHY: an explicit vision_unavailable reason,
    never a bare deterministic_degraded with no reason."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_a = _padded_clip_file(tmp_path, "clipA.mp4")
    clip_b = _padded_clip_file(tmp_path, "clipB.mp4")
    scenes = [
        _gaming_scene(0, requirement_hash="req-0"),
        _gaming_scene(1, requirement_hash="req-1"),
    ]

    def yt_lane(scene, _cancel):
        if scene["scene_index"] == 0:
            return [_yt_clip(clip_a, score=95, clip_id="yt:clipA"),
                    _yt_clip(clip_b, score=90, clip_id="yt:clipB")]
        return []

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=yt_lane,
        web_lane=lambda _s, _c: [],
        visual_verifier=None,
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
    )
    assert result[1].selected_path == clip_b
    assert result[1].verification_status == "deterministic_degraded"
    decision = result[1].visual_decision or {}
    assert decision.get("reason") == "vision_unavailable"
    assert any(
        a.get("status") == "vision_unavailable" for a in result[1].attempts
    )


def test_gaming_scene_skips_stock_window_entirely(tmp_path):
    """(d-part 2) A GTA-typed degraded scene never opens the stock window:
    the stock lane is not called for it and the ledger records
    stock_skipped_gaming_topic (policy, not failure). A non-gaming degraded
    scene in the same job still reaches the stock lane."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    gta = _gaming_scene(0, requirement_hash="req-gta")
    gta["source_policy"] = {"youtube_quota": True, "stock_last_resort_allowed": True}
    doc = {
        "scene_id": "doc-1",
        "scene_index": 1,
        "container_id": "doc",
        "text": "The Titanic sank in the Atlantic in 1912",
        "domain_category": "documentary",
        "positive_subjects": ["Titanic"],
        "forbidden_subjects": [],
        "requirement_hash": "req-doc",
        "source_policy": {"stock_last_resort_allowed": True},
    }
    stock_calls = []

    def stock_lane(scene, _cancel):
        stock_calls.append(scene["scene_index"])
        return []

    result = resolve_storyboard_media_v2(
        [gta, doc],
        youtube_lane=lambda _s, _c: [],
        web_lane=lambda _s, _c: [],
        stock_lane=stock_lane,
        visual_verifier=lambda _s, _c: _reject_verdict(),
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
    )
    assert 0 not in stock_calls, "stock lane must never be called for the gaming scene"
    assert 1 in stock_calls, "non-gaming scene must still reach the stock lane"
    assert any(
        a.get("status") == "stock_skipped_gaming_topic" for a in result[0].attempts
    ), "gaming skip must be logged in the ledger"
    assert not any(
        a.get("status") == "stock_skipped_gaming_topic" for a in result[1].attempts
    )


def test_spare_pool_tier_a_runs_once_per_unresolved_scene(monkeypatch):
    """(e) The spare-pool tier (a) block was duplicated (two identical
    blocks); after dedupe it runs exactly once per unresolved scene."""
    import services.media_resolution_coordinator as coordinator
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    real_pick = coordinator._pick_spare_candidate
    calls = []

    def counting_pick(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else None)
        return real_pick(*args, **kwargs)

    monkeypatch.setattr(coordinator, "_pick_spare_candidate", counting_pick)
    result = resolve_storyboard_media_v2(
        [_gaming_scene(0, requirement_hash="req-0")],
        youtube_lane=lambda _s, _c: [],
        web_lane=lambda _s, _c: [],
        visual_verifier=lambda _s, _c: _reject_verdict(),
        neutral_factory=lambda _s: None,
        deadline_seconds=30,
        scene_budget_seconds=30,
        vision_reserve_seconds=0.5,
    )
    assert result[0].selected_path == ""
    assert len(calls) == 1, f"spare tier ran {len(calls)} times, expected exactly 1"


# --- Verdict arbitration (Panama test 5, job 90f39628) ---
#
# Root cause: the coordinator seeds its in-memory fatal ledger ONCE at
# resolve start. Worker-3's fatal REJECT for 39_4MuOX_2U (30,
# fatal_mismatch — large burned-in captions block center-frame, expansion
# story contradicts the scene) was filed to the file ledger
# (_verdict_conflicts.json, record "adapter-fatal:7:39_4MuOX_2U") AFTER
# the seed, while worker-4's ACCEPT (82) had already won first-write-wins
# in the bridge. Nothing re-read the file ledger afterwards, so every
# later decision point kept using the ACCEPT — the clip is in the
# manifest's real_youtube_scenes (scenes 7 and 10, as seg1of2/seg2of2)
# AND in final_real_media_rejected_paths. A fatal REJECT must win over
# an ACCEPT for the same candidate; the tests below fail on clean HEAD
# (the ACCEPT is used) and pass once the coordinator refreshes the fatal
# ledger from the file at arbitration time.
#
# The fixtures below are faithful copies of the real Panama record
# (video id, requirement hash, asset sha, fatal verdict fields) with the
# job id re-scoped to a synthetic test job so the tests never touch the
# live runtime ledger.

_ARBITRATION_JOB_ID = "verdict-arbitration-panama"
_ARBITRATION_VIDEO_ID = "39_4MuOX_2U"
_ARBITRATION_REQUIREMENT_HASH = (
    "8b78419c746855b3e6aa4958e7d7d34851f2075707cee2ba663a05afbf611f10"
)
_ARBITRATION_ASSET_SHA = (
    "e902d61c37d371350ad9b7720a4b865d4d2613728d9f9a834b76c5c0bfc24bb0"
)


def _arbitration_fatal_verdict():
    """Worker-3's verdict on 39_4MuOX_2U, verbatim from the live ledger."""
    return {
        "candidate_id": "youtube:39_4MuOX_2U",
        "entity_conflict": False,
        "identity_score": 70.0,
        "action_score": 40.0,
        "context_score": 45.0,
        "fatal_mismatch": True,
        "domain_match": True,
        "scene_match": False,
        "final_score": 30.0,
        "visual_checked": True,
        "visual_available": True,
        "unsafe_content": False,
        "suggestive_content": False,
        "accepted": False,
        "status": "rejected",
        "reason": (
            "Authentic Panama Canal footage, but every frame carries a large "
            "burned-in caption ('AND THE WORLD NEEDED', 'MORE CAPACITY', "
            "'SO PANAMA BEGAN ANOTHER', 'ENORMOUS ENGINEERING PROJECT', "
            "'THE EXPANSION OF THE CANAL') that blocks the center of frame "
            "and tells the expansion story, contradicting this scene's "
            "narration about the two-hour passage. Frames 4-5 are the "
            "expansion construction, a different event from the transit. "
            "Unusable for this scene."
        ),
    }


def _arbitration_accept_verdict():
    """Worker-4's verdict on 39_4MuOX_2U: ACCEPT (82)."""
    return {
        "candidate_id": "youtube:39_4MuOX_2U",
        "accepted": True,
        "status": "accepted",
        "verification_status": "accepted",
        "visual_checked": True,
        "visual_available": True,
        "semantic_verified": True,
        "identity_score": 85.0,
        "action_score": 80.0,
        "context_score": 82.0,
        "final_score": 82.0,
        "fatal_mismatch": False,
        "entity_conflict": False,
        "reason": (
            "Authentic Panama Canal transit footage; captions are present "
            "across the frame but do not block the lock chambers or the "
            "transiting ship."
        ),
    }


def _arbitration_scene():
    """Panama scene 7 (0-based), the scene the fatal was filed for."""
    return {
        "scene_index": 7,
        "scene_id": "panama-passage",
        "text": "With it, the passage takes two hours.",
        "visual_requirement": {
            "requirement_hash": _ARBITRATION_REQUIREMENT_HASH,
            "domain": "documentary",
            "subject_type": "panama_canal",
        },
    }


def _write_arbitration_fatal_ledger(
    bridge_dir,
    job_id=_ARBITRATION_JOB_ID,
    scene_index=7,
    requirement_hash=_ARBITRATION_REQUIREMENT_HASH,
    video_id=_ARBITRATION_VIDEO_ID,
    verdict=None,
):
    """File one fatal record in a scratch bridge ledger, as the adapter
    does for a late bridge answer (``record_late_fatal_reject``)."""
    import json
    from pathlib import Path

    record = {
        "request_id": f"adapter-fatal:{scene_index}:{video_id}",
        "candidate_id": f"youtube:{video_id}",
        "video_id": video_id,
        "asset_sha256": _ARBITRATION_ASSET_SHA,
        "scene_index": scene_index,
        "requirement_hash": requirement_hash,
        "job_id": job_id,
        "verdict": dict(verdict if verdict is not None else _arbitration_fatal_verdict()),
        "winner": None,
    }
    Path(bridge_dir).mkdir(parents=True, exist_ok=True)
    (Path(bridge_dir) / "_verdict_conflicts.json").write_text(
        json.dumps([record]), encoding="utf-8"
    )
    return record


def test_verdict_arbitration_late_fatal_reject_beats_cached_accept(tmp_path, monkeypatch):
    """Fatal REJECT + cached ACCEPT for one candidate -> candidate NOT used.

    Worker-4's ACCEPT is cached first (bridge ledger still empty, so the
    in-memory fatal ledger is empty, exactly as seeded at resolve start).
    Worker-3's fatal REJECT is then filed to the file ledger. A later
    check of the same candidate must return the fatal veto, not the
    cached ACCEPT, and must not spend a fresh vision read to do it.
    """
    from services.job_context import job_scope
    from services.media_resolution_coordinator import _cached_verification_result

    bridge_dir = tmp_path / "bridge"
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(bridge_dir))
    clip = tmp_path / "39_4MuOX_2U.mp4"
    clip.write_bytes(b"panama clip bytes")
    candidate = {
        "path": str(clip),
        "source": "youtube",
        "candidate_id": "youtube:39_4MuOX_2U",
    }
    scene = _arbitration_scene()
    cache, unavailable = {}, {}
    fatal_rejections, fatal_reject_verdicts = set(), {}
    with job_scope({"job_id": _ARBITRATION_JOB_ID}):
        accepted, _reason, _score, _verdict = _cached_verification_result(
            7, scene, candidate,
            lambda _s, _c: dict(_arbitration_accept_verdict()),
            cache, unavailable,
            fatal_rejections=fatal_rejections,
            fatal_reject_verdicts=fatal_reject_verdicts,
        )
        assert accepted is True
        # Worker-3's fatal REJECT arrives late and is filed to the file ledger.
        _write_arbitration_fatal_ledger(bridge_dir)

        def _must_not_verify(_s, _c):
            raise AssertionError(
                "a late-filed fatal must veto without a fresh vision read"
            )

        accepted, reason, _score, verdict = _cached_verification_result(
            7, scene, candidate, _must_not_verify,
            cache, unavailable,
            fatal_rejections=fatal_rejections,
            fatal_reject_verdicts=fatal_reject_verdicts,
        )
    assert accepted is False
    assert reason == "prior_fatal_mismatch_rejection"
    assert verdict.get("fatal_mismatch") is True


def test_verdict_arbitration_late_fatal_reject_beats_branch_accept(tmp_path, monkeypatch):
    """Fatal REJECT + bound branch ACCEPT for one candidate -> NOT used.

    Same Panama conflict in branch-verdict shape: the candidate row
    carries worker-4's bound ACCEPT (the ``visual_pass`` reuse shortcut),
    while worker-3's fatal REJECT sits in the file ledger, filed after
    the job-start seed. The branch ACCEPT must not win: the fatal vetoes
    before any branch reuse. The first half proves the fixture really
    exercises branch reuse (ACCEPT with no ledger present).
    """
    import hashlib

    from services.job_context import job_scope
    from services.media_resolution_coordinator import _verification_result

    bridge_dir = tmp_path / "bridge"
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(bridge_dir))
    clip = tmp_path / "39_4MuOX_2U.mp4"
    clip.write_bytes(b"panama clip bytes")
    clip_sha = hashlib.sha256(clip.read_bytes()).hexdigest()
    branch = dict(
        _arbitration_accept_verdict(),
        verified_requirement_hash=_ARBITRATION_REQUIREMENT_HASH,
        verified_asset_sha256=clip_sha,
    )
    candidate = {
        "path": str(clip),
        "source": "youtube",
        "candidate_id": "youtube:39_4MuOX_2U",
        "visual_decision": branch,
    }
    scene = _arbitration_scene()

    def _must_not_verify(_s, _c):
        raise AssertionError("branch reuse / fatal veto must not call vision")

    with job_scope({"job_id": _ARBITRATION_JOB_ID}):
        accepted, reason, _score, _verdict = _verification_result(
            scene, candidate, _must_not_verify, scene_index=7,
            fatal_rejections=set(), fatal_reject_verdicts={},
        )
        assert accepted is True
        assert reason == "visual_pass"
        # Worker-3's fatal REJECT is filed late; the in-memory ledger
        # handed in below is still the empty pre-filing seed.
        _write_arbitration_fatal_ledger(bridge_dir)
        accepted, reason, _score, verdict = _verification_result(
            scene, candidate, _must_not_verify, scene_index=7,
            fatal_rejections=set(), fatal_reject_verdicts={},
        )
    assert accepted is False
    assert reason == "prior_fatal_mismatch_rejection"
    assert verdict.get("fatal_mismatch") is True


def test_verdict_arbitration_lone_accept_still_uses_candidate(tmp_path, monkeypatch):
    """Guard against over-blocking: with no fatal anywhere, a lone live
    ACCEPT is still used — through the cache and through branch reuse."""
    import hashlib

    from services.job_context import job_scope
    from services.media_resolution_coordinator import (
        _cached_verification_result,
        _verification_result,
    )

    bridge_dir = tmp_path / "bridge"
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(bridge_dir))
    clip = tmp_path / "39_4MuOX_2U.mp4"
    clip.write_bytes(b"panama clip bytes")
    clip_sha = hashlib.sha256(clip.read_bytes()).hexdigest()
    scene = _arbitration_scene()
    with job_scope({"job_id": _ARBITRATION_JOB_ID}):
        accepted, reason, _score, _verdict = _cached_verification_result(
            7, scene,
            {"path": str(clip), "source": "youtube",
             "candidate_id": "youtube:39_4MuOX_2U"},
            lambda _s, _c: dict(_arbitration_accept_verdict()),
            {}, {}, fatal_rejections=set(), fatal_reject_verdicts={},
        )
        assert accepted is True
        branch = dict(
            _arbitration_accept_verdict(),
            verified_requirement_hash=_ARBITRATION_REQUIREMENT_HASH,
            verified_asset_sha256=clip_sha,
        )
        accepted, reason, _score, _verdict = _verification_result(
            scene,
            {"path": str(clip), "source": "youtube",
             "candidate_id": "youtube:39_4MuOX_2U", "visual_decision": branch},
            lambda _s, _c: (_ for _ in ()).throw(
                AssertionError("branch reuse must not call vision")),
            scene_index=7, fatal_rejections=set(), fatal_reject_verdicts={},
        )
        assert accepted is True
        assert reason == "visual_pass"


def _arbitration_quota_scene(tmp_path, clip_path, still_path):
    """Strict quota scene whose late YouTube cut carries its video id.

    ``_quota_retention_strict_scene``'s late row has no candidate_id; the
    production pool rows do, and the fatal ledger attributes a cut by
    content (video id), so the id is set here to mirror production.
    """
    scene, yt_lane, web_lane = _quota_retention_strict_scene(
        tmp_path, clip_path, still_path
    )
    yt_lane._late_rows[0]["candidate_id"] = "youtube:39_4MuOX_2U"
    yt_lane._late_rows[0]["source_url"] = (
        "https://www.youtube.com/watch?v=39_4MuOX_2U"
    )
    return scene, yt_lane, web_lane


def test_quota_retention_vetoes_clip_with_late_filed_fatal_verdict(tmp_path, monkeypatch):
    """Panama timing, end to end: worker-4's ACCEPT is the live verdict
    the late quota lane records, while worker-3's fatal REJECT for the
    same cut is filed to the file ledger mid-gate (the verifier files it
    as a side effect, exactly as the adapter does for a late bridge
    answer). Quota retention must not mint the clip over the late-filed
    fatal: the scene stays on its web still."""
    from services.agent_bridge import bridge_root
    from services.job_context import job_scope
    from services.media_resolution_coordinator import resolve_storyboard_media_v2
    from services.verdict_conflicts import record_late_fatal_reject

    bridge_dir = tmp_path / "bridge"
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(bridge_dir))
    # The ledger lock opens its lock file without creating the parent
    # directory (production always has a bridge dir); make it so the
    # mid-gate filing below can write.
    bridge_dir.mkdir(parents=True, exist_ok=True)
    clip = tmp_path / "quota-cut.mp4"
    still = tmp_path / "quota-still.jpg"
    scene, yt_lane, web_lane = _arbitration_quota_scene(tmp_path, clip, still)
    filed = []

    def verifier(_scene, candidate):
        if str(candidate.get("source") or "") == "youtube":
            if not filed:
                filed.append(True)
                record_late_fatal_reject(bridge_root(), {
                    "request_id": "adapter-fatal:0:39_4MuOX_2U",
                    "candidate_id": "youtube:39_4MuOX_2U",
                    "video_id": "39_4MuOX_2U",
                    "asset_sha256": _ARBITRATION_ASSET_SHA,
                    "scene_index": 0,
                    "requirement_hash": "quota-strict-requirement-hash",
                    "job_id": _ARBITRATION_JOB_ID,
                    "verdict": _arbitration_fatal_verdict(),
                    "winner": None,
                })
            return dict(_arbitration_accept_verdict())
        return {"accepted": True, "status": "accepted", "visual_checked": True}

    with job_scope({"job_id": _ARBITRATION_JOB_ID, "llm_provider": "external_agent"}):
        result = resolve_storyboard_media_v2(
            [scene],
            youtube_lane=yt_lane,
            web_lane=web_lane,
            visual_verifier=verifier,
            neutral_factory=lambda _s: None,
            deadline_seconds=30.0,
        )
    assert filed == [True]
    assert result[0].provider == "web"
    assert result[0].selected_path == str(still)
    assert not any(
        a.get("stage") == "quota_retention_own_clip_upgrade"
        and a.get("status") == "upgraded_to_scene_youtube_cut"
        for a in result[0].attempts
    )


def test_quota_retention_still_vetoes_preseeded_fatal_reject(tmp_path, monkeypatch):
    """The a735a3f retention veto still holds: a fatal already in the
    file ledger when the job starts (seeded at resolve start) vetoes the
    quota mint even though live vision ACCEPTs the clip. Guards the
    pre-existing veto against the refresh change below."""
    from services.job_context import job_scope
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    bridge_dir = tmp_path / "bridge"
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(bridge_dir))
    clip = tmp_path / "quota-cut.mp4"
    still = tmp_path / "quota-still.jpg"
    scene, yt_lane, web_lane = _arbitration_quota_scene(tmp_path, clip, still)
    _write_arbitration_fatal_ledger(
        bridge_dir,
        scene_index=0,
        requirement_hash="quota-strict-requirement-hash",
    )

    def verifier(_scene, candidate):
        if str(candidate.get("source") or "") == "youtube":
            return dict(_arbitration_accept_verdict())
        return {"accepted": True, "status": "accepted", "visual_checked": True}

    with job_scope({"job_id": _ARBITRATION_JOB_ID, "llm_provider": "external_agent"}):
        result = resolve_storyboard_media_v2(
            [scene],
            youtube_lane=yt_lane,
            web_lane=web_lane,
            visual_verifier=verifier,
            neutral_factory=lambda _s: None,
            deadline_seconds=30.0,
        )
    assert result[0].provider == "web"
    assert result[0].selected_path == str(still)
    assert not any(
        a.get("stage") == "quota_retention_own_clip_upgrade"
        and a.get("status") == "upgraded_to_scene_youtube_cut"
        for a in result[0].attempts
    )


def _arbitration_nonfatal_reject_verdict():
    """Worker-2's verdict on GRDGQERSmDE (Panama scene 9): REJECT (32,
    wrong aspect/era), NOT fatal — the non-fatal half of the conflict."""
    return {
        "candidate_id": "youtube:GRDGQERSmDE",
        "accepted": False,
        "status": "rejected",
        "visual_checked": True,
        "visual_available": True,
        "fatal_mismatch": False,
        "entity_conflict": False,
        "identity_score": 40.0,
        "action_score": 30.0,
        "context_score": 35.0,
        "final_score": 32.0,
        "reason": (
            "Wrong aspect and era for the recognitions scene: vertical "
            "archival footage of an earlier ceremony, not the canal "
            "handover the scene narrates."
        ),
    }


def test_verdict_arbitration_live_branch_reject_beats_later_live_accept(tmp_path, monkeypatch):
    """Rule for non-fatal conflicts: a live vision REJECT for a candidate
    wins over a later live ACCEPT for the SAME cut (same scene, same
    bytes). The candidate row carries worker-2's live REJECT for
    GRDGQERSmDE (32, wrong aspect/era, not fatal); a fresh live read
    ACCEPTs the same cut. The REJECT stands and no fresh vision read is
    spent: the candidate is not used. A different cut of the same video
    is judged on its own — only the same cut is vetoed."""
    from services.job_context import job_scope
    from services.media_resolution_coordinator import _verification_result

    bridge_dir = tmp_path / "bridge"
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(bridge_dir))
    clip = tmp_path / "GRDGQERSmDE.mp4"
    clip.write_bytes(b"recognitions clip bytes")
    scene = {
        "scene_index": 9,
        "scene_id": "panama-recognitions",
        "text": "The canal recognitions ceremony honors the workers.",
        "visual_requirement": {
            "requirement_hash": "grdgqersmde-scene-9-hash",
            "domain": "documentary",
            "subject_type": "panama_canal",
        },
    }
    candidate = {
        "path": str(clip),
        "source": "youtube",
        "candidate_id": "youtube:GRDGQERSmDE",
        "visual_decision": _arbitration_nonfatal_reject_verdict(),
    }

    def _accepting_verifier(_s, _c):
        return {
            "candidate_id": "youtube:GRDGQERSmDE",
            "accepted": True,
            "status": "accepted",
            "visual_checked": True,
            "visual_available": True,
            "semantic_verified": True,
            "final_score": 82.0,
            "fatal_mismatch": False,
            "entity_conflict": False,
            "reason": "Authentic handover ceremony footage.",
        }

    with job_scope({"job_id": _ARBITRATION_JOB_ID}):
        accepted, _reason, _score, verdict = _verification_result(
            scene, candidate, _accepting_verifier, scene_index=9,
            fatal_rejections=set(), fatal_reject_verdicts={},
        )
    assert accepted is False
    assert verdict.get("final_score") == 32.0
    assert verdict.get("fatal_mismatch") is False


class _LateWebLane:
    """Shared-batch web lane whose loader finishes after the wall.

    Mirrors ``LazyBatchLane``'s protocol: rows appear atomically when
    the loader returns, never incrementally. ``__call__`` waits for its
    scene's row or the cancel event; ``get_row`` waits up to ``timeout``
    for the batch to become ready.
    """

    shared_batch = True

    def __init__(self, rows, delay):
        self._rows_to_publish = dict(rows)
        self._delay = delay
        self._rows = {}
        self._ready = threading.Event()
        self._started = False
        self._lock = threading.Lock()

    def __call__(self, scene, cancel_event):
        with self._lock:
            if not self._started:
                self._started = True
                threading.Thread(target=self._load, daemon=True).start()
        index = int(scene.get("scene_index", 0))
        while index not in self._rows and not cancel_event.is_set():
            time.sleep(0.01)
        return self._rows.get(index)

    def _load(self):
        time.sleep(self._delay)
        self._rows.update(self._rows_to_publish)
        self._ready.set()

    def get_row(self, index, timeout=0.0):
        self._ready.wait(timeout=timeout)
        return self._rows.get(index)


def _late_web_scene(requirement_hash):
    return {
        "scene_id": "late-web-scene",
        "scene_index": 0,
        "container_id": "late-web-container",
        "text": "Grand Theft Auto VI official artwork scene",
        "visual_requirement": {"requirement_hash": requirement_hash},
        "source_policy": {"mixed_media": True},
    }


def _late_web_row(image_path, sha, requirement_hash, *, scene_index=0,
                  verification_status=None, visual_checked=True):
    """A web row in the shape the real lane returns for a live verdict.

    The verdict's ``verified_requirement_hash`` is the resolver's own
    scene_context_hash — never equal to the pipeline requirement_hash;
    the row is bound to the scene through
    ``candidate_evidence.requirement_hash`` instead.
    """
    verdict = {
        "accepted": True,
        "status": "accepted",
        "visual_checked": visual_checked,
        "semantic_verified": visual_checked,
        "visual_available": visual_checked,
        "relevance": 92.0,
        "final_score": 92.0,
        "scene_index": scene_index,
        "verified_requirement_hash": "resolver-scene-context-hash",
        "verified_asset_sha256": sha,
    }
    if verification_status is not None:
        verdict["verification_status"] = verification_status
    return {
        "path": str(image_path),
        "source": "web",
        "provider": "web",
        "media_type": "image",
        "candidate_id": "web:late-still",
        "title": "Grand Theft Auto VI official artwork",
        "final_score": 92.0,
        "qwen_result": dict(verdict),
        "visual_decision": dict(verdict),
        "candidate_evidence": {
            "requirement_hash": requirement_hash,
            "identity_proof": "Grand Theft Auto VI",
        },
    }


def _never_called_verifier(_scene, _candidate):
    raise AssertionError("late web promotion must not spend a live vision call")


def test_late_web_verdict_joins_plan_after_discovery_wall(tmp_path, monkeypatch):
    """Regression: test 26 (job ffe90224) — the shared web batch returned
    at 754.68s, after the coordinator's ~715s discovery wall and the
    722.7s plan lock, with 13 resolved scenes; the plan shipped 0 web
    and 7 graphics because every consumption point is non-blocking and
    the web loader never publishes rows incrementally.

    The batch here finishes 1s after the discovery wall but inside the
    bounded late wait; its row carries a paid live verdict. The image
    must be selected, with no live vision call spent.
    """
    import hashlib

    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"late-web-image-bytes")
    sha = hashlib.sha256(b"late-web-image-bytes").hexdigest()
    requirement_hash = "late-web-scene-hash-001"
    row = _late_web_row(image, sha, requirement_hash)
    web_lane = _LateWebLane({0: [row]}, delay=3.0)

    started = time.monotonic()
    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "30")
    result = resolve_storyboard_media_v2(
        [_late_web_scene(requirement_hash)],
        youtube_lane=lambda *_args: [],
        web_lane=web_lane,
        visual_verifier=_never_called_verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=2.0,
        verification_deadline_seconds=1.0,
    )
    elapsed = time.monotonic() - started
    assert result[0].provider == "web"
    assert result[0].selected_path == str(image)
    assert result[0].verification_status == "verified"
    assert any(
        a.get("stage") == "late_web_promotion"
        and a.get("status") == "branch_verdict_accepted"
        for a in result[0].attempts
    )
    # Bounded: the batch landed ~2s after the wall; the run must not
    # have waited out the whole late-wait budget.
    assert elapsed < 15


def test_late_web_verdict_never_arrives_falls_back_bounded(tmp_path, monkeypatch, capsys):
    """The late wait must not stall the job: a web batch whose verdicts
    never arrive still falls back to graphic/neutral, and the wait ends
    at the bounded cap instead of hanging.
    """
    import hashlib

    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"late-web-image-bytes")
    sha = hashlib.sha256(b"late-web-image-bytes").hexdigest()
    requirement_hash = "late-web-scene-hash-002"
    row = _late_web_row(image, sha, requirement_hash)
    web_lane = _LateWebLane({0: [row]}, delay=3600.0)

    started = time.monotonic()
    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "3")
    result = resolve_storyboard_media_v2(
        [_late_web_scene(requirement_hash)],
        youtube_lane=lambda *_args: [],
        web_lane=web_lane,
        visual_verifier=_never_called_verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=2.0,
        verification_deadline_seconds=1.0,
    )
    elapsed = time.monotonic() - started
    assert result[0].provider in {"graphic", "neutral"}
    assert result[0].selected_path != str(image)
    # The wait ran (it is what separates this run from clean HEAD) and
    # it ended at the cap: ~2s wall + ~3s wait, never an open-ended hang.
    assert "late web promotion" in capsys.readouterr().out
    assert elapsed < 15


def test_late_web_verdict_for_another_scene_is_not_seeded(tmp_path, monkeypatch):
    """A late verdict created for a DIFFERENT scene (shared cache image
    reused across scenes, as in test 26 scenes 3/8) must not be seeded
    into the primary selection: the row is promoted into the ledger,
    but no ``branch_verdict_accepted`` outcome is minted for it.

    The final provider is deliberately NOT asserted here: the pre-stock
    rescue tier below the primary selection trusts any accepted verdict
    on its own looser rule and may still admit the image as a degraded
    rescue. The seeding tier is the strict one — a primary "verified"
    stamp must mean vision passed for THIS scene.
    """
    import hashlib

    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"late-web-image-bytes")
    sha = hashlib.sha256(b"late-web-image-bytes").hexdigest()
    requirement_hash = "late-web-scene-hash-003"
    # Evidence binds the ROW to this scene; the VERDICT was created for
    # scene 5, so only the scene-index half refuses it.
    row = _late_web_row(image, sha, requirement_hash, scene_index=5)
    web_lane = _LateWebLane({0: [row]}, delay=3.0)

    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "30")
    result = resolve_storyboard_media_v2(
        [_late_web_scene(requirement_hash)],
        youtube_lane=lambda *_args: [],
        web_lane=web_lane,
        visual_verifier=_never_called_verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=2.0,
        verification_deadline_seconds=1.0,
    )
    # The row WAS promoted (it is in the candidate ledger) — the refusal
    # is the scene binding, not a missed batch — but the primary
    # selection never seeded its verdict.
    assert any(
        str(ledger_row.get("path") or "") == str(image)
        for ledger_row in (result[0].candidate_ledger or [])
    )
    assert not any(
        a.get("stage") == "late_web_promotion"
        and a.get("status") == "branch_verdict_accepted"
        for a in result[0].attempts
    )


def test_late_web_deterministic_stamp_is_not_seeded(tmp_path, monkeypatch):
    """The adapter's synthesized ``deterministic_web_image`` stamp proves
    only that the file decoded — it is not a live verdict and must not
    be seeded as one. Same tier note as the cross-scene test above:
    the rescue tier below may still admit the image on its own looser
    rule; the assertion here is that the primary selection never seeds
    the stamp.
    """
    import hashlib

    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"late-web-image-bytes")
    sha = hashlib.sha256(b"late-web-image-bytes").hexdigest()
    requirement_hash = "late-web-scene-hash-004"
    row = _late_web_row(
        image, sha, requirement_hash,
        verification_status="deterministic_web_image",
        visual_checked=False,
    )
    web_lane = _LateWebLane({0: [row]}, delay=3.0)

    monkeypatch.setenv("VIDZEO_WEB_LATE_WAIT_SECONDS", "30")
    result = resolve_storyboard_media_v2(
        [_late_web_scene(requirement_hash)],
        youtube_lane=lambda *_args: [],
        web_lane=web_lane,
        visual_verifier=_never_called_verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=2.0,
        verification_deadline_seconds=1.0,
    )
    assert any(
        str(ledger_row.get("path") or "") == str(image)
        for ledger_row in (result[0].candidate_ledger or [])
    )
    assert not any(
        a.get("stage") == "late_web_promotion"
        and a.get("status") == "branch_verdict_accepted"
        for a in result[0].attempts
    )


def test_late_web_branch_accept_rules(tmp_path):
    """Unit tests for the seeding rule: a paid live verdict bound to
    this scene and these bytes is accepted; anything weaker is refused.
    """
    import hashlib

    from services.media_resolution_coordinator import _late_web_branch_accept

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"late-web-image-bytes")
    sha = hashlib.sha256(b"late-web-image-bytes").hexdigest()
    requirement_hash = "late-web-scene-hash-005"
    scene = _late_web_scene(requirement_hash)

    bound = _late_web_row(image, sha, requirement_hash)
    assert _late_web_branch_accept(scene, bound) is not None
    assert _late_web_branch_accept(scene, bound)[0] is True

    # Verdict created for another scene.
    other_scene = _late_web_row(image, sha, requirement_hash, scene_index=5)
    assert _late_web_branch_accept(scene, other_scene) is None

    # Row the resolver did not choose for this scene.
    other_evidence = _late_web_row(image, sha, "some-other-scene-hash")
    assert _late_web_branch_accept(scene, other_evidence) is None

    # Deterministic stamp: no live vision evidence.
    deterministic = _late_web_row(
        image, sha, requirement_hash,
        verification_status="deterministic_web_image",
        visual_checked=False,
    )
    assert _late_web_branch_accept(scene, deterministic) is None

    # No verdict at all.
    no_verdict = _late_web_row(image, sha, requirement_hash)
    no_verdict.pop("qwen_result")
    no_verdict.pop("visual_decision")
    assert _late_web_branch_accept(scene, no_verdict) is None

    # Verdict for different bytes.
    wrong_bytes = _late_web_row(image, "0" * 64, requirement_hash)
    assert _late_web_branch_accept(scene, wrong_bytes) is None

    # Live rejection / veto flags.
    rejected = _late_web_row(image, sha, requirement_hash)
    rejected["qwen_result"] = dict(rejected["qwen_result"], accepted=False, status="rejected")
    rejected["visual_decision"] = dict(rejected["qwen_result"])
    assert _late_web_branch_accept(scene, rejected) is None
    conflicted = _late_web_row(image, sha, requirement_hash)
    conflicted["qwen_result"] = dict(conflicted["qwen_result"], entity_conflict=True)
    conflicted["visual_decision"] = dict(conflicted["qwen_result"])
    assert _late_web_branch_accept(scene, conflicted) is None

