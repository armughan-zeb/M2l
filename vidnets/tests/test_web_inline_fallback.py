"""Web lane inline fallback (run-6 fix, job 453e5590).

Offline tests: no network, no live server, no render.
- The recovery stage wall must fit the real phase budget (shared batch +
  prefetch + scene waves at the actual worker count) instead of dying at
  the flat 90s recovery floor with 0/7 resolved.
- The shared-pool prefetch must be wall-clock bounded (run 6: ~33s for
  9 rows, 1/9 local, before any scene worker started).
- Prep-banked web images must be findable when the live job retitles /
  re-segments the scenes (fuzzy fallback in find_prepped_web_asset).
- A scene whose perfect first pass fails must resolve inline, in the go,
  from an already-fetched related row, then from a cached/prep row, with
  the resolving layer recorded in the audit.
"""

import json
import random
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from services import advance_prep as prep


@pytest.fixture()
def prep_env(tmp_path, monkeypatch):
    """Isolate every prep/temp root under tmp_path (same shape as
    test_advance_prep.prep_env)."""
    root = tmp_path / "prep-root"
    temp = tmp_path / "temp"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(root))
    monkeypatch.setenv("VIDZEO_TEMP_DIR", str(temp))
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(tmp_path / "yt-cuts"))
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", temp)
    return {"root": root, "temp": temp, "tmp": tmp_path}


def _make_image(path, seed=0):
    """A distinct decodable image (distinct bytes/phash per seed)."""
    random.seed(seed)
    Image.effect_noise((800, 450), 48).convert("RGB").save(path, quality=92)
    return path


# ---------------------------------------------------------------------------
# Deadline fix: the recovery wall must fit the real phase budget
# ---------------------------------------------------------------------------

def test_recovery_floor_fits_real_phase_budget():
    from services.web_image.resolver import _web_recovery_useful_floor_seconds

    # Run-6 numbers: 7 scenes, 2 workers -> 45 (batch) + 10 (prefetch)
    # + ceil(7/2) * 30 (scene waves) = 175s.  The 90s wall that killed
    # run 6 does not fit the phase budget.
    assert _web_recovery_useful_floor_seconds(7, 2) == 175.0
    assert _web_recovery_useful_floor_seconds(13, 2) == 265.0
    # Small jobs keep the 90s floor; a fully parallel fan-out needs no more.
    assert _web_recovery_useful_floor_seconds(2, 2) == 90.0
    assert _web_recovery_useful_floor_seconds(7, 7) == 90.0
    assert _web_recovery_useful_floor_seconds(24, 2) == 415.0


def test_shared_prefetch_seconds_bounded():
    from services.web_image.resolver import _web_shared_prefetch_seconds

    assert _web_shared_prefetch_seconds(90.0) == 10.0
    assert _web_shared_prefetch_seconds(600.0) == 10.0
    assert _web_shared_prefetch_seconds(30.0) == 4.5
    assert _web_shared_prefetch_seconds(10.0) == 3.0


def test_transport_visual_context_deadline_is_the_75s_trap():
    """Document the run-6 vision trap: the transport visual context is
    built with visual_understanding=False / pipeline_v2_mode="off", so
    from_config gives it the 75s non-V2 default deadline from adapter
    build time — long expired by the time the web branch runs, which is
    why the run-6 audit reports visual_deadline_exceeded with 0 calls.
    Transport vision is disabled by design (the coordinator owns the
    final gate); the deadline on a disabled context is meaningless, and
    the resolver must not treat it as a failed verification."""
    from services.visual_intelligence.context import VisualDecisionContext

    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": False,
            "visual_required": False,
            "pipeline_v2_mode": "off",
        },
        13,
    )
    assert context.enabled is False
    assert context.get_client() is None
    # 75s default, not inf: created at adapter build, expired ~7 min
    # before run 6's web branch even started.
    remaining = context.deadline_at - time.monotonic()
    assert 60.0 < remaining <= 75.0


# ---------------------------------------------------------------------------
# Prep-web fuzzy fallback: run-6 key mismatch
# ---------------------------------------------------------------------------

_FULL_TITLE = "Grand Theft Auto VI: Return to Vice City"
_SHORT_TITLE = "Grand Theft Auto VI"
_PREP_NARRATION = "After a thirteen-year wait Grand Theft Auto VI arrives today"
_PREP_QUERY = "Grand Theft Auto VI thirteen-year arrives official screenshot"
# The live 13-scene segmentation merges prep scenes 1-3 into one scene,
# and the live branch titles the job with the short title.
_LIVE_NARRATION = (
    "After a thirteen-year wait, Grand Theft Auto VI arrives today, "
    "November 19, 2026, on PlayStation 5 and Xbox Series XS, and it "
    "brings the series back to Vice City."
)
_LIVE_QUERIES = [
    "Grand Theft Auto VI documentary gameplay came city screenshot wallpaper",
    "Grand Theft Auto VI documentary came city gameplay official reference image",
    "Grand Theft Auto VI Liberty City photograph",
]


def _record_gta_prep_asset(prep_env, monkeypatch):
    source = prep_env["tmp"] / "gta6.jpg"
    _make_image(source, seed=7)
    asset = {
        "path": str(source),
        "source_url": "https://example.invalid/gta6-vice-city.jpg",
        "title": _FULL_TITLE,
        "active_subject": _SHORT_TITLE,
        "subjects": [_SHORT_TITLE],
    }
    assert prep.record_prepped_web_asset(
        _FULL_TITLE, _PREP_NARRATION, _PREP_QUERY, asset
    ) is True
    return source


def test_fuzzy_prep_hit_live_retitle_and_resegmentation(prep_env, monkeypatch):
    """Run-6 replay: exact keys miss (short title + merged narration),
    but the fuzzy fallback finds the banked verified image."""
    _record_gta_prep_asset(prep_env, monkeypatch)
    # Exact triple still hits without the fuzzy flag (no behaviour change).
    exact = prep.find_prepped_web_asset(
        _FULL_TITLE, _PREP_NARRATION, [_PREP_QUERY]
    )
    assert exact is not None
    assert not exact.get("prep_fuzzy_match")
    # Live inputs: short title, merged narration, repaired queries.
    hit = prep.find_prepped_web_asset(
        _SHORT_TITLE, _LIVE_NARRATION, _LIVE_QUERIES
    )
    assert hit is not None
    assert hit.get("prep_fuzzy_match") is True
    assert Path(str(hit.get("path") or "")).is_file()


