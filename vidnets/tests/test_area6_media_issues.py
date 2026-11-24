"""Area 6 — media issues (Wright job 9606ea41, Nov 16, 2026).

Regression cases for the web lane going 0/14 in the Wright run:

  * Main run 04:33 — all 4 engines dead in ~20s on one shared wall
    (DuckDuckGo bot-gated; Yandex/Bing/Flickr 15s read timeouts through
    the egress proxy), 0 candidates after 219s.
  * Recovery run — only a ~22s wall (10s query + 12s batch discovery)
    and died 0/6 the same way.
  * Candidates DID arrive 05:14 (20-54 per query) but a minute too late —
    the stage had already given up and the rows were discarded.
  * Final master: 8/14 real (all YouTube), 6/14 graphics, 0 web images.

Plus the Berlin Wall scene-2 watermark case (vision passed an AFP via
Getty photo; the ownership gate rejected it after the alternates were
gone) and the Wright "Visually repeated media remains" flag, which only
surfaced in release review after the render.
"""
import hashlib
import time
from unittest import mock

import pytest

from services.web_image.universal_scraper import (
    UniversalImageResult,
    UniversalScraperManager,
)


class _FakeEngine:
    def __init__(self, name, delay=0.0, rows=1, fail=False):
        self.name = name
        self.delay = delay
        self.rows = rows
        self.fail = fail
        self.calls = 0

    def search(self, query, limit, page, safe_search):
        del limit, page, safe_search
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.name} unavailable")
        return [
            UniversalImageResult(
                title=f"{self.name} {query} {idx}",
                image_url=f"https://{self.name}.example/{idx}.jpg",
                source_engine=self.name,
            )
            for idx in range(self.rows)
        ]


class _FlakyEngine(_FakeEngine):
    """Slow on the first call, fast afterwards — a stalled proxy wave."""

    def search(self, query, limit, page, safe_search):
        delay = self.delay if self.calls == 0 else 0.02
        self.calls += 1
        if delay:
            time.sleep(delay)
        return [
            UniversalImageResult(
                title=f"{self.name} {query} {idx}",
                image_url=f"https://{self.name}.example/{idx}.jpg",
                source_engine=self.name,
            )
            for idx in range(self.rows)
        ]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. Web lane cannot die on a 20s wall
# ---------------------------------------------------------------------------

def test_staggered_deadlines_keep_later_engine_alive(monkeypatch):
    """One dead engine must not kill the lane: the second engine gets a
    later deadline than the first, so it still returns after the first
    engine's wall has passed."""
    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0.5")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "0")
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=1)
    manager._engines = {
        "first": _FakeEngine("first", delay=1.4, rows=1),
        "second": _FakeEngine("second", delay=0.6, rows=1),
    }
    response = manager.search(
        "wright brothers first flight",
        engine_ids=["first", "second"],
        limit_per_engine=1,
        timeout_seconds=1.0,
    )
    # First engine's deadline is 1.0s (it dies); second's is 1.5s (it lives).
    assert response.total == 1
    assert response.results[0].source_engine == "second"
    assert any(
        stat.engine == "first" and not stat.success and "timeout" in stat.error
        for stat in response.stats
    )
    assert any(stat.engine == "second" and stat.success for stat in response.stats)


def test_late_engine_result_is_spooled_and_recovered_on_recheck(monkeypatch):
    """Wright replay: a slow engine's rows arrive after the batch wall.
    They must be salvaged to the spool and recovered by a re-check,
    not discarded as 'late result ignored'."""
    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0.05")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "0")
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=1)
    manager._engines = {
        "fast": _FakeEngine("fast", rows=1),
        "slow": _FakeEngine("slow", delay=0.8, rows=1),
    }
    first = manager.search(
        "wright brothers flyer",
        engine_ids=["fast", "slow"],
        limit_per_engine=1,
        timeout_seconds=0.5,
    )
    assert first.total == 1  # only the fast engine made the wall
    # A second search for the same query re-checks the spool first: the
    # slow engine's row from batch 1 has landed by now.
    manager._engines = {
        "fast": _FakeEngine("fast", rows=1),
        "slow": _FakeEngine("slow", delay=0.8, rows=1),
    }
    second = manager.search(
        "wright brothers flyer",
        engine_ids=["fast", "slow"],
        limit_per_engine=1,
        timeout_seconds=0.5,
    )
    assert {row.source_engine for row in second.results} == {"fast", "slow"}
    # And a direct re-check recovers the second batch's late row too.
    recovered = manager.collect_late_results(
        "wright brothers flyer", wait_seconds=1.0
    )
    assert [row.source_engine for row in recovered] == ["slow"]
    assert recovered[0].image_url == "https://slow.example/0.jpg"


