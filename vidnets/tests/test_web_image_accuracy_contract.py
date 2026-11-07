import os
import sys
import time
from pathlib import Path
from unittest import mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.web_image.resolver import (
    _cross_scene_phash_threshold,
    _is_biographical_person,
    _metadata_candidate_class,
    _shared_batch_subject,
)
from services.web_image import resolver
from services.web_image.safety import candidate_is_safe
from services.visual_intelligence.decision import normalize_scene_decision


def test_royal_anchor_does_not_invent_an_unmentioned_family_member():
    from services.web_image.query import _detect_royal_family_anchor, _royal_scene_subject
    anchor = _detect_royal_family_anchor('British Royal Family',
        'Elizabeth the Second reigned for seventy years. Charles became King.')
    assert anchor == 'British Royal Family'
    assert 'Harry' not in anchor
    assert _royal_scene_subject('Elizabeth the Second was crowned in 1953.', anchor) == 'Queen Elizabeth II'
from services.web_image.query import _enforce_royal_query_contract, _ensure_query_diversity


def test_titanic_is_a_vessel_not_a_person():
    assert not _is_biographical_person("RMS Titanic")
    assert not _is_biographical_person("SS Californian")
    assert _is_biographical_person("Edward Smith")


def test_cross_scene_phash_dedup_is_conservative_and_configurable(monkeypatch):
    """Distinct archival compositions must survive for scene-level vision review."""
    monkeypatch.delenv("VIDZEO_WEB_CROSS_SCENE_PHASH_THRESHOLD", raising=False)
    assert _cross_scene_phash_threshold() == 6

    monkeypatch.setenv("VIDZEO_WEB_CROSS_SCENE_PHASH_THRESHOLD", "4")
    assert _cross_scene_phash_threshold() == 4

    # Operators cannot widen this backstop beyond the legacy near-duplicate
    # ceiling, and invalid/zero values still leave exact pHash derivatives
    # blocked while allowing most distinct compositions through.
    monkeypatch.setenv("VIDZEO_WEB_CROSS_SCENE_PHASH_THRESHOLD", "99")
    assert _cross_scene_phash_threshold() == 12
    monkeypatch.setenv("VIDZEO_WEB_CROSS_SCENE_PHASH_THRESHOLD", "0")
    assert _cross_scene_phash_threshold() == 1


def test_identity_only_web_candidate_is_deferred_to_final_visual_gate(monkeypatch, tmp_path):
    """Scene-action metadata gaps must not discard an otherwise valid rail role."""
    from services.visual_requirement import build_visual_requirement

    candidate = {
        "url": "https://images.example/railway-crew-briefing.jpg",
        "image_url": "https://images.example/railway-crew-briefing.jpg",
        "title": "Locomotive Engineer at Work",
        "source": "flickr_api",
        "provider": "flickr",
        "domain": "flickr.com",
        "source_url": "https://railway.example/railway-crew-briefing",
        "query": "Train Crew briefing",
    }
    monkeypatch.setattr(
        resolver,
        "_discover_source_query",
        lambda *_args, **_kwargs: [dict(candidate)],
    )

    def fake_download(urls, output_dir, **_kwargs):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for index, url in enumerate(urls):
            path = output_dir / f"candidate_{index}.jpg"
            path.write_bytes(b"candidate" * 2048)
            rows.append((url, path, ""))
        return rows

    monkeypatch.setattr(resolver, "download_bulk", fake_download)
    monkeypatch.setattr(resolver, "_validate_image", lambda _path: (1280, 720))
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(resolver, "_compute_average_hash", lambda _path: 42)

    requirement = build_visual_requirement(
        title="Train Crew",
        script="A documentary about railway staff.",
        scene_text="Before departure, the crew prepares the safety briefing.",
    )
    row = {
        "_semantic_locked": True,
        "visual_requirement": requirement,
        "query_variants": ["Train Crew briefing"],
    }
    result = resolver.resolve_web_images_for_video_fast(
        script="A documentary about railway staff.",
        title="Train Crew",
        scene_texts=["Before departure, the crew prepares the safety briefing."],
        scene_durations=[3.0],
        scene_queries_override=[row],
        subject_override="Train Crew",
        output_namespace="identity-context-pending-test",
        fast_mode=True,
        max_candidate_downloads=8,
        max_scene_workers=1,
        api_worker_limit=1,
        defer_context_verification=True,
    )

    assert result["image_paths"][0]
    scene = result["audit"]["scenes"][0]
    assert scene["resolved"] is True
    assert scene["candidate_pool_size"] >= 1
    assert scene["candidate_evidence"]["metadata_source"] == "identity_only_scene_context_pending"


def test_royal_queries_use_complete_scene_names_and_reject_music_collisions():
    scenes = [
        "Prince Harry challenged the decision.",
        "Meghan Markle and Prince Harry stepped back from royal duties.",
        "Prince William met King Charles III at the palace.",
    ]
    rows = [
        {"search_query": "Harry singer interview", "youtube_query": "Harry music video"},
        {"search_query": "Meghan Harry news", "youtube_query": "Harry band footage"},
        {"search_query": "William Charles meeting", "youtube_query": "William concert"},
    ]
    repaired = _enforce_royal_query_contract(
        rows,
        "Royal family crisis",
        "The British Royal Family includes Prince Harry and King Charles III. " + " ".join(scenes),
        scenes,
    )

    assert [row["subject_query"] for row in repaired] == [
        "Prince Harry",
        "Meghan Markle and Prince Harry",
        "Prince William and King Charles III",
    ]
    for row in repaired:
        assert row["search_query"].startswith(row["subject_query"])
        assert row["youtube_query"].startswith(row["subject_query"])
        assert not any(
            bad in " ".join(row["query_variants"]).lower()
            for bad in ("singer", "concert", "music", "band")
        )