def test_fuzzy_prep_miss_on_unrelated_title(prep_env, monkeypatch):
    _record_gta_prep_asset(prep_env, monkeypatch)
    assert prep.find_prepped_web_asset(
        "How the Panama Canal Changed World Trade",
        "The canal changed world trade",
        ["Panama Canal Changed World trade photograph"],
    ) is None


def test_fuzzy_prep_miss_on_title_only_overlap(prep_env, monkeypatch):
    """A bare title match (no discriminating narration/query tokens)
    must never wrong-hit another scene's image."""
    _record_gta_prep_asset(prep_env, monkeypatch)
    assert prep.find_prepped_web_asset(
        _SHORT_TITLE,
        "Grand Theft Auto VI is a game",
        ["Grand Theft Auto VI photo"],
    ) is None


def test_record_keeps_all_variant_queries(prep_env, monkeypatch):
    """The fuzzy pass scores on variant query texts, so recording must
    keep ALL of them — run 6 proved the last variant alone overwrites
    the discriminating query with a generic one on every row."""
    source = prep_env["tmp"] / "cab.jpg"
    _make_image(source, seed=21)
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
        assert prep.record_prepped_web_asset(
            "Flooded Desert", narration, query, asset
        ) is True
    from services.web_image import resolver

    rows = resolver._load_historical_web_assets()
    assert len(rows) == 1
    stored = {str(q) for q in (rows[0].get("prep_queries") or [])}
    assert set(variants) <= stored


def test_fuzzy_prep_hit_via_manifest_for_legacy_rows(prep_env, monkeypatch):
    """Legacy rows (recorded before prep_queries existed) carry only the
    generic last variant as prep_query, so the cache-row fuzzy pass
    scores 0 — the per-title manifest still holds each entry's
    discriminating query and scene narration, and must rescue the hit.
    Also covers the empty-queries early return: narration alone must
    still reach the fuzzy passes."""
    source = _record_gta_prep_asset(prep_env, monkeypatch)
    # Simulate the legacy row state: generic prep_query, no variant
    # queries, no narration text.
    from services.web_image import resolver

    cache_dir = prep_env["temp"] / "web_image_asset_cache"
    manifest_path = cache_dir / "assets.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    rows[0]["prep_query"] = "Grand Theft Auto VI official image"
    rows[0].pop("prep_queries", None)
    rows[0].pop("prep_narration", None)
    manifest_path.write_text(json.dumps(rows), encoding="utf-8")
    # The prep manifest still records the discriminating query + scene.
    manifest = prep.new_manifest(_FULL_TITLE, 1.0)
    manifest["scenes"] = [
        {"index": 1, "text": _PREP_NARRATION, "words": 10},
    ]
    manifest["web"] = {
        "attempted": 1,
        "verified": 1,
        "entries": [
            {
                "scene": 1,
                "path": str(source),
                "query": _PREP_QUERY,
                "prep_key": prep.prep_cache_key(
                    _FULL_TITLE, _PREP_NARRATION, _PREP_QUERY
                ),
            }
        ],
    }
    assert prep.save_manifest(_FULL_TITLE, 1.0, manifest) is True
    hit = prep.find_prepped_web_asset(
        _SHORT_TITLE, _LIVE_NARRATION, _LIVE_QUERIES
    )
    assert hit is not None
    assert hit.get("prep_fuzzy_match") is True
    assert hit.get("prep_manifest_hit") is True
    assert Path(str(hit.get("path") or "")).is_file()
    # Narration alone (no queries at all) must still reach the fuzzy pass.
    hit_no_queries = prep.find_prepped_web_asset(
        _SHORT_TITLE, _LIVE_NARRATION, []
    )
    assert hit_no_queries is not None


# ---------------------------------------------------------------------------
# Inline per-scene fallback chain (_resolve_scene, offline)
# ---------------------------------------------------------------------------

def _locks():
    return {
        "phash_lock": threading.Lock(),
        "md5_lock": threading.Lock(),
        "file_lock": threading.Lock(),
    }


def _resolve_offline(monkeypatch, tmp_path, *, scene_text, queries,
                     shared_rows, main_subject, global_topic,
                     download_cache=None, output_dir=None):
    """Call _resolve_scene with every provider wave stubbed to no rows and
    every first-pass download stubbed to fail, so the perfect first pass
    cannot resolve and the inline chain is what decides.  Pool rows carry
    NO local path — their files live only in ``download_cache``, the way
    a background prefetch leaves them — so only the chain can use them."""
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "_discover_source_query", lambda *a, **k: [])
    monkeypatch.setattr(resolver, "_download_candidate_batch",
                        lambda *a, **k: [])
    locks = _locks()
    out = Path(output_dir) if output_dir else tmp_path / "out"
    return resolver._resolve_scene(
        0, scene_text, list(queries), None, out, main_subject,
        {}, {}, set(), locks["phash_lock"], locks["md5_lock"],
        locks["file_lock"], shared_pool_candidates=list(shared_rows),
        global_topic=global_topic, download_cache=dict(download_cache or {}),
        deadline_at=None,
    )


def _shared_row(url, title, path, tags="", description=""):
    return {
        "url": url,
        "title": title,
        "tags": tags,
        "description": description,
        "source": "flickr_api",
        "provider": "flickr_api",
        "query": title,
        "path": str(path) if path else "",
        "source_url": url,
        "page_url": url,
        "preview_url": "",
    }


def test_inline_chain_general_related_resolves(prep_env, monkeypatch):
    """Perfect fails (no provider rows, no first-pass downloads) ->
    general related image resolves from the already-fetched pool."""
    img = _make_image(prep_env["tmp"] / "vice.jpg", seed=11)
    url = "https://example.invalid/gta6-vice-city.jpg"
    row = _shared_row(
        url,
        "Grand Theft Auto VI Vice City gameplay screenshot",
        None,
        tags="vice city leonida",
        description="Grand Theft Auto VI Vice City gameplay",
    )
    result = _resolve_offline(
        monkeypatch, prep_env["tmp"],
        scene_text="Jason and Lucia drive through Vice City in Leonida.",
        queries=["Grand Theft Auto VI Vice City Jason Lucia gameplay"],
        shared_rows=[row],
        main_subject=_SHORT_TITLE,
        global_topic=_SHORT_TITLE,
        download_cache={url: str(img)},
    )
    assert result is not None
    assert Path(str(result.get("path") or "")).is_file()
    assert result.get("web_fallback_layer") == "general_related"
    assert result.get("semantic_visual_pending") is True


