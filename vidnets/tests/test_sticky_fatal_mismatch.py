"""Sticky prior_fatal_mismatch_rejection: the file ledger must not veto a new job.

Root cause (run 8, job 62d7671a, GTA VI 1-min, HEAD c39933c): the
``_verdict_conflicts.json`` ledger had no job identity and no expiry, so
every fatal verdict ever recorded vetoed the same (clip, scene) in every
later job before any fresh vision read — because runs 6/7/8 share the
same script, hence the same scene indices and requirement hashes.

Real records from the live ledger (~/workspace/vidnets-runtime/temp/
agent-bridge/_verdict_conflicts.json), reproduced here as fixtures:

- Scene 0, youtube:c5qhgwwLY50: a scene-less LATE bridge record filed
  during run 7 (job 6624244a, 2026-11-19T09:09:29Z) on scene 0's
  requirement hash d4f3ea25... — "four of five sampled frames are GTA V
  footage". Run 7 had ACCEPTED the identical md5 clip for the identical
  scene (coordinator visual final 82.0). In run 8 the hash key matched
  and the clip was vetoed at the visual gate without a fresh read,
  falling back to a frame.
- Scene 10, youtube:MArh6jOUu4g: an adapter-fatal record filed during
  run 7 (09:08:41Z) on (10, MArh6jOUu4g), hash 1b99f083... — run 7's
  live explicit_scene_conflict for that scene. In run 8 the same
  (scene, clip) key matched and the clip was vetoed again, third run
  in a row, without re-verification.
- Scenes 3/4/6/11/12: adapter-fatal records filed DURING run 8 itself
  (12:44–12:45Z) on live vision verdicts — same-job vetoes, which must
  keep working.

These tests prove the fix: a rejection vetoes only the job that filed
it. A new job re-verifies the clip live (the vision gate itself is
untouched — a genuinely mismatched clip is still rejected, by a fresh
verdict, and falls back). Legacy records with no job id are treated as
expired and seed nothing.
"""
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import agent_bridge
from services.media_resolution_coordinator import (
    _seed_fatal_rejections,
    _verification_result,
    resolve_storyboard_media_v2,
)
from services.verdict_conflicts import (
    build_late_fatal_record,
    fatal_content_keys,
    load_fatal_rejects,
    record_late_fatal_reject,
    records_for_job,
)

RUN7_JOB = "6624244a"
RUN8_JOB = "62d7671a"

# Real run-8 requirement hashes (GTA_VI_1Min.manifest.json visual_requirements).
HASH_SCENE_0 = "d4f3ea25186bbf2d5677e6d24ffd8fedd44c5de73adf91b9768e0328dfa3fc98"
HASH_SCENE_10 = "1b99f0835de258f331a0fa75d413fead30d765541038e8e4690a57b97134d294"
HASH_SCENE_3 = "cb4690cb46e3d8dc"


def _clip_file(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2040)
    return str(path)


def _clip_digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _scene(index, requirement_hash, text):
    return {
        "scene_id": f"gta-{index}",
        "scene_index": index,
        "container_id": "gta",
        "text": text,
        "domain_category": "gaming",
        "positive_subjects": ["Grand Theft Auto VI"],
        "forbidden_subjects": [],
        "source_policy": {"youtube_quota": True, "visual_verification_required": False,
                          "youtube_relaxed_visual_gate": True},
        "visual_requirement": {
            "requirement_hash": requirement_hash,
            "canonical_entity": "Vice City",
            "domain": "gaming",
        },
    }


def _accept(path, requirement_hash):
    return {
        "candidate_id": "",
        "identity_score": 82,
        "action_score": 80,
        "context_score": 84,
        "quality": 80,
        "final_score": 82,
        "entity_conflict": False,
        "fatal_mismatch": False,
        "domain_match": True,
        "scene_match": True,
        "reason": "GTA VI Vice City gameplay matches the narration.",
        "accepted": True,
        "status": "accepted",
        "visual_checked": True,
        "visual_available": True,
        "semantic_verified": True,
        "verification_status": "verified",
        "verified_requirement_hash": requirement_hash,
        "verified_asset_sha256": _clip_digest(path),
    }


