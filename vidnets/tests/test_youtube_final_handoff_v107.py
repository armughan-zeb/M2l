import time

import pytest

from services.visual_requirement import build_visual_requirement, _is_organization_entity
from services.youtube_pipeline_v2 import (
    _youtube_quality_formats, _candidate_source_height_floor, _record_source_resolution,
)
from services.media_resolution_coordinator import _late_local_verification_deadline, _quota_gate_is_hard_rejection
from services.visual_intelligence.semantic_guard import explicit_visual_facet_conflict


@pytest.mark.parametrize('title,text,expected', [
    ('Tesla Model 3', 'The Tesla Model 3 is an electric sedan.', 'automobile'),
    ('Apple nutrition', 'Fresh apples provide fiber and vitamin C.', 'named_entity'),
    ('The NFL', 'The NFL brings teams onto the football field.', 'organization'),
    ('NFL Four Downs', 'An NFL offense gets four downs to move ten yards.', 'organization'),
    ('Dallas Cowboys', 'The Dallas Cowboys take the field.', 'organization'),
])
def test_entities_keep_their_actual_domain(title, text, expected):
    req = build_visual_requirement(title=title, script=text, scene_text=text)
    assert req['subject_type'] == expected
    if title in {'The NFL', 'NFL Four Downs', 'Dallas Cowboys'}:
        assert req['domain'] == 'sports'
    if title == 'NFL Four Downs':
        assert req['canonical_entity'] == 'NFL'
    if title == 'Apple nutrition':
        assert not _is_organization_entity(title)


@pytest.mark.parametrize('source', [False, True])
def test_sd_fallback_reaches_both_download_lanes_with_bounds(monkeypatch, source):
    monkeypatch.setenv('VIDZEO_YT_ALLOW_SD_FALLBACK', '1')
    formats = _youtube_quality_formats(source=source, max_filesize=10000000 if source else None)
    assert 'height>=720' in formats[0]
    lower = [item for item in formats if 'height>=480' in item]
    assert lower
    assert all('height<=' in item for item in formats)
    if source:
        assert all('filesize' in item for item in lower)
    # 480p remains a bounded transport fallback, but only identified archive
    # material may be admitted below HD as a final scene source.
    assert _candidate_source_height_floor({'title': 'NFL footage'}) == 720
    row, audit = {}, {}
    _record_source_resolution(row, 480, audit)
    assert row == audit
    assert row['source_resolution_policy'] == 'sd_fallback'
    assert row['reduced_resolution_source'] and not row['archival_sd_source']


def test_hd_only_setting_and_explicit_archive_policy_remain_available(monkeypatch):
    monkeypatch.setenv('VIDZEO_YT_ALLOW_SD_FALLBACK', '0')
    assert _candidate_source_height_floor({'title': 'modern footage'}) == 720
    assert all('height>=720' in item for item in _youtube_quality_formats())
    monkeypatch.setenv('VIDZEO_YT_ALLOW_SD_FALLBACK', '1')
    assert _candidate_source_height_floor({'title': '1950 newsreel archive'}) == 360


def test_late_clip_gate_has_its_own_finite_window_and_respects_delivery_budget(monkeypatch):
    monkeypatch.setenv('VIDZEO_LATE_YT_VERIFICATION_SECONDS', '99999')
    now = time.monotonic()
    assert now + 89 <= _late_local_verification_deadline(2) <= now + 91
    assert _late_local_verification_deadline(2, now - 1) == now - 1
    assert _late_local_verification_deadline(2, now + 3) == now + 3
    monkeypatch.setenv('VIDZEO_LATE_YT_VERIFICATION_SECONDS', 'nan')
    assert time.monotonic() + 59 <= _late_local_verification_deadline(2) <= time.monotonic() + 61
    assert _quota_gate_is_hard_rejection('explicit_scene_conflict', {'accepted': False})
    assert not _quota_gate_is_hard_rejection('visual_deadline_exceeded', {})


