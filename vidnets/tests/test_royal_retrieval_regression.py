"""Regressions from the all-empty Royal Family media run."""
import sys
import threading
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def test_multientity_shared_prefetch_cannot_consume_scene_search_budget():
    from services.web_image.resolver import _shared_pool_prefetch_limit

    assert _shared_pool_prefetch_limit(
        24, multi_identity_job=True, configured_limit=28
    ) == 12
    assert _shared_pool_prefetch_limit(
        24, multi_identity_job=True
    ) == 8


def test_visual_unavailable_only_blocks_explicit_entity_conflicts():
    from services.web_image.resolver import _has_explicit_visual_identity_conflict

    musician = {
        "candidate_evidence": {
            "canonical_entity_mismatch": True,
            "matched_terms": [],
        }
    }
    terse_photo = {"candidate_evidence": {"metadata_source": "visual_pending"}}

    assert _has_explicit_visual_identity_conflict(musician)
    assert not _has_explicit_visual_identity_conflict(terse_photo)


def test_web_vision_deadline_outlives_long_image_discovery(monkeypatch):
    from app import _build_web_visual_context

    monkeypatch.setenv("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "120")
    monkeypatch.setenv("VIDZEO_WEB_VISUAL_GRACE_SECONDS", "30")
    monkeypatch.delenv("VIDZEO_VISUAL_DEADLINE_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_VISUAL_SHARED_DEADLINE_SECONDS", raising=False)

    context = _build_web_visual_context({"openrouter_api_key": "test-key"}, 24)

    assert context is not None
    assert context.deadline_at - time.monotonic() >= 470


def test_full_query_pack_reserves_canonical_subject(monkeypatch, tmp_path):
    from services.web_image import resolver

    searched = []
    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [("flickr_api", "flickr_api")])
    monkeypatch.setattr(resolver, "_SEARCH_QUERY_CACHE", {})

    def discover(source, kind, query, *args, **kwargs):
        searched.append(query)
        return []

    monkeypatch.setattr(resolver, "_discover_source_query", discover)
    resolver._resolve_scene(
        0, "The British Royal Family faces unexpected developments in 2026.",
        [f"British Royal Family 2026 variant {i}" for i in range(5)],
        None, tmp_path, "British Royal Family", {}, {}, set(),
        threading.Lock(), threading.Lock(), threading.Lock(), fast_mode=True,
        visual_requirement={"canonical_entity": "British Royal Family", "strict_identity": True},
    )
    # The canonical pack is unchanged: three variants, then the bare
    # canonical subject as the final wave of the pack. The F3 broadening
    # round appends one bounded wave after the pack (5 queries here); the
    # bare canonical subject is attempted exactly once, in the pack, and
    # is never repeated by the broadening round.
    assert searched[:4] == [
        "British Royal Family 2026 variant 0",
        "British Royal Family 2026 variant 1",
        "British Royal Family 2026 variant 2",
        "British Royal Family",
    ]
    assert len(searched) == 9
    assert searched.count("British Royal Family") == 1


def test_youtube_explicit_budget_includes_auth_retry(monkeypatch):
    from services import youtube_pipeline_v2 as yt

    clock = [100.0]
    calls = []
    monkeypatch.setattr(yt.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(yt, "_representative_pool_queries", lambda rows, limit: ["royal archive"])

    def search(jobs, **kwargs):
        calls.append(kwargs["search_timeout"])
        clock[0] += kwargs["search_timeout"]
        return True, {}

    monkeypatch.setattr(yt, "_run_pool_search_pass", search)
    assert not yt._search_container_pools({"royal": [{}]}, search_timeout_seconds=5, session_jar=object())
    assert calls == [5]


def test_web_download_rate_limit_is_recorded(monkeypatch, tmp_path):
    from services.web_image import resolver
    from services.web_image.source_broker import SourceTelemetry

    telemetry = SourceTelemetry()
    query = "British Royal Family"
    row = {
        "url": "https://live.staticflickr.com/royal.jpg",
        "title": query, "source": "flickr_api", "query": query,
    }
    monkeypatch.setattr(resolver, "_SEARCH_QUERY_CACHE", {})
    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [("flickr_api", "flickr_api")])
    monkeypatch.setattr(resolver, "_discover_source_query", lambda *args, **kwargs: [dict(row)])
    monkeypatch.setattr(resolver, "download_bulk", lambda urls, *args, **kwargs: [(u, None, "HTTP 429") for u in urls])
    result = resolver._resolve_scene(
        0, query, [query], None, tmp_path, query, {}, {}, set(),
        threading.Lock(), threading.Lock(), threading.Lock(), fast_mode=True,
        source_telemetry=telemetry,
        visual_requirement={"canonical_entity": query, "strict_identity": True},
    )
    assert result is None
    counters = telemetry.snapshot()["providers"]["flickr_api"]
    assert counters["download_failed"] == 1
    assert counters["download_http_429"] == 1


