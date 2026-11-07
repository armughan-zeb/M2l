import time
import json
import re
import os
import math
import requests

def _web_log(message: str) -> None:
    print(f"   [web] {message}", flush=True)

def _web_verbose() -> bool:
    return str(os.environ.get("VIDZEO_WEB_VERBOSE", "0")).strip().lower() in {"1", "true", "yes", "on"}

OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions'
OPENROUTER_MODELS_URL = 'https://openrouter.ai/api/v1/models'

DEFAULT_OPENROUTER_MODEL = 'qwen/qwen3.5-flash-02-23'
DEFAULT_QUERY_LLM_MODEL = 'qwen/qwen3.5-flash-02-23'
# Qwen is also the default for scene direction. Premium routes are explicit.
DEFAULT_SCENE_BRAIN_MODEL = DEFAULT_OPENROUTER_MODEL
DEFAULT_SCENE_BRAIN_FALLBACK_MODELS = (
    'qwen/qwen3.5-flash-02-23',
    'nvidia/nemotron-3.5-super-vl:free',
)

# Retain a conservative text fallback for generic callers that do not opt in
# to the scene-brain chain.  Do not point this at retired Qwen 2.5 IDs: the
# current OpenRouter catalog no longer guarantees those aliases.
OPENROUTER_FALLBACK_MODELS = (
    'nvidia/nemotron-3.5-super-vl:free',
)


def _compact_json_prompt(text: str) -> str:
    """Remove JSON formatting overhead without changing any embedded copy.

    Only unambiguous object/list inputs qualify. Ordinary prose, duplicate
    keys and non-finite values retain their exact original representation.
    """
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Ambiguous repeated field')
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError('Non-finite JSON constant')
    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        pass
    return text


def _normalise_model_chain(primary: str, fallbacks=None) -> tuple[str, ...]:
    """Return a stable, de-duplicated OpenRouter fallback chain."""
    values = [str(primary or '').strip()]
    if fallbacks:
        if isinstance(fallbacks, str):
            fallbacks = re.split(r'[,;|]+', fallbacks)
        values.extend(str(item or '').strip() for item in fallbacks)
    result = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _normalise_strict_json_schema(schema: dict) -> dict:
    """Make a caller schema valid for OpenAI-style strict structured output.

    Strict providers require every object to reject unknown properties and
    list every property as required. Preserve optional fields as nullable so
    existing callers can still distinguish an absent value from a real one.
    """
    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: visit(item) for key, item in value.items()}
        if result.get("type") != "object" and not isinstance(result.get("properties"), dict):
            return result
        properties = result.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        originally_required = set(result.get("required") or [])
        for key, child in properties.items():
            if key in originally_required or not isinstance(child, dict):
                continue
            child_type = child.get("type")
            if isinstance(child_type, str):
                child["type"] = [child_type, "null"] if child_type != "null" else ["null"]
            elif isinstance(child_type, list):
                child["type"] = list(dict.fromkeys([*child_type, "null"]))
            elif isinstance(child.get("anyOf"), list):
                child["anyOf"] = [*child["anyOf"], {"type": "null"}]
            if isinstance(child.get("enum"), list) and None not in child["enum"]:
                child["enum"] = [*child["enum"], None]
        result["type"] = result.get("type") or "object"
        result["properties"] = properties
        result["required"] = list(properties)
        result["additionalProperties"] = False
        return result

    return visit(schema)


def _json_schema_error(value, schema: dict, path: str = "$", max_errors: int = 1) -> str:
    """Small local validator for the JSON Schema subset used by OpenRouter.

    Keeping this dependency-free makes schema checks work in the bundled
    Windows runtime as well as the developer environment.
    """
    if not isinstance(schema, dict):
        return f"{path}: schema must be an object"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
        return f"{path}: JSON numbers must be finite"
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    type_ok = not expected
    for item in expected_types:
        if item == "object" and isinstance(value, dict): type_ok = True
        elif item == "array" and isinstance(value, list): type_ok = True
        elif item == "string" and isinstance(value, str): type_ok = True
        elif item == "integer" and isinstance(value, int) and not isinstance(value, bool): type_ok = True
        elif item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool): type_ok = True
        elif item == "boolean" and isinstance(value, bool): type_ok = True
        elif item == "null" and value is None: type_ok = True
    if not type_ok:
        return f"{path}: expected {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: value is outside the allowed choices"
    if isinstance(value, dict):
        required = schema.get("required") or []
        missing = [key for key in required if key not in value]
        if missing:
            return f"{path}: missing required fields {', '.join(map(str, missing))}"
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(properties)
            if unexpected:
                return f"{path}: unexpected fields {', '.join(sorted(map(str, unexpected)))}"
        for key, child in properties.items():
            if key in value:
                error = _json_schema_error(value[key], child, f"{path}.{key}", max_errors)
                if error:
                    return error
    elif isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            return f"{path}: too few items"
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            return f"{path}: too many items"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child_value in enumerate(value):
                error = _json_schema_error(child_value, item_schema, f"{path}[{index}]", max_errors)
                if error:
                    return error
    elif isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            return f"{path}: text is too short"
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            return f"{path}: text is too long"
        pattern = schema.get("pattern")
        if pattern:
            try:
                if re.search(str(pattern), value) is None:
                    return f"{path}: text does not match the required pattern"
            except re.error:
                return f"{path}: schema contains an invalid pattern"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < float(schema["minimum"]):
            return f"{path}: number is below the allowed range"
        if "maximum" in schema and value > float(schema["maximum"]):
            return f"{path}: number is above the allowed range"
    return ""

