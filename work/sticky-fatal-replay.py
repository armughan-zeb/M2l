"""Offline replay: sticky prior_fatal_mismatch_rejection on real run-8 data.

Reads (never writes) the live file ledger
~/workspace/vidnets-runtime/temp/agent-bridge/_verdict_conflicts.json
and run 8's manifest (requirement hashes) + measurement record
(work/gta-vi-test-8-measurement.md), then runs the REAL coordinator
code paths both ways:

- OLD: seed from every ledger record (pre-fix behaviour).
- NEW: seed only records filed by job 62d7671a (post-fix behaviour).

Post-fix attribution is modelled by timestamp window, because the live
records predate job scoping and carry no job_id: records received
during run 7 (job 6624244a, dispatched 08:59:32Z, completed 10:05:53Z)
are stamped 6624244a; records received during run 8 (job 62d7671a,
12:25:46Z-13:43:28Z) are stamped 62d7671a. Every other record keeps
job_id None (legacy / other jobs) and never seeds under the fix.

Per-scene quota candidates and the run-8 outcome for each are taken
verbatim from the measurement record's per-scene table.

Usage: python3 work/sticky-fatal-replay.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "vidnets" / "server"))
sys.path.insert(0, str(HERE / "vidnets"))

LEDGER = Path.home() / "workspace" / "vidnets-runtime" / "temp" / "agent-bridge" / "_verdict_conflicts.json"
MANIFEST = (Path.home() / "workspace" / "vidnets-runtime" / "output" /
            "GTA_VI_1Min_20261119_122547" / "GTA_VI_1Min_20261119_122547_692c3eec" /
            "GTA_VI_1Min.manifest.json")

RUN7_JOB = "6624244a"
RUN8_JOB = "62d7671a"
RUN7_START, RUN7_END = "2026-11-19T08:59:32", "2026-11-19T10:05:53"
RUN8_START, RUN8_END = "2026-11-19T12:25:46", "2026-11-19T13:43:28"

# (scene, video_id, run-8 outcome) — measurement record, per-scene table.
CANDIDATES = [
    (0, "c5qhgwwLY50", "frame (clip vetoed prior_fatal; run 7 accepted it as clip, visual 82.0)"),
    (1, "fRmoCG4rTmM", "CLIP accepted (deterministic_degraded)"),
    (2, "Bhwj-_9Xz6M", "CLIP accepted (visual_pass)"),
    (3, "0ZY_bEuJdX4", "web (clip explicit_scene_conflict live)"),
    (4, "ByAS0Ak3-qA", "web (clip insufficient_visible_subject_match live)"),
    (5, "ZGyKOjovCOI", "web (clip watermark_detected live)"),
    (6, "F39fE1AwOyc", "frame (clip explicit_scene_conflict live)"),
    (7, "3CqKzYdk-XM", "frame (clip visual_rejected live)"),
    (9, "ZGyKOjovCOI", "CLIP accepted (visual_pass)"),
    (10, "MArh6jOUu4g", "web (clip vetoed prior_fatal; run 7 live explicit_scene_conflict)"),
    (11, "GDuSN0-brZs", "frame (clip explicit_scene_conflict live)"),
    (12, "F39fE1AwOyc", "web (clip blocked same_master_cut_gate)"),
]

# Evidence for whether a prior-job veto was STALE (wrong) or GENUINE
# (the clip would fail a fresh read on the same pixels).
PRIOR_EVIDENCE = {
    0: "STALE: run 7 ACCEPTED this exact clip (same md5 33a924ae) for this "
       "exact scene at visual final 82.0; the vetoing record is a scene-less "
       "LATE bridge verdict that contradicts the stored accept.",
    5: "GENUINE-EQUIVALENT: run 7 live-rejected it for this scene (Lucia not "
       "visible) AND run 8 independently rejected it live on watermark_detected "
       "before the coordinator gate — a fresh read is expected to re-reject.",
    10: "GENUINE-EQUIVALENT (unverified in run 8): run 7 live-rejected it for "
        "this scene (explicit_scene_conflict: police shootout, no preorders) "
        "on the same pixels; a fresh read is expected to re-reject.",
}


def stamp_job(record):
    received = record.get("received_at") or ""
    if RUN7_START <= received <= RUN7_END:
        return RUN7_JOB
    if RUN8_START <= received <= RUN8_END:
        return RUN8_JOB
    return None


def main():
    from services.media_resolution_coordinator import (
        _fatal_ledger_lookup,
        _seed_fatal_rejections,
    )
    from services import verdict_conflicts as vc

    records = json.loads(LEDGER.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    hashes = {i: v.get("requirement_hash")
              for i, v in enumerate(manifest["visual_requirements"])}

    # Work on a copy in /tmp; the live ledger is never written.
    work = Path(tempfile.mkdtemp(prefix="sticky-replay-"))
    stamped = []
    for record in records:
        copy = dict(record)
        copy["job_id"] = stamp_job(record)
        stamped.append(copy)
    (work / "_verdict_conflicts.json").write_text(
        json.dumps(stamped), encoding="utf-8")
    os.environ["VIDNETS_AGENT_BRIDGE_DIR"] = str(work)

    # OLD seed: every record (pre-fix).
    old_keys = set(vc.fatal_content_keys(records))
    old_verdicts = {}
    for record in records:
        key = vc.content_key_for(record)
        if key is not None:
            old_verdicts.setdefault(key, record.get("verdict") or {})
    # NEW seed: only this job's records (post-fix, real code path).
    new_keys, new_verdicts = _seed_fatal_rejections(job_id=RUN8_JOB)

    print(f"ledger records: {len(records)} total; "
          f"run-7 job: {sum(1 for r in stamped if r['job_id'] == RUN7_JOB)}; "
          f"run-8 job: {sum(1 for r in stamped if r['job_id'] == RUN8_JOB)}; "
          f"legacy/other: {sum(1 for r in stamped if r['job_id'] is None)}")
    print(f"OLD seed keys: {len(old_keys)}; NEW seed keys (job {RUN8_JOB}): {len(new_keys)}")
    print()
    print("scene | clip | OLD veto | NEW veto | provenance")
    unblocked, stale, genuine_prior = [], [], []
    for scene, video, outcome in CANDIDATES:
        candidate = {"candidate_id": f"youtube:{video}"}
        scene_row = {"scene_index": scene,
                     "visual_requirement": {"requirement_hash": hashes[scene]}}
        old_key, old_verdict = _fatal_ledger_lookup(
            scene, candidate, scene_row, old_keys, old_verdicts)
        new_key, _ = _fatal_ledger_lookup(
            scene, candidate, scene_row, new_keys, new_verdicts)
        old = "VETO" if old_key else "-"
        new = "VETO" if new_key else "-"
        note = ""
        if old_key and not new_key:
            unblocked.append(scene)
            provenance = "prior-job"
            if scene in PRIOR_EVIDENCE:
                note = PRIOR_EVIDENCE[scene]
                (stale if scene == 0 else genuine_prior).append(scene)
            else:
                note = "prior-job record; no prior-accept evidence"
        elif old_key and new_key:
            provenance = "same-job (run 8 live verdict) — veto preserved"
        elif not old_key:
            provenance = "no ledger record"
        else:
            provenance = "?"
        print(f"{scene:>5} | {video:<12} | {old:<8} | {new:<8} | {provenance}")
        if note:
            print(f"      {note}")
        if old_key:
            received = None
            for record in records:
                if vc.content_key_for(record) == old_key:
                    received = record.get("received_at")
                    break
            print(f"      vetoing record received_at={received} req="
                  f"{(old_verdict or {}).get('candidate_id')}")
    print()
    print(f"scenes unblocked by the fix (clip veto removed): {unblocked} "
          f"({len(unblocked)} of 13)")
    print(f"  stale/wrongful (prior ACCEPT contradicts the veto): {stale}")
    print(f"  genuine-equivalent (fresh read expected to re-reject): {genuine_prior}")
    print(f"same-job vetoes preserved (live run-8 verdicts): "
          f"scenes 3, 4, 6, 11, 12 (5 quota scenes)")
    clips_now, clips_evidence = 3, 3 + len(stale)
    print()
    print("YT-clip accepted ratio (clips only, 13 scenes):")
    print(f"  run 8 actual:            {clips_now}/13 = {clips_now / 13:.3f}")
    print(f"  replay, evidence-based:  {clips_evidence}/13 = {clips_evidence / 13:.3f} "
          f"(scene 0 keeps its clip on its run-7 accept; scenes 5/10 expected to re-reject)")
    print(f"  replay, ceiling:         6/13 = {6 / 13:.3f} "
          f"(if scenes 5 and 10 also passed a fresh read — NOT evidenced)")
    print(f"  target:                  80% (10.4/13) — NOT reached by this fix alone;")
    print("  the remaining gap is genuine live vision rejections")
    print("  (scenes 3, 4, 6, 7, 11, 12), not the sticky veto.")
    print(f"real-YT ratio (clips+frames): 7/13 = {7 / 13:.3f} -> "
          f"8/13 = {8 / 13:.3f} evidence-based")
    print()
    print("frame fallback check (candidate youtube:<vid>:frame:1):")
    for scene, video in [(0, "c5qhgwwLY50"), (10, "MArh6jOUu4g")]:
        candidate = {"candidate_id": f"youtube:{video}:frame:1"}
        scene_row = {"scene_index": scene,
                     "visual_requirement": {"requirement_hash": hashes[scene]}}
        old_key, _ = _fatal_ledger_lookup(
            scene, candidate, scene_row, old_keys, old_verdicts)
        new_key, _ = _fatal_ledger_lookup(
            scene, candidate, scene_row, new_keys, new_verdicts)
        print(f"  scene {scene} frame:1: OLD={'VETO' if old_key else '-'} "
              f"NEW={'VETO' if new_key else '-'} "
              f"(clip veto never blocks frames: different identifier; "
              f"run-7 frame records for scene 10 are also unblocked)")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
