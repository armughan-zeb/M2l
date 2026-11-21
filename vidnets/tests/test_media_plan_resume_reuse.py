"""Resume reuses a completed media coordinator result (Nov 20, 2026).

Launch-day job a7f52c58 paused AFTER its media coordinator completed,
then attempt 2 re-ran media from scratch (~47 min rework). The fix:
``_media_orchestrator`` reuses ``checkpoints/media_plan.json`` when the
saved plan is complete and valid for the current scenes, and re-runs
media otherwise. These tests cover the loader/validator that decides
which of those two happens; the orchestrator hook itself is a thin call
into it (see ``services/media_plan_checkpoint.py``).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from services.media_plan_checkpoint import (  # noqa: E402
    load_saved_media_plan,
    rebuild_media_resolutions,
    validate_saved_media_plan,
)
from services.media_resolution_coordinator import SceneResolution  # noqa: E402

SCENES = ["GTA 6 is finally here", "After a decade of waiting", "16M pre-orders"]


def _make_plan(tmp_path, scenes=SCENES, *, accepted=None, unresolved=0,
               texts=None, rows=True, terminal=True, missing=()):
    """Write a synthetic completed media plan into a fake package dir."""
    package = tmp_path / "package"
    checkpoints = package / "checkpoints"
    checkpoints.mkdir(parents=True)
    paths = []
    labels = []
    for index, _scene in enumerate(scenes):
        asset = package / f"scene_{index}.jpg"
        asset.write_bytes(b"fake-image-bytes")
        paths.append(str(asset))
        labels.append("Web Images")
    for index in missing:
        Path(paths[index]).unlink()
    rows_data = []
    if rows:
        for index, path in enumerate(paths):
            rows_data.append({
                "scene_index": index,
                "selected_path": path,
                "media_type": "image",
                "provider": "web",
                "status": "accepted",
            })
    metadata = {
        "source_scene_texts": list(texts if texts is not None else scenes),
    }
    if rows:
        metadata["media_resolution_v2"] = rows_data
    if terminal:
        metadata["terminal_accounting"] = {
            "accepted": len(scenes) if accepted is None else accepted,
            "degraded": 0,
            "unresolved": unresolved,
        }
    plan = {"media_paths": paths, "source_labels": labels, "metadata": metadata}
    (checkpoints / "media_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return package, plan


def test_reuse_completed_plan(tmp_path):
    """(a) A resume with a completed media_plan.json reuses it verbatim."""
    package, plan = _make_plan(tmp_path)
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused == plan
    assert "3 scene(s)" in reason


def test_no_checkpoint_behaves_as_before(tmp_path):
    """(c) A resume with no coordinator result re-runs media (None)."""
    package = tmp_path / "package"
    (package / "checkpoints").mkdir(parents=True)
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert reason == "no saved media plan checkpoint"


def test_missing_package_dir_behaves_as_before(tmp_path):
    reused, _reason = load_saved_media_plan(tmp_path / "no-such-package", SCENES)
    assert reused is None


def test_partial_plan_falls_back(tmp_path):
    """(b) Coordinator accepted only 2/3 scenes: re-run, never ship partial."""
    package, _plan = _make_plan(tmp_path, accepted=2)
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "accepted 2/3" in reason


def test_unresolved_scene_falls_back(tmp_path):
    package, _plan = _make_plan(tmp_path, unresolved=1)
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "unresolved" in reason


def test_stale_scene_texts_fall_back(tmp_path):
    """Same job, different script/scene split: the plan is stale."""
    package, _plan = _make_plan(
        tmp_path, texts=["GTA 6 is finally here", "After a decade of waiting",
                         "A different third scene"])
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "differ" in reason


def test_scene_count_mismatch_falls_back(tmp_path):
    package, _plan = _make_plan(tmp_path, scenes=SCENES[:2])
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "covers 2 scene(s), current job has 3" in reason


def test_missing_asset_file_falls_back(tmp_path):
    """A resolved asset deleted since the pause: re-run, never render dangling."""
    package, _plan = _make_plan(tmp_path, missing=(1,))
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "no longer on disk" in reason


def test_no_terminal_accounting_rows_prove_completion(tmp_path):
    """Older plans without terminal accounting reuse when all rows accepted."""
    package, _plan = _make_plan(tmp_path, terminal=False)
    reused, _reason = load_saved_media_plan(package, SCENES)
    assert reused is not None


def test_rejected_resolution_row_falls_back(tmp_path):
    package, plan = _make_plan(tmp_path)
    plan["metadata"]["media_resolution_v2"][1]["status"] = "unresolved"
    (package / "checkpoints" / "media_plan.json").write_text(
        json.dumps(plan), encoding="utf-8")
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "not 'accepted'" in reason


def test_resolution_path_mismatch_falls_back(tmp_path):
    package, plan = _make_plan(tmp_path)
    plan["metadata"]["media_resolution_v2"][0]["selected_path"] = "/tmp/other.jpg"
    (package / "checkpoints" / "media_plan.json").write_text(
        json.dumps(plan), encoding="utf-8")
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "does not match media_paths" in reason


def test_invalid_json_falls_back(tmp_path):
    package = tmp_path / "package"
    checkpoints = package / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "media_plan.json").write_text("{not json", encoding="utf-8")
    reused, reason = load_saved_media_plan(package, SCENES)
    assert reused is None
    assert "not valid JSON" in reason


def test_validate_never_raises_on_garbage():
    assert validate_saved_media_plan(None, SCENES)[0] is False
    assert validate_saved_media_plan({"media_paths": "nope"}, SCENES)[0] is False
    assert validate_saved_media_plan({}, [])[0] is False


def test_rebuild_media_resolutions_round_trips_rows(tmp_path):
    _package, plan = _make_plan(tmp_path)
    resolutions = rebuild_media_resolutions(plan)
    assert set(resolutions) == {0, 1, 2}
    assert all(isinstance(row, SceneResolution) for row in resolutions.values())
    assert resolutions[1].selected_path == plan["media_paths"][1]
    assert resolutions[1].provider == "web"
    assert resolutions[1].status == "accepted"


def test_rebuild_media_resolutions_empty_without_rows():
    assert rebuild_media_resolutions({}) == {}
    assert rebuild_media_resolutions({"metadata": {}}) == {}


# --- Replay against the real launch-day artifacts (a7f52c58) -----------------
# The archived media_plan.json is attempt 2's intermediate "media_ready"
# write (attempt 1's copy was overwritten by attempt 2 — the bug this fix
# addresses). It is still a genuine completed coordinator result for the
# same 18 scenes, with all 18 assets on disk, so it proves the validator
# accepts real coordinator output and would have reused attempt 1's plan
# had it survived. Skipped where the runtime artifacts are absent.

RUNTIME = Path("/home/hatch/workspace/vidnets-runtime/output")
A7F52C58 = (
    RUNTIME
    / "GTA_VI_LaunchDay_1Min_20261119_200006_7845895e"
    / "checkpoints"
)
ARCHIVED_PLAN = (
    A7F52C58
    / "media_history"
    / "media_plan-f8f27e94117dc14b08e6a45141771293e9222c136e538912560f0cb59320ed8c.json"
)


@pytest.mark.skipif(
    not ARCHIVED_PLAN.is_file() or not (A7F52C58 / "audio_plan.json").is_file(),
    reason="a7f52c58 runtime artifacts not present on this machine",
)
def test_replay_real_a7f52c58_plan_reuses(tmp_path):
    archived = json.loads(ARCHIVED_PLAN.read_text(encoding="utf-8"))
    audio = json.loads((A7F52C58 / "audio_plan.json").read_text(encoding="utf-8"))
    scenes = list(audio["scene_texts"])
    assert len(scenes) == 18
    package = tmp_path / "package"
    checkpoints = package / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "media_plan.json").write_text(
        json.dumps(archived), encoding="utf-8")
    reused, reason = load_saved_media_plan(package, scenes)
    assert reused is not None, reason
    assert reused == archived  # plan identical: media stage skipped, same plan
    assert reused["metadata"]["terminal_accounting"]["accepted"] == 18
    resolutions = rebuild_media_resolutions(reused)
    assert len(resolutions) == 18
    assert resolutions[0].provider == "youtube"
