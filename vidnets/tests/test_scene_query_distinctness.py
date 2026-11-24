"""Scene-accurate, distinct queries (worker B).

Regression tests built from the GTA VI proof job's own records
(job a57cb5c7, output GTA_VI_1Min_20261116_181729_8f06fbb8):
its queries.json rows shipped with empty retrieval-contract fields and
every scene typed "person", because the shared-plan cache served them
without ever running the validator. Every test below either uses those
real rows verbatim (embedded, so the test runs anywhere) or loads the
real checkpoint files when they are present on this machine (skipped
otherwise, following the Hindenburg-fixture precedent).

Rules under test:
- a query with no scene-specific entity/action/viewpoint terms fails;
- a near-duplicate of another scene's primary query fails, unless the
  two narrations are genuinely identical (proven, not assumed);
- a wrong subject type fails (company/place/game typed "person");
- queries name the game THE SCENE narrates, not the video title's game
  (scene 0 is about GTA V: its queries must seek GTA V);
- no query ever seeks the 2002 Vice City game.
"""
import copy
import json
from pathlib import Path

import pytest

from services import vidzeo_brain
from services.vidzeo_brain import (
    _adapted_scene_row_error,
    _directed_scene_row_error,
    _infer_scene_subject_type,
    _is_format_phrase_subject,
    _subject_type_mismatch,
    game_keys_named_in_text,
    query_seeks_forbidden_2002,
    scene_subject_game_for_scene,
    validate_adapted_scene_plan,
    validate_cached_scene_bundle,
    validate_scene_query_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROOF_JOB_DIR = (
    REPO_ROOT.parent / "vidnets-runtime" / "output"
    / "GTA_VI_1Min_20261116_181729_8f06fbb8" / "checkpoints"
)
GTA_TITLE = "Grand Theft Auto VI: Return to Vice City"

# The proof job's 8 narrations, verbatim from scene_contracts.json.
GTA_SCENE_TEXTS = [
    "Grand Theft Auto Six is the most anticipated video game of the decade, and Rockstar Games has kept fans waiting for more than ten years since Grand Theft Auto Five, a game that has sold more than two hundred million copies.\n\n",
    "The game returns to Vice City, now set in the fictional state of Leonida, a sun-soaked reimagining of Florida filled with beaches, swamps, and neon streets.\n\n",
    "The trailers show the Leonida Keys, the swamps of the Grassrivers, and the neon of Vice City itself, a world far bigger and more detailed than anything Rockstar has built before.\n\n",
    "At the center of the story are Lucia Caminos and Jason Duval, two partners in crime whose story begins with Lucia walking out of prison and reuniting with Jason.\n\n",
    "The first trailer arrived in December 2023 and broke viewing records within a day, while a second trailer in May 2025 showed more of Leonida and its characters.\n\n",
    "Rockstar first promised the game for autumn 2025, then pushed it to May 2026, and later delayed it again to November 19, 2026, saying the extra time is needed to finish it to their standard.\n\n",
    "When it arrives, it launches first on PlayStation 5 and Xbox Series X and S, with a PC version yet to be announced.\n\n",
    "For Rockstar, Grand Theft Auto Six is not just a sequel, it is the next chapter of the biggest entertainment series in the world.",
]

# Proof-job queries.json web row, scene 1, verbatim (fields the validator
# reads; unrelated display fields trimmed). It shipped: empty
# domain_category / positive terms / intents, Rockstar Games typed person.
OLD_WEB_ROW_SCENE_1 = {
    "scene_number": 1,
    "section_subject": "Rockstar Games",
    "scene_subject": "Rockstar Games",
    "scene_subject_type": "person",
    "container_type": "entity",
    "subject_query": "Rockstar Games",
    "topic_anchor": "Rockstar Games",
    "global_topic": "Rockstar Games",
    "domain_anchor": "Grand Theft Auto VI",
    "domain_category": "",
    "search_disambiguator": "Rockstar Games",
    "retrieval_positive_terms": [],
    "retrieval_negative_terms": [],
    "youtube_query_intents": [],
    "container_id": "section_6",
    "section_id": "section_6",
    "visual_intent": "Rockstar Games logo and studio imagery with GTA VI key art reveal",
    "visual_subject": "Rockstar Games",
    "visual_subject_type": "organization",
    "subject_evidence": "Rockstar Games has kept fans waiting for more than ten years",
    "visible_target": "Rockstar Games branding and GTA VI key art on screen",
    "identity_scope": "subject",
    "query_source": "web",
    "search_query": "Rockstar Games logo studio",
    "supporting_search_query": "Rockstar Games Grand Theft Auto VI key art official",
    "fallback_query": "Rockstar Games headquarters building photo",
    "primary_query": "Rockstar Games logo studio",
    "web_query": "Rockstar Games logo studio",
    "_llm": True,
    "_brain": True,
    "_shared_scene_brain": True,
}

# Proof-job queries.json web row, scene 7, verbatim (trimmed as above):
# the container subject is the format phrase "video game official
# trailer gameplay", and the studio/game is again typed "person".
OLD_WEB_ROW_SCENE_7 = {
    "scene_number": 7,
    "section_subject": "video game official trailer gameplay",
    "scene_subject": "Grand Theft Auto VI",
    "scene_subject_type": "person",
    "container_type": "entity",
    "subject_query": "video game official trailer gameplay",
    "topic_anchor": "video game official trailer gameplay",
    "global_topic": "video game official trailer gameplay",
    "domain_anchor": "Grand Theft Auto VI",
    "domain_category": "",
    "search_disambiguator": "video game official trailer gameplay",
    "retrieval_positive_terms": [],
    "retrieval_negative_terms": [],
    "youtube_query_intents": [],
    "container_id": "entity_official_trailer_gameplay",
    "section_id": "entity_official_trailer_gameplay",
    "visual_intent": "Official gameplay footage showing Leonida action on console",
    "visual_subject": "Grand Theft Auto VI",
    "visual_subject_type": "video_game",
    "subject_evidence": "it launches first on PlayStation 5 and Xbox Series X and S",
    "visible_target": "GTA VI gameplay running on PlayStation 5 and Xbox Series X and S",
    "identity_scope": "context",
    "query_source": "web",
    "search_query": "video game official trailer gameplay Grand Theft Auto VI gameplay screenshot",
    "supporting_search_query": "video game official trailer gameplay GTA VI PlayStation 5 gameplay",
    "fallback_query": "video game official trailer gameplay GTA VI Xbox Series X gameplay",
    "primary_query": "video game official trailer gameplay Grand Theft Auto VI gameplay screenshot",
    "web_query": "video game official trailer gameplay Grand Theft Auto VI gameplay screenshot",
    "_llm": True,
    "_brain": True,
    "_shared_scene_brain": True,
}


def _corrected_scene_1_plan_row():
    """What scene 0's plan row must look like: filled contract fields,
    GTA V named (the scene narrates GTA V's sales), type "topic"."""
    return {
        "scene_number": 1,
        "container_subject": "Grand Theft Auto VI",
        "scene_subject": "Grand Theft Auto V two hundred million copies sold",
        "visual_intent": "Grand Theft Auto V gameplay montage with 200 million copies sales counter, Rockstar Games logo reveal",
        "visual_subject": "Grand Theft Auto V",
        "visual_subject_type": "video_game",
        "subject_evidence": "a game that has sold more than two hundred million copies",
        "visible_target": "GTA V gameplay and sales milestone",
        "identity_scope": "subject",
        "domain_category": "gaming",
        "scene_subject_type": "topic",
        "retrieval_positive_terms": [
            "Grand Theft Auto V", "GTA V gameplay", "200 million copies sold",
            "Rockstar Games", "Los Santos",
        ],
        "retrieval_negative_terms": ["streamer", "reaction", "mod", "fanmade"],
        "youtube_query_intents": [
            "raw gameplay no commentary", "direct gameplay capture 4k",
            "official gameplay trailer", "uncut gameplay 60fps",
        ],
        "youtube": {
            "primary": "Grand Theft Auto V 200 million copies gameplay no commentary",
            "supporting": "GTA V Los Santos gameplay direct capture",
            "fallback": "Grand Theft Auto V official trailer gameplay",
        },
        "web": {
            "primary": "Grand Theft Auto V 200 million copies sold sales milestone",
            "supporting": "GTA V Los Santos gameplay screenshot",
            "fallback": "Grand Theft Auto V key art official",
        },
        "stock": {
            "primary_query": "Grand Theft Auto V gameplay",
            "alt_query": "video game sales milestone",
            "fallback_query": "gaming celebration",
        },
    }


def _plan_row(scene_number, subject, subject_type, visual_type, yt_primary, web_primary,
              intent="Aerial view of the subject on screen", container=None):
    """Minimal contract-valid plan row for plan-level tests."""
    container = container or subject
    return {
        "scene_number": scene_number,
        "container_subject": container,
        "scene_subject": subject,
        "visual_intent": intent,
        "visual_subject": subject,
        "visual_subject_type": visual_type,
        "subject_evidence": f"{subject} is visible.",
        "visible_target": subject,
        "identity_scope": "subject",
        "domain_category": "documentary",
        "scene_subject_type": subject_type,
        "retrieval_positive_terms": [subject, "aerial view", "documentary"],
        "retrieval_negative_terms": [],
        "youtube_query_intents": ["aerial footage", "raw footage"],
        "youtube": {"primary": yt_primary, "supporting": f"{subject} video", "fallback": f"{subject} clip"},
        "web": {"primary": web_primary, "supporting": f"{subject} image", "fallback": f"{subject} picture"},
        "stock": {"primary_query": subject, "alt_query": f"{subject} scene", "fallback_query": "subject"},
    }


def _flat_row(scene_number, subject, subject_type, visual_type, primary, source="web"):
    """Minimal flat adapted row (one source) for cache/adapted tests."""
    return {
        "scene_number": scene_number,
        "section_subject": subject,
        "scene_subject": subject,
        "scene_subject_type": subject_type,
        "container_type": "entity",
        "subject_query": subject,
        "topic_anchor": subject,
        "global_topic": subject,
        "domain_anchor": "Grand Theft Auto VI",
        "domain_category": "gaming",
        "search_disambiguator": subject,
        "retrieval_positive_terms": [subject, "Grand Theft Auto VI", "gaming"],
        "retrieval_negative_terms": [],
        "youtube_query_intents": ["raw gameplay no commentary", "official gameplay trailer"],
        "container_id": f"section_{scene_number}",
        "section_id": f"section_{scene_number}",
        "visual_intent": f"Aerial view of {subject} on screen",
        "visual_subject": subject,
        "visual_subject_type": visual_type,
        "subject_evidence": f"{subject} is visible.",
        "visible_target": subject,
        "identity_scope": "subject",
        "query_source": source,
        "search_query": primary,
        "primary_query": primary,
        "web_query": primary,
        "youtube_query": primary,
        "_llm": True,
        "_brain": True,
        "_shared_scene_brain": True,
    }


# --- The proof job's real rows -------------------------------------------


def test_proof_row_scene_1_fails_adapted_validation():
    error = _adapted_scene_row_error(
        OLD_WEB_ROW_SCENE_1, scene_text=GTA_SCENE_TEXTS[0], title=GTA_TITLE)
    assert error is not None
    assert "domain_category" in error
    assert _subject_type_mismatch(OLD_WEB_ROW_SCENE_1) is True


def test_proof_row_scene_7_format_phrase_subject_fails():
    assert _is_format_phrase_subject(OLD_WEB_ROW_SCENE_7["section_subject"]) is True
    assert _subject_type_mismatch(OLD_WEB_ROW_SCENE_7) is True
    filled = copy.deepcopy(OLD_WEB_ROW_SCENE_7)
    filled["domain_category"] = "gaming"
    filled["retrieval_positive_terms"] = [
        "Grand Theft Auto VI", "gameplay", "PlayStation 5"]
    filled["youtube_query_intents"] = ["official gameplay trailer"]
    error = _adapted_scene_row_error(
        filled, scene_text=GTA_SCENE_TEXTS[6], title=GTA_TITLE)
    assert error is not None and "format phrase" in error


def test_proof_bundle_is_rejected_by_cache_gate():
    bundle = {
        "web": [copy.deepcopy(OLD_WEB_ROW_SCENE_1)],
        "yt": [_flat_row(1, "Rockstar Games", "person", "organization",
                         "Rockstar Games studio Grand Theft Auto VI announcement", source="youtube")],
        "stock": [_flat_row(1, "Rockstar Games", "person", "organization",
                            "Rockstar Games video game studio office developers working", source="stock")],
    }
    error = validate_cached_scene_bundle(bundle, GTA_SCENE_TEXTS[:1], GTA_TITLE)
    assert error is not None
    assert "scene 1 web" in error


def test_proof_job_live_queries_json_is_rejected():
    """The real checkpoint files, when present: the shipped bundle must
    not pass the cache gate (the stored cache file itself is gone, so
    the bundle is re-keyed youtube->yt and stamped with the current
    schema + a directed planner, isolating the contract gate)."""
    queries_path = PROOF_JOB_DIR / "queries.json"
    contracts_path = PROOF_JOB_DIR / "scene_contracts.json"
    if not queries_path.is_file() or not contracts_path.is_file():
        pytest.skip("GTA VI proof-job checkpoints not present")
    stored = json.loads(queries_path.read_text(encoding="utf-8"))
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    scene_texts = list((contracts.get("expected") or {}).get("scene_texts") or [])
    assert len(scene_texts) == 8
    script = " ".join(scene_texts)
    bundle = {"web": stored["web"], "yt": stored["youtube"], "stock": stored["stock"]}
    error = validate_cached_scene_bundle(bundle, scene_texts, GTA_TITLE)
    assert error is not None
    assert "scene 1 web" in error
    # And the loader itself refuses it once planted in a cache dir with
    # the current schema and a directed planner — so the contract gate,
    # not the schema bump, is what rejects it.
    import app as server_app  # noqa: PLC0415  (heavy server module)
    assert server_app._SHARED_PLAN_CACHE_SCHEMA.startswith("scene_context_v20")
    import os
    import tempfile
    from unittest import mock
    planted = dict(bundle)
    planted["_cache_schema"] = server_app._SHARED_PLAN_CACHE_SCHEMA
    planted["_planner"] = {"status": "directed", "fallback_scenes": 0}
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.dict(os.environ, {"VIDZEO_YT_PLAN_CACHE_DIR": tmp}):
            cache_file = server_app._shared_plan_cache_path(script, GTA_TITLE, scene_texts)
            cache_file.write_text(json.dumps(planted), encoding="utf-8")
            assert server_app._load_shared_plan_cache(script, GTA_TITLE, scene_texts, {}) is None


# --- Scene-relative game ---------------------------------------------------


def test_scene_1_subject_game_is_gta_v_not_the_title_game():
    assert game_keys_named_in_text(GTA_SCENE_TEXTS[0]) == ["gta_6", "gta_5"]
    game = scene_subject_game_for_scene(GTA_SCENE_TEXTS[0], GTA_TITLE)
    assert game["key"] == "gta_5"
    assert game["title"] == "Grand Theft Auto V"
    # Scenes that name no game inherit the title's game implicitly.
    assert scene_subject_game_for_scene(GTA_SCENE_TEXTS[1], GTA_TITLE)["key"] == "gta_6"
    assert scene_subject_game_for_scene(GTA_SCENE_TEXTS[7], GTA_TITLE)["key"] == "gta_6"


def test_garbled_gta_vi_v_does_not_count_as_gta_v():
    # The proof job's YT supporting query for scene 1 says
    # "Grand Theft Auto VI V 200 million copies sold milestone".
    # Token-boundary matching: that names GTA VI, not GTA V.
    assert game_keys_named_in_text(
        "Rockstar Games Grand Theft Auto VI V 200 million copies sold milestone") == ["gta_6"]


def test_corrected_scene_1_row_passes():
    row = _corrected_scene_1_plan_row()
    assert _directed_scene_row_error(row, scene_text=GTA_SCENE_TEXTS[0], title=GTA_TITLE) is None


def test_old_scene_1_queries_fail_the_game_check():
    """The shipped scene-1 primaries never name GTA V (the garbled
    supporting query does not count). With contract fields filled, the
    game check is what fails."""
    row = _corrected_scene_1_plan_row()
    row["youtube"]["primary"] = "Rockstar Games studio Grand Theft Auto VI announcement"
    row["web"]["primary"] = "Rockstar Games logo studio"
    row["scene_subject"] = "Rockstar Games"
    row["container_subject"] = "Rockstar Games"
    error = _directed_scene_row_error(row, scene_text=GTA_SCENE_TEXTS[0], title=GTA_TITLE)
    assert error is not None and "Grand Theft Auto V" in error


def test_gta_v_query_in_a_gta_vi_only_scene_fails():
    row = _corrected_scene_1_plan_row()
    error = _directed_scene_row_error(
        row, scene_text=GTA_SCENE_TEXTS[4], title=GTA_TITLE)
    assert error is not None and "Grand Theft Auto V" in error


def test_2002_vice_city_query_always_fails():
    for query in (
        "GTA Vice City 2002 gameplay",
        "Grand Theft Auto Vice City official trailer",
        "Grand Theft Auto III gameplay no commentary",
    ):
        assert query_seeks_forbidden_2002(query) is True
        row = _corrected_scene_1_plan_row()
        row["youtube"]["primary"] = query
        row["web"]["primary"] = query
        error = _directed_scene_row_error(row, scene_text=GTA_SCENE_TEXTS[0], title=GTA_TITLE)
        assert error is not None and "2002 Vice City" in error
    # The new Vice City beside the new game's title stays allowed.
    assert query_seeks_forbidden_2002("Vice City Grand Theft Auto VI neon streets night footage") is False
    assert query_seeks_forbidden_2002("Leonida Keys Grand Theft Auto VI island chain aerial footage") is False


# --- Subject types ----------------------------------------------------------


@pytest.mark.parametrize("subject,claimed,visual", [
    ("Rockstar Games", "person", "organization"),
    ("Vice City", "person", "place"),
    ("Leonida Keys", "person", "place"),
    ("Leonida", "person", "place"),
    ("Grand Theft Auto VI", "person", "video_game"),
    ("Rockstar Games", "place", "organization"),
    ("Vice City", "product", "place"),
])
def test_wrong_subject_type_is_a_mismatch(subject, claimed, visual):
    assert _subject_type_mismatch({
        "scene_subject": subject, "scene_subject_type": claimed,
        "visual_subject_type": visual}) is True


@pytest.mark.parametrize("subject,claimed,visual", [
    ("Rockstar Games", "topic", "organization"),
    ("Vice City", "place", "place"),
    ("Leonida Keys", "place", "place"),
    ("Lucia Caminos and Jason Duval", "person", "person"),
    ("Grand Theft Auto VI", "topic", "video_game"),
])
def test_correct_subject_type_is_not_a_mismatch(subject, claimed, visual):
    assert _subject_type_mismatch({
        "scene_subject": subject, "scene_subject_type": claimed,
        "visual_subject_type": visual}) is False


def test_non_enum_subject_type_is_a_mismatch():
    assert _subject_type_mismatch({
        "scene_subject": "Vice City", "scene_subject_type": "entity",
        "visual_subject_type": "place"}) is True


@pytest.mark.parametrize("subject,visual,expected", [
    ("Rockstar Games", "organization", "topic"),
    ("Rockstar Games", "", "topic"),
    ("Vice City", "", "place"),
    ("Leonida Keys", "", "place"),
    ("Grand Theft Auto VI", "", "topic"),
    ("Grand Theft Auto VI", "video_game", "topic"),
    ("Lucia Caminos and Jason Duval", "person", "person"),
])
def test_inferred_subject_type_never_defaults_to_person(subject, visual, expected):
    assert _infer_scene_subject_type(subject, visual) == expected


@pytest.mark.parametrize("subject,expected", [
    ("Erling Haaland", "person"),
    ("Lionel Messi", "person"),
    ("Lucia Caminos and Jason Duval", "person"),
    ("Panama Canal", "place"),
])
def test_name_like_subject_infers_person(subject, expected):
    """A name-like multi-word capitalized name is person evidence.

    Regression: the first inference implementation fell back to "topic"
    for every subject with no org/place/game signal, so "Erling Haaland"
    stopped being typed "person" and the adapter's wrong-global-person
    stripping (which only runs for person subjects) stopped firing —
    "cristiano" survived into the Haaland query. The org/place/game
    checks above still fire first, so Rockstar Games / Vice City /
    GTA VI are unaffected.
    """
    assert _infer_scene_subject_type(subject, "") == expected


# --- Duplicates + distinctiveness -------------------------------------------


def test_duplicate_primary_queries_fail_plan_validation():
    rows = [
        _plan_row(1, "Vice City neon streets", "place", "place",
                  "Vice City Leonida neon streets night footage",
                  "Vice City Leonida neon streets night photo"),
        _plan_row(2, "Vice City neon streets", "place", "place",
                  "Vice City Leonida neon streets night footage",
                  "Vice City Leonida neon streets night photo"),
    ]
    texts = [GTA_SCENE_TEXTS[1],
             "The trailers return to Vice City at night, neon streets glowing after dark."]
    errors = validate_scene_query_plan(rows, texts, GTA_TITLE)
    assert errors and errors[0]["scene_number"] == 2
    assert "duplicates scene 1" in errors[0]["error"]


def test_identical_narration_may_share_a_query():
    rows = [
        _plan_row(1, "Vice City neon streets", "place", "place",
                  "Vice City Grand Theft Auto VI neon streets night footage",
                  "Vice City neon streets photo"),
        _plan_row(2, "Vice City neon streets", "place", "place",
                  "Vice City Grand Theft Auto VI neon streets night footage",
                  "Vice City neon streets photo"),
    ]
    texts = ["The game returns to Vice City, neon streets at night."] * 2
    assert validate_scene_query_plan(rows, texts, GTA_TITLE) == []


def test_query_without_a_distinctive_token_fails():
    # The old scene-5 query: "Leonida Grand Theft Auto VI landscape"
    # carries no word distinctive to the December-2023 trailer narration.
    rows = [
        _plan_row(1, "Leonida revealed", "place", "place",
                  "Leonida Grand Theft Auto VI landscape",
                  "Leonida Grand Theft Auto VI landscape photo",
                  intent="Sweeping Leonida landscapes"),
        _plan_row(2, "Leonida Keys island chain", "place", "place",
                  "Leonida Keys Florida Keys aerial photo",
                  "Leonida Keys tropical island chain aerial",
                  intent="Island chain and tropical keys scenery"),
    ]
    texts = [GTA_SCENE_TEXTS[4], GTA_SCENE_TEXTS[2]]
    errors = validate_scene_query_plan(rows, texts, GTA_TITLE)
    assert errors and errors[0]["scene_number"] == 1
    assert "distinctive" in errors[0]["error"]


def test_fresh_path_rejects_a_duplicate_plan(monkeypatch):
    """Two per-row-valid scenes sharing a primary fail the whole chunk."""
    from unittest import mock
    from services.vidzeo_brain import VidzeoBrain

    rows = [
        _plan_row(1, "Vice City neon streets", "place", "place",
                  "Vice City Leonida neon streets night footage",
                  "Vice City Leonida neon streets night photo"),
        _plan_row(2, "Vice City neon streets", "place", "place",
                  "Vice City Leonida neon streets night footage",
                  "Vice City Leonida neon streets night photo"),
    ]
    monkeypatch.delenv("VIDZEO_BRAIN_SCENE_CHUNK_SIZE", raising=False)
    brain = VidzeoBrain("test-key", fallback_models=[])
    texts = [GTA_SCENE_TEXTS[1],
             "The trailers return to Vice City at night, neon streets glowing after dark."]
    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                           return_value=json.dumps({"scenes": rows})):
        assert brain.plan_scenes(
            topic=GTA_TITLE, script=" ".join(texts), scene_texts=texts) is None