def _live_fatal_reject(candidate_id):
    return {
        "candidate_id": candidate_id,
        "identity_score": 15,
        "action_score": 20,
        "context_score": 25,
        "quality": 55,
        "final_score": 20,
        "entity_conflict": True,
        "fatal_mismatch": True,
        "domain_match": False,
        "scene_match": False,
        "reason": "Wrong subject for this scene.",
        "visual_checked": True,
        "visual_available": True,
    }


def _run7_c5qh_late_record():
    """The real run-7 scene-less late record for c5qhgwwLY50."""
    return {
        "request_id": "db396daf8975199e13d0a4a4801a9c05",
        "candidate_id": "youtube:c5qhgwwLY50",
        "video_id": "c5qhgwwLY50",
        "asset_sha256": None,
        "scene_index": None,
        "requirement_hash": HASH_SCENE_0,
        "job_id": RUN7_JOB,
        "verdict": dict(
            _live_fatal_reject("youtube:c5qhgwwLY50"),
            reason="Wrong game and wrong era for most of the clip: four of "
                   "five sampled frames are Grand Theft Auto V footage.",
            final_score=32,
        ),
        "received_at": "2026-11-19T09:09:29.145399+00:00",
        "winner": None,
    }


def _run7_marh_record():
    """The run-7 adapter-fatal record for MArh6jOUu4g (real scene 10,
    renumbered to position 2 for this harness — see _run8_resolution)."""
    return {
        "request_id": "adapter-fatal:2:MArh6jOUu4g",
        "candidate_id": "youtube:MArh6jOUu4g",
        "video_id": "MArh6jOUu4g",
        "asset_sha256": None,
        "scene_index": 2,
        "requirement_hash": HASH_SCENE_10,
        "job_id": RUN7_JOB,
        "verdict": dict(
            _live_fatal_reject("youtube:MArh6jOUu4g"),
            reason="GTA VI police shootout gameplay. It shows no preorders, "
                   "no seventeen million copies.",
            final_score=40,
        ),
        "received_at": "2026-11-19T09:08:41.650716+00:00",
        "winner": None,
    }


def _run8_0zy_record():
    """A run-8 same-job adapter-fatal record (real scene 3, renumbered to
    position 1 for this harness — see _run8_resolution)."""
    return {
        "request_id": "adapter-fatal:1:0ZY_bEuJdX4",
        "candidate_id": "youtube:0ZY_bEuJdX4",
        "video_id": "0ZY_bEuJdX4",
        "asset_sha256": None,
        "scene_index": 1,
        "requirement_hash": HASH_SCENE_3,
        "job_id": RUN8_JOB,
        "verdict": dict(
            _live_fatal_reject("youtube:0ZY_bEuJdX4"),
            reason="Explicit scene conflict for scene 3.",
            final_score=40,
        ),
        "received_at": "2026-11-19T12:45:35.745078+00:00",
        "winner": None,
    }


def _seed_ledger(tmp_path, records):
    for record in records:
        record_late_fatal_reject(tmp_path, dict(record))


# --- ledger unit: job filtering ---

def test_records_for_job_keeps_only_matching_job():
    records = [_run7_c5qh_late_record(), _run7_marh_record(), _run8_0zy_record()]
    assert [r["video_id"] for r in records_for_job(records, RUN7_JOB)] == [
        "c5qhgwwLY50", "MArh6jOUu4g"]
    assert [r["video_id"] for r in records_for_job(records, RUN8_JOB)] == [
        "0ZY_bEuJdX4"]
    assert records_for_job(records, "no-such-job") == []


def test_records_for_job_treats_legacy_no_job_records_as_expired():
    legacy = dict(_run7_c5qh_late_record(), job_id=None)
    assert records_for_job([legacy], RUN7_JOB) == []
    assert records_for_job([legacy], RUN8_JOB) == []
    assert records_for_job([legacy], None) == []


def test_fatal_content_keys_job_filter():
    records = [_run7_c5qh_late_record(), _run7_marh_record(), _run8_0zy_record()]
    # Run 7's keys only: scene-less c5qh via its hash, (2, MArh).
    assert fatal_content_keys(records, job_id=RUN7_JOB) == {
        ("requirement_hash", HASH_SCENE_0, "c5qhgwwLY50"),
        (2, "MArh6jOUu4g"),
    }
    assert fatal_content_keys(records, job_id=RUN8_JOB) == {(1, "0ZY_bEuJdX4")}
    # Legacy records never contribute once a job is given.
    legacy = [dict(r, job_id=None) for r in records]
    assert fatal_content_keys(legacy, job_id=RUN8_JOB) == set()
    # No job given: the old whole-ledger behaviour is preserved.
    assert ("requirement_hash", HASH_SCENE_0, "c5qhgwwLY50") in fatal_content_keys(records)


