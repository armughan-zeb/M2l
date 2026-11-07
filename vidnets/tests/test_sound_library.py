from server.services import sound_library


def test_sound_scraper_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv('VIDZEO_SFX_WEB_SCRAPE', raising=False)
    monkeypatch.setenv('VIDZEO_SFX_ALLOWED_HOSTS', 'example.test')
    assert sound_library.scrape_sound_candidates('https://example.test/page') == []


def test_normalize_library_preserves_rights_metadata():
    result = sound_library.normalize_library([{
        'kind': 'impact', 'path': 'C:/sfx/impact.wav', 'license': 'CC0',
        'source_url': 'https://example.test/impact', 'publisher': 'Example',
    }])
    assert result['impact']['src'].endswith('impact.wav')
    assert result['impact']['rights_status'] == 'CC0'
    assert result['impact']['publisher'] == 'Example'


def test_audio_url_filter_rejects_non_audio_files():
    assert sound_library._audio_url('https://example.test/a.mp3')
    assert not sound_library._audio_url('https://example.test/page.html')
