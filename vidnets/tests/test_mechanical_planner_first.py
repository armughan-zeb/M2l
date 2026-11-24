"""Mechanical-first planner: rows pass BOTH validators on real scripts.

The fixtures are the REAL scripts of jobs a57cb5c7 (GTA VI, 8 scenes) and
89b01295 (Train Crew, 120 scenes), copied verbatim from the runtime
queue.json — no fabricated test data. Two validator families run on the
mechanical rows:

- JSON schema: SCENE_PLAN_SCHEMA via jsonschema (the same schema the LLM
  client enforces with json_schema_name="vidzeo_scene_queries_v3").
- Retrieval contract: _directed_scene_row_error per row plus the
  plan-level validate_scene_query_plan (duplicates + distinctiveness).

The investigation's prototype measured 0 row errors on GTA and 5-6 plan
errors per 32 on pronoun-only train scenes; the two prototype fixes
(event-framed subjects, working dedup) plus the facet/terms repairs in
the bundle bring the served bundle to 0 per-row errors on all three
workloads, with exactly one plan-level failure left (train chunk2 scene
16, whose only distinctive token is the junk word "still" — it stays in
the LLM repair set by design, not by validator gaming).
"""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mechanical_planner"

GTA_TITLE = "Grand Theft Auto VI: Return to Vice City"
TRAIN_TITLE = "Flooded Desert or Mountain Blizzard: What Changes Inside a Train Cab?"


def _load(job_id):
    return json.loads((FIXTURES / f"{job_id}.json").read_text(encoding="utf-8"))


def _train_sentences(script):
    import re
    return [t.strip() for t in re.split(r"(?<=[.!?])\s+", script.replace("\n", " ")) if t.strip()]


@pytest.fixture(scope="module")
def gta():
    return _load("a57cb5c7")


@pytest.fixture(scope="module")
def train():
    return _load("89b01295")


def test_mechanical_rows_pass_json_schema_and_contract_on_gta(gta):
    import jsonschema
    from services.mechanical_scene_planner import (
        paragraph_containers,
        plan_mechanical_scene_rows,
    )
    from services.vidzeo_brain import (
        SCENE_PLAN_SCHEMA,
        _directed_scene_row_error,
        validate_scene_query_plan,
    )

    paras = [p.strip() for p in gta["script"].split("\n\n") if p.strip()]
    assert len(paras) == 8
    containers = paragraph_containers(paras, "Grand Theft Auto VI")
    rows = plan_mechanical_scene_rows(paras, GTA_TITLE, "Grand Theft Auto VI", containers)
    assert len(rows) == 8
    for row in rows:
        jsonschema.validate(row, SCENE_PLAN_SCHEMA)
        assert _directed_scene_row_error(
            row, scene_text=paras[row["scene_number"] - 1], title=GTA_TITLE) is None
    assert validate_scene_query_plan(rows, paras, GTA_TITLE) == []


def test_mechanical_rows_pass_contract_on_train_chunks(train):
    from services.mechanical_scene_planner import (
        paragraph_containers,
        plan_mechanical_scene_rows,
    )
    from services.vidzeo_brain import _directed_scene_row_error, validate_scene_query_plan

    sents = _train_sentences(train["script"])
    assert len(sents) >= 95  # the prototype's chunk slices are [32:64] and [63:95]
    paras = [p.strip() for p in train["script"].split("\n\n") if p.strip()]
    para_cont = paragraph_containers(paras, "train cab")
    sent_para = []
    for s in sents:
        for pi, p in enumerate(paras):
            if s[:40] in p:
                sent_para.append(pi)
                break
        else:
            sent_para.append(sent_para[-1] if sent_para else 0)
    for name, chunk, offset in (
        ("chunk2", sents[32:64], 32),
        ("chunk3", sents[63:95], 63),
    ):
        containers = [para_cont[sent_para[offset + i]] for i in range(len(chunk))]
        rows = plan_mechanical_scene_rows(chunk, TRAIN_TITLE, "train cab", containers)
        assert len(rows) == 32
        row_errors = [
            (row["scene_number"], _directed_scene_row_error(
                row, scene_text=chunk[row["scene_number"] - 1], title=TRAIN_TITLE))
            for row in rows
        ]
        assert [e for _, e in row_errors if e] == [], f"{name} row errors: {row_errors}"
        plan_errors = validate_scene_query_plan(rows, chunk, TRAIN_TITLE)
        # Pronoun-only / near-duplicate scenes cannot be told apart by
        # tokens; they are routed to LLM repair, not papered over.
        assert len(plan_errors) <= 6, f"{name} plan errors: {plan_errors}"


