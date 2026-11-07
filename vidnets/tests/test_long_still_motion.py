"""Regression tests for duration-aware still-image camera motion."""

from PIL import Image

from server.services.semantic_edit_engine import build_semantic_edit_manifest
from server.services.semantic_ffmpeg_renderer import _motion_filter
from server.services import semantic_ffmpeg_renderer as renderer


def test_long_still_manifest_marks_motion_contract_even_when_animation_is_disabled():
    manifest = build_semantic_edit_manifest(
        ["The restored automobile returns to the road after decades in storage."],
        [12.0],
        topic="1961 Jaguar E-Type",
        media=[{"path": "C:/not-created-for-planning.jpg", "type": "image"}],
        config={
            "animate_all_media": False,
            "long_still_motion_seconds": 8,
        },
    )
    scene = manifest["scenes"][0]
    assert scene["design"]["long_still_motion"] is True
    assert scene["camera"]["motion"] not in {"", "static"}


def test_long_still_renderer_uses_three_camera_phases(tmp_path):
    source = tmp_path / "landscape.jpg"
    Image.new("RGB", (1600, 900), (40, 70, 100)).save(source)

    motion_filter = _motion_filter(
        "zoom_in_slight",
        12.0,
        320,
        180,
        source=str(source),
    )

    assert motion_filter is not None
    assert "zoompan" in motion_filter
    # The phase switches are represented by two nested `if(lt(on,...))`
    # branches; this is the contract that prevents a long still from reading
    # as one unchanging crop.
    assert motion_filter.count("if(lt(on\\,") >= 2


def test_focal_pan_is_bounded_after_subject_anchored_cover(tmp_path):
    source = tmp_path / "edge-subject.jpg"
    Image.new("RGB", (1600, 900), (40, 70, 100)).save(source)

    motion_filter = _motion_filter(
        "pan_right",
        3.0,
        320,
        180,
        source=str(source),
        focal_bbox=[.78, .18, .96, .62],
    )

    assert "0.87000" in motion_filter
    assert "0.40000" in motion_filter
    assert "clip(0.5+0.220" in motion_filter


def test_safe_framing_uses_centered_contain_backdrop_for_stills(monkeypatch, tmp_path):
    source = tmp_path / "portrait.jpg"
    Image.new("RGB", (700, 1200), (40, 70, 100)).save(source)
    output = tmp_path / "safe.ts"
    observed = {}

    def fake_run(command, *args, **kwargs):
        observed["command"] = list(command)
        output.write_bytes(b"x" * 8192)
        return type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr(renderer, "_run_ffmpeg_process", fake_run)
    assert renderer._render_base_scene(
        str(source), "image", "zoom_in", 2.0, str(output), str(tmp_path),
        safe_framing=True,
    )
    vf = observed["command"][observed["command"].index("-vf") + 1]
    assert "force_original_aspect_ratio=decrease" in vf
    assert "boxblur" in vf
    assert "overlay=x='(W-w)/2" in vf
    assert "y='(H-h)/2" in vf
    assert "eval=frame" in vf


def test_portrait_video_uses_contained_foreground_and_blurred_backdrop(monkeypatch, tmp_path):
    source = tmp_path / "portrait.mp4"
    source.write_bytes(b"placeholder")
    output = tmp_path / "portrait.ts"
    observed = {}

    def fake_run(command, *args, **kwargs):
        observed["command"] = list(command)
        output.write_bytes(b"x" * 8192)
        return type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr(renderer, "_probe_media_dimensions", lambda _path: (720, 1280))
    monkeypatch.setattr(renderer, "_run_ffmpeg_process", fake_run)
    monkeypatch.setattr(renderer, "_ACTIVE_FAST_RENDER", True)
    assert renderer._render_base_scene(
        str(source), "video", "pan_left", 2.0, str(output), str(tmp_path),
    )
    vf = observed["command"][observed["command"].index("-vf") + 1]
    assert "boxblur" in vf
    assert "force_original_aspect_ratio=decrease" in vf
    assert "overlay=x='" in vf
