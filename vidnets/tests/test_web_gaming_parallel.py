"""Web lane in gaming mode: parallel primary + rescue that cannot stall.

GTA test 11 (job 676bf2cf) finished 0 web images: gaming mode disabled the
primary web batch, so web could only fill YouTube quota misses — and the
rescue wave then burned its whole 175s stage wall waiting on a slow
per-scene universal query while a 60-row shared pool sat unused
(``retrieval_deadline`` on all 7 scenes, 0 downloads). These tests replay
that shape with fake transports and prove the fix:

* the rescue scene skips a redundant per-scene wave when the warm pool is
  already full — even in exhaustive/rescue mode;
* a wave already in flight breaks early once a warm pool is in hand instead
  of waiting out a slow universal future;
* the full 7-scene rescue branch resolves web images instead of timing out;
* gaming mode now runs web as a real parallel primary lane;
* a youtube_quota scene still keeps its YouTube clip when web also has one.
"""
import hashlib
import random
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _server_import_path():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    yield
    sys.path.pop(0)


def _gta_row(index):
    """One shared-batch row shaped like the test-11 audit rows."""
    return {
        "url": f"https://example.test/gta-vi-{index}.jpg",
        "image_url": f"https://example.test/gta-vi-{index}.jpg",
        "preview_url": f"https://example.test/gta-vi-{index}-thumb.jpg",
        "thumbnail_url": f"https://example.test/gta-vi-{index}-thumb.jpg",
        "source_url": f"https://example.test/gta-vi-{index}.jpg",
        "page_url": f"https://example.test/gta-vi-{index}.jpg",
        "title": "Grand Theft Auto VI gameplay screenshot Vice City",
        "description": "Grand Theft Auto VI gameplay in Vice City",
        "tags": "Grand Theft Auto VI Vice City gameplay",
        "source": "universal_scraper",
        "provider": "universal_yandex",
        "query": "Grand Theft Auto VI",
        "domain": "example.test",
        "width": 0,
        "height": 0,
        "metadata_pending": True,
    }


def _gta_shared_pool(count=60):
    return [_gta_row(index) for index in range(count)]


def _write_noise_png(path: Path, seed: str) -> Path:
    """A decodable 1280x720 image whose pixels differ per URL (phash dedup
    must see distinct assets across scenes, and the resolver's validator
    rejects files under 15KB or under 640px on the long edge). Seeded numpy
    noise keeps generation fast; the .png suffix is kept for the helper name
    but the bytes are JPEG, which the validator accepts."""
    import numpy as np
    from PIL import Image

    digest = hashlib.md5(seed.encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:4], "little"))
    array = rng.integers(0, 256, size=(720, 1280, 3), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, mode="RGB").save(path, format="JPEG", quality=85)
    return path


def _fake_downloader(items, output_dir, max_concurrent, timeout_seconds,
                     download_cache=None, download_cache_lock=None):
    """Stand in for the network transport: every URL becomes a local file."""
    output_dir = Path(output_dir)
    results = []
    for item in items:
        url = str(item.get("url") or "")
        name = hashlib.md5(url.encode()).hexdigest()[:16] + ".jpg"
        path = _write_noise_png(output_dir / name, url)
        results.append((url, path, ""))
    return results


