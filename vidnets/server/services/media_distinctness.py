"""Per-job web-media distinctness and source-diversity helpers.

Provenance: GTA VI 1-min proof job a57cb5c7 (Nov 16, 2026). Only 3
distinct web images served 5 web scenes: the Pinterest key art
(md5 f8afedec2bf23c74dfc240d075b8318e, sha256 63a2158a…) served scenes 0
AND 4, and one Vice City shot (md5 7d05b5fd0332…, sha256 ebd04e…)
served scenes 3 AND 7. All 5 web images came from Pinterest, and
scene 0 kept 1 candidate out of 8 downloads (pool=1, alternates=[]).

The rules enforced here, shared by the web resolver, the coordinator
rescue/recovery passes, and (later) other lanes:

* DISTINCTNESS is by content hash, never by URL or local path alone.
  The same bytes under a different pin, CDN size variant, or copied
  scene filename are one image. Reuse is the LAST resort: a scene may
  reuse an already-used image only after every unused verified
  candidate for that scene has been tried and failed.
* SOURCE DIVERSITY is by publisher/source host, never by CDN host.
  ``i.pinimg.com`` and ``br.pinterest.com`` are one source
  (Pinterest); a pool that is all Pinterest must keep searching
  instead of settling.
* SCENE-SUBJECT AWARENESS: the relevance of an image is judged
  against what the SCENE narrates, not the video title alone. A
  GTA V image is correct for a scene whose narration is ABOUT GTA V
  (its sales, the decade-long wait, its legacy — proof-job scene 0)
  and wrong for a scene about GTA VI content (trailers, Leonida,
  Lucia/Jason, the new Vice City). The 2002 Vice City game stays out
  everywhere: no anchor ever admits it.

This module is pure stdlib and imports nothing from the Vidnets
services, so the resolver, the coordinator, and tests can all use it
without import cycles. Identity strings deliberately match
``media_resolution_coordinator._web_asset_identity`` (``web:fingerprint:`` /
``web:url:`` / ``web:path:``) so a usage count kept in one module
compares equal in the other.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "source_host",
    "canonical_url",
    "content_identity",
    "file_content_md5",
    "UsageTracker",
    "partition_unused_first",
    "host_share",
    "dominant_source",
    "needs_more_sources",
    "promote_deferred",
    "narrated_installment_anchor",
    "anchor_matches_metadata",
    "is_legacy_installment_image",
    "alternate_anchor_allows",
    "dedupe_by_content",
]

# Publisher domains whose regional subdomains are the same source
# (br.pinterest.com, www.pinterest.com, … are all Pinterest).
_PUBLISHER_SUFFIXES = ("pinterest.com", "flickr.com", "wikimedia.org")

# CDN / thumbnail fronts that are delivery for a single publisher. The
# image host alone must never make two Pinterest pins look diverse.
_CDN_SOURCE_HOSTS = {
    "i.pinimg.com": "pinterest.com",
    "images.pinimg.com": "pinterest.com",
    "pinimg.com": "pinterest.com",
    "avatars.mds.yandex.net": "yandex.net",
    "ts1.mm.bing.net": "bing.com",
    "ts2.mm.bing.net": "bing.com",
    "tse.mm.bing.net": "bing.com",
    "th.bing.com": "bing.com",
    "tse1.mm.bing.net": "bing.com",
    "tse2.mm.bing.net": "bing.com",
    "tse3.mm.bing.net": "bing.com",
    "tse4.mm.bing.net": "bing.com",
}

_FINGERPRINT_FIELDS = ("asset_fingerprint", "fingerprint", "sha256", "sha", "md5")
_URL_FIELDS = ("url", "image_url", "media_url", "download_url", "preview_url")
_PAGE_FIELDS = ("origin_url", "source_url", "page_url")

_ROMAN_TO_ARABIC = {
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
    "vii": "7", "viii": "8", "ix": "9", "x": "10",
}
_ARABIC_TO_ROMAN = {v: k for k, v in _ROMAN_TO_ARABIC.items()}
_NUMBER_WORDS = {
    "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}
# A bare "vice city" without a version token is the 2002 game, not the
# GTA VI setting, unless the surrounding text says otherwise.
_LEGACY_CUTSCENE_PATTERNS = (
    re.compile(r"\b2002\b.{0,40}\bvice\s+city\b", re.IGNORECASE),
    re.compile(r"\bvice\s+city\b.{0,40}\b2002\b", re.IGNORECASE),
    re.compile(r"\bgrand\s+theft\s+auto\s*[:\-–]?\s*vice\s+city\b", re.IGNORECASE),
    re.compile(r"\bgta\s+vice\s+city\b", re.IGNORECASE),
)


def _host_of(url: str) -> str:
    try:
        return (urlsplit(str(url or "")).netloc or "").lower()
    except Exception:
        return ""


def source_host(row: Mapping[str, Any]) -> str:
    """Publisher/source host for a candidate row, CDN fronts collapsed.

    Prefers the page/source URL (the publisher) over the image CDN URL:
    a pin's ``source_url`` is the Pinterest pin page while its image URL
    is ``i.pinimg.com`` — both collapse to ``pinterest.com``.
    """
    if not isinstance(row, Mapping):
        return ""
    for field in _PAGE_FIELDS:
        host = _host_of(str(row.get(field) or ""))
        if host:
            break
    else:
        host = ""
    if not host:
        for field in _URL_FIELDS:
            host = _host_of(str(row.get(field) or ""))
            if host:
                break
    if not host:
        return ""
    if host in _CDN_SOURCE_HOSTS:
        return _CDN_SOURCE_HOSTS[host]
    if host.startswith("www."):
        host = host[4:]
    for suffix in _PUBLISHER_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return suffix
    for suffix, publisher in _CDN_SOURCE_HOSTS.items():
        if host.endswith("." + suffix):
            return publisher
    if host.endswith(".pinimg.com"):
        return "pinterest.com"
    return host


def canonical_url(url: str) -> str:
    """Canonical image URL: scheme + host + path, no query/fragment.

    CDN resize/cache query strings do not make a distinct asset.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return raw.casefold().rstrip("/")
    if parts.scheme and parts.netloc:
        return urlunsplit((
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            "",
            "",
        ))
    return raw.casefold().rstrip("/")


