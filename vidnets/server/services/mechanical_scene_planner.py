"""Deterministic, network-free scene planner (mechanical-first path).

Port of the Worker C investigation prototype (/tmp/mech_scene_queries.py),
promoted from scratch to the first planning path: these rows are served
without filing any inference request. Only rows failing the real
validators (JSON schema + retrieval contract) are sent to the LLM for
repair; see ``mechanical_first_failing_scenes``.

Rules only: proper-noun extraction, lexical type rules, distinctive-token
facets, per-lane query templates. No model call, no network, linear time.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

STOP = set("""a an the of and in on to for with from into over under after before during through across
between about than then there here while since until per via at by as or but not no is are was were be been
how what when where why who which that this these those very just also still yet more most other another
it its he she they we you his her their our your my me him them us i""".split())
SENT_START = {'The', 'A', 'An', 'On', 'In', 'It', 'That', 'Before', 'After', 'When', 'While', 'But', 'And', 'So', 'Now', 'Picture', 'Think', 'Both', 'At', 'As', 'If', 'One', 'Two', 'Three', 'For', 'From', 'With', 'By', 'To', 'Of'}
PLACE_WORDS = {'city', 'pass', 'desert', 'keys', 'summit', 'mountains', 'mountain', 'streets', 'street', 'island', 'beach', 'beaches', 'swamp', 'swamps', 'state', 'washington', 'utah', 'florida', 'chile', 'pakistan', 'york', 'sierra', 'nevada', 'leonida', 'vice', 'donner', 'promontory', 'wellington', 'stevens', 'yuba', 'atacama', 'salt', 'lake', 'northeast', 'country', 'network', 'track'}
COMPANY_WORDS = {'games', 'railway', 'railways', 'railroad', 'brothers', 'company', 'studios', 'studio', 'pacific', 'central', 'union', 'southern', 'northern', 'great', 'ferrocarril'}
EVENT_WORDS = {'blizzard', 'avalanche', 'flood', 'floods', 'trapped', 'buried', 'stranded', 'disaster', 'wreck', 'wrecks', 'rescue'}
GAME_WORDS = {'grand', 'theft', 'auto', 'gta'}
GENERIC_FACET_STOP = STOP | {'would', 'could', 'should', 'one', 'two', 'three', 'first', 'second', 'also', 'even', 'back', 'still', 'much', 'many', 'every', 'each', 'same', 'new', 'old', 'big', 'small', 'real', 'full', 'entire', 'long', 'made', 'make', 'like', 'take', 'takes', 'took', 'give', 'gives', 'gave', 'goes', 'went', 'come', 'comes', 'came', 'get', 'gets', 'got', 'keep', 'keeps', 'kept', 'know', 'knew', 'see', 'saw', 'say', 'says', 'said', 'think', 'tell', 'tells', 'told', 'show', 'shows', 'showed', 'mean', 'means', 'meant', 'turn', 'turns', 'turned', 'change', 'changes', 'changed', 'face', 'faces', 'faced', 'run', 'runs', 'ran', 'sit', 'sits', 'sat', 'stand', 'stands', 'stood', 'leave', 'leaves', 'left', 'bring', 'brings', 'brought', 'find', 'finds', 'found', 'put', 'puts', 'set', 'sets', 'become', 'becomes', 'became', 'seem', 'seems', 'feel', 'feels', 'felt', 'look', 'looks', 'looked', 'try', 'tries', 'tried', 'need', 'needs', 'needed', 'want', 'wants', 'wanted', 'work', 'works', 'worked', 'live', 'lives', 'lived', 'die', 'dies', 'died', 'kill', 'kills', 'killed', 'open', 'close', 'build', 'builds', 'built', 'lose', 'loses', 'lost', 'hold', 'holds', 'held', 'fall', 'falls', 'fell', 'rise', 'rises', 'rose', 'grow', 'grows', 'grew', 'move', 'moves', 'moved', 'pass', 'passes', 'passed', 'wait', 'waits', 'waited', 'stop', 'stops', 'stopped', 'start', 'starts', 'started', 'arrive', 'arrives', 'arrived', 'reach', 'reaches', 'reached', 'push', 'pushes', 'pushed', 'pull', 'pulls', 'pulled', 'cut', 'cuts', 'throw', 'throws', 'threw', 'eat', 'eats', 'fight', 'fights', 'fought', 'burn', 'burns', 'burned', 'freeze', 'froze', 'frozen', 'cold', 'warm', 'heat', 'snow', 'ice', 'water', 'wind', 'storm', 'rain', 'fire', 'steam', 'smoke', 'room', 'cab', 'train', 'trains', 'car', 'cars', 'line', 'track', 'tracks', 'road', 'rail', 'way', 'thing', 'things', 'part', 'parts', 'half', 'end', 'place', 'places', 'time', 'times', 'day', 'days', 'year', 'years', 'week', 'weeks', 'hour', 'hours', 'minute', 'minutes', 'night', 'morning', 'people', 'person', 'crew', 'team', 'man', 'men', 'woman', 'women', 'boy', 'girl', 'child', 'children', 'world', 'earth', 'country', 'state', 'home', 'house', 'door', 'wall', 'roof', 'floor', 'window', 'glass', 'steel', 'wood', 'iron', 'fire', 'box', 'boiler', 'engine', 'machine', 'system', 'job', 'work', 'story', 'stories', 'point', 'fact', 'facts', 'lesson', 'lessons', 'difference', 'question', 'answer', 'problem', 'change', 'chance', 'reason', 'result', 'effect', 'cause', 'side', 'front', 'back', 'top', 'bottom', 'center', 'middle', 'outside', 'inside', 'above', 'below', 'behind', 'front', 'around', 'along', 'among', 'away', 'off', 'out', 'up', 'down', 'over', 'under', 'between', 'through', 'without', 'within', 'against', 'toward', 'towards', 'upon', 'onto', 'per', 'via', 'etc', 'say', 'way', 'ways', 'kind', 'kinds', 'sort', 'type', 'types', 'form', 'forms', 'level', 'levels', 'case', 'cases', 'example', 'number', 'numbers', 'group', 'groups', 'name', 'names', 'word', 'words', 'line', 'lines', 'sentence', 'page', 'book', 'film', 'video', 'movie', 'show', 'scene', 'scenes', 'image', 'images', 'photo', 'photos', 'picture', 'pictures', 'shot', 'shots', 'clip', 'clips', 'footage', 'trailer', 'game', 'games', 'play', 'player', 'players', 'character', 'characters', 'series', 'version', 'edition', 'release', 'launch', 'date', 'dates', 'event', 'events', 'history', 'past', 'future', 'present', 'today', 'yesterday', 'tomorrow', 'ago', 'later', 'earlier', 'soon', 'already', 'always', 'never', 'often', 'sometimes', 'usually', 'finally', 'recently', 'once', 'twice', 'again', 'ever', 'never', 'maybe', 'perhaps', 'probably', 'certainly', 'surely', 'really', 'actually', 'exactly', 'almost', 'nearly', 'quite', 'rather', 'pretty', 'very', 'too', 'enough', 'less', 'least', 'few', 'fewer', 'little', 'several', 'various', 'certain', 'different', 'similar', 'possible', 'important', 'possible', 'main', 'major', 'minor', 'general', 'specific', 'special', 'common', 'usual', 'normal', 'natural', 'public', 'private', 'local', 'national', 'central', 'modern', 'steam', 'diesel', 'electric', 'wooden', 'open', 'sealed', 'insulated', 'drafty', 'cold', 'warm', 'hot', 'dry', 'wet', 'deep', 'high', 'low', 'big', 'small', 'large', 'great', 'huge', 'tiny', 'short', 'long', 'wide', 'narrow', 'thick', 'thin', 'heavy', 'light', 'strong', 'weak', 'hard', 'soft', 'fast', 'slow', 'quick', 'early', 'late', 'young', 'old', 'new', 'last', 'next', 'first', 'second', 'third', 'final', 'only', 'own', 'same', 'other', 'another', 'each', 'every', 'all', 'any', 'some', 'no', 'none', 'both', 'either', 'neither', 'few', 'many', 'much', 'more', 'most', 'less', 'least', 'own'}


def sentences(text: str) -> List[str]:
    return [t.strip() for t in re.split(r'(?<=[.!?])\s+', (text or '').replace('\n', ' ')) if t.strip()]


def cap_phrases(text: str) -> List[str]:
    """Capitalized word sequences (1-4 words), sentence-initial common words dropped."""
    out = []
    for m in re.finditer(r"\b(?:[A-Z][A-Za-z0-9'\u2019-]*)(?:\s+(?:[A-Z][A-Za-z0-9'\u2019-]*|of|de|a|the|and)){0,3}", text or ''):
        ph = re.sub(r'\s+', ' ', m.group(0)).strip(" .,:;")
        words = ph.split()
        while words and words[0] in SENT_START and len(words) > 1:
            words.pop(0)
        while words and words[-1].lower() in {'of', 'de', 'a', 'the', 'and'}:
            words.pop()
        if not words:
            continue
        if len(words) == 1 and words[0] in SENT_START:
            continue
        if any(not w[:1].isupper() for w in words):
            continue
        if words[0] in {'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'} and len(words) == 1:
            continue
        out.append(' '.join(words))
    return out


def content_words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9-]{2,}", (text or '').lower()) if w not in GENERIC_FACET_STOP]


def distinctive_tokens(text: str, siblings: Sequence[str]) -> List[str]:
    own = {w for w in re.findall(r"[a-z0-9]+", (text or '').lower()) if len(w) >= 4 and w not in STOP and w not in {'that', 'this', 'with', 'from', 'they', 'have', 'been', 'were', 'what', 'when', 'where', 'which', 'their', 'them', 'about', 'into', 'could', 'would', 'should', 'there', 'these', 'those', 'because', 'just', 'also', 'very', 'more', 'than', 'then', 'some', 'made', 'make', 'like', 'even', 'still', 'after', 'before'}}
    sib = set()
    for s in siblings:
        sib.update(re.findall(r"[a-z0-9]+", (s or '').lower()))
    return sorted(w for w in own if w not in sib)


def classify_subject(subject: str, scene_text: str = ""):
    low = (subject or '').lower()
    words = set(re.findall(r"[a-z]+", low))
    if words & {'grand', 'theft', 'auto'} or low.startswith('gta ') or low == 'gta':
        return 'topic', 'video_game'
    if words & COMPANY_WORDS and not (words & {'city'}):
        return 'topic', 'organization'
    if words & PLACE_WORDS or any(w in low for w in ['pass', 'desert', 'keys', 'summit', 'mountains', 'beach', 'swamp']):
        return 'place', 'place'
    if words & EVENT_WORDS:
        return 'event', 'event'
    parts = subject.split()
    if len(parts) == 2 and all(p[:1].isupper() for p in parts) and not (words & (PLACE_WORDS | COMPANY_WORDS | EVENT_WORDS | GAME_WORDS)):
        return 'person', 'person'
    if ' and ' in low and all(p[:1].isupper() for p in subject.replace(' and ', ' ').split()):
        return 'person', 'person'
    return 'topic', 'concept'


def domain_of(title: str, texts: Sequence[str]) -> str:
    blob = ' '.join(texts).lower()
    if 'grand theft auto' in (title or '').lower() or 'gta' in (title or '').lower():
        return 'gaming'
    if any(w in blob for w in ['blizzard', 'avalanche', 'flood', 'buried', 'trapped']):
        return 'disaster'
    if re.search(r'\b(18\d\d|19[0-4]\d)\b', blob):
        return 'history'
    return 'other'


INTENTS = {
    'gaming': ['gameplay', 'official trailer', 'announcement footage', 'gameplay footage'],
    'disaster': ['archival footage', 'documentary footage', 'news footage', 'historical footage'],
    'history': ['archival footage', 'historical footage', 'documentary footage'],
    'other': ['documentary footage', 'footage', 'b-roll footage'],
}

_GAME_NUM = {'six': 'VI', 'vi': 'VI', '6': 'VI', 'five': 'V', 'v': 'V', '5': 'V',
             'iv': 'IV', '4': 'IV', 'iii': 'III', '3': 'III'}


def game_for_scene(scene_text: str, title: str):
    """Scene-relative game rule, mirroring scene_subject_game_for_scene:
    first-named NON-TITLE game wins; else the title's game (implicit)."""
    t = scene_text or ''
    named = []
    for m in re.finditer(r"\bgrand theft auto\s+(six|five|vi|v|iv|iii|4|5|6|3)\b|\bgta\s*(vi|v|iv|iii|6|5|4|3)\b", t, re.I):
        num = (m.group(1) or m.group(2) or '').lower()
        key = _GAME_NUM.get(num)
        if key and key not in named:
            named.append(key)
    title_game = None
    for m in re.finditer(r"\bgrand theft auto\s+(six|five|vi|v)\b|\bgta\s*(vi|v)\b", title or '', re.I):
        num = (m.group(1) or m.group(2) or '').lower()
        title_game = _GAME_NUM.get(num)
        break
    if named:
        non_title = [k for k in named if k != title_game]
        key = non_title[0] if non_title else named[0]
        return 'Grand Theft Auto ' + key
    if title_game:
        return 'Grand Theft Auto ' + title_game
    return None