def _resolve_scene_for_gaming_test(monkeypatch, tmp_path, *, scene_index,
                                   discover, shared_pool, env=None,
                                   exhaustive_fallback=True,
                                   prioritize_director_query=False):
    """Drive one _resolve_scene with a fake transport and a warm pool."""
    from services.web_image import resolver
    from services.web_image.source_broker import SourceTelemetry

    monkeypatch.setenv("VIDZEO_UNIVERSAL_SCRAPER_SCENE_FALLBACK", "1")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(resolver, "_discover_source_query", discover)
    monkeypatch.setattr(resolver, "_download_candidate_batch", _fake_downloader)
    monkeypatch.setattr(
        resolver,
        "_enabled_fast_sources",
        lambda: [
            ("universal_scraper", "universal"),
            ("yandex", "api"),
            ("flickr_api", "flickr_api"),
        ],
    )
    with resolver._CACHE_LOCK:
        resolver._SEARCH_QUERY_CACHE.clear()
    with resolver._WEB_WAVE_PROGRESS_LOCK:
        resolver._WEB_WAVE_PROGRESS.clear()
    telemetry = SourceTelemetry()
    started = time.monotonic()
    result = resolver._resolve_scene(
        scene_index,
        "Grand Theft Auto VI returns to Vice City with new gameplay.",
        ["Grand Theft Auto VI Vice City gameplay"],
        None,
        tmp_path,
        "Grand Theft Auto VI",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        source_telemetry=telemetry,
        deadline_at=time.monotonic() + 120.0,
        shared_pool_query="Grand Theft Auto VI",
        shared_pool_candidates=list(shared_pool),
        global_topic="Grand Theft Auto VI: Return to Vice City",
        exhaustive_fallback=exhaustive_fallback,
        prioritize_director_query=prioritize_director_query,
    )
    return result, telemetry, time.monotonic() - started


# ── Fix 1: a full warm pool skips the redundant per-scene wave ──────────

def test_rescue_scene_with_full_warm_pool_skips_per_scene_wave(monkeypatch, tmp_path):
    """Test-11 shape, exhaustive/rescue mode: 60 shared rows in hand, so no
    per-scene provider wave is launched at all — the scene ranks/downloads
    the pool instead of burning the stage wall waiting for universal."""
    calls = []

    def discover(source_name, source_type, query, gallery_cmd=None,
                 max_timeout=None, wait_until_complete=False):
        calls.append(source_name)
        return []

    result, _telemetry, elapsed = _resolve_scene_for_gaming_test(
        monkeypatch, tmp_path, scene_index=0, discover=discover,
        shared_pool=_gta_shared_pool(60),
    )
    assert calls == [], f"rescue scene re-fanned out to {calls} despite a full warm pool"
    assert result is not None and result.get("path"), "warm-pool scene did not resolve"
    assert Path(str(result["path"])).is_file()
    assert int(result.get("candidate_pool_size") or 0) >= 1
    assert elapsed < 30.0, f"pool-backed scene took {elapsed:.1f}s"


# ── Fix 2: a wave in flight breaks early once a warm pool is in hand ─────

def test_wave_breaks_for_warm_pool_while_universal_still_running(monkeypatch, tmp_path):
    """A director query forces the per-scene wave (fix 1 cannot apply), the
    universal lane sleeps 3s, and a fast lane completes first. The wave must
    break on the warm pool instead of waiting out universal — the test-11
    burn was exactly this wait lasting until the 175s stage deadline."""
    calls = []

    def discover(source_name, source_type, query, gallery_cmd=None,
                 max_timeout=None, wait_until_complete=False):
        calls.append(source_name)
        if source_name == "universal_scraper":
            time.sleep(3.0)
            return [_gta_row(999)]
        if source_name == "yandex":
            return [_gta_row(1000)]
        return []

    result, _telemetry, elapsed = _resolve_scene_for_gaming_test(
        monkeypatch, tmp_path, scene_index=1, discover=discover,
        shared_pool=_gta_shared_pool(60),
        prioritize_director_query=True,
    )
    assert "universal_scraper" in calls, "wave was never launched"
    assert elapsed < 2.0, (
        f"wave waited {elapsed:.2f}s for the slow universal lane despite a warm pool"
    )
    assert result is not None and result.get("path"), "warm-pool scene did not resolve"
    assert Path(str(result["path"])).is_file()


