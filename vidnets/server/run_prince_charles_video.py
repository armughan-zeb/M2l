"""Create a real-media biography video about Prince Charles / King Charles III."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


TITLE = "Prince Charles: From Heir to King Charles III"
SCRIPT = (
    "Before he became King Charles III, he was Prince Charles, the longest-serving heir apparent in British history. "
    "Born Charles Philip Arthur George in 1948, he grew up inside a constitutional monarchy shaped by duty, tradition, and public service. "
    "In 1969, he was invested as the Prince of Wales in a ceremony at Caernarfon Castle, formally taking on a role that would define his public life. "
    "Instead of waiting quietly for the throne, Charles built a long record of causes and campaigns. "
    "Through The Prince's Trust, he supported young people with training, confidence, and routes into work. "
    "He also became an early and persistent voice on climate change, sustainable farming, architecture, and the protection of historic places. "
    "His approach was sometimes controversial because he spoke openly about subjects monarchs usually leave to elected governments. "
    "Yet those same campaigns helped make environmental issues part of mainstream public conversation. "
    "After the death of Queen Elizabeth II in September 2022, Charles became King at the age of seventy-three. "
    "His reign carries both continuity and change: respect for the institution, but also a smaller working monarchy and a sharper focus on service. "
    "The story of Prince Charles is therefore more than a path to a crown. It is the story of a public figure who spent decades preparing for one role while trying to shape the conversation around it."
)


def progress(pct: float, message: str) -> None:
    print(f"[progress {float(pct):5.1f}%] {message}", flush=True)


def main() -> int:
    # Keep an offline/transient OpenRouter outage from spending several
    # minutes on each recovery branch.  The brain still gets a short chance;
    # deterministic full-name query generation remains the safe fallback.
    os.environ.setdefault("VIDZEO_BRAIN_CONTAINER_STAGE_SECONDS", "15")
    os.environ.setdefault("VIDZEO_BRAIN_CONTAINER_TIMEOUT", "15")
    os.environ.setdefault("VIDZEO_BRAIN_SCENE_BUDGET_SECONDS", "20")
    os.environ.setdefault("VIDZEO_BRAIN_SCENE_TIMEOUT", "12")
    os.environ.setdefault("VIDZEO_QUERY_LLM_CANDIDATE_RACE_SECONDS", "20")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "output" / "productions" / f"prince_charles_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "script": SCRIPT,
        "title": TITLE,
        "content_title": TITLE,
        "name": TITLE,
        "output_name": f"Prince Charles - From Heir to King {stamp}",
        "output_dir": str(run_dir),
        # Keep the retrieval identity explicit and complete.  This prevents
        # generic "Charles" or unrelated royal-family results.
        "topic": "Prince Charles Charles Philip Arthur George King Charles III",
        "topic_anchor": "Prince Charles Charles Philip Arthur George King Charles III",
        "_topic_anchor": "Prince Charles Charles Philip Arthur George King Charles III",
        # This harness is intentionally explicit: worker/fallback branches
        # must not replace the full identity with a broad royal-family query.
        "_skip_sync_topic_anchor": True,
        "category": "history",
        "type": "long",
        "image_source": "Web Images",
        "media_mode": "manual",
        "pipeline_v2_mode": "off",
        "strict_retrieval_accuracy": True,
        "cross_source_fallback": True,
        "allow_relevant_stock": True,
        "allow_stock_last_resort": True,
        "allow_emergency_media": False,
        # Never synthesize a blank/text-card fallback. If a scene has no new
        # asset after all real retrieval waves, reusing the nearest verified
        # real image is preferable to failing the whole production.
        "allow_duplicate_media_reuse": True,
        "_real_media_recovery": True,
        # Three-beat documentary pacing gives the real-image resolver enough
        # unique slots without forcing Whisper's 30 micro-scenes to reuse a
        # 26-image web batch during render repair.
        "scene_mode": "Every 3 Beats",
        "captions_enabled": True,
        "caption_style": "hormozi",
        "caption_mode": "smart_emphasis",
        "words_per_group": 4,
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
        "voice": "en-GB-RyanNeural",
        "tts_model": "audiogram",
        "visual_verification_frame_count": 5,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "intro_max_shot_seconds": 2.5,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.5,
        "animate_all_media": True,
        "variation_seed": int(time.time()),
    }
    print("=" * 78)
    print("REAL PRINCE CHARLES / KING CHARLES III BIOGRAPHY")
    print(f"Title: {TITLE}")
    print(f"Script words: {len(SCRIPT.split())}")
    print("Source lane: verified web images with real-media fallback")
    print("Animation: adjacency-guarded still-image motion")
    print("=" * 78, flush=True)

    started = time.monotonic()
    result: dict = {}
    error = None
    try:
        result = app_module._run_video_pipeline(config, progress_callback=progress)
    except Exception as exc:
        import traceback
        error = f"{type(exc).__name__}: {exc}"
        (run_dir / "pipeline_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(f"[error] {error}", flush=True)

    video_path = Path(str(result.get("video_path") or "")) if isinstance(result, dict) else Path()
    report = {
        "success": bool(not error and video_path.is_file() and video_path.stat().st_size > 65536),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "title": TITLE,
        "script_word_count": len(SCRIPT.split()),
        "video_path": str(video_path.resolve()) if video_path else "",
        "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
        "error": error,
    }
    (run_dir / "run_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