ALIASES = {'Rockstar': 'Rockstar Games', 'PlayStation': 'PlayStation 5',
           'Grand Theft Auto Six': 'Grand Theft Auto VI', 'Grand Theft Auto Five': 'Grand Theft Auto V',
           'Xbox Series X': 'Xbox Series X', 'City of San Francisco': 'City of San Francisco'}


def expand_name(e: str) -> str:
    return ALIASES.get(e, e)


# ── Fix 1: event-framed subjects ──────────────────────────────────────
# Systematic gap in the prototype: a scene narrating a disaster at a place
# ("An avalanche at Wellington, Washington buries two Great Northern
# Railway trains at Stevens Pass") was typed as the place ("Wellington"),
# while the agent's event framing ("avalanche burying two trains at
# Stevens Pass") is the better subject. Prefer event framing when the
# scene has a disaster noun + a disaster verb + a place: the event is
# what the shot must show, the place is where it happened.

_EVENT_NOUNS = ('avalanche', 'blizzard', 'flood', 'floods', 'disaster', 'wreck', 'crash',
                'fire', 'explosion', 'derailment', 'landslide', 'earthquake', 'hurricane', 'storm')
_DISASTER_VERBS = {
    'bury': 'burying', 'buries': 'burying', 'buried': 'burying',
    'trap': 'trapping', 'traps': 'trapping', 'trapped': 'trapping',
    'strand': 'stranding', 'strands': 'stranding', 'stranded': 'stranding',
    'flood': 'flooding', 'floods': 'flooding', 'flooded': 'flooding',
    'wreck': 'wrecking', 'wrecks': 'wrecking', 'wrecked': 'wrecking',
    'derail': 'derailing', 'derails': 'derailing', 'derailed': 'derailing',
    'block': 'blocking', 'blocks': 'blocking', 'blocked': 'blocking',
    'kill': 'killing', 'kills': 'killing', 'killed': 'killing',
    'destroy': 'destroying', 'destroys': 'destroying', 'destroyed': 'destroying',
    'damage': 'damaging', 'damages': 'damaging', 'damaged': 'damaging',
    'wash': 'washing', 'washes': 'washing', 'washed': 'washing',
    'hit': 'hitting', 'hits': 'hitting',
    'strike': 'striking', 'strikes': 'striking', 'struck': 'striking',
    'cover': 'covering', 'covers': 'covering', 'covered': 'covering',
    'cut': 'cutting', 'cuts': 'cutting',
}
_PASSIVE_PARTICIPLES = ('stranded', 'trapped', 'buried', 'flooded', 'wrecked', 'derailed', 'blocked')
_NUMBER_WORDS = {'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
                 'eleven', 'twelve', 'twenty', 'thirty', 'forty', 'fifty', 'hundred', 'thousand'}


