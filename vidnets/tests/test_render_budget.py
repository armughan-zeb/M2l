import math
import inspect
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.services import semantic_ffmpeg_renderer as renderer


def test_ffmpeg_timeout_is_clamped_to_render_wall(monkeypatch):
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_DEADLINE", 105.0)
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_BUDGET_SECONDS", 210.0)
    monkeypatch.setattr(renderer.time, "monotonic", lambda: 100.0)

    assert renderer._budgeted_timeout(300) == pytest.approx(5.0)


def test_expired_render_wall_raises_typed_budget_error(monkeypatch):
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_DEADLINE", 99.0)
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_BUDGET_SECONDS", 210.0)
    monkeypatch.setattr(renderer.time, "monotonic", lambda: 100.0)

    with pytest.raises(renderer.SemanticRenderBudgetExceeded):
        renderer._budgeted_timeout(300)


def test_expired_render_wall_never_raises_when_budget_exempt(monkeypatch):
    """Last-chance delivery recovery must never die to the shrinking wall."""
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_DEADLINE", 99.0)
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_BUDGET_SECONDS", 85.0)
    monkeypatch.setattr(renderer.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(renderer, "_ACTIVE_CANCELLATION", SimpleNamespace(raise_if_cancelled=lambda: None))

    calls = {}

    def fake_cancel(command, **kwargs):
        calls["timeout"] = kwargs.get("timeout")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    import importlib

    patched = False
    for module_name in ("services.cancellation", "server.services.cancellation"):
        try:
            import importlib
            module = importlib.import_module(module_name)
            monkeypatch.setattr(module, "run_cancellable_process", fake_cancel)
            patched = True
        except Exception:
            pass
    assert patched
    result = renderer._run_ffmpeg_process(
        ["ffmpeg", "-version"], timeout=120, budget_exempt=True
    )
    assert result.returncode == 0
    assert calls["timeout"] == 120


def test_budget_exempt_runs_remain_budgeted_without_floor(monkeypatch):
    """Normal (non-exempt) calls still clamp to the wall; only recovery is exempt."""
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_DEADLINE", 103.0)
    monkeypatch.setattr(renderer, "_ACTIVE_RENDER_BUDGET_SECONDS", 210.0)
    monkeypatch.setattr(renderer.time, "monotonic", lambda: 100.0)

    assert renderer._budgeted_timeout(300) == pytest.approx(3.0)


def test_semantic_final_mux_has_bounded_budget_exemption():
    source = inspect.getsource(renderer._render_semantic_documentary_ffmpeg_impl)
    assert "timeout=90, budget_exempt=True" in source
    assert "_ACTIVE_FAST_RENDER = True" in source


def test_straight_recovery_still_budgeted_by_default_wall(monkeypatch, tmp_path):
    """The straight recovery accepts its wall, but per-scene encodes are exempt."""
    media = []
    for index in range(3):
        path = tmp_path / f"scene_{index}.jpg"
        path.write_bytes(b"image")
        media.append(str(path))
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"audio")
    output = tmp_path / "delivery2.mp4"
    commands = []
    timeouts = []

    def fake_run(command, *args, **kwargs):
        commands.append(list(command))
        timeouts.append(kwargs.get("timeout"))
        destination = Path(command[-1])
        if destination.suffix == ".ts":
            destination.write_bytes(b"x" * 2048)
        else:
            destination.write_bytes(b"x" * 8192)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(renderer, "_run_ffmpeg_process", fake_run)
    renderer.render_straight_media_ffmpeg(
        media,
        [12.0, 12.0, 12.0],
        str(audio),
        output,
        width=1920,
        height=1080,
        budget_seconds=60,
    )

    kwarg_overloads = [t for t in timeouts if t is not None]
    assert len(kwarg_overloads) >= 3
    assert all(
        isinstance(t, (int, float))
        for t in kwarg_overloads
    )


