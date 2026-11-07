"""Run a reproducible ~400-word Vidzeo documentary export.

This uses the production pipeline and the semantic edit manifest generated from
the final audio/Whisper scene plan. It is intentionally a Web Images job so the
test exercises the container-anchored web resolver instead of synthetic media.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SCRIPT = """RMS Titanic was designed as a floating city, a ship whose scale was meant to make the Atlantic feel smaller. In April 1912, she left Southampton carrying more than passengers. She carried confidence in engineering, class, and the promise that modern technology could tame the sea.

The vessel stretched nearly nine hundred feet, with grand staircases, electric lights, restaurants, and separate spaces for first, second, and third class. Every detail made the same argument: this was not simply a liner. It was a moving symbol of the industrial age, built for a world that believed progress was accelerating faster than danger.

On the night of April 14, Titanic moved through a dark, unusually calm North Atlantic. Radio operators received warnings about drifting ice, but the messages competed with private traffic and passenger telegrams. Lookouts watched from the crow's nest while the ship held its course. The sea offered no waves around an iceberg, no white water to announce the obstacle.

At about 11:40 p.m., the lookout spotted a shape ahead. The warning came quickly. The bridge ordered the engines to reverse and the ship to turn, but a vessel this large could not change direction instantly. Titanic passed close to the iceberg, yet the contact opened a series of narrow breaches below the waterline.

Inside, the first signs were confusing: a tremor, a shudder, and then water entering compartments that were expected to remain sealed. Captain Edward Smith and the designers realized the damage crossed too many watertight sections. The ship could survive flooding in some compartments, but not this pattern. The emergency became a problem of time, communication, and limited lifeboat capacity.

Passengers gathered on deck as officers began launching the boats. Some boats left with empty seats because the danger was difficult to believe. Wireless operators sent distress calls, and the nearby Carpathia changed course toward the signal. In the freezing darkness, families waited for instructions while the ship's band continued to play.

Before dawn on April 15, Titanic broke apart and sank. More than fifteen hundred people died. The disaster changed maritime law, strengthened ice patrols, and exposed the human cost of confidence without preparation. Titanic remains powerful not because it was simply large or famous, but because its story connects a real ship, real decisions, and a night when modern certainty met the limits of the sea."""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root / "server") not in sys.path:
        sys.path.insert(0, str(root / "server"))

    # Import after the repository root is on sys.path so this is the same code
    # path used by the Flask production application.
    import app as vidzeo_app

    words = len(SCRIPT.split())
    print(f"[400-test] script_words={words}", flush=True)
    config = {
        "type": "long",
        "name": "semantic_400_rms_titanic",
        "title": "RMS Titanic: The Night the Signal Changed",
        "script": SCRIPT,
        "image_source": "Web Images",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "tts_model": "audiogram",
        "voice": "Adam (Male US)",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "quality": "standard",
        "resolution": "1080p",
        "visual_understanding": False,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True,
        "semantic_theme": "history_archive",
        "semantic_variation_seed": 4002026,
    }

    def progress(fraction: float, message: str):
        print(f"[400-test] progress={fraction:.3f} {message}", flush=True)

    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)
    output = root / "output" / "semantic_400_rms_titanic_manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    manifest = result.get("semantic_edit_manifest") or {}
    scenes = manifest.get("scenes") or []
    print(f"[400-test] output={result.get('video_path') or result.get('output_path')}", flush=True)
    print(f"[400-test] scenes={len(scenes)} duration={manifest.get('duration_in_frames')} frames", flush=True)
    print(f"[400-test] manifest={output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
