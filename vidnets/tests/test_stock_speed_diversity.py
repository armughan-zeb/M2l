import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from services.real_media_service import (StockMediaPool, MediaItem, MediaConfig, MediaDownloader,
    _stock_cache_filename, _search_stock_candidates, _stock_candidate_asset_key)


def asset(number):
    return MediaItem(id=str(number), source='pexels', type='video', title='Palace exterior',
        description='Royal palace exterior', tags=['palace'], download_url=f'https://assets.test/{number}.mp4',
        preview_url='', width=1920, height=1080, score=90)


def test_concurrent_recovery_shares_search_but_selects_distinct_assets(tmp_path):
    pool = StockMediaPool('test-job')
    starts, release = threading.Event(), threading.Event()
    calls = []
    def provider(*args):
        calls.append(True)
        starts.set()
        assert release.wait(2)
        return [asset(i) for i in range(8)]
    manager = SimpleNamespace(config=MediaConfig(cache_dir=str(tmp_path)),
        search_engine=SimpleNamespace(_search_pexels=provider))
    manager.config.media_pool = pool
    def scene():
        candidates = _search_stock_candidates(manager, 'royal palace', 'pexels', {}, 'video')
        chosen = pool.choose(candidates)
        chosen.score_breakdown['scene_private'] = threading.get_ident()
        return chosen
    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = [workers.submit(scene) for _ in range(8)]
        assert starts.wait(1)
        release.set()
        selected = [future.result(2) for future in futures]
    assert len(calls) == 1
    assert len({_stock_candidate_asset_key(item) for item in selected}) == 8
    assert pool.report()['repeat_selections'] == 0
    replay = _search_stock_candidates(manager, 'royal palace', 'pexels', {}, 'video')
    assert all('scene_private' not in item.score_breakdown for item in replay)
    assert len(calls) == 1


