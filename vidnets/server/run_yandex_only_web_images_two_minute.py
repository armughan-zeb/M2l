"""Produce a two-minute video using only Yandex-discovered web images."""

from __future__ import annotations

import json
import os
import sys
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

# No cookies, YouTube, stock APIs, gallery-dl, or synthetic emergency media.
# Yandex's fast curl_cffi lane is enabled; its local browser path remains an
# emergency transport fallback if the HTTP index is challenged.
os.environ["VIDZEO_WEB_ONLY_PROVIDER"] = "yandex"
os.environ["VIDZEO_YANDEX_CURL_IMPERSONATE"] = "1"
os.environ["VIDZEO_YANDEX_BROWSER_FALLBACK"] = "1"
os.environ["VIDZEO_WEB_DISABLE_ROYAL_PREFILTER"] = "1"
os.environ["VIDZEO_WEB_ALLOW_DETERMINISTIC_REUSE"] = "0"
os.environ["VIDZEO_WEB_IMAGE_REUSE_CAP"] = "32"
os.environ["VIDZEO_WEB_CROSS_SCENE_PHASH_THRESHOLD"] = "1"
os.environ["VIDZEO_WEB_SAME_QUERY_CANDIDATES"] = "12"
os.environ["VIDZEO_WEB_VISION_SHORTLIST"] = "12"
os.environ.setdefault("VIDZEO_YANDEX_CACHE_TTL_SECONDS", "900")
os.environ["VIDZEO_WEB_ENABLE_GDL"] = "0"
os.environ["VIDZEO_WEB_COLLECT_ALL_SOURCES"] = "0"
os.environ.setdefault("VIDZEO_WEB_API_WORKERS", "4")
os.environ.setdefault("VIDZEO_WEB_SCENE_WORKERS", "8")
os.environ.setdefault("VIDZEO_WEB_MAX_CANDIDATES", "32")
os.environ.setdefault("VIDZEO_WEB_VERBOSE", "1")
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")

TITLE = "The British Royal Family: Ceremony and Change"
SCRIPT = """The British royal family remains one of the most recognisable institutions in the world, combining centuries of ceremony with a public role that continues to evolve.

At the centre of the system is the Crown’s constitutional position. The monarch is head of state, while elected ministers govern. That division makes royal appearances highly symbolic. A balcony appearance, a carriage procession, or a state opening can turn a formal duty into a shared national moment.

Buckingham Palace is the best-known setting for that public role. Guards, crowds, visiting heads of state, and the changing of the guard connect today’s monarchy with a much longer history. The movement of people around these ceremonies often says as much as the ceremony itself.

Queen Elizabeth the Second shaped the family’s public identity for seven decades. Her reign included jubilees, Commonwealth tours, state visits, and countless walkabouts where members of the royal household met people face to face.

King Charles the Third now carries that work forward. His programme combines traditional ceremonies with long-running interests in the environment, architecture, education, and community partnerships. Each engagement is carefully staged, but the people and places around it give the events their human meaning.

The Prince and Princess of Wales represent another part of the transition. Their public work often focuses on children, mental health, early years support, and the armed forces. Their appearances show how the institution speaks to a younger generation while preserving familiar rituals.

Across the Commonwealth, royal visits bring diplomacy and local history together. A welcoming ceremony, a community project, or a meeting with leaders can explain a relationship more clearly than a headline alone.

The royal family therefore exists in two frames at once: historic ceremony and contemporary public service. Strong documentary images capture both. They show movement, faces, places, and real public events, allowing the visual record to carry the story without turning it into a static presentation."""
OUTPUT_DIR = ROOT / "output" / "yandex_only_web_images_two_minute"
PREFETCH_DIR = ROOT / "temp" / "yandex_unique_prefetch"


