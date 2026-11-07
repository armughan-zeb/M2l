import math
import re

try:
    import emoji as _emoji_lib
except Exception:
    _emoji_lib = None


_EMOJI_FALLBACK_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)


def _count_emojis(text: str) -> int:
    if not text:
        return 0
    if _emoji_lib is not None:
        try:
            return int(_emoji_lib.emoji_count(text))
        except Exception:
            pass
    return len(_EMOJI_FALLBACK_RE.findall(text))

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1: UNIVERSAL NICHE DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

NICHE_TAXONOMY = {

    # ── CONFLICT & HISTORY ────────────────────────────────────
    'war': {
        'keywords': [
            'war', 'battle', 'invasion', 'invade', 'military',
            'troops', 'soldiers', 'combat', 'conflict', 'attack',
            'bombing', 'siege', 'offensive', 'frontline', 'operation',
            'navy', 'air force', 'army', 'nuclear', 'missile',
            'tank', 'wwii', 'ww2', 'vietnam', 'iraq', 'afghanistan',
            'ukraine', 'nato', 'cold war', 'civil war', 'revolution',
        ],
        'tone': 'cinematic',
        'visual_intent': 'raw_footage_primary',
        'search_suffix': ['footage', 'combat footage', 'documentary'],
        'preferred_clip_types': ['RAW_SHORT', 'RAW_LONG', 'DOCUMENTARY'],
        'reject_clip_types': ['COMMENTARY', 'REACTION'],
        'duration_sweet_spot': (30, 600),
    },
    'history': {
        'keywords': [
            'history', 'historical', 'ancient', 'empire', 'dynasty',
            'cricket', 'batting', 'bowling', 'wicket', 'century',
            'odi', 'test match', 't20', 'ipl', 'innings',
            'six', 'four', 'stumps', 'caught', 'lbw', 'run chase',
            'babar', 'kohli', 'rohit', 'root', 'warner', 'smith',
        ],
        'tone': 'energetic',
        'reject_clip_types': ['COMMENTARY', 'REACTION'],
        'duration_sweet_spot': (30, 600),
    },
    'basketball': {
        'keywords': [
            'nba', 'basketball', 'dunk', 'three pointer', 'lebron',
            'curry', 'jordan', 'kobe', 'playoffs', 'finals', 'slam dunk',
            'crossover', 'assist', 'rebound', 'block', 'steal',
        ],
        'tone': 'energetic',
        'visual_intent': 'highlights_primary',
        'search_suffix': ['highlights', 'mix', 'moments', 'footage'],
        'preferred_clip_types': ['COMPILATION', 'RAW_SHORT'],
        'reject_clip_types': ['COMMENTARY'],
        'duration_sweet_spot': (30, 480),
    },
    'mma_boxing': {
        'keywords': [
            'mma', 'ufc', 'boxing', 'knockout', 'ko', 'fight',
            'fighter', 'champion', 'title fight', 'round', 'punch',
            'kick', 'submission', 'grappling', 'welterweight',
            'heavyweight', 'conor', 'fury', 'canelo', 'usyk',
        ],
        'tone': 'intense',
        'visual_intent': 'raw_footage_primary',
        'search_suffix': ['highlights', 'knockout', 'fight footage'],
        'preferred_clip_types': ['COMPILATION', 'RAW_SHORT'],
        'reject_clip_types': ['COMMENTARY'],
        'duration_sweet_spot': (20, 360),
    },

    # ── SCIENCE & TECHNOLOGY ─────────────────────────────────
    'space': {
        'keywords': [
            'space', 'nasa', 'rocket', 'launch', 'astronaut', 'moon',
            'mars', 'planet', 'galaxy', 'black hole', 'satellite',
            'iss', 'spacex', 'orbit', 'telescope', 'nebula', 'cosmos',
            'shuttle', 'mission', 'hubble', 'james webb',
        ],
        'tone': 'awe_inspiring',
        'visual_intent': 'cinematic_primary',
        'search_suffix': ['footage', '4k', 'launch footage', 'documentary'],
        'preferred_clip_types': ['RAW_SHORT', 'DOCUMENTARY', 'CINEMATIC'],
        'reject_clip_types': ['COMMENTARY', 'REACTION'],
        'duration_sweet_spot': (30, 900),
    },
    'technology': {
        'keywords': [
            'ai', 'artificial intelligence', 'machine learning', 'robot',
            'tech', 'software', 'hardware', 'chip', 'processor', 'gpu',
            'quantum', 'computer', 'coding', 'programming', 'startup',
            'silicon valley', 'apple', 'google', 'microsoft', 'openai',
            'chatgpt', 'tesla', 'electric vehicle', 'ev', 'autonomous',
        ],
        'tone': 'informative',
        'visual_intent': 'demonstration_primary',
        'search_suffix': ['explained', 'documentary', 'footage', 'demo'],
        'preferred_clip_types': ['DOCUMENTARY', 'DEMONSTRATION', 'NEWS_REPORT'],
        'reject_clip_types': ['REACTION'],
        'duration_sweet_spot': (60, 1200),
    },
    'automotive': {
        'keywords': [
            'mercedes-benz trucks', 'mercedes benz trucks', 'mercedes truck',
            'mercedes-benz truck', 'actros', 'arocs', 'eactros', 'atego',
            'unimog', 'zetros', 'econic', 'truck', 'trucks', 'lorry',
            'semi truck', 'tractor trailer', 'haulage', 'longhaul',
            'construction truck', 'dump truck', 'electric truck',
            'commercial vehicle', 'fleet', 'logistics', 'heavy duty',
            'offroad truck', 'vehicle', 'car', 'automotive', 'engine',
            'ev', 'electric vehicle', 'autonomous vehicle',
        ],
        'tone': 'industrial_cinematic',
        'visual_intent': 'demonstration_primary',
        'search_suffix': ['official footage', 'driving footage', 'launch video', 'walkaround'],
        'preferred_clip_types': ['RAW_SHORT', 'RAW_LONG', 'DOCUMENTARY', 'DEMONSTRATION'],
        'reject_clip_types': ['COMMENTARY', 'REACTION', 'NEWS_REPORT'],
        'duration_sweet_spot': (30, 900),
    },
    'science': {
        'keywords': [
            'science', 'biology', 'chemistry', 'physics', 'experiment',
            'research', 'discovery', 'nature', 'animal', 'wildlife',
            'ocean', 'climate', 'evolution', 'dna', 'cell', 'brain',
            'earthquake', 'volcano', 'hurricane', 'tsunami',
        ],
        'tone': 'educational',
        'visual_intent': 'documentary_primary',
        'search_suffix': ['documentary', 'footage', '4k', 'explained'],
        'preferred_clip_types': ['DOCUMENTARY', 'RAW_SHORT', 'CINEMATIC'],
        'reject_clip_types': ['COMMENTARY'],
        'duration_sweet_spot': (60, 1800),
    },

    # ── CURRENT AFFAIRS & POLITICS ───────────────────────────
    'geopolitics': {
        'keywords': [
            'trump', 'biden', 'tariff', 'sanction', 'trade war',
            'china', 'russia', 'nato', 'g7', 'g20', 'un',
            'president', 'white house', 'congress', 'senate',
            'executive order', 'policy', 'diplomat', 'summit',
            'election', 'vote', 'geopolitics', 'foreign policy',
            'import', 'export', 'trade deal', 'embargo',
        ],
        'tone': 'analytical',
        'visual_intent': 'event_footage_primary',
        
        # What you ACTUALLY want to see for geopolitics
        'footage_search_terms': [
            'press conference', 'speech', 'signing ceremony',
            'footage', 'live', 'statement', 'address',
        ],
        
        'preferred_clip_types': ['PURE_FOOTAGE', 'MIXED'],
        'reject_clip_types':    ['NEWS_PACKAGE'],
        'duration_sweet_spot':  (30, 600),
    },
    'crime': {
        'keywords': [
            'crime', 'murder', 'criminal', 'serial killer', 'heist',
            'prison', 'trial', 'court', 'verdict', 'police', 'fbi',
            'investigation', 'detective', 'case', 'evidence', 'suspect',
            'true crime', 'documentary', 'cold case', 'unsolved',
        ],
        'tone': 'dark_investigative',
        'visual_intent': 'documentary_primary',
        'search_suffix': ['documentary', 'footage', 'case', 'explained'],
        'preferred_clip_types': ['DOCUMENTARY', 'NEWS_REPORT', 'RAW_SHORT'],
        'reject_clip_types': ['REACTION', 'COMMENTARY'],
        'duration_sweet_spot': (120, 1800),
    },

    # ── ENTERTAINMENT ────────────────────────────────────────
    'music': {
        'keywords': [
            'music', 'song', 'album', 'singer', 'rapper', 'band',
            'concert', 'live performance', 'music video', 'grammy',
            'billboard', 'pop', 'hip hop', 'rock', 'jazz', 'classical',
            'producer', 'studio', 'tour', 'festival',
        ],
        'tone': 'energetic',
        'visual_intent': 'performance_primary',
        'search_suffix': ['live', 'performance', 'concert footage', 'official'],
        'preferred_clip_types': ['RAW_SHORT', 'COMPILATION', 'RAW_LONG'],
        'reject_clip_types': ['COMMENTARY', 'REACTION'],
        'duration_sweet_spot': (60, 600),
    },
    'gaming': {
        'keywords': [
            'gaming', 'game', 'gameplay', 'playthrough', 'speedrun',
            'esports', 'tournament', 'ps5', 'xbox', 'pc gaming',
            'minecraft', 'fortnite', 'gta', 'call of duty', 'valorant',
            'twitch', 'streamer', 'youtuber', 'highlights', 'clutch',
            'video game', 'videogame', 'gamers', 'console', 'playstation',
            'nintendo', 'steam', 'grand theft auto', 'gta vi', 'gta v',
            'rockstar games', 'vice city', 'game trailer',
        ],
        'tone': 'energetic',
        'visual_intent': 'gameplay_primary',
        'search_suffix': ['gameplay', 'highlights', 'footage', 'moments'],
        'preferred_clip_types': ['COMPILATION', 'RAW_SHORT', 'RAW_LONG'],
        'reject_clip_types': ['COMMENTARY'],
        'duration_sweet_spot': (30, 600),
    },

    # ── LIFESTYLE & EDUCATION ────────────────────────────────
    'finance': {
        'keywords': [
            'finance', 'stock', 'market', 'investment', 'crypto',
            'bitcoin', 'economy', 'recession', 'inflation', 'bank',
            'wall street', 'trading', 'hedge fund', 'startup', 'ipo',
            'billionaire', 'wealth', 'money', 'budget', 'debt',
        ],
        'tone': 'analytical',
        'visual_intent': 'news_documentary_primary',
        'search_suffix': ['explained', 'documentary', 'news', 'analysis'],
        'preferred_clip_types': ['DOCUMENTARY', 'NEWS_REPORT', 'ANIMATION'],
        'reject_clip_types': ['REACTION'],
        'duration_sweet_spot': (60, 1200),
    },
    'food': {
        'keywords': [
            'food', 'cooking', 'recipe', 'chef', 'restaurant', 'cuisine',
            'baking', 'grilling', 'kitchen', 'ingredients', 'dish',
            'michelin', 'street food', 'bbq', 'dessert', 'breakfast',
        ],
        'tone': 'warm',
        'visual_intent': 'cinematic_primary',
        'search_suffix': ['footage', '4k food', 'cooking footage', 'recipe'],
        'preferred_clip_types': ['CINEMATIC', 'RAW_SHORT', 'DOCUMENTARY'],
        'reject_clip_types': ['COMMENTARY'],
        'duration_sweet_spot': (30, 600),
    },
    'travel': {
        'keywords': [
            'travel', 'destination', 'country', 'city', 'tourism',
            'adventure', 'explore', 'visit', 'culture', 'landscape',
            'beach', 'mountain', 'backpacking', 'vlog', 'road trip',
        ],
        'tone': 'inspiring',
        'visual_intent': 'cinematic_primary',
        'search_suffix': ['4k', 'footage', 'cinematic', 'travel footage'],
        'preferred_clip_types': ['CINEMATIC', 'RAW_SHORT', 'DOCUMENTARY'],
        'reject_clip_types': ['COMMENTARY', 'REACTION'],
        'duration_sweet_spot': (30, 900),
    },
    'nature': {
        'keywords': [
            'nature', 'wildlife', 'animal', 'forest', 'ocean', 'river',
            'mountain', 'desert', 'rainforest', 'arctic', 'safari',
            'predator', 'prey', 'migration', 'ecosystem', 'bbc earth',
            'national geographic', 'attenborough',
        ],
        'tone': 'awe_inspiring',
        'visual_intent': 'cinematic_primary',
        'search_suffix': ['4k footage', 'wildlife footage', 'documentary', 'bbc'],
        'preferred_clip_types': ['CINEMATIC', 'RAW_SHORT', 'DOCUMENTARY'],
        'reject_clip_types': ['COMMENTARY', 'REACTION'],
        'duration_sweet_spot': (30, 1800),
    },
    'animation': {
        'keywords': [
            'animated', 'animation', 'cartoon', 'episode', 'scene', 'clip',
            'sitcom', 'the simpsons', 'simpsons', 'homer simpson',
            'bart simpson', 'marge simpson', 'lisa simpson',
            'maggie simpson', 'springfield elementary', 'springfield',
            'moe tavern', 'mr burns', 'montgomery burns', 'smithers',
            'edna krabappel', 'principal skinner', 'comic book guy',
        ],
        'tone': 'entertainment',
        'visual_intent': 'fictional_scene_primary',
        'search_suffix': ['clip', 'scene', 'episode', 'moments'],
        'preferred_clip_types': ['RAW_SHORT', 'COMPILATION'],
        'reject_clip_types': ['NEWS_REPORT', 'COMMENTARY'],
        'duration_sweet_spot': (20, 900),
    },
    'film_tv': {
        'keywords': [
            'movie', 'film', 'tv show', 'series', 'episode', 'season',
            'character', 'characters', 'scene', 'clip', 'sitcom', 'drama',
            'trailer', 'behind the scenes', 'cast',
        ],
        'tone': 'entertainment',
        'visual_intent': 'fictional_scene_primary',
        'search_suffix': ['clip', 'scene', 'episode', 'trailer'],
        'preferred_clip_types': ['RAW_SHORT', 'COMPILATION'],
        'reject_clip_types': ['NEWS_REPORT'],
        'duration_sweet_spot': (20, 900),
    },

    # ── FALLBACK ─────────────────────────────────────────────
    'general': {
        'keywords': [],
        'tone': 'neutral',
        'visual_intent': 'balanced',
        'search_suffix': ['footage', 'video', 'documentary'],
        'preferred_clip_types': ['DOCUMENTARY', 'RAW_SHORT', 'NEWS_REPORT'],
        'reject_clip_types': [],
        'duration_sweet_spot': (60, 900),
    },
}


