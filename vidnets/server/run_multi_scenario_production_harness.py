"""Multi-scenario production harness — full media resolution loop.

Runs the real pipeline across several job shapes:
  - narrated_web : 18-scene narrated documentary (web images)
  - listicle_web : 10-item listicle (web images, distinct per-item subjects)
  - long_web     : 58-scene long documentary (web images, deadline scaling)
  - narrated_yt  : 8-scene narrative (YouTube clips, real downloads)
  - listicle_yt  : 8-item listicle (YouTube clips, real downloads)

Each scenario is scored on resolution rate, speed, source diversity, and (for
listicles) per-item subject accuracy.  A failure in one scenario does not stop
the loop.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SERVER_DIR)

MANIFEST_DIR = Path(SERVER_DIR).parent / "temp" / "manifests" / "multi_scenario"
MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

# ── Shared scenario content ────────────────────────────────────────────────

TITANIC_TITLE = "The Sinking of RMS Titanic: Unsinkable Legend"
TITANIC_SCRIPT = """On April 10, 1912, the RMS Titanic, the largest and most luxurious ocean liner ever built, embarked on its maiden voyage from Southampton, England, bound for New York City. Hailed as an unsinkable marvel of modern industrial engineering, the massive vessel carried over 2,200 passengers and crew members, including wealthy aristocrats, prominent businessmen, and hundreds of hopeful immigrants seeking new lives in America.

Constructed in Belfast by the Harland and Wolff shipyard for the White Star Line, Titanic measured 882 feet in length and featured state-of-the-art watertight bulkheads, opulent dining saloons, a grand staircase, and a heated swimming pool. Captain Edward John Smith commanded the magnificent ship across the chilly waters of the North Atlantic.

However, four days into the voyage, on the quiet, moonless night of April 14, 1912, at 11:40 PM, lookouts Frederick Fleet and Reginald Lee spotted a massive iceberg looming directly in the ship's path. Despite First Officer William Murdoch's desperate order to turn hard-a-starboard and reverse engines, Titanic struck the iceberg along its starboard side, buckling hull plates below the waterline and opening six compartments to the freezing ocean.

As water flooded the forward compartments, chief naval architect Thomas Andrews inspected the damage and calculated that the ship had less than two hours before sinking completely into the Atlantic. Distress signals, including the newly adopted SOS, were transmitted via Marconi wireless telegraphy to nearby ships, while distress rockets were launched into the night sky.

The ship carried only 20 lifeboats, enough for barely half of the people onboard. Under the rule of women and children first, officers loaded lifeboats into the freezing sea. The ocean liner Carpathia, commanded by Captain Arthur Rostron, heard the distress calls and altered course at full speed through dangerous ice fields to reach the scene.