def test_royal_search_aliases_reach_vision_without_becoming_identity_proof():
    from services.visual_requirement import build_visual_requirement, candidate_requirement_match, provider_query_variants

    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="A documentary about the British Royal Family.",
        scene_text="Prince Archie appears with Prince Harry and Meghan Markle.",
    )
    assert requirement["canonical_entity"] == "Prince Archie"
    assert "Archie Harrison Mountbatten-Windsor" in requirement["aliases"]
    queries = provider_query_variants(requirement, provider="web")
    assert queries[:4] == [
        "Prince Archie portrait",
        "Prince Archie photo",
        "Archie Harrison Mountbatten-Windsor portrait",
        "Archie Harrison Mountbatten-Windsor photo",
    ]

    matched, _reason, evidence = candidate_requirement_match(
        requirement,
        {"title": "Archie Harrison Mountbatten-Windsor with his family"},
    )
    assert matched
    assert evidence["identity_proof"] == "Prince Archie"


def test_alias_only_search_hit_is_pending_for_pixels_not_rejected(tmp_path, monkeypatch):
    from PIL import Image
    from services.visual_requirement import build_visual_requirement

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_WEB_VERBOSE", "1")
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="A documentary about the British Royal Family.",
        scene_text="Prince Archie appears with Prince Harry and Meghan Markle.",
    )
    query = "Archie Harrison Mountbatten Windsor portrait"
    candidate_path = tmp_path / "alias_candidate.jpg"
    Image.effect_noise((800, 500), 97).convert("RGB").save(
        candidate_path, format="JPEG", quality=94
    )
    candidate = {
        "path": str(candidate_path),
        "url": "https://images.example/archie-family-portrait.jpg",
        "image_url": "https://images.example/archie-family-portrait.jpg",
        "source_url": "https://news.example/royal-family/archie",
        "source": "universal_scraper",
        "provider": "yandex",
        "title": query,
        "query": query,
        "title_is_query": True,
        "query_echo": True,
    }
    monkeypatch.setattr(resolver, "_discover_source_query", lambda *_args, **_kwargs: [dict(candidate)])
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    locked_row = {
        "_semantic_locked": True,
        "section_subject": "Prince Archie",
        "visual_requirement": requirement,
        "web_query_variants": [query],
    }
    result = resolver.resolve_web_images_for_video_fast(
        script="A documentary about the British Royal Family.",
        title="The British Royal Family",
        scene_texts=["Prince Archie appears with Prince Harry and Meghan Markle."],
        scene_durations=[4.0],
        scene_queries_override=[locked_row],
        subject_override="Prince Archie",
        max_scene_workers=1,
        api_worker_limit=1,
        fast_mode=True,
        defer_context_verification=True,
        stage_deadline_seconds=45.0,
        output_namespace="royal-alias-pending-test",
    )

    assert result["audit"]["resolved"] == 1
    scene = result["audit"]["scenes"][0]
    assert scene["visual_outcome"] == "pending_external"
    assert scene["candidate_evidence"]["external_visual_gate_required"] is True


def test_prebuilt_query_rows_are_repaired_before_provider_search():
    rows = _ensure_query_diversity(
        [{
            "subject_query": "Messi",
            "scene_subject": "Messi",
            "search_query": "messi trophy celebration",
            "youtube_query": "messi match footage",
        }],
        "Football legends",
        "Lionel Messi lifted the World Cup trophy.",
        ["Messi lifted the World Cup trophy."],
    )
    assert rows[0]["subject_query"] == "Lionel Messi"
    assert rows[0]["search_query"].startswith("Lionel Messi")
    assert rows[0]["youtube_query"].startswith("Lionel Messi")


def test_recurring_scene_entity_replaces_weak_truncated_shared_anchor():
    scenes = [f"Passengers aboard RMS Titanic scene {index}" for index in range(17)]

    assert _shared_batch_subject(
        "On April",
        ["RMS Titanic"] * len(scenes),
        scenes,
    ) == "RMS Titanic"


def test_multi_person_royal_story_uses_family_pool_not_first_person():
    scene_subjects = ["Prince Harry"] * 8 + ["Prince William"] * 5 + ["King Charles III"] * 4
    scene_texts = [f"Royal Family scene {index}" for index in range(len(scene_subjects))]

    assert _shared_batch_subject(
        "Prince Harry",
        scene_subjects,
        scene_texts,
        topic_anchor="King Charles III and Prince Harry British Royal Family",
    ) == "British Royal Family"


def test_multi_person_story_without_a_broad_domain_skips_shared_person_pool():
    scene_subjects = ["Prince Harry", "Prince William", "King Charles III"]
    scene_texts = ["A scene about a royal family member."] * len(scene_subjects)

    assert _shared_batch_subject(
        "Prince Harry", scene_subjects, scene_texts, topic_anchor="Family reunion"
    ) == ""


