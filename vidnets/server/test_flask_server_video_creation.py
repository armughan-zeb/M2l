import json
import os
import sys
import time
import urllib.request
import urllib.error

FLASK_BASE_URL = "http://127.0.0.1:5000"

TEST_SCRIPT = """The Great Pyramid of Giza was built as a monumental tomb for the Pharaoh Khufu and stood as the tallest man-made structure for thousands of years.

The Lighthouse of Alexandria guided sailors into ancient Egyptian harbors with a burning fire and massive polished bronze mirrors.

The Hanging Gardens of Babylon were celebrated as a wondrous ascending series of terraced gardens rising like a green mountain in Mesopotamia.

Today, though most have vanished, these ancient monuments continue to capture the human imagination."""

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
    print("STARTING FLASK SERVER VIDEO PIPELINE TEST")
    print(f"Target: {FLASK_BASE_URL}")
    print("=" * 80, flush=True)

    status = make_request("/api/queue/status")
    print(f"[1] Server is responsive! Queue status: worker_running={status.get('worker_running')}, items={len(status.get('items', []))}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    title = f"Ancient Wonders of the World (Flask Test {stamp})"
    job_config = {
        "title": title,
        "name": title,
        "content_title": title,
        "output_name": title,
        "topic": "Ancient Wonders of the World Great Pyramid Lighthouse Alexandria",
        "script": TEST_SCRIPT,
        "type": "long",
        "scene_mode": "Every 2 Beats",
        "image_source": "Mixed Media",
        "mixed_media_policy": "manual",
        "mixed_media_weights": {
            "Web Images": 0.50,
            "YouTube Clips": 0.50,
        },
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-BrianNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "visual_deadline_seconds": 1200,
        "visual_shared_deadline_seconds": 1200,
    }

    print("\n[2] Submitting job to /api/queue/add...")
    res = make_request("/api/queue/add", job_config)
    item = res.get("item") or {}
    job_id = item.get("id")
    print(f"    Job queued! Job ID: {job_id}")
    print(f"    Worker running: {res.get('worker_running')}, started: {res.get('worker_started')}")

    print("\n[3] Monitoring job progress in real-time...")
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
            print(f"  [{elapsed:5.1f}s] [{status_val.upper()}] {pct:3d}% - {msg}", flush=True)
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

        time.sleep(2)

    print("\n" + "=" * 80)
    print("JOB COMPLETED SUCCESSFULLY VIA FLASK SERVER!")
    print("=" * 80)
    result = completed_item.get("result") or {}
    video_path = result.get("video_path")
    manifest_path = result.get("manifest_path")
    print(f"Video Path: {video_path}")
    print(f"Manifest Path: {manifest_path}")

    if video_path and os.path.exists(video_path):
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        print(f"Video File Exists: YES ({size_mb:.2f} MB)")
    else:
        print("Video File Exists: NO")

    if manifest_path and os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as mf:
            mdata = json.load(mf)
        resolutions = mdata.get("media_resolution_v2", [])
        yt_coverage = mdata.get("youtube_coverage", {})
        print(f"\nTotal Scenes: {len(resolutions)}")
        yt_scenes = []
        for idx, r in enumerate(resolutions):
            prov = r.get("provider")
            fpath = os.path.basename(r.get("path", ""))
            print(f"  Scene {idx+1}: provider={prov:8s} | file={fpath}")
            if prov == "youtube":
                yt_scenes.append((idx+1, fpath))
        print(f"\nYouTube Coverage: {json.dumps(yt_coverage, indent=2)}")
        print(f"Total YouTube Scenes: {len(yt_scenes)}")
        print("\nAll checks passed successfully!")

if __name__ == "__main__":
    main()
