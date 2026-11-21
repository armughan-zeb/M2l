"""Unit tests for the advance-prep system (services/advance_prep.py,
services/media_prescale.py, and the web-resolver prep-cache wiring).

No network, no live server, no render: everything runs against tmp dirs
via env overrides (VIDZEO_PREP_ROOT / VIDZEO_TEMP_DIR / VIDZEO_*_JSON).
"""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from services import advance_prep as prep
from services.media_prescale import ensure_scaled, scaled_path_for


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def prep_env(tmp_path, monkeypatch):
    """Isolate every prep root under tmp_path."""
    root = tmp_path / "prep-root"
    temp = tmp_path / "temp"
    queue = tmp_path / "queue.json"
    pool = tmp_path / "pool-status.json"
    active = tmp_path / "active-job.json"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(root))
    monkeypatch.setenv("VIDZEO_TEMP_DIR", str(temp))
    monkeypatch.setenv("VIDZEO_QUEUE_JSON", str(queue))
    monkeypatch.setenv("VIDZEO_POOL_STATUS_JSON", str(pool))
    monkeypatch.setenv("VIDZEO_ACTIVE_JOB_JSON", str(active))
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(tmp_path / "yt-cuts"))
    return {
        "root": root, "temp": temp, "queue": queue,
        "pool": pool, "active": active, "tmp": tmp_path,
    }


def _write_idle_signals(env, *, queue_items=(), pending=0):
    env["queue"].write_text(json.dumps({"items": list(queue_items)}), encoding="utf-8")
    env["pool"].write_text(
        json.dumps({"updatedAt": int(time.time() * 1000), "workers": 2, "pending": pending}),
        encoding="utf-8",
    )
    if env["active"].exists():
        env["active"].unlink()


# ---------------------------------------------------------------------------
# idle gate
# ---------------------------------------------------------------------------

def test_idle_gate_passes_when_everything_idle(prep_env):
    _write_idle_signals(prep_env)
    idle, reasons = prep.is_idle(check_processes=False)
    assert idle is True
    assert reasons == []


def test_idle_gate_blocks_on_busy_queue(prep_env):
    _write_idle_signals(prep_env, queue_items=[
        {"id": "abc123", "status": "processing", "progress": 24},
        {"id": "old", "status": "completed"},
    ])
    idle, reasons = prep.is_idle(check_processes=False)
    assert idle is False
    assert any("abc123" in reason for reason in reasons)


@pytest.mark.parametrize("status", ["queued", "processing", "cancellation_requested", "paused", "shutdown_timeout"])
def test_idle_gate_blocks_on_each_busy_status(prep_env, status):
    _write_idle_signals(prep_env, queue_items=[{"id": "j1", "status": status}])
    idle, _reasons = prep.is_idle(check_processes=False)
    assert idle is False


@pytest.mark.parametrize("status", ["completed", "cancelled", "error"])
def test_idle_gate_passes_on_terminal_statuses(prep_env, status):
    _write_idle_signals(prep_env, queue_items=[{"id": "j1", "status": status}])
    idle, _reasons = prep.is_idle(check_processes=False)
    assert idle is True


def test_idle_gate_blocks_on_pool_pending(prep_env):
    _write_idle_signals(prep_env, pending=3)
    idle, reasons = prep.is_idle(check_processes=False)
    assert idle is False
    assert any("pending=3" in reason for reason in reasons)


def test_idle_gate_blocks_on_active_daemon_job(prep_env):
    _write_idle_signals(prep_env)
    prep_env["active"].write_text(json.dumps({"jobId": "r_xyz", "payload": {}}), encoding="utf-8")
    idle, reasons = prep.is_idle(check_processes=False)
    assert idle is False
    assert any("r_xyz" in reason for reason in reasons)


def test_idle_gate_blocks_on_render_process(prep_env):
    _write_idle_signals(prep_env)
    fake = ["/home/hatch/workspace/M2l/vidnets/.venv/bin/python render-vidnets-linux.js --job x"]
    idle, reasons = prep.is_idle(process_list=fake)
    assert idle is False
    assert any("render" in reason for reason in reasons)


