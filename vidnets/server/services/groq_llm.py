"""Groq LLM client for Vidzeo query brain."""
import json
import re
import time
import requests

GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'
DEFAULT_GROQ_MODEL = 'qwen/qwen3.6-27b'
RETIRED_GROQ_MODELS = {'llama-3.3-70b-versatile', 'llama-3.1-8b-instant'}
JSON_MODE_UNSUPPORTED_MODELS = {
    'qwen/qwen3.6-27b', 'openai/gpt-oss-20b', 'openai/gpt-oss-120b'
}


class GroqLLM:
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError('Groq API key is required')
        self.api_key = api_key
        selected = str(model or DEFAULT_GROQ_MODEL).strip()
        self.model = DEFAULT_GROQ_MODEL if selected in RETIRED_GROQ_MODELS else selected

    def chat(self, user_prompt: str, system_prompt: str = None,
             temperature: float = 0.7, max_tokens: int = 1000,
             json_mode: bool = False) -> str:
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': user_prompt})
        use_json_mode = bool(json_mode) and self.model not in JSON_MODE_UNSUPPORTED_MODELS
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens
        }
        if use_json_mode:
            payload['response_format'] = {'type': 'json_object'}

        last_error = None
        for attempt in range(3):
            try:
                response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=120)
                if response.status_code == 401:
                    raise ValueError('Invalid Groq API key')
                if response.status_code == 429:
                    time.sleep((2 ** attempt) * 2)
                    continue
                if response.status_code == 400 and use_json_mode:
                    use_json_mode = False
                    payload.pop('response_format', None)
                    last_error = 'Groq model rejected json_mode; retrying with schema-prompt JSON'
                    continue
                response.raise_for_status()
                result = response.json()
                if 'error' in result:
                    raise ValueError(f"Groq API error: {result['error']}")
                choices = result.get('choices', [])
                if not choices:
                    detail = result.get('message') or result.get('detail') or result
                    raise ValueError(f"Groq returned no choices: {detail}")
                content = choices[0].get('message', {}).get('content', '') or ''
                finish_reason = choices[0].get('finish_reason', '')
                if not content:
                    if finish_reason == 'length' and attempt == 0:
                        payload['max_tokens'] = max((payload.get('max_tokens') or 0) * 2, 4000)
                        last_error = 'Groq response truncated; retrying with larger max_tokens'
                        time.sleep(1.0)
                        continue
                    raise ValueError('Groq returned empty content')
                if use_json_mode or json_mode:
                    extracted = self._extract_json_text(content)
                    if extracted:
                        return extracted
                return str(content)
            except requests.RequestException as error:
                last_error = str(error)
                time.sleep((2 ** attempt) * 2)
            except ValueError as error:
                last_error = str(error)
                break
        raise ValueError(last_error or 'Groq request failed')

    def _extract_json_text(self, text: str):
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
