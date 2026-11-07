import os
import json
import re
import sys
import threading
import time
import unittest
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app
from services.groq_brain_client import GroqBrainClient
from services.vidzeo_brain import VidzeoBrain


class PlanningBudgetTests(unittest.TestCase):
    def test_legacy_planner_override_is_bounded_by_film_scene_count(self):
        with mock.patch.dict(os.environ, {
            "VIDZEO_MIXED_QUERY_PLAN_DEADLINE_SECONDS": "1500",
            "VIDZEO_QUERY_PLAN_STAGE_DEADLINE_SECONDS": "1500",
        }):
            short_budget = server_app._default_source_query_stage_seconds(8)
            long_budget = server_app._default_source_query_stage_seconds(120)
        self.assertEqual(short_budget, 90.0)
        self.assertGreater(long_budget, short_budget)
        self.assertLessEqual(long_budget, 900.0)

    @staticmethod
    def _long_scenes(count=74):
        return [
            f"RMS Titanic scene {index + 1} describes the ship and passengers during the voyage."
            for index in range(count)
        ]

    def test_container_map_fills_all_74_rows_when_every_llm_chunk_fails(self):
        scenes = self._long_scenes()
        brain = VidzeoBrain("test-key")
        started = time.monotonic()
        with mock.patch.dict(os.environ, {
            "VIDZEO_BRAIN_CONTAINER_STAGE_SECONDS": "1",
            "VIDZEO_BRAIN_CONTAINER_REQUEST_BUDGET_SECONDS": "1",
            "VIDZEO_BRAIN_CONTAINER_CHUNK_SIZE": "18",
            "VIDZEO_BRAIN_CONTAINER_WORKERS": "6",
        }, clear=False), mock.patch.object(
            brain._client,
            "chat",
            side_effect=TimeoutError("provider stalled"),
        ) as chat:
            result = brain.plan_containers("RMS Titanic", " ".join(scenes), scenes)

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(result["containers"]), 74)
        self.assertEqual([row["scene_number"] for row in result["containers"]], list(range(1, 75)))
        self.assertTrue(all(row.get("container_subject") for row in result["containers"]))
        self.assertLessEqual(chat.call_count, 5)

    def test_container_map_keeps_successful_parallel_llm_chunks(self):
        scenes = self._long_scenes()
        brain = VidzeoBrain("test-key")

        def complete_chunk(*_args, **kwargs):
            count = len(re.findall(r"^Scene \d+:", kwargs["user_prompt"], flags=re.MULTILINE))
            return json.dumps({
                "is_listicle": False,
                "containers": [
                    {
                        "scene_number": index + 1,
                        "container_id": "rms_titanic",
                        "container_subject": "RMS Titanic",
                        "scene_subject": "RMS Titanic",
                        "container_boundary": index == 0,
                    }
                    for index in range(count)
                ],
            })

        with mock.patch.dict(os.environ, {
            "VIDZEO_BRAIN_CONTAINER_STAGE_SECONDS": "2",
            "VIDZEO_BRAIN_CONTAINER_CHUNK_SIZE": "18",
            "VIDZEO_BRAIN_CONTAINER_WORKERS": "6",
        }, clear=False), mock.patch.object(brain._client, "chat", side_effect=complete_chunk) as chat:
            result = brain.plan_containers("RMS Titanic", " ".join(scenes), scenes)

        self.assertEqual(len(result["containers"]), 74)
        self.assertEqual(chat.call_count, 5)
        self.assertTrue(all(row.get("_llm_container") for row in result["containers"]))
        self.assertFalse(any(row.get("_mechanical_container") for row in result["containers"]))

    def test_source_query_stage_times_out_to_complete_mechanical_bundle(self):
        scenes = self._long_scenes()
        entered = threading.Event()
        release = threading.Event()

        def hung_shared(*_args, **_kwargs):
            entered.set()
            release.wait(5.0)
            return None

        started = time.monotonic()
        try:
            with mock.patch.object(
                server_app,
                "_generate_shared_scene_brain_bundle",
                side_effect=hung_shared,
            ):
                bundle = server_app._generate_source_query_bundle_parallel(
                    " ".join(scenes),
                    "RMS Titanic",
                    scenes,
                    {"query_plan_stage_deadline_seconds": 0.10},
                    needs_web=True,
                    needs_yt=True,
                    needs_stock=False,
                )
        finally:
            release.set()

        self.assertTrue(entered.is_set())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(bundle["web"]), 74)
        self.assertEqual(len(bundle["yt"]), 74)
        self.assertTrue(all(row.get("_mechanical_query_plan") for row in bundle["web"]))
        self.assertTrue(all(row.get("search_query") for row in bundle["web"]))
        self.assertTrue(all(row.get("youtube_query") for row in bundle["yt"]))

    def test_fast_lane_keeps_openrouter_planner_parallel_with_a_45_second_budget(self):
        scenes = self._long_scenes(4)
        entered = threading.Event()
        observed = {}

        def inspect_deadline(_script, _title, _scene_texts, config):
            observed["seconds"] = config["_query_plan_deadline_monotonic"] - time.monotonic()
            entered.set()
            return None

        with mock.patch.dict(os.environ, {
            "VIDZEO_MIXED_QUERY_PLAN_BACKGROUND_SECONDS": "",
            "VIDZEO_MIXED_QUERY_PLAN_ASYNC": "1",
        }, clear=False), mock.patch.object(
            server_app,
            "_generate_shared_scene_brain_bundle",
            side_effect=inspect_deadline,
        ):
            bundle = server_app._generate_source_query_bundle_parallel(
                " ".join(scenes),
                "RMS Titanic",
                scenes,
                {
                    "_query_plan_fast_lane": True,
                    "query_plan_stage_deadline_seconds": 60,
                },
                needs_web=True,
                needs_yt=True,
            )
            self.assertTrue(entered.wait(1.0))

        self.assertGreaterEqual(observed["seconds"], 43.0)
        self.assertLessEqual(observed["seconds"], 45.0)
        self.assertTrue(all(row.get("_mechanical_query_plan") for row in bundle["web"]))
        self.assertTrue(all(row.get("_mechanical_query_plan") for row in bundle["yt"]))

    def test_mixed_query_default_scales_for_long_scene_plans(self):
        with mock.patch.dict(
            os.environ,
            {"VIDZEO_MIXED_QUERY_PLAN_DEADLINE_SECONDS": ""},
            clear=False,
        ):
            self.assertEqual(
                server_app._default_source_query_stage_seconds(8, fast_lane=True),
                20.0,
            )
            self.assertGreaterEqual(
                server_app._default_source_query_stage_seconds(23, fast_lane=True),
                33.0,
            )
            self.assertLessEqual(
                server_app._default_source_query_stage_seconds(100, fast_lane=True),
                45.0,
            )

    def test_source_query_stage_preserves_completed_rows_and_fills_only_gaps(self):
        scenes = self._long_scenes(3)
        completed = {
            "scene_number": 1,
            "search_query": "LLM completed row",
            "_llm": True,
        }
        with mock.patch.object(
            server_app,
            "_generate_shared_scene_brain_bundle",
            return_value={"web": [completed], "yt": [], "stock": []},
        ):
            bundle = server_app._generate_source_query_bundle_parallel(
                " ".join(scenes),
                "RMS Titanic",
                scenes,
                {"query_plan_stage_deadline_seconds": 1},
                needs_web=True,
            )

        self.assertIs(bundle["web"][0], completed)
        self.assertEqual(len(bundle["web"]), 3)
        self.assertTrue(bundle["web"][1]["_mechanical_query_plan"])
        self.assertTrue(bundle["web"][2]["_mechanical_query_plan"])

    def test_date_led_titanic_fallback_promotes_named_vessel_globally(self):
        scenes = [
            "On April 10 the RMS Titanic departed Southampton for New York.",
            "The ship carried more than two thousand passengers and crew.",
            "An iceberg damaged several compartments below the waterline.",
            "Titanic sank in the freezing North Atlantic.",
        ]
        bundle = server_app._generate_mechanical_shared_scene_bundle(
            " ".join(scenes),
            "On April 15, 1912, one of",
            scenes,
        )

        for row in bundle["web"] + bundle["yt"]:
            self.assertEqual(row["domain_anchor"], "RMS Titanic")
            self.assertEqual(row["global_topic"], "RMS Titanic")
        self.assertTrue(all(row["title_anchor"] == "RMS Titanic" for row in bundle["yt"]))

    @mock.patch("services.groq_brain_client.requests.post")
    def test_groq_explicit_single_attempt_does_not_expand_into_retry_chain(self, post):
        response = mock.Mock(status_code=429)
        post.return_value = response

        with self.assertRaises(ValueError):
            GroqBrainClient("test-key").chat(
                "Return JSON",
                json_mode=True,
                timeout_seconds=1,
                total_timeout_seconds=1,
                max_attempts=1,
            )

        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