def test_random_flickr_result_is_rejected_without_using_query_text_as_evidence():
    item = {
        "title": "Tomato harvest in India",
        "tags": "people market vegetable",
        "url": "https://live.staticflickr.com/example/tomato.jpg",
        "source_url": "https://www.flickr.com/photos/example/123/",
        "source": "flickr_api",
        # A provider returning this row for this query must not make it relevant.
        "query": "RMS Titanic passengers",
    }
    candidate_class, reason = _metadata_candidate_class(
        item,
        main_subject="RMS Titanic",
        scene_text="Titanic passengers waited beside the lifeboats.",
    )
    assert candidate_class == "irrelevant"
    assert reason == "missing_container_anchor"


def test_anchor_plus_scene_facet_outranks_entity_only_metadata():
    scene = "Titanic passengers waited beside lifeboats on the boat deck."
    exact, _ = _metadata_candidate_class(
        {
            "title": "Titanic passengers beside lifeboats",
            "url": "https://archive.example/titanic-lifeboats.jpg",
        },
        main_subject="RMS Titanic",
        scene_text=scene,
    )
    generic, reason = _metadata_candidate_class(
        {
            "title": "Unmarked grave of RMS Titanic quartermaster",
            "url": "https://archive.example/titanic-grave.jpg",
        },
        main_subject="RMS Titanic",
        scene_text=scene,
    )
    assert exact == "anchored"
    assert generic == "anchored_generic"
    assert reason == "missing_scene_facet"


def test_conflicting_named_entity_is_rejected_before_download():
    candidate_class, reason = _metadata_candidate_class(
        {
            "title": "SS Britannic hospital ship at sea",
            "url": "https://archive.example/britannic.jpg",
        },
        main_subject="RMS Titanic",
        scene_text="Titanic crossed the North Atlantic.",
        negative_entities=["SS Britannic"],
    )
    assert candidate_class == "irrelevant"
    assert reason.startswith("conflicting_entity:")


def test_adult_and_watermarked_publishers_are_blocked_from_all_provider_paths():
    assert candidate_is_safe({
        "title": "Titanic archive",
        "url": "https://cdn.example/titanic.jpg",
        "source_url": "https://alamy.com/titanic-watermarked-photo",
    }) == (False, "ownership_watermark_source")
    assert candidate_is_safe({
        "title": "NSFW nude model",
        "url": "https://live.staticflickr.com/example/image.jpg",
    }) == (False, "adult_metadata")


def test_vision_confirmed_adult_or_watermark_can_never_be_unverified_winner():
    adult = normalize_scene_decision({
        "relevance": 95,
        "confidence": 99,
        "adult_content": True,
    }, media_type="image")
    watermark = normalize_scene_decision({
        "relevance": 95,
        "confidence": 99,
        "ownership_watermark": True,
    }, media_type="image")
    assert adult.status == "rejected"
    assert adult.fatal_mismatch
    assert watermark.status == "rejected"


def test_historical_text_inside_photo_is_not_automatically_rejected():
    decision = normalize_scene_decision({
        "relevance": 82,
        "quality": 60,
        "confidence": 91,
        "has_text": True,
        "disqualifying_text": False,
    }, media_type="image")
    assert decision.status == "accepted"


def test_stable_topic_cache_survives_disposable_scene_directory(tmp_path, monkeypatch):
    from PIL import Image
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    scene_dir = tmp_path / "web_images_simple_job" / "scene_1"
    scene_dir.mkdir(parents=True)
    source = scene_dir / "titanic.jpg"
    Image.effect_noise((800, 450), 48).convert("RGB").save(source, quality=92)
    resolver._persist_web_asset_cache(
        [{
            "path": str(source),
            "title": "RMS Titanic lifeboats",
            "source_url": "https://archive.example/titanic-lifeboats",
            "domain": "archive.example",
            "candidate_class": "anchored",
        }],
        ["RMS Titanic"],
        "RMS Titanic",
    )
    source.unlink()

    cached = resolver._load_historical_web_assets()

    assert len(cached) == 1
    assert cached[0]["active_subject"] == "RMS Titanic"
    assert os.path.exists(cached[0]["path"])


def test_one_bad_comparison_candidate_does_not_reject_the_scene(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    wrong = tmp_path / "wrong.jpg"
    right = tmp_path / "right.jpg"
    Image.new("RGB", (800, 450), color=(180, 30, 30)).save(wrong)
    Image.new("RGB", (800, 450), color=(30, 60, 110)).save(right)

    class Client:
        def compare_images(self, _paths, _prompt):
            return {"candidates": [
                {"relevance": 10, "confidence": 95, "fatal_mismatch": True},
                {"relevance": 90, "quality": 70, "confidence": 94},
            ]}

    class Context:
        model = "test-model"

        def get_client(self):
            return Client()

    winner = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Titanic passengers entered lifeboats.",
        subject="RMS Titanic",
        global_topic="RMS Titanic",
        query="RMS Titanic passengers lifeboats",
        candidates=[
            {"path": str(wrong), "domain": "flickr_api", "candidate_class": "anchored", "provider_metadata_score": 8},
            {"path": str(right), "domain": "yandex", "candidate_class": "anchored", "provider_metadata_score": 7},
        ],
        visual_context=Context(),
    )

    assert winner is not None
    assert winner["path"] == str(right)
    assert winner["visual_decision"]["status"] == "accepted"


