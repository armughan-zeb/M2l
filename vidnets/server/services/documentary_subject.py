"""Preserve a literal title subject before retrieval adds story context."""
from __future__ import annotations
import re
import unicodedata


def search_key(value):
    text = str(value or '')
    # Provider metadata and already-normalized keys are usually ASCII. Do
    # not walk every character in Python just to discover no accent marks.
    if not text.isascii():
        text = unicodedata.normalize('NFKD', text)
        text = ''.join(char for char in text if not unicodedata.combining(char))
    # Retain letters/numbers in every script. Dropping non-Latin names
    # produced empty identity keys and silently rejected valid scene subjects.
    return re.sub(r'[\W_]+', ' ', text.casefold(), flags=re.UNICODE).strip()


def is_source_attribution_subject(subject, narration):
    """A cited publisher is evidence provenance, not the illustrated entity."""
    key = search_key(subject)
    if not key:
        return False
    source_words = {'article', 'account', 'report', 'publication', 'magazine', 'study'}
    if source_words & set(key.split()) and key in search_key(narration):
        return True
    # Keep capitalization local to the name; a case-insensitive whole regex
    # would absorb ordinary narration as though it were an organization name.
    pattern = (r"\b(?:The\s+)?(?P<name>[A-Z][\w'-]*(?:\s+(?:and\s+)?[A-Z][\w'-]*){0,3})"
               r"(?:['’]s)?\s+(?:(?:18|19|20)\d0s\s+)?"
               r"(?i:article|account|page|report|publication|magazine|study)\b")
    for match in re.finditer(pattern, str(narration or '')):
        name = search_key(re.sub(r'^The\s+', '', match.group('name')))
        if name and (name == key or (' ' + name + ' ') in (' ' + key + ' ')):
            return True
    return bool(re.search(r'(?i:according to|reported by|cited by)\s+(?:the\s+)?'
                          + re.escape(str(subject).strip()) + r'\b', str(narration or '')))