def test_wave_without_warm_pool_still_waits_for_universal(monkeypatch, tmp_path):
    """Guard for fix 2: with NO warm pool the universal exemption is
    unchanged — a 0.6s universal batch is still awaited past a 0.15s query
    deadline instead of being abandoned."""
    from services.web_image import resolver

    def discover(source_name, source_type, query, gallery_cmd=None,
                 max_timeout=None, wait_until_complete=False):
        if source_name == "universal_scraper":
            time.sleep(0.6)
            return [_gta_row(0)]
        return []

    _result, telemetry, elapsed = _resolve_scene_for_gaming_test(
        monkeypatch, tmp_path, scene_index=2, discover=discover,
        shared_pool=[],
        exhaustive_fallback=False,
        env={"VIDZEO_WEB_QUERY_DEADLINE_SECONDS": "0.15"},
    )
    providers = telemetry.snapshot().get("providers", {})
    assert providers.get("universal_scraper", {}).get("discovered", 0) >= 1
    assert elapsed >= 0.6, f"wave returned after {elapsed:.2f}s, universal needed 0.6s"
    assert resolver.web_wave_stalled_for_scene(3) is False


# ── Branch replay: the test-11 rescue shape resolves web > 0 ─────────────

def test_test11_replay_rescue_branch_resolves_web(monkeypatch, tmp_path):
    """Replay of job 676bf2cf's web branch: 7 scenes, a 60-candidate shared
    batch, exhaustive rescue, 175s stage wall, slow per-scene universal
    lanes. Before the fix this resolved 0/7 with every scene on
    ``retrieval_deadline``; after the fix the branch resolves web images
    well inside the wall."""
    from services.web_image import resolver

    monkeypatch.setenv("VIDZEO_UNIVERSAL_SCRAPER_SCENE_FALLBACK", "1")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    monkeypatch.setattr(
        resolver, "_warm_shared_discovery_pool",
        lambda **kwargs: {
            "query": "Grand Theft Auto VI",
            "candidates": 60,
            "candidate_rows": _gta_shared_pool(60),
            "providers_completed": 3,
            "providers_pending": 0,
        },
    )

    def discover(source_name, source_type, query, gallery_cmd=None,
                 max_timeout=None, wait_until_complete=False):
        if source_name == "universal_scraper":
            time.sleep(3.0)
            return [_gta_row(999)]
        return []

    monkeypatch.setattr(resolver, "_discover_source_query", discover)
    monkeypatch.setattr(resolver, "_download_candidate_batch", _fake_downloader)
    with resolver._CACHE_LOCK:
        resolver._SEARCH_QUERY_CACHE.clear()

    scene_texts = [
        "Grand Theft Auto VI returns to Vice City with new gameplay.",
        "Lucia and Jason drive through Leonida in a stolen car.",
        "Rockstar began work on GTA VI after GTA V launched in 2013.",
        "The trailer broke records with millions of views in a day.",
        "Vice City neon streets and beaches fill the open world.",
        "The game was delayed twice before its 2026 release window.",
        "Fans waited over a decade for the next Grand Theft Auto.",
    ]
    started = time.monotonic()
    result = resolver.resolve_web_images_for_video_fast(
        script=" ".join(scene_texts),
        title="Grand Theft Auto VI",
        scene_texts=scene_texts,
        scene_durations=[6.0] * len(scene_texts),
        output_namespace=f"gaming-parallel-replay-{tmp_path.name}",
        fast_mode=True,
        max_scene_workers=2,
        exhaustive_fallback=True,
        stage_deadline_seconds=175.0,
    )
    elapsed = time.monotonic() - started
    audit = result.get("audit") or {}
    resolved = int(audit.get("resolved") or 0)
    assert resolved > 0, (
        f"test-11 replay resolved 0/{len(scene_texts)} web scenes "
        f"(audit: {audit.get('scenes')})"
    )
    assert elapsed < 60.0, f"rescue branch took {elapsed:.1f}s (wall was 175s)"
    assert all(Path(p).is_file() for p in result.get("image_paths") or [] if p)


# ── (a): gaming mode runs web as a real parallel primary lane ────────────

