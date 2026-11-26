"""Keep discovery from spending the time reserved for actual clip transfers."""
import math
import os


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


def youtube_clip_grace_seconds(transfer_seconds):
    """Use the same merge and verification allowance in the planner and worker."""
    try:
        configured = float(os.environ.get("VIDZEO_YT_CLIP_GRACE_SECONDS") or 0)
    except (TypeError, ValueError):
        configured = 0.0
    if math.isfinite(configured) and configured > 0:
        return max(15.0, min(120.0, configured))
    return max(20.0, min(90.0, transfer_seconds * 0.25))


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
    # Deduct global review headroom once from the outer window, then apply
    # the already-computed first-wave cap. Subtracting it from that cap too
    # turned a 480s first wave into 180s inside a 1200s job.
    transport_wall = max(min(60.0, internal), internal - vision_reserve)
    hard = min(transport_wall, positive(settings.get('yt_hard_timeout_seconds'), transport_wall))
    search_ceiling = min(120.0, hard * 0.28)
    search = min(search_ceiling, positive(settings.get('yt_search_timeout_seconds'), search_ceiling))
    if 0 < scene_count <= 12 and str(settings.get('llm_provider') or '') != 'external_agent':
        # Short non-agent jobs keep the fast 35s search cap. Agent jobs
        # answer through the local inbox and fan out dozens of yt-dlp
        # searches: 35s timed out the whole search phase with 0 candidates
        # even though a single yt-dlp search answers in ~3s. Agent jobs
        # keep the ceiling instead; the branch wall still bounds the stage.
        search = min(search, 35.0)
    plan = {'yt_search_timeout_seconds': search, 'yt_hard_timeout_seconds': hard}
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
        if transport_wall > hard:
            plan['yt_cooldown_hard_timeout_seconds'] = transport_wall
    if scene_count > 0 and not settings.get('yt_pool_first'):
        preselected = bool(settings.get('_youtube_quota_preselected'))
        try:
            coverage = 1.0 if preselected else float(settings.get('youtube_accepted_coverage_target',
                                                                    settings.get('youtube_coverage_target', 0.60)))
            if not math.isfinite(coverage):
                coverage = 1.0 if preselected else 0.60
        except (TypeError, ValueError):
            coverage = 1.0 if preselected else 0.60
        coverage = max(0.0, min(1.0, coverage))
        target_scenes = max(1, math.ceil(scene_count * coverage))
        waves = math.ceil(target_scenes / max(1, int(download_workers)))
        # Reserve enough time for a useful transfer attempt before expanding
        # discovery. The previous preselected-quota path spread a short wall
        # across every requested scene, reducing each clip attempt to 10–24s;
        # the live run then timed out healthy candidates and rendered graphics.
        # The working app budgets 60s for transport plus 20s for merge/probe.
        # The shared deadline bounds actual work; wave estimates must not
        # remove later scenes before fast transfers/cache hits can reach them.
        handoff = 10.0
        minimum_transfer = 60.0
        minimum_wave = minimum_transfer + youtube_clip_grace_seconds(minimum_transfer)
        minimum_search = min(30.0, search)
        reserved_waves = min(waves, int(max(0.0, hard - minimum_search - handoff) // minimum_wave))
        if reserved_waves > 0:
            search_for_waves = max(0.0, hard - handoff - reserved_waves * minimum_wave)
            search = min(search, search_for_waves)
            plan['yt_search_timeout_seconds'] = search
        available = max(0.0, hard - search - handoff)
        schedulable_waves = int(available // minimum_wave)
        effective_waves = max(1, min(waves, schedulable_waves or 1))
        timeout_floor = minimum_transfer if schedulable_waves > 0 else 10.0
        slot_seconds = available / effective_waves
        upper_clip = min(150.0, max(timeout_floor, slot_seconds - youtube_clip_grace_seconds(0)))
        plan['yt_per_clip_timeout_seconds'] = max(
            timeout_floor, min(upper_clip, slot_seconds - youtube_clip_grace_seconds(upper_clip)),
        )
        explicit_cap = positive(settings.get('yt_max_download_scenes'), 0)
        if explicit_cap > 0:
            plan['yt_max_download_scenes'] = min(scene_count, max(1, int(explicit_cap)))
    return plan
