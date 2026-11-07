"""Keep discovery from spending the time reserved for actual clip transfers."""
import math


def youtube_stage_budget(wall_seconds, config=None, *, scene_count=0, download_workers=1):
    settings = config or {}

    def positive(value, default):
        try:
            number = float(value)
            return number if math.isfinite(number) and number > 0 else default
        except (ValueError, TypeError):
            return default

    wall = positive(wall_seconds, 240.0)
    # Keep a small bounded provider-drain and parent-handoff margin. Reserving
    # 20% made the 150s mixed-media cap unusable for the three transfer waves
    # required by a 60% clip target on short films.
    internal = max(1.0, wall - min(18.0, wall * 0.10))
    hard = min(internal, positive(settings.get('yt_hard_timeout_seconds'), internal))
    search_ceiling = min(120.0, hard * 0.28)
    search = min(search_ceiling, positive(settings.get('yt_search_timeout_seconds'), search_ceiling))
    if 0 < scene_count <= 12:
        search = min(search, 35.0)
    plan = {'yt_search_timeout_seconds': search, 'yt_hard_timeout_seconds': hard}
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
        # Keep at least 45s for a transfer and 20s for merge/probe. The shared
        # deadline bounds actual work; pessimistic wave estimates must not
        # remove later scenes before fast transfers/cache hits can reach them.
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
        available = max(0.0, hard - search - handoff)
        schedulable_waves = int(available // minimum_wave)
        effective_waves = max(1, min(waves, schedulable_waves or 1))
        timeout_floor = minimum_transfer if schedulable_waves > 0 else 10.0
        timeout_ceiling = max(timeout_floor, (available / effective_waves) - grace)
        plan['yt_per_clip_timeout_seconds'] = min(150.0, timeout_ceiling)
        explicit_cap = positive(settings.get('yt_max_download_scenes'), 0)
        if explicit_cap > 0:
            plan['yt_max_download_scenes'] = min(scene_count, max(1, int(explicit_cap)))
    return plan
