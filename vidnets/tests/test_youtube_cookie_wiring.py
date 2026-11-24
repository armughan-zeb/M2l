"""Area A wiring tests: every YouTube call site must use the shared session.

Covers the spots that ran anonymous before the Nov 17 cookie-wiring build:
- ``select_best_youtube_candidate`` search (pipeline)
- transcript fallback yt-dlp extract + caption fetch (transcript index)
- youtube-transcript-api client (transcript index)
- chapter-metadata subprocess (youtube_source_chapters)
- ``_install_shared_youtube_session`` shared accessor (pipeline)
- download retry: sign-in retry uses the backup account, throttling
  marks the account throttled instead of retrying it forever.
"""
import http.cookiejar
import json
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import youtube_pipeline_v2 as pipeline
from services import youtube_transcript_index as transcript_index
from services.youtube_transcript_index import YouTubeTranscriptIndex


def _cookie(name="SAPISID", value="test-value", domain=".youtube.com"):
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True, secure=True,
        expires=int(time.time()) + 3600, discard=False,
        comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False,
    )


def _jar():
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(_cookie())
    jar.set_cookie(_cookie("LOGIN_INFO", "test-login"))
    return jar


# --- select_best_youtube_candidate -------------------------------------

def test_select_best_resolves_shared_session_for_search():
    seen = {}

    def fake_search(query, max_results=12, session_jar=None, cancellation=None):
        seen["session_jar"] = session_jar
        return [{"id": "vid1", "title": "GTA VI trailer footage", "duration": 120}]

    sentinel = _jar()
    with mock.patch.object(
        pipeline, "_get_authenticated_youtube_session", return_value=sentinel
    ) as resolve, mock.patch.object(
        pipeline, "_search_youtube", side_effect=fake_search
    ), mock.patch.object(
        pipeline, "_score_candidate", return_value=10.0
    ):
        result = pipeline.select_best_youtube_candidate("GTA VI Leonida", title="GTA VI")
    resolve.assert_called_once()
    assert seen["session_jar"] is sentinel
    assert result["selected_video"]["id"] == "vid1"


def test_select_best_accepts_explicit_session():
    explicit = _jar()
    with mock.patch.object(
        pipeline, "_get_authenticated_youtube_session", return_value=explicit
    ) as resolve, mock.patch.object(
        pipeline, "_search_youtube", return_value=[]
    ), mock.patch.object(
        pipeline, "_score_candidate", return_value=0.0
    ):
        pipeline.select_best_youtube_candidate("x", session_jar=explicit)
    resolve.assert_called_once()
    assert resolve.call_args.kwargs.get("session_jar") is explicit


# --- shared accessor ----------------------------------------------------

def test_install_shared_session_attaches_jar_to_ydl():
    from services.yt_dlp_runtime import load_yt_dlp

    jar = _jar()
    yt_dlp = load_yt_dlp()
    with mock.patch.object(
        pipeline, "_get_authenticated_youtube_session", return_value=jar
    ):
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            assert pipeline._install_shared_youtube_session(ydl) is True
            assert ydl.cookiejar is not None
            assert sum(1 for _ in ydl.cookiejar) == 2


def test_install_shared_session_is_anonymous_without_session():
    from services.yt_dlp_runtime import load_yt_dlp

    yt_dlp = load_yt_dlp()
    with mock.patch.object(
        pipeline, "_get_authenticated_youtube_session", return_value=None
    ):
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            assert pipeline._install_shared_youtube_session(ydl) is False


# --- chapters subprocess -------------------------------------------------

def test_chapter_subprocess_resolves_shared_session():
    import services.youtube_source_chapters as chapters

    captured = {}

    def fake_run(argv, **kwargs):
        captured["script"] = argv[2]
        result = mock.Mock()
        result.stdout = 'VIDNETS_CHAPTERS=[]\n'
        return result

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert chapters._fetch_source_chapters("dQw4w9WgXcQ", timeout_seconds=5) == ([], True)
    assert "_install_shared_youtube_session" in captured["script"]


# --- transcript index ----------------------------------------------------

def test_transcript_api_client_carries_session_cookies():
    client = transcript_index._transcript_api_client(_jar())
    assert client is not None
    names = {cookie.name for cookie in client.cookies}
    assert names == {"SAPISID", "LOGIN_INFO"}
    assert transcript_index._transcript_api_client(None) is None