def test_event_framed_subject_beats_bare_place():
    from services.mechanical_scene_planner import _event_framed_subject

    assert _event_framed_subject(
        "An avalanche at Wellington, Washington buries two Great Northern "
        "Railway trains at Stevens Pass in March 1910."
    ) == "avalanche burying two trains at Stevens Pass"
    assert _event_framed_subject(
        "New York's experience in the 1888 blizzard, when elevated trains "
        "were stranded above snowbound streets for days."
    ) == "New York elevated trains stranded in 1888 blizzard"
    # No disaster verb + place, or no place at all: no event framing.
    assert _event_framed_subject("The blizzard of 1888 was the worst on record.") is None
    assert _event_framed_subject("Trains ran on time through the mountains.") is None


def test_dedup_actually_removes_duplicates():
    from services.mechanical_scene_planner import plan_mechanical_scene_rows

    # Two near-identical scenes whose first-entity subjects and facets
    # collide: the old dedup appended an already-present token (a no-op).
    texts = [
        "The rotary snowplow throws snow clear of the track in winter.",
        "The rotary snowplow throws snow clear of the track in winter storms.",
    ]
    rows = plan_mechanical_scene_rows(texts, "Trains", "trains", ["rotary snowplow", "rotary snowplow"])
    yt = [r["youtube"]["primary"] for r in rows]
    web = [r["web"]["primary"] for r in rows]
    assert len(set(yt)) == len(yt), yt
    assert len(set(web)) == len(web), web


def test_served_bundle_passes_adapted_validators_on_real_scripts(gta, train):
    """The rows the pipeline actually serves (flat adapted bundle) pass
    the adapted contract validator on GTA fully, and on train chunk3
    fully; train chunk2 keeps exactly one failing scene (16)."""
    import app as server_app
    from services.vidzeo_brain import adapted_scene_plan_errors

    gta_paras = [p.strip() for p in gta["script"].split("\n\n") if p.strip()]
    bundle = server_app._generate_mechanical_shared_scene_bundle(None, GTA_TITLE, gta_paras)
    assert adapted_scene_plan_errors(bundle, gta_paras, GTA_TITLE) == {}

    sents = _train_sentences(train["script"])
    chunk3 = sents[63:95]
    bundle3 = server_app._generate_mechanical_shared_scene_bundle(None, TRAIN_TITLE, chunk3)
    assert adapted_scene_plan_errors(bundle3, chunk3, TRAIN_TITLE) == {}

    chunk2 = sents[32:64]
    bundle2 = server_app._generate_mechanical_shared_scene_bundle(None, TRAIN_TITLE, chunk2)
    assert adapted_scene_plan_errors(bundle2, chunk2, TRAIN_TITLE) == {
        15: "primary queries carry no token distinctive to this scene's narration"
    }


def test_mechanical_first_files_no_llm_request_when_all_validate(gta, monkeypatch):
    """The first path serves validated rows without filing any inference
    request: the shared brain bundle builder must not be called."""
    import app as server_app

    called = []
    monkeypatch.setattr(
        server_app, "_generate_shared_scene_brain_bundle",
        lambda *args, **kwargs: called.append(args) or (_ for _ in ()).throw(AssertionError("LLM filed")),
    )
    gta_paras = [p.strip() for p in gta["script"].split("\n\n") if p.strip()]
    result = server_app._generate_source_query_bundle_parallel(
        gta["script"], GTA_TITLE, gta_paras, {}, needs_web=True, needs_yt=True,
    )
    assert not called
    assert result["_planner"]["status"] == "mechanical_first"
    assert len(result["web"]) == 8 and len(result["yt"]) == 8


