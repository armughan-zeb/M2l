import sys
import threading
import time
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def test_same_query_pool_is_wide_but_bounded(monkeypatch):
    from services.web_image.resolver import _same_query_candidate_limit

    monkeypatch.setenv("VIDZEO_WEB_SAME_QUERY_CANDIDATES", "8")
    assert _same_query_candidate_limit(20, 8.5) == 8
    assert _same_query_candidate_limit(5, 8.5) == 5
    # Medium-confidence rows must still expose enough alternatives for
    # cross-scene diversity; recovery callers cap the effective ceiling.
    assert _same_query_candidate_limit(8, 5.5) == 6
    assert _same_query_candidate_limit(4, 5.5) == 4
    assert _same_query_candidate_limit(20, 2.0) == 20


def test_strong_same_query_result_keeps_twelve_unique_candidates_for_vision(
    monkeypatch, tmp_path
):
    """A good query must retain a page of unique results for verification."""
    from services.web_image import resolver

    rows = [
        {
            "url": f"https://images.example/jaguar-e-type-{index}.jpg",
            "image_url": f"https://images.example/jaguar-e-type-{index}.jpg",
            "title": f"Jaguar E-Type side view {index}",
            "source": "wikimedia",
            "provider": "wikimedia",
            "source_url": f"https://commons.wikimedia.org/wiki/File:Jaguar_{index}",
            "query": "Jaguar E-Type side view",
            "tags": "Jaguar E-Type side view classic automobile",
            "description": "Jaguar E-Type classic automobile side view",
        }
        for index in range(14)
    ]
    downloaded_urls = []

    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [("wikimedia", "api")])
    monkeypatch.setattr(
        resolver,
        "_discover_source_query",
        lambda _source, _kind, _query, _gdl=None, max_timeout=None: list(rows),
    )

    def fake_download(urls, output_dir, max_concurrent=50, timeout_seconds=15.0, **kwargs):
        downloaded_urls.extend(urls)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for index, url in enumerate(urls):
            path = output_dir / f"jaguar-e-type-{index}.jpg"
            # Noise keeps each fixture above the resolver's minimum byte size
            # and gives the content/visual deduplication code distinct pixels.
            Image.effect_noise((800, 450), 64 + index).convert("RGB").save(
                path, format="JPEG", quality=92
            )
            result.append((url, path, ""))
        return result

    monkeypatch.setattr(resolver, "download_bulk", fake_download)

    result = resolver._resolve_scene(
        0,
        "Jaguar E-Type side view",
        ["Jaguar E-Type side view"],
        None,
        tmp_path,
        "Jaguar E-Type",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        max_candidate_downloads=20,
        deadline_at=time.monotonic() + 30,
    )

    assert result and result.get("path")
    assert len(downloaded_urls) == 12
    assert result["downloaded_candidate_count"] == 12
    assert result["candidate_pool_size"] == 12
    shortlist = result["_vision_shortlist"]
    assert len(shortlist) == 12
    assert len({candidate["url"] for candidate in shortlist}) == 12


