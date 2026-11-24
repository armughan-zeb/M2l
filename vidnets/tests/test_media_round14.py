"""Round 14 (Nov 24, 2026): fix ALL remaining media failures, both lanes.

Job 66b52291 (GTA VI 30-sec, 8 scenes) was paused at 62% on the user
order "Stop and fix all". Diagnosis, grounded in the job's own log + code:

YT lane — 0/7 clips resolved, three stacked defects:
- Y1: round 12 raised the transcript deadline to 300 s in the pipeline
  default and the runtime .env, but server/app.py:11381 hard-pinned
  VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS="120" in the YT worker child's
  environment and never consulted the env key. Worker log:
  "deadline 120s: retaining 8/8 completed transcript(s); 0 unfinished;
  1 ready". The 300 s never reached the worker.
- Y2: 7/8 transcripts were unusable (cache rows: status=error,
  youtube_transcript_unavailable, empty detail), so ready=1 of
  completed=8. match_scene_moments returns [] for unavailable docs
  and prepare_ranged_moments returns no moments — the lane reported
  "ranged moment evidence: 0/7 scenes". CORRECTION during round 14:
  this is NOT a defect in the ranged path. Moments are transcript
  evidence by contract (three existing tests assert moments == {}
  with no transcript, and they are right), and the download planner
  already degrades via best_timestamp / _pick_timestamp when moments
  are missing. A heuristic moment injected here would be mislabelled
  timestamp_evidence='transcript_match' and would suppress the
  chapter fallback for long videos, so the heuristic-moment idea was
  built, tested, and then WITHDRAWN. The gap is closed by Y1 (the
  300 s pin gives the fetch time to succeed) plus the existing
  planner fallback. The 15 s read timeout in one cache row is Round
  13's remaining-race-wall cap on the fallback leg, not a missed pin.
- Y3: the discovery budget (180 s = 300 s/min x 0.6 min) was measured
  from batch start, before search, so search (~60 s) + the 120 s
  transcript fetch spent it before any download could start. The loop
  printed "discovery budget reached (soft_timeout); no new scene
  workers" with 0/7 resolved; no probe ever landed (empty media dir,
  no yt_clip_scene_*.mp4 anywhere). The 480 s branch wall fired even
  though round 12 claimed 505 s: mixed_youtube_time_budget(7, 480,
  150, workers=2, coverage=0.30) returns (480.0, 120.0) — the 505 s
  never materialises in the live arithmetic.

Web lane — STALLS, not just slow:
- W1: "Preparing media 3/8" sat for 2389 s. Download chunks burned
  100-400 s landing 0 on the same dead hosts (i.pinimg.com,
  washingtonpost, gtaboom: "Cannot connect to host ...:443 ssl").
  Round 13's wait_until_complete removes the per-chunk batch wall, and
  connection errors are retryable by design (test 33), so the same
  dead hosts were retried wave after wave. The liveness watchdog
  covers search waves only, never download landings.
- V1: the visual-gate join waits with an unbounded (inf) deadline, so
  a lost vision verdict would block the whole visual phase silently,
  with no retry and no log line. (Vision did NOT stall in this job —
  scenes 6, 7 resolved — this is the guard the task orders.)

Fix:
- Y1: the app.py pin follows the env/config (default 300 s).
- Y2: no code change in the ranged path (see the correction above):
  moments stay transcript-evidence-only; the planner's existing
  best_timestamp fallback covers caption-less videos, and Y1 gives
  the fetch the time to succeed where captions exist.
- Y3: the discovery (soft) clock starts at the download loop's start,
  not batch start; the hard deadline stays batch-anchored so the loop
  always ends before the adapter's 480 s branch wall.
- W1: _download_bulk_chunked counts consecutive zero-land chunks;
  after VIDZEO_WEB_STALL_ZERO_CHUNKS (default 2) it logs a plain
  [web-stall] line and skips remaining URLs on hosts that already
  failed to connect in this batch.
- V1: the visual-gate join gets a no-progress watchdog
  (VIDZEO_VISION_STALL_SECONDS, default 300 s): retry once, then
  retain the downloaded winner as degraded-unverified.

Every test here FAILS on clean 7981892 and PASSES with the fix,
except the two knob-off tests and the Y2 contract pin, which pass on
clean by design: the knob-off tests assert the disable-knob
reproduces the old behaviour, and the Y2 pin asserts the
transcript-evidence-only contract that round 14 deliberately kept.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

SERVER_ROOT = Path(__file__).resolve().parents[1] / "server"


# ---------------------------------------------------------------------------
# Y1: the transcript-deadline pin must follow the env, not hardcode 120 s
# ---------------------------------------------------------------------------

def test_transcript_deadline_pin_follows_env_not_120():
    source = (SERVER_ROOT / "app.py").read_text()
    marker = 'yt_environment["VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS"] = str('
    assert marker in source
    window = source[source.index(marker): source.index(marker) + 600]
    # The worker env must consult the runtime .env value first.
    assert 'yt_environment.get("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS")' in window
    # And the fallback must be round 12's 300 s, not the old 120 s pin.
    assert '"300"' in window
    assert '"120"' not in window


def _fake_index(documents):
    class _FakeIndex:
        def fetch_many(self, candidates, workers=6, deadline_seconds=20):
            return {video_id: documents[video_id] for video_id in candidates}
    return _FakeIndex()


class _UnavailableDocument:
    available = False
    status = "error"


# ---------------------------------------------------------------------------
# Y2: moments are transcript evidence only — pin the contract (round-14
# correction: the heuristic-moment idea was withdrawn, see module docstring)
# ---------------------------------------------------------------------------

def test_no_transcript_means_no_moment():
    """Pin: with no usable transcript a scene gets no moment at all.

    The download planner covers the scene via best_timestamp /
    _pick_timestamp; prepare_ranged_moments must not invent
    transcript evidence. Passes on clean by design — it guards the
    contract round 14 chose to keep.
    """
    from services.youtube_ranged_moments import prepare_ranged_moments

    rows = [{"scene_text": "GTA VI street chase", "candidate_subject": "GTA VI"}]
    assignments = [{"id": "GpBG1uX2Q5Y", "best_timestamp": 12.5}]
    documents = {"GpBG1uX2Q5Y": _UnavailableDocument()}
    moments, audit = prepare_ranged_moments(
        rows, assignments, {}, [4.0], index=_fake_index(documents)
    )
    assert moments == {}
    assert audit["sources_available"] == 0
    assert audit["scenes_with_moments"] == 0


def test_real_transcript_match_still_wins():
    from services.youtube_ranged_moments import prepare_ranged_moments
    from services.youtube_transcript_index import TranscriptDocument, TranscriptSegment

    rows = [{
        "scene_text": "Grand Theft Auto VI street chase through Vice City",
        "candidate_subject": "GTA VI",
    }]
    assignments = [{"id": "GpBG1uX2Q5Y", "best_timestamp": 12.5}]
    document = TranscriptDocument(
        video_id="GpBG1uX2Q5Y",
        status="ready",
        segments=[
            TranscriptSegment(
                start=10.0, end=14.0,
                text="GTA VI gameplay shows the Vice City street chase",
                normalized_text="gta vi gameplay shows the vice city street chase",
            ),
        ],
    )
    moments, audit = prepare_ranged_moments(
        rows, assignments, {}, [4.0], index=_fake_index({"GpBG1uX2Q5Y": document})
    )
    assert audit["scenes_with_moments"] == 1
    (moment,) = moments[0]
    assert moment["heuristic"] is False
    assert moment["match_score"] > 0.0


# ---------------------------------------------------------------------------
# Y3: the discovery clock starts at the download loop, not batch start
# ---------------------------------------------------------------------------

def test_soft_clock_starts_at_loop_start():
    from services.youtube_pipeline_v2 import (
        _yt_discovery_deadlines,
        _yt_download_loop_decision,
    )

    # Job 66b52291 timeline: batch starts at T, search+rank take ~70 s,
    # the download loop starts at T+70. Budget 180 s, hard 300 s.
    batch_start = 1000.0
    loop_start = batch_start + 70.0
    soft, ceiling, hard = _yt_discovery_deadlines(batch_start, loop_start, 180, 300, 300)
    assert soft == batch_start + 250.0
    assert ceiling == batch_start + 370.0
    assert hard == batch_start + 300.0  # hard stays batch-anchored
    # At T+200 transcripts are still fetching: the old batch anchor would
    # already have fired soft_timeout here; the loop anchor must not.
    action, _ = _yt_download_loop_decision(
        batch_start + 200, hard_deadline=hard, soft_ceiling_deadline=ceiling,
        soft_deadline=soft, soft_enabled=True, ready_ratio=0.0,
        soft_coverage=0.0, discovery_stopped=False,
    )
    assert action == "continue"
    action, _ = _yt_download_loop_decision(
        batch_start + 260, hard_deadline=hard, soft_ceiling_deadline=ceiling,
        soft_deadline=soft, soft_enabled=True, ready_ratio=0.0,
        soft_coverage=0.0, discovery_stopped=False,
    )
    assert action == "soft_timeout"


def test_soft_clock_batch_anchor_with_knob_off(monkeypatch):
    from services.youtube_pipeline_v2 import _yt_discovery_deadlines

    monkeypatch.setenv("VIDZEO_YT_DISCOVERY_CLOCK_FROM_LOOP_START", "0")
    soft, _ceiling, hard = _yt_discovery_deadlines(1000.0, 1070.0, 180, 300, 300)
    assert soft == 1180.0
    assert hard == 1300.0


def test_zero_timeouts_give_no_deadlines():
    from services.youtube_pipeline_v2 import _yt_discovery_deadlines

    assert _yt_discovery_deadlines(1000.0, 1070.0, 0, 0, 0) == (None, None, None)


# ---------------------------------------------------------------------------
# W1: a stalled web batch stops feeding the same dead hosts
# ---------------------------------------------------------------------------

def test_stall_skips_dead_hosts_after_two_zero_chunks(monkeypatch):
    import services.web_image.resolver as resolver

    seen = []
    logs = []

    def fake_bulk(urls, out, max_concurrent=6, timeout_seconds=60,
                  headers_by_url=None, **kw):
        seen.append(list(urls))
        results = []
        for url in urls:
            host = url.split("/")[2]
            if "live-cdn" in host:
                results.append((url, None, "404 Not Found"))
            else:
                results.append((url, None, f"Cannot connect to host {host}:443 ssl"))
        return results

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)
    monkeypatch.setattr(resolver, "_web_log", lambda msg: logs.append(msg))
    urls = [
        "https://i.pinimg.com/a.jpg", "https://www.washingtonpost.com/b.jpg",
        "https://gtaboom.com/c.jpg", "https://i.pinimg.com/d.jpg",
        "https://www.washingtonpost.com/e.jpg", "https://gtaboom.com/f.jpg",
        "https://i.pinimg.com/g.jpg", "https://www.washingtonpost.com/h.jpg",
        "https://gtaboom.com/i.jpg", "https://live-cdn.example.com/j.jpg",
        "https://live-cdn.example.com/k.jpg", "https://live-cdn.example.com/l.jpg",
        "https://i.pinimg.com/m.jpg", "https://live-cdn.example.com/n.jpg",
    ]
    resolver._download_bulk_chunked(urls, Path("/tmp"), 6, 60)
    # Chunks 1-2 (6 urls each) land 0 on dead hosts; the stall trips;
    # chunk 3 must be filtered to the live-host URL only.
    assert len(seen) == 3
    assert seen[2] == ["https://live-cdn.example.com/n.jpg"]
    assert not any("i.pinimg.com/m.jpg" in chunk for chunk in seen[2:])
    assert any("[web-stall]" in line for line in logs)


def test_landed_chunk_resets_stall_counter(monkeypatch, tmp_path):
    import services.web_image.resolver as resolver

    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_CHUNK_SIZE", "3")
    landed_file = tmp_path / "img.jpg"
    landed_file.write_bytes(b"fake-jpeg")
    seen = []
    logs = []
    lands_url = "https://live-cdn.example.com/lands.jpg"

    def fake_bulk(urls, out, max_concurrent=6, timeout_seconds=60,
                  headers_by_url=None, **kw):
        seen.append(list(urls))
        results = []
        for url in urls:
            host = url.split("/")[2]
            if url == lands_url:
                results.append((url, landed_file, ""))
            elif "live-cdn" in host:
                results.append((url, None, "404 Not Found"))
            else:
                results.append((url, None, f"Cannot connect to host {host}:443 ssl"))
        return results

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)
    monkeypatch.setattr(resolver, "_web_log", lambda msg: logs.append(msg))
    urls = [
        # chunk 1: zero-land (1) — then chunk 2 lands one and resets.
        "https://i.pinimg.com/1.jpg", "https://gtaboom.com/2.jpg",
        "https://www.washingtonpost.com/3.jpg",
        # chunk 2: lands the live file, counter resets to 0.
        lands_url, "https://i.pinimg.com/4.jpg", "https://gtaboom.com/5.jpg",
        # chunk 3: zero-land (1). chunk 4: zero-land (2) — stall trips.
        "https://i.pinimg.com/6.jpg", "https://gtaboom.com/7.jpg",
        "https://www.washingtonpost.com/8.jpg",
        "https://i.pinimg.com/9.jpg", "https://gtaboom.com/10.jpg",
        "https://www.washingtonpost.com/11.jpg",
        # chunk 5: dead-host url must be skipped, live url attempted.
        "https://i.pinimg.com/12.jpg", "https://live-cdn.example.com/13.jpg",
    ]
    resolver._download_bulk_chunked(urls, tmp_path, 6, 60)
    assert len(seen) == 5
    assert seen[4] == ["https://live-cdn.example.com/13.jpg"]
    assert any("[web-stall]" in line for line in logs)


def test_stall_skip_disabled_by_knob(monkeypatch):
    import services.web_image.resolver as resolver

    monkeypatch.setenv("VIDZEO_WEB_STALL_ZERO_CHUNKS", "0")
    seen = []
    logs = []

    def fake_bulk(urls, out, max_concurrent=6, timeout_seconds=60,
                  headers_by_url=None, **kw):
        seen.append(list(urls))
        return [(u, None, "Cannot connect to host %s:443 ssl" % u.split("/")[2])
                for u in urls]

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)
    monkeypatch.setattr(resolver, "_web_log", lambda msg: logs.append(msg))
    urls = [f"https://i.pinimg.com/{i}.jpg" for i in range(13)]
    resolver._download_bulk_chunked(urls, Path("/tmp"), 6, 60)
    assert sum(len(chunk) for chunk in seen) == 13
    assert not any("[web-stall]" in line for line in logs)


def test_connection_error_classifier():
    import services.web_image.resolver as resolver

    assert resolver._is_connection_error("Cannot connect to host i.pinimg.com:443 ssl")
    assert resolver._is_connection_error("Connection refused")
    assert resolver._is_connection_error("SSL: CERTIFICATE_VERIFY_FAILED")
    # A slow host is not a dead host: read timeouts must not blacklist it.
    assert not resolver._is_connection_error("Read timed out (read timeout=60.0)")
    assert not resolver._is_connection_error("404 Not Found")
    assert not resolver._is_connection_error("")


# ---------------------------------------------------------------------------
# V1: a vision verdict that never returns is retried/escalated, never waited on
# ---------------------------------------------------------------------------

def test_vision_stall_seconds_default_and_knob(monkeypatch):
    import services.web_image.resolver as resolver

    monkeypatch.delenv("VIDZEO_VISION_STALL_SECONDS", raising=False)
    assert resolver._vision_stall_seconds() == 300.0
    monkeypatch.setenv("VIDZEO_VISION_STALL_SECONDS", "120")
    assert resolver._vision_stall_seconds() == 120.0
    monkeypatch.setenv("VIDZEO_VISION_STALL_SECONDS", "0")
    assert resolver._vision_stall_seconds() == 0.0
    monkeypatch.setenv("VIDZEO_VISION_STALL_SECONDS", "garbage")
    assert resolver._vision_stall_seconds() == 300.0


def test_visual_gate_join_has_stall_watchdog():
    source = (SERVER_ROOT / "services" / "web_image" / "resolver.py").read_text()
    # The join must name the stall, retry once, and escalate to the
    # degraded-unverified retention instead of waiting forever.
    assert "[vision-stall]" in source
    assert "stall_retried" in source
    assert '"visual_reason": "vision_stall"' in source
