#!/usr/bin/env python3
"""
Integration Test: Full Pipeline with Real Football Script
Tracks search, downloads, concurrency, timing, and results.
"""
import os
import sys
import time
import json
import threading
from datetime import datetime
from pathlib import Path

# Add server to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

# Set environment variables BEFORE importing pipeline
os.environ["VIDZEO_YT_DOWNLOAD_CONCURRENCY"] = "8"
os.environ["VIDZEO_YT_BATCH_SIZE"] = "8"
os.environ["VIDZEO_YT_SEARCH_CONCURRENCY"] = "8"
os.environ["VIDZEO_YT_SELECTION_CONCURRENCY"] = "8"
os.environ["VIDZEO_YT_CLIP_DOWNLOAD_DEADLINE_SECONDS"] = "180"
os.environ["VIDZEO_YT_SEARCH_DELAY"] = "0.5"
os.environ["VIDZEO_YT_HTTP_CHUNK_SIZE"] = "10485760"
os.environ["VIDZEO_YT_FRAGMENT_CONCURRENCY"] = "4"
os.environ["VIDZEO_YT_DOWNLOAD_RETRIES"] = "3"
os.environ["VIDZEO_YT_DOWNLOAD_FRAGMENT_RETRIES"] = "3"
os.environ["VIDZEO_YT_V2_DOWNLOAD_STAGE_TIMEOUT"] = "0"
os.environ["VIDZEO_YT_V2_SCORE_FLOOR"] = "-200"
os.environ["VIDZEO_YT_PREFERRED_HEIGHT"] = "1080"
os.environ["VIDZEO_YT_MIN_SOURCE_HEIGHT"] = "240"
os.environ["VIDZEO_YT_SHARED_SOURCE_CACHE"] = "1"
os.environ["VIDZEO_YT_SHARED_SOURCE_MIN_REUSE"] = "3"
os.environ["VIDZEO_YT_SHARED_SOURCE_ALWAYS_MAX_DURATION"] = "240"
os.environ["VIDZEO_YT_SHARED_SOURCE_MAX_DURATION"] = "600"
os.environ["VIDZEO_YT_SHARED_SOURCE_MAX_MB"] = "80"
os.environ["VIDZEO_YT_SHARED_SOURCE_MAX_END"] = "300"
os.environ["VIDZEO_YT_MULTI_CLIP_SOURCE"] = "1"
os.environ["VIDZEO_YT_COOKIES_FROM_BROWSER"] = "firefox"
os.environ["VIDZEO_YT_PUBLIC_DOWNLOAD_CONCURRENCY"] = "8"
os.environ["VIDZEO_YT_FAST_DOWNLOAD_PLAYER_CLIENTS"] = "tv_downgraded,android_vr,web_embedded"
os.environ["VIDZEO_YT_RESCUE_PLAYER_CLIENTS"] = "mweb,tv_downgraded,android_vr"

from services.youtube_pipeline_v2 import get_youtube_clips

# ─── Football Script ────────────────────────────────────────────────────────────
SCRIPT = """Every football generation has its legends. But today's era has produced some of the most complete, talented, and dominant players the sport has ever seen. Here are the top five best footballers of the current era.

At number five is Erling Haaland. The Norwegian goal machine has rewritten scoring records wherever he's played. With incredible strength, blistering pace, and deadly finishing, Haaland is one of the most feared strikers in world football. Give him one chance, and he'll probably score.

At number four is Jude Bellingham. Still incredibly young, Bellingham has already become one of the world's best midfielders. He combines vision, technical ability, defensive work rate, and leadership beyond his years, making him the complete modern midfielder.

Taking the third spot is Kylian Mbappé. Few players in football history possess his combination of speed, skill, and finishing. Whether it's a World Cup final or a Champions League night, Mbappé consistently delivers on the biggest stages and remains one of the most unstoppable attackers in the game.

At number two is Cristiano Ronaldo. Even after two decades at the highest level, Ronaldo continues to break records and score goals. With over 900 career goals, multiple Champions League titles, league championships across different countries, and countless individual awards, his legacy as one of football's greatest icons is unquestionable.

And at number one is Lionel Messi. From winning countless league titles and Champions League trophies to finally lifting the FIFA World Cup, Messi has completed football's greatest collection of achievements. His vision, dribbling, passing, finishing, and ability to decide matches have made him a player unlike any other. For many fans, he is simply the greatest footballer of all time.

Choosing only five players is never easy. Stars like Kevin De Bruyne, Mohamed Salah, Vinícius Júnior, Rodri, Harry Kane, and Lamine Yamal have all produced incredible performances and could easily be included in this conversation.

But based on consistency, influence, achievements, and overall impact on modern football, these five players stand above the rest and have defined this generation of the beautiful game."""

