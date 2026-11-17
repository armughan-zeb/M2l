"""Area 4 — Remotion render bugs: regression tests.

Replays the real failures behind GTA VI ef306926 and Wright 9606ea41:

* GTA VI's render died with exit code 1 / CancelledError because the media
  cache computed under low memory (64 MB) is below Remotion's 240 MB
  minimum. That deterministic config error was classified "transient"
  (blanket "exit code" match), burned the one in-process retry identically,
  and was recorded only as a generic render_failed.
* A checkpoint resume rendered to a different output path than the first
  render, so the JS section checkpoints beside the first path were never
  reused and every resume re-rendered finished sections from scratch.
* A resume that failed again left the FIRST failure's checkpoint in place,
  so the driver and humans chased a stale cause.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import app as server_app
from services import native_render_policy

# Exact GTA VI ef306926 failure, from its checkpoints/render_failure.json.
GTA_MEDIACACHE_ERROR = (
    "Remotion render failed with exit code 1: columnNumber: 37,\n"
    "fileName: 'http://localhost:3000/bundle.js',\n"
    "functionName: 'workLoopSync',\n"
    "lineNumber: 12847\n"
    "},\n"
    "{\n"
    "columnNumber: 7,\n"
    "fileName: 'http://localhost:3000/bundle.js',\n"
    "functionName: 'renderRootSync',\n"
    "lineNumber: 12828\n"
    "}\n"
    "],\n"
    "delayRenderCall: null,\n"
    "frame: null,\n"
    "chunk: null,\n"
    "name: 'CancelledError'\n"
    "}\n"
    "[Remotion] Failure: {\"code\":\"VIDZEO_RENDER_FAILED\",\"message\":"
    "\"Error: The minimum value for the \\\"mediaCacheSizeInBytes\\\" prop "
    "is 240MB (251658240), got: 67108864\"}"
)


def test_gta_mediacache_error_is_not_an_immediate_retry(monkeypatch):
    """The deterministic 64 MB media-cache error must not burn the retry."""
    error = RuntimeError(GTA_MEDIACACHE_ERROR)
    assert native_render_policy._transient_remotion_failure(error) is False
    # ...but the job must pause for a later retry, not fail terminally:
    # a later attempt can pass once render-mode freed memory or the cache
    # floor is fixed.
    assert native_render_policy._retryable_remotion_failure(error) is True


def test_gta_mediacache_error_is_rendered_once_then_pauses_for_retry(monkeypatch):
    """Unlocked render: one attempt, wrapped retryable, stage remotion_render."""
    calls = []

    def failing_render(*, manifest, output_path, config, progress_callback, cancellation, memory_snapshot=None):
        calls.append(dict(config))
        raise RuntimeError(GTA_MEDIACACHE_ERROR)

    monkeypatch.setattr(
        native_render_policy, "render_semantic_documentary_remotion", failing_render
    )
    monkeypatch.setattr(native_render_policy.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="legacy fallback is disabled") as excinfo:
        native_render_policy._render_semantic_documentary_unlocked(
            manifest={"scenes": []},
            output_path="/tmp/never-written.mp4",
            config={"render_backend": "remotion", "semantic_render_required": True},
        )
    assert len(calls) == 1
    assert excinfo.value.stage == "remotion_render"
    assert excinfo.value.retryable is True
    assert excinfo.value.immediate_retry is False


def test_transient_browser_crash_retries_once_with_backoff(monkeypatch):
    """A genuine transient (page crash) retries once, after a backoff pause."""
    sleeps = []
    attempts = {"count": 0}

    def flaky_render(*, manifest, output_path, config, progress_callback, cancellation, memory_snapshot=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("Remotion render failed with exit code 1: page crashed")
        return {"output_path": str(output_path), "renderer": "remotion-react"}

    monkeypatch.setattr(
        native_render_policy, "render_semantic_documentary_remotion", flaky_render
    )
    monkeypatch.setattr(native_render_policy.time, "sleep", lambda s: sleeps.append(s))
    result = native_render_policy._render_semantic_documentary_unlocked(
        manifest={"scenes": []},
        output_path="/tmp/never-written.mp4",
        config={"render_backend": "remotion", "semantic_render_required": True},
    )
    assert result["renderer"] == "remotion-react"
    assert attempts["count"] == 2
    assert sleeps and sleeps[0] > 0


def test_hard_bundle_error_never_retries_and_is_not_retryable(monkeypatch):
    """A syntax error in the bundle is terminal: no retry, not retryable."""
    calls = []

    def failing_render(*, manifest, output_path, config, progress_callback, cancellation, memory_snapshot=None):
        calls.append(1)
        raise RuntimeError("SyntaxError: Unexpected token in bundle.js")

    monkeypatch.setattr(
        native_render_policy, "render_semantic_documentary_remotion", failing_render
    )
    with pytest.raises(RuntimeError, match="legacy fallback is disabled") as excinfo:
        native_render_policy._render_semantic_documentary_unlocked(
            manifest={"scenes": []},
            output_path="/tmp/never-written.mp4",
            config={"render_backend": "remotion", "semantic_render_required": True},
        )
    assert len(calls) == 1
    assert excinfo.value.retryable is False


def test_failure_checkpoint_writer_covers_every_kind(tmp_path):
    """render_failure.json always carries kind/error/edit_plan_path/retryable."""
    package = tmp_path / "pkg"
    (package / "checkpoints").mkdir(parents=True)
    config = {
        "_output_package_dir": str(package),
        "_remotion_plan_path": str(package / "plan.json"),
    }

    low_memory = RuntimeError("out of memory")
    low_memory.memory_snapshot = {"available_mb": 128}
    cases = [
        (low_memory, "low_memory"),
        (server_app.RemotionEditPlanError("bad plan"), "edit_plan_invalid"),
        (RuntimeError(GTA_MEDIACACHE_ERROR), "render_config_invalid"),
        (RuntimeError("Remotion render failed with exit code 1: boom"), "render_failed"),
    ]
    for error, expected_kind in cases:
        payload = server_app._write_render_failure_checkpoint(config, error)
        assert payload["kind"] == expected_kind
        assert payload["retryable"] is True
        assert payload["edit_plan_path"] == str(package / "plan.json")
        assert payload["error"].startswith(type(error).__name__)
        stored = json.loads((package / "checkpoints" / "render_failure.json").read_text(encoding="utf-8"))
        assert stored == payload

    from services.cancellation import JobCancelled

    cancelled = JobCancelled("Shared video processing deadline exceeded")
    payload = server_app._write_render_failure_checkpoint(config, cancelled)
    assert payload["kind"] == "cancelled"
    assert payload["cancelled_by"] == "delivery_deadline"
    assert payload["retryable"] is True


def _build_resume_package(tmp_path, *, failure_kind="render_failed", recorded_render_path=None):
    package = tmp_path / "resume-video"
    checkpoints = package / "checkpoints"
    editorial = package / "editorial"
    render_dir = package / "render"
    for folder in (checkpoints, editorial, render_dir):
        folder.mkdir(parents=True)
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"fake-jpeg-bytes" * 100)
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake-audio-bytes" * 100)
    plan_path = editorial / "remotion_edit_plan.json"
    plan_path.write_text(json.dumps({
        "fps": 30,
        "duration": 1.0,
        "audio": {"voiceover": {"src": str(audio)}},
        "scenes": [{
            "scene_index": 0,
            "start": 0.0,
            "duration": 1.0,
            "narration": "One beat only.",
            "media": {"type": "image", "src": str(image)},
            "primary_visual": {"type": "SmartImage", "asset": {"type": "image", "src": str(image)}},
        }],
    }), encoding="utf-8")
    (checkpoints / "state.json").write_text(json.dumps({
        "stage": "waiting_for_render_retry", "status": "retryable",
    }), encoding="utf-8")
    (checkpoints / "render_failure.json").write_text(json.dumps({
        "kind": failure_kind, "error": "stale first failure", "retryable": True,
    }), encoding="utf-8")
    pre_render = {
        "backend": "remotion", "edit_plan_path": str(plan_path),
        "media_paths": [str(image)], "audio_path": str(audio), "duration": 1.0,
    }
    if recorded_render_path is not None:
        pre_render["render_output_path"] = recorded_render_path
    (checkpoints / "pre_render.json").write_text(json.dumps(pre_render), encoding="utf-8")
    (checkpoints / "audio_plan.json").write_text(json.dumps({
        "audio_path": str(audio), "duration": 1.0,
        "scene_texts": ["One beat only."], "scene_durations": [1.0],
    }), encoding="utf-8")
    return package, plan_path


def _patch_resume_harness(monkeypatch):
    monkeypatch.setattr(server_app, "_validate_remotion_edit_plan", lambda _m, _n: {
        "status": "pass", "asset_status": [], "errors": [],
    })
    monkeypatch.setattr(server_app, "_validate_remotion_master", lambda *args, **kwargs: {
        "status": "pass", "backend": "remotion",
    })
    monkeypatch.setattr(server_app, "_memory_status_snapshot", lambda: {"available_mb": 2048})


def test_resume_render_failure_rewrites_checkpoint_with_new_cause(tmp_path, monkeypatch):
    """A failed resume must not leave the first failure's checkpoint in place."""
    package, plan_path = _build_resume_package(tmp_path)
    _patch_resume_harness(monkeypatch)

    def failing_render(*, manifest, output_path, config, progress_callback, cancellation):
        raise RuntimeError("Remotion render failed with exit code 1: second boom")

    monkeypatch.setattr(
        "services.native_render_policy.render_semantic_documentary", failing_render
    )
    with pytest.raises(RuntimeError, match="second boom"):
        server_app._resume_checkpointed_remotion_render(
            {"_output_package_dir": str(package), "output_name": "saved-edit"},
            lambda *_args: None,
        )
    stored = json.loads((package / "checkpoints" / "render_failure.json").read_text(encoding="utf-8"))
    assert stored["kind"] == "render_failed"
    assert "second boom" in stored["error"]
    assert "stale first failure" not in stored["error"]
    assert stored["edit_plan_path"] == str(plan_path)
    assert stored["retryable"] is True


