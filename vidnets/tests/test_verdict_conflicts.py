"""Bridge half of the vision-consensus fix: late fatal verdicts are kept.

Job 4f61cc9e, scene 1: an ACCEPT for youtube:F4yOVIKCZpw was stored first
and a later REJECT (final_score 20, fatal_mismatch true) was refused by
complete_request and discarded. These tests prove the late fatal verdict
now lands in _verdict_conflicts.json while first-write-wins — and the
raised error — stay exactly as before.
"""
import json
import time

import pytest

from services import agent_bridge
from services.verdict_conflicts import (
    build_late_fatal_record,
    content_key_for,
    fatal_content_keys,
    is_fatal_reject,
    load_fatal_rejects,
    record_late_fatal_reject,
)

# Real late verdict, copied verbatim from
# vidnets-runtime/temp/vidnets-answer-8fc1a5f71d11747927affcc0b7e8ad59.json
# (the hook worker's answer that never entered bridge/server state).
REAL_REJECT = {
    "action_score": 20,
    "candidate_id": "youtube:F4yOVIKCZpw",
    "context_score": 25,
    "domain_match": False,
    "entity_conflict": True,
    "fatal_mismatch": True,
    "final_score": 20,
    "has_text": True,
    "identity_score": 15,
    "quality": 55,
    "reason": "Wrong game for this scene: frame 1 shows GTA V-style race gameplay "
              "with a progress/lap HUD and minimap, and the remaining frames are "
              "generic nightclub and night-city shots with no Vice City, Leonida, "
              "beach or swamp content. The scene requires GTA VI Vice City / "
              "Leonida; GTA V gameplay is a competing game and wrong subject here.",
    "safe_text_regions": [],
    "scene_description": "Nighttime gameplay montage: a street race with a HUD "
                         "(progress, lap counter, timer, minimap), crowded nightclub "
                         "dancing and bar scenes, and night city shots including an "
                         "elevated highway and an aerial view of a neon-lit plaza. "
                         "No beaches, swamps, or Vice City / Leonida landmarks are "
                         "visible in any frame.",
    "scene_match": False,
    "source_presentation_type": "gameplay",
    "subject_box": {"h": 100, "w": 100, "x": 0, "y": 0},
    "visible_overlays": [
        "Frame 1: race HUD with PROGRESS 0%, 3/6, timer 00:07.03 and minimap bottom-left",
        "Frame 3: small HUD icons bottom-right",
        "Frame 4: MEGAMUNDO building sign",
    ],
}

# The winning ACCEPT the resident pool delivered first (response.json).
REAL_ACCEPT = {
    "candidate_id": "youtube:F4yOVIKCZpw",
    "identity_score": 75,
    "action_score": 70,
    "context_score": 78,
    "quality": 72,
    "final_score": 76,
    "entity_conflict": False,
    "fatal_mismatch": False,
    "domain_match": True,
    "scene_match": True,
    "reason": "The Miami-style neon nightlife, clubs and towers match the "
              "narrated neon streets of Vice City in Leonida.",
    "source_presentation_type": "gameplay",
    "has_text": True,
}

REQUIREMENT_HASH = "dfb2a7852862d9b5e4026a5fdf5bbcef96fa4dc0b8238784f49c3dd508314e36"


def _real_messages():
    """The real request's prompt shape: candidate id + visual requirement."""
    requirement = json.dumps({
        "schema_version": "visual_requirement.v2",
        "canonical_entity": "Vice City",
        "scene_subject_game": "gta_6",
        "requirement_hash": REQUIREMENT_HASH,
    })
    text = (
        "Inspect the actual pixels first. Return one JSON object.\n"
        "Candidate ID: youtube:F4yOVIKCZpw\n"
        "Exact narration: The game returns to Vice City, now set in the fictional "
        "state of Leonida, a sun-soaked reimagining of Florida filled with beaches, "
        "swamps, and neon streets.\n\n"
        f"Visual requirement: {requirement}\n"
    )
    return [
        {"role": "system", "content": "You are the final factual visual verifier."},
        {"role": "user", "content": [{"type": "text", "text": text}]},
    ]


