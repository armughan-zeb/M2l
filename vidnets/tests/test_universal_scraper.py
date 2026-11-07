import time
import json
import subprocess
from types import SimpleNamespace

from services.web_image.universal_scraper import (
    UniversalImageResult,
    UniversalScraperManager,
    universal_result_to_candidate,
)


def test_native_image_search_timeout_terminates_process(monkeypatch):
    from services.web_image import universal_scraper as scraper
    processes = []
    class Process:
        def __init__(self, *args, **kwargs):
            self.killed = False
            processes.append(self)
        def communicate(self, *args, **kwargs):
            if not self.killed:
                raise subprocess.TimeoutExpired('native-search', 4)
            return ('[]', '')
        def poll(self):
            return 1 if self.killed else None
        def kill(self):
            self.killed = True
    monkeypatch.setattr(scraper, 'DDGS', object())
    monkeypatch.setattr(scraper.subprocess, 'Popen', Process)
    assert scraper.DuckDuckGoEngine().search('NFL', 3, 1, True) == []
    assert processes and all(process.killed for process in processes)


def test_native_image_search_preserves_metadata_and_pagination(monkeypatch):
    from services.web_image import universal_scraper as scraper
    captured = []
    class Process:
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, request=None, **kwargs):
            if request:
                captured.append(json.loads(request))
            return (json.dumps([{'image': 'https://example.com/first.jpg'},
                {'image': 'https://example.com/second.jpg', 'title': 'NFL field',
                 'width': 1920, 'height': 1080, 'url': 'https://example.com/article'}]), '')
        def poll(self):
            return 0
    monkeypatch.setattr(scraper, 'DDGS', object())
    monkeypatch.setattr(scraper.subprocess, 'Popen', Process)
    rows = scraper.DuckDuckGoEngine().search('NFL', 1, 2, True)
    assert len(rows) == 1 and rows[0].image_url.endswith('second.jpg')
    assert rows[0].width == 1920 and rows[0].source_url.endswith('article')
    assert captured[0]['kind'] == 'images' and captured[0]['limit'] == 2


def test_native_tls_bing_yandex_live_in_child_processes(monkeypatch):
    from services.web_image import universal_scraper as scraper
    captured = []
    class Process:
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, request=None, **kwargs):
            if request:
                captured.append(json.loads(request))
            return ('{"status_code":200,"text":"search markup"}', '')
        def poll(self):
            return 0
    monkeypatch.setattr(scraper, 'primp', SimpleNamespace(Client=lambda: (_ for _ in ()).throw(AssertionError('Native TLS initialized in API'))))
    monkeypatch.setattr(scraper.subprocess, 'Popen', Process)
    for engine, url in ((scraper.BingEngine(), 'https://www.bing.com/images/async'), (scraper.YandexEngine(), 'https://yandex.com/images/search')):
        response = engine._get_client().get(url, headers={}, timeout=6)
        assert response.status_code == 200
    assert len(captured) == 2 and all(row['kind'] == 'http' for row in captured)


class _FakeEngine:
    def __init__(self, name, delay=0.0, rows=1, fail=False):
        self.name = name
        self.delay = delay
        self.rows = rows
        self.fail = fail

    def search(self, query, limit, page, safe_search):
        del limit, page, safe_search
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.name} unavailable")
        return [
            UniversalImageResult(
                title=f"{self.name} {query} {idx}",
                image_url=f"https://{self.name}.example/{idx}.jpg",
                source_engine=self.name,
            )
            for idx in range(self.rows)
        ]


def test_manager_fans_out_and_interleaves_results():
    manager = UniversalScraperManager(max_workers=3, engine_timeout_seconds=2)
    manager._engines = {
        "one": _FakeEngine("one", delay=0.12, rows=2),
        "two": _FakeEngine("two", delay=0.12, rows=2),
        "three": _FakeEngine("three", delay=0.12, rows=2),
    }
    started = time.perf_counter()
    response = manager.search("Apollo 11", engine_ids=["one", "two", "three"], limit_per_engine=2)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.30
    assert response.total == 6
    assert [row.source_engine for row in response.results[:3]] == ["one", "two", "three"]
    assert all(stat.success for stat in response.stats)


def test_manager_keeps_partial_success_when_one_engine_fails():
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=2)
    manager._engines = {
        "good": _FakeEngine("good", rows=2),
        "bad": _FakeEngine("bad", fail=True),
    }
    response = manager.search("royal family", engine_ids=["good", "bad"], limit_per_engine=2)
    assert response.total == 2
    assert response.stats[0].success is True
    assert response.stats[1].success is False
    assert "unavailable" in response.stats[1].error


def test_manager_converts_future_timeout_into_partial_response():
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=1)
    manager._engines = {
        "good": _FakeEngine("good", rows=1),
        "slow": _FakeEngine("slow", delay=1.4, rows=1),
    }
    response = manager.search(
        "royal family", engine_ids=["good", "slow"], limit_per_engine=1, timeout_seconds=1
    )
    assert response.total == 1
    assert any(stat.engine == "slow" and not stat.success and "timeout" in stat.error for stat in response.stats)


def test_result_adapter_preserves_provenance_and_pending_visual_gate():
    row = UniversalImageResult(
        title="Moon landing",
        image_url="https://images.example/moon.jpg",
        thumbnail_url="https://images.example/moon-small.jpg",
        source_url="https://archive.example/apollo",
        source_engine="Yandex",
        description="Yandex visual search index",
    )
    candidate = universal_result_to_candidate(row, query="Apollo 11")
    assert candidate["source"] == "universal_scraper"
    assert candidate["provider"] == "universal_yandex"
    assert candidate["metadata_pending"] is True
    assert candidate["source_url"] == "https://archive.example/apollo"
