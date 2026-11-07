from __future__ import annotations

from pathlib import Path

import pytest

from server.services import semantic_ffmpeg_renderer as renderer


def test_native_renderer_concatenates_multiple_verified_shots(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    renderer.Image.new("RGB", (320, 180), (30, 70, 120)).save(first)
    renderer.Image.new("RGB", (320, 180), (120, 70, 30)).save(second)
    output = tmp_path / "multi-shot.mp4"

    manifest = {
        "version": 3,
        "width": 320,
        "height": 180,
        "fps": 30,
        "duration": 2.0,
        "scenes": [{
            "id": "scene-1",
            "scene_id": "scene-1",
            "scene_index": 0,
            "start": 0.0,
            "duration": 2.0,
            "narration": "A verified multi-shot scene.",
            "media": {
                "path": str(first),
                "type": "image",
                "shots": [
                    {
                        "path": str(first),
                        "media_type": "image",
                        "shot_start": 0.0,
                        "shot_end": 1.0,
                        "candidate_id": "web:first",
                        "verification": "verified",
                    },
                    {
                        "path": str(second),
                        "media_type": "image",
                        "shot_start": 1.0,
                        "shot_end": 2.0,
                        "candidate_id": "web:second",
                        "verification": "verified",
                    },
                ],
            },
            "graphic": {"template_id": "full-bleed", "props": {}},
            "camera": {"motion": "static"},
            "transition": {"template_id": "cut", "duration": 0.2},
        }],
        "audio": {},
        "theme": {},
        "captions": {"enabled": False},
    }

    result = renderer.render_semantic_documentary_ffmpeg(
        manifest,
        output,
        config={
            "semantic_render_concurrency": 1,
            "semantic_render_threads_per_job": 1,
            "semantic_render_budget_seconds": 120,
            "allow_emergency_media": False,
        },
    )

    assert Path(result["output_path"]).is_file()
    assert renderer._probe_duration_seconds(str(output)) == pytest.approx(2.0, abs=0.08)