def _keyword_in_text(keyword: str, text: str) -> bool:
    keyword = (keyword or "").strip().lower()
    if not keyword or not text:
        return False
    escaped = re.escape(keyword).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9]){escaped}(?:s|es|d|ed|ing)?(?![a-z0-9])", text))


def score_niches(title: str, script_text: str = '') -> dict:
    """Return taxonomy-driven niche scores for arbitrary text."""
    combined_title = (title or "").lower()
    combined_script = (script_text or "").lower()
    scores = {}
    for niche, config in NICHE_TAXONOMY.items():
        if niche == 'general':
            continue
        score = 0
        for keyword in config.get('keywords', []):
            if _keyword_in_text(keyword, combined_title):
                score += 3
            elif _keyword_in_text(keyword, combined_script):
                score += 1
        scores[niche] = score
    return scores


def detect_niche_conflict(expected_text: str, candidate_text: str, minimum_score: int = 3, detected_niche: str = ""):
    """Detect strong candidate drift using the shared niche taxonomy."""
    def _sports_override(text: str) -> tuple[str, int]:
        lower = (text or "").lower()
        cricket_terms = ("cricket", "kohli", "babar", "batting", "bowling", "wicket", "innings", "century", "icc", "t20", "odi", "ipl")
        football_terms = ("football", "soccer", "fifa", "world cup", "goal", "goals", "striker", "premier league", "champions league")
        cricket_score = sum(1 for term in cricket_terms if _keyword_in_text(term, lower))
        football_score = sum(1 for term in football_terms if _keyword_in_text(term, lower))
        if cricket_score >= 2 and cricket_score > football_score:
            return "cricket", cricket_score + 3
        if football_score >= 2 and football_score > cricket_score:
            return "football", football_score + 3
        return "", 0

    expected_scores = score_niches(expected_text or "")
    candidate_scores = score_niches(candidate_text or "")
    if not expected_scores or not candidate_scores:
        return False, "", ""

    expected_niche, expected_score = max(expected_scores.items(), key=lambda item: item[1])
    candidate_niche, candidate_score = max(candidate_scores.items(), key=lambda item: item[1])
    override_expected, override_expected_score = _sports_override(expected_text or "")
    override_candidate, override_candidate_score = _sports_override(candidate_text or "")
    if override_expected:
        expected_niche, expected_score = override_expected, override_expected_score
    if override_candidate:
        candidate_niche, candidate_score = override_candidate, override_candidate_score
    if expected_score <= 0 or candidate_score < minimum_score:
        return False, expected_niche, candidate_niche
    
    # History is a broad meta-niche that overlaps with many subjects (e.g. history of football, space, etc.)
    if expected_niche == 'history' or candidate_niche == 'history':
        return False, expected_niche, candidate_niche

    if candidate_niche == expected_niche:
        return False, expected_niche, candidate_niche
    if candidate_scores.get(expected_niche, 0) >= candidate_score:
        return False, expected_niche, candidate_niche
    if detected_niche and candidate_scores.get(detected_niche, 0) >= candidate_score:
        return False, expected_niche, candidate_niche
    return True, expected_niche, candidate_niche


