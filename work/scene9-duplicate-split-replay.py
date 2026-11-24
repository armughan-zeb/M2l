"""Offline replay: scene-9 coordinator loss on real run-7 data (job 6624244a).

Loads the coordinator-time media_plan snapshot
(checkpoints/media_history/media_plan-18efbf....json), where scene 9 is
still a YouTube clip, copies the two byte-identical cuts to /tmp (the
run-7 evidence on disk is never modified), runs the assembly
(``resolve_and_build_media_plan``) with the duplicate-cut split, and
measures the outcome with the REAL downstream dedup
(``excess_media_reuse_indices``):

  * before: the dedup flags scene 9 (the run-7 loss);
  * after:  scene 9 keeps a real, decodable video clip, no scene is a
    graphic, and no two scenes share identical bytes.

Usage (from vidnets/):
    VIDZEO_RENDER_GATE=off /path/to/venv/python ../work/scene9-duplicate-split-replay.py
"""

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

OUT_DIR = Path(
    "/home/hatch/workspace/vidnets-runtime/output/GTA_VI_1Min_20261119_085929"
    "/GTA_VI_1Min_20261119_085929_00f71a33"
)
SNAPSHOT = (
    OUT_DIR
    / "checkpoints/media_history/media_plan-18efbf1cbc0b97642d0259472f042beaefb33dd91736e319c8dbabf49f69baa4.json"
)
WORK = Path("/tmp/scene9-replay")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return float(out.stdout.strip())


def frame_bytes(path, at=0.5):
    dest = WORK / f"frame-{Path(path).stem}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(path),
         "-frames:v", "1", str(dest)],
        check=True, timeout=60,
    )
    return dest.read_bytes()


def main():
    sys.path.insert(0, "server")
    sys.path.insert(0, ".")
    import services.pipeline_v2_media_adapter as adapter
    from services.media_asset_policy import excess_media_reuse_indices
    from services.media_resolution_coordinator import SceneResolution

    plan = json.loads(SNAPSHOT.read_text())
    rows = plan["metadata"]["media_resolution_v2"]
    paths = list(plan["media_paths"])
    labels = list(plan["source_labels"])
    assert len(paths) == 13 and len(rows) == 13, (len(paths), len(rows))

    WORK.mkdir(parents=True, exist_ok=True)
    # Copy ONLY the two shared cuts; everything else keeps its real path.
    for scene_index in (7, 9):
        src = Path(paths[scene_index])
        dst = WORK / src.name
        if dst.resolve() != src.resolve():
            shutil.copyfile(src, dst)
        paths[scene_index] = str(dst)

    print(f"scene 7 file: {paths[7]}")
    print(f"scene 9 file: {paths[9]}")
    print(f"byte-identical before split: {sha256(paths[7]) == sha256(paths[9])}")
    print(f"shared sha256: {sha256(paths[7])}")
    print(f"shared clip duration: {ffprobe_duration(paths[7]):.3f} s")

    plan_before = {"media_paths": list(paths), "metadata": {"media_resolution_v2": rows}}
    flagged_before = excess_media_reuse_indices(plan_before)
    print(f"downstream dedup flags before split: {flagged_before}")

    allowed = {f.name for f in fields(SceneResolution)}
    resolutions = {}
    for row in rows:
        clean = {k: v for k, v in row.items() if k in allowed}
        resolutions[int(row["scene_index"])] = SceneResolution(**clean)
    # Point the assembly inputs at the /tmp copies: the assembly rebuilds
    # paths from the resolutions, so rewriting the plan's path list alone
    # would still cut from (and write next to) the real run-7 files.
    from dataclasses import replace as _replace

    for scene_index in (7, 9):
        resolutions[scene_index] = _replace(
            resolutions[scene_index], selected_path=paths[scene_index]
        )
        rows[scene_index] = dict(rows[scene_index])
        rows[scene_index]["selected_path"] = paths[scene_index]
    scenes = [dict(row.get("final_scene_context") or {}) for row in rows]

    adapter.resolve_storyboard_media_v2 = lambda *a, **k: resolutions
    media_plan, _returned = adapter.resolve_and_build_media_plan(
        scenes,
        youtube_lane=lambda scene, event: None,
        web_lane=lambda scene, event: None,
        stock_lane=None,
        visual_verifier=None,
        neutral_factory=lambda scene: None,
    )

    new_paths = media_plan["media_paths"]
    new_rows = media_plan["metadata"]["media_resolution_v2"]
    splits = media_plan["metadata"].get("duplicate_cut_splits") or []
    print(f"duplicate_cut_splits entries: {len(splits)}")
    for entry in splits:
        print(f"  scene {entry.get('scene_index')}: {entry.get('status')} "
              f"seg {entry.get('segment_index')}of{entry.get('segment_count')} "
              f"file[{entry.get('file_offset_start')}..{entry.get('file_offset_end')}s] "
              f"yt[{entry.get('video_start')}..{entry.get('video_end')}s] "
              f"-> {entry.get('segment_path')}")
    print(f"scene 7 after: provider={new_rows[7]['provider']} "
          f"path={new_paths[7]}")
    print(f"scene 9 after: provider={new_rows[9]['provider']} "
          f"status={new_rows[9]['status']} path={new_paths[9]}")
    print(f"scene 9 label: {media_plan['source_labels'][9]}")
    print(f"scene 7 duration: {ffprobe_duration(new_paths[7]):.3f} s; "
          f"scene 9 duration: {ffprobe_duration(new_paths[9]):.3f} s")
    print(f"segments byte-identical: "
          f"{sha256(new_paths[7]) == sha256(new_paths[9])}")
    print(f"segments show different frames: "
          f"{frame_bytes(new_paths[7]) != frame_bytes(new_paths[9])}")
    plan_after = {"media_paths": list(new_paths),
                  "metadata": {"media_resolution_v2": new_rows}}
    flagged_after = excess_media_reuse_indices(plan_after)
    print(f"downstream dedup flags after split: {flagged_after}")
    graphics = [i for i, label in enumerate(media_plan["source_labels"])
                if "Graphic" in str(label)]
    print(f"graphic scenes after split: {graphics}")
    print(f"scene 9 resolution_kind: {new_rows[9]['resolution_kind']}")
    print(f"scene 9 candidate_id: {new_rows[9]['candidate_id']}")

    assert flagged_before == [9], f"expected dedup to flag scene 9, got {flagged_before}"
    assert flagged_after == [], f"dedup still flags scenes: {flagged_after}"
    assert new_rows[9]["provider"] == "youtube"
    assert new_rows[9]["status"] == "accepted"
    assert graphics == []
    assert sha256(new_paths[7]) != sha256(new_paths[9])
    print("REPLAY PASS: scene 9 keeps a real clip; no duplicate media; no graphic.")


if __name__ == "__main__":
    main()
