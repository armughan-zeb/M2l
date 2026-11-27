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
    options.update(pipeline._youtube_js_runtime_options())
    ranged.download_range_attempt(
        options, source.as_uri(), [], None, [], work_dir=tmp_path,
        extraction_seconds=12, deadline=time.monotonic() + 20,
        cancellation=None, check_transfer=None, on_process=None,
    )
    assert output.stat().st_size > 1024
    assert pipeline._source_has_video_stream(output)
    assert list(tmp_path.glob(".yt-attempt-*")) == []


def test_range_attempt_serializes_set_valued_options(tmp_path):
    # Regression: production passes compat_opts as a {"no-certifi"} set.
    # The in-process path tolerates sets, but the subprocess payload is
    # JSON, so every attempt died in ~0.1s with "Object of type set is
    # not JSON serializable" before yt-dlp started (live job 08f0e2c1).
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
        "compat_opts": {"no-certifi"},
    }
    ranged.download_range_attempt(
        options, source.as_uri(), [], None, [], work_dir=tmp_path,
        extraction_seconds=30, deadline=time.monotonic() + 60,
        cancellation=None, check_transfer=None, on_process=None,
    )
    assert output.stat().st_size > 1024
    assert pipeline._source_has_video_stream(output)
    assert list(tmp_path.glob(".yt-attempt-*")) == []


@pytest.mark.parametrize(("session_status", "require_session", "expected_routes"), [
    ("verified", "0", ["signed_in", "public"]),
    ("signed_out", "0", ["public", "signed_in"]),
    ("verified", "1", ["signed_in"]),
])
def test_pre_transfer_timeout_rotates_clients_before_abandoning_candidate(
    tmp_path, monkeypatch, session_status, require_session, expected_routes,
):
    """Nov 26: a pre-transfer extraction timeout rotates through every
    client list before the candidate fails — it no longer dies on the
    first attempt, and no clip deadline is imposed at all."""
    class RealPackage:
        __path__ = ["yt_dlp"]

    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED", raising=False)
    monkeypatch.setattr(pipeline, "_current_youtube_network_diagnostic", lambda: {})
    monkeypatch.setattr("services.yt_dlp_runtime.load_yt_dlp", RealPackage)
    monkeypatch.setenv("VIDZEO_YT_REQUIRE_SESSION", require_session)
    monkeypatch.setenv("VIDZEO_YT_DOWNLOAD_ANON_FALLBACK", "1")
    monkeypatch.setenv("VIDZEO_YT_PER_CLIP_TIMEOUT", "45")
    monkeypatch.setenv("VIDZEO_YT_CLIP_GRACE_SECONDS", "20")
    monkeypatch.setenv("VIDZEO_YT_RANGE_EXTRACTION_TIMEOUT_SECONDS", "40")
    monkeypatch.setattr(pipeline, "_ANON_DOWNLOAD_THROTTLE_UNTIL", 0.0)
    calls = []

    def stall(options, _url, clients, jar, _ranges, **kwargs):
        calls.append((
            options["format"], "public" if jar is None else "signed_in",
            kwargs["extraction_seconds"], kwargs["deadline"], tuple(clients),
        ))
        raise ranged.YouTubeExtractionTimeout("youtube_extraction_timeout_after_40s")

    monkeypatch.setattr(pipeline, "download_range_attempt", stall)
    jar = ranged._cookie_jar([])
    jar._vidzeo_session_status = session_status
    candidate = {"id": "fixture", "url": "https://www.youtube.com/watch?v=fixture"}
    assert not pipeline._download_clip(candidate, tmp_path / "clip.mp4", 5, 3, session_jar=jar)
    rotations = pipeline._youtube_player_client_rotations()
    # One full session/anon pass per client rotation.
    assert [call[1] for call in calls] == expected_routes * len(rotations)
    assert len({(call[0], call[3]) for call in calls}) == 1
    # No clip deadline is imposed even though the env sets one.
    assert all(call[3] is None for call in calls)
    # The client list follows the rotation order on every pass.
    expected_clients = []
    for auth, anon in rotations:
        for route in expected_routes:
            expected_clients.append(tuple(anon if route == "public" else auth))
    assert [call[4] for call in calls] == expected_clients
    if expected_routes == ["signed_in", "public"]:
        assert [call[2] for call in calls] == [40.0, 240.0] * len(rotations)
    if expected_routes == ["public", "signed_in"]:
        # Anonymous-first extraction timed out: the session is tried next
        # with the full configured wall, since the public attempt already
        # ran first and no reserve needs holding back for it.
        assert [call[2] for call in calls] == [240.0, 40.0] * len(rotations)
    if expected_routes == ["signed_in"]:
        assert [call[2] for call in calls] == [40.0] * len(rotations)
    assert candidate["_transport_failure"] == "youtube_extraction_timeout"
    assert pipeline._ANON_DOWNLOAD_THROTTLE_UNTIL == 0.0