def detect_niche(title, script_text=''):
    """
    Score every niche by keyword matches in title + script.
    Title matches count 3×, script matches count 1×.
    Returns best niche with confidence score.
    """
    combined_title = title.lower()
    combined_script = script_text.lower() if script_text else ''
    combined = f"{combined_title} {combined_script}"

    gaming_anchor_terms = (
        "video game", "videogame", "gaming", "gamers", "gameplay",
        "playthrough", "console", "playstation", "xbox", "nintendo",
        "steam", "grand theft auto", "gta vi", "gta v", "rockstar games",
        "vice city", "call of duty", "fortnite", "minecraft", "valorant",
    )
    if any(_keyword_in_text(term, combined) for term in gaming_anchor_terms):
        return 'gaming', 20

    football_anchor_terms = (
        "football", "footballer", "footballers", "soccer", "fifa",
        "world cup", "champions league", "premier league", "la liga",
        "goal", "goals", "striker", "midfielder", "ronaldo", "messi",
        "mbappe", "haaland", "bellingham", "harry kane", "neymar",
        "salah", "lewandowski", "de bruyne",
    )
    football_score = sum(1 for term in football_anchor_terms if _keyword_in_text(term, combined))
    cricket_terms = (
        "cricket", "kohli", "babar", "batting", "bowling", "wicket",
        "innings", "century", "icc", "t20", "odi", "ipl",
    )
    cricket_score = sum(1 for term in cricket_terms if _keyword_in_text(term, combined))
    if football_score >= 2 and football_score >= cricket_score:
        return 'football', max(20, football_score + 8)
    if cricket_score >= 2 and cricket_score > football_score:
        return 'cricket', max(18, cricket_score + 8)

    wrestling_terms = (
        "wrestling", "wrestler", "wwe", "wwf", "wrestlemania",
        "royal rumble", "smackdown",
    )
    if any(_keyword_in_text(term, combined) for term in wrestling_terms):
        return 'sports', 20

    animation_anchor_terms = (
        "the simpsons", "simpsons", "homer simpson", "bart simpson",
        "marge simpson", "lisa simpson", "maggie simpson",
        "springfield elementary", "moe tavern", "mr burns",
        "montgomery burns", "smithers", "krabappel", "principal skinner",
        "comic book guy", "animated town", "animated series", "cartoon",
    )
    if any(term in combined for term in animation_anchor_terms):
        return 'animation', 20

    scores = {}
    for niche, config in NICHE_TAXONOMY.items():
        if niche == 'general':
            continue
        score = 0
        for kw in config['keywords']:
            if _keyword_in_text(kw, combined_title):
                score += 3   # title match is 3× more valuable
            elif _keyword_in_text(kw, combined_script):
                score += 1
        scores[niche] = score

    if not scores or max(scores.values()) == 0:
        fiction_pattern = re.compile(
            r"\b(fictional|characters?|tv show|movie|film|episode|season|series|sitcom|anime)\b"
        )
        if fiction_pattern.search(combined):
            return 'film_tv', 8
        return 'general', 0

    best = max(scores, key=scores.get)
    fiction_pattern = re.compile(
        r"\b(fictional|characters?|tv show|movie|film|episode|season|series|sitcom|anime)\b"
    )
    conflict_score = max(
        scores.get('war', 0),
        scores.get('history', 0),
        scores.get('geopolitics', 0),
        scores.get('crime', 0),
    )
    if fiction_pattern.search(combined) and conflict_score < 3 and scores.get(best, 0) < 6:
        return 'film_tv', max(8, scores.get('film_tv', 0))
    return best, scores[best]


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2: UNIVERSAL CLIP CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────

