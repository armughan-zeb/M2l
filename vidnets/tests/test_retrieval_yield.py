"""Coverage regressions: spend bounded retrieval on new, eligible sources."""
from collections import defaultdict
import threading


def test_verified_clip_cache_skips_network_but_still_checks_overlay(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from services import youtube_pipeline_v2 as youtube
    from services import youtube_media_cache as cache
    source = tmp_path / 'cached.mp4'
    source.write_bytes(b'\x00\x00\x00\x20ftyp' + b'x' * 9000)
    candidate = {'id': 'cached00001', 'title': 'LNER Class A4 railway footage',
                 'duration': 60, 'best_timestamp': 20}
    rows = [{'scene_index': 0, 'scene_text': 'The locomotive runs.',
             'section_subject': 'LNER Class A4', 'candidate_subject': 'LNER Class A4'}]
    monkeypatch.setattr(youtube, '_build_scene_rows', lambda *a, **k: rows)
    monkeypatch.setattr(youtube, '_search_container_pools', lambda *a, **k: {'lner class a4': [candidate]})
    monkeypatch.setattr(youtube, '_assign_candidates', lambda *a, **k: [candidate])
    monkeypatch.setattr(youtube, '_score_candidate', lambda *a, **k: 100)
    monkeypatch.setattr(youtube, '_get_authenticated_youtube_session', lambda **k: None)
    monkeypatch.setattr(cache, 'get_verified_cut', lambda *a, **k: {'path': str(source)})
    monkeypatch.setattr(cache, 'put_verified_cut', lambda *a, **k: None)
    def network(*a, **k):
        raise AssertionError('Verified cached bytes should skip network retrieval')
    monkeypatch.setattr(youtube, '_download_clip', network)
    checked = []
    def overlay(path, **kwargs):
        checked.append(path)
        return path, {'checked': True, 'clean': True}
    monkeypatch.setattr(youtube, '_clean_downloaded_clip_overlays', overlay)
    result = youtube.get_youtube_clips(
        'The locomotive runs.', 'LNER Class A4', 1, scene_texts=['The locomotive runs.'],
        scene_durations=[4], output_dir=tmp_path / 'out', pool_first=False,
        visual_context=SimpleNamespace(enabled=False, visual_required=False),
    )
    assert result[0] and checked
    assert result.audit['attempts']['0'][0]['cache_hit'] is True


def test_repeated_chapter_lookup_reuses_metadata_without_another_process(monkeypatch):
    from types import SimpleNamespace
    from services import youtube_source_chapters as chapters
    calls = []
    def fetch(*args, **kwargs):
        calls.append(kwargs['timeout'])
        return SimpleNamespace(stdout='VIDNETS_CHAPTERS=[{"title":"Wheels","start_time":30}]\n')
    monkeypatch.setattr(chapters.subprocess, 'run', fetch)
    first = chapters.source_chapters('chapter0001', 5)
    second = chapters.source_chapters('chapter0001', 5)
    assert first == second == [{'title': 'Wheels', 'start_time': 30}]
    assert calls == [5]


def test_failed_chapter_probe_has_short_cooldown_and_can_recover(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from services import youtube_source_chapters as chapters
    calls = []
    now = [1000.0]
    monkeypatch.setattr(chapters.time, 'time', lambda: now[0])
    def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args[0], 5)
        return SimpleNamespace(stdout='VIDNETS_CHAPTERS=[{"title":"Station","start_time":60}]\n')
    monkeypatch.setattr(chapters.subprocess, 'run', fetch)
    assert chapters.source_chapters('chapter0002', 5) == []
    assert chapters.source_chapters('chapter0002', 5) == []
    assert len(calls) == 1
    now[0] += 121
    assert chapters.source_chapters('chapter0002', 5)[0]['title'] == 'Station'
    assert len(calls) == 2


def test_corrupt_optional_chapter_cache_is_replaced(monkeypatch):
    from types import SimpleNamespace
    from services import youtube_source_chapters as chapters
    path = chapters._cache_path('chapter0003')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[]', encoding='utf-8')
    monkeypatch.setattr(chapters.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(stdout='VIDNETS_CHAPTERS=[]\n'))
    assert chapters.source_chapters('chapter0003', 5) == []
    assert chapters._cached_chapters(path) == []


def test_single_topic_retrieval_preserves_model_designations_and_unicode():
    from services import youtube_pipeline_v2 as youtube
    assert 'LNER Class A4' in youtube._single_topic_entity_candidates('LNER Class A4 steam locomotive')
    assert 'Citroën DS' in youtube._single_topic_entity_candidates('Citroën DS hydropneumatic suspension')
    scenes = ['LNER Class A4 steam locomotive runs on the railway.'] * 3
    rows = [{'section_subject': 'LNER Class A4'}] * 3
    anchor, support, _ratio = youtube._infer_single_topic_anchor('LNER Class A4', scenes, rows, ['LNER Class A4'] * 3)
    assert anchor == 'LNER Class A4'
    assert support == 3


def test_search_interleaves_subjects_even_below_total_cap(monkeypatch):
    from services import youtube_pipeline_v2 as youtube
    captured = []
    monkeypatch.setattr(youtube, '_run_pool_search_pass',
                        lambda jobs, **kwargs: (captured.extend(jobs) or False, defaultdict(list)))
    monkeypatch.setenv('VIDZEO_YT_V2_QUERY_TOTAL_CAP', '50')
    groups = {subject: [{'queries': [f'{subject} {kind}' for kind in ('running', 'wheels', 'station')]}]
              for subject in ('Mallard', 'Flying Scotsman', 'Tornado')}
    youtube._search_container_pools(groups, search_timeout_seconds=10)
    assert [key for key, _query in captured[:3]] == list(groups)
    assert len(captured) == 9


def test_cached_searches_do_not_wait_for_network_pacing(monkeypatch):
    from services import youtube_pipeline_v2 as youtube
    monkeypatch.setenv('VIDZEO_YT_SEARCH_PACE_SECONDS', '0.4')
    monkeypatch.setattr(youtube, '_cached_search_results', lambda query: [{'id': query}])
    monkeypatch.setattr(youtube, '_search_youtube', lambda query, *args, **kwargs: [{'id': query}])
    def unexpected_wait(seconds):
        raise AssertionError('Cache hits must not wait for provider pacing')
    monkeypatch.setattr(youtube.time, 'sleep', unexpected_wait)
    timed_out, pools = youtube._run_pool_search_pass(
        [('a', 'first'), ('b', 'second'), ('c', 'third')], search_timeout=5, max_workers=3)
    assert not timed_out
    assert set(pools) == {'a', 'b', 'c'}


def test_web_filters_reserved_urls_before_limit_and_recovers_dead_hosts(tmp_path, monkeypatch):
    from PIL import Image
    from services.web_image import resolver
    monkeypatch.setenv('VIDZEO_WEB_FAST_CANDIDATES_PER_QUERY', '3')
    monkeypatch.setenv('VIDZEO_WEB_FAST_DOWNLOADS_PER_SCENE', '6')
    rows = [{'url': f'https://host{idx}.example/LNER-Class-A4-{idx}.jpg',
             'title': 'LNER Class A4 steam locomotive', 'source': 'flickr_api',
             'query': 'LNER Class A4'} for idx in range(9)]
    called = []

    def download(items, output_dir, **kwargs):
        results = []
        for item in items:
            index = rows.index(next(row for row in rows if row['url'] == item['url']))
            called.append(index)
            path = None
            if index >= 6:
                path = tmp_path / f'valid-{index}.jpg'
                Image.effect_noise((900, 600), 60 + index).convert('RGB').save(path)
            results.append((item['url'], path, '' if path else 'HTTP 403'))
        return results

    monkeypatch.setattr(resolver, '_download_candidate_batch', download)
    monkeypatch.setattr(resolver, '_pre_score_candidate', lambda *a, **k: 9.0)
    monkeypatch.setattr(resolver, '_score_candidate', lambda *a, **k: 9.0)
    monkeypatch.setattr(resolver, 'image_is_safe', lambda *a, **k: (True, ''))
    result = resolver._resolve_scene(
        idx=0, scene_text='LNER Class A4 steam locomotive', queries=['LNER Class A4'],
        gdl=None, output_dir=tmp_path, main_subject='LNER Class A4',
        global_phashes={}, global_md5s={}, used_files=set(),
        phash_lock=threading.Lock(), md5_lock=threading.Lock(), file_lock=threading.Lock(),
        fast_mode=True, shared_pool_query='LNER Class A4', shared_pool_candidates=rows,
        shared_reserved_owners={row['url']: 1 for row in rows[:3]},
    )
    assert result and result['path']
    # Reserved rows do not consume slots; first three eligible hosts fail,
    # then the same provider page supplies a working second wave.
    assert called == [3, 4, 5, 6, 7, 8]


def test_web_does_not_repeat_failed_urls_on_later_query(tmp_path, monkeypatch):
    from services.web_image import resolver
    monkeypatch.setenv('VIDZEO_WEB_FAST_CANDIDATES_PER_QUERY', '2')
    monkeypatch.setenv('VIDZEO_WEB_FAST_DOWNLOADS_PER_SCENE', '4')
    rows = [{'url': f'https://host{idx}.example/A4-{idx}.jpg',
             'title': 'LNER Class A4 steam locomotive', 'source': 'flickr_api',
             'query': 'LNER Class A4'} for idx in range(3)]
    called = []
    def download(items, output_dir, **kwargs):
        called.extend(item['url'] for item in items)
        return [(item['url'], None, 'HTTP 403') for item in items]
    monkeypatch.setattr(resolver, '_download_candidate_batch', download)
    monkeypatch.setattr(resolver, '_pre_score_candidate', lambda *a, **k: 9.0)
    monkeypatch.setattr(resolver, '_discover_flickr_api_urls', lambda *a, **k: rows)
    monkeypatch.setattr(resolver, '_discover_hybrid_urls', lambda *a, **k: [])
    result = resolver._resolve_scene(
        idx=0, scene_text='LNER Class A4', queries=['LNER Class A4', 'LNER Class A4 train'],
        gdl=None, output_dir=tmp_path, main_subject='LNER Class A4',
        global_phashes={}, global_md5s={}, used_files=set(),
        phash_lock=threading.Lock(), md5_lock=threading.Lock(), file_lock=threading.Lock(),
        fast_mode=True, shared_pool_query='LNER Class A4', shared_pool_candidates=rows,
    )
    assert result is None
    assert len(called) == len(set(called)) == 3
