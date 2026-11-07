"""Generate a ten-niche video portfolio through the real Flask /api/generate route."""

from __future__ import annotations

import json
import argparse
import os
import re
import sys
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "1")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")

VIDEOS = [
    {
        "category": "science",
        "niche": "science",
        "title": "Why a Solar Eclipse Moves Like a Shadow",
        "script": "A total solar eclipse is not a darkening of the Sun. It is a moving shadow. The Moon blocks sunlight, and its umbra sweeps across Earth as the Moon continues along its orbit. Because the shadow is small compared with the planet, totality lasts only briefly in any one place. Watch the path on a map, and you can see an astronomical alignment become a journey across the ground.",
    },
    {
        "category": "sports",
        "niche": "football",
        "title": "The Goalkeeper’s Split-Second Problem",
        "script": "A penalty kick looks like a contest between one shooter and one goalkeeper. In reality, it is a race against time. From twelve yards, the ball can reach the goal before a keeper can react and cross the full distance. So keepers often read the striker’s approach and choose a direction early. The drama comes from that gamble: commit too soon, and the shot goes the other way.",
    },
    {
        "category": "crime",
        "niche": "crime",
        "title": "How Investigators Rebuild a Timeline",
        "script": "A crime timeline is not a story written backward from a suspect. Investigators start with evidence that can be checked: timestamps, travel records, witness accounts, and physical traces. Each item has limits, so a careful timeline marks what is confirmed and what remains uncertain. When two accounts disagree, that gap becomes a question to investigate, not a detail to quietly smooth over. The sequence is only as strong as its sources.",
    },
    {
        "category": "nature",
        "niche": "wildlife",
        "title": "An Octopus Has More Than One Control Center",
        "script": "An octopus does not move like an animal with a rigid skeleton. Its arms bend, stretch, and explore in almost any direction. A large share of its neurons are distributed through those arms, helping each one sense and respond to its surroundings. That means the animal’s movement is not simply a central command sent to eight passive limbs. It is a flexible system of coordination, touch, and rapid problem-solving.",
    },
    {
        "category": "travel",
        "niche": "travel",
        "title": "The Thousands of Gates at Fushimi Inari",
        "script": "In Kyoto, a path climbs Mount Inari through thousands of vermilion torii gates. Many were donated by people and businesses, with their names written on the posts. Walk uphill and the city gradually disappears behind the dense rows of wood. The gates are not one giant monument built at once; they form a living record of devotion, repeated offering, and a route that keeps unfolding around the mountain.",
    },
    {
        "category": "finance",
        "niche": "personal finance",
        "title": "What Compounding Can Do Over Time",
        "script": "Imagine investing one hundred dollars every month for thirty years. At a hypothetical seven percent average annual return, compounded monthly, the account could grow to roughly one hundred twenty thousand dollars before taxes and fees. Your deposits total thirty-six thousand. The difference comes from returns earning returns over time. This is an illustration, not a promise: markets rise and fall, and the result depends on the actual return and costs.",
    },
    {
        "category": "gaming",
        "niche": "gaming",
        "title": "Why Tetris Has Seven Tetrominoes",
        "script": "Tetris builds a whole puzzle from seven shapes, each made of four connected squares. Every piece can rotate, but the board keeps filling from the bottom up. That small rule set creates a constant tradeoff: clear a line now, or save space for a better fit later. The game feels simple because its controls are simple. The strategy comes from seeing patterns before the falling pieces reach the stack.",
    },
    {
        "category": "technology",
        "niche": "technology",
        "title": "Why GPS Needs Einstein’s Relativity",
        "script": "GPS satellites carry clocks, and those clocks do not tick exactly like clocks on Earth. Their speed makes them run a little slower, while weaker gravity in orbit makes them run faster. The gravity effect wins, by about thirty-eight microseconds each day. That sounds tiny, but navigation depends on precise timing. Without relativity corrections, the position calculated from satellite signals would drift by kilometers in a single day.",
    },
    {
        "category": "automotive",
        "niche": "automotive",
        "title": "How a Turbocharger Reuses Exhaust Energy",
        "script": "A turbocharger helps an automotive engine draw in more air without adding a larger engine. Hot exhaust spins a turbine, which drives a compressor on the same shaft. The compressor packs more air into the cylinders, where fuel can burn with it. That extra airflow can produce more power from a smaller engine, but it also creates heat and pressure that engineers must carefully control.",
    },
    {
        "category": "history",
        "niche": "history",
        "title": "The Boston Molasses Flood of 1919",
        "script": "On a January afternoon in 1919, a storage tank burst in Boston’s North End. A wave of molasses rushed through nearby streets, damaging buildings and trapping people and horses. Twenty-one people died, and many others were injured. The disaster led to years of legal scrutiny over the tank’s construction and inspection. It is remembered for its strange substance, but its real story is about industrial safety and accountability.",
    },
]