UNIVERSAL_CLIP_TYPES = {

    'RAW_SHORT': {
        'description': 'Raw footage under 4 min — full clip is the content',
        'timestamp_strategy': 'skip_intro_only',
        'signals': [
            'raw footage', 'combat footage', 'caught on camera',
            'dashcam', 'bodycam', 'frontline', 'live footage',
            'actual footage', 'real footage', 'uncut', 'unedited',
            'caught on', 'security camera', 'cctv',
            'goal', 'knockout', 'highlights clip', 'moment',
        ],
        'max_duration': 240,
    },

    'RAW_LONG': {
        'description': 'Full match/event/archival footage over 4 min',
        'timestamp_strategy': 'chapters_then_description_then_spread',
        'signals': [
            'full match', 'full game', 'full fight', 'full innings',
            'full documentary', 'extended highlights', 'archival',
            'newsreel', 'archive', 'full footage', 'complete match',
            'entire match', 'colorized', 'restored',
        ],
        'min_duration': 240,
    },

    'DOCUMENTARY': {
        'description': 'Produced documentary with narrative structure',
        'timestamp_strategy': 'chapters_then_transcript_then_spread',
        'signals': [
            'documentary', 'explained', 'the story of', 'history of',
            'rise and fall', 'how did', 'what happened', 'who was',
            'inside', 'untold', 'secrets of', 'the truth about',
            'investigation', 'exposé', 'deep dive',
        ],
        'min_duration': 120,
    },

    'NEWS_REPORT': {
        'description': 'News package — relevant in first 90 seconds',
        'timestamp_strategy': 'fixed_broll_offset',
        'signals': [
            'cnn', 'bbc', 'abc news', 'nbc', 'cbs news', 'fox news',
            'al jazeera', 'france 24', 'dw news', 'reuters', 'ap',
            'sky news', 'channel 4', 'afp', 'bloomberg', 'cnbc',
            'report', 'breaking news', 'news update', 'live report',
            'breaking:', 'breaking', 'exclusive:', 'the point:',
            'tonight:', 'update:', 'alert:',
        ],
    },

    'COMPILATION': {
        'description': 'Highlights/best-of — spread scenes across it',
        'timestamp_strategy': 'even_spread',
        'signals': [
            'compilation', 'highlights', 'best of', 'top 10',
            'top moments', 'greatest', 'all goals', 'all wickets',
            'all knockouts', 'mix', 'montage', 'collection',
        ],
    },

    'CINEMATIC': {
        'description': 'B-roll, 4K nature/travel/space footage — visually rich',
        'timestamp_strategy': 'skip_intro_only',
        'signals': [
            '4k', '8k', 'cinematic', 'aerial', 'drone footage',
            'timelapse', 'time lapse', 'slowmotion', 'slow motion',
            'b-roll', 'broll', 'stock footage', 'nature footage',
            'wildlife footage', 'landscape', 'aerial view',
        ],
    },

    'ANIMATION': {
        'description': 'Animated explainers, map animations, 3D renders',
        'timestamp_strategy': 'skip_intro_animation',
        'signals': [
            '3d animation', 'animated', 'map animation', 'every day',
            'animated map', 'infographic', 'motion graphic',
            'visualization', 'explainer animation',
        ],
    },

    'COMMENTARY': {
        'description': 'Talk/analysis/reaction — low visual value',
        'timestamp_strategy': 'deprioritize',
        'signals': [
            'reaction', 'reacts', 'commentary', 'analysis', 'review',
            'podcast', 'interview', 'discusses', 'opinion', 'debate',
            'panel', 'responds to', 'weighs in', 'takes on',
            'my thoughts', 'we need to talk', 'addressing',
            'song', 'anthem', 'fan made', 'fanmade', 'reacting',
            'reacts to', 'react to', 'our reaction', 'first time',
            'tries', 'trying', 'ranking', 'ranked', 'top ', 'best ',
            'waiting', 'speaks', 'talking about', 'chat about',
        ],
    },
}