def test_long_form_wrapper_enables_bounded_fast_profile(monkeypatch, tmp_path):
    observed = {}

    def fake_impl(**kwargs):
        observed["config"] = kwargs["config"]
        observed["fast"] = renderer._ACTIVE_FAST_RENDER
        observed["threads"] = renderer._ACTIVE_ENCODE_THREADS
        observed["remaining"] = renderer._remaining_render_budget()
        return {"output_path": str(tmp_path / "fake.mp4")}

    monkeypatch.setattr(renderer, "_render_semantic_documentary_ffmpeg_impl", fake_impl)
    result = renderer.render_semantic_documentary_ffmpeg(
        {"scenes": [{} for _ in range(74)]},
        tmp_path / "fake.mp4",
        {
            "semantic_render_budget_seconds": 210,
            "semantic_render_concurrency": 4,
        },
    )

    assert result["output_path"].endswith("fake.mp4")
    assert observed["config"]["semantic_render_concurrency"] == 4
    assert observed["fast"] is True
    assert observed["threads"] >= 1
    assert 205 <= observed["remaining"] <= 210
    assert renderer._ACTIVE_RENDER_DEADLINE is None


def test_duration_based_long_form_enables_bounded_fast_profile(monkeypatch, tmp_path):
    """Long timelines with fewer, longer scenes still use the fast profile."""
    observed = {}

    def fake_impl(**kwargs):
        observed["fast"] = renderer._ACTIVE_FAST_RENDER
        return {"output_path": str(tmp_path / "duration-fast.mp4")}

    monkeypatch.setattr(renderer, "_render_semantic_documentary_ffmpeg_impl", fake_impl)
    renderer.render_semantic_documentary_ffmpeg(
        {"duration": 300, "scenes": [{} for _ in range(30)]},
        tmp_path / "duration-fast.mp4",
        {"semantic_render_budget_seconds": 720, "semantic_render_concurrency": 4},
    )
    assert observed["fast"] is True


def test_explicit_long_job_enables_fast_profile_for_two_minute_timeline(monkeypatch, tmp_path):
    observed = {}

    def fake_impl(**kwargs):
        observed["fast"] = renderer._ACTIVE_FAST_RENDER
        return {"output_path": str(tmp_path / "long-job-fast.mp4")}

    monkeypatch.setattr(renderer, "_render_semantic_documentary_ffmpeg_impl", fake_impl)
    renderer.render_semantic_documentary_ffmpeg(
        {"duration": 142.5, "scenes": [{} for _ in range(23)]},
        tmp_path / "long-job-fast.mp4",
        {
            "job_type": "long",
            "semantic_render_budget_seconds": 720,
            "semantic_render_concurrency": 4,
        },
    )
    assert observed["fast"] is True


def test_long_form_fast_profile_skips_companion_passes_by_duration(monkeypatch):
    monkeypatch.delenv("VIDZEO_FAST_RENDER_SCENE_THRESHOLD", raising=False)

    assert renderer._should_skip_decorative_passes(
        fast_render_mode=True,
        n_scenes=30,
        planned_scene_duration=300.0,
        budget_forced_fast=False,
    ) is True


def test_short_fast_profile_keeps_companion_passes(monkeypatch):
    monkeypatch.delenv("VIDZEO_FAST_RENDER_SCENE_THRESHOLD", raising=False)

    assert renderer._should_skip_decorative_passes(
        fast_render_mode=True,
        n_scenes=10,
        planned_scene_duration=90.0,
        budget_forced_fast=False,
    ) is False


def test_straight_recovery_encodes_each_scene_once_then_stream_copies(
    monkeypatch, tmp_path
):
    media = []
    for index in range(3):
        path = tmp_path / f"scene_{index}.jpg"
        path.write_bytes(b"image")
        media.append(str(path))
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"audio")
    output = tmp_path / "delivery.mp4"
    commands = []

    def fake_run(command, *args, **kwargs):
        commands.append(list(command))
        destination = Path(command[-1])
        if destination.suffix == ".ts":
            destination.write_bytes(b"x" * 2048)
        else:
            destination.write_bytes(b"x" * 8192)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(renderer, "_run_ffmpeg_process", fake_run)
    result = renderer.render_straight_media_ffmpeg(
        media,
        [2.5, 2.5, 2.5],
        str(audio),
        output,
        width=1920,
        height=1080,
        budget_seconds=85,
    )

    scene_commands = [cmd for cmd in commands if "mpegts" in cmd]
    assert len(scene_commands) == 3
    assert all("ultrafast" in cmd for cmd in scene_commands)
    assert all(cmd[cmd.index("-threads") + 1] == "1" for cmd in scene_commands)
    # Still-image recovery preserves the complete subject instead of
    # reverting to the old edge-to-edge cover crop.
    assert all("boxblur" in " ".join(cmd) for cmd in scene_commands)
    final_command = commands[-1]
    assert final_command[final_command.index("-c:v") + 1] == "copy"
    assert "concat" in final_command
    assert Path(result) == output
    assert output.stat().st_size == 8192


