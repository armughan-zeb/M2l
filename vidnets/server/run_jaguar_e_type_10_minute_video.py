"""Produce a full-length, identity-locked Jaguar E-Type documentary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import traceback

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
sys.path.insert(0, str(SERVER_DIR))

OUTPUT_DIR = ROOT / "output" / "productions" / "jaguar_e_type_10_minute"
TITLE = "Jaguar E-Type: The Car That Rewrote the Sports Car"
OUTPUT_NAME = "Jaguar E-Type: The Car That Rewrote the Sports Car"

SCRIPT = """In March 1961, a new sports car appeared at the Geneva Motor Show and instantly changed the conversation around performance cars. It was long, low, fast, and unmistakably elegant. Its name was the Jaguar E-Type. This film is about the historic automobile itself: the 1960s machine, the people who designed it, the engineering beneath its beautiful body, and the reasons it still commands attention more than six decades later.

The E-Type did not arrive from nowhere. Its story began with Jaguar's earlier XK sports cars, especially the XK120, which had already shown that a British manufacturer could combine speed with long-distance comfort. Jaguar's competition department then developed the D-Type, an aerodynamic racing car that won the 24 Hours of Le Mans. The E-Type carried that racing knowledge into a road car that ordinary drivers could actually buy.

At the center of the project was chief engineer William Heynes, supported by a team that included aerodynamicist Malcolm Sayer. Sayer had studied aircraft design, and that background shaped the E-Type's flowing surfaces. The car was not styled as a collection of decorations added to a mechanical package. Its shape came from the relationship between airflow, structure, suspension, cooling, and the practical space needed for a driver and passenger.

The proportions were immediately distinctive. A very long bonnet stretched ahead of a compact cabin, while the rear of the car tapered into a rounded tail. The headlights sat beneath clear covers on early cars, the grille was small, and the front fenders seemed to flow continuously into the doors. Even at rest, the E-Type looked as if it had been designed to move through air. Its visual identity was specific: a classic Jaguar sports car from the early 1960s, not a generic modern coupe.

Under that bonnet was Jaguar's proven XK straight-six engine. The first road cars used a 3.8-litre version with twin overhead camshafts and three SU carburetors. Published performance figures varied with testing conditions, but the E-Type was widely understood to be a genuine 150-mile-per-hour sports car. That speed mattered, but so did the way the engine delivered it. The straight-six was smooth enough for touring, yet strong enough to make the car feel urgent whenever the road opened ahead.

The chassis was just as important as the engine. The E-Type used independent suspension at the front and rear, an unusual level of sophistication for a road car in its price range. Its rear suspension was packaged in a compact unit with the differential, driveshafts, brakes, and springs. The brakes were mounted inboard at the rear, reducing unsprung weight and helping the suspension respond to changes in the road. The result was a car that could be quick in a straight line and composed through corners.

The structure also revealed the influence of Jaguar's racing experience. The front section used a strong space-frame arrangement, while the passenger compartment and rear structure formed a carefully integrated body. The famous bonnet opened forward as one large piece, exposing an engine bay that looked almost as dramatic as the exterior. That design made maintenance easier and turned ordinary mechanical work into a visual reminder of the car's engineering character.

When the E-Type was unveiled in Geneva, Jaguar had prepared two cars for the event. Public interest was so intense that one additional roadster was driven overnight from Coventry to Switzerland so journalists could experience it. That story has become part of the car's mythology, but the reaction was not based on mythology alone. Reporters saw a machine with the appearance of an exotic, the performance of a serious grand tourer, and a price that undercut many established rivals.

Inside, the E-Type was more purposeful than luxurious. The early cockpit placed round instruments, toggle switches, and a thin-rimmed steering wheel directly in front of the driver. Leather, metal, and dark trim created an environment that felt mechanical rather than electronic. The driver had to understand the relationship between the clutch, the gearbox, the steering, and the engine. That physical connection is one reason the car feels so different from a modern performance vehicle.