def test_visual_unavailable_strict_mode_keeps_metadata_winner(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    candidate = tmp_path / "candidate.jpg"
    Image.new("RGB", (800, 450), color=(40, 80, 120)).save(candidate)

    class Client:
        def compare_images(self, _paths, _prompt):
            raise RuntimeError("vision provider unavailable")

    class Context:
        model = "test-model"
        visual_required = True

        def get_client(self):
            return Client()

    winner = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="RMS Titanic passengers entered lifeboats.",
        subject="RMS Titanic",
        global_topic="RMS Titanic",
        query="RMS Titanic passengers lifeboats",
        candidates=[{"path": str(candidate), "domain": "yandex", "candidate_class": "anchored"}],
        visual_context=Context(),
    )

    assert winner is not None
    assert winner["path"] == str(candidate)
    assert winner["visual_outcome"] == "unavailable"
    assert winner["visual_unverified"] is True


def test_default_web_resolver_keeps_all_bounded_provider_lanes_enabled():
    from services.web_image.resolver import _enabled_fast_sources

    names = {name for name, _kind in _enabled_fast_sources()}
    assert {"flickr_api", "yandex", "bing_hybrid", "ddg_hybrid", "openverse", "wikimedia"}.issubset(names)
    assert {"getty_bing", "getty_ddg"}.issubset(names)


def test_visual_unavailable_non_strict_marks_unverified(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    candidate = tmp_path / "candidate.jpg"
    Image.new("RGB", (800, 450), color=(40, 80, 120)).save(candidate)

    class Client:
        def compare_images(self, _paths, _prompt):
            raise RuntimeError("vision provider unavailable")

    class Context:
        model = "test-model"
        visual_required = False

        def get_client(self):
            return Client()

    winner = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="RMS Titanic passengers entered lifeboats.",
        subject="RMS Titanic",
        global_topic="RMS Titanic",
        query="RMS Titanic passengers lifeboats",
        candidates=[{"path": str(candidate), "domain": "yandex", "candidate_class": "anchored"}],
        visual_context=Context(),
    )

    assert winner is not None
    assert winner["visual_gate_status"] == "unavailable"


