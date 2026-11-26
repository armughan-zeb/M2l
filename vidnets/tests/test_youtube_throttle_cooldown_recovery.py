"""Tests for YouTube search-burst isolation, fast 0-byte stall detection,
session/client fallback, and throttle cooldown probe + wave retry.
"""
import http.cookiejar
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from services import youtube_pipeline_v2 as ytv2


def _make_jar():
    jar = http.cookiejar.CookieJar()
    return jar


def test_anonymous_first_search_preserves_authenticated_session(tmp_path, monkeypatch):
    """Search runs anonymously first so the signed-in session isn't heated
    before downloads; missing pools fall back to the authenticated session."""
    search_calls = []

    def fake_search_pools(groups, *, search_timeout_seconds=None, session_jar=None, cancellation=None, wall_cap_seconds=None):
        search_calls.append((list(groups.keys()), session_jar))
        out = {}
        keys = list(groups.keys())
        for idx, key in enumerate(keys):
            if session_jar is None and idx == 0:
                out[key] = [{"id": "vid_a", "title": "Flying Scotsman steam locomotive", "url": "https://www.youtube.com/watch?v=vid_a", "duration": 60}]
            elif session_jar is not None:
                out[key] = [{"id": "vid_b", "title": "Shinkansen bullet train", "url": "https://www.youtube.com/watch?v=vid_b", "duration": 60}]
            else:
                out[key] = []
        return out

    fake_jar = _make_jar()
    monkeypatch.setenv("VIDZEO_YT_SEARCH_ANONYMOUS_FIRST", "1")
    monkeypatch.setenv("VIDZEO_YT_V2_SKIP_DOWNLOAD", "1")
    monkeypatch.setenv("VIDZEO_YT_RANGED_MOMENTS", "0")
    monkeypatch.setattr(ytv2, "_search_container_pools", fake_search_pools)
    monkeypatch.setattr(ytv2, "_get_authenticated_youtube_session", lambda **kw: fake_jar)

    res = ytv2.get_youtube_clips(
        script="1. Flying Scotsman steam locomotive. 2. Shinkansen bullet train.",
        title="Top 2 Fastest Trains: Flying Scotsman vs Shinkansen",
        num_scenes=2,
        scene_texts=["Flying Scotsman steam locomotive departs", "Shinkansen bullet train races"],
        scene_query_overrides=[
            {"section_subject": "Flying Scotsman", "container_id": "c1", "query": "Flying Scotsman"},
            {"section_subject": "Shinkansen", "container_id": "c2", "query": "Shinkansen bullet train"},
        ],
        output_dir=str(tmp_path),
    )
    assert len(search_calls) == 2
    assert search_calls[0][1] is None
    assert len(search_calls[0][0]) == 2
    assert search_calls[1][1] is fake_jar
    # Only the single empty pool from pass 1 was retried with the authenticated session.
    assert search_calls[1][0] == [search_calls[0][0][1]]
    assert res.audit["metadata_selected"] == 2


def test_download_with_session_rotates_on_zero_byte_stall(tmp_path):
    """A zero-byte stall marks the primary account throttled and rotates to
    the next verified account in the pool."""
    attempts = []
    marked = {}
    backup = _make_jar()

    class _Pool:
        def mark_throttled(self, index, reason=""):
            marked["index"] = index
            marked["reason"] = reason

        def next_verified(self, after=-1):
            return (1, backup)

    class _YDL:
        def __init__(self, opts):
            self.opts = opts
            self.cookiejar = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            attempts.append(list(self.opts["extractor_args"]["youtube"]["player_client"]))
            if len(attempts) == 1:
                raise TimeoutError("youtube_zero_byte_stall")

    class _Mod:
        def YoutubeDL(self, opts):
            return _YDL(opts)

    jar = _make_jar()
    jar._vidzeo_session_pool = _Pool()
    jar._vidzeo_session_index = 0
    reset_calls = []

    ytv2._download_with_youtube_session(
        _Mod(),
        {},
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        jar,
        None,
        tmp_path,
        "raw_",
        on_retry_reset=lambda: reset_calls.append(True),
    )
    assert marked == {"index": 0, "reason": "youtube-throttled"}
    assert len(attempts) == 2
    assert len(reset_calls) == 1