def test_empty_batch_retries_timed_out_engine_once_with_backoff(monkeypatch):
    """Wright replay: every engine timed out and the batch came back
    empty.  One retry with backoff — not a silent 0-candidate stage —
    must follow, and only once."""
    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "0")
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=1)
    engine = _FlakyEngine("flaky", delay=0.9, rows=2)
    manager._engines = {"flaky": engine}
    response = manager.search(
        "wright brothers kitty hawk",
        engine_ids=["flaky"],
        limit_per_engine=2,
        timeout_seconds=0.5,
    )
    assert response.total == 2
    assert engine.calls == 2  # first wave timed out, exactly one retry
    assert all(stat.success for stat in response.stats)


def test_bot_gated_engine_is_not_retried_on_empty_batch(monkeypatch):
    """A bot-gated engine is a definitive refusal, not a stall: the
    empty-batch retry must not waste a wave on it."""
    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "0")
    manager = UniversalScraperManager(max_workers=1, engine_timeout_seconds=1)
    manager._engines = {"gated": _FakeEngine("gated", fail=True)}
    manager._engines["gated"].search = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("Yandex bot challenge (SmartCaptcha; egress IP gated)")
    )
    response = manager.search(
        "wright brothers", engine_ids=["gated"], limit_per_engine=1,
        timeout_seconds=0.2,
    )
    assert response.total == 0
    assert not response.stats[0].success


# ---------------------------------------------------------------------------
# 2. Recovery gets a real budget
# ---------------------------------------------------------------------------

def test_recovery_stage_wall_never_collapses_below_one_engine_wave(monkeypatch):
    """Wright replay: the recovery run got a 22s wall from the delivery
    allowance snapshot.  The stage wall must keep a real minimum — one
    engine round-trip — instead of collapsing to the snapshot."""
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_MIN_SECONDS", raising=False)
    from services.pipeline_v2_media_adapter import (
        _web_recovery_batch_discovery_seconds,
        _web_recovery_min_seconds,
        _web_recovery_query_deadline_seconds,
        _web_recovery_stage_seconds,
    )

    assert _web_recovery_min_seconds() == 60.0
    # A 22s allowance snapshot used to produce a 22s wall; now it floors.
    assert _web_recovery_stage_seconds(6, 6, 22.0) >= 60.0
    assert _web_recovery_stage_seconds(6, 6, None) >= 90.0
    assert _web_recovery_stage_seconds(14, 6, 22.0) >= 60.0
    # The sub-walls must each fit one full universal engine round-trip
    # (30s), not the old 10s query / 12s discovery that killed Wright.
    assert _web_recovery_query_deadline_seconds({}) >= 30.0
    assert _web_recovery_batch_discovery_seconds({}) >= 30.0


def test_recovery_budget_floor_is_bounded_by_remaining_time(monkeypatch):
    """The resolver-side floor may dip into the render reserve by at most
    one wave, but never past the job's absolute deadline."""
    monkeypatch.delenv("VIDZEO_WEB_RECOVERY_MIN_SECONDS", raising=False)
    from services.web_image.resolver import _web_recovery_budget_floor_seconds

    assert _web_recovery_budget_floor_seconds(22.0, 352.0) == 60.0
    assert _web_recovery_budget_floor_seconds(120.0, 352.0) == 120.0
    assert _web_recovery_budget_floor_seconds(22.0, 40.0) == 40.0
    assert _web_recovery_budget_floor_seconds(22.0, 0.0) == 22.0


# ---------------------------------------------------------------------------
# 3. Watermark caught early, alternates kept alive
# ---------------------------------------------------------------------------