def test_player_client_rotations_restore_android_first(monkeypatch):
    """Nov 26: rotation 1 is the Nov 15 android-first list that landed 6
    clips in test 21; rotation 2 is the Nov 18 measured list; rotation 3
    is yt-dlp's own defaults. An env override still wins with one list."""
    monkeypatch.delenv("VIDZEO_YT_PLAYER_CLIENTS", raising=False)
    rotations = pipeline._youtube_player_client_rotations()
    assert rotations[0] == (
        ["android", "web", "web_embedded", "web_creator", "mweb"],
        ["android", "web_embedded", "web_creator", "mweb"],
    )
    assert rotations[1] == (["web", "tv_samsung"], ["tv_embedded"])
    assert rotations[2] == ([], [])
    monkeypatch.setenv("VIDZEO_YT_PLAYER_CLIENTS", "android,web")
    assert pipeline._youtube_player_client_rotations() == [(["android", "web"], ["android", "web"])]


def test_download_clip_rotates_client_lists_on_transport_timeout(tmp_path, monkeypatch):
    """Nov 26: a transport timeout rotates through every client list
    before the candidate fails — and no clip deadline is imposed."""
    class RealPackage:
        __path__ = ["yt_dlp"]

    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED", raising=False)
    monkeypatch.setattr(pipeline, "_current_youtube_network_diagnostic", lambda: {})
    monkeypatch.setattr("services.yt_dlp_runtime.load_yt_dlp", RealPackage)
    monkeypatch.setenv("VIDZEO_YT_REQUIRE_SESSION", "0")
    monkeypatch.setenv("VIDZEO_YT_DOWNLOAD_ANON_FALLBACK", "1")
    monkeypatch.setenv("VIDZEO_YT_RANGE_EXTRACTION_TIMEOUT_SECONDS", "40")
    monkeypatch.setattr(pipeline, "_ANON_DOWNLOAD_THROTTLE_UNTIL", 0.0)
    calls = []

    def stall(_options, _url, clients, jar, _ranges, **kwargs):
        calls.append((tuple(clients), "public" if jar is None else "signed_in", kwargs["deadline"]))
        raise TimeoutError("timed out")

    monkeypatch.setattr(pipeline, "download_range_attempt", stall)
    jar = ranged._cookie_jar([])
    jar._vidzeo_session_status = "verified"
    candidate = {"id": "fixture", "url": "https://www.youtube.com/watch?v=fixture"}
    assert not pipeline._download_clip(candidate, tmp_path / "clip.mp4", 5, 3, session_jar=jar)
    rotations = pipeline._youtube_player_client_rotations()
    expected = []
    for auth, anon in rotations:
        expected.extend([(tuple(auth), "signed_in"), (tuple(anon), "public")])
    assert [(call[0], call[1]) for call in calls] == expected
    assert all(call[2] is None for call in calls)
    assert candidate["_transport_failure"] == "transport_timeout"