Its connection to competition continued through lightweight and low-drag racing versions. Jaguar produced a small number of lighter E-Types with aluminum bodywork, and private teams developed cars for racing circuits on both sides of the Atlantic. The E-Type did not simply repeat the D-Type's Le Mans record, but the relationship between road and track remained visible. Racing pushed aerodynamic development, braking, cooling, and weight reduction, while the road cars carried that sense of purpose into everyday life.

The biggest mechanical change arrived with the Series 3 in 1971. Jaguar installed a 5.3-litre V12, giving the E-Type a new character. The long bonnet now made room for twelve cylinders, and the car became a more relaxed, powerful grand tourer. It was offered primarily as a long-wheelbase roadster and fixed-head coupe, and its larger engine reflected a different moment in automotive history. The original lightweight sports-car idea had grown into a prestigious high-speed touring car.

The E-Type also changed the expectations placed on British sports cars. It demonstrated that a car could be technically ambitious without looking clinical, and glamorous without being mechanically empty. Its bonnet created a visual sense of occasion, but the independent suspension and disc brakes gave that appearance a functional foundation. The car's beauty was not separate from its engineering. The beauty was one of the ways the engineering became visible.

By the 1980s and 1990s, many E-Types were no longer expensive new cars. Some had been neglected, modified, or used as inexpensive transportation. Others survived because owners understood that the underlying design was worth protecting. Restoration culture gradually turned the E-Type into a moving archive. Specialists began documenting chassis numbers, production changes, original colors, trim patterns, engine details, and the differences between factory specifications and later repairs.

That restoration work raises an important question: what does authenticity mean for a historic car? One owner may value an exact factory restoration, while another may prefer a mechanically improved car that can be driven regularly. Some cars retain their original engines and body panels. Others carry sympathetic upgrades, stronger cooling systems, improved brakes, or modern electrical components. The answer is not simply whether a car looks shiny. Authenticity is a relationship between evidence, history, condition, and the honest description of what has been changed.

That is why period footage matters. A modern promotional image can show a beautifully restored car, but archival road footage reveals how the E-Type actually occupied the world: its narrow tires on old pavement, its chrome catching changing light, its bonnet rising ahead of the driver, and its body moving through corners. A workshop image can explain the engine or suspension. A cockpit view can show the instruments. Different visual evidence answers different parts of the story.

The E-Type's influence can be seen in later Jaguar sports cars, in the styling of grand tourers, and in the continuing demand for cars that communicate speed without relying on aggressive graphics. Modern sports cars are usually faster, safer, quieter, and more precise. They can also be easier to use every day. Yet the E-Type still feels immediate because so much of its character is exposed. You see the bodywork, hear the engine, feel the steering, and understand the limits.

More than sixty years after Geneva, the 1961 Jaguar E-Type remains a landmark of classic car design. Its significance is not that it was flawless, or that every surviving example is identical. Its significance is that it united aerodynamic form, serious chassis engineering, long-distance performance, and an unforgettable visual language in one automobile. The E-Type made the sports car feel like both a machine and an event.

When an original E-Type appears on the road today, the reaction is rarely limited to its age. People recognize the silhouette before they recognize the year. They notice the nose, the glass, the wire wheels, the cockpit, and the long sweep of the body. That recognition is the result of coherent design, not a keyword or a logo. The car has remained memorable because every major element supports the same idea.

The most honest way to understand the E-Type is therefore to look closely. Look at the straight-six and the V12. Look at the inboard brakes and independent rear suspension. Look at the early cockpit, the changing bumpers, the Series 2 details, and the long-wheelbase Series 3. Look at restored cars, unrestored cars, workshop evidence, and period road presence. Together, those details explain why this historic Jaguar is still more than a beautiful shape.