def test_idle_gate_fails_closed_on_missing_queue(prep_env):
    prep_env["pool"].write_text(
        json.dumps({"updatedAt": int(time.time() * 1000), "pending": 0}), encoding="utf-8"
    )
    idle, reasons = prep.is_idle(check_processes=False)
    assert idle is False
    assert any("queue file missing" in reason for reason in reasons)


def test_idle_gate_fails_closed_on_stale_pool_status(prep_env):
    _write_idle_signals(prep_env)
    prep_env["pool"].write_text(
        json.dumps({"updatedAt": int((time.time() - 3600) * 1000), "pending": 0}),
        encoding="utf-8",
    )
    idle, reasons = prep.is_idle(check_processes=False)
    assert idle is False
    assert any("stale" in reason for reason in reasons)


# ---------------------------------------------------------------------------
# keys + normalization
# ---------------------------------------------------------------------------

def test_prep_cache_key_is_deterministic_and_title_insensitive():
    key_a = prep.prep_cache_key("  Flooded Desert ", "The crew checks the brakes.", "train cab photo")
    key_b = prep.prep_cache_key("flooded desert", "The crew checks the brakes.", "Train Cab Photo")
    key_c = prep.prep_cache_key("flooded desert", "The crew checks the brakes.", "train cab photo ")
    assert key_a == key_b == key_c
    assert len(key_a) == 64


def test_prep_cache_key_changes_with_narration_or_query():
    base = prep.prep_cache_key("T", "narration one", "query one")
    assert base != prep.prep_cache_key("T", "narration two", "query one")
    assert base != prep.prep_cache_key("T", "narration one", "query two")
    assert base != prep.prep_cache_key("Other", "narration one", "query one")


def test_title_key_matches_across_duration_forms():
    assert prep.title_key("Some Title", 10) == prep.title_key("some title", 10.0)


# ---------------------------------------------------------------------------
# script store
# ---------------------------------------------------------------------------

def test_script_store_roundtrip_and_miss(prep_env):
    assert prep.find_prepped_script("No Such Title", 10) is None
    path = prep.save_prepped_script("Flooded Desert", 10, "word " * 1500, {"engine": "scriptit"})
    assert path and Path(path).is_file()
    found = prep.find_prepped_script("flooded desert", 10)
    assert found is not None
    assert found["script_text"].startswith("word word")
    assert found["path"] == path
    # A different duration is a different entry (exact lookup).
    assert prep.find_prepped_script("Flooded Desert", 8) is None
    assert prep.find_prepped_script("Other Title", 10) is None


def test_script_store_rejects_wrong_title_in_meta(prep_env):
    directory = prep.script_store_dir("Real Title", 10)
    directory.mkdir(parents=True)
    (directory / "script.txt").write_text("hello world\n", encoding="utf-8")
    (directory / "meta.json").write_text(
        json.dumps({"title": "Different Title", "duration_minutes": 10.0}), encoding="utf-8"
    )
    assert prep.find_prepped_script("Real Title", 10) is None


# ---------------------------------------------------------------------------
# scene split + query derivation (SAME functions as the live job)
# ---------------------------------------------------------------------------

SCRIPT = (
    "The train leaves Denver at dawn. The crew checks the brakes before the climb. "
    "Snow begins to fall over the pass. The dispatcher reroutes the freight around the storm. "
    "By midnight the blizzard buries the tracks. The crew waits for the plow to arrive."
)


def test_derive_scene_texts_is_deterministic_on_script():
    first = prep.derive_scene_texts(SCRIPT, 600.0)
    second = prep.derive_scene_texts(SCRIPT, 600.0)
    assert first == second
    assert len(first) >= 3
    assert " ".join(first).replace("  ", " ").strip() != ""


