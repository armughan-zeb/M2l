"""Tests for media manifest provenance (Nov 10, 2026 — BNSF incident).

The CMS job r_mw85awoskks8lh was delivered the WRONG video: the manual
deliver picked a stale output-package file by TITLE (job 876f0e84, Nov 9,
10/10 graphic fallback, 18 MB) instead of the claiming job's own master
(job f04c6b9f, Nov 10, 15/15 real media, 277 MB). The delivered manifest
even said so — its 10 media_paths all point at temp/jobs/876f0e84/.

Fixtures below are the real manifests' essential fields (paths trimmed
for readability; the full files live in the runtime output/archive dirs).
These tests pin the fix:

* every manifest is stamped with its producing job id + run id;
* media_paths under another job's temp dir are reported as foreign;
* an all-graphic manifest summarizes to zero real media, while the good
  f04c6b9f manifest summarizes to 15/15 real.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


# The delivered (wrong) BNSF manifest: 10 assets, all graphic fallback,
# all paths under the OTHER job's temp dir.
BNSF_WRONG_PATHS = [
    f"/home/hatch/workspace/vidnets-runtime/temp/jobs/876f0e84/876f0e84-run-0001-415318d21b33/render_media/graphics/graphic_fallback_1794197179528_{i}_xxxx.png"
    for i in range(1, 11)
]
BNSF_WRONG_LABELS = ["Grounded Graphic Fallback"] * 10
BNSF_WRONG_ROWS = [
    {
        "scene_index": i,
        "provider": "graphic",
        "status": "degraded",
        "verification_status": "grounded_graphic_fallback",
        "resolution_kind": "grounded_graphic_fallback",
        "selected_path": BNSF_WRONG_PATHS[i],
    }
    for i in range(10)
]

# The good f04c6b9f manifest: 15 assets, 14 web + 1 YouTube clip, all
# paths under the claiming job's own temp dir.
BNSF_GOOD_PATHS = [
    "/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0002-c834c4759400/render_media/web_v2_1794280371551_8e6732/scene_0001.jpg",
    "/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0002-c834c4759400/render_media/web_v2_alt_1_3_1794280371577/scene_0002.jpg",
    "/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0001-490d4e742c5c/render_media/web_v2_1794278341521_54e61d/scene_0003.jpg",
    "/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0002-c834c4759400/render_media/web_v2_alt_3_4_1794280371682/scene_0004.jpg",
    "/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0002-c834c4759400/render_media/youtube_v2_1794281035123_ab12cd/scene_0005.mp4",
] + [
    f"/home/hatch/workspace/vidnets-runtime/temp/jobs/f04c6b9f/f04c6b9f-run-0002-c834c4759400/render_media/web_v2_1794280371551_8e6732/scene_{i:04d}.jpg"
    for i in range(6, 16)
]
BNSF_GOOD_LABELS = ["Web Images"] * 4 + ["YouTube Clips"] + ["Web Images"] * 10
BNSF_GOOD_ROWS = [
    {
        "scene_index": i,
        "provider": "youtube" if i == 4 else "web",
        "status": "accepted",
        "verification_status": "verified",
        "resolution_kind": "late_real_media_fallback",
        "selected_path": BNSF_GOOD_PATHS[i],
    }
    for i in range(15)
]


class TestBuildMediaManifest(unittest.TestCase):
    def test_stamps_job_and_run_ids(self):
        from services.media_manifest import build_media_manifest

        manifest = build_media_manifest(
            {"media_paths": ["/tmp/a.jpg"], "source_labels": ["Web Images"], "metadata": {}},
            {"_queue_job_id": "f04c6b9f", "_pipeline_run_id": "f04c6b9f-run-0002-c834c4759400"},
        )
        self.assertEqual(manifest["job_id"], "f04c6b9f")
        self.assertEqual(manifest["run_id"], "f04c6b9f-run-0002-c834c4759400")
        self.assertEqual(manifest["asset_count"], 1)

    def test_job_id_falls_back_to_plain_keys(self):
        from services.media_manifest import build_media_manifest

        manifest = build_media_manifest({}, {"job_id": "abc123", "run_id": "run-1"})
        self.assertEqual(manifest["job_id"], "abc123")
        self.assertEqual(manifest["run_id"], "run-1")

    def test_missing_config_leaves_blank_stamp(self):
        from services.media_manifest import build_media_manifest

        manifest = build_media_manifest({"media_paths": []}, None)
        self.assertEqual(manifest["job_id"], "")
        self.assertEqual(manifest["asset_count"], 0)


class TestForeignJobReferences(unittest.TestCase):
    def test_bnsf_wrong_manifest_paths_are_foreign_to_f04c6b9f(self):
        from services.media_manifest import foreign_job_references

        self.assertEqual(foreign_job_references(BNSF_WRONG_PATHS, "f04c6b9f"), ["876f0e84"])

    def test_own_job_paths_are_not_foreign(self):
        from services.media_manifest import foreign_job_references

        self.assertEqual(foreign_job_references(BNSF_GOOD_PATHS, "f04c6b9f"), [])

    def test_shared_cache_paths_without_job_dir_are_not_foreign(self):
        from services.media_manifest import foreign_job_references

        self.assertEqual(
            foreign_job_references(["/home/hatch/workspace/vidnets-runtime/temp/cache/a.jpg"], "f04c6b9f"),
            [],
        )

    def test_multiple_foreign_jobs_are_all_reported_once(self):
        from services.media_manifest import foreign_job_references

        paths = [
            "/x/temp/jobs/aaa/run/render_media/a.jpg",
            "/x/temp/jobs/bbb/run/render_media/b.jpg",
            "/x/temp/jobs/aaa/run/render_media/c.jpg",
        ]
        self.assertEqual(foreign_job_references(paths, "ccc"), ["aaa", "bbb"])


class TestSummarizeManifest(unittest.TestCase):
    def test_bnsf_wrong_manifest_is_zero_real_media(self):
        from services.media_manifest import summarize_manifest

        summary = summarize_manifest(
            {
                "asset_count": 10,
                "media_paths": BNSF_WRONG_PATHS,
                "source_labels": BNSF_WRONG_LABELS,
                "metadata": {"media_resolution_v2": BNSF_WRONG_ROWS},
            }
        )
        self.assertEqual(summary["asset_count"], 10)
        self.assertEqual(summary["real_count"], 0)
        self.assertEqual(summary["graphic_count"], 10)

    def test_bnsf_good_manifest_is_fifteen_real_media(self):
        from services.media_manifest import summarize_manifest

        summary = summarize_manifest(
            {
                "job_id": "f04c6b9f",
                "asset_count": 15,
                "media_paths": BNSF_GOOD_PATHS,
                "source_labels": BNSF_GOOD_LABELS,
                "metadata": {"media_resolution_v2": BNSF_GOOD_ROWS},
            }
        )
        self.assertEqual(summary["asset_count"], 15)
        self.assertEqual(summary["real_count"], 15)
        self.assertEqual(summary["graphic_count"], 0)
        self.assertEqual(summary["foreign_jobs"], [])

    def test_labels_alone_detect_graphics_when_no_rows(self):
        from services.media_manifest import summarize_manifest

        summary = summarize_manifest(
            {
                "asset_count": 3,
                "media_paths": ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg"],
                "source_labels": ["Web Images", "Grounded Graphic Fallback", "YouTube Clips"],
            }
        )
        self.assertEqual(summary["real_count"], 2)
        self.assertEqual(summary["graphic_count"], 1)


class TestManifestMatchesJob(unittest.TestCase):
    def test_stamped_mismatch_is_not_a_match(self):
        from services.media_manifest import manifest_matches_job

        self.assertFalse(manifest_matches_job({"job_id": "876f0e84"}, "f04c6b9f"))
        self.assertTrue(manifest_matches_job({"job_id": "f04c6b9f"}, "f04c6b9f"))

    def test_unstamped_manifest_cannot_contradict(self):
        from services.media_manifest import manifest_matches_job

        self.assertTrue(manifest_matches_job({}, "f04c6b9f"))
        self.assertTrue(manifest_matches_job({"job_id": ""}, "f04c6b9f"))


if __name__ == "__main__":
    unittest.main()
