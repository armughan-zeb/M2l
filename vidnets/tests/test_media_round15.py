"""Round 15 (Nov 24, 2026): transcript-skip hierarchy + two-track soft vision.

User order: "Make a good hierarchy you can skip transcripts and go for
the metadata, only use transcripts when available, some videos don't
have, then see other meta data and metrics you can use the random parts
of the video, still not, fallback to the web, but I hope you mostly
accept the related clips to the topic as well if not scene by scene, u
can use general, Accordingly, so vision can't reject and can accept
it.. that would be better. In this soft vision we can get max clips, it
would see both, related to the specific title or the topic or the niche,
the video is about, and secondly, related scene by scene context etc
both gets accepted."

Hierarchy: metadata first; transcripts only when available (never a
gate); heuristic/random cut when no transcript moment; web fallback per
scene. Soft vision, two tracks: accept when the frames show the video's
overall topic/title/niche OR the scene's specific content; reject on
relevance only when they show neither.

What stays blocked (standing orders): fake/concept trailers, streamer/
reaction packages, copyright-high-risk channels, wrong entity/person,
wrong domain, talking head, animation for real subjects, era conflicts,
and the 2002 Vice City cutscene.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.visual_intelligence.prompts import (
    build_clip_batch_prompt,
    build_clip_prompt,
)
from services.youtube_pipeline_v2 import (
    _RangedMomentOverlap,
    _score_candidate,
)


LUCIA_SCENE = (
    "At the center of the story are Lucia and Jason, two outlaws whose "
    "bond is tested by a conspiracy that reaches across the state."
)
LUCIA_CONTEXT = {
    "primary_container_subject": "Grand Theft Auto VI",
    "domain_anchor": "Grand Theft Auto VI",
    "container_subject": "Grand Theft Auto VI",
    "domain_category": "gaming",
}


def test_topic_anchor_candidate_survives_scene_subject_mismatch():
    # A general VI gameplay clip for the Lucia scene: it names the topic
    # anchor, not the scene's micro-subject — it must survive metadata
    # and rank, not die as scene_subject_mismatch.
    candidate = {
        "title": "Grand Theft Auto VI gameplay Vice City",
        "description": "Open-world driving in Leonida.",
        "channel": "Rockstar Games",
        "duration": 300,
    }
    score = _score_candidate(
        candidate, "Lucia and Jason", LUCIA_SCENE,
        "Grand Theft Auto VI gameplay", LUCIA_CONTEXT,
    )
    assert score > 0, f"topic clip must rank, got {score}"
    assert candidate.get("v2_reject_reason") != "scene_subject_mismatch"


def test_off_topic_candidate_still_rejected():
    # Matches neither the scene subject nor the topic anchor: genuinely
    # off-topic, still a hard reject — for a distinct-subject scene
    # (Lucia vs the GTA VI anchor) that is scene_subject_mismatch, the
    # same rule that keeps a Ronaldo clip out of a Haaland scene.
    candidate = {
        "title": "How to make sourdough bread at home",
        "description": "Baking bread step by step in a home kitchen.",
        "channel": "Home Kitchen",
        "duration": 600,
    }
    score = _score_candidate(
        candidate, "Lucia and Jason", LUCIA_SCENE,
        "Grand Theft Auto VI gameplay", LUCIA_CONTEXT,
    )
    assert score == -999.0, f"off-topic clip must be rejected, got {score}"
    assert candidate.get("v2_reject_reason") == "scene_subject_mismatch"


def _fake_prepare_factory(release, moments, audit=None):
    def _fake_prepare(rows, assignments, pools, durations, *, session_jar=None,
                      deadline_seconds=None, cancellation=None):
        release.wait(timeout=30.0)
        return moments, dict(audit or {"status": "completed",
                                       "sources_requested": len(moments),
                                       "scenes_with_moments": len(moments)})
    return _fake_prepare


def test_ranged_overlap_no_wait_when_heuristic_cut_exists():
    # Transcripts are opportunistic, never a gate: when the assigned
    # source is long enough for a heuristic cut, the scene returns landed
    # moments at once instead of waiting for transcripts that may never
    # come.
    release = threading.Event()
    overlap = _RangedMomentOverlap(
        _fake_prepare_factory(release, {}),
        [{}], [{"id": "Bhwj-_9Xz6M", "duration": 300.0}], {}, [9.0],
        deadline_seconds=30.0)
    try:
        overlap.start()
        started = time.monotonic()
        assert overlap.moments_for_scene(0) == []
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        overlap.resolve()


def test_ranged_overlap_waits_when_no_heuristic_cut_possible():
    # No usable timestamp AND no known source duration: the scene still
    # waits, bounded by the ranged deadline, then falls back the same way.
    release = threading.Event()
    overlap = _RangedMomentOverlap(
        _fake_prepare_factory(release, {}),
        [{}], [{"id": "Bhwj-_9Xz6M"}], {}, [9.0],
        deadline_seconds=0.8)
    try:
        overlap.start()
        started = time.monotonic()
        assert overlap.moments_for_scene(0) == []
        elapsed = time.monotonic() - started
        assert 0.6 < elapsed < 5.0, f"bounded wait expected, took {elapsed:.2f}s"
    finally:
        release.set()
        overlap.resolve()


def test_clip_prompt_states_two_track_acceptance():
    prompt = build_clip_prompt(
        topic="Grand Theft Auto VI",
        narration="Rockstar Games is rebuilding Leonida.",
        subject="Grand Theft Auto VI",
        frame_count=2,
    )
    assert "TWO-TRACK ACCEPTANCE" in prompt
    assert "overall topic/subject" in prompt
    assert "NEITHER the topic NOR the scene" in prompt


def test_clip_batch_prompt_states_two_track_acceptance():
    prompt = build_clip_batch_prompt(
        "Grand Theft Auto VI",
        "Rockstar Games is rebuilding Leonida.",
        "Grand Theft Auto VI",
        ["clipA", "clipB"],
    )
    assert "TWO-TRACK ACCEPTANCE" in prompt
    assert "NEITHER the documentary's topic/subject NOR the scene's content" in prompt