def classify_clip_universal(title, duration, channel, description=''):
    """
    Classify any video into a universal clip type.
    Works across all niches with no niche-specific logic.
    """
    combined = (title + ' ' + channel + ' ' + description[:200]).lower()

    type_scores = {t: 0 for t in UNIVERSAL_CLIP_TYPES}

    for clip_type, config in UNIVERSAL_CLIP_TYPES.items():
        for signal in config.get('signals', []):
            if signal in combined:
                type_scores[clip_type] += 1

    # Duration-based adjustments
    if duration:
        if duration < 240:
            type_scores['RAW_SHORT'] += 2
            type_scores['DOCUMENTARY'] -= 2
            type_scores['RAW_LONG'] -= 3
        elif duration > 1800:
            type_scores['RAW_LONG'] += 2
            type_scores['DOCUMENTARY'] += 1
            type_scores['RAW_SHORT'] -= 5
        elif 240 <= duration <= 900:
            type_scores['DOCUMENTARY'] += 1
            type_scores['COMPILATION'] += 1

    best_type = max(type_scores, key=type_scores.get)
    confidence = type_scores[best_type]

    # No clear signal — use duration as tiebreaker
    if confidence == 0:
        if duration and duration < 180:
            best_type = 'RAW_SHORT'
        elif duration and duration > 900:
            lowered = (channel or "").lower()
            if any(token in lowered for token in ('official', 'c-span', 'white house', 'archive', 'ap archive', 'critical past')):
                best_type = 'RAW_LONG'
            else:
                best_type = 'DOCUMENTARY'
        else:
            best_type = 'NEWS_REPORT'

    return best_type, confidence


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3: NICHE x CLIP TYPE PRIORITY MATRIX
# ─────────────────────────────────────────────────────────────────────────────

NICHE_CLIP_PRIORITY = {
#                        RAW   RAW   DOC   NEWS  COMP  CIN   ANIM  COMM
#                        SHORT LONG  UMENT REPRT PILA  EMAT  ATION MENT
    'war':          [10,   9,    7,    4,    6,    5,    4,   -8],
    'history':      [ 7,   9,   10,    5,    4,    6,    7,   -5],
    'cricket':      [ 9,   8,    3,    4,   10,    2,    1,   -3],
    'football':     [ 9,   7,    3,    4,   10,    3,    2,   -3],
    'basketball':   [ 9,   6,    3,    4,   10,    2,    1,   -3],
    'mma_boxing':   [10,   7,    4,    4,    9,    2,    1,   -5],
    'space':        [ 8,   6,    9,    4,    5,   10,    7,   -3],
    'science':      [ 6,   7,   10,    5,    4,    9,    8,   -3],
    'technology':   [ 4,   5,    9,    7,    4,    5,    8,    2],
    'automotive':   [ 8,   8,    7,    2,    5,    7,    2,   -4],
    'geopolitics':  [10,   9,    4,   -2,    1,    4,    2,   -8],
    'crime':        [ 6,   7,   10,    8,    3,    3,    4,   -2],
    'music':        [ 9,   8,    5,    4,    7,    6,    2,   -2],
    'gaming':       [ 7,   8,    4,    3,   10,    3,    4,    2],
    'finance':      [ 3,   4,    9,    8,    2,    3,    7,    3],
    'food':         [ 8,   5,    7,    4,    5,   10,    2,   -2],
    'travel':       [ 7,   6,    8,    3,    5,   10,    3,   -3],
    'nature':       [ 7,   8,    9,    3,    5,   10,    4,   -4],
    'general':      [ 6,   6,    7,    6,    5,    5,    5,    0],
}

CLIP_TYPE_ORDER = [
    'RAW_SHORT', 'RAW_LONG', 'DOCUMENTARY', 'NEWS_REPORT',
    'COMPILATION', 'CINEMATIC', 'ANIMATION', 'COMMENTARY'
]

def get_clip_type_priority(clip_type, niche):
    row = NICHE_CLIP_PRIORITY.get(niche, NICHE_CLIP_PRIORITY['general'])
    idx = CLIP_TYPE_ORDER.index(clip_type) if clip_type in CLIP_TYPE_ORDER else -1
    return row[idx] if idx >= 0 else 0


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 4: UNIVERSAL SCORER
# ─────────────────────────────────────────────────────────────────────────────

