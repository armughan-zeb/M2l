"""Execute the CMS bridge without providers, media retrieval or rendering."""
import json
import runpy
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('unlink_fails', [False, True])
def test_normal_generation_import_is_independent_of_request_cleanup(tmp_path, monkeypatch, capsys, unlink_fails):
    bridge = Path(__file__).resolve().parents[3] / 'Management' / 'YT CMS' / 'harness' / 'vidzeo-bridge.py'
    job = tmp_path / 'request.json'
    job.write_text(json.dumps({'_output_package_dir': str(tmp_path), '_cms_bridge_attempt_id': 'fixture'}))
    expected = {'success': True, 'duration': 600, 'component_manifest': {'fixture': True}}
    generate = Mock(return_value=expected)
    fake = types.ModuleType('server.app')
    fake.generate_video = generate
    monkeypatch.setitem(sys.modules, 'server.app', fake)
    monkeypatch.setattr(sys, 'argv', [str(bridge), str(job)])
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setenv('VIDZEO_ROOT', str(tmp_path))
    monkeypatch.setattr('threading.Thread.start', lambda _self: None)
    if unlink_fails:
        monkeypatch.setattr(Path, 'unlink', Mock(side_effect=PermissionError('isolated cleanup failure')))
    namespace = runpy.run_path(str(bridge))
    namespace['main']()
    generate.assert_called_once()
    assert generate.call_args.args[0]['_cms_bridge_attempt_id'] == 'fixture'
    assert job.exists() is unlink_fails
    receipt = json.loads((tmp_path / 'checkpoints' / 'cms_runner_result.json').read_text())
    assert receipt['ok'] is True and receipt['attempt'] == 'fixture'
    assert receipt['result']['duration'] == 600
    assert '__VIDZEO_ULTRA_RESULT__' in capsys.readouterr().out


def test_generation_error_preserves_attempt_receipt_after_request_consumption(tmp_path, monkeypatch, capsys):
    bridge = Path(__file__).resolve().parents[3] / 'Management' / 'YT CMS' / 'harness' / 'vidzeo-bridge.py'
    job = tmp_path / 'request.json'
    job.write_text(json.dumps({'_output_package_dir': str(tmp_path), '_cms_bridge_attempt_id': 'failed-fixture'}))
    fake = types.ModuleType('server.app')
    fake.generate_video = Mock(side_effect=RuntimeError('isolated renderer failure'))
    monkeypatch.setitem(sys.modules, 'server.app', fake)
    monkeypatch.setattr(sys, 'argv', [str(bridge), str(job)])
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setenv('VIDZEO_ROOT', str(tmp_path))
    monkeypatch.setattr('threading.Thread.start', lambda _self: None)
    with pytest.raises(RuntimeError, match='isolated renderer failure'):
        runpy.run_path(str(bridge))['main']()
    assert not job.exists()
    receipt = json.loads((tmp_path / 'checkpoints' / 'cms_runner_result.json').read_text())
    assert receipt['ok'] is False and receipt['attempt'] == 'failed-fixture'
    assert '__VIDZEO_ULTRA_ERROR__' in capsys.readouterr().out
