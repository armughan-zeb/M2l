import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest


def test_unbound_nearby_master_never_shortcuts_manifest_render(tmp_path, monkeypatch):
    from services import native_render_policy, media_integrity
    output = tmp_path / 'render' / 'new_remotion.mp4'
    output.parent.mkdir()
    nearby = tmp_path / 'video.mp4'
    nearby.write_bytes(b'old unrelated video' * 1000)
    monkeypatch.setattr(media_integrity, 'probe_media', lambda *a, **k: SimpleNamespace(valid=True, has_video=True, duration=60))
    monkeypatch.setenv('VIDZEO_PACKAGED', '1')
    monkeypatch.setenv('VIDZEO_NODE_PATH', str(tmp_path / 'missing-node.exe'))
    with pytest.raises(FileNotFoundError, match='bundled Node.js runtime'):
        native_render_policy.render_semantic_documentary_remotion({'duration':60}, output)
    assert not output.exists()


def test_fast_start_parent_exists_even_when_format_follows_overwrite_flag(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "missing" / "stitch" / "fast-start.remotion-in-progress"
    code = """
const fs = require('fs');
const path = require('path');
const {ensureFfmpegOutputDirectory} = require('./scripts/render-remotion-manifest');
const output = process.argv[1];
ensureFfmpegOutputDirectory(['-i', 'frames.jpeg', '-y', '-f', 'mp4', output]);
if (!fs.existsSync(path.dirname(output))) throw new Error('Final mux folder missing');
ensureFfmpegOutputDirectory(['-f', 'rawvideo', 'pipe:1']);
console.log(JSON.stringify({parentCreated: true}));
"""
    result = subprocess.run(["node", "-e", code, str(output)], cwd=root,
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["parentCreated"]
