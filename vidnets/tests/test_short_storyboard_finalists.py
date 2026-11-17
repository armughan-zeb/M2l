import unittest

from services.media_resolution_coordinator import _finalists_for_storyboard


class ShortStoryboardFinalistsTest(unittest.TestCase):
    def test_short_video_uses_one_primary_candidate_per_scene(self):
        self.assertEqual(_finalists_for_storyboard([{"duration": 4}] * 14, 3), 1)

    def test_long_video_preserves_configured_limit(self):
        self.assertEqual(_finalists_for_storyboard([{"duration": 10}] * 20, 3), 3)

    def test_storyboard_start_end_timing_is_supported(self):
        self.assertEqual(_finalists_for_storyboard([{"start_seconds": 0, "end_seconds": 30}, {"start_seconds": 30, "end_seconds": 60}], 3), 1)

    def test_missing_or_invalid_duration_preserves_configured_limit(self):
        for rows in ([], [{}], [{"duration": "unknown"}], [{"duration": -1}]):
            self.assertEqual(_finalists_for_storyboard(rows, 3), 3)


if __name__ == "__main__":
    unittest.main()