def test_resume_cancelled_render_records_cancel_not_render_failed(tmp_path, monkeypatch):
    """A cancelled resume is recorded as cancelled, not as a render failure."""
    from services.cancellation import JobCancelled

    package, _plan_path = _build_resume_package(tmp_path)
    _patch_resume_harness(monkeypatch)

    def cancelling_render(*, manifest, output_path, config, progress_callback, cancellation):
        raise JobCancelled("Job cancelled by user")

    monkeypatch.setattr(
        "services.native_render_policy.render_semantic_documentary", cancelling_render
    )
    with pytest.raises(JobCancelled, match="Job cancelled by user"):
        server_app._resume_checkpointed_remotion_render(
            {"_output_package_dir": str(package), "output_name": "saved-edit"},
            lambda *_args: None,
        )
    stored = json.loads((package / "checkpoints" / "render_failure.json").read_text(encoding="utf-8"))
    assert stored["kind"] == "cancelled"
    assert stored["cancelled_by"] == "user"
    assert stored["retryable"] is True


def test_resume_reuses_recorded_render_output_path(tmp_path, monkeypatch):
    """Resume renders to the failed render's own output path (section reuse)."""
    package, _plan_path = _build_resume_package(tmp_path)
    recorded = str(package / "render" / "saved-edit_remotion.mp4")
    (package / "checkpoints" / "pre_render.json").write_text(json.dumps({
        "backend": "remotion",
        "edit_plan_path": str(package / "editorial" / "remotion_edit_plan.json"),
        "render_output_path": recorded,
        "media_paths": [], "audio_path": "", "duration": 1.0,
    }), encoding="utf-8")
    _patch_resume_harness(monkeypatch)
    seen = {}

    def capturing_render(*, manifest, output_path, config, progress_callback, cancellation):
        seen["output_path"] = str(Path(output_path).resolve())
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered-remotion-video")
        return {"output_path": str(output), "renderer": "remotion-react"}

    monkeypatch.setattr(
        "services.native_render_policy.render_semantic_documentary", capturing_render
    )
    result = server_app._resume_checkpointed_remotion_render(
        {"_output_package_dir": str(package), "output_name": "saved-edit"},
        lambda *_args: None,
    )
    assert seen["output_path"] == str(Path(recorded).resolve())
    assert result["success"] is True


def test_remotion_render_output_path_is_stable(tmp_path):
    """First render and resume compute the identical output path."""
    package = tmp_path / "pkg"
    first = server_app._remotion_render_output_path(package, "My Video!")
    resume = server_app._remotion_render_output_path(package, "My Video!")
    assert first == resume
    assert first == package.resolve() / "render" / "My Video!_remotion.mp4"
