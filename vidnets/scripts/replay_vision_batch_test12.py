#!/usr/bin/env python3
"""Replay batched YouTube-lane vision on REAL GTA test-12 data (job 5137cb34).

No network, no running server, no live vision. Reads only the test-12
artifacts:

- ``<run>/youtube_scene_*.attempts.json`` (main YT pass) and
  ``<run>/youtube_content_recovery/youtube_scene_*.attempts.json``
  (recovery YT pass): which YouTube candidates each scene-pass downloaded,
  in order. The old progressive loop visions every downloaded candidate
  exactly once, so the downloaded-candidate count IS the sequential
  vision-request count.
- ``<output>/.../media/manifest.json`` (``media_resolution_v2``): per-scene
  winner + ``visual_decision`` (the recorded vision verdict for the
  winning YouTube candidate).

Then:
  1. Counts sequential vision requests BEFORE (one per downloaded
     candidate) vs batched requests AFTER (one per scene-pass) — as a
     COUNTERFACTUAL for a vision-enabled progressive run. The artifacts
     show the progressive gate's vision did NOT run in test 12 at all:
     ``visual_checked`` is None on 16 of 17 downloaded candidates and
     False on the 17th, and there is no ``contact_probe`` entry anywhere
     in the run dir — so the old loop filed 0 vision requests in this
     job (first download won per scene-pass) and the vision that did run
     was the coordinator's finalist verification (a separate code path
     this task does not touch). The 17 vs 17 numbers below are what the
     old loop WOULD have filed vs what the new code files for the same
     scene-passes with vision enabled.
  2. Runs the NEW batched code path (``_batched_visual_verdicts``) on every
     scene-pass with a stub vision client whose answers are the recorded
     verdicts, extracting REAL frames from the scene's real clip file with
     ffmpeg, and checks the batched path picks the same winner test 12
     recorded. The progressive gate's per-candidate verdicts were not
     persisted (the run-dir files are download-time snapshots), so the
     stub answers use the recorded evidence: a scene-pass's downloaded
     candidate is an ACCEPT — the YT lane returned a clip path for every
     one of these passes (all 11 ``yt_clip_scene_*.mp4`` files exist), so
     the progressive gate did not hard-reject any of them. Scores come
     from the manifest ``visual_decision`` where the candidate is a
     recorded YouTube winner, else a neutral 70. The coordinator's own
     final-gate verdicts are deliberately NOT used as progressive-gate
     answers: they are a different gate (a candidate the coordinator
     rejected could still have passed the progressive gate).
  3. Builds a batched response from the recorded ``visual_decision``
     verdicts and validates it through ``validate_answer.py`` against the
     new batch schema (valid passes, malformed fails).

Usage:
    python3 vidnets/scripts/replay_vision_batch_test12.py
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VIDNETS = REPO_ROOT / "vidnets"
RUN_DIR = Path("/home/hatch/workspace/vidnets-runtime/temp/jobs/5137cb34/5137cb34-run-0001-ebb19221693f")
MANIFEST = Path("/home/hatch/workspace/vidnets-runtime/output/GTA_VI_1Min_20261120_120122/GTA_VI_1Min_20261120_120122_46f68e69/media/manifest.json")

sys.path.insert(0, str(VIDNETS / "server"))
sys.path.insert(0, str(VIDNETS))

from services.visual_intelligence.prompts import CLIP_BATCH_RESPONSE_FORMAT  # noqa: E402
from services.visual_intelligence.vision_client import _normalise_clip_response  # noqa: E402


def _load_scene_passes():
    """Return {(pass_name, scene_index): [candidate_id, ...]} in download order."""
    passes = {}
    for pass_name, pattern in (
        ("main", "youtube_scene_*.attempts.json"),
        ("recovery", "youtube_content_recovery/youtube_scene_*.attempts.json"),
    ):
        for path in sorted(RUN_DIR.glob(pattern)):
            scene_index = int(path.name.split("scene_")[1][:3]) - 1
            rows = json.loads(path.read_text(encoding="utf-8"))
            downloaded = [
                str(row.get("candidate_id") or "")
                for row in rows
                if isinstance(row, dict) and row.get("status") == "downloaded"
            ]
            if downloaded:
                passes[(pass_name, scene_index)] = downloaded
    return passes


def _load_manifest():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out = {}
    for row in data["metadata"]["media_resolution_v2"]:
        out[int(row["scene_index"])] = row
    return out


def _accept_row(candidate_id, score, fatal=False, talking_head=False):
    return {
        "candidate_id": candidate_id,
        "verdict": "reject" if fatal else "accept",
        "reason": "recorded test-12 verdict",
        "score": float(score),
        "story_relevance": float(score),
        "confidence": 85.0,
        "fatal_mismatch": bool(fatal),
        "talking_head": bool(talking_head),
    }


def main():
    passes = _load_scene_passes()
    manifest = _load_manifest()
    before = sum(len(cands) for cands in passes.values())
    after = len(passes)
    # Actual test-12 fact: the progressive gate's vision never ran (no
    # contact_probe anywhere in the run dir; visual_checked is None/False
    # on every downloaded candidate). 17/17 below is the vision-enabled
    # counterfactual for the same scene-passes.
    print("progressive-gate vision in test 12: DID NOT RUN "
          "(visual_checked None/False on all 17 downloads, no contact_probe)")
    print(f"counterfactual BEFORE (sequential, one request per candidate): {before}")
    print(f"counterfactual AFTER  (batched, one request per scene-pass):   {after}")
    print("NOTE: every test-12 scene-pass tried exactly one candidate, so the")
    print("request count is flat ON THIS JOB. The batching win is coverage")
    print("per request: one batch judges a scene's top candidates (up to 4)")
    print("instead of one — the old loop would need up to 4 sequential")
    print("requests per scene to judge the same candidates.")

    # --- agreement: run the NEW batched path on the recorded candidates ---
    import services.youtube_pipeline_v2 as ytv2
    from services.visual_intelligence import clip_pipeline as cp

    agreed = 0
    checked = 0
    details = []
    for (pass_name, scene_index), cand_ids in sorted(passes.items()):
        clip_file = RUN_DIR / f"yt_clip_scene_{scene_index + 1:03d}.mp4"
        if not clip_file.is_file():
            details.append((pass_name, scene_index, "SKIP", "no clip file for frame extraction"))
            continue
        rows = []
        for cand_id in cand_ids:
            manifest_row = manifest.get(scene_index, {})
            winner = str(manifest_row.get("candidate_id") or "")
            vd = manifest_row.get("visual_decision") or {}
            if winner == f"youtube:{cand_id}" and vd.get("accepted"):
                score = float(vd.get("final_score") or 70.0)
                rows.append(_accept_row(
                    cand_id, score,
                    fatal=bool(vd.get("fatal_mismatch")),
                    talking_head=bool(vd.get("talking_head")),
                ))
            else:
                rows.append(_accept_row(cand_id, 70.0))
        by_id = {r["candidate_id"]: r for r in rows}

        class StubClient:
            def __init__(self):
                self.batch_calls = 0

            def analyze_clip_frames_batch(self, candidates, user_prompt, system_prompt):
                self.batch_calls += 1
                return {
                    c["candidate_id"]: _normalise_clip_response({
                        **by_id[c["candidate_id"]],
                        "final_score": by_id[c["candidate_id"]]["score"],
                        "reason": by_id[c["candidate_id"]]["reason"],
                    }) | {
                        "batch_verdict": by_id[c["candidate_id"]]["verdict"],
                        "batch_score": by_id[c["candidate_id"]]["score"],
                    }
                    for c in candidates
                }

        class Ctx:
            enabled = True
            visual_required = False

            def get_client(self):
                return StubClient()

        candidates = [
            {"candidate_id": cid, "path": str(clip_file)} for cid in cand_ids
        ]
        verdicts = ytv2._batched_visual_verdicts(
            candidates, scene_index, "Grand Theft Auto VI",
            "GTA VI documentary", "Grand Theft Auto VI", Ctx(),
        )
        checked += 1
        # Winner rule (same as the resolve loop): first accepted candidate
        # in download order, else fail-open/unavailable.
        winner = None
        for cid in cand_ids:
            verdict = verdicts.get(cid)
            if verdict is None:
                continue
            accepted, _score, vision_available, reason = verdict
            outcome = ytv2._classify_visual_result(*verdict)
            if accepted or outcome == "unavailable":
                winner = cid
                break
        recorded = cand_ids[0]
        ok = winner == recorded
        agreed += int(ok)
        details.append((pass_name, scene_index, "OK" if ok else "MISMATCH",
                        f"batch winner={winner} recorded={recorded} verdicts={verdicts}"))
    print(f"\nagreement: batched path kept the recorded winner in {agreed}/{checked} scene-passes")
    for pass_name, scene_index, status, detail in details:
        print(f"  {pass_name} scene {scene_index + 1}: {status} ({detail})")

    # --- schema: a batched response built from the recorded verdicts ---
    yt_winners = []
    for scene_index, row in sorted(manifest.items()):
        vd = row.get("visual_decision") or {}
        winner = str(row.get("candidate_id") or "")
        if row.get("provider") == "youtube" and winner.startswith("youtube:") and vd.get("accepted"):
            yt_winners.append(_accept_row(
                winner, float(vd.get("final_score") or 70.0),
                fatal=bool(vd.get("fatal_mismatch")),
                talking_head=bool(vd.get("talking_head")),
            ))
    batch = {"candidates": yt_winners[:6]}  # one scene's batch holds at most 6
    from services.openrouter_llm import _json_schema_error
    issue = _json_schema_error(batch, CLIP_BATCH_RESPONSE_FORMAT["json_schema"]["schema"])
    print(f"\nbatched response from {len(yt_winners)} recorded YouTube winners "
          f"validates against the batch schema: {'PASS' if not issue else 'FAIL: ' + issue}")
    tmp = Path("/tmp/vision_batch_test12_replay")
    tmp.mkdir(exist_ok=True)
    req = tmp / "request.json"
    ans = tmp / "answer.json"
    req.write_text(json.dumps({
        "id": "test12-replay", "kind": "vision", "operation": "visual_review",
        "response_format": CLIP_BATCH_RESPONSE_FORMAT,
        "messages": [{"role": "user", "content": "judge the candidates"}],
    }), encoding="utf-8")
    ans.write_text(json.dumps(batch), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "resident-inference-worker" / "validate_answer.py"),
         str(ans), str(req)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env={**dict(__import__("os").environ), "VIDZEO_RENDER_GATE": "off"},
    )
    print(f"validate_answer.py on the recorded-verdict batch: exit={proc.returncode} ({proc.stdout.strip()})")
    bad = dict(batch)
    bad["candidates"] = [dict(bad["candidates"][0], verdict="maybe")]
    bad_path = tmp / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    proc_bad = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "resident-inference-worker" / "validate_answer.py"),
         str(bad_path), str(req)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env={**dict(__import__("os").environ), "VIDZEO_RENDER_GATE": "off"},
    )
    print(f"validate_answer.py on a malformed batch (bad verdict): exit={proc_bad.returncode} ({proc_bad.stdout.strip()[:160]})")

    print(f"\nREPLAY RESULT: before={before} after={after} agreement={agreed}/{checked} "
          f"schema={'PASS' if not issue and proc.returncode == 0 else 'FAIL'}")
    return 0 if (agreed == checked and not issue and proc.returncode == 0
                 and proc_bad.returncode == 1) else 1


if __name__ == "__main__":
    raise SystemExit(main())
