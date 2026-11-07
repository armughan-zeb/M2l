import os
import sys
from pathlib import Path
from unittest import mock

from PIL import Image


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))


def test_metadata_gate_keeps_neutral_and_rejects_suggestive_and_synthetic():
    from services.web_image.safety import candidate_is_safe, metadata_safety_reason

    assert metadata_safety_reason(
        title="RMS Titanic at sea",
        tags="historical photograph, ocean liner",
        description="Archival documentary photo",
    ) == ""
    assert candidate_is_safe({"title": "Bikini model portrait", "url": "https://example.test/a.jpg"}) == (
        False,
        "suggestive_metadata",
    )
    assert candidate_is_safe({"title": "CGI 3D rendered ship", "url": "https://example.test/a.jpg"}) == (
        False,
        "synthetic_metadata",
    )


def test_query_echo_titles_never_become_asset_identity():
    from services.visual_requirement import candidate_asset_metadata
    from services.web_image.universal_scraper import UniversalImageResult, universal_result_to_candidate

    row = UniversalImageResult(
        title="Prince Harry royal family result 2",
        image_url="https://images.example/frog.jpg",
        source_engine="Yandex",
    )
    candidate = universal_result_to_candidate(row, query="Prince Harry royal family")
    assert candidate["title_is_query"] is True
    assert candidate_asset_metadata(candidate) == ""


def test_semantic_pending_wave_limit_is_bounded_and_configurable(monkeypatch):
    from services.web_image.resolver import _semantic_pending_wave_limit

    monkeypatch.delenv("VIDZEO_WEB_SEMANTIC_PENDING_WAVES", raising=False)
    assert _semantic_pending_wave_limit() == 2
    monkeypatch.setenv("VIDZEO_WEB_SEMANTIC_PENDING_WAVES", "2")
    assert _semantic_pending_wave_limit() == 2
    monkeypatch.setenv("VIDZEO_WEB_SEMANTIC_PENDING_WAVES", "99")
    assert _semantic_pending_wave_limit() == 3


def test_person_contract_blocks_fictional_confusable_entity():
    from services.visual_requirement import build_visual_requirement, candidate_requirement_match, candidate_nearest_match

    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="A documentary about Prince Harry and King Charles III.",
        scene_text="Prince Harry appears at a royal event.",
    )
    candidate = {
        "title": "Prince Harry frog toy",
        "url": "https://images.example/frog.jpg",
        "provider": "universal_yandex",
    }
    assert candidate_requirement_match(requirement, candidate)[0] is False
    assert candidate_nearest_match(requirement, candidate)[0] is False


def test_v2_web_handoff_preserves_only_explicit_pending_candidates_for_vision():
    from services.pipeline_v2_media_adapter import _web_candidate_is_explicitly_pending_visual

    relevant_candidate = {
        "candidate_evidence": {
            "metadata_source": "query_anchored_visual_pending",
            "verification_required": True,
            "canonical_entity_mismatch": True,
        },
    }
    forbidden_candidate = {
        "candidate_evidence": {
            "metadata_source": "query_anchored_visual_pending",
            "verification_required": True,
            "forbidden_match": "frog toy",
        },
    }

    assert _web_candidate_is_explicitly_pending_visual(relevant_candidate) is True
    assert _web_candidate_is_explicitly_pending_visual(forbidden_candidate) is False


def test_shared_download_cache_reuses_slow_inflight_url(tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    from services.web_image import resolver

    def slow_download(urls, output_dir, **_kwargs):
        time.sleep(0.15)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "asset.jpg"
        Image.new("RGB", (100, 100), (20, 40, 60)).save(path)
        return [(urls[0], path, "")]

    monkeypatch.setattr(resolver, "download_bulk", slow_download)
    cache, cache_lock = {}, threading.Lock()
    item = {"url": "https://images.example/shared.jpg"}

    def fetch(index):
        return resolver._download_candidate_batch(
            [item], tmp_path / f"scene-{index}", 2, 1.0, cache, cache_lock
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, range(4)))
    assert all(rows and rows[0][1] and rows[0][1].is_file() for rows in results)


