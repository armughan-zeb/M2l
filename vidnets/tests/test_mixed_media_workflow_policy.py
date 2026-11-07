from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch


def test_shipped_defaults_allow_director_video_mix_and_hd_sources():
    from dotenv import dotenv_values
    values = dotenv_values(Path(__file__).resolve().parents[1] / '.env.defaults')
    assert values['VIDZEO_YT_MAX_SOURCE_HEIGHT'] == '1080'
    assert values['VIDZEO_YT_SOURCE_MAX_HEIGHT'] == '1080'
    assert values['VIDZEO_YT_MIN_SOURCE_HEIGHT'] == '720'
    assert float(values['VIDZEO_MIXED_MIN_WEB_RATIO']) == 0
    assert values['VIDZEO_MIXED_WEB_MISS_USE_YT_POOL'] == '1'

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def test_web_source_catalog_exposes_complete_ordered_fallback_map():
    from services.web_image.source_broker import (
        web_image_fallback_layers,
        web_image_source_catalog,
    )

    catalog = web_image_source_catalog()
    names = [row["name"] for row in catalog]
    assert {"yandex", "bing_hybrid", "ddg_hybrid", "google_images", "openverse", "wikimedia", "flickr_api", "pinterest", "reddit"} <= set(names)
    layers = web_image_fallback_layers()
    assert layers[0]["name"] == "fast_primary"
    assert "yandex" in layers[0]["providers"]
    assert layers[-1]["name"] == "slow_gallery_last_resort"


def test_normalized_config_enables_layered_mixed_policy_and_model_chains(monkeypatch):
    from config import normalize_generation_config

    monkeypatch.delenv("VIDZEO_SCENE_BRAIN_FALLBACK_CHAIN", raising=False)
    monkeypatch.delenv("VIDZEO_QWEN_VISION_FALLBACK_MODELS", raising=False)
    config = normalize_generation_config({})
    assert config["cross_source_fallback"] is True
    assert config["web_collect_all_sources"] is True
    assert config["unique_web_assets_required"] is True
    assert config["scene_brain_fallback_chain"] == ["qwen/qwen3.5-flash-02-23"]
    assert config["qwen_vision_fallback_models"] == ["qwen/qwen3.5-flash-02-23"]


@patch("services.visual_intelligence.vision_client.requests.post")
def test_vision_client_sends_ordered_multimodal_fallback_models(post, tmp_path):
    from PIL import Image
    from services.visual_intelligence.vision_client import QwenVisionClient

    image = tmp_path / "candidate.jpg"
    Image.new("RGB", (64, 64), (80, 100, 120)).save(image, "JPEG")
    response = Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "model": "google/gemma-4-26b-a4b-it:free",
        "choices": [{"message": {"content": '{"acceptable": true}'}, "finish_reason": "stop"}],
    }
    post.return_value = response
    client = QwenVisionClient(
        "test-key",
        model="qwen/qwen3.5-flash-02-23",
        fallback_models=["google/gemma-4-26b-a4b-it:free", "nex-agi/nex-n2.5-mini:free"],
        max_attempts=1,
        timeout_seconds=2,
    )
    client.analyze_image(str(image), "Describe this image as JSON.", "You are a vision model.")
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "qwen/qwen3.5-flash-02-23"
    assert payload["models"] == [
        "qwen/qwen3.5-flash-02-23",
        "google/gemma-4-26b-a4b-it:free",
        "nex-agi/nex-n2.5-mini:free",
    ]


@patch("services.visual_intelligence.vision_client.requests.post")
def test_vision_client_caps_openrouter_fallback_groups_at_three(post, tmp_path):
    from PIL import Image
    from services.visual_intelligence.vision_client import QwenVisionClient

    image = tmp_path / "candidate.jpg"
    Image.new("RGB", (64, 64), (80, 100, 120)).save(image, "JPEG")
    retryable = Mock(status_code=503)
    retryable.raise_for_status.side_effect = RuntimeError("provider unavailable")
    success = Mock(status_code=200)
    success.raise_for_status.return_value = None
    success.json.return_value = {
        "model": "openrouter/free",
        "choices": [{"message": {"content": '{"acceptable": true}'}, "finish_reason": "stop"}],
    }
    post.side_effect = [retryable, success]
    client = QwenVisionClient(
        "test-key",
        model="qwen/qwen3.5-flash-02-23",
        fallback_models=[
            "google/gemma-4-26b-a4b-it:free",
            "nex-agi/nex-n2.5-mini:free",
            "openrouter/free",
        ],
        max_attempts=2,
        timeout_seconds=2,
    )

    client.analyze_image(str(image), "Describe this image as JSON.", "You are a vision model.")

    first = post.call_args_list[0].kwargs["json"]
    second = post.call_args_list[1].kwargs["json"]
    assert len(first["models"]) <= 3
    assert len(second["models"]) <= 3
    assert second["model"] == "nex-agi/nex-n2.5-mini:free"


def test_source_lane_policy_carries_web_layers_into_mixed_media():
    from services.pipeline_v2_media_adapter import _source_lane_policy

    policy = _source_lane_policy({"image_source": "Mixed Media", "unique_web_assets_required": True})
    assert policy["web_enabled"] is True
    assert policy["cross_source_fallback"] is True
    assert policy["web_unique_assets_required"] is True
    assert any(row["name"] == "yandex" for row in policy["web_source_catalog"])