def test_coordinator_reserves_the_actual_serial_transfer_wave_budget(monkeypatch):
    from services.pipeline_v2_media_adapter import _media_stage_deadline_seconds, mixed_youtube_time_budget
    monkeypatch.delenv('VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY', raising=False)
    config = {'media_deadline_seconds': 240, 'yt_branch_wall_timeout_seconds': 150,
              '_youtube_budget_scene_count': 4}
    wall, _ = mixed_youtube_time_budget(4, 150, download_workers=1, coverage_target=1.0)
    assert _media_stage_deadline_seconds(config, 'Mixed Media') >= wall + 180


def test_static_branding_is_not_action_but_legitimate_branding_remains_usable():
    logo = 'A static graphic of the NFL logo, a shield against a black background.'
    assert explicit_visual_facet_conflict('Before each snap, the offense chooses a formation.', logo) == 'static_branding_for_action_scene'
    assert explicit_visual_facet_conflict('The NFL shield logo identifies the league.', logo) == ''
    assert explicit_visual_facet_conflict('A quarterback throws a pass.', 'Players in formation as the quarterback throws the ball past a field logo.') == ''


def test_sports_queries_prefer_real_game_footage_and_keep_game_niches_separate():
    from services.youtube_pipeline_v2 import _score_candidate
    from services.visual_intelligence.semantic_guard import explicit_domain_conflict
    def score(title):
        return _score_candidate({'title': title, 'channel': 'NFL', 'duration': 600}, 'NFL',
            'An offense gets four downs to move ten yards.', 'NFL football game footage', {'domain_category': 'sports'})
    assert score('NFL Chiefs vs Ravens Game Highlights') > score('NFL four downs explained breakdown')
    assert explicit_domain_conflict('NFL football rules', 'Madden NFL 26 footage') == 'simulation_for_real_world'
    assert explicit_domain_conflict('Madden NFL gameplay', 'Madden NFL 26 footage') == ''


def test_direct_clip_lane_preserves_measured_live_motion(monkeypatch):
    from services import youtube_pool_renderer as pool
    from services.youtube_pipeline_v2 import _screen_downloaded_motion
    monkeypatch.setenv('VIDZEO_YT_LENIENT_ACCEPTANCE', '0')
    real_game = {'checked': True, 'reject': True, 'reason': 'static_image_or_pan',
                 'motion_mean': 40.238, 'motion_p90': 54.541, 'motion_changed_fraction': .7735,
                 'perceptual_hold_fraction': 0, 'optical_flow_pairs': 9,
                 'optical_flow_median': 1.145, 'optical_flow_coherence': .553}
    monkeypatch.setattr(pool, '_screen_moment_quality', lambda _path: real_game)
    assert not _screen_downloaded_motion('game.mp4', {})['reject']
    assert real_game['reject']  # Do not mutate an existing audit/cache record.
    monkeypatch.setattr(pool, '_screen_moment_quality', lambda _path: {
        'checked': True, 'reject': True, 'reason': 'static_image_or_pan', 'motion_mean': .1})
    assert _screen_downloaded_motion('still.mp4', {})['reject']


def test_title_card_screen_reuses_decoded_samples_without_unbounded_capture(monkeypatch):
    import inspect
    from services import youtube_pool_renderer as pool
    from services.youtube_pipeline_v2 import _clean_downloaded_clip_overlays
    monkeypatch.setattr(pool, '_screen_moment_overlays', lambda *_args, **_kw: {
        'checked': True, 'clean': True, 'reject': False,
        'title_card_samples': [[.3, .6]] * 4})
    path, audit = _clean_downloaded_clip_overlays('card.mp4')
    assert path == '' and audit['rejection_reason'] == 'title_card_or_large_text_rejected'
    assert 'VideoCapture(' not in inspect.getsource(_clean_downloaded_clip_overlays)
    monkeypatch.setattr(pool, '_screen_moment_overlays', lambda *_args, **_kw: {
        'checked': True, 'clean': True, 'reject': False,
        'title_card_samples': [[.05, .1]] * 4})
    assert _clean_downloaded_clip_overlays('football.mp4')[0] == 'football.mp4'


