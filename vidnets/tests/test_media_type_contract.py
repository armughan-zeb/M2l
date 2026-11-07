import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_type_contract import enforce_mixed_media_type_contract


class MediaTypeContractTests(unittest.TestCase):
    def test_repairs_fallback_type_drift_without_network_search(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "clip.mp4"
            image = root / "image.jpg"
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            plan = {
                "media_paths": [str(image), str(image), str(video), str(video)],
                "source_labels": ["Web"] * 4,
                "metadata": {
                    "strict_mixed_percentages": True,
                    "media_plan": {
                        "grouped_indices": {
                            "Web Images": [0, 1, 2],
                            "YouTube Clips": [3],
                            "Stock Media": [],
                            "AI Images": [],
                        }
                    }
                },
            }
            result = enforce_mixed_media_type_contract(
                plan,
                ["ship at port", "ship at sea", "ship interior", "ship moving"],
            )
            paths = result["media_paths"]
            self.assertTrue(paths[2].endswith(".jpg"))
            self.assertTrue(paths[3].endswith(".mp4"))
            contract = result["metadata"]["media_type_contract"]
            self.assertEqual(contract["target_video"], 1)
            self.assertEqual(contract["actual_video"], 1)
            self.assertEqual(contract["target_image"], 3)
            self.assertEqual(contract["actual_image"], 3)

    def test_reports_shortfall_when_requested_type_has_no_valid_pool(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "image.jpg"
            image.write_bytes(b"image")
            plan = {
                "media_paths": [str(image)],
                "source_labels": ["Web"],
                "metadata": {
                    "strict_mixed_percentages": True,
                    "media_plan": {
                        "grouped_indices": {
                            "Web Images": [],
                            "YouTube Clips": [0],
                        }
                    }
                },
            }
            result = enforce_mixed_media_type_contract(plan, ["moving ship"])
            contract = result["metadata"]["media_type_contract"]
            self.assertEqual(contract["actual_video"], 0)
            self.assertEqual(contract["shortfall_scene_indices"], [0])

    def test_final_whisper_plan_overrides_stale_retrieval_count_and_replaces_emergency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "clip.mp4"
            image = root / "image.jpg"
            emergency = root / "emergency.jpg"
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            emergency.write_bytes(b"emergency")
            plan = {
                "media_paths": [
                    str(video), str(image), str(emergency), str(emergency)
                ],
                "source_labels": [
                    "YouTube Clips", "Web Images",
                    "Emergency Visual Fallback", "Emergency Visual Fallback",
                ],
                "metadata": {
                    "strict_mixed_percentages": True,
                    "media_plan": {
                        "grouped_indices": {
                            "Web Images": [0],
                            "YouTube Clips": [1],
                        }
                    },
                    "final_media_plan": {
                        "grouped_indices": {
                            "Web Images": [0, 1],
                            "YouTube Clips": [2, 3],
                            "Stock Media": [],
                            "AI Images": [],
                        }
                    },
                },
            }

            result = enforce_mixed_media_type_contract(
                plan,
                ["ship portrait", "ship deck", "ship moving", "ocean voyage"],
            )
            contract = result["metadata"]["media_type_contract"]

            self.assertEqual(contract["target_video"], 2)
            self.assertEqual(contract["target_image"], 2)
            self.assertEqual(contract["actual_video"], 2)
            self.assertEqual(contract["actual_image"], 2)
            self.assertEqual(contract["emergency_asset_count"], 0)

    def test_reports_diversity_shortfall_instead_of_unlimited_clip_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"video")
            second.write_bytes(b"video")
            plan = {
                "media_paths": [
                    str(first), str(first), str(first), str(second), str(second)
                ],
                "source_labels": ["YouTube Clips"] * 5,
                "metadata": {
                    "strict_mixed_percentages": True,
                    "final_media_plan": {
                        "grouped_indices": {
                            "Web Images": [],
                            "YouTube Clips": [0, 1, 2, 3, 4],
                        }
                    }
                },
            }

            with patch.dict(
                os.environ,
                {"VIDZEO_FINAL_MEDIA_REUSE_CAP": "2"},
            ):
                result = enforce_mixed_media_type_contract(
                    plan,
                    ["ship moving"] * 5,
                )

            contract = result["metadata"]["media_type_contract"]
            self.assertEqual(contract["asset_reuse_cap"], 2)
            self.assertTrue(contract["diversity_shortfall_scene_indices"])
            self.assertTrue(contract["shortfall_scene_indices"])


if __name__ == "__main__":
    unittest.main()
