import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.visual_intelligence.clip_pipeline import ClipIntelligencePipeline
from services.visual_intelligence.image_pipeline import ImageIntelligencePipeline
from services.visual_intelligence.models import ClipAnalysis, ImageAnalysis
from services.visual_intelligence.vision_client import QwenVisionClient


def test_retrieval_decision_can_be_reused_by_final_pass(monkeypatch, tmp_path):
    from services.visual_intelligence import orchestrator_v2
    from services.visual_intelligence.cache import VisualDecisionCache
    from services.visual_intelligence.context import VisualDecisionContext
    from services.visual_intelligence.decision import scene_context_hash
    from PIL import Image
    image_path = tmp_path / "retrieval.jpg"
    Image.new("RGB", (1280, 720), color=(20, 40, 60)).save(image_path)
    context = VisualDecisionContext(
        enabled=True, api_key="key", model="qwen/test", scene_count=1,
        max_calls=4, budget_usd=1, deadline_at=9999999999,
        visual_required=True, cache=VisualDecisionCache(str(tmp_path / "cache.sqlite3")),
    )
    fingerprint = orchestrator_v2.asset_fingerprint(str(image_path))
    context_hash = scene_context_hash(narration="Scene text", subject="Subject", topic="Topic", media_type="image")
    plan = {
        "media_paths": [str(image_path)],
        "source_labels": ["pool"],
        "metadata": {"retrieval_visual_decisions": {"0": {
            "version": "scene-visual-v1", "scene_index": 0,
            "asset_fingerprint": fingerprint, "scene_context_hash": context_hash,
            "media_type": "image", "status": "accepted", "relevance": 90,
            "quality": 90, "confidence": 90,
        }}},
    }
    config = {"title": "Topic", "__vip_scene_texts": ["Scene text"], "__vip_section_subjects": ["Subject"]}
    with mock.patch.object(context, "get_client", return_value=None), \
         mock.patch.object(orchestrator_v2, "write_content_database", return_value=str(tmp_path / "manifest.json")), \
         mock.patch.object(orchestrator_v2.ImageIntelligencePipeline, "analyze_scene_image", side_effect=AssertionError("reused decision should skip vision")):
        result, _manifest, summary = orchestrator_v2.run_visual_intelligence_pass(plan, config, visual_context=context)
    assert result["media_paths"] == [str(image_path)]
    assert summary["kept"] == 1


def test_required_image_pipeline_replaces_unverified_asset():
    analysis = ImageAnalysis(path="candidate.jpg")
    assert ImageIntelligencePipeline(None, visual_required=True).verdict_for(analysis) == "replace"


def test_degraded_context_fails_open_and_keeps_every_pick(monkeypatch, tmp_path):
    from services.visual_intelligence import orchestrator_v2
    from services.visual_intelligence.cache import VisualDecisionCache
    from services.visual_intelligence.context import VisualDecisionContext
    from PIL import Image
    image_path = tmp_path / "degraded.jpg"
    Image.new("RGB", (1280, 720), color=(50, 60, 70)).save(image_path)
    context = VisualDecisionContext(
        enabled=True, api_key="key", model="qwen/test", scene_count=1,
        max_calls=4, budget_usd=1, deadline_at=9999999999,
        visual_required=True, cache=VisualDecisionCache(str(tmp_path / "cache.sqlite3")),
    )
    context.degraded_reason = "visual_deadline_exceeded"
    plan = {
        "media_paths": [str(image_path)],
        "source_labels": ["Web Images"],
        "metadata": {},
    }
    config = {"title": "Topic", "__vip_scene_texts": ["Scene text"], "__vip_section_subjects": ["Subject"]}
    with mock.patch.object(orchestrator_v2, "write_content_database", return_value=str(tmp_path / "manifest.json")), \
         mock.patch.object(orchestrator_v2.ImageIntelligencePipeline, "analyze_scene_image", side_effect=AssertionError("degraded pass must not analyze")):
        result, _manifest, summary = orchestrator_v2.run_visual_intelligence_pass(plan, config, visual_context=context)
    assert result["media_paths"] == [str(image_path)]
    assert summary["kept"] == 1
    assert summary["replaced"] == 0
    assert summary["degraded_reason"] == "visual_deadline_exceeded"
    assert summary["vision_available"] is False


def test_required_clip_pipeline_replaces_unverified_asset():
    analysis = ClipAnalysis(path="candidate.mp4")
    assert ClipIntelligencePipeline(None, visual_required=True).verdict_for(analysis) == "replace"


def test_relaxed_clip_gate_rejects_only_confident_irrelevance(monkeypatch):
    monkeypatch.setenv("VIDZEO_VISUAL_REJECT_ONLY_IRRELEVANT", "1")
    pipeline = ClipIntelligencePipeline(None, visual_required=False)

    borderline = ClipAnalysis(
        path="candidate.mp4", vision_available=True, story_relevance=42,
        confidence=60, final_score=20, watermark=True,
    )
    irrelevant = ClipAnalysis(
        path="candidate.mp4", vision_available=True, story_relevance=10,
        confidence=95, final_score=10,
    )
    mismatch = ClipAnalysis(
        path="candidate.mp4", vision_available=True, story_relevance=80,
        confidence=95, final_score=80, fatal_mismatch=True,
    )

    assert pipeline.verdict_for(borderline) == "keep"
    assert pipeline.verdict_for(irrelevant) == "replace"
    assert pipeline.verdict_for(mismatch) == "replace"


def test_vision_client_accepts_list_content_and_retries_empty_json_response():
    client = QwenVisionClient("key", model="qwen/test", max_attempts=1, timeout_seconds=1)
    response = mock.Mock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.side_effect = [
        {"choices": [{"message": {"content": []}}]},
        {"choices": [{"message": {"content": [{"type": "text", "text": '{"relevance": 80}' }]}}]},
    ]
    with mock.patch("services.visual_intelligence.vision_client.requests.post", return_value=response), \
         mock.patch.object(client, "_parse_json", return_value={"relevance": 80}):
        result = client._post([{"type": "text", "text": "test"}], json_mode=True, max_tokens=20)

    assert result == '{"relevance": 80}'
    assert response.json.call_count == 2
