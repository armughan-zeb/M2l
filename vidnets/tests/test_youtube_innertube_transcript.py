"""Regression tests for the round-5 YouTube fixes (Worker C).

Run 4 (job 3006fd62) surfaced two YouTube blockers:

(a) yt-dlp ``KeyError('INNERTUBE_CONTEXT')`` on all 6 download
    verifications. Root cause: yt-dlp 2026.10.26 (latest release) calls
    the unguarded ``_fix_embedded_ytcfg(ytcfg)`` for embedded clients,
    which does a bare ``ytcfg['INNERTUBE_CONTEXT']``. When YouTube serves
    an embed page without a ytcfg, that raises a raw KeyError — not an
    ExtractorError — so yt-dlp's per-client loop aborts instead of
    falling through to the next client. The fix is to keep every
    ``*_embedded`` client out of the authenticated download list.

(b) Transcript deadline: the live mixed-media worker ran with an 18 s
    transcript deadline (app.py overrode the pipeline's 120 s default
    for non-gaming jobs), so run 4 kept only 2/11 transcripts. The
    pool-path fetch also defaulted to 3 workers while the ranged-moments
    caller already used 6.
"""

import http.cookiejar
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.youtube_pipeline_v2 import _youtube_player_clients
from services.youtube_transcript_index import YouTubeTranscriptIndex

yt_dlp = pytest.importorskip("yt_dlp")


def _cookie_jar():
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(http.cookiejar.Cookie(
        version=0,
        name="SAPISID",
        value="account-cookie",
        port=None,
        port_specified=False,
        domain=".youtube.com",
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    ))
    return jar


def _embedded_variant(client_name):
    """True when yt-dlp would route ``client_name`` through the embedded
    ytcfg fixup (``_fix_embedded_ytcfg``)."""
    from yt_dlp.extractor.youtube._base import _split_innertube_client

    return _split_innertube_client(client_name)[2] == "embedded"


def test_embedded_ytcfg_gap_raises_keyerror():
    """Pin the upstream throw site behind run 4's download failures.

    ``_fix_embedded_ytcfg`` does a bare ``ytcfg['INNERTUBE_CONTEXT']``,
    so an embed page without a ytcfg raises exactly the KeyError run 4
    logged 6 times. If a future yt-dlp guards this access, this test
    fails on purpose: re-check the client list before deleting it.
    """
    from yt_dlp.extractor.youtube._base import _fix_embedded_ytcfg

    with pytest.raises(KeyError, match="INNERTUBE_CONTEXT"):
        _fix_embedded_ytcfg({})


def test_authenticated_download_clients_exclude_embedded_variant(monkeypatch):
    """Authenticated downloads must never list an embedded client first
    (or at all): one degenerate embed page aborts the whole extraction
    instead of falling through to the next client (run 4: 6/6 dead)."""
    monkeypatch.delenv("VIDZEO_YT_PLAYER_CLIENTS", raising=False)
    clients = _youtube_player_clients(_cookie_jar())
    assert clients == ["web", "tv_samsung"]
    assert not any(_embedded_variant(client) for client in clients)


def test_anonymous_download_uses_the_android_player_client(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_PLAYER_CLIENTS", raising=False)
    assert _youtube_player_clients(None) == ["android"]


def test_player_client_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_PLAYER_CLIENTS", "web")
    assert _youtube_player_clients(_cookie_jar()) == ["web"]


def test_transcript_default_clients_lead_with_web():
    """Transcript legs must try ``web`` before ``web_embedded``: web is
    faster live (20.9 s vs 77.7 s on the same video) and never takes
    yt-dlp's embedded-ytcfg path that raises raw KeyError
    ('INNERTUBE_CONTEXT') (run 4). web_embedded stays as fallback for
    its caption coverage."""
    from services.youtube_transcript_index import _DEFAULT_TRANSCRIPT_CLIENTS

    clients = [item.strip() for item in _DEFAULT_TRANSCRIPT_CLIENTS.split(",") if item.strip()]
    assert clients[0] == "web"
    assert "web_embedded" in clients


def test_transcript_provider_falls_through_to_captioned_client(tmp_path, monkeypatch):
    """The yt-dlp caption leg must not stop at the first client whose
    extraction merely succeeds: ``web`` can return no caption tracks
    for a video ``web_embedded`` does caption (measured live Nov 19:
    nnsAUPuOYaY). It must keep trying clients until one carries captions."""
    import io
    import json
    import types
    import urllib.request

    from services.youtube_transcript_index import _DEFAULT_TRANSCRIPT_CLIENTS

    assert _DEFAULT_TRANSCRIPT_CLIENTS.split(",")[0] == "web"

    seen_clients = []

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, video_id, download=False):
            client = self.opts["extractor_args"]["youtube"]["player_client"][0]
            seen_clients.append(client)
            if client == "web":
                return {"subtitles": {}, "automatic_captions": {}}
            return {"automatic_captions": {"en": [
                {"ext": "json3", "url": "https://example.test/caps"},
            ]}}

    fake_yt_dlp = types.ModuleType("yt_dlp")
    fake_yt_dlp.YoutubeDL = FakeYoutubeDL
    monkeypatch.setattr(
        "services.yt_dlp_runtime.load_yt_dlp", lambda: fake_yt_dlp
    )
    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_CLIENTS", raising=False)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"events": [
                {"tStartMs": 0, "dDurationMs": 1000,
                 "segs": [{"utf8": "hello world"}]},
            ]}).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse())

    class FakeJar:
        def get_cookie_header(self, url):
            return ""

        def __iter__(self):
            return iter([])

    index = YouTubeTranscriptIndex(
        str(tmp_path / "caption-fallthrough.sqlite3"), session_jar=FakeJar()
    )
    document = index.fetch("nnsAUPuOYaY")
    assert document.status == "ready"
    assert document.available
    assert document.segments and document.segments[0].text == "hello world"
    assert seen_clients == ["web", "web_embedded"]


