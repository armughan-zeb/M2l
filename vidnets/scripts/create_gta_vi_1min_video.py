"""Submit a 1-minute GTA VI documentary job to the local Vidnets server.

Modelled on the known-good Wright Brothers 1-min job (9606ea41): same
1080p / Mixed Media / external-agent config, new title + script. The
pipeline sources its own media (web + YouTube); no custom images.
Server is on :5010 (not :5000 — the old royal-family script is stale).
Run: python3 vidnets/scripts/create_gta_vi_1min_video.py
Prints the job id and exits; poll /api/queue/item/<id> for progress.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = "http://127.0.0.1:5010"
RUNTIME_ROOT = Path("/home/hatch/workspace/vidnets-runtime")
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUTPUT_DIR = RUNTIME_ROOT / "output" / f"GTA_VI_1Min_{STAMP}"

GTA_SCRIPT = """Grand Theft Auto Six is the most anticipated video game of the decade, and Rockstar Games has kept fans waiting for more than ten years since Grand Theft Auto Five, a game that has sold more than two hundred million copies.

The game returns to Vice City, now set in the fictional state of Leonida, a sun-soaked reimagining of Florida filled with beaches, swamps, and neon streets.

The trailers show the Leonida Keys, the swamps of the Grassrivers, and the neon of Vice City itself, a world far bigger and more detailed than anything Rockstar has built before.

At the center of the story are Lucia Caminos and Jason Duval, two partners in crime whose story begins with Lucia walking out of prison and reuniting with Jason.

The first trailer arrived in December 2023 and broke viewing records within a day, while a second trailer in May 2025 showed more of Leonida and its characters.

Rockstar first promised the game for autumn 2025, then pushed it to May 2026, and later delayed it again to November 19, 2026, saying the extra time is needed to finish it to their standard.

When it arrives, it launches first on PlayStation 5 and Xbox Series X and S, with a PC version yet to be announced.

For Rockstar, Grand Theft Auto Six is not just a sequel, it is the next chapter of the biggest entertainment series in the world."""


def post_json(endpoint, payload):
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    # Local pro mode: the server requires the paired local agent key for
    # external_agent mode (Muse inference is compulsory in code).
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer local-bypass",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    job_payload = {
        "title": "Grand Theft Auto VI: Return to Vice City",
        "content_title": "Grand Theft Auto VI: Return to Vice City",
        "name": "GTA_VI_1Min",
        "output_name": "GTA_VI_1Min",
        "topic": "Grand Theft Auto VI GTA 6 Rockstar Games Vice City Leonida Lucia Jason",
        "topic_anchor": "Grand Theft Auto VI GTA 6 Rockstar Games Vice City Leonida Lucia Jason",
        "script": GTA_SCRIPT,
        "type": "long",
        "duration": "1min",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "render_resolution": [1920, 1080],
        "quality": "standard",
        "scene_mode": "Every Beat",
        "media_mode": "automatic",
        "image_source": "Mixed Media",
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_required": False,
        "strict_retrieval_accuracy": True,
        "allow_emergency_media": True,
        "allow_grounded_graphic_fallback": True,
        "allow_grounded_graphic_fallback_strict": True,
        "allow_relevant_stock": True,
        "allow_stock_last_resort": True,
        "cross_source_fallback": True,
        "global_media_guarantee": True,
        "fail_on_unresolved_media": True,
        "media_fallback_policy": "grounded_graphic",
        "media_provider_retries": 2,
        "max_final_video_reuse_per_clip": 1,
        "enable_editorial_cues": True,
        "enable_motion_templates": True,
        "enable_sound_library": True,
        "format_hint": "auto",
        "language": "auto",
        "cost_profile": "efficient",
        "composition_model": "external-agent",
        "director_model": "external-agent",
        "llm_model": "external-agent",
        "query_llm_model": "external-agent",
        "research_model": "external-agent",
        "scene_brain_model": "external-agent",
        "qwen_vision_model": "external-agent",
        "llm_provider": "external_agent",
        "composition_fallback_models": ["external-agent"],
        "director_fallback_models": ["external-agent"],
        "scene_brain_fallback_models": ["external-agent"],
        "qwen_vision_fallback_models": ["external-agent"],
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
    print(f"Submitting GTA VI 1-min job to {BASE_URL}/api/queue/add ...")
    code, resp = post_json("/api/queue/add", job_payload)
    print(f"HTTP {code}: {json.dumps(resp)[:600]}")
    if code != 200 or not resp.get("success"):
        print("ERROR: submit failed")
        return 1
    item = resp.get("item") or {}
    print(f"JOB_ID={item.get('id')}")
    print(f"worker running={resp.get('worker_running')} started={resp.get('worker_started')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