At 2:20 AM on April 15, 1912, Titanic broke in two and sank to the ocean floor, resulting in the tragic loss of over 1,500 lives. Carpathia arrived at dawn, rescuing 705 shivering survivors from the lifeboats. The disaster stunned the global public, sparking sweeping international maritime safety regulations, mandating 24-hour radio watches and lifeboats for all passengers. Decades later, in 1985, Dr. Robert Ballard discovered the shipwreck resting 12,500 feet below the surface, preserving its tragic legacy forever."""

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
    {"subject": "Robert Ballard Titanic Shipwreck Discovery", "text": "In 1985, Dr. Robert Ballard discovered the shipwreck resting 12,500 feet below the surface."},
]

SHIPWRECK_LISTICLE_TITLE = "Top 10 Most Famous Shipwrecks in Maritime History"
SHIPWRECK_LISTICLE_SCRIPT = " ".join(
    f"Number {i + 1}: {item['name']}. {item['text']}"
    for i, item in enumerate([
        {"name": "RMS Titanic", "text": "The luxury ocean liner struck an iceberg and sank in 1912."},
        {"name": "RMS Lusitania", "text": "The British ocean liner was torpedoed by a German U-boat in 1915."},
        {"name": "Bismarck", "text": "The German battleship was scuttled after being crippled by the Royal Navy in 1941."},
        {"name": "Costa Concordia", "text": "The Italian cruise ship capsized after hitting rocks off Giglio island in 2012."},
        {"name": "Vasa", "text": "The Swedish warship sank on its maiden voyage in Stockholm harbor in 1628."},
        {"name": "Mary Rose", "text": "The English Tudor warship sank in the Solent during battle in 1545."},
        {"name": "Edmund Fitzgerald", "text": "The American freighter vanished in a storm on Lake Superior in 1975."},
        {"name": "Kursk Submarine", "text": "The Russian nuclear submarine sank after an explosion in 2000."},
        {"name": "HMHS Britannic", "text": "The sister ship of Titanic struck a mine and sank in the Aegean in 1916."},
        {"name": "Andrea Doria", "text": "The Italian liner collided with Stockholm off Nantucket in 1956."},
    ])
)
SHIPWRECK_ITEMS = [
    {"subject": "RMS Titanic shipwreck", "text": "Number 1: RMS Titanic. The luxury ocean liner struck an iceberg and sank in 1912."},
    {"subject": "RMS Lusitania torpedoed liner", "text": "Number 2: RMS Lusitania. The British ocean liner was torpedoed by a German U-boat in 1915."},
    {"subject": "Bismarck battleship scuttled", "text": "Number 3: Bismarck. The German battleship was scuttled after being crippled by the Royal Navy in 1941."},
    {"subject": "Costa Concordia capsized cruise ship", "text": "Number 4: Costa Concordia. The Italian cruise ship capsized after hitting rocks off Giglio island in 2012."},
    {"subject": "Vasa warship Stockholm harbor", "text": "Number 5: Vasa. The Swedish warship sank on its maiden voyage in Stockholm harbor in 1628."},
    {"subject": "Mary Rose Tudor warship Solent", "text": "Number 6: Mary Rose. The English Tudor warship sank in the Solent during battle in 1545."},
    {"subject": "Edmund Fitzgerald freighter Lake Superior", "text": "Number 7: Edmund Fitzgerald. The American freighter vanished in a storm on Lake Superior in 1975."},
    {"subject": "Kursk submarine explosion", "text": "Number 8: Kursk Submarine. The Russian nuclear submarine sank after an explosion in 2000."},
    {"subject": "HMHS Britannic mine Aegean", "text": "Number 9: HMHS Britannic. The sister ship of Titanic struck a mine and sank in the Aegean in 1916."},
    {"subject": "Andrea Doria collision Nantucket", "text": "Number 10: Andrea Doria. The Italian liner collided with Stockholm off Nantucket in 1956."},
]

YT_NARRATIVE_SCENES = [
    "On April 10, 1912, the RMS Titanic embarked on its maiden voyage from Southampton, England.",
    "Constructed in Belfast by Harland and Wolff, Titanic was the largest passenger liner in the world.",
    "Captain Edward Smith commanded the massive ocean liner as it sped across the chilly North Atlantic.",
    "On the moonless night of April 14, lookouts spotted a giant iceberg directly ahead.",
    "First Officer Murdoch ordered hard-a-starboard and full reverse, but the ship scraped the iceberg.",
    "Freezing Atlantic water flooded five forward watertight compartments below the waterline.",
    "At 2:20 AM, the ocean liner broke in two and plunged into the abyss.",
    "In 1985, Dr. Robert Ballard discovered the shipwreck resting 12,500 feet under the sea.",
]

YT_LISTICLE_SCENES = [
    "Number 1: RMS Titanic. The luxury ocean liner struck an iceberg and sank in 1912.",
    "Number 2: RMS Lusitania. The British ocean liner was torpedoed by a German U-boat in 1915.",
    "Number 3: Bismarck. The German battleship was scuttled after being crippled by the Royal Navy in 1941.",
    "Number 4: Costa Concordia. The Italian cruise ship capsized after hitting rocks off Giglio island in 2012.",
    "Number 5: Vasa. The Swedish warship sank on its maiden voyage in Stockholm harbor in 1628.",
    "Number 7: Edmund Fitzgerald. The American freighter vanished in a storm on Lake Superior in 1975.",
    "Number 8: Kursk Submarine. The Russian nuclear submarine sank after an explosion in 2000.",
    "Number 10: Andrea Doria. The Italian liner collided with Stockholm off Nantucket in 1956.",
]


# ── Helpers ────────────────────────────────────────────────────────────────

def _subject_tokens(subject: str) -> set:
    return {
        t for t in re.findall(r"[a-z0-9]{3,}", str(subject or "").lower())
        if t not in {"the", "and", "for", "with", "number", "ship", "ships"}
    }


def _overlap_ratio(haystack: str, subject_tokens: set) -> float:
    blob = set(re.findall(r"[a-z0-9]{3,}", str(haystack or "").lower()))
    if not subject_tokens:
        return 1.0
    return len(blob & subject_tokens) / len(subject_tokens)


def _query_rows(scenes, prefix: str = "") -> list:
    rows = []
    for s in scenes:
        subject = str(s["subject"] if isinstance(s, dict) else s)
        text = str(s["text"] if isinstance(s, dict) else s)
        rows.append({
            "section_subject": subject,
            "scene_subject": subject,
            "subject_query": subject,
            "query_variants": [
                f"{subject} photograph",
                f"{subject} historical image",
                f"{subject} archival photo",
            ],
            "specific_query": f"{subject} photograph",
            "general_query": f"{subject}",
        })
    return rows


def _scene_texts(scenes) -> list:
    return [str(s["text"] if isinstance(s, dict) else s) for s in scenes]


def _scene_subjects(scenes) -> list:
    return [str(s["subject"] if isinstance(s, dict) else "") for s in scenes]


def _elapsed(t0: float) -> float:
    return time.time() - t0


# ── Web image scenario runner ──────────────────────────────────────────────

def run_web_scenario(name: str, title: str, script: str, scenes, pass_ratio: float, budget_s: float):
    print(f"\n{'=' * 78}\nSCENARIO: {name} ({len(scenes)} scenes) — web images\n{'=' * 78}", flush=True)
    from services.web_image.resolver import resolve_web_images_for_video_fast

    scene_texts = _scene_texts(scenes)
    overrides = _query_rows(scenes)
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_WEB_COMPACT_QUERIES"] = "1"

    t0 = time.time()
    res = resolve_web_images_for_video_fast(
        script=script,
        title=title,
        scene_texts=scene_texts,
        scene_durations=[5.0] * len(scene_texts),
        scene_queries_override=overrides,
    )
    dt = _elapsed(t0)

    image_paths = list((res or {}).get("image_paths") or [])
    meta = list((res or {}).get("image_metadata") or [])
    audit = (res or {}).get("audit") or {}
    resolved = 0
    rows = []
    source_counts = {}
    for idx, path in enumerate(image_paths):
        row = dict(meta[idx]) if idx < len(meta) and isinstance(meta[idx], dict) else {}
        row["path"] = path or ""
        rows.append(row)
        if path:
            resolved += 1
            src = str(row.get("domain") or row.get("provider") or "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1
    ratio = resolved / max(1, len(scene_texts))

    # Listicle accuracy: the winning query should carry the item subject.
    # The resolver audit records per-scene query_used / active_subject.
    subjects = _scene_subjects(scenes)
    accuracy = 0.0
    audit_data = None
    audited = []
    audit_path = Path(SERVER_DIR).parent / "temp" / "manifests" / "web_image_audit.json"
    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            audit_data = json.load(f)
        audited = audit_data.get("scenes") or []
    except Exception:
        audit_data = None
    if audited:
        hits = 0
        for idx, entry in enumerate(audited):
            if idx >= len(subjects):
                break
            q = str((entry or {}).get("query_used") or "")
            if q and _overlap_ratio(q, _subject_tokens(subjects[idx])) >= 0.4:
                hits += 1
        accuracy = hits / max(1, len(audited))

    passed = ratio >= pass_ratio and dt <= budget_s
    result = {
        "scenario": name,
        "kind": "web",
        "resolved": resolved,
        "total": len(scene_texts),
        "ratio": round(ratio, 3),
        "elapsed_seconds": round(dt, 1),
        "budget_seconds": budget_s,
        "sources": dict(sorted(source_counts.items(), key=lambda kv: -kv[1])),
        "listicle_accuracy": round(accuracy, 3),
        "passed": bool(passed),
        "audit_snapshot": {
            "shared_batch_query": str((audit_data or {}).get("shared_batch") or {}).replace("'", '"')[:200],
            "deadline_reached": bool((audit_data or {}).get("deadline_reached")),
            "stage_deadline_seconds": (audit_data or {}).get("stage_deadline_seconds")
            or (audit or {}).get("stage_deadline_seconds"),
        },
        "per_scene": [],
    }
    for idx, row in enumerate(rows):
        q_used = row.get("query_used") or ""
        if idx < len(audited) and not q_used:
            q_used = str((audited[idx] or {}).get("query_used") or "")
        result["per_scene"].append({
            "scene": idx + 1,
            "subject": subjects[idx],
            "resolved": bool(row.get("path")),
            "domain": row.get("domain", ""),
            "query_used": q_used[:60],
        })
    print(f"  resolved: {resolved}/{len(scene_texts)} ({ratio * 100:.0f}%) in {dt:.1f}s", flush=True)
    print(f"  sources: {dict(sorted(source_counts.items(), key=lambda kv: -kv[1]))}", flush=True)
    if subjects[0]:
        print(f"  listicle accuracy: {accuracy * 100:.0f}%", flush=True)
    _dump_manifest(name, result)
    return result


# ── YouTube clips scenario runner ──────────────────────────────────────────

def run_yt_scenario(name: str, title: str, script: str, scenes, pass_ratio: float, budget_s: float, download: bool = True):
    print(f"\n{'=' * 78}\nSCENARIO: {name} ({len(scenes)} scenes) — YouTube clips\n{'=' * 78}", flush=True)
    from services.youtube_pipeline_v2 import get_youtube_clips

    scene_texts = _scene_texts(scenes)
    subjects = _scene_subjects(scenes)
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_YT_BLUR_SMALL_LOGOS"] = "1"
    os.environ["VIDZEO_YT_SOURCE_MAX_HEIGHT"] = "1080"
    os.environ["VIDZEO_YT_SHARED_SOURCE_MAX_MB"] = "40"
    os.environ["VIDZEO_YT_FORMAT"] = (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    )

    out_dir = Path(SERVER_DIR).parent / "temp" / "yt_harness_output" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("VIDZEO_OPENROUTER_API_KEY") or ""
    results = get_youtube_clips(
        script=script,
        title=title,
        num_scenes=len(scene_texts),
        scene_durations=[4.0] * len(scene_texts),
        scene_texts=scene_texts,
        scene_query_overrides=_query_rows(scenes) if not None else None,
        output_dir=out_dir,
        openrouter_api_key=openrouter_key,
        max_download_scenes=len(scene_texts) if download else 0,
        download_candidate_limit=1,
    )
    dt = _elapsed(t0)

    paths = list(results or [])
    resolved = 0
    per_scene = []
    for idx, path in enumerate(paths):
        ok = bool(path) and os.path.exists(path)
        if ok:
            resolved += 1
        per_scene.append({
            "scene": idx + 1,
            "subject": subjects[idx] if idx < len(subjects) else "",
            "resolved": ok,
            "file": os.path.basename(str(path)) if path else "",
        })
    ratio = resolved / max(1, len(paths))
    passed = ratio >= pass_ratio and dt <= budget_s
    result = {
        "scenario": name,
        "kind": "yt",
        "resolved": resolved,
        "total": len(paths),
        "ratio": round(ratio, 3),
        "elapsed_seconds": round(dt, 1),
        "budget_seconds": budget_s,
        "passed": bool(passed),
        "per_scene": per_scene,
    }
    print(f"  resolved: {resolved}/{len(paths)} ({ratio * 100:.0f}%) in {dt:.1f}s", flush=True)
    _dump_manifest(name, result)
    return result


def _dump_manifest(name: str, result: dict) -> None:
    path = MANIFEST_DIR / f"{name}_scenario.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  manifest: {path}", flush=True)


# ── Long 58-scene generator ────────────────────────────────────────────────

def build_long_scenes() -> list:
    scenes = []
    base = list(TITANIC_SCENES)
    variants = [
        ("Detail", "This chapter focuses on {}."),
        ("Perspective", "From a historical perspective, {}."),
        ("Reflection", "Reflecting on the events, {}."),
    ]
    for _round in range(3):
        for s in base:
            text = variants[_round % 3][1].format(s["text"].rstrip(".")) + "."
            scenes.append({"subject": s["subject"], "text": text})
    extra = [
        {"subject": "Titanic Lifeboat Collapsible", "text": "Collapsible lifeboats Engelhardt A and B were washed off the decks as the ship went down."},
        {"subject": "Jack Phillips Radio Operator", "text": "Senior wireless operator Jack Phillips kept sending distress messages until water reached the radio room."},
        {"subject": "Titanic Boiler Room Stokers", "text": "Stokers and firemen toiled in the boiler rooms to keep the pumps running as the bow flooded."},
        {"subject": "Bruce Ismay White Star Line", "text": "White Star Line chairman J. Bruce Ismay boarded a lifeboat and was later criticized for the decision."},
        {"subject": "Titanic Band Musicians", "text": "The ship's band famously played on the deck as the vessel settled deeper into the sea."},
    ]
    scenes = scenes[:53] + extra[:5]
    return scenes


# ── Main loop ──────────────────────────────────────────────────────────────

def main():
    _only = [a for a in sys.argv[1:] if not a.startswith("-")]
    _only_scenario = _only[0] if _only else None
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    results = []
    started = time.time()

    scenarios = [
        ("narrated_web", run_web_scenario, TITANIC_TITLE, TITANIC_SCRIPT, TITANIC_SCENES, 0.90, 180),
        ("listicle_web", run_web_scenario, SHIPWRECK_LISTICLE_TITLE, SHIPWRECK_LISTICLE_SCRIPT, SHIPWRECK_ITEMS, 0.85, 300),
        ("long_web", run_web_scenario, TITANIC_TITLE, TITANIC_SCRIPT, None, 0.85, 480),
        ("narrated_yt", run_yt_scenario, TITANIC_TITLE, " ".join(YT_NARRATIVE_SCENES), [{"subject": s, "text": s} for s in YT_NARRATIVE_SCENES], 0.70, 900),
        ("listicle_yt", run_yt_scenario, SHIPWRECK_LISTICLE_TITLE, SHIPWRECK_LISTICLE_SCRIPT, [{"subject": s.split(". ")[0], "text": s} for s in YT_LISTICLE_SCENES], 0.70, 900),
    ]

    for spec in scenarios:
        name, runner, title, script, scenes, ratio, budget = spec
        if _only_scenario and name != _only_scenario:
            continue
        try:
            if name == "long_web":
                scenes = build_long_scenes()
                print(f"  long_web: generated {len(scenes)} scenes", flush=True)
            result = runner(name, title, script, scenes, ratio, budget)
            results.append(result)
        except Exception as exc:
            import traceback
            print(f"SCENARIO {name} CRASHED: {exc}", flush=True)
            traceback.print_exc()
            results.append({
                "scenario": name,
                "kind": "web" if name.endswith("_web") else "yt",
                "resolved": 0,
                "total": 0,
                "ratio": 0.0,
                "elapsed_seconds": round(_elapsed(started), 1),
                "passed": False,
                "error": str(exc),
            })

    total_elapsed = _elapsed(started)
    passed_count = sum(1 for r in results if r.get("passed"))
    print("\n" + "=" * 78)
    print("MULTI-SCENARIO PRODUCTION LOOP — FINAL REPORT")
    print("=" * 78)
    for r in results:
        flag = "PASS" if r.get("passed") else "FAIL"
        extra = ""
        if r.get("listicle_accuracy") is not None and r.get("kind") == "web":
            extra = f" | accuracy={r['listicle_accuracy'] * 100:.0f}%"
        print(
            f"  [{flag}] {r.get('scenario'):14s} "
            f"{r.get('resolved', 0)}/{r.get('total', 0)} "
            f"({r.get('ratio', 0) * 100:.0f}%) in {r.get('elapsed_seconds', 0):.1f}s{extra}"
        )
    print(f"\n  TOTAL LOOP TIME: {total_elapsed:.1f}s | PASSED {passed_count}/{len(results)}")
    summary_path = MANIFEST_DIR / "loop_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({"total_elapsed_seconds": round(total_elapsed, 1), "results": results}, f, indent=2, ensure_ascii=False)
    print(f"  summary: {summary_path}")
    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
