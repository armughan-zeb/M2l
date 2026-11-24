"""Tests for balanced YouTube scoring, relaxed conflict vetoes, and pre-stock web image rescue."""
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.visual_intelligence.semantic_guard import (
    calculate_youtube_weighted_score,
    explicit_scene_conflict,
)
from services.media_resolution_coordinator import (
    _quota_gate_is_hard_rejection,
    resolve_storyboard_media_v2,
    SceneResolution,
)


def test_calculate_youtube_weighted_score_formula():
    # Both vision and transcript available: 0.40 vision + 0.35 transcript + 0.25 metadata
    # is_relaxed=True adds +5 if vision >= 35.0
    score = calculate_youtube_weighted_score(
        vision_score=80.0,
        vision_available=True,
        transcript_score=60.0,
        transcript_available=True,
        metadata_score=70.0,
        is_relaxed=False,
    )
    expected = 80.0 * 0.40 + 60.0 * 0.35 + 70.0 * 0.25
    assert abs(score - round(expected, 2)) < 0.01

    # In relaxed mode, gets +5 bonus if vision >= 35.0
    relaxed_score = calculate_youtube_weighted_score(
        vision_score=80.0,
        vision_available=True,
        transcript_score=60.0,
        transcript_available=True,
        metadata_score=70.0,
        is_relaxed=True,
    )
    assert abs(relaxed_score - round(expected + 5.0, 2)) < 0.01

    # Vision unavailable: rebalances to transcript 0.55, metadata 0.45
    no_vision = calculate_youtube_weighted_score(
        vision_score=0.0,
        vision_available=False,
        transcript_score=80.0,
        transcript_available=True,
        metadata_score=60.0,
        is_relaxed=True,
    )
    expected_no_vision = 80.0 * 0.55 + 60.0 * 0.45
    assert abs(no_vision - round(expected_no_vision, 2)) < 0.01


def test_explicit_scene_conflict_relaxed_vs_strict():
    # NFL clip describing different team / different matchup
    verdict_spurious = {
        "status": "rejected",
        "fatal_mismatch": True,
        "reason": "Different matchup: Kansas City Chiefs vs Miami Dolphins with quarterback Tua Tagovailoa",
    }
    # Strict mode must flag this as conflict
    assert explicit_scene_conflict(verdict_spurious, relaxed=False) is True
    # Relaxed mode must NOT flag this as conflict
    assert explicit_scene_conflict(verdict_spurious, relaxed=True) is False

    # In-domain wrong entity is strict conflict but relaxed advisory
    verdict_wrong_entity = {
        "status": "rejected",
        "fatal_mismatch": True,
        "reason": "wrong entity: Kansas City Chiefs instead of Baltimore Ravens",
    }
    assert explicit_scene_conflict(verdict_wrong_entity, relaxed=False) is True
    assert explicit_scene_conflict(verdict_wrong_entity, relaxed=True) is False

    # True fatal conflict (wrong domain / different sport)
    verdict_soccer = {
        "status": "rejected",
        "fatal_mismatch": True,
        "reason": "Wrong domain: different_sport soccer match in stadium",
    }
    assert explicit_scene_conflict(verdict_soccer, relaxed=False) is True
    assert explicit_scene_conflict(verdict_soccer, relaxed=True) is True

    # Unsafe content is always fatal
    verdict_unsafe = {
        "unsafe_content": True,
        "reason": "suggestive overlay",
    }
    assert explicit_scene_conflict(verdict_unsafe, relaxed=False) is True
    assert explicit_scene_conflict(verdict_unsafe, relaxed=True) is True


def test_quota_gate_is_hard_rejection_soundness():
    # Spurious matchup difference is not a hard rejection
    assert not _quota_gate_is_hard_rejection(
        "visual_rejected",
        {"fatal_mismatch": True, "reason": "Different matchup: Chiefs vs Dolphins"},
    )
    # Fatal difference is a hard rejection
    assert _quota_gate_is_hard_rejection(
        "visual_rejected",
        {"fatal_mismatch": True, "reason": "different_sport: soccer penalty kick"},
    )
    # Unsafe is always hard rejection
    assert _quota_gate_is_hard_rejection(
        "visual_pass",
        {"unsafe_content": True, "reason": "explicit"},
    )


