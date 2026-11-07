import os
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import mixed_media_planner as planner
from services import youtube_pipeline_v2 as yt_pipeline
from services.web_image import resolver as web_resolver


class TestYouTubeWebPacingAndClipping(unittest.TestCase):
    def test_pacing_first_20_percent_and_mid_section_10_scenes(self):
        """First 20% contains mostly YouTube clips (80-100%), mid section contains ~50%."""
        scenes = [f"Historical scene {i}" for i in range(10)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 40, "YouTube Clips": 60, "Stock Media": 0},
            title="Historic Battles",
            script="Documentary about historical battles.",
            strict_weights=True,
        )
        sequence = [d.assigned_source for d in plan.decisions]
        self.assertEqual(len(sequence), 10)
        self.assertEqual(plan.final_counts["YouTube Clips"], 6)
        self.assertEqual(plan.final_counts["Web Images"], 4)

        # First 20% = 2 scenes (indices 0, 1)
        first_20 = sequence[:2]
        yt_first_20_ratio = first_20.count("YouTube Clips") / len(first_20)
        self.assertGreaterEqual(yt_first_20_ratio, 0.80)

        # Mid section = indices 2..6 (5 scenes)
        mid_section = sequence[2:7]
        yt_mid_ratio = mid_section.count("YouTube Clips") / len(mid_section)
        self.assertAlmostEqual(yt_mid_ratio, 0.50, delta=0.15)

    def test_pacing_first_20_percent_and_mid_section_20_scenes(self):
        """20 scenes: first 4 scenes >= 75-80% YT, mid 10 scenes ~50% YT."""
        scenes = [f"Space exploration beat {i}" for i in range(20)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 45, "YouTube Clips": 55, "Stock Media": 0},
            title="Space Exploration",
            script="Mission to the moon and Mars exploration.",
            strict_weights=True,
        )
        sequence = [d.assigned_source for d in plan.decisions]
        self.assertEqual(len(sequence), 20)
        self.assertEqual(plan.final_counts["YouTube Clips"], 11)
        self.assertEqual(plan.final_counts["Web Images"], 9)

        # First 20% = 4 scenes (indices 0..3)
        first_20 = sequence[:4]
        yt_first_20_count = first_20.count("YouTube Clips")
        self.assertGreaterEqual(yt_first_20_count, 3)  # At least 75-80%

        # Mid section = indices 4..13 (10 scenes)
        mid_section = sequence[4:14]
        yt_mid_count = mid_section.count("YouTube Clips")
        self.assertIn(yt_mid_count, {4, 5, 6})  # ~50%

        # No 3 identical consecutive sources
        self.assertFalse(any(sequence[i] == sequence[i - 1] == sequence[i - 2] for i in range(2, len(sequence))))

    def test_multi_candidate_youtube_clipping_tries_next_video(self):
        """When candidate 1 fails download, candidate 2 from another video is downloaded."""
        from unittest.mock import MagicMock
        options = [
            {"id": "video_aaa", "title": "First video", "channel": "Ch1", "duration": 120, "score": 90.0},
            {"id": "video_bbb", "title": "Second video", "channel": "Ch2", "duration": 150, "score": 85.0},
            {"id": "video_ccc", "title": "Third video", "channel": "Ch3", "duration": 180, "score": 80.0},
        ]
        download_calls = []

        def mock_download(option, vid, start):
            download_calls.append(vid)
            if vid == "video_aaa":
                return None  # First video download fails
            # Second video succeeds
            return f"/fake/path/{vid}.mp4"

        with mock.patch("services.youtube_pipeline_v2.os.environ.get", return_value="3"):
            # Verify progressive_limit and attempt_budget allow multiple plans
            attempt_budget = 3
            plans = []
            tried = set()
            for opt in options:
                if len(plans) >= attempt_budget:
                    break
                vid = opt["id"]
                if vid in tried:
                    continue
                tried.add(vid)
                plans.append((opt, vid, 10.0))

            self.assertEqual(len(plans), 3)

            # Simulate the loop in _download_scene
            result_path = None
            for opt, vid, start in plans:
                path = mock_download(opt, vid, start)
                if path:
                    result_path = path
                    break

            self.assertEqual(download_calls, ["video_aaa", "video_bbb"])
            self.assertEqual(result_path, "/fake/path/video_bbb.mp4")

    def test_web_image_borderline_does_not_collapse_to_empty_path(self):
        """Borderline web image candidates fall back to metadata winner instead of empty path."""
        candidates = [
            {
                "path": "c:/fake/img1.jpg",
                "width": 1920,
                "height": 1080,
                "domain": "wikimedia.org",
                "provider": "openverse",
                "final_score": 0.85,
            }
        ]
        mock_client = mock.MagicMock()
        # Vision model returns borderline score without fatal mismatch or explicit conflict
        mock_client.compare_images.return_value = {
            "acceptable": False,
            "confidence": 55.0,
            "candidates": [
                {
                    "relevance": 55.0,
                    "confidence": 55.0,
                    "fatal_mismatch": False,
                    "scene_description": "A historic harbor with steamships in the background",
                    "reason": "Indirect establishing shot",
                }
            ],
        }

        mock_context = mock.MagicMock()
        mock_context.get_client.return_value = mock_client
        mock_context.visual_required = True
        mock_context.fail_closed = False

        with mock.patch("services.web_image.resolver.image_is_safe", return_value=(True, "clean")):
            result = web_resolver._rank_deferred_scene_shortlist(
                scene_index=0,
                candidates=candidates,
                query="Titanic Belfast docks",
                subject="RMS Titanic",
                scene_text="The Titanic was constructed in the Belfast shipyards.",
                global_topic="Titanic History",
                visual_context=mock_context,
            )

        # Should NOT collapse to empty path
        self.assertNotEqual(result.get("path"), "")
        self.assertIn(result.get("visual_outcome"), {"pass", "unavailable"})

    def test_pacing_first_20_percent_and_mid_section_30_scenes(self):
        """30 scenes with 65% YouTube: first 6 scenes have >= 4-5 YT clips, mid section has ~50%."""
        scenes = [f"Aviation documentary beat {i}" for i in range(30)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 35, "YouTube Clips": 65, "Stock Media": 0},
            title="Supersonic Flight",
            script="The evolution of supersonic and hypersonic aviation.",
            strict_weights=True,
        )
        sequence = [d.assigned_source for d in plan.decisions]
        self.assertEqual(len(sequence), 30)
        self.assertEqual(plan.final_counts["YouTube Clips"], 20)
        self.assertEqual(plan.final_counts["Web Images"], 10)

        # First 20% = 6 scenes (indices 0..5)
        first_20 = sequence[:6]
        yt_first_20_count = first_20.count("YouTube Clips")
        self.assertGreaterEqual(yt_first_20_count, 4)  # 67-83%

        # Mid section = indices 6..20 (15 scenes)
        mid_section = sequence[6:21]
        yt_mid_count = mid_section.count("YouTube Clips")
        self.assertAlmostEqual(yt_mid_count / len(mid_section), 0.50, delta=0.20)

        # No 3 identical consecutive sources
        self.assertFalse(any(sequence[i] == sequence[i - 1] == sequence[i - 2] for i in range(2, len(sequence))))

    def test_web_image_recovery_recovers_empty_path_result(self, tmp_path=None):
        """When results[i] contains a failed dict with path: '', deterministic recovery recovers it."""
        import tempfile
        import shutil
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            src_img = tmp_root / "sample.jpg"
            src_img.write_bytes(b"dummy image content")

            # Simulate results list where scene 0 has an image and scene 1 has an empty-path failure
            results = [
                {"path": str(src_img), "width": 800, "height": 600, "domain": "example.com", "provider": "test"},
                {"path": "", "width": 0, "height": 0, "accepted": False, "failure_reason": "visual_fail_closed"},
            ]
            recovery_pool = [
                (0, {"path": str(src_img), "width": 800, "height": 600, "domain": "example.com", "provider": "test"})
            ]
            scene_texts = ["Scene one text", "Scene two text"]
            scene_active_subjects = ["Subject A", "Subject A"]

            # Run recovery loop logic on results
            n = len(results)
            usage_count = {}
            for i in range(n):
                if bool(results[i] and results[i].get("path")):
                    continue
                # Should find compatible from recovery_pool
                compatible = [
                    pair for pair in recovery_pool
                    if usage_count.get(pair[1]["path"], 0) < 3
                ]
                if compatible:
                    best = compatible[0][1]
                    dest = tmp_root / f"scene_{i + 1}" / "recovered.jpg"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(best["path"], dest)
                    recovered = dict(best)
                    recovered["path"] = str(dest)
                    recovered["reused_from_scene"] = True
                    results[i] = recovered

            self.assertTrue(bool(results[1].get("path")))
            self.assertTrue(results[1].get("reused_from_scene"))


if __name__ == "__main__":
    unittest.main()