def test_library_of_congress_is_wired_into_fast_sources(monkeypatch):
    """Web-first audit (Nov 10, 2026): LOC was defined but never dispatched.

    The Library of Congress public JSON API needs no key and returns
    rights-documented photos (verified live: 'locomotive' -> 20 rows).
    It must be in the default fast set and routable through the api
    dispatcher like wikimedia.
    """
    from services.web_image import resolver

    for key in (
        "VIDZEO_WEB_ONLY_PROVIDER",
        "VIDZEO_ENABLE_LEGACY_WEB_SEARCH",
        "VIDZEO_SEARXNG_ENABLE",
        "VIDZEO_SEARXNG_URLS",
        "VIDZEO_SEARXNG_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    names = [name for name, _kind in resolver._enabled_fast_sources()]
    assert "library_of_congress" in names
    assert ("library_of_congress", "api") in resolver._FAST_SOURCES

    from services.sources.base_source import ImageCandidate

    class FakeLoc:
        def search(self, query, limit=20):
            return [
                ImageCandidate(
                    id="loc_test",
                    source="library_of_congress",
                    title="Locomotive 3840",
                    description="A steam locomotive",
                    download_url="https://tile.loc.gov/storage-services/service/pnp/cph/3a30000/3a30100/3a30132r.jpg",
                    preview_url="https://tile.loc.gov/storage-services/service/pnp/cph/3a30000/3a30100/3a30132r.jpg",
                    metadata={"provider": "library_of_congress", "foreign_url": "https://www.loc.gov/pictures/item/abc/"},
                )
            ]

    monkeypatch.setattr(resolver, "_LOC_SOURCE", FakeLoc())
    rows = resolver._discover_api_urls("library_of_congress", "locomotive")
    assert len(rows) == 1
    assert rows[0]["source"] == "library_of_congress"
    assert rows[0]["url"].startswith("https://tile.loc.gov/")


def test_five_hundred_px_stays_out_of_slow_sources():
    """500px needs a logged-in cookie and returns no URLs without it
    (verified live Nov 10, 2026: 40s, zero URLs). It stays undispatched
    so the slow harvest does not burn its budget on a dead lane."""
    from services.web_image import resolver

    assert ("500px", "gdl") not in resolver._SLOW_SOURCES
    assert ("500px", "https://500px.com/search?q={query}&type=photos") in resolver._GALLERYDL_SOURCES


def test_streaming_vision_winner_keeps_the_downloaded_alternates_for_later_review():
    from services.web_image.resolver import _preserve_downloaded_shortlist

    downloaded = [
        {"path": f"candidate-{index}.jpg", "title": f"candidate {index}"}
        for index in range(3)
    ]
    streamed_winner = {"path": "candidate-1.jpg", "visual_outcome": "pass"}
    result = _preserve_downloaded_shortlist(streamed_winner, downloaded, 12, 3)

    assert result["candidate_pool_size"] == 12
    assert result["downloaded_candidate_count"] == 3
    assert [row["path"] for row in result["_vision_shortlist"]] == [
        "candidate-1.jpg", "candidate-0.jpg", "candidate-2.jpg"
    ]


def test_pending_first_query_does_not_stop_canonical_query_waves(
    monkeypatch, tmp_path
):
    """A weak domain-only hit must not prevent the exact pool query from running."""
    from services.web_image import resolver

    mountain = {
        "url": "https://images.example/eiger.jpg",
        "image_url": "https://images.example/eiger.jpg",
        "title": "Eiger mountain railway station landscape",
        "source": "flickr_api",
        "provider": "flickr_api",
        "source_url": "https://www.flickr.com/photos/eiger",
        "query": "Train Crew railway crew early starts documentary photograph",
        "tags": "mountain railway station crew",
        "description": "A mountain railway station and alpine landscape.",
    }
    crew = {
        "url": "https://images.example/conductor.jpg",
        "image_url": "https://images.example/conductor.jpg",
        "title": "Railway conductor beside locomotive",
        "source": "flickr_api",
        "provider": "flickr_api",
        "source_url": "https://www.flickr.com/photos/conductor",
        "query": "Train Crew railway crew conductor locomotive engineer documentary photograph",
        "tags": "railway conductor locomotive crew",
        "description": "A railway conductor beside a locomotive.",
    }
    searched = []

    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [("flickr_api", "api")])

    def fake_discover(_source, _kind, query, *_args, **_kwargs):
        searched.append(query)
        return [mountain] if "early starts" in query else [crew]

    monkeypatch.setattr(resolver, "_discover_source_query", fake_discover)

    def fake_download(urls, output_dir, **_kwargs):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for url in urls:
            filename = "eiger.jpg" if "eiger" in url else "conductor.jpg"
            path = output_dir / filename
            noise_seed = 64 if "eiger" in url else 65
            Image.effect_noise((800, 450), noise_seed).convert("RGB").save(path, format="JPEG", quality=92)
            result.append((url, path, ""))
        return result

    monkeypatch.setattr(resolver, "download_bulk", fake_download)

    result = resolver._resolve_scene(
        0,
        "Early starts and long shifts are part of train crew life.",
        [
            "Train Crew railway crew early starts documentary photograph",
            "Train Crew railway crew conductor locomotive engineer documentary photograph",
        ],
        None,
        tmp_path,
        "Train Crew",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        max_candidate_downloads=8,
        deadline_at=time.monotonic() + 30,
        allow_visual_pending=True,
        visual_requirement={
            "canonical_entity": "Train Crew",
            "subject_type": "rail_crew",
            "required_terms": ["Train Crew", "railway crew", "conductor", "locomotive engineer"],
            "strict_identity": True,
        },
    )

    assert result and result.get("path")
    assert result["title"] == "Railway conductor beside locomotive"
    assert len(searched) == 2