def test_gaming_mode_web_primary_lane_is_live(monkeypatch, tmp_path):
    """Before the fix the gaming web index set was empty, so the primary web
    lane was a disabled lane and web could only appear via rescue. In gaming
    mode the coordinator must now receive a real shared-batch web lane."""
    import services.pipeline_v2_media_adapter as adapter
    from unittest import mock

    captured = {}
    web_runner_calls = []

    def fake_coordinator(rows, **kwargs):
        captured.update(kwargs)
        captured["rows"] = rows
        return {"metadata": {"media_resolution_v2": []}}, {}

    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", fake_coordinator)
    config = {
        "_gaming_mode": True,
        "mixed_media_weights": {
            "Web Images": 0.20,
            "Stock Media": 0.0,
            "YouTube Clips": 0.80,
        },
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "visual_understanding": False,
        "global_media_guarantee": False,
        "_topic_anchor": "Grand Theft Auto VI",
    }
    scenes = [{
        "scene_id": "gta-beat-0",
        "order": 0,
        "duration": 6.0,
        "text": "Grand Theft Auto VI returns to Vice City with new gameplay.",
    }]
    narration = scenes[0]["text"]

    def web_runner(*args, **kwargs):
        web_runner_calls.append(True)
        return {"web_result": {"image_metadata": [], "audit": {}}}

    adapter.resolve_existing_provider_branches_v2(
        config,
        {"scene_contracts": scenes},
        title="Grand Theft Auto VI: Return to Vice City",
        script=narration,
        render_resolution=(1280, 720),
        youtube_runner=lambda *a, **k: [],
        web_runner=web_runner,
        stock_runner=lambda *a, **k: {},
        snapshotter=lambda paths, **kwargs: paths,
        neutral_asset_factory=lambda *a, **k: None,
        report_progress=lambda *a, **k: None,
    )

    web_lane = captured.get("web_lane")
    assert web_lane is not None
    assert getattr(web_lane, "shared_batch", False) is True, (
        "gaming web lane is not a live shared-batch lane"
    )
    # Invoking the lane must start the primary web batch (not a disabled lane).
    import threading as _threading

    web_lane({"scene_index": 0, "order": 0}, _threading.Event())
    assert web_runner_calls, "gaming web lane never ran its primary web batch"


# ── (iii): a good YouTube clip is NOT displaced by web ───────────────────

def test_quota_scene_keeps_youtube_clip_over_web_image(tmp_path):
    """A youtube_quota scene with both an accepted clip and an accepted web
    image must resolve to YouTube — the parallel web lane fills YT misses,
    it never steals a quota scene's clip."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip_file = tmp_path / "gta_clip.mp4"
    clip_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000)
    web_file = tmp_path / "gta_web.jpg"
    web_file.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)

    rows = [{
        "scene_index": 0,
        "scene_id": "scene-0",
        "container_id": "c-0",
        "text": "Grand Theft Auto VI returns to Vice City with new gameplay.",
        "duration": 6.0,
        "source_policy": {"youtube_quota": True},
    }]
    yt_candidate = {
        "candidate_id": "yt-cand-1",
        "path": str(clip_file),
        "media_type": "video",
        "source": "youtube",
        "provider": "youtube",
        "final_score": 85.0,
        "visual_decision": {"accepted": True, "status": "accepted", "score": 85.0},
    }
    web_candidate = {
        "candidate_id": "web-cand-1",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 90.0,
        "visual_decision": {"accepted": True, "status": "accepted", "score": 90.0},
    }
    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [yt_candidate],
        web_lane=lambda s, e: [web_candidate],
        stock_lane=lambda s, e: [],
        max_workers=1,
        deadline_seconds=10.0,
    )
    assert len(res) == 1
    assert res[0].provider == "youtube", (
        f"quota scene resolved to {res[0].provider} instead of its YouTube clip"
    )
    assert res[0].status == "accepted"
