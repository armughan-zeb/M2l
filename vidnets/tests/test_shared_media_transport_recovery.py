from pathlib import Path
import threading
import time

from PIL import Image

from services.web_image import resolver, safety


def test_saved_contract_duration_matches_live_contract():
    from services.pipeline_v2_media_adapter import _scene_value
    assert _scene_value({'start_seconds': 15.25, 'end_seconds': 21.75}, 'duration', 0) == 6.5
    assert _scene_value({'duration': 4, 'start_seconds': 15, 'end_seconds': 21}, 'duration', 0) == 4
    assert _scene_value({'start_seconds': 21, 'end_seconds': 15}, 'duration', 0) == 0
    assert _scene_value({'start_seconds': 'invalid'}, 'duration', 0) == 0


def test_inflight_batch_wait_is_shared_and_does_not_steal_owner(tmp_path, monkeypatch):
    urls = [f'https://example.com/{i}.jpg' for i in range(8)]
    cache = {url: '__vidzeo_inflight__original-owner' for url in urls}
    lock = threading.Lock()
    calls = []
    monkeypatch.setattr(resolver, 'download_bulk', lambda *args, **kwargs: calls.append(args))
    started = time.monotonic()
    rows = resolver._download_candidate_batch([{'url': url} for url in urls], tmp_path, 4, .5, cache, lock)
    assert time.monotonic() - started < 1.5
    assert len(rows) == 8 and all(row[2] == 'inflight_cached' for row in rows)
    assert not calls
    assert all(value == '__vidzeo_inflight__original-owner' for value in cache.values())


def test_independent_fetch_starts_before_waiting_for_shared_urls(tmp_path, monkeypatch):
    cache = {'https://example.com/shared.jpg': '__vidzeo_inflight__owner'}
    lock = threading.Lock()
    def download(urls, output_dir, **kwargs):
        assert urls == ['https://example.com/new.jpg']
        path = Path(output_dir) / 'new.jpg'
        Image.new('RGB', (100, 100), 'blue').save(path)
        with lock:
            cache['https://example.com/shared.jpg'] = str(path)
        return [(urls[0], path, '')]
    monkeypatch.setattr(resolver, 'download_bulk', download)
    rows = resolver._download_candidate_batch(
        [{'url': 'https://example.com/shared.jpg'}, {'url': 'https://example.com/new.jpg'}],
        tmp_path, 2, .5, cache, lock)
    assert len(rows) == 2 and all(row[1].is_file() for row in rows)


def test_transport_exception_releases_only_owned_markers(tmp_path, monkeypatch):
    cache = {'https://example.com/other.jpg': '__vidzeo_inflight__other'}
    def failed(*args, **kwargs):
        raise ConnectionError('fixture socket failed')
    monkeypatch.setattr(resolver, 'download_bulk', failed)
    import pytest
    with pytest.raises(ConnectionError):
        resolver._download_candidate_batch([{'url': 'https://example.com/new.jpg'}], tmp_path, 2, 1, cache)
    assert cache == {'https://example.com/other.jpg': '__vidzeo_inflight__other'}


def test_failed_cdn_is_not_hammered_by_each_scene_and_cooldown_expires(tmp_path, monkeypatch):
    cache, calls = {}, []
    item = {'url': 'https://example.com/blocked.jpg'}
    def blocked(urls, *args, **kwargs):
        calls.extend(urls)
        return [(url, None, 'HTTP 429') for url in urls]
    monkeypatch.setattr(resolver, 'download_bulk', blocked)
    first = resolver._download_candidate_batch([item], tmp_path, 2, 1, cache)
    assert first[0][2] == 'HTTP 429'
    for _ in range(10):
        assert resolver._download_candidate_batch([item], tmp_path, 2, 1, cache)[0][2] == 'cached_download_failure'
    assert len(calls) == 1
    cache[item['url']] = '__vidzeo_failed__' + str(time.monotonic() - 31) + ':HTTP 429'
    resolver._download_candidate_batch([item], tmp_path, 2, 1, cache)
    assert len(calls) == 2


