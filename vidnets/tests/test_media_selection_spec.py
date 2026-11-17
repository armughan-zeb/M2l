"""Tests for the user-specified media selection flow.

Spec (Nov 10, 2026):
  WEB: specific queries (web vs YT differ; YT most detailed) -> direct
  multi-engine search -> metadata rank, top 12 per scene -> top 3 to
  vision -> rank top 1, scene resolved.
  YT: specific queries -> metadata rank -> transcripts -> timestamp rank
  -> vision on 1-2 FRAMES per clip at matched timestamps (frames only,
  never the full clip) -> resolve.
  PARALLEL: YT + web lanes run in parallel; one vision wave per job.
  No API keys; direct retrieval only.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def _scene(index, text="BNSF locomotive cab in a whiteout"):
    return {
        "scene_id": f"scene-{index}",
        "scene_index": index,
        "container_id": "bnsf",
        "text": text,
        "positive_subjects": ["BNSF"],
        "forbidden_subjects": [],
        "source_policy": {"visual_verification_required": True, "strict_real_media": True},
    }


def _web_candidates(scene_index, count=12):
    return [
        {
            "path": f"scene-{scene_index}-candidate-{i}.jpg",
            "source": "web",
            "provider": "web",
            "title": "BNSF locomotive snow",
            "score": 100 - i,
        }
        for i in range(count)
    ]


class TestTopTwelveTopThreeTopOne(unittest.TestCase):
    def test_long_job_finalists_are_top_three_by_metadata(self):
        """A >6-scene job sends the top 3 (not 2) per scene to vision."""
        from services.media_resolution_coordinator import (
            resolve_storyboard_media_v2,
        )

        scenes = [_scene(i) for i in range(10)]
        verified = []

        def verify(_scene, candidate):
            verified.append(candidate["path"])
            return {"accepted": True, "status": "accepted", "score": 90}

        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"VIDZEO_VISUAL_FINALIST_LIMIT", "VIDZEO_VISUAL_TOP_CANDIDATES_PER_SCENE"}
        }
        with mock.patch.dict(os.environ, env, clear=True):
            result = resolve_storyboard_media_v2(
                scenes,
                youtube_lane=lambda _scene, _cancel: [],
                web_lane=lambda _scene, _cancel: _web_candidates(_scene["scene_index"]),
                visual_verifier=verify,
                neutral_factory=lambda _scene: None,
                deadline_seconds=30,
            )
        self.assertEqual(len(result), 10)
        for index in range(10):
            seen = [p for p in verified if p.startswith(f"scene-{index}-candidate-")]
            self.assertEqual(
                seen,
                [f"scene-{index}-candidate-{i}.jpg" for i in range(3)],
                f"scene {index} should verify exactly its top 3 metadata candidates",
            )
            # Top vision score ties -> highest metadata wins -> top 1.
            self.assertEqual(result[index].selected_path, f"scene-{index}-candidate-0.jpg")

    def test_expansion_stays_inside_top_twelve(self):
        """Retained expansion verifies only the top-12 pool, never ranks 13-16."""
        from services.media_resolution_coordinator import (
            resolve_storyboard_media_v2,
        )

        verified = []

        def verify(_scene, candidate):
            verified.append(candidate["path"])
            # Reject the top 3 finalists; accept everything else.
            rank = int(candidate["path"].split("candidate-")[1].split(".")[0])
            accepted = rank >= 3
            return {
                "accepted": accepted,
                "status": "accepted" if accepted else "rejected",
                "score": 90 if accepted else 10,
            }

        with mock.patch.dict(
            os.environ,
            {"VIDZEO_VISUAL_PRIMARY_EXPANSION_LIMIT": "6"},
            clear=False,
        ):
            result = resolve_storyboard_media_v2(
                [_scene(0)],
                youtube_lane=lambda _scene, _cancel: [],
                web_lane=lambda _scene, _cancel: _web_candidates(0, 16),
                visual_verifier=verify,
                neutral_factory=lambda _scene: None,
                deadline_seconds=30,
            )
        # Expansion reaches ranks 4-7 (indices 3-6) in one recovery wave
        # of 4; it stops once rank 4 (index 3) is accepted, so ranks 8-12
        # (indices 7-11) are never reached even though they are in the pool.
        for rank in range(3, 7):
            self.assertIn(f"scene-0-candidate-{rank}.jpg", verified)
        # ...and never ranks 8-16: 8-12 are unreached pool members, 13-16
        # are outside the top-12 pool and must not reach vision.
        for rank in range(7, 16):
            self.assertNotIn(
                f"scene-0-candidate-{rank}.jpg",
                verified,
                f"rank {rank + 1} is outside the verified set and must not reach vision",
            )
        self.assertEqual(result[0].selected_path, "scene-0-candidate-3.jpg")

    def test_select_scene_finalists_helper(self):
        """Pure helper: top-12 pool, top-3 finalists, YouTube reservation."""
        from services.media_resolution_coordinator import _select_scene_finalists

        def item(score, source, path):
            return (0.0, float(score), 0, {"path": path}, source)

        viable = [item(100 - i, "web", f"w{i}.jpg") for i in range(12)]
        viable.insert(12, item(50, "youtube", "yt-low.jpg"))
        finalists, top_pool = _select_scene_finalists(viable, 3, 12)
        self.assertEqual(len(top_pool), 12)
        self.assertEqual(len(finalists), 3)
        # The 13th-ranked YouTube clip is outside the top-12: no reservation.
        self.assertNotIn("yt-low.jpg", [f[3]["path"] for f in finalists])
        # A YouTube clip inside the top-12 is reserved a finalist slot.
        viable2 = [item(100 - i, "web", f"w{i}.jpg") for i in range(7)]
        viable2.append(item(60, "youtube", "yt-in.jpg"))
        finalists2, _ = _select_scene_finalists(viable2, 3, 12)
        self.assertIn("yt-in.jpg", [f[3]["path"] for f in finalists2])


class TestVisionFrames(unittest.TestCase):
    def test_visual_gate_frames_defaults_to_two(self):
        from services.youtube_pipeline_v2 import _visual_gate_frames

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_visual_gate_frames(), 2)
        with mock.patch.dict(os.environ, {"VIDZEO_YT_VISUAL_GATE_FRAMES": "1"}, clear=False):
            self.assertEqual(_visual_gate_frames(), 1)
        with mock.patch.dict(os.environ, {"VIDZEO_YT_VISUAL_GATE_FRAMES": "5"}, clear=False):
            # Spec caps vision at 1-2 frames per clip.
            self.assertEqual(_visual_gate_frames(), 2)
        with mock.patch.dict(os.environ, {"VIDZEO_YT_VISUAL_GATE_FRAMES": "abc"}, clear=False):
            self.assertEqual(_visual_gate_frames(), 2)

    def test_probe_window_frames_bracket_matched_moment(self):
        """A 3s probe (starts at the moment timestamp) yields 2 frames:
        near the moment start and near its end — never the full clip."""
        from services.visual_intelligence.frame_extractor import (
            _scene_split_timestamps,
            extract_clip_frames,
        )

        stamps = _scene_split_timestamps(3.0)
        self.assertEqual(len(stamps), 3)
        for got, want in zip(stamps, (0.3, 1.5, 2.7)):
            self.assertAlmostEqual(got, want, places=6)
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not available")
        with tempfile.TemporaryDirectory() as tmp:
            clip = os.path.join(tmp, "probe.mp4")
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=duration=3:size=320x240:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", clip,
                ],
                check=True,
            )
            frames, duration = extract_clip_frames(clip, max_frames=2)
            self.assertEqual(len(frames), 2)
            self.assertAlmostEqual(duration, 3.0, places=1)
            for frame in frames:
                self.assertTrue(os.path.isfile(frame))
                self.assertGreater(os.path.getsize(frame), 0)


class TestParallelLanes(unittest.TestCase):
    def test_youtube_and_web_lanes_run_and_share_one_vision_wave(self):
        """Both lanes are invoked for every scene; the vision wave covers
        finalists of all scenes in one batch."""
        from services.media_resolution_coordinator import (
            resolve_storyboard_media_v2,
        )

        scenes = [_scene(i) for i in range(3)]
        lane_calls = []
        verified = []

        def yt_lane(scene, _cancel):
            lane_calls.append(("youtube", scene["scene_index"]))
            return [{
                "path": f"yt-{scene['scene_index']}.mp4",
                "source": "youtube",
                "provider": "youtube",
                "title": "BNSF cab whiteout",
                "score": 95,
            }]

        def web_lane(scene, _cancel):
            lane_calls.append(("web", scene["scene_index"]))
            return _web_candidates(scene["scene_index"], count=2)

        def verify(_scene, candidate):
            verified.append(candidate["path"])
            return {"accepted": True, "status": "accepted", "score": 90}

        result = resolve_storyboard_media_v2(
            scenes,
            youtube_lane=yt_lane,
            web_lane=web_lane,
            visual_verifier=verify,
            neutral_factory=lambda _scene: None,
            deadline_seconds=30,
        )
        for index in range(3):
            self.assertIn(("youtube", index), lane_calls)
            self.assertIn(("web", index), lane_calls)
        # The single vision wave verified finalists from every scene.
        verified_scenes = set()
        for p in verified:
            if p.startswith("yt-"):
                verified_scenes.add(p.split("-")[1].split(".")[0])
            elif p.startswith("scene-"):
                verified_scenes.add(p.split("-")[1])
        self.assertEqual(verified_scenes, {"0", "1", "2"})
        self.assertTrue(any(p.startswith("yt-") for p in verified))
        self.assertTrue(any(p.startswith("scene-") for p in verified))
        self.assertEqual(len(result), 3)


class TestQuerySpecificity(unittest.TestCase):
    def test_youtube_queries_most_specific_web_queries_differ(self):
        """YT queries carry the director's detailed plan + viewpoint terms;
        web queries are a different, photo-oriented pack."""
        from services.media_query_strategy import enrich_scene_queries

        requirement = {"canonical_entity": "BNSF"}
        narration = "It is shown from the cab, not from the lineside."
        yt_planned = [
            "BNSF locomotive cab interior whiteout snowstorm",
            "BNSF driver cab view snow",
        ]
        web_planned = ["BNSF locomotive snow"]
        yt_queries = enrich_scene_queries(
            yt_planned, requirement, narration,
            provider="youtube", preserve_planned_order=True,
        )
        web_queries = enrich_scene_queries(
            web_planned, requirement, narration,
            provider="web", preserve_planned_order=True,
        )
        # Director's detailed YT query leads, viewpoint phrase survives.
        self.assertEqual(yt_queries[0], yt_planned[0])
        self.assertTrue(any("from the cab" in q for q in yt_queries))
        self.assertTrue(any("footage" in q for q in yt_queries))
        self.assertTrue(any("photo" in q for q in web_queries))
        self.assertNotEqual(yt_queries, web_queries)
        # Negated viewpoint never becomes a positive facet in either pack.
        for queries in (yt_queries, web_queries):
            self.assertFalse(any("lineside" in q for q in queries))


if __name__ == "__main__":
    unittest.main()