def test_visual_rejection_is_advisory_in_delivery_mode(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    candidate = tmp_path / "candidate.jpg"
    Image.new("RGB", (800, 450), color=(40, 80, 120)).save(candidate)

    class Client:
        def compare_images(self, _paths, _prompt):
            return {"acceptable": False, "candidates": []}

    class Context:
        model = "test-model"
        visual_required = False

        def get_client(self):
            return Client()

    with mock.patch.dict(os.environ, {"VIDZEO_VISUAL_REJECT_ONLY_IRRELEVANT": "0"}, clear=False):
        winner = _rank_deferred_scene_shortlist(
            scene_index=0,
            scene_text="RMS Titanic passengers entered lifeboats.",
            subject="RMS Titanic",
            global_topic="RMS Titanic",
            query="RMS Titanic passengers lifeboats",
            candidates=[{"path": str(candidate), "domain": "archive", "candidate_class": "anchored"}],
            visual_context=Context(),
        )

    assert winner is not None
    assert winner["path"] == str(candidate)
    assert winner["visual_gate_status"] == "advisory_rejected"


def test_high_brand_score_cannot_override_explicit_scene_mismatch(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist
    paths = [tmp_path / 'exterior.jpg', tmp_path / 'touchscreen.jpg']
    for path in paths:
        Image.new('RGB', (800, 450), color=(40, 80, 120)).save(path)
    class Client:
        def compare_images(self, _paths, _prompt):
            return {'acceptable': True, 'candidates': [
                {'relevance': 95, 'quality': 95, 'confidence': 95,
                 'domain_match': True, 'scene_match': False},
                {'relevance': 70, 'quality': 70, 'confidence': 90,
                 'domain_match': True, 'scene_match': True}]}
    class Context:
        model = 'test-model'
        visual_required = True
        def get_client(self):
            return Client()
    winner = _rank_deferred_scene_shortlist(scene_index=0,
        scene_text='Software lives on the central touchscreen.', subject='Tesla', global_topic='Tesla',
        query='Tesla touchscreen', candidates=[{'path':str(path), 'domain':'archive',
            'candidate_class':'anchored'} for path in paths], visual_context=Context())
    assert winner['path'] == str(paths[1])
    assert winner['visual_decision']['scene_match'] is True


def test_web_shortlist_skips_mods_and_visible_wrong_subsystem(tmp_path):
    from PIL import Image
    paths = [tmp_path / name for name in ('mod.jpg', 'solar.jpg', 'charging.jpg')]
    for path in paths:
        Image.new('RGB', (800, 450), color=(40, 80, 120)).save(path)
    class Client:
        def compare_images(self, selected, _prompt):
            assert selected == [str(paths[1]), str(paths[2])]
            return {'confidence':95, 'candidates':[
                {'relevance':99, 'quality':90, 'scene_match':True, 'domain_match':True,
                 'scene_description':'Workers installing solar panels on a roof.'},
                {'relevance':85, 'quality':85, 'scene_match':True, 'domain_match':True,
                 'scene_description':'Tesla cars parked beside Supercharger stations.'}]}
    class Context:
        model = 'test-model'
        visual_required = True
        def get_client(self):
            return Client()
    winner = resolver._rank_deferred_scene_shortlist(scene_index=0,
        scene_text='Supercharger stations help drivers recharge.', subject='Tesla', global_topic='Tesla',
        query='Tesla Supercharger station', candidates=[
            {'path':str(paths[0]), 'title':'Tesla Supercharger FS25 KingMods', 'domain':'kingmods.net'},
            {'path':str(paths[1]), 'title':'Tesla solar installation', 'domain':'archive'},
            {'path':str(paths[2]), 'title':'Tesla charging station', 'domain':'archive'}],
        visual_context=Context())
    assert winner['path'] == str(paths[2])
    assert 'Supercharger' in winner['visual_decision']['scene_description']


def test_aggregate_visual_rejection_cannot_override_contextual_candidate_evidence(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    candidate = tmp_path / "contextual.jpg"
    Image.new("RGB", (800, 450), color=(40, 80, 120)).save(candidate)

    class Client:
        def compare_images(self, _paths, _prompt):
            return {
                "acceptable": False,
                "confidence": 95,
                "reason": "The image is contextual rather than an exact moment.",
                "candidates": [{
                    "relevance": 35,
                    "quality": 65,
                    "confidence": 90,
                    "domain_match": True,
                    "scene_match": True,
                    "fatal_mismatch": False,
                }],
            }

    class Context:
        model = "test-model"
        visual_required = True

        def get_client(self):
            return Client()

    winner = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Prince Harry and Meghan prepare to return to Britain.",
        subject="Prince Harry",
        global_topic="The British Royal Family",
        query="Prince Harry portrait",
        candidates=[{"path": str(candidate), "domain": "archive", "candidate_class": "anchored"}],
        visual_context=Context(),
    )

    assert winner["path"] == str(candidate)
    assert winner["visual_outcome"] == "pass"
    assert winner["visual_decision"]["status"] == "accepted"


def test_aggregate_visual_rejection_without_rows_is_uncertain_not_a_hard_veto(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    candidate = tmp_path / "uncertain.jpg"
    Image.new("RGB", (800, 450), color=(40, 80, 120)).save(candidate)

    class Client:
        def compare_images(self, _paths, _prompt):
            return {"acceptable": False, "confidence": 98, "candidates": []}

    class Context:
        model = "test-model"
        visual_required = True

        def get_client(self):
            return Client()

    winner = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Prince Harry and Meghan prepare to return to Britain.",
        subject="Prince Harry",
        global_topic="The British Royal Family",
        query="Prince Harry portrait",
        candidates=[{"path": str(candidate), "domain": "archive", "candidate_class": "anchored"}],
        visual_context=Context(),
    )

    assert winner["path"] == str(candidate)
    assert winner["visual_outcome"] == "uncertain"
    assert winner["visual_unverified"] is True
    assert winner.get("visual_rejected") is not True


def test_relaxed_visual_gate_keeps_borderline_but_rejects_confident_irrelevance(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    candidate = tmp_path / "candidate.jpg"
    Image.new("RGB", (800, 450), color=(40, 80, 120)).save(candidate)

    class Client:
        def __init__(self, relevance, confidence):
            self.relevance = relevance
            self.confidence = confidence

        def compare_images(self, _paths, _prompt):
            return {
                "acceptable": True,
                "candidates": [{
                    "relevance": self.relevance,
                    "quality": 40,
                    "confidence": self.confidence,
                    "fatal_mismatch": False,
                }],
            }

    class Context:
        model = "test-model"
        visual_required = False

        def __init__(self, relevance, confidence):
            self.client = Client(relevance, confidence)

        def get_client(self):
            return self.client

    kwargs = dict(
        scene_index=0,
        scene_text="RMS Titanic passengers entered lifeboats.",
        subject="RMS Titanic",
        global_topic="RMS Titanic",
        query="RMS Titanic passengers lifeboats",
        candidates=[{"path": str(candidate), "domain": "archive", "candidate_class": "anchored"}],
    )
    with mock.patch.dict(os.environ, {"VIDZEO_VISUAL_REJECT_ONLY_IRRELEVANT": "1"}, clear=False):
        borderline = _rank_deferred_scene_shortlist(**kwargs, visual_context=Context(42, 60))
        irrelevant = _rank_deferred_scene_shortlist(**kwargs, visual_context=Context(10, 95))

    assert borderline["path"] == str(candidate)
    assert irrelevant is not None
    assert irrelevant["path"] == ""
    assert irrelevant["visual_rejected"] is True


def test_slow_vision_does_not_block_later_scene_discovery(tmp_path, monkeypatch):
    from PIL import Image
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "10")
    monkeypatch.setenv("VIDZEO_VISION_JOB_CONCURRENCY", "2")
    network_finished = []
    vision_finished = []

    def fake_resolve(idx, *_args, **_kwargs):
        time.sleep(0.03)
        path = tmp_path / f"candidate_{idx}.jpg"
        Image.new("RGB", (800, 450), color=(20 + idx, 40, 70)).save(path)
        network_finished.append(time.monotonic())
        candidate = {
            "path": str(path),
            "domain": "yandex",
            "title": f"RMS Titanic scene {idx}",
            "source_url": f"https://archive.example/titanic/{idx}",
            "candidate_class": "anchored",
            "provider_metadata_score": 8,
        }
        return dict(
            candidate,
            _vision_query="RMS Titanic",
            _vision_shortlist=[candidate],
            reused_from_scene=False,
        )

    class Client:
        def compare_images(self, _paths, _prompt):
            time.sleep(0.20)
            vision_finished.append(time.monotonic())
            return {"candidates": [{"relevance": 90, "quality": 70, "confidence": 90}]}

    class Context:
        model = "test-model"

        def get_client(self):
            return Client()

    monkeypatch.setattr(resolver, "_resolve_scene", fake_resolve)
    scenes = [f"RMS Titanic scene {idx}" for idx in range(8)]
    result = resolver.resolve_web_images_for_video_fast(
        script=" ".join(scenes),
        title="RMS Titanic",
        scene_texts=scenes,
        scene_durations=[3.0] * len(scenes),
        scene_queries_override=[{"query_variants": ["RMS Titanic"]} for _ in scenes],
        subject_override="RMS Titanic",
        max_scene_workers=4,
        api_worker_limit=4,
        visual_context=Context(),
        output_namespace="parallel-vision-test",
    )

    assert all(result["image_paths"])
    assert len(network_finished) == 8
    assert len(vision_finished) == 8
    assert max(network_finished) < min(vision_finished)


def test_semantic_lock_precedes_legacy_subject_extraction(tmp_path, monkeypatch):
    """A locked entity must be installed before broad legacy extraction runs."""
    from pathlib import Path
    from PIL import Image
    from services.web_image import query, resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "3")

    def forbidden_legacy_extractor(*_args, **_kwargs):
        raise AssertionError("legacy subject extraction ran despite semantic lock")

    monkeypatch.setattr(query, "_extract_main_subject", forbidden_legacy_extractor)
    monkeypatch.setattr(query, "_extract_all_potential_subjects", forbidden_legacy_extractor)

    def fake_resolve(idx, *_args, **_kwargs):
        path = Path(tmp_path) / f"locked_candidate_{idx}.jpg"
        Image.new("RGB", (800, 450), color=(30, 50, 80)).save(path)
        candidate = {
            "path": str(path),
            "domain": "archive",
            "title": "Jaguar E-Type 1961 archival photograph",
            "source_url": f"https://archive.example/jaguar/{idx}",
            "candidate_class": "anchored",
            "reused_from_scene": False,
        }
        return dict(candidate, _vision_shortlist=[candidate])

    monkeypatch.setattr(resolver, "_resolve_scene", fake_resolve)
    locked_row = {
        "_semantic_locked": True,
        "section_subject": "Geneva Motor",
        "visual_requirement": {
            "canonical_entity": "Jaguar",
            "subject_type": "automobile",
            "required_attributes": {
                "model": "e type",
                "year": "1961",
                "era": "classic_historical",
            },
            "required_terms": ["Jaguar", "1961", "e type"],
            "strict_identity": True,
        },
    }
    result = resolver.resolve_web_images_for_video_fast(
        script="The E-Type was revealed at the Geneva Motor Show.",
        title="Jaguar",
        scene_texts=["The E-Type was revealed at the Geneva Motor Show."],
        scene_durations=[3.0],
        scene_queries_override=[locked_row],
        subject_override="Geneva Motor",
        max_scene_workers=1,
        api_worker_limit=1,
        visual_context=None,
        output_namespace="semantic-lock-order-test",
    )

    assert result["image_paths"][0]
    assert result["audit"]["scenes"][0]["active_subject"] == "Jaguar"


def test_v2_transport_hands_metadata_mismatch_to_external_vision(tmp_path, monkeypatch):
    """A disabled transport verifier must not preempt V2's live pixel gate."""
    from PIL import Image

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_WEB_VISUAL_GRACE_SECONDS", "0.1")
    candidate_path = tmp_path / "related-royal-photo.jpg"
    Image.effect_noise((800, 450), 117).convert("RGB").save(
        candidate_path, format="JPEG", quality=92
    )

    candidate = {
        "path": str(candidate_path),
        "url": "https://images.example/royal-family-event.jpg",
        "provider": "yandex",
        "title": "Prince William and Prince Harry at a family event",
        "identity_failed": True,
        "semantic_visual_pending": True,
        "candidate_evidence": {
            "canonical_entity_mismatch": True,
            "metadata_source": "nearest_match_pending",
            "verification_required": True,
        },
        "candidate_pool_size": 1,
        "downloaded_candidate_count": 1,
        "_vision_shortlist": [],
    }

    def fake_resolve(*_args, **_kwargs):
        return dict(candidate, _vision_shortlist=[dict(candidate)])

    class DisabledTransportContext:
        model = "transport-disabled"
        visual_required = False
        degraded_reason = ""

        def get_client(self):
            return None

    monkeypatch.setattr(resolver, "_resolve_scene", fake_resolve)
    locked_row = {
        "_semantic_locked": True,
        "section_subject": "Prince Harry",
        "visual_requirement": {
            "canonical_entity": "Prince Harry",
            "subject_type": "person",
            "required_terms": ["Prince Harry"],
            "strict_identity": True,
        },
        "web_query_variants": ["Prince Harry portrait"],
    }
    result = resolver.resolve_web_images_for_video_fast(
        script="Prince Harry and Prince William at a family event.",
        title="British Royal Family",
        scene_texts=["Prince Harry and Prince William at a family event."],
        scene_durations=[4.0],
        scene_queries_override=[locked_row],
        subject_override="British Royal Family",
        max_scene_workers=1,
        visual_context=DisabledTransportContext(),
        defer_context_verification=True,
        stage_deadline_seconds=3.0,
        output_namespace="external-vision-handoff-test",
    )

    assert result["audit"]["resolved"] == 1
    assert result["image_paths"][0] == str(candidate_path)
    row = result["audit"]["scenes"][0]
    assert row["visual_outcome"] == "pending_external"
    assert row["candidate_evidence"]["external_visual_gate_required"] is True

    # The wire format intentionally serializes a compact audit, not the
    # resolver's transient semantic_visual_pending flag. Verify the exact row
    # that the V2 adapter and coordinator receive remains eligible for the
    # authoritative pixel gate despite its metadata mismatch.
    from services.pipeline_v2_media_adapter import _web_candidate_is_explicitly_pending_visual
    from services.media_resolution_coordinator import _candidate_hard_gate

    assert _web_candidate_is_explicitly_pending_visual(row)
    scene = {
        "container_id": "royal-family",
        "visual_requirement": locked_row["visual_requirement"],
    }
    candidate_for_gate = {
        **row,
        "container_id": "royal-family",
        "semantic_context_pending": True,
        "path": str(candidate_path),
    }
    passed, reason = _candidate_hard_gate(scene, candidate_for_gate)
    assert passed, reason


def test_retrieval_deadline_returns_completed_candidates_without_waiting_for_blocked_provider(tmp_path, monkeypatch):
    from PIL import Image
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "1")

    def fake_resolve(idx, *_args, **_kwargs):
        time.sleep(2.0 if idx == 0 else 0.05)
        path = tmp_path / f"deadline_candidate_{idx}.jpg"
        Image.new("RGB", (800, 450), color=(30 + idx, 50, 80)).save(path)
        return {
            "path": str(path),
            "domain": "archive",
            "title": f"RMS Titanic scene {idx}",
            "source_url": f"https://archive.example/titanic/{idx}",
            "candidate_class": "anchored",
            "reused_from_scene": False,
        }

    monkeypatch.setattr(resolver, "_resolve_scene", fake_resolve)
    started = time.monotonic()
    result = resolver.resolve_web_images_for_video_fast(
        script="RMS Titanic archive documentary",
        title="RMS Titanic",
        scene_texts=["departure", "collision", "rescue"],
        scene_durations=[3.0, 3.0, 3.0],
        scene_queries_override=[{"query_variants": ["RMS Titanic"]}] * 3,
        subject_override="RMS Titanic",
        max_scene_workers=3,
        visual_context=None,
        output_namespace="retrieval-deadline-test",
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.7
    assert result["audit"]["deadline_reached"] is True
    assert any(result["image_paths"])


def test_visual_grace_retains_completed_strict_decisions(tmp_path, monkeypatch):
    from PIL import Image
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "5")
    monkeypatch.setenv("VIDZEO_WEB_VISUAL_GRACE_SECONDS", "1")
    monkeypatch.setenv("VIDZEO_WEB_VISUAL_RETRIES", "0")

    def fake_resolve(idx, *_args, **_kwargs):
        path = tmp_path / f"strict_candidate_{idx}.jpg"
        Image.new("RGB", (800, 450), color=(30 + idx, 50, 80)).save(path)
        candidate = {
            "path": str(path),
            "domain": "archive",
            "title": f"RMS Titanic scene {idx}",
            "source_url": f"https://archive.example/titanic/{idx}",
            "candidate_class": "anchored",
        }
        return dict(candidate, _vision_shortlist=[candidate], reused_from_scene=False)

    class Client:
        def compare_images(self, paths, _prompt):
            if "strict_candidate_0" in str(paths[0]):
                time.sleep(2.0)
            return {"candidates": [{"relevance": 92, "quality": 80, "confidence": 95}]}

    class Context:
        model = "test-model"
        visual_required = True

        def get_client(self):
            return Client()

    monkeypatch.setattr(resolver, "_resolve_scene", fake_resolve)
    started = time.monotonic()
    result = resolver.resolve_web_images_for_video_fast(
        script="RMS Titanic archive documentary",
        title="RMS Titanic",
        scene_texts=["departure", "collision", "rescue"],
        scene_durations=[3.0, 3.0, 3.0],
        scene_queries_override=[{"query_variants": ["RMS Titanic"]}] * 3,
        subject_override="RMS Titanic",
        max_scene_workers=3,
        visual_context=Context(),
        output_namespace="visual-grace-test",
    )
    elapsed = time.monotonic() - started

    assert elapsed < 4.0
    assert result["image_paths"][0]
    assert result["image_paths"][1]
    assert result["image_paths"][2]