def test_pre_stock_web_image_rescue(tmp_path):
    web_file = tmp_path / "nfl_web_image.jpg"
    web_file.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)

    rows = [{
        "scene_index": 0,
        "scene_id": "scene-0",
        "container_id": "c-0",
        "text": "Kansas City Chiefs offense on the field",
        "duration": 4.0,
    }]

    # Simulate discovery returning a valid web candidate on disk
    web_candidate = {
        "candidate_id": "web-cand-1",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 85.0,
        "visual_decision": {
            "accepted": True,
            "status": "accepted",
            "score": 85.0,
        },
    }

    # stock lane that should NOT be called if web rescue works
    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    # web lane returns nothing in initial discovery so scene falls to neutral before stock
    def mock_web_lane(scene, event):
        return []

    def mock_yt_lane(scene, event):
        return []

    # We mock discover or inject discovered candidate
    from unittest.mock import patch

    def mock_discover(index, source, lane):
        if source == "web":
            return index, source, [web_candidate], "ok"
        return index, source, [], "empty"

    with patch("services.media_resolution_coordinator.ThreadPoolExecutor") as mock_tpe:
        # Run resolution with web image available
        pass

    # Direct test on resolve_storyboard_media_v2:
    # If initial discovery yields web candidate, coordinator resolves it to web
    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=lambda s, e: [web_candidate],
        stock_lane=mock_stock_lane,
        max_workers=1,
        deadline_seconds=10.0,
    )
    # Must be resolved to web, NOT stock or neutral
    assert len(res) == 1
    assert res[0].provider == "web"
    assert res[0].status == "accepted"
    assert len(stock_called) == 0


def test_pre_stock_web_image_rescue_when_initial_empty(tmp_path):
    web_file = tmp_path / "nfl_rescue_image.jpg"
    web_file.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)

    rows = [{
        "scene_index": 0,
        "scene_id": "scene-0",
        "container_id": "c-0",
        "text": "Kansas City Chiefs offense on the field",
        "duration": 4.0,
    }]

    web_candidate = {
        "candidate_id": "web-cand-rescue",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 90.0,
        "visual_decision": {
            "accepted": True,
            "status": "accepted",
            "score": 90.0,
        },
    }

    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    class MockBatchWebLane:
        def __init__(self):
            self.shared_batch = False
            self.call_count = 0

        def __call__(self, scene, event):
            # Discovery returns empty so scene falls through to neutral
            return []

        def get_row(self, index, timeout=0.0):
            self.call_count += 1
            if self.call_count < 4:
                return []
            return [web_candidate]

    lane = MockBatchWebLane()
    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=lane,
        stock_lane=mock_stock_lane,
        max_workers=1,
        deadline_seconds=10.0,
    )
    # The pre-stock rescue pass must retrieve web_candidate via get_row and rescue scene to web!
    assert len(res) == 1
    assert res[0].provider == "web"
    assert res[0].status == "accepted"
    assert res[0].resolution_kind == "pre_stock_web_rescue"
    assert len(stock_called) == 0


def test_score_verdict_relaxed_mode_normalizes_entity_difference():
    import sys
    if "server" not in sys.path:
        sys.path.insert(0, "server")
    from services.pipeline_v2_media_adapter import _score_verdict

    scene = {
        "text": "The Baltimore Ravens take the field for kickoff",
        "source_policy": {"youtube_relaxed_visual_gate": True},
    }
    verdict = {
        "accepted": True,
        "fatal_mismatch": True,
        "reason": "wrong entity: Kansas City Chiefs instead of Baltimore Ravens",
        "domain_match": True,
        "identity_score": 75.0,
        "context_score": 85.0,
        "final_score": 80.0,
    }
    res = _score_verdict(scene, verdict)
    assert res.get("spurious_mismatch") is True
    assert res.get("fatal_mismatch") is False
    assert res.get("status") != "rejected"
    assert res.get("rejection_reason") != "explicit_scene_conflict"


def test_score_verdict_strict_mode_rejects_domain_conflict():
    import sys
    if "server" not in sys.path:
        sys.path.insert(0, "server")
    from services.pipeline_v2_media_adapter import _score_verdict

    scene = {
        "text": "The Baltimore Ravens score a touchdown in the NFL",
        "source_policy": {"youtube_relaxed_visual_gate": True},
    }
    verdict = {
        "accepted": True,
        "fatal_mismatch": True,
        "reason": "wrong domain: different_sport soccer match with ball in net",
        "domain_match": False,
        "identity_score": 20.0,
        "context_score": 20.0,
        "final_score": 20.0,
    }
    res = _score_verdict(scene, verdict)
    assert res.get("accepted") is False
    assert res.get("status") == "rejected"
    assert res.get("rejection_reason") == "explicit_scene_conflict"


