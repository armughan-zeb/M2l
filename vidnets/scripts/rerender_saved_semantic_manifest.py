"""Re-plan and render a saved semantic manifest using staged local assets.

This is a recovery utility for a completed retrieval job. It deliberately does
not repeat TTS, Whisper, web search, or YouTube downloads.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from services.native_render_policy import (  # noqa: E402
    procedural_sound_library,
    render_semantic_documentary,
)
from services.semantic_edit_engine import build_semantic_edit_manifest  # noqa: E402


def _saved_asset(raw: object, fallback: object = "") -> str:
    for value in (raw, fallback):
        text = str(value or "").strip()
        if not text:
            continue
        candidate = Path(text)
        if candidate.is_file():
            return str(candidate.resolve())
        if text.startswith("/"):
            public_candidate = ROOT / "public" / text.lstrip("/")
            if public_candidate.is_file():
                return str(public_candidate.resolve())
    return ""


def _latest_manifest(topic: str) -> Path:
    candidates = sorted(
        (ROOT / "temp" / "manifests").glob("*_semantic_manifest.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            manifest = payload.get("semantic_edit_manifest") or payload
            if topic.lower() in str(manifest.get("topic") or "").lower():
                return candidate
        except Exception:
            continue
    raise FileNotFoundError(f"No saved semantic manifest found for {topic!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="How China Became the Global Superpower")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    source_path = Path(args.manifest).resolve() if args.manifest else _latest_manifest(args.topic)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    source = payload.get("semantic_edit_manifest") or payload
    scenes = list(source.get("scenes") or [])
    if not scenes:
        raise RuntimeError("Saved manifest contains no scenes")

    media = []
    for scene in scenes:
        item = dict(scene.get("media") or {})
        local_path = _saved_asset(item.get("path"), item.get("src"))
        if not local_path:
            raise FileNotFoundError(
                f"Scene {int(scene.get('scene_index') or 0) + 1} staged media is missing"
            )
        media.append(
            {
                **item,
                "path": local_path,
                "src": local_path,
                "type": item.get("type") or "image",
            }
        )

    audio = dict(source.get("audio") or {})
    voiceover = dict(audio.get("voiceover") or {})
    voice_path = _saved_asset(voiceover.get("path"), voiceover.get("src"))
    if not voice_path:
        raise FileNotFoundError("Saved voiceover is missing")

    seed = args.seed or secrets.randbits(31)
    manifest = build_semantic_edit_manifest(
        scene_texts=[str(scene.get("narration") or "") for scene in scenes],
        scene_durations=[float(scene.get("duration") or 1) for scene in scenes],
        media=media,
        editorial_beats=[dict(scene.get("semantic_beat") or {}) for scene in scenes],
        topic=str(source.get("topic") or args.topic),
        category=str(source.get("category") or ""),
        config={
            "fps": int(source.get("fps") or 30),
            "width": int(source.get("width") or 1920),
            "height": int(source.get("height") or 1080),
            "theme_id": str((source.get("theme") or {}).get("id") or ""),
            "variation_seed": seed,
            "sound_library": procedural_sound_library(),
            "voiceover": {
                **voiceover,
                "path": voice_path,
                "src": voice_path,
            },
            "music": dict(audio.get("music") or {}),
            "render_quality": "cinematic",
            "captions_enabled": bool((source.get("captions") or {}).get("enabled")),
        },
    )
    audit = (manifest.get("metrics") or {}).get("variation_audit") or {}
    if audit.get("status") != "pass":
        raise RuntimeError(f"Replanned edit failed variation audit: {audit}")

    output = (
        Path(args.output).resolve()
        if args.output
        else ROOT / "output" / f"{args.topic} - Smart Edit.mp4"
    )
    manifest_output = output.with_suffix(".semantic-manifest.json")
    manifest_output.write_text(
        json.dumps({"semantic_edit_manifest": manifest}, indent=2),
        encoding="utf-8",
    )
    if args.plan_only:
        print(json.dumps({
            "manifest": str(manifest_output),
            "seed": seed,
            "metrics": manifest.get("metrics") or {},
        }, indent=2))
        return
    result = render_semantic_documentary(
        manifest,
        output,
        {
            "name": args.topic,
            "semantic_render_quality": "cinematic",
            "semantic_render_scale": 1,
            "semantic_render_concurrency": 2,
            "semantic_render_required": True,
            "semantic_variation_required": True,
            "semantic_render_timeout_seconds": 1800,
        },
    )
    print(json.dumps({
        "output": result["output_path"],
        "manifest": str(manifest_output),
        "render_seconds": result["render_seconds"],
        "seed": seed,
        "metrics": manifest.get("metrics") or {},
    }, indent=2))


if __name__ == "__main__":
    main()
