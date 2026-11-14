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
