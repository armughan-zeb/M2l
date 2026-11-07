"""Build a representative script-driven hybrid manifest for Remotion QA."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from server.services.semantic_edit_engine import build_semantic_edit_manifest

    texts = [
        "On April 15, 1912, the ship left Southampton with a promise of safety.",
        "The route connected Southampton to New York across the North Atlantic.",
        "More than 1,500 people were aboard when the voyage began.",
        "Radio operators received two warnings about drifting ice.",
        "A decision delayed the warning until it was too late.",
        "A witness said the sea was calm during the final minute.",
        "The archive revealed the missing message in 1985.",
        "The comparison was stark: confidence before the collision, panic after it.",
        "Number one: the collision changed how ships were designed.",
        "The final lesson is that scale is not the same as safety.",
    ]
    beats = [
        {"role": "evidence", "treatment": "date", "importance": .9},
        {"role": "location", "treatment": "map", "importance": .8},
        {"role": "evidence", "treatment": "counter", "importance": .95},
        {"role": "evidence", "treatment": "signal", "importance": .8},
        {"role": "comparison", "treatment": "decision", "importance": .85},
        {"role": "quote", "treatment": "quote", "importance": .7},
        {"role": "evidence", "treatment": "document", "importance": .8},
        {"role": "comparison", "treatment": "comparison", "importance": .8},
        {"role": "chapter", "treatment": "listicle", "importance": .9},
        {"role": "climax", "treatment": "highlight", "importance": .95},
    ]
    media = [
        {"path": str(root / "verification_output" / f"scene{index % 4}.jpg"), "type": "image"}
        for index in range(len(texts))
    ]
    manifest = build_semantic_edit_manifest(
        texts,
        [3.0] * len(texts),
        media=media,
        editorial_beats=beats,
        topic="Titanic history",
        category="history",
        variation_seed=2026,
        config={
            "width": 1280,
            "height": 720,
            "fps": 30,
            "graphic_density": .9,
            "render_quality": "draft",
            "captions_enabled": True,
        },
    )
    output = root / "output" / "hybrid_motion_validation.manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    metrics = manifest["metrics"]
    print(json.dumps({
        "manifest": str(output),
        "duration": manifest["duration"],
        "scene_count": metrics["scene_count"],
        "templates": metrics["template_counts"],
        "image_treatments": metrics["image_treatment_counts"],
        "motion_families": metrics["motion_family_counts"],
        "transition_families": metrics["transition_family_counts"],
        "variation_audit": metrics["variation_audit"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