def test_scene_locked_william_photo_survives_legacy_harry_charles_prefilter(
    monkeypatch, tmp_path
):
    """A named William scene must not inherit the old Harry/Charles-only gate."""
    from services.web_image import resolver

    image_path = tmp_path / "william.jpg"
    Image.effect_noise((900, 500), 91).convert("RGB").save(image_path, format="JPEG")
    query = "Prince William portrait photo"
    candidate = {
        "url": "https://images.example/prince-william-portrait.jpg",
        "image_url": "https://images.example/prince-william-portrait.jpg",
        "source_url": "https://news.example/prince-william-portrait",
        "title": "Prince William portrait",
        "description": "Official public appearance",
        "source": "flickr_api",
        "provider": "flickr_api",
        "query": query,
    }
    monkeypatch.setattr(resolver, "_SEARCH_QUERY_CACHE", {})
    monkeypatch.setattr(
        resolver, "_enabled_fast_sources", lambda: [("flickr_api", "flickr_api")]
    )
    monkeypatch.setattr(
        resolver, "_discover_source_query", lambda *_args, **_kwargs: [dict(candidate)]
    )
    monkeypatch.setattr(
        resolver,
        "download_bulk",
        lambda urls, *_args, **_kwargs: [(urls[0], image_path, "")],
    )
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(
        resolver, "_validate_image", lambda *_args, **_kwargs: (900, 500)
    )

    result = resolver._resolve_scene(
        0,
        "Prince William's position in the Royal Family is different. He is now heir to the throne.",
        [query],
        None,
        tmp_path,
        "Prince William",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        deadline_at=time.monotonic() + 20,
        defer_context_verification=True,
        visual_requirement={
            "canonical_entity": "Prince William",
            "subject_type": "person",
            "strict_identity": True,
        },
    )

    assert result and result["path"]
    assert result["semantic_context_pending"] is True
    assert result["candidate_evidence"]["verification_required"] is True


def test_scene_query_matched_url_only_person_candidate_reaches_visual_gate(
    monkeypatch, tmp_path
):
    """Exact person queries can return real photos with only terse metadata."""
    from services.web_image import resolver

    image_path = tmp_path / "royal-event.jpg"
    Image.effect_noise((900, 500), 92).convert("RGB").save(image_path, format="JPEG")
    query = "Prince William portrait photo"
    candidate = {
        "url": "https://images.example/royal-event.jpg",
        "image_url": "https://images.example/royal-event.jpg",
        "title": "Royal family public appearance",
        "source": "flickr_api",
        "provider": "flickr_api",
        "query": query,
    }
    downloaded = []
    monkeypatch.setattr(resolver, "_SEARCH_QUERY_CACHE", {})
    monkeypatch.setattr(
        resolver, "_enabled_fast_sources", lambda: [("flickr_api", "flickr_api")]
    )
    monkeypatch.setattr(
        resolver, "_discover_source_query", lambda *_args, **_kwargs: [dict(candidate)]
    )

    def fake_download(urls, *_args, **_kwargs):
        downloaded.extend(urls)
        return [(urls[0], image_path, "")]

    monkeypatch.setattr(resolver, "download_bulk", fake_download)
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(
        resolver, "_validate_image", lambda *_args, **_kwargs: (900, 500)
    )

    result = resolver._resolve_scene(
        0,
        "Prince William's position in the Royal Family is different.",
        [query],
        None,
        tmp_path,
        "Prince William",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        deadline_at=time.monotonic() + 20,
        defer_context_verification=True,
        visual_requirement={
            "canonical_entity": "Prince William",
            "subject_type": "person",
            "strict_identity": True,
        },
    )

    assert downloaded == [candidate["url"]]
    assert result and result["path"]
    assert result["semantic_visual_pending"] is True


def test_youtube_pacing_cannot_spend_more_than_search_wall(monkeypatch):
    from services import youtube_pipeline_v2 as yt

    monkeypatch.setenv("VIDZEO_YT_SEARCH_PACE_SECONDS", "0.4")
    monkeypatch.setattr(yt, "_search_youtube", lambda *args, **kwargs: [])
    started = time.monotonic()
    timed_out, pools = yt._run_pool_search_pass(
        [("royal", str(i)) for i in range(10)], search_timeout=0.05, max_workers=2,
    )
    assert timed_out
    assert time.monotonic() - started < 0.75
