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


def test_flickr_403_is_reported_as_provider_outage(monkeypatch):
    from services.web_image import flickr_api_source as flickr

    response = mock.Mock(status_code=403, text="forbidden")
    session = mock.Mock()
    session.get.return_value = response
    monkeypatch.setattr(flickr, "_get_session", lambda: session)
    monkeypatch.setattr(flickr, "_retry_count", lambda: 1)

    assert flickr.search_flickr_api("railway wheel", limit=5) == []
    failed, error = flickr.flickr_last_search_network_failure()
    assert failed is True
    assert "403" in error


def test_long_storyboard_gets_multiple_scene_waves(monkeypatch):
    assert long_form_web_budget(10, 12) == 180
    assert long_form_web_budget(121, 12) == 855
    monkeypatch.delenv("VIDZEO_MEDIA_DEADLINE_SECONDS", raising=False)
    config = {"llm_provider": "external_agent", "_web_budget_scene_count": 500}
    assert _media_stage_deadline_seconds(config, "Mixed Media") >= 1800


def test_original_403_recovers_from_search_preview(tmp_path, monkeypatch):
    from services.web_image import resolver
    original = "https://blocked.example/image.jpg"
    preview = "https://tse1.mm.bing.net/image.jpg"
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
    results = resolver._download_candidate_batch(
        [{"url": original, "preview_url": preview}], tmp_path, 1, 2,
    )
    assert calls == [[original], [preview]]
    assert results[0][0] == original
    assert results[0][1] == image


def test_single_candidate_403_falls_back_to_provider_preview(tmp_path, monkeypatch):
    from services.web_image import resolver

    original = "https://blocked.example/original.jpg"
    preview = "https://tse1.mm.bing.net/thumbnail.jpg"
    image = tmp_path / "preview.jpg"
    image.write_bytes(b"image bytes")
    calls = []

    def download(urls, *_args, **_kwargs):
        calls.append(urls)
        if urls == [original]:
            return [(original, None, "HTTP 403")]
        return [(preview, image, "")]

    monkeypatch.setattr(resolver, "download_bulk", download)
    monkeypatch.setattr(resolver, "candidate_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(resolver, "_validate_image", lambda _path: (800, 600))
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(resolver, "_compute_md5", lambda _path: None)
    monkeypatch.setattr(resolver, "_compute_average_hash", lambda _path: None)
    monkeypatch.setattr(resolver, "_score_candidate", lambda *_args, **_kwargs: 1.0)
    monkeypatch.setattr(resolver, "_passes_web_relevance_floor", lambda *_args, **_kwargs: True)

    row = resolver._download_and_accept_single_candidate(
        {
            "url": original,
            "preview_url": preview,
            "title": "Railway wheel",
            "source": "universal_scraper",
            "provider": "universal_yandex",
            "query": "railway wheel flange",
        },
        tmp_path,
        0,
        "railway wheel flange",
        {"railway", "wheel"},
        "railway wheel",
        {},
        {},
        set(),
        __import__("threading").Lock(),
        __import__("threading").Lock(),
        __import__("threading").Lock(),
    )

    assert calls == [[original], [preview]]
    assert row and row["path"] == str(image)
    assert row["url"] == original
    assert row["download_url_used"] == preview
