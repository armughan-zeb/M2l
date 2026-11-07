"""Render a short, fully semantic Documentary Motion Engine smoke reel."""

from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "server"))
    from services.native_render_policy import procedural_sound_library, render_semantic_documentary
    from services.semantic_edit_engine import build_semantic_edit_manifest

    media_root = root / "temp" / "semantic_400_remotion_media"
    media = [{"path": str(media_root / f"scene_{index:03d}.jpg"), "type": "image"} for index in range(1, 7)]
    voice = root / "temp" / "edge_tts_full_84b0bce8b0f2.mp3"
    texts = [
        "RMS Titanic was designed as a floating city, a ship whose scale was meant to make the Atlantic feel smaller.",
        "In April 1912, Titanic left Southampton carrying the confidence of the industrial age.",
        "Captain Edward Smith stood at the center of a ship built to symbolize modern certainty.",
        "Radio operators received warnings about drifting ice, but those messages competed with private traffic.",
        "The route crossed a dark, unusually calm North Atlantic where an iceberg was hard to see.",
        "More than fifteen hundred people died after the collision opened too many compartments below the waterline.",
    ]
    beats = [
        {"role": "intro", "importance": .92, "emphasis": "RMS Titanic"},
        {"role": "evidence", "treatment": "date", "importance": .78, "emphasis": "April 1912"},
        {"role": "person", "importance": .8, "emphasis": "Edward Smith", "subtitle": "CAPTAIN"},
        {"role": "evidence", "treatment": "document", "importance": .85, "emphasis": "ice warnings"},
        {"role": "location", "importance": .73, "emphasis": "North Atlantic"},
        {"role": "evidence", "treatment": "counter", "importance": .94, "emphasis": "1,500 people"},
    ]
    manifest = build_semantic_edit_manifest(
        texts, [3.2] * len(texts), media=media, editorial_beats=beats,
        topic="RMS Titanic", category="history",
        config={
            "width": 1920, "height": 1080, "fps": 30, "theme_id": "history_archive",
            "render_quality": "draft", "sound_library": procedural_sound_library(),
            "voiceover": {"path": str(voice), "src": str(voice), "rights_status": "user_generated"},
        },
    )
    result = render_semantic_documentary(
        manifest, root / "temp" / "motion_engine_smoke.mp4",
        {"name": "motion_engine_smoke", "semantic_render_quality": "draft", "semantic_render_timeout_seconds": 240},
    )
    print(result["output_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
