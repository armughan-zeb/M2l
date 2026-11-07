"""Opt-in live-provider audit using explicitly fictional multi-niche fixtures.

Writes plans and timings for inspection; does not publish or retrieve media.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import app  # Load the same provider configuration as the desktop application.
from services.editorial_director import EditingDirector

CASES = {
    "crime": [
        "In this fictional investigation, a missing ledger changes the case.",
        "A witness entered the warehouse before the alarm sounded.",
        "Investigators compared the entrance record with the witness statement.",
        "The first report recorded 12 entries; the second recorded 19 entries.",
        "The ledger moved from London to Paris before investigators found it.",
        "The evidence explains the sequence, but it does not establish guilt.",
    ],
    "finance": [
        "In this fictional company, revenue doubled while cash reserves fell.",
        "Revenue rose from 20 million dollars to 40 million dollars.",
        "Customer orders created invoices, then invoices generated cash payments.",
        "The expansion cost twenty five billion dollars in today's money.",
        "Cash reserves declined from 12 million dollars to 8 million dollars.",
        "The distinction between revenue and cash explains the company's risk.",
    ],
    "sports": [
        "In this fictional football season, one late goal changed the final.",
        "The home team recorded 12 shots; the away team recorded 8 shots.",
        "The goalkeeper blocked the first shot before the striker scored.",
        "The first half ended at 45 minutes; the decisive goal arrived at 89 minutes.",
        "The home team pressed forward while the away team defended deeper.",
        "The winning move began with positioning rather than luck.",
    ],
    "science": [
        "In this fictional experiment, ocean heat altered coastal currents.",
        "Ocean heat shifted currents, and currents carried warmth toward the coast.",
        "The first trial recorded 12 units; the second recorded 19 units.",
        "Sensors measured the water before researchers compared the results.",
        "The observation window opened in 2010 and closed in 2020.",
        "The results suggest a mechanism, but a second experiment is still needed.",
    ],
}


def run_case(niche, texts, output):
    started = time.monotonic()
    plan = EditingDirector().plan_documentary(
        script_text=" ".join(texts), scene_texts=texts,
        scene_durations=[10.0] * len(texts),
        topic=f"Fictional {niche} validation fixture", category=niche,
        variation_seed=sum(ord(char) for char in niche),
    )
    manifest = plan.to_remotion_manifest()
    target = output / f"{niche}.manifest.json"
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    result = {
        "niche": niche, "elapsed_seconds": round(time.monotonic() - started, 2),
        "director": plan.director_metadata,
        "scene_types": [scene.primary_visual.type for scene in plan.scenes],
        "variants": [scene.primary_visual.variant for scene in plan.scenes],
        "recipe_count": sum(bool(scene.template_recipe) for scene in plan.scenes),
        "motion_cue_count": sum(len(scene.motion_cues or []) for scene in plan.scenes),
        "manifest": str(target),
    }
    print(json.dumps({key: value for key, value in result.items() if key != "director"}), flush=True)
    return result


if __name__ == "__main__":
    output = ROOT / "output" / "release-live-director-audit"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_case, niche, texts, output) for niche, texts in CASES.items()]
        for future in as_completed(futures):
            results.append(future.result())
    (output / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
