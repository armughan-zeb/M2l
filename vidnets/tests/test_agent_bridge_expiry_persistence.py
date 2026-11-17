import json
import time

from services import agent_bridge


def test_expiry_is_persisted_and_existing_answer_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDNETS_AGENT_BRIDGE_DIR', str(tmp_path))
    for index, answered in enumerate((False, True), start=1):
        request_id = str(index) * 32
        directory = tmp_path / request_id
        directory.mkdir()
        row = {'id': request_id, 'owner_id': 'owner', 'state': 'pending',
               'expires_at': time.time() - 1}
        path = directory / 'request.json'
        path.write_text(json.dumps(row), encoding='utf-8')
        if answered:
            (directory / 'response.json').write_text('{"content":"ok"}', encoding='utf-8')
        expected = 'answered' if answered else 'expired'
        assert agent_bridge.read_request(request_id, 'owner')['state'] == expected
        assert json.loads(path.read_text(encoding='utf-8'))['state'] == expected
