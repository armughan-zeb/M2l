import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.youtube_stage_budget import youtube_stage_budget
from services.youtube_clip_provenance import write_clip_provenance, load_clip_provenance


@pytest.mark.parametrize('wall', [30, 120, 240, 480, 900])
def test_search_reserves_majority_of_worker_wall_for_downloads(wall):
    plan = youtube_stage_budget(wall)
    assert 0 < plan['yt_search_timeout_seconds'] <= plan['yt_hard_timeout_seconds'] * .28
    assert plan['yt_hard_timeout_seconds'] < wall


def test_explicit_shorter_stage_limits_remain_authoritative():
    plan = youtube_stage_budget(240, {'yt_search_timeout_seconds': 15, 'yt_hard_timeout_seconds': 100})
    assert plan == {'yt_search_timeout_seconds': 15, 'yt_hard_timeout_seconds': 100}
    excessive = youtube_stage_budget(240, {'yt_search_timeout_seconds': 600, 'yt_hard_timeout_seconds': 1000})
    assert excessive['yt_hard_timeout_seconds'] == 222
    assert excessive['yt_search_timeout_seconds'] == pytest.approx(62.16)


def test_cut_provenance_is_bound_to_exact_pixels_and_scene(tmp_path):
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'first clip')
    write_clip_provenance(clip, 2, {'status': 'downloaded', 'video_id': 'source-1', 'timestamp': 45, 'clip_duration': 4})
    assert load_clip_provenance(clip, 2)['timestamp'] == 45
    assert not load_clip_provenance(clip, 3)
    clip.write_bytes(b'changed clip')
    assert not load_clip_provenance(clip, 2)


def test_per_transfer_budget_scales_with_actual_download_waves():
    serial = youtube_stage_budget(240, scene_count=5, download_workers=1)
    parallel = youtube_stage_budget(240, scene_count=5, download_workers=2)
    # Both may hit the minimum usable HD transfer window. More workers must
    # never reduce it; use a larger wall to exercise actual wave scaling.
    assert serial['yt_per_clip_timeout_seconds'] <= parallel['yt_per_clip_timeout_seconds']
    assert serial['yt_per_clip_timeout_seconds'] >= 45
    assert youtube_stage_budget(480, scene_count=5, download_workers=1)['yt_per_clip_timeout_seconds'] < youtube_stage_budget(480, scene_count=5, download_workers=2)['yt_per_clip_timeout_seconds']
    assert 'yt_per_clip_timeout_seconds' not in youtube_stage_budget(240, {'yt_pool_first': True}, scene_count=5)


def test_preselected_quota_keeps_useful_transfer_windows_without_truncating_scenes():
    plan = youtube_stage_budget(
        420,
        {'_youtube_quota_preselected': True},
        scene_count=35,
        download_workers=4,
    )
    assert plan['yt_per_clip_timeout_seconds'] >= 45
    assert 'yt_max_download_scenes' not in plan
    assert plan['yt_search_timeout_seconds'] <= 67
    assert plan['yt_hard_timeout_seconds'] < 420


def test_explicit_user_scene_cap_remains_authoritative():
    plan = youtube_stage_budget(420, {'_youtube_quota_preselected': True,
        'yt_max_download_scenes': 7}, scene_count=35, download_workers=4)
    assert plan['yt_max_download_scenes'] == 7


def test_salvage_retains_original_master_and_timestamp(tmp_path):
    import app
    clip = tmp_path / 'yt_clip_scene_001.mp4'
    clip.write_bytes(b'clip')
    write_clip_provenance(clip, 0, {'status': 'downloaded', 'video_id': 'original-source',
        'timestamp': 123, 'clip_duration': 4, 'source_url': 'https://youtu.be/original-source'})
    result = app._build_youtube_salvage_result([str(clip)], [4])
    moment = result.audit['retrieval_visual_decisions']['0']['source_moment']
    assert moment['video_id'] == 'original-source'
    assert moment['start'] == 123
    assert moment['end'] == 127
    assert result.audit['cut_audit'][0]['no_blur'] is True


