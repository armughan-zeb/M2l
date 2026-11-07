import html
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))


def test_yandex_parses_current_data_state_and_preserves_unknown_rights():
    from services.sources.yandex_source import YandexSource

    payload = {
        "initialState": {
            "serpList": {"items": {"entities": {
                "one": {
                    "origUrl": "https://images.example.test/titanic.webp?x=1",
                    "origWidth": 1920,
                    "origHeight": 1080,
                    "previewUrl": "https://yandex.example.test/titanic-preview.jpg",
                    "alt": "Titanic lifeboats",
                    "snippet": {
                        "title": "Titanic lifeboats",
                        "domain": "archive.example.test",
                        "url": "https://archive.example.test/titanic",
                    },
                },
            }}}
        }
    }
    source = YandexSource()
    source.session.get = mock.Mock(return_value=mock.Mock(status_code=200, text=(
        '<div data-state="' + json.dumps(payload).replace('"', '&quot;') + '"></div>'
    )))

    results = source.search("Titanic lifeboats", limit=3)

    assert len(results) == 1
    assert results[0].download_url.startswith("https://images.example.test")
    assert results[0].width == 1920
    assert results[0].preview_url == "https://yandex.example.test/titanic-preview.jpg"
    assert results[0].metadata["page_url"] == "https://archive.example.test/titanic"
    assert results[0].metadata["rights_status"] == "unknown"
    assert results[0].metadata["rights_complete"] is False


def test_yandex_deduplicates_and_circuits_after_schema_failures():
    from services.sources.yandex_source import YandexSource

    source = YandexSource()
    source.session.get = mock.Mock(return_value=mock.Mock(status_code=200, text="<html></html>"))
    assert source.search("empty", 5) == []
    assert source.search("empty", 5) == []
    assert source.search("empty", 5) == []
    assert source.is_available() is False


def test_yandex_tries_regional_endpoints_before_circuit(monkeypatch):
    from services.sources.yandex_source import YandexSource
    source = YandexSource()
    calls = []
    source.session.get = mock.Mock(side_effect=lambda url, **kwargs: calls.append(url) or mock.Mock(
        status_code=200, text='<title>Are you not a robot?</title>'
    ))
    monkeypatch.setenv("VIDZEO_YANDEX_REGION_FALLBACK", "1")
    assert source.search("test") == []
    assert calls == [source.SEARCH_URL, source.SEARCH_URL_RU, "https://yandex.com.tr/images/search"]


def test_yandex_search_is_single_flight_under_scene_concurrency(monkeypatch):
    from services.sources.yandex_source import YandexSource

    source = YandexSource()
    active = 0
    peak = 0

    def challenged(_url, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.04)
        active -= 1
        return mock.Mock(status_code=200, text="<html></html>")

    source.session.get = mock.Mock(side_effect=challenged)
    monkeypatch.setenv("VIDZEO_YANDEX_REGION_FALLBACK", "0")
    monkeypatch.setenv("VIDZEO_YANDEX_BROWSER_FALLBACK", "0")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _index: source.search("single flight"), range(4)))

    assert results == [[], [], [], []]
    assert peak == 1
    assert source.session.get.call_count == 1
