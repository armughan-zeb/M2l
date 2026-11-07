# =============================================================================
# VIDNETS STUDIO - Configuration Constants
# =============================================================================

import os
import re
from pathlib import Path

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    candidates = [
        Path(os.environ.get('VIDZEO_ROOT', '')).resolve() if os.environ.get('VIDZEO_ROOT') else None,
        Path(__file__).parent.parent,
        Path.cwd(),
    ]
    loaded_paths = []
    seen_paths = set()
    for base in filter(None, candidates):
        env_f = base / '.env'
        if env_f.is_file() and str(env_f.resolve()) not in seen_paths:
            load_dotenv(env_f, encoding='utf-8-sig')
            seen_paths.add(str(env_f.resolve()))
            loaded_paths.append('.env')
        defaults_f = base / '.env.defaults'
        if defaults_f.is_file():
            # load_dotenv does not override an existing local/process value, so
            # private developer settings remain authoritative while packaged
            # builds get the embedded non-secret runtime profile.
            load_dotenv(defaults_f, encoding='utf-8-sig')
            if str(defaults_f.resolve()) not in seen_paths:
                seen_paths.add(str(defaults_f.resolve()))
                loaded_paths.append('.env.defaults')
        pub_f = base / '.env.public'
        if pub_f.is_file():
            load_dotenv(pub_f, encoding='utf-8-sig')
            if str(pub_f.resolve()) not in seen_paths:
                seen_paths.add(str(pub_f.resolve()))
                loaded_paths.append('.env.public')
    if loaded_paths:
        print("Environment variables loaded from " + ", ".join(dict.fromkeys(loaded_paths)))
    else:
        print("Environment variables loaded from process environment")
except ImportError:
    pass  # python-dotenv not installed, use system env vars

# =============================================================================
# DIRECTORIES
# =============================================================================

RESOURCE_DIR = Path(os.environ.get('VIDZEO_RESOURCE_ROOT', str(Path(__file__).parent.parent))).resolve()
BASE_DIR = Path(os.environ.get('VIDZEO_ROOT', str(RESOURCE_DIR))).resolve()

SERVER_DIR = Path(__file__).parent
FONTS_DIR = Path(os.environ.get('VIDZEO_FONTS_DIR', str(RESOURCE_DIR / "fonts"))).resolve()
OUTPUT_DIR = Path(os.environ.get('VIDZEO_OUTPUT_DIR', str(BASE_DIR / "output"))).resolve()
TEMP_DIR = Path(os.environ.get('VIDZEO_TEMP_DIR', str(BASE_DIR / "temp"))).resolve()