def title_hook_subject(title, script=''):
    """Use the supported literal subject of a hook, never its predicate."""
    raw = str(title or '').strip()
    if not raw:
        return ''

    verbs = (
        r'was|were|is|are|has|have|had|became|becomes?|changed|changes|'
        r'transformed|transforms|redefined|redefines|looked|looks|'
        r'sank|sunk|sink|sinks?|fell|falls?|fall|collapsed|collapses?|collapse|'
        r'exploded|explodes?|explode|crashed|crashes?|crash|failed|fails?|fail|'
        r'built|builds?|build|disappeared|disappears?|disappear|'
        r'died|dies?|die|lost|loses?|lose|destroyed|destroys?|destroy|'
        r'worked|works?|work|ended|ends?|end|began|begins?|begin|'
        r'started|starts?|start|ruled|rules?|rule|conquered|conquers?|conquer|'
        r'vanished|vanishes?|vanish|struck|strikes?|strike|'
        r'survived|survives?|survive|happened|happens?|happen|won|wins?|win|'
        r'break|breaks?|broke|broken|matter|matters?|mattered|landed|lands?|land'
    )

    candidate = ''

    # 1. Auxiliary question forms: "Why did the Titanic sink", "How did Rome fall"
    m = re.match(
        rf'^(?:why|how)\s+(?:did|does|do|can|could|would|will|should)\s+(?:the\s+)?(.+?)\s+(?:{verbs})\b',
        raw,
        re.I,
    )
    if m:
        candidate = m.group(1)

    # 2. Direct hook forms: "Why the Titanic sank", "How Rome fell", "What made the Concorde fail"
    if not candidate:
        m = re.match(
            rf'^(?:why|how|what made)\s+(?:the\s+)?(.+?)\s+(?:{verbs})\b',
            raw,
            re.I,
        )
        if m:
            candidate = m.group(1)

    # 3. Temporal hooks: "When the Titanic sank", "The day the Titanic sank", "When Rome fell"
    if not candidate:
        m = re.match(
            rf'^(?:the\s+day|the\s+night|the\s+moment|when)\s+(?:the\s+)?(.+?)\s+(?:{verbs})\b',
            raw,
            re.I,
        )
        if m:
            candidate = m.group(1)

    # 4. Inquiry forms: "What happened to Flight 370", "What really happened to the Titanic"
    if not candidate:
        m = re.match(
            r'^(?:what\s+(?:really\s+)?happened\s+to|what\s+became\s+of)\s+(?:the\s+)?(.+?)\s*[?!.]*$',
            raw,
            re.I,
        )
        if m:
            candidate = m.group(1)

    # 5. Documentary narrative chronicles: "The Mystery of the Mary Celeste", "The Sinking of the Titanic", "The Fall of Rome", "The Destruction of Pompeii"
    if not candidate:
        chronicle_nouns = (
            r'mystery|truth|secret|untold\s+story|real\s+story|tragic\s+story|story|'
            r'rise\s+and\s+fall|downfall|collapse|tragedy|disaster|history|legend|curse|'
            r'sinking|fall|rise|destruction|ruin|making|building|invention|discovery|'
            r'origin|origins|battle|war|crimes?|assassination|execution'
        )
        m = re.match(
            rf'^(?:the\s+)?(?:{chronicle_nouns})\s+of\s+(?:the\s+)?(.+?)\s*[?!.]*$',
            raw,
            re.I,
        )
        if m:
            candidate = m.group(1)

    # 6. Behind-the-scenes/investigation: "Inside the Gardner Museum Heist"
    if not candidate:
        m = re.match(
            r'^(?:inside|behind)\s+(?:the\s+)?(.+?)\s+(?:disaster|collapse|heist|mystery|crash|scandal|investigation|story)\s*[?!.]*$',
            raw,
            re.I,
        )
        if m:
            candidate = m.group(1)

    # 7. Title prefix before colon or dash: "Titanic: The Sinking of the Unsinkable Ship", "Concorde - Why the Airliner Failed"
    if not candidate and re.search(r'[:|]|\s+[-–—]\s*', raw):
        prefix = re.split(r'[:|]|\s+[-–—]\s*', raw, maxsplit=1)[0].strip()
        prefix_clean = re.sub(r'^(?:the|a|an)\s+', '', prefix, flags=re.I).strip()
        if 1 <= len(prefix_clean.split()) <= 6 and not re.search(r'\b(?:and|versus|vs|top|best)\b', prefix_clean, re.I):
            candidate = prefix_clean

    # 8. Direct declarative hook / statement: "The Tacoma Narrows Bridge Collapsed in 1940"
    if not candidate:
        m = re.match(
            rf'^(?:the\s+)?([A-Z0-9][A-Za-z0-9\s&\'\.]+?)\s+(?:{verbs})\b',
            raw,
            re.I,
        )
        if m:
            candidate = m.group(1)

    if not candidate:
        return ''

    subject = re.sub(r'^(?:the|a|an)\s+', '', candidate, flags=re.I)
    subject = re.sub(r'\s+', ' ', subject).strip(' ,.;:!?\'\"')
    if not 1 <= len(subject.split()) <= 7 or re.search(r'\b(?:and|versus|vs|top|best)\b', subject, re.I):
        return ''

    key = search_key(subject)
    if not key:
        return ''

    if script:
        script_key = search_key(script)
        words = [w for w in key.split() if len(w) >= 3]
        matches = re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', script_key) or (
            words and any(re.search(r'(?<!\w)' + re.escape(w) + r'(?!\w)', script_key) for w in words if len(w) >= 4)
        )
        if not matches:
            return ''

    return subject


def collection_title_subject(title):
    """Keep a numbered film's category, rather than its first listed item."""
    match = re.match(r'^(?:(?:top|best|greatest|worst|most)\s+)?\d{1,2}\s+(.+)$', str(title or '').strip(), re.I)
    if not match:
        return ''
    subject = re.split(
        r'\s+(?:that|who|which|remembered|ranked|you\b|every|before|after|in|from|across|around|during)|[:|]',
        match.group(1),
        maxsplit=1,
        flags=re.I,
    )[0]
    subject = subject.strip(' ,.;:!?')
    if re.match(r'(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b', subject, re.I):
        return ''
    if not any(re.fullmatch(r"[A-Za-z]+s", word) for word in subject.split()):
        return ''
    return subject if 1 <= len(subject.split()) <= 9 else ''

