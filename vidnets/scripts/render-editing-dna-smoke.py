"""Render a short deterministic QA reel for the editing-DNA libraries."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.services.editing_profile import deterministic_profile
from server.services.native_render_policy import procedural_sound_library, render_semantic_documentary
from server.services.semantic_edit_engine import build_semantic_edit_manifest


def main() -> int:
    media_root = ROOT / "public" / "remotion-manifest-media"
    images = sorted(media_root.glob("scene_*.jpg"))[:9]
    if len(images) < 9:
        raise FileNotFoundError("The QA harness requires nine staged scene images.")
    texts = [
        "In April 1912, the world's largest passenger liner began its first voyage.",
        "Its route crossed the North Atlantic toward New York.",
        "More than seventy three percent of one crucial group faced the same risk.",
        "The number rose from thirty two to seventy six in a matter of hours.",
        "Wireless operators received a warning, but the record arrived too late.",
        "Notice the signal written near the center of the surviving log.",
        "The decision led directly to a consequence nobody could reverse.",
        "Archive testimony later described the sea as perfectly calm.",
        "The evidence changed how the disaster would be remembered.",
    ]
    beats = [
        {"role": "chapter", "treatment": "date", "emphasis": "APRIL 1912", "importance": .94},
        {"role": "location", "treatment": "map", "emphasis": "NORTH ATLANTIC", "importance": .78},
        {"role": "evidence", "treatment": "statistic", "emphasis": "73 PERCENT", "importance": .86},
        {"role": "comparison", "treatment": "chart", "emphasis": "THE RAPID CHANGE", "importance": .84},
        {"role": "evidence", "treatment": "signal", "emphasis": "WARNING RECEIVED", "energy": "tense", "importance": .92},
        {"role": "evidence", "emphasis": "THE SIGNAL", "importance": .8},
        {"role": "turn", "treatment": "relationship", "emphasis": "THE DECISION", "energy": "urgent", "importance": .94},
        {"role": "quote", "treatment": "quote", "emphasis": "THE SEA WAS CALM", "importance": .82},
        {"role": "climax", "emphasis": "THE EVIDENCE CHANGED HISTORY", "importance": .96},
    ]
    media = [{"path": str(path), "type": "image"} for path in images]
    media[5]["visual_decision"] = {
        "subject_bbox": [.52, .25, .73, .48],
        "safe_text_regions": [[.05, .1, .35, .4]],
        "status": "unverified",
        "confidence": 0,
    }
    profile = deterministic_profile("RMS Titanic historical investigation", "history", " ".join(texts), variation_seed=2907)
    manifest = build_semantic_edit_manifest(
        texts,
        [2.8] * len(texts),
        media=media,
        editorial_beats=beats,
        topic="RMS Titanic historical investigation",
        category="history",
        config={
            "fps": 30,
            "width": 1280,
            "height": 720,
            "variation_seed": 2907,
            "editing_profile": profile,
            "sound_library": procedural_sound_library(),
            "render_quality": "draft",
        },
    )
    output = ROOT / "temp" / "editing-dna-smoke.mp4"
    result = render_semantic_documentary(
        manifest,
        output,
        {
            "name": "editing-dna-smoke",
            "semantic_render_quality": "draft",
            "semantic_render_scale": .5,
            # Low concurrency avoids Chromium's Page.bringToFront/Target closed
            # worker crash on Windows during longer Remotion QA renders.
            "semantic_render_concurrency": 1,
            "semantic_render_timeout_seconds": 900,
        },
    )
    print(result["output_path"])
    print(manifest["metrics"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