def test_mechanical_first_repairs_only_failing_scenes(train, monkeypatch):
    """A failing scene goes to the LLM for exactly that scene; the rest
    stay mechanical, and an unrepaired scene keeps its mechanical row."""
    import app as server_app

    sents = _train_sentences(train["script"])
    chunk2 = sents[32:64]
    seen = []

    def fake_brain(script, title, scene_texts, config):
        seen.append(list(scene_texts))
        # Return complete, contract-valid LLM rows for the requested scenes.
        rows_web, rows_yt, rows_stock = [], [], []
        for text in scene_texts:
            base = {
                "scene_number": 1,
                "section_subject": "Canadian Pacific Railway",
                "scene_subject": "Canadian Pacific Railway",
                "scene_subject_type": "topic",
                "container_subject": "Canadian Pacific Railway",
                "domain_category": "disaster",
                "retrieval_positive_terms": ["Canadian Pacific Railway", "stranded", "cold"],
                "retrieval_negative_terms": [],
                "youtube_query_intents": ["archival footage", "documentary footage"],
                "visual_intent": "stranded train cab still cold",
                "_llm": True,
            }
            rows_web.append({**base, "search_query": "Canadian Pacific Railway stranded train cab still cold archival photo",
                             "alt_search_query": "stranded train cab still cold photo", "fallback_query": "Canadian Pacific Railway photo"})
            rows_yt.append({**base, "search_query": "Canadian Pacific Railway stranded train cab still cold archival footage",
                            "alt_search_query": "stranded train cab still cold footage", "fallback_query": "Canadian Pacific Railway footage"})
            rows_stock.append({**base, "query_source": "stock", "primary_query": "stranded train cab cold",
                               "alt_query": "stranded train cab cold close up", "fallback_query": "stranded train cab cold footage"})
        return {"web": rows_web, "yt": rows_yt, "stock": rows_stock,
                "_planner": {"status": "directed"}}

    monkeypatch.setattr(server_app, "_generate_shared_scene_brain_bundle", fake_brain)
    result = server_app._generate_source_query_bundle_parallel(
        train["script"], TRAIN_TITLE, chunk2,
        {"query_plan_stage_deadline_seconds": 60},
        needs_web=True, needs_yt=True, needs_stock=True,
    )
    # Only the one failing scene (16, index 15) was filed to the LLM.
    assert len(seen) == 1 and len(seen[0]) == 1
    assert seen[0][0] == chunk2[15]
    assert result["_planner"]["status"] == "mechanical_first_llm_repair"
    assert result["_planner"]["llm_repair_scenes"] == [16]
    assert len(result["web"]) == 32
    # The repaired scene carries the LLM row; a passing scene stays mechanical.
    assert result["web"][15]["_llm"] is True
    assert result["web"][0].get("_mechanical_query_plan") is True


def test_deterministic_beats_pass_validator_and_skip_llm(monkeypatch):
    """Beats: deterministic beats pass the validator, so no LLM chunk is
    filed; a forced-invalid beat is the only one sent to the LLM."""
    from services import editorial_brain

    calls = []
    monkeypatch.setattr(
        editorial_brain, "_plan_chunk",
        lambda *args, **kwargs: calls.append(args) or [],
    )
    texts = [
        "On January 13, 1952, the City of San Francisco was trapped by a blizzard.",
        "Only 27 percent of the evidence survived.",
        "The final warning changed the investigation.",
    ]
    beats = editorial_brain.plan_editorial_beats(
        texts, {"openrouter_api_key": "test-key", "documentary_density": "balanced"},
    )
    assert calls == []
    assert len(beats) == 3
    assert all(editorial_brain.validate_editorial_beat(b, t) is None for b, t in zip(beats, texts))