def test_derive_query_bundle_uses_live_derivation():
    scene_texts = prep.derive_scene_texts(SCRIPT, 600.0)
    bundle = prep.derive_query_bundle("Flooded Desert or Mountain Blizzard", scene_texts)
    assert set(bundle) == {"web", "yt", "stock"}
    assert len(bundle["web"]) == len(scene_texts)
    assert len(bundle["yt"]) == len(scene_texts)
    # Deterministic: same inputs, same queries.
    again = prep.derive_query_bundle("Flooded Desert or Mountain Blizzard", scene_texts)
    assert bundle == again
    # Every row carries a usable primary query.
    for row in bundle["web"]:
        assert str(row.get("search_query") or "").strip()
    for row in bundle["yt"]:
        assert str(row.get("search_query") or "").strip()


def test_prep_key_equality_for_same_derivation():
    """Cache-key equality: a key computed from prep-derived queries equals
    the key computed from the same derivation run again (the live job
    re-derives with the same function, so the same triple gives the same
    key on both sides)."""
    scene_texts = prep.derive_scene_texts(SCRIPT, 600.0)
    bundle = prep.derive_query_bundle("Flooded Desert", scene_texts)
    query = str(bundle["web"][0]["search_query"])
    key_prep = prep.prep_cache_key("Flooded Desert", scene_texts[0], query)
    bundle_live = prep.derive_query_bundle("Flooded Desert", scene_texts)
    query_live = str(bundle_live["web"][0]["search_query"])
    key_live = prep.prep_cache_key("Flooded Desert", scene_texts[0], query_live)
    assert query == query_live
    assert key_prep == key_live


def test_record_keeps_all_variant_keys(prep_env, monkeypatch):
    """One entry per query variant must stay findable: the historical asset
    cache dedupes rows by source URL, so recording three variants of one
    asset used to keep only the LAST variant's key (Worker V proof:
    24 records -> 8 rows, each holding only the last variant)."""
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", prep_env["temp"])
    source = prep_env["tmp"] / "cab.jpg"
    Image.effect_noise((800, 450), 48).convert("RGB").save(source, quality=92)
    asset = {
        "path": str(source),
        "source_url": "https://example.invalid/cab.jpg",
        "title": "Flooded Desert",
        "active_subject": "Train Crew",
        "subjects": ["Train Crew"],
    }
    narration = "The crew checks the brakes."
    variants = ["train cab photo", "train cab documentary image", "train cab"]
    for query in variants:
        assert prep.record_prepped_web_asset("Flooded Desert", narration, query, asset) is True
    rows = resolver._load_historical_web_assets()
    assert len(rows) == 1
    stored_keys = {str(rows[0].get("prep_key") or "")}
    stored_keys |= {str(key) for key in (rows[0].get("prep_keys") or [])}
    expected = {prep.prep_cache_key("Flooded Desert", narration, q) for q in variants}
    assert expected <= stored_keys
    # Every variant finds the asset, not just the last one recorded.
    for query in variants:
        assert prep.find_prepped_web_asset("Flooded Desert", narration, [query]) is not None


def test_resolver_prep_hit_at_production_deadline(prep_env, monkeypatch):
    """Production-deadline hit proof: prep primes with its own bundle
    variants (exactly like _prep_web_media), the live job resolves with the
    live bundle rows at stage_deadline_seconds=60. The repair pass rewrites
    most bundle queries at that deadline, so the lookup must also try the
    raw planner variants — before that fix this hit only 3/8 scenes
    (Worker V proof); the 5s verbatim branch hid it in the old test."""
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", prep_env["temp"])
    scene_texts = prep.derive_scene_texts(SCRIPT, 600.0)
    bundle = prep.derive_query_bundle("Flooded Desert", scene_texts)
    source = prep_env["tmp"] / "cab.jpg"
    Image.effect_noise((800, 450), 48).convert("RGB").save(source, quality=92)
    for idx, scene_text in enumerate(scene_texts):
        row = bundle["web"][idx]
        queries = list(row.get("query_variants") or [])
        if not queries and row.get("search_query"):
            queries = [str(row["search_query"])]
        asset = {
            "path": str(source),
            "source_url": str(row.get("search_query") or str(source)),
            "title": "Flooded Desert",
            "active_subject": str(row.get("scene_subject") or row.get("subject_query") or ""),
            "subjects": [str(row.get("scene_subject") or "")] if row.get("scene_subject") else [],
        }
        for query in queries:
            assert prep.record_prepped_web_asset("Flooded Desert", scene_text, str(query), asset) is True
    source.unlink()

    def _boom_worker(*args, **kwargs):
        raise AssertionError("scene worker ran despite a prep cache hit")

    monkeypatch.setattr(resolver, "_resolve_scene", _boom_worker)
    monkeypatch.setattr(
        resolver, "_warm_shared_discovery_pool",
        lambda **kwargs: {"query": "", "candidates": 0, "providers_completed": 0, "providers_pending": 0},
    )
    result = resolver.resolve_web_images_for_video_fast(
        script=SCRIPT, title="Flooded Desert", scene_texts=scene_texts,
        scene_durations=[10.0] * len(scene_texts),
        scene_queries_override=[dict(row) for row in bundle["web"]],
        stage_deadline_seconds=60.0,
    )
    assert len(result["image_paths"]) == len(scene_texts)
    assert all(path and Path(path).is_file() for path in result["image_paths"])


