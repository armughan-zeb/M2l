"""No paid inference: real inbox handoff, local authentication and API boundary."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from functools import wraps
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from services import agent_access, agent_bridge
from services.job_context import job_scope
from services.provider_selection import normalize_provider_config, OPENROUTER_MODEL
from services.generation_cost_policy import apply_generation_cost_policy


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {'VIDNETS_AGENT_BRIDGE_DIR': str(self.root / 'bridge'),
            'VIDZEO_ROOT': str(self.root), 'VIDZEO_SECURE_DATA_DIR': str(self.root / 'secure'),
            'VIDZEO_RESOURCE_ROOT': str(self.root), 'VIDZEO_PACKAGED': '0', 'VIDZEO_AUTH_MODE': 'supabase'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.directory.cleanup)

    def wait_request(self, owner='alice'):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            requests = agent_bridge.pending_requests(owner)
            if requests:
                return agent_bridge.read_request(requests[0]['id'], owner)
            time.sleep(.01)
        self.fail('No agent inference request was published.')

    def test_manual_rejects_uncatalogued_models_and_pins_saved_selection(self):
        for profile in ('efficient', 'custom'):
            with self.assertRaises(ValueError):
                normalize_provider_config(apply_generation_cost_policy({'cost_profile': profile,
                    'llm_model': 'expensive', 'qwen_vision_model': 'expensive',
                    'scene_brain_fallback_models': ['expensive']}))
        # The saved catalog selection is admitted unchanged and pinned to every role.
        cfg = normalize_provider_config(apply_generation_cost_policy({'cost_profile': 'custom',
            'llm_model': OPENROUTER_MODEL, 'qwen_vision_model': OPENROUTER_MODEL}))
        for name in ('llm_model', 'director_model', 'scene_brain_model', 'query_llm_model',
                     'vision_model', 'qwen_vision_model', 'research_model', 'composition_model'):
            self.assertEqual(cfg[name], OPENROUTER_MODEL)
        self.assertEqual(cfg['scene_brain_fallback_models'], [OPENROUTER_MODEL])

    def test_external_normalization_removes_paid_and_subscription_credentials(self):
        cfg = normalize_provider_config({'llm_provider': 'external_agent', 'agent_owner_id': 'alice',
            'openrouter_api_key': 'paid-secret', 'openrouterApiKey': 'other-secret',
            'chatgpt_access_token': 'token', 'tts_model': 'openai/gpt-audio-mini'})
        self.assertEqual(cfg['openrouter_api_key'], 'external-agent:alice')
        self.assertEqual(cfg['tts_model'], 'audiogram')
        self.assertEqual(cfg['vision_model'], 'external-agent')
        self.assertNotIn('openrouterApiKey', cfg)
        self.assertNotIn('chatgpt_access_token', cfg)
        with self.assertRaises(ValueError):
            normalize_provider_config({'llm_provider': 'chatgpt'})

    def test_text_and_vision_clients_complete_real_inbox_without_network(self):
        from services.openrouter_llm import OpenRouterLLM
        from services.visual_intelligence.vision_client import QwenVisionClient
        def call(kind):
            with job_scope({'llm_provider': 'external_agent', 'agent_owner_id': 'alice',
                            'job_id': 'test', 'cost_profile': 'efficient'}):
                if kind == 'text':
                    client = OpenRouterLLM('ignored-paid-key', 'expensive', fallback_models=['expensive-2'])
                    self.assertEqual(client.model_chain, ('external-agent',))
                    return client.chat('Return JSON', json_mode=True, max_attempts=1, total_timeout_seconds=4)
                client = QwenVisionClient('ignored-paid-key', 'expensive', max_attempts=1, timeout_seconds=4)
                return client._post([{'type': 'text', 'text': 'Inspect'}, {'type': 'image_url',
                    'image_url': {'url': 'data:image/png;base64,AA=='}}], json_mode=True)
        with patch('requests.post') as network, ThreadPoolExecutor(max_workers=1) as pool:
            for kind in ('text', 'vision'):
                future = pool.submit(call, kind)
                row = self.wait_request()
                self.assertEqual(row['kind'], kind)
                if kind == 'vision':
                    image = row['messages'][-1]['content'][1]
                    self.assertEqual(image['type'], 'local_image')
                    self.assertEqual(Path(image['path']).read_bytes(), b'\0')
                agent_bridge.complete_request(row['id'], 'alice', {'ok': True})
                self.assertEqual(json.loads(future.result(timeout=5)), {'ok': True})
            network.assert_not_called()

    def test_schema_and_ownership_reject_bad_answer_then_accept_valid_answer(self):
        payload = {'messages': [{'role': 'user', 'content': 'Choose grounded copy.'}], 'response_format':
                   {'type': 'json_schema', 'json_schema': {'schema': {'type': 'object',
                    'properties': {'label': {'type': 'string'}}, 'required': ['label'], 'additionalProperties': False}}}}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(agent_bridge.agent_post, payload, 4, handle='external-agent:alice')
            row = self.wait_request()
            with self.assertRaises(PermissionError):
                agent_bridge.complete_request(row['id'], 'bob', {'label': 'other'})
            with self.assertRaises(ValueError):
                agent_bridge.complete_request(row['id'], 'alice', {'wrong': True})
            self.assertEqual(agent_bridge.read_request(row['id'], 'alice')['state'], 'pending')
            agent_bridge.complete_request(row['id'], 'alice', {'label': 'Approved'})
            self.assertEqual(future.result(timeout=5).json()['provider'], 'external_agent')
            with self.assertRaises(ValueError):
                agent_bridge.complete_request(row['id'], 'alice', {'label': 'Duplicate'})

    def test_timeout_is_bounded_and_late_answer_is_rejected(self):
        start = time.monotonic()
        with patch.object(agent_bridge, 'remaining_seconds', return_value=.05):
            with self.assertRaisesRegex(agent_bridge.AgentBridgeError, r'Muse request [a-f0-9]{32}'):
                agent_bridge.agent_post({'messages': []}, .08, handle='external-agent:alice')
        self.assertLess(time.monotonic() - start, 1)
        row = json.loads(next((self.root / 'bridge').glob('*/request.json')).read_text())
        self.assertEqual(row['state'], 'expired')
        self.assertEqual(agent_bridge.pending_requests('alice'), [])
        with self.assertRaises(ValueError):
            agent_bridge.complete_request(row['id'], 'alice', 'late')

    def test_agent_requests_get_a_minimum_answer_window(self):
        # Small direct-provider timeouts do not apply to the Muse inbox.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(agent_bridge.agent_post, {'messages': []}, 20,
                                 operation='vidnets_response', handle='external-agent:alice')
            row = self.wait_request()
            self.assertAlmostEqual(row['expires_at'] - row['created_at'], 120, places=1)
            agent_bridge.complete_request(row['id'], 'alice', {'ok': True})
            reply = future.result(timeout=5).json()
            self.assertEqual(json.loads(reply['choices'][0]['message']['content']), {'ok': True})

    def test_vision_requests_get_the_same_minimum_answer_window(self):
        # Vision and text share the same bounded request lifetime.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(agent_bridge.agent_post, {'messages': []}, 20,
                                 operation='vidnets_vision', kind='vision',
                                 handle='external-agent:alice')
            row = self.wait_request()
            self.assertEqual(row['kind'], 'vision')
            self.assertAlmostEqual(row['expires_at'] - row['created_at'], 120, places=1)
            agent_bridge.complete_request(row['id'], 'alice', {'ok': True})
            reply = future.result(timeout=5).json()
            self.assertEqual(reply['provider'], 'external_agent')

    def test_legacy_environment_cannot_extend_the_answer_window(self):
        # Old installations may still export a longer wall.
        with patch.dict(os.environ, {'VIDZEO_AGENT_MIN_WALL_SECONDS': '900'}):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(agent_bridge.agent_post, {'messages': []}, 20,
                                     operation='vidnets_response', handle='external-agent:alice')
                row = self.wait_request()
                self.assertAlmostEqual(row['expires_at'] - row['created_at'], 120, places=1)
                agent_bridge.complete_request(row['id'], 'alice', {'ok': True})
                future.result(timeout=5)

    def test_missing_owner_and_paid_voice_never_use_network(self):
        from services.bounded_http import provider_post
        network = Mock()
        with job_scope({'llm_provider': 'external_agent'}):
            with self.assertRaises(agent_bridge.AgentBridgeError):
                provider_post(network, 'https://openrouter.ai/api/v1/chat/completions',
                    headers={'Authorization': 'Bearer paid-secret'}, json={}, timeout=1)
            with self.assertRaises(ValueError):
                provider_post(network, 'https://openrouter.ai/api/v1/audio/speech', headers={}, json={}, timeout=1, usage_kind='tts')
        network.assert_not_called()

    def test_external_clients_and_safety_vision_need_no_openrouter_key(self):
        from services.openrouter_llm import OpenRouterLLM
        from services.visual_intelligence.vision_client import QwenVisionClient
        from services.visual_intelligence.safety_override import _resolve_openrouter_key
        with job_scope({'llm_provider': 'external_agent', 'agent_owner_id': 'alice'}):
            self.assertEqual(OpenRouterLLM('').api_key, 'external-agent:alice')
            self.assertEqual(QwenVisionClient('').api_key, 'external-agent:alice')
            self.assertEqual(_resolve_openrouter_key(), 'external-agent:alice')

    def test_manual_custom_client_cannot_upgrade_model_or_accept_agent_handle(self):
        from services.bounded_http import provider_post
        import requests
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"choices":[]}'
        network = Mock(return_value=response)
        with job_scope({'llm_provider': 'openrouter', 'cost_profile': 'custom'}):
            with self.assertRaises(ValueError):
                provider_post(network, 'https://openrouter.ai/api/v1/chat/completions', headers={},
                    json={'model': 'expensive', 'models': ['expensive-2']}, timeout=1)
            network.assert_not_called()
            # An omitted model is admitted only as the saved catalog selection.
            provider_post(network, 'https://openrouter.ai/api/v1/chat/completions', headers={},
                json={}, timeout=1)
            self.assertEqual(network.call_args.kwargs['json']['model'], OPENROUTER_MODEL)
            self.assertNotIn('models', network.call_args.kwargs['json'])
            with self.assertRaises(ValueError):
                provider_post(network, 'url', headers={'Authorization': 'Bearer external-agent:alice'}, json={}, timeout=1)

    def seed_access(self):
        with patch.object(agent_access, 'register_installation'):
            agent_access.pair('vidnets-session', {'user_id': 'alice', 'access_state': 'active'},
                              {'device_fingerprint': 'paired-device'}, 'renew-token')
        return agent_access._read()['key']

    def test_dpapi_storage_scopes_owner_status_and_revocation(self):
        key = self.seed_access()
        encrypted = agent_access.credential_path().read_bytes()
        for secret in ('vidnets-session', key, 'renew-token'):
            self.assertNotIn(secret.encode(), encrypted)
        self.assertEqual(agent_access.authorized_credentials(key, 'POST', '/api/generate')['owner_id'], 'alice')
        self.assertFalse(agent_access.status('bob')['enabled'])
        self.assertNotIn('key', agent_access.status())
        for method, path in [('GET', '/api/admin/users'), ('POST', '/api/agent/pair'),
            ('GET', '/api/agent/requests/../../secure'), ('GET', '/api/queue')]:
            with self.assertRaises(PermissionError):
                agent_access.authorized_credentials(key, method, path)
        with self.assertRaises(PermissionError):
            agent_access.revoke('bob')
        agent_access.revoke('alice')
        with self.assertRaises(PermissionError):
            agent_access.authorized_credentials(key, 'POST', '/api/generate')

    def test_pairing_requires_active_owner_and_replaces_previous_key(self):
        with self.assertRaises(PermissionError):
            agent_access.pair('token', {'user_id': 'alice', 'access_state': 'inactive'}, {})
        old = self.seed_access()
        current = self.seed_access()
        self.assertNotEqual(old, current)
        with self.assertRaises(PermissionError):
            agent_access.authorized_credentials(old, 'POST', '/api/generate')

    def test_file_render_slot_serializes_and_releases(self):
        from services.remotion_render_gate import _ProcessRenderSlot
        with patch.dict(os.environ, {'VIDNETS_RENDER_LOCK_FILE': str(self.root / 'render.lock')}):
            first, second = _ProcessRenderSlot(), _ProcessRenderSlot()
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:
                if not first.stream.closed:
                    first.close()
                second.close()

    def test_headless_configuration_has_writable_queue_and_does_not_enable_bypass(self):
        import headless_agent
        resources, root, env = headless_agent.configure(self.root)
        self.assertEqual(env['VIDZEO_QUEUE_FILE'], str(self.root / 'server' / 'queue.json'))
        self.assertEqual(env['VIDZEO_AUTH_MODE'], 'supabase')
        self.assertEqual(env['VIDZEO_NO_BROWSER'], '1')
        self.assertNotIn('vna_', json.dumps(env.get('VIDZEO_DESKTOP_CONNECTOR_TOKEN', '')))


class EndpointTests(AgentTests):
    # Reuse fixtures without rerunning inherited cases in this class.
    def make_api(self):
        from flask import Flask, request, jsonify
        from services.auth_service import AuthServiceError
        from config import normalize_generation_config
        wanted = {'_json_error', '_extract_bearer_token', '_extract_device_context', '_get_authenticated_state',
            '_is_local_request', '_is_trusted_browser_origin', 'require_authenticated_user', 'require_active_access',
            'api_agent_access', 'api_agent_bridge', 'api_generate', '_apply_env_api_key_defaults',
            '_parse_duration_to_seconds', '_estimate_script_duration_seconds'}
        tree = ast.parse((ROOT / 'server/app.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        app = Flask(__name__)
        active = {'user_id': 'alice', 'access_state': 'active', 'plan': 'pro'}
        self.sync = Mock(return_value=active)
        self.enqueue = Mock(side_effect=lambda data, user: {'success': True, 'item': {'id': 'job-test', 'config': data}})
        self.qm = Mock()
        namespace = {'app': app, 'request': request, 'jsonify': jsonify, 'os': os, 'json': json,
            'urllib': __import__('urllib'), 'wraps': wraps, 'AuthServiceError': AuthServiceError,
            'sync_authenticated_user': self.sync, 'secrets': secrets, 'Path': Path,
            're': re, 'math': math,
            'OpenRouterScenePlanningError': type('OpenRouterScenePlanningError', (Exception,), {}),
            'TEMP_DIR': self.root, 'OUTPUT_DIR': self.root, 'normalize_generation_config': normalize_generation_config,
            '_attach_plan_constraints': lambda data, user: data, '_coerce_media_source_from_weights': lambda data: data,
            '_enforce_generation_plan_limits': lambda data, user: data, '_validate_media_request_config': lambda data: None,
            '_prepare_video_output_package': lambda data, name: None, '_generation_response': lambda data, status=200: (jsonify(data), status),
            'get_queue_manager': lambda: self.qm, '_enqueue_generation_request': self.enqueue,
            '_queue_generation_callback': Mock(),
            'generate_video': lambda data: {'success': True, 'model': data['llm_model']}}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'agent-api-boundary', 'exec'), namespace)
        return app.test_client()

    def test_agent_generate_pins_owner_and_routes_to_same_queue_api(self):
        from services import export_entitlements
        key = self.seed_access()
        client = self.make_api()
        with patch.object(export_entitlements, 'reserve_generation_export', lambda *args, **kwargs: {'allowed': True}), \
                patch.object(export_entitlements, 'finish_generation_export', lambda *args, **kwargs: None):
            response = client.post('/api/generate', json={'script': 'Grounded script', 'agent_owner_id': 'spoofed',
                'llm_model': 'expensive', 'openrouter_api_key': 'paid-secret'}, headers={'Authorization': 'Bearer ' + key})
            self.assertEqual(response.status_code, 202, response.get_json())
            cfg = self.enqueue.call_args.args[0]
            self.assertEqual(cfg['agent_owner_id'], 'alice')
            self.assertEqual(cfg['llm_provider'], 'external_agent')
            self.assertEqual(cfg['openrouter_api_key'], 'external-agent:alice')
            self.assertEqual(cfg['tts_model'], 'audiogram')
            self.assertEqual(self.sync.call_args.kwargs['device_fingerprint'], 'paired-device')
            denied = client.post('/api/generate', json={'script': 'script', 'llm_provider': 'openrouter'}, headers={'Authorization': 'Bearer ' + key})
            self.assertEqual(denied.status_code, 400)

    def test_manual_cannot_spoof_agent_or_paid_models(self):
        client = self.make_api()
        headers = {'Authorization': 'Bearer user-session'}
        denied = client.post('/api/generate', json={'llm_provider': 'external_agent', 'agent_owner_id': 'alice'}, headers=headers)
        self.assertEqual(denied.status_code, 400)
        uncatalogued = client.post('/api/generate', json={'script': 'script', 'llm_model': 'expensive', 'cost_profile': 'custom'}, headers=headers)
        self.assertEqual(uncatalogued.status_code, 400, uncatalogued.get_json())

    def test_key_remote_bad_origin_inactive_and_wrong_identity_are_rejected(self):
        key = self.seed_access()
        client = self.make_api()
        headers = {'Authorization': 'Bearer ' + key}
        path = '/api/agent/capabilities'
        self.assertEqual(client.get(path, headers=headers).status_code, 200)
        self.assertEqual(client.get(path, headers=headers, environ_overrides={'REMOTE_ADDR': '203.0.113.1'}).status_code, 403)
        self.assertEqual(client.get(path, headers={**headers, 'Origin': 'https://evil.test'}).status_code, 403)
        self.sync.return_value = {'user_id': 'alice', 'access_state': 'inactive'}
        self.assertEqual(client.get(path, headers=headers).status_code, 403)
        self.sync.return_value = {'user_id': 'bob', 'access_state': 'active'}
        self.assertEqual(client.get(path, headers=headers).status_code, 403)
        self.assertEqual(client.post('/api/agent/pair', json={}, headers=headers).status_code, 403)

    def test_job_status_cannot_read_another_owner(self):
        key = self.seed_access()
        client = self.make_api()
        headers = {'Authorization': 'Bearer ' + key}
        self.qm.get_item_snapshot.return_value = {'id': 'job', 'config': {'agent_owner_id': 'bob'}}
        self.assertEqual(client.get('/api/agent/jobs/job', headers=headers).status_code, 404)
        self.qm.get_item_snapshot.return_value = {'id': 'job', 'config': {'agent_owner_id': 'alice'}}
        self.assertEqual(client.get('/api/agent/jobs/job', headers=headers).status_code, 200)

    def test_pairing_endpoint_returns_no_key_or_session_and_disables_old_routes(self):
        client = self.make_api()
        with patch.object(agent_access, 'register_installation'):
            response = client.post('/api/agent/pair', json={'refresh_token': 'renew-secret'}, headers={'Authorization': 'Bearer session-secret'})
        self.assertEqual(response.status_code, 200, response.get_json())
        for secret in ('session-secret', 'renew-secret', 'vna_'):
            self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertEqual(client.get('/api/chatgpt/status').status_code, 404)

    def test_retry_is_owner_scoped_and_reuses_existing_queue_worker(self):
        key = self.seed_access()
        client = self.make_api()
        headers = {'Authorization': 'Bearer ' + key}
        self.qm.get_item_snapshot.return_value = {'id': 'job', 'config': {'agent_owner_id': 'bob'}}
        self.assertEqual(client.post('/api/agent/jobs/job/retry', json={}, headers=headers).status_code, 404)
        self.qm.resume_item.assert_not_called()
        self.qm.get_item_snapshot.return_value = {'id': 'job', 'config': {'agent_owner_id': 'alice'}}
        self.qm.resume_item.return_value = False
        self.assertEqual(client.post('/api/agent/jobs/job/retry', json={}, headers=headers).status_code, 409)
        self.qm.resume_item.return_value = True
        self.assertEqual(client.post('/api/agent/jobs/job/retry', json={}, headers=headers).status_code, 200)
        self.qm.start_worker.assert_called_once()


if __name__ == '__main__':
    unittest.main()
