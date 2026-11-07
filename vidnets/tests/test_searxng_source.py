import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def test_searxng_maps_image_results_and_rotates_instances(monkeypatch):
    from services.sources.searxng_source import SearXNGSource
    monkeypatch.setenv("VIDZEO_SEARXNG_URLS", "https://one.example,https://two.example")
    response = Mock(status_code=200)
    response.json.return_value = {"results": [{
        "title": "Royal archive", "content": "Historical image",
        "img_src": "https://cdn.example/royal.jpg",
        "thumbnail_src": "https://one.example/image_proxy?url=royal",
        "url": "https://archive.example/royal", "source": "archive.example",
        "resolution": "1600x900",
    }]}
    with patch("services.sources.searxng_source.requests.get", return_value=response) as get:
        result = SearXNGSource().search("Royal Family", limit=5)
    assert len(result) == 1
    assert result[0].download_url.endswith("royal.jpg")
    assert result[0].preview_url.endswith("royal")
    assert (result[0].width, result[0].height) == (1600, 900)
    assert get.call_args.kwargs["params"]["categories"] == "images"


def test_searxng_failover_uses_second_instance(monkeypatch):
    from services.sources.searxng_source import SearXNGSource
    monkeypatch.setenv("VIDZEO_SEARXNG_URLS", "https://one.example,https://two.example")
    bad = Mock(status_code=503, text="")
    good = Mock(status_code=200)
    good.json.return_value = {"results": [{"img_src": "https://cdn.example/a.jpg"}]}
    with patch("services.sources.searxng_source.requests.get", side_effect=[bad, good]):
        result = SearXNGSource().search("test")
    assert len(result) == 1
    assert result[0].download_url.endswith("a.jpg")


def test_searxng_discovers_healthy_instances_from_searx_space(monkeypatch):
    from services.sources.searxng_source import SearXNGSource
    monkeypatch.delenv("VIDZEO_SEARXNG_URLS", raising=False)
    monkeypatch.delenv("VIDZEO_SEARXNG_URL", raising=False)
    SearXNGSource._instance_cache = []
    SearXNGSource._instance_cache_at = 0
    directory = Mock(status_code=200)
    directory.json.return_value = {"instances": {
        "https://healthy.example/": {
            "network_type": "normal", "http": {"status_code": 200},
            "engines": {"bing images": {}},
            "timing": {"search": {"success_percentage": 100, "all": {"mean": 0.5}}},
        },
        "https://offline.example/": {
            "network_type": "normal", "http": {"status_code": 503},
            "engines": {"bing images": {}},
            "timing": {"search": {"success_percentage": 100, "all": {"mean": 0.1}}},
        },
    }}
    with patch("services.sources.searxng_source.requests.get", return_value=directory):
        assert SearXNGSource()._instances() == ["https://healthy.example"]