def test_pre_stock_web_image_rescue_cross_scene(tmp_path):
    web_file = tmp_path / "nfl_scene1_image.jpg"
    web_file.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)

    rows = [
        {
            "scene_index": 0,
            "scene_id": "scene-0",
            "container_id": "c-0",
            "text": "The NFL kickoff begins",
            "duration": 4.0,
        },
        {
            "scene_index": 1,
            "scene_id": "scene-1",
            "container_id": "c-0",
            "text": "Kansas City Chiefs offense on the field",
            "duration": 4.0,
        },
    ]

    web_candidate = {
        "candidate_id": "web-cand-cross",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 90.0,
        "visual_decision": {
            "accepted": True,
            "status": "accepted",
            "score": 90.0,
        },
    }

    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    class MockBatchWebLane:
        def __init__(self):
            self.shared_batch = False

        def __call__(self, scene, event):
            idx = int(scene.get("scene_index", 0))
            if idx == 1:
                return [web_candidate]
            return []

        def get_row(self, index, timeout=0.0):
            if index == 1:
                return [web_candidate]
            return []

    lane = MockBatchWebLane()
    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=lane,
        stock_lane=mock_stock_lane,
        max_workers=1,
        deadline_seconds=10.0,
    )
    assert len(res) == 2
    assert res[0].provider == "web"
    assert res[1].provider == "web"
    assert len(stock_called) == 0


# ---------------------------------------------------------------------------
# Worker A — pre-stock rescue consults the prep cache first (job 676bf2cf)
#
# Test 11 finished 0 web images with vision dead on arrival
# (visual_deadline_exceeded, call_cap 0) while 48 verified prep images
# sat unused: the rescue only collected discovered / web_lane /
# web_fallback_lane candidates and never looked at prep.  A prep hit is
# already vision-verified, so the rescue must resolve from cached_prep
# with NO fresh vision call; live candidates still verify through a
# fresh rescue-scoped visual budget that is restored when the rescue
# loop ends.
# ---------------------------------------------------------------------------

import json as _json
import random as _random
import time as _time

from PIL import Image as _PILImage


@pytest.fixture()
def _rescue_prep_env(tmp_path, monkeypatch):
    """Isolate prep/temp roots (same shape as test_web_inline_fallback)."""
    root = tmp_path / "prep-root"
    temp = tmp_path / "temp"
    monkeypatch.setenv("VIDZEO_PREP_ROOT", str(root))
    monkeypatch.setenv("VIDZEO_TEMP_DIR", str(temp))
    from services.web_image import resolver

    monkeypatch.setattr(resolver, "TEMP_DIR", temp)
    return {"root": root, "temp": temp, "tmp": tmp_path}


def _rescue_make_image(path, seed=0):
    _random.seed(seed)
    _PILImage.effect_noise((800, 450), 48).convert("RGB").save(path, quality=92)
    return path


_GTA_FULL_TITLE = "Grand Theft Auto VI: Return to Vice City"
_GTA_SHORT_TITLE = "Grand Theft Auto VI"

# Real test-11 graphic scenes (job 676bf2cf manifest): the 13-scene live
# segmentation merges the 24 prep scenes, and the live branch titles the
# job with the short title — the exact keys miss, so only the fuzzy
# manifest pass (discriminating query + scene narration) can match.
_GTA_PREP_SCENES = [
    (
        "Lucia came from Liberty City, fought for her family",
        "Grand Theft Auto VI liberty family official screenshot",
    ),
    (
        "Rockstar began preliminary work in 2014, principal production after Red Dead Redemption 2",
        "Grand Theft Auto VI began preliminary principal official screenshot",
    ),
    (
        "The game was confirmed in February 2022, unfinished footage leaked that September",
        "Grand Theft Auto VI confirmed february unfinished official screenshot",
    ),
    (
        "It became the most preordered game of all time, estimated sales seventeen million",
        "Grand Theft Auto VI estimated sales seventeen official screenshot",
    ),
    (
        "Vice City is open again across all of Leonida",
        "Grand Theft Auto VI leonida again official screenshot",
    ),
]
_GTA_LIVE_NARRATIONS = [
    "Lucia came from Liberty City, fought for her family, and served time in Leonida Penitentiary. ",
    "Rockstar began preliminary work in 2014, and principal production followed the launch of Red Dead Redemption 2 in 2018. ",
    "The game was confirmed in February 2022, unfinished footage leaked that September, and the formal reveal came in December 2023. ",
    "It became the most preordered game of all time, with estimated sales of more than seventeen million digital copies and over one point six billion dollars in revenue before launch. ",
    "Vice City is open again , this time, across all of Leonida.",
]


