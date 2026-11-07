"""Contract tests for scraper recall and mandatory visual verification."""

import threading
from pathlib import Path
import os
import sys
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.web_image import resolver


def _strict_automobile_requirement():
    return {
        "canonical_entity": "Jaguar",
        "subject_type": "automobile",
        "required_attributes": {
            "model": "e type",
            "year": "1961",
            "era": "classic_historical",
        },
        "strict_identity": True,
    }


def test_gallerydl_row_is_recall_only_and_reaches_visual_pending_lane(tmp_path):
    source = tmp_path / "scraped.jpg"
    Image.new("RGB", (1200, 675), (30, 50, 80)).save(source)

    def fake_discover(source_name, source_type, query, *_args, **_kwargs):
        if source_type != "gdl":
            return []
        return [{
            "url": "https://images.example/jaguar-e-type.jpg",
            "title": "",
            "source": source_name,
            "query": query,
            "metadata_pending": True,
            "discovery_method": "gallery-dl",
        }]

    def fake_download(urls, _output_dir, **_kwargs):
        return [(urls[0], source, "")]

    with mock.patch.object(resolver, "_enabled_fast_sources", return_value=[]), \
         mock.patch.object(resolver, "_discover_source_query", side_effect=fake_discover), \
         mock.patch.object(resolver, "download_bulk", side_effect=fake_download), \
         mock.patch.object(resolver, "image_is_safe", return_value=(True, "")), \
         mock.patch.object(resolver, "_validate_image", return_value=(1200, 675)):
        result = resolver._resolve_scene(
            0,
            "The 1961 Jaguar E-Type returns to the road.",
            ["Jaguar E-Type 1961 archival photograph"],
            ["gallery-dl"],
            tmp_path,
            "Jaguar",
            {},
            {},
            set(),
            threading.Lock(),
            threading.Lock(),
            threading.Lock(),
            fast_mode=True,
            allow_visual_pending=True,
            visual_requirement=_strict_automobile_requirement(),
        )

    assert result is not None
    assert result["semantic_visual_pending"] is True
    assert result["discovery_method"] == "gallery-dl"


def test_visual_pending_row_cannot_pass_when_required_verifier_is_unavailable(tmp_path):
    source = tmp_path / "scraped.jpg"
    Image.new("RGB", (1200, 675), (30, 50, 80)).save(source)

    class NoClientContext:
        visual_required = True
        model = "test-model"

        def get_client(self):
            return None

    result = resolver._rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="The 1961 Jaguar E-Type returns to the road.",
        subject="Jaguar",
        global_topic="Jaguar E-Type",
        query="Jaguar E-Type 1961 archival photograph",
        candidates=[{
            "path": str(source),
            "semantic_visual_pending": True,
            "title": "",
            "url": "https://images.example/jaguar-e-type.jpg",
            "domain": "pinterest",
        }],
        visual_context=NoClientContext(),
    )

    assert result is not None
    assert result["path"] == ""
    assert result["visual_reason"] == "visual_pending_requires_verifier"