def test_inline_chain_general_rejects_wrong_topic(prep_env, monkeypatch):
    """A pool row from another topic must not resolve the scene, even
    when it is local: the scene falls through (None) rather than
    rendering the wrong image."""
    img = _make_image(prep_env["tmp"] / "panama.jpg", seed=12)
    url = "https://example.invalid/panama-canal.jpg"
    row = _shared_row(
        url,
        "Panama Canal cargo ship crossing the locks",
        None,
        tags="panama canal locks",
        description="Panama Canal cargo ship",
    )
    result = _resolve_offline(
        monkeypatch, prep_env["tmp"],
        scene_text="Jason and Lucia drive through Vice City in Leonida.",
        queries=["Grand Theft Auto VI Vice City Jason Lucia gameplay"],
        shared_rows=[row],
        main_subject=_SHORT_TITLE,
        global_topic=_SHORT_TITLE,
        download_cache={url: str(img)},
    )
    assert result is None


def test_inline_chain_cached_prep_resolves(prep_env, monkeypatch):
    """Perfect fails and the pool is empty -> prep-banked image resolves
    via the fuzzy lookup (short title + merged narration)."""
    _record_gta_prep_asset(prep_env, monkeypatch)
    result = _resolve_offline(
        monkeypatch, prep_env["tmp"],
        scene_text=_LIVE_NARRATION,
        queries=_LIVE_QUERIES,
        shared_rows=[],
        main_subject=_SHORT_TITLE,
        global_topic=_SHORT_TITLE,
    )
    assert result is not None
    assert Path(str(result.get("path") or "")).is_file()
    assert result.get("web_fallback_layer") == "cached_prep"
    assert result.get("prep_cache_hit") is True


def test_inline_chain_cached_download_resolves(prep_env, monkeypatch):
    """No usable subject (unverified class) -> an already-downloaded
    on-topic pool row resolves as cached_download, not a graphic."""
    img = _make_image(prep_env["tmp"] / "topic.jpg", seed=13)
    url = "https://example.invalid/gta6-topic.jpg"
    row = _shared_row(
        url,
        "Grand Theft Auto VI Vice City",
        None,
    )
    result = _resolve_offline(
        monkeypatch, prep_env["tmp"],
        scene_text="Jason and Lucia drive through Vice City in Leonida.",
        queries=["Grand Theft Auto VI Vice City"],
        shared_rows=[row],
        main_subject="",
        global_topic=_SHORT_TITLE,
        download_cache={url: str(img)},
    )
    assert result is not None
    assert result.get("web_fallback_layer") == "cached_download"


# ---------------------------------------------------------------------------
# Offline replay: run-6 shape (7 scenes, 90s wall, 60-row batch)
# ---------------------------------------------------------------------------

_RUN6_SCENES = [
    "After a thirteen-year wait, Grand Theft Auto VI arrives today, November 19, 2026, on PlayStation 5 and Xbox Series XS, and it brings the series back to Vice City.",
    "The story starts with a score gone wrong.",
    "Jason Duval and Lucia Caminos, a criminal duo in the fictional state of Leonida, are pulled into a state-wide conspiracy after a failed bank heist.",
    "Jason served in the Army, then worked for drug runners in the Leonida Keys.",
    "Lucia came from Liberty City, fought for her family, and served time.",
    "Rockstar began preliminary work in 2014, after the launch of Grand Theft Auto V.",
    "The game returns to Vice City, a fictional Miami in the state of Leonida.",
]
_RUN6_QUERIES = [
    "Grand Theft Auto VI documentary gameplay vice city screenshot wallpaper",
    "Grand Theft Auto VI the series documentary gameplay first canon screenshot wallpaper",
    "Grand Theft Auto VI Leonida Jason Lucia documentary gameplay",
    "Grand Theft Auto VI Leonida Keys drug runners documentary",
    "Grand Theft Auto VI Liberty City Lucia documentary",
    "Grand Theft Auto VI Rockstar began preliminary 2014 photo",
    "Grand Theft Auto VI Vice City Leonida documentary gameplay",
]


def test_run6_shape_resolves_instead_of_dying_at_90s(prep_env, monkeypatch):
    """Offline replay of the run-6 web branch shape: 7 scenes, the
    adapter's 90s recovery wall, a 60-row shared batch (9 rows local),
    no live providers.  Before the fix the branch resolved 0/7 at its
    deadline; the phase-budget floor must raise the wall and every scene
    must resolve with its layer recorded."""
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "_discover_source_query", lambda *a, **k: [])
    # Offline: URL-only rows cannot download (no network).  Local rows
    # still resolve — _download_candidate_batch copies supplied local
    # paths without calling the bulk downloader.  Return one error tuple
    # per URL (like the real downloader): an empty list would leak the
    # in-flight cache markers and make later calls wait out the full
    # pending-collection window.
    monkeypatch.setattr(
        resolver, "download_bulk",
        lambda urls, *a, **k: [(u, None, "offline") for u in (urls or [])],
    )
    rows = []
    for i in range(60):
        local = None
        if i < 9:
            local = _make_image(prep_env["tmp"] / f"pool_{i}.jpg", seed=100 + i)
        rows.append(_shared_row(
            f"https://example.invalid/gta6-pool-{i}.jpg",
            "Grand Theft Auto VI Vice City gameplay screenshot",
            local,
            tags="vice city leonida",
            description="Grand Theft Auto VI Vice City gameplay",
        ))
    monkeypatch.setattr(
        resolver, "_warm_shared_discovery_pool",
        lambda **kwargs: {
            "query": _SHORT_TITLE, "candidates": 60,
            "candidate_rows": [dict(row) for row in rows],
            "providers_completed": 3, "providers_pending": 0,
        },
    )
    started = time.monotonic()
    result = resolver.resolve_web_images_for_video_fast(
        script=" ".join(_RUN6_SCENES),
        title=_SHORT_TITLE,
        scene_texts=list(_RUN6_SCENES),
        scene_durations=[10.0] * len(_RUN6_SCENES),
        scene_queries_override=[
            {"query_variants": [q], "search_query": q,
             "scene_subject": _SHORT_TITLE}
            for q in _RUN6_QUERIES
        ],
        max_scene_workers=2,
        stage_deadline_seconds=90.0,
        exhaustive_fallback=True,
        output_namespace="run6-replay-test",
    )
    elapsed = time.monotonic() - started
    audit = result["audit"]
    # The 90s wall is floored to the phase budget (175s for 7 scenes /
    # 2 workers); the branch finishes in seconds on local rows.
    assert audit["stage_deadline_seconds"] == 175.0
    assert audit["resolved"] == len(_RUN6_SCENES)
    assert audit["deadline_reached"] is False
    assert elapsed < 60.0
    layers = [scene.get("web_fallback_layer") for scene in audit["scenes"]]
    assert all(layer for layer in layers), layers
    assert all(Path(path).is_file() for path in result["image_paths"])

