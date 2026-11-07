"""Produce the two-minute Toyota Corolla AE101 history documentary.

This is a production-style verification run against the FFmpeg renderer.  It
keeps the real-media contract enabled: every visual must be a verified web
image, YouTube clip, or an extracted frame from a verified clip.  No blank
plates or synthetic rescue cards are permitted.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

# Production renderer/model defaults for this run.  The scene brain tries the
# free Nemotron route first and falls back to cheap Qwen Flash; Qwen Flash is
# also the authoritative visual verifier.
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
os.environ.setdefault("VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_DEEP_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_SCENE_BRAIN_MODEL", "nvidia/nemotron-3.5-lightning:free")
os.environ.setdefault("VIDZEO_SCENE_BRAIN_FALLBACK_MODELS", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_YOUTUBE_USE_AUTHENTICATED_SESSION", "1")
os.environ.setdefault("VIDZEO_YT_USE_AUTHENTICATED_SESSION", "1")

SCRIPT = """The Toyota Corolla AE101 is one of the most complete compact cars of the 1990s. AE101 is Toyota's model and chassis code inside the seventh-generation Toyota Corolla E100 family; engines and body styles changed by market.

Toyota launched that seventh-generation Corolla in June 1991. The goal was practical: make a compact sedan quieter, safer and more refined, while keeping the low running costs that made Corolla ownership accessible.

Underneath were front-wheel drive, compact dimensions, efficient four-cylinder engines and conservative Toyota engineering. Many markets used the 4A-FE. Selected Japanese-market performance versions used Toyota's 4A-GE family, including famous 20-valve variants. Those engines are enthusiast highlights, not every AE101's specification.

Manufacturing scale mattered as much as engineering. Toyota's production network and common-parts strategy put Corolla sedans, hatchbacks and wagons on roads across Asia, Europe, Africa, Australia and the Americas. A global parts supply made repairs familiar long after the car left the showroom. Local factories adapted trim, engines and safety equipment, but the core formula stayed recognizable everywhere. That consistency made ownership predictable, even when a family moved countries or bought parts from a different market.

That is why the Toyota Corolla AE101 became popular. It was dependable without being dull, economical for daily driving, practical for families and strong enough for long ownership. Tuners discovered a light chassis, responsive engines and a huge aftermarket. In Japan, selected AE101-based cars paired it with a high-revving 4A-GE 20-valve character.

The legacy is simple: the AE101 connected Toyota's mass-market discipline with real enthusiast appeal. It was never just a badge or one engine. It was a globally produced Corolla that earned trust first—and became a classic because owners kept finding more reasons to keep it."""


def main() -> int:
    import app as vidnets_app

    words = len(SCRIPT.split())
    print(f"[corolla] words={words} (target ~120s at documentary narration pace)", flush=True)

    config = {
        "type": "long",
        "name": "toyota_corolla_ae101_history_2min",
        "title": "Toyota Corolla AE101: Why It Became a Legend",
        "script": SCRIPT,
        # Automotive history has abundant still photography but the available
        # AE101 YouTube results are dominated by burned-in subtitles.  For
        # this curated factual documentary we use the web-image lane so every
        # shot is a verified real car image and no streamer/captioned clip is
        # forced into the edit.
        "image_source": "Web Images",
        "media_mode": "manual",
        "cross_source_fallback": False,
        "allow_relevant_stock": False,
        "allow_stock_last_resort": False,
        "scene_mode": "Every 3 Beats",
        "vo_mode": "Use OpenRouter TTS",
        "tts_model": "fish-audio/s2.1-pro-free:free",
        "voice": "",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "quality": "standard",
        "resolution": "1080p",
        "aspect_ratio": "16:9",
        "visual_understanding": True,
        "visual_required": True,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "youtube_recall_mode": False,
        "youtube_relaxed_visual_gate": False,
        "youtube_fail_open_on_visual_unavailable": False,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_theme": "automotive_documentary",
        "semantic_fast_render": True,
        "semantic_render_concurrency": 2,
        # Give the real-media retrieval and the 120-second render enough room
        # on a local machine while keeping the run bounded.
        "delivery_budget_seconds": 900,
        "render_reserve_seconds": 240,
        # Never let a production documentary hide a retrieval miss behind a
        # neutral/emergency plate.  The V2 resolver must either fill the slot
        # with verified real media or fail with an actionable contract report.
        "allow_emergency_media": False,
    }

    events = []
    started = time.monotonic()

    def progress(fraction: float, message: str):
        event = {
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "percent": int(max(0.0, min(1.0, fraction)) * 100),
            "message": str(message),
        }
        events.append(event)
        print(json.dumps(event), flush=True)

    report = ROOT / "output" / "corolla_ae101_workflow_audit.json"
    try:
        result = vidnets_app._run_video_pipeline(config, progress_callback=progress)
        payload = {
            "success": True,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "events": events,
            "result": result,
        }
    except Exception as exc:  # keep a useful audit report for production QA
        import traceback

        traceback.print_exc()
        payload = {
            "success": False,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "events": events,
            "error": str(exc),
        }

    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"[corolla] report={report} success={payload['success']}", flush=True)
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
