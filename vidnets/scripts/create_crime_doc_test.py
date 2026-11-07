"""Create and run a fact-rich True Crime documentary test to verify Director behavior.

Tests:
1. Scene Grammar: Director Lego grammar assignment (chapter, statcard, chart, timeline, map, quote)
2. Motion Graphics & Charts: Smooth animations, truthful facts & figures rendering
3. Colors & Branding: True-crime visual DNA, dark noir palette, crimson accent, condensed typography
4. Captions: Moderate size, clean subtitle placement
5. Animation smoothness: sceneDuration-based animations, snappy spring physics, zero lag
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:5000"
OUTPUT_DIR = ROOT / "output" / "antwerp_diamond_heist_crime_test"

CRIME_SCRIPT = """In February 2003, an Italian crew orchestrated the largest diamond heist in world history beneath the streets of Antwerp.

The thieves breached a subterranean vault protected by ten layers of security to seize a staggering $100 million in loose diamonds.

Vault security recorded zero alarms, bypassing 100 million lock combinations, 63 security cameras, and infrared Doppler detectors.

Between 2000 and 2003, mastermind Leonardo Notarbartolo rented an office in the Diamond Center for three years to study every guard rotation.

The perpetrators fled from Antwerp across the Belgian border to Italy, discarding DNA evidence on a sandwich crust in a nearby forest.