# ---------------------------------------------------------------------------
# Worker C — run-7 live-layer diagnosis (job 6624244a, HEAD 7d4e1eb)
#
# Run 7's web branch resolved 9/9 in 14.16s, ALL at cached_prep, with ZERO
# live downloads.  The audit proves the stacked causes:
#   (a) the shared batch banked 0 candidates: its 12s wall (and the hard
#       scene_count>=8 clamp to 12s) closed before the universal lane
#       could answer (engines land at ~25-27s via the proxy), while 40
#       rows arrived later into an orphaned cache entry;
#   (b) a scene wave asking for a query the batch already owned gave up
#       after the 15s DiscoveryCache coalesce wait, before the owner
#       landed its rows;
#   (c) the second web run's vision call died on the fixed 30s grace
#       (1 submitted / 0 completed) although the shared context's call
#       wall is minutes for an external-agent verifier — scene 9 fell
#       to a graphic with a verified prep image in hand.
# The transport-context 75s expiry is NOT the binder: it kills in-branch
# vision by design (the coordinator owns the final gate and verified
# 52 calls in run 7), and the worded one-liner (web_wait_until_complete
# in the transport from_config) must NOT be applied — it hands the
# transport a live client with an infinite deadline and 17 calls, i.e.
# exactly the in-branch double-spend the design forbids.
# ---------------------------------------------------------------------------

def test_shared_batch_wall_fits_one_engine_wave(monkeypatch):
    """Run-7 fix: the shared batch wall must fit one full engine
    round-trip (engines land ~25-27s), not the old 12s that banked 0
    candidates on every >=8-scene job.  45s matches the adapter's
    recovery batch default and the resolver's own useful-floor batch."""
    from services.web_image.resolver import _shared_batch_wall_seconds

    monkeypatch.delenv("VIDZEO_WEB_BATCH_DISCOVERY_SECONDS", raising=False)
    assert _shared_batch_wall_seconds(None, 9) == 45.0
    assert _shared_batch_wall_seconds(None, 2) == 45.0
    # An explicit operator value still wins, low or high.
    assert _shared_batch_wall_seconds(12.0, 9) == 12.0
    assert _shared_batch_wall_seconds(60.0, 9) == 60.0
    monkeypatch.setenv("VIDZEO_WEB_BATCH_DISCOVERY_SECONDS", "30")
    assert _shared_batch_wall_seconds(None, 9) == 30.0


def test_shared_batch_waits_for_slow_provider(prep_env, monkeypatch):
    """The batch wall is a ceiling the pool actually waits out: a
    provider answering at 1.0s is collected with a 3s wall, and missed
    with a 0.5s wall.  (Run 7: the universal lane answered at ~25s
    against a 12s wall, so the batch banked 0.)"""
    import time as _time

    from services.web_image import resolver
    from services.web_image.source_broker import SourceTelemetry

    def _slow_discover(source_name, source_type, query, gallery_cmd=None, **kwargs):
        _time.sleep(1.0)
        return [{
            "url": f"https://example.invalid/batch-{source_name}.jpg",
            "title": f"{query} official gameplay screenshot",
            "tags": query,
            "description": f"{query} gameplay",
            "source": source_name,
            "provider": source_name,
            "query": query,
            "source_url": f"https://example.invalid/batch-{source_name}.jpg",
            "page_url": f"https://example.invalid/batch-{source_name}.jpg",
            "preview_url": "",
        }]

    monkeypatch.setattr(resolver, "_discover_source_query", _slow_discover)
    warm = resolver._warm_shared_discovery_pool(
        query=_SHORT_TITLE,
        scene_count=9,
        gallery_cmd=None,
        discovery_cache=None,
        source_telemetry=SourceTelemetry(),
        stage_deadline_at=_time.monotonic() + 300.0,
        source_health=None,
        batch_seconds_override=3.0,
    )
    assert warm["candidates"] >= 8, warm
    assert warm["providers_completed"] >= 8, warm
    missed = resolver._warm_shared_discovery_pool(
        query=_SHORT_TITLE,
        scene_count=9,
        gallery_cmd=None,
        discovery_cache=None,
        source_telemetry=SourceTelemetry(),
        stage_deadline_at=_time.monotonic() + 300.0,
        source_health=None,
        batch_seconds_override=0.5,
    )
    assert missed["candidates"] == 0, missed


