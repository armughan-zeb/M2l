"""Opt-in live contract check for the current OpenRouter Qwen Flash endpoint."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


pytestmark = pytest.mark.skipif(
    str(os.environ.get("VIDZEO_RUN_LIVE_QWEN") or "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="set VIDZEO_RUN_LIVE_QWEN=1 to run the paid live capability probe",
)


def test_qwen35_flash_accepts_the_production_multimodal_request_shape(tmp_path):
    from PIL import Image

    from services.visual_intelligence.vision_client import QwenVisionClient

    api_key = str(
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
        or ""
    ).strip()
    if not api_key:
        pytest.skip("OpenRouter key is not configured")

    image_path = tmp_path / "qwen-live-probe.jpg"
    Image.new("RGB", (96, 64), (45, 100, 170)).save(image_path, "JPEG")
    client = QwenVisionClient(
        api_key=api_key,
        model="qwen/qwen3.5-flash-02-23",
        max_attempts=1,
        timeout_seconds=30,
    )
    verdict = client.analyze_image(
        str(image_path),
        (
            "Return JSON with candidate_id='live-probe', entity_conflict=false, "
            "identity_score, action_score, context_score, quality, final_score, "
            "visible_overlays, subject_box, safe_text_regions, fatal_mismatch, and reason."
        ),
        "Inspect the supplied image and return one JSON object only.",
    )

    assert isinstance(verdict, dict)
    assert verdict
