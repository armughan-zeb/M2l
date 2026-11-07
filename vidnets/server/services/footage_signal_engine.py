# ============================================================
# footage_signal_engine.py
# Universal footage vs noise detection system
# Works across ALL niches — no niche-specific logic required
# ============================================================

import re
import math
import subprocess
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor


# ============================================================
# S1 — NOISE CLASSIFIER
# The most important signal. Runs before anything else.
# Classifies titles into FOOTAGE / MIXED / NOISE
# based purely on linguistic patterns.
# ============================================================

# These title structures ALWAYS indicate a reporter/analyst
# talking to camera — never actual footage of the event
NOISE_TITLE_PATTERNS = [

    # Question formats — reporter explaining something
    r'^why ', r'^how ', r'^what ', r'^here\'s ',
    r'^is ', r'^will ', r'^can ', r'^should ',
    r'^does ', r'^did ', r'^are ', r'^was ',
    r'^who is ', r'^who are ',

    # Opinion/analysis structures
    r'explains?$', r'explained$', r'explains? how',
    r'explains? why', r'explains? what',
    r'what you need to know',
    r'everything you need',
    r'here\'s what',
    r'here\'s why',
    r'here\'s how',

    # Reaction/discussion formats
    r'reacts? to', r'responds? to', r'weighs? in',
    r'discusses?', r'talks? about', r'breaks? down',
    r'breaks? it down', r'deep dive', r'my thoughts on',
    r'opinions? on', r'the truth about', r'let\'s talk about',

    # Attribution quote formats (reporter quoting someone)
    r"^'[^']+'\s*:", r'^"[^"]+"\s*:',

    # Channel branding in title (broadcaster package)
    r'\|\s*cnn\s*$', r'\|\s*bbc\s*$', r'\|\s*fox\s*$',
    r'\|\s*msnbc\s*$', r'\|\s*cnbc\s*$', r'\|\s*abc\s*$',
    r'\|\s*nbc\s*$', r'\|\s*cbs\s*$', r'\|\s*sky\s*$',
    r'\|\s*wsj\s*$', r'\|\s*reuters\s*$',

    # Economic/political commentary patterns
    r'economists?', r'analysts? say', r'experts? say',
    r'market watch', r'fact.?check',
    r'the truth about', r'the real reason',
    r'you won\'t believe', r'shocking truth',

    # Branded news-package / studio formats
    r'\bbreaking news\b', r'\btop story\b', r'\bnews update\b',
    r'\bexclusive\b', r'\bnews bulletin\b', r'\bheadlines\b',
    r'\blive updates?\b',
    r'\bexclusive interview\b', r'\bpanel\b', r'\broundtable\b',
    r'\bpodcast\b', r'\banchor\b', r'\breporter\b', r'\bhost\b',
    r'\bwhat this means\b', r'\blive now\b',

    # Listicle/compilation commentary
    r'^\d+ reasons?', r'^\d+ things?',
    r'^\d+ ways?', r'^\d+ times?',
]

# These patterns indicate actual footage of real events
FOOTAGE_TITLE_PATTERNS = [

    # Subject doing something (event footage)
    r'\b(?:trump|biden|putin|zelensky|modi|xi)\b.{0,30}\b(?:signs?|announces?|speaks?|addresses?|meets?|holds?|delivers?|arrives?|departs?|visits?)\b',
    r'\bpress (?:conference|briefing|statement)\b',
    r'\bsigning ceremony\b', r'\bexecutive order signing\b',
    r'\blive\b.{0,20}\b(?:footage|video|stream|broadcast)\b',
    r'\braw footage\b', r'\bunedited\b', r'\buncut\b',
    r'\bwithout commentary\b', r'\bwithout narration\b',
    r'\bofficial footage\b', r'\bdirect feed\b',
    r'\bfull remarks\b', r'\bfull address\b', r'\bpublic address\b',
    r'\bstatement in full\b', r'\bopening remarks\b',
    r'\bon the ground\b', r'\bground footage\b', r'\bfield footage\b',
    r'\bbodycam\b', r'\bdashcam\b', r'\bhelmet cam\b',
    r'\bcaught on camera\b', r'\bscene footage\b',
    r'\bc-?span\b',

    # Physical locations/events (indicates footage, not studio)
    r'\bwhite house\b', r'\boval office\b', r'\bcapitol\b',
    r'\brose garden\b', r'\bunited nations\b',
    r'\bfactory\b', r'\bwarehouse\b', r'\bport\b',
    r'\btrading floor\b', r'\bwall street\b', r'\bnyse\b',
    r'\bempty shelf\b', r'\bempty shelves\b',
    r'\bshipping container\b', r'\bcargo ship\b',
    r'\bfarm\b.{0,20}\b(?:footage|video|protest|rally)\b',

    # Sports footage signals
    r'\bfull (?:match|game|innings|fight|race|highlights)\b',
    r'\b(?:match|game) (?:highlights|footage)\b',
    r'\blive (?:match|game|coverage)\b',
    r'\bgoal\b', r'\bwicker\b', r'\bknockout\b',

    # War/history footage signals
    r'\bcombat footage\b', r'\bwar footage\b',
    r'\barchiv(?:al|e) footage\b', r'\bnewsreel\b',
    r'\bcoloriz(?:ed|ation)\b', r'\brestored footage\b',
    r'\bbattle of\b', r'\bsiege of\b',
    r'\bdrone footage\b', r'\bsatellite footage\b',

    # Science/space footage signals
    r'\blaunch footage\b', r'\blaunch video\b',
    r'\b4k\b.{0,20}\b(?:footage|video)\b',
    r'\baerial\b', r'\bdrone footage\b',
    r'\btimelapse\b', r'\btime.?lapse\b',

    # Nature/wildlife footage signals
    r'\bwildlife footage\b', r'\bnature footage\b',
    r'\b(?:bbc earth|national geographic|nat geo)\b',
]


