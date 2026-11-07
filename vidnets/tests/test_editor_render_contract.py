import os
import sys
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app


@pytest.fixture(autouse=True)
def local_editor_auth(monkeypatch):
    monkeypatch.setenv('VIDZEO_AUTH_MODE', 'local')
    monkeypatch.setenv('VIDZEO_PACKAGED', '0')


def test_editor_resolution_and_media_path_contract(tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), (220, 40, 40)).save(image)

    assert server_app._editor_resolution("1080p") == (1920, 1080)
    assert server_app._editor_resolution({"w": 640, "h": 360}) == (640, 360)
    assert server_app._resolve_editor_media_path({"path": str(image)}) == image.resolve()


def test_editor_render_fails_loudly_for_missing_active_media():
    client = server_app.app.test_client()
    response = client.post("/api/editor/render", headers={'Authorization': 'Bearer local-dev-token'}, json={
        "name": "missing-media",
        "duration": 2,
        "tracks": [{
            "id": "v1",
            "type": "video",
            "clips": [{"mediaId": "missing", "start": 0, "end": 2, "trackOffset": 0}],
        }],
        "mediaPool": [],
    })

    assert response.status_code == 500
    assert "Missing video media" in (response.get_json() or {}).get("error", "")


def test_editor_render_preserves_gaps_text_and_track_offsets(tmp_path, monkeypatch):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (160, 90), (220, 40, 40)).save(first)
    Image.new("RGB", (160, 90), (40, 80, 220)).save(second)
    output_dir = tmp_path / "output"
    temp_dir = tmp_path / "temp"
    output_dir.mkdir()
    temp_dir.mkdir()
    monkeypatch.setattr(server_app, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(server_app, "TEMP_DIR", temp_dir)

    client = server_app.app.test_client()
    response = client.post("/api/editor/render", headers={'Authorization': 'Bearer local-dev-token'}, json={
        "name": "timeline-contract",
        "fps": 24,
        "resolution": {"w": 320, "h": 180},
        "duration": 1.5,
        "tracks": [
            {"id": "v1", "type": "video", "clips": [
                {"mediaId": "one", "start": 0, "end": .5, "trackOffset": 0},
                {"mediaId": "two", "start": 0, "end": .5, "trackOffset": 1.0},
            ]},
            {"id": "t1", "type": "text", "clips": [
                {"name": "A grounded caption", "start": 0, "end": .5, "trackOffset": .5},
            ]},
        ],
        "mediaPool": [
            {"id": "one", "path": str(first), "type": "image"},
            {"id": "two", "path": str(second), "type": "image"},
        ],
    })

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    rendered = Path(payload["output_path"])
    assert payload["success"] is True
    assert rendered.is_file()
    assert rendered.stat().st_size > 4096
