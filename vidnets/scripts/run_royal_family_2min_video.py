import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add server directory to path
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

# Create log directory
RUN_ID = f"test_run_royal_family_2min_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
LOG_DIR = ROOT_DIR / "logs" / "test_runs" / RUN_ID
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "execution.log"

# Set optimal environment defaults for royal documentary pipeline
os.environ["VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS"] = "480"
os.environ["VIDZEO_SCRIPT_NICHE"] = "documentary"
os.environ["VIDZEO_TOPIC_NICHE"] = "documentary"
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
os.environ["VIDZEO_WEB_DISABLE_ROYAL_PREFILTER"] = "1"


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


def probe_video(video_path: str) -> dict:
    ffprobe_bin = ROOT_DIR / "tools" / "ffmpeg" / "ffprobe.exe"
    if not ffprobe_bin.is_file():
        ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
    cmd = [
        str(ffprobe_bin),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(res.stdout)
    except Exception as exc:
        print(f"[ffprobe] Warning: failed to probe {video_path}: {exc}")
        return {}


def main():
    print("=" * 70)
    print("VIDNETS STUDIO: 2-MINUTE BRITISH ROYAL FAMILY DOCUMENTARY")
    print(f"Run ID: {RUN_ID}")
    print(f"Logs: {LOG_FILE}")
    print("=" * 70)

    title = "The British Royal Family: Tradition and Change"

    # 307 words, 14 paragraphs/scenes (~120 seconds target duration after silence removal)
    script = (
        "For nearly a millennium, the British Royal Family has stood as an enduring symbol of history, continuity, and sovereign authority.\n\n"
        "From the thousand-year-old stone halls of Westminster Abbey to the grand gates of Buckingham Palace, the British Crown has witnessed the rise and transformation of modern civilization.\n\n"
        "Across centuries of turbulent global shifts, the monarchy has survived by carefully balancing ancient ceremonial traditions with modern constitutional duty.\n\n"
        "The contemporary era of the royal house was forever defined by Queen Elizabeth the Second, who ascended the throne in 1952.\n\n"
        "Throughout her historic seventy-year reign, she served as an unwavering anchor of stability through post-war reconstruction, the dawn of television, and the modern Commonwealth.\n\n"
        "Her diplomatic visits and annual Christmas broadcasts connected generations of citizens across Great Britain and across the wider world.\n\n"
        "Today, King Charles the Third carries forward the sacred responsibilities of the Crown, guiding the royal family into a brand new era.\n\n"
        "The King has championed urgent environmental sustainability, architectural heritage preservation, and diverse community empowerment.\n\n"
        "Alongside the King, the Prince and Princess of Wales represent the next generation of royal leadership, championing mental health, early childhood development, and modern public engagement.\n\n"
        "Every year, millions of spectators gather in central London to witness the pomp and splendour of Trooping the Colour and the State Opening of Parliament.\n\n"
        "Gilded carriages roll along the Mall, escorted by the Household Cavalry in gleaming breastplates and iconic bearskin caps.\n\n"
        "Yet beneath the dazzling imperial crowns, the timeless palaces, and the grandeur of state banquets lies a dedicated institution navigating public life under constant scrutiny.\n\n"
        "From the fortress walls of Windsor Castle to the wild highland landscapes of Balmoral, royal history is woven deeply into the British identity.\n\n"
        "As modern Britain continues to evolve, the House of Windsor endures, bridging a thousand years of sovereign heritage with the promises of tomorrow."
    )

    job_config = {
        "title": title,
        "content_title": title,
        "output_name": "British_Royal_Family_2Min_Production",
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
        "silence_min_duration_seconds": 0.38,
        "silence_target_cadence_seconds": 0.22,
        # ── Approved editing config: sentence-level beats & full director ──
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
        # ── Director model chain ──
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
        # ── Media pipeline settings ──
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
        "niche": "British royal family documentary",
        "script_description": (
            "Two-minute British Royal Family documentary covering royal ceremonies, "
            "Trooping the Colour, Buckingham Palace, Westminster Abbey, Queen Elizabeth II, "
            "King Charles III, and the House of Windsor. Avoid unrelated footage."
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
        print("\n[Pipeline] Launching core _run_video_pipeline for 2-Minute Royal Family...")
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

            probe_info = probe_video(video_path)
            fmt = probe_info.get("format", {})
            duration = float(fmt.get("duration", 0.0))
            audit_data["ffprobe_duration_seconds"] = round(duration, 2)
            print(f"[ffprobe] Duration: {duration:.2f} seconds ({duration/60:.2f} minutes)")

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
    summary_md = f"""# 2-Minute Test Run Summary: The British Royal Family

- **Run ID**: `{RUN_ID}`
- **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **Title**: {title}
- **Status**: {'SUCCESS' if success else 'FAILED'}
- **Generation Elapsed**: {audit_data['elapsed_seconds']}s ({round(audit_data['elapsed_seconds']/60, 2)} mins)
- **Output Video**: `{audit_data.get('video_path', 'N/A')}`
- **Video Duration**: {audit_data.get('ffprobe_duration_seconds', 'N/A')}s ({round(audit_data.get('ffprobe_duration_seconds', 0)/60, 2) if audit_data.get('ffprobe_duration_seconds') else 'N/A'} mins)
- **Video File Size**: {audit_data.get('video_file_size_mb', 'N/A')} MB

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