def classify_noise_vs_footage(title, channel='', duration=0):
    """
    Core classifier. Returns:
        ('FOOTAGE', score)   — actual event/raw footage
        ('MIXED', score)     — could be either
        ('NOISE', score)     — reporter/analyst package

    score = confidence (0-10)
    """
    title_lower = title.lower()
    channel_lower = channel.lower()

    noise_hits: int = 0
    footage_hits: int = 0

    # Check noise patterns
    for pattern in NOISE_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            noise_hits += 1

    # Check footage patterns
    for pattern in FOOTAGE_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            footage_hits += 2  # footage signals weighted 2x

    # Channel DNA (see S2 below)
    channel_type, channel_weight = get_channel_dna(channel_lower)
    if channel_type == 'BROADCASTER':
        noise_hits += 15  # Extremely heavy penalty to force NOISE class
    elif channel_type == 'ARCHIVE':
        footage_hits += 8
    elif channel_type == 'OFFICIAL_SOURCE':
        footage_hits += 8
    elif channel_type == 'SPORTS_OFFICIAL':
        # Official sports = highlights packages, not raw footage
        # Useful but not pure footage
        noise_hits += 1
        footage_hits += 1

    if any(signal in title_lower for signal in (
        'raw footage', 'actual footage', 'real footage', 'archive footage',
        'archival footage', 'uncut', 'without commentary', 'without narration',
        'full speech', 'full remarks', 'public address', 'statement in full',
        'official footage', 'direct feed', 'press conference full',
    )):
        footage_hits += 3

    if any(signal in title_lower for signal in (
        'analysis', 'explained', 'panel', 'podcast', 'interview',
        'reporter', 'anchor', 'host', 'breaking news', 'news update',
        'what this means', 'exclusive interview', 'reaction', 'reacts',
        'discusses', 'opinions', 'my thoughts', 'debate', 'pundit', 'commentary',
    )):
        noise_hits += 10  # Heavily penalize talking head buzzwords

    # Duration fingerprint (see S3)
    dur_class = classify_duration(duration)
    if dur_class == 'CLIP':       # under 3 min
        footage_hits += 2
    elif dur_class == 'PACKAGE':  # 3-8 min (news package length)
        noise_hits += 2
    elif dur_class == 'LONG':     # over 30 min
        if channel_type != 'BROADCASTER':
            footage_hits += 1  # long = likely full event or documentary

    # Determine classification
    if footage_hits > noise_hits + 2:
        confidence = min(footage_hits, 10)
        return 'FOOTAGE', confidence
    elif noise_hits > footage_hits + 1:
        confidence = min(noise_hits, 10)
        return 'NOISE', confidence
    else:
        confidence = min(max(footage_hits, noise_hits), 5)
        return 'MIXED', confidence


# ============================================================
# S2 — CHANNEL DNA
# Every YouTube channel has a type. The type determines
# what kind of content it produces — always.
# ============================================================

CHANNEL_DNA = {

    # BROADCASTER — always produces reporter packages
    # These NEVER upload raw footage
    'BROADCASTER': [
        'cnn', 'bbc news', 'bbc', 'fox news', 'msnbc',
        'nbc news', 'abc news', 'cbs news', 'cnbc',
        'bloomberg', 'sky news', 'al jazeera english',
        'france 24', 'dw news', 'euronews', 'channel 4 news',
        'channel 5 news', 'itv news', 'sky sports news',
        'espn', 'beinsports', 'sky sports', 'bt sport',
        'star sports', 'sony sports', 'ten sports',
        'the hill', 'axios', 'politico', 'vox', 'vice news',
        'business insider', 'the guardian', 'washington post',
        'new york times', 'the economist', 'wall street journal',
        'financial times', 'usa today', 'wion', 'times now',
        'india today', 'geo news', 'ary news', 'samaa tv',
        'dunya news', 'express news', 'bol news',
    ],

    # ARCHIVE — exclusively raw/archival footage, always safe
    'ARCHIVE': [
        'british pathé', 'british pathe', 'ap archive',
        'critical past', 'periscope film', 'us national archives',
        'getty images', 'reuters events', 'afp news agency',
        'universal newsreels', 'the film archives',
        'footage farm', 'historic films',
    ],

    # OFFICIAL_SOURCE — primary source footage (govts, orgs)
    'OFFICIAL_SOURCE': [
        'the white house', 'whitehouse', 'c-span', 'cspan',
        'nasa', 'esa', 'spacex', 'noaa',
        'united nations', 'un web tv',
        'european parliament', 'uk parliament',
        'pentagon channel', 'us army', 'us navy',
        'us air force',
    ],

    # SPORTS_OFFICIAL — highlights, not pure raw footage
    'SPORTS_OFFICIAL': [
        'icc', 'cricket australia', 'ecb cricket', 'bcci',
        'pcb cricket', 'nzc', 'cricket west indies',
        'fifa', 'uefa', 'premier league', 'la liga',
        'bundesliga', 'serie a', 'nba', 'nfl', 'nhl',
        'mlb', 'ufc', 'wwe', 'f1', 'motogp',
    ],

    # DOCUMENTARY — produced docs, some footage, some narration
    'DOCUMENTARY': [
        'dw documentary', 'al jazeera documentary',
        'real stories', 'spark', 'timeline',
        'absolute history', 'war archives',
        'history hit', 'smithsonian channel',
        'national geographic', 'bbc earth',
        'netflix documentary', 'hbo documentary',
    ],
}


