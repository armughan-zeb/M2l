"""Generate and monitor a researched royal documentary through the live queue."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "artifacts" / "royal_family_reliability_20261002"
BASE = "http://127.0.0.1:5000"
SCRIPT = """The British royal family projects an image of timeless tradition. But behind the crowns, the institution keeps changing.

In 1953, Elizabeth the Second was crowned at Westminster Abbey. Television brought the ceremony into homes, turning ancient ritual into a shared national moment.

Her reign lasted seventy years, the longest in British history. When she died in 2022, Charles became King. His coronation followed in May 2023.

The crown, the robes, and the procession made the change look medieval. The constitutional reality is modern.

At the State Opening of Parliament, the monarch reads the government's legislative agenda. Parliament passes laws; royal assent completes the formal process.

Beyond ceremony, members of the royal family support charities and represent Britain at public events. The public sees balcony appearances, but much of their role is everyday service.

Through new monarchs, new media, and changing expectations, the family keeps adapting the performance of duty. The symbols survive because their meaning keeps evolving."""

SOURCES = [
    {"id": "royal:1953", "title": "Queen Elizabeth II's Accession and Coronation", "publisher": "The Royal Household", "source_kind": "primary", "url": "https://www.royal.uk/queen-elizabeth-iis-accession-and-coronation"},
    {"id": "royal:reign", "title": "Queen Elizabeth II's Life and Reign", "publisher": "The Royal Household", "source_kind": "primary", "url": "https://www.royal.uk/queen-elizabeth-iis-life-and-reign"},
    {"id": "royal:2023", "title": "Coronation Day", "publisher": "The Royal Household", "source_kind": "primary", "url": "https://www.royal.uk/coronation-guide"},
    {"id": "parliament:crown", "title": "Parliament and Crown", "publisher": "UK Parliament", "source_kind": "primary", "url": "https://www.parliament.uk/about/how/role/relations-with-other-institutions/parliament-crown/"},
]


def main() -> int:
    REVIEW.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    status = session.get(f"{BASE}/api/queue/status?compact=1", timeout=10)
    status.raise_for_status()
    resume_job = sys.argv[1] if len(sys.argv) > 1 else ""
    if status.json().get("active_jobs") and not resume_job:
        raise RuntimeError("Another job is active; the royal test must wait before submitting.")
    payload = {
        "title": "The Royal Family: Tradition That Keeps Changing",
        "content_title": "The Royal Family: Tradition That Keeps Changing",
        "output_name": "royal_family_one_minute",
        "script": SCRIPT,
        "topic_anchor": "British royal family",
        "_topic_anchor": "British royal family",
        "type": "long", "image_source": "Mixed Media", "media_mode": "mixed",
        "scene_mode": "Every 2 Beats", "aspect_ratio": "16:9",
        "resolution": "1080p", "quality": "high",
        "vo_mode": "Use OpenRouter TTS", "tts_model": "deepgram/flux-tts:free",
        "voice": "flux-bruce-en", "whisper_mode": "local",
        "captions_enabled": True, "caption_mode": "phrase", "words_per_group": 4,
        "visual_understanding": True, "visual_required": True,
        "strict_retrieval_accuracy": True, "fail_on_unresolved_media": True,
        "allow_emergency_media": False, "render_backend": "remotion",
        "semantic_edit_enable": True, "semantic_render_enabled": True,
        "semantic_render_scale": 1.0, "semantic_render_crf": 18,
        "semantic_render_concurrency": 2,
        "research_sources": SOURCES, "research_fetch_sources": True,
        "research_llm_review": True, "research_budget_seconds": 30,
        "output_dir": str(REVIEW),
    }
    (REVIEW / "narration.txt").write_text(SCRIPT, encoding="utf-8")
    (REVIEW / "request.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if resume_job:
        job_id = resume_job
    else:
        response = session.post(f"{BASE}/api/queue/add", json=payload, timeout=30)
        response.raise_for_status()
        submitted = response.json()
        if not submitted.get("success"):
            raise RuntimeError(str(submitted.get("error") or submitted))
        job_id = submitted["item"]["id"]
    (REVIEW / "job_id.txt").write_text(job_id, encoding="utf-8")
    print(f"Submitted {job_id}: {len(SCRIPT.split())} narration words, live Mixed Media pipeline", flush=True)
    started = time.monotonic()
    last_seq = 0
    while time.monotonic() - started < 1950:
        try:
            response = session.get(f"{BASE}/api/queue/item/{job_id}", timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"Monitor reconnecting to {job_id}: {type(exc).__name__}", flush=True)
            time.sleep(2)
            continue
        item = response.json()["item"]
        for event in item.get("progress_events") or []:
            seq = int(event.get("seq") or 0)
            if seq > last_seq:
                print(f"[{time.monotonic()-started:.0f}s] {event.get('progress')}% {event.get('text')}", flush=True)
                last_seq = seq
        state = {key: item.get(key) for key in (
            "id", "status", "progress", "progress_text", "paused_reason", "error",
            "error_detail", "checkpoint_dir", "result", "retryable",
        )}
        state["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (REVIEW / "run_state.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        if item.get("status") in {"completed", "paused", "error", "cancelled", "shutdown_timeout"}:
            print(f"Final status: {item['status']}; saved {REVIEW / 'run_state.json'}", flush=True)
            return 0 if item["status"] == "completed" else 1
        time.sleep(2)
    raise TimeoutError("The queue did not publish a terminal state within its delivery deadline.")


if __name__ == "__main__":
    raise SystemExit(main())