def _place_entities(text: str) -> List[str]:
    """Capitalized phrases in the scene that classify as a place."""
    out = []
    for ent in cap_phrases(text):
        stype, _ = classify_subject(ent, text)
        if stype == 'place':
            out.append(ent)
    return out


def _shorten_object(obj: str) -> str:
    """'two Great Northern Railway trains' -> 'two trains'; keep it short."""
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", obj or '') if w.lower() not in {'the', 'a', 'an'}]
    if not words:
        return ''
    if len(words) <= 2:
        return ' '.join(words)
    if words[0].lower() in _NUMBER_WORDS or words[0].isdigit():
        return f"{words[0]} {words[-1]}"
    return ' '.join(words[:2])


def _event_framed_subject(text: str) -> Optional[str]:
    """Event-phrase subject for a disaster scene, or None.

    Active:  "avalanche burying two trains at Stevens Pass"
    Passive: "New York elevated trains stranded in 1888 blizzard"
    Returns None unless the construction is complete (event noun +
    disaster verb + place); the caller then falls back to the
    first-entity subject. Every word comes from the scene's own text.
    """
    low = (text or '').lower()
    event = next((w for w in _EVENT_NOUNS if re.search(rf"\b{w}\b", low)), None)
    if not event:
        return None
    places = _place_entities(text)
    if not places:
        return None
    # Work sentence by sentence: the event noun and its verb can sit in
    # different comma-clauses ("An avalanche at Wellington, Washington
    # buries two ... trains at Stevens Pass"), but must stay in one
    # sentence — the object after the verb stays comma-free.
    verb_alt = '|'.join(sorted(_DISASTER_VERBS))
    place_pat = r"[A-Z][A-Za-z0-9'\u2019-]*(?:\s+[A-Z][A-Za-z0-9'\u2019-]*){0,2}"
    for sent in sentences(text):
        if not re.search(rf"\b{event}\b", sent, re.I):
            continue
        # Active voice: "<event> ... <verb> <object> at <Place>". The
        # place capture must stay case-sensitive (re.I would let "in
        # March" in), so the event/verb/"at" parts use scoped (?i:...)
        # groups instead of a global re.I.
        for m in re.finditer(rf"(?i:\b{event}\b).{{0,160}}?(?i:\b({verb_alt})\b)\s+([^,;:.]+?)(?i:\s+at\s+)({place_pat})", sent):
            verb = m.group(1).lower()
            obj = _shorten_object(m.group(2))
            place = m.group(3).strip().strip(",;:. ")
            if not obj or not place:
                continue
            if ' and ' in obj.lower():
                continue
            if place not in places and not any(place in p or p in place for p in places):
                continue
            return f"{event} {_DISASTER_VERBS[verb]} {obj} at {place}"
        # Passive voice: "<Place> ... <object> were|was <participle> ... in <year> <event>".
        for m in re.finditer(rf"\b(?:was|were)\s+({'|'.join(_PASSIVE_PARTICIPLES)})\b", sent, re.I):
            participle = m.group(1).lower()
            before = sent[:m.start()]
            before_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", before)
            if len(before_words) < 2:
                continue
            obj_words = [w for w in before_words[-3:] if w.lower() not in {'the', 'a', 'an'}]
            if not obj_words:
                continue
            obj = ' '.join(obj_words[-2:])
            year_event = re.search(rf"\b(\d{{4}})\s+{event}\b", sent.lower()) or re.search(rf"\b{event}\s+(?:of\s+)?(\d{{4}})\b", sent.lower())
            place = re.sub(r"['\u2019]s$", "", places[0])
            if year_event:
                year = year_event.group(1)
                return f"{place} {obj} {participle} in {year} {event}"
            return f"{place} {obj} {participle} in the {event}"
    return None


