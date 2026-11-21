"""Replay batched web vision on REAL GTA test-12 data (job 5137cb34).

Source artifacts (read-only; skipped when absent):
- media manifest:
  ~/workspace/vidnets-runtime/output/GTA_VI_1Min_20261120_120122/
  GTA_VI_1Min_20261120_120122_46f68e69/media/manifest.json
- web audits:
  ~/workspace/vidnets-runtime/temp/manifests/
  web_image_audit_5137cb34_5137cb34-run-0001-ebb19221693f_1d950d92.json (13 scenes, 65.18s)
  web_image_audit_5137cb34_5137cb34-run-0001-ebb19221693f_2706d9f9.json (8 scenes, 37.58s)

What test 12 recorded: every audited scene resolved from cached_prep
(already vision-verified at prep time), yet the resolver's visual wave
still filed one live vision call per scene — visual_budget submitted
13 + 8 = 21 — and completed 0 of the first 13 inside the 28s grace
(the inbox round-trip is minutes); the other 8 raised immediately
(single_verify_unavailable, dead visual context). The 5 web images in
the final manifest are byte-identical to the audit-1 cached_prep winners.

The replay drives the REAL _rank_deferred_scene_shortlist twice over
the same scenes/candidates with a counting stub vision client:

- BEFORE: the batch method absent AND the prep flags stripped, i.e.
  the old call pattern — one sequential analyze_image per candidate.
  The stub mimics the live outcome per audit (audit 1 raises a timeout,
  audit 2 raises unavailable), so winners are retained via the metadata
  fallback exactly as in test 12.
- AFTER: the batch method present and prep flags set — the new code.
  Cached_prep scenes must spend 0 vision calls; multi-candidate live
  shortlists must spend 1 batch call instead of one single call per
  tried candidate; the winners must be identical.
"""
import hashlib
import json
from pathlib import Path

import pytest

from services.web_image.resolver import _rank_deferred_scene_shortlist

RUNTIME = Path.home() / "workspace" / "vidnets-runtime"
MANIFEST = (
    RUNTIME
    / "output" / "GTA_VI_1Min_20261120_120122"
    / "GTA_VI_1Min_20261120_120122_46f68e69" / "media" / "manifest.json"
)
AUDIT_GLOB = "web_image_audit_5137cb34_*.json"
AUDIT_DIR = RUNTIME / "temp" / "manifests"
ASSET_CACHE_DIR = RUNTIME / "temp" / "web_image_asset_cache"

ACCEPT_ROW = {
    "entity_conflict": False,
    "identity_score": 90,
    "action_score": 80,
    "context_score": 85,
    "quality": 88,
    "final_score": 87,
    "relevance": 88,
    "confidence": 90,
    "visible_overlays": [],
    "subject_box": {},
    "safe_text_regions": [],
    "fatal_mismatch": False,
    "domain_match": True,
    "scene_match": True,
    "reason": "Grand Theft Auto VI gameplay matches the narration",
    "scene_description": "Grand Theft Auto VI gameplay screenshot",
    "source_presentation_type": "photograph",
    "has_text": False,
}

REJECT_ROW = dict(
    ACCEPT_ROW,
    identity_score=10,
    action_score=10,
    context_score=10,
    final_score=10,
    relevance=10,
    confidence=90,
    fatal_mismatch=True,
    domain_match=False,
    scene_match=False,
    reason="wrong entity: a different game, unrelated to Grand Theft Auto VI",
    scene_description="a different game",
)


