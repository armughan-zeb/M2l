"""Build a reference-inspired media-pipeline showcase for Remotion.

This is intentionally a real-media test: every semantic graphic is placed
over a downloaded documentary clip so the compositor and visibility rules are
exercised together instead of previewing isolated cards.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REFERENCE_VIDEO = ROOT / "output" / "research" / "facebook_documentary" / "kunwar_nabeel_documentary.mp4"
OUT = ROOT / "output" / "reference_media_showcase.manifest.json"


def scene(index: int, narration: str, primary: dict, safe_region: list[float], subject_bbox: list[float]) -> dict:
    return {
        "id": f"reference-media-{index + 1}",
        "scene_index": index,
        "start": index * 3.2,
        "duration": 3.2,
        "narration": narration,
        "media": {
            "src": str(REFERENCE_VIDEO),
            "type": "video",
            "source_start": index * 8.0,
            "fit": "cover",
            "position": "center",
            "safe_framing": False,
            "subject_bbox": subject_bbox,
            "visual_decision": {
                "subject_bbox": subject_bbox,
                "safe_text_regions": [safe_region],
                "status": "accepted",
            },
        },
        "primary_visual": primary,
        "camera": {"motion": "static" if index % 2 else "slow_push"},
        "design": {"image_treatment": "hold"},
        "transition": {"template_id": "cut", "duration": 0.18},
        "atmosphere": {"layers": [], "intensity": 0.0},
        "sound_cues": [],
    }


def main() -> None:
    if not REFERENCE_VIDEO.exists():
        raise SystemExit(f"Reference video is missing: {REFERENCE_VIDEO}")
    manifest = {
        "version": 1,
        "kind": "reference-media-showcase",
        "fps": 30,
        "width": 1280,
        "height": 720,
        "duration": 19.2,
        "duration_in_frames": 576,
        "topic": "Nokia documentary — media-led graphic grammar proof",
        "category": "documentary",
        "qa_showcase": True,
        "visual_dna": {
            "palette": {
                "background": "#0a0d12",
                "surface": "#141a22",
                "primary": "#f7f8f5",
                "secondary": "#bbc3ce",
                "accent": "#d7c45b",
                "accent2": "#7fd1c4",
                "border": "#526171",
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
            scene(0, "On the 11th of February 2011, employees at Nokia's headquarters opened their morning email.", {"type": "ChapterTitle", "variant": "chapter-slate", "props": {"title": "THE BURNING PLATFORM", "kicker": "CHAPTER 01", "date": "ESPOO, FINLAND · FEBRUARY 11, 2011"}}, [0.06, 0.07, 0.52, 0.28], [0.54, 0.16, 0.96, 0.88]),
            scene(1, "Stephen Elop compared the company to a man standing on a burning oil platform.", {"type": "QuoteCard", "variant": "source-quote", "props": {"quote": "We are standing on a burning platform.", "attribution": "STEPHEN ELOP · INTERNAL MEMO"}}, [0.05, 0.64, 0.53, 0.93], [0.5, 0.16, 0.95, 0.85]),
            scene(2, "Within hours, the memo had leaked to the technology press and Nokia's stock had dropped over fourteen percent.", {"type": "StatCard", "variant": "editorial-stat", "props": {"value": "-14%", "label": "HELSINKI EXCHANGE", "unit": "WITHIN HOURS"}}, [0.63, 0.08, 0.96, 0.31], [0.04, 0.18, 0.58, 0.87]),
            scene(3, "The company had sold more mobile phones than any company on earth for fourteen straight years.", {"type": "Collage", "variant": "archive-contact-sheet", "props": {"label": "NOKIA ARCHIVE", "items": [{"label": "2110 · 1994"}, {"label": "Communicator"}, {"label": "Symbian"}, {"label": "40% MARKET"}]}}, [0.05, 0.69, 0.95, 0.94], [0.12, 0.12, 0.88, 0.68]),
            scene(4, "The new chief executive's memo changed the story of Nokia overnight.", {"type": "PersonProfile", "variant": "portrait-id", "props": {"name": "STEPHEN ELOP", "role": "CHIEF EXECUTIVE", "detail": "THE BURNING PLATFORM MEMO"}}, [0.05, 0.09, 0.46, 0.33], [0.52, 0.14, 0.94, 0.86]),
            scene(5, "The story began two decades earlier in Finland, inside a sprawling industrial company.", {"type": "AnimatedMap", "variant": "map-locator", "props": {"from": "ESPOO, FINLAND", "to": "GLOBAL MARKET", "label": "INDUSTRIAL BEGINNINGS"}}, [0.06, 0.69, 0.52, 0.94], [0.5, 0.12, 0.94, 0.84]),
        ],
    }
    # Run the same planning path used by production manifests so this proof
    # exercises media placement, grammar diversity, and the swarm audit.
    from server.services.editorial_director import annotate_media_compositions, manager_review, run_documentary_swarm

    manifest = run_documentary_swarm(annotate_media_compositions(manifest))
    manifest["quality_control"] = manager_review(manifest)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
