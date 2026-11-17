"""Launch the real backend without Electron in an isolated development runtime."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))


def test_recovery_refuses_to_start_a_second_service_while_previous_pid_exists(tmp_path):
    import headless_agent
    from services import agent_access

    secure = tmp_path / 'secure'
    secure.mkdir()
    (secure / 'agent-service.json').write_text(json.dumps({
        'pid': os.getpid(), 'resource_root': str(ROOT),
    }))
    with patch.object(agent_access, '_read', side_effect=PermissionError), \
            patch.object(headless_agent, 'service_url', side_effect=ConnectionRefusedError), \
            patch.object(headless_agent.subprocess, 'Popen') as spawn:
        with pytest.raises(RuntimeError, match='previous Vidnets process still exists'):
            headless_agent.start(ROOT, tmp_path, {}, recover=True)
        spawn.assert_not_called()


def test_recovery_restarts_a_dead_service_in_the_same_runtime(tmp_path):
    import headless_agent
    from services import agent_access

    secure = tmp_path / 'secure'
    secure.mkdir()
    descriptor = secure / 'agent-service.json'
    descriptor.write_text(json.dumps({'pid': 100, 'resource_root': str(ROOT)}))
    with patch.object(agent_access, '_read', side_effect=PermissionError), \
            patch.object(headless_agent, 'service_url', side_effect=ConnectionRefusedError), \
            patch.object(headless_agent, '_json_http', return_value={
                'pipeline_build_id': headless_agent.BUILD_ID, 'resource_root': str(ROOT),
            }), patch('psutil.pid_exists', return_value=False), \
            patch.object(headless_agent.subprocess, 'Popen') as spawn:
        spawn.return_value.pid = 101
        spawn.return_value.poll.return_value = None
        result = headless_agent.start(ROOT, tmp_path, {}, recover=True)
        assert result['ready']
        assert json.loads(descriptor.read_text())['pid'] == 101
        assert spawn.call_args.kwargs['env']['VIDZEO_AGENT_RECOVER_QUEUE'] == '1'


def test_live_headless_start_and_authenticated_inbox():
    import headless_agent
    from services import agent_access, agent_bridge
    import psutil
    report_dir = ROOT / 'artifacts' / 'external_agent_review_20261009'
    report_dir.mkdir(parents=True, exist_ok=True)
    child_pid = None
    with tempfile.TemporaryDirectory(prefix='vidnets-agent-smoke-') as directory:
        runtime = Path(directory)
        with patch.dict(os.environ, {'VIDZEO_ROOT': str(runtime), 'VIDZEO_SECURE_DATA_DIR': str(runtime / 'secure'),
            'VIDZEO_TEMP_DIR': str(runtime / 'temp'), 'VIDNETS_AGENT_BRIDGE_DIR': str(runtime / 'temp' / 'agent-bridge'),
            'VIDZEO_AUTH_MODE': 'local', 'VIDZEO_PACKAGED': '0', 'LOCALAPPDATA': str(runtime / 'local-app-data')}):
            resources, root, env = headless_agent.configure(runtime)
            # Explicit isolated development fixture; production pairing never
            # enables local authentication or creates an active account.
            env['VIDZEO_AUTH_MODE'] = 'local'
            env['VIDZEO_PACKAGED'] = '0'
            with patch.object(agent_access, 'register_installation'):
                agent_access.pair('local-dev-token', {'user_id': 'local-user', 'access_state': 'active'}, {})
            key = agent_access._read()['key']
            try:
                started = headless_agent.start(resources, root, env)
                assert started['ready'] and not started.get('reused')
                child_pid = started['pid']
                url = started['url']
                capabilities = headless_agent._json_http(url + '/api/agent/capabilities', key=key)
                assert capabilities['openrouter_allowed'] is False
                assert capabilities['generation_endpoint'] == '/api/generate'
                assert headless_agent.start(resources, root, env)['reused'] is True
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(agent_bridge.agent_post, {'messages': [{'role': 'user', 'content': 'Test a grounded handoff.'}],
                        'response_format': {'type': 'json_object'}}, 10, handle='external-agent:local-user')
                    pending = []
                    deadline = time.monotonic() + 5
                    while not pending and time.monotonic() < deadline:
                        pending = headless_agent._json_http(url + '/api/agent/requests', key=key)['requests']
                        time.sleep(.05)
                    assert pending
                    rid = pending[0]['id']
                    request = headless_agent._json_http(url + '/api/agent/requests/' + rid, key=key)
                    assert request['messages'][0]['content'] == 'Test a grounded handoff.'
                    headless_agent._json_http(url + '/api/agent/requests/' + rid + '/complete', key=key, body={'content': {'approved': True}})
                    result = future.result(timeout=5).json()
                    assert result['provider'] == 'external_agent'
                (report_dir / 'headless-smoke.json').write_text(json.dumps({'ready': True, 'electron_launched': False,
                    'service_reused': True, 'authenticated_inbox_handoff': True, 'openrouter_allowed': False,
                    'production_auth_bypassed': False, 'fixture': 'isolated local development account; no generation or paid inference'}, indent=2))
            finally:
                if child_pid:
                    child = psutil.Process(child_pid)
                    assert str(resources / 'main.py') in child.cmdline()
                    child.terminate()
                    child.wait(timeout=10)
                log = runtime / 'logs' / 'agent-service.log'
                if log.is_file():
                    (report_dir / 'headless-service.log').write_bytes(log.read_bytes())