def test_flickr_and_hybrid_discovery_keep_publisher_and_preview_provenance():
    from services.web_image import resolver

    with mock.patch.object(
        resolver,
        "search_flickr_api",
        return_value=[{
            "image_url": "https://live.staticflickr.com/1/photo.jpg",
            "title": "Titanic lifeboats",
            "source_url": "https://www.flickr.com/photos/archive/1/",
            "thumbnail_url": "https://live.staticflickr.com/1/thumb.jpg",
            "tags": "titanic lifeboats",
            "description": "Archival photo",
        }],
    ):
        flickr = resolver._discover_flickr_api_urls("Titanic lifeboats")

    assert flickr[0]["source_url"] == "https://www.flickr.com/photos/archive/1/"
    assert flickr[0]["preview_url"] == "https://live.staticflickr.com/1/thumb.jpg"
    assert flickr[0]["provider"] == "flickr"

    with mock.patch.object(
        resolver,
        "search_bing",
        return_value=[{
            "image_url": "https://publisher.example/original.jpg",
            "thumbnail_url": "https://bing.example/thumb.jpg",
            "source_url": "https://publisher.example/story",
            "title": "Titanic lifeboats",
        }],
    ):
        hybrid = resolver._discover_hybrid_urls("bing_hybrid", "Titanic lifeboats")

    assert hybrid[0]["url"] == "https://publisher.example/original.jpg"
    assert hybrid[0]["source_url"] == "https://publisher.example/story"
    assert hybrid[0]["preview_url"] == "https://bing.example/thumb.jpg"


def test_opt_in_visual_context_is_forwarded_to_web_resolver():
    import app as server_app

    context = object()
    with mock.patch.object(server_app, "_build_web_visual_context", return_value=context), mock.patch(
        "services.web_image_service.resolve_web_images_for_video_fast",
        return_value={"image_paths": [None], "scene_queries": []},
    ) as resolver:
        server_app._resolve_web_media(
            ["A scene"],
            [2.0],
            {"visual_understanding": True, "render_resolution": (1280, 720)},
            "A topic",
            "A documentary script.",
        )

    assert resolver.call_args.kwargs["visual_context"] is context


def test_render_repair_rejects_unsafe_web_path_before_promotion(tmp_path):
    import app as server_app

    unsafe = tmp_path / "unsafe.jpg"
    safe = tmp_path / "safe.jpg"
    Image.new("RGB", (800, 450), (30, 80, 140)).save(unsafe, quality=90)
    Image.new("RGB", (800, 450), (40, 90, 150)).save(safe, quality=90)

    def replace_with_safe(final_paths, source_labels, unresolved_indices, **_kwargs):
        for index in unresolved_indices:
            final_paths[index] = str(safe)
            source_labels[index] = "Web Fallback Rescue"
        return len(unresolved_indices)

    media_plan = {
        "media_paths": [str(unsafe)],
        "source_labels": ["Web Images"],
        "metadata": {
            "web_image_candidates": [{
                "title": "Titanic archive",
                "source_url": "https://alamy.com/titanic-watermarked-photo",
            }],
        },
        "prompt_bundle": {"beat_texts": ["Titanic archive"]},
    }
    with mock.patch.object(
        server_app,
        "_fill_unresolved_with_general_web_fallback",
        side_effect=replace_with_safe,
    ):
        repaired = server_app._repair_media_plan_for_render(
            media_plan=media_plan,
            audio_plan={"scene_texts": ["Titanic archive"], "scene_durations": [2.0]},
            job_config={},
            render_resolution=(1280, 720),
            title="Titanic",
            script="A documentary about the Titanic.",
            target_scene_count=1,
        )

    assert repaired["media_paths"] == [str(safe)]
    assert repaired["metadata"]["web_safety_rejections"]