def file_content_md5(path: str) -> Optional[str]:
    """MD5 hex of a local file's bytes, or None when unreadable."""
    try:
        digest = hashlib.md5()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def content_identity(row: Mapping[str, Any]) -> str:
    """Stable content identity for a candidate row.

    Order matches the coordinator's ``_web_asset_identity``: provider
    fingerprint first, then the canonical media URL, then page URLs,
    then the local path. One addition: when no fingerprint is recorded
    but a local file exists, the file's MD5 is used — the same bytes
    copied into another scene's folder (or re-downloaded under another
    pin URL) still compare equal, where a path-only identity would not.
    """
    if not isinstance(row, Mapping):
        return ""
    for field in _FINGERPRINT_FIELDS:
        fingerprint = str(row.get(field) or "").strip().casefold()
        if fingerprint:
            return f"web:fingerprint:{fingerprint}"
    path = str(row.get("path") or row.get("selected_path") or "").strip()
    if path:
        digest = file_content_md5(path)
        if digest:
            # Same token shape as a recorded md5 fingerprint, so a row
            # carrying the md5 in a field and a row carrying only the
            # local file compare equal for the same bytes.
            return f"web:fingerprint:{digest}"
    for field in _URL_FIELDS:
        raw_url = str(row.get(field) or "").strip()
        if raw_url:
            canonical = canonical_url(raw_url)
            if canonical:
                return f"web:url:{canonical}"
    for field in _PAGE_FIELDS:
        raw_url = str(row.get(field) or "").strip()
        if raw_url:
            canonical = canonical_url(raw_url)
            if canonical:
                return f"web:url:{canonical}"
    if path:
        return f"web:path:{os.path.normcase(os.path.normpath(path))}"
    return ""


