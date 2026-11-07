"""Generate and verify a five-minute car-niche production-path speed test."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

TITLE = "Toyota Supra A80: The Car That Refused to Disappear"
SCRIPT = """The Toyota Supra A80 arrived in the early nineteen nineties with a simple promise: become a serious grand touring car without losing the spirit of a sports machine. Its long bonnet, short rear deck, and wide stance made it look fast before the engine even started. Decades later, the fourth generation Supra remains one of the most recognizable Japanese performance cars ever built, and its story explains why engineering discipline can outlast fashion.

The A80 was developed during a period when Japanese manufacturers were competing at the highest level. Toyota wanted a car that could travel comfortably across a country, yet respond immediately when a driver found an empty mountain road. The design team lowered the center of gravity, widened the track, and used a double wishbone suspension at both ends. The result was a car that felt planted, predictable, and composed when other powerful coupes could become nervous.

Under the hood, the best known version used the 2JZ GTE, a three liter, twin turbocharged, straight six. The engine was not famous only because of its headline power. Its cast iron block, strong crankshaft, and carefully supported internals gave it a reputation for surviving serious tuning. Toyota engineered the original system with sequential turbochargers, so the first turbo helped at lower speeds and the second joined later. That arrangement balanced response with strong top end acceleration.

The published factory figures were conservative in several markets. Enthusiasts soon discovered that the engine had headroom when it received better fuel delivery, cooling, and engine management. Tuning culture turned the Supra into a rolling laboratory. Some owners pursued a modest increase for reliable road use, while others built competition cars with strengthened transmissions, wider tires, and data logging. The important point was that upgrades worked because the foundation was coherent.

Power was only half of the A80 character. The cabin was wrapped around the driver, with a dashboard that curved toward the instruments and a tall center console separating the seats. Visibility over the long nose required practice, but the seating position gave the driver a clear sense of the car's rotation. On a long trip, the Supra could settle into a quiet rhythm. In a corner, the same structure communicated grip through the steering wheel and seat.

Toyota also paid attention to weight distribution and braking. Aluminum components were used where they made a useful difference, and the available braking hardware was sized for repeated high speed stops. The rear wing was not just decoration; it formed part of an aerodynamic package that helped keep the car stable. Later models received updates to suspension tuning, wheels, and interior equipment, but the central character remained a balanced front engine sports coupe.

The car was not perfect. It was expensive when new, the rear seats were small, and a heavy option list could push its mass upward. Twin turbo plumbing and aging rubber hoses make neglected examples costly to restore. Automatic versions can be excellent cruisers, but drivers seeking maximum involvement usually look for a healthy manual gearbox. Rust, accident repairs, overheating, and incomplete modifications matter more than a low number on the odometer.

That buying lesson is part of the Supra legacy. A documented, mostly original car with a clean cooling system is often a better starting point than a dramatic project with unknown wiring. A compression test, leak inspection, chassis check, and careful look at the turbo system can reveal more than a polished engine bay. The best examples are not museum pieces; they are cars maintained well enough to be driven and understood.

Today, the fourth generation Supra is valuable because it connects several eras. It represents a time when manufacturers invested heavily in mechanical hardware, but it also fits the modern world of digital tuning and global enthusiast communities. Restorers can protect original details, while engineers can measure every change with modern tools. The car rewards both approaches when the work respects its balance.

The lasting lesson is not that every Supra should be modified for maximum power. It is that a great performance car gives its owner room to learn. The A80 combines a durable engine, a communicative chassis, useful highway comfort, and a design that still looks purposeful. That is why prices rose, why replicas keep appearing, and why a well maintained original can still make an ordinary drive feel like an event. The Supra refused to disappear because its engineering left people something worth returning to."""

OUTPUT_DIR = ROOT / "output" / "five_minute_car_speed_test"


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
        # Omitted deliberately: the renderer must select the duration-aware
        # fast profile automatically for this long-form production job.
    }
    started = time.monotonic()
    print("=" * 88)
    print(f"FIVE-MINUTE CAR SPEED TEST: {TITLE}")
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
        print(f"FIVE-MINUTE CAR SPEED TEST FAILED after {time.monotonic() - started:.1f}s: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