def _score_anchors_weighted(title: str, anchors: list, niche: str) -> float:
    """Helper adapted for universal scoring. Matches anchor keywords in title."""
    if not anchors:
        return 0.0
    t = title.lower()
    score = 0.0
    for a in anchors:
        a = a.lower()
        if a in t:
            score += 10.0
        else:
            parts = a.split()
            if len(parts) > 1:
                matches = sum(1 for p in parts if p in t)
                if matches > 0:
                    score += 5.0 * (matches / len(parts))
    return min(score, 20.0)

def _get_publisher_trust(channel: str) -> float:
    """Helper for publisher trust bonus."""
    val = channel.lower()
    trusted_primary = (
        'official', 'c-span', 'cspan', 'white house', 'un web tv',
        'united nations', 'nasa', 'esa', 'spacex', 'ap archive',
        'critical past', 'british pathe', 'british path', 'reuters events',
        'getty images', 'historic films', 'footage farm',
    )
    broadcaster_noise = (
        'news', 'cnn', 'bbc', 'fox', 'msnbc', 'al jazeera',
        'bloomberg', 'sky news', 'france 24', 'euronews', 'podcast',
    )
    if any(token in val for token in trusted_primary):
        return 4.0
    if any(token in val for token in broadcaster_noise):
        return -4.0
    if 'archive' in val or 'official' in val:
        return 2.0
    return 0.0

def _is_talking_head_title(title: str) -> bool:
    """
    Detect talking-head video formats by title pattern.
    These are interviews, expert panels, and anchor commentary.
    """
    t = title.strip()
    
    # Pattern 1: "Name On Topic" (expert interview format)
    if re.search(r'\b[A-Z0-9][a-zA-Z0-9]+\s+(?:On|on|About|about|Reacts|Responds)\s+[A-Z0-9]', t, re.IGNORECASE):
        return True
    
    # Pattern 2: "Name: Quote" (anchor attribution format)
    if re.search(r'^[A-Z0-9][a-zA-Z0-9\s]{2,25}:', t.strip(), re.IGNORECASE):
        return True
    
    # Pattern 3: Question-format titles (commentary / clickbait)
    if t.rstrip().endswith('?') or t.lower().startswith(('what ', 'why ', 'how ', 'will ', 'can ', 'is ', 'are ', 'could ', 'should ', 'would ', 'is it ')):
        return True
    
    # Pattern 4: "Expert" / "Analyst" in title
    if any(term in t.lower() for term in ('expert', 'analyst', 'professor', 'dr.', 'prof.')):
        return True
    
    return False


def classify_footage_vs_package(title: str, channel: str, duration: int) -> str:
    """
    Returns 'PURE_FOOTAGE', 'NEWS_PACKAGE', or 'MIXED'.
    Now with talking-head detection.
    """
    import re as _re
    t = title.lower()
    c = channel.lower()

    broadcaster_terms = (
        'news', 'msnbc', 'cnn', 'fox', 'bbc', 'al jazeera',
        'reuters', 'bloomberg', 'sky news', 'france 24',
        'india today', 'ndtv', 'wion', 'times now', 'republic',
        'geo', 'ary', 'dawn', 'samaa', 'express',
        'cnbc', 'abc', 'nbc', 'cbs', 'pbs', 'euronews', 'dw news',
        'pundit', 'expert', 'analyst', 'tass', 'xinhua', 'cctv', 
        'cgtn', 'trt world', 'rt news'
    )
    raw_footage_terms = (
        'raw footage', 'actual footage', 'real footage', 'field footage',
        'ground footage', 'archive footage', 'archival footage', 'bodycam',
        'dashcam', 'without commentary', 'without narration', 'uncut',
        'unedited', 'direct feed', 'official footage', 'full speech',
        'full remarks', 'full address', 'public address', 'signing ceremony',
        'press conference full', 'press briefing full', 'opening remarks',
        'statement in full', 'c-span', 'live stream', 'official stream',
    )
    package_terms = (
        'explains', 'tonight', 'report', 'anchor', 'analysis', 'panel',
        'discusses', 'breaking news', 'news update', 'what this means',
        'exclusive interview', 'interview', 'podcast', 'host', 'responds',
        'reacts', 'debate', 'commentary', 'reporter',
        'breaking:', 'exclusive:', 'the point:', 'update:', 'alert:',
        'panics', 'strikes back', 'will hit', 'set to',
        'expert', 'analyst',
    )
    event_terms = (
        'speech', 'remarks', 'address', 'briefing', 'press conference',
        'statement', 'rally', 'meeting', 'summit', 'signing', 'ceremony',
        'arrival', 'departure',
    )

    # FIRST CHECK: Talking-head title pattern (highest priority after raw footage)
    is_talking_head = _is_talking_head_title(title)

    has_broadcaster_channel = any(x in c for x in broadcaster_terms)
    has_raw_signal = any(x in t for x in raw_footage_terms)
    has_package_signal = any(x in t for x in package_terms)
    has_event_signal = any(x in t for x in event_terms)

    # Raw footage always wins
    if has_raw_signal:
        return 'PURE_FOOTAGE'

    # Talking-head detected = always NEWS_PACKAGE
    if is_talking_head:
        return 'NEWS_PACKAGE'

    if has_package_signal:
        return 'NEWS_PACKAGE'

    if has_broadcaster_channel:
        if has_event_signal and duration >= 900:
            return 'PURE_FOOTAGE'
        if has_event_signal and duration >= 480:
            return 'MIXED'
        return 'NEWS_PACKAGE'

    if has_event_signal and duration >= 180:
        return 'PURE_FOOTAGE'

    if 'documentary' in t or 'history of' in t or 'explained' in t:
        return 'MIXED'

    return 'MIXED'