def test_coalesce_wait_covers_inflight_batch_owner(prep_env, monkeypatch):
    """Run-7 fix: a scene wave for a query the shared batch already
    owns waits for that owner (60s cap) instead of giving up after 15s
    and orphaning rows that land at ~25s.  Capture the wait_timeout the
    wave actually passes to DiscoveryCache.get_or_fetch."""
    import hashlib

    from services.web_image import resolver
    from services.web_image.source_broker import DiscoveryCache, SourceTelemetry

    seen_waits = []
    cache = DiscoveryCache()
    real_get_or_fetch = cache.get_or_fetch

    def _recording_get_or_fetch(source, query, fetch, telemetry=None, wait_timeout=None):
        seen_waits.append(wait_timeout)
        return real_get_or_fetch(source, query, fetch, telemetry=telemetry, wait_timeout=wait_timeout)

    monkeypatch.setattr(cache, "get_or_fetch", _recording_get_or_fetch)

    def _rows(source_name, source_type, query, gallery_cmd=None, **kwargs):
        digest = hashlib.sha1(f"{source_name}:{query}".encode()).hexdigest()[:10]
        return [{
            "url": f"https://example.invalid/wave-{digest}.jpg",
            "title": f"{query} official gameplay screenshot",
            "tags": query,
            "description": f"{query} gameplay",
            "source": source_name,
            "provider": source_name,
            "query": query,
            "source_url": f"https://example.invalid/wave-{digest}.jpg",
            "page_url": f"https://example.invalid/wave-{digest}.jpg",
            "preview_url": "",
        }]

    monkeypatch.setattr(resolver, "_discover_source_query", _rows)
    monkeypatch.setattr(resolver, "_download_candidate_batch", lambda *a, **k: [])
    # The rescue rounds call download_bulk directly (not the batch
    # helper): fail fast offline instead of timing out on example.invalid.
    monkeypatch.setattr(
        resolver, "download_bulk",
        lambda urls, *a, **k: [(u, None, "offline") for u in (urls or [])],
    )
    locks = _locks()
    result = resolver._resolve_scene(
        0, _RUN6_SCENES[0], [_RUN6_QUERIES[0]], None,
        prep_env["tmp"] / "coalesce-out", _SHORT_TITLE,
        {}, {}, set(), locks["phash_lock"], locks["md5_lock"],
        locks["file_lock"], discovery_cache=cache,
        source_telemetry=SourceTelemetry(),
        global_topic=_SHORT_TITLE, deadline_at=None,
    )
    assert seen_waits, "no provider fetch went through the cache"
    assert max(wait for wait in seen_waits if wait is not None) >= 60.0, seen_waits


class _FakeVisualContext:
    """Minimal stand-in for VisualDecisionContext for grace tests."""

    def __init__(self, *, call_timeout_seconds, deadline_at=float("inf")):
        self.call_timeout_seconds = call_timeout_seconds
        self.deadline_at = deadline_at
        self.max_calls = 100
        self.calls = 0
        self.degraded_reason = ""
        self.visual_required = False
        self._client = object()

    def get_client(self):
        return self._client


def _live_vision_pass(monkeypatch, resolver):
    """Stub _rank_deferred_scene_shortlist to a passing verdict."""
    calls = []

    def _judge(*, scene_index, candidates, **kwargs):
        calls.append(scene_index)
        winner = dict(candidates[0]) if candidates else {}
        winner.update({"visual_outcome": "pass", "visual_score": 90.0})
        return winner

    monkeypatch.setattr(resolver, "_rank_deferred_scene_shortlist", _judge)
    return calls


def _local_pool_rows(prep_env, count=3):
    rows = []
    for i in range(count):
        local = _make_image(prep_env["tmp"] / f"live_pool_{i}.jpg", seed=200 + i)
        rows.append(_shared_row(
            f"https://example.invalid/live-pool-{i}.jpg",
            "Grand Theft Auto VI Vice City gameplay screenshot",
            local,
            tags="vice city leonida",
            description="Grand Theft Auto VI Vice City gameplay",
        ))
    return rows


def test_visual_grace_follows_context_call_wall(prep_env, monkeypatch):
    """Run-7 fix (scene 9): the visual grace must fit one call wall of
    the context in use.  A 120s-wall context gets a 120s grace; a 30s
    Qwen wall keeps the 30s default.  The grace is recorded in the
    audit so the next failure is diagnosable from the artifact."""
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "_discover_source_query", lambda *a, **k: [])
    # One scene has no shared batch in production (_shared_batch_subject
    # needs >=2 scenes); force the batch subject here so the stubbed
    # warm pool is actually consulted — the grace, not the batch, is
    # what this test measures.
    monkeypatch.setattr(
        resolver, "_shared_batch_subject", lambda *a, **k: _SHORT_TITLE
    )
    rows = _local_pool_rows(prep_env)
    monkeypatch.setattr(
        resolver, "_warm_shared_discovery_pool",
        lambda **kwargs: {
            "query": _SHORT_TITLE, "candidates": len(rows),
            "candidate_rows": [dict(row) for row in rows],
            "providers_completed": 3, "providers_pending": 0,
        },
    )
    _live_vision_pass(monkeypatch, resolver)
    for wall, expected_grace in ((120.0, 120.0), (30.0, 30.0)):
        result = resolver.resolve_web_images_for_video_fast(
            script=" ".join(_RUN6_SCENES[:1]),
            title=_SHORT_TITLE,
            scene_texts=[_RUN6_SCENES[0]],
            scene_durations=[10.0],
            scene_queries_override=[
                {"query_variants": [_RUN6_QUERIES[0]], "search_query": _RUN6_QUERIES[0],
                 "scene_subject": _SHORT_TITLE}
            ],
            max_scene_workers=1,
            stage_deadline_seconds=120.0,
            visual_context=_FakeVisualContext(call_timeout_seconds=wall),
            output_namespace=f"grace-{int(wall)}",
        )
        assert result["audit"]["resolved"] == 1
        assert result["audit"]["visual_budget"]["visual_grace_seconds"] == expected_grace


def test_visual_grace_scene9_shape_slow_call_completes(prep_env, monkeypatch):
    """Scene-9 shape: a vision call that answers at ~35s (past the old
    fixed 30s grace) must complete instead of timing out.  The shared
    external-agent context has a 3600s call wall, so the grace follows
    it; the delivery budget (absent here) and the context deadline
    still bound the wait."""
    import time as _time

    from services.web_image import resolver

    monkeypatch.setattr(resolver, "_discover_source_query", lambda *a, **k: [])
    # One scene has no shared batch in production (_shared_batch_subject
    # needs >=2 scenes); force the batch subject here so the stubbed
    # warm pool is actually consulted — the grace, not the batch, is
    # what this test measures.
    monkeypatch.setattr(
        resolver, "_shared_batch_subject", lambda *a, **k: _SHORT_TITLE
    )
    rows = _local_pool_rows(prep_env)
    monkeypatch.setattr(
        resolver, "_warm_shared_discovery_pool",
        lambda **kwargs: {
            "query": _SHORT_TITLE, "candidates": len(rows),
            "candidate_rows": [dict(row) for row in rows],
            "providers_completed": 3, "providers_pending": 0,
        },
    )
    calls = {"n": 0}

    def _slow_judge(*, scene_index, candidates, **kwargs):
        calls["n"] += 1
        winner = dict(candidates[0]) if candidates else {}
        if calls["n"] == 1:
            # Streaming verdict: keep the candidate without marking it
            # streaming-passed, so the post-retrieval pass still runs.
            winner.update({"visual_outcome": "uncertain", "visual_score": 50.0})
            return winner
        _time.sleep(35.0)
        winner.update({"visual_outcome": "pass", "visual_score": 90.0})
        return winner

    monkeypatch.setattr(resolver, "_rank_deferred_scene_shortlist", _slow_judge)
    started = _time.monotonic()
    result = resolver.resolve_web_images_for_video_fast(
        script=" ".join(_RUN6_SCENES[:1]),
        title=_SHORT_TITLE,
        scene_texts=[_RUN6_SCENES[0]],
        scene_durations=[10.0],
        scene_queries_override=[
            {"query_variants": [_RUN6_QUERIES[0]], "search_query": _RUN6_QUERIES[0],
             "scene_subject": _SHORT_TITLE}
        ],
        max_scene_workers=1,
        stage_deadline_seconds=300.0,
        visual_context=_FakeVisualContext(call_timeout_seconds=3600.0),
        output_namespace="grace-scene9",
    )
    elapsed = _time.monotonic() - started
    budget = result["audit"]["visual_budget"]
    assert budget["submitted"] == 1, budget
    assert budget["completed"] == 1, budget
    assert budget["timed_out"] is False, budget
    assert budget["visual_grace_seconds"] == 3600.0, budget
    assert elapsed >= 35.0
    assert result["audit"]["resolved"] == 1


