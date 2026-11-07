"""Submit and monitor a real five-minute Mixed Media job through Flask."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:5000"

SCRIPT = """
The story of the British royal family is a story of duty, change, and life under an extraordinary public spotlight. For generations, the Crown has connected ceremony with the everyday work of representing the United Kingdom and the Commonwealth. Behind every balcony appearance and state occasion is a network of people, places, and decisions that continue to shape the modern monarchy.

King Charles III was born into that public role and spent decades preparing for it. His early years were shaped by constitutional tradition, military service, and an interest in architecture, conservation, and the environment. As Prince of Wales, he supported charities and communities across Britain while learning how the institution around him worked. His accession after the death of Queen Elizabeth II marked a new chapter for the royal household.

Queen Elizabeth II served for more than seventy years and became the longest reigning monarch in British history. Her reign included the transformation from an imperial age to a modern Commonwealth, the arrival of television and digital media, and enormous social change. She travelled widely, met generations of leaders, and maintained a steady public rhythm that made the Crown a familiar part of national life.

The royal residences tell another part of the story. Buckingham Palace is the best known working headquarters, while Windsor Castle links the family to centuries of English history. Balmoral in Scotland became a private retreat, and Sandringham remains associated with family gatherings. These locations are also working places where staff prepare ceremonies, receive visitors, protect archives, and keep public schedules moving.

Prince William, the Prince of Wales, represents the next generation of the institution. His work has focused on homelessness, mental health, conservation, and the future of the planet. Catherine, Princess of Wales, has made early childhood and family wellbeing central themes in her public work. Their appearances show how a traditional role can be presented through contemporary campaigns, community visits, and direct conversations.

Prince Harry grew up inside the same intense attention but chose a different path as an adult. His service in the British Army, work with veterans, and support for the Invictus Games became important parts of his public identity. His marriage to Meghan Markle brought a new international audience to the family and also exposed the pressures that can accompany royal life, media scrutiny, and expectations about public duty.

Harry and Meghan stepped back from being senior working royals and began building an independent life in California. They continued projects through the Archewell Foundation and spoke about mental health, service, and the challenges of public attention. Their decision created difficult conversations about family, privacy, constitutional roles, and the boundaries between personal choice and institutional responsibility.

The younger generation brings a more personal image of the monarchy. Prince George, Princess Charlotte, and Prince Louis are seen at carefully chosen public events, while their parents try to give them an ordinary childhood away from cameras. Prince Archie and Princess Lilibet, living in California with Harry and Meghan, are part of the same wider family story even though they appear in public far less often.

Royal ceremonies remain some of the most carefully produced events in the world. Coronations, jubilees, weddings, and remembrance services combine music, uniforms, architecture, and symbolism. Every procession has a route, every invitation has a constitutional meaning, and every image is studied by audiences across the globe. At the same time, the people involved still experience ordinary emotions: pride, grief, disagreement, and affection.

The monarchy also faces questions about its future. Supporters see continuity, service, and a useful connection between communities and the state. Critics question cost, privilege, inherited status, and the institution's place in a modern democracy. The royal household has responded with smaller ceremonies, environmental commitments, and a greater emphasis on practical charitable work, while public opinion continues to change from one generation to the next.

The British royal family therefore cannot be understood through a single photograph or one famous ceremony. It is a living institution made up of public duty and private relationships, historic buildings and current campaigns, inherited expectations and individual choices. From King Charles III to the youngest children, its future will depend on how convincingly it can serve people while adapting to a rapidly changing country and world.
""".strip()


def main() -> int:
    payload = {
        "title": "The British Royal Family: Duty, Change and the Future",
        "content_title": "The British Royal Family: Duty, Change and the Future",
        "output_name": "British Royal Family 5 Minute Production Test",
        "script": SCRIPT,
        "type": "long",
        "image_source": "Mixed Media",
        "media_weights": {"web_images": 75, "youtube_clips": 20, "stock_media": 5},
        "captions_enabled": True,
        "caption_style": "hormozi",
        "words_per_group": 3,
        "bold": True,
        "uppercase": True,
        "semantic_theme": "editorial",
        "resolution": "1920x1080",
    }
    started = time.monotonic()
    response = requests.post(f"{BASE_URL}/api/queue/add", json=payload, timeout=20)
    print(f"submit status={response.status_code}", flush=True)
    if response.status_code != 200:
        print(response.text, flush=True)
        return 1
    item = response.json().get("item") or {}
    job_id = item.get("id")
    print(f"job_id={job_id}", flush=True)
    last = None
    while True:
        status = requests.get(f"{BASE_URL}/api/queue/status", timeout=15).json()
        current = next((row for row in status.get("items", []) if row.get("id") == job_id), None)
        if current is None:
            print("job disappeared from queue", flush=True)
            return 1
        state = current.get("status")
        progress = current.get("progress", 0)
        text = current.get("progress_text", "")
        marker = (state, progress, text)
        if marker != last:
            print(f"[{time.monotonic() - started:7.1f}s] {state} {progress}% {text}", flush=True)
            last = marker
        if state == "completed":
            result = current.get("result") or {}
            print(json.dumps({
                "success": True,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "video_path": result.get("video_path") or result.get("video"),
                "manifest_path": result.get("manifest_path"),
            }, indent=2), flush=True)
            return 0
        if state in {"error", "cancelled"}:
            print(json.dumps({
                "success": False,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "error": current.get("error"),
                "detail": current.get("error_detail"),
            }, indent=2), flush=True)
            return 1
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
