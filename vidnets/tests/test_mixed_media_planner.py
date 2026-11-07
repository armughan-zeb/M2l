import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import mixed_media_planner as planner


class MixedMediaPlannerTests(unittest.TestCase):
    def test_automatic_mixed_media_does_not_require_weights(self):
        weights = planner.normalize_mixed_media_weights(strict=True)
        self.assertAlmostEqual(weights["YouTube Clips"], 0.55, places=4)
        self.assertEqual(weights["Stock Media"], 0.20)
        self.assertEqual(weights["Web Images"], 0.25)

    def test_automatic_profiles_are_adaptive_and_preserve_stock(self):
        for topic in planner._NICHE_MEDIA_PROFILES:
            weights = planner._automatic_mode_weights(topic)
            self.assertAlmostEqual(sum(weights.values()), 1.0)
            self.assertTrue(all(0 <= value <= 1 for value in weights.values()))
            self.assertEqual(weights["AI Images"], 0)
        self.assertGreater(planner._automatic_mode_weights("travel")["Stock Media"], .2)
        self.assertGreater(planner._automatic_mode_weights("sports")["YouTube Clips"], .6)
        self.assertNotEqual(planner._automatic_mode_weights("history"), planner._automatic_mode_weights("crime"))

    def test_strict_weights_reject_zero_sum(self):
        with self.assertRaisesRegex(ValueError, "at least one enabled media source"):
            planner.normalize_mixed_media_weights(
                {"Web Images": 0, "Stock Media": 0, "AI Images": 0},
                strict=True,
            )

    def test_normalize_weights_sums_to_one(self):
        weights = planner.normalize_mixed_media_weights(
            {"Web Images": 50, "Stock Media": 30, "AI Images": 20}
        )
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)
        self.assertGreater(weights["Web Images"], weights["AI Images"])

    def test_adaptive_plan_stays_close_to_targets(self):
        scenes = [
            "Donald Trump walks to a rally stage",
            "Cargo ships line up at the port",
            "Drone view of Shanghai skyline",
            "Factory workers inspect machinery",
            "President signs trade order at meeting",
            "Street view of Beijing at night",
            "Warehouse shelves stacked with goods",
            "Officials gather for summit photo",
            "Aerial view of city harbor",
            "Trump arrives for press event",
        ]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 0.5, "Stock Media": 0.3, "AI Images": 0.2},
            title="Video",
            script="Trump tariffs on China affect supply chains, ports, and factories.",
        )

        self.assertEqual(sum(plan.final_counts.values()), len(scenes))
        self.assertTrue(all(count >= 0 for count in plan.final_counts.values()))
        self.assertGreaterEqual(plan.final_counts["Web Images"], 3)
        self.assertGreaterEqual(plan.final_counts["AI Images"], 1)

    def test_largest_remainder_plan_respects_exact_ui_split(self):
        scenes = [f"Scene {idx}" for idx in range(7)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 65, "Stock Media": 35, "AI Images": 0},
            title="Video",
            script="Script",
            strict_weights=True,
        )

        self.assertEqual(plan.final_counts["Web Images"], 5)
        self.assertEqual(plan.final_counts["Stock Media"], 2)
        self.assertEqual(plan.final_counts["AI Images"], 0)
        self.assertEqual(sum(plan.final_counts.values()), len(scenes))

    def test_video_sources_get_early_hook_slots_and_break_web_runs(self):
        scenes = [f"Virat Kohli scene {idx}" for idx in range(14)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 50, "YouTube Clips": 30, "Stock Media": 20, "AI Images": 0},
            title="Virat Kohli innings",
            script="Virat Kohli 82 not out against Australia.",
            strict_weights=True,
        )

        sequence = [decision.assigned_source for decision in plan.decisions]
        self.assertTrue(all(source in {"YouTube Clips", "Stock Media"} for source in sequence[:3]))
        self.assertGreaterEqual(sum(1 for source in sequence[:4] if source in {"YouTube Clips", "Stock Media"}), 4)
        self.assertEqual(plan.final_counts["Web Images"], 7)
        self.assertEqual(plan.final_counts["YouTube Clips"], 4)
        self.assertEqual(plan.final_counts["Stock Media"], 3)
        self.assertFalse(any(sequence[i] == sequence[i - 1] == sequence[i - 2] for i in range(2, len(sequence))))

    def test_short_mixed_plan_prioritizes_video_intro_without_changing_counts(self):
        scenes = [f"Virat Kohli match beat {idx}" for idx in range(7)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 34, "YouTube Clips": 27, "Stock Media": 39, "AI Images": 0},
            title="Virat Kohli masterclass",
            script="Virat Kohli innings with match footage, stock stadium b-roll, and web images.",
            strict_weights=True,
        )

        sequence = [decision.assigned_source for decision in plan.decisions]
        video_sources = {"YouTube Clips", "Stock Media"}
        self.assertTrue(all(source in video_sources for source in sequence[:4]))
        self.assertEqual(plan.final_counts["Web Images"], 2)
        self.assertEqual(plan.final_counts["YouTube Clips"], 2)
        self.assertEqual(plan.final_counts["Stock Media"], 3)
        self.assertEqual(sum(plan.final_counts.values()), 7)

    def test_large_mixed_plan_caps_youtube_before_branch_launch(self):
        old_cap = os.environ.get("VIDZEO_MIXED_YT_SCENE_CAP")
        try:
            os.environ["VIDZEO_MIXED_YT_SCENE_CAP"] = "24"
            scenes = [f"Virat Kohli scene {idx}" for idx in range(69)]
            plan = planner.build_adaptive_mixed_media_plan(
                scenes,
                weights={"Web Images": 42, "YouTube Clips": 58, "Stock Media": 0, "AI Images": 0},
                title="Virat Kohli innings",
                script="Virat Kohli 82 not out against Australia.",
                strict_weights=True,
            )
        finally:
            if old_cap is None:
                os.environ.pop("VIDZEO_MIXED_YT_SCENE_CAP", None)
            else:
                os.environ["VIDZEO_MIXED_YT_SCENE_CAP"] = old_cap

        self.assertEqual(plan.final_counts["YouTube Clips"], 24)
        self.assertEqual(plan.final_counts["Web Images"], 45)
        self.assertEqual(sum(plan.final_counts.values()), 69)

    def test_titanic_history_uses_verified_youtube_at_the_automatic_floor(self):
        scenes = [f"Titanic history scene {idx}" for idx in range(10)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            title="Titanic History Documentary",
            script="A serious documentary about the history of the Titanic and its sinking.",
        )

        youtube_count = plan.final_counts["YouTube Clips"]
        self.assertEqual(plan.topic_policy, "history")
        self.assertEqual(plan.final_counts["Stock Media"], 1)
        self.assertEqual(plan.final_counts["Web Images"], 5)
        self.assertAlmostEqual(youtube_count / len(scenes), 0.40)
        self.assertTrue(all(
            "Web Images" in decision.fallback_sources
            for decision in plan.decisions
            if decision.assigned_source == "YouTube Clips"
        ))

    def test_verified_cookie_policy_can_reserve_a_majority_for_relevant_video(self):
        scenes = [f"Apollo mission documentary scene {idx}" for idx in range(10)]
        boosted = planner.authenticated_youtube_mix({
            "Web Images": 0.80,
            "AI Images": 0.0,
            "Stock Media": 0.0,
            "YouTube Clips": 0.20,
        })
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            title="Apollo Mission History",
            script="A documentary about the Apollo mission.",
            media_policy={
                "niche": "serious",
                "weights": boosted,
                "youtube_session_verified": True,
            },
        )

        self.assertAlmostEqual(plan.requested_weights["YouTube Clips"], 0.60)
        self.assertEqual(plan.final_counts["YouTube Clips"], 6)
        self.assertEqual(plan.final_counts["Web Images"], 4)

    def test_automatic_history_uses_evidence_and_motion_preferences(self):
        scenes = [f"Titanic sinking scene {idx}" for idx in range(20)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            title="Titanic documentary",
            script="Historical documentary about the Titanic disaster.",
            scene_media_mode="automatic",
        )

        self.assertEqual(plan.requested_weights["Web Images"], 0.45)
        self.assertEqual(plan.requested_weights["YouTube Clips"], 0.45)
        self.assertGreater(plan.final_counts["YouTube Clips"], 4)
        self.assertEqual(sum(plan.final_counts.values()), len(scenes))

    def test_automatic_context_can_use_planned_stock(self):
        plan = planner.build_adaptive_mixed_media_plan(
            ["Factory workers inspect machinery", "Drone view of a river", "Clouds drift above the hills"],
            title="Travel", script="A travel itinerary across the landscape.", scene_media_mode="auto")
        self.assertGreater(plan.requested_weights["Stock Media"], 0)
        self.assertGreater(plan.final_counts["Stock Media"], 0)

    def test_explicit_single_source_weights_remain_supported(self):
        plan = planner.build_adaptive_mixed_media_plan(
            ["A web scene", "Another web scene"],
            weights={"Web Images": 1, "Stock Media": 0, "YouTube Clips": 0},
            title="Titanic History",
            script="A history documentary.",
            strict_weights=True,
        )
        self.assertEqual(plan.final_counts["Web Images"], 2)
        self.assertEqual(plan.final_counts["YouTube Clips"], 0)
        self.assertEqual(plan.final_counts["Stock Media"], 0)

    def test_script_policy_uses_mostly_youtube_footage_for_gaming(self):
        policy = planner.resolve_media_policy(
            title="GTA 6 trailer breakdown",
            script="GTA 6 gameplay, Rockstar trailers, and open world missions.",
        )
        self.assertEqual(policy["niche"], "gaming")
        self.assertTrue(policy["gta_vi"])
        self.assertEqual(
            policy["gta_policy"],
            "gta_vi_exact_title_official_first; prior_title_only_for_scripted_scene_comparison",
        )
        self.assertEqual(policy["weights"]["YouTube Clips"], 0.80)
        self.assertEqual(policy["weights"]["Web Images"], 0.15)
        self.assertEqual(policy["weights"]["Stock Media"], 0.05)
        plan = planner.build_adaptive_mixed_media_plan(
            ["GTA 6 scene"] * 10,
            title="GTA 6 trailer breakdown",
            script="GTA 6 gameplay, Rockstar trailers, and open world missions.",
            scene_media_mode="automatic",
            media_policy=policy,
        )
        self.assertEqual(plan.final_counts["YouTube Clips"], 8)
        self.assertEqual(plan.final_counts["Web Images"], 2)

    def test_single_clip_is_frontloaded_to_scene_zero(self):
        scenes = [f"Scene {idx}" for idx in range(4)]
        plan = planner.build_adaptive_mixed_media_plan(
            scenes,
            weights={"Web Images": 75, "YouTube Clips": 25, "Stock Media": 0, "AI Images": 0},
            title="Ancient History",
            script="Ancient history overview.",
            strict_weights=True,
        )
        self.assertEqual(plan.final_counts["YouTube Clips"], 1)
        self.assertEqual(plan.final_counts["Web Images"], 3)
        self.assertEqual(plan.decisions[0].assigned_source, "YouTube Clips")

    def test_llm_niche_is_used_only_when_evidence_supports_it(self):
        policy = planner.resolve_media_policy(
            title="A story",
            script="The game walkthrough shows a boss fight and gameplay mechanics.",
            llm_analysis={
                "niche": "gaming",
                "confidence": 0.96,
                "evidence_terms": ["game walkthrough", "boss fight"],
                "visual_strategy": "Prioritize clean gameplay clips.",
            },
        )
        self.assertEqual(policy["niche"], "gaming")
        self.assertTrue(policy["llm_supported"])
        self.assertEqual(policy["weights"]["YouTube Clips"], 0.80)

    def test_crime_and_history_have_distinct_evidence_led_profiles(self):
        crime = planner.resolve_media_policy(
            title="Crime investigation",
            script="Police investigation, forensic evidence, and a murder trial.",
        )
        history = planner.resolve_media_policy(
            title="History documentary",
            script="A historical documentary about an ancient battle and its archives.",
        )
        self.assertEqual(crime["niche"], "crime")
        self.assertAlmostEqual(crime["weights"]["YouTube Clips"], 0.55, places=4)
        self.assertEqual(history["niche"], "history")
        self.assertAlmostEqual(history["weights"]["Web Images"], 0.45, places=4)


if __name__ == "__main__":
    unittest.main()
