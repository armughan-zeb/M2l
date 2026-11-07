"""Run a researched one-minute Titanic documentary through the real app pipeline."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "1")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")

SCRIPT = """At 11:40 p.m. on April 14, 1912, Titanic struck an iceberg in the North Atlantic. Before dawn, it had sunk.

More than 2,200 people were aboard. The lifeboats could carry only a fraction. Titanic met the rules of its day; those rules did not protect its passengers.

Warnings of ice had arrived. The ship kept moving. After the collision, some lifeboats left partly empty. The Carpathia answered the distress call and rescued 706 people.

The sinking killed 1,517. U.S. and British investigations led to stricter safety requirements. In 1914, the International Ice Patrol began tracking icebergs across the North Atlantic.

Then, for 73 years, the wreck stayed hidden. In 1985, a French-American expedition found it more than two miles below the surface, split in two.

An iceberg began the disaster. But human choices and weak rules shaped its scale—and who had a chance to survive."""


def main() -> int:
    import app as vidzeo_app

    stamp = time.strftime("%Y%m%d_%H%M%S")
    output_name = f"titanic_1min_director_test_{stamp}"
    config = {
        "type": "long",
        "name": output_name,
        "output_name": output_name,
        "title": "Titanic: The Rules That Couldn’t Save It",
        # The story subject is explicit. Pin it in this test job so a one-off
        # named institution in the closing minute cannot hijack media search.
        "_topic_anchor": "Titanic",
        "topic_anchor": "Titanic",
        "script": SCRIPT,
        "image_source": "Mixed Media",
        "media_mode": "mixed",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "tts_model": "audiogram",
        "voice": "Adam (Male US)",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "quality": "standard",
        "resolution": "1080p",
        "render_backend": "remotion",
        "semantic_render_scale": 1.0,
        "semantic_render_crf": 18,
        "visual_understanding": True,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_theme": "history_archive",
        "fast_mode": True,
        "semantic_fast_render": True,
        "semantic_render_concurrency": 2,
    }

    print(
        f"[titanic] {len(SCRIPT.split())} narration words; target about 60 seconds",
        flush=True,
    )
    resolved_anchor = vidzeo_app._resolve_topic_anchor_for_config(
        config["title"], SCRIPT, config
    )
    print(f"[titanic] Resolved retrieval anchor: {resolved_anchor}", flush=True)
    if resolved_anchor.casefold() != "titanic":
        raise RuntimeError(f"Expected Titanic retrieval anchor, got {resolved_anchor!r}")
    print(f"[titanic] Starting full Flask pipeline at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()
    response = vidzeo_app.app.test_client().post("/api/generate", json=config)
    result = response.get_json(silent=True) or {}
    if response.status_code != 200 or not result.get("success"):
        raise RuntimeError(
            f"Generation failed ({response.status_code}): "
            f"{result.get('error') or result.get('message') or 'no error details returned'}"
        )

    render = result.get("render_metadata") or {}
    validation = render.get("edit_plan_validation") or {}
    component_manifest = result.get("component_manifest") or {}
    manifest = component_manifest.get("remotion_manifest") or {}
    scenes = manifest.get("scenes") or []
    output_dir = ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "success": True,
        "title": config["title"],
        "script_word_count": len(SCRIPT.split()),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "duration_seconds": result.get("duration"),
        "video_path": result.get("video_path") or result.get("output_path"),
        "output_package_dir": result.get("output_package_dir"),
        "manifest_path": result.get("manifest_path"),
        "scene_count": validation.get("scene_count") or len(scenes),
        "selected_backend": render.get("selected_backend"),
        "quality_status": (render.get("quality_checks") or {}).get("status"),
        "director": manifest.get("director") or result.get("director_metadata") or {},
    }
    result_file = output_dir / f"{output_name}_result.json"
    result_file.write_text(json.dumps({"summary": summary, "manifest": manifest}, indent=2, default=str), encoding="utf-8")
    print("[titanic] Complete:", flush=True)
    print(json.dumps(summary, indent=2, default=str), flush=True)
    print(f"[titanic] Manifest and run summary: {result_file}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