def test_streaming_vision_skipped_when_coordinator_owns_gate(prep_env, monkeypatch):
    """The transport context must never spend vision in-branch when the
    V2 coordinator owns the final gate.  An external-agent job forces
    the transport context enabled with a 75s fuse; the fuse burning out
    is what accidentally enforced the design in run 7 — the resolver
    must enforce it explicitly via defer_context_verification."""
    from concurrent.futures import ThreadPoolExecutor

    from services.web_image import resolver

    monkeypatch.setattr(resolver, "_discover_source_query", lambda *a, **k: [])
    rows = _local_pool_rows(prep_env, count=1)
    calls = []

    def _counting_judge(*, scene_index, candidates, **kwargs):
        calls.append(scene_index)
        winner = dict(candidates[0]) if candidates else {}
        winner.update({"visual_outcome": "pass", "visual_score": 90.0})
        return winner

    monkeypatch.setattr(resolver, "_rank_deferred_scene_shortlist", _counting_judge)
    locks = _locks()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        deferred = resolver._resolve_scene(
            0, _RUN6_SCENES[0], [_RUN6_QUERIES[0]], None,
            prep_env["tmp"] / "stream-deferred", _SHORT_TITLE,
            {}, {}, set(), locks["phash_lock"], locks["md5_lock"],
            locks["file_lock"], shared_pool_candidates=[dict(rows[0])],
            global_topic=_SHORT_TITLE, deadline_at=None,
            visual_context=object(), visual_pool=pool,
            defer_context_verification=True,
        )
        assert deferred is not None and Path(str(deferred.get("path") or "")).is_file()
        assert calls == [], calls
        calls.clear()
        live = resolver._resolve_scene(
            0, _RUN6_SCENES[0], [_RUN6_QUERIES[0]], None,
            prep_env["tmp"] / "stream-live", _SHORT_TITLE,
            {}, {}, set(), locks["phash_lock"], locks["md5_lock"],
            locks["file_lock"], shared_pool_candidates=[dict(rows[0])],
            global_topic=_SHORT_TITLE, deadline_at=None,
            visual_context=object(), visual_pool=pool,
            defer_context_verification=False,
        )
        assert live is not None and live.get("_streaming_vision_passed") is True
        assert calls == [0], calls
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def test_streaming_vision_hard_fail_releases_claim_for_chain(prep_env, monkeypatch):
    """A streaming vision hard-fail must release the candidate's dedup
    claim.  The first pass claims each candidate (used_files /
    used_urls / global_phashes) BEFORE vision runs; without a rollback
    the inline general_related chain — whose whole job is rescuing a
    vision-rejected scene from already-downloaded rows — finds every
    row already consumed and the scene dies.  (Run-7 replay: scene 2
    resolved 0/1 before the rollback, general_related after it.)"""
    from concurrent.futures import ThreadPoolExecutor

    from services.web_image import resolver

    monkeypatch.setattr(resolver, "_discover_source_query", lambda *a, **k: [])
    rows = _local_pool_rows(prep_env, count=3)
    calls = []

    def _rejecting_judge(*, scene_index, candidates, **kwargs):
        calls.append(scene_index)
        winner = dict(candidates[0]) if candidates else {}
        if not any(
            isinstance(r, dict) and r.get("web_fallback_layer") == "general_related"
            for r in (candidates or [])
        ):
            winner.update({"visual_outcome": "hard_fail", "visual_rejected": True})
        else:
            winner.update({"visual_outcome": "pass", "visual_score": 90.0})
        return winner

    monkeypatch.setattr(resolver, "_rank_deferred_scene_shortlist", _rejecting_judge)
    locks = _locks()
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        result = resolver._resolve_scene(
            1, _RUN6_SCENES[1], [_RUN6_QUERIES[1]], None,
            prep_env["tmp"] / "stream-rollback", _SHORT_TITLE,
            {}, {}, set(), locks["phash_lock"], locks["md5_lock"],
            locks["file_lock"], shared_pool_candidates=[dict(r) for r in rows],
            shared_pool_query=_SHORT_TITLE,
            global_topic=_SHORT_TITLE, deadline_at=None,
            visual_context=_FakeVisualContext(call_timeout_seconds=30.0), visual_pool=pool,
            defer_context_verification=False,
        )
        assert result is not None, "chain must rescue the vision-rejected scene"
        assert result.get("web_fallback_layer") == "general_related", result
        assert Path(str(result.get("path") or "")).is_file()
        assert calls, "the first pass must have submitted vision calls"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def test_transport_one_liner_would_reactivate_inbranch_vision(monkeypatch):
    """Do NOT apply web_wait_until_complete=True to the transport
    context's from_config.  The key DOES map (from_config reads it and
    gives an infinite deadline) — but under an external-agent job the
    transport context is already enabled with 17 calls, so the
    one-liner hands it a live client that never expires: exactly the
    in-branch double-spend the design forbids.  The 75s expiry is what
    accidentally enforces the design today; the resolver enforces it
    explicitly instead (see test_streaming_vision_skipped...)."""
    import time as _time
    from unittest import mock

    import services.job_context as job_context
    from services.visual_intelligence.context import VisualDecisionContext

    transport = {
        "visual_understanding": False,
        "visual_required": False,
        "pipeline_v2_mode": "off",
    }
    with mock.patch.object(
        job_context, "current_job",
        lambda: {"llm_provider": "external_agent", "agent_owner_id": "local-user"},
    ):
        context = VisualDecisionContext.from_config(dict(transport), 13)
        assert context.enabled is True
        assert context.max_calls == 17
        assert 60.0 < context.deadline_at - _time.monotonic() <= 75.0
        # Fresh client is live (external-agent probe is skipped)...
        assert context.get_client() is not None
        # ...but a probe after the 75s fuse gets the run-7 audit state:
        # visual_deadline_exceeded, 0 calls.
        expired = VisualDecisionContext.from_config(dict(transport), 13)
        expired.deadline_at = _time.monotonic() - 1.0
        assert expired.get_client() is None
        assert expired.degraded_reason == "visual_deadline_exceeded"
        assert expired.calls == 0
        # The one-liner maps — and that is precisely why it is wrong here.
        import math

        oneliner = VisualDecisionContext.from_config(
            {**transport, "web_wait_until_complete": True}, 13
        )
        assert oneliner.enabled is True
        assert math.isinf(oneliner.deadline_at)
        assert oneliner.get_client() is not None
        assert oneliner.max_calls == 17