_MODEL_CACHE = {"expires_at": 0.0, "ids": None}

def openrouter_model_available(model: str, timeout_seconds: float = 10.0) -> bool:
    """Best-effort model existence check. Network failures should not block jobs."""
    model = str(model or '').strip()
    if not model:
        return False
    now = time.time()
    ids = _MODEL_CACHE.get("ids")
    if ids is None or float(_MODEL_CACHE.get("expires_at") or 0.0) <= now:
        try:
            response = requests.get(  # nosec B113
                OPENROUTER_MODELS_URL,
                timeout=max(1.0, float(timeout_seconds or 3.0)),
            )
            response.raise_for_status()
            payload = response.json()
            ids = {str(item.get("id") or "").strip() for item in payload.get("data", []) if isinstance(item, dict)}
            _MODEL_CACHE["ids"] = ids
            _MODEL_CACHE["expires_at"] = now + 3600.0
        except Exception as exc:
            if _web_verbose():
                _web_log(f"openrouter-llm: model list unavailable, trusting configured model {model}: {exc}")
            return True
    return model in ids

class OpenRouterLLM:
    def __init__(self, api_key: str, model: str = None, fallback_models=None,
                 use_server_fallbacks: bool = True):
        from .provider_selection import account_from_handle, ACCOUNT_PREFIX, CHATGPT_MODEL
        from .job_context import current_job
        context = current_job()
        if context.get('llm_provider') == 'chatgpt':
            api_key = ACCOUNT_PREFIX + str(context.get('chatgpt_account_id') or account_from_handle(api_key))
        if str(api_key).startswith(ACCOUNT_PREFIX) and not account_from_handle(api_key):
            raise ValueError('Choose a connected ChatGPT account in Settings.')
        from .agent_bridge import AGENT_HANDLE, AGENT_MODEL
        if context.get('llm_provider') == 'external_agent':
            api_key = AGENT_HANDLE + str(context.get('agent_owner_id') or '')
        if not api_key:
            raise ValueError('OpenRouter API key is required')
        self.provider = 'external_agent' if str(api_key).startswith(AGENT_HANDLE) else ('chatgpt' if account_from_handle(api_key) else 'openrouter')
        self.api_key = api_key
        self.model = str(model or DEFAULT_OPENROUTER_MODEL).strip()
        from .job_context import current_job
        if current_job().get('cost_profile') == 'efficient':
            self.model = str(self.model if self.model in {context.get(k) for k in ('llm_model','query_llm_model','scene_brain_model','director_model','composition_model','research_model')} else context.get('llm_model') or self.model)
            fallback_models = []
        if self.provider == 'chatgpt':
            self.model = CHATGPT_MODEL
            fallback_models = [CHATGPT_MODEL]
            use_server_fallbacks = False
        if self.provider == 'external_agent':
            self.model = AGENT_MODEL
            fallback_models = [AGENT_MODEL]
            use_server_fallbacks = False
        elif context.get('llm_provider') == 'openrouter':
            self.model = str(self.model if self.model in {context.get(k) for k in ('llm_model','query_llm_model','scene_brain_model','director_model','composition_model','research_model')} else context.get('llm_model') or self.model)
            fallback_models = []
        # Generic/editorial callers preserve their historical retry semantics
        # unless they explicitly opt into a model chain. The scene brain does
        # so explicitly, which prevents an unrelated text request from gaining
        # an unexpected extra HTTP attempt.
        configured_fallbacks = fallback_models if fallback_models is not None else ()
        self.fallback_models = _normalise_model_chain(self.model, configured_fallbacks)[1:]
        self.use_server_fallbacks = bool(use_server_fallbacks)
        self.last_model = self.model
        # Bounded safe diagnostics for provider retries; never retain prompts,
        # API keys, or full provider response bodies.
        self.attempt_history = []

    @property
    def model_chain(self) -> tuple[str, ...]:
        """Models sent to OpenRouter in priority order."""
        return _normalise_model_chain(self.model, self.fallback_models)

    from .structured_response_cache import cache_structured_response

    @cache_structured_response
    def chat(self, user_prompt: str, system_prompt: str = None,
             temperature: float = 0.7, max_tokens: int = 1000,
             json_mode: bool = False, timeout_seconds: float = None,
             max_attempts: int = None, json_schema: dict = None,
             total_timeout_seconds: float = None,
             json_schema_name: str = "vidnets_response",
             primary_timeout_share: float = None) -> str:
        self.attempt_history = []
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
            
        strict_schema = (
            _normalise_strict_json_schema(json_schema)
            if isinstance(json_schema, dict)
            else None
        )

        user_content = str(user_prompt or '')
        if json_mode:
            user_content = _compact_json_prompt(user_content)
            json_suffix = (
                ' Return a valid JSON object matching the supplied JSON schema. Output JSON only.'
                if strict_schema
                else ' Return valid JSON only.'
            )
            user_content = f"{user_content.rstrip()}\n\n{json_suffix.strip()}"
            if strict_schema:
                # Some Qwen providers accept response_format but do not apply
                # its field constraints. Supply the contract in the prompt too;
                # local validation remains authoritative before use or caching.
                user_content += '\nRequired response schema (exact field names and types):\n' + json.dumps(strict_schema, ensure_ascii=False, separators=(',', ':'))
        messages.append({'role': 'user', 'content': user_content})

        payload = {
            'model': self.model,
            'messages': messages,
            'max_tokens': max_tokens
        }
        # OpenRouter performs model failover server-side. ``model`` is the
        # primary; ``models`` contains only fallbacks. Duplicating the primary
        # in ``models`` used to consume one of the three fallback slots and
        # omit Qwen from the server-side chain.
        model_chain = self.model_chain
        from .job_context import current_job
        if current_job().get('cost_profile') == 'efficient':
            model_chain = (self.model,) if self.provider in {'chatgpt', 'external_agent'} else (DEFAULT_OPENROUTER_MODEL,)

        last_error = None
        if timeout_seconds is None:
            try:
                request_timeout = max(30.0, float(os.environ.get('VIDZEO_OPENROUTER_TIMEOUT_SECONDS', '360') or 360))
            except Exception:
                request_timeout = 360.0
        else:
            request_timeout = max(5.0, float(timeout_seconds or 5.0))

        if max_attempts is None:
            try:
                request_attempts = max(1, int(os.environ.get('VIDZEO_OPENROUTER_MAX_ATTEMPTS', '5') or 5))
            except Exception:
                request_attempts = 3
        else:
            request_attempts = max(1, int(max_attempts or 1))
        from .job_context import current_job
        if current_job().get('cost_profile') == 'efficient':
            request_attempts = min(request_attempts, 2)

        effective_max_tokens = max_tokens
        # Strict schemas bound the shape and size of these director outputs;
        # honor the caller's deliberately small cap (art direction asks for
        # 700 tokens). The old floor was intended for unstructured legacy JSON
        # and unnecessarily inflated every structured request to 2,000 tokens.
        # Honor the caller's cap; truncation has a bounded recovery below.

        truncation_retried = False
        structured_fallback_used = False

        # Scene plans need compact JSON, not hidden chain-of-thought. Keep
        # reasoning disabled by default on routes that expose this control.
        _REASONING_MODEL_HINTS = ('gpt-oss', 'qwen', 'deepseek', 'qwq', 'kimi', 'nemotron')

        if total_timeout_seconds is None:
            try:
                configured_total = max(
                    request_timeout,
                    float(os.environ.get('VIDZEO_OPENROUTER_TOTAL_TIMEOUT_SECONDS', '1500') or 1500),
                )
            except (TypeError, ValueError):
                configured_total = max(request_timeout, 1500.0)
        else:
            try:
                configured_total = float(total_timeout_seconds)
            except (TypeError, ValueError):
                configured_total = request_timeout
        if not math.isfinite(configured_total):
            configured_total = max(request_timeout, 1500.0)
        # Honor an explicit total budget. A 30-second floor here used to turn
        # an 8-second four-model retry budget into 30 seconds and give the
        # first route 7.5 seconds, starving every fallback. Keep a small floor
        # per route and a hard upper bound for malformed persisted settings.
        minimum_chain_budget = max(0.25, len(model_chain) * 0.25)
        total_budget = max(minimum_chain_budget, min(1800.0, configured_total))
        from .job_context import check_job_active, remaining_seconds
        check_job_active()
        total_budget = min(total_budget, remaining_seconds(total_budget))
        deadline = time.monotonic() + total_budget

        def sleep_within_budget(delay_seconds: float) -> None:
            """Never let retry backoff consume more than the request budget."""
            try:
                remaining_seconds = max(0.0, deadline - time.monotonic())
                bounded_delay = min(max(0.0, float(delay_seconds)), remaining_seconds)
            except (TypeError, ValueError):
                return
            if bounded_delay > 0:
                time.sleep(bounded_delay)

        model_chain = list(model_chain)
        active_model_index = 0
        # A model fallback is a separate provider attempt, not a retry of the
        # same slow model. Allow one extra bounded request for each configured
        # fallback even when a caller deliberately sets max_attempts=1.
        total_attempts = request_attempts + max(0, len(model_chain) - 1)
        for attempt in range(total_attempts):
            try:
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    last_error = f'OpenRouter total timeout exceeded ({total_budget:.1f}s)'
                    break
                remaining_model_attempts = max(1, len(model_chain) - active_model_index)
                attempt_timeout = request_timeout
                if remaining is not None:
                    # Reserve time for each remaining route. Without this
                    # apportionment, one slow primary could consume the whole
                    # director deadline before any configured fallback runs.
                    allocated = remaining / remaining_model_attempts
                    # A complex creative response needs enough time to finish
                    # its first route. Callers may reserve a larger primary
                    # share while keeping the shared deadline and fallback tail.
                    if active_model_index == 0 and primary_timeout_share is not None:
                        try:
                            share = min(0.8, max(0.0, float(primary_timeout_share)))
                            allocated = max(allocated, remaining * share)
                        except (TypeError, ValueError):
                            pass
                    attempt_timeout = min(request_timeout, max(0.25, allocated))
                request_payload = dict(payload)
                request_payload['max_tokens'] = effective_max_tokens
                active_model = model_chain[min(active_model_index, len(model_chain) - 1)]
                request_payload['model'] = active_model
                # GPT-6 Luna and GPT-5.6 Luna reject non-default sampling
                # controls on the OpenRouter Chat Completions route. Omitting
                # temperature for those models allows strict JSON-schema
                # requests to reach their supported providers; other routes
                # retain the caller's temperature setting.
                model_id = str(active_model or '').lower()
                if model_id.startswith(('openai/gpt-6-luna', 'openai/gpt-5.6-luna')):
                    request_payload.pop('temperature', None)
                else:
                    request_payload['temperature'] = temperature
                if self.use_server_fallbacks and len(model_chain) > active_model_index + 1:
                    # OpenRouter expects the selected model in ``model`` and
                    # only subsequent fallbacks in ``models`` (maximum three).
                    request_payload['models'] = model_chain[active_model_index + 1:active_model_index + 4]
                else:
                    request_payload.pop('models', None)

                model_hint = str(active_model or '').lower()
                if any(hint in model_hint for hint in _REASONING_MODEL_HINTS):
                    # Qwen's OpenRouter adapter uses the explicit enabled flag;
                    # keep the legacy exclude contract for GPT-OSS/other
                    # reasoning models so existing integrations remain stable.
                    if 'qwen' in model_hint:
                        thinking = str(os.environ.get('VIDZEO_LLM_THINKING', '0') or '0').strip().lower()
                        request_payload['reasoning'] = (
                            {'enabled': True}
                            if thinking in {'1', 'true', 'yes', 'on'}
                            else {'enabled': False}
                        )
                    else:
                        request_payload['reasoning'] = {
                            'max_tokens': max(64, min(192, int(effective_max_tokens * 0.15))),
                            'exclude': True,
                        }

                if strict_schema:
                    request_payload['response_format'] = {
                        'type': 'json_schema',
                        'json_schema': {
                            'name': re.sub(r'[^a-zA-Z0-9_-]+', '_', str(json_schema_name or 'vidnets_response'))[:64],
                            'strict': True,
                            'schema': strict_schema,
                        },
                    }
                    request_payload['provider'] = {**(request_payload.get('provider') or {}), 'require_parameters': True}
                elif json_mode and attempt == 0:
                    request_payload['response_format'] = {'type': 'json_object'}
                elif json_mode:
                    retry_messages = list(messages)
                    retry_messages.append({
                        'role': 'user',
                        'content': 'Your previous response was not parseable JSON. Return raw valid JSON only, no markdown, no prose.'
                    })
                    request_payload['messages'] = retry_messages
                from .bounded_http import provider_post
                response = provider_post(requests.post,
                    OPENROUTER_API_URL,
                    headers=headers,
                    json=request_payload,
                    timeout=attempt_timeout,
                    usage_kind='text', operation=json_schema_name,
                )
                self.attempt_history.append({
                    "attempt": int(attempt + 1),
                    "model": active_model,
                    "status_code": int(response.status_code),
                })
                if response.status_code == 401:
                    raise ValueError('OpenRouter API key rejected (401). Add or replace it in Settings: https://openrouter.ai/keys')
                if response.status_code == 402:
                    if active_model_index < len(model_chain) - 1:
                        active_model_index += 1
                        continue
                    raise ValueError('OpenRouter credits or the key spending limit are exhausted (402). Add credits: https://openrouter.ai/credits')
                if response.status_code == 403:
                    if active_model_index < len(model_chain) - 1:
                        active_model_index += 1
                        continue
                    raise ValueError('OpenRouter denied this model/key request (403). Check key permissions: https://openrouter.ai/keys')
                if response.status_code == 400:
                    # Strict schemas are a contract. Never silently drop the
                    # response format: fail over to a route that honors it.
                    # Legacy JSON-object callers keep their compatibility retry.
                    if json_mode and not strict_schema and 'response_format' in request_payload and not structured_fallback_used:
                        structured_fallback_used = True
                        request_payload.pop('response_format', None)
                        payload.pop('response_format', None)
                        last_error = 'OpenRouter provider rejected structured JSON; retrying without response_format'
                        continue
                    if active_model_index < len(model_chain) - 1:
                        active_model_index += 1
                        structured_fallback_used = False
                        continue
                    raise ValueError('OpenRouter rejected the request (400). Check the selected model and request settings.')
                if response.status_code == 404:
                    if active_model_index < len(model_chain) - 1:
                        active_model_index += 1
                        continue
                    detail = response.text[:300]
                    raise ValueError(f"OpenRouter model not available or not found: {self.model}: {detail}")
                if response.status_code == 429:
                    if active_model_index < len(model_chain) - 1:
                        active_model_index += 1
                        continue
                    if attempt + 1 < request_attempts:
                        delay = min((2 ** attempt) * 2, 8)
                        sleep_within_budget(delay)
                        continue
                    raise ValueError('OpenRouter rate limit reached for all configured director models')
                response.raise_for_status()
                result = response.json()
                if 'error' in result:
                    raise ValueError(f"OpenRouter API error: {result['error']}")
                choices = result.get('choices', [])
                if not choices:
                    detail = result.get('message') or result.get('detail') or result
                    raise ValueError(f"OpenRouter returned no choices: {detail}")
                content = choices[0].get('message', {}).get('content', '') or ''
                resolved_model = str(result.get('model') or '').strip()
                if resolved_model:
                    self.last_model = resolved_model
                    if resolved_model != self.model and _web_verbose():
                        _web_log(
                            f"openrouter-llm: fallback resolved "
                            f"{self.model} -> {resolved_model}"
                        )
                finish_reason = choices[0].get('finish_reason', '')
                self.attempt_history[-1].update({
                    "resolved_model": resolved_model or active_model,
                    "finish_reason": str(finish_reason or ""),
                    "content_length": len(str(content or "")),
                })
                if not content:
                    if active_model_index < len(model_chain) - 1:
                        active_model_index += 1
                        truncation_retried = False
                        last_error = (
                            f"{active_model} returned empty content; "
                            f"failing over to {model_chain[active_model_index]}"
                        )
                        if _web_verbose():
                            _web_log(f"openrouter-llm: {last_error}")
                        continue
                    if finish_reason == 'length' and not truncation_retried:
                        truncation_retried = True
                        effective_max_tokens = max(effective_max_tokens * 2, 4000)
                        request_payload['max_tokens'] = effective_max_tokens
                        last_error = (
                            f"OpenRouter response truncated (reasoning consumed tokens); "
                            f"retrying with max_tokens={effective_max_tokens}"
                        )
                        if _web_verbose():
                            _web_log(f"openrouter-llm: {last_error}")
                        sleep_within_budget(1.0)
                        continue
                    raise ValueError('OpenRouter returned empty content')

                if finish_reason == 'length' and json_mode and active_model_index < len(model_chain) - 1:
                    active_model_index += 1
                    truncation_retried = False
                    last_error = (
                        f"{active_model} returned a length-truncated JSON response; "
                        f"failing over to {model_chain[active_model_index]}"
                    )
                    if _web_verbose():
                        _web_log(f"openrouter-llm: {last_error}")
                    continue
                if finish_reason == 'length' and json_mode and not truncation_retried:
                    truncation_retried = True
                    effective_max_tokens = max(effective_max_tokens * 2, 4000)
                    last_error = f"OpenRouter response truncated; retrying with max_tokens={effective_max_tokens}"
                    if _web_verbose():
                        _web_log(f"openrouter-llm: {last_error}")
                    sleep_within_budget(1.0)
                    continue

                if json_mode:
                    extracted = self._extract_json_text(content)
                    if extracted:
                        if strict_schema:
                            try:
                                parsed_json = json.loads(extracted)
                            except Exception as exc:
                                raise ValueError(f"OpenRouter returned invalid structured JSON: {exc}")
                            # These fields are private directing notes, never
                            # rendered copy or evidence. A verbose note should
                            # not discard an otherwise valid art direction or
                            # trigger another paid request. Approved scene text
                            # and all other schemas remain exact and unmodified.
                            if json_schema_name == 'vidnets_art_direction_v1' and isinstance(parsed_json, dict):
                                shortened = []
                                for field in ('visual_motif', 'motion_rhythm', 'story_arc', 'retention_plan'):
                                    limit = (strict_schema.get('properties', {}).get(field) or {}).get('maxLength')
                                    value = parsed_json.get(field)
                                    if isinstance(value, str) and isinstance(limit, int) and len(value) > limit:
                                        prefix = value[:limit]
                                        parsed_json[field] = prefix.rsplit(' ', 1)[0] if ' ' in prefix else prefix
                                        shortened.append(field)
                                if shortened:
                                    extracted = json.dumps(parsed_json, ensure_ascii=False, separators=(',', ':'))
                                    self.attempt_history[-1]['shortened_direction_notes'] = shortened
                            if json_schema_name in {'vidnets_scene_composition_v1', 'vidnets_scene_composition_repair_v1'} and isinstance(parsed_json, dict):
                                motion_choices = (strict_schema.get('properties', {}).get('objects', {}).get('items', {}).get('properties', {}).get('motion', {}) or {}).get('enum')
                                for index, obj in enumerate(parsed_json.get('objects') or []):
                                    if not isinstance(obj, dict) or not motion_choices or obj.get('motion') in motion_choices:
                                        continue
                                    motion = str(obj.get('motion') or '').casefold().replace('-', '_').replace(' ', '_')
                                    motion = {'fade_in': 'fade', 'reveal': 'wipe', 'trace': 'draw',
                                              'slide_up': 'rise', 'pop': 'scale', 'zoom': 'scale'}.get(motion, motion)
                                    obj['motion'] = motion if motion in motion_choices else ('fade' if 'fade' in motion_choices else motion_choices[0])
                                    self.attempt_history[-1].setdefault('normalized_composition_motion_indices', []).append(index)
                                extracted = json.dumps(parsed_json, ensure_ascii=False, separators=(',', ':'))
                            if json_schema_name == 'vidnets_scene_direction_v1' and isinstance(parsed_json, dict):
                                deferred = []
                                for row in parsed_json.get('scenes', []):
                                    if not isinstance(row, dict):
                                        continue
                                    # First-pass direction explicitly defers recipes
                                    # to the dedicated composition designer. A
                                    # premature, malformed recipe must not discard
                                    # the actor choices, timing and whole scene.
                                    if row.get('template_recipe') is not None:
                                        row['template_recipe'] = None
                                        deferred.append(row.get('scene_index'))
                                    row_schema = strict_schema.get('properties', {}).get('scenes', {}).get('items', {}).get('properties', {})
                                    # Decorative motion vocabulary is optional.
                                    # Unsupported phrasing must not discard the
                                    # director's factual copy and graphic plan.
                                    for motion_field, safe_default in (('entrance', 'fade'), ('transition', 'cut'), ('camera', 'static')):
                                        value = row.get(motion_field)
                                        choices = row_schema.get(motion_field, {}).get('enum')
                                        if value is not None and choices and value not in choices:
                                            normalized = str(value).lower().replace('-', '_').replace(' ', '_')
                                            aliases = {'fade_in':'fade', 'fade_out':'fade', 'crossfade':'dissolve',
                                                       'push_in':'slow_push', 'zoom_in':'slow_push', 'none':'static'}
                                            normalized = aliases.get(normalized, normalized)
                                            row[motion_field] = normalized if normalized in choices else (
                                                safe_default if safe_default in choices else choices[0])
                                            self.attempt_history[-1].setdefault('normalized_motion_fields', []).append(
                                                {'scene_index':row.get('scene_index'),'field':motion_field})
                                    cue_schema = row_schema.get('motion_cues', {}).get('items', {}).get('properties', {})
                                    cues = row.get('motion_cues')
                                    if isinstance(cues, list) and cue_schema:
                                        valid_cues = []
                                        aliases = {'draw': 'trace', 'connect': 'trace', 'link': 'trace', 'flow': 'trace',
                                                   'zoom': 'pan', 'slide': 'wipe', 'fade': 'reveal', 'pop': 'reveal', 'appear': 'reveal'}
                                        for cue in cues:
                                            if not isinstance(cue, dict):
                                                continue
                                            cue = dict(cue)
                                            action = str(cue.get('action') or '').lower()
                                            cue['action'] = aliases.get(action, action)
                                            if cue.get('action') not in cue_schema.get('action', {}).get('enum', []):
                                                continue
                                            if cue.get('element') not in cue_schema.get('element', {}).get('enum', []):
                                                continue
                                            valid_cues.append(cue)
                                        row['motion_cues'] = valid_cues[:row_schema.get('motion_cues', {}).get('maxItems', 3)]
                                        if valid_cues != cues:
                                            self.attempt_history[-1].setdefault('normalized_motion_scene_indices', []).append(row.get('scene_index'))
                                    note = row.get('visual_intent')
                                    limit = (strict_schema.get('properties', {}).get('scenes', {}).get('items', {}).get('properties', {}).get('visual_intent', {}) or {}).get('maxLength')
                                    if isinstance(note, str) and isinstance(limit, int) and len(note) > limit:
                                        row['visual_intent'] = note[:limit].rsplit(' ', 1)[0]
                                extracted = json.dumps(parsed_json, ensure_ascii=False, separators=(',', ':'))
                                if deferred:
                                    self.attempt_history[-1]['deferred_composition_scene_indices'] = deferred
                            schema_error = _json_schema_error(parsed_json, strict_schema)
                            if schema_error:
                                raise ValueError(f"OpenRouter response did not match the requested schema: {schema_error}")
                        return extracted
                    last_error = f"OpenRouter returned non-JSON content: {str(content)[:160]}"
                    if active_model_index < len(model_chain) - 1:
                        active_model_index += 1
                        truncation_retried = False
                        if _web_verbose():
                            _web_log(
                                f"openrouter-llm: {active_model} returned non-JSON; "
                                f"failing over to {model_chain[active_model_index]}"
                            )
                        continue
                    if attempt + 1 < request_attempts:
                        sleep_within_budget((2 ** attempt) * 2)
                        continue
                    break
                return str(content)
            except requests.RequestException as error:
                last_error = str(error)
                self.attempt_history.append({
                    "attempt": int(attempt + 1),
                    "model": active_model if 'active_model' in locals() else self.model,
                    "error": f"{type(error).__name__}: {str(error)[:180]}",
                })
                if active_model_index < len(model_chain) - 1:
                    active_model_index += 1
                    continue
                if attempt + 1 < request_attempts:
                    sleep_within_budget((2 ** attempt) * 2)
            except ValueError as error:
                last_error = str(error)
                if not self.attempt_history or self.attempt_history[-1].get("attempt") != int(attempt + 1):
                    self.attempt_history.append({
                        "attempt": int(attempt + 1),
                        "model": active_model if 'active_model' in locals() else self.model,
                    })
                self.attempt_history[-1]["error"] = str(error)[:240]
                if active_model_index < len(model_chain) - 1 and "401" not in last_error:
                    active_model_index += 1
                    truncation_retried = False
                    continue
                break

        raise ValueError(last_error or 'OpenRouter request failed')

    def _extract_json_text(self, text: str) -> str | None:
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
        # Prefer the first complete schema object.  Looking only at the
        # widest ``{...}``/``[...]`` span below can accidentally select a
        # nested beats array or combine the answer with a trailing example.
        decoder = json.JSONDecoder()
        for match in re.finditer(r'[\[{]', candidate):
            fragment = candidate[match.start():]
            try:
                payload, end = decoder.raw_decode(fragment)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, (dict, list)):
                parsed = fragment[:end]
                parsed = re.sub(r',\s*([}\]])', r'\1', parsed)
                parsed = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]+', ' ', parsed)
                try:
                    json.loads(parsed)
                    return parsed
                except Exception:
                    pass
        start_object = candidate.find("{")
        end_object = candidate.rfind("}")
        start_array = candidate.find("[")
        end_array = candidate.rfind("]")
        ranges = []
        if start_object != -1 and end_object > start_object:
            ranges.append(candidate[start_object:end_object + 1])
        if start_array != -1 and end_array > start_array:
            ranges.append(candidate[start_array:end_array + 1])
        
        ranges.sort(key=len, reverse=True)
        
        for raw_json in ranges:
            cleaned = re.sub(r',\s*([}\]])', r'\1', raw_json)
            cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]+', ' ', cleaned)
            try:
                json.loads(cleaned)
                return cleaned
            except Exception:
                continue
        # ``find/rfind`` above can still span two objects when a routed model
        # adds an example after the answer.  Walk each plausible opening token
        # with JSONDecoder.raw_decode so a valid first object/array survives
        # surrounding prose or trailing examples.
        decoder = json.JSONDecoder()
        for match in re.finditer(r'[\[{]', candidate):
            fragment = candidate[match.start():]
            try:
                payload, end = decoder.raw_decode(fragment)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, (dict, list)):
                continue
            parsed = fragment[:end]
            parsed = re.sub(r',\s*([}\]])', r'\1', parsed)
            parsed = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]+', ' ', parsed)
            try:
                json.loads(parsed)
                return parsed
            except Exception:
                continue
        return None