def get_channel_dna(channel_lower):
    """Returns (channel_type, weight)"""
    for dna_type, channels in CHANNEL_DNA.items():
        if any(ch in channel_lower for ch in channels):
            weights = {
                'BROADCASTER':    -6,
                'ARCHIVE':        +6,
                'OFFICIAL_SOURCE': +5,
                'SPORTS_OFFICIAL': 0,
                'DOCUMENTARY':    0,
            }
            return dna_type, weights[dna_type]
    return 'UNKNOWN', 0


def get_source_originality_score(title: str, channel: str, channel_type: str) -> int:
    """
    Prefer first-hand/originating sources over second-hand repackages.

    First-hand means the uploader is likely the event owner, official source,
    archive custodian, or direct capture source. Broadcasters and studio/news
    brands are treated as second-hand unless the title strongly suggests a raw
    feed.
    """
    title_lower = title.lower()
    channel_lower = channel.lower()

    first_hand_score = 0

    if channel_type == 'OFFICIAL_SOURCE':
        first_hand_score += 14
    elif channel_type == 'ARCHIVE':
        first_hand_score += 10
    elif channel_type == 'BROADCASTER':
        first_hand_score -= 14
    elif channel_type == 'DOCUMENTARY':
        first_hand_score -= 4

    direct_capture_signals = [
        'raw', 'raw footage', 'live feed', 'full briefing', 'full remarks',
        'full speech', 'full ceremony', 'official stream', 'official footage',
        'archival footage', 'archive footage', 'unedited', 'uncut',
        'c-span', 'white house', 'pentagon', 'un web tv',
    ]
    if any(signal in title_lower for signal in direct_capture_signals):
        first_hand_score += 10

    repost_signals = [
        'breaking news', 'news update', 'analysis', 'explained', 'report',
        'watch', 'latest', 'headline', 'debate', 'panel', 'exclusive',
        'warning', 'reaction', 'responds', 'what this means',
    ]
    if any(signal in title_lower for signal in repost_signals):
        first_hand_score -= 10

    watermark_style_channels = [
        'news', 'tv', 'network', 'channel', 'times', 'today'
    ]
    if channel_type == 'UNKNOWN' and any(sig in channel_lower for sig in watermark_style_channels):
        first_hand_score -= 5

    return max(-18, min(18, first_hand_score))


# ============================================================
# S2B — UNIVERSAL VISUAL INTENT POLICY
# Classifies whether a candidate is the clip/footage the scene needs, or
# a second-hand package such as commentary, news, stickers, or streamer clips.
# ============================================================

_RAW_VISUAL_SIGNALS = (
    "raw footage", "actual footage", "real footage", "archive footage",
    "archival footage", "official footage", "direct feed", "pool feed",
    "without commentary", "no commentary", "without narration", "uncut",
    "unedited", "full match", "full game", "match footage", "gameplay",
    "official gameplay", "walkthrough no commentary", "4k gameplay",
)

_TEMPLATE_JUNK_SIGNALS = (
    "sticker", "stickers", "png", "transparent", "green screen",
    "greenscreen", "overlay", "template", "logo pack", "wallpaper",
    "status video", "whatsapp status", "edit pack", "editing pack",
    "scene pack", "scenepack", "clips for editing", "for editing",
    "capcut", "topaz", "amv", "velocity edit", "twixtor",
)

_REACTION_SIGNALS = (
    "reacts to", "reaction", "reacting to", "my reaction", "our reaction",
    "first time watching", "watching", "watch along", "watchalong",
)

_STREAMER_SIGNALS = (
    "streamer", "streamers", "livestream", "live stream", "stream highlights",
    "twitch", "kick stream", "facecam", "face cam", "webcam",
)

_COMMENTARY_PACKAGE_SIGNALS = (
    "analysis", "breakdown", "explained", "explains", "review",
    "discussion", "discusses", "podcast", "panel", "debate",
    "opinion", "commentary", "what this means", "the truth about",
    "everything you need to know", "deep dive", "leak explained",
    "how to play", "play early", "details revealed", "features revealed",
    "features that are", "almost certainly", "broke gaming",
)

_NEWS_PACKAGE_SIGNALS = (
    "breaking news", "news update", "latest update", "report", "reports",
    "reporter", "anchor", "headline", "headlines", "coverage", "rumor",
    "rumour", "leak", "leaks",
)