def test_short_video_scene_is_padded_to_exact_narration_slot(monkeypatch, tmp_path):
    source = tmp_path / "short.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "scene.mp4"
    observed = {}

    def fake_run(command, *args, **kwargs):
        observed["command"] = list(command)
        output.write_bytes(b"x" * 8192)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    # Deliberately below the old 80% threshold. This used to return False and
    # escape into an unpadded fallback, shrinking every such narration slot.
    monkeypatch.setattr(renderer, "_probe_duration_seconds", lambda _path: 0.6)
    monkeypatch.setattr(renderer, "_run_ffmpeg_process", fake_run)

    assert renderer._render_base_scene(
        str(source),
        "video",
        "static",
        3.0,
        str(output),
        str(tmp_path),
    )
    vf = observed["command"][observed["command"].index("-vf") + 1]
    assert "tpad=stop_mode=clone" in vf
    assert "trim=end_frame=90" in vf
    assert observed["command"][observed["command"].index("-frames:v") + 1] == "90"


def test_scene_frame_contracts_preserve_total_without_per_scene_rounding(monkeypatch):
    monkeypatch.setattr(renderer, "FPS", 30)
    scenes = [{"duration": 1.001} for _ in range(77)]

    contracts = renderer._scene_frame_contracts(scenes)

    assert all(frames > 0 for frames in contracts)
    assert sum(contracts) == math.ceil(sum(s["duration"] for s in scenes) * 30)
    assert sum(contracts) < sum(math.ceil(s["duration"] * 30) for s in scenes)


def test_real_mixed_short_video_and_image_concat_keeps_full_timeline(
    monkeypatch, tmp_path
):
    """Regression: short downloads and stills cannot shrink a long narration."""
    ffmpeg = renderer._ffmpeg()
    try:
        subprocess.run(
            [ffmpeg, "-version"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("FFmpeg is unavailable")

    monkeypatch.setattr(renderer, "WIDTH", 160)
    monkeypatch.setattr(renderer, "HEIGHT", 90)
    monkeypatch.setattr(renderer, "FPS", 10)
    monkeypatch.setattr(renderer, "_NVENC_AVAILABLE", False)
    renderer._probe_duration_seconds.cache_clear()

    short_video = tmp_path / "short.mp4"
    generated = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=160x90:r=10:d=0.6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(short_video),
        ],
        capture_output=True,
        timeout=20,
    )
    if generated.returncode != 0:
        pytest.skip("FFmpeg test fixture could not be encoded")

    still = tmp_path / "still.png"
    overlay = tmp_path / "overlay.png"
    renderer.Image.new("RGB", (160, 90), (70, 25, 25)).save(still)
    renderer.Image.new("RGBA", (160, 90), (255, 255, 255, 24)).save(overlay)

    scenes = [{"duration": 2.21}, {"duration": 1.34}, {"duration": 2.02}]
    frame_contracts = renderer._scene_frame_contracts(scenes)
    durations = [frames / renderer.FPS for frames in frame_contracts]
    sources = [(short_video, "video"), (still, "image"), (short_video, "video")]
    segments = []
    for index, ((source, media_type), duration) in enumerate(zip(sources, durations)):
        segment = tmp_path / f"scene_{index:02d}.ts"
        assert renderer._render_base_scene(
            str(source), media_type, "static", duration, str(segment), str(tmp_path)
        )
        if index == 1:
            composited = tmp_path / "scene_01_composited.ts"
            assert renderer._composite_overlay(
                str(segment), str(overlay), str(composited), duration
            )
            segment = composited
        exact = renderer._ensure_scene_segment_duration(
            str(segment), duration, str(tmp_path / f"exact_{index:02d}.ts")
        )
        assert exact
        assert renderer._probe_duration_seconds(exact) + 0.05 >= duration
        segments.append(exact)

    concat_list = tmp_path / "concat.txt"
    concat_list.write_text(
        "".join(
            f"file '{segment}'\nduration {duration:.9f}\n"
            for segment, duration in zip(segments, durations)
        ),
        encoding="utf-8",
    )
    concat_output = tmp_path / "concat.ts"
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(concat_output),
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    expected = sum(frame_contracts) / renderer.FPS
    assert renderer._probe_duration_seconds(str(concat_output)) + 0.05 >= expected
