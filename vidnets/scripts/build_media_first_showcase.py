"""Build a small real-media proof for the media-first Remotion compositor."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMAGE = ROOT / "output" / "harness_logs" / "jaguar_e_type_relevance" / "qa_frames_latest" / "scene_0_youtube.jpg"
VIDEO = ROOT / "output" / "productions" / "gta_vi_1min_showcase" / "video_clips" / "scene_0_jason_drive.mp4"
OUT = ROOT / "output" / "media_first_showcase.manifest.json"


def media(path: Path, kind: str, subject_bbox, safe_text_regions):
    return {
        "src": str(path),
        "type": kind,
        "fit": "cover",
        "position": "center",
        "safe_framing": False,
        "subject_bbox": subject_bbox,
        "visual_decision": {
            "subject_bbox": subject_bbox,
            "safe_text_regions": safe_text_regions,
            "status": "accepted",
        },
    }


def scene(index, start, narration, asset, primary, camera, treatment):
    return {
        "id": f"media-first-{index + 1}",
        "scene_index": index,
        "start": start,
        "duration": 3.5,
        "narration": narration,
        "media": asset,
        "primary_visual": primary,
        "camera": {"motion": camera},
        "design": {"image_treatment": treatment},
        "transition": {"template_id": "cut", "duration": 0.18},
        "atmosphere": {"layers": [], "intensity": 0.0},
        "sound_cues": [],
    }


def main() -> None:
    manifest = {
        "version": 1,
        "kind": "media-first-showcase",
        "fps": 30,
        "width": 1280,
        "height": 720,
        "duration": 14.0,
        "duration_in_frames": 420,
        "topic": "Media-first documentary integration",
        "category": "documentary",
        "visual_dna": {
            "palette": {
                "background": "#10141b",
                "surface": "#1b222c",
                "primary": "#f8fafc",
                "secondary": "#b7c0cc",
                "accent": "#f4c542",
                "accent2": "#f07a54",
                "border": "#526171",
            },
            "typography": {"display": "Archivo Black", "body": "Inter", "numbers": "JetBrains Mono"},
            "render_mode": "advanced-doc-v1",
        },
        "scenes": [
            scene(
                0,
                0.0,
                "The machine is introduced through the source image, not a title card.",
                media(IMAGE, "image", [0.02, 0.08, 0.92, 0.94], [[0.05, 0.05, 0.42, 0.22]]),
                {"type": "TextCard", "variant": "field-caption", "props": {"title": "THE MACHINE", "subtitle": "FIELD IMAGE", "text": "THE MACHINE"}},
                "slow_push",
                "hold",
            ),
            scene(
                1,
                3.5,
                "The road sequence carries the motion while the statistic stays in the corner.",
                media(VIDEO, "video", [0.08, 0.22, 0.68, 0.88], [[0.70, 0.08, 0.96, 0.28]]),
                {"type": "StatCard", "variant": "hero_counter_large", "props": {"value": "07 SEC", "label": "SOURCE CLIP", "unit": "CONTINUOUS MOTION"}},
                "static",
                "hold",
            ),
            scene(
                2,
                7.0,
                "A signal annotation points to the evidence without hiding the shot.",
                media(IMAGE, "image", [0.20, 0.18, 0.92, 0.85], [[0.04, 0.70, 0.44, 0.94]]),
                {"type": "Diagram", "variant": "code_console", "props": {"message": "SOURCE DETAIL VERIFIED", "label": "EVIDENCE"}},
                "drift_left",
                "editorial_crop",
            ),
            scene(
                3,
                10.5,
                "The final locator clarifies the route while the clip remains readable.",
                media(VIDEO, "video", [0.28, 0.10, 0.96, 0.82], [[0.04, 0.06, 0.38, 0.25]]),
                {"type": "AnimatedMap", "variant": "route_flight_arc", "props": {"from": "GARAGE", "to": "ROAD", "label": "ROUTE"}},
                "pan_right",
                "hold",
            ),
        ],
        "media_first_rules": {
            "hero_layer": "source_media",
            "minimum_media_visibility": 0.78,
            "maximum_overlay_area": 0.28,
            "maximum_overlay_opacity": 0.88,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()