@pytest.mark.parametrize('failure_kind', ['download', 'overlay', 'transcript'])
def test_every_scene_gets_a_first_attempt_before_retry_and_keeps_its_timestamp(monkeypatch, tmp_path, failure_kind):
    from services import youtube_pipeline_v2 as yt
    from services import youtube_media_cache as cache
    rows = [{'scene_index': i, 'scene_text': f'scene{i}', 'candidate_subject': 'Corolla',
             'section_subject': 'Corolla', 'domain_category': 'automotive'} for i in range(3)]
    assigned = [dict(id=name, title='Corolla road footage', duration=300, best_timestamp=start,
                     owner=i, webpage_url='https://youtu.be/' + name)
                for i, (name, start) in enumerate([('bad0', 20), ('good1', 60), ('good2', 100)])]
    alternative = dict(assigned[0], id='good0', best_timestamp=150)
    monkeypatch.delenv("VIDZEO_YT_PROGRESSIVE_ATTEMPTS", raising=False)
    monkeypatch.setattr(yt, '_build_scene_rows', lambda *_args: rows)
    monkeypatch.setattr(yt, '_search_container_pools', lambda *_args, **_kw: {'corolla': assigned + [alternative]})
    monkeypatch.setattr(yt, '_assign_candidates', lambda *_args, **_kw: assigned)
    monkeypatch.setattr(yt, '_score_candidate', lambda c, subject, text, *_args: 100 if text == f"scene{c['owner']}" else -1000)
    monkeypatch.setattr(yt, '_ranged_download_worker_limit', lambda *_args: 1)
    monkeypatch.setattr(yt, '_get_authenticated_youtube_session', lambda **_kw: None)
    if failure_kind == 'transcript':
        from services import youtube_ranged_moments
        monkeypatch.setattr(youtube_ranged_moments, 'prepare_ranged_moments', lambda *_args, **_kwargs:
            ({0: [{'video_id': 'bad0', 'source_id': 'bad0', 'start': 180., 'end': 184.,
                    'match_score': 90., 'heuristic': False}]}, {'scenes_with_moments': 1}))
    monkeypatch.setattr(cache, 'get_verified_cut', lambda *_args: None)
    monkeypatch.setattr(cache, 'put_verified_cut', lambda *_args, **_kw: None)
    calls = []

    def overlay(path, **_kwargs):
        if failure_kind == 'overlay' and len(calls) == 1:
            return None, {'checked': True, 'rejection_reason': 'confident_talking_head_rejected'}
        return path, {'checked': True, 'clean': True}
    monkeypatch.setattr(yt, '_clean_downloaded_clip_overlays', overlay)

    def download(candidate, out, start, duration, **_kw):
        calls.append((candidate['id'], start))
        if candidate['id'] == 'bad0' and failure_kind == 'download':
            return False
        out.write_bytes(b'x' * 8192)
        return True

    monkeypatch.setattr(yt, '_download_clip', download)
    result = yt.get_youtube_clips('Corolla road footage', 'Corolla', num_scenes=3,
        scene_texts=['scene0', 'scene1', 'scene2'], scene_durations=[4, 4, 4],
        visual_context=SimpleNamespace(enabled=False, visual_required=False), pool_first=False,
        download_candidate_limit=2, max_download_attempts_per_scene=2, output_dir=str(tmp_path))
    first_start = 180 if failure_kind == 'transcript' else 20
    assert calls[:3] == [('bad0', first_start), ('good1', 60), ('good2', 100)]
    if failure_kind == 'download':
        assert calls[3:] == [('good0', 150)]
    elif failure_kind == 'overlay':
        assert len(calls) == 4 and calls[3][0] == 'bad0'
        assert all(abs(calls[3][1] - start) >= 20 for _source, start in calls[:3])
    else:
        assert len(calls) == 3
        assert result.audit['attempts']['0'][0]['timestamp_evidence'] == 'transcript_match'
    assert result.audit['resolved'] == 3
    assert result.audit['strategy'] == 'coverage_first_round_robin_downloads'
    assert all(load_clip_provenance(path, i) for i, path in enumerate(result))
    assert all(not load_clip_provenance(path, i).get('cleaned_path') for i, path in enumerate(result))


def test_intro_can_use_distant_master_moments_after_fresh_sources_run_out(monkeypatch):
    from services import youtube_pipeline_v2 as yt
    monkeypatch.setattr(yt, '_score_candidate', lambda *_args: 100)
    monkeypatch.setenv('VIDZEO_YT_REUSE_TIMESTAMP_GAP', '25')
    candidate = {'id': 'only-source', 'title': 'Corolla road footage', 'duration': 600}
    rows = [{'scene_index': i, 'scene_text': 'Corolla driving', 'candidate_subject': 'Corolla',
             'intro_unique': True, 'bite_clip': True} for i in range(3)]
    assigned = yt._assign_candidates(rows, {'corolla': [candidate]}, [4, 4, 4], allow_same_source_reuse=True,
                                    prefer_distinct_sources=True)
    assert all(assigned)
    starts = sorted(c['best_timestamp'] for c in assigned)
    assert all(b - a >= 25 for a, b in zip(starts, starts[1:]))