def safe_stem(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:72]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--indices",
        help="Comma-separated portfolio indices to run, e.g. 1,3-5,7-10. Defaults to all ten.",
    )
    parser.add_argument('--artifact-dir', help='Preserve reports and finished videos outside disposable outputs.')
    args = parser.parse_args()
    from app import app

    out_dir = Path(args.artifact_dir).resolve() if args.artifact_dir else ROOT / 'artifacts' / 'vidnets_10_niche_portfolio'
    out_dir.mkdir(parents=True, exist_ok=True)
    portfolio: list[dict] = []
    total_started = time.monotonic()
    selected_indices = set(range(1, len(VIDEOS) + 1))
    if args.indices:
        selected_indices = set()
        for part in args.indices.split(","):
            part = part.strip()
            if "-" in part:
                start, end = (int(value) for value in part.split("-", 1))
                selected_indices.update(range(start, end + 1))
            elif part:
                selected_indices.add(int(part))

    for index, item in enumerate(VIDEOS, 1):
        if index not in selected_indices:
            continue
        word_count = len(item["script"].split())
        if word_count > 90:
            raise ValueError(f"{item['niche']} script is over the 40-second limit: {word_count} words")
        stem = f"{index:02d}_{safe_stem(item['niche'])}_{safe_stem(item['title'])}"
        print(f"[{index:02d}/10] START {item['niche']}: {item['title']} ({word_count} words)", flush=True)
        config = {
            "type": "long",
            "name": stem,
            "output_name": stem,
            "title": item["title"],
            "script": item["script"],
            "category": item["category"],
            "image_source": "Mixed Media",
            "media_mode": "mixed",
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
            "render_backend": "remotion",
            "semantic_render_scale": 1.0,
            "semantic_render_crf": 18,
            "visual_understanding": True,
            "strict_mixed_percentages": False,
            "strict_retrieval_accuracy": True,
            "fail_on_unresolved_media": False,
            "auto_export": True,
            "semantic_edit_enable": True,
            "semantic_render_enabled": True,
            "fast_mode": True,
            "semantic_fast_render": True,
            "semantic_render_concurrency": 2,
        }
        started = time.monotonic()
        try:
            response = app.test_client().post("/api/generate", json=config)
            result = response.get_json(silent=True) or {}
            if response.status_code != 200 or not result.get("success"):
                raise RuntimeError(
                    f"API returned {response.status_code}: "
                    f"{result.get('error') or result.get('message') or 'unspecified failure'}"
                )
            render = result.get("render_metadata") or {}
            component_manifest = result.get("component_manifest") or {}
            remotion_manifest = component_manifest.get("remotion_manifest") or {}
            edit_validation = render.get("edit_plan_validation") or {}
            quality = render.get("quality_checks") or {}
            summary = {
                "index": index,
                "niche": item["niche"],
                "title": item["title"],
                "script": item["script"],
                "script_word_count": word_count,
                "estimated_read_seconds_at_150wpm": round(word_count / 150 * 60, 1),
                "success": True,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "video_path": result.get("video_path") or result.get("output_path"),
                "video_url": result.get("video_url"),
                "duration_seconds": result.get("duration"),
                "manifest_path": result.get("manifest_path"),
                "storyboard_path": result.get("storyboard_path") or result.get("storyboard"),
                "selected_backend": render.get("selected_backend"),
                "quality_status": quality.get("status"),
                "scene_count": edit_validation.get("scene_count") or len(remotion_manifest.get("scenes") or []),
                "sound_cue_count": (
                    remotion_manifest.get("metrics", {}).get("sound_cue_count")
                    or remotion_manifest.get("sound_audit", {}).get("after")
                    or len(remotion_manifest.get("audio", {}).get("effects") or [])
                ),
                "render_metadata": render,
                "release_review": render.get('release_review') or {},
                "release_ready": (render.get('release_review') or {}).get('ready_to_publish') is True,
            }
            source_video = Path(str(summary.get('video_path') or ''))
            if not source_video.is_file():
                raise RuntimeError('API reported success without a finished video file')
            preserved_video = out_dir / f'{stem}.mp4'
            shutil.copy2(source_video, preserved_video)
            summary['preserved_video_path'] = str(preserved_video)
            for source_plan in source_video.parent.glob('*remotion_edit_plan.json'):
                shutil.copy2(source_plan, out_dir / source_plan.name)
            if float(summary.get('duration_seconds') or 0) > 40.1:
                raise RuntimeError(f"Output exceeds the 40-second acceptance limit: {summary['duration_seconds']}s")
            (out_dir / f"{stem}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            portfolio.append(summary)
            print(
                f"[{index:02d}/10] DONE duration={summary['duration_seconds']}s "
                f"scenes={summary['scene_count']} quality={summary['quality_status']} "
                f"video={summary['video_path']}",
                flush=True,
            )
        except Exception as exc:  # preserve successful outputs and keep the batch moving
            failed_evidence = out_dir / f'{stem}_failed_review'
            failed_evidence.mkdir(exist_ok=True)
            for source_plan in (ROOT / 'output').glob(f'{stem}*/*remotion_edit_plan.json'):
                shutil.copy2(source_plan,failed_evidence / source_plan.name)
            for evidence in (ROOT / 'temp').glob(f'{stem}*creative-review'):
                if evidence.is_dir():
                    shutil.copytree(evidence,failed_evidence / evidence.name,dirs_exist_ok=True)
            for evidence in (ROOT / 'temp').glob(f'{stem}*.preview.json'):
                shutil.copy2(evidence,failed_evidence / evidence.name)
            summary = {
                "index": index,
                "niche": item["niche"],
                "title": item["title"],
                "script": item["script"],
                "script_word_count": word_count,
                "success": False,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "error": str(exc),
                "review_evidence_path": str(failed_evidence),
            }
            (out_dir / f"{stem}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            portfolio.append(summary)
            print(f"[{index:02d}/10] FAILED {exc}", flush=True)

    index_name = "portfolio.json" if len(selected_indices) == len(VIDEOS) else (
        "portfolio_rerun_" + "_".join(str(index) for index in sorted(selected_indices)) + ".json"
    )
    index_path = out_dir / index_name
    index_path.write_text(json.dumps({
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_elapsed_seconds": round(time.monotonic() - total_started, 1),
        "videos": portfolio,
    }, indent=2, default=str), encoding="utf-8")
    succeeded = sum(1 for item in portfolio if item.get("success"))
    accepted = sum(1 for item in portfolio if item.get('release_ready'))
    print(f"[PORTFOLIO] {succeeded}/{len(selected_indices)} rendered; {accepted}/{len(selected_indices)} accepted; index={index_path}", flush=True)
    return 0 if accepted == len(selected_indices) else 1


if __name__ == "__main__":
    raise SystemExit(main())
