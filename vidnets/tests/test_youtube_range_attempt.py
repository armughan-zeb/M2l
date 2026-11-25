import http.cookiejar
import subprocess
import sys
import time

import psutil
import pytest

from services import youtube_pipeline_v2 as pipeline
from services import youtube_range_attempt as ranged


def test_extract_timeout_reaps_attempt_before_it_can_write_media(tmp_path):
    late_output = tmp_path / "late-media"
    code = "import pathlib, sys, time; time.sleep(3); pathlib.Path(sys.argv[1]).touch()"
    started = time.monotonic()
    bound_pids = []
    with pytest.raises(ranged.YouTubeExtractionTimeout, match="youtube_extraction_timeout"):
        ranged._monitor_attempt(
            [sys.executable, "-c", code, str(late_output)],
            b"x" * (512 * 1024), tmp_path / "transfer",
            extraction_seconds=0.6, deadline=started + 8, on_process=bound_pids.append,
        )
    assert time.monotonic() - started < 3
    assert len(bound_pids) == 2 and bound_pids[1] is None
    assert not late_output.exists()
    assert not psutil.pid_exists(bound_pids[0])


def test_active_transfer_can_continue_beyond_extraction_wall(tmp_path):
    transfer = tmp_path / "transfer"
    code = (
        "import pathlib, sys, time; sys.stdin.buffer.read(); "
        "pathlib.Path(sys.argv[1]).touch(); time.sleep(1.2); print('clip-ready')"
    )
    result = ranged._monitor_attempt(
        [sys.executable, "-c", code, str(transfer)],
        b"{}", transfer, extraction_seconds=0.9, deadline=time.monotonic() + 8,
    )
    assert b"clip-ready" in result


def test_range_attempt_runs_yt_dlp_and_delivers_local_media(tmp_path):
    # Exercise the real subprocess protocol against locally generated media.
    ffmpeg = pipeline._ffmpeg_bin("ffmpeg")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=20:duration=3", "-c:v", "libx264", str(source)],
        timeout=15, check=True,
    )
    from services.yt_dlp_runtime import YouTubeRuntimeLog
    output = tmp_path / "range.raw.mp4"
    options = {
        "enable_file_urls": True,
        "quiet": True,
        "noprogress": True,
        "logger": YouTubeRuntimeLog(),
        "outtmpl": str(output),
        "ffmpeg_location": ffmpeg,
    }
    ranged.download_range_attempt(
        options, source.as_uri(), [], None, [], work_dir=tmp_path,
        extraction_seconds=12, deadline=time.monotonic() + 20,
        cancellation=None, check_transfer=None, on_process=None,
    )
    assert output.stat().st_size > 1024
    assert pipeline._source_has_video_stream(output)
    assert list(tmp_path.glob(".yt-attempt-*")) == []


def test_pre_transfer_failure_does_not_try_another_session_or_format(tmp_path, monkeypatch):
    class RealPackage:
        __path__ = ["yt_dlp"]

    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED", raising=False)
    monkeypatch.setattr(pipeline, "_current_youtube_network_diagnostic", lambda: {})
    monkeypatch.setattr("services.yt_dlp_runtime.load_yt_dlp", RealPackage)
    calls = []

    def stall(options, _url, _clients, jar, _ranges, **kwargs):
        calls.append((options["format"], jar))
        raise ranged.YouTubeExtractionTimeout("youtube_extraction_timeout_after_40s")

    monkeypatch.setattr(pipeline, "download_range_attempt", stall)
    jar = ranged._cookie_jar([])
    candidate = {"id": "fixture", "url": "https://www.youtube.com/watch?v=fixture"}
    assert not pipeline._download_clip(candidate, tmp_path / "clip.mp4", 5, 3, session_jar=jar)
    assert len(calls) == 1 and calls[0][1] is jar
    assert candidate["_transport_failure"] == "youtube_extraction_timeout"



def test_worker_passes_requested_range_and_cookie_in_memory(tmp_path, monkeypatch):
    from services.yt_dlp_runtime import YouTubeRuntimeLog

    marker = tmp_path / "transfer"
    segment_ranges = [(12.25, 16.5)]
    cookie = http.cookiejar.Cookie(
        0, "fixture", "test-value", None, False, ".youtube.com", True, True,
        "/", True, True, None, True, None, None, {"HttpOnly": None}, False,
    )
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(cookie)

    class FakeYDL:
        def __init__(self, options):
            assert list(options["download_ranges"]({}, self)) == [
                {"start_time": 12.25, "end_time": 16.5}
            ]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def extract_info(self, url):
            assert not marker.exists()
            expected = "rotated-test-value" if url == "retry" else "test-value"
            assert list(self.cookiejar)[0].value == expected

        def dl(self, _filename, _info):
            assert marker.is_file()
            list(self.cookiejar)[0].value = "rotated-test-value"

    def retrieve(ydl, url, _clients):
        ydl.extract_info(url)
        ydl.dl("clip.mp4", {})
        ydl.extract_info("retry")

    class FakePackage:
        YoutubeDL = FakeYDL

    monkeypatch.setattr("services.yt_dlp_runtime.load_yt_dlp", FakePackage)
    monkeypatch.setattr(pipeline, "_youtube_download_with_cached_info", retrieve)
    result = ranged._execute({
        "options": {}, "url": "https://www.youtube.com/watch?v=fixture",
        "clients": ["web"], "cookies": ranged._cookie_rows(jar),
        "ranges": segment_ranges, "transfer_marker": str(marker),
    }, YouTubeRuntimeLog())

    assert result["ok"]
    assert ranged._cookie_rows(jar)[0]["value"] == "test-value"
    assert result["cookies"][0]["value"] == "rotated-test-value"
    assert not marker.exists()