_GAMING_FOOTAGE_SIGNALS = (
    "gameplay", "official gameplay", "no commentary", "without commentary",
    "walkthrough", "mission", "free roam", "driving", "police chase",
    "trailer", "official trailer", "reveal trailer", "game footage",
)

_SYNTHETIC_FOOTAGE_SIGNALS = (
    "animation", "animated", "cgi", "3d remake", "3d recreation",
    "recreation", "reenactment", "re-enactment", "dramatization",
    "docudrama", "simulation", "render", "official animation",
    "ai generated", "ai-generated", "virtual tour",
)


def classify_candidate_visual_intent(
    title: str,
    channel: str = "",
    description: str = "",
    duration: int = 0,
    expected_niche: str = "",
    scene_text: str = "",
    allows_commentary: bool = False,
):
    """Return a dict describing footage-vs-noise fit for any niche.

    The returned score_delta is intentionally large enough to prevent "topic
    match" from beating the wrong content type. Hard rejects are reserved for
    media that is almost never useful as scene footage.
    """
    title_lower = (title or "").lower()
    channel_lower = (channel or "").lower()
    description_lower = (description or "").lower()
    text = f"{title_lower} {description_lower} {channel_lower}"
    scene_lower = (scene_text or "").lower()
    niche = (expected_niche or "").lower()
    channel_type, _ = get_channel_dna(channel_lower)

    scene_asks_news = any(term in scene_lower for term in ("news", "report", "headline", "broadcast"))
    scene_asks_reaction = any(term in scene_lower for term in ("reaction", "reacts", "commentary", "podcast", "interview"))
    scene_asks_trailer = any(term in scene_lower for term in ("trailer", "teaser", "reveal"))
    allow_package = allows_commentary or scene_asks_news or scene_asks_reaction

    reasons = []
    category = "clean_footage"
    hard_reject = False
    score_delta = 0.0

    raw_hits = [term for term in _RAW_VISUAL_SIGNALS if term in text]
    template_hits = [term for term in _TEMPLATE_JUNK_SIGNALS if term in text]
    reaction_hits = [term for term in _REACTION_SIGNALS if term in text]
    streamer_hits = [term for term in _STREAMER_SIGNALS if term in text]
    commentary_hits = [term for term in _COMMENTARY_PACKAGE_SIGNALS if term in text]
    news_hits = [term for term in _NEWS_PACKAGE_SIGNALS if term in text]
    gaming_hits = [term for term in _GAMING_FOOTAGE_SIGNALS if term in text]

    if template_hits:
        category = "template_or_sticker"
        hard_reject = True
        score_delta -= 90.0
        reasons.append(f"template_junk:{template_hits[0].replace(' ', '_')}")

    if reaction_hits and not allow_package:
        category = "reaction_commentary"
        score_delta -= 95.0
        reasons.append(f"reaction_package:{reaction_hits[0].replace(' ', '_')}")

    if streamer_hits and "no commentary" not in text and not allow_package:
        category = "streamer_or_facecam"
        score_delta -= 95.0
        reasons.append(f"streamer_facecam:{streamer_hits[0].replace(' ', '_')}")

    if commentary_hits and not allow_package and not raw_hits:
        category = "commentary_package"
        score_delta -= 48.0
        reasons.append(f"commentary_package:{commentary_hits[0].replace(' ', '_')}")

    if news_hits and not allow_package and not raw_hits:
        category = "news_or_leak_package"
        score_delta -= 42.0
        reasons.append(f"news_package:{news_hits[0].replace(' ', '_')}")

    if channel_type == "BROADCASTER" and not allow_package and not raw_hits:
        category = "broadcaster_package"
        score_delta -= 36.0
        reasons.append("broadcaster_package")

    if niche == "gaming":
        gaming_title_noise = any(term in title_lower for term in (
            "how to play", "play early", "details revealed", "features revealed",
            "features that are", "almost certainly", "broke gaming",
            "before release", "leak explained", "news update", "release date",
            "change gaming forever", "could freeze", "freeze the world",
            "i found gameplay", "found gameplay footage", "trailer music",
            "ai gameplay concept", "gameplay concept", "concept trailer",
            "fan concept", "concept", "music @rockstargames",
        ))
        gaming_roundup_noise = (
            ("leaked" in title_lower or "leak" in title_lower)
            and ("," in title_lower or "news" in title_lower or "details" in title_lower)
        )
        gaming_leak_noise = (
            ("leaked" in title_lower or "leak" in title_lower)
            and "official" not in title_lower
            and "rockstar games" not in channel.lower()
        )
        if (gaming_title_noise or gaming_roundup_noise or gaming_leak_noise) and not allow_package:
            category = "gaming_news_or_explainer"
            score_delta -= 120.0
            reasons.append("gaming_news_or_explainer_rank_penalty")
        if gaming_hits:
            score_delta += 24.0
            reasons.append(f"gaming_footage_signal:{gaming_hits[0].replace(' ', '_')}")
        if "no commentary" in text or "without commentary" in text:
            score_delta += 16.0
            reasons.append("no_commentary_gameplay")
        if ("trailer" in title_lower or "teaser" in title_lower) and not scene_asks_trailer and "gameplay" not in title_lower:
            score_delta -= 60.0
            reasons.append("trailer_when_scene_wants_gameplay")
        if any(term in text for term in ("news", "leak", "update", "explained", "breakdown")) and not allow_package:
            score_delta -= 38.0
            reasons.append("gaming_news_or_explainer")

    if raw_hits:
        score_delta += min(28.0, 10.0 + len(raw_hits) * 5.0)
        reasons.append(f"raw_visual_signal:{raw_hits[0].replace(' ', '_')}")

    synthetic_hits = [term for term in _SYNTHETIC_FOOTAGE_SIGNALS if term in text]
    if synthetic_hits and not raw_hits and niche != "gaming" and not allow_package:
        category = "synthetic_or_reenactment"
        score_delta -= 48.0
        reasons.append(f"synthetic_media:{synthetic_hits[0].replace(' ', '_')}")

    if duration and duration < 12:
        score_delta -= 24.0
        reasons.append("too_short_for_scene_clip")

    return {
        "category": category,
        "hard_reject": bool(hard_reject),
        "score_delta": round(score_delta, 2),
        "reasons": reasons,
    }