def _record_gta_prep_bank(prep_env, monkeypatch):
    """Test-11 prep store shape: legacy cache rows (generic prep_query,
    no variant queries / narration) + a manifest whose entries point at
    deleted prep-run temp files.  The verified bytes live only in the
    stable asset cache."""
    from services import advance_prep as prep

    entries = []
    for i, (narration, query) in enumerate(_GTA_PREP_SCENES):
        source = prep_env["tmp"] / f"gta_prep_{i}.jpg"
        _rescue_make_image(source, seed=300 + i)
        asset = {
            "path": str(source),
            "source_url": f"https://example.invalid/gta6-prep-{i}.jpg",
            "title": _GTA_FULL_TITLE,
            "active_subject": _GTA_SHORT_TITLE,
            "subjects": [_GTA_SHORT_TITLE],
        }
        assert prep.record_prepped_web_asset(
            _GTA_FULL_TITLE, narration, query, asset
        ) is True
        entries.append({
            "scene": i + 1,
            "path": str(prep_env["tmp"] / "gone" / f"prep_run_scene_{i + 1}.jpg"),
            "query": query,
            "prep_key": prep.prep_cache_key(_GTA_FULL_TITLE, narration, query),
        })
    # Legacy row state, exactly like the real bank.
    from services.web_image import resolver

    cache_dir = prep_env["temp"] / "web_image_asset_cache"
    manifest_path = cache_dir / "assets.json"
    rows = _json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(rows) == len(_GTA_PREP_SCENES)
    for row in rows:
        row["prep_query"] = "Grand Theft Auto VI official image"
        row.pop("prep_queries", None)
        row.pop("prep_narration", None)
    manifest_path.write_text(_json.dumps(rows), encoding="utf-8")
    manifest = prep.new_manifest(_GTA_FULL_TITLE, 1.0)
    manifest["scenes"] = [
        {"index": i + 1, "text": narration, "words": len(narration.split())}
        for i, (narration, _query) in enumerate(_GTA_PREP_SCENES)
    ]
    manifest["web"] = {
        "attempted": len(entries),
        "verified": len(entries),
        "entries": entries,
    }
    assert prep.save_manifest(_GTA_FULL_TITLE, 1.0, manifest) is True


def _dead_visual_context(*, calls=0, max_calls=17):
    """Test-11 audit shape: deadline expired, 0 calls submitted, cap 17."""
    from services.visual_intelligence.context import VisualDecisionContext

    context = VisualDecisionContext(
        enabled=True,
        api_key="external-agent:test",
        model="test-model",
        scene_count=5,
        max_calls=max_calls,
        budget_usd=1.0,
        deadline_at=_time.monotonic() - 10.0,
    )
    context.calls = calls
    context.degraded_reason = "visual_deadline_exceeded"
    return context


class _RecordingVerifier:
    """Fake visual verifier bound to a real (dead) visual context.

    Answers like the real verify(): unavailable while the context is
    dead (expired deadline or spent call cap), accepted once the
    rescue grants a fresh budget.  Records every call.
    """

    def __init__(self, context):
        self.visual_context = context
        self.calls = []

    def __call__(self, scene, candidate):
        self.calls.append(str(candidate.get("path") or ""))
        context = self.visual_context
        if context is None:
            return {"accepted": False, "status": "visual_unavailable",
                    "reason": "no_context", "visual_checked": False}
        if (
            _time.monotonic() >= float(context.deadline_at)
            or int(context.calls) >= int(context.max_calls)
            or str(context.degraded_reason or "")
        ):
            return {"accepted": False, "status": "visual_unavailable",
                    "reason": "visual_deadline_exceeded",
                    "visual_checked": False, "visual_available": False}
        return {"accepted": True, "status": "accepted", "final_score": 88.0,
                "visual_checked": True, "visual_available": True,
                "semantic_verified": True}


