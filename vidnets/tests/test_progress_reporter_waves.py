"""Progress-reporter wave handling (Broken Rail preparation_stall root cause).

A web recovery wave restarts its scene counter at 1/N after the primary wave
reached 50+/M. ``_make_progress_reporter`` treated every report from the new
wave as a stale subset count and silently dropped it, so a working 1,440s
web collection looked like 300s of silence to the preparation-stall
detector and the job was paused. These tests pin the forwarding behaviour
at the reporter boundary: a new wave must be visible, a shadow branch of
the wave still running must stay suppressed, and a "retrying N/M
unresolved" count must never be presented as N completed scenes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import app


def _reporter():
    calls = []

    def capture(fraction, message):
        calls.append((fraction, message))

    return app._make_progress_reporter(capture), calls


def test_sanitizer_does_not_count_retrying_scenes_as_completed():
    result = app._sanitize_user_progress_message(
        0.44, "V2 web recovery: retrying 120/122 unresolved scene(s)"
    )
    assert "120/122" not in result
    assert result == "Finding web images"


def test_reporter_forwards_new_wave_with_different_total():
    report, calls = _reporter()
    report(0.60, "Web image scene 50/121 checked (40 resolved)")
    assert calls, "primary wave report should be forwarded"
    report(0.58, "Web image scene 1/120 checked (1 resolved)")
    forwarded = [message for _fraction, message in calls]
    assert "Finding scene 1/120" in forwarded


def test_reporter_forwards_new_wave_after_completed_wave_same_total():
    report, calls = _reporter()
    report(0.60, "Web image scene 121/121 checked (100 resolved)")
    assert calls, "completed primary wave report should be forwarded"
    report(0.58, "Web image scene 1/121 checked (1 resolved)")
    forwarded = [message for _fraction, message in calls]
    assert "Finding scene 1/121" in forwarded


def test_reporter_still_ignores_shadow_branch_subset():
    report, calls = _reporter()
    report(0.60, "Web image scene 50/121 checked (40 resolved)")
    assert calls, "primary wave report should be forwarded"
    before = len(calls)
    report(0.58, "Web image scene 3/121 checked (3 resolved)")
    assert len(calls) == before, (
        "a subset count from a shadow branch of the wave still running "
        "must stay suppressed, not rewind the displayed count"
    )


def _progress_reporter():
    calls = []
    advances = []

    def capture(fraction, message):
        calls.append((fraction, message))

    reporter = app._make_progress_reporter(
        capture, on_progress=lambda: advances.append(1)
    )
    return reporter, calls, advances


def test_on_progress_fires_on_fraction_and_scene_advance_only():
    """The delivery budget must extend on real progress, never on a
    heartbeat re-emission: a stalled job must not keep its own deadline
    alive by re-emitting 'still working (Ns)'."""
    report, calls, advances = _progress_reporter()
    report(0.30, "Web image scene 3/14 checked (3 resolved)")
    assert len(advances) == 1
    # Heartbeat re-emissions: same fraction, no scene advance.
    report(0.30, "still working (300s)")
    report(0.30, "still working (400s)")
    assert len(advances) == 1
    # Fraction advance fires.
    report(0.35, "Web image scene 3/14 checked (3 resolved)")
    assert len(advances) == 2
    # Scene-count advance at the same fraction fires.
    report(0.35, "Web image scene 4/14 checked (4 resolved)")
    assert len(advances) == 3
    # Exact duplicate is dropped entirely (no raw call, no advance).
    before = len(calls)
    report(0.35, "Web image scene 4/14 checked (4 resolved)")
    assert len(calls) == before
    assert len(advances) == 3