TITLE = "Top 5 Best Footballers of Current Era"

# ─── Scene Definitions ──────────────────────────────────────────────────────────
SCENES = [
    {
        "text": "Every football generation has its legends. But today's era has produced some of the most complete, talented, and dominant players the sport has ever seen. Here are the top five best footballers of the current era.",
        "duration": 8.0,
        "subject": "football legends",
        "query": "best footballers 2024 2025 current era highlights",
        "fallback_query": "top football players current generation",
    },
    {
        "text": "At number five is Erling Haaland. The Norwegian goal machine has rewritten scoring records wherever he's played.",
        "duration": 7.0,
        "subject": "Erling Haaland",
        "query": "Erling Haaland best goals highlights 2024",
        "fallback_query": "Haaland goals compilation",
    },
    {
        "text": "With incredible strength, blistering pace, and deadly finishing, Haaland is one of the most feared strikers in world football.",
        "duration": 6.0,
        "subject": "Erling Haaland",
        "query": "Erling Haaland powerful goals speed strength",
        "fallback_query": "Haaland unstoppable goals",
    },
    {
        "text": "At number four is Jude Bellingham. Still incredibly young, Bellingham has already become one of the world's best midfielders.",
        "duration": 7.0,
        "subject": "Jude Bellingham",
        "query": "Jude Bellingham best moments goals highlights",
        "fallback_query": "Bellingham midfield highlights",
    },
    {
        "text": "He combines vision, technical ability, defensive work rate, and leadership beyond his years, making him the complete modern midfielder.",
        "duration": 6.0,
        "subject": "Jude Bellingham",
        "query": "Jude Bellingham skill assists dribbling",
        "fallback_query": "Bellingham technical skills",
    },
    {
        "text": "Taking the third spot is Kylian Mbappé. Few players in football history possess his combination of speed, skill, and finishing.",
        "duration": 7.0,
        "subject": "Kylian Mbappé",
        "query": "Kylian Mbappe best goals speed highlights",
        "fallback_query": "Mbappe goals compilation",
    },
    {
        "text": "Whether it's a World Cup final or a Champions League night, Mbappé consistently delivers on the biggest stages.",
        "duration": 6.0,
        "subject": "Kylian Mbappé",
        "query": "Mbappe World Cup Champions League goals",
        "fallback_query": "Mbappe big game goals",
    },
    {
        "text": "At number two is Cristiano Ronaldo. Even after two decades at the highest level, Ronaldo continues to break records and score goals.",
        "duration": 7.0,
        "subject": "Cristiano Ronaldo",
        "query": "Cristiano Ronaldo best goals 2024 records",
        "fallback_query": "Ronaldo goals highlights",
    },
    {
        "text": "With over 900 career goals, multiple Champions League titles, league championships across different countries, and countless individual awards.",
        "duration": 7.0,
        "subject": "Cristiano Ronaldo",
        "query": "Cristiano Ronaldo 900 goals career highlights",
        "fallback_query": "Ronaldo career best moments",
    },
    {
        "text": "And at number one is Lionel Messi. From winning countless league titles and Champions League trophies to finally lifting the FIFA World Cup.",
        "duration": 7.0,
        "subject": "Lionel Messi",
        "query": "Lionel Messi best goals World Cup highlights",
        "fallback_query": "Messi goals compilation",
    },
    {
        "text": "His vision, dribbling, passing, finishing, and ability to decide matches have made him a player unlike any other.",
        "duration": 6.0,
        "subject": "Lionel Messi",
        "query": "Messi dribbling assists skill goals",
        "fallback_query": "Messi best skills and assists",
    },
    {
        "text": "For many fans, he is simply the greatest footballer of all time.",
        "duration": 5.0,
        "subject": "Lionel Messi",
        "query": "Messi greatest of all time GOAT highlights",
        "fallback_query": "Messi legendary moments",
    },
    {
        "text": "Stars like Kevin De Bruyne, Mohamed Salah, Vinícius Júnior, Rodri, Harry Kane, and Lamine Yamal have all produced incredible performances.",
        "duration": 8.0,
        "subject": "football stars",
        "query": "De Bruyne Salah Vinicius Rodri Kane best goals 2024",
        "fallback_query": "top footballers 2024 highlights",
    },
    {
        "text": "But based on consistency, influence, achievements, and overall impact on modern football, these five players stand above the rest.",
        "duration": 7.0,
        "subject": "football legends",
        "query": "best footballers of all time current era",
        "fallback_query": "top 5 footballers current generation",
    },
]