def test_game_stamp_is_conditional_on_a_derived_game(monkeypatch):
    """The scene_subject_game stamp appears only when a game is derived.

    Regression: the stamp was applied unconditionally, so every row
    gained "scene_subject_game": "" / "scene_subject_games": [] and the
    compact-schema test's exact row equality (rows == [scene]) broke
    for game-less scenes. A Panama scene returns byte-identical rows;
    a GTA V scene is stamped gta_5.
    """
    from unittest import mock
    from services.vidzeo_brain import VidzeoBrain

    monkeypatch.delenv("VIDZEO_BRAIN_SCENE_CHUNK_SIZE", raising=False)
    brain = VidzeoBrain("test-key", fallback_models=[])

    panama_text = "A cargo ship enters a Panama Canal lock."
    panama_row = _plan_row(
        1, "Panama Canal locks", "place", "place",
        "Panama Canal cargo ship entering lock raw footage",
        "Panama Canal cargo ship entering lock photo",
        intent="Cargo ship entering a canal lock")
    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                           return_value=json.dumps({"scenes": [panama_row]})):
        rows = brain.plan_scenes(
            topic="Panama Canal", script=panama_text, scene_texts=[panama_text])
    assert rows == [panama_row]
    assert "scene_subject_game" not in rows[0]
    assert "scene_subject_games" not in rows[0]

    gta_row = _corrected_scene_1_plan_row()
    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                           return_value=json.dumps({"scenes": [gta_row]})):
        rows = brain.plan_scenes(
            topic=GTA_TITLE, script=GTA_SCENE_TEXTS[0],
            scene_texts=[GTA_SCENE_TEXTS[0]])
    assert rows is not None
    assert rows[0]["scene_subject_game"] == "gta_5"
    assert "gta_5" in rows[0]["scene_subject_games"]