def test_beat_validator_rejects_hallucinated_emphasis():
    from services.editorial_brain import validate_editorial_beat

    beat = {
        "scene_index": 0, "role": "evidence", "emphasis": "room went cold",
        "treatment": "keyword", "transition": "cut", "energy": "normal",
        "highlight_kind": "key_fact",
    }
    # "room went cold" is not a phrase in this narration (real agent miss).
    assert validate_editorial_beat(beat, "The City of San Francisco is the bridge between the old stories and the new ones.") is not None
    assert validate_editorial_beat(
        {**beat, "emphasis": "City of San Francisco"},
        "The City of San Francisco is the bridge between the old stories and the new ones.",
    ) is None


def test_diagram_scenes_stay_on_inference():
    from services.mechanical_scene_planner import scene_requests_diagram

    assert scene_requests_diagram("The diagram shows how the rotary plow throws snow clear.")
    assert scene_requests_diagram("A timeline of the three blizzards follows.")
    assert not scene_requests_diagram("The blizzard trapped the train for three days.")


# ── Gap 4: Panama f66daa53 — all 13 mechanical rows failed, none repaired ──
# Live evidence (server-runtime.log 2026-11-18 09:49:00-09:50:25, daemon.log):
# the planner resolved the anchor to "Panama Canal", every section came back
# intro, and every served row carried only ["Panama Canal", "other"] as its
# positive terms, so all 13 failed "fewer than 3 retrieval_positive_terms".
# The repair request WAS filed and answered in 74.5s, but the agent's row 1
# failed "scene_subject_type does not match the subject" and the whole
# 13-scene chunk was discarded, so 0/13 were repaired and the failure was
# silent in the plan checkpoint (elapsed 0.15s is the resume re-run's copy).
# The fixture below is the REAL job: script + 13 Whisper-split scene texts
# verbatim from the job checkpoints, query_title = the live resolved anchor.


@pytest.fixture(scope="module")
def panama():
    return _load("f66daa53")


def _panama_llm_answer(texts):
    """A directed answer for the Panama scenes: 12 valid rows + row 1
    corrupted exactly the way the live repair answer failed (subject type
    "topic" against visual type "place"). The raw prototype rows type the
    canal "person" and leave scene 7 subject-less ("Without"); normalize
    exactly those fields the way the bundle builder re-types them when it
    serves the same rows flat, so the 12 good rows pass the directed
    contract on their own merits."""
    from services.mechanical_scene_planner import plan_mechanical_scene_rows

    rows = plan_mechanical_scene_rows(texts, "Panama Canal", "Panama Canal", [""] * len(texts))
    for row in rows:
        if row["scene_subject"] == "Panama Canal":
            row["scene_subject_type"] = "place"
            row["visual_subject_type"] = "place"
        if row["scene_subject"] == "Without":
            row["scene_subject"] = "Panama Canal"
            row["scene_subject_type"] = "place"
            row["visual_subject_type"] = "place"
            row["visual_intent"] = "Panama Canal: separated must"
            row["youtube"]["primary"] = "Panama Canal separated must footage"
            row["youtube"]["supporting"] = "Panama Canal separated footage"
            row["web"]["primary"] = "Panama Canal separated must photograph"
            row["web"]["supporting"] = "Panama Canal separated photo"
    return rows


def test_served_bundle_passes_adapted_validators_on_panama(panama):
    """Gap 4, cause 1: with the live anchor "Panama Canal" the served
    bundle used to fail 13/13 on fewer-than-3 positive terms. Now 0/13."""
    import app as server_app
    from services.vidzeo_brain import adapted_scene_plan_errors

    texts = panama["scene_texts"]
    assert len(texts) == 13
    bundle = server_app._generate_mechanical_shared_scene_bundle(
        panama["script"], panama["query_title"], texts)
    assert adapted_scene_plan_errors(bundle, texts, panama["query_title"]) == {}
    for source in ("web", "yt", "stock"):
        for row in bundle[source]:
            terms = [t for t in row["retrieval_positive_terms"] if str(t or "").strip()]
            assert len(terms) >= 3, (source, row["scene_number"], terms)