# ============================================================
# S3 — DURATION FINGERPRINT
# News packages cluster around 2-6 minutes.
# Raw event footage is either very short (<90s)
# or very long (>20min for full events).
# ============================================================

def classify_duration(duration):
    if not duration:
        return 'UNKNOWN'
    if duration < 90:
        return 'CLIP'       # short raw clip
    if duration < 480:
        return 'PACKAGE'    # news package length
    if duration < 1800:
        return 'MEDIUM'     # documentary/full highlight
    return 'LONG'           # full event/archive


def get_duration_score(duration, niche):
    """
    Score duration fit. Niche-aware sweet spots.
    Returns 0-8.
    """
    SWEET_SPOTS = {
        'war':        (20, 600),
        'history':    (120, 1800),
        'cricket':    (60, 900),
        'football':   (30, 600),
        'basketball': (30, 480),
        'mma_boxing': (20, 360),
        'space':      (30, 900),
        'science':    (60, 1800),
        'technology': (60, 1200),
        'geopolitics':(30, 600),
        'finance':    (60, 1200),
        'crime':      (120, 1800),
        'music':      (60, 600),
        'gaming':     (30, 600),
        'food':       (30, 600),
        'travel':     (30, 900),
        'nature':     (30, 1800),
        'general':    (60, 900),
    }
    lo, hi = SWEET_SPOTS.get(niche, (60, 900))

    if lo <= duration <= hi:
        return 8.0
    elif duration < lo:
        return max(2.0, 6.0 - (lo - duration) / 20.0)
    else:
        return max(2.0, 6.0 - (duration - hi) / 300.0)


# ============================================================
# S4 — TITLE VERB INTENT
# The verb in a title tells you whether the title describes
# an event (footage) or an explanation (noise).
# "Trump SIGNS order" = event. "Why Trump signed" = noise.
# ============================================================

# Verbs that describe an event happening (footage exists)
EVENT_VERBS = {
    'signs', 'signed', 'announces', 'announced', 'launches',
    'launched', 'invades', 'invaded', 'attacks', 'attacked',
    'strikes', 'struck', 'arrests', 'arrested', 'meets', 'met',
    'addresses', 'addressed', 'speaks', 'spoke', 'delivers',
    'delivered', 'fires', 'fired', 'resigns', 'resigned',
    'wins', 'won', 'loses', 'lost', 'scores', 'scored',
    'hits', 'hit', 'reaches', 'reached', 'falls', 'fell',
    'crashes', 'crashed', 'collapses', 'collapsed',
    'erupts', 'erupted', 'floods', 'flooded',
    'lands', 'landed', 'launches', 'launched',
    'protests', 'marches', 'rallies', 'votes', 'voted',
    'captures', 'captured', 'reveals', 'revealed', 'unveils', 'unveiled',
    'discovers', 'discovered', 'observes', 'observed', 'detects', 'detected',
}

# Verbs that describe explanation (noise)
EXPLANATION_VERBS = {
    'explains', 'explain', 'explained',
    'analyzes', 'analyze', 'analysed',
    'discusses', 'discuss', 'discussed',
    'breaks down', 'broke down',
    'examines', 'examine', 'examined',
    'uncovers', 'uncover', 'uncovered',
    'investigates', 'investigate',
    'explores', 'explore', 'explored',
}


def get_verb_intent_score(title):
    """
    Returns positive score for event verbs,
    negative for explanation verbs.
    Range: -6 to +6
    """
    words = set(title.lower().split())
    bigrams = set()
    title_words = title.lower().split()
    for i in range(len(title_words) - 1):
        bigrams.add(f"{title_words[i]} {title_words[i+1]}")

    score: int = 0
    for verb in EVENT_VERBS:
        if verb in words or verb in bigrams:
            score += 3
    for verb in EXPLANATION_VERBS:
        if verb in words or verb in bigrams:
            score -= 3

    return max(-6, min(6, score))


# ============================================================
# S5 — CLICKBAIT DETECTOR
# Clickbait = noise. Pure footage doesn't need
# sensationalist titles. Hard penalty.
# ============================================================