# --- Generation side: the fields the validator demands -----------------------


def test_container_schema_requires_retrieval_fields():
    required = set(vidzeo_brain.CONTAINER_MAP_SCHEMA["properties"]["containers"]["items"]["required"])
    assert {"domain_category", "scene_subject_type", "retrieval_positive_terms",
            "retrieval_negative_terms", "youtube_query_intents"} <= required


def test_container_merge_fills_omitted_intents(monkeypatch):
    """An LLM container row that omits youtube_query_intents gets the
    domain fallback instead of shipping empty (the old failure)."""
    from unittest import mock
    from services.vidzeo_brain import VidzeoBrain

    monkeypatch.delenv("VIDZEO_BRAIN_CONTAINER_STAGE_SECONDS", raising=False)
    brain = VidzeoBrain("test-key", fallback_models=[])
    reply = json.dumps({"is_listicle": False, "containers": [{
        "scene_number": 1,
        "container_id": "section_1",
        "container_subject": "Vice City",
        "scene_subject": "Vice City neon streets",
        "scene_subject_type": "place",
        "container_boundary": True,
    }]})
    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat", return_value=reply):
        result = brain.plan_containers(
            topic=GTA_TITLE,
            script=GTA_SCENE_TEXTS[1],
            scene_texts=[GTA_SCENE_TEXTS[1]])
    assert result is not None
    row = result["containers"][0]
    assert row["scene_subject_type"] == "place"
    assert row["domain_category"] == "gaming"
    assert len(row["youtube_query_intents"]) >= 3