def test_build_late_fatal_record_carries_job_id_from_row():
    row = {"job_id": RUN7_JOB, "messages": []}
    record = build_late_fatal_record(
        "a" * 32, row, dict(_live_fatal_reject("youtube:c5qhgwwLY50")))
    assert record["job_id"] == RUN7_JOB


def test_adapter_fatal_rerecord_in_new_job_files_fresh_record(tmp_path):
    """Adapter request ids are deterministic per (scene, content): without
    the job in the dedupe key, a new job's re-record would return the old
    job's record instead of filing its own."""
    base = _run7_marh_record()
    first = record_late_fatal_reject(tmp_path, dict(base))
    again_same_job = record_late_fatal_reject(tmp_path, dict(base))
    assert again_same_job == first
    assert len(load_fatal_rejects(tmp_path)) == 1
    new_job = dict(base, job_id=RUN8_JOB,
                   received_at="2026-11-19T12:50:00+00:00")
    second = record_late_fatal_reject(tmp_path, new_job)
    assert second["job_id"] == RUN8_JOB
    assert second != first
    assert len(load_fatal_rejects(tmp_path)) == 2


# --- coordinator seed: only this job's records ---

def test_seed_loads_only_this_job(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    _seed_ledger(tmp_path, [_run7_c5qh_late_record(), _run7_marh_record(),
                            _run8_0zy_record()])
    keys, verdicts = _seed_fatal_rejections(job_id=RUN8_JOB)
    assert keys == {(1, "0ZY_bEuJdX4")}
    assert verdicts[(1, "0ZY_bEuJdX4")]["final_score"] == 40
    keys7, _ = _seed_fatal_rejections(job_id=RUN7_JOB)
    assert keys7 == {
        ("requirement_hash", HASH_SCENE_0, "c5qhgwwLY50"),
        (2, "MArh6jOUu4g"),
    }


def test_seed_ignores_legacy_records_with_no_job(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    legacy = [dict(r, job_id=None) for r in
              [_run7_c5qh_late_record(), _run7_marh_record(), _run8_0zy_record()]]
    _seed_ledger(tmp_path, legacy)
    # The live pre-fix ledger is exactly this shape: 133 records, no job
    # id. None of them may seed any job.
    assert _seed_fatal_rejections(job_id=RUN8_JOB) == (set(), {})
    assert _seed_fatal_rejections(job_id=RUN7_JOB) == (set(), {})


def test_seed_without_job_identity_seeds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    _seed_ledger(tmp_path, [_run7_c5qh_late_record(), _run8_0zy_record()])
    assert _seed_fatal_rejections(job_id=None) == (set(), {})


# --- resolve-level replay on the real run-8 scenes ---
#
# Harness notes (production resolves 13 contiguous scenes 0..12; the
# coordinator keys resolutions by row position, so this replay uses
# positions 0/1/2 with the REAL video ids, requirement hashes, scene
# texts and verdicts — real scene 3 is position 1, real scene 10 is
# position 2, and the ledger records above are renumbered to match).
# Candidates carry NO bound visual_decision here: that exercises the
# live-read path, which is what the sticky veto was suppressing (a
# production restored clip carries a bound accept that is reused once
# the veto is gone — the F4y regression test covers that form).
# The fake verifier models "live vision agrees with the recorded
# verdicts": it ACCEPTS scene 0's clip (run 7 accepted the identical
# clip at visual final 82.0) and fatally REJECTS the other two (their
# recorded verdicts are live vision verdicts on the same pixels). The
# fix changes only WHETHER the read happens, never its outcome.

def _run8_resolution(tmp_path, job_id, verifier):
    scenes = [
        _scene(0, HASH_SCENE_0,
               "After a thirteen-year wait, Grand Theft Auto VI arrives today, "
               "November 19, 2026, on PlayStation 5 and Xbox Series XS, and it "
               "brings the series back to Vice City."),
        _scene(1, HASH_SCENE_3, "Jason served in the Army, then worked for drug "
               "runners in the Leonida Keys."),
        _scene(2, HASH_SCENE_10, "Most preordered ever, $1.6B."),
    ]
    clips = {}

    def yt_lane(scene, _cancel):
        index = scene["scene_index"]
        video = {0: "c5qhgwwLY50", 1: "0ZY_bEuJdX4", 2: "MArh6jOUu4g"}[index]
        clip = _clip_file(tmp_path, f"clip-{index}.mp4")
        clips[index] = clip
        return [{
            "candidate_id": f"youtube:{video}",
            "path": clip,
            "source": "youtube",
            "provider": "youtube",
            "media_type": "video",
            # Production YT candidates carry search metadata naming the
            # scene's entity; without it the metadata hard gate drops the
            # candidate before vision ever sees it.
            "title": "Grand Theft Auto VI - Return to Vice City Trailer",
            "description": "Official GTA VI Vice City Leonida gameplay",
            "tags": ["Grand Theft Auto VI", "Vice City", "Leonida"],
            "score": 95,
            "final_score": 95,
        }]

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=yt_lane,
        web_lane=lambda _s, _c: [],
        visual_verifier=verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=10,
        scene_budget_seconds=10,
        vision_reserve_seconds=0.5,
        job_id=job_id,
    )
    return result, clips


def _live_verifier(calls):
    """Accept scene 0's clip; fatally reject scenes 1 and 2 live."""
    def verifier(scene, candidate):
        calls.append((scene["scene_index"], candidate.get("candidate_id")))
        if scene["scene_index"] == 0:
            return dict(_accept(candidate["path"], scene["visual_requirement"]["requirement_hash"]),
                        candidate_id=candidate["candidate_id"])
        return _live_fatal_reject(candidate.get("candidate_id"))
    return verifier


def test_run8_scene0_clip_unblocked_by_run7_record(tmp_path, monkeypatch):
    """The sticky case: run 7's late record must not veto run 8's scene 0.
    The clip gets its fresh live read and is kept."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    _seed_ledger(tmp_path, [_run7_c5qh_late_record(), _run7_marh_record(),
                            _run8_0zy_record()])
    calls = []
    result, clips = _run8_resolution(tmp_path, RUN8_JOB, _live_verifier(calls))
    assert (0, "youtube:c5qhgwwLY50") in calls  # fresh live read happened
    assert result[0].selected_path == clips[0]


def test_run8_same_job_reject_still_vetoes_without_fresh_read(tmp_path, monkeypatch):
    """The gate is untouched for genuine same-job rejections: scene 1's
    run-8 record vetoes the clip at the primary gate — no primary live
    read for that candidate — and the clip stays out of the manifest
    (the pool-recovery re-presentation is live-rejected the same way)."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    _seed_ledger(tmp_path, [_run7_c5qh_late_record(), _run7_marh_record(),
                            _run8_0zy_record()])
    calls = []
    result, clips = _run8_resolution(tmp_path, RUN8_JOB, _live_verifier(calls))
    assert (1, "youtube:0ZY_bEuJdX4") not in calls  # no primary read
    assert result[1].selected_path != clips[1]