def score_candidate_universal(candidate, anchors, niche, scene_keywords):
    """
    V2: Prioritizes footage purity heavily.
    """
    title    = candidate.get('title', '')
    duration = candidate.get('duration', 0)
    channel  = candidate.get('channel', '')
    views    = candidate.get('view_count', 0)
    desc     = candidate.get('description', '')
    followers = candidate.get('channel_follower_count', 0) or 0
    
    import math
    
    # ─── 1. Core Classification ──────────────────────────────────
    clip_type, type_conf = classify_clip_universal(
        title, duration, channel, desc
    )
    candidate['clip_type'] = clip_type
    
    footage_type = classify_footage_vs_package(title, channel, duration)
    candidate['footage_type'] = footage_type
    
    # ─── 2. Base Scoring ─────────────────────────────────────────
    score = 0.0
    
    raw_bonus_terms = (
        'raw footage', 'actual footage', 'real footage', 'archive footage',
        'archival footage', 'bodycam', 'dashcam', 'uncut', 'unedited',
        'without commentary', 'without narration', 'public address',
        'full speech', 'full remarks', 'press conference', 'briefing',
        'statement in full', 'official footage', 'direct feed',
    )
    package_penalty_terms = (
        'analysis', 'explained', 'panel', 'debate', 'podcast', 'reporter',
        'anchor', 'interview', 'reacts', 'commentary', 'what this means',
        'breaking news', 'news update', 'exclusive', 'journalist', 'host',
        'responds', 'discusses', 'reaction', 'pundit',
        'breaking:', 'the point:', 'tonight:', 'exclusive:',
        'panics', 'strikes back', 'slams', 'destroys', 'will hit',
        'set to', 'report:', 'update:', 'alert:',
        'song', 'anthem', 'lyrics', 'music video', 'fan made',
        'fanmade', 'remix', 'cover', 'tribute',
    )

    # Dominant Signal 1: Footage Purity (Massive weighting)
    if footage_type == 'PURE_FOOTAGE':
        score += 60.0 # Force PURE_FOOTAGE to the top
    elif footage_type == 'MIXED':
        score += 5.0
    elif footage_type == 'NEWS_PACKAGE':
        score -= 30.0 # Reduced penalty: educational/news content still has usable clips
        
    # Dominant Signal 2: Niche-specific Clip Type (Up to 22 pts)
    type_priority = get_clip_type_priority(clip_type, niche)
    score += max(0, type_priority * 2.2)  # 0 to 22
    
    # ─── 3. Keyword / Relevance Match (Up to 20 pts) ─────────────
    # Exact match bonus
    t_lower = title.lower()
    desc_lower = desc.lower()
    
    match_score = _score_anchors_weighted(title, anchors, niche)
    score += match_score
    
    # ─── 4. Quality & Trust Signals (Up to 15 pts) ───────────────
    # Views logic - logarithmic scaling (up to 5 pts)
    if views > 1000:
        score += min(5.0, math.log10(views) - 3)
        
    # Publisher trust (up to 5 pts)
    score += _get_publisher_trust(channel)
    score += min(
        20.0,
        sum(4.0 for term in raw_bonus_terms if term in f"{t_lower} {desc_lower}")
    )

    # Subscriber penalty — prefer small channels (<10k followers)
    # Reduced for non-event content where large channels still have usable footage
    if followers > 0:
        if followers > 500000:
            score -= 25.0
        elif followers > 100000:
            score -= 15.0
        elif followers > 50000:
            score -= 10.0
        elif followers > 20000:
            score -= 5.0
        elif followers > 10000:
            score -= 2.0
        else:
            score += 5.0  # bonus for truly small channels
    
    # ─── 5. BLOCKING PENALTIES ───────────────
    # Question-format title = ALWAYS commentary/clickbait → massive block
    _title_stripped = t_lower.strip()
    if _title_stripped.rstrip().endswith('?'):
        score -= 50.0
    if any(_title_stripped.startswith(w) for w in ('what', 'why', 'how', 'will', 'can', 'is ', 'are ', 'who ', 'where ', 'when ', 'should ', 'could ', 'would ', 'does ', 'do ', 'did ', 'has ', 'have ', 'was ')):
        score -= 40.0
    # "Name: Topic" format = interview/podcast/commentary → block
    if ':' in title and not any(raw in t_lower for raw in ('raw footage', 'actual footage', 'real footage')):
        score -= 30.0
    # "Re:" prefix = re-upload/reaction → block
    if t_lower.startswith('re:') or t_lower.startswith('re :'):
        score -= 35.0
    # "?" anywhere in title = clickbait/commentary
    if '?' in title:
        score -= 25.0
    # Sensationalism (ALL CAPS words)
    caps_words = [w for w in title.split() if w.isupper() and len(w) > 2]
    if len(caps_words) >= 2:
        score -= min(30.0, len(caps_words) * 5.0)
        
    emoji_count = _count_emojis(title)
    if emoji_count > 0:
        score -= min(20.0, emoji_count * 5.0)

    score -= min(
        30.0,
        sum(10.0 for term in package_penalty_terms if term in f"{t_lower} {desc_lower}")
    )

    # News/documentary/tv channel in channel name → very strong block
    _channel_lower = channel.lower().strip()
    if any(token in _channel_lower for token in (
        'news', 'cnn', 'bbc', 'fox', 'msnbc', 'al jazeera', 'bloomberg',
        'skynews', 'france24', 'ndtv', 'geo', 'ary', 'india today',
        'wion', 'times now', 'republic', 'dawn', 'samaa', 'cnbc',
        'abc', 'nbc', 'express', 'espn', 'sportsnet', 'tsn',
        'sports', 'media', 'report', 'press', 'journal',
        'times', 'post', 'tribune', 'chronicle', 'observer',
        'dispatch', 'today', 'weekly', 'tv', 'network',
        'podcast', 'talks', 'show', 'official',
    )):
        score -= 50.0

    # Duration signal: long videos = commentary/documentary
    if duration and duration > 300:
        score -= 30.0

    # Personal brand / talking head penalty
    _personal_brand_patterns = (
        'vlog', 'lifestyle', 'daily life', 'my day', 'day in my life',
        'morning routine', 'evening routine', 'get ready with me',
        'grwm', 'come with me', 'follow me around', 'weekly vlog',
        'travel vlog', 'family vlog', 'mom vlog', 'dad vlog',
    )
    if any(p in t_lower for p in _personal_brand_patterns):
        score -= 30.0
    # Talking head signals in title
    _talking_head_patterns = (
        'my thoughts on', 'i think', 'in my opinion', 'let me explain',
        'let me tell you', 'here\'s why', 'here is why', 'heres why',
        'what i think', 'why i', 'how i', 'my opinion on',
        'reacting to', 'react to', 'my reaction', 'watching',
        'talking about', 'discussing', 'chat about',
        'face reveal', 'speaking', 'interview with me',
        'fans react', 'fan reacts', 'reacts to', 'reacting',
        'we react', 'our reaction', 'first time watching',
        'first time hearing', 'tries', 'trying',
        'fans speak', 'speaks out', 'speak out',
        'we talk', 'we discuss', 'let\'s talk', 'lets talk',
    )
    if any(p in t_lower for p in _talking_head_patterns):
        score -= 35.0
    # Fan song / anthem / music video penalties (not b-roll)
    _fan_content_patterns = (
        'song', 'anthem', 'official song', 'music video', 'lyrics',
        'fan made', 'fanmade', 'edit', 'status', 'whatsapp status',
        'remix', 'remastered', 'cover', 'tribute', 'beat',
    )
    if any(p in t_lower for p in _fan_content_patterns):
        score -= 35.0
    # Review / ranking / listicle penalties
    _review_patterns = (
        'the good, the bad', 'the good the bad', 'ranking', 'ranked',
        'top ', 'best ', 'worst ', 'every ', 'all ',
        'rating', 'rated', 'tier list', 'tierlist',
        'story of', 'history of', 'story about', 'story behind',
        'legend', 'legends', 'journey of', 'rise of', 'fall of',
        'predictions', 'prediction', 'preview', 'looking ahead',
    )
    if any(p in t_lower for p in _review_patterns):
        score -= 20.0
    # News / documentary / explainer content
    _news_content_patterns = (
        'crisis', 'explained', 'explainer', 'documentary',
        'inside the', 'behind the', 'secret', 'untold',
        'what happens', 'what is', 'what are', 'who is',
        'report', 'investigation', 'exposed', 'revealed',
        'in crisis', 'boycott', 'controversy', 'scandal',
        'question', 'answered', 'need to know',
    )
    if any(p in t_lower for p in _news_content_patterns):
        score -= 35.0
    # Channel name signal for fan/commentary channels
    _channel_lower = channel.lower().strip()
    _fan_channel_signals = (
        'fan', 'fans', 'edits', 'reacts', 'reaction', 'shorts',
        'songs', 'music', 'lyrics', 'status', 'productions',
        'talks', 'podcast', 'show',
    )
    if any(s in _channel_lower for s in _fan_channel_signals):
        score -= 30.0
        
    candidate['rank_score'] = score
    candidate['score_breakdown'] = {
        'total': score,
        'type': clip_type,
        'footage': footage_type,
        'match': match_score
    }
    return score
 # ─────────────────────────────────────────────────────────────────────────────
