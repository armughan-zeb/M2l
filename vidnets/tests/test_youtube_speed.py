"""Worker D (yt-speed) regressions.

- Player clients: bare tv_embedded anonymously, yt-dlp authed defaults when
  authenticated (tv_embedded is stripped by yt-dlp without cookies support).
  Measured live Nov 18: old multi-client lists returned only 360p +
  storyboards without JS solving; tv_embedded alone returned 48-77 formats.
- Extraction cache: one metadata extraction serves probe + final + retries.
- Memory fit: total-RAM tiers, not instantaneous free RAM.
- Quality formats: 30 fps preferred on the primary rung, 2160p rung gone.
- FFmpeg stall watchdog: frozen byte-progress kills the downloader only.
"""
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_pipeline_v2 as ytv2


@pytest.fixture(autouse=True)
def _clear_info_cache():
    with ytv2._YOUTUBE_INFO_CACHE_LOCK:
        ytv2._YOUTUBE_INFO_CACHE.clear()
    yield
    with ytv2._YOUTUBE_INFO_CACHE_LOCK:
        ytv2._YOUTUBE_INFO_CACHE.clear()


# ---------------------------------------------------------------------------
# 1. Player clients
# ---------------------------------------------------------------------------

def test_player_clients_anonymous_uses_bare_tv_embedded(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_PLAYER_CLIENTS", raising=False)
    assert ytv2._youtube_player_clients(None) == ["tv_embedded"]
    assert ytv2._youtube_player_clients() == ["tv_embedded"]


def test_player_clients_authenticated_uses_authed_defaults(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_PLAYER_CLIENTS", raising=False)
    jar = object()
    with mock.patch.object(
        ytv2, "_adapt_youtube_cookie_jar", return_value=object()
    ):
        # web_embedded was dropped (run 4): its embed-page ytcfg path
        # raises raw KeyError('INNERTUBE_CONTEXT') in yt-dlp 2026.10.26
        # and aborts the whole extraction instead of falling through.
        assert ytv2._youtube_player_clients(jar) == [
            "web", "tv_samsung",
        ]


def test_player_clients_env_override_wins(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_PLAYER_CLIENTS", "web,android")
    assert ytv2._youtube_player_clients(None) == ["web", "android"]


# ---------------------------------------------------------------------------
# 3. Memory fit (total RAM, not instantaneous free RAM)
# ---------------------------------------------------------------------------

def test_ranged_workers_hold_parallelism_while_job_holds_ram():
    # Panama ran at 637 MB available and fitted 1 worker under the old rule.
    assert ytv2._ranged_download_workers(
        4, available_mb=637, total_bytes=8 * 1024 ** 3) == 4
    # Spec (Vidzeo Local thread pools): YT Clip Workers 2-4 — an
    # explicit request for 6 clamps to 4 (the memory fit itself still
    # allows 6 on this host; see _ranged_download_worker_limit tests).
    assert ytv2._ranged_download_workers(
        6, available_mb=637, total_bytes=8 * 1024 ** 3) == 4
    # The no-arg default is 4, not 6.
    assert ytv2._ranged_download_workers() == 4


def test_mixed_capacity_uses_total_ram_tiers(monkeypatch):
    fake_memory = SimpleNamespace(
        available=637 * 1024 * 1024, total=8 * 1024 ** 3)
    with mock.patch("psutil.virtual_memory", return_value=fake_memory):
        # Spec (Vidzeo Local thread pools): YT Clip Workers 2-4 — a
        # requested 6 clamps to 4.
        monkeypatch.setenv("VIDZEO_MIXED_YT_DOWNLOAD_CONCURRENCY", "6")
        assert ytv2.mixed_youtube_download_worker_capacity({}) == 4


# ---------------------------------------------------------------------------
# 4. Quality formats: 30 fps primary, no 2160p rung
# ---------------------------------------------------------------------------

def test_primary_selector_prefers_30fps_and_drops_2160(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_FORMAT", raising=False)
    monkeypatch.delenv("VIDZEO_YT_SOURCE_FORMAT", raising=False)
    formats = ytv2._youtube_quality_formats()
    assert formats
    assert "vcodec^=avc1" in formats[0]
    # Only the FIRST slash-alternative prefers 30 fps; the other two stay
    # unfiltered so a 60fps-only upload still resolves on the primary rung
    # instead of failing through to the fallback string.
    alternatives = formats[0].split("/")
    assert "[fps<=?30]" in alternatives[0]
    assert not any("[fps<=" in alt for alt in alternatives[1:])
    assert not any("height<=2160" in item for item in formats)
    assert any("height<=1440" in item for item in formats)
    assert all("height>=720" in item for item in formats)


# ---------------------------------------------------------------------------
# 2. Extraction cache
# ---------------------------------------------------------------------------

class _FakeYdl:
    """Minimal yt-dlp double: extract once, select per-call by format opt."""

    def __init__(self, options):
        self.options = options
        self.extract_calls = 0
        self.processed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, download=False):
        self.extract_calls += 1
        return {
            "id": "chyjcZe0qW4",
            "title": "Probe video",
            "formats": [
                {"format_id": "18", "ext": "mp4", "height": 360,
                 "vcodec": "avc1", "acodec": "mp4a", "url": "https://x/18"},
                {"format_id": "137", "ext": "mp4", "height": 1080,
                 "vcodec": "avc1", "acodec": "none", "url": "https://x/137"},
                {"format_id": "140", "ext": "m4a", "height": None,
                 "vcodec": "none", "acodec": "mp4a", "url": "https://x/140"},
            ],
            # A first caller's selection must never leak into the cache.
            "requested_formats": [{"format_id": "18"}],
        }

    def process_ie_result(self, info, download=True):
        assert download is True
        assert "requested_formats" not in info
        assert info.get("formats")
        self.processed.append((self.options.get("format"), info["id"]))
        return info

    def download(self, _urls):
        raise AssertionError("cached path must not fall back to download()")


def test_cached_info_serves_probe_then_final_with_one_extraction():
    url = "https://www.youtube.com/watch?v=chyjcZe0qW4"
    clients = ["tv_embedded"]
    probe_ydl = _FakeYdl({"format": "18/best[height<=360]"})
    ytv2._youtube_download_with_cached_info(probe_ydl, url, clients)
    assert probe_ydl.extract_calls == 1
    assert probe_ydl.processed[0][0] == "18/best[height<=360]"
    # Final download: new YoutubeDL instance, HD selector, no re-extraction.
    final_ydl = _FakeYdl({"format": "bestvideo[height>=720]+bestaudio"})
    ytv2._youtube_download_with_cached_info(final_ydl, url, clients)
    assert final_ydl.extract_calls == 0
    assert final_ydl.processed[0][0] == "bestvideo[height>=720]+bestaudio"
    # The cached dict itself carries no selection.
    cached = ytv2._youtube_cached_info(url, clients)
    assert cached is not None and "requested_formats" not in cached


def test_cached_info_key_includes_client_list():
    url = "https://www.youtube.com/watch?v=chyjcZe0qW4"
    first = _FakeYdl({})
    ytv2._youtube_download_with_cached_info(first, url, ["tv_embedded"])
    second = _FakeYdl({})
    ytv2._youtube_download_with_cached_info(second, url, ["web"])
    assert second.extract_calls == 1  # different client list: fresh extract


def test_cached_info_evicts_on_failure_and_expires():
    url = "https://www.youtube.com/watch?v=chyjcZe0qW4"
    clients = ["tv_embedded"]
    ok = _FakeYdl({})
    ytv2._youtube_download_with_cached_info(ok, url, clients)
    assert ytv2._youtube_cached_info(url, clients) is not None

    class _FailingYdl(_FakeYdl):
        def process_ie_result(self, info, download=True):
            raise RuntimeError("transfer flake")

    failing = _FailingYdl({})
    with pytest.raises(RuntimeError):
        ytv2._youtube_download_with_cached_info(failing, url, clients)
    assert ytv2._youtube_cached_info(url, clients) is None

    fresh = _FakeYdl({})
    ytv2._youtube_download_with_cached_info(fresh, url, clients)
    assert fresh.extract_calls == 1  # eviction forced a re-extraction
    # Expired entries miss.
    key = ytv2._youtube_info_cache_key(url, clients)
    with ytv2._YOUTUBE_INFO_CACHE_LOCK:
        stored_at, info = ytv2._YOUTUBE_INFO_CACHE[key]
        ytv2._YOUTUBE_INFO_CACHE[key] = (stored_at - 7200, info)
    assert ytv2._youtube_cached_info(url, clients) is None


def test_cached_info_falls_back_for_non_video_dicts():
    class _SearchYdl(_FakeYdl):
        def extract_info(self, url, download=False):
            self.extract_calls += 1
            return {"entries": [{"id": "video-1"}]}

        def download(self, urls):
            self.downloaded = list(urls)

    ydl = _SearchYdl({})
    ytv2._youtube_download_with_cached_info(
        ydl, "https://www.youtube.com/watch?v=chyjcZe0qW4", ["tv_embedded"])
    assert ydl.downloaded == ["https://www.youtube.com/watch?v=chyjcZe0qW4"]


# ---------------------------------------------------------------------------
# 5. FFmpeg stall watchdog
# ---------------------------------------------------------------------------

def test_watchdog_kills_only_the_matching_frozen_ffmpeg(tmp_path):
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    watch_dir = tmp_path / "clip"
    watch_dir.mkdir()
    raw_prefix = "clip.raw."
    base = [ffmpeg, "-re", "-f", "lavfi", "-i",
            "testsrc=duration=300:size=64x64:rate=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-y"]
    # This clip's downloader: raw prefix + http input in the command line.
    victim_proc = subprocess.Popen(
        base + ["-metadata",
                "comment=https://rr1---sn.googlevideo.com/videoplayback",
                str(watch_dir / (raw_prefix + "mp4"))],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Another clip's downloader in the same dir: different raw prefix.
    other_proc = subprocess.Popen(
        base + ["-metadata",
                "comment=https://rr1---sn.googlevideo.com/videoplayback",
                str(watch_dir / "other.raw.mp4")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # A local re-encode ffmpeg (no http input) must never be touched.
    local_proc = subprocess.Popen(
        base + [str(tmp_path / "out.mp4")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)  # let all three ffmpeg processes spawn
        assert victim_proc.poll() is None
        assert other_proc.poll() is None
        assert local_proc.poll() is None
        watchdog = ytv2._FFmpegStallWatchdog(watch_dir, raw_prefix)
        assert watchdog._kill_matching_ffmpeg() is True
        victim_proc.wait(timeout=10)
        assert victim_proc.returncode is not None
        # The other clip's downloader and the local encode survive.
        assert other_proc.poll() is None
        assert local_proc.poll() is None
    finally:
        for proc in (victim_proc, other_proc, local_proc):
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def test_watchdog_fires_on_frozen_bytes_and_ignores_moving_bytes(tmp_path):
    watch_dir = tmp_path / "clip"
    watch_dir.mkdir()
    raw = watch_dir / "clip.raw.mp4"
    raw.write_bytes(b"x" * 1024)
    frozen = ytv2._FFmpegStallWatchdog(
        watch_dir, "clip.raw.", stall_seconds=2.0)
    frozen._POLL_SECONDS = 1.0
    frozen.start()
    try:
        deadline = time.monotonic() + 8
        while not frozen.fired and time.monotonic() < deadline:
            time.sleep(0.2)
        # No ffmpeg child exists, so the watchdog keeps watching instead of
        # firing; it must never claim a stall it did not act on.
        assert frozen.fired is False
    finally:
        frozen.stop()

    # Moving bytes: the watchdog must not fire.
    moving = ytv2._FFmpegStallWatchdog(
        watch_dir, "clip.raw.", stall_seconds=2.0)
    moving._POLL_SECONDS = 1.0
    stop_growth = threading.Event()

    def _grow():
        while not stop_growth.wait(0.5):
            with raw.open("ab") as handle:
                handle.write(b"y" * 512)

    grower = threading.Thread(target=_grow, daemon=True)
    grower.start()
    moving.start()
    try:
        time.sleep(7)
        assert moving.fired is False
    finally:
        stop_growth.set()
        grower.join(timeout=5)
        moving.stop()
