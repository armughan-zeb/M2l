"""Run a second one-minute history documentary through Flask /api/generate."""

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

SCRIPT = """The Suez Canal links the Mediterranean to the Red Sea through Egypt. For decades, a British-French company operated the waterway. On July 26, 1956, Egyptian President Gamal Abdel Nasser announced its nationalization. The law transferred the company’s assets to Egypt and promised compensation to shareholders.

Britain and France opposed the move and held secret military talks with Israel. On October 29, Israeli forces crossed into Egypt’s Sinai Peninsula, advancing toward the canal. Britain and France sent troops days later, saying they were protecting the waterway. The fighting drew international pressure, and a United Nations ceasefire took effect on November 6.

The canal closed as ships were sunk to block the route. After the forces withdrew, workers cleared the wreckage, and navigation resumed in March 1957. The crisis became a turning point: Britain and France could no longer dictate events around the canal, while Egypt’s control of the waterway had become a fact the world had to reckon with."""


def main() -> int:
    import app as vidzeo_app

    stamp = time.strftime("%Y%m%d_%H%M%S")
    output_name = f"suez_crisis_history_validation_{stamp}"
    config = {
        "type": "long",
        "name": output_name,
        "output_name": output_name,
        "title": "Suez Crisis: The Canal That Changed the Balance of Power",
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
        f"[history] Starting Flask /api/generate: {config['title']} "
        f"({len(SCRIPT.split())} words, target about one minute)",
        flush=True,
    )
    started = time.monotonic()
    response = vidzeo_app.app.test_client().post("/api/generate", json=config)
    result = response.get_json(silent=True) or {}
    if response.status_code != 200 or not result.get("success"):
        raise RuntimeError(
            f"Flask generation failed ({response.status_code}): "
            f"{result.get('error') or result.get('message') or 'no error details returned'}"
        )

    render = result.get("render_metadata") or {}
    edit_validation = render.get("edit_plan_validation") or {}
    quality = render.get("quality_checks") or {}
    component_manifest = result.get("component_manifest") or {}
    remotion_manifest = component_manifest.get("remotion_manifest") or {}
    summary = {
        "success": True,
        "title": config["title"],
        "script_word_count": len(SCRIPT.split()),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "video_path": result.get("video_path") or result.get("output_path"),
        "output_package_dir": result.get("output_package_dir"),
        "manifest_path": result.get("manifest_path"),
        "duration_seconds": result.get("duration"),
        "selected_backend": render.get("selected_backend"),
        "quality_status": quality.get("status"),
        "scene_count": edit_validation.get("scene_count") or len(remotion_manifest.get("scenes") or []),
        "render_metadata": render,
    }

    package_dir = Path(str(result.get("output_package_dir") or ""))
    if package_dir.is_dir():
        (package_dir / "generation_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
    print("[history] Generation finished:", flush=True)
    print(json.dumps({key: value for key, value in summary.items() if key != "render_metadata"}, indent=2), flush=True)
    print("[history] Detailed renderer and quality metadata saved with the video package.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
