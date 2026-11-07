"""Upgrade the saved 400-word Vidzeo test into a full semantic motion master.

It uses the final Whisper narration and actual final scene frames from the
production export. This is intentionally a render-only harness: it proves the
motion engine without repeating web retrieval or TTS.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "server"))
    from services.editorial_brain import plan_editorial_beats
    from services.native_render_policy import procedural_sound_library, render_semantic_documentary
    from services.semantic_edit_engine import build_semantic_edit_manifest

    prior = json.loads((root / "output" / "semantic_400_rms_titanic_manifest.json").read_text(encoding="utf-8"))
    old = prior["semantic_edit_manifest"]
    scenes = old["scenes"]
    texts = [scene["narration"] for scene in scenes]
    durations = [float(scene["duration"]) for scene in scenes]
    frame_dir = root / "temp" / "semantic_400_remotion_media"
    media = [{"path": str(frame_dir / f"scene_{index + 1:03d}.jpg"), "type": "image"} for index in range(len(scenes))]
    if not all(Path(item["path"]).is_file() for item in media):
        raise FileNotFoundError("Run scripts/prepare_semantic_manifest_media.py before this render.")
    voice = Path((old.get("audio") or {}).get("voiceover", {}).get("path") or "")
    if not voice.is_file():
        raise FileNotFoundError("The generated Edge TTS voiceover is unavailable.")
    beats = plan_editorial_beats(texts, {
        "groq_api_key": os.environ.get("GROQ_API_KEY", ""),
        "documentary_density": "balanced",
        "_editorial_total_duration": sum(durations),
        "_editorial_scene_starts": [scene["start"] for scene in scenes],
    })
    manifest = build_semantic_edit_manifest(
        texts, durations, media=media, editorial_beats=beats,
        topic="RMS Titanic: The Night the Signal Changed", category="history",
        config={
            "fps": 30, "width": 1920, "height": 1080,
            "theme_id": "history_archive", "render_quality": "standard",
            "sound_library": procedural_sound_library(),
            "voiceover": {"path": str(voice), "src": str(voice), "rights_status": "user_generated"},
        },
        variation_seed=4002026,
    )
    output_stem = os.environ.get("VIDZEO_MOTION_MASTER_OUTPUT", "semantic_400_rms_titanic_motion_master").strip() or "semantic_400_rms_titanic_motion_master"
    timeout_seconds = int(os.environ.get("VIDZEO_MOTION_MASTER_TIMEOUT_SECONDS", "900"))
    concurrency = int(os.environ.get("VIDZEO_MOTION_MASTER_CONCURRENCY", "3"))
    result = render_semantic_documentary(
        manifest, root / "output" / f"{output_stem}.mp4",
        {"name": output_stem, "semantic_render_quality": "standard", "semantic_render_timeout_seconds": timeout_seconds, "semantic_render_concurrency": concurrency},
    )
    (root / "output" / f"{output_stem}_manifest.json").write_text(json.dumps({"semantic_edit_manifest": manifest, "render": result}, indent=2), encoding="utf-8")
    print(result["output_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
