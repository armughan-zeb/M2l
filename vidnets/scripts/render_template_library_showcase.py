"""Render the newer documentary treatments as a compact visual regression reel.

This is intentionally a local, source-media-only harness. It catches invalid
JSX/template registrations without rerunning retrieval, TTS, or any paid vision
operation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "server"))
    from services.native_render_policy import procedural_sound_library, render_semantic_documentary
    from services.semantic_edit_engine import _graphic_props, build_semantic_edit_manifest, stable_seed

    showcase = [
        ("field-caption", "The North Atlantic was quiet that night.", {"role": "location", "emphasis": "North Atlantic"}),
        ("spotlight-lens", "One small detail changed the outcome.", {"role": "evidence", "emphasis": "one small detail"}),
        ("exhibit-label", "Titanic became an archive object as well as a ship.", {"role": "context", "emphasis": "RMS Titanic"}),
        ("witness-quote", 'A witness wrote, "The sea was calm."', {"role": "quote", "attribution": "WITNESS ACCOUNT"}),
        ("calendar-rip", "On April 15, 1912, the story changed.", {"role": "evidence", "treatment": "date", "emphasis": "April 15, 1912"}),
        ("terminal-log", "The wireless signal arrived too late.", {"role": "evidence", "treatment": "signal", "emphasis": "signal received"}),
        ("metric-grid", "More than 1500 people died that night.", {"role": "evidence", "treatment": "counter", "emphasis": "1500 people"}),
        ("route-progress", "The route crossed the North Atlantic.", {"role": "location", "emphasis": "North Atlantic"}),
        ("index-card", "The warning appeared in the archive log.", {"role": "evidence", "treatment": "document", "emphasis": "warning log"}),
        ("stamp-mark", "The archive record was verified.", {"role": "evidence", "emphasis": "verified"}),
        ("word-cascade", "Too fast. Too late. No second chance.", {"role": "climax", "emphasis": "Too fast too late"}),
        ("case-file", "The missing warning became the central case file.", {"role": "evidence", "treatment": "document", "emphasis": "missing warning"}),
    ]
    media_root = root / "temp" / "semantic_400_remotion_media"
    media = [{"path": str(media_root / f"scene_{index + 1:03d}.jpg"), "type": "image"} for index in range(len(showcase))]
    if not all(Path(item["path"]).is_file() for item in media):
        raise FileNotFoundError("Saved scene images are required before running the showcase.")
    texts = [item[1] for item in showcase]
    beats = [item[2] for item in showcase]
    manifest = build_semantic_edit_manifest(
        texts, [2.1] * len(texts), media=media, editorial_beats=beats,
        topic="RMS Titanic", category="history",
        config={"fps": 30, "width": 1280, "height": 720, "theme_id": "history_archive", "sound_library": procedural_sound_library()},
        variation_seed=20260729,
    )
    for index, (template_id, text, beat) in enumerate(showcase):
        scene = manifest["scenes"][index]
        scene["graphic"]["template_id"] = template_id
        scene["graphic"]["family"] = "showcase"
        scene["graphic"]["props"] = {**_graphic_props(template_id, text, scene["semantic_beat"], "RMS Titanic"), "variant": index % 6}
        scene["graphic"]["variant"] = index % 6
        scene["graphic"]["seed"] = stable_seed(template_id, index, "showcase")
    output = root / "temp" / "documentary_template_library_showcase.mp4"
    result = render_semantic_documentary(
        manifest, output,
        {"name": "documentary_template_library_showcase", "semantic_render_quality": "draft", "semantic_render_timeout_seconds": 300, "semantic_render_concurrency": 3},
    )
    (root / "temp" / "documentary_template_library_showcase.json").write_text(
        json.dumps({"templates": [item[0] for item in showcase], "result": result}, indent=2), encoding="utf-8"
    )
    print(result["output_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
