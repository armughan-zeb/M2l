"""One provider per project. Account handles are references, never OAuth tokens."""
OPENROUTER_MODEL = 'qwen/qwen3.5-flash-02-23'
CHATGPT_MODEL = 'gpt-6-luna'
ACCOUNT_PREFIX = 'chatgpt-account:'


def provider_name(config):
    value = str((config or {}).get('llm_provider') or 'openrouter').strip().lower()
    # Older saved projects used this label. Migrate the label while the
    # existing normalization still pins every role to the Qwen budget model.
    if value == 'groq':
        value = 'openrouter'
    if value not in {'external_agent', 'openrouter'}:
        raise ValueError('llm_provider must be external_agent or openrouter. ChatGPT subscription sign-in is disabled.')
    return value


def default_model(provider):
    return 'external-agent' if provider == 'external_agent' else OPENROUTER_MODEL


def account_from_handle(value):
    text = str(value or '')
    return text[len(ACCOUNT_PREFIX):] if text.startswith(ACCOUNT_PREFIX) else ''


def normalize_provider_config(config):
    result = dict(config or {})
    legacy_groq = str(result.get('llm_provider') or '').strip().lower() == 'groq'
    provider = provider_name(result)
    result['llm_provider'] = provider
    if legacy_groq and '/' not in str(result.get('llm_model') or ''):
        # Old Groq model IDs are not OpenRouter catalog IDs. Migrate the
        # model with the provider label rather than rejecting saved projects.
        result['llm_model'] = result.get('openrouter_model') or OPENROUTER_MODEL
    for name in ('chatgpt_access_token', 'chatgpt_refresh_token', 'chatgpt_id_token',
                 'chatgpt_account_id', 'agent_api_key'):
        result.pop(name, None)
    chosen = default_model(provider) if provider == 'external_agent' else str(
        result.get('llm_model') or result.get('openrouter_model') or default_model(provider)).strip()
    def validate(model):
        import re
        if model in {OPENROUTER_MODEL, 'qwen/qwen3.5-flash-02-23'}:
            return model
        if not re.fullmatch(r'[a-zA-Z0-9][\w.-]*/[a-zA-Z0-9][\w.-]*:free', model):
            raise ValueError('Select a catalog-confirmed free image-capable model; automatic routing and paid models are disabled.')
        from .model_catalog_policy import confirm_free_vision_model
        return confirm_free_vision_model(model)
    if provider != 'external_agent':
        chosen = validate(chosen)
    result['llm_model'] = chosen
    vision = str(result.get('vision_model') or result.get('qwen_vision_model') or chosen).strip()
    for name in ('query_llm_model', 'scene_brain_model', 'director_model',
                 'composition_model', 'research_model', 'vision_model',
                 'qwen_vision_model', 'qwen_vision_fallback_model'):
        value = str(result.get(name) or (vision if 'vision' in name else chosen)).strip()
        result[name] = chosen if provider == 'external_agent' else validate(value)
    result['qwen_vision_fallback_model'] = result['qwen_vision_model']
    for name, role in (('scene_brain_fallback_chain','scene_brain_model'), ('scene_brain_fallback_models','scene_brain_model'),
                       ('director_fallback_models','director_model'), ('composition_fallback_models','composition_model'),
                       ('qwen_vision_fallback_models','qwen_vision_model')):
        result[name] = [result[role]]
    if provider == 'external_agent':
        from .agent_bridge import AGENT_HANDLE
        owner = str(result.get('agent_owner_id') or '')
        result['openrouter_api_key'] = AGENT_HANDLE + owner
        result.pop('openrouterApiKey', None)
        result['tts_model'] = 'audiogram'
    elif str(result.get('openrouter_api_key') or '').startswith(('chatgpt-account:', 'external-agent:')):
        result['openrouter_api_key'] = ''
    return result


def request_account(headers):
    from .job_context import current_job
    context = current_job()
    handle = account_from_handle(str((headers or {}).get('Authorization', '')).removeprefix('Bearer '))
    raw = str((headers or {}).get('Authorization', '')).removeprefix('Bearer ')
    if raw.startswith(ACCOUNT_PREFIX) and not handle:
        raise ValueError('Choose a connected ChatGPT account in Settings.')
    if context.get('llm_provider') == 'openrouter' and handle:
        raise ValueError('A ChatGPT account cannot be used in an OpenRouter project.')
    if context.get('llm_provider') == 'chatgpt':
        account = context.get('chatgpt_account_id') or handle
        if not account:
            raise ValueError('Choose a connected ChatGPT account in Settings.')
        return account
    return handle