def test_transcript_fetch_many_default_concurrency_is_six(tmp_path, monkeypatch):
    """The pool path (the one the live mixed-media worker uses) must
    default to 6 workers, matching the ranged-moments caller — 3 could
    not clear 11 videos inside the transcript deadline (run 4: 2/11)."""
    monkeypatch.delenv("VIDZEO_YT_TRANSCRIPT_CONCURRENCY", raising=False)
    peak = 0
    active = 0
    lock = threading.Lock()

    def provider(video_id):
        nonlocal peak, active
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return {"segments": [{"start": 0, "duration": 1, "text": video_id}]}

    index = YouTubeTranscriptIndex(
        str(tmp_path / "default-concurrency.sqlite3"), provider=provider
    )
    documents = index.fetch_many(
        [f"video-{index}" for index in range(12)],
        deadline_seconds=10.0,
    )
    assert len(documents) == 12
    assert peak >= 5, f"expected ~6 parallel workers, peak was {peak}"


def test_transcript_fetch_many_env_override_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_CONCURRENCY", "2")
    peak = 0
    active = 0
    lock = threading.Lock()

    def provider(video_id):
        nonlocal peak, active
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return {"segments": [{"start": 0, "duration": 1, "text": video_id}]}

    index = YouTubeTranscriptIndex(
        str(tmp_path / "env-concurrency.sqlite3"), provider=provider
    )
    documents = index.fetch_many(
        [f"video-{index}" for index in range(6)],
        deadline_seconds=10.0,
    )
    assert len(documents) == 6
    assert peak <= 2, f"expected at most 2 parallel workers, peak was {peak}"


def test_transcript_fetch_many_always_logs_completed_unfinished_count(tmp_path, capsys):
    """Run 7 gap: the count line printed only when the deadline was hit
    with futures still pending, so a worker whose transcripts all
    finished in time logged no accounting at all (main 9/11, small 2/3
    — ranged-moment evidence only, no count line). Every fetch_many
    call must print completed/unfinished plus the deadline applied."""
    def provider(video_id):
        return {"segments": [{"start": 0, "duration": 1, "text": video_id}]}

    index = YouTubeTranscriptIndex(
        str(tmp_path / "count-always.sqlite3"), provider=provider
    )
    documents = index.fetch_many(
        ["count-a", "count-b", "count-c"], deadline_seconds=10.0
    )
    assert len(documents) == 3
    out = capsys.readouterr().out
    assert (
        "[yt-transcript] deadline 10s: retaining 3/3 completed transcript(s); "
        "0 unfinished; 3 ready" in out
    )


def test_transcript_fetch_many_logs_count_when_deadline_hit(tmp_path, capsys):
    """The old behaviour (count line only on deadline hit) is kept as
    a subset of the always-on line: same wording, deadline included."""
    # Distinct video ids from the neighbouring count tests: the
    # single-flight table is module-global by video id, so a slow
    # provider here must not be joined by another test's fetch.
    def provider(video_id):
        time.sleep(5)
        return {"segments": [{"start": 0, "duration": 1, "text": video_id}]}

    index = YouTubeTranscriptIndex(
        str(tmp_path / "count-deadline.sqlite3"), provider=provider
    )
    documents = index.fetch_many(
        ["slow-a", "slow-b", "slow-c"], workers=3, deadline_seconds=0.5
    )
    assert len(documents) == 0
    out = capsys.readouterr().out
    assert (
        "[yt-transcript] deadline 0.5s: retaining 0/3 completed transcript(s); "
        "3 unfinished; 0 ready" in out
    )


def test_transcript_fetch_many_logs_count_when_all_cached(tmp_path, capsys):
    """A worker that reuses only cached transcripts still prints its
    accounting — 0 unfinished, all cached — instead of staying silent."""
    def provider(video_id):
        return {"segments": [{"start": 0, "duration": 1, "text": video_id}]}

    index = YouTubeTranscriptIndex(
        str(tmp_path / "count-cached.sqlite3"), provider=provider
    )
    assert len(index.fetch_many(["video-a", "video-b"], deadline_seconds=10.0)) == 2
    capsys.readouterr()  # drain the first call's line
    documents = index.fetch_many(["video-a", "video-b"], deadline_seconds=10.0)
    assert len(documents) == 2
    out = capsys.readouterr().out
    assert (
        "[yt-transcript] deadline 10s: retaining 2/2 completed transcript(s); "
        "0 unfinished; 2 ready; all cached" in out
    )