@pytest.mark.parametrize("failure", [
    TimeoutError("youtube_zero_byte_stall"),
    ytv2.YouTubeExtractionTimeout("youtube_extraction_timeout_after_40s"),
])
def test_download_with_session_falls_back_to_public_android(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(ytv2, "_ANON_DOWNLOAD_THROTTLE_UNTIL", 0.0)
    clients_seen = []
    throttle_marks = []

    class _Pool:
        def mark_throttled(self, index, reason=""):
            throttle_marks.append((index, reason))

        def next_verified(self, after=-1):
            return (-1, None)

    class _YDL:
        def __init__(self, opts):
            self.opts = opts
            self.cookiejar = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            clients_seen.append(list(self.opts["extractor_args"]["youtube"]["player_client"]))
            if len(clients_seen) == 1:
                raise failure

    class _Mod:
        def YoutubeDL(self, opts):
            return _YDL(opts)

    jar = _make_jar()
    jar._vidzeo_session_pool = _Pool()
    jar._vidzeo_session_index = 0
    ytv2._download_with_youtube_session(
        _Mod(),
        {},
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        jar,
        None,
        tmp_path,
        "raw_",
    )
    assert clients_seen == [["web", "tv_samsung"], ["android"]]
    if isinstance(failure, ytv2.YouTubeExtractionTimeout):
        assert throttle_marks == []
    else:
        assert throttle_marks == [(0, "youtube-throttled")]


def test_zero_byte_watchdog_kills_hung_ffmpeg_child(tmp_path):
    """_FFmpegStallWatchdog detects a 0-byte child after zero_byte_grace_seconds
    and sets zero_byte_stalled=True."""
    import subprocess
    ffmpeg_bin = ytv2._ffmpeg_bin("ffmpeg")
    if not Path(ffmpeg_bin).exists() and ffmpeg_bin == "ffmpeg":
        import shutil
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg binary not installed")

    watch_dir = tmp_path / "zb"
    watch_dir.mkdir()
    raw_prefix = "clip_zb.raw."
    # Spawn an inert ffmpeg process whose cmdline contains raw_prefix and http
    proc = subprocess.Popen(
        [
            ffmpeg_bin, "-hide_banner", "-loglevel", "error",
            "-re", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=30",
            "-metadata", f"title=http://example.test/{raw_prefix}mp4",
            "-f", "null", "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    watchdog = ytv2._FFmpegStallWatchdog(
        watch_dir, raw_prefix, stall_seconds=60.0, zero_byte_grace_seconds=0.5
    )
    watchdog._POLL_SECONDS = 0.2
    watchdog._zero_byte_grace_seconds = 0.5
    watchdog.start()
    try:
        deadline = time.monotonic() + 10.0
        while not watchdog.fired and time.monotonic() < deadline:
            time.sleep(0.1)
        assert watchdog.fired is True
        assert watchdog.zero_byte_stalled is True
        proc.wait(timeout=5)
    finally:
        watchdog.stop()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_cooldown_probe_and_wave_retry_recovers_after_throttle_cools(tmp_path, monkeypatch):
    """Wave 1 stalls at 0 bytes for all 3 scenes under a 300s hard timeout;
    probe #1 still stalls, probe #2 succeeds (window cooled), and the cooled
    wave retry recovers all 3/3 clips without being clamped by a 548s reserve."""
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_RETRY", "1")
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_INITIAL_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_PROBE_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_MAX_PROBES", "5")
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_RETRY_WAVE_RESERVE_SECONDS", "45")
    monkeypatch.setenv("VIDZEO_YT_RANGED_MOMENTS", "0")
    monkeypatch.setenv("VIDZEO_YT_OVERLAY_SCREEN", "0")

    pools_data = {
        "cars": [
            {
                "id": f"vid_{i}",
                "title": f"Classic car {i} driving",
                "url": f"https://www.youtube.com/watch?v=vid_{i}",
                "duration": 90.0,
            }
            for i in range(3)
        ]
    }
    monkeypatch.setattr(ytv2, "_search_container_pools", lambda *a, **kw: pools_data)
    monkeypatch.setattr(ytv2, "_get_authenticated_youtube_session", lambda **kw: None)
    monkeypatch.setattr(ytv2, "_screen_downloaded_motion", lambda *a, **kw: {"checked": True, "reject": False})
    monkeypatch.setattr(ytv2, "_clean_downloaded_clip_overlays", lambda path, **kw: (path, {"checked": True, "clean": True}))

    call_state = {"calls": 0}

    def fake_download(candidate, output_path, start, duration, **kwargs):
        call_state["calls"] += 1
        # First 3 calls = Wave 1 (all stall at 0 bytes);
        # Call 4 = Probe #1 (still throttled);
        # Calls 5+ = Probe #2 (cooled!) + Cooled wave for remaining scenes.
        if call_state["calls"] <= 4:
            candidate["_transport_failure"] = "zero_byte_throttle"
            return False
        Path(output_path).write_bytes(b"\x00" * 16384)
        return True

    monkeypatch.setattr(ytv2, "_download_clip", fake_download)

    res = ytv2.get_youtube_clips(
        script="Car one. Car two. Car three.",
        title="Classic Cars",
        num_scenes=3,
        scene_texts=["Car zero drives", "Car one drives", "Car two drives"],
        scene_query_overrides=[{"section_subject": "cars", "query": "classic car"}] * 3,
        output_dir=str(tmp_path),
        download_candidate_limit=1,
        max_download_attempts_per_scene=1,
        hard_timeout_seconds=300.0,
    )
    assert all(res), f"Expected all 3 scenes recovered, got {list(res)}"
    assert res.audit["cooldown_retry"]["triggered"] is True
    assert res.audit["cooldown_retry"]["cooled"] is True
    assert res.audit["cooldown_retry"]["probes"] == 2
    assert res.audit["cooldown_retry"]["recovered"] == 3


def test_cooldown_retry_skips_when_only_content_rejects(tmp_path, monkeypatch):
    """Content/overlay rejections must not wait through a cooldown loop."""
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_RETRY", "1")
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_INITIAL_WAIT_SECONDS", "0.05")
    monkeypatch.setenv("VIDZEO_YT_RANGED_MOMENTS", "0")

    pools_data = {
        "cars": [
            {"id": "vid_0", "title": "Classic car", "url": "https://www.youtube.com/watch?v=vid_0", "duration": 90.0}
        ]
    }
    monkeypatch.setattr(ytv2, "_search_container_pools", lambda *a, **kw: pools_data)
    monkeypatch.setattr(ytv2, "_get_authenticated_youtube_session", lambda **kw: None)
    monkeypatch.setattr(ytv2, "_screen_downloaded_motion", lambda *a, **kw: {"checked": True, "reject": True, "reason": "static"})

    def fake_download(candidate, output_path, start, duration, **kwargs):
        Path(output_path).write_bytes(b"\x00" * 16384)
        return True

    monkeypatch.setattr(ytv2, "_download_clip", fake_download)

    res = ytv2.get_youtube_clips(
        script="Car zero.",
        title="Classic Cars",
        num_scenes=1,
        scene_texts=["Car zero drives"],
        scene_query_overrides=[{"section_subject": "cars", "query": "classic car"}],
        output_dir=str(tmp_path),
        download_candidate_limit=1,
        max_download_attempts_per_scene=1,
        hard_timeout_seconds=300.0,
    )
    assert res[0] is None
    assert res.audit["cooldown_retry"]["triggered"] is False


@pytest.mark.parametrize("blocked", [False, True])
def test_muse_route_outage_uses_reserved_wall_without_retrying_a_blocked_runtime(tmp_path, monkeypatch, blocked):
    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED", raising=False)
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_RETRY", "1")
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_INITIAL_WAIT_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_YT_COOLDOWN_PROBE_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_YT_RANGED_MOMENTS", "0")
    monkeypatch.setattr(ytv2, "_get_authenticated_youtube_session", lambda **kw: None)
    monkeypatch.setattr(ytv2, "_screen_downloaded_motion", lambda *a, **kw: {"checked": True, "reject": False})
    monkeypatch.setattr(ytv2, "_clean_downloaded_clip_overlays", lambda path, **kw: (path, {"checked": True}))
    monkeypatch.setattr(ytv2, "_search_container_pools", lambda *a, **kw: {
        "cars": [{
            "id": f"classic0{i}", "title": f"Classic car {i} driving",
            "url": f"https://www.youtube.com/watch?v=classic0{i}", "duration": 90.0,
        } for i in range(3)],
    })
    monotonic = time.monotonic
    elapsed = 0.0
    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + elapsed)
    attempts = []

    def fake_download(candidate, path, start, duration, **kwargs):
        nonlocal elapsed
        attempts.append(candidate["id"])
        if len(attempts) == 1:
            elapsed += 60.0  # The first wave used all of its 60-second wall.
            candidate["_transport_failure"] = (
                "network_disabled_by_sandbox" if blocked else "outbound_network_unavailable"
            )
            return False
        path.write_bytes(b"\x00" * 16384)
        return True

    monkeypatch.setattr(ytv2, "_download_clip", fake_download)
    result = ytv2.get_youtube_clips(
        script="Car zero drives. Car one drives. Car two drives.",
        title="Classic Cars",
        num_scenes=3,
        scene_texts=["Car zero drives", "Car one drives", "Car two drives"],
        scene_query_overrides=[{"section_subject": "cars", "query": "classic car"}] * 3,
        output_dir=str(tmp_path),
        download_candidate_limit=1,
        max_download_attempts_per_scene=1,
        download_concurrency=1,
        hard_timeout_seconds=60.0,
        cooldown_hard_timeout_seconds=240.0,
    )

    if blocked:
        assert not any(result)
        assert len(attempts) == 1
        assert result.audit["network_diagnostic"]["code"] == "network_disabled_by_sandbox"
        assert not result.audit["cooldown_retry"]["triggered"]
    else:
        assert all(result)
        assert attempts[0] == attempts[1]
        assert set(attempts[1:]) == {"classic00", "classic01", "classic02"}
        assert result.audit["cooldown_retry"]["trigger"] == "network_unavailable"
        assert result.audit["cooldown_retry"]["recovered"] == 3
        assert not result.audit["provider_network_unavailable"]