@pytest.mark.parametrize("prioritize_director_query", [False, True])
def test_exhaustive_semantic_recovery_uses_warm_candidate_before_provider_wave(
    monkeypatch, tmp_path, prioritize_director_query
):
    """A ready shared-pool image must reach ranking/vision before slow scrapers."""
    from services.web_image import resolver

    image_path = tmp_path / "warm-prince-harry.jpg"
    Image.effect_noise((800, 450), 81).convert("RGB").save(
        image_path, format="JPEG", quality=92
    )
    candidate = {
        "url": "https://images.example/prince-harry-portrait.jpg",
        "image_url": "https://images.example/prince-harry-portrait.jpg",
        "path": str(image_path),
        "title": "Prince Harry portrait at a public event",
        "source": "yandex",
        "provider": "yandex",
        "domain": "images.example",
        "source_url": "https://news.example/prince-harry-profile",
        "query": "Prince Harry",
        "tags": "Prince Harry portrait British royal family",
        "description": "Prince Harry at a public event.",
    }
    provider_calls = []

    def unexpected_provider_call(*args, **kwargs):
        provider_calls.append((args, kwargs))
        return []

    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [("yandex", "api")])
    monkeypatch.setattr(resolver, "_discover_source_query", unexpected_provider_call)
    monkeypatch.setattr(resolver, "_validate_image", lambda _path: (800, 450))
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(resolver, "_compute_average_hash", lambda _path: 81)
    monkeypatch.setattr(resolver, "_compute_md5", lambda _path: "warm-harry")

    result = resolver._resolve_scene(
        0,
        "Prince Harry returns to the United Kingdom.",
        ["Prince Harry portrait", "Prince Harry official event photograph"],
        None,
        tmp_path / "scene-output",
        "Prince Harry",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        max_candidate_downloads=8,
        deadline_at=time.monotonic() + 4,
        shared_pool_query="Prince Harry",
        shared_pool_candidates=[candidate],
        visual_requirement={
            "canonical_entity": "Prince Harry",
            "subject_type": "person",
            "required_terms": ["Prince Harry"],
            "strict_identity": True,
        },
        defer_context_verification=True,
        exhaustive_fallback=True,
        prioritize_director_query=prioritize_director_query,
    )

    assert result and result.get("path")
    assert result["title"] == candidate["title"]
    assert result["candidate_pool_size"] >= 1
    assert bool(provider_calls) is prioritize_director_query
    # Scene-specific evidence must be written to a worker-owned copy; the same
    # warmed row is shared by every scene in the real pipeline.
    assert "_from_shared_batch" not in candidate
    assert "candidate_evidence" not in candidate
    assert "requirement_hash" not in candidate


def test_portrait_resolution_is_orientation_neutral(tmp_path):
    from services.web_image.resolver import _validate_image

    portrait = tmp_path / "portrait.jpg"
    Image.effect_noise((500, 736), 91).convert("RGB").save(
        portrait, format="JPEG", quality=92
    )
    assert _validate_image(portrait) == (500, 736)


def test_warm_downloaded_candidate_path_skips_publisher_redownload(
    monkeypatch, tmp_path
):
    """Warm-pool files must not be fetched again from expiring/hotlink URLs."""
    from services.web_image import resolver
    from services.visual_requirement import VisualRequirementV1

    image_path = tmp_path / "warm-prince-harry-portrait.jpg"
    Image.effect_noise((800, 450), 94).convert("RGB").save(
        image_path, format="JPEG", quality=92
    )
    candidate = {
        "url": "https://images.example/prince-harry-portrait.jpg",
        "image_url": "https://images.example/prince-harry-portrait.jpg",
        "path": str(image_path),
        "title": "Prince Harry portrait at a public event",
        "source": "yandex",
        "provider": "yandex",
        "domain": "images.example",
        "source_url": "https://news.example/prince-harry-profile",
        "query": "Prince Harry",
        "tags": "Prince Harry portrait British royal family",
        "description": "Prince Harry at a public event.",
    }
    requirement = VisualRequirementV1.from_mapping({
        "canonical_entity": "Prince Harry",
        "subject_type": "person",
        "required_terms": ["Prince Harry"],
        "strict_identity": True,
    }).to_dict()

    monkeypatch.setattr(resolver, "_enabled_fast_sources", lambda: [])
    monkeypatch.setattr(resolver, "_discover_source_query", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        resolver,
        "_download_candidate_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("warm image was re-downloaded")),
    )

    result = resolver._resolve_scene(
        0,
        "Prince Harry returns to the United Kingdom.",
        ["Prince Harry portrait"],
        None,
        tmp_path / "scene-output",
        "Prince Harry",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        max_candidate_downloads=6,
        deadline_at=time.monotonic() + 10,
        shared_pool_query="Prince Harry",
        shared_pool_candidates=[candidate],
        visual_requirement=requirement,
        allow_visual_pending=True,
        defer_context_verification=True,
        global_topic="British Royal Family",
    )

    assert result and result["path"] == str(image_path)
    assert result["title"] == candidate["title"]
    assert "_from_shared_batch" not in candidate


