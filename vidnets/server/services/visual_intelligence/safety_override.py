"""Qwen-Vision safety override — kills false-positive ``high_exposure_visual`` rejections.

The legacy ``web_image.safety._exposure_risk`` heuristic uses a CbCr skin-ratio
test that misfires on legitimate sports/documentary content (football players
in shorts on a green pitch, beach scenes, historical art). When the heuristic
flags an image as ``high_exposure_visual`` or ``suggestive_person_visual``,
this module asks Qwen 3.5 Flash Vision to confirm whether the image is
actually unsafe/adult content or legitimate documentary content.

Only when Qwen confirms the image is unsafe do we honour the rejection.
When Qwen says it is safe (e.g., "athletes in uniforms on a pitch"), the
image is reinstated and proceeds to normal scoring.

Gate:
  - ``VIDZEO_VIP_SAFETY_OVERRIDE`` env var (default: "1" = ON)
  - Requires an OpenRouter key (auto-degrades to noop if missing)
  - Triggers ONLY on ``high_exposure_visual`` / ``suggestive_person_visual``
    reasons; genuine NSFW metadata hits (adult_domain, adult_metadata) are
    never overridden.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from .vision_client import QwenVisionClient, VisionClientError


# Reasons that are visual heuristics and therefore overridable by Qwen.
_OVERRIDABLE_REASONS = {"high_exposure_visual", "suggestive_person_visual"}

# Reasons that are metadata-based and never overridden.
_NEVER_OVERRIDE = {"adult_domain", "adult_metadata"}


SAFETY_OVERRIDE_SYSTEM_PROMPT = (
    "You are a content safety reviewer for a documentary video studio. "
    "You are shown an image that a conservative pixel-level skin-tone "
    "heuristic flagged as potentially unsafe. Your job is to confirm or "
    "overrule that flag. Documentary, sports, historical, artistic, news, "
    "and educational content is SAFE. Only confirm unsafe if the image is "
    "genuinely adult/pornographic/nude content. Return STRICT JSON only."
)

SAFETY_OVERRIDE_USER_PROMPT = (
    "Heuristic flag: {reason}\n"
    "Candidate metadata title: {title}\n"
    "Candidate metadata source: {source}\n\n"
    "Analyze this image and return compact JSON with EXACTLY these keys:\n"
    "{{\n"
    "  \"is_unsafe\": false,             # true only if genuinely adult content\n"
    "  \"category\": \"sports|documentary|news|art|portrait|landscape|other|adult\",\n"
    "  \"confidence\": 0-100,             # your confidence in the judgment\n"
    "  \"reason\": \"one short sentence describing what is actually in the image\"\n"
    "}}\n"
    "Return JSON only. No markdown, no prose."
)


def _override_enabled() -> bool:
    return str(os.environ.get("VIDZEO_VIP_SAFETY_OVERRIDE", "1")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _resolve_openrouter_key() -> str:
    from ..job_context import current_job
    context = current_job()
    if context.get('llm_provider') == 'external_agent':
        from ..agent_bridge import AGENT_HANDLE
        return AGENT_HANDLE + str(context.get('agent_owner_id') or '')
    if context.get('llm_key'):
        return context['llm_key']
    return str(
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
        or ""
    ).strip()


def is_overridable_reason(reason: str) -> bool:
    """Return True when ``reason`` is a visual heuristic Qwen can override."""
    return reason in _OVERRIDABLE_REASONS


def should_override(reason: str) -> bool:
    """Return True if the safety override should even attempt for this reason."""
    if not reason or reason in _NEVER_OVERRIDE:
        return False
    if not _override_enabled():
        return False
    return reason in _OVERRIDABLE_REASONS


def override_safety_flag(
    image_path: str,
    reason: str,
    *,
    title: str = "",
    source: str = "",
) -> Tuple[bool, str]:
    """Ask Qwen Vision to confirm or overrule a visual safety flag.

    Args:
        image_path: local path to the downloaded image
        reason: the safety heuristic's rejection reason
        title: candidate metadata title (for context)
        source: candidate metadata source domain

    Returns:
        ``(is_actually_unsafe, qwen_reason)``. When the override succeeds
        and Qwen says the image is safe, ``is_actually_unsafe`` is False
        and the caller should reinstate the image. When Qwen confirms the
        image is unsafe, ``is_actually_unsafe`` is True. When the override
        is disabled, unavailable, or errors, returns ``(True, reason)`` so
        the caller falls back to the original heuristic rejection.
    """
    if not should_override(reason):
        return True, reason
    if not image_path or not os.path.exists(image_path):
        return True, reason

    # Use the shared VisualDecisionQueue client (single semaphore across
    # all Qwen Vision callers).
    from .queue import get_visual_queue
    client = get_visual_queue().get_client()
    if client is None:
        # No key / vision unavailable -> honour the original heuristic.
        return True, reason

    prompt = SAFETY_OVERRIDE_USER_PROMPT.format(
        reason=reason,
        title=(title or "")[:120],
        source=(source or "")[:80],
    )
    try:
        raw = client._post(
            [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _encode_image(image_path)}},
            ],
            system=SAFETY_OVERRIDE_SYSTEM_PROMPT,
            json_mode=False,
            max_tokens=200,
        )
        parsed = client._parse_json(raw)
        if not isinstance(parsed, dict):
            return True, reason
        is_unsafe = bool(parsed.get("is_unsafe"))
        qwen_reason = str(parsed.get("reason") or parsed.get("category") or reason)
        return is_unsafe, qwen_reason
    except Exception:
        return True, reason


def _encode_image(image_path: str) -> str:
    """Encode an image to a data URI for the vision API."""
    from .vision_client import _encode_image_to_data_uri
    uri = _encode_image_to_data_uri(image_path, max_long_edge=512)
    if not uri:
        raise VisionClientError(f"Could not encode image: {image_path}")
    return uri