def test_pre_stock_web_rescue_cached_prep_dead_vision(_rescue_prep_env, monkeypatch):
    """Test-11 replay shape: 5 unresolved scenes, dead vision (expired
    deadline, 0 calls), prep bank warm in its legacy split-brain state.
    The rescue must resolve 5/5 from cached_prep, verified, with NO
    vision call attempted — and stock must never run."""
    _record_gta_prep_bank(_rescue_prep_env, monkeypatch)

    rows = [{
        "scene_index": i,
        "scene_id": f"scene-{i}",
        "container_id": "c-0",
        "content_title": _GTA_SHORT_TITLE,
        "text": narration,
        "duration": 4.0,
    } for i, narration in enumerate(_GTA_LIVE_NARRATIONS)]

    context = _dead_visual_context(calls=0, max_calls=17)
    verifier = _RecordingVerifier(context)

    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=lambda s, e: [],
        stock_lane=mock_stock_lane,
        visual_verifier=verifier,
        max_workers=1,
        deadline_seconds=10.0,
    )
    assert len(res) == 5
    for i in range(5):
        assert res[i].provider == "web", res[i]
        assert res[i].status == "accepted"
        assert res[i].resolution_kind == "pre_stock_web_rescue"
        assert res[i].verification_status == "verified"
        assert Path(str(res[i].selected_path or "")).is_file()
        assert res[i].visual_decision.get("visual_checked") is True
        accepted = [
            a for a in (res[i].attempts or [])
            if a.get("stage") == "pre_stock_web_rescue"
            and a.get("status") == "accepted"
        ]
        assert accepted, res[i].attempts
        assert accepted[0].get("source") == "cached_prep", accepted
        assert accepted[0].get("prep_cache_hit") is True
        assert float(accepted[0].get("prep_fuzzy_score") or 0) >= 2.0
    # Vision was dead on arrival and prep needs none: zero calls.
    assert verifier.calls == [], verifier.calls
    assert len(stock_called) == 0
    # The rescue's fresh budget was granted (ledger note) and then
    # restored: the deadline must be back to the main loop's own
    # visual-gate grace (~50s), not the rescue's 120s window.  (The
    # main loop refreshes the dead fixture context before the rescue
    # runs, so "restored" means the pre-rescue value, not the expired
    # fixture value.)
    granted = [
        a for a in (res[0].attempts or [])
        if a.get("stage") == "pre_stock_web_rescue"
        and a.get("status") == "rescue_visual_budget_granted"
    ]
    assert granted, res[0].attempts
    _t_end = _time.monotonic()
    assert context.deadline_at <= _t_end + 60, context.deadline_at - _t_end
    assert context.max_calls == 17


def test_pre_stock_web_rescue_fresh_visual_budget_verifies_live_candidate(_rescue_prep_env, monkeypatch):
    """Dead main context (expired deadline AND spent call cap) + a live
    unverified web candidate: the rescue grants its own bounded budget,
    the candidate verifies, and the budget is restored afterwards."""
    web_file = _rescue_prep_env["tmp"] / "live_rescue.jpg"
    _rescue_make_image(web_file, seed=77)

    rows = [{
        "scene_index": 0,
        "scene_id": "scene-0",
        "container_id": "c-0",
        "content_title": "How the Panama Canal Changed World Trade",
        "text": "The canal changed world trade when it opened in 1914.",
        "duration": 4.0,
    }]

    live_candidate = {
        "candidate_id": "web-cand-live",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "title": "Panama Canal cargo ship crossing the locks",
        "final_score": 90.0,
    }

    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    class MockBatchWebLane:
        def __init__(self):
            self.shared_batch = False
            self.call_count = 0

        def __call__(self, scene, event):
            return []

        def get_row(self, index, timeout=0.0):
            self.call_count += 1
            if self.call_count < 4:
                return []
            return [live_candidate]

    # Call cap spent as well as deadline expired: the rescue must grant
    # headroom, not just a deadline refresh.
    context = _dead_visual_context(calls=17, max_calls=17)
    verifier = _RecordingVerifier(context)

    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=MockBatchWebLane(),
        stock_lane=mock_stock_lane,
        visual_verifier=verifier,
        max_workers=1,
        deadline_seconds=10.0,
    )
    assert len(res) == 1
    assert res[0].provider == "web"
    assert res[0].status == "accepted"
    assert res[0].resolution_kind == "pre_stock_web_rescue"
    assert res[0].verification_status == "verified"
    # The live candidate was verified inside the rescue's fresh budget.
    assert len(verifier.calls) == 1, verifier.calls
    granted = [
        a for a in (res[0].attempts or [])
        if a.get("stage") == "pre_stock_web_rescue"
        and a.get("status") == "rescue_visual_budget_granted"
    ]
    assert granted, res[0].attempts
    assert int(granted[0].get("headroom_calls") or 0) >= 1
    assert len(stock_called) == 0
    # Budget restored: the deadline is back to the main loop's own
    # grace (not the rescue's 120s window) and the headroom is gone.
    _t_end = _time.monotonic()
    assert context.deadline_at <= _t_end + 60, context.deadline_at - _t_end
    assert context.max_calls == 17

