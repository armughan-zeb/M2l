import os
import sys
import time
import json

# Ensure server root is in sys.path
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SERVER_DIR)

from services.web_image.resolver import resolve_web_images_for_video_fast
import app as app_module

TITANIC_SCRIPT = """On April 10, 1912, the RMS Titanic, the largest and most luxurious ocean liner ever built, embarked on its maiden voyage from Southampton, England, bound for New York City. Hailed as an unsinkable marvel of modern industrial engineering, the massive vessel carried over 2,200 passengers and crew members, including wealthy aristocrats, prominent businessmen, and hundreds of hopeful immigrants seeking new lives in America.

Constructed in Belfast by the Harland and Wolff shipyard for the White Star Line, Titanic measured 882 feet in length and featured state-of-the-art watertight bulkheads, opulent dining saloons, a grand staircase, and a heated swimming pool. Captain Edward John Smith commanded the magnificent ship across the chilly waters of the North Atlantic.

However, four days into the voyage, on the quiet, moonless night of April 14, 1912, at 11:40 PM, lookouts Frederick Fleet and Reginald Lee spotted a massive iceberg looming directly in the ship's path. Despite First Officer William Murdoch's desperate order to turn hard-a-starboard and reverse engines, Titanic struck the iceberg along its starboard side, buckling hull plates below the waterline and opening six compartments to the freezing ocean.

As water flooded the forward compartments, chief naval architect Thomas Andrews inspected the damage and calculated that the ship had less than two hours before sinking completely into the Atlantic. Distress signals, including the newly adopted SOS, were transmitted via Marconi wireless telegraphy to nearby ships, while distress rockets were launched into the night sky.

The ship carried only 20 lifeboats, enough for barely half of the people onboard. Under the rule of women and children first, officers loaded lifeboats into the freezing sea. The ocean liner Carpathia, commanded by Captain Arthur Rostron, heard the distress calls and altered course at full speed through dangerous ice fields to reach the scene.

At 2:20 AM on April 15, 1912, Titanic broke in two and sank to the ocean floor, resulting in the tragic loss of over 1,500 lives. Carpathia arrived at dawn, rescuing 705 shivering survivors from the lifeboats. The disaster stunned the global public, sparking sweeping international maritime safety regulations, mandating 24-hour radio watches and lifeboats for all passengers. Decades later, in 1985, Dr. Robert Ballard discovered the shipwreck resting 12,500 feet below the surface, preserving its tragic legacy forever."""

# Split into 18 distinct scene narration lines
TITANIC_SCENES = [
    {"subject": "RMS Titanic Southampton", "text": "On April 10, 1912, the RMS Titanic embarked on its maiden voyage from Southampton bound for New York."},
    {"subject": "RMS Titanic Passenger Ship", "text": "Hailed as an unsinkable marvel of engineering, the vessel carried over 2,200 passengers and crew members."},
    {"subject": "Titanic Immigrants Passengers", "text": "Wealthy aristocrats and hundreds of hopeful immigrants seeking new lives in America boarded the ship."},
    {"subject": "Harland and Wolff Shipyard Belfast", "text": "Titanic was constructed in Belfast by the Harland and Wolff shipyard for the White Star Line."},
    {"subject": "Titanic Grand Staircase Interior", "text": "The ship measured 882 feet in length and featured opulent dining saloons and a grand staircase."},
    {"subject": "Captain Edward Smith Titanic", "text": "Captain Edward John Smith commanded the magnificent liner across the chilly North Atlantic."},
    {"subject": "Titanic Iceberg North Atlantic", "text": "On April 14, 1912, lookouts spotted a massive iceberg looming directly in the ship's path."},
    {"subject": "First Officer William Murdoch Titanic", "text": "First Officer William Murdoch ordered to turn hard-a-starboard and reverse engines immediately."},
    {"subject": "Titanic Iceberg Collision", "text": "Titanic struck the iceberg along its starboard side, opening six forward compartments to freezing ocean water."},
    {"subject": "Thomas Andrews Naval Architect Titanic", "text": "Chief naval architect Thomas Andrews calculated that the ship had less than two hours before sinking."},
    {"subject": "Marconi Wireless Distress Signal SOS", "text": "Distress signals, including SOS, were transmitted via Marconi wireless telegraphy to nearby vessels."},
    {"subject": "Titanic Distress Flare Rocket", "text": "White distress rockets were launched high into the quiet night sky to signal for immediate help."},
    {"subject": "Titanic Lifeboats Lowering", "text": "The vessel carried only 20 lifeboats, enough for barely half of the people onboard."},
    {"subject": "RMS Carpathia Rescue Ship", "text": "The ocean liner Carpathia, commanded by Captain Arthur Rostron, raced at full speed through ice fields."},
    {"subject": "Titanic Sinking Night Atlantic", "text": "At 2:20 AM, Titanic broke in two and sank to the ocean floor, claiming over 1,500 lives."},
    {"subject": "Carpathia Titanic Survivors Lifeboat", "text": "Carpathia arrived at dawn, rescuing 705 shivering survivors from the wooden lifeboats."},
    {"subject": "Titanic Maritime Safety Regulations", "text": "The disaster led to sweeping international maritime safety regulations and 24-hour radio watches."},
    {"subject": "Robert Ballard Titanic Shipwreck Discovery", "text": "In 1985, Dr. Robert Ballard discovered the shipwreck resting 12,500 feet below the surface."}
]

