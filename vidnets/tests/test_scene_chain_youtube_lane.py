"""Scene-chain YouTube lane integration tests.

The scene chain walks per-scene lanes and trusts a lane that self-ranks. The
YouTube lane must clear the Qwen visual gate via
``youtube_pipeline_v2.resolve_clip_for_scene`` before returning a clip, so the
chain can accept it immediately without re-ranking.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.scene_chain.chain import Scene, SceneChainResolver, YouTubeLane
from services import youtube_pipeline_v2 as v2


class SceneChainYouTubeLaneTests(unittest.TestCase):
    def _overrides(self):
        return [
            {"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"},
            {"_llm": True, "section_subject": "The Undertaker", "scene_subject": "The Undertaker", "domain_category": "sports", "youtube_query": "The Undertaker WrestleMania footage"},
        ]

    def _scenes(self):
        return [
            "John Cena starts with early WWE training.",
            "The Undertaker's WrestleMania streak becomes the defining chapter.",
        ]

    def _fake_search(self, query, max_results=12, session_jar=None):
        q = query.lower()
        if "john cena" in q:
            return [{"id": "cena01", "title": "John Cena WWE training raw footage", "channel": "Wrestling Archive", "description": "raw footage highlights", "duration": 300, "query_text": query}]
        if "undertaker" in q:
            return [{"id": "taker01", "title": "Undertaker WrestleMania streak highlights", "channel": "Wrestling Archive", "description": "match archive highlights", "duration": 300, "query_text": query}]
        return []

    def test_youtube_lane_resolves_visual_gated_clips_for_both_scenes(self):
        def fake_dl(_option, out, _start, _dur, session_jar=None, stream_downloads=False):
            out.write_bytes(b"x" * 100)
            return True

        with mock.patch.object(v2, "_search_youtube", side_effect=self._fake_search), \
             mock.patch.object(v2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(v2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(v2, "_visual_verdict", return_value=(True, "", True)):
            lane = YouTubeLane.build(
                "Top WWE Wrestlers",
                self._scenes(),
                scene_durations=[4.0, 4.0],
                scene_query_overrides=self._overrides(),
            )

        self.assertIsNotNone(lane)
        # Rebuild the lane for the resolve pass (pools carry search state).
        with mock.patch.object(v2, "_search_youtube", side_effect=self._fake_search), \
             mock.patch.object(v2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(v2, "_download_clip", side_effect=fake_dl), \
             mock.patch.object(v2, "_visual_verdict", return_value=(True, "", True)):
            lane = YouTubeLane.build(
                "Top WWE Wrestlers",
                self._scenes(),
                scene_durations=[4.0, 4.0],
                scene_query_overrides=self._overrides(),
            )
            resolver = SceneChainResolver(lanes=[lane], max_workers=2)
            chain_scenes = [
                Scene(index=i, text=text, subject=("John Cena" if i == 0 else "The Undertaker"), duration=4.0)
                for i, text in enumerate(self._scenes())
            ]
            out = resolver.resolve(chain_scenes)

        self.assertEqual(sorted(out), [0, 1])
        self.assertTrue(all(Path(res.path).exists() for res in out.values()))
        self.assertTrue(all(res.source == "youtube" for res in out.values()))

    def test_youtube_lane_returns_none_when_no_pool_survives_metadata(self):
        def fake_search(query, max_results=12, session_jar=None):
            # Only a hard-rejected streamer reaction for the scene subject.
            return [{"id": "stream1", "title": "Streamer reacts to John Cena", "channel": "Live Streams", "description": "reaction facecam", "duration": 300, "query_text": query}]

        with mock.patch.object(v2, "_search_youtube", side_effect=fake_search), \
             mock.patch.object(v2, "_get_authenticated_youtube_session", return_value=None), \
             mock.patch.object(v2, "_download_clip", return_value=True), \
             mock.patch.object(v2, "_visual_verdict", return_value=(True, "", True)):
            lane = YouTubeLane.build(
                "John Cena",
                ["John Cena trains for WWE."],
                scene_durations=[4.0],
                scene_query_overrides=[{"_llm": True, "section_subject": "John Cena", "scene_subject": "John Cena", "domain_category": "sports", "youtube_query": "John Cena WWE training footage"}],
            )
            resolver = SceneChainResolver(lanes=[lane], max_workers=1)
            out = resolver.resolve([Scene(index=0, text="John Cena trains for WWE.", subject="John Cena", duration=4.0)])

        # The streamer candidate is metadata-hard-rejected, so the lane yields
        # nothing and the chain leaves the scene unresolved rather than pushing
        # a wrong reaction clip into the render.
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
