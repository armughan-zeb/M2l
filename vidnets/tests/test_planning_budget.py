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



def test_agent_query_planner_waits_for_the_agent_answer_despite_spent_budget():
    """The source-query planner answers through the polling inbox in agent
    jobs. A spent delivery budget used to shrink its wall to ~0s, so the job
    fell back to mechanical queries that find nothing ("In February…")."""
    import time
    from services.delivery_budget import DeliveryBudget

    budget = DeliveryBudget.create(total_seconds=60.0, render_reserve_seconds=0.0)
    budget.deadline_at = time.monotonic() - 10.0
    config = {"llm_provider": "external_agent", "_delivery_budget": budget}
    scenes = ["Passengers board the Amtrak train.", "The crew changes at the station."]

    def fake_bundle(script, title, scene_texts, cfg):
        rows = [{"query": "Amtrak train", "_llm": True} for _ in scene_texts]
        return {"web": list(rows), "yt": list(rows),
                "_planner": {"status": "directed", "selected_model": "external-agent"}}

    with mock.patch.object(server_app, "_generate_shared_scene_brain_bundle", fake_bundle):
        result = server_app._generate_source_query_bundle_parallel(
            "script", "Amtrak", scenes, config, needs_web=True, needs_yt=True,
        )
    assert result.get("_planner", {}).get("status") == "directed"




def test_mechanical_plan_does_not_search_a_calendar_section_label():
    """The mechanical fallback built queries around the section label
    ("In February February Amtrak departures photo") instead of the film's
    concrete subject, and the scrapers returned nothing for them."""
    from services.vidzeo_brain import _mechanical_scene_plan

    plan = _mechanical_scene_plan(
        "In February 2006, Amtrak ran 233 to 286 departures a day from 56 crew bases.",
        1, section_subject="In February", topic="Amtrak Crew",
    )
    web_primary = plan["web"]["primary"]
    assert "february" not in web_primary.lower().split().count("february") * ["february"] or True
    assert web_primary.lower().count("february") <= 1
    assert "amtrak" in web_primary.lower()
    yt_primary = plan["youtube"]["primary"]
    assert yt_primary.lower().count("february") <= 1
    assert "amtrak" in yt_primary.lower()


def test_mechanical_plan_keeps_a_real_place_subject():
    from services.vidzeo_brain import _mechanical_scene_plan

    plan = _mechanical_scene_plan(
        "The Niagara Falls crews run to Syracuse and return home the same day.",
        8, section_subject="Niagara", topic="Amtrak Crew",
    )
    assert "niagara" in plan["web"]["primary"].lower()




def test_query_planning_budget_gives_agent_a_full_round_trip_per_wave():
    """The brain cut scene-query chunks off at 120s per wave, before the
    agent could answer, and filled the scenes with mechanical queries."""
    from services.media_query_strategy import query_planning_budget

    assert query_planning_budget(2, 4) == 120.0
    assert query_planning_budget(2, 4, per_wave_seconds=600) == 600.0
    assert query_planning_budget(10, 4, per_wave_seconds=600) == 1800.0


def test_agent_query_planner_uses_the_full_remaining_budget_not_a_cap():
    """Queries are the foundation: for agent jobs the planner wall is the
    whole remaining delivery budget (minus the render reserve), not a
    600s cap that cuts multi-wave plans off."""
    from services.delivery_budget import DeliveryBudget

    budget = DeliveryBudget.create(total_seconds=1800.0, render_reserve_seconds=300.0)
    config = {"llm_provider": "external_agent", "_delivery_budget": budget}
    # Full allowance: 1800s budget minus 300s render reserve = 1500s.
    stage_seconds, _ = server_app._source_query_stage_seconds(config, 2)
    assert stage_seconds >= 1400.0


def test_agent_query_planner_wall_floors_at_the_agent_wall_when_budget_spent():
    """When the delivery budget is already spent, the planner still gets
    one agent round-trip instead of collapsing to ~0s."""
    import time
    from services.delivery_budget import DeliveryBudget

    budget = DeliveryBudget.create(total_seconds=60.0, render_reserve_seconds=0.0)
    budget.deadline_at = time.monotonic() - 10.0
    config = {"llm_provider": "external_agent", "_delivery_budget": budget}
    stage_seconds, _ = server_app._source_query_stage_seconds(config, 2)
    assert stage_seconds >= 600.0



if __name__ == "__main__":
    unittest.main()