# ─── Metrics Tracker ────────────────────────────────────────────────────────────
class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.search_start = None
        self.search_end = None
        self.search_queries = []
        self.search_results = {}
        self.download_start = None
        self.download_end = None
        self.clip_attempts = []
        self.concurrency_peak = 0
        self.concurrency_current = 0
        self.total_downloaded = 0
        self.total_failed = 0
        self.errors = []

    def log_search(self, query, results_count, duration):
        with self.lock:
            self.search_queries.append({
                "query": query,
                "results": results_count,
                "duration_s": round(duration, 2),
            })

    def log_download(self, scene_idx, success, path, duration, error=None):
        with self.lock:
            self.clip_attempts.append({
                "scene": scene_idx,
                "success": success,
                "path": path,
                "duration_s": round(duration, 2),
                "error": error,
            })
            if success:
                self.total_downloaded += 1
            else:
                self.total_failed += 1

    def log_concurrency(self, delta):
        with self.lock:
            self.concurrency_current += delta
            self.concurrency_peak = max(self.concurrency_peak, self.concurrency_current)

metrics = Metrics()

# ─── Progress Callback ──────────────────────────────────────────────────────────
def progress_callback(fraction, message):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] [{fraction*100:5.1f}%] {message}")

# ─── Main Test ──────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(f"  INTEGRATION TEST: {TITLE}")
    print(f"  Scenes: {len(SCENES)}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Build inputs
    scene_texts = [s["text"] for s in SCENES]
    scene_durations = [s["duration"] for s in SCENES]

    # Build query overrides
    scene_query_overrides = []
    for i, s in enumerate(SCENES):
        scene_query_overrides.append({
            "scene_number": i + 1,
            "section_subject": s["subject"],
            "scene_subject": s["subject"],
            "subject_query": s["query"],
            "youtube_query": s["query"],
            "search_query": s["query"],
            "alt_search_query": s["fallback_query"],
            "fallback_query": s["fallback_query"],
            "query_variants": [s["query"], s["fallback_query"]],
        })

    cancel_event = threading.Event()

    print("\n[1/3] Starting pipeline...")
    print(f"  Search concurrency: {os.environ.get('VIDZEO_YT_SEARCH_CONCURRENCY', 4)}")
    print(f"  Download concurrency: {os.environ.get('VIDZEO_YT_DOWNLOAD_CONCURRENCY', 8)}")
    print(f"  Fragment concurrency: {os.environ.get('VIDZEO_YT_FRAGMENT_CONCURRENCY', 4)}")
    print(f"  Shared source max_end: {os.environ.get('VIDZEO_YT_SHARED_SOURCE_MAX_END', 90)}s")
    print(f"  Multi-clip source: {os.environ.get('VIDZEO_YT_MULTI_CLIP_SOURCE', '0')}")

    pipeline_start = time.time()

    try:
        results = get_youtube_clips(
            script=SCRIPT,
            title=TITLE,
            num_scenes=len(SCENES),
            scene_durations=scene_durations,
            scene_texts=scene_texts,
            scene_query_overrides=scene_query_overrides,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
    except Exception as e:
        print(f"\n[ERROR] Pipeline crashed: {e}")
        import traceback
        traceback.print_exc()
        return

    pipeline_end = time.time()
    total_time = pipeline_end - pipeline_start

    # ─── Results ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)

    successful = sum(1 for r in results if r is not None)
    failed = sum(1 for r in results if r is None)

    print(f"\n  Total scenes: {len(SCENES)}")
    print(f"  Successful downloads: {successful}")
    print(f"  Failed downloads: {failed}")
    print(f"  Success rate: {successful/len(SCENES)*100:.1f}%")
    print(f"  Total pipeline time: {total_time:.1f}s")
    print(f"  Average time per clip: {total_time/len(SCENES):.1f}s")

    print(f"\n  Per-scene results:")
    for i, (result, scene) in enumerate(zip(results, SCENES)):
        status = "✓" if result else "✗"
        path = result if result else "FAILED"
        print(f"    [{status}] Scene {i+1:2d} ({scene['subject']:25s}): {path}")

    # ─── Metrics ────────────────────────────────────────────────────────────────
    print(f"\n  Concurrency peak: {metrics.concurrency_peak}")
    print(f"  Total clip attempts: {len(metrics.clip_attempts)}")

    if metrics.clip_attempts:
        avg_download_time = sum(a["duration_s"] for a in metrics.clip_attempts) / len(metrics.clip_attempts)
        print(f"  Average download time per attempt: {avg_download_time:.1f}s")

    # ─── File sizes ─────────────────────────────────────────────────────────────
    print(f"\n  Downloaded clips:")
    total_size = 0
    for i, result in enumerate(results):
        if result and os.path.exists(result):
            size_mb = os.path.getsize(result) / (1024 * 1024)
            total_size += size_mb
            print(f"    Scene {i+1:2d}: {size_mb:.1f} MB - {result}")
    print(f"  Total download size: {total_size:.1f} MB")

    # ─── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  TEST COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
