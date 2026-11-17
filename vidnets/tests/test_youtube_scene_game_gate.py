"""Scene-relative GTA game gate: proof-job regression tests.

Fixtures are the REAL records of proof job a57cb5c7 (GTA VI 1-min,
checkpoints under
``vidnets-runtime/output/GTA_VI_1Min_20261116_181729_8f06fbb8/``):
scene texts from ``provider_web_candidates.json``, row anchors from
``queries.json``, candidate titles/descriptions from the
``media_plan.json`` candidate ledger and the vision verdicts in
``vidnets-answer-*.json``.

The rule (user correction, Nov 16 2026): relevance is SCENE-relative.
GTA V footage is CORRECT for a scene narrating GTA V (scene 0: sales,
the decade-long wait) and WRONG for scenes about GTA VI content
(trailers, Leonida, Lucia/Jason, new Vice City). The 2002 Vice City
cutscene stays rejected everywhere. Only GTA V is allowed, and only
where the scene calls for it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_pipeline_v2 as ytv2
from services.universal_clip_classifier import (
    _gta_entries,
    _gta_entry,
    detect_niche_conflict,
)

TITLE = "Grand Theft Auto VI: Return to Vice City"

# Real scene texts (provider_web_candidates.json scene_texts).
SCENE_TEXTS = [
    "Grand Theft Auto Six is the most anticipated video game of the decade, and Rockstar Games has kept fans waiting for more than ten years since Grand Theft Auto Five, a game that has sold more than two hundred million copies.",
    "The game returns to Vice City, now set in the fictional state of Leonida, a sun-soaked reimagining of Florida filled with beaches, swamps, and neon streets.",
    "The trailers show the Leonida Keys, the swamps of the Grassrivers, and the neon of Vice City itself, a world far bigger and more detailed than anything Rockstar has built before.",
    "At the center of the story are Lucia Caminos and Jason Duval, two partners in crime whose story begins with Lucia walking out of prison and reuniting with Jason.",
    "The first trailer arrived in December 2023 and broke viewing records within a day, while a second trailer in May 2025 showed more of Leonida and its characters.",
    "Rockstar first promised the game for autumn 2025, then pushed it to May 2026, and later delayed it again to November 19, 2026, saying the extra time is needed to finish it to their standard.",
    "When it arrives, it launches first on PlayStation 5 and Xbox Series X and S, with a PC version yet to be announced.",
    "For Rockstar, Grand Theft Auto Six is not just a sequel, it is the next chapter of the biggest entertainment series in the world.",
]

# Minimal query-row overrides carrying the real anchors (queries.json).
ROW_OVERRIDES = [
    {"scene_subject": "Rockstar Games", "section_subject": "Rockstar Games",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "Rockstar Games",
     "global_topic": "Rockstar Games", "container_type": "entity"},
    {"scene_subject": "Vice City neon streets", "section_subject": "Vice City",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "Vice City",
     "global_topic": "Vice City", "container_type": "entity"},
    {"scene_subject": "Leonida Keys island chain shown", "section_subject": "Leonida Keys",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "Leonida Keys",
     "global_topic": "Leonida Keys", "container_type": "entity"},
    {"scene_subject": "Lucia Caminos walking out of prison",
     "section_subject": "Lucia Caminos and Jason Duval",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "Lucia Caminos and Jason Duval",
     "global_topic": "Lucia Caminos and Jason Duval", "container_type": "entity"},
    {"scene_subject": "Leonida revealed by the record-breaking first",
     "section_subject": "Leonida",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "Leonida",
     "global_topic": "Leonida", "container_type": "entity"},
    {"scene_subject": "Rockstar Games", "section_subject": "Rockstar Games",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "Rockstar Games",
     "global_topic": "Rockstar Games", "container_type": "entity"},
    {"scene_subject": "Grand Theft Auto VI", "section_subject": "video game official trailer gameplay",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "video game official trailer gameplay",
     "global_topic": "video game official trailer gameplay", "container_type": "entity"},
    {"scene_subject": "Grand Theft Auto VI", "section_subject": "Rockstar Games",
     "domain_anchor": "Grand Theft Auto VI", "topic_anchor": "Rockstar Games",
     "global_topic": "Rockstar Games", "container_type": "entity"},
]

# Real ledger records (media_plan.json candidate_ledger).
GTA_V_CLIP = {
    "title": "Grand Theft Auto V: Official Gameplay Video",
    "description": "Grand Theft Auto V is coming September 17, 2013. "
                   "Pre-order now and visit www.rockstargames.com/V for more details.",
    "channel": "Rockstar Games",
    "duration": 300,
}
VICE_CITY_2002 = {
    "title": "GTA VICE CITY Gameplay Walkthrough FULL GAME [4K 60FPS PS5 PRO] - No Commentary",
    "description": "GTA Vice City full game walkthrough",
    "channel": "GameChannel",
    "duration": 1800,
}
# Trailer 2, May 2025, per the vision verdicts (vidnets-answer-*.json).
VI_TRAILER = {
    "title": "Grand Theft Auto VI Trailer 2",
    "description": "Grand Theft Auto VI Trailer 2. Lucia and Jason in Leonida.",
    "channel": "Rockstar Games",
    "duration": 166,
}
SAN_ANDREAS = {
    "title": "Grand Theft Auto: San Andreas - Official Trailer",
    "description": "GTA San Andreas trailer.",
    "channel": "Rockstar Games",
    "duration": 120,
}
FRANCHISE_ROUNDUP = {
    "title": "GTA gameplay moments",
    "description": "Best GTA moments compilation.",
    "channel": "Clips Daily",
    "duration": 600,
}


class SceneGameGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = ytv2._build_scene_rows(TITLE, SCENE_TEXTS, ROW_OVERRIDES)
        # The test premise: live rows carry the VI anchor on every scene.
        for row in cls.rows:
            assert row.get("domain_anchor") == "Grand Theft Auto VI", row

    def _score(self, idx, candidate):
        row = self.rows[idx]
        subject = row.get("candidate_subject") or row.get("section_subject") or row.get("scene_subject") or ""
        ctx = ytv2._row_retrieval_context(row)
        cand = dict(candidate)
        score = ytv2._score_candidate(cand, subject, SCENE_TEXTS[idx], "", ctx)
        return score, cand

    def test_scene_subject_games_from_real_scenes(self):
        self.assertEqual(
            ytv2._scene_subject_games(SCENE_TEXTS[0], "Grand Theft Auto VI",
                                      {"domain_anchor": "Grand Theft Auto VI"}),
            {"vi", "v"},
        )
        for idx in (1, 2, 3, 4, 5, 6, 7):
            row = self.rows[idx]
            subject = row.get("candidate_subject") or ""
            ctx = ytv2._row_retrieval_context(row)
            self.assertEqual(
                ytv2._scene_subject_games(SCENE_TEXTS[idx], subject, ctx),
                {"vi"},
                f"scene {idx}",
            )

    def test_gta_entries_spelled_out_and_bare_vice_city(self):
        self.assertEqual(_gta_entries("Grand Theft Auto Six is coming"), {"vi"})
        self.assertEqual(_gta_entries("Grand Theft Auto Five sold 200M"), {"v"})
        self.assertEqual(_gta_entries("Grand Theft Auto Six ... Grand Theft Auto Five"), {"vi", "v"})
        self.assertEqual(_gta_entries("GTA VICE CITY Gameplay Walkthrough"), {"vice_city"})
        # Bare "Vice City" is the new setting, not the 2002 game.
        self.assertEqual(_gta_entries("The game returns to Vice City, now set in Leonida"), set())
        self.assertEqual(_gta_entry("Grand Theft Auto Six"), "vi")
        self.assertEqual(_gta_entry("Grand Theft Auto Five"), "v")

    def test_gta_v_allowed_for_gta_v_history_scene(self):
        score, cand = self._score(0, GTA_V_CLIP)
        self.assertFalse(
            ytv2._gaming_exact_title_gate(dict(GTA_V_CLIP), "Grand Theft Auto VI",
                                          SCENE_TEXTS[0],
                                          ytv2._row_retrieval_context(self.rows[0])))
        self.assertGreater(score, 0, f"scene 0 GTA V clip must be rankable, got {score}")
        self.assertEqual(cand.get("scene_subject_games"), ["v", "vi"])
        self.assertEqual(cand.get("candidate_games"), ["v"])

    def test_gta_v_blocked_before_download_for_vi_content_scenes(self):
        for idx in (1, 2, 3, 4, 5, 6, 7):
            row = self.rows[idx]
            subject = row.get("candidate_subject") or ""
            ctx = ytv2._row_retrieval_context(row)
            self.assertTrue(
                ytv2._gaming_exact_title_gate(dict(GTA_V_CLIP), subject, SCENE_TEXTS[idx], ctx),
                f"scene {idx}: GTA V clip must be gated before download",
            )
            score, cand = self._score(idx, GTA_V_CLIP)
            self.assertEqual(score, -999.0, f"scene {idx}")
            self.assertEqual(cand.get("v2_reject_reason"), "gaming_exact_title_missing", f"scene {idx}")

    def test_2002_vice_city_blocked_for_all_real_scenes(self):
        for idx in range(8):
            row = self.rows[idx]
            subject = row.get("candidate_subject") or ""
            ctx = ytv2._row_retrieval_context(row)
            self.assertTrue(
                ytv2._gaming_exact_title_gate(dict(VICE_CITY_2002), subject, SCENE_TEXTS[idx], ctx),
                f"scene {idx}: 2002 Vice City must stay rejected",
            )
            score, _ = self._score(idx, VICE_CITY_2002)
            self.assertEqual(score, -999.0, f"scene {idx}")

    def test_san_andreas_and_franchise_roundup_blocked_for_vi_scenes(self):
        score, cand = self._score(1, SAN_ANDREAS)
        self.assertEqual(score, -999.0)
        self.assertEqual(cand.get("v2_reject_reason"), "gaming_exact_title_missing")
        score, cand = self._score(1, FRANCHISE_ROUNDUP)
        self.assertEqual(score, -999.0)
        self.assertEqual(cand.get("v2_reject_reason"), "gaming_exact_title_missing")

    def test_vi_trailer_passes_all_real_scenes(self):
        # Scene 3 (Lucia/Jason) previously failed with a false-positive
        # niche_conflict even for the correct trailer.
        for idx in range(8):
            score, cand = self._score(idx, VI_TRAILER)
            self.assertGreater(score, 0, f"scene {idx}: VI trailer must pass, got {score} ({cand.get('v2_reject_reason')})")

    def test_ranking_is_scene_subject_first(self):
        v_scene0, _ = self._score(0, GTA_V_CLIP)
        vc_scene0, _ = self._score(0, VICE_CITY_2002)
        trailer_scene0, _ = self._score(0, VI_TRAILER)
        self.assertGreater(v_scene0, vc_scene0)
        # Scene 0 narrates GTA V: its footage ranks first, above the VI trailer.
        self.assertGreater(v_scene0, trailer_scene0)
        trailer_scene1, _ = self._score(1, VI_TRAILER)
        v_scene1, _ = self._score(1, GTA_V_CLIP)
        self.assertGreater(trailer_scene1, 0)
        self.assertEqual(v_scene1, -999.0)

    def test_pure_v_scene_allows_v_and_blocks_vi(self):
        # Wording mirrors scene 0 ("video game" in the copy): a gameplay
        # clip for a scene that never says it is about a game is rejected
        # by the fictional-footage check, not by the game gate.
        scene = "Grand Theft Auto Five, the video game, sold more than two hundred million copies."
        ctx = {"domain_anchor": "Grand Theft Auto V", "container_subject": "Grand Theft Auto V"}
        self.assertFalse(ytv2._gaming_exact_title_gate(dict(GTA_V_CLIP), "Grand Theft Auto V", scene, ctx))
        self.assertTrue(ytv2._gaming_exact_title_gate(dict(VI_TRAILER), "Grand Theft Auto V", scene, ctx))
        score, _ = self._score_pure(scene, GTA_V_CLIP, ctx)
        self.assertGreater(score, 0)

    def _score_pure(self, scene, candidate, ctx):
        cand = dict(candidate)
        score = ytv2._score_candidate(cand, "Grand Theft Auto V", scene, "", ctx)
        return score, cand

    def test_niche_conflict_is_scene_relative(self):
        # Lucia/Jason scene: the correct VI trailer is not a conflict.
        conflict, _, _ = detect_niche_conflict(
            f"Grand Theft Auto VI {SCENE_TEXTS[3]}",
            f"{VI_TRAILER['title']} {VI_TRAILER['description']}",
            scene_entries={"vi"},
        )
        self.assertFalse(conflict)
        # Leonida scene: the GTA V clip IS a conflict.
        conflict, _, _ = detect_niche_conflict(
            f"Grand Theft Auto VI {SCENE_TEXTS[1]}",
            f"{GTA_V_CLIP['title']} {GTA_V_CLIP['description']}",
            scene_entries={"vi"},
        )
        self.assertTrue(conflict)
        # GTA V-history scene: the GTA V clip is not a conflict.
        conflict, _, _ = detect_niche_conflict(
            f"Grand Theft Auto VI {SCENE_TEXTS[0]}",
            f"{GTA_V_CLIP['title']} {GTA_V_CLIP['description']}",
            scene_entries={"vi", "v"},
        )
        self.assertFalse(conflict)
        # Without scene_entries the old behaviour is unchanged: a V clip
        # offered for a VI scene still conflicts.
        conflict, _, _ = detect_niche_conflict(
            f"Grand Theft Auto VI {SCENE_TEXTS[1]}",
            f"{GTA_V_CLIP['title']} {GTA_V_CLIP['description']}",
        )
        self.assertTrue(conflict)


if __name__ == "__main__":
    unittest.main()


# --- Proof-job ratio ledger (real media_plan.json resolutions) -----------
# provider / media_type / status / resolution_kind / candidate_id /
# fallback_reason, copied verbatim from the proof job's media_plan.json.
# Scene 5 is the one accepted YouTube frame (_yl5ELcmugI); scenes 2 and 6
# are graphics with reason all_verified_real_media_exhausted.
PROOF_JOB_RESOLUTIONS = [
    {"provider": "web", "media_type": "image", "status": "accepted",
     "resolution_kind": "pre_stock_web_rescue", "candidate_id": "web:scn_920430008982465c3dae",
     "fallback_reason": "web_image_rescued_before_stock_fallback", "selected_path": "/x/scene_0001.jpg"},
    {"provider": "web", "media_type": "image", "status": "accepted",
     "resolution_kind": "pre_stock_web_rescue", "candidate_id": "web:scn_9d3c414661a07bf7f74b",
     "fallback_reason": "web_image_rescued_before_stock_fallback", "selected_path": "/x/scene_0002.jpg"},
    {"provider": "graphic", "media_type": "image", "status": "degraded",
     "resolution_kind": "grounded_graphic_fallback", "candidate_id": "graphic:fallback:2",
     "fallback_reason": "all_verified_real_media_exhausted", "selected_path": "/x/graphic_2.png"},
    {"provider": "web", "media_type": "image", "status": "accepted",
     "resolution_kind": "pre_stock_web_rescue", "candidate_id": "web:scn_9d3c414661a07bf7f74b:alt1",
     "fallback_reason": "web_image_rescued_before_stock_fallback", "selected_path": "/x/scene_0001.jpg"},
    {"provider": "web", "media_type": "image", "status": "accepted",
     "resolution_kind": "pre_stock_web_rescue", "candidate_id": "web:scn_920430008982465c3dae",
     "fallback_reason": "web_image_rescued_before_stock_fallback", "selected_path": "/x/scene_0001.jpg"},
    {"provider": "youtube", "media_type": "image", "status": "accepted",
     "resolution_kind": "youtube_extracted_frame", "candidate_id": "youtube:_yl5ELcmugI:frame:2",
     "fallback_reason": "same_video_timestamp_frame", "selected_path": "/x/frame_01.jpg"},
    {"provider": "graphic", "media_type": "image", "status": "degraded",
     "resolution_kind": "grounded_graphic_fallback", "candidate_id": "graphic:fallback:6",
     "fallback_reason": "all_verified_real_media_exhausted", "selected_path": "/x/graphic_6.png"},
    {"provider": "web", "media_type": "image", "status": "accepted",
     "resolution_kind": "pre_stock_web_rescue", "candidate_id": "web:scn_9d3c414661a07bf7f74b:alt1",
     "fallback_reason": "web_image_rescued_before_stock_fallback", "selected_path": "/x/scene_0001.jpg"},
]


class RatioLedgerTests(unittest.TestCase):
    def _resolutions(self, rows):
        from services.media_resolution_coordinator import SceneResolution
        out = {}
        for idx, row in enumerate(rows):
            out[idx] = SceneResolution(
                scene_index=idx,
                provider=row["provider"],
                selected_path=row["selected_path"],
                media_type=row["media_type"],
                status=row["status"],
                resolution_kind=row["resolution_kind"],
                candidate_id=row["candidate_id"],
                fallback_reason=row["fallback_reason"],
            )
        return out

    def test_proof_job_ledger_counts_real_youtube_frame(self):
        """The proof ledger said 0/8 while the video held 1/8 real YT."""
        from services.pipeline_v2_media_adapter import (
            _real_youtube_scene_indices,
            _youtube_quota_coverage_indices,
        )
        resolutions = self._resolutions(PROOF_JOB_RESOLUTIONS)
        accepted, missing = _youtube_quota_coverage_indices(list(range(8)), resolutions)
        # Moving quota: still 0 — the only YT media is an extracted frame.
        self.assertEqual(accepted, [])
        self.assertEqual(missing, list(range(8)))
        real_indices, details = _real_youtube_scene_indices(resolutions)
        self.assertEqual(real_indices, [5])
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["candidate_id"], "youtube:_yl5ELcmugI:frame:2")
        self.assertFalse(details[0]["moving_footage"])

    def test_real_youtube_counts_degraded_clip_and_frame(self):
        from services.media_resolution_coordinator import SceneResolution
        from services.pipeline_v2_media_adapter import (
            _real_youtube_scene_indices,
            _youtube_quota_coverage_indices,
        )
        resolutions = {
            0: SceneResolution(scene_index=0, provider="youtube", selected_path="/x/a.mp4",
                               media_type="video", status="degraded"),
            3: SceneResolution(scene_index=3, provider="youtube", selected_path="/x/b.jpg",
                               media_type="image", status="accepted",
                               resolution_kind="youtube_extracted_frame"),
            5: SceneResolution(scene_index=5, provider="youtube", selected_path="/x/c.mp4",
                               media_type="video", status="accepted"),
        }
        moving, _ = _youtube_quota_coverage_indices([0, 3, 5], resolutions)
        self.assertEqual(moving, [0, 5])  # frame is a still, never moving quota
        real, details = _real_youtube_scene_indices(resolutions)
        self.assertEqual(real, [0, 3, 5])
        self.assertEqual([d["moving_footage"] for d in details], [True, False, True])

    def test_gaming_target_ratio_reads_stamped_target(self):
        from services.pipeline_v2_media_adapter import _mixed_media_youtube_target_ratio
        self.assertEqual(
            _mixed_media_youtube_target_ratio(
                {"_gaming_mode": True, "_gaming_youtube_target": 0.90}), 0.90)
        self.assertEqual(
            _mixed_media_youtube_target_ratio({"_gaming_mode": True}), 0.90)
        # An explicit per-job coverage key still wins over gaming mode.
        self.assertEqual(
            _mixed_media_youtube_target_ratio(
                {"_gaming_mode": True, "youtube_ratio": 0.5}), 0.5)

    def test_gaming_policy_stamps_target(self):
        import app as server_app
        config = {}
        policy = {"niche": "gaming",
                  "weights": {"YouTube Clips": 0.8, "Web Images": 0.15, "Stock Media": 0.05},
                  "retrieval_policy": {}}
        server_app._apply_gaming_mode_policy(
            config, policy, TITLE,
            "Grand Theft Auto Six is the most anticipated video game of the decade.",
            SCENE_TEXTS)
        self.assertTrue(config["_gaming_mode"])
        self.assertEqual(config["_gaming_youtube_target"], 0.90)

    def test_planner_gaming_detector_spelled_out(self):
        from services.mixed_media_planner import (
            GAMING_YOUTUBE_TARGET_RATIO,
            _gta_vi_story,
            gaming_mode_for,
        )
        self.assertEqual(GAMING_YOUTUBE_TARGET_RATIO, 0.90)
        self.assertTrue(_gta_vi_story(
            "Grand Theft Auto Six: Return to Vice City",
            "Grand Theft Auto Six is the most anticipated video game.",
            SCENE_TEXTS))
        self.assertTrue(gaming_mode_for(TITLE, SCENE_TEXTS[0], SCENE_TEXTS))


class FrameFallbackFilterTests(unittest.TestCase):
    """The coordinator must not mint frames from a wrong-game download."""

    def _clip(self, tmp_path, name, candidate, final_score):
        path = tmp_path / name
        path.write_bytes(b"fake clip bytes")
        return {**candidate, "path": str(path), "selected_path": str(path),
                "source": "youtube", "provider": "youtube", "final_score": final_score}

    def test_wrong_game_clip_yields_no_frames_for_vi_scene(self):
        import tempfile
        from pathlib import Path
        from services.media_resolution_coordinator import _youtube_frame_fallback_candidates
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            frame = tmp_path / "frame_00.jpg"
            frame.write_bytes(b"fake frame")
            v_clip = self._clip(tmp_path, "gta-v.mp4", GTA_V_CLIP, 100.0)
            v_clip["candidate_id"] = "youtube:N-xHcvug3WI"
            trailer = self._clip(tmp_path, "gta-vi.mp4", VI_TRAILER, 50.0)
            trailer["candidate_id"] = "youtube:_yl5ELcmugI"
            scene = {"text": SCENE_TEXTS[1], "visual_intent": "documentary_context",
                     "positive_subjects": ["Vice City"], "content_title": TITLE}
            with mock.patch(
                "services.visual_intelligence.frame_extractor.extract_clip_frames",
                return_value=([str(frame)], 120.0),
            ) as extractor:
                out = _youtube_frame_fallback_candidates(scene, [v_clip, trailer], 1)
            # The wrong-game clip had the higher final_score (100, as in the
            # proof job) and still yields nothing; only the trailer frames.
            self.assertEqual(extractor.call_count, 1)
            self.assertTrue(all("N-xHcvug3WI" not in str(c.get("candidate_id", "")) for c in out))
            self.assertTrue(any("Trailer 2" in str(c.get("title", "")) for c in out))

    def test_gta_v_clip_yields_frames_for_gta_v_history_scene(self):
        import tempfile
        from pathlib import Path
        from services.media_resolution_coordinator import _youtube_frame_fallback_candidates
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            frame = tmp_path / "frame_00.jpg"
            frame.write_bytes(b"fake frame")
            v_clip = self._clip(tmp_path, "gta-v.mp4", GTA_V_CLIP, 100.0)
            v_clip["candidate_id"] = "youtube:N-xHcvug3WI"
            scene = {"text": SCENE_TEXTS[0], "visual_intent": "documentary_context",
                     "positive_subjects": ["Rockstar Games"], "content_title": TITLE}
            with mock.patch(
                "services.visual_intelligence.frame_extractor.extract_clip_frames",
                return_value=([str(frame)], 120.0),
            ) as extractor:
                out = _youtube_frame_fallback_candidates(scene, [v_clip], 0)
            self.assertEqual(extractor.call_count, 1)
            self.assertEqual(len(out), 1)
            self.assertIn("N-xHcvug3WI", out[0]["candidate_id"])
