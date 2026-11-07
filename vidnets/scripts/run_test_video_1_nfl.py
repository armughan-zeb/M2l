import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add server directory to path
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

# Create log directory
RUN_ID = f"test_run_01_nfl_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
LOG_DIR = ROOT_DIR / "logs" / "test_runs" / RUN_ID
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "execution.log"

# Set optimal environment defaults for sports documentary pipeline
os.environ["VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS"] = "480"
os.environ["VIDZEO_SCRIPT_NICHE"] = "sports"
os.environ["VIDZEO_TOPIC_NICHE"] = "sports"
os.environ["VIDZEO_YT_REUSE_TIMESTAMP_GAP"] = "20"
os.environ["VIDZEO_YT_LENIENT_ACCEPTANCE"] = "1"
os.environ["VIDZEO_YT_ALLOW_BOUNDED_CAPTION_BLUR"] = "1"
os.environ["VIDZEO_YT_DOWNLOAD_CANDIDATES"] = "2"
os.environ["VIDZEO_STRICT_CREATIVE_REVIEW"] = "0"
os.environ["VIDZEO_YT_RELAXED_VISUAL_GATE"] = "1"
os.environ["VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE"] = "1"
os.environ["VIDZEO_YT_HARD_STOP_PROCESS"] = "0"
os.environ["VIDZEO_REMOTION_CONCURRENCY"] = "2"
os.environ["VIDZEO_REMOTION_MAX_WORKERS"] = "2"


# Setup dual logging (stdout + file)
class DualLogger:
    def __init__(self, filepath):
        self.terminal = sys.__stdout__
        self.logfile = open(filepath, "w", encoding="utf-8", buffering=1)

    def write(self, message):
        try:
            self.terminal.write(message)
            self.terminal.flush()
        except Exception:
            pass
        if not self.logfile.closed:
            try:
                self.logfile.write(message)
                self.logfile.flush()
            except Exception:
                pass

    def flush(self):
        try:
            self.terminal.flush()
        except Exception:
            pass
        if not self.logfile.closed:
            try:
                self.logfile.flush()
            except Exception:
                pass

    def close(self):
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        try:
            self.logfile.close()
        except Exception:
            pass


dual_logger = DualLogger(LOG_FILE)
sys.stdout = dual_logger
sys.stderr = dual_logger

from app import _run_video_pipeline
from services.pipeline_decision_auditor import (
    audit_youtube_quota,
    audit_distant_timestamp_reuse,
    audit_llm_decisions,
    audit_pipeline_health,
)


def progress_tracker(percentage: float, message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{percentage:5.1f}%] {message}", flush=True)


