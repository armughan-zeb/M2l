"""Overlap-tolerant verified-cut cache lookups (Gap 2: YT prep-cache key).

The exact cache key is (video_id, start, duration). A later job often
re-splits the same script into different scenes (Whisper split live vs the
fallback split at prep time), so the same source moment is requested with a
different duration and the exact key misses even though the prep cut covers
the requested window. These tests pin the tolerant fallback in
services.youtube_media_cache.get_verified_cut:

- exact hits still work and are preferred over tolerant hits;
- a cached cut whose window covers the requested window is a hit;
- a cached cut shorter than requested is NOT a hit (a fresh download is
  rejected below the requested duration, and a cache hit must not smuggle
  a short clip past that contract);
- the fallback never crosses videos, never reuses a pre-v4 row, and still
  respects TTL;
- the Panama replay shapes (job f66daa53, 6 prep cuts): 3/6 hit, 3/6 miss.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from services import youtube_media_cache as cache

FAKE_MP4 = b"xxxxftypisom" + (b"x" * 12_000)


@pytest.fixture
def cut_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(tmp_path / "cut-cache"))
    cache._INITIALIZED.clear()
    source = tmp_path / "source.mp4"
    source.write_bytes(FAKE_MP4)
    return tmp_path, source


def _store(source: Path, video_id: str, start: float, duration: float) -> str:
    stored = cache.put_verified_cut(video_id, start, duration, str(source))
    assert stored and Path(stored).is_file()
    return stored


def test_exact_hit_still_works(cut_cache):
    _, source = cut_cache
    stored = _store(source, "video123", 12.345, 4.0)
    hit = cache.get_verified_cut("video123", 12.345, 4.0)
    assert hit is not None
    assert hit["path"] == stored
    # Base code has no tolerant_hit key; the default keeps this assertion
    # meaningful on both sides of the fix.
    assert hit.get("tolerant_hit", False) is False


def test_tolerant_hit_when_prep_cut_covers_shorter_live_scene(cut_cache):
    # Panama prep scene 1: cut (98.119, 6.75); live scene 0 requests
    # (98.12, 5.77) — same moment, shorter Whisper duration.
    _, source = cut_cache
    stored = _store(source, "YzFGkdPmDZU", 98.119, 6.75)
    hit = cache.get_verified_cut("YzFGkdPmDZU", 98.12, 5.77)
    assert hit is not None
    assert hit["path"] == stored
    assert hit["tolerant_hit"] is True
    assert hit["coverage"] == pytest.approx(1.0)
    assert hit["overlap_seconds"] == pytest.approx(5.77)


def test_tolerant_hit_with_start_jitter_within_tolerance(cut_cache):
    _, source = cut_cache
    stored = _store(source, "vid", 100.0, 5.0)
    hit = cache.get_verified_cut("vid", 100.4, 4.0)
    assert hit is not None
    assert hit["path"] == stored
    assert hit["tolerant_hit"] is True


def test_no_hit_when_cached_cut_is_shorter_than_requested(cut_cache):
    # Panama prep scene 8: cut (2208.08, 4.95); merged live scene 6
    # requests (2208.08, 7.69). The prep cut cannot cover the scene.
    _, source = cut_cache
    _store(source, "2gEmYKeziek", 2208.08, 4.95)
    assert cache.get_verified_cut("2gEmYKeziek", 2208.08, 7.69) is None


def test_no_hit_on_a_different_video(cut_cache):
    _, source = cut_cache
    _store(source, "videoA", 10.0, 6.0)
    assert cache.get_verified_cut("videoB", 10.0, 5.0) is None


def test_no_hit_when_re_match_picks_a_different_moment(cut_cache):
    # Panama prep scene 3: cut (637.8, 10.35); the live re-match for the
    # same text picks (685.64, 8.85) — no overlap, so no key can hit it.
    _, source = cut_cache
    _store(source, "ZdFXSbQWa4w", 637.8, 10.35)
    assert cache.get_verified_cut("ZdFXSbQWa4w", 685.64, 8.85) is None


def test_no_tolerant_hit_on_pre_v4_row(cut_cache):
    tmp_path, source = cut_cache
    _store(source, "vid", 10.0, 6.0)
    # A v2 row (admitted at 70% of the requested duration) covering the
    # same window must stay unusable under the v4 full-duration contract.
    db_path = Path(str(tmp_path / "cut-cache" / "cuts.sqlite3"))
    v2_target = tmp_path / "cut-cache" / "v2row.mp4"
    v2_target.write_bytes(FAKE_MP4)
    now = time.time()
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "INSERT INTO youtube_verified_cuts"
            " (cache_key, video_id, start_seconds, duration_seconds, media_path,"
            "  metadata_json, created_at, last_used_at, schema_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "v2row", "videoV2", 20.0, 9.0, str(v2_target),
                json.dumps({}), now, now, "youtube-cut-v2-sharp-source",
            ),
        )
    assert cache.get_verified_cut("videoV2", 20.0, 5.0) is None


def test_tolerant_hit_respects_ttl(cut_cache):
    tmp_path, source = cut_cache
    _store(source, "vid", 10.0, 6.0)
    db_path = Path(str(tmp_path / "cut-cache" / "cuts.sqlite3"))
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE youtube_verified_cuts SET created_at = ?",
            (time.time() - 2 * 604800,),
        )
    assert cache.get_verified_cut("vid", 10.0, 5.0) is None


def test_exact_hit_is_preferred_over_tolerant_hit(cut_cache):
    _, source = cut_cache
    exact = _store(source, "vid", 50.0, 4.0)
    _store(source, "vid", 49.9, 6.0)
    hit = cache.get_verified_cut("vid", 50.0, 4.0)
    assert hit is not None
    assert hit["path"] == exact
    assert hit.get("tolerant_hit", False) is False


def test_panama_replay_shapes(cut_cache):
    """The 6 Panama prep cuts against the 6 live requests the real moment
    matcher returns (cached transcripts, job f66daa53): 3 hits, 3 misses.
    The hits are the pure duration mismatches; the misses are re-match
    divergences (0% window overlap) or a merged scene longer than the cut.
    """
    _, source = cut_cache
    prep_cuts = [
        ("YzFGkdPmDZU", 98.119, 6.75),
        ("-1jOqORJXr4", 0.0, 3.0),
        ("ZdFXSbQWa4w", 637.8, 10.35),
        ("qPKAtMPt40k", 563.48, 3.0),
        ("2gEmYKeziek", 2208.08, 4.95),
        ("YzFGkdPmDZU", 166.8, 4.05),
    ]
    for video_id, start, duration in prep_cuts:
        _store(source, video_id, start, duration)
    live_requests = [
        ("YzFGkdPmDZU", 98.12, 5.77, True),
        ("-1jOqORJXr4", 0.0, 2.31, True),
        ("ZdFXSbQWa4w", 685.64, 8.85, False),
        ("qPKAtMPt40k", 563.48, 1.92, True),
        ("2gEmYKeziek", 1195.88, 7.69, False),
        ("YzFGkdPmDZU", 47.32, 7.69, False),
    ]
    hits = 0
    for video_id, start, duration, expect_hit in live_requests:
        hit = cache.get_verified_cut(video_id, start, duration)
        assert (hit is not None) == expect_hit, (video_id, start, duration)
        hits += bool(hit)
    assert hits == 3