# LAYER 5: UNIVERSAL TIMESTAMP RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

def get_smart_timestamp(video_id, clip_type, footage_type, 
                         duration, scene_index, total_scenes):
    """
    Smart timestamp without ANY transcript dependency.
    Based purely on clip type, footage type, and duration.
    No external API calls. Instant.
    """
    
    if not duration or duration == 0:
        duration = 180  # safe default
    
    # ── PURE FOOTAGE clips ────────────────────────────────────
    if footage_type == 'PURE_FOOTAGE':
        
        if duration < 60:
            # Very short clip — start at 5% in, avoid black frame
            return max(int(duration * 0.05), 2)
        
        elif duration < 180:
            # Short footage clip (1-3 min)
            # Spread multiple scenes across the clip
            usable = duration * 0.85
            offset = (scene_index / max(total_scenes, 1)) * usable
            return max(int(offset), 4)
        
        else:
            # Longer footage — skip opening title card
            # Land at 10-15% in where action starts
            base = duration * 0.12
            spread = duration * 0.65
            ratio = scene_index / max(total_scenes - 1, 1)
            return int(base + spread * ratio)
    
    # ── NEWS PACKAGE clips ────────────────────────────────────
    elif footage_type == 'NEWS_PACKAGE':
        
        if duration < 90:
            # Short news clip — b-roll starts around 8-12s
            # Skip anchor intro
            return 12
        
        elif duration < 300:
            # Standard news package (2-5 min)
            # B-roll heavy section is usually 15-40% in
            base = duration * 0.15
            spread = duration * 0.30
            ratio = scene_index / max(total_scenes, 1)
            return int(base + spread * ratio)
        
        else:
            # Long news documentary
            # Skip intro, land in meat of content
            base = duration * 0.10
            spread = duration * 0.60
            ratio = scene_index / max(total_scenes - 1, 1)
            return int(base + spread * ratio)
    
    # ── MIXED/UNKNOWN clips ───────────────────────────────────
    else:
        # Safe middle section, spread across scenes
        base = int(duration * 0.15)
        spread = int(duration * 0.55)
        ratio = scene_index / max(total_scenes - 1, 1)
        return int(base + spread * ratio)

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 6: MULTI-QUERY BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_search_queries(topic, niche):
    """
    Build 3 queries per group: primary, footage-specific, broader.
    """
    config = NICHE_TAXONOMY.get(niche, NICHE_TAXONOMY['general'])
    suffixes = (
        config.get('search_suffix')
        or config.get('footage_search_terms')
        or NICHE_TAXONOMY['general'].get('search_suffix', [])
    )

    queries = []

    # Primary: topic as-is (catches exact matches)
    queries.append(topic)

    # Footage-intent variants
    for suffix in suffixes[:2]:
        q = f"{topic} {suffix}"
        if q not in queries:
            queries.append(q)

    return queries