def test_run8_scene10_gets_fresh_read_and_gate_still_rejects(tmp_path, monkeypatch):
    """MArh6jOUu4g is unblocked in run 8, so vision looks at it again —
    and a genuinely mismatched clip is still rejected by that fresh
    verdict (same pixels, same scene: police shootout, no preorders),
    not smuggled through. The fix removes stickiness, not the gate."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    _seed_ledger(tmp_path, [_run7_c5qh_late_record(), _run7_marh_record(),
                            _run8_0zy_record()])
    calls = []
    result, clips = _run8_resolution(tmp_path, RUN8_JOB, _live_verifier(calls))
    assert (2, "youtube:MArh6jOUu4g") in calls  # fresh read happened
    assert result[2].selected_path != clips[2]  # gate still rejected it


def test_different_clip_same_scene_not_blocked(tmp_path, monkeypatch):
    """A rejection is scoped to its (clip, scene) pair: another clip for
    scene 0 is verified normally even in the job that filed the veto."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    _seed_ledger(tmp_path, [_run7_c5qh_late_record()])
    scene = _scene(0, HASH_SCENE_0, "Arrives today, back to Vice City.")
    clip = _clip_file(tmp_path, "other.mp4")
    candidate = {
        "candidate_id": "youtube:ZGyKOjovCOI",
        "path": clip,
        "source": "youtube",
        "provider": "youtube",
        "media_type": "video",
        "visual_decision": dict(_accept(clip, HASH_SCENE_0),
                                candidate_id="youtube:ZGyKOjovCOI"),
    }
    accepted, reason, _score, _verdict = _verification_result(
        scene, dict(candidate), lambda _s, _c: (_ for _ in ()).throw(
            AssertionError("verifier must not be called for a vetoed clip")),
        fatal_rejections={("requirement_hash", HASH_SCENE_0, "c5qhgwwLY50")},
        fatal_reject_verdicts={},
    )
    # The ledger holds c5qh, not ZGyK: the bound accept is reused.
    assert accepted is True
    assert reason == "visual_pass"


