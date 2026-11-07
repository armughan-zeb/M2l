"""Deterministic provenance and rights-risk classification.

This is not a legal clearance decision. It makes the source evidence visible
and lets the resolver prefer candidates with usable license metadata while
keeping relevance as the hard editorial gate.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping


_OPEN_LICENSE_MARKERS = (
    "creative commons", "cc by", "cc0", "public domain", "pdm",
    "pexels license", "pixabay content license", "unsplash license",
    "open government license", "government work",
)
_COMMERCIAL_MARKERS = (
    "getty", "bigstock", "shutterstock", "adobe stock", "alamy",
    "istock", "depositphotos", "pond5", "stock photo",
)


def classify_rights(candidate: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Return normalized, auditable rights signals for a provider candidate."""
    row = candidate if isinstance(candidate, Mapping) else {}
    provider = str(row.get("provider") or row.get("source") or "").strip().lower()
    license_name = " ".join(
        str(row.get(key) or "").strip()
        for key in ("license", "license_name", "rights", "rights_status")
    ).strip()
    license_url = str(row.get("license_url") or "").strip()
    source_url = str(
        row.get("source_url") or row.get("origin_url") or row.get("page_url")
        or row.get("webpage_url") or row.get("url") or ""
    ).strip()
    haystack = f"{provider} {license_name} {license_url} {source_url}".casefold()

    if any(marker in haystack for marker in _COMMERCIAL_MARKERS):
        return {
            "rights_status": "commercial_license_required",
            "rights_risk": "high",
            "rights_review_required": True,
            "rights_evidence": "commercial_stock_or_rights_restricted_source",
        }
    if any(marker in haystack for marker in _OPEN_LICENSE_MARKERS):
        return {
            "rights_status": "reported_open_license",
            "rights_risk": "low",
            "rights_review_required": True,
            "rights_evidence": "provider_or_metadata_reports_open_license",
        }
    if provider in {"youtube", "youtube clips", "yt", "youtube_pool_moment"} or "youtube.com" in haystack or "youtu.be" in haystack:
        return {
            "rights_status": "platform_terms_review_required",
            "rights_risk": "high",
            "rights_review_required": True,
            "rights_evidence": "youtube_source_without_reuse_clearance",
        }
    if license_name or license_url:
        return {
            "rights_status": "reported_license_unverified",
            "rights_risk": "medium",
            "rights_review_required": True,
            "rights_evidence": "license_metadata_present_not_independently_cleared",
        }
    return {
        "rights_status": "unknown",
        "rights_risk": "high",
        "rights_review_required": True,
        "rights_evidence": "no_license_evidence",
    }


def annotate_rights(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Add provenance fields without overwriting explicit provider evidence."""
    if not isinstance(candidate, dict):
        return candidate
    signals = classify_rights(candidate)
    for key, value in signals.items():
        if key not in candidate or candidate.get(key) in (None, ""):
            candidate[key] = value
    return candidate