def run_titanic_production_harness():
    print("=" * 80)
    print("STARTING 400+ WORD TITANIC PRODUCTION HARNESS (FULL WEB IMAGE RESOLVER PIPELINE)")
    print("=" * 80)

    word_count = len(TITANIC_SCRIPT.split())
    print(f"Script Total Word Count: {word_count} words")
    print(f"Total Scenes: {len(TITANIC_SCENES)} scenes")
    
    title = "The Sinking of RMS Titanic: Unsinkable Legend"
    scene_texts = [s["text"] for s in TITANIC_SCENES]
    durations = [5.0] * len(TITANIC_SCENES)
    
    scene_overrides = []
    for s in TITANIC_SCENES:
        scene_overrides.append({
            "section_subject": s["subject"],
            "scene_subject": s["subject"],
            "subject_query": s["subject"],
            "queries": [f"{s['subject']} photograph", f"{s['subject']} historical image"],
        })

    # Turn ON fast mode or standard mode with Qwen Vision Safety Override
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_WEB_COMPACT_QUERIES"] = "1"
    
    t0 = time.time()
    res = resolve_web_images_for_video_fast(
        script=TITANIC_SCRIPT,
        title=title,
        scene_texts=scene_texts,
        scene_durations=durations,
        scene_queries_override=scene_overrides,
    )
    dt = time.time() - t0
    
    print("\n" + "=" * 80)
    print(f"WEB IMAGE RESOLUTION COMPLETED IN {dt:.2f}s")
    print("=" * 80)

    if isinstance(res, dict):
        # The resolver's public contract is image_paths + image_metadata;
        # older harness code looked for a nonexistent `results` field and
        # falsely reported 0/N after a successful run.
        image_paths = list(res.get("image_paths") or [])
        image_metadata = list(res.get("image_metadata") or [])
        results_list = []
        for idx, path in enumerate(image_paths):
            row = dict(image_metadata[idx]) if idx < len(image_metadata) and isinstance(image_metadata[idx], dict) else {}
            row["path"] = path or ""
            results_list.append(row)
    else:
        results_list = list(res or [])
    resolved_count = sum(1 for r in results_list if r and r.get("path"))
    print(f"Resolved Scenes: {resolved_count}/{len(TITANIC_SCENES)} ({resolved_count/len(TITANIC_SCENES)*100:.1f}%)\n")

    sources_count = {}
    for idx, r in enumerate(results_list):
        if r and r.get("path"):
            src = r.get("domain") or r.get("source") or r.get("provider") or "unknown"
            sources_count[src] = sources_count.get(src, 0) + 1
            filename = os.path.basename(r.get("path", ""))
            query_used = (r.get("query_used", "") or "")[:35]
            reused = " (REUSED)" if r.get("reused_from_scene") else ""
            print(f"Scene {idx+1:02d}: [RESOLVED{reused:8s}] Provider: {src:18s} | Query: '{query_used:35s}' | File: {filename}")
        else:
            print(f"Scene {idx+1:02d}: ❌ UNRESOLVED")

    print("\nSource Distribution Breakdown:")
    for src, cnt in sources_count.items():
        print(f"   - {src:18s}: {cnt} scenes")

if __name__ == '__main__':
    run_titanic_production_harness()
