"""Create a Prince Charles biography with a 50/50 YouTube + web-media mix."""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
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


def _source_counts(result: dict) -> dict[str, int]:
    manifest = result.get("component_manifest") if isinstance(result, dict) else None
    labels = []
    if isinstance(manifest, dict):
        labels = list(manifest.get("source_labels") or [])
        if not labels:
            labels = list(manifest.get("media_source_labels") or [])
    return dict(Counter(str(label or "Unknown") for label in labels))


def _manifest_summary(result: dict) -> tuple[dict[str, int], dict]:
    """Read the persisted manifest because the pipeline result is intentionally slim."""
    manifest_path = str((result or {}).get("manifest_path") or "")
    if not manifest_path:
        return {}, {}
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}, {}
    providers = []
    for row in payload.get("media_resolution_v2") or []:
        if isinstance(row, dict):
            provider = str(row.get("provider") or "").strip()
            if provider:
                providers.append(provider)
    counts = dict(Counter(providers))
    return counts, dict(payload.get("youtube_coverage") or {})


def main() -> int:
    # Keep a transient OpenRouter outage from blocking the media test. The
    # brain still gets a bounded chance and the full-name deterministic query
    # path remains available for both lanes.
    os.environ["VIDZEO_QUERY_PLAN_STAGE_DEADLINE_SECONDS"] = "20"
    os.environ["VIDZEO_BRAIN_CONTAINER_STAGE_SECONDS"] = "15"
    os.environ["VIDZEO_BRAIN_CONTAINER_TIMEOUT"] = "15"
    os.environ["VIDZEO_BRAIN_SCENE_BUDGET_SECONDS"] = "20"
    os.environ["VIDZEO_BRAIN_SCENE_TIMEOUT"] = "12"
    os.environ["VIDZEO_QUERY_LLM_CANDIDATE_RACE_SECONDS"] = "20"
    # Keep web rescue bounded while allowing the YouTube branch to complete
    # authenticated source downloads and cut multiple moments from a master.
    os.environ.setdefault("VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS", "180")
    os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_CANDIDATES", "5")
    os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_ATTEMPTS", "5")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "output" / "productions" / f"prince_charles_mixed_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "script": SCRIPT,
        "title": TITLE,
        "content_title": TITLE,
        "name": TITLE,
        "output_name": f"Prince Charles - From Heir to King (Mixed) {stamp}",
        "output_dir": str(run_dir),
        "topic": "Prince Charles Charles Philip Arthur George King Charles III",
        "topic_anchor": "Prince Charles Charles Philip Arthur George King Charles III",
        "_topic_anchor": "Prince Charles Charles Philip Arthur George King Charles III",
        "_skip_sync_topic_anchor": True,
        "category": "history",
        # Explicit 50/50 quota: the planner assigns half the scene slots to
        # YouTube and half to web images. Web remains the verified rescue lane
        # only when authenticated footage cannot be obtained.
        "image_source": "Mixed Media",
        "mixed_media_policy": "manual",
        "mixed_media_weights": {
            "Web Images": 0.50,
            "AI Images": 0.00,
            "Stock Media": 0.00,
            "YouTube Clips": 0.50,
        },
        "strict_mixed_source_contract": False,
        "mixed_yt_shadow_web": True,
        # Let a verified master supply several distinct, transcript-timed
        # moments. Small corner watermarks are tolerated; facecams, presenters,
        # caption bands, and title-card overlays remain rejected or blurred.
        "yt_pool_first": True,
        "yt_allow_same_source_reuse": True,
        "yt_stream_downloads": True,
        "yt_lenient_acceptance": True,
        "youtube_recall_mode": True,
        "youtube_relaxed_visual_gate": True,
        "youtube_fail_open_on_visual_unavailable": True,
        "youtube_allow_bounded_caption_blur": True,
        "youtube_prefer_whisper_timing": True,
        "yt_min_source_duration": 20,
        "yt_max_download_scenes": 0,
        "yt_download_candidate_limit": 5,
        "yt_max_download_attempts_per_scene": 5,
        "strict_retrieval_accuracy": True,
        "cross_source_fallback": True,
        "allow_relevant_stock": False,
        "allow_stock_last_resort": False,
        "allow_emergency_media": False,
        # No blank cards: if a lane has a rare final gap, use a verified real
        # image/clip already in the story rather than synthetic media.
        "allow_duplicate_media_reuse": True,
        "_real_media_recovery": True,
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
    print("PRINCE CHARLES MIXED MEDIA PRODUCTION (50% YOUTUBE / 50% WEB)")
    print(f"Title: {TITLE}")
    print(f"Script words: {len(SCRIPT.split())}")
    print("YouTube lane: authenticated raw footage, transcript-timed moments")
    print("Watermarks: small corner logos tolerated; captions/facecams rejected or blurred")
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
    counts, youtube_coverage = _manifest_summary(result if isinstance(result, dict) else {})
    if not counts:
        counts = _source_counts(result if isinstance(result, dict) else {})
    report = {
        "success": bool(not error and video_path.is_file() and video_path.stat().st_size > 65536),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "title": TITLE,
        "script_word_count": len(SCRIPT.split()),
        "requested_mix": {"YouTube Clips": 0.50, "Web Images": 0.50},
        "source_counts": counts,
        "youtube_coverage": youtube_coverage,
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