CLICKBAIT_EMOJIS = [
    '🚨', '😡', '🔥', '⚡', '💥', '🤯', '😱', '👀',
    '🤦', '🤬', '😤', '💀', '☠️', '🧨', '💣', '⚠️',
    '❗', '‼️', '❌', '🛑', '📢', '📣',
]

CLICKBAIT_PHRASES = [
    'you won\'t believe', 'shocking', 'mind blowing',
    'gone wrong', 'goes wrong', 'nobody is talking about',
    'they don\'t want you to know', 'the truth about',
    'what they\'re not telling you', 'jaw dropping',
    'insane', 'unbelievable', 'this is bad',
    'oh no', 'uh oh', 'holy moly',
    'wait for it', 'watch till the end',
    'must watch', 'must see',
]


def get_clickbait_penalty(title):
    """Returns 0 to -12 penalty."""
    title_lower = title.lower()
    penalty = 0

    # Emoji penalty
    emoji_count = sum(1 for e in CLICKBAIT_EMOJIS if e in title)
    penalty -= emoji_count * 3

    # ALL CAPS words (2+ in title = sensationalist)
    caps_words = sum(
        1 for w in title.split()
        if w.isupper() and len(w) > 2 and w.isalpha()
    )
    if caps_words >= 2:
        penalty -= caps_words * 2

    # Clickbait phrases
    for phrase in CLICKBAIT_PHRASES:
        if phrase in title_lower:
            penalty -= 4

    return max(penalty, -12)


# ============================================================
# S6 — NICHE VISUAL MAP + QUERY TARGETING
# Every niche has a specific visual vocabulary.
# Searches are built to target that vocabulary directly.
# ============================================================

NICHE_VISUAL_MAP = {

    'war': {
        'visual_subjects': [
            'combat footage', 'war footage', 'battle footage',
            'military footage', 'frontline footage',
            'archival footage', 'newsreel', 'documentary footage',
        ],
        'avoid_subjects': [
            'explained', 'analysis', 'breakdown', 'documentary explained',
        ],
        'footage_query_terms': ['footage', 'combat', 'archival', 'film'],
    },

    'history': {
        'visual_subjects': [
            'historical footage', 'archival footage', 'documentary',
            'colorized footage', 'restored film', 'newsreel',
        ],
        'avoid_subjects': ['explained', 'analysis', 'lecture'],
        'footage_query_terms': ['footage', 'documentary', 'archival', 'film'],
    },

    'cricket': {
        'visual_subjects': [
            'match highlights', 'innings highlights', 'full innings',
            'batting footage', 'bowling footage', 'match footage',
        ],
        'avoid_subjects': ['explained', 'analysis', 'podcast'],
        'footage_query_terms': ['highlights', 'footage', 'innings', 'match'],
    },

    'football': {
        'visual_subjects': [
            'match highlights', 'goals', 'full match', 'match footage',
            'skills footage', 'training footage',
        ],
        'avoid_subjects': ['explained', 'analysis', 'reaction'],
        'footage_query_terms': ['highlights', 'goals', 'footage', 'match'],
    },

    'basketball': {
        'visual_subjects': [
            'game highlights', 'dunks', 'full game', 'plays',
            'mix', 'footage',
        ],
        'avoid_subjects': ['explained', 'breakdown', 'reaction'],
        'footage_query_terms': ['highlights', 'mix', 'footage', 'plays'],
    },

    'mma_boxing': {
        'visual_subjects': [
            'fight footage', 'knockout', 'full fight', 'highlights',
            'sparring footage', 'training footage',
        ],
        'avoid_subjects': ['explained', 'reaction', 'breakdown'],
        'footage_query_terms': ['footage', 'knockout', 'highlights', 'fight'],
    },

    'geopolitics': {
        'visual_subjects': [
            'press conference', 'speech', 'signing ceremony',
            'summit footage', 'live address', 'statement',
            'protest footage', 'rally footage',
        ],
        'avoid_subjects': ['explained', 'analysis', 'breakdown', 'reaction'],
        'footage_query_terms': ['footage', 'speech', 'live', 'press conference'],
    },

    'finance': {
        'visual_subjects': [
            'trading floor footage', 'wall street footage',
            'stock market footage', 'nyse footage',
            'press conference', 'earnings call footage',
        ],
        'avoid_subjects': ['explained', 'analysis', 'breakdown'],
        'footage_query_terms': ['footage', 'trading floor', 'live', 'market'],
    },

    'space': {
        'visual_subjects': [
            'launch footage', 'launch video', '4k footage',
            'nasa footage', 'rocket launch', 'spacewalk footage',
            'iss footage', 'mission footage',
        ],
        'avoid_subjects': ['explained', 'analysis'],
        'footage_query_terms': ['footage', 'launch', '4k', 'nasa'],
    },

    'science': {
        'visual_subjects': [
            'experiment footage', 'lab footage', 'wildlife footage',
            '4k footage', 'nature footage', 'documentary footage',
        ],
        'avoid_subjects': ['explained', 'lecture'],
        'footage_query_terms': ['footage', '4k', 'documentary', 'experiment'],
    },

    'nature': {
        'visual_subjects': [
            '4k wildlife footage', 'nature footage', 'wildlife documentary',
            'aerial footage', 'drone footage', 'timelapse',
        ],
        'avoid_subjects': ['explained', 'analysis'],
        'footage_query_terms': ['4k footage', 'wildlife', 'nature', 'drone'],
    },

    'food': {
        'visual_subjects': [
            'cooking footage', 'food footage', '4k food',
            'recipe video', 'chef footage', 'street food footage',
        ],
        'avoid_subjects': ['explained', 'reaction', 'review'],
        'footage_query_terms': ['footage', '4k', 'cooking', 'recipe'],
    },

    'travel': {
        'visual_subjects': [
            'travel footage', '4k footage', 'cinematic footage',
            'aerial footage', 'drone footage', 'city footage',
        ],
        'avoid_subjects': ['vlog explained', 'analysis'],
        'footage_query_terms': ['footage', '4k', 'cinematic', 'drone'],
    },

    'gaming': {
        'visual_subjects': [
            'gameplay footage', 'highlights', 'clutch plays',
            'montage', 'esports footage', 'tournament footage',
        ],
        'avoid_subjects': ['review', 'explained', 'analysis'],
        'footage_query_terms': ['gameplay', 'highlights', 'footage', 'montage'],
    },

    'music': {
        'visual_subjects': [
            'live performance', 'concert footage', 'music video',
            'live session', 'studio footage', 'backstage footage',
        ],
        'avoid_subjects': ['review', 'reaction', 'analysis'],
        'footage_query_terms': ['live', 'performance', 'concert', 'footage'],
    },

    'crime': {
        'visual_subjects': [
            'documentary footage', 'court footage', 'press conference',
            'arrest footage', 'crime scene footage', 'trial footage',
        ],
        'avoid_subjects': ['explained', 'reaction', 'podcast'],
        'footage_query_terms': ['footage', 'documentary', 'court', 'trial'],
    },

    'general': {
        'visual_subjects': ['footage', 'video', 'documentary'],
        'avoid_subjects': ['explained', 'analysis'],
        'footage_query_terms': ['footage', 'video', 'documentary'],
    },
}