def test_pool_wait_honors_cancellation_and_does_not_cache_failed_searches():
    pool = StockMediaPool()
    started, finish = threading.Event(), threading.Event()
    def provider():
        started.set()
        finish.wait(2)
        return [asset(1)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        owner = workers.submit(pool.search, 'query', provider)
        assert started.wait(1)
        cancel = threading.Event()
        cancel.set()
        assert pool.search('query', provider, timeout=.1, cancel_event=cancel) == []
        assert pool.search('query', provider, timeout=.01) == []
        finish.set()
        assert owner.result(1)
    with pytest.raises(ValueError):
        pool.search('other', lambda: (_ for _ in ()).throw(ValueError('provider unavailable')))
    assert pool.search('other', lambda: [asset(2)])


def test_download_bytes_shared_without_sharing_scene_contracts(tmp_path):
    pool = StockMediaPool('job-a')
    downloads = []
    first, second = asset(1), asset(1)
    first.score_breakdown = {'cache_scope':'scene-a', 'shared_download_scope':pool.scope}
    second.score_breakdown = {'cache_scope':'scene-b', 'shared_download_scope':pool.scope}
    assert _stock_cache_filename(first,'.mp4') == _stock_cache_filename(second,'.mp4')
    other = copy.deepcopy(first)
    other.score_breakdown['shared_download_scope'] = StockMediaPool('job-b').scope
    assert _stock_cache_filename(other,'.mp4') != _stock_cache_filename(first,'.mp4')
    instances = [MediaDownloader(MediaConfig(cache_dir=str(tmp_path))) for _ in range(2)]
    started, finish = threading.Event(), threading.Event()
    for downloader in instances:
        downloader._inflight_lock, downloader._inflight_downloads = pool.download_lock, pool.downloads
        def download(item, filename, destination):
            downloads.append(filename)
            started.set()
            assert finish.wait(2)
            from pathlib import Path
            Path(destination).write_bytes(b'fixture')
            item.local_path, item.is_downloaded = destination, True
            return True
        downloader._download_item_owned = download
        def mark(item, destination):
            item.local_path, item.is_downloaded = destination, True
            return True
        downloader._mark_downloaded = mark
    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(instances[0].download_item, first)
        assert started.wait(1)
        b = workers.submit(instances[1].download_item, second)
        finish.set()
        assert a.result(2) and b.result(2)
    assert len(downloads) == 1
    assert first.score_breakdown['cache_scope'] != second.score_breakdown['cache_scope']


def test_pool_rotation_remains_bounded_and_failed_reservations_are_released():
    pool = StockMediaPool()
    candidates = [asset(i) for i in range(3)]
    selected = [pool.choose(candidates) for _ in range(6)]
    assert [item.id for item in selected] == ['0','1','2','0','1','2']
    pool.release(selected[-1])
    assert pool.choose(candidates).id == '2'
    assert pool.choose([]) is None


def test_stock_diversity_never_promotes_a_weak_match_just_because_it_is_unused():
    pool = StockMediaPool()
    good, similar, weak = asset(1), asset(2), asset(3)
    good.score, similar.score, weak.score = 90, 86, 40
    assert pool.choose([good,similar,weak]) is good
    assert pool.choose([good,similar,weak]) is similar
    assert pool.choose([good,similar,weak]) is good


def test_equal_quality_chooses_short_hd_transfer_without_forcing_clip_loops():
    pool = StockMediaPool()
    long, short, too_short, sd = [asset(i) for i in range(4)]
    long.duration, short.duration, too_short.duration, sd.duration = 94, 12, 2, 10
    sd.width, sd.height, sd.size_bytes = 960, 540, 100_000
    chosen = pool.choose([long,short,too_short,sd],duration_needed=5)
    assert chosen is short


def test_coverage_check_records_graphics_without_percentage_rejection(tmp_path, monkeypatch):
    from services.media_delivery_gate import validate_documentary_coverage
    from services.pipeline_contracts import MediaContractError
    path = tmp_path / 'real.jpg'
    path.write_bytes(b'fixture')
    plan = {'media_paths':[str(path)]*10, 'source_labels':['Web Images']*10,
            'metadata':{'media_resolution_v2':[{'provider':'web'} for _ in range(10)]}}
    assert validate_documentary_coverage(plan,[1]*10)['passed']
    plan['metadata']['media_resolution_v2'][0]['provider']='graphic'
    assert validate_documentary_coverage(plan,[1]*10)['passed']
    plan['metadata']['media_resolution_v2'][1]['provider']='graphic'
    monkeypatch.setenv('VIDZEO_MAX_EMERGENCY_VISUAL_RATIO','1')
    summary=validate_documentary_coverage(plan,[1]*10)
    assert summary['passed'] and summary['warnings'] and summary['advisory_only']


def test_web_rescue_limits_repetition_without_replaying_youtube():
    from services.media_resolution_coordinator import SceneResolution, _reuse_limit_for_resolution, _topic_reuse_limit
    scene = {'source_policy':{'mixed_media':True},'start_seconds':30}
    web = SceneResolution(scene_index=3, provider='web')
    youtube = SceneResolution(scene_index=3, provider='youtube')
    assert _reuse_limit_for_resolution(scene,web,non_mixed_limit=4) == 2
    assert _topic_reuse_limit(scene,web,non_mixed_limit=4) == 2
    assert _reuse_limit_for_resolution(scene,youtube,non_mixed_limit=4) == 1


def test_similar_quality_verified_clips_prefer_another_video_source():
    from services.media_resolution_coordinator import _choose_diverse_verified_media, SceneResolution
    previous = SceneResolution(scene_index=0, provider='youtube', selected_path='first.mp4',
        source_url='https://youtu.be/aaaaaaaaaaa')
    same = (90, 0, {'provider':'youtube','source_url':'https://youtu.be/aaaaaaaaaaa'}, {})
    different = (88, 0, {'provider':'youtube','source_url':'https://youtu.be/bbbbbbbbbbb'}, {})
    weak = (70, 0, {'provider':'youtube','source_url':'https://youtu.be/ccccccccccc'}, {})
    choose = lambda values: _choose_diverse_verified_media(values,[previous],lambda candidate: 0)
    assert choose([same,different,weak]) == different
    assert choose([same,weak]) == same
    event_match = lambda candidate: int(candidate is same[2])
    assert _choose_diverse_verified_media([same,different],[previous],event_match) == same


def test_production_stock_entrypoint_shares_pool_across_independent_scene_calls(tmp_path, monkeypatch):
    from pathlib import Path
    import services.real_media_service as stock
    pool = StockMediaPool('production-wiring-test')
    managers = []
    def manager_factory(*args):
        manager = stock.RealMediaManager(pexels_key='fixture-key',cache_dir=str(tmp_path))
        managers.append(manager)
        return manager
    monkeypatch.setattr(stock,'create_media_manager',manager_factory)
    monkeypatch.setattr(stock.MultiSourceSearchEngine,'_search_pexels',
                        lambda *args: [asset(i) for i in range(8)])
    def download(self,item,filename,destination):
        Path(destination).write_bytes(b'fixture-provider-bytes')
        return self._mark_downloaded(item,destination)
    monkeypatch.setattr(stock.MediaDownloader,'_download_item_owned',download)
    def scene(index):
        return stock.get_real_media_for_video(['A royal palace exterior.'],[4],title='Royal palace',
            pexels_key='fixture-key',media_sources=['pexels'],
            scene_queries=[{'primary_query':'palace exterior','query_variants':['palace exterior']}],
            runtime_options={'_stock_media_pool':pool,'_stock_last_resort':True,
                             '_render_repair_mode':True,'_return_metadata':True})
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(scene,range(4)))
    paths = [result['paths'][0] for result in results]
    assert all(paths) and len(set(paths)) == 4
    assert pool.report()['repeat_selections'] == 0
    assert all(manager.config.media_pool is pool for manager in managers)
    assert all(manager.downloader._inflight_downloads is pool.downloads for manager in managers)


def test_pexels_rendition_quality_is_not_lowered_to_save_download_bytes(tmp_path):
    from services.real_media_service import MultiSourceSearchEngine
    engine = MultiSourceSearchEngine(MediaConfig(cache_dir=str(tmp_path),max_file_size_mb=35))
    sd = {'width':960,'height':540,'size':1_000_000,'quality':'sd'}
    hd = {'width':1280,'height':720,'size':2_000_000,'quality':'hd'}
    full_hd = {'width':1920,'height':1080,'size':25_000_000,'quality':'hd'}
    assert engine._find_best_pexels_video([sd,hd,full_hd]) == full_hd
    assert engine._find_best_pexels_video([sd,hd,{**full_hd,'size':40_000_000}]) == hd
    assert engine._find_best_pexels_video([sd,{**full_hd,'size':40_000_000}]) is None