def test_vehicle_namesake_location_does_not_rank_as_car_footage():
    from services import youtube_pipeline_v2 as yt
    wrong = {'id': 'beach', 'title': 'Carova Beach Sunrise Drive Corolla NC Outer Banks in 4K',
             'description': 'Carova beach morning drive raw GoPro footage', 'duration': 900}
    assert yt._score_candidate(wrong, 'Toyota Corolla', 'Toyota Corolla driving',
        'Toyota Corolla driving raw footage', {'domain_category': 'automotive'}) == -999
    assert wrong['v2_reject_reason'] == 'vehicle_namesake_without_vehicle_context'


def test_planned_scene_query_precedes_generic_niche_queries():
    from services import youtube_pipeline_v2 as yt
    row = {'domain_category': 'automotive', 'candidate_subject': 'Toyota Corolla',
           'title_anchor': 'Toyota Corolla', 'youtube_query': 'Toyota Corolla interior cabin touchscreen b roll'}
    queries = yt._queries_for_row(row, 'Toyota Corolla', 'Toyota Corolla cabin touchscreen')
    assert 'interior cabin touchscreen' in queries[0].lower()


def test_matching_source_chapter_beats_random_middle_timestamp(monkeypatch):
    from services import youtube_pipeline_v2 as yt
    from collections import defaultdict
    monkeypatch.setenv('VIDZEO_YT_REUSE_TIMESTAMP_GAP', '25')
    candidate = {'id': 'review', 'duration': 600, 'scene_text': 'The cabin dashboard touchscreen',
                 'chapters': [{'title': 'Exterior walkaround', 'start_time': 20, 'end_time': 80},
                              {'title': 'Cabin dashboard touchscreen', 'start_time': 100, 'end_time': 200}]}
    used = defaultdict(list)
    assert yt._pick_timestamp(candidate, 0, used, 4) == 101
    assert yt._pick_timestamp(candidate, 1, used, 4) != 101


def test_chapter_match_excludes_repeated_subject_words():
    from collections import defaultdict
    from services import youtube_pipeline_v2 as yt
    candidate = {'id': 'review', 'duration': 600, 'scene_subject': 'Toyota Corolla',
                 'scene_text': 'Toyota Corolla exterior changes across generations',
                 'chapters': [{'title': 'Toyota Corolla interior', 'start_time': 100, 'end_time': 200},
                              {'title': 'Exterior', 'start_time': 30, 'end_time': 80}]}
    assert yt._pick_timestamp(candidate, 0, defaultdict(list), 4) == 31


def test_source_chapter_probe_has_wall_deadline(monkeypatch):
    from services import youtube_source_chapters as chapters
    import subprocess
    calls = []
    def timeout(*args, **kwargs):
        calls.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(chapters.subprocess, 'run', timeout)
    assert chapters.source_chapters('b-VvXVvxnfo', 100) == []
    assert calls == [12]


def test_source_chapter_probe_reads_only_structured_marker(monkeypatch):
    from services import youtube_source_chapters as chapters
    from types import SimpleNamespace
    monkeypatch.setattr(chapters.subprocess, 'run', lambda *_a, **_k:
        SimpleNamespace(stdout='noise\nVIDNETS_CHAPTERS=[{"title":"Exterior","start_time":20}]\n'))
    assert chapters.source_chapters('b-VvXVvxnfo')[0]['title'] == 'Exterior'
    assert chapters.source_chapters('invalid') == []


def test_unavailable_hd_source_does_not_repeat_every_format(monkeypatch, tmp_path):
    import sys
    import types
    from services import youtube_pipeline_v2 as yt
    from services import yt_dlp_runtime
    calls = []
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = dict(options)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def download(self, _urls):
            calls.append(self.options['format'])
            raise RuntimeError('Requested format is not available')
    utils = types.ModuleType('yt_dlp.utils')
    utils.download_range_func = lambda *_args: 'range'
    monkeypatch.setitem(sys.modules, 'yt_dlp.utils', utils)
    monkeypatch.setattr(yt_dlp_runtime, 'load_yt_dlp', lambda: types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    assert not yt._download_clip({'id': 'missing', 'webpage_url': 'https://youtube.com/watch?v=missing'},
                                 tmp_path / 'cut.mp4', 20, 4)
    assert len(calls) == 2
    assert 'height<=2160' in calls[1]
    assert calls[1].endswith('bestvideo[height>=720][height<=2160]')