def build_footage_queries(topic, niche):
    """
    Build 3 targeted queries per group.
    Each query specifically targets footage vocabulary.
    """
    visual_map = NICHE_VISUAL_MAP.get(niche, NICHE_VISUAL_MAP['general'])
    footage_terms = visual_map['footage_query_terms']

    queries = []

    # Query 1: topic + primary footage term
    queries.append(f"{topic} {footage_terms[0]}")

    # Query 2: topic + secondary footage term
    if len(footage_terms) > 1:
        queries.append(f"{topic} {footage_terms[1]}")

    # Query 3: raw topic (catches exact matches not using footage terms)
    queries.append(topic)

    return queries


# ============================================================
# S7 — FOOTAGE SCORE (combined signal output, 0-40)
# This is the DOMINANT term in the final score.
# A NOISE-classified clip can never outscore a FOOTAGE clip
# on this dimension alone, regardless of keyword matches.
# ============================================================

def compute_footage_score(noise_class, noise_confidence,
                           verb_score, clickbait_penalty,
                           channel_type):
    """
    Combines S1-S5 into a single footage score.
    Range: -10 to 40.
    Negative is possible for strong noise signals.
    """

    # Base score from classification
    BASE_SCORES = {
        'FOOTAGE': 30,
        'MIXED':   15,
        'NOISE':    0,
    }
    base = BASE_SCORES.get(noise_class, 0)

    # Confidence modifier (±0 to ±8)
    if noise_class == 'FOOTAGE':
        conf_mod = min(noise_confidence, 8)
    elif noise_class == 'NOISE':
        conf_mod = -min(noise_confidence, 8)
    else:
        conf_mod = 0

    # Verb intent (+6 to -6)
    verb_mod = verb_score

    # Channel bonus
    channel_mods = {
        'ARCHIVE':        +7,
        'OFFICIAL_SOURCE': +6,
        'SPORTS_OFFICIAL': 0,
        'DOCUMENTARY':    +1,
        'BROADCASTER':    -8,
        'UNKNOWN':         0,
    }
    channel_mod = channel_mods.get(channel_type, 0)

    total = base + conf_mod + verb_mod + channel_mod + clickbait_penalty
    return max(-10, min(40, total))


# ============================================================
# S8 — CLIP-TYPE TIMESTAMP RESOLVER
# Timestamp strategy based on classified clip type.
# No transcripts. No external APIs. Instant.
# ============================================================