def test_watermarked_top_candidate_swapped_early_with_alternates_alive(
    tmp_path, monkeypatch
):
    """Berlin Wall scene-2 replay: the top web photo passed vision but
    carries an AFP-via-Getty ownership mark.  The coordinator must swap
    to the next ranked candidate while the alternates are still alive —
    not at the final handoff, after they are gone."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    top = tmp_path / "top.jpg"
    top.write_bytes(b"top watermarked photo bytes")
    second = tmp_path / "second.jpg"
    second.write_bytes(b"clean second photo bytes")

    def verify(_scene, _candidate):
        return {
            "accepted": True,
            "status": "accepted",
            "final_score": 95.0,
            "identity_score": 90.0,
            "action_score": 90.0,
            "context_score": 90.0,
        }

    def web_lane(_scene, _cancel_event):
        return [
            {"path": str(top), "source": "web", "title": "Schabowski press conference",
             "score": 95, "metadata_score": 95, "final_score": 95, "media_type": "image"},
            {"path": str(second), "source": "web", "title": "Schabowski press conference",
             "score": 90, "metadata_score": 90, "final_score": 90, "media_type": "image"},
        ]

    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": str(path) == str(top)})

    result = resolve_storyboard_media_v2(
        [{"scene_index": 0, "scene_id": "berlin-2",
          "text": "Schabowski announces the new travel rules."}],
        youtube_lane=lambda *_args: [],
        web_lane=web_lane,
        visual_verifier=verify,
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
    )
    resolution = result[0]
    assert resolution.selected_path == str(second)
    assert resolution.status == "accepted"
    assert any(
        attempt.get("stage") == "ownership_watermark_early_gate"
        and attempt.get("status") == "ownership_watermark_detected"
        and attempt.get("path") == str(top)
        for attempt in resolution.attempts
    )
    # The watermarked top candidate must not resurface as an alternate.
    assert all(
        str(alternate.get("path") or "") != str(top)
        for alternate in resolution.alternates
    )


def test_all_watermarked_pool_degrades_instead_of_shipping_watermark(tmp_path, monkeypatch):
    """When every ranked candidate is watermarked, the scene must fall
    through to the rescue tiers — never ship the watermarked still."""
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    top = tmp_path / "top.jpg"
    top.write_bytes(b"top watermarked photo bytes")

    def verify(_scene, _candidate):
        return {"accepted": True, "status": "accepted", "final_score": 95.0}

    def web_lane(_scene, _cancel_event):
        return [{"path": str(top), "source": "web", "title": "Schabowski",
                 "score": 95, "metadata_score": 95, "final_score": 95,
                 "media_type": "image"}]

    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": True})

    result = resolve_storyboard_media_v2(
        [{"scene_index": 0, "scene_id": "berlin-2",
          "text": "Schabowski announces the new travel rules."}],
        youtube_lane=lambda *_args: [],
        web_lane=web_lane,
        visual_verifier=verify,
        neutral_factory=lambda _scene: None,
        deadline_seconds=30,
    )
    # The watermarked still must not ship — not as the selected still,
    # and not re-admitted by a later rescue tier.  The scene degrades to
    # a graphic instead (a quality/availability fallback, never a
    # watermarked photo).
    assert result[0].selected_path != str(top)
    assert result[0].provider in {"neutral", "graphic", "generated_graphic"}
    assert any(
        attempt.get("stage") == "ownership_watermark_early_gate"
        for attempt in result[0].attempts
    )


# ---------------------------------------------------------------------------
# 4. Pre-render repeated-media check
# ---------------------------------------------------------------------------

def _mixed_media_plan(paths, ledger):
    return {
        "media_paths": list(paths),
        "source_labels": ["Web Images"] * len(paths),
        "metadata": {
            "source_lane_policy": {"mode": "Mixed Media"},
            "media_resolution_v2": [
                {
                    "provider": "web",
                    "media_type": "image",
                    "status": "accepted",
                    "verification_status": "verified",
                    "asset_fingerprint": f"fp-{path}",
                    "candidate_ledger": ledger,
                }
                for path in paths
            ],
        },
    }


def _ledger_entry(path, fingerprint, score=9):
    return {
        "path": path,
        "provider": "web",
        "media_type": "image",
        "asset_fingerprint": fingerprint,
        "verified": True,
        "status": "accepted",
        "final_score": score,
    }


def test_non_adjacent_repeat_swapped_pre_render(monkeypatch):
    """Wright replay: the same still at scenes 0 and 2 (non-adjacent)
    sailed past the adjacent-only repair and was only flagged by
    release review after the render.  With a distinct verified
    alternate in the pool, the later scene must be swapped before render."""
    import services.pipeline_v2_media_adapter as adapter

    ledger = [
        _ledger_entry("a.jpg", "fp-a", 10),
        _ledger_entry("b.jpg", "fp-b", 9),
        _ledger_entry("c.jpg", "fp-c", 8),
        _ledger_entry("d.jpg", "fp-d", 7),
    ]
    plan = _mixed_media_plan(["a.jpg", "b.jpg", "a.jpg", "c.jpg"], ledger)
    monkeypatch.setattr(
        adapter, "_validate_renderable_asset",
        lambda path: (True, "ok", "image"))
    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": False})
    repaired = adapter.validate_media_plan_for_render_v2(
        plan,
        [{"scene_id": f"scene-{idx}"} for idx in range(4)],
        render_resolution=(1920, 1080),
        title="Test",
        neutral_asset_factory=lambda *_args, **_kwargs: None,
    )
    paths = repaired["media_paths"]
    assert paths[0] == "a.jpg"
    assert paths[1] == "b.jpg"
    assert paths[2] == "d.jpg"  # the unused distinct alternate
    assert paths[3] == "c.jpg"
    assert len(set(paths)) == 4
    assert repaired["metadata"]["pre_render_repeat_repairs"] == 1
    row = repaired["metadata"]["media_resolution_v2"][2]
    assert row["resolution_kind"] == "repeated_media_pre_render_repair"


def test_repeat_with_no_distinct_alternate_records_shortfall_without_failing(monkeypatch):
    """A repeat the pool cannot fix is recorded, not fatal: a quality
    defect must never kill the video (only no-media / 0-real do)."""
    import services.pipeline_v2_media_adapter as adapter

    ledger = [
        _ledger_entry("a.jpg", "fp-a", 10),
        _ledger_entry("b.jpg", "fp-b", 9),
    ]
    plan = _mixed_media_plan(["a.jpg", "b.jpg", "a.jpg"], ledger)
    monkeypatch.setattr(
        adapter, "_validate_renderable_asset",
        lambda path: (True, "ok", "image"))
    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": False})
    repaired = adapter.validate_media_plan_for_render_v2(
        plan,
        [{"scene_id": f"scene-{idx}"} for idx in range(3)],
        render_resolution=(1920, 1080),
        title="Test",
        neutral_asset_factory=lambda *_args, **_kwargs: None,
    )
    assert repaired["media_paths"] == ["a.jpg", "b.jpg", "a.jpg"]
    shortfalls = repaired["metadata"]["pre_render_repeat_shortfalls"]
    assert shortfalls and shortfalls[0]["scene_index"] == 2
    assert shortfalls[0]["reason"] == "no_distinct_verified_local_alternate"


def test_near_duplicate_images_swapped_pre_render(tmp_path, monkeypatch):
    """Two different files showing the same pixels (different bytes, so
    the fingerprint check misses them) must still be caught before
    render via the perceptual hash, and swapped to a distinct alternate."""
    pytest.importorskip("PIL")
    from PIL import Image

    import services.pipeline_v2_media_adapter as adapter

    def gradient(name, direction, tweak=None):
        # Structured images: a solid colour hashes identically to every
        # other solid colour, so the fixtures need real luminance structure
        # for the perceptual hash to distinguish them.
        path = tmp_path / name
        image = Image.new("RGB", (64, 64))
        pixels = image.load()
        for y in range(64):
            for x in range(64):
                if direction == "horizontal":
                    value = int(255 * x / 63)
                elif direction == "vertical":
                    value = int(255 * y / 63)
                elif direction == "diagonal":
                    value = int(255 * (x + y) / 126)
                else:  # reversed horizontal
                    value = int(255 * (63 - x) / 63)
                pixels[x, y] = (value, value, value)
        if tweak:
            image.putpixel((0, 0), tweak)
        image.save(path, format="PNG")
        return str(path)

    gray1 = gradient("gray1.png", "horizontal")
    gray2 = gradient("gray2.png", "horizontal", tweak=(129, 129, 129))
    black = gradient("black.png", "vertical")
    white = gradient("white.png", "diagonal")
    alt = gradient("alt.png", "reversed")
    assert gray1 != gray2  # different files, different bytes
    ledger = [
        _ledger_entry(gray1, "fp-gray1", 10),
        _ledger_entry(black, "fp-black", 9),
        _ledger_entry(gray2, "fp-gray2", 8),
        _ledger_entry(white, "fp-white", 7),
        _ledger_entry(alt, "fp-alt", 6),
    ]
    plan = _mixed_media_plan([gray1, black, gray2, white], ledger)
    monkeypatch.setattr(
        adapter, "_validate_renderable_asset",
        lambda path: (True, "ok", "image"))
    monkeypatch.setattr(
        "services.web_image.ownership_mark.inspect_ownership_mark",
        lambda path: {"watermark": False})
    repaired = adapter.validate_media_plan_for_render_v2(
        plan,
        [{"scene_id": f"scene-{idx}"} for idx in range(4)],
        render_resolution=(1920, 1080),
        title="Test",
        neutral_asset_factory=lambda *_args, **_kwargs: None,
    )
    assert repaired["media_paths"][2] == alt
    assert repaired["metadata"]["pre_render_repeat_repairs"] == 1


# ---------------------------------------------------------------------------
# 5. a48fdf1 ("render never stops for one image") still holds end to end
# ---------------------------------------------------------------------------

def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _healthy_scene(path):
    sha = _sha(path)
    return {
        "start": 0, "duration": 3, "narration": "The wall stood for decades.",
        "primary_visual": {"type": "SmartImage"},
        "media": {
            "src": str(path), "type": "image",
            "shots": [{
                "path": str(path), "asset_path": str(path),
                "media_type": "image", "type": "image",
                "verification": "verified", "verification_status": "verified",
                "verified_asset_sha256": sha, "asset_sha256": sha,
                "asset_fingerprint": sha,
                "visual_decision": {
                    "accepted": True, "visual_checked": True,
                    "verified_asset_sha256": sha,
                },
            }],
        },
    }


def _defective_scene(path, photo_sha, alternates=None):
    file_sha = _sha(path)
    scene = {
        "start": 0, "duration": 3,
        "narration": "Schabowski reads the new travel rules.",
        "primary_visual": {"type": "SmartImage"},
        "media": {
            "src": str(path), "type": "graphic", "is_graphic_fallback": True,
            "shots": [{
                "path": str(path), "asset_path": str(path),
                "media_type": "image", "type": "image", "source": "graphic",
                "fallback_layer": "terminal_grounded_graphic_after_real_media_exhausted:ownership_watermark_detected",
                "verification": "grounded_graphic_fallback",
                "verification_status": "grounded_graphic_fallback",
                "verified_asset_sha256": photo_sha, "asset_sha256": file_sha,
                "asset_fingerprint": file_sha,
                "visual_decision": {
                    "accepted": True, "visual_checked": True,
                    "semantic_verified": True,
                    "verified_asset_sha256": photo_sha,
                },
            }],
        },
    }
    if alternates is not None:
        scene["primary_visual"]["asset"] = {"alternates": alternates}
    return scene


def test_two_single_image_defects_repair_end_to_end_and_render_continues(tmp_path):
    """a48fdf1 replay, two defects at once: one scene swaps to its next
    ranked alternate, the other falls to a clean graphic re-stamped to
    the file's real hash — and the audit passes, so the render continues."""
    from services.editorial_director.production_contract import repair_media_defects

    graphic1 = _write(tmp_path, "emergency1.jpg", b"emergency graphic one")
    alternate = _write(tmp_path, "bundesarchiv.jpg", b"watermark-free photo bytes")
    alt_sha = _sha(alternate)
    graphic2 = _write(tmp_path, "emergency2.jpg", b"emergency graphic two")
    healthy = _healthy_scene(_write(tmp_path, "good.jpg", b"good photo bytes"))
    with_alternate = _defective_scene(
        graphic1, photo_sha="deadbeef" * 8,
        alternates=[{
            "path": str(alternate), "candidate_id": "web:bundesarchiv-1",
            "visual_decision": {
                "accepted": True, "visual_checked": True,
                "verified_asset_sha256": alt_sha,
            },
        }],
    )
    without_alternate = _defective_scene(graphic2, photo_sha="deadbeef" * 8)
    manifest = {"scenes": [healthy, with_alternate, without_alternate]}
    result = repair_media_defects(manifest)
    assert result["audit"]["status"] == "pass"
    assert result["audit"]["errors"] == []
    assert result["substitutions"][0]["to"] == "alternate"
    assert result["substitutions"][1]["to"] == "graphic"
    assert result["graphic_scenes"] == 1
    assert result["allowance_exceeded"] is False
    repaired = manifest["scenes"][1]["media"]["shots"][0]
    assert repaired["path"] == str(alternate)
    assert repaired["verified_asset_sha256"] == alt_sha
    graphic_shot = manifest["scenes"][2]["media"]["shots"][0]
    assert graphic_shot["verified_asset_sha256"] == _sha(graphic2)