def _make_request(root, request_id, *, state="pending", expires_in=120, messages=None):
    directory = root / request_id
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    row = {
        "id": request_id,
        "owner_id": "owner",
        "state": state,
        "job_id": "4f61cc9e",
        "operation": "visual_review",
        "kind": "vision",
        "messages": messages if messages is not None else _real_messages(),
        "response_format": {"type": "json_object"},
        "created_at": now,
        "expires_at": now + expires_in,
    }
    (directory / "request.json").write_text(json.dumps(row), encoding="utf-8")
    return row


def _ledger(root):
    path = root / "_verdict_conflicts.json"
    assert path.is_file(), "ledger was not created"
    return json.loads(path.read_text(encoding="utf-8"))


def test_late_fatal_reject_after_accept_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    request_id = "a" * 32
    _make_request(tmp_path, request_id)
    agent_bridge.complete_request(request_id, "owner", REAL_ACCEPT)
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(request_id, "owner", REAL_REJECT)
    records = _ledger(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record["request_id"] == request_id
    assert record["candidate_id"] == "youtube:F4yOVIKCZpw"
    assert record["video_id"] == "F4yOVIKCZpw"
    assert record["requirement_hash"] == REQUIREMENT_HASH
    assert record["scene_index"] is None  # real rows carry no scene field
    assert record["verdict"] == REAL_REJECT
    assert record["winner"] == REAL_ACCEPT
    assert record["received_at"]
    # The winning response itself is untouched: first-write-wins holds.
    reply = json.loads((tmp_path / request_id / "response.json").read_text(encoding="utf-8"))
    assert json.loads(reply["content"]) == REAL_ACCEPT


def test_late_lenient_accept_after_reject_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    request_id = "b" * 32
    _make_request(tmp_path, request_id)
    agent_bridge.complete_request(request_id, "owner", REAL_REJECT)
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(request_id, "owner", REAL_ACCEPT)
    assert not (tmp_path / "_verdict_conflicts.json").exists()
    assert load_fatal_rejects(tmp_path) == []


def test_late_non_vision_content_is_ignored_without_error(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    request_id = "c" * 32
    _make_request(tmp_path, request_id)
    agent_bridge.complete_request(request_id, "owner", REAL_ACCEPT)
    for late_content in ("not json at all", '{"score": 85}', ["a", "list"], 42):
        with pytest.raises(ValueError, match="expired or was already answered"):
            agent_bridge.complete_request(request_id, "owner", late_content)
    assert not (tmp_path / "_verdict_conflicts.json").exists()


def test_ledger_write_failure_does_not_change_raised_error(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    request_id = "d" * 32
    _make_request(tmp_path, request_id)
    agent_bridge.complete_request(request_id, "owner", REAL_ACCEPT)

    def _boom(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(agent_bridge, "record_late_fatal_reject", _boom)
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(request_id, "owner", REAL_REJECT)
    assert not (tmp_path / "_verdict_conflicts.json").exists()


def test_regression_real_f4yovikczpw_reject_replay(tmp_path, monkeypatch):
    """Replay the real records: the hook worker's REJECT as a late answer.

    Same request id, same prompt (requirement hash dfb2a785...), same
    ACCEPT already stored, same REJECT payload — it must land in the ledger
    keyed by video id F4yOVIKCZpw.
    """
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    request_id = "8fc1a5f71d11747927affcc0b7e8ad59"
    _make_request(tmp_path, request_id)
    agent_bridge.complete_request(request_id, "owner", REAL_ACCEPT)
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(request_id, "owner", dict(REAL_REJECT))
    records = load_fatal_rejects(tmp_path)
    assert len(records) == 1
    assert records[0]["video_id"] == "F4yOVIKCZpw"
    assert records[0]["verdict"]["final_score"] == 20
    assert records[0]["verdict"]["fatal_mismatch"] is True
    assert records[0]["verdict"]["entity_conflict"] is True
    assert records[0]["winner"]["final_score"] == 76
    assert (1, "F4yOVIKCZpw") not in fatal_content_keys(records)
    assert ("requirement_hash", REQUIREMENT_HASH, "F4yOVIKCZpw") in fatal_content_keys(records)
    assert (None, "F4yOVIKCZpw") in fatal_content_keys(
        records, requirement_hash=REQUIREMENT_HASH)
    assert fatal_content_keys(records, requirement_hash="0" * 64) == set()


def test_late_fatal_reject_for_expired_request_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    request_id = "e" * 32
    _make_request(tmp_path, request_id, state="expired", expires_in=-10)
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(request_id, "owner", REAL_REJECT)
    records = _ledger(tmp_path)
    assert len(records) == 1
    assert records[0]["video_id"] == "F4yOVIKCZpw"
    assert records[0]["winner"] is None  # nothing was ever recorded


def test_is_fatal_reject_coercion():
    assert is_fatal_reject({"fatal_mismatch": "true", "candidate_id": "youtube:x"})
    assert is_fatal_reject({"fatal_mismatch": 1})
    assert is_fatal_reject({"entity_conflict": True, "scene_match": False})
    assert is_fatal_reject({"entity_conflict": "yes", "scene_match": "false"})
    assert not is_fatal_reject({"entity_conflict": True})  # no explicit scene_match
    assert not is_fatal_reject({"entity_conflict": True, "scene_match": True})
    assert not is_fatal_reject({"fatal_mismatch": False, "final_score": 76})
    assert not is_fatal_reject({"score": 85})
    assert not is_fatal_reject("not a dict")
    assert not is_fatal_reject(None)


def test_fatal_content_keys_scene_and_hash_rules():
    records = [
        {"scene_index": 1, "video_id": "F4yOVIKCZpw", "candidate_id": "youtube:F4yOVIKCZpw"},
        {"scene_index": 2, "asset_sha256": "abc123", "candidate_id": "web:img"},
        {"scene_index": None, "requirement_hash": REQUIREMENT_HASH,
         "video_id": "F4yOVIKCZpw", "candidate_id": "youtube:F4yOVIKCZpw"},
        {"scene_index": None, "video_id": "NoHashVid", "candidate_id": "youtube:NoHashVid"},
    ]
    keys = fatal_content_keys(records)
    assert (1, "F4yOVIKCZpw") in keys
    assert (2, "abc123") in keys  # asset hash when no video id
    assert ("requirement_hash", REQUIREMENT_HASH, "F4yOVIKCZpw") in keys
    assert (None, "NoHashVid") in keys
    assert fatal_content_keys(records, scene_index=1) == {
        (1, "F4yOVIKCZpw"),
        ("requirement_hash", REQUIREMENT_HASH, "F4yOVIKCZpw"),
        (None, "NoHashVid"),
    }
    # A matching requirement hash turns the scene-less record into a
    # scene key for the queried scene; a non-match contributes nothing.
    assert (1, "F4yOVIKCZpw") in fatal_content_keys(
        records, scene_index=1, requirement_hash=REQUIREMENT_HASH)
    assert ("requirement_hash", REQUIREMENT_HASH, "F4yOVIKCZpw") not in fatal_content_keys(
        records, requirement_hash="f" * 64)
    assert content_key_for(records[0]) == (1, "F4yOVIKCZpw")
    assert content_key_for(records[2]) == ("requirement_hash", REQUIREMENT_HASH, "F4yOVIKCZpw")
    assert content_key_for({}) is None


def test_record_late_fatal_reject_dedupes_and_fills_derived_fields(tmp_path):
    record = build_late_fatal_record(
        "f" * 32, {"messages": _real_messages()}, dict(REAL_REJECT), winner=dict(REAL_ACCEPT))
    assert record["video_id"] == "F4yOVIKCZpw"
    assert record["requirement_hash"] == REQUIREMENT_HASH
    stored = record_late_fatal_reject(tmp_path, record)
    assert stored["request_id"] == "f" * 32
    assert len(load_fatal_rejects(tmp_path)) == 1
    again = record_late_fatal_reject(tmp_path, dict(record))
    assert again == stored
    assert len(load_fatal_rejects(tmp_path)) == 1
    with pytest.raises(ValueError):
        record_late_fatal_reject(tmp_path, {"verdict": dict(REAL_REJECT)})


def test_load_fatal_rejects_tolerates_missing_and_corrupt_ledger(tmp_path):
    assert load_fatal_rejects(tmp_path) == []
    (tmp_path / "_verdict_conflicts.json").write_text("not json", encoding="utf-8")
    assert load_fatal_rejects(tmp_path) == []
    (tmp_path / "_verdict_conflicts.json").write_text('{"not": "a list"}', encoding="utf-8")
    assert load_fatal_rejects(tmp_path) == []