def test_preview_success_publishes_under_original_url(tmp_path, monkeypatch):
    from services.web_image import host_allowlist

    cache = {}
    item = {'url': 'https://example.com/full.jpg', 'preview_url': 'https://example.com/preview.jpg'}
    seen_hosts = tmp_path / 'seen_hosts.json'
    monkeypatch.setenv('VIDZEO_SEEN_HOSTS_PATH', str(seen_hosts))
    host_allowlist.record_hosts(['example.com'], source='test', path=seen_hosts)
    def download(urls, output_dir, **kwargs):
        if urls == [item['url']]:
            return [(item['url'], None, 'HTTP 403')]
        path = Path(output_dir) / 'preview.jpg'
        Image.new('RGB', (800, 600), 'blue').save(path)
        return [(item['preview_url'], path, '')]
    monkeypatch.setattr(resolver, 'download_bulk', download)
    rows = resolver._download_candidate_batch([item], tmp_path, 2, 1, cache)
    assert rows[0][1].is_file()
    assert cache[item['url']] == str(rows[0][1])


def test_copied_images_share_pixel_work_but_metadata_and_context_still_apply(tmp_path, monkeypatch):
    safety._IMAGE_CACHE.clear()
    safety._CONTENT_CACHE.clear()
    paths = [tmp_path / f'copy-{i}.png' for i in range(12)]
    for path in paths:
        Image.new('RGB', (100, 100), 'blue').save(path)
    calls = []
    monkeypatch.setattr(safety, '_placeholder_card_reason', lambda path: calls.append(path) or '')
    monkeypatch.setattr(safety, '_exposure_risk', lambda path, text: (False, ''))
    for path in paths:
        assert safety.image_is_safe(path, title='Royal Warrant plaque') == (True, '')
    assert len(calls) == 1
    assert safety.image_is_safe(paths[0], title='nude gallery')[0] is False
    assert len(calls) == 1
    assert safety.image_is_safe(paths[0], title='woman portrait')[0] is True
    assert len(calls) == 2
    monkeypatch.setenv('VIDZEO_WEB_DISABLE_VISUAL_SAFETY', '1')
    assert safety.image_is_safe(paths[0], title='Royal Warrant plaque')[0] is True
    assert len(calls) == 3
    Image.new('RGB', (100, 100), 'green').save(paths[0])
    assert safety.image_is_safe(paths[0], title='Royal Warrant plaque')[0] is True
    assert len(calls) == 4


def test_cached_metadata_preserves_all_rejection_decisions():
    inputs = [dict(title=value) for value in (
        'Royal Warrant certificate', 'Bikini Atoll', 'nude photo', 'image not found',
        'AI-generated illustration', 'swimsuit model', 'watermarked portrait')]
    inputs += [dict(title='archive', page_url='https://gettyimages.com/photo'),
               dict(title='archive', url='https://example.com/cdn-cgi/image/photo.jpg')]
    for row in inputs:
        assert safety.metadata_safety_reason(**row) == safety._metadata_safety_reason(**row)


def test_long_archival_descriptions_normalize_once_without_dropping_tail_evidence(monkeypatch):
    from services import visual_requirement as requirements, documentary_subject
    requirements._cached_long_search_key.cache_clear()
    original = documentary_subject.search_key
    calls = []
    def counted(value):
        if len(value) > 8192:
            calls.append(len(value))
        return original(value)
    monkeypatch.setattr(documentary_subject, 'search_key', counted)
    text = 'Archival descriptive context. ' * 2500 + 'Royal Warrant Citroën'
    normalized = requirements._norm(text)
    for _ in range(100):
        assert requirements._contains_phrase(normalized, 'Royal Warrant')
        assert requirements._contains_phrase(normalized, 'Citroen')
        assert not requirements._contains_phrase(normalized, 'warrants')
    assert calls == [len(text)]


def test_literal_phrase_lookup_matches_previous_regex_for_unicode_and_boundaries():
    import re
    from services import visual_requirement as requirements, documentary_subject
    texts = ['Citroën DS', 'Royal Warrants warrant', 'prewar car', '', 'Warrant-system seal',
             'CoachWorks Royal Warrant certificate', 'foo foofoo foo bar', 'Häagen-Dazs Straße']
    phrases = ['Citroen', 'Royal Warrant', 'warrant', 'war', 'foo bar', '', '!!!', 'Haagen Dazs', 'strasse']
    for text in texts:
        for phrase in phrases:
            normalized_phrase = documentary_subject.search_key(phrase)
            expected = bool(normalized_phrase and re.search(r'(?<![a-z0-9])' + re.escape(normalized_phrase)
                                                            + r'(?![a-z0-9])', documentary_subject.search_key(text)))
            assert requirements._contains_phrase(text, phrase) == expected
    assert requirements._contains_phrase('any content', '!!!') is False
