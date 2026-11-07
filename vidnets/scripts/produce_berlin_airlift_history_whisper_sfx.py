"""Generate a one-minute Berlin Airlift history documentary through Flask."""

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

SCRIPT = (
    "On June 24, 1948, Soviet authorities blocked road, rail, and barge routes "
    "linking West Berlin to the western zones. The city was surrounded, but "
    "the Allies still had access through three agreed air corridors. They "
    "answered with an airlift. American crews called their operation Vittles; "
    "Britain called its effort Plainfare. Planes carried food, fuel, and "
    "medicine into Tempelhof and Gatow, landing in tightly controlled "
    "sequences. Winter fog, ice, and coal dust made every approach dangerous. "
    "On April 16, 1949, the Easter Parade brought a plane into Berlin about "
    "every minute as crews moved thousands of tons in a single day. The "
    "blockade ended on May 12, but flights continued to build reserves. By "
    "September 1949, Allied aircraft had delivered more than 2.3 million tons "
    "of supplies. A crisis that might have triggered war became an extraordinary "
    "test of logistics, air power, and political resolve."
)

SOURCES = [
    "https://www.afhistory.af.mil/FAQs/Fact-Sheets/Article/458961/1949-the-berlin-airlift/",
    "https://www.af.mil/News/Article-Display/Article/123142/veterans-honor-berlin-airlifts-60th-anniversary/",
    "https://www.amc.af.mil/Portals/12/documents/The%20AF%20Can%20Deliver%20Anything%2C%20The%20Berlin%20Airlift%20%28Harrington%202008%29.pdf",
]


def main() -> int:
    import app as vidzeo_app

    stamp = time.strftime("%Y%m%d_%H%M%S")
    output_name = f"berlin_airlift_history_whisper_sfx_verified_{stamp}"
    config = {
        "type": "long",
        "name": output_name,
        "output_name": output_name,
        "title": "Berlin Airlift: Feeding a City Under Blockade",
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
        "semantic_variation_seed": int(time.time()) % 1_000_000,
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
    quality = render.get("quality_checks") or {}
    summary = {
        "success": True,
        "title": config["title"],
        "script_word_count": len(SCRIPT.split()),
        "script": SCRIPT,
        "fact_sources": SOURCES,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "video_path": result.get("video_path") or result.get("output_path"),
        "output_package_dir": result.get("output_package_dir"),
        "manifest_path": result.get("manifest_path"),
        "duration_seconds": result.get("duration"),
        "selected_backend": render.get("selected_backend"),
        "quality_status": quality.get("status"),
        "render_metadata": render,
    }
    package_dir = Path(str(result.get("output_package_dir") or ""))
    if package_dir.is_dir():
        checkpoint = package_dir / "checkpoints" / "audio_plan.json"
        if checkpoint.is_file():
            audio_plan = json.loads(checkpoint.read_text(encoding="utf-8"))
            summary["whisper"] = {
                "timing_source": audio_plan.get("timing_source"),
                "alignment": audio_plan.get("whisper_alignment"),
                "scene_alignment_validation": audio_plan.get("scene_alignment_validation"),
                "scene_count": len(audio_plan.get("scene_texts") or []),
            }
        manifest_path = Path(str(result.get("manifest_path") or ""))
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            summary["sound_effect_audit"] = (manifest.get("semantic_manifest_metrics") or {}).get("sound_cue_audit")
            summary["sound_effect_count"] = (manifest.get("semantic_manifest_metrics") or {}).get("sound_cue_count")
        (package_dir / "generation_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )

    print("[history] Generation finished:", flush=True)
    print(json.dumps({key: value for key, value in summary.items() if key not in {"render_metadata", "script"}}, indent=2, default=str), flush=True)
    print("[history] Detailed render, Whisper and sound audits are saved with the video package.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