def test_small_host_reserves_serial_download_waves_before_child_memory_changes(monkeypatch):
    import psutil
    from types import SimpleNamespace
    import server.app as app
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(
        total=8 * 1024 ** 3, available=3 * 1024 ** 3))
    assert app._mixed_youtube_download_worker_capacity({'yt_download_concurrency': 5}) == 1
    assert app._mixed_youtube_branch_wall_cap_seconds({'_youtube_quota_preselected': True}, 5) >= 503


def test_active_transcription_keeps_its_model_when_idle_cache_is_released(monkeypatch):
    from services import transcription_service as service
    model = object()
    monkeypatch.setattr(service, '_whisper_model', model)
    monkeypatch.setattr(service, '_available', True)
    monkeypatch.setattr(service, '_whisper_backend', 'test')
    def infer(owned, *_args, **_kwargs):
        service.unload_whisper_model()
        assert owned is model and service._whisper_model is None
        return [{'word': 'four', 'start': 0, 'end': 1}], []
    monkeypatch.setattr(service, '_transcribe_with_model', infer)
    assert service._transcribe_audio_local('test.wav')[0][0]['word'] == 'four'


@pytest.mark.parametrize('clip_passes', [True, False])
@pytest.mark.parametrize('render_reserve_elapsed', [False, True])
def test_completed_clip_is_checked_after_primary_window_without_overriding_wrong_entity(monkeypatch, tmp_path, clip_passes, render_reserve_elapsed):
    from services import media_resolution_coordinator as coordinator
    real_time = time
    offset = [0.0]
    class Clock:
        def monotonic(self): return real_time.monotonic() + offset[0]
        def __getattr__(self, key): return getattr(real_time, key)
    monkeypatch.setattr(coordinator, 'time', Clock())
    monkeypatch.setattr(coordinator, '_candidate_hard_gate', lambda *_: (True, 'test_transport_pass'))
    clip = tmp_path / 'late.mp4'
    clip.write_bytes(b'local-cut' * 1024)
    ready = [False]
    class Lane:
        def __call__(self, *_): return []
        def get_row(self, *_args, **_kwargs):
            return {'source': 'youtube', 'path': str(clip), 'media_type': 'video', 'score': 95} if ready[0] else None
    calls = []
    def verify(_scene, candidate):
        calls.append(candidate['source'])
        if candidate['source'] == 'web':
            ready[0] = True
            offset[0] = 1000.0  # Discovery and its original gate are now expired.
            return {'accepted': True, 'status': 'accepted', 'score': 90, 'visual_checked': True}
        return {'accepted': clip_passes, 'status': 'accepted' if clip_passes else 'rejected',
                'score': 95 if clip_passes else 0, 'visual_checked': True,
                'visual_available': True, 'fatal_mismatch': not clip_passes,
                'reason': 'actual scene footage' if clip_passes else 'wrong entity'}
    row = {'scene_id': 'late', 'scene_index': 0, 'text': 'A quarterback throws the ball.',
           'source_policy': {'mixed_media': True, 'youtube_quota': True, 'visual_verification_required': True}}
    from services.job_context import job_scope
    class Budget:
        render_reserve_seconds = 600
        render_started = False
        def allowance(self, requested, reserve_after_seconds=0):
            # Discovery is beyond the old reserve boundary after the fake
            # clock advances 1000s, but the actual 1200s job wall remains.
            return max(0, 1200 - reserve_after_seconds)
    with job_scope({'delivery_budget': Budget()} if render_reserve_elapsed else {}):
        resolved = coordinator.resolve_storyboard_media_v2([row], youtube_lane=Lane(),
            web_lane=lambda *_: {'source': 'web', 'path': str(tmp_path / 'verified.jpg'), 'score': 90},
            visual_verifier=verify, neutral_factory=lambda *_: None, deadline_seconds=1)
    assert 'youtube' in calls
    assert resolved[0].provider == ('youtube' if clip_passes else 'web')