def test_mechanical_bundle_types_rockstar_not_person():
    from services.mechanical_query_bundle import build_mechanical_query_bundle

    subjects = ["Rockstar Games", "Vice City", "Leonida Keys",
                "Lucia Caminos and Jason Duval", "Leonida",
                "Rockstar Games", "Grand Theft Auto VI", "Rockstar Games"]
    bundle = build_mechanical_query_bundle(
        title=GTA_TITLE, scene_texts=GTA_SCENE_TEXTS,
        section_subjects=subjects,
        sections=[{"container_subject": s} for s in subjects])
    assert bundle["web"][0]["scene_subject_type"] == "topic"
    assert bundle["web"][1]["scene_subject_type"] == "place"
    assert bundle["web"][3]["scene_subject_type"] == "person"
    assert bundle["web"][6]["scene_subject_type"] == "topic"
    assert bundle["web"][0]["youtube_query_intents"]
    assert "Grand Theft Auto V" in bundle["web"][0]["retrieval_positive_terms"]


def test_adapter_no_longer_defaults_to_person():
    """The adapter hunk: a plan row without scene_subject_type for
    Rockstar Games adapts to "topic", and the scene game is carried."""
    import app as server_app  # noqa: PLC0415  (heavy server module)

    plan = _corrected_scene_1_plan_row()
    plan.pop("scene_subject_type", None)
    plan["container_subject"] = "Rockstar Games"
    plan["scene_subject"] = "Rockstar Games"
    plan["visual_subject"] = "Rockstar Games"
    plan["visual_subject_type"] = "organization"
    plan.pop("scene_subject_game", None)
    adapted = server_app._adapt_scene_brain_plans(
        [plan], [GTA_SCENE_TEXTS[0]], GTA_TITLE,
        section_subjects=["Rockstar Games"],
        sections=[{"container_subject": "Rockstar Games",
                   "container_id": "section_6"}])
    assert adapted["web"][0]["scene_subject_type"] == "topic"
    assert adapted["web"][0]["scene_subject_game"] == "gta_5"
    assert adapted["yt"][0]["scene_subject_type"] == "topic"