def _sha12(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


class _CountingVisionClient:
    """Stub vision client: counts single + batch calls, answers per path."""

    def __init__(self, calls, accept_paths, raise_kind=None):
        self.calls = calls
        self.accept_paths = set(accept_paths)
        self.raise_kind = raise_kind  # None | "timeout" | "unavailable"
        self.model = "test/vision"

    def _row(self, path):
        row = dict(ACCEPT_ROW if str(path) in self.accept_paths else REJECT_ROW)
        row["candidate_id"] = str(path)
        return row

    def analyze_image(self, path, prompt, system_prompt):
        self.calls["single"] += 1
        if self.raise_kind == "timeout":
            raise TimeoutError("inbox round-trip exceeded the visual grace window")
        if self.raise_kind == "unavailable":
            raise RuntimeError("visual_deadline_exceeded")
        return self._row(path)

    def analyze_images_batch(self, paths, prompt, system_prompt, candidate_ids=None):
        self.calls["batch"] += 1
        if self.raise_kind is not None:
            raise RuntimeError("visual_deadline_exceeded")
        ids = list(candidate_ids or [])
        rows = {}
        for index, path in enumerate(paths):
            cid = ids[index] if index < len(ids) else str(path)
            rows[cid] = self._row(path)
        return rows


class _NoBatchVisionClient(_CountingVisionClient):
    """The old transport: no batch method, so the gate single-verifies."""

    analyze_images_batch = None


class _FakeVisualContext:
    def __init__(self, client):
        self._client = client
        self.visual_required = False
        self.model = "test/vision"

    def get_client(self):
        return self._client


def _load_test12():
    audits = sorted(AUDIT_DIR.glob(AUDIT_GLOB))
    if not MANIFEST.is_file() or len(audits) != 2:
        pytest.skip("test-12 artifacts not present on this machine")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    audit_data = [json.loads(path.read_text(encoding="utf-8")) for path in audits]
    return manifest, audit_data


def _scene_inputs(audit):
    """(scene_number, scene_text, query, winner_path) per audited scene."""
    scenes = []
    for scene in audit["scenes"]:
        path = str(scene.get("path") or "")
        if not path or not Path(path).is_file():
            continue
        queries = [q for q in (scene.get("queries") or []) if q]
        scenes.append({
            "scene_number": int(scene.get("scene_number") or 0),
            "scene_text": queries[0] if queries else "Grand Theft Auto VI",
            "query": queries[0] if queries else "",
            "path": path,
        })
    return scenes


def _candidate(path, prep=False):
    row = {
        "path": str(path),
        "candidate_id": str(path),
        "source": "web",
        "provider": "yandex",
        "title": "Grand Theft Auto VI official screenshot",
        "final_score": 80.0,
    }
    if prep:
        row["prep_cache_hit"] = True
        row["reused_from_prep"] = True
        row["prep_key"] = "prep-key-" + Path(path).stem
    return row


def _run_gate(scene, candidates, context):
    return _rank_deferred_scene_shortlist(
        scene_index=int(scene["scene_number"]) - 1,
        scene_text=scene["scene_text"],
        subject="Grand Theft Auto VI",
        global_topic="Grand Theft Auto VI",
        query=scene["query"],
        candidates=candidates,
        visual_context=context,
    )


def test_manifest_web_images_are_audit_cached_prep_winners():
    """Ground truth: the 5 shipped web images ARE the cached_prep winners."""
    manifest, audits = _load_test12()
    web_paths = [
        path for path, label in zip(manifest["media_paths"], manifest["source_labels"])
        if label == "Web Images"
    ]
    assert len(web_paths) == 5, "test 12 shipped exactly 5 web images"
    audit1_hashes = {_sha12(s["path"]) for s in _scene_inputs(audits[0])}
    for path in web_paths:
        assert _sha12(path) in audit1_hashes, f"manifest web image not an audit-1 winner: {path}"


def test_replay_cached_prep_21_before_0_after():
    """21 filed vision calls before (13 + 8) vs 0 after; same winners."""
    _manifest, audits = _load_test12()
    # Audit 1: 13 scenes, visual_budget submitted 13, completed 0 (grace
    # timeout). Audit 2: 8 scenes, submitted 8, completed 8 — but every
    # call raised single_verify_unavailable (dead visual context).
    audit_meta = [
        (audits[0], "timeout", 13),
        (audits[1], "unavailable", 8),
    ]
    before_calls = {"single": 0, "batch": 0}
    after_calls = {"single": 0, "batch": 0}
    for audit, raise_kind, expected_submitted in audit_meta:
        scenes = _scene_inputs(audit)
        assert len(scenes) == expected_submitted, (
            f"audit has {len(scenes)} scenes, expected {expected_submitted}")
        assert audit["visual_budget"]["submitted"] == expected_submitted
        for scene in scenes:
            winner = scene["path"]
            # BEFORE: old pattern — no batch method, prep flags stripped.
            old_client = _NoBatchVisionClient(
                before_calls, accept_paths={winner}, raise_kind=raise_kind)
            old_result = _run_gate(
                scene, [_candidate(winner, prep=False)],
                _FakeVisualContext(old_client))
            assert old_result is not None, f"scene {scene['scene_number']} unresolved (before)"
            assert old_result["path"] == winner
            # AFTER: new code — batch present, prep flags set.
            new_client = _CountingVisionClient(
                after_calls, accept_paths={winner}, raise_kind=None)
            new_result = _run_gate(
                scene, [_candidate(winner, prep=True)],
                _FakeVisualContext(new_client))
            assert new_result is not None, f"scene {scene['scene_number']} unresolved (after)"
            assert new_result["path"] == winner
            assert new_result.get("visual_outcome") == "pass"
    assert before_calls == {"single": 21, "batch": 0}, before_calls
    assert after_calls == {"single": 0, "batch": 0}, after_calls


def test_replay_live_shortlist_batch_collapses_sequential_tries():
    """Live 3-candidate shortlists: 3 sequential singles -> 1 batch call."""
    _manifest, audits = _load_test12()
    scenes = _scene_inputs(audits[0])
    # The manifest web scenes (4, 5, 7) as live shortlists: two alternates
    # from the prep asset cache (wrong subject for the scene) ranked ahead
    # of the recorded winner, so the old loop tries all three.
    alternates = sorted(
        p for p in ASSET_CACHE_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    from services.web_image.safety import image_is_safe

    alternates = [
        p for p in alternates
        if image_is_safe(
            str(p), title="Grand Theft Auto VI official screenshot",
            url="", tags="", source="yandex", description="",
            source_url="", page_url="", preview_url="",
        )[0]
    ]
    assert len(alternates) >= 6, "need safety-passing alternate images in the asset cache"
    before_calls = {"single": 0, "batch": 0}
    after_calls = {"single": 0, "batch": 0}
    web_scenes = [s for s in scenes if s["scene_number"] in (4, 5, 7)]
    assert len(web_scenes) == 3
    for index, scene in enumerate(web_scenes):
        winner = scene["path"]
        alts = [str(alternates[index * 2]), str(alternates[index * 2 + 1])]
        assert winner not in alts
        shortlist = [_candidate(p, prep=False) for p in [*alts, winner]]
        accept = {winner}
        old_client = _NoBatchVisionClient(before_calls, accept_paths=accept)
        old_result = _run_gate(scene, shortlist, _FakeVisualContext(old_client))
        assert old_result is not None and old_result["path"] == winner
        new_client = _CountingVisionClient(after_calls, accept_paths=accept)
        new_result = _run_gate(scene, shortlist, _FakeVisualContext(new_client))
        assert new_result is not None and new_result["path"] == winner
    assert before_calls == {"single": 9, "batch": 0}, before_calls
    assert after_calls == {"single": 0, "batch": 3}, after_calls
