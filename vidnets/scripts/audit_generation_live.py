"""Run one real Fish TTS / mixed-media production job without touching the queue."""
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import app

SCRIPT = """Grand Theft Auto VI brings the series back to Vice City, a fictional city inspired by Miami. Rockstar Games introduced Lucia in the first official trailer, placing her at the center of a new crime story.

The trailer moves between crowded beaches, busy highways, and wetlands. Those scenes offer a glimpse of the game's setting, but a trailer cannot prove how every feature will work during play.

Grand Theft Auto V gameplay can illustrate driving and exploration while discussing the series. It should be clearly understood as footage from the earlier game, rather than evidence of new GTA VI mechanics.

For the clearest picture of Grand Theft Auto VI, distinguish official trailer scenes from speculation and older gameplay. Watch what Rockstar Games actually shows, and keep unconfirmed claims separate."""

if __name__ == "__main__":
    started = time.monotonic()
    events = []
    report = ROOT / "output" / ("workflow_audit_smoke.json" if '--smoke' in sys.argv else ("workflow_audit_retest.json" if '--retest' in sys.argv else "workflow_audit_live.json"))
    config = {
        "title": "GTA VI Workflow Audit", "name": "GTA VI Workflow Audit",
        "script": SCRIPT, "image_source": "Mixed Media", "type": "long",
        "scene_mode": "Every 2 Beats", "tts_model": "fish-audio/s2.1-pro-free:free",
        "vo_mode": "Use OpenRouter TTS", "voice": "", "whisper_mode": "local",
        "aspect_ratio": "16:9", "resolution": "1080p",
        "captions_enabled": True,
        # The live audit must always produce an inspectable file even when a
        # provider times out.  This is an audit-only last-resort switch; the
        # production app still keeps synthetic media disabled by default.
        "allow_emergency_media": True,
    }
    if '--retest' in sys.argv:
        config.update(delivery_budget_seconds=600, render_reserve_seconds=180)
    if '--smoke' in sys.argv:
        config.update(script=SCRIPT.split('\n\n')[0], title='Grand Theft Auto VI', name='GTA VI Smoke Test', delivery_budget_seconds=480, render_reserve_seconds=180)

    def progress(percent, message):
        event = {"elapsed_seconds": round(time.monotonic() - started, 2),
                 "percent": percent, "message": str(message)}
        events.append(event)
        print(json.dumps(event), flush=True)

    try:
        result = app._run_video_pipeline(config, progress_callback=progress)
        data = {"success": True, "elapsed_seconds": time.monotonic() - started,
                "events": events, "result": result}
    except Exception as exc:
        traceback.print_exc()
        data = {"success": False, "elapsed_seconds": time.monotonic() - started,
                "events": events, "error": str(exc)}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print("AUDIT_REPORT", report, "SUCCESS", data["success"], flush=True)
    sys.exit(0 if data["success"] else 1)
