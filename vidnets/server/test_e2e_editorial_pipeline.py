import json
import os
import sys
import time
import urllib.request
import urllib.error

FLASK_BASE_URL = "http://127.0.0.1:5000"

TEST_SCRIPT = """The Roman Colosseum was completed in 80 AD, capable of holding over fifty thousand spectators.

Deep beneath the arena floor, the hypogeum housed gladiators, prisoners, and exotic animals within a labyrinth of tunnels.

Engineers crafted ingenious trapdoors and hydraulic lifts to elevate lions and combatants directly into the sunlit arena.

Today, after nearly two millennia, this monumental amphitheater stands as Rome's greatest architectural triumph."""

def make_request(endpoint, data=None):
    url = f"{FLASK_BASE_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"HTTP Error {e.code}: {body}")
        raise

def main():
    print("=" * 80)
    print("STARTING FULL EDITORIAL PIPELINE E2E TEST (4 SCENES)")
    print(f"Target: {FLASK_BASE_URL}")
    print("=" * 80, flush=True)

    status = make_request("/api/queue/status")
    print(f"[1] Queue status: worker_running={status.get('worker_running')}, queue_count={len(status.get('queue', []))}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    title = f"Colosseum Documentary ({stamp})"
    job_config = {
        "title": title,
        "name": title,
        "content_title": title,
        "output_name": title,
        "topic": "Roman Colosseum ancient amphitheater history gladiators architecture",
        "script": TEST_SCRIPT,
        "type": "long",
        "scene_mode": "Every 2 Beats",
        "image_source": "Web Images",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "enable_sound_library": True,
        "enable_editorial_cues": True,
        "enable_motion_templates": True,
        "editorial_style": "cinematic_documentary",
        "documentary_density": "dynamic",
        "motion_sfx_enabled": True,
        "transition_sfx_enabled": True,
        "semantic_theme": "cinematic_gold",
        "visual_deadline_seconds": 600,
        "visual_shared_deadline_seconds": 600,
    }

    print("\n[2] Submitting job to /api/queue/add...")
    res = make_request("/api/queue/add", job_config)
    item = res.get("item") or {}
    job_id = item.get("id")
    print(f"    Job queued! Job ID: {job_id}")

    # Ensure queue worker is started
    if not res.get("worker_running"):
        print("    Starting queue worker...")
        make_request("/api/queue/start", {})

    print("\n[3] Monitoring job execution...")
    last_msg = ""
    last_pct = -1
    t0 = time.time()
    completed_item = None

    while True:
        elapsed = time.time() - t0
        item_data = make_request(f"/api/queue/item/{job_id}").get("item") or {}
        status_val = item_data.get("status")
        pct = item_data.get("progress", 0)
        msg = item_data.get("message", "")

        if msg != last_msg or pct != last_pct:
            print(f"  [{elapsed:5.1f}s] [{str(status_val).upper()}] {pct:3d}% - {msg}", flush=True)
            last_msg = msg
            last_pct = pct

        if status_val == "completed":
            completed_item = item_data
            break
        elif status_val == "failed":
            print(f"\n[ERROR] Job failed: {item_data.get('error')}")
            sys.exit(1)
        elif status_val == "cancelled":
            print("\n[ERROR] Job was cancelled")
            sys.exit(1)

        time.sleep(1.5)

    print("\n" + "=" * 80)
    print("JOB COMPLETED SUCCESSFULLY!")
    result = completed_item.get("result") or {}
    print(f"Output Video Path: {result.get('video_path')}")
    print(f"Video URL: {result.get('video_url')}")
    print(f"Duration: {result.get('duration')}s")
    print(f"Manifest Path: {result.get('manifest_path')}")
    manifest = result.get("component_manifest") or {}
    print(f"Component Manifest Clips: {len(manifest.get('video_clips', []))}")
    print("=" * 80)

if __name__ == "__main__":
    main()