def test_clip_veto_does_not_block_its_frames(tmp_path, monkeypatch):
    """Frame fallback uses youtube:<vid>:frame:N, a different identifier
    from the clip's youtube:<vid> — a clip veto must not block frames."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    _seed_ledger(tmp_path, [_run7_c5qh_late_record()])
    scene = _scene(0, HASH_SCENE_0, "Arrives today, back to Vice City.")
    clip = _clip_file(tmp_path, "frame.mp4")
    candidate = {
        "candidate_id": "youtube:c5qhgwwLY50:frame:1",
        "path": clip,
        "source": "youtube",
        "provider": "youtube",
        "media_type": "image",
        "visual_decision": dict(_accept(clip, HASH_SCENE_0),
                                candidate_id="youtube:c5qhgwwLY50:frame:1"),
    }
    keys, _ = _seed_fatal_rejections(job_id=RUN7_JOB)
    accepted, reason, _score, _verdict = _verification_result(
        scene, dict(candidate), lambda _s, _c: (_ for _ in ()).throw(
            AssertionError("no live call expected here")),
        fatal_rejections=keys, fatal_reject_verdicts={},
    )
    assert accepted is True
    assert reason == "visual_pass"


def test_bridge_late_record_carries_job_id(tmp_path, monkeypatch):
    """End to end through the bridge: a late fatal answer is filed with
    the request row's job id, so the coordinator can attribute it."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    import json
    import time

    request_id = "d" * 32
    directory = tmp_path / request_id
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    row = {
        "id": request_id,
        "owner_id": "owner",
        "state": "pending",
        "job_id": RUN7_JOB,
        "operation": "visual_review",
        "kind": "vision",
        "messages": [{"role": "user", "content": "Candidate ID: youtube:c5qhgwwLY50\n"}],
        "response_format": {"type": "json_object"},
        "created_at": now,
        "expires_at": now + 120,
    }
    (directory / "request.json").write_text(json.dumps(row), encoding="utf-8")
    clip = _clip_file(tmp_path, "x.mp4")
    agent_bridge.complete_request(request_id, "owner", dict(
        _accept(clip, HASH_SCENE_0),
        candidate_id="youtube:c5qhgwwLY50", final_score=82))
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(request_id, "owner", dict(
            _live_fatal_reject("youtube:c5qhgwwLY50"), final_score=32))
    records = load_fatal_rejects(tmp_path)
    assert len(records) == 1
    assert records[0]["job_id"] == RUN7_JOB
    assert records_for_job(records, RUN8_JOB) == []
    assert records_for_job(records, RUN7_JOB) == records


def test_adapter_fatal_record_stamped_with_current_job(tmp_path, monkeypatch):
    """Writer side: the adapter stamps the current job id on the record
    it files, so the coordinator can attribute it to that job."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    from services.job_context import job_scope
    from services.pipeline_v2_media_adapter import _record_raw_fatal_verdict

    scene = _scene(10, HASH_SCENE_10, "Most preordered ever, $1.6B.")
    candidate = {"candidate_id": "youtube:MArh6jOUu4g"}
    verdict = dict(_live_fatal_reject("youtube:MArh6jOUu4g"), final_score=40)
    with job_scope({"job_id": RUN8_JOB}):
        stored = _record_raw_fatal_verdict(scene, candidate, verdict)
    assert stored is not None
    assert stored["job_id"] == RUN8_JOB
    assert stored["request_id"] == "adapter-fatal:10:MArh6jOUu4g"
    records = load_fatal_rejects(tmp_path)
    assert len(records) == 1
    assert records_for_job(records, RUN8_JOB) == records
    assert records_for_job(records, RUN7_JOB) == []
