import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.web_image import universal_scraper
from services.web_image.source_broker import long_form_web_budget
from services.pipeline_v2_media_adapter import _media_stage_deadline_seconds


def test_isolated_client_exposes_json_for_flickr(monkeypatch):
    response = {"status_code": 200, "text": json.dumps({"items": []})}
    process = mock.Mock()
    process.communicate.return_value = (json.dumps(response), "")
    process.poll.return_value = 0
    monkeypatch.setattr(universal_scraper.subprocess, "Popen", lambda *args, **kwargs: process)
    client = universal_scraper._IsolatedNativeClient()
    result = client.get("https://example.test/feed", headers={}, timeout=3)
    assert result.status_code == 200
    assert result.json() == {"items": []}


def test_flickr_feed_uses_short_subject_tags_and_parses_results():
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {"items": [{
        "title": "Railway wheel", "link": "https://www.flickr.com/photos/a/1",
        "media": {"m": "https://live.staticflickr.com/1/image_m.jpg"}, "tags": "train rail"
    }]}
    client = mock.Mock()
    client.get.return_value = response
    engine = universal_scraper.FlickrEngine()
    with mock.patch.object(engine, "_get_client", return_value=client):
        rows = engine.search("How the railway wheel flange causes derailment from track", 10, 1, True)
    query = parse_qs(urlparse(client.get.call_args.args[0]).query)
    assert query["tagmode"] == ["any"]
    assert query["tags"] == ["railway,wheel,flange,causes,derailment"]
    assert rows and rows[0].image_url.endswith("_b.jpg")


def test_long_storyboard_gets_multiple_scene_waves(monkeypatch):
    assert long_form_web_budget(10, 12) == 180
    assert long_form_web_budget(121, 12) == 855
    monkeypatch.delenv("VIDZEO_MEDIA_DEADLINE_SECONDS", raising=False)
    config = {"llm_provider": "external_agent", "_web_budget_scene_count": 500}
    assert _media_stage_deadline_seconds(config, "Mixed Media") >= 1800


def test_original_403_recovers_from_search_preview(tmp_path, monkeypatch):
    from services.web_image import resolver
    original = "https://blocked.example/image.jpg"
    preview = "https://preview.example/image.jpg"
    image = tmp_path / "preview.jpg"
    image.write_bytes(b"preview content")
    calls = []

    def download(urls, *_args, **_kwargs):
        calls.append(urls)
        if urls == [original]:
            return [(original, None, "HTTP 403")]
        return [(preview, image, "")]

    monkeypatch.setattr(resolver, "download_bulk", download)
    monkeypatch.setattr(resolver, "load_seen_hosts", lambda: set())
    monkeypatch.setattr(resolver, "_preferred_image_hosts", lambda: set())
    results = resolver._download_candidate_batch(
        [{"url": original, "preview_url": preview}], tmp_path, 1, 2,
    )
    assert calls == [[original], [preview]]
    assert results[0][0] == original
    assert results[0][1] == image