def test_prep_bundle_matches_live_bundle():
    """Prep must derive the same queries as the live job's mechanical path
    (app._generate_mechanical_shared_scene_bundle), not just the same
    queries as itself run twice (what the old test proved)."""
    import app as live_app

    scene_texts = prep.derive_scene_texts(SCRIPT, 600.0)
    bundle_prep = prep.derive_query_bundle("Flooded Desert", scene_texts)
    bundle_live = live_app._generate_mechanical_shared_scene_bundle(SCRIPT, "Flooded Desert", scene_texts)
    assert bundle_prep == bundle_live


def test_named_pair_grounding_misses_fail_soft():
    """Known limit (documented, not fixed): the live job rewrites queries
    for explicit named-pair scenes (\"Denver vs Chicago\") via
    _ground_exact_matchup_queries, which prep does not apply.  The prepped
    entry then MISSES on the grounded query — it must never wrong-hit."""
    import copy

    import app as live_app

    pair_scenes = [
        "Denver vs Chicago: the two terminals race to clear the storm first. Crews work through the night.",
        "Engineers clear the drift by hand while the storm keeps coming down.",
    ]
    bundle_prep = prep.derive_query_bundle("Flooded Desert", pair_scenes)
    bundle_live = live_app._generate_mechanical_shared_scene_bundle(SCRIPT, "Flooded Desert", pair_scenes)
    grounded = copy.deepcopy(bundle_live)
    live_app._ground_exact_matchup_queries(grounded, pair_scenes, "Flooded Desert")
    assert bundle_prep != grounded
    prep_query = str(bundle_prep["web"][0]["search_query"])
    grounded_query = str(grounded["web"][0]["search_query"])
    assert prep_query != grounded_query
    assert prep.prep_cache_key("Flooded Desert", pair_scenes[0], prep_query) != prep.prep_cache_key(
        "Flooded Desert", pair_scenes[0], grounded_query
    )


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def test_manifest_write_read_roundtrip(prep_env):
    manifest = prep.new_manifest("Flooded Desert", 10, "Train Crew")
    manifest["script"] = {"path": "/tmp/x/script.txt", "words": 1500}
    assert prep.save_manifest("Flooded Desert", 10, manifest) is True
    loaded = prep.load_manifest("Flooded Desert", 10)
    assert loaded["title"] == "Flooded Desert"
    assert loaded["script"]["words"] == 1500
    assert loaded["schema"] == "vidzeo_prep_manifest_v1"


def test_manifest_missing_returns_empty(prep_env):
    assert prep.load_manifest("Nothing Here", 5) == {}


def test_note_reuse_appends_to_manifest(prep_env):
    prep.save_manifest("Flooded Desert", 10, prep.new_manifest("Flooded Desert", 10, ""))
    prep.note_reuse_by_title("flooded desert", "web_cache_hit", {"scene": 2, "query": "train cab photo"})
    loaded = prep.load_manifest("Flooded Desert", 10)
    assert len(loaded["reuse_log"]) == 1
    assert loaded["reuse_log"][0]["kind"] == "web_cache_hit"
    assert loaded["reuse_log"][0]["scene"] == 2


