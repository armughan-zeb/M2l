"""Generate a researched Toyota Corolla documentary through Vidzeo /api/generate."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ["VIDZEO_RENDER_CONTINUE_WHILE_ACTIVE"] = "1"
os.environ["VIDZEO_REMOTION_MAX_WORKERS"] = "8"
os.environ["VIDZEO_AUTH_MODE"] = "local"
os.environ["VIDZEO_PACKAGED"] = "0"
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "1")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")

ARTIFACTS = ROOT / "artifacts" / "toyota_corolla_video_20261009"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
SCRIPT = """In 1966, Toyota launched a compact car for a country changing fast. The Corolla was designed to be practical and affordable, with enough comfort for a new generation of everyday drivers.

The idea traveled. Toyota began exporting Corollas to the United States in 1968, where a small, economical car met a market built around bigger machines. Sales passed one million globally just four years after launch.

But the Corolla did not stay one car. Across generations, Toyota changed its shape, engineering, and production to fit different roads and buyers. Local factories helped turn a Japanese compact into a global model.

By 2016, Toyota reported 44.1 million Corollas sold since launch. That figure includes vehicles carrying the Corolla name across its many versions.

Its story is not a single breakthrough. It is a long series of adjustments: make the car useful, take it to new markets, then redesign it for the next generation of drivers. That is how an ordinary commute became a global automotive story."""


def main() -> int:
    import app as vidzeo_app

    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"toyota_corolla_director_test_{stamp}"
    config = {
        "type": "long",
        "name": name,
        "output_name": name,
        "title": "Toyota Corolla: How an Everyday Car Went Global",
        "topic_anchor": "Toyota Corolla",
        "niche": "automotive",
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
        "semantic_theme": "automotive_documentary",
        "fast_mode": True,
        "semantic_fast_render": True,
        "continue_render_while_active": True,
    }
    words = len(SCRIPT.split())
    print(f"[toyota] Start /api/generate; {words} narration words; target about 60 seconds", flush=True)
    start = time.monotonic()
    response = vidzeo_app.app.test_client().post("/api/generate", json=config)
    payload = response.get_json(silent=True) or {}
    (ARTIFACTS / "api-response.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    if response.status_code != 200 or not payload.get("success"):
        reason = payload.get("error") or payload.get("message") or f"HTTP {response.status_code}"
        (ARTIFACTS / "failure.json").write_text(
            json.dumps({"status_code": response.status_code, "error": reason}, indent=2), encoding="utf-8"
        )
        print(f"[toyota] Generation failed: {reason}", flush=True)
        return 1

    render = payload.get("render_metadata") or {}
    validation = render.get("edit_plan_validation") or {}
    component_manifest = payload.get("component_manifest") or {}
    manifest = component_manifest.get("remotion_manifest") or {}
    source_video = Path(str(payload.get("video_path") or payload.get("output_path") or ""))
    if not source_video.is_file():
        raise RuntimeError(f"API returned success but video was not found: {source_video}")
    archived_video = ARTIFACTS / f"{name}.mp4"
    shutil.copy2(source_video, archived_video)
    (ARTIFACTS / "failure.json").unlink(missing_ok=True)
    summary = {
        "success": True,
        "title": config["title"],
        "script_word_count": words,
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "duration_seconds": payload.get("duration"),
        "video_path": str(archived_video),
        "original_video_path": str(source_video),
        "scene_count": validation.get("scene_count") or len(manifest.get("scenes") or []),
        "selected_backend": render.get("selected_backend"),
        "quality_status": (render.get("quality_checks") or {}).get("status"),
        "director": manifest.get("director") or payload.get("director_metadata") or {},
        "source_manifest": payload.get("manifest_path"),
    }
    (ARTIFACTS / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    if manifest:
        (ARTIFACTS / "remotion-manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    print(f"[toyota] Finished in {summary['elapsed_seconds']} seconds", flush=True)
    print(f"[toyota] Video: {archived_video}", flush=True)
    print(f"[toyota] Scenes: {summary['scene_count']}; renderer: {summary['selected_backend']}; quality: {summary['quality_status']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
