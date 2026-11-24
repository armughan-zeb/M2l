"""Fatal verdicts scoped per-scene + no filing on incoherent reasoning.

Run 9 (job a7f52c58, GTA VI Launch Day, 18 scenes): the file ledger's
job scoping (HEAD 5039905) is kept, and two more layers are added.

1. PER-SCENE SCOPING. A fatal vetoes only the scene it was filed for.
   The leak vectors closed here: the ``(None, identifier)`` fallback key
   in the coordinator lookup (a hash-less, scene-less record vetoed the
   candidate for EVERY scene) and ``fatal_content_keys`` returning that
   key for a scene query. Records carrying a scene index already only
   matched that scene; scene-less records still match only through their
   requirement hash (their only scene attribution).

   The run-9 replay below uses REAL records from the live ledger
   (~/workspace/vidnets-runtime/temp/agent-bridge/_verdict_conflicts.json)
   and the real per-scene requirement hashes from the run-9 manifest:
   tJbzMqJGH4k:frame:1 was fatally rejected for scenes 6, 9 and 12 (each
   scene's own live verdict) and must veto only those scenes — scenes 1,
   2, 7, 10, 13, 15, 17, which were also offered the frame, keep a fresh
   live read.

2. NO FATAL ON INCOHERENT REASONING. Before filing, the verdict's
   reasoning must be coherent and grounded. The real run-9 verdict for
   youtube:_yl5ELcmugI:frame:1 (scene 4, "current-gen only") reasons
   "the pixels are not the yacht from the earlier clip review" — there
   is no yacht in that request; the yacht came from the same clip's
   scene-10 review. That verdict must NOT be filed (the clip is still
   rejected for scene 4 on its own merits). The genuine fatal for
   youtube:AKoZY5TgpjI (scene 12 — a Germany travel documentary, "Real-
   world drone footage of Dresden, Germany") MUST still be filed.
   Identity notes ("same pixels as the earlier X review") assert the
   frames ARE the same pixels and do not block filing.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import agent_bridge
from services.media_resolution_coordinator import (
    _fatal_ledger_lookup,
    _fatal_ledger_record,
    _seed_fatal_rejections,
)
from services.pipeline_v2_media_adapter import _record_raw_fatal_verdict
from services.verdict_conflicts import (
    fatal_content_keys,
    load_fatal_rejects,
    record_late_fatal_reject,
    records_for_job,
    records_for_scene,
    verdict_reason_coherence_issue,
    verdict_reason_coherent,
)

RUN9_JOB = "a7f52c58"

# Real run-9 requirement hashes (GTA_VI_LaunchDay_1Min.manifest.json).
HASH_SCENE_4 = "193ec888c8f82311fadaad64b25f01caf695330845e591da01b663516a509b0a"
HASH_SCENE_6 = "32b4f50edc39"
HASH_SCENE_9 = "bf38908516e1"
HASH_SCENE_12 = "98741d311ded"

# Real run-9 verdicts, copied verbatim from the live ledger.
YACHT_VERDICT = {
    "candidate_id": "youtube:_yl5ELcmugI:frame:1",
    "identity_score": 50,
    "action_score": 40,
    "context_score": 25,
    "quality": 40,
    "final_score": 40,
    "entity_conflict": False,
    "fatal_mismatch": True,
    "domain_match": True,
    "scene_match": False,
    "reason": "A dark, blurry nightclub frame — a woman dancing in neon light "
              "with car headlights behind; despite the frame id, the pixels "
              "are not the yacht from the earlier clip review, and nothing "
              "here shows consoles, platforms, or anything current-gen — the "
              "scene's subject is entirely absent.",
    "scene_description": "current-gen only.",
    "source_presentation_type": "gameplay_footage",
    "has_text": False,
}

# The scene-4 request text (bridge request c5dfc5e2...): narration +
# film context + metadata hint. No yacht anywhere in it.
SCENE4_REQUEST_TEXT = (
    "Exact narration: current-gen only. Visual requirement: "
    '{"canonical_entity": "Grand Theft Auto VI", "subject_type": "video_game", '
    '"scene_subject_game": "gta_6", "positive_evidence": ["Grand Theft Auto VI", '
    '"current-gen only."]} Positive subjects: Grand Theft Auto VI. Film context: '
    "Grand Theft Auto 6 is finally here. After more than a decade of waiting, "
    "launch day has arrived. It launched at midnight, November 19th, on "
    "PlayStation 5 and Xbox Series X and S. No PC, no last-gen — current-gen only. "
    "Metadata hint: Grand Theft Auto VI: Out Now | Today we are proud to announce "
    "that Grand Theft Auto VI is now available worldwide for PlayStation 5 and "
    "XBOX Series X|S. | youtube"
)

AKOZY_VERDICT = {
    "candidate_id": "youtube:AKoZY5TgpjI",
    "identity_score": 15,
    "action_score": 20,
    "context_score": 25,
    "quality": 55,
    "final_score": 40,
    "entity_conflict": True,
    "fatal_mismatch": True,
    "domain_match": False,
    "scene_match": False,
    "reason": "Real-world drone footage of Dresden, Germany — the Frauenkirche "
              "dome and red-roofed old town — across three frames; a real "
              "European city, no game, no console, no frame rate; entirely "
              "the wrong place and domain for the scene.",
    "scene_description": "Expect 30 frames per second on every console —",
    "visual_checked": True,
}

# Genuine fatal whose reason carries an identity note: still files.
AKOZY_FRAME1_VERDICT = dict(
    AKOZY_VERDICT,
    candidate_id="youtube:AKoZY5TgpjI:frame:1",
    reason="Real-world drone footage of Dresden, Germany — the Frauenkirche "
           "dome and red-roofed old town — the same pixels as the scene 12 "
           "clip review; a real European city, no game, no console, no frame "
           "rate; entirely the wrong place and domain.",
)

TJbz_FRAME1_SCENE6 = {
    "candidate_id": "youtube:tJbzMqJGH4k:frame:1",
    "final_score": 40,
    "fatal_mismatch": True,
    "scene_match": False,
    "reason": "GTA VI party interior — Lucia at a counter, the same pixels as "
              "the scene 3/4 frame 1; no download, no 212 gigabytes, no "
              "pre-loads, no patches — the scene's subject is entirely absent.",
    "scene_description": "The download runs up to 212 gigabytes, pre-loads "
                         "opened November 10th, and two patches are already out —",
    "visual_checked": True,
}

TJbz_FRAME1_SCENE9 = dict(
    TJbz_FRAME1_SCENE6,
    reason="GTA VI party interior — Lucia at a counter, the same pixels as "
           "the scene 3/4/6 frame 1; no pre-orders, no Vintage Vice City "
           "Pack, and no GTA+ — the scene's subject is entirely absent.",
    scene_description="Pre-orders get the Vintage Vice City Pack plus a month of GTA+.",
)


def _run9_tjbz_record(scene_index, verdict):
    return {
        "request_id": f"adapter-fatal:{scene_index}:tJbzMqJGH4k:frame:1",
        "candidate_id": "youtube:tJbzMqJGH4k:frame:1",
        "video_id": "tJbzMqJGH4k:frame:1",
        "asset_sha256": None,
        "scene_index": scene_index,
        "requirement_hash": {6: HASH_SCENE_6, 9: HASH_SCENE_9,
                             12: HASH_SCENE_12}[scene_index],
        "job_id": RUN9_JOB,
        "verdict": dict(verdict),
        "received_at": "2026-11-19T15:35:18+00:00",
        "winner": None,
    }


def _scene(index, requirement_hash, text=""):
    return {
        "scene_id": f"scn-{index}",
        "scene_index": index,
        "text": text,
        "visual_requirement": {"requirement_hash": requirement_hash},
    }


# --- Part 1: per-scene scoping ---

def test_scene6_tjbz_fatal_does_not_veto_other_scenes():
    """The run-9 replay: tJbz frame:1 fatals filed for scenes 6/9/12 veto
    only those scenes. Scenes 1, 2, 7, 10, 13, 15, 17 were also offered
    the frame and keep a fresh live read."""
    records = [
        _run9_tjbz_record(6, TJbz_FRAME1_SCENE6),
        _run9_tjbz_record(9, TJbz_FRAME1_SCENE9),
        _run9_tjbz_record(12, dict(TJbz_FRAME1_SCENE6,
                                   reason="GTA VI party interior — a woman at "
                                          "a counter; it shows no consoles and "
                                          "no frame rate.")),
    ]
    scoped = records_for_job(records, RUN9_JOB)
    seed = set(fatal_content_keys(scoped))
    assert seed == {
        (6, "tJbzMqJGH4k:frame:1"),
        (9, "tJbzMqJGH4k:frame:1"),
        (12, "tJbzMqJGH4k:frame:1"),
    }
    hashes = {6: HASH_SCENE_6, 9: HASH_SCENE_9, 12: HASH_SCENE_12}
    for scene_index in (1, 2, 6, 7, 9, 10, 12, 13, 15, 17):
        scene = _scene(scene_index, hashes.get(scene_index, "hash-%d" % scene_index))
        key, _ = _fatal_ledger_lookup(
            scene_index, {"candidate_id": "youtube:tJbzMqJGH4k:frame:1"},
            scene, seed, {})
        if scene_index in (6, 9, 12):
            assert key == (scene_index, "tJbzMqJGH4k:frame:1")
        else:
            assert key is None, f"scene {scene_index} must not be vetoed"


def test_hashless_sceneless_record_vetoes_no_scene():
    """The closed leak: a record with no scene index and no requirement
    hash used to seed a (None, identifier) key that the lookup matched
    for EVERY scene. Now it vetoes nothing."""
    record = {
        "request_id": "adapter-fatal:unknown:NoHashVid",
        "candidate_id": "youtube:NoHashVid",
        "video_id": "NoHashVid",
        "asset_sha256": None,
        "scene_index": None,
        "requirement_hash": None,
        "job_id": RUN9_JOB,
        "verdict": {"fatal_mismatch": True, "final_score": 20},
        "received_at": "2026-11-19T15:00:00+00:00",
        "winner": None,
    }
    assert fatal_content_keys([record], scene_index=3) == set()
    assert fatal_content_keys([record], scene_index=3,
                              requirement_hash="a" * 64) == set()
    # Whole-ledger inspection still shows it (unchanged behaviour).
    assert (None, "NoHashVid") in fatal_content_keys([record])
    seed = set(fatal_content_keys(records_for_job([record], RUN9_JOB)))
    for scene_index in (0, 3, 9):
        key, _ = _fatal_ledger_lookup(
            scene_index, {"candidate_id": "youtube:NoHashVid"},
            _scene(scene_index, "hash-%d" % scene_index), seed, {})
        assert key is None


def test_sceneless_hash_record_vetoes_only_its_hash_scene():
    """A scene-less record's only scene attribution is its requirement
    hash: it vetoes the scene with that hash, and no other scene."""
    record = {
        "request_id": "db396daf8975199e13d0a4a4801a9c05",
        "candidate_id": "youtube:c5qhgwwLY50",
        "video_id": "c5qhgwwLY50",
        "asset_sha256": None,
        "scene_index": None,
        "requirement_hash": HASH_SCENE_4,
        "job_id": RUN9_JOB,
        "verdict": {"fatal_mismatch": True, "final_score": 32},
        "received_at": "2026-11-19T09:09:29+00:00",
        "winner": None,
    }
    seed = set(fatal_content_keys(records_for_job([record], RUN9_JOB)))
    assert seed == {("requirement_hash", HASH_SCENE_4, "c5qhgwwLY50")}
    key, _ = _fatal_ledger_lookup(
        4, {"candidate_id": "youtube:c5qhgwwLY50"},
        _scene(4, HASH_SCENE_4), seed, {})
    assert key == ("requirement_hash", HASH_SCENE_4, "c5qhgwwLY50")
    key, _ = _fatal_ledger_lookup(
        6, {"candidate_id": "youtube:c5qhgwwLY50"},
        _scene(6, HASH_SCENE_6), seed, {})
    assert key is None


def test_records_for_scene():
    records = [
        _run9_tjbz_record(6, TJbz_FRAME1_SCENE6),
        _run9_tjbz_record(9, TJbz_FRAME1_SCENE9),
        {"request_id": "x", "candidate_id": "youtube:c5qhgwwLY50",
         "video_id": "c5qhgwwLY50", "scene_index": None,
         "requirement_hash": HASH_SCENE_4, "job_id": RUN9_JOB, "verdict": {}},
        {"request_id": "y", "candidate_id": "youtube:NoHashVid",
         "video_id": "NoHashVid", "scene_index": None,
         "requirement_hash": None, "job_id": RUN9_JOB, "verdict": {}},
    ]
    assert [r["candidate_id"] for r in records_for_scene(records, 6)] == [
        "youtube:tJbzMqJGH4k:frame:1"]
    assert [r["candidate_id"] for r in records_for_scene(
        records, 4, HASH_SCENE_4)] == ["youtube:c5qhgwwLY50"]
    # The hash-less scene-less record belongs to no scene.
    assert records_for_scene(records, 4, "f" * 64) == []
    assert records_for_scene(records, None) == []


def test_seed_drops_unattributable_keys(tmp_path, monkeypatch):
    """_seed_fatal_rejections no longer carries (None, identifier) keys:
    they can never match at lookup, so the seeded count is vetoable keys."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    records = [
        _run9_tjbz_record(6, TJbz_FRAME1_SCENE6),
        {"request_id": "adapter-fatal:unknown:NoHashVid",
         "candidate_id": "youtube:NoHashVid", "video_id": "NoHashVid",
         "asset_sha256": None, "scene_index": None, "requirement_hash": None,
         "job_id": RUN9_JOB, "verdict": {"fatal_mismatch": True},
         "received_at": "2026-11-19T15:00:00+00:00", "winner": None},
    ]
    for record in records:
        record_late_fatal_reject(tmp_path, dict(record))
    keys, _ = _seed_fatal_rejections(job_id=RUN9_JOB)
    assert keys == {(6, "tJbzMqJGH4k:frame:1")}