def test_note_reuse_never_raises_without_manifest(prep_env):
    prep.note_reuse_by_title("Ghost Title", "web_cache_hit", {})
    prep.note_reuse("Ghost Title", 10, "script_hit", {})


# ---------------------------------------------------------------------------
# web asset cache (prep_key roundtrip through the live resolver's cache)
# ---------------------------------------------------------------------------

def test_prepped_web_asset_roundtrip(prep_env, monkeypatch):
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", prep_env["temp"])
    source = prep_env["tmp"] / "cab.jpg"
    # Noisy image: a solid colour is <15 KB and fails the resolver's
    # _validate_image floor, so the hit would (correctly) fail closed.
    Image.effect_noise((800, 450), 48).convert("RGB").save(source, quality=92)
    asset = {
        "path": str(source),
        "source_url": "https://example.invalid/cab.jpg",
        "title": "Flooded Desert",
        "active_subject": "Train Crew",
        "subjects": ["Train Crew"],
    }
    assert prep.record_prepped_web_asset(
        "Flooded Desert", "The crew checks the brakes.", "train cab photo", asset
    ) is True
    # The source file is gone by live time; the cache keeps its own copy.
    source.unlink()
    found = prep.find_prepped_web_asset(
        "Flooded Desert", "The crew checks the brakes.", ["train cab photo"]
    )
    assert found is not None
    assert Path(found["path"]).is_file()
    assert found["prep_key"] == prep.prep_cache_key(
        "Flooded Desert", "The crew checks the brakes.", "train cab photo"
    )
    # Wrong narration hash => miss (graceful live fallback, not a wrong hit).
    assert prep.find_prepped_web_asset(
        "Flooded Desert", "Different narration.", ["train cab photo"]
    ) is None
    # A repaired variant of the same query still hits (live resolver tries
    # each of its queries in order).
    assert prep.find_prepped_web_asset(
        "Flooded Desert", "The crew checks the brakes.",
        ["unrelated query", "train cab photo"],
    ) is not None


# ---------------------------------------------------------------------------
# resolver hit path: a prepped entry is found and the scene skips search
# ---------------------------------------------------------------------------

def test_resolver_prep_hit_skips_scene_worker(prep_env, monkeypatch):
    """End-to-end hit proof through the public resolver entry point, with
    no network: the scene worker is replaced by a stub that would fail the
    test if called, and the shared-pool warmup is disabled by the short
    stage deadline."""
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", prep_env["temp"])
    scene_text = "The crew checks the brakes before the climb."
    script = scene_text + " " + "The train rolls on through the snow. " * 4
    source = prep_env["tmp"] / "cab.jpg"
    # Noisy image: a solid colour is <15 KB and fails the resolver's
    # _validate_image floor, so the hit would (correctly) fail closed.
    Image.effect_noise((800, 450), 48).convert("RGB").save(source, quality=92)
    # Prime the cache for every query the resolver could derive for this
    # scene: run the resolver once with a stubbed worker that records the
    # queries it was given, then prime, then run for real.
    seen_queries = []

    def _stub_worker(idx, text, queries, *args, **kwargs):
        seen_queries.append(list(queries))
        return {"path": "", "width": 0, "height": 0, "url": "", "domain": "stub"}

    monkeypatch.setattr(resolver, "_resolve_scene", _stub_worker)
    probe = resolver.resolve_web_images_for_video_fast(
        script=script, title="Flooded Desert", scene_texts=[scene_text],
        scene_durations=[10.0], stage_deadline_seconds=5.0,
    )
    assert probe["image_paths"] == [""]
    assert seen_queries and seen_queries[0]
    # Prime the prep cache under the queries the live resolver derived.
    asset = {
        "path": str(source),
        "source_url": "https://example.invalid/cab.jpg",
        "title": "Flooded Desert",
        "active_subject": "Train Crew",
        "subjects": ["Train Crew"],
    }
    for query in seen_queries[0]:
        assert prep.record_prepped_web_asset("Flooded Desert", scene_text, query, asset) is True
    source.unlink()

    # Now the real worker must never run for this scene.
    def _boom_worker(*args, **kwargs):
        raise AssertionError("scene worker ran despite a prep cache hit")

    monkeypatch.setattr(resolver, "_resolve_scene", _boom_worker)
    result = resolver.resolve_web_images_for_video_fast(
        script=script, title="Flooded Desert", scene_texts=[scene_text],
        scene_durations=[10.0], stage_deadline_seconds=5.0,
    )
    assert len(result["image_paths"]) == 1
    hit_path = result["image_paths"][0]
    assert hit_path and Path(hit_path).is_file()
    assert "scene_1" in hit_path
    assert result.get("scene_queries")