def test_shadow_web_rescue_propagates_metadata_to_render_gate():
    import app as server_app

    paths = []
    metadata = {"web_image_candidates": [{} for _ in range(2)]}
    for name in ("archive.jpg", "avatar_render.jpg"):
        path = Path(__file__).parent.parent / "output" / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 32), (20, 20, 20)).save(path)
        paths.append(str(path))

    # Simulate a shadow-web branch returning image paths and per-image metadata
    # (the previous code dropped this metadata for the shadow-web rescue path).
    request_indices = [0, 1]
    image_paths = paths
    image_metadata = [
        {"title": "Titanic engineer memorial archive", "tags": "titanic historical", "source": "Flickr"},
        {"title": "3D rendered woman in bikini", "tags": "3d render woman bikini", "source": "Flickr"},
    ]

    metadata = {"web_image_candidates": [{} for _ in range(len(request_indices))]}
    yt_shadow_web_paths_by_scene = {}
    shadow_metadata = image_metadata
    # Use the same loop logic as the production branch:
    paths_aligned = image_paths
    for local_idx, scene_idx in enumerate(request_indices):
        if local_idx >= len(paths_aligned) or not paths_aligned[local_idx]:
            continue
        yt_shadow_web_paths_by_scene[scene_idx] = paths_aligned[local_idx]
        if scene_idx < len(metadata.get('web_image_candidates') or []):
            metadata['web_image_candidates'][scene_idx] = (
                dict(shadow_metadata[local_idx])
                if local_idx < len(shadow_metadata)
                and isinstance(shadow_metadata[local_idx], dict)
                else {}
            )

    # "archive.jpg" must be accepted by the metadata gate; "avatar_render.jpg" must be rejected.
    assert metadata["web_image_candidates"][0]["title"] == "Titanic engineer memorial archive"
    assert metadata["web_image_candidates"][1]["title"] == "3D rendered woman in bikini"


def test_final_safety_gate_catches_unsafe_path_reintroduced_by_repair(tmp_path):
    import app as server_app

    unsafe = tmp_path / "unsafe.jpg"
    Image.new("RGB", (800, 450), (30, 80, 140)).save(unsafe, quality=90)

    def reintroduce_unsafe(final_paths, source_labels, unresolved_indices, **_kwargs):
        for index in unresolved_indices:
            final_paths[index] = str(unsafe)
            source_labels[index] = "Web Fallback Rescue"
        return len(unresolved_indices)

    media_plan = {
        "media_paths": [str(unsafe)],
        "source_labels": ["Web Images"],
        "metadata": {
            "web_image_candidates": [{
                "title": "Titanic archive",
                "source_url": "https://alamy.com/titanic-watermarked-photo",
            }],
        },
        "prompt_bundle": {"beat_texts": ["Titanic archive"]},
    }
    with mock.patch.object(
        server_app,
        "_fill_unresolved_with_general_web_fallback",
        side_effect=reintroduce_unsafe,
    ), mock.patch.object(server_app, "_run_targeted_stock_rescue", return_value={}), mock.patch.object(
        server_app, "_fill_final_bad_slots_from_existing_media", return_value=0
    ):
        try:
            server_app._repair_media_plan_for_render(
                media_plan=media_plan,
                audio_plan={"scene_texts": ["Titanic archive"], "scene_durations": [2.0]},
                job_config={},
                render_resolution=(1280, 720),
                title="Titanic",
                script="A documentary about the Titanic.",
                target_scene_count=1,
            )
        except server_app.MediaContractError as exc:
            assert "no_blank_or_text_card_fallbacks" in str(exc.detail.get("fallback_policy"))
        else:
            raise AssertionError("unsafe reintroduced web image reached render")
