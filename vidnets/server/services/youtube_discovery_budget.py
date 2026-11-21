"""Discovery time budget for the two-track YouTube lane.

The YouTube discovery budget bounds ONLY discovery: search, metadata rank,
transcript fetch, probe download, and vision verification. Clip downloads
are exempt: once a clip is vision-verified, its ranged download is queued
to a background download queue and may finish after discovery ends.

Budget rule (user-ordered): 5 minutes of discovery per minute of video.
``budget_seconds = 300 * duration_minutes`` with NO cap. The rate is a
configurable setting so it can be shifted:

* config key ``yt_discovery_seconds_per_minute`` (job config wins), else
* env ``VIDZEO_YT_DISCOVERY_SECONDS_PER_MINUTE``, else
* default 300.0.

The discovery budget bounds ONLY discovery. The media stage as a whole is
bounded by ``total_media_budget_seconds`` — discovery budget plus the
post-discovery tail (default 120s, config ``yt_media_tail_seconds`` /
env ``VIDZEO_YT_MEDIA_TAIL_SECONDS``). The tail is the wall for the
review gate, the recovery lanes, and the sequential inline finalization
after them; without it those phases ran against the 3600s media wall
(GTA test 18: ~22 min of sequential inline vision after discovery stopped).

This module is pure: no provider calls, no threads, no coordinator state.
The coordinator owns the clock; this module only computes it.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Mapping, Optional

DEFAULT_SECONDS_PER_MINUTE = 300.0
ENV_SECONDS_PER_MINUTE = "VIDZEO_YT_DISCOVERY_SECONDS_PER_MINUTE"
CONFIG_SECONDS_PER_MINUTE = "yt_discovery_seconds_per_minute"

# Post-discovery tail (seconds). The discovery budget bounds ONLY discovery
# (search, rank, transcript, probe, vision). The review gate, the recovery
# lanes, and the sequential inline finalization that follows it (quota-inline
# and frame-fallback vision calls) used to run against the coordinator's
# 3600s media wall instead — GTA test 18 (job 2451ee04) spent ~22 min there
# on 17 sequential external-agent vision calls after discovery had already
# stopped, because the per-scene budget is bypassed for quota clips and
# frame fallbacks and the global visual-gate deadline sat ~57 min out.
# The tail bounds all of that post-discovery vision: total media time is
# discovery budget + tail. Default 120s (Nov 21, 2026, user-ordered):
# a 1-min video targets ~5-8 min of media (300s discovery + 120s tail
# = 420s), with scenes whose YT clip was not verified in time falling
# back to the web image the parallel web lane already resolved, instead
# of waiting out a long tail for more clips. The old 600s default is
# still available via config/env. The 3600s media wall stays the absolute
# backstop. Config key ``yt_media_tail_seconds`` wins, else env
# ``VIDZEO_YT_MEDIA_TAIL_SECONDS``, else 120s.
DEFAULT_MEDIA_TAIL_SECONDS = 120.0
ENV_MEDIA_TAIL_SECONDS = "VIDZEO_YT_MEDIA_TAIL_SECONDS"
CONFIG_MEDIA_TAIL_SECONDS = "yt_media_tail_seconds"
MAX_MEDIA_TAIL_SECONDS = 3600.0


def _positive_number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number <= 0:
        return default
    return number


def discovery_seconds_per_minute(config: Optional[Mapping[str, Any]] = None) -> float:
    """Return the configured discovery rate (seconds of discovery per minute
    of video). Job config wins over env; env wins over the 300s default."""
    settings = config or {}
    if settings.get(CONFIG_SECONDS_PER_MINUTE) is not None:
        return _positive_number(
            settings.get(CONFIG_SECONDS_PER_MINUTE), DEFAULT_SECONDS_PER_MINUTE
        )
    return _positive_number(
        os.environ.get(ENV_SECONDS_PER_MINUTE), DEFAULT_SECONDS_PER_MINUTE
    )


def discovery_budget_seconds(
    duration_minutes: Any,
    *,
    seconds_per_minute: Optional[float] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> float:
    """Discovery budget in seconds for a video of ``duration_minutes``.

    ``budget = rate * duration_minutes`` with NO cap. A zero/unknown
    duration falls back to one minute of budget so discovery still runs.
    """
    rate = _positive_number(
        seconds_per_minute,
        discovery_seconds_per_minute(config),
    )
    try:
        minutes = float(duration_minutes)
    except (TypeError, ValueError):
        minutes = 0.0
    if not math.isfinite(minutes) or minutes <= 0:
        minutes = 1.0
    return rate * minutes


def media_tail_seconds(config: Optional[Mapping[str, Any]] = None) -> float:
    """Post-discovery tail in seconds (review + recovery + inline finalization).

    Job config key ``yt_media_tail_seconds`` wins over env
    ``VIDZEO_YT_MEDIA_TAIL_SECONDS``; both fall back to the 120s default.
    Clamped to [0, 3600]: 0 means no post-discovery vision time at all
    (scenes resolve with cached/best-so-far verdicts), 3600 keeps the tail
    inside the media wall.
    """
    settings = config or {}
    raw = settings.get(CONFIG_MEDIA_TAIL_SECONDS)
    if raw is None:
        raw = os.environ.get(ENV_MEDIA_TAIL_SECONDS)
    try:
        seconds = float(raw) if raw is not None else DEFAULT_MEDIA_TAIL_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_MEDIA_TAIL_SECONDS
    if not math.isfinite(seconds):
        return DEFAULT_MEDIA_TAIL_SECONDS
    return max(0.0, min(MAX_MEDIA_TAIL_SECONDS, seconds))


def total_media_budget_seconds(
    discovery_budget: Any,
    *,
    config: Optional[Mapping[str, Any]] = None,
    tail_seconds: Optional[float] = None,
) -> float:
    """Bounded total media time: discovery budget + post-discovery tail.

    This is the wall the media stage must finish inside: discovery runs
    under its own budget, and every post-discovery vision phase (parallel
    review, recovery lanes, sequential inline finalization) stops at this
    total. Downloads stay exempt — they drain in the background to the
    transport hard deadline — but the stage never waits on them past the
    total.
    """
    try:
        discovery = float(discovery_budget)
    except (TypeError, ValueError):
        discovery = DEFAULT_SECONDS_PER_MINUTE
    if not math.isfinite(discovery) or discovery < 0:
        discovery = DEFAULT_SECONDS_PER_MINUTE
    tail = media_tail_seconds(config) if tail_seconds is None else tail_seconds
    try:
        tail = float(tail)
    except (TypeError, ValueError):
        tail = DEFAULT_MEDIA_TAIL_SECONDS
    if not math.isfinite(tail) or tail < 0:
        tail = DEFAULT_MEDIA_TAIL_SECONDS
    return discovery + min(MAX_MEDIA_TAIL_SECONDS, tail)


def video_duration_minutes(duration_seconds: Any, scene_count: int = 0) -> float:
    """Best-effort video duration in minutes.

    Prefers an explicit duration in seconds. When no duration is known,
    estimates from the scene count using the narration pace the pipeline
    already assumes (~4.5s per scene: a 1-minute video has ~13 scenes).
    """
    try:
        seconds = float(duration_seconds)
    except (TypeError, ValueError):
        seconds = 0.0
    if math.isfinite(seconds) and seconds > 0:
        return seconds / 60.0
    if scene_count and scene_count > 0:
        return (float(scene_count) * 4.5) / 60.0
    return 1.0


class DiscoveryBudget:
    """A wall-clock deadline for YouTube discovery.

    Discovery (search, rank, transcript, probe, vision) must stop when the
    budget expires. Downloads are NOT governed by this clock: they are
    queued to the background download queue and drain after discovery ends.
    """

    def __init__(
        self,
        budget_seconds: float,
        *,
        started_at: Optional[float] = None,
    ) -> None:
        self.budget_seconds = max(1.0, float(budget_seconds))
        self.started_at = (
            float(started_at) if started_at is not None else time.monotonic()
        )

    @property
    def deadline_at(self) -> float:
        return self.started_at + self.budget_seconds

    def elapsed(self, now: Optional[float] = None) -> float:
        return (time.monotonic() if now is None else now) - self.started_at

    def remaining(self, now: Optional[float] = None) -> float:
        return max(0.0, self.deadline_at - (time.monotonic() if now is None else now))

    def expired(self, now: Optional[float] = None) -> bool:
        return self.remaining(now) <= 0.0

    def to_dict(self) -> dict:
        return {
            "budget_seconds": round(self.budget_seconds, 3),
            "started_at_monotonic": round(self.started_at, 3),
            "deadline_at_monotonic": round(self.deadline_at, 3),
            "elapsed_seconds": round(self.elapsed(), 3),
            "remaining_seconds": round(self.remaining(), 3),
            "expired": self.expired(),
        }