# --- Part 2: coherence gate ---

def test_yacht_verdict_is_incoherent():
    """The run-9 case, verbatim: the yacht comes from the earlier clip
    review, not from this request."""
    assert verdict_reason_coherent(YACHT_VERDICT) is False
    assert verdict_reason_coherence_issue(YACHT_VERDICT) == (
        "cross_review_reference:earlier clip review")
    # With the real request text the diagnosis names the foreign entity:
    # "yacht" appears in neither the scene text nor the metadata hint.
    assert verdict_reason_coherence_issue(
        YACHT_VERDICT, SCENE4_REQUEST_TEXT) == (
        "foreign_entity_from_other_review:yacht")


def test_genuine_akozy_fatal_is_coherent():
    """A Germany travel documentary for a 30-fps scene: genuinely wrong,
    grounded in the frame itself — it MUST still be filed."""
    assert verdict_reason_coherent(AKOZY_VERDICT) is True
    assert verdict_reason_coherence_issue(AKOZY_VERDICT) is None
    assert verdict_reason_coherent(AKOZY_VERDICT, SCENE4_REQUEST_TEXT) is True


def test_identity_notes_do_not_block_filing():
    """'Same pixels as the earlier X review' asserts pixel identity —
    the operative claims still describe this frame."""
    assert verdict_reason_coherent(AKOZY_FRAME1_VERDICT) is True
    assert verdict_reason_coherent(TJbz_FRAME1_SCENE6) is True
    assert verdict_reason_coherent(TJbz_FRAME1_SCENE9) is True
    # The scene-10 yacht frame describes its own pixels (a speeding
    # yacht IS in the frame); the trailing identity note is not imported
    # reasoning.
    scene10_frame1 = {
        "candidate_id": "youtube:_yl5ELcmugI:frame:1",
        "fatal_mismatch": True,
        "final_score": 40,
        "reason": "A speeding GTA VI yacht with Ship's And Giggles text; it "
                  "shows no boxed copy, no disc, and no download code — the "
                  "scene's subject is entirely absent. Same pixels as frame 1 "
                  "of the earlier _yl5ELcmugI clip review.",
    }
    assert verdict_reason_coherent(scene10_frame1) is True