def get_smart_timestamp(noise_class, duration,
                         scene_index, total_scenes):
    """
    Returns timestamp in seconds based on clip classification
    and scene position. Never returns 0.
    """
    if not duration or duration == 0:
        duration = 180

    if noise_class == 'FOOTAGE':
        # Pure footage — spread across the full usable range
        # Multiple scenes from same clip get different positions
        usable_start = max(int(duration * 0.05), 3)
        usable_end   = int(duration * 0.90)
        usable_range = usable_end - usable_start

        if total_scenes <= 1:
            return usable_start + int(usable_range * 0.25)

        ratio = scene_index / max(total_scenes - 1, 1)
        return usable_start + int(usable_range * ratio)

    elif noise_class == 'NOISE':
        # News package — b-roll section is 15-40% in
        # Skip anchor intro (first ~15s)
        if duration < 90:
            return 12
        b_roll_start = max(15, int(duration * 0.12))
        b_roll_end   = int(duration * 0.45)
        b_roll_range = b_roll_end - b_roll_start

        if total_scenes <= 1:
            return b_roll_start + int(b_roll_range * 0.3)

        ratio = scene_index / max(total_scenes - 1, 1)
        return b_roll_start + int(b_roll_range * ratio)

    else:
        # MIXED — use middle-safe zone
        safe_start = max(int(duration * 0.10), 8)
        safe_end   = int(duration * 0.80)
        safe_range = safe_end - safe_start

        if total_scenes <= 1:
            return safe_start + int(safe_range * 0.25)

        ratio = scene_index / max(total_scenes - 1, 1)
        return safe_start + int(safe_range * ratio)


# ============================================================
# MASTER SCORER — combines all 8 signals
# ============================================================

def score_candidate_master(candidate, anchors, niche):
    """
    Master scoring function.
    Footage purity is the dominant term (0-40).
    A NOISE clip with perfect keyword match
    still loses to a FOOTAGE clip with zero keywords.
    """
    title    = str(candidate.get('title', ''))
    duration = int(candidate.get('duration', 0) or 0)
    channel  = str(candidate.get('channel', '') or candidate.get('uploader', ''))
    views    = int(candidate.get('view_count', 0) or 0)
    title_lower = title.lower()
    channel_lower = channel.lower()

    # ── Run all signal detectors ──────────────────────────────

    # S1: Noise classification
    noise_class, noise_conf = classify_noise_vs_footage(
        title, channel, duration
    )

    # S2: Channel DNA
    channel_type, _ = get_channel_dna(channel.lower())

    # S4: Verb intent
    verb_score = get_verb_intent_score(title)

    # S5: Clickbait
    clickbait_penalty = get_clickbait_penalty(title)

    # ── Compute footage score (S7, dominant term) ─────────────
    footage_score = compute_footage_score(
        noise_class, noise_conf,
        verb_score, clickbait_penalty,
        channel_type
    )

    # ── Hard gate: reject pure noise before anchor scoring ────
    if noise_class == 'NOISE' and footage_score < 5:
        candidate['score'] = -999
        candidate['noise_class'] = noise_class
        candidate['rejected'] = True
        return -999

    # ── Anchor/keyword relevance (0-20) ──────────────────────
    anchor_score = 0
    for anchor in anchors:
        if anchor.lower() in title_lower:
            # Weight anchors by specificity
            if re.match(r'\d{2,4}', anchor):   # year or number
                anchor_score += 3
            elif len(anchor) > 8:               # specific term
                anchor_score += 2
            else:                               # short/common term
                anchor_score += 1
    anchor_score = min(anchor_score, 20)

    # ── Duration fit (0-8) ────────────────────────────────────
    dur_score = get_duration_score(duration, niche)

    # ── View signal (0-4, log scale) ─────────────────────────
    view_score = min(math.log10(views + 1), 4) if views > 0 else 0

    # ── Language penalty ──────────────────────────────────────
    lang_penalty = 0
    FOREIGN_SIGNALS = [
        'urdu', 'hindi', 'arabic', 'in urdu', 'in hindi',
        'русский', 'deutsch', 'français', 'español',
        'türkçe', '中文', 'हिंदी', 'اردو', 'বাংলা',
    ]
    if any(s in title_lower or s in channel_lower for s in FOREIGN_SIGNALS):
        lang_penalty = -20

    source_originality = get_source_originality_score(title, channel, channel_type)

    package_penalty = 0
    package_signals = [
        'news', 'analysis', 'explained', 'warning', 'debate', 'update',
        'live updates', 'breaking', 'headlines', 'watch', 'report',
    ]
    if channel_type == 'BROADCASTER':
        package_penalty -= 8
    if any(signal in title_lower for signal in package_signals):
        package_penalty -= 4

    # ── Final score ───────────────────────────────────────────
    total = (footage_score + anchor_score + dur_score
             + view_score + lang_penalty + source_originality + package_penalty)

    candidate['score']       = total
    candidate['noise_class'] = noise_class
    candidate['clip_type']   = noise_class
    candidate['score_breakdown'] = {
        'footage': f"{noise_class}({footage_score})",
        'anchor':  anchor_score,
        'dur':     round(float(dur_score), 1),
        'views':   round(float(view_score), 1),
        'lang':    lang_penalty,
        'source':  source_originality,
        'package': package_penalty,
        'total':   round(float(total), 1),
    }

    return total


# ============================================================
# FULL PIPELINE — search → classify → score → select → timestamp
# ============================================================

def process_group_full_pipeline(group_id, topic, anchors,
                                 niche, scene_count):
    """
    Complete group processing pipeline.
    Returns list of scene assignments with timestamps.
    """
    print(f"\n🔍 Group '{group_id}' [niche={niche}]")
    
    # We will implement this inside searcher.py and app.py 
    # directly using the functions above. This stub is here
    # primarily to serve as a reference architecture.
    pass
