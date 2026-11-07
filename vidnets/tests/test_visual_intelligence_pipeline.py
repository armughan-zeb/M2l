import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import mixed_media_planner
from services.visual_intelligence.cache import VisualDecisionCache
from services.visual_intelligence.context import (
    VisualDecisionContext,
    classify_visual_outcome,
    scene_requires_visual_verification,
)
from services.visual_intelligence import context as context_module
from services.visual_intelligence import orchestrator_v2
from services.visual_intelligence.image_pipeline import ImageIntelligencePipeline
from services.visual_intelligence.clip_pipeline import ClipIntelligencePipeline
from services.visual_intelligence.models import ImageAnalysis, ClipAnalysis
from services.visual_intelligence.vision_client import QwenVisionClient
from services.visual_intelligence.prompts import build_image_prompt, build_clip_prompt
import app as server_app


class FakeQwenClient:
    instances = []

    def __init__(self, api_key, model, **_kwargs):
        self.api_key = api_key
        self.model = model
        self.last_cost = 0.0001
        self.calls = 0
        self.prompts = []
        self.__class__.instances.append(self)

    def analyze_image(self, _image_path, user_prompt, _system_prompt):
        self.calls += 1
        self.prompts.append(user_prompt)
        time.sleep(0.03)
        return {
            "relevance": 10.0,
            "quality": 80.0,
            "historical_match": 80.0,
            "composition": 80.0,
            "watermark": False,
            "has_text": False,
            "overlay_type": "none",
            "fatal_mismatch": True,
            "confidence": 95.0,
            "reason": "wrong subject",
            "scene_description": "A different subject",
            "motion_effect": "static",
            "text_safe_area": "center",
        }


class FakeProbeQwenClient:
    models = []

    def __init__(self, api_key, model, **_kwargs):
        self.api_key = api_key
        self.model = model
        self.last_cost = 0.0
        self.__class__.models.append(model)

    def probe(self):
        if self.model == "qwen/primary-empty":
            return False, "VisionClientError:empty content"
        return True, f"model={self.model}"


def _context(tmp_path: Path) -> VisualDecisionContext:
    return VisualDecisionContext(
        enabled=True,
        api_key="test-key",
        model="qwen/test",
        scene_count=1,
        max_calls=4,
        budget_usd=0.02,
        deadline_at=time.monotonic() + 60,
        cache=VisualDecisionCache(str(tmp_path / "visual.sqlite3")),
    )


def test_vision_prompts_enforce_exact_era_for_dated_archival_events():
    image_prompt=build_image_prompt(
        topic="The 1967 Ice Bowl",
        narration="On December 31, 1967, the Packers and Cowboys played the NFL Championship.",
        subject="Green Bay Packers vs Dallas Cowboys",
    )
    clip_prompt=build_clip_prompt(
        topic="The 1967 Ice Bowl",
        narration="The Packers and Cowboys played the NFL Championship in 1967.",
        subject="Green Bay Packers vs Dallas Cowboys",
        frame_count=5,
    )
    assert "same-team modern footage is a wrong era" in image_prompt
    assert "strict era contract" in clip_prompt
    assert "minor era differences" not in clip_prompt


def test_visual_outcomes_separate_rejection_from_provider_failure():
    assert classify_visual_outcome(True, True, "keep", score=92) == "pass"
    assert classify_visual_outcome(False, True, "fatal mismatch", score=12) == "hard_fail"
    assert classify_visual_outcome(False, True, "borderline evidence", score=45) == "uncertain"
    assert classify_visual_outcome(False, False, "provider timeout", score=0) == "unavailable"


def test_strict_context_respects_explicit_shared_six_call_ceiling(monkeypatch):
    monkeypatch.delenv("VIDZEO_VISUAL_MAX_CALLS", raising=False)
    monkeypatch.delenv("VIDZEO_VISUAL_SHARED_MAX_CALLS", raising=False)
    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "visual_required": True,
            "visual_shared_max_calls": 6,
            "openrouter_api_key": "test-key",
        },
        scene_count=3,
    )
    assert context.max_calls == 6


def test_long_context_has_hard_shared_call_and_deadline_defaults(monkeypatch):
    monkeypatch.delenv("VIDZEO_VISUAL_MAX_CALLS", raising=False)
    monkeypatch.delenv("VIDZEO_VISUAL_SHARED_MAX_CALLS", raising=False)
    monkeypatch.delenv("VIDZEO_VISUAL_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_VISUAL_SHARED_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_VISUAL_CALL_TIMEOUT_SECONDS", raising=False)
    started = time.monotonic()

    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            # An operator's explicit cap wins over a larger branch request.
            "visual_shared_max_calls": 6,
            "visual_max_calls": 76,
            "visual_deadline_seconds": 900,
        },
        scene_count=74,
    )

    assert context.max_calls == 6
    assert 73.0 <= context.deadline_at - started <= 76.0
    # Production uses the cheap Qwen Flash verifier with a short transport
    # budget and one bounded retry, so a provider stall cannot consume the
    # whole media stage.
    assert context.call_timeout_seconds == 15.0
    assert context.call_max_attempts == 2