def test_download_clip_does_not_rotate_on_429(tmp_path, monkeypatch):
    """Nov 26: a 429 never rotates — it is a shared-window failure, and
    the cooldown wave retry (not another client list) handles it."""
    class RealPackage:
        __path__ = ["yt_dlp"]

    monkeypatch.delenv("CODEX_SANDBOX_NETWORK_DISABLED", raising=False)
    monkeypatch.setattr(pipeline, "_current_youtube_network_diagnostic", lambda: {})
    monkeypatch.setattr("services.yt_dlp_runtime.load_yt_dlp", RealPackage)
    monkeypatch.setenv("VIDZEO_YT_REQUIRE_SESSION", "0")
    monkeypatch.setenv("VIDZEO_YT_DOWNLOAD_ANON_FALLBACK", "1")
    monkeypatch.setenv("VIDZEO_YT_RANGE_EXTRACTION_TIMEOUT_SECONDS", "40")
    monkeypatch.setattr(pipeline, "_ANON_DOWNLOAD_THROTTLE_UNTIL", 0.0)
    calls = []

    def throttled(_options, _url, clients, jar, _ranges, **kwargs):
        calls.append(tuple(clients))
        raise Exception("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(pipeline, "download_range_attempt", throttled)
    jar = ranged._cookie_jar([])
    jar._vidzeo_session_status = "verified"
    candidate = {"id": "fixture", "url": "https://www.youtube.com/watch?v=fixture"}
    assert not pipeline._download_clip(candidate, tmp_path / "clip.mp4", 5, 3, session_jar=jar)
    auth, anon = pipeline._youtube_player_client_rotations()[0]
    assert calls == [tuple(auth), tuple(anon), tuple(auth)]


@pytest.mark.parametrize("public_error", [None, "login required", "Sign in to confirm you're not a bot"])
def test_signed_out_session_uses_signed_in_attempt_only_if_public_requires_login(
    tmp_path, monkeypatch, public_error,
):
    monkeypatch.setenv("VIDZEO_YT_REQUIRE_SESSION", "0")
    monkeypatch.setenv("VIDZEO_YT_DOWNLOAD_ANON_FALLBACK", "1")
    monkeypatch.setattr(pipeline, "_ANON_DOWNLOAD_THROTTLE_UNTIL", 0.0)
    persisted = []
    monkeypatch.setattr(pipeline, "_persist_youtube_session_cookies", lambda used, _: persisted.append(used))
    jar = ranged._cookie_jar([])
    jar._vidzeo_session_status = "signed_out"
    attempts = []

    def download(_opts, session):
        attempts.append(session)
        if session is None and public_error:
            raise RuntimeError(public_error)
        return session

    def run():
        pipeline._download_with_youtube_session(
            None, {}, "https://www.youtube.com/watch?v=fixture", jar, None, tmp_path,
            "clip.raw.", attempt_runner=download,
        )

    if public_error and "not a bot" in public_error:
        with pytest.raises(RuntimeError, match="not a bot"):
            run()
    else:
        run()
    assert attempts == ([None, jar] if public_error == "login required" else [None])
    assert persisted == ([jar] if public_error == "login required" else [])


def test_unverified_session_falls_back_to_public_on_signed_in_extraction_timeout(
    tmp_path, monkeypatch,
):
    """Nov 26 (Test 21 recipe: signed-in first): the anonymous attempt
    is the fallback after an authenticated failure, not the first
    attempt. A pre-transfer extraction timeout on the signed-in attempt
    must fall through to public playback instead of abandoning the
    candidate."""
    monkeypatch.setenv("VIDZEO_YT_REQUIRE_SESSION", "0")
    monkeypatch.setenv("VIDZEO_YT_DOWNLOAD_ANON_FALLBACK", "1")
    monkeypatch.setattr(pipeline, "_ANON_DOWNLOAD_THROTTLE_UNTIL", 0.0)
    persisted = []
    monkeypatch.setattr(pipeline, "_persist_youtube_session_cookies", lambda used, _: persisted.append(used))
    jar = ranged._cookie_jar([])
    jar._vidzeo_session_status = "unverified"
    attempts = []

    def download(_opts, session):
        attempts.append(session)
        if session is not None:
            raise ranged.YouTubeExtractionTimeout("youtube_extraction_timeout_after_55s")
        return session

    pipeline._download_with_youtube_session(
        None, {}, "https://www.youtube.com/watch?v=fixture", jar, None, tmp_path,
        "clip.raw.", attempt_runner=download,
    )
    assert attempts == [jar, None]
    assert persisted == []


def test_range_attempt_subprocess_env_keeps_proxy_vars_by_default(monkeypatch):
    """Nov 26 (Test 21 recipe): the range-attempt child env inherits
    the egress proxy vars by default, as on Nov 15. VIDZEO_YT_DIRECT=1
    restores the Nov 25 direct-only strip for when the proxy dies."""
    monkeypatch.delenv("VIDZEO_YT_DIRECT", raising=False)
    monkeypatch.setenv("https_proxy", "http://hatch-egress-proxy:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://hatch-egress-proxy:3128")
    monkeypatch.setenv("ALL_PROXY", "http://hatch-egress-proxy:3128")
    monkeypatch.setenv("no_proxy", "localhost")
    env = ranged._child_env()
    assert env["https_proxy"] == "http://hatch-egress-proxy:3128"
    assert env["HTTP_PROXY"] == "http://hatch-egress-proxy:3128"
    assert env["ALL_PROXY"] == "http://hatch-egress-proxy:3128"
    assert env["no_proxy"] == "localhost"
    assert "PATH" in env
    monkeypatch.setenv("VIDZEO_YT_DIRECT", "1")
    env = ranged._child_env()
    for key in (
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    ):
        assert key not in env
    assert "PATH" in env


def test_youtube_runtime_options_use_proxy_by_default(monkeypatch):
    """Nov 26 (Test 21 recipe): YT options carry no proxy override, so
    yt-dlp reads the egress proxy env vars — as on Nov 15 (c5133b4),
    when 6 of 7 clips landed. VIDZEO_YT_DIRECT=1 restores the Nov 25
    direct-only override (proxy="") for when the proxy itself dies."""
    monkeypatch.delenv("VIDZEO_YT_DIRECT", raising=False)
    assert "proxy" not in pipeline._youtube_js_runtime_options()
    monkeypatch.setenv("VIDZEO_YT_DIRECT", "1")
    assert pipeline._youtube_js_runtime_options()["proxy"] == ""
@pytest.mark.parametrize("failure, override, expected_clients", [
    ("Requested format is not available", None, [["android", "web_embedded", "web_creator", "mweb"], ["tv"]]),
    ("Requested format is not available", "web", [["web"]]),
    ("Sign in to confirm you're not a bot", None, [["android", "web_embedded", "web_creator", "mweb"]]),
    ("HTTP Error 429: Too Many Requests", None, [["android", "web_embedded", "web_creator", "mweb"]]),
])
def test_public_range_uses_tv_only_when_android_lacks_formats(
    tmp_path, monkeypatch, failure, override, expected_clients,
):
    monkeypatch.delenv("VIDZEO_YT_PLAYER_CLIENTS", raising=False)
    monkeypatch.setattr(pipeline, "_ANON_DOWNLOAD_THROTTLE_UNTIL", 0.0)
    if override:
        monkeypatch.setenv("VIDZEO_YT_PLAYER_CLIENTS", override)
    calls = []

    def download(options, session):
        assert session is None
        clients = options["extractor_args"]["youtube"]["player_client"]
        calls.append(clients)
        if clients != ["tv"]:
            raise RuntimeError(failure)

    def run():
        pipeline._download_with_youtube_session(
            None, {}, "https://www.youtube.com/watch?v=fixture", None, None, tmp_path,
            "clip.raw.", attempt_runner=download,
        )

    if len(expected_clients) == 1:
        with pytest.raises(RuntimeError, match=failure):
            run()
    else:
        run()
    assert calls == expected_clients


def test_worker_passes_requested_range_and_cookie_in_memory(tmp_path, monkeypatch):
    from services.yt_dlp_runtime import YouTubeRuntimeLog

    monkeypatch.delenv("VIDZEO_YT_DIRECT", raising=False)
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
            # Nov 26 (Test 21 recipe): _execute leaves the proxy unset
            # by default, so yt-dlp reads the egress proxy env vars;
            # VIDZEO_YT_DIRECT=1 forces proxy="" instead.
            assert "proxy" not in options
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
