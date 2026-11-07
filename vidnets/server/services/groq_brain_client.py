"""Enhanced Groq LLM client with JSON schema support, retry, and truncation handling."""
import json
import re
import time
import os
import requests

GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'

DEFAULT_BRAIN_MODEL = 'qwen/qwen3.6-27b'
FALLBACK_BRAIN_MODELS = [
    'qwen/qwen3.6-27b',
    'openai/gpt-oss-20b',
    'openai/gpt-oss-120b',
]


JSON_MODE_UNSUPPORTED_MODELS = {
    'qwen/qwen3.6-27b',
    'openai/gpt-oss-20b',
    'openai/gpt-oss-120b',
}

RETIRED_BRAIN_MODELS = {
    'llama-3.3-70b-versatile',
    'llama-3.1-8b-instant',
}



RETRYABLE_GROQ_ERRORS = (
    'Groq returned empty content',
    'Groq returned no choices',
    'Failed to extract valid JSON from response',
    'JSON response did not match',
)


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 600) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _extract_json_text(text: str) -> str | None:
    candidate = str(text or '').strip()
    if not candidate:
        return None
    if "```" in candidate:
        parts = candidate.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{") or part.startswith("["):
                candidate = part
                break
    ranges = []
    start_object = candidate.find("{")
    end_object = candidate.rfind("}")
    start_array = candidate.find("[")
    end_array = candidate.rfind("]")
    if start_object != -1 and end_object > start_object:
        ranges.append(candidate[start_object:end_object + 1])
    if start_array != -1 and end_array > start_array:
        ranges.append(candidate[start_array:end_array + 1])
    for raw_json in ranges:
        cleaned = re.sub(r',\s*([}\]])', r'\1', raw_json)
        cleaned = re.sub(r'[\x00-\x1f]+', ' ', cleaned)
        try:
            json.loads(cleaned)
            return cleaned
        except Exception:
            continue
    return None


def _validate_json_against_schema(instance: dict, schema: dict) -> bool:
    """Basic JSON schema validation — checks required fields exist and are correct types."""
    if not isinstance(instance, dict):
        return False
    required = schema.get("required", [])
    for field in required:
        if field not in instance:
            return False
        props = schema.get("properties", {})
        prop = props.get(field, {})
        prop_type = prop.get("type")
        if prop_type == "array" and not isinstance(instance[field], list):
            return False
        if prop_type == "object" and not isinstance(instance[field], dict):
            return False
        if prop_type == "string" and not isinstance(instance[field], str):
            return False
        if prop_type == "integer" and not isinstance(instance[field], int):
            return False
    return True


class GroqBrainClient:
    """Enhanced Groq LLM client for brain operations.

    Supports JSON mode with schema validation, automatic retry with
    exponential backoff, truncation handling, and model fallback chain.
    """

    def __init__(self, api_key: str, model: str = None):
        if not api_key:
            raise ValueError('Groq API key is required')
        self.api_key = api_key
        selected = str(model or DEFAULT_BRAIN_MODEL).strip()
        self.model = DEFAULT_BRAIN_MODEL if selected in RETIRED_BRAIN_MODELS else selected

    def chat(self, user_prompt: str, system_prompt: str = None,
             temperature: float = 0.3, max_tokens: int = 2000,
             json_mode: bool = False, json_schema: dict = None,
             timeout_seconds: float = None, total_timeout_seconds: float = None,
             max_attempts: int = None) -> str:
        timeout_seconds = float(
            timeout_seconds
            or os.environ.get('VIDZEO_GROQ_TIMEOUT_SECONDS')
            or os.environ.get('VIDZEO_BRAIN_TIMEOUT_SECONDS')
            or 180.0
        )
        explicit_attempt_budget = max_attempts is not None
        if max_attempts is None:
            max_attempts = _env_int('VIDZEO_GROQ_MAX_ATTEMPTS', 4, minimum=2, maximum=8)
        else:
            max_attempts = max(1, min(8, int(max_attempts)))
        total_deadline = None
        if total_timeout_seconds is not None:
            total_deadline = time.monotonic() + max(0.25, float(total_timeout_seconds))

        def _remaining_seconds():
            if total_deadline is None:
                return None
            return total_deadline - time.monotonic()

        def _bounded_sleep(seconds):
            remaining = _remaining_seconds()
            if remaining is not None and remaining <= 0:
                return False
            time.sleep(min(float(seconds), max(0.0, remaining)) if remaining is not None else float(seconds))
            remaining = _remaining_seconds()
            return remaining is None or remaining > 0
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        if json_schema:
            user_prompt = f"{user_prompt}\n\nYou MUST return valid JSON exactly matching this schema:\n{json.dumps(json_schema, indent=2)}"
        messages.append({'role': 'user', 'content': user_prompt})

        effective_max_tokens = max_tokens
        if json_mode and effective_max_tokens < 2000:
            effective_max_tokens = 2000

        # A model can be retired independently of the API endpoint. Always
        # retain a short provider-supported fallback chain, even when a stage
        # deliberately allows only one request attempt per model.
        models_to_try = [self.model]
        models_to_try.extend(m for m in FALLBACK_BRAIN_MODELS if m != self.model)
        last_error = None
        requests_used = 0

        for model_idx, model in enumerate(models_to_try):
            if explicit_attempt_budget and requests_used >= max_attempts:
                break
            # Groq's GPT-OSS endpoint rejects response_format=json_object. The
            # schema is still included in the prompt, and the response parser
            # validates the returned JSON below.
            use_json_mode = bool(json_mode) and model not in JSON_MODE_UNSUPPORTED_MODELS
            payload = {
                'model': model,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': effective_max_tokens,
            }
            if use_json_mode:
                payload['response_format'] = {'type': 'json_object'}

            truncation_retried = False
            for attempt in range(max_attempts):
                if explicit_attempt_budget and requests_used >= max_attempts:
                    break
                remaining = _remaining_seconds()
                if remaining is not None and remaining <= 0:
                    last_error = f'Groq total timeout exceeded ({float(total_timeout_seconds):.1f}s)'
                    break
                try:
                    request_timeout = min(timeout_seconds, max(0.25, remaining)) if remaining is not None else timeout_seconds
                    requests_used += 1
                    response = requests.post(
                        GROQ_API_URL,
                        headers=headers,
                        json=payload,
                        timeout=request_timeout
                    )
                    if response.status_code == 401:
                        raise ValueError('Invalid Groq API key')
                    if response.status_code == 400:
                        if use_json_mode:
                            # Some deployments reject response_format even when
                            # the model normally advertises JSON support.
                            use_json_mode = False
                            payload.pop('response_format', None)
                            last_error = f"Model '{model}' rejected json_mode; retrying with schema-prompt JSON"
                            continue
                        # Bad request — invalid model name or payload. Skip to next model immediately.
                        last_error = f"Model '{model}' rejected (400): invalid model or payload"
                        break
                    if response.status_code == 429:
                        last_error = "Groq rate limited (429)"
                        if attempt + 1 >= max_attempts:
                            break
                        retry_wait = (2 ** attempt) * 2
                        if not _bounded_sleep(retry_wait):
                            last_error = f'Groq total timeout exceeded ({float(total_timeout_seconds):.1f}s)'
                            break
                        continue
                    if response.status_code == 404:
                        last_error = f"Groq model '{model}' is unavailable (404)"
                        break
                    response.raise_for_status()
                    result = response.json()
                    if 'error' in result:
                        raise ValueError(f"Groq API error: {result['error']}")
                    choices = result.get('choices', [])
                    if not choices:
                        last_error = f"Groq returned no choices: {result}"
                        if attempt + 1 < max_attempts:
                            if not _bounded_sleep((2 ** attempt) * 1.5):
                                break
                            continue
                        raise ValueError(last_error)
                    message = choices[0].get('message', {}) or {}
                    raw_content = message.get('content') or ''
                    reasoning_text = (
                        message.get('reasoning_content')
                        or message.get('reasoning')
                        or ''
                    )
                    content = raw_content or reasoning_text or ''
                    finish_reason = choices[0].get('finish_reason', '')
                    if not raw_content and finish_reason == 'length' and not truncation_retried:
                        truncation_retried = True
                        effective_max_tokens = max(effective_max_tokens * 2, 4000)
                        payload['max_tokens'] = effective_max_tokens
                        last_error = (
                            f"Groq response truncated (reasoning consumed tokens); "
                            f"retrying with max_tokens={effective_max_tokens}"
                        )
                        if not _bounded_sleep(0.5):
                            break
                        continue
                    if not content:
                        last_error = 'Groq returned empty content'
                        if attempt + 1 < max_attempts:
                            messages.append({
                                'role': 'user',
                                'content': 'Return the requested JSON now. Do not return an empty response.'
                            })
                            if not _bounded_sleep((2 ** attempt) * 1.5):
                                break
                            continue
                        raise ValueError(last_error)

                    if finish_reason == 'length' and (use_json_mode or json_schema) and not truncation_retried:
                        truncation_retried = True
                        effective_max_tokens = max(effective_max_tokens * 2, 4000)
                        payload['max_tokens'] = effective_max_tokens
                        if not _bounded_sleep(0.5):
                            break
                        continue

                    if use_json_mode:
                        extracted = _extract_json_text(content)

                        if extracted:
                            parsed = json.loads(extracted)
                            if json_schema and not _validate_json_against_schema(parsed, json_schema):
                                last_error = 'JSON response did not match the required schema'
                                if attempt + 1 < max_attempts:
                                    messages.append({
                                        'role': 'user',
                                        'content': 'Your JSON response did not match the required schema. Return valid JSON only matching the schema exactly.'
                                    })
                                    continue
                            return extracted
                        last_error = 'Failed to extract valid JSON from response'
                        if attempt + 1 < max_attempts:
                            messages.append({
                                'role': 'user',
                                'content': 'Return raw valid JSON only, no markdown, no prose.'
                            })
                            continue
                        raise ValueError(last_error)

                    if json_schema and not use_json_mode:
                        extracted = _extract_json_text(str(content))
                        if extracted:
                            parsed = json.loads(extracted)
                            if _validate_json_against_schema(parsed, json_schema):
                                return extracted
                            last_error = 'JSON response did not match the required schema'
                        else:
                            last_error = 'Failed to extract valid JSON from response'
                        if attempt + 1 < max_attempts:
                            messages.append({
                                'role': 'user',
                                'content': 'Return one complete valid JSON object matching the schema. No markdown or prose.'
                            })
                            continue
                        raise ValueError(last_error)
                    return str(content)

                except requests.RequestException as e:
                    last_error = str(e)
                    if attempt + 1 < max_attempts:
                        if not _bounded_sleep((2 ** attempt) * 2):
                            break
                except ValueError as e:
                    last_error = str(e)
                    if any(marker in last_error for marker in RETRYABLE_GROQ_ERRORS) and attempt + 1 < max_attempts:
                        if not _bounded_sleep((2 ** attempt) * 1.5):
                            break
                        continue
                    break

            remaining = _remaining_seconds()
            if (
                (explicit_attempt_budget and requests_used >= max_attempts)
                or (remaining is not None and remaining <= 0)
            ):
                break

        raise ValueError(last_error or 'All Groq models failed')