def test_shared_web_batch_coalesces_provider_calls_and_keeps_fast_pool(tmp_path, monkeypatch):
    """One slow provider must not erase a completed shared Flickr pool."""
    from pathlib import Path
    from services.web_image import resolver
    monkeypatch.setattr(resolver, '_discover_universal_urls', lambda *_args, **_kwargs: [])
    # Exercise controlled provider fan-out; optional installed providers and
    # earlier fixtures must not turn an offline coalescing test into web work.
    monkeypatch.setattr(resolver, '_enabled_fast_sources', lambda: [
        ('flickr_api', 'flickr_api'), ('bing_hybrid', 'hybrid'), ('wikimedia', 'api')])
    monkeypatch.setattr(resolver, '_SEARCH_QUERY_CACHE', {})

    monkeypatch.setattr(resolver, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_WEB_STAGE_DEADLINE_SECONDS", "4")
    monkeypatch.setenv("VIDZEO_WEB_BATCH_DISCOVERY_SECONDS", "0.25")
    monkeypatch.setenv("VIDZEO_WEB_QUERY_DEADLINE_SECONDS", "0.25")
    monkeypatch.setenv("VIDZEO_WEB_WORKER_DRAIN_SECONDS", "0.1")

    facets = [
        "boilers", "lifeboats", "iceberg", "bridge", "wireless",
        "passengers", "deck", "propellers", "funnels", "staircase",
        "captain", "lookouts", "collision", "evacuation", "wreckage",
        "ocean", "memorial",
    ]
    candidate_facets = facets + ["launch", "shipyard", "route"]
    candidates = [
        {
            "url": f"https://images.example/titanic_{index}.jpg",
            "image_url": f"https://images.example/titanic_{index}.jpg",
            "title": f"RMS Titanic {facet} archival photograph",
            "source": "flickr_api",
            "provider": "flickr",
            "domain": "flickr.com",
            "query": "RMS Titanic",
            "tags": f"RMS Titanic ship archive {facet}",
            "source_url": f"https://flickr.example/photos/{index}",
        }
        for index, facet in enumerate(candidate_facets)
    ]
    provider_calls = {"flickr": 0, "hybrid": 0, "api": 0}

    def fake_flickr(_query):
        provider_calls["flickr"] += 1
        return list(candidates)

    def slow_hybrid(_source, _query):
        provider_calls["hybrid"] += 1
        time.sleep(0.6)
        return []

    def fake_api(_source, _query):
        provider_calls["api"] += 1
        return []

    def fake_download(urls, output_dir, **_kwargs):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for url in urls:
            candidate_id = url.rsplit("_", 1)[-1].split(".", 1)[0]
            path = output_dir / f"batch_{candidate_id}.jpg"
            path.write_bytes((f"image-{candidate_id}" * 2048).encode("ascii"))
            rows.append((url, path, ""))
        return rows

    monkeypatch.setattr(resolver, "_discover_flickr_api_urls", fake_flickr)
    monkeypatch.setattr(resolver, "_discover_hybrid_urls", slow_hybrid)
    monkeypatch.setattr(resolver, "_discover_api_urls", fake_api)
    monkeypatch.setattr(resolver, "download_bulk", fake_download)
    monkeypatch.setattr(resolver, "_validate_image", lambda _path: (1280, 720))
    monkeypatch.setattr(resolver, "image_is_safe", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(
        resolver,
        "_compute_average_hash",
        lambda path: hash(Path(path).name) & ((1 << 64) - 1),
    )
    monkeypatch.setattr(resolver, "_hamming_distance", lambda a, b: 0 if a == b else 64)

    scenes = [f"RMS Titanic {facet} documentary scene" for facet in facets]
    query_rows = [
        {
            "section_subject": "RMS Titanic",
            "query_variants": [f"RMS Titanic {facet} archival photograph"],
        }
        for facet in facets
    ]
    started = time.monotonic()
    result = resolver.resolve_web_images_for_video_fast(
        script=" ".join(scenes),
        title="On April",
        scene_texts=scenes,
        scene_durations=[3.0] * len(scenes),
        scene_queries_override=query_rows,
        subject_override="On April",
        max_scene_workers=8,
        visual_context=None,
        output_namespace="shared-batch-retention-test",
    )
    elapsed = time.monotonic() - started

    assert elapsed < 4.5
    assert all(result["image_paths"])
    assert len(set(result["image_paths"])) == len(scenes)
    assert provider_calls["flickr"] == 1
    assert provider_calls["hybrid"] <= sum(kind == "hybrid" for _, kind in resolver._enabled_fast_sources())
    # One API-lane call per enabled provider (yandex, openverse, wikimedia,
    # google_images) — the pool coalesces; it must not fan out per scene.
    assert provider_calls["api"] <= 4
    assert result["audit"]["shared_batch"]["candidates"] == 20
    assert result["audit"]["shared_batch"]["query"] == "RMS Titanic"
    assert all(
        scene["active_subject"] == "RMS Titanic"
        for scene in result["audit"]["scenes"]
    )
    assert all(
        any(facet in query for query in result["audit"]["scenes"][index]["queries"])
        for index, facet in enumerate(facets)
    )
    assert all(
        facet in result["audit"]["scenes"][index]["title"].lower()
        for index, facet in enumerate(facets)
    )
    provider_budget = result["audit"]["source_broker"]["provider_request_budget"]
    assert provider_budget["cap"] is None
    assert provider_budget["rejected"] == 0
    assert provider_budget["started"] >= 1
def test_gaming_queries_never_use_flickr_and_keep_game_artifact_terms(monkeypatch):
    from services.web_image import resolver
    from services.web_image.query import _topic_fallback_queries

    def forbidden_flickr(*_args, **_kwargs):
        raise AssertionError("Flickr called for a gaming query")

    monkeypatch.setattr(resolver, "search_flickr_api", forbidden_flickr)
    assert resolver._discover_flickr_api_urls("Grand Theft Auto VI official gameplay screenshot") == []
    queries = _topic_fallback_queries(
        "Grand Theft Auto VI",
        "Lucia drives through Vice City in the official trailer.",
        limit=4,
    )
    assert len(queries) == 4
    assert all("Grand Theft Auto VI" in query for query in queries)
    assert all(any(term in query.lower() for term in ("screenshot", "trailer still", "key art")) for query in queries)


def test_exhaustive_web_fallback_is_exposed_as_a_distinct_resolver_mode():
    """The pre-stock web tier must have an explicit exhaustive mode."""
    import inspect

    signature = inspect.signature(resolver.resolve_web_images_for_video_fast)
    assert "exhaustive_fallback" in signature.parameters
    assert signature.parameters["exhaustive_fallback"].default is False