def test_stock_row_is_exempt_from_shape_but_not_from_game():
    """Stock queries are generic by design: a stock row need not carry
    the scene's entity + intent viewpoint words. It must still name no
    game the scene does not narrate."""
    row = _flat_row(1, "Rockstar Games", "topic", "organization",
                    "game developers studio teamwork", source="stock")
    assert _adapted_scene_row_error(row, scene_text=GTA_SCENE_TEXTS[0], title=GTA_TITLE) is None
    row["search_query"] = "GTA Vice City 2002 gameplay"
    row["primary_query"] = "GTA Vice City 2002 gameplay"
    row["web_query"] = "GTA Vice City 2002 gameplay"
    row["youtube_query"] = "GTA Vice City 2002 gameplay"
    error = _adapted_scene_row_error(row, scene_text=GTA_SCENE_TEXTS[0], title=GTA_TITLE)
    assert error is not None and "2002 Vice City" in error


def test_enrich_scene_queries_adds_the_scene_game():
    from services.media_query_strategy import enrich_scene_queries

    requirement = {
        "canonical_entity": "Rockstar Games",
        "subject_type": "organization",
        "scene_subject_game": "gta_5",
        "visual_intent": "Rockstar Games logo and studio imagery",
    }
    out = enrich_scene_queries(
        ["Grand Theft Auto VI key art official", "GTA VI trailer footage"],
        requirement, GTA_SCENE_TEXTS[0], provider="youtube")
    assert out
    assert "Grand Theft Auto V" in out[0]


def test_visual_requirement_carries_the_scene_game():
    from services.visual_requirement import build_visual_requirement, provider_query_variants

    requirement = build_visual_requirement(
        title=GTA_TITLE, script=" ".join(GTA_SCENE_TEXTS),
        scene_text=GTA_SCENE_TEXTS[0],
        semantic_context={"scene_subject_game": "gta_5"})
    assert requirement["scene_subject_game"] == "gta_5"
    variants = provider_query_variants(
        {"canonical_entity": "Grand Theft Auto VI", "subject_type": "video_game",
         "scene_subject_game": "gta_5"}, provider="youtube")
    assert any("Grand Theft Auto V" in variant for variant in variants)
