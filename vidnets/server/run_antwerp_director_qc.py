"""Submit and monitor a new fact-led crime-video director test via Flask."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_URL = "http://127.0.0.1:5000"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output" / "antwerp_diamond_heist_director_final"

# Fact statements are deliberately sentence-complete.  This provides a stable
# test of the phrase-preservation contract for figures such as 123 of 160 and
# 100 million, rather than relying on generated text that may split them.
SCRIPT = " ".join([
    "On the weekend of February 15 and 16, 2003, thieves entered Antwerp's Diamond Center in Belgium.",
    "Inside the vault, they opened 123 of 160 deposit boxes.",
    "That left 37 boxes untouched, making the scale of the break-in immediately visible.",
    "The vault had 10 security layers, including cameras, infrared sensors, radar, and a 100-million-combination lock.",
    "The crew took diamonds, gold, jewelry, and other valuables valued at more than 100 million US dollars.",
    "The break-in was discovered on Monday morning, after the weekend theft had already ended.",
    "Police arrested four suspects, while reports said the missing goods had not been recovered.",
    "The facts tell the story: 123 boxes opened, 37 untouched, 10 security layers, and a loss beyond 100 million dollars.",
    "That is why the Antwerp diamond heist remains one of the most audacious property crimes on record.",
])

# Hand-selected, already local source visuals for this production. This is
# deliberately a real-media lane: it keeps the director test deterministic and
# prevents a failed remote search from injecting a blank gradient plate.
ASSET_MEDIA = ROOT / "assets" / "crime_heist_footage"
CUSTOM_IMAGES = [
    str(ASSET_MEDIA / "scene_01_antwerp_district.jpg"),
    str(ASSET_MEDIA / "scene_02_vault_door.jpg"),
    str(ASSET_MEDIA / "scene_03_security_sensors.jpg"),
    str(ASSET_MEDIA / "scene_04_mastermind_planner.jpg"),
    str(ASSET_MEDIA / "scene_05_escape_route.jpg"),
    str(ASSET_MEDIA / "scene_06_police_evidence.jpg"),
]


def request_json(endpoint: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    title = "Antwerp Diamond Heist: The Numbers Behind the Vault"
    config = {
        "title": title,
        "name": "Antwerp_Diamond_Heist_Director_Final",
        "content_title": title,
        "output_name": "Antwerp_Diamond_Heist_Director_Final",
        "output_dir": str(OUTPUT_DIR),
        "topic": "2003 Antwerp Diamond Heist",
        "topic_anchor": "Antwerp Diamond Center heist 2003",
        "category": "crime",
        "semantic_theme": "investigative_crime",
        "script": SCRIPT,
        "type": "long",
        "scene_mode": "Every Sentence",
        "image_source": "Custom Images",
        "images": CUSTOM_IMAGES,
        "media_mode": "manual",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "fps": 30,
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_render_required": True,
        "semantic_fast_render": False,
        "semantic_render_budget_seconds": 900,
        "semantic_render_concurrency": 4,
        "editorial_llm_fallback": False,
        "semantic_variation_seed": 20260322,
        "render_backend": "ffmpeg",
        "graphic_density": 0.85,
        "sound_density": 0.6,
        "captions_enabled": False,
        "animate_all_media": True,
        "max_still_hold_seconds": 3.0,
        "max_visual_beat_seconds": 3.0,
        "visual_verification_frame_count": 12,
        "visual_deadline_seconds": 420,
        "visual_shared_deadline_seconds": 420,
        "allow_emergency_media": False,
    }

    status = request_json("/api/queue/status")
    print(f"Queue check: {len(status.get('items') or [])} item(s), worker={status.get('worker_running')}")
    response = request_json("/api/queue/add", config)
    item = response.get("item") or {}
    job_id = str(item.get("id") or "")
    if not job_id:
        raise RuntimeError(f"Queue did not return a job id: {response}")
    print(f"JOB_ID={job_id}", flush=True)

    start = time.monotonic()
    last = None
    while True:
        item_response = request_json(f"/api/queue/item/{job_id}")
        current = item_response.get("item") or {}
        state = str(current.get("status") or "unknown")
        progress = current.get("progress", 0)
        message = str(current.get("progress_text") or current.get("message") or "")
        snapshot = (state, progress, message)
        if snapshot != last:
            print(f"[{time.monotonic() - start:6.1f}s] {state.upper():10} {progress:>3}% {message}", flush=True)
            last = snapshot
        if state == "completed":
            result = current.get("result") or {}
            print("RESULT=" + json.dumps(result, sort_keys=True), flush=True)
            return 0
        if state in {"failed", "cancelled"}:
            raise RuntimeError(str(current.get("error") or current.get("message") or state))
        time.sleep(2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