class UsageTracker:
    """Per-job count of how many scenes already use each content hash."""

    def __init__(self, counts: Optional[Mapping[str, int]] = None) -> None:
        self._counts: Dict[str, int] = dict(counts or {})

    def uses(self, identity: str) -> int:
        return int(self._counts.get(identity or "", 0))

    def is_used(self, identity: str) -> bool:
        return bool(identity) and self.uses(identity) > 0

    def claim(self, identity: str) -> int:
        if not identity:
            return 0
        self._counts[identity] = self.uses(identity) + 1
        return self._counts[identity]

    def counts(self) -> Dict[str, int]:
        return dict(self._counts)


def partition_unused_first(
    candidates: Sequence[Any],
    used: Any,
    key=None,
) -> Tuple[List[Any], List[Any]]:
    """Split candidates into (unused, already-used), order preserved.

    ``used`` may be a ``UsageTracker``, a mapping of identity -> count,
    or a set of identities. A candidate whose identity is unknown ("")
    counts as unused: an unidentified row must never be treated as a
    repeat of something else. Reuse is the caller's last resort — it
    tries every row of the first list before touching the second.
    """
    if key is None:
        key = content_identity

    def _is_used(candidate: Any) -> bool:
        identity = key(candidate) if callable(key) else ""
        if not identity:
            return False
        if isinstance(used, UsageTracker):
            return used.is_used(identity)
        if isinstance(used, Mapping):
            return int(used.get(identity, 0) or 0) > 0
        try:
            return identity in used  # type: ignore[operator]
        except Exception:
            return False

    unused: List[Any] = []
    already_used: List[Any] = []
    for candidate in candidates:
        (already_used if _is_used(candidate) else unused).append(candidate)
    return unused, already_used


def host_share(rows: Iterable[Mapping[str, Any]]) -> Counter:
    """Count of candidate rows per publisher/source host."""
    counts: Counter = Counter()
    for row in rows or []:
        host = source_host(row)
        if host:
            counts[host] += 1
    return counts


def dominant_source(
    rows: Iterable[Mapping[str, Any]],
) -> Tuple[str, int, float]:
    """(host, count, share) of the largest source in ``rows``."""
    counts = host_share(rows)
    total = sum(counts.values())
    if not total:
        return "", 0, 0.0
    host, count = counts.most_common(1)[0]
    return host, count, count / total


def needs_more_sources(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_hosts: int = 2,
    max_share: float = 0.6,
) -> bool:
    """Whether a candidate set is too single-source to settle on.

    True when fewer than ``min_hosts`` distinct publishers are present
    or one publisher holds more than ``max_share`` of the rows. An
    empty set does not need "more sources" — it needs any source.
    """
    rows = list(rows or [])
    if not rows:
        return False
    counts = host_share(rows)
    if len(counts) < max(1, int(min_hosts)):
        return True
    _host, _count, share = dominant_source(rows)
    return share > float(max_share)


