"""Diagnostic harness: replay the V2 visual gate on stored assets for scenes
that ended up as Neutral No-Claim Visual in the last Top-5 listicle run.

The point is to capture the exact Qwen verdicts (scores + rejection reasons)
for the candidates the coordinator rejected, WITHOUT re-running the network
discovery.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), encoding="utf-8-sig")

OUTPUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
STORYBOARD = os.path.join(OUTPUTS, "Top 5 Most Famous Shipwrecks in History.storyboard.json")
RUN_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "temp",
    "jobs",
    "job_5770276d9ead02f891b2",
    "run_d368551c4333db42d928",
)

from services.pipeline_v2_media_adapter import build_qwen_verifier
from services.visual_intelligence.context import VisualDecisionContext


def load_storyboard():
    with open(STORYBOARD, encoding="utf-8") as handle:
        return json.load(handle)


def main():
    data = load_storyboard()
    scenes = data["script_plan"]["scenes"]
    states = data.get("scene_states") or {}
    web_dir = os.path.join(RUN_DIR, "render_media", "web_v2_1786521766265_6031dc")
    yt_dir = os.path.join(RUN_DIR, "render_media", "youtube_v2_1786521890424_264d5b")

    degraded = [
        (index, scene) for index, scene in enumerate(scenes)
        if (states.get(scene["scene_id"]) or {}).get("status") == "degraded"
    ]
    print(f"Degraded scenes: {[index for index, _ in degraded]}")

    config = {
        "storyboard_v2": True,
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_fail_closed": True,
        "visual_concurrency": 3,
        "visual_deadline_seconds": 600,
        "visual_shared_deadline_seconds": 600,
        "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY") or "",
        "visual_max_calls": 40,
        "visual_shared_max_calls": 40,
        "visual_call_timeout_seconds": 30,
        "visual_call_max_attempts": 2,
    }
    verifier, context = build_qwen_verifier(config, scenes)
    print(f"Vision model: {context.model} fallback={context.fallback_model}")
    print(f"enabled={context.enabled} degraded={context.degraded_reason}")

    for index, scene in degraded:
        print("\n" + "=" * 90)
        print(f"SCENE {index}  {scene['scene_id']}  container={scene['container_id']}")
        print(f"  text: {scene['text']}")
        print(f"  positive: {scene.get('positive_subjects')}")
        print(f"  forbidden: {scene.get('forbidden_subjects')}")
        print(f"  visual_intent: {scene.get('visual_intent')}")

        candidates = []
        web_path = os.path.join(web_dir, f"scene_{index + 1:04d}.jpg")
        if os.path.isfile(web_path):
            candidates.append({
                "candidate_id": f"web:{scene['scene_id']}",
                "path": web_path,
                "media_type": "image",
                "provider": "web",
                "source": "web",
                "container_id": scene["container_id"],
                "final_score": 50.0,
            })
        yt_path = os.path.join(yt_dir, f"scene_{index + 1:04d}.mp4")
        if os.path.isfile(yt_path):
            candidates.append({
                "candidate_id": f"youtube:{os.path.basename(yt_path)}",
                "path": yt_path,
                "media_type": "video",
                "provider": "youtube",
                "source": "youtube",
                "container_id": scene["container_id"],
                "final_score": 50.0,
            })
        if not candidates:
            print("  NO STORED CANDIDATE ASSETS - skipping")
            continue

        for candidate in candidates:
            t0 = time.time()
            try:
                verdict = verifier(scene, candidate)
            except Exception as exc:
                print(f"  [{candidate['provider']}] VERIFIER RAISED: {type(exc).__name__}: {exc}")
                continue
            elapsed = time.time() - t0
            print(f"  [{candidate['provider']}] {os.path.basename(candidate['path'])}")
            print(f"     verdict ({elapsed:.1f}s): {json.dumps(verdict, indent=2, default=str)[:1800]}")


if __name__ == "__main__":
    main()