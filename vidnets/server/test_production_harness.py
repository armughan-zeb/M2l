import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.web_image.resolver import resolve_web_images_for_video_fast

SCRIPT_SCENES = [
    {"subject": "Grand Theft Auto VI", "text": "Grand Theft Auto VI is the most anticipated video game of the decade, breaking internet records."},
    {"subject": "Vice City", "text": "Rockstar Games returns players to the vibrant, neon-lit streets of Vice City."},
    {"subject": "Jason and Lucia", "text": "Protagonists Jason and Lucia lead a high-stakes crime story inspired by Bonnie and Clyde."},
    {"subject": "Grand Theft Auto VI", "text": "Crowded beaches, realistic weather, and dynamic AI make the world feel alive."},
    {"subject": "Grand Theft Auto VI", "text": "Industry analysts predict GTA 6 will generate billions of dollars upon launch."},
    {"subject": "Colosseum Rome", "text": "Ancient Roman Colosseum hosted gladiatorial games and historical spectacles in Rome."},
    {"subject": "Roman Colosseum Architecture", "text": "Architects used arch vaults and concrete to construct the massive amphitheater."},
    {"subject": "Emperor Vespasian", "text": "Emperor Vespasian commissioned the Colosseum around 72 AD as a gift to the Roman people."},
    {"subject": "Cricket Match", "text": "Cricket is played worldwide, with legendary matches taking place at Lord's Cricket Ground."},
    {"subject": "Cricket Bowler", "text": "Bowlers deliver fast deliveries trying to knock down the opponent's wickets."},
    {"subject": "Cricket Batsman", "text": "Batsmen hit powerful shots towards the boundary line to score runs for their team."},
    {"subject": "Quantum Computer", "text": "Quantum computers leverage superposition and entanglement to solve complex problems."},
    {"subject": "Quantum Processor Microchip", "text": "Superconducting microchip processors are cooled near absolute zero temperature."},
    {"subject": "Eiffel Tower Night", "text": "Eiffel Tower in Paris lights up the night sky with stunning golden illuminations."},
    {"subject": "Gustave Eiffel", "text": "Gustave Eiffel designed the iconic wrought-iron lattice tower for the 1889 World's Fair."}
]

def run_harness():
    print("=" * 70)
    print("STARTING PRODUCTION RESOLVER TEST HARNESS WITH SCENE OVERRIDES")
    print("=" * 70)
    
    script_text = "\n".join([s["text"] for s in SCRIPT_SCENES])
    scene_texts = [s["text"] for s in SCRIPT_SCENES]
    num_scenes = len(scene_texts)
    durations = [4.0] * num_scenes
    title = "Documentary: GTA VI, Colosseum, Cricket, Quantum Tech & Eiffel Tower"
    
    scene_overrides = []
    for s in SCRIPT_SCENES:
        scene_overrides.append({
            "section_subject": s["subject"],
            "scene_subject": s["subject"],
            "subject_query": s["subject"],
            "queries": [f"{s['subject']} HD photo", f"{s['subject']} photography"],
        })
    
    t0 = time.time()
    results = resolve_web_images_for_video_fast(
        script=script_text,
        title=title,
        scene_texts=scene_texts,
        scene_durations=durations,
        scene_queries_override=scene_overrides,
    )
    dt = time.time() - t0
    
    print("\n" + "=" * 70)
    print(f"HARNESS RESOLUTION COMPLETED IN {dt:.2f}s")
    print("=" * 70)
    
    res_list = results.get("results", []) if isinstance(results, dict) else results
    
    resolved_count = sum(1 for r in res_list if r and r.get("path"))
    print(f"\nResolved Scenes: {resolved_count}/{num_scenes} ({resolved_count/num_scenes*100:.1f}%)")
    
    source_counts = {}
    for idx, r in enumerate(res_list):
        if r and r.get("path"):
            src = r.get("domain") or r.get("source") or r.get("provider") or "unknown"
            source_counts[src] = source_counts.get(src, 0) + 1
            path_base = os.path.basename(r.get('path', ''))
            query_used = (r.get('query_used', '') or '')[:35]
            reused = " (REUSED)" if r.get("reused_from_scene") else ""
            print(f"Scene {idx+1:02d}: [RESOLVED{reused:8s}] Source: {src:15s} | File: {path_base:30s} | Query: '{query_used}'")
        else:
            print(f"Scene {idx+1:02d}: ❌ UNRESOLVED")

    print("\nSource Distribution Breakdown:")
    for src, count in source_counts.items():
        print(f"   - {src}: {count} scenes")

if __name__ == '__main__':
    run_harness()