# Packaged resources can be read-only. Only runtime data directories are
# created here; the package validator guarantees the font tree exists.
for d in [OUTPUT_DIR, TEMP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# =============================================================================
# SCRIPT SERVICE CONSTANTS
# =============================================================================

# The "No BS" Banned List - Never use these
BANNED_PHRASES_MASTER = [
    # User's specific hate list
    "in this video",
    "let's dive in",
    "don't forget to subscribe",
    "make sure to",
    "at the end of the day",
    "game changer",
    "unlock the secrets",
    "without further ado",
    "let's get started",
    "welcome back",
    
    # Common AI Slop
    "delve", "realm", "tapestry", "testament", "landscape", "dynamic",
    "foster", "harness", "empower", "elevate", "unleash", "unlock",
    "nested", "bustling", "vibrant", "crucial", "pivotal",
    "buckle up", "stay tuned", "let's explore", "let's analyze",
    "basically", "literally", "honestly", "i think", "in my opinion",
    "rich history", "modern era", "digital world",
    "journey", "explore", "discover", "world of", "revolutionize",
    
    # v4 Additions - More AI Slop
    "it's important to note", "it is worth noting", "it goes without saying",
    "at the core", "when it comes to", "in order to", "the fact that",
    "it is essential", "one thing is clear", "there's no denying",
    "plays a crucial role", "in today's world", "throughout history",
    "it's no secret", "needless to say", "as we all know",
    "the bottom line", "at the heart of", "in the grand scheme",
    "a testament to", "it begs the question", "food for thought",
    "take a moment to", "let's talk about", "here's the thing",
    "the truth is", "the reality is", "at its core",
    "moving forward", "going forward", "as we move forward",
    "in conclusion", "to sum up", "in summary",
    "first and foremost", "last but not least", "more importantly",
]

# =============================================================================
# SCRIPT ENGINE V7 - META-LANGUAGE KILL SWITCH
# =============================================================================

# Phrases that indicate AI "assistant mode" leakage - triggers auto-retry
META_LANGUAGE_VIOLATIONS = [
    "here is", "here's", "below is", "above is",
    "fixed version", "revised", "updated version",
    "script:", "outline:", "narration:",
    "as an ai", "as an assistant", "let me",
    "i will", "i'll provide", "certainly",
    "sure,", "sure!", "of course", "absolutely",
    "here is your", "here's your", "i've written",
    "this script", "let's begin", "let's start",
    "without further ado", "hope this helps",
]

# Academic/AI-ish phrasing banned for de-AI-fication
ACADEMIC_SLOP_PHRASES = [
    "in today's world", "in today's society", "in the modern era",
    "it's important to note", "it bears mentioning", "it goes without saying",
    "this shows that", "this demonstrates that", "one cannot underestimate",
    "the fact remains", "plays a crucial role", "in order to",
    "it is essential to", "a testament to", "serves as a reminder",
]

# V7 Retry configuration
MAX_META_RETRIES = 3
CONTINUITY_RETRY_LIMIT = 2


VOICE_PROFILES = {
    "Casual": {
        "tone": "Casual and friendly",
        "style": "Use short sentences. Speak like a friend. Use 'you' and 'I'. Mix of 5-word and 15-word sentences.",
        "pov": "I/We (Personal)",
    },
    "Professional": {
        "tone": "Authoritative and clear",
        "style": "Focus on facts and clear explanations. No slang. concise but detailed.",
        "pov": "We (Editorial)",
    },
    "High Energy": {
        "tone": "Excited and fast-paced",
        "style": "Punchy. Lots of questions. High momentum. Use strong verbs.",
        "pov": "You (Direct)",
    },
    "Storyteller": {
        "tone": "Narrative and immersive",
        "style": "Focus on the sequence of events. Use sensory details. Build tension.",
        "pov": "Third Person or I (Narrator)",
    }
}

# Audiogram voices (free Microsoft neural voices; no API key needed)
EDGE_VOICES = {
    # American Male
    'Brian (Male US)': 'en-US-BrianNeural',
    'Andrew (Male US)': 'en-US-AndrewNeural',
    'Christopher (Male US)': 'en-US-ChristopherNeural',
    'Eric (Male US)': 'en-US-EricNeural',
    'Guy (Male US)': 'en-US-GuyNeural',
    'Roger (Male US)': 'en-US-RogerNeural',
    # American Female
    'Ana (Female US)': 'en-US-AnaNeural',
    'Aria (Female US)': 'en-US-AriaNeural',
    'Jenny (Female US)': 'en-US-JennyNeural',
    'Michelle (Female US)': 'en-US-MichelleNeural',
    # British Male
    'Ryan (Male UK)': 'en-GB-RyanNeural',
    'Thomas (Male UK)': 'en-GB-ThomasNeural',
    # British Female
    'Maisie (Female UK)': 'en-GB-MaisieNeural',
    'Sonia (Female UK)': 'en-GB-SoniaNeural',
}

# =============================================================================
# VOICE OVER MODES
# =============================================================================

DEFAULT_VO_MODE = 'Use Audiogram'
DEFAULT_TTS_MODEL = 'audiogram'
OPENROUTER_VO_MODE = 'Use OpenRouter TTS'
DEFAULT_WHISPER_MODE = 'local'
OPENROUTER_TTS_MODEL_IDS = (
    'fish-audio/s2.1-pro-free:free',
)
SUPPORTED_TTS_MODELS = (DEFAULT_TTS_MODEL,) + OPENROUTER_TTS_MODEL_IDS
TTS_MODEL_OPTIONS = [
    {'id': DEFAULT_TTS_MODEL, 'label': 'Edge TTS (Free)', 'provider': 'Microsoft Edge'},
    {'id': 'fish-audio/s2.1-pro-free:free', 'label': 'Fish Audio S2.1 Pro Free', 'provider': 'Fish Audio'},
]
VO_MODES = [DEFAULT_VO_MODE, OPENROUTER_VO_MODE]


def _generation_bool(value, default=False):
    """Parse persisted/request boolean values without treating ``"false"`` as true."""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def normalize_generation_config(config: dict | None) -> dict:
    """Normalize persisted and incoming jobs to the supported providers."""
    normalized = dict(config or {})

    # Generation V2 keeps the semantic identity of the content separate from
    # the filesystem-safe output name.  Older queue code assigned one sanitized
    # value to both ``name`` and ``title`` which destroyed listicle/topic
    # context before retrieval started.
    content_title = str(
        normalized.get('content_title')
        or normalized.get('title')
        or ''
    ).strip()
    output_name = str(
        normalized.get('output_name')
        or normalized.get('name')
        or content_title
        or 'Video'
    ).strip()
    if not content_title:
        content_title = output_name
    normalized['content_title'] = content_title
    normalized['output_name'] = output_name
    # Preserve the legacy keys as compatibility aliases without conflating
    # their meanings.
    normalized['title'] = content_title
    normalized['name'] = output_name

    format_hint = str(normalized.get('format_hint') or 'auto').strip().lower()
    supported_formats = {
        'auto', 'narrative', 'listicle', 'how_to', 'comparison', 'timeline',
        'q_and_a', 'dialogue', 'mixed_multi_subject',
    }
    normalized['format_hint'] = format_hint if format_hint in supported_formats else 'auto'
    normalized['language'] = str(normalized.get('language') or 'auto').strip() or 'auto'
    # TTS voices commonly use locale tags such as en-US, while Whisper's
    # language parameter accepts the ISO base code only.  Keep the locale for
    # the voice field, but normalize the transcription language so parallel
    # Whisper workers do not all fail and force a slow full-audio fallback.
    if normalized['language'].lower() not in {'auto', 'none'}:
        normalized['language'] = normalized['language'].split('-', 1)[0].split('_', 1)[0].lower()
    # All documentary requests use one narration beat per scene. Canonicalize
    # legacy values such as Adaptive and Every N Beats at the API boundary.
    normalized['scene_mode'] = 'Every Beat'
    has_media_mode = 'media_mode' in (config or {})
    normalized['media_mode'] = str(normalized.get('media_mode') or 'automatic').strip().lower()
    if not has_media_mode:
        normalized['_media_mode_defaulted'] = True
    normalized['youtube_rights_mode'] = str(
        normalized.get('youtube_rights_mode') or 'all_public'
    ).strip().lower()
    normalized['allow_relevant_stock'] = _generation_bool(
        normalized.get('allow_relevant_stock'),
        True,
    )
    normalized['allow_stock_last_resort'] = _generation_bool(
        normalized.get('allow_stock_last_resort'),
        True,
    )
    # Stock is the terminal real-media availability lane. Once web/YouTube
    # and their visual gates are exhausted, avoid paying for a second vision
    # request on the stock rescue; decode/safety/container checks still apply.
    normalized['stock_last_resort_no_vision'] = _generation_bool(
        normalized.get('stock_last_resort_no_vision'),
        True,
    )
    normalized['pipeline_v2_mode'] = str(
        normalized.get('pipeline_v2_mode')
        or os.environ.get('VIDZEO_PIPELINE_V2_MODE')
        or 'on'
    ).strip().lower()
    if normalized['pipeline_v2_mode'] not in {'off', 'shadow', 'on'}:
        normalized['pipeline_v2_mode'] = 'on'

    # Qwen Flash is the authoritative visual verifier in V2.  The call budget
    # is sized per scene later, once the final audio-aligned count is known.
    normalized['visual_understanding'] = _generation_bool(
        normalized.get('visual_understanding'),
        True,
    )
    normalized['visual_required'] = _generation_bool(
        normalized.get('visual_required'),
        # Vision remains enabled as a relevance checker, but an unavailable
        # or low-confidence model must not turn a healthy decoded asset into
        # a blank scene. Operators can opt into fail-closed verification per
        # job with ``visual_required=true``.
        _generation_bool(os.environ.get('VIDZEO_VISUAL_REQUIRED'), False),
    )
    normalized['qwen_vision_model'] = str(
        normalized.get('qwen_vision_model')
        or os.environ.get('VIDZEO_QWEN_VISION_MODEL')
        or 'qwen/qwen3.5-flash-02-23'
    ).strip()
    normalized['qwen_vision_fallback_model'] = str(
        normalized.get('qwen_vision_fallback_model')
        or os.environ.get('VIDZEO_QWEN_VISION_FALLBACK_MODEL')
        or 'qwen/qwen3.5-flash-02-23'
    ).strip()
    # Keep the legacy single fallback field for persisted UI compatibility,
    # while exposing a real multimodal chain for production transport failover.
    vision_chain_raw = normalized.get('qwen_vision_fallback_models')
    if vision_chain_raw is None:
        vision_chain_raw = os.environ.get(
            'VIDZEO_QWEN_VISION_FALLBACK_MODELS',
            'nvidia/nemotron-3.5-super-vl:free,google/gemma-4-26b-a4b-it:free,nex-agi/nex-n2.5-mini:free,openrouter/free',
        )
    if isinstance(vision_chain_raw, str):
        vision_chain_raw = [item.strip() for item in vision_chain_raw.replace(';', ',').split(',') if item.strip()]
    normalized['qwen_vision_fallback_models'] = list(vision_chain_raw or [normalized['qwen_vision_fallback_model']])
    # The source-query director uses a capable primary route and a verified,
    # ordered fallback chain. This is intentionally independent of the vision
    # model, which may remain a cheaper multimodal route.
    requested_scene_brain_model = str(
        normalized.get('scene_brain_model')
        or os.environ.get('VIDZEO_SCENE_BRAIN_MODEL')
        or 'nvidia/nemotron-3.5-super-vl:free'
    ).strip()
    # Preserve explicit models. Never migrate an inexpensive Qwen choice to GPT.
    normalized['scene_brain_model'] = requested_scene_brain_model
    fallback_models = normalized.get('scene_brain_fallback_models')
    if fallback_models is None:
        fallback_models = os.environ.get(
            'VIDZEO_SCENE_BRAIN_FALLBACK_MODELS',
            'nvidia/nemotron-3.5-super-vl:free',
        )
    if isinstance(fallback_models, str):
        fallback_models = [item.strip() for item in fallback_models.replace(';', ',').split(',') if item.strip()]
    normalized['scene_brain_fallback_models'] = list(fallback_models or [
        'nvidia/nemotron-3.5-super-vl:free',
    ])
    brain_chain_raw = normalized.get('scene_brain_fallback_chain')
    if brain_chain_raw is None:
        brain_chain_raw = os.environ.get(
            'VIDZEO_SCENE_BRAIN_FALLBACK_CHAIN',
            'nvidia/nemotron-3.5-super-vl:free',
        )
    if isinstance(brain_chain_raw, str):
        brain_chain_raw = [item.strip() for item in brain_chain_raw.replace(';', ',').split(',') if item.strip()]
    normalized['scene_brain_fallback_chain'] = list(brain_chain_raw or normalized['scene_brain_fallback_models'])

    # ``youtube_fair_use_mode`` could never establish fair use.  Keep the
    # legacy field false and expose the explicit rights mode instead.
    normalized['youtube_fair_use_mode'] = False
    normalized['yt_lenient_acceptance'] = _generation_bool(
        normalized.get('yt_lenient_acceptance'),
        False,
    )
    # Keep the historical compatibility flag intact, but give the active
    # YouTube lane its own recall default.  A normal Mixed Media job should
    # not lose plausible clips at the metadata-ranking boundary merely
    # because the legacy flag was omitted or persisted as false.
    normalized['youtube_recall_mode'] = _generation_bool(
        normalized.get('youtube_recall_mode'),
        _generation_bool(
            config.get('yt_lenient_acceptance'),
            True,
        ) if isinstance(config, dict) and 'yt_lenient_acceptance' in config else True,
    )
    # YouTube clips use a relevance-oriented final gate because they have
    # already passed local decode, quality, overlay, and safety checks. This
    # preserves real motion coverage when vision is uncertain without
    # permitting explicit wrong-entity/confusable clips.
    normalized['youtube_relaxed_visual_gate'] = _generation_bool(
        normalized.get('youtube_relaxed_visual_gate'),
        True,
    )
    normalized['youtube_fail_open_on_visual_unavailable'] = _generation_bool(
        normalized.get('youtube_fail_open_on_visual_unavailable'),
        True,
    )
    normalized['youtube_prefer_whisper_timing'] = _generation_bool(
        normalized.get('youtube_prefer_whisper_timing'),
        True,
    )
    normalized['strict_retrieval_accuracy'] = _generation_bool(
        normalized.get('strict_retrieval_accuracy'),
        True,
    )
    # A production job must report an unresolved media contract instead of
    # silently exporting a degraded stock-heavy timeline. The explicitly
    # enabled stock terminal lane still keeps the normal mixed workflow
    # deliverable when it can provide a real asset.
    normalized['fail_on_unresolved_media'] = _generation_bool(
        normalized.get('fail_on_unresolved_media'),
        True,
    )
    # Prefer verified real media, then use a restrained, script-grounded
    # Remotion graphic only after all relevant real-media lanes are exhausted.
    # This is a last-resort scene repair, not an alternative retrieval lane.
    normalized['global_media_guarantee'] = _generation_bool(
        normalized.get('global_media_guarantee'),
        _generation_bool(os.environ.get('VIDZEO_GLOBAL_MEDIA_GUARANTEE'), True),
    )
    fallback_policy = str(
        normalized.get('media_fallback_policy')
        or os.environ.get('VIDZEO_MEDIA_FALLBACK_POLICY')
        or ('grounded_graphic' if normalized['global_media_guarantee'] else 'real_media_only')
    ).strip().lower()
    normalized['media_fallback_policy'] = (
        fallback_policy if fallback_policy in {'real_media_only', 'grounded_graphic', 'none'} else 'real_media_only'
    )
    normalized['allow_grounded_graphic_fallback'] = _generation_bool(
        normalized.get('allow_grounded_graphic_fallback'),
        _generation_bool(
            os.environ.get('VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK'),
            normalized['global_media_guarantee'] and normalized['media_fallback_policy'] == 'grounded_graphic',
        ),
    )
    normalized['allow_grounded_graphic_fallback_strict'] = _generation_bool(
        normalized.get('allow_grounded_graphic_fallback_strict'),
        _generation_bool(
            os.environ.get('VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK_STRICT'),
            normalized['global_media_guarantee'] and normalized['media_fallback_policy'] == 'grounded_graphic',
        ),
    )
    try:
        fallback_retries = int(
            normalized.get('media_provider_retries')
            or os.environ.get('VIDZEO_MEDIA_PROVIDER_RETRIES')
            or 2
        )
    except (TypeError, ValueError):
        fallback_retries = 2
    normalized['media_provider_retries'] = max(1, min(3, fallback_retries))
    # Mixed Media retrieval policy: all available web lanes are eligible in a
    # bounded parallel wave, then the ordered rescue tiers run only for holes.
    normalized['cross_source_fallback'] = _generation_bool(
        normalized.get('cross_source_fallback'),
        _generation_bool(os.environ.get('VIDZEO_CROSS_SOURCE_FALLBACK'), True),
    )
    normalized['web_collect_all_sources'] = _generation_bool(
        normalized.get('web_collect_all_sources'),
        _generation_bool(os.environ.get('VIDZEO_WEB_COLLECT_ALL_SOURCES'), True),
    )
    normalized['unique_web_assets_required'] = _generation_bool(
        normalized.get('unique_web_assets_required'),
        _generation_bool(os.environ.get('VIDZEO_UNIQUE_WEB_ASSETS_REQUIRED'), True),
    )
    normalized['web_fallback_policy'] = str(
        normalized.get('web_fallback_policy')
        or os.environ.get('VIDZEO_WEB_FALLBACK_POLICY')
        or 'fast_primary>rights_and_html_rescue>editorial_site_rescue>slow_gallery_last_resort'
    ).strip()
    requested_tts_model = str(normalized.get('tts_model') or DEFAULT_TTS_MODEL).strip()
    if requested_tts_model not in SUPPORTED_TTS_MODELS:
        requested_tts_model = DEFAULT_TTS_MODEL
    normalized['tts_model'] = requested_tts_model
    normalized['vo_mode'] = (
        DEFAULT_VO_MODE
        if requested_tts_model == DEFAULT_TTS_MODEL
        else OPENROUTER_VO_MODE
    )
    normalized['whisper_mode'] = DEFAULT_WHISPER_MODE
    normalized['semantic_edit_enable'] = _generation_bool(
        normalized.get('semantic_edit_enable'),
        True,
    )
    normalized['semantic_render_enabled'] = _generation_bool(
        normalized.get('semantic_render_enabled'),
        True,
    )
    normalized['enable_sound_library'] = _generation_bool(
        normalized.get('enable_sound_library'),
        True,
    )
    normalized['enable_editorial_cues'] = _generation_bool(
        normalized.get('enable_editorial_cues'),
        True,
    )
    normalized['enable_motion_templates'] = _generation_bool(
        normalized.get('enable_motion_templates'),
        True,
    )

    # Drop provider secrets from jobs created by older builds. They are no
    # longer consumed and should not be carried into new queue snapshots.
    for key in list(normalized):
        compact_key = str(key).replace('_', '').lower()
        if compact_key.startswith('deep') and compact_key.endswith('apikey'):
            normalized.pop(key, None)

    # Documentary planning and query generation use OpenRouter. Discard
    # provider credentials left by older projects so they cannot leak into
    # queue snapshots or select a client that is not shipped in distributions.
    normalized.pop('groq_api_key', None)
    normalized.pop('groq_model', None)
    if str(normalized.get('llm_provider') or 'openrouter').lower() not in {'openrouter', 'external_agent', 'chatgpt'}:
        # Migrate obsolete saved projects; API admission still rejects new
        # unsupported provider selections before reaching this boundary.
        normalized['llm_provider'] = 'openrouter'
    from services.provider_selection import normalize_provider_config
    normalized = normalize_provider_config(normalized)
    if normalized['llm_provider'] == 'external_agent':
        normalized['vo_mode'] = DEFAULT_VO_MODE
    return normalized

# =============================================================================
# FONTS
# =============================================================================

# Font URLs from Google Fonts GitHub
FONT_URLS = {
    "BebasNeue-Regular": "https://raw.githubusercontent.com/dharmatype/Bebas-Neue/master/fonts/BebasNeue(2018)ByDhamraType/ttf/BebasNeue-Regular.ttf",
    "Anton-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    "Montserrat-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf",
    "Poppins-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf",
    "Poppins-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Regular.ttf",
    "Roboto-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/roboto/Roboto%5Bwdth%2Cwght%5D.ttf",
    "LilitaOne-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/lilitaone/LilitaOne-Regular.ttf",
    "FredokaOne-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/fredokaone/FredokaOne-Regular.ttf",
    "Creepster-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/creepster/Creepster-Regular.ttf",
    "Nosifer-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/nosifer/Nosifer-Regular.ttf",
    "Caveat-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/caveat/Caveat%5Bwght%5D.ttf",
    "Pacifico-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/pacifico/Pacifico-Regular.ttf",
    "DancingScript-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/dancingscript/DancingScript%5Bwght%5D.ttf",
    "PatrickHand-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/patrickhand/PatrickHand-Regular.ttf",
    "PlayfairDisplay-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "Cinzel-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf",
    "TitanOne-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/titanone/TitanOne-Regular.ttf",
    "Bungee-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/bungee/Bungee-Regular.ttf",
    "Righteous-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/righteous/Righteous-Regular.ttf",
    "Audiowide-Regular": "https://raw.githubusercontent.com/google/fonts/main/ofl/audiowide/Audiowide-Regular.ttf",
    "Orbitron-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/orbitron/Orbitron%5Bwght%5D.ttf",
    "ComicNeue-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/comicneue/ComicNeue-Bold.ttf",
    "OpenSans-Bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/opensans/OpenSans%5Bwdth%2Cwght%5D.ttf",
}

# Font categories for UI
FONT_CATEGORIES = {
    "⬛ Bold": ["BebasNeue-Regular", "Anton-Regular", "Roboto-Bold", "TitanOne-Regular", "Bungee-Regular"],
    "🔷 Modern": ["Montserrat-Bold", "Poppins-Bold", "Poppins-Regular"],
    "🟣 Bubbly": ["LilitaOne-Regular", "FredokaOne-Regular"],
    "👻 Horror": ["Creepster-Regular", "Nosifer-Regular"],
    "✍️ Handwriting": ["Caveat-Bold", "Pacifico-Regular", "DancingScript-Bold", "PatrickHand-Regular"],
    "🎩 Formal": ["PlayfairDisplay-Bold", "Cinzel-Bold"],
    "🎨 Creative": ["Righteous-Regular", "Audiowide-Regular", "Orbitron-Bold"],
    "😊 Informal": ["ComicNeue-Bold", "OpenSans-Bold", "Arial"],
}

# Font filename → Font family name (for ASS subtitles)
FONT_NAME_MAP = {
    "BebasNeue-Regular": "Bebas Neue",
    "Anton-Regular": "Anton",
    "Roboto-Bold": "Roboto",
    "TitanOne-Regular": "Titan One",
    "Bungee-Regular": "Bungee",
    "Montserrat-Bold": "Montserrat",
    "Poppins-Bold": "Poppins",
    "Poppins-Regular": "Poppins",
    "LilitaOne-Regular": "Lilita One",
    "FredokaOne-Regular": "Fredoka One",
    "Creepster-Regular": "Creepster",
    "Nosifer-Regular": "Nosifer",
    "Caveat-Bold": "Caveat",
    "Pacifico-Regular": "Pacifico",
    "DancingScript-Bold": "Dancing Script",
    "PatrickHand-Regular": "Patrick Hand",
    "PlayfairDisplay-Bold": "Playfair Display",
    "Cinzel-Bold": "Cinzel",
    "Righteous-Regular": "Righteous",
    "Audiowide-Regular": "Audiowide",
    "Orbitron-Bold": "Orbitron",
    "ComicNeue-Bold": "Comic Neue",
    "OpenSans-Bold": "Open Sans",
    "Arial": "Arial",
}

# Flatten fonts for dropdown
FONTS_FLAT = []
for category, fonts in FONT_CATEGORIES.items():
    for font in fonts:
        FONTS_FLAT.append(f"{category} | {font}")

# =============================================================================
# LLM PROVIDERS
# =============================================================================

DEFAULT_LLM_PROVIDER = 'openrouter'
DEFAULT_LLM_MODEL = 'nvidia/nemotron-3.5-super-vl:free'

LLM_PROVIDERS = {
    'openrouter': {'label': 'OpenRouter — Qwen 3.5 Flash',
                   'description': 'Qwen 3.5 Flash for all manual text and vision decisions.'}
}

LLM_MODELS = {'openrouter': {'Qwen 3.5 Flash': 'qwen/qwen3.5-flash-02-23'}}


# =============================================================================
# SHORT-FORM CONTENT (TikTok, YouTube Shorts, Instagram Reels)
# =============================================================================

# Short-form resolutions (Portrait 9:16 and 4:5)
SHORT_FORM_RESOLUTIONS = {
    '1080x1920 (TikTok/Shorts)': {'width': 1080, 'height': 1920},  # Standard 9:16 portrait
    '1080x1350 (Instagram 4:5)': {'width': 1080, 'height': 1350},  # Instagram 4:5 format
    '720x1280 (Fast)': {'width': 720, 'height': 1280},             # Faster processing
}

# Short-form specific durations
SHORT_FORM_DURATIONS = [
    "15 seconds", "30 seconds", "45 seconds", "60 seconds", "90 seconds"
]

# Short-form scene modes (beat-based, audio-synced)
SHORT_FORM_SCENE_MODES = [
    "Every Beat",
]

# Short-form specific effects - uses same names as EFFECTS (unified system)
# Subset of main EFFECTS that work best for short-form content
SHORT_FORM_EFFECTS = [
    'Zoom In', 'Zoom Out', 'Zoom Combo', 'Ken Burns',
    'Pan Left', 'Pan Right', 'Diagonal Drift',
    'Bounce Zoom', 'Spiral', 'Orbit',
    'Crossfade Zoom', 'Slide Left', 'Slide Right',
    'Dynamic', 'Alternate'
]

# Long-form scene modes (beat-based, audio-synced)
# Beats = natural speech segments (sentences/clauses) detected by Whisper
SCENE_COUNT_MODES = [
    'Every Beat',
]



# Richer, rendered, or illustrated styles
ANIMATED_STYLE_NAMES = [
    "Ghibli Stickman", "2D Animation Stickman", "2D Illustration",
    "3D Rendered Objects", "2D Textures", "2D Hand-Drawn",
    "2D Mini Characters", "2D Beige", "2D Faced",
    "Centered Hyperrealistic", "Multi-Grid Stickman", "Cute Stickman",
    "Avatar Painting", "Illustrated Face", "Animal Illustration", "Colored Painting"
]

# =============================================================================
# SHORT-FORM SCRIPT STYLES (Fast-Paced, Hook-Driven)
# =============================================================================

SHORT_FORM_SCRIPT_STYLES = {
    "viral_hook": {
        "name": "🔥 Viral Hook",
        "description": "Start with shocking hook, rapid-fire facts, strong CTA",
        "instructions": """
        CRITICAL RULES:
        - First sentence MUST be a shocking hook (3 seconds max)
        - Use 5-10 word sentences only
        - No fluff, no meta-language
        - Every sentence must add value
        - End with strong call-to-action
        - Optimize for maximum retention
        - Energy level: 9/10
        """
    },
    "fast_story": {
        "name": "⚡ Fast Story",
        "description": "Quick narrative arc, emotional peak, satisfying ending",
        "instructions": """
        CRITICAL RULES:
        - Hook in first 2 seconds
        - Set up problem immediately
        - Build tension quickly
        - Deliver emotional payoff
        - Complete story arc in 15-60 seconds
        - No unnecessary details
        """
    },
    "fact_bombs": {
        "name": "💥 Fact Bombs",
        "description": "Rapid succession of surprising facts, no fluff",
        "instructions": """
        CRITICAL RULES:
        - Lead with most shocking fact
        - One fact per sentence
        - Use numbers and statistics
        - Build excitement with each fact
        - End with mind-blowing revelation
        - Pace: rapid-fire
        """
    },
    "tutorial_quick": {
        "name": "🎯 Quick Tutorial",
        "description": "Problem → Solution in 30 seconds",
        "instructions": """
        CRITICAL RULES:
        - State problem in 3 seconds
        - Present solution immediately
        - Step-by-step (max 3-5 steps)
        - Visual and actionable
        - End with result/benefit
        - No theory, only practice
        """
    },
}

# =============================================================================
# SCRIPT GENERATION
# =============================================================================

SCRIPT_DURATIONS = [
    "30 seconds", "1 minute", "2 minutes", "3 minutes", "5 minutes",
    "10 minutes", "15 minutes", "30 minutes", "45 minutes",
    "1 hour", "1.5 hours", "2 hours", "3 hours"
]

# Planning speed for Audiogram narration.
WORDS_PER_MINUTE = 140

# Keywords that trigger automatic web research
RESEARCH_TRIGGERS = [
    "news", "latest", "recent", "current", "today", "2024", "2025", "update",
    "breaking", "new", "trending", "viral",
    "facts", "statistics", "stats", "data", "numbers", "study", "research",
    "percent", "million", "billion", "how many", "how much",
    "ai", "technology", "tech", "science", "discovery", "invention", "innovation",
    "crypto", "bitcoin", "blockchain", "smartphone", "app", "software",
    "market", "economy", "stock", "price", "cost", "investment", "business",
    "company", "startup", "billionaire", "richest",
    "country", "countries", "world", "global", "war", "election", "president",
    "government", "politics", "climate", "environment",
    "movie", "film", "celebrity", "famous", "star", "singer", "actor",
    "sport", "championship", "record", "winner"
]



# =============================================================================
# VIDEO PROCESSING
# =============================================================================

FPS = 30
CROSSFADE_DURATION = 0.4

EFFECTS = [
    # ── Position / Pan ──
    'Static', 'Pan Left', 'Pan Right', 'Pan Up', 'Pan Down',
    'Diagonal', 'Diagonal Drift', 'Ken Burns', 'Ken Burns Pro',
    'Slide Left', 'Slide Right', 'Slide Up', 'Slide Down',
    'Orbit', 'Figure 8', 'Sine Wave', 'Spiral Path',
    'Bounce Pan',

    # ── Scale / Zoom ──
    'Zoom In', 'Zoom Out', 'Zoom Combo',
    'Zoom Corner TL', 'Zoom Corner BR',
    'Breathing Scale', 'Bounce Zoom', 'Stretch X', 'Stretch Y',

    # ── Rotation ──
    'Slow Spin', 'Wobble', 'Pendulum',

    # ── Combinations ──
    'Zoom Pan', 'Spiral', 'Dolly Zoom',
    'Pan Vertical', 'Pan Horizontal',
    'Crossfade Zoom',

    # ── Multi-Phase ──
    'Dynamic', 'Master Combo',

    # ── Auto ──
    'Alternate',
]

ANIMATIONS = {
    "None": {"in": ""},
    "Fade": {"in": "\\fad(150,100)"},
    "Pop": {"in": "\\t(0,70,\\fscx112\\fscy112)\\t(70,140,\\fscx100\\fscy100)"},
    "Bounce": {"in": "\\t(0,50,\\fscx88\\fscy88)\\t(50,110,\\fscx118\\fscy118)\\t(110,160,\\fscx100\\fscy100)"},
    "Rise": {"in": "\\move(0,25,0,0,0,140)\\fad(120,0)"},
    "Pulse": {"in": "\\t(0,90,\\fscx110\\fscy110)\\t(90,180,\\fscx100\\fscy100)"},
    "Glitch": {"in": "\\t(0,35,\\fscx115\\fscy85)\\t(35,70,\\fscx90\\fscy115)\\t(70,110,\\fscx100\\fscy100)"},
    "Typewriter": {"in": "\\fad(50,0)"},
}

# =============================================================================
# CAPTION & TEXT CARD STYLES PRESETS
# =============================================================================
CAPTION_STYLES = {
    "hormozi": {
        "label": "Hormozi Viral",
        "font": "Montserrat-Bold",
        "fontsize": 52,
        "bold": True,
        "uppercase": True,
        "text_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_color": "#000000",
        "outline_size": 4,
        "shadow_on": True,
        "shadow_color": "#000000",
        "shadow_depth": 3,
        "bg_on": False,
        "animation": "Pop",
        "caption_mode": "smart_emphasis",
        "words_per_group": 3,
        "sfx": "whoosh_snap",
    },
    "mrbeast": {
        "label": "MrBeast High-Energy",
        "font": "LilitaOne-Regular",
        "fontsize": 54,
        "bold": True,
        "uppercase": True,
        "text_color": "#FFFFFF",
        "highlight_color": "#00FF66",
        "outline_color": "#000000",
        "outline_size": 5,
        "shadow_on": True,
        "shadow_color": "#000000",
        "shadow_depth": 3,
        "bg_on": False,
        "animation": "Bounce",
        "caption_mode": "smart_emphasis",
        "words_per_group": 3,
        "sfx": "impact_soft",
    },
    "cyberpunk": {
        "label": "Cyberpunk Neon",
        "font": "Orbitron-Bold",
        "fontsize": 48,
        "bold": True,
        "uppercase": True,
        "text_color": "#00E5FF",
        "highlight_color": "#FF007F",
        "outline_color": "#0D0D1A",
        "outline_size": 4,
        "shadow_on": True,
        "shadow_color": "#00E5FF",
        "shadow_depth": 2,
        "bg_on": False,
        "animation": "Glitch",
        "caption_mode": "karaoke",
        "words_per_group": 3,
        "sfx": "notification_up",
    },
    "cinematic_minimal": {
        "label": "Cinematic Minimalist",
        "font": "Poppins-Bold",
        "fontsize": 44,
        "bold": True,
        "uppercase": False,
        "text_color": "#F8F9FA",
        "highlight_color": "#FFD700",
        "outline_color": "#000000",
        "outline_size": 2,
        "shadow_on": False,
        "bg_on": True,
        "bg_color": "#000000",
        "bg_opacity": 75,
        "animation": "Rise",
        "caption_mode": "smart_emphasis",
        "words_per_group": 4,
        "sfx": "whoosh_soft",
    },
    "vox_documentary": {
        "label": "Vox Documentary",
        "font": "Roboto-Bold",
        "fontsize": 46,
        "bold": True,
        "uppercase": False,
        "text_color": "#FFFFFF",
        "highlight_color": "#FFB300",
        "outline_color": "#111111",
        "outline_size": 2,
        "shadow_on": True,
        "shadow_color": "#000000",
        "shadow_depth": 2,
        "bg_on": False,
        "animation": "Fade",
        "caption_mode": "smart_emphasis",
        "words_per_group": 4,
        "sfx": "paper_flip",
    },
    "karaoke_box": {
        "label": "Karaoke Jump Box",
        "font": "Poppins-Bold",
        "fontsize": 46,
        "bold": True,
        "uppercase": True,
        "text_color": "#FFFFFF",
        "highlight_color": "#8A2BE2",
        "outline_color": "#000000",
        "outline_size": 2,
        "shadow_on": False,
        "bg_on": True,
        "bg_color": "#000000",
        "bg_opacity": 60,
        "animation": "Pop",
        "caption_mode": "karaoke_box",
        "words_per_group": 3,
        "sfx": "ui_tick",
    },
    "classic": {
        "label": "Classic Subtitle",
        "font": "Arial",
        "fontsize": 42,
        "bold": True,
        "uppercase": False,
        "text_color": "#FFFFFF",
        "highlight_color": "#FFFF00",
        "outline_color": "#000000",
        "outline_size": 3,
        "shadow_on": True,
        "shadow_color": "#000000",
        "shadow_depth": 2,
        "bg_on": False,
        "animation": "None",
        "caption_mode": "line",
        "words_per_group": 5,
        "sfx": None,
    },
}

# =============================================================================
# IMAGE-SCENE ALIGNMENT SYSTEM (Zero-Misleading Images)
# =============================================================================
# Core principle: Image models understand only objects, people, actions,
# environments, lighting, and camera angles. They do NOT understand
# psychology, motivation, realization, success, failure, or mindset.

# Abstract concepts → Concrete visual metaphors
# Used to convert abstract scenes into photographable reality
ABSTRACT_TO_VISUAL_METAPHORS = {
    # Emotions & States
    "insecurity": "person looking down, shoulders slightly hunched, closed posture",
    "insecure": "person looking down, shoulders slightly hunched, closed posture",
    "confidence": "person standing upright with good posture, making eye contact, open stance",
    "confident": "person standing upright with good posture, making eye contact, open stance",
    "anxiety": "person with tense shoulders, hands clasped together, worried expression",
    "anxious": "person with tense shoulders, hands clasped together, worried expression",
    "fear": "person stepping back, wide eyes, defensive posture",
    "afraid": "person stepping back, wide eyes, defensive posture",
    "happiness": "person with genuine smile, relaxed shoulders, open body language",
    "happy": "person with genuine smile, relaxed shoulders, open body language",
    "sadness": "person with downcast eyes, drooping shoulders, looking away",
    "sad": "person with downcast eyes, drooping shoulders, looking away",
    "anger": "person with furrowed brow, clenched jaw, tense stance",
    "angry": "person with furrowed brow, clenched jaw, tense stance",
    "confusion": "person with tilted head, furrowed brow, hand on chin",
    "confused": "person with tilted head, furrowed brow, hand on chin",
    "surprise": "person with raised eyebrows, mouth slightly open, leaning back",
    "surprised": "person with raised eyebrows, mouth slightly open, leaning back",
    "shame": "person looking down and away, shoulders curved inward, avoiding eye contact",
    "ashamed": "person looking down and away, shoulders curved inward, avoiding eye contact",
    "guilt": "person with lowered head, hand touching forehead, slumped posture",
    "guilty": "person with lowered head, hand touching forehead, slumped posture",
    "pride": "person standing tall, chin slightly raised, confident smile",
    "proud": "person standing tall, chin slightly raised, confident smile",
    "embarrassment": "person with flushed face, hand covering mouth, looking sideways",
    "embarrassed": "person with flushed face, hand covering mouth, looking sideways",
    
    # Mental Processes (NEVER use directly - must convert)
    "realization": "person pausing mid-action, eyes widening, thoughtful expression",
    "realizes": "person pausing mid-action, eyes widening, thoughtful expression",
    "understanding": "person nodding slowly, relaxed face, leaning forward attentively",
    "understands": "person nodding slowly, relaxed face, leaning forward attentively",
    "thinking": "person with hand on chin, looking up or to the side, contemplative pose",
    "thinks": "person with hand on chin, looking up or to the side, contemplative pose",
    "remembering": "person looking into the distance, slight smile, unfocused gaze",
    "remembers": "person looking into the distance, slight smile, unfocused gaze",
    "decision": "person at a crossroads or doorway, looking between two options",
    "deciding": "person at a crossroads or doorway, looking between two options",
    "awareness": "person with alert posture, looking around observantly",
    "aware": "person with alert posture, looking around observantly",
    
    # Abstract Concepts
    "psychology": "person in conversation, showing body language and facial expressions",
    "motivation": "person in active stance, moving forward purposefully",
    "success": "person with arms raised in celebration, triumphant pose",
    "failure": "person with slumped shoulders, head bowed, defeated posture",
    "growth": "person standing taller, more confident stance than before",
    "change": "person in transition moment, between two states",
    "connection": "two people facing each other, engaged in conversation",
    "loneliness": "single person sitting alone, empty space around them",
    "stress": "person with hands on temples, tense shoulders, exhausted expression",
    "peace": "person with closed eyes, relaxed posture, serene expression",
    "mindset": "person in contemplative pose, natural setting",
    "personality": "person expressing natural gestures and expressions in daily life",
    
    # Social Behaviors
    "apologizing": "person with apologetic body language, slight bow, hands together",
    "over-apologizing": "person repeatedly gesturing with open palms, submissive posture",
    "listening": "person leaning forward, maintaining eye contact, nodding",
    "ignoring": "person turned away, looking elsewhere, closed body language",
    "agreeing": "person nodding, open expression, leaning toward speaker",
    "disagreeing": "person with crossed arms, shaking head slightly, pulled back",
    "lying": "person avoiding eye contact, touching face, shifting weight",
    "truth": "person with direct gaze, open palms, stable stance",
}

# Words that MUST NEVER appear in image prompts
# Image models cannot understand these concepts
FORBIDDEN_PROMPT_WORDS = [
    # Psychology/Mental
    "psychology", "psychological", "psychologically",
    "motivation", "motivated", "motivational",
    "realization", "realize", "realizing",
    "awareness", "aware", "unaware",
    "mindset", "mentality", "mental state",
    "consciousness", "subconscious", "unconscious",
    "perception", "perceive", "perceived",
    "cognition", "cognitive", "thinking about",
    
    # Abstract Success/Failure
    "success", "successful", "successfully",
    "failure", "failing", "failed",
    "achievement", "accomplish", "accomplishment",
    "goal", "ambition", "aspiration",
    "potential", "capability", "ability",
    
    # Emotions (as concepts, not expressions)
    "emotionally", "emotional intelligence",
    "feeling of", "sense of", "experience of",
    "inner", "internally", "within themselves",
    
    # Abstract Storytelling
    "emotional storytelling", "character development",
    "narrative", "metaphorically", "symbolically",
    "represents", "symbolizes", "means",
    "indicates", "suggests", "implies",
    
    # Self/Identity
    "self-esteem", "self-worth", "self-confidence",
    "personality trait", "character flaw",
    "inner strength", "inner weakness",
]

# Mandatory negative prompt - append to ALL image generation calls
# Reduces misleading visuals drastically
MANDATORY_NEGATIVE_PROMPT = """text, words, subtitles, logos, watermarks,
illustration style mixing, cartoon in realistic scene,
surreal elements, abstract art, symbolic imagery,
deformed body, extra limbs, missing limbs,
unrealistic proportions, distorted face,
floating objects, impossible physics,
multiple people when one requested,
wrong gender, wrong ethnicity, wrong age,
inconsistent lighting, mismatched shadows"""

# Default character anchor for maintaining consistency
DEFAULT_CHARACTER_ANCHOR = {
    "gender": "neutral",
    "age_range": "25-35",
    "ethnicity": "diverse/unspecified",
    "hair": "natural hair",
    "clothing": "casual modern clothes",
    "body_type": "average build",
}

# Visual Blueprint schema fields (for validation)
VISUAL_BLUEPRINT_FIELDS = [
    "scene_id",
    "scene_type",  # "visual" or "abstract"
    "visual_metaphor",  # Converted description
    "subject",
    "action_posture",
    "camera",
    "environment",
    "lighting",
    "time",
    "style",
]

# Allowed camera angles for prompts
CAMERA_ANGLES = [
    "extreme close-up",
    "close-up",
    "medium close-up",
    "medium shot",
    "medium wide shot",
    "wide shot",
    "extreme wide shot",
    "over-the-shoulder",
    "low angle",
    "high angle",
    "eye level",
]

# Allowed environments for prompts
ENVIRONMENTS = [
    "indoor home",
    "indoor office",
    "indoor cafe",
    "indoor restaurant",
    "indoor gym",
    "indoor studio",
    "outdoor street",
    "outdoor park",
    "outdoor nature",
    "outdoor urban",
    "neutral background",
    "gradient background",
]

# Lighting options
LIGHTING_OPTIONS = [
    "soft natural light",
    "harsh sunlight",
    "golden hour light",
    "overcast diffused light",
    "studio lighting",
    "dramatic side lighting",
    "backlit silhouette",
    "indoor ambient light",
    "warm artificial light",
    "cool fluorescent light",
]

# =============================================================================
# 4-LAYER NSFW PROTECTION (YOUTUBE-SAFE)
# =============================================================================
# These settings ensure images are safe for YouTube monetization.
# ANY violation = potential channel strikes.

# MINIMUM AGE LOCK - ABSOLUTE RULE
# Every human subject MUST be explicitly aged ≥25
MINIMUM_SUBJECT_AGE = 25

# =============================================================================
# LAYER 1: PROMPT HARDENING
# =============================================================================

# Words that statistically correlate with sexualized training data
# These MUST be removed/replaced from ALL prompts
RISKY_WORDS = [
    # Age-related risks (AI hallucinates young = sexualized)
    "young woman", "young girl", "young lady", "young female",
    "teen", "teenager", "youthful", "juvenile",
    "girl", "schoolgirl", "college girl",
    
    # Beauty triggers (AI associates beauty with sexualized poses)
    "beautiful", "gorgeous", "stunning", "attractive",
    "pretty", "lovely", "hot", "sexy", "seductive",
    "alluring", "enticing", "tempting",
    
    # Emotional triggers (these drift to intimacy)
    "emotional", "vulnerable", "lonely", "longing",
    "intimate", "passionate", "desire", "yearning",
    
    # Body-focused (even innocent terms drift)
    "soft body", "curves", "curvy", "body",
    "skin", "bare", "exposed", "revealing",
    "tight", "form-fitting", "figure",
    
    # Lighting that drifts to bedroom (VERY risky)
    "soft lighting", "dim lighting", "mood lighting",
    "romantic lighting", "candlelight", "warm glow",
    "bedroom lighting", "ambient bedroom",
    
    # Camera angles that sexualize
    "cinematic close-up", "close-up of face", "close-up woman",
    "low angle", "looking up at", "from below",
    
    # Settings that drift to NSFW
    "bedroom", "bed", "lying down", "lying in bed",
    "bathroom", "shower", "bath",
    "private", "alone at night",
    
    # Clothing risks
    "lingerie", "underwear", "nightwear", "sleepwear",
    "swimsuit", "bikini", "revealing clothes",
    "low-cut", "short skirt", "mini skirt",
    
    # Pose risks
    "sensual", "seductive pose", "provocative",
    "suggestive", "flirty", "coy",
    "looking over shoulder", "arched back",
]

# Safe replacements for risky words
# Format: "risky_phrase": "safe_replacement"
SAFE_REPLACEMENTS = {
    # Age fixes (ALWAYS upgrade to adult)
    "young woman": "adult woman (age 28)",
    "young girl": "adult woman (age 30)",
    "young lady": "adult woman (age 28)",
    "young female": "adult female professional (age 30)",
    "girl": "adult woman",
    "teen": "adult (age 28)",
    "teenager": "adult (age 28)",
    
    # Beauty neutralization
    "beautiful": "plain-looking",
    "gorgeous": "ordinary appearance",
    "stunning": "average-looking",
    "attractive": "professional appearance",
    "pretty": "neutral expression",
    "hot": "professional",
    "sexy": "professional",
    
    # Emotional neutralization
    "emotional": "neutral expression",
    "vulnerable": "composed",
    "lonely": "alone and calm",
    "longing": "waiting patiently",
    "intimate": "professional",
    "passionate": "focused",
    
    # Lighting fixes
    "soft lighting": "evenly lit",
    "dim lighting": "well-lit room",
    "mood lighting": "standard office lighting",
    "romantic lighting": "daylight",
    "candlelight": "lamp light",
    "bedroom lighting": "office lighting",
    
    # Camera fixes
    "cinematic close-up": "documentary-style medium shot",
    "close-up of face": "medium shot showing upper body",
    "cinematic": "documentary style",
    
    # Setting fixes
    "bedroom": "living room",
    "bed": "couch",
    "lying down": "sitting upright",
    "bathroom": "office",
    
    # Clothing fixes
    "revealing clothes": "professional attire",
    "tight": "loose-fitting",
    "form-fitting": "comfortable casual",
}

# =============================================================================
# LAYER 2: YOUTUBE-SAFE NEGATIVE PROMPT (NON-NEGOTIABLE)
# =============================================================================

# This MUST be appended to EVERY image generation call
# It catches what prompt hardening misses
YOUTUBE_SAFE_NEGATIVE_PROMPT = """nsfw, nude, naked, nudity,
cleavage, exposed chest, exposed skin, bare skin,
lingerie, underwear, bra, panties,
sexualized pose, erotic, sensual, seductive,
bedroom, bedroom scene, in bed, lying in bed,
romantic, intimate, passionate,
revealing clothes, tight clothes, low-cut, short skirt,
provocative, suggestive, flirty, coy,
fetish, bondage, leather, latex,
soft focus, dreamy, fantasy,
anime, illustration, cartoon character,
young, teenager, teen, child, minor,
cleavage, breasts, bust,
arched back, looking over shoulder,
wet, sweaty, glistening,
text, words, subtitles, logos, watermarks,
deformed, distorted, extra limbs, missing limbs,
unrealistic proportions"""

# =============================================================================
# LAYER 3: MODEL SAFETY FLAGS
# =============================================================================

# Safety parameters to pass to image generation APIs
MODEL_SAFETY_FLAGS = {
    "safety_checker": True,
    "nsfw_filter": "strict",
    "content_filter": True,
}

# =============================================================================
# LAYER 4: POST-GENERATION THRESHOLDS
# =============================================================================

# Lower threshold = stricter filtering
# YouTube requires VERY strict filtering (0.3 is conservative)
YOUTUBE_NSFW_THRESHOLD = 0.30  # Reject if ANY nsfw indicator > 0.30

# Maximum regeneration attempts before falling back to stock image
MAX_NSFW_REGEN_ATTEMPTS = 5

# Woman close-up restriction (70% of strikes come from this)
RESTRICT_WOMAN_CLOSEUPS = True
ALLOWED_WOMAN_CONTEXTS = [
    "doctor", "nurse", "teacher", "professor",
    "lawyer", "businesswoman", "executive", "manager",
    "scientist", "researcher", "engineer",
    "journalist", "reporter", "anchor",
    "athlete", "coach", "instructor",
    "chef", "artist", "musician",
]