def paragraph_containers(paras: Sequence[str], topic: str) -> List[str]:
    """Container = most frequent capitalized entity in the scene's paragraph."""
    out = []
    for p in paras:
        c = Counter(cap_phrases(p))
        best = ''
        best_key = (-1, -1)
        for ent, n in c.items():
            key = (len(ent.split()), n)
            if key > best_key:
                best_key, best = key, ent
        out.append(best or topic)
    return out


def plan_mechanical_scene_rows(scene_texts: Sequence[str], title: str, topic: str,
                               containers: Sequence[str]) -> List[Dict[str, Any]]:
    """Deterministic SCENE_PLAN_SCHEMA-shaped rows for the given scenes.

    ``containers`` is the per-scene container subject (paragraph top entity
    or the section subject from the semantic detector); it is only the
    fallback for pronoun-only scenes.
    """
    scene_texts = list(scene_texts or [])
    containers = list(containers or [])
    domain = domain_of(title, scene_texts)
    intents = INTENTS[domain]
    rows = []
    for idx, text in enumerate(scene_texts):
        sibs = [t for j, t in enumerate(scene_texts) if j != idx]
        ents = cap_phrases(text)
        # Fix 1: event framing wins over the bare place when the scene
        # narrates a disaster at a place.
        subject = _event_framed_subject(text) or ''
        event_framed = bool(subject)
        if not subject:
            for e in ents:
                if e.lower() in ('grand theft auto vi', 'grand theft auto six') and idx > 0:
                    continue
                subject = expand_name(e)
                break
        if not subject:
            # pronoun-only scene ("It built a tunnel...") -> inherit previous subject/container
            subject = (containers[idx] if idx < len(containers) else '') or topic
        g = game_for_scene(text, title)
        if g and ('grand theft auto' in subject.lower() or 'gta' in subject.lower()):
            subject = g
        if event_framed:
            stype, vtype = 'event', 'event'
        else:
            stype, vtype = classify_subject(subject, text)
        dist = distinctive_tokens(text, sibs)
        subj_words = set(re.findall(r"[a-z0-9]+", subject.lower()))
        VISUAL = {'neon', 'aerial', 'beach', 'beaches', 'swamp', 'swamps', 'street', 'streets', 'prison', 'gates', 'gate', 'snow', 'blizzard', 'cab', 'interior', 'exterior', 'bridge', 'bridges', 'island', 'coast', 'night', 'sunset', 'desert', 'mountain', 'mountains', 'river', 'ocean', 'sea', 'water', 'fire', 'smoke', 'crowd', 'portrait', 'landscape', 'skyline', 'tunnel', 'roof', 'track', 'railway', 'locomotive', 'plow', 'plows', 'helicopter', 'avalanche', 'flood', 'shelter', 'furniture', 'frozen', 'cold', 'steam', 'boiler', 'firebox', 'windshield', 'diagram', 'map', 'timeline', 'key', 'art', 'logo', 'studio', 'office', 'console', 'controller', 'city', 'keys', 'trailer', 'gameplay'}
        cands = [w for w in content_words(text) if w in set(dist) and w not in subj_words]
        if len(cands) < 2:
            cands = (cands + [w for w in content_words(text) if w not in subj_words and w not in cands])[:3]
        if not cands:
            # Thin scene ("In 1888 the cold came through the open back of
            # the cab over days.", "Three days."): the facet stop-list
            # strips every content word and the query degrades to a bare
            # "<subject> footage" that fails the shape check. Fall back to
            # the scene's own short words (stopwords only) so the query
            # still carries the scene's action.
            cands = [w for w in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower())
                     if w not in STOP and w not in subj_words][:3]
        facet_words = sorted(set(cands), key=lambda w: (0 if w in VISUAL else 1, cands.index(w)))[:3]
        facet = ' '.join(facet_words)
        # Distinctiveness: the plan-level validator requires a primary
        # query to carry a token this scene owns vs its siblings. If the
        # facet has none (thin scene: "Three days.", a year-led scene),
        # append one owned token so the row can pass without inference.
        if dist and not any(w in set(dist) for w in facet_words):
            facet_words = (facet_words + [dist[0]])[:4]
            facet = ' '.join(facet_words)
        clauses = [c.strip() for c in re.split(r'[,;:]', text) if c.strip()]
        ev = next((c for c in clauses if subject.lower() in c.lower()), None)
        if not ev:
            ev = text[:120]
        ev = ev if len(ev) <= 120 else ev[:117] + '...'
        anchor = subject
        game_suffix = ''
        if domain == 'gaming' and g and g.lower() not in subject.lower():
            game_suffix = ' ' + g
        yt_p = f"{anchor} {facet}{game_suffix} footage".strip() if facet else f"{anchor}{game_suffix} footage"
        yt_s = f"{anchor} {facet_words[1] if len(facet_words) > 1 else 'official'}{game_suffix} footage".strip() if facet else f"{anchor} official trailer"
        yt_f = f"{anchor}{game_suffix} footage"
        web_p = f"{anchor} {facet} photograph".strip() if facet else f"{anchor} photograph"
        web_s = f"{anchor} {facet_words[0] if facet_words else ''} photo".strip() if facet else f"{anchor} photo"
        web_f = f"{anchor} photograph"
        if game_suffix:
            web_p = f"{anchor} {facet}{game_suffix} photograph".strip() if facet else f"{anchor}{game_suffix} photograph"
        stock_facet = ' '.join(w for w in facet_words if w not in {'grand', 'theft', 'auto', 'gta', 'rockstar', 'leonida', 'vice', 'city', 'san', 'francisco', 'pacific', 'southern', 'canadian', 'northern', 'great'}) or facet
        stock_p = stock_facet if stock_facet else 'railway snow blizzard' if domain == 'disaster' else 'city street night'
        stock_a = f"{stock_facet} close up" if stock_facet else stock_p
        stock_f = f"{stock_facet} footage" if stock_facet else stock_p
        pos = [w for w in dict.fromkeys(content_words(text))][:10]
        if len(pos) < 3:
            pos = (pos + [anchor.lower(), topic.lower(), domain])[:10]
        rows.append({
            'scene_number': idx + 1,
            'container_subject': (containers[idx] if idx < len(containers) else '') or topic,
            'scene_subject': subject,
            'visual_intent': f"{subject}: {facet}" if facet else subject,
            'visual_subject': subject,
            'visual_subject_type': vtype,
            'subject_evidence': ev,
            'visible_target': f"{subject} {facet}".strip(),
            'identity_scope': 'subject' if subject and subject[0].isupper() else 'context',
            'domain_category': domain,
            'scene_subject_type': stype,
            'retrieval_positive_terms': pos,
            'retrieval_negative_terms': [],
            'youtube_query_intents': intents,
            'youtube': {'primary': yt_p, 'supporting': yt_s, 'fallback': yt_f},
            'web': {'primary': web_p, 'supporting': web_s, 'fallback': web_f},
            'stock': {'primary_query': stock_p, 'alt_query': stock_a, 'fallback_query': stock_f},
        })
    # Fix 2: de-duplicate primaries mechanically (validator rule). The
    # prototype appended the scene's first own token, which was often
    # already in the query (a no-op that left the duplicate in place).
    # Append a token the scene owns vs its siblings AND that is not
    # already in the query (token-level, not substring); when the scene
    # owns no such token the two narrations are near-identical and the
    # duplicate is left for the LLM repair path.
    for lane, key in (('youtube', 'primary'), ('web', 'primary')):
        seen = {}
        for r in rows:
            q = r[lane][key]
            if q in seen:
                idx = r['scene_number'] - 1
                sibs = [t for j, t in enumerate(scene_texts) if j != idx]
                owned = distinctive_tokens(scene_texts[idx], sibs)
                q_words = set(re.findall(r"[a-z0-9]+", q.lower()))
                extra = next((t for t in owned if t not in q_words), None)
                if extra:
                    r[lane][key] = q + ' ' + extra
                    q = r[lane][key]
            seen[q] = r['scene_number']
    return rows