def test_cookie_opener_carries_session_cookies():
    opener = transcript_index._cookie_opener(_jar())
    assert opener is not None
    assert transcript_index._cookie_opener(None) is None


class _FakeYDL:
    instances = []

    def __init__(self, opts, fail_extract=False):
        self.opts = opts
        self.cookiejar = None
        self.fail_extract = fail_extract
        _FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, video_id, download=False):
        if self.fail_extract:
            raise RuntimeError("primary provider down")
        return {
            "subtitles": {},
            "automatic_captions": {
                "en": [{"ext": "json3", "url": "https://example.test/captions"}]
            },
        }


class _FakeYtDlpModule:
    def __init__(self, fail_extract=False):
        self.fail_extract = fail_extract

    def YoutubeDL(self, opts):
        return _FakeYDL(opts, fail_extract=self.fail_extract)


class _FakeCaptionResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        payload = {"events": [
            {"tStartMs": 1000, "dDurationMs": 2000,
             "segs": [{"utf8": "Grand Theft Auto six trailer "}]},
        ]}
        return json.dumps(payload).encode("utf-8")


class _FakeOpener:
    def open(self, url, timeout=None):
        return _FakeCaptionResponse()


def test_transcript_fallback_ytdlp_uses_session_jar(tmp_path):
    """The fallback extract_info ran anonymous; it must attach the jar."""
    _FakeYDL.instances.clear()
    jar = _jar()
    index = YouTubeTranscriptIndex(
        str(tmp_path / "transcripts.sqlite3"), session_jar=jar
    )
    fake_primary = _FakeYtDlpModule(fail_extract=True)
    fake_fallback = _FakeYtDlpModule(fail_extract=False)
    with mock.patch(
        "services.yt_dlp_runtime.load_yt_dlp", return_value=fake_primary
    ), mock.patch(
        "youtube_transcript_api.YouTubeTranscriptApi",
        side_effect=RuntimeError("api blocked"),
    ), mock.patch.dict(
        sys.modules, {"yt_dlp": fake_fallback}
    ), mock.patch.object(
        transcript_index, "_cookie_opener", return_value=_FakeOpener()
    ):
        document = index.fetch("dQw4w9WgXcQ")
    assert document.available
    assert document.segments, "fallback should return caption segments"
    assert _FakeYDL.instances, "fallback YoutubeDL was never built"
    fallback_ydl = _FakeYDL.instances[-1]
    # The provider adapts the jar into yt-dlp's cookie jar first, so
    # assert on the attached cookies, not object identity.
    assert fallback_ydl.cookiejar is not None
    assert {cookie.name for cookie in fallback_ydl.cookiejar} == {"SAPISID", "LOGIN_INFO"}


# --- download retry ------------------------------------------------------

def test_download_sign_in_retry_uses_backup_account(tmp_path):
    attempts = {"count": 0}
    backup = _jar()

    class _Pool:
        def next_verified(self, after=-1):
            return (1, backup)

    class _RetryYDL:
        def __init__(self, opts):
            self.opts = opts
            self.cookiejar = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("Sign in to confirm your age")

    class _RetryModule:
        def YoutubeDL(self, opts):
            return _RetryYDL(opts)

    jar = _jar()
    jar._vidzeo_session_pool = _Pool()
    jar._vidzeo_session_index = 0
    pipeline._download_with_youtube_session(
        _RetryModule(), {}, "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        jar, None, tmp_path, "raw_",
    )
    assert attempts["count"] == 2


def test_download_throttle_marks_account_throttled(tmp_path):
    marked = {}

    class _Pool:
        def mark_throttled(self, index, reason=""):
            marked["index"] = index
            marked["reason"] = reason

        def next_verified(self, after=-1):
            return (-1, None)

    class _ThrottleYDL:
        def __init__(self, opts):
            self.cookiejar = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            raise RuntimeError("HTTP Error 429: Too Many Requests")

    class _ThrottleModule:
        def YoutubeDL(self, opts):
            return _ThrottleYDL(opts)

    jar = _jar()
    jar._vidzeo_session_pool = _Pool()
    jar._vidzeo_session_index = 0
    try:
        pipeline._download_with_youtube_session(
            _ThrottleModule(), {}, "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            jar, None, tmp_path, "raw_",
        )
    except RuntimeError:
        pass
    assert marked.get("index") == 0
    assert marked.get("reason") == "youtube-throttled"
