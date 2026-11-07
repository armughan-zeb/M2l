"""Adapt existing text/vision callers to the official streaming Responses route."""
import json
import time
import requests
from .chatgpt_accounts import ChatGPTError, RESOURCE, accounts, _provider_error
from .job_context import check_job_active, remaining_seconds


def responses_payload(payload):
    instructions, inputs = [], []
    for message in payload.get('messages', []):
        role = message.get('role', 'user')
        content = message.get('content', '')
        if role in {'system', 'developer'}:
            instructions.append(content if isinstance(content, str) else json.dumps(content))
            continue
        parts = []
        if isinstance(content, str):
            parts = [{'type': 'input_text', 'text': content}]
        else:
            for part in content:
                if part.get('type') == 'text':
                    parts.append({'type': 'input_text', 'text': str(part.get('text', ''))})
                elif part.get('type') == 'image_url':
                    image = part.get('image_url', {})
                    url = image.get('url') if isinstance(image, dict) else image
                    parts.append({'type': 'input_image', 'image_url': url,
                                  'detail': image.get('detail', 'auto') if isinstance(image, dict) else 'auto'})
                else:
                    raise ChatGPTError('This ChatGPT flow only supports text and image inputs.', 'unsupported_input', 400)
        inputs.append({'role': role, 'content': parts})
    result = {'model': 'gpt-6-luna', 'input': inputs, 'store': False, 'stream': True}
    if instructions:
        result['instructions'] = '\n\n'.join(instructions)
    output = payload.get('response_format') or {}
    if output.get('type') == 'json_schema':
        schema = output['json_schema']
        result['text'] = {'format': {'type': 'json_schema', 'name': schema.get('name', 'vidnets_response'),
                                   'schema': schema['schema'], 'strict': schema.get('strict', True)}}
    elif output.get('type') == 'json_object':
        result['text'] = {'format': {'type': 'json_object'}}
        result['instructions'] = result.get('instructions', '') + '\nReturn valid JSON only.'
    # max_output_tokens and sampling controls are unsupported on this route.
    # Keep outputs compact using the contract/prompt, never forward those fields.
    if payload.get('max_tokens'):
        result['instructions'] = result.get('instructions', '') + f'\nKeep your answer concise, approximately within {int(payload["max_tokens"])} tokens, while satisfying the complete schema.'
    return result


def _stream_error(event, request_id):
    error = event.get('error') or (event.get('response') or {}).get('error') or {}
    code = str(error.get('code') or 'inference_failed') if isinstance(error, dict) else 'inference_failed'
    status = 429 if code == 'subscription_sharing_usage_limit_exceeded' else 503 if code.endswith('_unavailable') else 400
    return ChatGPTError('ChatGPT inference failed. ' + code, code, status, request_id)


def consume_stream(response, deadline):
    """Partial text is never a successful answer; require the completed event."""
    text, terminal, completed = [], None, False
    request_id = response.headers.get('x-request-id', '')
    event_data = []
    size = 0

    def dispatch(lines):
        nonlocal terminal, completed, size
        if not lines:
            return
        raw = '\n'.join(lines)
        if raw == '[DONE]':
            return
        try:
            event = json.loads(raw)
        except ValueError:
            raise ChatGPTError('ChatGPT sent an invalid response event.', 'invalid_stream', 502) from None
        kind = event.get('type')
        if kind in {'response.failed', 'error'}:
            raise _stream_error(event, request_id)
        if kind == 'response.incomplete':
            raise ChatGPTError('ChatGPT response was incomplete; no partial plan was applied.', 'incomplete_response', 502, request_id)
        if kind == 'response.output_text.delta':
            value = str(event.get('delta', ''))
            size += len(value)
            if size > 2_000_000:
                raise ChatGPTError('ChatGPT response exceeded the output safety limit.', 'output_too_large', 502)
            text.append(value)
        if kind == 'response.completed':
            terminal, completed = event.get('response') or {}, True

    try:
        for raw in response.iter_lines(chunk_size=1024):
            check_job_active()
            if time.monotonic() >= deadline:
                raise requests.Timeout('ChatGPT streaming response exceeded its wall budget.')
            line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
            if not line:
                dispatch(event_data)
                event_data = []
                if completed:
                    break
            elif line.startswith('data:'):
                event_data.append(line[5:].lstrip())
        if event_data and not completed:
            dispatch(event_data)
        if not completed:
            raise ChatGPTError('ChatGPT stream ended before completion. No partial answer was used.', 'interrupted_stream', 502, request_id)
        if not text:
            text = [part.get('text', '') for item in terminal.get('output', [])
                    if item.get('type') == 'message' for part in item.get('content', []) if part.get('type') == 'output_text']
        content = ''.join(text)
        if not content.strip():
            raise ChatGPTError('ChatGPT returned no usable text.', 'empty_response', 502, request_id)
        usage = terminal.get('usage') or {}
        return {'id': terminal.get('id', ''), 'model': terminal.get('model') or 'gpt-6-luna',
                'provider': 'chatgpt', 'billing': 'user_chatgpt_plan',
                'choices': [{'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': usage.get('input_tokens', 0),
                          'completion_tokens': usage.get('output_tokens', 0),
                          'total_tokens': usage.get('total_tokens', 0)}}
    finally:
        response.close()


def chatgpt_post(account_id, payload, timeout):
    started = time.monotonic()
    deadline = started + min(float(timeout), remaining_seconds(float(timeout)))
    accounts.ensure_model(account_id, 'gpt-6-luna')
    token = accounts.access_token(account_id)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise requests.Timeout('ChatGPT account verification exceeded the request budget.')
    body = responses_payload(payload)
    for attempt in range(2):
        check_job_active()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout('ChatGPT response exceeded the request budget.')
        response = requests.post(RESOURCE + '/responses', headers={'Authorization': 'Bearer ' + token,
                                'Content-Type': 'application/json', 'Accept': 'text/event-stream'},
                                json=body, stream=True, timeout=(min(10, remaining), remaining))
        try:
            if response.status_code != 200:
                raise _provider_error(response)
            value = consume_stream(response, deadline)
            break
        except ChatGPTError as error:
            # One bounded retry for transient service/stream errors; never
            # change the account/model, repeat limits or repeat invalid bodies.
            accounts.record_error(account_id, error)
            if not error.retryable or attempt == 1 or deadline - time.monotonic() < 1:
                raise
            time.sleep(min(.5, max(0, deadline - time.monotonic())))
        finally:
            response.close()
    adapted = requests.Response()
    adapted.status_code = 200
    adapted.headers['Content-Type'] = 'application/json'
    adapted._content = json.dumps(value).encode('utf-8')
    return adapted
