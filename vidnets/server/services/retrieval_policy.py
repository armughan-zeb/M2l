from __future__ import annotations

import json
import os
import time
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse


_POLICY_CACHE: Dict[str, Any] = {}
_POLICY_CACHE_AT = 0.0
_REMOTE_CACHE_AT = 0.0
_REMOTE_POLICY: Dict[str, Any] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_policy_path() -> Path:
    return Path(os.environ.get(
        "VIDZEO_RETRIEVAL_POLICY_FILE",
        str(_repo_root() / "server" / "data" / "retrieval_policy.json"),
    ))


def _load_json_file(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _merge_policy(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_policy(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_remote_policy() -> Dict[str, Any]:
    global _REMOTE_CACHE_AT, _REMOTE_POLICY
    url = os.environ.get("VIDZEO_RETRIEVAL_POLICY_URL", "").strip()
    if not url:
        return {}
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        return {}
    ttl = max(60, int(os.environ.get("VIDZEO_RETRIEVAL_POLICY_TTL", "3600") or 3600))
    now = time.time()
    if _REMOTE_POLICY and now - _REMOTE_CACHE_AT < ttl:
        return _REMOTE_POLICY
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VidzeoLocal/1.0"})
        with urllib.request.urlopen(req, timeout=3) as response:  # nosec B310
            if response.status >= 400:
                return _REMOTE_POLICY or {}
            payload = json.loads(response.read(256_000).decode("utf-8", "replace"))
        if isinstance(payload, dict):
            _REMOTE_POLICY = payload
            _REMOTE_CACHE_AT = now
    except Exception:
        pass
    return _REMOTE_POLICY or {}


def get_retrieval_policy(refresh: bool = False) -> Dict[str, Any]:
    global _POLICY_CACHE, _POLICY_CACHE_AT
    now = time.time()
    if _POLICY_CACHE and not refresh and now - _POLICY_CACHE_AT < 30:
        return _POLICY_CACHE
    local_policy = _load_json_file(_default_policy_path())
    remote_policy = _load_remote_policy()
    _POLICY_CACHE = _merge_policy(local_policy, remote_policy)
    _POLICY_CACHE_AT = now
    return _POLICY_CACHE


def _terms(section: str, key: str) -> List[str]:
    value = (get_retrieval_policy().get(section) or {}).get(key) or []
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower() for item in value if str(item).strip()]


def text_has_policy_term(text: str, section: str, key: str) -> bool:
    lower = str(text or "").lower()
    return any(term in lower for term in _terms(section, key))


def candidate_text(candidate: Dict[str, Any]) -> str:
    return " ".join(str(candidate.get(field) or "") for field in (
        "title",
        "description",
        "channel",
        "uploader",
        "url",
        "webpage_url",
    )).lower()


def candidate_domain(candidate: Dict[str, Any]) -> str:
    raw = str(candidate.get("url") or candidate.get("webpage_url") or "")
    try:
        return (urlparse(raw).netloc or "").lower()
    except Exception:
        return ""


def policy_source_adjustment(candidate: Dict[str, Any], medium: str = "youtube") -> Dict[str, Any]:
    text = candidate_text(candidate)
    domain = candidate_domain(candidate)
    trusted_terms = _terms(medium, "trusted_channel_terms") + _terms(medium, "trusted_domain_terms")
    blocked_terms = _terms(medium, "blocked_metadata_terms")
    high_risk_terms = _terms(medium, "high_risk_metadata_terms")

    trust_hits = [term for term in trusted_terms if term and (term in text or term in domain)]
    blocked_hits = [term for term in blocked_terms if term and term in text]
    high_risk_hits = [term for term in high_risk_terms if term and term in text]
    return {
        "trusted": bool(trust_hits),
        "trust_hits": trust_hits[:5],
        "blocked_hits": blocked_hits[:5],
        "high_risk_hits": high_risk_hits[:5],
        "source_quality_delta": 8.0 if trust_hits else 0.0,
        "copyright_risk_delta": (10 * len(blocked_hits)) + (8 * len(high_risk_hits)),
    }


def transformation_policy() -> Dict[str, Any]:
    return dict(((get_retrieval_policy().get("youtube") or {}).get("transformative_defaults") or {}))
