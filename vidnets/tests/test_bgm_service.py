"""
Tests for Vidzeo/Vidnets Background Music (BGM) Engine
Verifies Content-ID safe catalog, channel persona resolution,
audio ducking algorithms, and Remotion/FFmpeg audio contracts.
"""

import os
import sys
import unittest
from pathlib import Path

# Setup paths
TEST_DIR = Path(__file__).resolve().parent
VIDNETS_ROOT = TEST_DIR.parent
SERVER_DIR = VIDNETS_ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from services.bgm_service import (
    get_bgm_catalog,
    resolve_channel_music,
    calculate_ducked_volume,
    CHANNEL_PERSONA_MAP,
    TRACK_METADATA,
)


class TestBGMService(unittest.TestCase):
    """Test suite for background music catalog and resolution."""

    def test_catalog_completeness(self):
        """Verify that all 7 production channels have available music tracks in the catalog."""
        catalog = get_bgm_catalog()
        self.assertGreaterEqual(len(catalog), 7, "Catalog should have at least 7 tracks")
        
        expected_slugs = {
            "classic-wheels",
            "nostalgic-brit",
            "royal-celeb",
            "crown-royal",
            "crown-zone",
            "after-death",
            "train-crew",
        }
        catalog_slugs = {track["slug"] for track in catalog}
        self.assertTrue(expected_slugs.issubset(catalog_slugs), f"Missing slugs: {expected_slugs - catalog_slugs}")

        for track in catalog:
            self.assertTrue(track["available"], f"Track {track['slug']} should be marked available")
            self.assertTrue(Path(track["file_path"]).is_file(), f"File for {track['slug']} must exist on disk")
            self.assertGreater(Path(track["file_path"]).stat().st_size, 50000, f"File {track['slug']} too small")

    def test_channel_persona_resolution(self):
        """Verify that all 7 IX channel profiles resolve accurately to their persona tracks."""
        channels = [
            ("Classic Wheels", "classic-wheels"),
            ("Nostalgic Brit", "nostalgic-brit"),
            ("Royal Celeb", "royal-celeb"),
            ("Crown Royal", "crown-royal"),
            ("Crown Zone", "crown-zone"),
            ("After Death", "after-death"),
            ("Train Crew", "train-crew"),
        ]
        for channel_name, expected_slug in channels:
            resolved = resolve_channel_music(channel_name=channel_name)
            self.assertIsNotNone(resolved, f"Should resolve for channel: {channel_name}")
            self.assertEqual(resolved["slug"], expected_slug, f"Mismatched slug for {channel_name}")
            self.assertTrue(Path(resolved["path"]).is_file(), f"Resolved path must exist for {channel_name}")

    def test_niche_keyword_resolution(self):
        """Verify niche / category fallback when channel name is generic."""
        niche_tests = [
            ("vintage cars documentary", "classic-wheels"),
            ("british history and heritage", "nostalgic-brit"),
            ("royal celebrity rumors", "royal-celeb"),
            ("monarchy and kings", "crown-royal"),
            ("true crime mystery", "after-death"),
            ("railways and steam locomotives", "train-crew"),
        ]
        for niche, expected_slug in niche_tests:
            resolved = resolve_channel_music(channel_name="Generic Documentary", niche=niche)
            self.assertIsNotNone(resolved, f"Should resolve for niche: {niche}")
            self.assertEqual(resolved["slug"], expected_slug, f"Mismatched slug for niche '{niche}'")

    def test_genre_preset_resolution(self):
        """Verify resolution via explicit genre preset."""
        genre_tests = [
            ("retro_groove", "classic-wheels"),
            ("british_orchestral", "nostalgic-brit"),
            ("investigative_tension", "royal-celeb"),
            ("royal_orchestral", "crown-royal"),
            ("ambient_dark", "after-death"),
            ("industrial_acoustic", "train-crew"),
            ("documentary", "crown-zone"),
        ]
        for genre, expected_slug in genre_tests:
            resolved = resolve_channel_music(genre=genre)
            self.assertIsNotNone(resolved, f"Should resolve for genre: {genre}")
            self.assertEqual(resolved["slug"], expected_slug, f"Mismatched slug for genre '{genre}'")

    def test_explicit_path_precedence(self):
        """Verify explicit file path takes highest precedence."""
        # Use an existing track file as explicit path
        existing_track = VIDNETS_ROOT / "public" / "music" / "classic-wheels.mp3"
        resolved = resolve_channel_music(
            channel_name="After Death",  # would normally resolve to after-death
            explicit_path=str(existing_track)
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["slug"], "classic-wheels")
        self.assertEqual(resolved["source"], "explicit_path")

    def test_ducking_volume_calculation(self):
        """Verify instantaneous ducked volume during narration and pauses."""
        # Narration from 2.0s to 5.0s and 8.0s to 12.0s
        speech_intervals = [(2.0, 5.0), (8.0, 12.0)]
        base_vol = 0.20
        duck_ratio = 0.45
        ducked_vol = base_vol * duck_ratio  # 0.09

        # At 0.5s: idle (no speech) -> full base volume
        vol_idle = calculate_ducked_volume(0.5, speech_intervals, base_volume=base_vol, ducked_ratio=duck_ratio)
        self.assertAlmostEqual(vol_idle, base_vol, places=2)

        # At 3.0s: active speech -> ducked volume
        vol_speech = calculate_ducked_volume(3.0, speech_intervals, base_volume=base_vol, ducked_ratio=duck_ratio)
        self.assertAlmostEqual(vol_speech, ducked_vol, places=2)

        # At 6.5s: pause between speeches -> full base volume
        vol_pause = calculate_ducked_volume(6.5, speech_intervals, base_volume=base_vol, ducked_ratio=duck_ratio)
        self.assertAlmostEqual(vol_pause, base_vol, places=2)

        # At 10.0s: second speech block -> ducked volume
        vol_speech2 = calculate_ducked_volume(10.0, speech_intervals, base_volume=base_vol, ducked_ratio=duck_ratio)
        self.assertAlmostEqual(vol_speech2, ducked_vol, places=2)

        # Closely spaced intervals: active speech in second interval must NOT be overridden by release of first
        close_intervals = [(0.0, 5.0), (5.2, 10.0)]
        vol_close = calculate_ducked_volume(5.3, close_intervals, base_volume=base_vol, ducked_ratio=duck_ratio)
        self.assertAlmostEqual(vol_close, ducked_vol, places=2, msg="Speech in 2nd interval must stay ducked")

        # Unsorted intervals must produce same robust ducking
        unsorted_intervals = [(5.2, 10.0), (0.0, 5.0)]
        vol_unsorted = calculate_ducked_volume(5.3, unsorted_intervals, base_volume=base_vol, ducked_ratio=duck_ratio)
        self.assertAlmostEqual(vol_unsorted, ducked_vol, places=2)

        # Attack ramp: before speech begins
        vol_attack = calculate_ducked_volume(1.8, [(2.0, 5.0)], base_volume=base_vol, ducked_ratio=duck_ratio, attack_sec=0.35)
        self.assertTrue(ducked_vol < vol_attack < base_vol, f"Attack ramp should be between {ducked_vol} and {base_vol}")

        # Release ramp: after speech ends
        vol_release = calculate_ducked_volume(5.25, [(2.0, 5.0)], base_volume=base_vol, ducked_ratio=duck_ratio, release_sec=0.50)
        self.assertTrue(ducked_vol < vol_release < base_vol, f"Release ramp should be between {ducked_vol} and {base_vol}")

    def test_profile_id_and_alias_resolution(self):
        """Verify numeric IDs, dashed aliases, and profile prefixes resolve to expected personas."""
        alias_tests = [
            ("1", "classic-wheels"),
            ("ix-1", "classic-wheels"),
            ("profile 1", "classic-wheels"),
            ("2", "nostalgic-brit"),
            ("ix-2", "nostalgic-brit"),
            ("profile-2", "nostalgic-brit"),
            ("3", "royal-celeb"),
            ("ix-3", "royal-celeb"),
            ("4", "crown-royal"),
            ("ix-4", "crown-royal"),
            ("5", "crown-zone"),
            ("ix-5", "crown-zone"),
            ("6", "after-death"),
            ("ix-6", "after-death"),
            ("7", "train-crew"),
            ("ix-7", "train-crew"),
        ]
        for name_input, expected_slug in alias_tests:
            resolved = resolve_channel_music(channel_name=name_input)
            self.assertIsNotNone(resolved, f"Failed to resolve alias: {name_input}")
            self.assertEqual(resolved["slug"], expected_slug, f"Mismatched slug for alias '{name_input}'")

    def test_cms_editorial_presets(self):
        """Verify CMS-assigned editorial presets map to appropriate genre tracks."""
        cms_presets = [
            ("archival-dossier", "classic-wheels"),
            ("broadsheet-heritage", "nostalgic-brit"),
            ("royal-prestige", "crown-royal"),
            ("forensic-noir", "after-death"),
            ("engineering-blueprint", "train-crew"),
        ]
        for preset, expected_slug in cms_presets:
            resolved = resolve_channel_music(genre=preset)
            self.assertIsNotNone(resolved, f"Failed to resolve CMS preset: {preset}")
            self.assertEqual(resolved["slug"], expected_slug, f"Mismatched slug for preset '{preset}'")

    def test_edge_cases_and_fallbacks(self):
        """Test boundary cases: empty inputs, bad explicit paths, zero volume."""
        # 1. Empty strings -> should fall back to documentary
        fallback = resolve_channel_music(channel_name="", niche="", genre="")
        self.assertIsNotNone(fallback)
        self.assertTrue(Path(fallback["path"]).is_file())

        # 2. Non-existent explicit path -> should fall back gracefully to channel
        bad_path_res = resolve_channel_music(channel_name="Classic Wheels", explicit_path="/nonexistent/fake/file.mp3")
        self.assertIsNotNone(bad_path_res)
        self.assertEqual(bad_path_res["slug"], "classic-wheels")

        # 3. Empty speech intervals -> music stays at base volume
        vol_no_speech = calculate_ducked_volume(5.0, [], base_volume=0.25)
        self.assertAlmostEqual(vol_no_speech, 0.25, places=2)

        # 4. Extreme volume inputs -> clamped to 0.0 .. 1.0
        vol_high = calculate_ducked_volume(1.0, [], base_volume=2.5)
        self.assertAlmostEqual(vol_high, 1.0, places=2)
        vol_low = calculate_ducked_volume(1.0, [], base_volume=-0.5)
        self.assertAlmostEqual(vol_low, 0.0, places=2)

    def test_manifest_scene_timing_ducking_compatibility(self):
        """Verify that scenes with start/duration in seconds extract speech intervals correctly."""
        # Simulated scenes from build_semantic_edit_manifest where from and duration_in_frames are None
        scenes = [
            {"from": None, "start": 0.0, "duration": 4.0, "duration_in_frames": None, "narration": "First scene narration."},
            {"from": None, "start": 4.0, "duration": 1.0, "duration_in_frames": None, "narration": ""},  # pause
            {"from": None, "start": 5.0, "duration": 5.0, "duration_in_frames": None, "narration": "Second scene narration."},
        ]
        # Calculate intervals as done in DocumentaryComposition
        fps = 30
        intervals = []
        for s in scenes:
            if s.get("narration") and str(s["narration"]).strip():
                s_from = int(s["from"]) if s.get("from") is not None else int(round(float(s.get("start", 0) or 0) * fps))
                s_dur = int(s["duration_in_frames"]) if s.get("duration_in_frames") is not None else int(round(float(s.get("duration", 0) or 0) * fps))
                if s_dur > 0:
                    intervals.append((s_from / fps, (s_from + s_dur) / fps))

        self.assertEqual(len(intervals), 2)
        self.assertAlmostEqual(intervals[0][0], 0.0)
        self.assertAlmostEqual(intervals[0][1], 4.0)
        self.assertAlmostEqual(intervals[1][0], 5.0)
        self.assertAlmostEqual(intervals[1][1], 10.0)

        # Active speech in scene 1 -> ducked
        vol_sc1 = calculate_ducked_volume(2.0, intervals, base_volume=0.20, ducked_ratio=0.45)
        self.assertAlmostEqual(vol_sc1, 0.09, places=2)

        # Gap at 4.5s -> swells back towards 0.20
        vol_gap = calculate_ducked_volume(4.5, intervals, base_volume=0.20, ducked_ratio=0.45)
        self.assertAlmostEqual(vol_gap, 0.20, places=2)

        # Active speech in scene 2 -> ducked
        vol_sc2 = calculate_ducked_volume(7.0, intervals, base_volume=0.20, ducked_ratio=0.45)
        self.assertAlmostEqual(vol_sc2, 0.09, places=2)


if __name__ == "__main__":
    unittest.main()