def main():
    print("=" * 70)
    print(f"VIDZEO TEST VIDEO 1: 1-MINUTE NFL DOCUMENTARY GENERATION")
    print(f"Run ID: {RUN_ID}")
    print(f"Logs: {LOG_FILE}")
    print("=" * 70)

    title = "Modern NFL: Speed and Power"
    script = (
        "The 2026 NFL season rewrote the record books.\n\n"
        "Thirty-two teams. Eighteen weeks. Over 17,000 plays.\n\n"
        "Patrick Mahomes threw for 4,839 yards and 41 touchdowns. "
        "Lamar Jackson rushed for over 1,000 yards for the fourth time. "
        "Josh Allen became the first quarterback to throw and rush for 30 touchdowns in a single season.\n\n"
        "But offenses weren't the only story. "
        "Defenses adapted with aggressive disguise coverages and devastating edge-rush packages. "
        "Pass rush win rates hit 48 percent league-wide, the highest since tracking began.\n\n"
        "Every Sunday came down to the final minutes. "
        "Fourth-quarter comebacks rose to 67 across the season. "
        "Acrobatic sideline catches and clutch field goals decided entire playoff races.\n\n"
        "From freezing snow games in Kansas City to deafening indoor stadiums in Las Vegas, "
        "the race for the Lombardi Trophy was fiercer than ever.\n\n"
        "In the NFL, perfection is never given. It is fought for on every single blade of grass."
    )

    job_config = {
        "title": title,
        "content_title": title,
        "output_name": "Modern_NFL_Speed_and_Power",
        "script": script,
        "type": "long",
        "image_source": "Mixed Media",
        "youtube_target_percentage": "50%",
        "yt_branch_wall_timeout_seconds": 480,
        "yt_hard_stop_process": False,
        "captions_enabled": True,
        "caption_style": "hormozi",
        "words_per_group": 3,
        "bold": True,
        "uppercase": True,
        "resolution": "1920x1080 (Full HD)",
        "render_resolution": [1920, 1080],
        "render_backend": "remotion",
        "concurrency": 2,
        "remotion_concurrency": 2,
        "allow_emergency_media": True,
        "allow_grounded_graphic_fallback_strict": True,
        "allow_grounded_graphic_fallback": True,
        "strict_creative_review": False,
        "tts_model": "audiogram",
        "voice": "en-US-ChristopherNeural",
        # ── Approved#1 editing config: sentence-level beats & full director ──
        "scene_mode": "Every Beat",
        "director_mode": True,
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_render_required": True,
        "visual_understanding": True,
        "graphic_density": 0.62,
        "sound_density": 0.56,
        "editorial_cue_v2_enabled": True,
        "enable_editorial_cues": True,
        "enable_motion_templates": True,
        "enable_sound_library": True,
        "animate_all_media": True,
        "effect": "Zoom Combo",
        "strict_retrieval_accuracy": True,
        "web_collect_all_sources": True,
        # ── Director model chain (matches Approved#1) ──
        "scene_brain_model": "openai/gpt-6-luna",
        "scene_brain_fallback_models": [
            "openai/gpt-5.6-luna",
            "deepseek/deepseek-v4.1-flash",
            "qwen/qwen3.5-flash-02-23",
        ],
        "scene_brain_fallback_chain": [
            "openai/gpt-6-luna",
            "openai/gpt-5.6-luna",
            "deepseek/deepseek-v4.1-flash",
            "qwen/qwen3.5-flash-02-23",
        ],
        "llm_model": "qwen/qwen3.5-flash-02-23",
        "llm_provider": "openrouter",
        "qwen_vision_model": "qwen/qwen3.5-flash-02-23",
        "qwen_vision_fallback_model": "qwen/qwen3.5-flash-02-23",
        "qwen_vision_fallback_models": [
            "qwen/qwen3.5-flash-02-23",
            "google/gemma-4-26b-a4b-it:free",
            "nex-agi/nex-n2.5-mini:free",
            "openrouter/free",
        ],
        # ── Media pipeline settings (matches Approved#1) ──
        "visual_required": True,
        "global_media_guarantee": True,
        "media_fallback_policy": "real_media_only",
        "cross_source_fallback": True,
        "unique_web_assets_required": True,
        "yt_lenient_acceptance": True,
        "youtube_prefer_whisper_timing": True,
        "youtube_recall_mode": True,
        "allow_stock_last_resort": True,
        "allow_relevant_stock": True,
        "mixed_media_policy": "automatic",
        "yt_allow_same_source_reuse": True,
        "max_final_video_reuse_per_clip": 1,
        "yt_download_candidate_limit": 4,
        "yt_max_download_attempts_per_scene": 3,
        "pipeline_v2_mode": "on",
        "niche": "NFL sports history",
        "script_description": (
            "One-minute NFL documentary about the modern 2026 season. "
            "Keep every media query about NFL, football, quarterbacks, "
            "defensive plays, stadiums, and the Lombardi Trophy. "
            "Avoid unrelated footage."
        ),
        # ── Caption & typography ──
        "font": "DM Sans",
        "fontsize": 42,
        "text_color": "#F7F6F2",
        "highlight_color": "#F2C94C",
        "outline_color": "#07121A",
        "outline_size": 2,
        "shadow_on": True,
        "bg_on": False,
        "caption_mode": "word",
        "music_volume": 14,
        "quality": "high",
    }

    # Save job config for records
    config_record_path = LOG_DIR / "job_config.json"
    config_record_path.write_text(json.dumps(job_config, indent=2), encoding="utf-8")
    print(f"[Record] Saved job_config.json to {config_record_path}")

    start_time = time.monotonic()
    success = False
    result = None

    try:
        print("\n[Pipeline] Launching core _run_video_pipeline...")
        result = _run_video_pipeline(job_config, progress_callback=progress_tracker)
        elapsed = time.monotonic() - start_time
        success = True
        print("\n" + "=" * 70)
        print(f"[SUCCESS] Video generation completed in {elapsed:.1f}s ({elapsed/60:.2f} mins)!")
        print("=" * 70)
    except Exception as exc:
        elapsed = time.monotonic() - start_time
        print("\n" + "!" * 70)
        print(f"[FAILED] Pipeline terminated with error after {elapsed:.1f}s: {exc}")
        import traceback
        traceback.print_exc()
        print("!" * 70)
        success = False

    # Audit & Record
    print("\n[Audit] Performing Pipeline Decision Audit & Quality Analysis...")
    audit_data = {
        "run_id": RUN_ID,
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(time.monotonic() - start_time, 2),
        "success": success,
    }

    if result:
        video_path = result.get("video_path")
        manifest = result.get("component_manifest") or result.get("manifest") or {}
        manifest_path = result.get("manifest_path")

        if not manifest and manifest_path and os.path.exists(manifest_path):
            try:
                manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            except Exception:
                pass

        if not manifest and result.get("output_package_dir"):
            pkg_p = Path(result["output_package_dir"])
            for m_candidate in pkg_p.glob("*.manifest.json"):
                try:
                    manifest = json.loads(m_candidate.read_text(encoding="utf-8"))
                    break
                except Exception:
                    pass

        audit_data["video_path"] = video_path
        if video_path and os.path.exists(video_path):
            file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
            audit_data["video_file_size_mb"] = round(file_size_mb, 2)
            print(f"[Output] Rendered Video: {video_path} ({file_size_mb:.2f} MB)")

        # Save manifest
        if manifest:
            manifest_p = LOG_DIR / "manifest.json"
            manifest_p.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
            print(f"[Record] Saved manifest.json to {manifest_p}")

        # Run Audits
        quota_audit = audit_youtube_quota(manifest, requested_percentage=50)
        distant_audit = audit_distant_timestamp_reuse(manifest, min_gap_seconds=8.0)
        llm_audit = audit_llm_decisions(manifest)
        health_audit = audit_pipeline_health(manifest, max_fallback_ratio=0.25)

        audit_data["quota_audit"] = quota_audit
        audit_data["distant_timestamp_audit"] = distant_audit
        audit_data["llm_decisions_audit"] = llm_audit
        audit_data["health_audit"] = health_audit

        print("\n--- DECISION AUDIT SUMMARY ---")
        print(f"1. YouTube Quota Target: requested=50%, actual={quota_audit.get('actual_count')}/{quota_audit.get('scene_count')} scenes ({round(quota_audit.get('actual_ratio', 0)*100, 1)}%) [Status: {quota_audit.get('status').upper()}]")
        print(f"2. Distant YouTube Cuts: reuses={distant_audit.get('reused_sources_count')}, zero_collision={distant_audit.get('zero_collision_guarantee')}, overlaps={len(distant_audit.get('overlaps_found', []))} [Status: {distant_audit.get('status').upper()}]")
        print(f"3. Scene Baseline Health: primary_real_media={health_audit.get('primary_scenes_count')}, fallbacks={health_audit.get('fallback_scenes_count')}, missing={health_audit.get('missing_slots_count')} [Status: {health_audit.get('status').upper()}]")
        print(f"4. LLM Decision Score: {llm_audit.get('decision_reliability_score')}% reliability [Status: {llm_audit.get('status').upper()}]")
        print("-------------------------------")

    # Save audit report
    audit_report_p = LOG_DIR / "audit_report.json"
    audit_report_p.write_text(json.dumps(audit_data, indent=2, default=str), encoding="utf-8")
    print(f"[Record] Saved audit_report.json to {audit_report_p}")

    # Generate Markdown Summary
    summary_md = f"""# Test Video 1 Run Summary: Modern NFL

- **Run ID**: `{RUN_ID}`
- **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **Title**: {title}
- **Status**: {'SUCCESS' if success else 'FAILED'}
- **Duration Elapsed**: {audit_data['elapsed_seconds']}s
- **Output Video**: `{audit_data.get('video_path', 'N/A')}`
- **Video Size**: {audit_data.get('video_file_size_mb', 'N/A')} MB

## Pipeline Audits
- **YouTube Quota**: {audit_data.get('quota_audit', {}).get('actual_count', 0)} / {audit_data.get('quota_audit', {}).get('scene_count', 0)} scenes ({round(audit_data.get('quota_audit', {}).get('actual_ratio', 0)*100, 1)}%)
- **Zero Collision Guarantee**: {audit_data.get('distant_timestamp_audit', {}).get('zero_collision_guarantee', 'N/A')}
- **Missing Slots**: {audit_data.get('health_audit', {}).get('missing_slots_count', 'N/A')}
- **Primary Media Ratio**: {round(audit_data.get('health_audit', {}).get('primary_ratio', 0)*100, 1)}%

## Preserved Artifacts
- Log: `execution.log`
- Config: `job_config.json`
- Manifest: `manifest.json`
- Audit: `audit_report.json`
"""
    summary_p = LOG_DIR / "summary.md"
    summary_p.write_text(summary_md, encoding="utf-8")
    print(f"[Record] Preserved documentation summary to {summary_p}")

    dual_logger.close()


if __name__ == "__main__":
    main()