# ---------------------------------------------------------------------------
# Offline replay: run-7 shape with prep COLD (no banked assets)
#
# The live provider rows run 7 never got are reconstructed: same title,
# same scenes/queries, same engine timing shape (rows arriving from the
# shared batch + scene waves).  The provider rows themselves are
# synthetic — the real 40 universal rows were orphaned in the dead
# run-7 process and are not on disk — but the download/validate/select
# path they exercise is the real one.  Prep stays cold on purpose: with
# prep warm every scene short-circuits to cached_prep before any live
# attempt, which is exactly why run 7 never exercised these layers.
# ---------------------------------------------------------------------------

def _live_provider_rows(query, count=3, source="universal_scraper"):
    """Provider rows echoing the query (the way real provider results
    do), with distinct URLs per (source, query) so per-scene attempt
    ledgers and the shared batch do not collapse onto one URL."""
    import hashlib

    rows = []
    for i in range(count):
        digest = hashlib.sha1(f"{source}:{query}:{i}".encode()).hexdigest()[:12]
        url = f"https://example.invalid/live-{digest}.jpg"
        rows.append({
            "url": url,
            "title": f"{query} official gameplay screenshot",
            "tags": query,
            "description": f"{query} gameplay screenshot",
            "source": source,
            "provider": source,
            "query": query,
            "source_url": url,
            "page_url": url,
            "preview_url": "",
        })
    return rows


def _stub_live_downloads(monkeypatch, tmp_path):
    """download_bulk materializes every requested URL as a distinct
    local image (a successful live download), keyed by URL hash.
    Single-URL downloads (max_concurrent=1, the rescue-round helper
    _download_and_accept_single_candidate) fail, modelling the dead
    publisher URLs the rescue rounds hit in production — the batch
    first-pass downloads are the live path under test."""
    import hashlib

    from services.web_image import resolver

    def _download(urls, output_dir, *args, **kwargs):
        if int(kwargs.get("max_concurrent") or 0) == 1:
            return [(str(url), None, "offline") for url in (urls or [])]
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        results = []
        for url in (urls or []):
            seed = int(hashlib.sha1(str(url).encode()).hexdigest()[:6], 16)
            dest = out / f"live_{hashlib.sha1(str(url).encode()).hexdigest()[:12]}.jpg"
            _make_image(dest, seed=seed)
            results.append((str(url), dest, ""))
        return results

    monkeypatch.setattr(resolver, "download_bulk", _download)


def _run_cold_replay(monkeypatch, prep_env, *, vision_judge=None, visual_context=None,
                     namespace="run7-cold-replay"):
    """Run-7 shape, prep cold, providers live.  Returns the resolver
    result.  ``vision_judge`` (optional) replaces the vision shortlist
    judge; ``visual_context`` (optional) is passed through."""
    from services.web_image import resolver

    known_queries = {_RUN6_QUERIES[i] for i in range(len(_RUN6_QUERIES))}
    shared_query = _SHORT_TITLE

    def _discover(source_name, source_type, query, gallery_cmd=None, **kwargs):
        if str(query or "").strip() in known_queries or str(query or "").strip() == shared_query:
            return _live_provider_rows(str(query), source=str(source_name))
        return []

    monkeypatch.setattr(resolver, "_discover_source_query", _discover)
    _stub_live_downloads(monkeypatch, prep_env["tmp"])
    if vision_judge is not None:
        monkeypatch.setattr(resolver, "_rank_deferred_scene_shortlist", vision_judge)
    kwargs = dict(
        script=" ".join(_RUN6_SCENES),
        title=_SHORT_TITLE,
        scene_texts=list(_RUN6_SCENES),
        scene_durations=[10.0] * len(_RUN6_SCENES),
        scene_queries_override=[
            {"query_variants": [q], "search_query": q,
             "scene_subject": _SHORT_TITLE}
            for q in _RUN6_QUERIES
        ],
        max_scene_workers=2,
        stage_deadline_seconds=205.0,
        exhaustive_fallback=True,
        wait_until_complete=True,
        output_namespace=namespace,
    )
    if visual_context is not None:
        kwargs["visual_context"] = visual_context
    return resolver.resolve_web_images_for_video_fast(**kwargs)


def test_run7_cold_replay_live_perfect_layers_fire(prep_env, monkeypatch):
    """Cold-prep replay of run 7: with the batch wall fitting one
    engine wave, live rows are harvested, downloaded, and selected by
    the perfect first pass — the layer run 7 never exercised (0 live
    downloads across 10 branch scenes)."""
    result = _run_cold_replay(monkeypatch, prep_env)
    audit = result["audit"]
    assert audit["resolved"] == len(_RUN6_SCENES), audit["scenes"]
    assert audit["shared_batch"]["candidates"] >= 10, audit["shared_batch"]
    layers = [scene.get("web_fallback_layer") for scene in audit["scenes"]]
    assert layers == ["perfect"] * len(_RUN6_SCENES), layers
    downloads = [int(scene.get("downloaded_candidate_count") or 0) for scene in audit["scenes"]]
    assert all(count >= 1 for count in downloads), downloads
    assert all(Path(path).is_file() for path in result["image_paths"])
    assert audit["deadline_reached"] is False