def _prefetch_yandex_assets(target_count: int = 7) -> list[dict]:
    """Build a distinct local pool from Yandex HTML results, no cookies."""
    from services.sources.yandex_source import YandexSource
    from services.web_image.hybrid_search import download_bulk
    from PIL import Image

    existing: list[dict] = []
    existing_hashes: set[str] = set()
    if PREFETCH_DIR.is_dir():
        for path in sorted(PREFETCH_DIR.iterdir()):
            if len(existing) >= target_count or not path.is_file():
                continue
            try:
                with Image.open(path) as image:
                    image.verify()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest in existing_hashes:
                    continue
                existing_hashes.add(digest)
                existing.append({
                    "path": str(path.resolve()),
                    "title": "Yandex image",
                    "source_url": "",
                    "preview_url": "",
                    "query_used": "Yandex paginated image result",
                })
            except Exception:
                continue
    if len(existing) >= target_count:
        print(f"YANDEX PREFETCH: reused {len(existing)}/{target_count} distinct validated assets", flush=True)
        return existing[:target_count]

    queries = [
        "Buckingham Palace ceremony public event",
        "King Charles III state opening ceremony",
        "Queen Elizabeth II jubilee balcony appearance",
        "Prince William Princess Catherine public engagement",
        "British royal family carriage procession",
        "British monarchy Commonwealth royal visit",
        "Changing of the Guard Buckingham Palace",
    ]
    source = YandexSource()
    candidates: list[dict] = []
    seen_urls: set[str] = set()
    def fetch(query: str) -> list[dict]:
        return [
            {
                "url": str(getattr(item, "download_url", "") or ""),
                "title": str(getattr(item, "title", "") or ""),
                "source_url": str((getattr(item, "metadata", {}) or {}).get("foreign_url") or ""),
                "preview_url": str(getattr(item, "preview_url", "") or ""),
                "query_used": query,
            }
            for item in source.search(query, limit=20)
        ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, query) for query in queries]
        for future in as_completed(futures):
            for row in future.result():
                key = row["url"].split("#", 1)[0].casefold()
                if key and key not in seen_urls:
                    seen_urls.add(key)
                    candidates.append(row)
    PREFETCH_DIR.mkdir(parents=True, exist_ok=True)
    urls = [row["url"] for row in candidates]
    downloaded = download_bulk(urls, PREFETCH_DIR, max_concurrent=16, timeout_seconds=10.0)
    assets: list[dict] = []
    seen_hashes: set[str] = set()
    for row, (_url, path, _error) in zip(candidates, downloaded):
        if not path or not Path(path).is_file():
            continue
        try:
            with Image.open(path) as image:
                image.verify()
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            assets.append({**row, "path": str(Path(path).resolve())})
            if len(assets) >= target_count:
                break
        except Exception:
            continue
    print(f"YANDEX PREFETCH: {len(assets)}/{target_count} distinct validated assets", flush=True)
    return assets


def progress(fraction: float, message: str) -> None:
    print(f"  [{float(fraction) * 100:5.1f}%] {message}", flush=True)


def main() -> int:
    import app as vidzeo_app

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prefetched_assets = _prefetch_yandex_assets(7)
    if len(prefetched_assets) < 7:
        print("Yandex prefetch did not produce seven distinct assets; aborting without reuse.", flush=True)
        return 2
    config = {
        "type": "long",
        "name": TITLE,
        "title": TITLE,
        "content_title": TITLE,
        "topic_anchor": "British royal family ceremony public service monarchy",
        "_topic_anchor": "British royal family ceremony public service monarchy",
        "script": SCRIPT,
        "image_source": "Web Images",
        # Avoid normalize_generation_config's automatic-media default, which
        # would otherwise relabel the request as Mixed Media before routing.
        "media_mode": "web_images",
        # Keep the V2 scene planner and immutable cardinality checks. The
        # benchmark is source-locked to Yandex, so local decode/safety gates
        # are used instead of a paid remote visual verifier.
        "pipeline_v2_mode": "on",
        "deterministic_web_images_only": True,
        "_yandex_prefetched_assets": prefetched_assets,
        "visual_understanding": False,
        "visual_required": False,
        # This is a transport/diversity stress test. Local safety screening is
        # retained, while the remote semantic verifier is intentionally off so
        # it cannot turn valid Yandex results into neutral slots.
        "strict_retrieval_accuracy": False,
        "allow_emergency_media": False,
        "allow_relevant_stock": False,
        "allow_stock_last_resort": False,
        "cross_source_fallback": False,
        "fail_on_unresolved_media": True,
        "allow_duplicate_media_reuse": False,
        "unique_web_assets_required": True,
        "strict_visual_dedupe": True,
        "scene_mode": "Every 5 Beats",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "render_resolution": (1280, 720),
        "quality": "standard",
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_render_concurrency": 2,
        "output_dir": str(OUTPUT_DIR),
        "visual_verification_frame_count": 5,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "intro_max_shot_seconds": 2.5,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.5,
        "media_deadline_seconds": 900,
        "visual_deadline_seconds": 900,
        "visual_shared_deadline_seconds": 900,
        "auto_export": True,
    }
    started = time.monotonic()
    print(f"YANDEX-ONLY WEB IMAGE RUN — {len(SCRIPT.split())} words", flush=True)
    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)
    result = result if isinstance(result, dict) else {}
    video_path = str(result.get("video_path") or result.get("output_path") or "")
    manifest_path = str(result.get("manifest_path") or "")
    summary = {
        "success": bool(result.get("success") and Path(video_path).is_file()),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "video_path": video_path,
        "manifest_path": manifest_path,
        "duration_seconds": result.get("duration"),
        "title": TITLE,
        "provider_allowlist": "yandex",
        "image_source": "Web Images",
        "cookies_used": False,
        "youtube_used": False,
    }
    (OUTPUT_DIR / "generation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
