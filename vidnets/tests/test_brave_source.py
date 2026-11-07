import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def test_brave_api_maps_original_and_proxy_urls(monkeypatch):
    from services.sources.brave_source import BraveSource
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
    payload = {
        "type": "images",
        "results": [{
            "title": "Royal Family archive",
            "description": "Archival photo",
            "url": "https://example.org/royal",
            "source": "example.org",
            "thumbnail": {"src": "https://imgs.brave.com/thumb", "width": 500, "height": 333},
            "properties": {"url": "https://example.org/royal.jpg", "width": 1800, "height": 1200},
            "confidence": "high",
        }],
    }
    response = Mock(status_code=200)
    response.json.return_value = payload
    with patch("services.sources.brave_source.requests.get", return_value=response) as get:
        results = BraveSource().search("Royal Family", limit=5)
    assert len(results) == 1
    assert results[0].download_url.endswith("royal.jpg")
    assert results[0].preview_url.endswith("thumb")
    assert results[0].metadata["provider_proxy"] is True
    assert get.call_args.kwargs["headers"]["X-Subscription-Token"] == "test-key"


def test_brave_is_disabled_without_key(monkeypatch):
    from services.sources.brave_source import BraveSource
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("VIDZEO_BRAVE_SEARCH_API_KEY", raising=False)
    assert BraveSource().search("anything") == []
