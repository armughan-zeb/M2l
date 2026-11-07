import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.real_media_service import _prefer_stock_media_type


class StockMediaPreferenceTests(unittest.TestCase):
    def test_prefers_video_for_motion_scene(self):
        scene = {
            "text": "Crowd celebration as the team walks onto the field",
            "duration": 4.2,
            "scene_type": "sports",
        }
        self.assertEqual(_prefer_stock_media_type(scene, "Championship Night"), "video")

    def test_prefers_images_for_static_historical_scene(self):
        scene = {
            "text": "Portrait of the emperor in ancient Rome",
            "duration": 2.5,
            "scene_type": "historical",
        }
        self.assertEqual(_prefer_stock_media_type(scene, "Roman History"), "image")

    def test_defaults_to_video_for_general_stock_scene(self):
        scene = {
            "text": "Trader studies screens in a modern office",
            "duration": 3.1,
            "scene_type": "finance",
        }
        self.assertEqual(_prefer_stock_media_type(scene, "Online Trading Facts"), "video")


if __name__ == "__main__":
    unittest.main()
