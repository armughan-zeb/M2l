"""Keep discovery from spending the time reserved for actual clip transfers."""
import math
import os


def youtube_clip_grace_seconds(transfer_seconds):
    """Use the same merge and verification allowance in the planner and worker."""
    try:
        configured = float(os.environ.get("VIDZEO_YT_CLIP_GRACE_SECONDS") or 0)
    except (TypeError, ValueError):
        configured = 0.0
    if math.isfinite(configured) and configured > 0:
        return max(15.0, min(120.0, configured))
    return max(20.0, min(90.0, transfer_seconds * 0.25))


def mixed_agent_cooldown_wall(config, environment=None):
    env = os.environ if environment is None else environment
    if (
        config.get('llm_provider') != 'external_agent'
        or config.get('yt_branch_wall_timeout_seconds')
        or str(env.get('VIDZEO_YT_COOLDOWN_RETRY', '1') or '1').strip().lower()
        not in {'1', 'true', 'yes', 'on'}
    ):
        return 0.0
    try:
        return max(0.0, min(1800.0, float(env.get('VIDZEO_MIXED_YT_AGENT_WALL_CAP_SECONDS', '1200') or 1200)))
    except (TypeError, ValueError):
        return 1200.0


def youtube_stage_budget(wall_seconds, config=None, *, scene_count=0, download_workers=1, vision_reserve_seconds=None):
    settings = config or {}

    def positive(value, default):
        try:
            number = float(value)
            return number if math.isfinite(number) and number > 0 else default
        except (ValueError, TypeError):
            return default

    wall = positive(wall_seconds, 240.0)
    # Vision reserve: the YT hard wall shrinks by the time verification
    # needs (one external-agent inbox round trip, >=300s), instead of
    # reserving only transfer time. The reserve is threaded from the
    # coordinator's vision_reserve_seconds / VIDZEO_VISION_RESERVE_SECONDS;
    # without it the stage keeps the old transfer-only reservation.
    if vision_reserve_seconds is None:
        vision_reserve_seconds = settings.get(
            'vision_reserve_seconds',
            settings.get('vision_reserve'),
        )
    if vision_reserve_seconds is None:
        vision_reserve_seconds = os.environ.get("VIDZEO_VISION_RESERVE_SECONDS")
    try:
        vision_reserve = max(0.0, float(vision_reserve_seconds or 0.0))
    except (TypeError, ValueError):
        vision_reserve = 0.0
    # Keep a small bounded provider-drain and parent-handoff margin. Reserving
    # 20% made the 150s mixed-media cap unusable for the three transfer waves
    # required by a 60% clip target on short films.
    internal = max(1.0, wall - min(18.0, wall * 0.10))
    # Nov 26 (user order: remove every timeout that stops a download): the
    # stage budget no longer publishes a hard wall or a per-clip ceiling.
    # ``internal`` below is planning input only (search ceiling, wave
    # sizing) — it never stops a download. The lane ends when its work is
    # done (every quota scene resolved or its candidates exhausted), not
    # when a clock fires. The cooldown recovery wall is still published:
    # it is a recovery allowance, not a kill.
    hard = min(internal, positive(settings.get('yt_hard_timeout_seconds'), internal))
    if vision_reserve:
        # Shrink the hard wall by the vision reserve (floored so a search
        # plus one minimum transfer still fits); the reserve is verification
        # time, not discovery time.
        hard = max(60.0, hard - vision_reserve)
    search_ceiling = min(120.0, hard * 0.28)
    search = min(search_ceiling, positive(settings.get('yt_search_timeout_seconds'), search_ceiling))
    if 0 < scene_count <= 12 and str(settings.get('llm_provider') or '') != 'external_agent':
        # Short non-agent jobs keep the fast 35s search cap. Agent jobs
        # answer through the local inbox and fan out dozens of yt-dlp
        # searches: 35s timed out the whole search phase with 0 candidates
        # even though a single yt-dlp search answers in ~3s. Agent jobs
        # keep the ceiling instead; the branch wall still bounds the stage.
        search = min(search, 35.0)
    plan = {'yt_search_timeout_seconds': search}
    if settings.get('muse_clip_job_id'):
        from .pc_youtube_clips import CLIP_WAIT_SECONDS
        # The remote runner has an independent download deadline followed by
        # a streamed transfer. Feed the full slot into transcript reservation.
        plan['yt_per_clip_timeout_seconds'] = CLIP_WAIT_SECONDS
        explicit_cap = positive(settings.get('yt_max_download_scenes'), 0)
        if scene_count > 0 and explicit_cap > 0:
            plan['yt_max_download_scenes'] = min(scene_count, max(1, int(explicit_cap)))
        return plan
    if vision_reserve:
        plan['vision_reserve_seconds'] = vision_reserve
    if settings.get('_yt_cooldown_headroom'):
        # The adapter supplies a first-wave cap. Keep it and give the cooldown
        # a separate deadline inside the job-clamped outer wall.
        recovery_wall = max(60.0, internal - vision_reserve) if vision_reserve else internal
        if recovery_wall > hard:
            plan['yt_cooldown_hard_timeout_seconds'] = recovery_wall
    if scene_count > 0 and not settings.get('yt_pool_first'):
        # Reserve enough time for a useful transfer attempt before expanding
        # discovery. The previous preselected-quota path spread a short wall
        # across every requested scene, reducing each clip attempt to 10–24s;
        # the live run then timed out healthy candidates and rendered graphics.
        # Nov 26 (user order): no per-clip ceiling is published any more —
        # the wave math below only sizes the search budget now; transfers
        # are no longer wall-clock bounded.
        handoff = 10.0
        grace = 20.0
        minimum_transfer = 45.0
        minimum_wave = minimum_transfer + grace
        minimum_search = min(30.0, search)
        max_complete_waves = int(max(0.0, hard - minimum_search - handoff) // minimum_wave)
        if max_complete_waves > 0:
            search_for_waves = max(0.0, hard - handoff - max_complete_waves * minimum_wave)
            search = min(search, search_for_waves)
            plan['yt_search_timeout_seconds'] = search
        explicit_cap = positive(settings.get('yt_max_download_scenes'), 0)
        if explicit_cap > 0:
            plan['yt_max_download_scenes'] = min(scene_count, max(1, int(explicit_cap)))
    return plan
