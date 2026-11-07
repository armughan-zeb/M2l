from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock
from concurrent.futures import wait as futures_wait

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from services.web_image import query as web_query  # noqa: E402
from services.web_image.query import _clean_query, analyze_script_entities  # noqa: E402


def test_entity_analysis_assigns_confidence_before_transition_role():
    rows = analyze_script_entities(
        "Train crew operations keep every journey moving.",
        "Train Crew",
        [
            "Train crew operations keep every journey moving.",
            "Then, dispatchers protect the route through the network.",
        ],
    )
    assert len(rows) == 2
    assert all(0.0 <= float(row["confidence"]) <= 1.0 for row in rows)
    assert rows[1]["scene_role"] in {"entity_focus", "transition", "outro"}


def test_clean_query_preserves_hyphenated_person_name_boundaries_and_exclusions():
    assert _clean_query("Archie Harrison Mountbatten-Windsor portrait") == (
        "Archie Harrison Mountbatten Windsor portrait"
    )
    assert _clean_query("Toyota Corolla -wildlife") == "Toyota Corolla -wildlife"


def test_web_query_director_gets_safe_openrouter_timeout_and_retries():
    observed = []
    rows = [{
        "scene_number": 1,
        "search_query": "Berlin Airlift cargo plane",
        "supporting_search_query": "Berlin Airlift supply flight",
        "alt_search_query": "Berlin Airlift archive photo",
        "query_variants": [
            "Berlin Airlift cargo plane",
            "Berlin Airlift supply flight",
            "Berlin Airlift archive photo",
        ],
    }]

    with mock.patch.dict(
        "os.environ",
        {
            "VIDZEO_QUERY_LLM_CALL_TIMEOUT": "120",
            "VIDZEO_QUERY_LLM_MAX_ATTEMPTS": "2",
        },
    ), mock.patch("services.openrouter_llm.OpenRouterLLM"), mock.patch.object(
        web_query, "_chat_query_llm",
        side_effect=lambda _llm, _prompt, _system, **kwargs: observed.append(kwargs) or "{}",
    ), mock.patch.object(
        web_query, "_parse_llm_response", return_value={"queries": [{}]},
    ), mock.patch.object(
        web_query, "_normalize_llm_query_rows", return_value=(rows, []),
    ):
        result = web_query._generate_queries_llm(
            "The Berlin Airlift supplied the city.",
            "Berlin Airlift",
            ["The Berlin Airlift supplied the city."],
            "sk-or-test-key-long-enough-for-validation-123456",
            model="qwen/qwen3.5-flash-02-23",
        )

    assert result == rows
    assert observed[0]["timeout_seconds"] == 120.0
    assert observed[0]["max_attempts"] == 2


def test_parallel_query_model_race_can_use_configured_long_budget():
    observed_waits = []

    def observe_wait(futures, timeout=None, return_when=None):
        observed_waits.append(timeout)
        return futures_wait(futures, timeout=timeout, return_when=return_when)

    with mock.patch.dict(
        "os.environ",
        {"VIDZEO_QUERY_LLM_CANDIDATE_RACE_SECONDS": "480"},
    ), mock.patch.object(
        web_query, "_generate_queries_llm", return_value=[{"scene_number": 1}],
    ), mock.patch(
        "services.openrouter_llm.openrouter_model_available", return_value=True,
    ), mock.patch.object(
        web_query, "wait", side_effect=observe_wait,
    ):
        rows = web_query._generate_queries_with_candidates(
            "A one-scene documentary script.",
            "Test Documentary",
            ["The scene describes the subject."],
            [{
                "provider": "openrouter",
                "api_key": "sk-or-test-key-long-enough-to-pass-local-check-123456",
                "model": "qwen/qwen3.5-flash-02-23",
            }],
        )

    assert rows == [{"scene_number": 1}]
    assert observed_waits and observed_waits[0] > 400.0
