"""Render a footage-first, two-minute British royal family documentary."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

# Keep this run deterministic and prevent a rejected clip from turning into a
# still-image slideshow. Web/Yandex discovery remains available to the normal
# app, while this brief explicitly requires moving YouTube footage.
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
os.environ.setdefault("VIDZEO_YT_OVERLAY_SCREEN", "1")
# A presenter flag alone is not enough to reject a moving documentary shot;
# the footage brief relies on the frame/overlay audit for hard contamination.
os.environ.setdefault("VIDZEO_YT_REJECT_TALKING_HEAD", "0")
os.environ.setdefault("VIDZEO_YT_PROGRESSIVE_ATTEMPTS", "8")
os.environ.setdefault("VIDZEO_YT_V2_QUERIES_PER_CONTAINER", "10")
os.environ.setdefault("VIDZEO_YT_RESULTS_PER_QUERY", "24")

TITLE = "British Royal Family"
SCRIPT = """The British royal family is one of the world’s most recognisable institutions, combining centuries of ceremony with a public role that continues to change.

The modern story begins with the Crown’s constitutional position. The monarch is head of state, while elected ministers govern. That balance makes royal appearances symbolic: a balcony, a carriage procession, or a state opening can turn a formal duty into a shared national moment.

Buckingham Palace remains the best-known setting for that public role. Guards, crowds, visiting heads of state, and the changing of the guard create moving images that connect today’s monarchy with a much longer history.

Queen Elizabeth II shaped the family’s public identity for seven decades. Her reign included jubilees, Commonwealth tours, state visits, and countless walkabouts in which the royal household met people face to face.

King Charles III now carries that work forward. His programme combines traditional ceremonies with long-running interests in the environment, architecture, education, and community partnerships. Each engagement is carefully staged, but the people and places around it give the footage its human meaning.

The Prince and Princess of Wales represent another part of the transition. Their public work often focuses on children, mental health, early years support, and the armed forces. Their appearances show how the institution tries to speak to a younger generation while preserving familiar rituals.

Across the Commonwealth, royal visits still bring diplomacy and local history together. A welcoming ceremony, a community project, or a meeting with leaders can explain a relationship more clearly than a headline alone.

The royal family therefore exists in two frames at once: historic ceremony and contemporary public service. The strongest documentary footage captures both, allowing movement, faces, places, and genuine events to carry the story without replacing them with static slides."""

OUTPUT_DIR = ROOT / "output" / "british_royal_family_two_minute_footage"


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
        "topic_anchor": "British royal family archival ceremony moving footage",
        "_topic_anchor": "British royal family archival ceremony moving footage",
        "script": SCRIPT,
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "footage_only": True,
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_required": True,
        "strict_retrieval_accuracy": True,
        "allow_emergency_media": False,
        "allow_relevant_stock": False,
        "allow_stock_last_resort": False,
        # Footage-only jobs still exercise the web branch for telemetry, but
        # keep the full YouTube retrieval window so moving clips can replace
        # rejected title-card candidates before the final contract is checked.
        "cross_source_fallback": False,
        "fail_on_unresolved_media": True,
        "youtube_accepted_coverage_target": 1.0,
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
        "max_still_hold_seconds": 0.0,
        "max_visual_beat_seconds": 3.5,
        "media_deadline_seconds": 900,
        "visual_deadline_seconds": 900,
        "visual_shared_deadline_seconds": 900,
        "yt_branch_wall_timeout_seconds": 600,
        "mixed_yt_wall_cap_seconds": 600,
        "auto_export": True,
    }
    started = time.monotonic()
    print(f"ROYAL FAMILY FOOTAGE RUN — {len(SCRIPT.split())} words", flush=True)
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
        "footage_only": True,
    }
    summary_path = OUTPUT_DIR / "generation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