def dedupe_by_content(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """Drop rows whose content identity was already seen, order kept."""
    seen = set()
    kept: List[Mapping[str, Any]] = []
    for row in rows or []:
        identity = content_identity(row)
        if identity:
            if identity in seen:
                continue
            seen.add(identity)
        kept.append(row)
    return kept


def promote_deferred(
    deferred: Sequence[Tuple[float, Mapping[str, Any]]],
    limit: int = 3,
) -> List[Tuple[float, Dict[str, Any]]]:
    """Promote floor-failed downloads to vision-pending alternates.

    ``deferred`` is (score, candidate-dict) pairs for images that were
    downloaded and passed safety/aspect checks but failed the metadata
    relevance floor (or scored below zero). When a scene's verified pool
    would otherwise be empty, these are strictly better than a graphic:
    vision — the validator — still decides each one, and a rejection
    simply moves to the next candidate. Legacy-cutscene rows are never
    promoted, and content duplicates collapse to one entry. Returns
    (score, promoted-row) pairs, best score first.
    """
    ranked = sorted(deferred, key=lambda pair: float(pair[0] or 0.0), reverse=True)
    promoted: List[Tuple[float, Dict[str, Any]]] = []
    seen = set()
    for score, row in ranked:
        if len(promoted) >= max(1, int(limit)):
            break
        if not isinstance(row, Mapping):
            continue
        metadata = " ".join(
            str(row.get(key) or "")
            for key in ("title", "url", "source_url", "page_url", "tags", "description")
        )
        if is_legacy_installment_image(metadata):
            continue
        identity = content_identity(row)
        if identity:
            if identity in seen:
                continue
            seen.add(identity)
        promoted_row = dict(row)
        promoted_row["semantic_visual_pending"] = True
        promoted_row["relevance_deferred"] = True
        promoted.append((float(score), promoted_row))
    return promoted


# ── Scene-subject awareness ──────────────────────────────────────────────
# A scene that narrates an earlier installment of a franchise ("more than
# ten years since Grand Theft Auto Five… sold more than two hundred
# million copies") may legitimately show that installment. The anchor is
# derived from the SCENE's own text, never from the video title, and it
# only fires when the scene names a DIFFERENT version than the scene's
# main subject — a GTA VI-content scene never gains a GTA V anchor.


def _version_token(text: str) -> Optional[str]:
    """Arabic version number (\"5\"/\"6\") for a roman/arabic/word token."""
    token = str(text or "").strip().lower()
    if token in _ROMAN_TO_ARABIC:
        return _ROMAN_TO_ARABIC[token]
    if token in _NUMBER_WORDS:
        return _NUMBER_WORDS[token]
    if token.isdigit():
        return str(int(token))
    return None


def _version_tokens(version: str) -> set:
    """All spellings of one version: arabic, roman, word (\"5\"/\"v\"/\"five\")."""
    tokens = {str(version)}
    roman = _ARABIC_TO_ROMAN.get(str(version))
    if roman:
        tokens.add(roman)
    for word, arabic in _NUMBER_WORDS.items():
        if arabic == str(version):
            tokens.add(word)
    return tokens


def narrated_installment_anchor(
    scene_text: str, main_subject: str
) -> Optional[Dict[str, Any]]:
    """Alternate relevance anchor when a scene narrates another installment.

    Returns ``{"franchise": ..., "version": "5", "version_tokens": {...},
    "terms": [...]}`` when the scene text names a versioned installment
    of the same franchise as ``main_subject`` but a DIFFERENT version
    (GTA V narrated in a GTA VI video, scene 0 of the proof job).
    Returns None when the scene names no other installment — the main
    subject stays the only anchor, so GTA V imagery stays wrong for
    GTA VI-content scenes.
    """
    scene = str(scene_text or "").lower()
    subject = str(main_subject or "").lower()
    if not scene:
        return None

    version_re = (
        r"(ii|iii|iv|v|vi|vii|viii|ix|x|\d+|two|three|four|five|six|seven|eight|nine|ten)\b"
    )

    def _mentions() -> List[Tuple[str, str]]:
        """(franchise, version) mentions in scene (text) order."""
        found: List[Tuple[int, str, str]] = []
        for pattern in (r"grand\s+theft\s+auto", r"\bgta\b"):
            for match in re.finditer(pattern + r"\s+" + version_re, scene):
                version = _version_token(match.group(1))
                if version:
                    found.append((match.start(), "grand theft auto", version))
        if found:
            found.sort(key=lambda item: item[0])
            return [(franchise, version) for _at, franchise, version in found]
        # Generic: main subject ends with a version token; the scene
        # text must name the same franchise stem with a version.
        subject_tokens = re.findall(r"[a-z0-9]+", subject)
        if len(subject_tokens) < 2:
            return []
        if _version_token(subject_tokens[-1]) is None:
            return []
        stem = " ".join(subject_tokens[:-1])
        if len(stem) < 3:
            return []
        for match in re.finditer(
            re.escape(stem) + r"\s+" + version_re, scene
        ):
            version = _version_token(match.group(1))
            if version:
                found.append((match.start(), stem, version))
        found.sort(key=lambda item: item[0])
        return [(franchise, version) for _at, franchise, version in found]

    mentions = _mentions()
    if not mentions:
        return None
    # The scene's topic version is the version in the main subject when
    # it has one; otherwise the FIRST version the narration names (the
    # topic sentence). The anchor is the first DIFFERENT version the
    # scene narrates — e.g. scene 0 opens on "Grand Theft Auto Six"
    # (topic) and then narrates "Grand Theft Auto Five" (the history
    # the scene is about). A scene naming only one version anchors to
    # it when the main subject carries no version of its own.
    subject_tokens = re.findall(r"[a-z0-9]+", subject)
    subject_version = (
        _version_token(subject_tokens[-1]) if subject_tokens else None
    )
    if subject_version is not None:
        topic_version = subject_version
    elif len({version for _franchise, version in mentions}) == 1:
        topic_version = mentions[0][1]
        # Single-version scene, version-less subject: the scene IS about
        # that installment, so it is its own anchor.
        franchise, version = mentions[0]
        tokens = _version_tokens(version)
        terms = sorted(set(re.findall(r"[a-z0-9]+", franchise)) | tokens | {"gta"})
        return {
            "franchise": franchise,
            "version": version,
            "version_tokens": tokens,
            "terms": terms,
        }
    else:
        topic_version = mentions[0][1]
    for franchise, version in mentions:
        if version == topic_version:
            continue
        tokens = _version_tokens(version)
        terms = sorted(set(re.findall(r"[a-z0-9]+", franchise)) | tokens | {"gta"})
        return {
            "franchise": franchise,
            "version": version,
            "version_tokens": tokens,
            "terms": terms,
        }
    return None


def anchor_matches_metadata(anchor: Optional[Mapping[str, Any]], metadata_text: str) -> bool:
    """Whether provider metadata shows the anchor's franchise + version."""
    if not anchor:
        return False
    text = str(metadata_text or "").lower()
    franchise = str(anchor.get("franchise") or "")
    franchise_words = [w for w in re.findall(r"[a-z0-9]+", franchise) if len(w) >= 3]
    if franchise_words and not all(
        re.search(r"\b" + re.escape(word) + r"\b", text) for word in franchise_words
    ):
        # Acronym form ("gta v") counts when the full franchise is absent.
        if not re.search(r"\bgta\b", text):
            return False
    version_tokens = set(anchor.get("version_tokens") or set())
    if not version_tokens:
        return False
    return any(
        re.search(r"\b" + re.escape(token) + r"\b", text) for token in version_tokens
    )


def is_legacy_installment_image(metadata_text: str) -> bool:
    """Whether metadata marks the 2002 Vice City game (never admissible).

    The 2002 game is not the GTA VI setting and not GTA V: a row whose
    title/URL says "2002 … Vice City", "Grand Theft Auto: Vice City",
    or "GTA Vice City" is a legacy cutscene still, even when the scene
    is about Vice City. A "Vice City" mention WITHOUT a 2002/game
    marker is not legacy by itself — the new Vice City is GTA VI's
    setting and is judged on its own scene.
    """
    text = str(metadata_text or "")
    return any(pattern.search(text) for pattern in _LEGACY_CUTSCENE_PATTERNS)


def alternate_anchor_allows(
    anchor: Optional[Mapping[str, Any]], metadata_text: str
) -> bool:
    """Whether the scene-narrated installment anchor admits this row."""
    if not anchor:
        return False
    if is_legacy_installment_image(metadata_text):
        return False
    return anchor_matches_metadata(anchor, metadata_text)