class OpenRouterClient:
    def __init__(self, api_key: str, model: str):
        self._llm = OpenRouterLLM(api_key, model)

    def chat(self, system_prompt: str, user_prompt: str,
             temperature: float = 0.7, max_tokens: int = 1000,
             json_mode: bool = False, timeout_seconds: float = None,
             max_attempts: int = None, json_schema: dict = None) -> str:
        return self._llm.chat(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            json_schema=json_schema,
        )


def check_openrouter_key_status(api_key: str = None) -> dict:
    """Validate OpenRouter API key and query key limits/account credits."""
    if api_key is not None:
        raw_key = str(api_key).strip()
    else:
        raw_key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not raw_key:
        return {
            "ok": False,
            "configured": False,
            "status": "missing_key",
            "message": "OpenRouter key missing. Local fallbacks remain available."
        }

    headers = {
        "Authorization": f"Bearer {raw_key}",
        "HTTP-Referer": "https://vidnets.com",
        "X-Title": "Vidnets Studio",
    }

    try:
        resp = requests.get("https://openrouter.ai/api/v1/auth/key", headers=headers, timeout=10)
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "status": "connection_error",
            "message": f"Could not connect to OpenRouter ({exc}). Check internet connection."
        }

    if resp.status_code == 401:
        return {
            "ok": False,
            "configured": True,
            "status": "invalid_key",
            "message": "Invalid OpenRouter API key. Check or replace key at https://openrouter.ai/keys"
        }
    elif resp.status_code == 402:
        return {
            "ok": False,
            "configured": True,
            "status": "credits_exhausted",
            "limit_remaining": 0.0,
            "message": "OpenRouter credits or spending limit are exhausted. Add credits at https://openrouter.ai/credits"
        }
    elif resp.status_code == 403:
        return {
            "ok": False,
            "configured": True,
            "status": "forbidden",
            "message": "OpenRouter key access denied. Check key permissions at https://openrouter.ai/keys"
        }
    elif resp.status_code != 200:
        return {
            "ok": False,
            "configured": True,
            "status": "error",
            "message": f"OpenRouter returned HTTP {resp.status_code}. Check key at https://openrouter.ai/keys"
        }

    try:
        data = resp.json().get("data") or {}
    except Exception:
        data = {}

    limit_remaining = data.get("limit_remaining")
    limit = data.get("limit")
    usage = data.get("usage", 0.0)
    label = data.get("label", "")

    # If limit_remaining is null and a key limit is set, calculate remaining
    if limit_remaining is None and limit is not None:
        try:
            limit_remaining = max(0.0, float(limit) - float(usage or 0.0))
        except (TypeError, ValueError):
            limit_remaining = None

    # If still null, query account credits endpoint
    if limit_remaining is None:
        try:
            c_resp = requests.get("https://openrouter.ai/api/v1/credits", headers=headers, timeout=5)
            if c_resp.status_code == 200:
                c_data = c_resp.json().get("data") or {}
                tot = c_data.get("total_credits")
                use = c_data.get("total_usage", 0.0)
                if tot is not None:
                    limit_remaining = max(0.0, float(tot) - float(use or 0.0))
        except Exception:
            pass

    if limit_remaining is not None and limit_remaining <= 0.0001 and limit is not None:
        return {
            "ok": False,
            "configured": True,
            "status": "limit_exhausted",
            "limit_remaining": 0.0,
            "label": label,
            "message": "OpenRouter key spending limit reached ($0.00 remaining). Increase limit at https://openrouter.ai/keys"
        }

    masked = f"{raw_key[:8]}...{raw_key[-4:]}" if len(raw_key) > 16 else "sk-..."
    return {
        "ok": True,
        "configured": True,
        "status": "ready",
        "limit_remaining": round(limit_remaining, 2) if isinstance(limit_remaining, (int, float)) else limit_remaining,
        "label": label,
        "masked_key": masked,
        "message": "OpenRouter connected successfully."
    }