def test_shared_visual_caps_are_explicitly_tunable(monkeypatch):
    monkeypatch.setenv("VIDZEO_VISUAL_SHARED_MAX_CALLS", "5")
    monkeypatch.setenv("VIDZEO_VISUAL_SHARED_DEADLINE_SECONDS", "45")
    started = time.monotonic()

    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "visual_max_calls": 99,
            "visual_deadline_seconds": 500,
        },
        scene_count=74,
    )

    assert context.max_calls == 5
    assert 43.0 <= context.deadline_at - started <= 46.0


def test_v2_visual_deadline_survives_provider_retrieval(monkeypatch):
    monkeypatch.delenv("VIDZEO_VISUAL_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_VISUAL_SHARED_DEADLINE_SECONDS", raising=False)
    started = time.monotonic()

    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "storyboard_v2": {"enabled": True},
        },
        scene_count=23,
    )

    assert 458.0 <= context.deadline_at - started <= 461.0


def test_expired_context_does_not_construct_or_probe_vision_client(tmp_path, monkeypatch):
    class UnexpectedVisionClient:
        constructions = 0
        probes = 0

        def __init__(self, *_args, **_kwargs):
            self.__class__.constructions += 1

        def probe(self):
            self.__class__.probes += 1
            return True, "unexpected"

    monkeypatch.setattr(context_module, "QwenVisionClient", UnexpectedVisionClient)
    context = _context(tmp_path)
    context.deadline_at = time.monotonic() - 0.01

    assert context.get_client() is None
    assert UnexpectedVisionClient.constructions == 0
    assert UnexpectedVisionClient.probes == 0
    assert context.calls == 0
    assert context.degraded_reason == "visual_deadline_exceeded"


def test_auto_mix_keeps_counts_and_routes_discovery_by_scene_evidence(monkeypatch):
    monkeypatch.setenv("VIDZEO_MIXED_MIN_WEB_RATIO", "0.50")
    monkeypatch.setenv("VIDZEO_MIXED_YT_TARGET_RATIO", "0.50")
    monkeypatch.setenv("VIDZEO_STRICT_MIXED_WEB_MINIMUM", "0")
    scenes = [
        "The rocket launches from the pad",
        "A portrait of the mission commander",
        "Engineers inspect a diagram",
        "The crowd celebrates the landing",
        "A map shows the flight path",
        "The spacecraft races over Earth",
        "A still photograph of mission control",
        "The rescue team runs toward the capsule",
        "An archival portrait of the scientist",
        "A chart explains the final result",
    ]
    plan = mixed_media_planner.build_adaptive_mixed_media_plan(
        scenes,
        weights=None,
        title="Space mission",
        script="A documentary about the mission.",
        scene_media_mode="auto",
    )

    # Automatic documentary direction favors moving footage for action beats;
    # a legacy 50% hint is not a fixed quota over the adaptive source plan.
    assert sum(plan.final_counts.values()) == len(scenes)
    assert plan.final_counts["YouTube Clips"] > plan.final_counts["Web Images"] >= 2
    assert plan.final_counts["Stock Media"] == 0
    # The pacing assignment can prefer an early clip, while discovery retains
    # still evidence for portraits. Final accepted assets determine the mix.
    assert plan.decisions[1].preferred_sources[0] == "Web Images"
    assert plan.decisions[8].preferred_sources[0] == "Web Images"
    assert plan.decisions[3].preferred_sources[0] == "YouTube Clips"
    assert plan.decisions[7].preferred_sources[0] == "YouTube Clips"


def test_request_policy_locks_auto_but_preserves_manual_source():
    auto = server_app._coerce_media_source_from_weights({
        "scene_media_mode": "auto",
        "image_source": "Web Images",
    })
    manual = server_app._coerce_media_source_from_weights({
        "scene_media_mode": "manual",
        "image_source": "Web Images",
    })

    assert auto["image_source"] == "Mixed Media"
    # V2 policy: legacy 30/70 weight hints are dropped so they can never
    # pre-assign scenes or let a weaker source win (app.py:2387).
    assert "mixed_media_weights" not in auto
    assert auto["mixed_media_policy"] == "automatic"
    assert manual["image_source"] == "Web Images"