def test_run7_cold_replay_general_related_fires_on_vision_reject(prep_env, monkeypatch):
    """Cold-prep replay, one scene's vision hard-fails every downloaded
    candidate: the scene must resolve inline at general_related from
    the already-downloaded rows — not fall through to a graphic.  The
    judge fails streaming calls (no fallback layer on the row yet) and
    passes the post-retrieval call (the row carries general_related)."""
    def _judge(*, scene_index, candidates, **kwargs):
        winner = dict(candidates[0]) if candidates else {}
        if scene_index == 1 and not any(
            isinstance(row, dict) and row.get("web_fallback_layer") == "general_related"
            for row in (candidates or [])
        ):
            winner.update({"visual_outcome": "hard_fail", "visual_rejected": True})
            return winner
        winner.update({"visual_outcome": "pass", "visual_score": 90.0})
        return winner

    result = _run_cold_replay(
        monkeypatch, prep_env,
        vision_judge=_judge,
        visual_context=_FakeVisualContext(call_timeout_seconds=30.0),
        namespace="run7-cold-replay-general",
    )
    audit = result["audit"]
    assert audit["resolved"] == len(_RUN6_SCENES), audit["scenes"]
    layers = [scene.get("web_fallback_layer") for scene in audit["scenes"]]
    assert layers[1] == "general_related", layers
    assert all(layer == "perfect" for i, layer in enumerate(layers) if i != 1), layers
    assert int(audit["scenes"][1].get("downloaded_candidate_count") or 0) >= 1
    assert all(Path(path).is_file() for path in result["image_paths"])


# ---------------------------------------------------------------------------
# Worker A — test-11 manifest/cache bridge (job 676bf2cf)
#
# Test 11's prep store is split-brain: the per-title manifest holds 48
# entries with discriminating queries and scene narrations, but every
# entry path is a deleted prep-run temp file; the verified bytes live
# only in the stable asset cache (24 rows, all files present).  The
# manifest fuzzy pass used to drop every such entry on is_file(), so
# find_prepped_web_asset missed 5/5 graphic scenes even with the real
# live queries.  A manifest hit must resolve through the cache by
# prep_key instead of dropping the verified image for a stale path.
# ---------------------------------------------------------------------------

def test_fuzzy_prep_hit_via_manifest_when_entry_path_dead(prep_env, monkeypatch):
    """Legacy cache rows (generic prep_query, no narration) + manifest
    entries pointing at deleted temp files + live stable-cache copies:
    the lookup must still hit, returning the cache copy's path."""
    _record_gta_prep_asset(prep_env, monkeypatch)
    # Legacy row state, exactly like the real test-11 bank: the generic
    # last variant overwrote the discriminating query, so the cache-row
    # fuzzy pass scores 0 and only the manifest can match.
    from services.web_image import resolver

    cache_dir = prep_env["temp"] / "web_image_asset_cache"
    manifest_path = cache_dir / "assets.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    rows[0]["prep_query"] = "Grand Theft Auto VI official image"
    rows[0].pop("prep_queries", None)
    rows[0].pop("prep_narration", None)
    manifest_path.write_text(json.dumps(rows), encoding="utf-8")
    # The manifest entry points at a temp file that no longer exists.
    manifest = prep.new_manifest(_FULL_TITLE, 1.0)
    manifest["scenes"] = [
        {"index": 1, "text": _PREP_NARRATION, "words": 10},
    ]
    manifest["web"] = {
        "attempted": 1,
        "verified": 1,
        "entries": [
            {
                "scene": 1,
                "path": str(prep_env["tmp"] / "gone" / "prep_run_deleted.jpg"),
                "query": _PREP_QUERY,
                "prep_key": prep.prep_cache_key(
                    _FULL_TITLE, _PREP_NARRATION, _PREP_QUERY
                ),
            }
        ],
    }
    assert prep.save_manifest(_FULL_TITLE, 1.0, manifest) is True
    hit = prep.find_prepped_web_asset(
        _SHORT_TITLE, _LIVE_NARRATION, _LIVE_QUERIES
    )
    assert hit is not None
    assert hit.get("prep_fuzzy_match") is True
    assert hit.get("prep_manifest_hit") is True
    assert hit.get("prep_cache_resolved") is True
    hit_path = Path(str(hit.get("path") or ""))
    assert hit_path.is_file()
    # The returned bytes are the stable cache copy, not the dead path.
    assert str(hit_path).startswith(str(cache_dir))
    assert "gone" not in str(hit_path)


def test_fuzzy_prep_miss_when_manifest_path_dead_and_no_cache_copy(prep_env, monkeypatch):
    """A manifest entry with a dead path and NO stable-cache copy must
    still miss — the bridge never invents bytes, it only re-routes to
    a verified copy that exists."""
    source = _record_gta_prep_asset(prep_env, monkeypatch)
    from services.web_image import resolver

    cache_dir = prep_env["temp"] / "web_image_asset_cache"
    manifest_path = cache_dir / "assets.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows[0]["prep_query"] = "Grand Theft Auto VI official image"
    rows[0].pop("prep_queries", None)
    rows[0].pop("prep_narration", None)
    manifest_path.write_text(json.dumps(rows), encoding="utf-8")
    manifest = prep.new_manifest(_FULL_TITLE, 1.0)
    manifest["scenes"] = [
        {"index": 1, "text": _PREP_NARRATION, "words": 10},
    ]
    manifest["web"] = {
        "attempted": 1,
        "verified": 1,
        "entries": [
            {
                "scene": 1,
                "path": str(prep_env["tmp"] / "gone" / "prep_run_deleted.jpg"),
                "query": _PREP_QUERY,
                "prep_key": prep.prep_cache_key(
                    _FULL_TITLE, _PREP_NARRATION, _PREP_QUERY
                ),
            }
        ],
    }
    assert prep.save_manifest(_FULL_TITLE, 1.0, manifest) is True
    # Delete the stable cache copy: nothing live remains for the entry.
    cache_copy = Path(str(rows[0].get("path") or ""))
    assert cache_copy.is_file()
    cache_copy.unlink()
    assert prep.find_prepped_web_asset(
        _SHORT_TITLE, _LIVE_NARRATION, _LIVE_QUERIES
    ) is None
