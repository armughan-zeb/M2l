"""Generate and verify a five-minute production-path speed test."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

TITLE = "Coral Reefs: Cities Beneath the Sea"
SCRIPT = """Beneath warm, shallow water, coral reefs build some of the most crowded cities on Earth. They are not single animals and they are not simple rocks. A reef is a living partnership between tiny coral animals, microscopic algae, fish, invertebrates, currents, and the chemistry of the surrounding sea. Together, these relationships create a structure that can grow for centuries and shelter thousands of species.

The story begins with a polyp, a small animal related to sea anemones. It attaches itself to a hard surface and produces a cup of calcium carbonate around its body. When a polyp divides, the new animals remain connected. Generation after generation, their limestone skeletons accumulate into branches, plates, domes, and walls. A reef is therefore a community and a geological record at the same time.

Sunlight powers much of this construction. Inside many reef-building corals live microscopic algae called zooxanthellae. The algae use light to make energy, and they share part of that energy with their coral hosts. The coral provides shelter and access to nutrients, while the algae help the colony build its skeleton. This partnership explains why the richest reefs usually grow in clear water where sunlight can reach the seafloor.

A healthy reef has layers of life. Parrotfish scrape algae from rock with beak-like teeth, and their digestion helps turn coral fragments into fine sand. Sea turtles move between feeding grounds and nesting beaches. Groupers wait among ledges, while schools of small fish turn together when a predator approaches. Sponges filter water, crabs patrol crevices, and tiny worms recycle material that would otherwise collect on the reef floor.

Reefs also connect distant habitats. A young fish may begin life in a mangrove forest, move into a seagrass meadow, and later spend its adult years among corals. Tidal channels carry nutrients between these places. When one habitat disappears, the effect can travel through the entire coastal system. Protecting a reef therefore means protecting the shoreline, the nursery grounds, and the open water around it.

For people, reefs are both a source of food and a natural breakwater. Their rough surfaces absorb wave energy before it reaches the coast. Communities depend on reef fisheries, tourism, small businesses, and the knowledge passed down by divers and boat crews. In many island regions, the health of the reef is closely tied to the safety and income of families living nearby.

The modern threat is not a single event. Warmer water can push corals to release their algae, a process called bleaching. Bleached coral is stressed but not automatically dead; if temperatures fall, some colonies can recover. Repeated heat waves, however, leave less time for recovery. Pollution, sediment, destructive fishing, disease, and careless anchoring add pressure to the same animals.

Ocean acidification creates another challenge. As the sea absorbs extra carbon dioxide from the atmosphere, its chemistry changes and carbonate becomes harder for corals to use. Young colonies may grow more slowly, and older skeletons can become weaker. The effect is gradual, but it reaches the basic material from which a reef is built.

The future of coral reefs depends on choices made on land as well as at sea. Cleaner water, responsible fishing, protected areas, careful tourism, and lower greenhouse gas emissions all matter. No single action can carry the entire burden, but many practical actions can give reefs more time and space to adapt.

A reef may look distant when viewed from a boat, yet its story is connected to every river, city, farm, and atmosphere above it. It is a living archive of cooperation, competition, and change. When we protect the conditions that let coral build, we protect a shelter for wildlife, a defense for coastlines, and a source of wonder for generations that have not yet seen the cities beneath the sea. Its future will be shaped by patient observation, practical choices, and the decision to value living systems before they are gone."""

OUTPUT_DIR = ROOT / "output" / "five_minute_speed_test"


def progress(fraction: float, message: str) -> None:
    print(f"  [{float(fraction) * 100:5.1f}%] {message}", flush=True)


def main() -> int:
    from app import _run_video_pipeline

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "script": SCRIPT,
        "title": TITLE,
        "name": TITLE,
        "image_source": "Mixed Media",
        "type": "long",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "render_resolution": (1920, 1080),
        "output_dir": str(OUTPUT_DIR),
        # Intentionally omitted: semantic_fast_render. The renderer must
        # choose its duration-based long-form profile automatically.
    }
    started = time.monotonic()
    print("=" * 88)
    print(f"FIVE-MINUTE SPEED TEST: {TITLE}")
    print(f"Narration words: {len(SCRIPT.split())}")
    print("=" * 88, flush=True)
    try:
        result = _run_video_pipeline(config, progress_callback=progress)
        elapsed = round(time.monotonic() - started, 1)
        video_path = Path(str((result or {}).get("video_path") or ""))
        manifest_path = Path(str((result or {}).get("manifest_path") or ""))
        report = {
            "title": TITLE,
            "narration_words": len(SCRIPT.split()),
            "elapsed_seconds": elapsed,
            "success": bool(isinstance(result, dict) and result.get("success")),
            "video_path": str(video_path),
            "manifest_path": str(manifest_path),
        }
        if video_path.is_file():
            report["size_bytes"] = video_path.stat().st_size
            report["sha256"] = hashlib.sha256(video_path.read_bytes()).hexdigest()
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration:stream=codec_name,width,height", "-of", "json",
                 str(video_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                report["ffprobe"] = json.loads(probe.stdout or "{}")
            except ValueError:
                report["ffprobe"] = {"raw": probe.stdout}
        report_path = OUTPUT_DIR / "speed_test_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("=" * 88)
        print(f"SUCCESS: {report['success']}")
        print(f"Elapsed: {elapsed}s")
        print(f"Video: {video_path}")
        print(f"Manifest: {manifest_path}")
        print(f"Report: {report_path}")
        print("=" * 88, flush=True)
        return 0 if report["success"] else 2
    except Exception as exc:
        print(f"FIVE-MINUTE SPEED TEST FAILED after {time.monotonic() - started:.1f}s: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
