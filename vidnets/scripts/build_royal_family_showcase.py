"""Build a Royal Family documentary proof using curated local media."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MEDIA_ROOT = ROOT / "output" / "king_charles_news_2min"
FAMILY_IMAGE = ROOT / "temp" / "codex_validated_royal_20260917_v17" / "web_images_simple_f3caaf2b_f3caaf2b-run-0001-15ff6fe40c1d" / "shared_pool" / "img_f06cc04ef32b_ec5944.jpg"
OUT = ROOT / "output" / "royal_family_documentary_showcase.manifest.json"


def image_scene(index: int, image: Path, narration: str, primary: dict, safe_region: list[float], subject_bbox: list[float], text_tone: str = "light") -> dict:
    return {
        "id": f"royal-family-{index + 1}",
        "scene_index": index,
        "start": index * 3.4,
        "duration": 3.4,
        "narration": narration,
        "media": {
            "src": str(image),
            "type": "image",
            "fit": "cover",
            "position": "center",
            "safe_framing": False,
            "text_tone": text_tone,
            "subject_bbox": subject_bbox,
            "visual_decision": {
                "subject_bbox": subject_bbox,
                "safe_text_regions": [safe_region],
                "text_tone": text_tone,
                "status": "accepted",
            },
        },
        "primary_visual": primary,
        "camera": {"motion": ["slow_push", "drift_left", "static", "pan_right"][index % 4]},
        "design": {"image_treatment": "hold"},
        "transition": {"template_id": "cut", "duration": 0.2},
        "atmosphere": {"layers": [], "intensity": 0.0},
        "sound_cues": [],
    }


def main() -> None:
    assets = {
        "family": FAMILY_IMAGE,
        "charles_bahamas": MEDIA_ROOT / "bermuda_royal.jpg",
        "commonwealth": MEDIA_ROOT / "commonwealth_games.jpg",
        "glasgow_stage": MEDIA_ROOT / "glasgow_royal_1.jpg",
        "glasgow_walk": MEDIA_ROOT / "glasgow_royal_2.jpg",
        "guyana_meeting": MEDIA_ROOT / "guyana_meeting.jpg",
        "guyana_portrait": MEDIA_ROOT / "guyana_charles.jpg",
    }
    missing = [str(path) for path in assets.values() if not path.exists()]
    if missing:
        raise SystemExit(f"Missing curated royal media: {missing}")

    manifest = {
        "version": 1,
        "kind": "royal-family-documentary-showcase",
        "fps": 30,
        "width": 1280,
        "height": 720,
        "duration": 23.8,
        "duration_in_frames": 714,
        "topic": "The Royal Family — ceremony, duty, and continuity",
        "category": "documentary",
        "qa_showcase": True,
        "visual_dna": {
            "palette": {
                "background": "#0b0b10",
                "surface": "#17151b",
                "primary": "#f5f0e7",
                "secondary": "#c7bdc0",
                "accent": "#d8b35b",
                "accent2": "#8f2033",
                "border": "#5e4b51",
            },
            "typography": {
                "display": "Sora, Inter, sans-serif",
                "body": "DM Sans, Inter, sans-serif",
                "numbers": "IBM Plex Mono, monospace",
                "editorial": "Source Serif 4, Georgia, serif",
            },
            "render_mode": "advanced-doc-v1",
        },
        "scenes": [
            image_scene(0, assets["family"], "The royal family is private life transformed into public ritual.", {"type": "ChapterTitle", "variant": "chapter-slate", "props": {"title": "THE CROWN IN PUBLIC", "kicker": "ROYAL FAMILY · 01", "date": "CEREMONY / DUTY / CONTINUITY"}}, [0.06, 0.03, 0.42, 0.17], [0.02, 0.18, 0.98, 0.84]),
            image_scene(1, assets["glasgow_stage"], "Ceremony gives the institution a visual language audiences recognise instantly.", {"type": "QuoteCard", "variant": "editorial-quote", "props": {"quote": "Ceremony is the public face of continuity.", "attribution": "EDITORIAL VOICE"}}, [0.05, 0.68, 0.45, 0.93], [0.20, 0.16, 0.74, 0.90]),
            image_scene(2, assets["commonwealth"], "Every appearance carries more than a family story; it carries a network of relationships.", {"type": "Collage", "variant": "archive-contact-sheet", "props": {"label": "THE PUBLIC ROLE", "items": [{"label": "CEREMONY"}, {"label": "DUTY"}, {"label": "DIPLOMACY"}, {"label": "CONTINUITY"}]}}, [0.05, 0.04, 0.45, 0.18], [0.02, 0.18, 0.98, 0.88]),
            image_scene(3, assets["charles_bahamas"], "At the centre, the crown is carried by individuals—but read as an institution.", {"type": "PersonProfile", "variant": "portrait-id", "props": {"name": "KING CHARLES III", "role": "THE CROWN IN MOTION", "detail": "PUBLIC DUTY / PERSONAL PRESENCE"}}, [0.05, 0.07, 0.48, 0.28], [0.25, 0.16, 0.72, 0.92], "dark"),
            image_scene(4, assets["guyana_meeting"], "From palace rooms to Commonwealth stages, the role is diplomatic as much as ceremonial.", {"type": "AnimatedMap", "variant": "map-locator", "props": {"from": "LONDON", "to": "GEORGETOWN, GUYANA", "label": "A NETWORK OF DUTY"}}, [0.05, 0.68, 0.55, 0.94], [0.38, 0.10, 0.82, 0.90], "dark"),
            image_scene(5, assets["guyana_portrait"], "Tradition survives by changing its surface while keeping its symbols legible.", {"type": "QuoteCard", "variant": "editorial-quote-2", "props": {"quote": "Tradition survives by changing its surface.", "attribution": "EDITORIAL VOICE"}}, [0.05, 0.06, 0.48, 0.29], [0.24, 0.12, 0.78, 0.92], "dark"),
            image_scene(6, assets["glasgow_walk"], "The crown remains a story told in public—one appearance, one image, one generation at a time.", {"type": "TextCard", "variant": "field-caption", "props": {"title": "THE STORY CONTINUES", "subtitle": "THE ROYAL FAMILY / A PUBLIC INSTITUTION"}}, [0.06, 0.06, 0.48, 0.26], [0.22, 0.10, 0.78, 0.92]),
        ],
    }

    from server.services.editorial_director import annotate_media_compositions, manager_review, run_documentary_swarm

    manifest = run_documentary_swarm(annotate_media_compositions(manifest))
    manifest["quality_control"] = manager_review(manifest)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
