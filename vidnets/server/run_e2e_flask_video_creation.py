"""E2E Test: Creates a complete video through the active Flask server queue,
monitors generation through TTS, media download, editorial brain, and FFmpeg render,
then extracts and verifies output frames.
"""

import os
import sys
import time
import json
import urllib.request
import urllib.error
import subprocess
from pathlib import Path

FLASK_BASE_URL = "http://127.0.0.1:5000"
ROOT = Path(__file__).resolve().parent.parent

TEST_SCRIPT = """In October 1962, military intelligence confirmed an unprecedented missile crisis in Cuba.

Reconnaissance aircraft swept across the Caribbean coastline monitoring suspicious naval convoys.

Emergency defense appropriations surged past $50 billion as DEFCON 2 was officially declared.

Top commanders briefed the president on diplomatic containment measures to secure peace."""


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
    print("STARTING LIVE E2E FLASK VIDEO CREATION AND VERIFICATION TEST")
    print("=" * 80)

    # 1. Health check
    status = make_request("/api/queue/status")
    print(f"[1] Queue responsive: worker_running={status.get('worker_running')}, total_items={len(status.get('items', []))}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    title = f"Cuban Missile Crisis Cadence Test {stamp}"

    job_config = {
        "title": title,
        "name": title,
        "content_title": title,
        "output_name": title,
        "topic": "Cuban Missile Crisis 1962 Defense",
        "script": TEST_SCRIPT,
        "type": "long",
        "scene_mode": "Every 2 Beats",
        "image_source": "Web Images",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-BrianNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "render_backend": "ffmpeg",
        "visual_deadline_seconds": 300,
        "visual_shared_deadline_seconds": 300,
    }

    # 2. Submit job
    print("\n[2] Submitting video job to /api/queue/add...")
    res = make_request("/api/queue/add", job_config)
    item = res.get("item") or {}
    job_id = item.get("id")
    print(f"    Job queued successfully! Job ID: {job_id}")
    print(f"    Worker running: {res.get('worker_running')}, started: {res.get('worker_started')}")

    # 3. Monitor job
    print("\n[3] Monitoring generation progress...")
    last_msg = ""
    last_pct = -1
    t0 = time.time()
    completed_item = None

    while True:
        elapsed = time.time() - t0
        item_data = make_request(f"/api/queue/item/{job_id}").get("item") or {}
        status_val = item_data.get("status")
        pct = item_data.get("progress", 0)
        msg = str(item_data.get("progress_text") or item_data.get("message") or "")

        if msg != last_msg or pct != last_pct:
            safe_msg = msg.encode("ascii", "replace").decode("ascii")
            print(f"  [{elapsed:5.1f}s] [{status_val.upper()}] {pct:3d}% - {safe_msg}", flush=True)
            last_msg = msg
            last_pct = pct

        if status_val == "completed":
            completed_item = item_data
            break
        elif status_val == "failed":
            err = item_data.get("error") or item_data.get("message")
            print(f"\n[ERROR] Job failed: {err}")
            sys.exit(1)
        elif status_val == "cancelled":
            print("\n[ERROR] Job was cancelled")
            sys.exit(1)

        time.sleep(2)

    print("\n" + "=" * 80)
    print("LIVE VIDEO CREATION COMPLETED SUCCESSFULLY!")
    print("=" * 80)

    result = completed_item.get("result") or {}
    video_path = result.get("video_path")
    manifest_path = result.get("manifest_path")
    print(f"Output Video Path: {video_path}")
    print(f"Manifest Path: {manifest_path}")

    assert video_path and os.path.exists(video_path), f"Video not found at {video_path}"
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"Video File Size: {size_mb:.2f} MB")
    assert size_mb > 1.0, f"Video size too small: {size_mb} MB"

    # 4. Extract verification frames
    out_dir = ROOT / "verification_output" / f"job_{job_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[4] Extracting video frames to {out_dir}...")
    for sec, label in [(1.5, "scene0_date"), (5.0, "scene1_clean"), (8.5, "scene2_stat50b"), (12.0, "scene3_clean")]:
        frame_out = out_dir / f"frame_{label}.png"
        cmd = ["ffmpeg", "-y", "-ss", str(sec), "-i", str(video_path), "-vframes", "1", str(frame_out)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if frame_out.exists():
            print(f"  Extracted {frame_out.name} ({frame_out.stat().st_size:,} bytes) at {sec}s")

    print("\n[SUCCESS] Video creation and testing completed successfully!")


if __name__ == "__main__":
    main()