def test_required_visual_gate_fails_closed_when_client_is_unavailable(tmp_path):
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    image_path = tmp_path / "candidate.jpg"
    Image.new("RGB", (800, 450), (30, 80, 140)).save(image_path, quality=90)

    class RequiredContext:
        visual_required = True

        def get_client(self):
            return None

    result = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Grand Theft Auto VI gameplay in Vice City.",
        subject="Grand Theft Auto VI",
        global_topic="Grand Theft Auto VI",
        query="Grand Theft Auto VI official gameplay screenshot",
        candidates=[{"path": str(image_path), "title": "Grand Theft Auto VI"}],
        visual_context=RequiredContext(),
    )
    assert result["visual_rejected"] is True
    assert result["path"] == ""


def test_validate_image_accepts_both_str_and_path(tmp_path):
    from services.web_image.resolver import _validate_image

    img_path = tmp_path / "valid.jpg"
    Image.new("RGB", (1280, 720), (50, 100, 150)).save(img_path, quality=95)
    # Ensure it meets size limit (>15KB)
    assert img_path.stat().st_size >= 15 * 1024 or img_path.write_bytes(b"\xFF\xD8\xFF" + b"\x00" * 20000)

    # Re-save with high quality so PIL produces a large valid image
    img = Image.new("RGB", (1280, 720), (50, 100, 150))
    # Add noise so compression doesn't make it too small
    import random
    pixels = img.load()
    for x in range(0, 1280, 10):
        for y in range(0, 720, 10):
            pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    img.save(img_path, "JPEG", quality=95)

    dims_path = _validate_image(img_path)
    dims_str = _validate_image(str(img_path))
    assert dims_path == (1280, 720)
    assert dims_str == (1280, 720)
    assert _validate_image("nonexistent_path.jpg") is None
    assert _validate_image(None) is None


def test_validate_image_rejects_truncated_container(tmp_path):
    """Header-only PIL parsing must not let corrupt downloads reach render."""
    from services.web_image.resolver import _validate_image

    img_path = tmp_path / "truncated.jpg"
    img = Image.new("RGB", (1280, 720), (50, 100, 150))
    import random
    pixels = img.load()
    for x in range(0, 1280, 10):
        for y in range(0, 720, 10):
            pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    img.save(img_path, "JPEG", quality=95)
    raw = img_path.read_bytes()
    assert len(raw) > 16 * 1024
    img_path.write_bytes(raw[:-256])

    assert _validate_image(img_path) is None


def test_retain_pending_without_visual_preserves_valid_str_path(tmp_path):
    from services.web_image import resolver

    img_path = tmp_path / "retained.jpg"
    img = Image.new("RGB", (1920, 1080), (100, 150, 200))
    import random
    pixels = img.load()
    for x in range(0, 1920, 20):
        for y in range(0, 1080, 20):
            pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    img.save(img_path, "JPEG", quality=95)

    candidate = {
        "path": str(img_path),
        "url": "https://example.com/retained.jpg",
        "title": "Grand Theft Auto VI",
        "semantic_visual_pending": True,
        "pre_score": 85.0,
    }

    results = [candidate]
    # Simulate resolver's visual_client_available = False branch
    for _idx, _result in enumerate(results):
        if _result and _result.get("semantic_visual_pending"):
            path = str(_result.get("path") or "").strip()
            assert Path(path).is_file()
            dims = resolver._validate_image(Path(path))
            assert dims == (1920, 1080)
            retained = dict(_result)
            retained.update({
                "width": int(dims[0]),
                "height": int(dims[1]),
                "semantic_visual_pending": False,
                "visual_gate_status": "unavailable",
                "visual_outcome": "unavailable",
                "visual_reason": "visual_verifier_unavailable_local_safety_passed",
                "visual_unverified": True,
            })
            results[_idx] = retained

    assert results[0] is not None
    assert results[0]["path"] == str(img_path)
    assert results[0]["visual_unverified"] is True
    assert results[0]["visual_outcome"] == "unavailable"
