import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import mixed_media_planner
from services import scene_splitter
from services import audio_first_pipeline
from services import transcription_service
from services import web_image_service


class AudioFirstMediaSyncTests(unittest.TestCase):
    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_whisper_policy_uses_tiered_cpu_scaling(self):
        self.assertEqual(
            transcription_service.get_whisper_parallel_policy(chunk_count=16, cpu_count=12)["worker_count"],
            4,
        )
        self.assertEqual(
            transcription_service.get_whisper_parallel_policy(chunk_count=16, cpu_count=6)["worker_count"],
            3,
        )
        self.assertEqual(
            transcription_service.get_whisper_parallel_policy(chunk_count=16, cpu_count=2)["worker_count"],
            3,
        )

    def test_whisper_policy_caps_to_chunk_count(self):
        policy = transcription_service.get_whisper_parallel_policy(chunk_count=3, cpu_count=16)
        self.assertEqual(policy["worker_count"], 3)
        self.assertEqual(policy["cpu_tier_workers"], 4)

    def test_whisper_cold_load_wait_is_bounded_and_shared(self):
        started = threading.Event()
        release = threading.Event()

        def load(_device):
            with transcription_service._whisper_init_lock:
                started.set()
                return release.wait(2)

        with mock.patch.object(transcription_service, "_available", False), \
                mock.patch.object(transcription_service, "_whisper_start", None), \
                mock.patch.object(transcription_service, "init_whisper", side_effect=load) as init:
            try:
                start = time.monotonic()
                self.assertFalse(transcription_service._init_whisper_with_timeout(timeout=0.05))
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertTrue(started.is_set())
                self.assertFalse(transcription_service._init_whisper_with_timeout(timeout=0.05))
                cleanup_started = time.monotonic()
                self.assertFalse(transcription_service.unload_whisper_model())
                self.assertLess(time.monotonic() - cleanup_started, 0.5)
                init.assert_called_once()
            finally:
                release.set()
                transcription_service._whisper_start.result(timeout=2)

    def test_low_memory_prewarm_does_not_start_duplicate_models(self):
        with mock.patch.object(transcription_service, "get_whisper_parallel_policy", return_value={
            "worker_count": 1, "threads_per_worker": 2
        }), mock.patch.object(transcription_service, "_init_whisper_with_timeout", return_value=False), \
                mock.patch.object(transcription_service, "_load_worker_model") as worker:
            handle = transcription_service.start_whisper_job_warmup(worker_count=8)
            self.assertTrue(handle["event"].wait(2))
            self.assertEqual(handle["state"]["target_workers"], 0)
            self.assertFalse(handle["state"]["base_model_ready"])
            worker.assert_not_called()

    def test_whisper_prewarm_sync_defaults_to_immediate_dispatch(self):
        self.assertEqual(transcription_service.WHISPER_PREWARM_SYNC_SECONDS, 0.0)

    def test_whisper_model_defaults_to_cached_local_model(self):
        self.assertIn(transcription_service.WHISPER_MODEL_NAME, {"tiny.en", "base"})

    @mock.patch("services.edge_tts_service.generate_audio", return_value=("audio.mp3", 1.0))
    def test_tts_always_uses_audiogram_backend(self, mock_generate):
        result = audio_first_pipeline._run_tts(
            "A short narration script.",
            "Brian (Male US)",
            vo_mode="Use Previous VO",
            tts_model="legacy-local",
        )

        self.assertEqual(result, ("audio.mp3", 1.0))
        mock_generate.assert_called_once()

    def test_whisper_chunk_plan_scales_up_to_target_workers(self):
        plan = transcription_service._build_chunk_plan(100.9, target_chunks=8)
        self.assertEqual(len(plan), 8)
        self.assertGreater(plan[0]["duration"], 10.0)

    def test_fallback_scene_split_preserves_short_sentences_and_all_narration(self):
        script = "Alpha, beta, gamma. Delta: epsilon - zeta; eta, theta."
        scenes = scene_splitter.fallback_split_by_text(script, "Every Beat", 12.0)
        self.assertEqual(len(scenes), 2)
        import re
        self.assertEqual(re.findall(r'\w+', ' '.join(scene.text for scene in scenes)),
                         re.findall(r'\w+', script))
        self.assertAlmostEqual(sum(scene.duration for scene in scenes), 12.0, places=2)

    def test_mixed_media_rounding_respects_final_scene_count(self):
        scenes = [f"Scene {idx}" for idx in range(7)]
        plan = mixed_media_planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 50, "Stock Media": 30, "AI Images": 20},
            title="Video",
            script="Script",
        )
        self.assertEqual(plan.final_counts["Web Images"], 4)
        self.assertEqual(plan.final_counts["Stock Media"], 2)
        self.assertEqual(plan.final_counts["AI Images"], 1)
        self.assertEqual(sum(plan.final_counts.values()), 7)

    @mock.patch("services.web_image.resolver._resolve_scene")
    @mock.patch("services.web_image.query.generate_web_image_queries")
    def test_web_resolver_returns_audit_and_freshness_mode(self, mock_queries, mock_resolve):
        mock_queries.return_value = [
            {
                "search_query": "test query",
                "supporting_search_query": "",
                "alt_search_query": "",
                "scene_family": "theme",
                "topic_tag": "general",
                "topic_anchor": "Video",
                "subject_query": "subject",
            }
        ]
        mock_resolve.return_value = {
            "path": r"C:\temp\scene_1.jpg",
            "width": 1280,
            "height": 720,
            "url": "https://example.com/photo.jpg",
            "domain": "example.com",
            "reused_from_scene": False,
            "query_used": "test query",
            "title": "test title",
            "source_url": "https://example.com/photo",
            "pre_score": 5.0,
            "quality_score": 4.0,
            "final_score": 9.0,
            "identity_failed": False,
        }

        result = web_image_service.resolve_web_images_for_video_fast(
            script="Scene 1",
            title="Video",
            scene_texts=["Scene 1"],
            scene_durations=[3.0],
            api_key="test-key",
            gallery_cmd=["gallery-dl"],
        )

        self.assertEqual(result["audit"]["freshness_mode"], "streaming-live-search")
        self.assertEqual(result["audit"]["scene_count"], 1)
        self.assertEqual(result["image_paths"], [r"C:\temp\scene_1.jpg"])
        self.assertTrue(result["audit"]["gallery_enabled"])

    @mock.patch("services.web_image.resolver.resolve_gallery_dl", return_value=None)
    @mock.patch("services.web_image.resolver._resolve_scene")
    @mock.patch("services.web_image.query.generate_web_image_queries")
    def test_web_resolver_keeps_running_without_gallery_dl(
        self,
        mock_queries,
        mock_resolve,
        _mock_gallery,
    ):
        mock_queries.return_value = [
            {
                "search_query": "test query",
                "supporting_search_query": "",
                "alt_search_query": "",
                "scene_family": "theme",
                "topic_tag": "general",
                "topic_anchor": "Video",
                "subject_query": "subject",
            }
        ]
        mock_resolve.return_value = {
            "path": r"C:\temp\scene_1.jpg",
            "width": 1280,
            "height": 720,
            "url": "https://example.com/photo.jpg",
            "domain": "example.com",
            "reused_from_scene": False,
            "query_used": "test query",
            "title": "test title",
            "source_url": "https://example.com/photo",
            "pre_score": 5.0,
            "quality_score": 4.0,
            "final_score": 9.0,
            "identity_failed": False,
        }

        result = web_image_service.resolve_web_images_for_video_fast(
            script="Scene 1",
            title="Video",
            scene_texts=["Scene 1"],
            scene_durations=[3.0],
            api_key="",
            gallery_cmd=None,
        )

        self.assertFalse(result["audit"]["gallery_enabled"])
        self.assertEqual(result["image_paths"], [r"C:\temp\scene_1.jpg"])

    @mock.patch("services.transcription_service.transcribe_audio", return_value=([{"word": "alpha"}], []))
    @mock.patch("services.transcription_service.is_available", return_value=True)
    def test_run_whisper_reuses_job_warmup_handle(self, _mock_available, mock_transcribe_audio):
        warmup_handle = {"event": mock.Mock(), "state": {"base_model_ready": True, "ready_workers": 8, "target_workers": 8}}

        words = audio_first_pipeline._run_whisper(
            "C:\\temp\\audio.wav",
            whisper_mode="local",
            warmup_handle=warmup_handle,
        )

        self.assertEqual(words, [{"word": "alpha"}])
        mock_transcribe_audio.assert_called_once_with(
            "C:\\temp\\audio.wav",
            warmup_handle=warmup_handle,
            language=None,
        )

    @mock.patch("services.transcription_service.transcribe_audio_parallel", return_value=([{"word": "beta"}], []))
    @mock.patch("services.transcription_service._init_whisper_with_timeout", return_value=True)
    @mock.patch("services.transcription_service.describe_whisper_job_warmup")
    def test_transcribe_audio_starts_without_waiting_for_warmup(self, mock_describe, mock_init_whisper, mock_parallel):
        warmup_event = mock.Mock()
        mock_describe.return_value = {
            "base_model_ready": False,
            "ready_workers": 0,
            "target_workers": 4,
            "error": "",
        }

        with mock.patch.object(transcription_service, "_available", False):
            words, segments = transcription_service.transcribe_audio(
                "C:\\temp\\audio.wav",
                warmup_handle={"event": warmup_event},
            )

        self.assertEqual(words, [{"word": "beta"}])
        self.assertEqual(segments, [])
        warmup_event.wait.assert_not_called()
        mock_init_whisper.assert_called_once()
        mock_parallel.assert_called_once()

    def test_youtube_snapshot_survives_retrieval_workspace_cleanup(self):
        from app import _snapshot_media_for_render

        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "clip.mp4")
            with open(source, "wb") as handle:
                handle.write(b"0" * 4096)

            snapshots = _snapshot_media_for_render([source, source], namespace="test")
            os.remove(source)

            self.assertEqual(len(snapshots), 2)
            self.assertEqual(snapshots[0], snapshots[1])
            self.assertTrue(os.path.isfile(snapshots[0]))
            self.assertGreater(os.path.getsize(snapshots[0]), 64)

    def test_youtube_timeout_salvage_keeps_verified_cut_audit(self):
        import app

        result = app._build_youtube_salvage_result(
            [None, "C:\\temp\\scene_0002.clean.mp4"],
            [4.0, 5.0],
            requested_count=2,
        )

        self.assertEqual(result[0], None)
        self.assertEqual(result[1], "C:\\temp\\scene_0002.clean.mp4")
        self.assertEqual(result.audit["resolved"], 1)
        self.assertEqual(result.audit["cut_audit"][0]["scene_index"], 1)
        self.assertEqual(result.audit["cut_audit"][0]["status"], "accepted")
        decision = result.audit["retrieval_visual_decisions"]["1"]
        self.assertEqual(decision["status"], "visual_verification_pending")
        self.assertFalse(decision["accepted"])
        self.assertFalse(decision["semantic_verified"])
        self.assertFalse(result.audit["cut_audit"][0]["visual_checked"])

    def test_youtube_single_cut_is_not_marked_verified_for_unrelated_scene_holes(self):
        import app

        with tempfile.TemporaryDirectory() as temp_dir:
            first = os.path.join(temp_dir, "first.jpg")
            second = os.path.join(temp_dir, "second.jpg")
            for path in (first, second):
                with open(path, "wb") as handle:
                    handle.write(b"0" * 4096)

            result = app._fill_youtube_verified_reuse(
                [first, None, second, None],
                {"resolved": 2},
            )

        self.assertEqual(list(result), [first, None, second, None])
        self.assertEqual(
            result.audit["same_cut_reuse_skipped"]["reason"],
            "requires_distinct_distant_scene_verified_downloaded_cut",
        )

    def test_youtube_branch_does_not_wait_for_parallel_llm_queries(self):
        import app
        from services import youtube_clips_service as ycs

        def slow_llm(*_args, **_kwargs):
            time.sleep(0.6)
            return []

        mechanical = [{
            "youtube_query": "John Cena training footage",
            "youtube_supporting_query": "John Cena WWE footage",
            "youtube_fallback_query": "John Cena archive footage",
        }]
        captured = {}

        def fake_get_youtube_clips(**kwargs):
            captured.update(kwargs)
            return ["C:\\temp\\clip.mp4"]

        with mock.patch.object(app, "_resolve_topic_anchor_for_config", return_value="John Cena"), \
             mock.patch.object(app, "_generate_fast_scene_queries", side_effect=slow_llm), \
             mock.patch.object(app, "_repair_concept_topic_query_rows", side_effect=lambda rows, *_args: rows), \
             mock.patch.object(ycs, "_generate_queries_mechanical", return_value=mechanical), \
             mock.patch.object(ycs, "get_youtube_clips", side_effect=fake_get_youtube_clips):
            started = time.time()
            result = app._run_youtube_clips_branch(
                ["John Cena trained for wrestling."],
                [4.0],
                {},
                "John Cena",
                "John Cena trained for wrestling.",
            )
            elapsed = time.time() - started

        self.assertEqual(result, ["C:\\temp\\clip.mp4"])
        self.assertLess(elapsed, 0.4)
        self.assertIsNotNone(captured.get("scene_query_future"))


if __name__ == "__main__":
    unittest.main()
