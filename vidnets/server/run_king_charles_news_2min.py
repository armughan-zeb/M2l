"""Generate a two-minute current-affairs video about King Charles III's latest news."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
os.environ.setdefault("VIDZEO_WEB_SAME_QUERY_CANDIDATES", "8")
os.environ.setdefault("VIDZEO_WEB_VISION_SHORTLIST", "8")
os.environ.setdefault("VIDZEO_WEB_ENABLE_GDL", "1")
os.environ.setdefault("VIDZEO_WEB_ALLOW_DETERMINISTIC_REUSE", "0")
os.environ.setdefault("VIDZEO_YT_REJECT_TALKING_HEADS", "1")
os.environ.setdefault("VIDZEO_YT_BLUR_SMALL_LOGOS", "1")
os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_CANDIDATES", "6")
os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_ATTEMPTS", "6")
os.environ.setdefault("VIDZEO_YT_PROGRESSIVE_ATTEMPTS", "6")
os.environ.setdefault("VIDZEO_YT_V2_QUERIES_PER_CONTAINER", "9")
os.environ.setdefault("VIDZEO_YT_RESULTS_PER_QUERY", "24")

TITLE = "King Charles III Caribbean Commonwealth Tour"
SCRIPT = """Here is the latest news about King Charles III. Britain’s King and Queen Camilla are preparing for an autumn tour of the Caribbean, announced by Royal Communications on August 26, 2026.

The tour is scheduled to run from October 27 through November 4. It will begin in the Bahamas, one of the countries where Charles is head of state. The King will then visit Guyana on a state visit marking sixty years of bilateral relations between Guyana and the United Kingdom.

The final part of the tour will take Charles and Camilla to Antigua and Barbuda. The timing is significant: the Commonwealth Heads of Government Meeting begins on November 1 in St. John’s, the capital of Antigua and Barbuda.

Charles is the symbolic head of the Commonwealth, an association of fifty-six independent countries. Its members range from major economies such as Australia, Canada, and India to small island nations across the Pacific and Caribbean. The leaders’ meeting is held every two years to discuss shared challenges, cooperation, and development.

The announcement gives the tour both a diplomatic and regional focus. In the Bahamas, the visit connects the monarch with a Caribbean realm. In Guyana, it highlights a six-decade relationship. And in Antigua and Barbuda, the royal visit will coincide with a wider gathering of Commonwealth leaders.

This is also part of a busy year for the King. In July, Charles opened the Commonwealth Games in Glasgow, making a memorable entrance from a Doctor Who TARDIS before formally opening the event in his role as head of the Commonwealth.

For now, the autumn tour is the central royal story to watch. The schedule has been announced, but the detailed programme and individual engagements are expected to shape the public meaning of the visit. This report is based on the Royal Family’s official announcement and Associated Press reporting, checked on September 2, 2026."""

OUTPUT_DIR = ROOT / "output" / "king_charles_news_2min"


def progress(fraction: float, message: str) -> None:
    print(f"  [{float(fraction) * 100:5.1f}%] {message}", flush=True)


def main() -> int:
    import app as vidzeo_app

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "type": "long",
        "name": TITLE,
        "title": TITLE,
        "content_title": TITLE,
        "topic_anchor": "King Charles III autumn 2026 Caribbean Commonwealth tour",
        "_topic_anchor": "King Charles III autumn 2026 Caribbean Commonwealth tour",
        "script": SCRIPT,
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_required": True,
        "strict_retrieval_accuracy": True,
        "allow_emergency_media": False,
        "allow_relevant_stock": True,
        "allow_stock_last_resort": True,
        "cross_source_fallback": True,
        "youtube_accepted_coverage_target": 0.60,
        # A two-minute current-affairs item has limited unique event imagery.
        # Use broader editorial beats so the resolver can verify each beat and
        # then subdivide it with distinct shots during rendering.
        "scene_mode": "Every 5 Beats",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "render_resolution": (1280, 720),
        "quality": "standard",
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_render_concurrency": 2,
        "output_dir": str(OUTPUT_DIR),
        "visual_verification_frame_count": 5,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "intro_max_shot_seconds": 2.5,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.5,
        "media_deadline_seconds": 900,
        "visual_deadline_seconds": 900,
        "visual_shared_deadline_seconds": 900,
        "yt_branch_wall_timeout_seconds": 420,
        "auto_export": True,
    }
    print("=" * 88)
    print("KING CHARLES NEWS — TWO-MINUTE PRODUCTION")
    print(f"Title: {TITLE}")
    print(f"Script words: {len(SCRIPT.split())}")
    print("Resolver: strict mixed media with YouTube, web, cross-source, and final Stock recovery")
    print("=" * 88, flush=True)

    started = time.monotonic()
    try:
        result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)
        result = result if isinstance(result, dict) else {}
        video_path = str(result.get("video_path") or result.get("output_path") or "")
        manifest_path = str(result.get("manifest_path") or "")
        summary = {
            "success": bool(result.get("success") and Path(video_path).is_file()),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "video_path": video_path,
            "manifest_path": manifest_path,
            "duration_seconds": result.get("duration"),
            "title": TITLE,
            "sources": [
                "https://www.royal.uk/media-centre/press-releases",
                "https://apnews.com/article/king-charles-iii-commonwealth-caribbean-fc0f9474b091cd3f9ea9d3dc31f366fa",
            ],
        }
        summary_path = OUTPUT_DIR / "generation_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        return 0 if summary["success"] else 1
    except Exception as exc:
        failure = {
            "success": False,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "error": repr(exc),
        }
        (OUTPUT_DIR / "generation_summary.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        print(json.dumps(failure, indent=2), flush=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
