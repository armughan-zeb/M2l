"""Small, provider-safe normalizer for YouTube retrieval queries.

The YouTube search box is not a policy engine.  Passing a sentence copied from
the narration together with a long list of ``-keyword`` operators makes the
provider rank on the wrong words and, in some clients, treats the whole query
as one brittle exact phrase.  Safety decisions belong to the metadata/frame
gates after a result is downloaded; this module keeps the search string a
short, human noun phrase instead.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


_FORMAT_EXCLUSION_WORDS = {
    "facecam", "webcam", "streamer", "reaction", "react", "reacts",
    "podcast", "subtitles", "subtitle", "captioned", "caption", "lyrics",
    "slideshow", "presentation", "interview", "host", "presenter",
    "talking", "talk", "talkinghead", "talking-head", "watchparty", "watch-party", "livestream",
    "live-stream", "virtualreality", "reenactment", "aigenerated",
}

# These are narration/editorial glue, not things a camera can show.  Keep the
# list deliberately conservative: concrete nouns such as ``constitutional``
# and useful visual verbs such as ``driving`` must survive.
_NARRATIVE_NOISE = {
    "grew", "grow", "grows", "became", "becomes", "become", "returns",
    "return", "returned", "includes", "included", "features", "featured",
    "reveals", "revealed", "protect", "protects", "protected", "drives",
    "views", "view", "shows", "show", "talks", "talked", "scoring",
    "pressure", "away", "inside", "behind",
    "remained", "continues", "continued", "began", "begun", "begins",
    "known", "called", "considered", "claimed", "reported", "showed",
    "told", "explains", "explained", "discusses", "discussion", "means",
    "meaningful", "meaning", "story", "stories", "history", "journey",
    "what", "why", "how", "when", "where", "which", "who", "that",
    "this", "these", "those", "there", "they", "them", "their", "he",
    "she", "his", "her", "its", "our", "your", "you", "we", "us",
    "about", "with", "from", "into", "onto", "toward", "towards", "over",
    "under", "behind", "around", "through", "inside", "while", "before",
    "after", "during", "often", "usually", "just", "really", "very",
    "also", "still", "finally", "suddenly", "entirely", "directly",
    "clean", "real", "actual", "valid", "important", "more", "most",
}

_TITLE_NOISE = {"king", "queen", "prince", "princess", "duke", "duchess"}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but",
    "can", "could", "did", "do", "does", "doing", "for", "has", "have",
    "having", "if", "in", "is", "it", "may", "might", "must", "nor", "not",
    "of", "on", "or", "out", "should", "so", "than", "the", "then", "to",
    "too", "until", "upon", "was", "were", "will", "would", "you", "your",
}

_MEDIA_TERMS = {
    "footage", "film", "archive", "archival", "newsreel", "b-roll", "broll",
    "video", "gameplay", "trailer", "walkaround", "driving", "road", "test",
    "broadcast", "appearance", "ceremony", "event", "operations", "cab",
    "conductor", "locomotive", "engineer", "match", "training", "launch",
    "b", "roll", "walking", "tour", "drone", "street", "landmark",
    "clip", "scene", "action", "documentary", "highlight", "cinematic", "historical",
}

_FALLBACK_BY_CATEGORY = {
    "gaming": "gameplay footage",
    "history": "archival footage",
    "disaster": "archival footage",
    "person": "event footage",
    "sports": "match footage",
    "automotive": "driving footage",
    "transportation": "operations footage",
}


def _clean(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip(" \t\r\n\"'`.,:;|/")


def _key(value: str) -> str:
    from .documentary_subject import search_key
    return search_key(value)


def _tokens(value: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:['\-][^\W_]+)*", value, re.UNICODE)


def _contains_phrase(text: str, phrase: str) -> bool:
    wanted = _key(phrase)
    return bool(wanted) and wanted in _key(text)


def _strip_exclusion_tokens(text: str) -> str:
    """Remove search operators and their format words, not safety metadata."""
    # ``_tokens`` intentionally drops punctuation, so remove the operator
    # before tokenization; otherwise unknown exclusions such as ``-breaking``
    # would silently turn back into positive search terms.
    text = re.sub(r"(?<![A-Za-z0-9])-\s*[A-Za-z0-9][A-Za-z0-9_-]*", " ", text)
    words = _tokens(text)
    result: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        key = word.casefold().strip(".,:;!?()[]{}")
        # Every negative operator is local policy, never positive retrieval
        # intent.  Do not leak it to YouTube's ranking algorithm.
        if key.startswith("-"):
            index += 1
            continue
        if key in {"no", "without"} and index + 1 < len(words):
            next_key = words[index + 1].casefold().replace("_", "-")
            if next_key in _FORMAT_EXCLUSION_WORDS:
                index += 2
                continue
            # ``no commentary`` is a useful gameplay/footage intent; retain
            # it as a normal phrase rather than a negative operator.
            if next_key != "commentary":
                index += 1
                continue
        if key in _FORMAT_EXCLUSION_WORDS:
            index += 1
            continue
        result.append(word)
        index += 1
    return " ".join(result)


def normalize_youtube_query(
    value: Any,
    *,
    anchor: str = "",
    subject: str = "",
    category: str = "",
    fallback: str = "documentary footage",
    max_words: int = 12,
    max_chars: int = 160,
) -> str:
    """Return a compact full-name YouTube query.

    The caller can still keep all exclusion terms in its retrieval contract;
    they are applied by candidate metadata and visual verification.  Only the
    positive search phrase is sent to YouTube.
    """
    raw = _strip_exclusion_tokens(_clean(value))
    anchor = _clean(anchor)
    subject = _clean(subject)
    fallback = _clean(fallback) or "documentary footage"

    # Remove narration glue and duplicate tokens while protecting the complete
    # entity anchor.  ``King Charles III`` and ``Grand Theft Auto VI`` therefore
    # remain intact even when an LLM supplied a full sentence around them.
    anchor_keys = set(_key(anchor).split())
    words: list[str] = []
    seen: set[str] = set()
    for word in _tokens(raw):
        key = word.casefold().strip(".,:;!?()[]{}")
        normalized_key = _key(word)
        if not normalized_key:
            continue
        if key in _NARRATIVE_NOISE or key in _STOPWORDS or (
            key in _TITLE_NOISE and anchor and normalized_key not in anchor_keys
        ):
            # Never remove a word that is part of the canonical entity.
            if normalized_key not in anchor_keys:
                continue
        if normalized_key in seen and normalized_key not in anchor_keys:
            continue
        seen.add(normalized_key)
        words.append(word.strip(".,:;!?()[]{}"))

    # Add the full canonical name when the LLM only emitted a surname or an
    # abbreviated topic.  This is the key guard against namesake results.
    current_key = _key(" ".join(words))
    if anchor and not _contains_phrase(" ".join(words), anchor):
        anchor_words = _tokens(anchor)
        overlap = sum(1 for token in anchor_words if _key(token) in current_key.split())
        if overlap:
            words = anchor_words + [word for word in words if _key(word) not in {_key(t) for t in anchor_words}]
        else:
            words = anchor_words + words

    # A distinct subject is useful for listicle scenes, but only when it is a
    # short phrase.  Full sentences must never be injected into the query.
    if subject and not _contains_phrase(" ".join(words), subject):
        subject_words = _tokens(subject)
        if 1 <= len(subject_words) <= 5:
            words.extend(subject_words)

    # Keep at least one visual retrieval intent.  Avoid adding ``clean`` or a
    # second quality adjective; those were the source of the long noisy rows.
    if not any(_key(word) in _MEDIA_TERMS for word in words):
        fallback_words = [word for word in _tokens(_FALLBACK_BY_CATEGORY.get(_key(category), fallback))]
        for word in fallback_words:
            if _key(word) not in {_key(item) for item in words}:
                words.append(word)

    words = [word for word in words if word]
    if not words:
        words = _tokens(anchor or fallback) or ["documentary", "footage"]
    # Keep entity-first ordering and a bounded phrase.  Trimming is done after
    # anchor insertion so a long scene description cannot drop the name.
    if anchor:
        anchor_words = _tokens(anchor)
        anchor_len = len(anchor_words)
        head = words[:anchor_len] if len(words) >= anchor_len else anchor_words
        tail = words[anchor_len:] if len(words) > anchor_len else []
        # Keep proper-name casing for the anchor and for any additional named
        # entity (for example ``Rockstar Games`` or ``Grand Theft Auto VI``).
        # YouTube ranking is case-insensitive, but retaining the source casing
        # makes the generated query auditable and protects full-name anchors.
        words = head + tail[: max(0, int(max_words) - len(head))]
    else:
        words = words[:max_words]
    result = re.sub(r"\s+", " ", " ".join(words)).strip()
    if len(result) > max_chars:
        result = result[:max_chars].rsplit(" ", 1)[0].strip()
    return result or _clean(anchor or fallback)


def normalize_youtube_queries(values: Iterable[Any], **kwargs: Any) -> list[str]:
    """Normalize, deduplicate, and keep the original ordering of query waves."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        query = normalize_youtube_query(value, **kwargs)
        key = _key(query)
        if query and key not in seen:
            seen.add(key)
            result.append(query)
    return result
