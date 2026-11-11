"""Tests for the Nov 10, 2026 media speedups.

1. Parallel YT downloads: ranged-download workers (default 6, cap 8, with
   a tiered small-host memory fit instead of the flat 2-worker cap under
   12 GB), pool probe workers (default 10, cap 12), shared-source batch
   workers (default 6, cap 8) and batch deadline (default 300s, was 120s).
2. Vision gate pool 6 -> 12 (env VIDZEO_VISUAL_GATE_WORKERS wins).
3. Tighter pre-filter: the per-scene top-12 pool drops entries scoring below
   VIDZEO_VISUAL_RELATIVE_FLOOR (default 0.5) times the best metadata score,
   while always keeping at least the finalist count — vision never receives
   fewer finalists, and the YouTube motion reservation still works.
5. Pool reuse (fallback tier (a)): an unselected vision-accepted image
   from another scene's top-12 pool resolves a scene whose own lanes
   found nothing, before YT clip frames (tier (b)) and graphics (tier (c)).
   YT coverage target 30% and web pool 12 are covered by
   test_media_selection_spec.py / test_web_candidate_pool.py.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def _item(score, source, path):
    return (0.0, float(score), 0, {"path": path}, source)


class TestRangedDownloadWorkers(unittest.TestCase):
    def test_small_host_cap_is_tiered_not_flat_two(self):
        from services.youtube_pipeline_v2 import _ranged_download_worker_limit

        # This VM: 7.9 GB total, ~1.7 GB available. The old flat cap forced
        # 2 workers on every host under 12 GB; the tiered cap allows 4.
        self.assertEqual(_ranged_download_worker_limit(6, 1759, 8 * 1024**3), 4)
        self.assertEqual(_ranged_download_worker_limit(8, 1759, 8 * 1024**3), 4)
        # Genuinely small hosts (< 6 GB total) still stay at 2.
        self.assertEqual(_ranged_download_worker_limit(6, 5000, 5 * 1024**3), 2)
        # Big hosts are bound only by the request and the per-worker allowance.
        self.assertEqual(_ranged_download_worker_limit(8, 5000, 16 * 1024**3), 8)
        # Tight memory scales down on its own whatever the host size.
        self.assertEqual(_ranged_download_worker_limit(8, 300, 16 * 1024**3), 1)
        # One worker is always the minimum.
        self.assertEqual(_ranged_download_worker_limit(6, 0, 4 * 1024**3), 1)

    def test_ranged_download_workers_default_and_cap(self):
        from services.youtube_pipeline_v2 import (
            _ranged_download_workers,
            _requested_download_workers,
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_ranged_download_workers(), 6)
            self.assertEqual(_ranged_download_workers(download_concurrency=3), 3)
            # Hard cap 8, even when the caller asks for more.
            self.assertEqual(_ranged_download_workers(download_concurrency=20), 8)
            # Memory fit still applies when host memory is known.
            self.assertEqual(
                _ranged_download_workers(available_mb=1759, total_bytes=8 * 1024**3),
                4,
            )
            # Requested count (pre-fit, used by the telemetry receipt)
            # is the same parsing without the memory clamp.
            self.assertEqual(_requested_download_workers(), 6)
            self.assertEqual(_requested_download_workers(download_concurrency=20), 8)
            self.assertEqual(
                _requested_download_workers(download_concurrency=3), 3)
        with mock.patch.dict(
            os.environ, {"VIDZEO_YT_DOWNLOAD_CONCURRENCY": "2"}, clear=True
        ):
            self.assertEqual(_ranged_download_workers(), 2)


class TestPoolDownloadHelpers(unittest.TestCase):
    def test_probe_workers_default_and_env(self):
        from services.youtube_pool_renderer import _pool_probe_workers

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_pool_probe_workers(), 10)
        with mock.patch.dict(
            os.environ, {"VIDZEO_YT_POOL_PROBE_CONCURRENCY": "3"}, clear=True
        ):
            self.assertEqual(_pool_probe_workers(), 3)
        with mock.patch.dict(
            os.environ, {"VIDZEO_YT_POOL_PROBE_CONCURRENCY": "99"}, clear=True
        ):
            self.assertEqual(_pool_probe_workers(), 12)

    def test_source_download_workers_and_deadline(self):
        from services.youtube_pool_renderer import (
            _source_batch_deadline_seconds,
            _source_download_workers,
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_source_download_workers(), 6)
            self.assertEqual(_source_batch_deadline_seconds(), 300.0)
        with mock.patch.dict(
            os.environ,
            {
                "VIDZEO_YT_SOURCE_DOWNLOAD_CONCURRENCY": "2",
                "VIDZEO_YT_POOL_SOURCE_DOWNLOAD_DEADLINE_SECONDS": "90",
            },
            clear=True,
        ):
            self.assertEqual(_source_download_workers(), 2)
            self.assertEqual(_source_batch_deadline_seconds(), 90.0)
        with mock.patch.dict(
            os.environ, {"VIDZEO_YT_SOURCE_DOWNLOAD_CONCURRENCY": "99"}, clear=True
        ):
            self.assertEqual(_source_download_workers(), 8)


class TestVisionGateWorkers(unittest.TestCase):
    def test_gate_workers_default_twelve(self):
        from services.media_resolution_coordinator import _visual_gate_workers

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_visual_gate_workers(38), 12)
            self.assertEqual(_visual_gate_workers(3), 3)
            self.assertEqual(_visual_gate_workers(0), 1)
        with mock.patch.dict(
            os.environ, {"VIDZEO_VISUAL_GATE_WORKERS": "4"}, clear=True
        ):
            self.assertEqual(_visual_gate_workers(38), 4)
        with mock.patch.dict(
            os.environ, {"VIDZEO_VISUAL_GATE_WORKERS": "99"}, clear=True
        ):
            self.assertEqual(_visual_gate_workers(38), 24)

    def test_relative_floor_default_and_clamp(self):
        from services.media_resolution_coordinator import _visual_relative_floor

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_visual_relative_floor(), 0.5)
        with mock.patch.dict(
            os.environ, {"VIDZEO_VISUAL_RELATIVE_FLOOR": "0.8"}, clear=True
        ):
            self.assertEqual(_visual_relative_floor(), 0.8)
        with mock.patch.dict(
            os.environ, {"VIDZEO_VISUAL_RELATIVE_FLOOR": "5"}, clear=True
        ):
            self.assertEqual(_visual_relative_floor(), 0.9)


class TestRelativeFloorPrefilter(unittest.TestCase):
    def test_floor_trims_weak_tail_but_keeps_finalists(self):
        from services.media_resolution_coordinator import _select_scene_finalists

        viable = [_item(score, "web", f"w{i}.jpg") for i, score in enumerate(
            [100, 90, 80, 40, 30, 20, 10, 5, 4, 3, 2, 1]
        )]
        finalists, top_pool = _select_scene_finalists(viable, 3, 12, relative_floor=0.5)
        # Floor is 50: ranks 4-12 (40..1) are cut; the top 3 finalists are kept.
        self.assertEqual(len(top_pool), 3)
        self.assertEqual(len(finalists), 3)
        self.assertEqual([f[3]["path"] for f in finalists], ["w0.jpg", "w1.jpg", "w2.jpg"])

    def test_floor_keeps_strong_pool(self):
        from services.media_resolution_coordinator import _select_scene_finalists

        viable = [_item(score, "web", f"w{i}.jpg") for i, score in enumerate(
            [100, 90, 80, 70, 60, 55, 40, 30]
        )]
        finalists, top_pool = _select_scene_finalists(viable, 3, 12, relative_floor=0.5)
        # Floor is 50: only the 40 and 30 are cut.
        self.assertEqual(len(top_pool), 6)
        self.assertEqual(len(finalists), 3)

    def test_floor_zero_keeps_full_pool(self):
        from services.media_resolution_coordinator import _select_scene_finalists

        viable = [_item(100 - i, "web", f"w{i}.jpg") for i in range(12)]
        _finalists, top_pool = _select_scene_finalists(viable, 3, 12, relative_floor=0.0)
        self.assertEqual(len(top_pool), 12)

    def test_youtube_reservation_survives_floor(self):
        from services.media_resolution_coordinator import _select_scene_finalists

        viable = [_item(100 - i, "web", f"w{i}.jpg") for i in range(7)]
        viable.append(_item(60, "youtube", "yt-in.jpg"))
        finalists, top_pool = _select_scene_finalists(viable, 3, 12, relative_floor=0.5)
        # The YouTube clip (60 >= 50) stays in the pool and keeps its slot.
        self.assertIn("yt-in.jpg", [f[3]["path"] for f in finalists])
        self.assertIn("yt-in.jpg", [f[3]["path"] for f in top_pool])


class TestSparePoolReuse(unittest.TestCase):
    """Fallback tier (a): reuse the job's already-downloaded pool first."""

    def _real_file(self, directory, name):
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(b"\x00" * 2048)
        return path

    def _setup(self, directory):
        from services.media_resolution_coordinator import _pick_spare_candidate  # noqa: F401

        near = self._real_file(directory, "near.jpg")
        far = self._real_file(directory, "far.jpg")
        # Five scenes: the spare target is scene 2, so origins 0 and 4
        # are both non-adjacent (distance 2). Adjacent origins (1, 3)
        # are never eligible — the same still in two consecutive scenes
        # reads as a frozen frame (see test_skips_adjacent_origin).
        rows = [
            {"scene_index": 0, "container_id": "bnsf", "text": "BNSF locomotive snow"},
            {"scene_index": 1, "container_id": "bnsf", "text": "BNSF locomotive snow"},
            {"scene_index": 2, "container_id": "bnsf", "text": "BNSF locomotive snow"},
            {"scene_index": 3, "container_id": "other", "text": "BNSF locomotive snow"},
            {"scene_index": 4, "container_id": "other", "text": "BNSF locomotive snow"},
        ]
        discovered = [
            [{"path": near, "source": "web", "provider": "web", "title": "BNSF snow",
              "url": "https://images.example/near.jpg", "score": 90}],
            [],
            [],
            [],
            [{"path": far, "source": "web", "provider": "web", "title": "BNSF snow",
              "url": "https://images.example/far.jpg", "score": 95}],
        ]
        outcomes = {
            (0, near): (90.0, 0, True, "accepted", 85.0, {"accepted": True, "status": "accepted"}),
            (4, far): (95.0, 0, True, "accepted", 90.0, {"accepted": True, "status": "accepted"}),
        }
        return rows, discovered, outcomes, near, far

    def test_picks_same_container_spare_over_higher_scored_distant_one(self):
        from services.media_resolution_coordinator import _pick_spare_candidate

        with tempfile.TemporaryDirectory() as directory:
            rows, discovered, outcomes, near, far = self._setup(directory)
            spare = _pick_spare_candidate(2, rows[2], rows, discovered, outcomes, {})
            self.assertIsNotNone(spare)
            # Same container (scene 0) beats the higher-scored other-container
            # spare (scene 4, visual 90 vs 85).
            self.assertEqual(spare[2]["path"], near)
            self.assertEqual(spare[2]["fallback_layer"], "spare_pool_reuse")
            self.assertEqual(spare[3], {"accepted": True, "status": "accepted"})

    def test_skips_adjacent_origin(self):
        from services.media_resolution_coordinator import _pick_spare_candidate

        with tempfile.TemporaryDirectory() as directory:
            rows, discovered, outcomes, near, far = self._setup(directory)
            # Scene 1's only donor is scene 0, next door: skipped even
            # though the photo is vision-accepted. With the far donor
            # (scene 4, distance 3) available, scene 1 takes that one.
            adjacent_only = {
                (0, near): outcomes[(0, near)],
            }
            self.assertIsNone(
                _pick_spare_candidate(1, rows[1], rows, discovered, adjacent_only, {}))
            # And the far spare is still reachable from scene 1 (distance 3).
            self.assertEqual(
                _pick_spare_candidate(1, rows[1], rows, discovered, outcomes, {})[2]["path"],
                far)

    def test_skips_fatal_verdicts_unverified_and_missing_files(self):
        from services.media_resolution_coordinator import _pick_spare_candidate

        with tempfile.TemporaryDirectory() as directory:
            rows, discovered, outcomes, near, far = self._setup(directory)
            # Fatal identity rejection on the near spare.
            fatal = dict(outcomes)
            fatal[(0, near)] = (90.0, 0, True, "wrong operator", 85.0,
                                {"accepted": False, "status": "rejected", "fatal_mismatch": True})
            spare = _pick_spare_candidate(2, rows[2], rows, discovered, fatal, {})
            self.assertIsNotNone(spare)
            self.assertEqual(spare[2]["path"], far)
            # Unverified + missing file: nothing usable left.
            dead = {
                (0, near): (90.0, 0, False, "not checked", 0.0, {"accepted": False}),
                (4, "/tmp/does-not-exist.jpg"): (95.0, 0, True, "accepted", 90.0,
                                                 {"accepted": True, "status": "accepted"}),
            }
            discovered[4] = [{"path": "/tmp/does-not-exist.jpg", "source": "web",
                              "provider": "web", "url": "https://images.example/far.jpg"}]
            self.assertIsNone(_pick_spare_candidate(2, rows[2], rows, discovered, dead, {}))

    def test_skips_same_scene_origin_and_capped_reuse(self):
        from services.media_resolution_coordinator import _pick_spare_candidate, _web_asset_identity

        with tempfile.TemporaryDirectory() as directory:
            rows, discovered, outcomes, near, far = self._setup(directory)
            # The target scene's own pool never counts as a spare: for
            # scene 0 the picker must skip origin 0 and take scene 4's far.
            spare = _pick_spare_candidate(0, rows[0], rows, discovered, outcomes, {})
            self.assertIsNotNone(spare)
            self.assertEqual(spare[2]["path"], far)
            # Spend the web reuse cap (2) on the near asset: for scene 2
            # the near spare is capped, so the far one is taken instead.
            identity = _web_asset_identity(discovered[0][0])
            spare = _pick_spare_candidate(2, rows[2], rows, discovered, outcomes,
                                           {identity: 2})
            self.assertIsNotNone(spare)
            self.assertEqual(spare[2]["path"], far)

    def test_empty_scene_resolves_from_another_scenes_pool(self):
        from services.media_resolution_coordinator import resolve_storyboard_media_v2

        with tempfile.TemporaryDirectory() as directory:
            shared = self._real_file(directory, "shared.jpg")
            own = self._real_file(directory, "own.jpg")
            # Three scenes: scene 0 donates, scene 1 has its own image,
            # scene 2 has nothing — the spare must come from scene 0
            # (distance 2, non-adjacent), never from scene 1 (adjacent).
            scenes = [
                {"scene_id": "scene-0", "scene_index": 0, "container_id": "bnsf",
                 "text": "BNSF locomotive in snow", "positive_subjects": ["BNSF"],
                 "forbidden_subjects": [],
                 "source_policy": {"visual_verification_required": True, "strict_real_media": True}},
                {"scene_id": "scene-1", "scene_index": 1, "container_id": "bnsf",
                 "text": "BNSF locomotive in snow", "positive_subjects": ["BNSF"],
                 "forbidden_subjects": [],
                 "source_policy": {"visual_verification_required": True, "strict_real_media": True}},
                {"scene_id": "scene-2", "scene_index": 2, "container_id": "bnsf",
                 "text": "BNSF locomotive in snow", "positive_subjects": ["BNSF"],
                 "forbidden_subjects": [],
                 "source_policy": {"visual_verification_required": True, "strict_real_media": True}},
            ]

            def web_lane(scene, _cancel):
                if scene["scene_index"] == 2:
                    return []
                path = shared if scene["scene_index"] == 0 else own
                return [{"path": path, "source": "web", "provider": "web",
                         "title": "BNSF locomotive snow", "score": 90,
                         "url": f"https://images.example/{os.path.basename(path)}"}]

            def verify(_scene, _candidate):
                return {"accepted": True, "status": "accepted", "score": 90}

            result = resolve_storyboard_media_v2(
                scenes,
                youtube_lane=lambda _scene, _cancel: [],
                web_lane=web_lane,
                visual_verifier=verify,
                neutral_factory=lambda _scene: None,
                deadline_seconds=30,
            )
            self.assertEqual(len(result), 3)
            self.assertEqual(result[0].selected_path, shared)
            self.assertEqual(result[0].status, "accepted")
            self.assertEqual(result[1].selected_path, own)
            self.assertEqual(result[1].status, "accepted")
            self.assertEqual(result[2].selected_path, shared)
            self.assertEqual(result[2].status, "accepted")
            self.assertEqual(result[2].fallback_reason, "spare_pool_reuse")


if __name__ == "__main__":
    unittest.main()