def test_mechanical_first_files_no_llm_request_on_panama(panama, monkeypatch):
    """Gap 4, replay: the Panama job now needs no repair at all — the
    first path serves all 13 scenes without filing an inference request."""
    import app as server_app

    monkeypatch.setattr(
        server_app, "_generate_shared_scene_brain_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM filed")),
    )
    result = server_app._generate_source_query_bundle_parallel(
        panama["script"], panama["query_title"], panama["scene_texts"], {},
        needs_web=True, needs_yt=True, needs_stock=True,
    )
    assert result["_planner"]["status"] == "mechanical_first"
    assert result["_planner"]["llm_repair_scenes"] == []
    assert len(result["web"]) == 13 and len(result["yt"]) == 13 and len(result["stock"]) == 13


def test_brain_salvages_single_invalid_row(panama):
    """Gap 4, cause 2: the live repair answer failed on row 1 alone and
    the whole 13-scene chunk was discarded (0/13 repaired). Now the bad
    row is replaced by its mechanical fallback and the other 12 directed
    rows survive."""
    import copy
    import json as _json

    from services.vidzeo_brain import VidzeoBrain, _directed_scene_row_error

    texts = panama["scene_texts"]
    answer = _panama_llm_answer(texts)
    good = [r for r in answer]
    for row in good[1:]:
        assert _directed_scene_row_error(
            row, scene_text=texts[row["scene_number"] - 1], title="Panama Canal") is None
    bad = copy.deepcopy(answer[0])
    bad["scene_subject_type"] = "topic"
    bad["visual_subject_type"] = "place"
    assert _directed_scene_row_error(
        bad, scene_text=texts[0], title="Panama Canal") == "scene_subject_type does not match the subject"

    class _FakeClient:
        model = "external-agent"
        fallback_models = ()
        attempt_history = []
        last_model = "external-agent"
        model_chain = ("external-agent",)

        def __init__(self, payload):
            self._payload = payload

        def chat(self, **kwargs):
            return _json.dumps({"scenes": self._payload})

    payload = [bad] + [copy.deepcopy(row) for row in answer[1:]]
    brain = VidzeoBrain(client=_FakeClient(payload))
    rows = brain._plan_scenes_impl(
        topic="Panama Canal", script=panama["script"], scene_texts=texts,
        section_subjects=[""] * len(texts), pipeline_type="mixed",
    )
    assert isinstance(rows, list) and len(rows) == 13
    assert rows[0].get("_query_fallback") == "invalid_llm_row"
    assert rows[0]["scene_number"] == 1
    for offset, row in enumerate(rows[1:], start=1):
        assert row.get("_query_fallback") in (None, ""), row
        assert row["scene_number"] == offset + 1
        assert row["youtube"]["primary"] == answer[offset]["youtube"]["primary"]


def test_brain_returns_none_when_every_row_is_invalid(panama):
    """Salvage has a floor: an all-invalid chunk still fails outright,
    exactly as before (the caller then tries the next provider or falls
    back to mechanical rows)."""
    import copy
    import json as _json

    from services.vidzeo_brain import VidzeoBrain

    texts = panama["scene_texts"]
    answer = _panama_llm_answer(texts)
    for row in answer:
        row["scene_subject_type"] = "topic"
        row["visual_subject_type"] = "place"

    class _FakeClient:
        model = "external-agent"
        fallback_models = ()
        attempt_history = []
        last_model = "external-agent"
        model_chain = ("external-agent",)

        def __init__(self, payload):
            self._payload = payload

        def chat(self, **kwargs):
            return _json.dumps({"scenes": self._payload})

    brain = VidzeoBrain(client=_FakeClient(copy.deepcopy(answer)))
    assert brain._plan_scenes_impl(
        topic="Panama Canal", script=panama["script"], scene_texts=texts,
        section_subjects=[""] * len(texts), pipeline_type="mixed",
    ) is None


