"""Build a short visual QA reel for locator maps and centered data charts."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MEDIA_ROOT = ROOT / "output" / "king_charles_news_2min"
OUT = ROOT / "output" / "map_chart_media_pipeline_showcase.manifest.json"


def scene(index: int, image: Path, narration: str, primary: dict, safe_region: list[float], tone: str = "light") -> dict:
    return {
        "id": f"map-chart-qa-{index + 1}",
        "scene_index": index,
        "start": index * 3.2,
        "duration": 3.2,
        "narration": narration,
        "media": {
            "src": str(image),
            "type": "image",
            "fit": "cover",
            "position": "center",
            "safe_framing": False,
            "text_tone": tone,
            "visual_decision": {
                "subject_bbox": [0.4, 0.1, 0.82, 0.9],
                "safe_text_regions": [safe_region],
                "text_tone": tone,
                "status": "accepted",
            },
        },
        "primary_visual": primary,
        "camera": {"motion": "static"},
        "design": {"image_treatment": "hold"},
        "transition": {"template_id": "cut", "duration": 0.2},
        "atmosphere": {"layers": [], "intensity": 0.0},
        "sound_cues": [],
    }


def main() -> None:
    image_a = MEDIA_ROOT / "guyana_meeting.jpg"
    image_b = MEDIA_ROOT / "commonwealth_games.jpg"
    image_c = MEDIA_ROOT / "glasgow_royal_1.jpg"
    missing = [str(path) for path in (image_a, image_b, image_c) if not path.exists()]
    if missing:
        raise SystemExit(f"Missing QA media: {missing}")

    manifest = {
        "version": 1,
        "kind": "map-chart-media-pipeline-showcase",
        "fps": 30,
        "width": 1280,
        "height": 720,
        "duration": 12.8,
        "duration_in_frames": 384,
        "topic": "Map and chart placement QA",
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
            scene(0, image_a, "The route connects London to Georgetown.", {"type": "AnimatedMap", "variant": "map-locator", "props": {"from": "LONDON", "to": "GEORGETOWN, GUYANA", "label": "ROUTE / VERIFIED LOCATIONS"}}, [0.04, 0.68, 0.34, 0.93], "dark"),
            scene(1, image_b, "The verified series rises across three points.", {"type": "Chart", "variant": "trend-line", "props": {"title": "VERIFIED SERIES", "subtitle": "SCRIPT-GROUNDED VALUES", "data": [{"label": "Q1", "value": 12}, {"label": "Q2", "value": 18}, {"label": "Q3", "value": 27}]}}, [0.18, 0.18, 0.82, 0.82]),
            scene(2, image_c, "The source image carries a breath before the comparison.", {"type": "SmartImage", "props": {}}, [0.04, 0.06, 0.34, 0.22]),
            scene(3, image_c, "The same evidence can be read as a comparison of periods.", {"type": "Chart", "variant": "editorial-bar", "props": {"title": "PERIOD COMPARISON", "chart_type": "bar", "subtitle": "SCRIPT-GROUNDED VALUES", "data": [{"label": "BEFORE", "value": 8}, {"label": "DURING", "value": 17}, {"label": "AFTER", "value": 13}]}}, [0.18, 0.18, 0.82, 0.82]),
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