# ---------------------------------------------------------------------------
# youtube verified-cut cache (live cache, prep-shaped writes)
# ---------------------------------------------------------------------------

def test_youtube_cut_cache_roundtrip(prep_env):
    cut = prep_env["tmp"] / "cut.mp4"
    cut.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 9000)
    stored = prep.record_verified_cut("dQw4w9WgXcQ", 12.5, 4.0, str(cut), {"prep_title": "x"})
    assert stored and Path(stored).is_file()
    found = prep.find_verified_cut("dQw4w9WgXcQ", 12.5, 4.0)
    assert found is not None
    assert found["path"] == stored
    assert prep.find_verified_cut("dQw4w9WgXcQ", 99.0, 4.0) is None


# ---------------------------------------------------------------------------
# prescale
# ---------------------------------------------------------------------------

def test_scaled_path_naming():
    assert str(scaled_path_for("/a/b/clip.mp4")).endswith("clip.scaled-1080p.mp4")
    assert str(scaled_path_for("/a/b/photo.jpg")).endswith("photo.scaled-1080p.jpg")
    assert scaled_path_for("/a/b/clip.mp4").parent == Path("/a/b")


def test_ensure_scaled_downscales_only_when_larger(prep_env):
    big = prep_env["tmp"] / "big.jpg"
    Image.new("RGB", (2560, 1440), color=(10, 20, 30)).save(big, quality=90)
    scaled = ensure_scaled(big)
    assert scaled is not None
    assert Path(scaled).is_file()
    assert scaled.endswith(".scaled-1080p.jpg")
    with Image.open(scaled) as image:
        assert image.size[0] <= 1920 and image.size[1] <= 1080
        assert image.size[0] == 1920
    # Second call reuses the existing scaled file.
    assert ensure_scaled(big) == scaled

    small = prep_env["tmp"] / "small.jpg"
    Image.new("RGB", (1280, 720), color=(10, 20, 30)).save(small, quality=90)
    assert ensure_scaled(small) is None  # never upscale, never touch
    assert not Path(str(small).replace(".jpg", ".scaled-1080p.jpg")).exists()


def test_ensure_scaled_returns_none_on_missing_or_bad_file(prep_env):
    assert ensure_scaled(prep_env["tmp"] / "nope.jpg") is None
    bad = prep_env["tmp"] / "bad.jpg"
    bad.write_bytes(b"not an image at all")
    assert ensure_scaled(bad) is None


# ---------------------------------------------------------------------------
# prep queue loading
# ---------------------------------------------------------------------------

def test_load_prep_queue(prep_env):
    queue_file = prep_env["tmp"] / "prep-queue.json"
    queue_file.write_text(json.dumps({"titles": [
        {"title": "  Flooded Desert ", "duration": 10, "channel": "Train Crew"},
        {"title": "", "duration": 5},
        {"title": "Second", "duration_minutes": 2},
    ]}), encoding="utf-8")
    entries = prep.load_prep_queue(queue_file)
    assert [entry["title"] for entry in entries] == ["Flooded Desert", "Second"]
    assert entries[0]["duration_minutes"] == 10.0
    assert entries[0]["channel"] == "Train Crew"
    assert entries[1]["duration_minutes"] == 2.0
    assert prep.load_prep_queue(prep_env["tmp"] / "missing.json") == []
