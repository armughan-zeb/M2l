"""Run one real one-minute Toyota Corolla AE101 mixed-media production job.

This is an operator harness, not a test double: it calls the same production
pipeline used by the desktop queue, with live YouTube/web retrieval enabled.
It writes a self-contained report beside the rendered MP4 and never prints
provider credentials.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Importing app loads the local .env and the same provider/runtime configuration
# as the desktop backend. Keep this before importing any service modules.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


TITLE = "Toyota Corolla AE101: Why the 1990s Corolla Became a Legend"
SCRIPT = (
    "The Toyota Corolla AE101 arrived in the early 1990s with a simple promise: "
    "make a compact car dependable, efficient, and genuinely enjoyable to drive. "
    "Built on the seventh-generation Corolla platform, the AE101 spread across "
    "sedans, wagons, and the famous Levin and Trueno performance variants. "
    "Its lightweight body, front-wheel drive layout, and Toyota engineering made "
    "maintenance affordable while keeping everyday handling predictable. "
    "Under the hood, buyers could choose economical four-cylinder engines, while "
    "the high-revving 4A-GE gave enthusiasts a reason to visit the back roads. "
    "The formula travelled well: Japan, Southeast Asia, Australia, and export "
    "markets all gave the AE101 a strong owner community. Decades later, its "
    "parts availability, durable mechanics, and unmistakable 1990s shape keep it "
    "popular with restorers and grassroots racers. The AE101 became famous not "
    "through hype, but because it delivered honest engineering every day."
)


def progress(pct: float, message: str) -> None:
    print(f"[progress {float(pct):5.1f}%] {message}", flush=True)


def _manifest_summary(manifest: dict) -> dict:
    # The production manifest stores the authoritative accounting separately
    # from the render timeline; older harnesses only looked for ``items`` and
    # incorrectly reported zero media after a successful render.
    items = list(manifest.get("media_items") or manifest.get("items") or manifest.get("video_clips") or [])
    counts: dict[str, int] = {}
    fallback_reasons: list[str] = []
    for item in items:
        source = str(item.get("source") or item.get("provider") or "unknown")
        counts[source] = counts.get(source, 0) + 1
        reason = str(item.get("fallback_reason") or "").strip()
        if reason:
            fallback_reasons.append(reason)
    accounting = manifest.get("source_accounting")
    if isinstance(accounting, dict):
        counts = {str(key): int(value or 0) for key, value in accounting.items()}
    return {
        "item_count": len(items),
        "source_counts": counts,
        "fallback_count": len(fallback_reasons),
        "fallback_reasons": fallback_reasons[:30],
        "duration": manifest.get("duration"),
        "fps": manifest.get("fps"),
    }


def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "output" / "productions" / f"ae101_1min_real_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    # Automotive is intentionally mixed: YouTube carries real driving footage,
    # while web retrieval supplies model/history/detail stills and a resilient
    # alternate lane. The production pipeline performs the final allocation.
    config = {
        "script": SCRIPT,
        "title": TITLE,
        "content_title": TITLE,
        "name": TITLE,
        "output_name": f"Toyota Corolla AE101 - 1 Minute History {stamp}",
        "output_dir": str(run_dir),
        "topic": "Toyota Corolla AE101",
        "topic_anchor": "Toyota Corolla AE101",
        "category": "automotive",
        "type": "long",
        "image_source": "Mixed Media",
        "media_weights": {"youtube_clips": 55, "web_images": 45, "stock_media": 0},
        "scene_mode": "Every 2 Beats",
        "captions_enabled": True,
        "caption_style": "hormozi",
        "caption_mode": "smart_emphasis",
        "words_per_group": 3,
        "bold": True,
        "uppercase": True,
        "font": "Montserrat-Bold",
        "highlight_color": "#FFE600",
        "semantic_theme": "documentary",
        "semantic_fast_render": False,
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "render_resolution": (1920, 1080),
        "quality": "standard",
        "whisper_mode": "local",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "allow_emergency_media": True,
        "yt_lenient_acceptance": True,
        "yt_stop_on_soft_timeout": True,
        "yt_stream_downloads": True,
        "yt_download_candidate_limit": 5,
        "yt_allow_same_source_reuse": True,
        "yt_max_download_scenes": 0,
        "yt_min_resolved_ratio_before_soft_stop": 0.60,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "intro_max_shot_seconds": 2.5,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.5,
    }

    print("=" * 78)
    print("REAL AE101 ONE-MINUTE MIXED-MEDIA PRODUCTION")
    print(f"Title: {TITLE}")
    print(f"Run directory: {run_dir}")
    print("Live endpoints: OpenRouter scene brain, YouTube retrieval, web-image retrieval")
    print("=" * 78, flush=True)

    result: dict = {}
    error: str | None = None
    try:
        result = app_module._run_video_pipeline(config, progress_callback=progress)
    except Exception as exc:  # keep a durable report for UI/debugging
        import traceback

        error = f"{type(exc).__name__}: {exc}"
        (run_dir / "pipeline_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(f"[error] {error}", flush=True)

    elapsed = time.monotonic() - started
    video_path = Path(str(result.get("video_path") or "")) if isinstance(result, dict) else Path()
    manifest_path = Path(str(result.get("manifest_path") or "")) if isinstance(result, dict) else Path()
    manifest = {}
    if manifest_path and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    report = {
        "success": bool(not error and video_path.is_file() and video_path.stat().st_size > 65536),
        "elapsed_seconds": round(elapsed, 2),
        "title": TITLE,
        "script_word_count": len(SCRIPT.split()),
        "video_path": str(video_path.resolve()) if video_path else "",
        "manifest_path": str(manifest_path.resolve()) if manifest_path else "",
        "media_summary": _manifest_summary(manifest),
        "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
        "error": error,
    }
    report_path = run_dir / "run_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