def test_exhaustive_recovery_cascades_into_secondary_sources(
    monkeypatch, tmp_path
):
    """A failed fast tier advances to secondary sources on the next query."""
    from services.web_image import resolver

    provider_calls = []
    candidate = {
        "url": "https://images.example/prince-harry-profile.jpg",
        "image_url": "https://images.example/prince-harry-profile.jpg",
        "title": "Prince Harry portrait at a public event",
        "source": "openverse",
        "provider": "openverse",
        "domain": "images.example",
        "source_url": "https://news.example/prince-harry-profile",
        "query": "Prince Harry photo",
        "tags": "Prince Harry portrait British royal family",
        "description": "Prince Harry at a public event.",
    }
    source_rows = [
        ("universal_scraper", "universal"),
        ("yandex", "api"),
        ("flickr_api", "flickr_api"),
        ("bing_hybrid", "hybrid"),
        ("ddg_hybrid", "hybrid"),
        ("openverse", "api"),
    ]
    monkeypatch.setattr(resolver, "_FAST_SOURCES", source_rows)
    monkeypatch.setattr(resolver, '_enabled_fast_sources', lambda: list(source_rows))
    monkeypatch.setattr(resolver, '_ACTIVE_PROVIDER_HEALTH', None)
    monkeypatch.setattr(resolver, '_SEARCH_QUERY_CACHE', {
        ('universal_scraper', 'Prince Harry portrait'): [],
        ('openverse', 'Prince Harry photo'): [],
    })

    def discover(source, _kind, query, *_args, **_kwargs):
        provider_calls.append((source, query))
        return [dict(candidate)] if source == "openverse" and query == "Prince Harry photo" else []

    def fake_download(urls, output_dir, **_kwargs):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for url in urls:
            path = output_dir / "prince-harry.jpg"
            Image.effect_noise((800, 450), 82).convert("RGB").save(
                path, format="JPEG", quality=92
            )
            rows.append((url, path, ""))
        return rows

    monkeypatch.setattr(resolver, "_discover_source_query", discover)
    monkeypatch.setattr(resolver, "download_bulk", fake_download)
    monkeypatch.setattr(resolver, "_validate_image", lambda _path: (800, 450))
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(resolver, "_compute_average_hash", lambda _path: 82)
    monkeypatch.setattr(resolver, "_compute_md5", lambda _path: "secondary-harry")

    result = resolver._resolve_scene(
        0,
        "Prince Harry returns to the United Kingdom.",
        ["Prince Harry portrait", "Prince Harry photo"],
        None,
        tmp_path / "scene-output",
        "Prince Harry",
        {},
        {},
        set(),
        threading.Lock(),
        threading.Lock(),
        threading.Lock(),
        fast_mode=True,
        max_candidate_downloads=8,
        deadline_at=time.monotonic() + 5,
        query_seconds_override=0.5,
        visual_requirement={
            "canonical_entity": "Prince Harry",
            "subject_type": "person",
            "required_terms": ["Prince Harry"],
            "strict_identity": True,
        },
        defer_context_verification=True,
        exhaustive_fallback=True,
    )

    assert result and result.get("path")
    assert result["provider"] == "openverse"
    # Scene-action enrichment can precede the portrait query. The first
    # concrete query gets the fast tier; later queries get recovery sources.
    first_query = provider_calls[0][1]
    assert {source for source, query in provider_calls if query == first_query} == {
        "universal_scraper", "yandex", "flickr_api", "bing_hybrid", "ddg_hybrid"
    }
    assert ('openverse', 'Prince Harry photo') in provider_calls


def test_web_only_provider_cannot_drop_universal_scraper(monkeypatch):
    """The universal scraper is the web-image path by permanent operator
    instruction: no VIDZEO_WEB_ONLY_PROVIDER allowlist may drop it. The
    allowlist restricts the OTHER lanes only."""
    from services.web_image.resolver import _enabled_fast_sources

    monkeypatch.setenv("VIDZEO_WEB_ONLY_PROVIDER", "wikimedia")
    names = [name for name, _kind in _enabled_fast_sources()]
    assert "universal_scraper" in names
    assert "wikimedia" in names
    # The allowlist still restricts every other lane.
    assert "yandex" not in names
    assert "bing_hybrid" not in names
    assert "flickr_api" not in names

    monkeypatch.setenv("VIDZEO_WEB_ONLY_PROVIDER", "yandex,google_images")
    names = [name for name, _kind in _enabled_fast_sources()]
    assert "universal_scraper" in names
    assert "yandex" in names
    assert "google_images" in names
    assert "wikimedia" not in names

    # With no allowlist the scraper is in the defaults as well.
    monkeypatch.delenv("VIDZEO_WEB_ONLY_PROVIDER", raising=False)
    assert "universal_scraper" in [name for name, _kind in _enabled_fast_sources()]