The E-Type did not invent the sports car, and it did not end the story of the sports car. What it did was bring several difficult ideas together at exactly the right moment. It made advanced engineering look desirable, made speed look elegant, and made a British automobile relevant to audiences around the world. That combination is why the Jaguar E-Type remains one of the defining cars of the 1960s—and why its story still deserves to be told with the machine, not an unrelated image, at the center."""


def _progress(percent: float, message: str) -> None:
    print(f"  [{float(percent) * 100:5.1f}%] {message}", flush=True)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("VIDZEO_WEB_FAST_MODE", "1")
    os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
    os.environ.setdefault("VIDZEO_WEB_VERBOSE", "1")
    os.environ.setdefault("VIDZEO_YT_REJECT_TALKING_HEADS", "1")
    os.environ.setdefault("VIDZEO_YT_BLUR_SMALL_LOGOS", "1")
    os.environ.setdefault("VIDZEO_WEB_ENABLE_GDL", "1")
    os.environ.setdefault("VIDZEO_WEB_GDL_RANGE", "20")
    os.environ.setdefault("VIDZEO_YT_POOL_SOURCE_SHORTLIST", "10")
    os.environ.setdefault("VIDZEO_YT_POOL_SOURCE_RESERVES", "3")
    os.environ.setdefault("VIDZEO_YT_POOL_TOTAL_SOURCES", "12")
    os.environ.setdefault("VIDZEO_VISUAL_FINALIST_LIMIT", "8")
    os.environ.setdefault("VIDZEO_MIXED_YT_TARGET_RATIO", "0.50")
    os.environ.setdefault("VIDZEO_YT_FRAME_FALLBACK_SOURCES", "3")
    os.environ.setdefault("VIDZEO_YT_FRAME_FALLBACK_FRAMES", "5")

    config = {
        "script": SCRIPT,
        "title": TITLE,
        "content_title": TITLE,
        "name": OUTPUT_NAME,
        "output_name": OUTPUT_NAME,
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "mixed_media_weights": {
            "Web Images": 0.50,
            "YouTube Clips": 0.50,
            "Stock Media": 0.0,
        },
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_required": True,
        "allow_emergency_media": False,
        "allow_relevant_stock": False,
        "type": "long",
        "scene_mode": "Every 5 Beats",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "render_resolution": (1280, 720),
        "output_dir": str(OUTPUT_DIR),
        "visual_deadline_seconds": 1800,
        "visual_shared_deadline_seconds": 1800,
        "media_deadline_seconds": 900,
        "yt_branch_wall_timeout_seconds": 900,
        # The native one-pass recovery is intentionally only the last safety
        # net. Keep its real 85s reserve instead of inheriting the stale 600s
        # environment reserve, so long-form semantic motion gets enough wall
        # time to finish and is not silently downgraded at the end.
        "render_reserve_seconds": 180,
        "straight_render_budget_seconds": 85,
        # Keep the native semantic renderer's multi-phase still motion and
        # editorial timing, but use the ultrafast encode profile for this
        # 33-scene long-form export so it completes before the shared wall.
        "semantic_fast_render": True,
    }

    started = time.monotonic()
    print("=" * 88)
    print("JAGUAR E-TYPE 10-MINUTE DOCUMENTARY PRODUCTION")
    print(f"Title: {TITLE}")
    print(f"Narration words: {len(SCRIPT.split())}")
    print("Expected subject: 1960s Jaguar E-Type automobile, not wildlife or unrelated modern cars")
    print("=" * 88, flush=True)

    try:
        from app import _run_video_pipeline

        result = _run_video_pipeline(config, progress_callback=_progress)
        elapsed = round(time.monotonic() - started, 1)
        report = {
            "title": TITLE,
            "narration_words": len(SCRIPT.split()),
            "elapsed_seconds": elapsed,
            "success": bool(isinstance(result, dict) and result.get("success")),
            "video_path": str((result or {}).get("video_path") or "") if isinstance(result, dict) else "",
            "manifest_path": str((result or {}).get("manifest_path") or "") if isinstance(result, dict) else "",
        }
        report_path = OUTPUT_DIR / "production_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("\n" + "=" * 88)
        print(f"Pipeline success: {report['success']}")
        print(f"Elapsed: {elapsed}s")
        print(f"Video: {report['video_path']}")
        print(f"Manifest: {report['manifest_path']}")
        print(f"Report: {report_path}")
        print("=" * 88, flush=True)
        return 0 if report["success"] else 2
    except Exception as exc:
        print(f"\nJAGUAR 10-MINUTE PRODUCTION FAILED after {time.monotonic() - started:.1f}s: {exc}", flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