def test_legacy_scene_selection_alias_is_normalized_to_auto_contract():
    normalized = server_app._coerce_media_source_from_weights({
        "scene_selection_mode": "auto",
        "image_source": "Web Images",
    })

    assert normalized["scene_media_mode"] == "auto"
    assert normalized["image_source"] == "Mixed Media"
    assert "mixed_media_weights" not in normalized
    assert normalized["mixed_media_policy"] == "automatic"


def test_visual_cache_avoids_duplicate_paid_calls(monkeypatch, tmp_path):
    FakeQwenClient.instances.clear()
    monkeypatch.setattr(context_module, "QwenVisionClient", FakeQwenClient)
    image_path = tmp_path / "candidate.jpg"
    Image.new("RGB", (1280, 720), color=(20, 40, 60)).save(image_path)
    context = _context(tmp_path)
    client = context.get_client()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _index: client.analyze_image(str(image_path), "scene prompt", "system"),
            range(2),
        ))

    assert results[0] == results[1]
    assert FakeQwenClient.instances[0].calls == 1
    assert context.calls == 1
    assert context.cache_hits == 1


def test_empty_primary_probe_uses_one_fallback_model(monkeypatch, tmp_path):
    FakeProbeQwenClient.models.clear()
    context_module._PROBE_DONE.clear()
    monkeypatch.setattr(context_module, "QwenVisionClient", FakeProbeQwenClient)
    context = VisualDecisionContext(
        enabled=True,
        api_key="test-key",
        model="qwen/primary-empty",
        fallback_model="qwen/fallback-working",
        scene_count=1,
        max_calls=4,
        budget_usd=0.02,
        deadline_at=time.monotonic() + 60,
        cache=VisualDecisionCache(str(tmp_path / "visual.sqlite3")),
    )

    client = context.get_client()

    assert client is not None
    assert client.model == "qwen/fallback-working"
    assert context.model == "qwen/fallback-working"
    assert FakeProbeQwenClient.models == [
        "qwen/primary-empty",
        "qwen/fallback-working",
    ]


def test_final_pass_uses_scene_zero_and_nulls_fatal_mismatch(monkeypatch, tmp_path):
    FakeQwenClient.instances.clear()
    monkeypatch.setattr(context_module, "QwenVisionClient", FakeQwenClient)
    monkeypatch.setattr(
        orchestrator_v2,
        "write_content_database",
        lambda *_args, **_kwargs: str(tmp_path / "manifest.json"),
    )
    image_path = tmp_path / "wrong-subject.jpg"
    Image.new("RGB", (1280, 720), color=(70, 90, 110)).save(image_path)
    context = _context(tmp_path)
    plan = {
        "media_paths": [str(image_path)],
        "source_labels": ["Web Images"],
        "metadata": {},
    }
    config = {
        "name": "test",
        "title": "Fallback title",
        "__vip_scene_texts": ["The exact first scene narration"],
        "__vip_section_subjects": ["Mission commander"],
    }

    result, _manifest, summary = orchestrator_v2.run_visual_intelligence_pass(
        plan,
        config,
        visual_context=context,
    )

    assert result["media_paths"] == [None]
    assert summary["replaced"] == 1
    assert result["metadata"]["visual_intelligence_records"][0]["scene_index"] == 0
    assert "The exact first scene narration" in FakeQwenClient.instances[0].prompts[0]


def test_prefilter_allows_clean_800px_historical_derivative(tmp_path):
    from services.visual_intelligence.prefilter import prefilter_image

    image_path = tmp_path / "historical_800.jpg"
    Image.new("RGB", (800, 450), color=(90, 100, 110)).save(image_path)

    verdict = prefilter_image(str(image_path))

    assert verdict.rejected is False


def test_low_aesthetic_score_does_not_remove_relevant_documentary_image(tmp_path):
    from services.visual_intelligence.image_pipeline import ImageIntelligencePipeline
    from services.visual_intelligence.models import ImageAnalysis

    image_path = tmp_path / "sepia_titanic.jpg"
    Image.new("RGB", (1280, 720), color=(110, 85, 55)).save(image_path)
    analysis = ImageAnalysis(path=str(image_path), scene_index=0)
    analysis.vision_available = True
    analysis.relevance = 62.0
    analysis.quality = 18.0
    analysis.historical_match = 40.0
    analysis.composition = 20.0
    analysis.confidence = 90.0
    analysis.final_score = 31.0

    assert ImageIntelligencePipeline(None).verdict_for(analysis) == "keep"


def test_strict_semantic_requirement_overrides_legacy_false_policy_flag():
    assert scene_requires_visual_verification(
        {
            "source_policy": {"visual_verification_required": False},
            "visual_requirement": {"strict_identity": True},
        }
    ) is True