Police detectives later declared: "It was the most sophisticated bank burglary Europe had ever witnessed, leaving 80 percent of the diamonds unrecovered today." """


def post_json(endpoint: str, payload: dict) -> tuple[int, dict]:
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}


def get_json(endpoint: str) -> tuple[int, dict]:
    url = f"{BASE_URL}{endpoint}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 70)
    print("VIDNETS STUDIO — TRUE CRIME DOCUMENTARY TEST (ANTWERP HEIST)")
    print("Evaluating Director behavior for grammar, charts, colors, and motion")
    print("=" * 70)

    # 1. Health check
    code, status = get_json("/api/queue/status")
    if code != 200:
        print(f"ERROR: Flask server queue unreachable at {BASE_URL} (status code: {code})")
        return 1
    print(f"Flask server active. Queue items: {len(status.get('items', []))}")

    # 2. Gather authentic documentary images
    footage_dir = ROOT / "assets" / "crime_heist_footage"
    footage_files = sorted(list(footage_dir.glob("*.jpg")))
    if len(footage_files) < 6:
        print(f"ERROR: Expected 6 images in {footage_dir}, found {len(footage_files)}")
        return 1

    custom_media_paths = [str(f.resolve()) for f in footage_files]
    print(f"Configured {len(custom_media_paths)} cinematic true-crime documentary stills:")
    for f in footage_files:
        print(f"   • {f.name} ({f.stat().st_size / 1024:.1f} KB)")

    # 3. Configure the True Crime production job
    job_payload = {
        "title": "The Antwerp Diamond Heist: The Vault Beneath the Streets",
        "content_title": "The Antwerp Diamond Heist: The Vault Beneath the Streets",
        "name": "Antwerp_Diamond_Heist_Crime_Test",
        "output_name": "Antwerp_Diamond_Heist_Crime_Test",
        "topic": "Antwerp diamond heist subterranean vault Italian crime syndicate detective investigation",
        "_topic_anchor": "Antwerp diamond heist subterranean vault Italian crime syndicate detective investigation",
        "topic_anchor": "Antwerp diamond heist subterranean vault Italian crime syndicate detective investigation",
        "genre": "true-crime",
        "script": CRIME_SCRIPT,
        "type": "long",
        "image_source": "Custom Images",
        "custom_images": custom_media_paths,
        "images": custom_media_paths,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "render_resolution": [1280, 720],
        "quality": "standard",
        "scene_mode": "Every Sentence",
        "media_mode": "manual",
        "scene_media_mode": "manual",
        "allow_emergency_media": True,
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_required": False,
        "strict_retrieval_accuracy": True,
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "fontsize": 28,
        "outline_size": 2,
        "margin_v": 36,
        "output_dir": str(OUTPUT_DIR.resolve()),
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "render_backend": "remotion",
        "semantic_render_concurrency": 1,
        "semantic_render_budget_seconds": 1200,
        "semantic_render_timeout_seconds": 1200,
    }

    # 4. Submit to Flask Server /api/queue/add
    print("\nSubmitting crime test job to /api/queue/add...")
    code, submit_resp = post_json("/api/queue/add", job_payload)
    if code != 200 or not submit_resp.get("success"):
        print(f"ERROR submitting job to queue: {code} -> {submit_resp}")
        return 1

    item = submit_resp.get("item") or {}
    job_id = item.get("id")
    print(f"SUCCESS! Job queued with ID: {job_id}")

    # 5. Poll queue item until completion
    print("\nMonitoring production pipeline in real time...")
    last_msg = ""
    started = time.monotonic()
    final_item = None

    while True:
        code, item_resp = get_json(f"/api/queue/item/{job_id}")
        if code != 200:
            print(f"Polling error ({code}): {item_resp}")
            time.sleep(2)
            continue

        item_data = item_resp.get("item") or {}
        status = str(item_data.get("status") or "").lower()
        progress = item_data.get("progress", 0)
        message = str(item_data.get("progress_message") or item_data.get("message") or "")

        if message != last_msg:
            elapsed = time.monotonic() - started
            print(f"  [{elapsed:5.1f}s] [{progress:3d}%] {message}", flush=True)
            last_msg = message

        if status in {"completed", "failed", "cancelled", "error"}:
            final_item = item_data
            break

        time.sleep(2)

    elapsed_total = round(time.monotonic() - started, 1)
    print("\n" + "=" * 70)
    print(f"Job finished in {elapsed_total}s with status: {final_item.get('status')}")
    print("=" * 70)

    if final_item.get("status") != "completed":
        print(f"ERROR: Generation did not succeed. Error: {final_item.get('error')}")
        return 1

    # 6. Validate Output Artifacts
    res = final_item.get("result") or {}
    video_path_str = res.get("video_path") or res.get("video")
    manifest_path_str = res.get("manifest_path")
    if not video_path_str or not Path(video_path_str).exists():
        candidates = list(OUTPUT_DIR.glob("*.mp4"))
        if candidates:
            video_path = candidates[0]
        else:
            print("ERROR: Output MP4 video file not found!")
            return 1
    else:
        video_path = Path(video_path_str)

    print(f"Output Video: {video_path}")
    print(f"Size: {video_path.stat().st_size / 1024 / 1024:.2f} MB")

    # 7. Audit Manifest
    semantic_manifest_candidate = ROOT / "temp" / f"{job_payload['output_name']}_semantic.manifest.json"
    if semantic_manifest_candidate.exists():
        manifest_file = semantic_manifest_candidate
    elif manifest_path_str and Path(manifest_path_str).exists():
        manifest_file = Path(manifest_path_str)
    else:
        manifest_candidates = list(OUTPUT_DIR.glob("*manifest*.json")) + list(OUTPUT_DIR.glob("*.json"))
        manifest_file = manifest_candidates[0] if manifest_candidates else None

    if manifest_file:
        print(f"\nAudit of Manifest: {manifest_file.name}")
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        v_dna = manifest.get("visual_dna") or {}
        print(f"   • Detected Genre: {v_dna.get('genre')}")
        print(f"   • Palette: {v_dna.get('palette')}")
        print(f"   • Typography: {v_dna.get('typography')}")
        print(f"   • Motion Style: {v_dna.get('motion_style')}")

        scenes = manifest.get("scenes") or []
        print(f"\n   • Total Scenes Generated: {len(scenes)}")
        for sc in scenes:
            idx = sc.get("scene_index", 0)
            dur = sc.get("duration", 0)
            pv = sc.get("primary_visual") or {}
            pv_type = pv.get("type", "N/A")
            pv_var = pv.get("variant", "N/A")
            contract = sc.get("editorial_contract") or pv.get("props", {}).get("editorial_contract") or {}
            family = contract.get("family", "N/A")
            narr = (sc.get("narration") or "")[:60]
            print(f"     [Scene {idx + 1}] ({dur:.1f}s) Visual: {pv_type} ({pv_var}) | Grammar Family: {family}")
            print(f"        Narration: {narr}...")

    # 8. Extract verification frames using ffmpeg
    print("\nExtracting verification frame captures...")
    frame_dir = ROOT / "output" / "antwerp_diamond_heist_crime_test" / "verification_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    brain_dir = Path(r"C:\Users\user\.gemini\antigravity\brain\fc1f212f-a68a-4229-8b3a-416703f35e6d")

    # Compute timeline timestamps for scenes
    cur_t = 0.0
    targets = []
    if manifest_file and manifest.get("scenes"):
        for i, sc in enumerate(manifest["scenes"]):
            dur = float(sc.get("duration", 3.5))
            pv = sc.get("primary_visual") or {}
            pv_type = pv.get("type", "Media")
            mid_t = round(cur_t + dur * 0.5, 2)
            targets.append((i + 1, mid_t, pv_type, pv.get("variant", "")))
            cur_t += dur
    else:
        targets = [(1, 3.0, "Intro", ""), (6, 17.0, "Chart", ""), (10, 31.0, "Map", ""), (14, 45.0, "Quote", "")]

    print(f"Computed {len(targets)} scene midpoints:")
    for sc_num, ts, pv_type, pv_var in targets:
        print(f"   Scene {sc_num:2d} @ {ts:5.2f}s: {pv_type} ({pv_var})")

    extracted_frames = []
    for sc_num, ts, pv_type, pv_var in targets:
        slug = f"crime_scene_{sc_num}_{pv_type}_{int(ts)}s".replace(" ", "_").lower()
        out_jpg = frame_dir / f"{slug}.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", str(ts), "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(out_jpg)
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            print(f"   • Extracted Scene {sc_num} ({pv_type}) at {ts}s: {out_jpg.name}")
            extracted_frames.append(out_jpg)
            if brain_dir.exists():
                import shutil
                shutil.copy2(out_jpg, brain_dir / out_jpg.name)
        except Exception as e:
            print(f"   • Could not extract frame at {ts}s: {e}")

    print("\n" + "=" * 70)
    print("CRIME DOCUMENTARY TEST COMPLETE!")
    print(f"Total verification frames extracted: {len(extracted_frames)}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
