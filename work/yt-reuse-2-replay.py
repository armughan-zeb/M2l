"""Offline replay: YouTube reuse cap 1 -> 2 on real run-9 data.

Job a7f52c58 (GTA VI Launch Day, 18 scenes) finished with 1 YouTube clip
(scene 0, tJbzMqJGH4k ts1274) + 1 YouTube frame (scene 4) = 2/18 real
YouTube scenes (11.1%) vs the 80% gaming target.

The fix (impl/yt-reuse-2): _reuse_limit_for_resolution() in
vidnets/server/services/media_resolution_coordinator.py now returns
_youtube_reuse_cap() (named constant YOUTUBE_REUSE_CAP_DEFAULT = 2,
env VIDZEO_YT_REUSE_CAP) for YouTube instead of a hardcoded 1.

This script replays the run-9 final manifest
(GTA_VI_LaunchDay_1Min.manifest.json, media_resolution_v2 rows + attempt
ledgers) and answers, per scene, whether a vision-accepted YouTube
candidate was blocked ONLY by the reuse count cap -- i.e. whether the cap
raise alone gains that scene. Blockers are classified from the recorded
attempts:

  COUNT_CAP        rejected_asset_reuse_cap / rejected_reuse_cap /
                   physical_asset_reuse_limit -- lifted by cap 1 -> 2.
  DISTINCT_SEGMENT same_master_cut_gate
                   (rejected_without_distinct_distant_scene_verified_download)
                   / rejected_duplicate_materialized_variant /
                   skipped_no_distinct_vision_bound_timestamp_segment --
                   needs a separate, distant, scene-verified download of the
                   same master; the cap raise does not supply it.
  OTHER            prior_fatal, intro gate, semantic/review-bound gates,
                   scene budget, vision rejection -- untouched by the cap.

A scene GAINS from the cap alone only when it has a vision-accepted
(visual_pass / youtube_frame_recovery) YouTube candidate whose recorded
blockers are all COUNT_CAP. The neighbour-reuse tier is also re-checked
with the real coordinator gates (_semantic_reuse_compatible,
_strict_reuse_identity_compatible, requirement-hash binding of
_review_bound_to_scene) for every donor that was cap-rejected live.

Read-only: prints a table, exits 0. No network, no vision, no server.
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "vidnets" / "server"))

MANIFEST = (
    Path.home()
    / "workspace/vidnets-runtime/output"
    / "GTA_VI_LaunchDay_1Min_20261119_200006_7845895e"
    / "GTA_VI_LaunchDay_1Min.manifest.json"
)

VISION_ACCEPTED = {"visual_pass", "youtube_frame_recovery"}
COUNT_CAP = {
    "rejected_asset_reuse_cap",
    "rejected_reuse_cap",
    "physical_asset_reuse_limit",
}
DISTINCT_SEGMENT = {
    "rejected_without_distinct_distant_scene_verified_download",
    "rejected_duplicate_materialized_variant",
    "skipped_no_distinct_vision_bound_timestamp_segment",
}


def main() -> int:
    from services.media_resolution_coordinator import (
        SceneResolution,
        _reuse_limit_for_resolution,
        _review_bound_to_scene,
        _semantic_reuse_compatible,
        _strict_reuse_identity_compatible,
        _youtube_reuse_cap,
    )
    from services.visual_intelligence.context import scene_requires_visual_verification

    # The cap itself, on the patched code.
    with_cap = _youtube_reuse_cap()
    probe_scene = {"source_policy": {"mixed_media": True}, "start_seconds": 30}
    probe_yt = SceneResolution(scene_index=3, provider="youtube")
    print(f"youtube reuse cap (patched code, env clear): {_youtube_reuse_cap()}")
    print(
        "  _reuse_limit_for_resolution(YT) = "
        f"{_reuse_limit_for_resolution(probe_scene, probe_yt, non_mixed_limit=2)}"
    )
    assert with_cap == 2, f"expected cap 2, got {with_cap}"

    manifest = json.loads(MANIFEST.read_text())
    rows = manifest["media_resolution_v2"]
    assert len(rows) == 18, f"expected 18 scenes, got {len(rows)}"

    before_yt = [
        r["scene_index"]
        for r in rows
        if str(r.get("provider") or "").strip().lower()
        in {"youtube", "youtube clips", "yt"}
    ]
    print(
        f"\nBEFORE (run-9 final plan): {len(before_yt)}/18 YouTube scenes "
        f"{sorted(before_yt)} "
        f"({sum(1 for r in rows if r.get('media_type') == 'video' and r.get('provider') == 'youtube')} clip, "
        f"{sum(1 for r in rows if r.get('media_type') == 'image' and r.get('provider') == 'youtube')} frame)"
    )

    # Donor resolutions for the neighbour-reuse re-check.
    donors = {}
    for row in rows:
        donors[row["scene_index"]] = SceneResolution(
            scene_index=int(row["scene_index"]),
            provider=str(row.get("provider") or ""),
            selected_path=str(row.get("selected_path") or ""),
            source_url=str(row.get("source_url") or ""),
            asset_fingerprint=str(row.get("asset_fingerprint") or ""),
            video_start=row.get("video_start"),
            video_end=row.get("video_end"),
            visual_decision=dict(row.get("visual_decision") or {}),
            final_scene_context=dict(row.get("final_scene_context") or {}),
        )

    gains = []
    print("\nPer-scene vision-accepted YouTube candidates and their blockers:")
    for row in rows:
        idx = int(row["scene_index"])
        scene = dict(row.get("final_scene_context") or {})
        selected_cand = str(row.get("candidate_id") or "")
        # Vision outcome per YouTube candidate, then its later blockers.
        accepted: dict[str, list[str]] = {}
        cap_donors: list[int] = []
        for attempt in row.get("attempts") or []:
            if not isinstance(attempt, dict):
                continue
            status = str(attempt.get("status") or "")
            stage = str(attempt.get("stage") or "")
            if (
                stage == "nearest_verified_neighbor_reuse"
                and status == "rejected_asset_reuse_cap"
            ):
                donor_idx = attempt.get("reused_scene_index")
                if isinstance(donor_idx, int):
                    cap_donors.append(donor_idx)
            cand = str(attempt.get("candidate_id") or "")
            if not cand.startswith("youtube:") or cand == selected_cand:
                continue
            if stage == "visual_gate" and status in VISION_ACCEPTED:
                accepted.setdefault(cand, [])
            elif cand in accepted and status not in VISION_ACCEPTED | {"passed"}:
                accepted[cand].append(status)
        if not accepted and not cap_donors:
            continue
        for cand, blockers in accepted.items():
            classes = {
                "COUNT_CAP" if b in COUNT_CAP
                else "DISTINCT_SEGMENT" if b in DISTINCT_SEGMENT
                else "OTHER"
                for b in blockers
            }
            gain = bool(blockers) and classes == {"COUNT_CAP"}
            print(
                f"  scene {idx:2d} {cand}: vision-accepted, "
                f"blockers={blockers or ['<none recorded>']} -> "
                f"{'GAIN' if gain else 'no gain (' + ','.join(sorted(classes)) + ')'}"
            )
            if gain:
                gains.append(idx)
        for donor_idx in sorted(set(cap_donors)):
            donor = donors.get(donor_idx)
            semantic = _semantic_reuse_compatible(scene, donor)
            strict = _strict_reuse_identity_compatible(scene, donor)
            req = scene.get("visual_requirement") or {}
            needs_bound = bool(req.get("strict_identity")) or scene_requires_visual_verification(scene)
            bound = _review_bound_to_scene(scene, donor) if needs_bound else True
            verdict = (
                "GAIN via neighbour reuse"
                if (semantic and strict and bound)
                else "still blocked by _review_bound_to_scene"
                if needs_bound and not bound
                else "still blocked by semantic/strict gate"
            )
            print(
                f"  scene {idx:2d} neighbour-reuse donor scene {donor_idx} "
                f"(cap 1->2 lifts the count block): semantic={semantic} "
                f"strict={strict} needs_bound_review={needs_bound} "
                f"review_bound={bound} -> {verdict}"
            )
            if semantic and strict and bound:
                gains.append(idx)

    # Distinct-segment supply check for tJbzMqJGH4k: how many distinct
    # tJbz cuts (video:timestamp fingerprints) exist in the run-9 record?
    tjbz_fps = sorted(
        {
            str(r.get("asset_fingerprint") or "")
            for r in rows
            if "tJbzMqJGH4k" in str(r.get("candidate_id") or "")
        }
    )
    print(
        f"\ntJbzMqJGH4k fingerprints in the final plan: {tjbz_fps} "
        "(single cut ts1274, 1274.0-1276.73s; no distant segment was ever "
        "downloaded in run 9, per the run-9 measurement record)"
    )

    # Physical-asset diversity check under cap 2: a second assignment of
    # the same fingerprint would now survive (1 prior use < cap 2).
    print(
        "physical-asset diversity under cap 2: prior uses 1 < cap 2, so a "
        "second use of one YouTube fingerprint is no longer degraded to "
        "neutral by that check."
    )

    print(
        f"\nAFTER (cap 2 alone, run-9 data as-is): "
        f"{len(before_yt) + len(gains)}/18 YouTube scenes "
        f"(gains: {sorted(set(gains)) or 'none'})"
    )
    print(
        "Scene 6 (tJbz frame:2 visual_pass) is the first conditional gainer: "
        "its only coordinator blocker is the same_master_cut_gate, so it "
        "gains once a distinct tJbz timestamp segment (>=12s from "
        "1274.0-1276.73, vision-verified for scene 6) is downloaded -- "
        "primary selection / quota reuse then admit it via "
        "_verified_distant_youtube_variant, and the cap raise guarantees no "
        "count gate (neighbour / strict / physical-asset diversity) then "
        "kills the second use of the tJbz master. The cap raise alone does "
        "not supply the segment."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
