"""No paid requests: provider isolation, stream completion and account security."""
import ast
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from config import normalize_generation_config
from services.chatgpt_accounts import ChatGPTAccounts, ChatGPTError, PLAN_SCOPE, ISSUER
from services.chatgpt_inference import responses_payload, consume_stream
from services.generation_cost_policy import apply_generation_cost_policy
from services.job_context import job_scope
from services.provider_selection import ACCOUNT_PREFIX, normalize_provider_config


def response(value, status=200):
    import requests
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(value).encode()
    return result


def stream(*events):
    result = Mock()
    result.headers = {'x-request-id': 'test-request'}
    lines = []
    for event in events:
        lines.extend([('data: ' + json.dumps(event)).encode(), b''])
    result.iter_lines.return_value = iter(lines)
    return result


class ProviderTests(unittest.TestCase):
    def test_subscription_provider_is_disabled_in_current_app(self):
        for cfg in ({'llm_provider': 'chatgpt'},):
            with self.assertRaises(ValueError):
                normalize_generation_config(cfg)
        self.assertEqual(normalize_generation_config({})['llm_provider'], 'openrouter')

    def test_all_text_and_vision_requests_route_to_chatgpt(self):
        from services.openrouter_llm import OpenRouterLLM
        from services.visual_intelligence.vision_client import QwenVisionClient
        data = {'model': 'gpt-6-luna', 'choices': [{'message': {'content': '{"ok":true}'}, 'finish_reason': 'stop'}]}
        with job_scope({'llm_provider': 'chatgpt', 'chatgpt_account_id': 'saved-account', 'cost_profile': 'efficient'}), \
                patch('services.chatgpt_inference.chatgpt_post', return_value=response(data)) as chatgpt, \
                patch('requests.post') as openrouter:
            text = OpenRouterLLM('legacy-router-key', 'expensive', fallback_models=['expensive-2'])
            self.assertEqual(text.model_chain, ('gpt-6-luna',))
            self.assertEqual(text.chat('Return JSON', json_mode=True, max_attempts=1), '{"ok":true}')
            vision = QwenVisionClient('legacy-router-key', 'expensive', max_attempts=1)
            self.assertEqual(vision._post([{'type': 'text', 'text': 'Look'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]), '{"ok":true}')
            self.assertEqual(chatgpt.call_count, 2)
            self.assertEqual(chatgpt.call_args.args[0], 'saved-account')
            openrouter.assert_not_called()

    def test_openrouter_remains_exclusive_and_efficient(self):
        from services.bounded_http import provider_post
        post = Mock(return_value=response({'choices': []}))
        with job_scope({'llm_provider': 'openrouter', 'cost_profile': 'efficient'}), patch('services.chatgpt_inference.chatgpt_post') as other:
            provider_post(post, 'https://openrouter.ai/api/v1/chat/completions',
                          headers={'Authorization': 'Bearer router-secret'}, json={'model': 'expensive', 'models': ['expensive']}, timeout=2)
            other.assert_not_called()
            self.assertEqual(post.call_args.kwargs['json']['model'], 'qwen/qwen3.5-flash-02-23')
            with self.assertRaises(ValueError):
                provider_post(post, 'https://openrouter.ai/api/v1/chat/completions',
                              headers={'Authorization': 'Bearer ' + ACCOUNT_PREFIX + 'saved-account'}, json={}, timeout=2)

    def test_json_and_vision_contract_uses_supported_responses_fields(self):
        payload = responses_payload({'model': 'qwen', 'max_tokens': 500, 'temperature': .2,
            'messages': [{'role': 'system', 'content': 'You are a director.'}, {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Check this image'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'plan', 'schema': {'type': 'object'}, 'strict': True}}})
        self.assertEqual(payload['model'], 'gpt-6-luna')
        self.assertIs(payload['store'], False)
        self.assertIs(payload['stream'], True)
        self.assertNotIn('temperature', payload)
        self.assertNotIn('max_output_tokens', payload)
        self.assertNotIn('max_tokens', payload)
        self.assertEqual(payload['input'][0]['content'][1]['type'], 'input_image')
        self.assertEqual(payload['text']['format']['name'], 'plan')

    def test_success_requires_terminal_completed_event(self):
        result = consume_stream(stream({'type': 'response.output_text.delta', 'delta': 'ok'},
            {'type': 'response.completed', 'response': {'id': 'resp-test', 'usage': {'input_tokens': 3, 'output_tokens': 1}}}), time.monotonic() + 5)
        self.assertEqual(result['choices'][0]['message']['content'], 'ok')
        self.assertEqual(result['billing'], 'user_chatgpt_plan')
        self.assertNotIn('cost', result['usage'])
        for event in [{'type': 'response.incomplete'}, {'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}}]:
            with self.assertRaises(ChatGPTError):
                consume_stream(stream({'type': 'response.output_text.delta', 'delta': 'partial'}, event), time.monotonic() + 5)
        with self.assertRaises(ChatGPTError):
            consume_stream(stream({'type': 'response.output_text.delta', 'delta': 'partial'}), time.monotonic() + 5)

    def test_vision_queue_does_not_reuse_another_provider_client(self):
        from services.visual_intelligence.queue import get_visual_queue
        with job_scope({'attempt_id': 'test-chatgpt-queue', 'llm_provider': 'chatgpt', 'chatgpt_account_id': 'account-a'}):
            chat_queue = get_visual_queue()
            self.assertEqual(chat_queue.get_client()._client.provider, 'chatgpt')
        with job_scope({'attempt_id': 'test-openrouter-queue', 'llm_provider': 'openrouter', 'llm_key': 'router-key'}):
            router_queue = get_visual_queue()
            self.assertEqual(router_queue.get_client()._client.provider, 'openrouter')
        self.assertIsNot(chat_queue, router_queue)


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI credential vault')
class AccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = ChatGPTAccounts(Path(self.directory.name) / 'accounts.dpapi')
        self.env = patch.dict(os.environ, {'VIDNETS_CHATGPT_OPEN_SOURCE_CLIENT': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.directory.cleanup)

    def seed(self, account='account-a', expires=3600):
        with self.service._locked():
            data = self.service._read()
            data['accounts'][account] = {'client_id': 'oaiapp_saved', 'subject': account, 'issuer': ISSUER,
                'email': 'same@example.test', 'label': 'Saved ' + account, 'access_token': 'sensitive-access-' + account,
                'refresh_token': 'sensitive-refresh-' + account, 'id_token': 'sensitive-id-' + account,
                'scopes': [PLAN_SCOPE], 'expires_at': time.time() + expires, 'state': 'ready'}
            data['active_account_id'] = account
            self.service._write(data)

    def flow(self, account='', state='expected'):
        self.service._flows['test-flow'] = {'state': state, 'nonce': 'test-nonce', 'verifier': 'test-verifier',
            'status': 'pending', 'expires_at': time.time() + 600, 'client_id': 'oaiapp_saved' if account else 'dynamic_agent_client',
            'redirect_uri': 'http://127.0.0.1:12345/auth/callback', 'host_id': 'stable-host', 'account_id': account}

    def test_credentials_encrypted_and_status_has_no_tokens(self):
        self.seed()
        data = self.service.path.read_bytes()
        self.assertNotIn(b'sensitive-access', data)
        public = json.dumps(self.service.status())
        for value in ['sensitive-access', 'sensitive-refresh', 'sensitive-id', 'oaiapp_saved']:
            self.assertNotIn(value, public)

    def test_state_and_client_id_checked_before_exchange(self):
        self.flow()
        with patch('requests.post') as exchange:
            with self.assertRaises(ChatGPTError):
                self.service.complete_sign_in('test-flow', {'state': ['wrong'], 'code': ['code']})
            self.assertEqual(self.service.flow_status('test-flow')['status'], 'pending')
            with self.assertRaises(ChatGPTError):
                self.service.complete_sign_in('test-flow', {'state': ['expected'], 'code': ['code']})
            exchange.assert_not_called()

    def test_oauth_account_registration_saved_after_validation(self):
        self.flow()
        token = {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'id_token': 'new-id',
                 'token_type': 'Bearer', 'expires_in': 3600, 'scope': PLAN_SCOPE}
        with patch('requests.post', return_value=response(token)) as exchange, \
             patch.object(self.service, '_validate_id_token', return_value={'sub': 'new-identity', 'iss': ISSUER, 'email': 'same@example.test'}) as verify:
            self.service.complete_sign_in('test-flow', {'state': ['expected'], 'code': ['one-time-code'], 'client_id': ['oaiapp_new']})
            verify.assert_called_once_with('new-id', 'oaiapp_new', 'test-nonce')
            self.assertEqual(exchange.call_args.kwargs['data']['client_id'], 'oaiapp_new')
            self.assertEqual(self.service.status()['accounts'][0]['state'], 'ready')
            with self.assertRaises(ChatGPTError):
                self.service.complete_sign_in('test-flow', {'state': ['expected'], 'code': ['replay']})

    def test_returning_identity_cannot_overwrite_saved_account(self):
        self.seed()
        self.flow('account-a')
        with patch('requests.post', return_value=response({'id_token': 'different-id'})), \
             patch.object(self.service, '_validate_id_token', return_value={'sub': 'wrong-identity', 'iss': ISSUER}):
            with self.assertRaises(ChatGPTError):
                self.service.complete_sign_in('test-flow', {'state': ['expected'], 'code': ['code']})
        self.assertEqual(self.service.access_token('account-a'), 'sensitive-access-account-a')

    def test_same_email_accounts_are_separate_and_switch_manually(self):
        self.seed('account-a')
        self.seed('account-b')
        self.assertEqual(len(self.service.status()['accounts']), 2)
        self.service.select('account-a')
        self.service.record_error('account-a', ChatGPTError('limit', 'subscription_sharing_usage_limit_exceeded', 429))
        with patch('requests.post') as network:
            with self.assertRaises(ChatGPTError):
                self.service.access_token('account-a')
            network.assert_not_called()
        self.assertEqual(self.service.active_account_id(), 'account-a')
        self.service.select('account-b')
        self.assertEqual(self.service.access_token('account-b'), 'sensitive-access-account-b')

    def test_refreshes_serialized_and_rotated_atomically(self):
        self.seed(expires=1)
        token = {'access_token': 'rotated-access', 'refresh_token': 'rotated-refresh',
                 'token_type': 'Bearer', 'expires_in': 3600}
        with patch('requests.post', return_value=response(token)) as refresh:
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(self.service.access_token, ['account-a'] * 4))
            self.assertEqual(results, ['rotated-access'] * 4)
            self.assertEqual(refresh.call_count, 1)
            with self.service._locked():
                self.assertEqual(self.service._read()['accounts']['account-a']['refresh_token'], 'rotated-refresh')

    def test_terminal_refresh_erases_tokens_but_keeps_registration(self):
        self.seed(expires=1)
        with patch('requests.post', return_value=response({'error': 'invalid_grant'}, 400)):
            with self.assertRaises(ChatGPTError):
                self.service.access_token('account-a')
        row = self.service.status()['accounts'][0]
        self.assertEqual(row['state'], 'signed_out')
        with self.service._locked():
            self.assertEqual(self.service._read()['accounts']['account-a']['client_id'], 'oaiapp_saved')

    def test_plan_permission_required_even_after_identity_login(self):
        self.seed()
        with self.service._locked():
            data = self.service._read()
            data['accounts']['account-a']['scopes'] = ['openid', 'email']
            self.service._write(data)
        with patch('requests.post') as network:
            with self.assertRaises(ChatGPTError):
                self.service.access_token('account-a')
            network.assert_not_called()

    def test_private_app_requires_approval_without_using_owner_credentials(self):
        with patch.dict(os.environ, {'VIDNETS_CHATGPT_OPEN_SOURCE_CLIENT': '0', 'VIDNETS_CHATGPT_PLAN_ACCESS_APPROVED': '0', 'VIDNETS_CHATGPT_CLIENT_ID': ''}):
            with patch('webbrowser.open') as browser:
                with self.assertRaises(ChatGPTError):
                    self.service.start_sign_in()
                browser.assert_not_called()

    def test_missing_cheap_model_never_upgrades(self):
        self.seed()
        with patch('requests.get', return_value=response({'models': [{'slug': 'expensive', 'visibility': 'list'}]})):
            with self.assertRaisesRegex(ChatGPTError, 'GPT-6 Luna is not available'):
                self.service.model_catalog('account-a')

    def test_disconnect_clears_local_tokens_when_remote_unavailable(self):
        self.seed()
        import requests
        with patch('requests.get', side_effect=requests.ConnectionError('offline')):
            result = self.service.disconnect('account-a')
        self.assertFalse(result['revocation_confirmed'])
        self.assertEqual(result['accounts'][0]['state'], 'signed_out')

    def test_id_token_signature_issuer_audience_expiry_and_nonce(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {'iss': ISSUER, 'sub': 'verified-user', 'aud': 'oaiapp_saved',
                  'iat': int(time.time()), 'exp': int(time.time()) + 600, 'nonce': 'expected-nonce'}
        public = Mock(key=key.public_key())
        with patch('jwt.PyJWKClient') as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value = public
            token = jwt.encode(claims, key, algorithm='RS256')
            self.assertEqual(self.service._validate_id_token(token, 'oaiapp_saved', 'expected-nonce')['sub'], 'verified-user')
            for update in [{'iss': 'https://evil.test'}, {'aud': 'another-client'}, {'exp': int(time.time()) - 60}, {'nonce': 'wrong-nonce'}]:
                invalid = jwt.encode({**claims, **update}, key, algorithm='RS256')
                with self.assertRaises(Exception):
                    self.service._validate_id_token(invalid, 'oaiapp_saved', 'expected-nonce')
            other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            with self.assertRaises(Exception):
                self.service._validate_id_token(jwt.encode(claims, other_key, algorithm='RS256'), 'oaiapp_saved', 'expected-nonce')

    def test_pkce_loopback_sign_in_uses_stable_host_and_issued_returning_client(self):
        from urllib.parse import urlparse, parse_qs
        self.seed()
        with patch('webbrowser.open', return_value=True) as browser:
            started = self.service.start_sign_in('account-a')
            parsed = urlparse(browser.call_args.args[0])
            params = parse_qs(parsed.query)
            self.assertEqual(parsed.hostname, 'auth.openai.com')
            self.assertEqual(params['client_id'], ['oaiapp_saved'])
            self.assertEqual(params['code_challenge_method'], ['S256'])
            self.assertTrue(params['redirect_uri'][0].startswith('http://127.0.0.1:'))
            self.assertIn(PLAN_SCOPE, params['scope'][0])
            self.assertNotIn('agent_name_hint', params)
            self.assertNotIn('auth_url', started)
            self.service.cancel_sign_in(started['flow_id'])

    def test_account_catalog_and_inference_send_only_own_bearer_to_openai(self):
        from services.chatgpt_inference import chatgpt_post
        self.seed()
        model_response = response({'models': [{'slug': 'gpt-6-luna', 'display_name': 'GPT-6 Luna', 'visibility': 'list'}]})
        inference_response = stream({'type': 'response.output_text.delta', 'delta': 'done'},
                                    {'type': 'response.completed', 'response': {'id': 'resp-test'}})
        inference_response.status_code = 200
        with patch('services.chatgpt_inference.accounts', self.service), \
             patch('requests.get', return_value=model_response) as models, \
             patch('requests.post', return_value=inference_response) as inference:
            result = chatgpt_post('account-a', {'messages': [{'role': 'user', 'content': 'Plan'}]}, 5)
            self.assertEqual(models.call_args.args[0], 'https://api.openai.com/v1/models')
            self.assertEqual(inference.call_args.args[0], 'https://api.openai.com/v1/responses')
            self.assertEqual(inference.call_args.kwargs['headers']['Authorization'], 'Bearer sensitive-access-account-a')
            self.assertEqual(result.json()['choices'][0]['message']['content'], 'done')

    def test_mid_stream_limit_pauses_account_without_cross_provider_retry(self):
        from services.chatgpt_inference import chatgpt_post
        self.seed()
        bad_stream = stream({'type': 'response.output_text.delta', 'delta': 'partial'},
                            {'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}})
        bad_stream.status_code = 200
        with patch('services.chatgpt_inference.accounts', self.service), \
             patch.object(self.service, 'ensure_model'), patch('requests.post', return_value=bad_stream) as network:
            with self.assertRaises(ChatGPTError):
                chatgpt_post('account-a', {'messages': []}, 5)
            self.assertEqual(network.call_count, 1)
        self.assertEqual(self.service.status()['accounts'][0]['state'], 'limit_reached')


class EndpointTests(unittest.TestCase):
    def test_subscription_routes_and_ui_are_disabled(self):
        source = (ROOT / 'server/app.py').read_text(encoding='utf-8')
        self.assertNotIn("@app.route('/api/chatgpt/", source)
        html = (ROOT / 'src/index.html').read_text(encoding='utf-8')
        self.assertNotIn('chatgptAccountPanel', html)

    def test_query_chain_supports_external_agent_without_paid_key(self):
        tree = ast.parse((ROOT / 'server/app.py').read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_resolve_query_llm_settings', '_build_query_llm_candidates'}]
        namespace = {'os': os}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'query-route', 'exec'), namespace)
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'router-environment-secret'}):
            candidates = namespace['_build_query_llm_candidates']({'llm_provider': 'external_agent', 'agent_owner_id': 'alice'})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['provider'], 'external_agent')
        self.assertEqual(candidates[0]['api_key'], 'external-agent:alice')


if __name__ == '__main__':
    unittest.main()
