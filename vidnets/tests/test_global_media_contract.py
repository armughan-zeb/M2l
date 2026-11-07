import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from config import normalize_generation_config
from services.mixed_media_planner import classify_mixed_media_topic, resolve_media_policy
from services.visual_requirement import build_visual_requirement, provider_query_variants


def test_explicit_global_categories_are_preserved_without_llm():
    assert classify_mixed_media_topic(category="politics", title="Apple", script="") == "politics"
    assert classify_mixed_media_topic(category="music", title="Harry", script="") == "music"
    assert classify_mixed_media_topic(category="food", title="Apple", script="") == "food"


def test_global_policy_returns_specific_profile_when_key_is_unavailable():
    policy = resolve_media_policy(
        title="The history of electric cars",
        script="A technology documentary about battery research and charging networks.",
        category="technology",
    )
    assert policy["niche"] == "technology"
    assert policy["weights"]["YouTube Clips"] >= 0.20
    assert policy["context_hash"]


def test_provider_queries_keep_full_identity_and_add_domain_anchor():
    requirement = build_visual_requirement(
        title="Apple nutrition and health",
        script="This food documentary explains apple nutrition, recipes, and healthy meals.",
        scene_text="Fresh apples are prepared in a kitchen.",
        semantic_context={"domain_category": "food"},
    )
    queries = provider_query_variants(requirement, provider="web")
    assert queries
    assert all(query.lower().startswith("apple") for query in queries[:3])
    assert any("food" in query.lower() for query in queries[:4])


def test_generation_config_enables_nonblank_global_fallback_by_default(monkeypatch):
    monkeypatch.delenv("VIDZEO_GLOBAL_MEDIA_GUARANTEE", raising=False)
    monkeypatch.delenv("VIDZEO_MEDIA_FALLBACK_POLICY", raising=False)
    monkeypatch.delenv("VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK", raising=False)
    cfg = normalize_generation_config({})
    assert cfg["global_media_guarantee"] is True
    assert cfg["media_fallback_policy"] == "grounded_graphic"
    assert cfg["allow_grounded_graphic_fallback"] is True
    assert cfg["allow_grounded_graphic_fallback_strict"] is True
    assert cfg["media_provider_retries"] == 2


def test_grounded_graphic_fallback_remains_explicitly_opt_outable(monkeypatch):
    monkeypatch.delenv("VIDZEO_MEDIA_FALLBACK_POLICY", raising=False)
    monkeypatch.delenv("VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK", raising=False)
    monkeypatch.delenv("VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK_STRICT", raising=False)
    cfg = normalize_generation_config({
        "media_fallback_policy": "real_media_only",
        "allow_grounded_graphic_fallback": False,
        "allow_grounded_graphic_fallback_strict": False,
    })
    assert cfg["media_fallback_policy"] == "real_media_only"
    assert cfg["allow_grounded_graphic_fallback"] is False
    assert cfg["allow_grounded_graphic_fallback_strict"] is False