def test_missing_or_empty_reason_is_coherent():
    """Absence of reasoning is not evidence of contamination: file it."""
    assert verdict_reason_coherent({"fatal_mismatch": True}) is True
    assert verdict_reason_coherent({"fatal_mismatch": True, "reason": ""}) is True
    assert verdict_reason_coherent(None) is True
    assert verdict_reason_coherent("not a dict") is True


def test_adapter_does_not_file_yacht_verdict(tmp_path, monkeypatch):
    """The path that filed the run-9 yacht record (adapter-fatal:4:...)
    now refuses it; the genuine AKoZY fatal still files."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    from services.job_context import job_scope

    scene4 = _scene(4, HASH_SCENE_4, "current-gen only.")
    candidate = {"candidate_id": "youtube:_yl5ELcmugI:frame:1",
                 "title": "Grand Theft Auto VI: Out Now"}
    with job_scope({"job_id": RUN9_JOB}):
        assert _record_raw_fatal_verdict(scene4, candidate, dict(YACHT_VERDICT)) is None
    assert load_fatal_rejects(tmp_path) == []

    scene12 = _scene(12, HASH_SCENE_12,
                     "Expect 30 frames per second on every console —")
    candidate12 = {"candidate_id": "youtube:AKoZY5TgpjI",
                   "title": "Dresden Germany Travel Guide"}
    with job_scope({"job_id": RUN9_JOB}):
        stored = _record_raw_fatal_verdict(scene12, candidate12, dict(AKOZY_VERDICT))
    assert stored is not None
    assert stored["request_id"] == "adapter-fatal:12:AKoZY5TgpjI"
    assert len(load_fatal_rejects(tmp_path)) == 1


def test_bridge_does_not_file_late_yacht_verdict(tmp_path, monkeypatch):
    """Late-fatal path: a contaminated late answer is still refused, but
    no ledger entry is created; a coherent late fatal still files."""
    import json
    import time

    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    request_id = "c" * 32
    directory = tmp_path / request_id
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    row = {
        "id": request_id,
        "owner_id": "owner",
        "state": "pending",
        "job_id": RUN9_JOB,
        "operation": "visual_review",
        "kind": "vision",
        "messages": [{"role": "user", "content": SCENE4_REQUEST_TEXT}],
        "response_format": {"type": "json_object"},
        "created_at": now,
        "expires_at": now + 120,
    }
    (directory / "request.json").write_text(json.dumps(row), encoding="utf-8")
    accept = dict(YACHT_VERDICT, fatal_mismatch=False, final_score=82)
    agent_bridge.complete_request(request_id, "owner", accept)
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(request_id, "owner", dict(YACHT_VERDICT))
    assert load_fatal_rejects(tmp_path) == []


def test_in_memory_ledger_does_not_record_yacht_verdict():
    """The coordinator's in-memory ledger applies the same gate."""
    fatal_rejections, fatal_reject_verdicts = set(), {}
    scene4 = _scene(4, HASH_SCENE_4, "current-gen only.")
    candidate = {"candidate_id": "youtube:_yl5ELcmugI:frame:1",
                 "title": "Grand Theft Auto VI: Out Now"}
    assert _fatal_ledger_record(4, candidate, dict(YACHT_VERDICT), scene4,
                                fatal_rejections, fatal_reject_verdicts) is None
    assert fatal_rejections == set()
    scene12 = _scene(12, HASH_SCENE_12, "Expect 30 fps on every console.")
    candidate12 = {"candidate_id": "youtube:AKoZY5TgpjI"}
    assert _fatal_ledger_record(12, candidate12, dict(AKOZY_VERDICT), scene12,
                                fatal_rejections, fatal_reject_verdicts) == (
        12, "AKoZY5TgpjI")
    assert fatal_rejections == {(12, "AKoZY5TgpjI")}