def test_repair_failure_is_recorded_not_silent(train, monkeypatch):
    """Gap 4, cause 2b: a failed repair used to vanish — the thread's
    exception was stored and never logged or recorded, so the plan
    checkpoint showed 0/13 repaired with no reason. Now the reason is in
    the planner metadata the checkpoint persists."""
    import app as server_app

    sents = _train_sentences(train["script"])
    chunk2 = sents[32:64]

    def failing_brain(script, title, scene_texts, config):
        raise server_app.OpenRouterScenePlanningError(
            "OpenRouter returned an invalid scene plan at row 1: "
            "scene_subject_type does not match the subject")

    monkeypatch.setattr(server_app, "_generate_shared_scene_brain_bundle", failing_brain)
    result = server_app._generate_source_query_bundle_parallel(
        train["script"], TRAIN_TITLE, chunk2,
        {"query_plan_stage_deadline_seconds": 60},
        needs_web=True, needs_yt=True, needs_stock=True,
    )
    assert result["_planner"]["status"] == "mechanical_first_llm_repair"
    assert result["_planner"]["llm_repair_scenes"] == [16]
    assert result["_planner"]["llm_repaired_scenes"] == []
    assert result["_planner"]["unrepaired_scenes"] == [16]
    assert "scene_subject_type does not match the subject" in result["_planner"]["reason"]


def test_repair_keeps_fallback_row_mechanical(train, monkeypatch):
    """A brain row the brain itself salvaged (marked _query_fallback) is
    mechanical, not LLM-directed: the repair merge must keep the
    mechanical row for that scene instead of counting it as repaired."""
    import app as server_app

    sents = _train_sentences(train["script"])
    chunk2 = sents[32:64]

    def fallback_brain(script, title, scene_texts, config):
        assert len(scene_texts) == 1
        base = {
            "scene_number": 1,
            "section_subject": "Canadian Pacific Railway",
            "scene_subject": "Canadian Pacific Railway",
            "scene_subject_type": "topic",
            "container_subject": "Canadian Pacific Railway",
            "domain_category": "disaster",
            "retrieval_positive_terms": ["Canadian Pacific Railway", "stranded", "cold"],
            "retrieval_negative_terms": [],
            "youtube_query_intents": ["archival footage", "documentary footage"],
            "visual_intent": "stranded train cab still cold",
            "_llm": True,
            "_query_fallback": "invalid_llm_row",
        }
        return {
            "web": [{**base, "search_query": "Canadian Pacific Railway stranded train cab still cold archival photo",
                     "alt_search_query": "stranded train cab still cold photo", "fallback_query": "Canadian Pacific Railway photo"}],
            "yt": [{**base, "search_query": "Canadian Pacific Railway stranded train cab still cold archival footage",
                    "alt_search_query": "stranded train cab still cold footage", "fallback_query": "Canadian Pacific Railway footage"}],
            "stock": [{**base, "query_source": "stock", "primary_query": "stranded train cab cold",
                       "alt_query": "stranded train cab cold close up", "fallback_query": "stranded train cab cold footage"}],
            "_planner": {"status": "directed"},
        }

    monkeypatch.setattr(server_app, "_generate_shared_scene_brain_bundle", fallback_brain)
    result = server_app._generate_source_query_bundle_parallel(
        train["script"], TRAIN_TITLE, chunk2,
        {"query_plan_stage_deadline_seconds": 60},
        needs_web=True, needs_yt=True, needs_stock=True,
    )
    assert result["_planner"]["llm_repair_scenes"] == [16]
    assert result["_planner"]["llm_repaired_scenes"] == []
    assert result["_planner"]["unrepaired_scenes"] == [16]
    assert result["web"][15].get("_mechanical_query_plan") is True