# ── Diagram scenes keep inference ─────────────────────────────────────
# A scene whose narration explicitly studies a diagram / map / timeline /
# chart needs inference to specify it (mechanical defaults cannot lay out
# a diagram). These scenes join the LLM repair set even when their
# mechanical rows validate: the direction for them is not mechanical.

_DIAGRAM_RE = re.compile(
    r"\b(diagram|schematic|blueprint|flowchart|timeline|chronology|cross-section|cross section)\b"
    r"|\bmap of\b|\bshows? (?:a|the) map\b|\bchart (?:of|showing|shows)\b",
    re.I,
)


def scene_requests_diagram(scene_text: str) -> bool:
    """True when the narration explicitly studies a diagram/map/timeline/chart."""
    return bool(_DIAGRAM_RE.search(scene_text or ''))


def mechanical_first_failing_scenes(bundle: Dict[str, Any], scene_texts: Sequence[str],
                                    title: Any = "") -> List[int]:
    """0-based scene indices that must go to the LLM repair path.

    A scene fails when any of its flat bundle rows violates the retrieval
    contract, when the plan-level checks (duplicate / non-distinctive
    primaries, scene game) flag it, or when it requests a diagram (its
    direction stays on inference by design). Everything else is served
    mechanically with no inference request filed.
    """
    from .vidzeo_brain import adapted_scene_plan_errors

    scene_texts = list(scene_texts or [])
    failing = set(adapted_scene_plan_errors(bundle, scene_texts, title).keys())
    for idx, text in enumerate(scene_texts):
        if scene_requests_diagram(text):
            failing.add(idx)
    return sorted(failing)
