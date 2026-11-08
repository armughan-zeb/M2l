"""Qwen Flash Vision client via OpenRouter multimodal chat completions.

OpenRouter follows the OpenAI vision format: messages carry a content list
with ``{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}``.

This module is intentionally self-contained — it does NOT extend the legacy
text-only ``OpenRouterLLM`` in ``services.openrouter_llm``. The two clients
share the same endpoint and key, but the vision payload schema is different,
so keeping them apart avoids any regression in the existing pipeline.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests

from .decision import normalize_overlay_regions
from .cache import cache_vision_response

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Qwen3.5 Flash is the production visual verifier. It is the requested cheap
# multimodal default. Keep the route single-model: silently switching a
# visual decision to a different model made results non-reproducible and was
# the reason the old trace could report a healthy probe while later requests
# were being judged by a different provider.
DEFAULT_QWEN_VISION_MODEL = os.environ.get(
    "VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23"
)
DEEP_QWEN_VISION_MODEL = os.environ.get(
    "VIDZEO_QWEN_VISION_DEEP_MODEL", "qwen/qwen3.5-flash-02-23"
)
# Deliberately empty. Transport retries stay on Qwen Flash; media retrieval
# has its own deterministic/local fallback when the provider is unavailable.
FREE_VISION_FALLBACK_MODELS: list[str] = [
    "google/gemma-4-26b-a4b-it:free",
    "nex-agi/nex-n2.5-mini:free",
]



class VisionClientError(Exception):
    """Raised when the vision client cannot complete a request."""


def _verbose() -> bool:
    return str(os.environ.get("VIDZEO_VISUAL_INTELLIGENCE_VERBOSE", "0")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _model_name() -> str:
    return str(os.environ.get("VIDZEO_QWEN_VISION_MODEL") or DEFAULT_QWEN_VISION_MODEL).strip()


def _default_timeout() -> float:
    try:
        return max(90.0, float(os.environ.get("VIDZEO_QWEN_VISION_TIMEOUT_SECONDS", "120") or 120))
    except Exception:
        return 120.0


def _default_attempts() -> int:
    try:
        return max(1, int(os.environ.get("VIDZEO_QWEN_VISION_MAX_ATTEMPTS", "3") or 3))
    except Exception:
        return 2


def _retry_after_seconds(response, fallback: float) -> float:
    raw = ""
    try:
        raw = response.headers.get("Retry-After", "")
    except Exception:
        raw = ""
    try:
        return max(0.0, min(30.0, float(raw))) if str(raw).strip() else max(0.0, fallback)
    except (TypeError, ValueError):
        return max(0.0, fallback)


def _max_image_bytes() -> int:
    """Hard cap on a single image payload (side of the request) in bytes."""
    try:
        return max(64 * 1024, int(os.environ.get("VIDZEO_QWEN_VISION_MAX_IMAGE_BYTES", str(2 * 1024 * 1024)) or (2 * 1024 * 1024)))
    except Exception:
        return 2 * 1024 * 1024


def _max_frame_tokens_hint() -> int:
    """Soft cap on total base64 bytes across all frames in one clip request."""
    try:
        return max(256 * 1024, int(os.environ.get("VIDZEO_QWEN_VISION_MAX_CLIP_PAYLOAD_BYTES", str(1536 * 1024)) or (1536 * 1024)))
    except Exception:
        return 1536 * 1024


def _vision_image_probe_edge() -> int:
    """Long edge used for still-image relevance probes.

    The probe is only a semantic check; the original high-resolution asset is
    kept for rendering. A 512px default is enough for identity/action cues and
    keeps OpenRouter payloads small on large jobs.
    """
    try:
        return max(
            256,
            min(
                768,
                int(os.environ.get("VIDZEO_VISION_IMAGE_PROBE_EDGE", "512") or 512),
            ),
        )
    except (TypeError, ValueError):
        return 512


def _vision_frame_probe_edge() -> int:
    """Long edge used for sampled YouTube frames sent to vision."""
    try:
        return max(
            240,
            min(
                640,
                int(os.environ.get("VIDZEO_VISION_FRAME_PROBE_EDGE", "384") or 384),
            ),
        )
    except (TypeError, ValueError):
        return 384


def _encode_image_to_data_uri(path: str, max_long_edge: int = 768, quality: int = 82) -> Optional[str]:
    """Read an image, downscale if too large, return a data:image/jpeg;base64, URI.

    Downscaling keeps the payload small and the response fast on the Flash
    tier. ``quality`` lets clip-frame callers degrade harder (smaller, cheaper,
    faster verification). Returns ``None`` if encoding fails.
    """
    try:
        from PIL import Image, ImageFile  # type: ignore
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        img = Image.open(path)
        img.load()
        w, h = img.size
        scale = min(1.0, float(max_long_edge) / float(max(w, h) or max_long_edge))
        if scale < 1.0:
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            img = img.convert("RGB").resize(new_size, Image.LANCZOS)
        else:
            img = img.convert("RGB")
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) > _max_image_bytes():
            buf.seek(0)
            buf.truncate(0)
            img.save(buf, format="JPEG", quality=max(45, quality - 25), optimize=True)
            data = buf.getvalue()
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except Exception as exc:
        if _verbose():
            print(f"   [vip] image encode failed {path}: {exc}")
        return None


def _extract_json_text(text: str) -> Optional[str]:
    """Pull the first valid JSON object/array out of a model response."""
    candidate = str(text or "").strip()
    if not candidate:
        return None
    if "```" in candidate:
        for part in candidate.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{") or part.startswith("["):
                candidate = part
                break
    ranges = []
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        ranges.append(candidate[start:end + 1])
    start, end = candidate.find("["), candidate.rfind("]")
    if start != -1 and end > start:
        ranges.append(candidate[start:end + 1])
    ranges.sort(key=len, reverse=True)
    for raw in ranges:
        cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", cleaned)
        try:
            json.loads(cleaned)
            return cleaned
        except Exception:
            continue
    return None


_WORD_SCORES = {
    "high": 90.0, "medium": 65.0, "low": 25.0, "none": 0.0,
    "strong": 85.0, "moderate": 60.0, "weak": 20.0,
    "excellent": 95.0, "good": 82.0, "fair": 55.0, "poor": 20.0,
    "very high": 95.0, "very low": 10.0, "not applicable": 0.0,
}


def _coerce_float(value, default: float = 0.0) -> float:
    try:
        num = float(value)
        if 0.0 <= num <= 1.0:
            num *= 100.0
        return max(0.0, min(100.0, num))
    except Exception:
        label = str(value or "").strip().lower()
        if label in _WORD_SCORES:
            return _WORD_SCORES[label]
        return default


def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return default


def _coerce_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


_V2_VERDICT_FIELDS = (
    "candidate_id",
    "entity_conflict",
    "identity_score",
    "action_score",
    "context_score",
    "quality",
    "visible_overlays",
    "subject_box",
    "safe_text_regions",
)


def _v2_schema_metadata(payload: dict) -> tuple[list[str], list[str]]:
    """Preserve enough raw-shape evidence for strict post-normalization QA."""
    present = [field for field in _V2_VERDICT_FIELDS if field in payload]
    errors: list[str] = []
    if "candidate_id" in payload and not isinstance(payload.get("candidate_id"), str):
        errors.append("candidate_id_not_string")
    if "entity_conflict" in payload and not isinstance(payload.get("entity_conflict"), bool):
        raw_val = payload.get("entity_conflict")
        if not (isinstance(raw_val, (int, float)) and raw_val in (0, 1, 0.0, 1.0)) and not (
            isinstance(raw_val, str) and raw_val.strip().lower() in ("true", "false", "0", "1")
        ):
            errors.append("entity_conflict_not_boolean")
    for field in ("identity_score", "action_score", "context_score", "quality"):
        if field not in payload:
            continue
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{field}_not_number")
        elif not math.isfinite(float(value)) or not 0.0 <= float(value) <= 100.0:
            errors.append(f"{field}_out_of_range")
    if "visible_overlays" in payload and not isinstance(payload.get("visible_overlays"), list):
        errors.append("visible_overlays_not_array")
    if "subject_box" in payload and not isinstance(payload.get("subject_box"), dict):
        errors.append("subject_box_not_object")
    if "safe_text_regions" in payload and not isinstance(payload.get("safe_text_regions"), list):
        errors.append("safe_text_regions_not_array")
    return present, errors


def _normalise_image_response(payload: dict) -> dict:
    schema_present, schema_errors = _v2_schema_metadata(payload)
    return {
        **({'identity_confirmed': _coerce_bool(payload['identity_confirmed'])} if 'identity_confirmed' in payload else {}),
        "candidate_id": _coerce_str(payload.get("candidate_id")),
        "entity_conflict": _coerce_bool(payload.get("entity_conflict")),
        "identity_score": _coerce_float(payload.get("identity_score")),
        "action_score": _coerce_float(payload.get("action_score")),
        "context_score": _coerce_float(payload.get("context_score")),
        "relevance": _coerce_float(payload.get("relevance")),
        "quality": _coerce_float(payload.get("quality")),
        "historical_match": _coerce_float(payload.get("historical_match")),
        "composition": _coerce_float(payload.get("composition")),
        "watermark": _coerce_bool(payload.get("watermark")),
        "has_text": _coerce_bool(payload.get("has_text")),
        "source_presentation_type": _coerce_str(payload.get('source_presentation_type'), 'unknown'),
        "unsafe_content": _coerce_bool(
            payload.get("unsafe_content")
            or payload.get("adult_content")
            or payload.get("nsfw")
        ),
        "suggestive_content": _coerce_bool(
            payload.get("suggestive_content")
            or payload.get("suggestive")
        ),
        "synthetic_content": _coerce_bool(
            payload.get("synthetic_content")
            or payload.get("is_synthetic")
            or payload.get("cgi")
            or payload.get("rendered")
        ),
        "overlay_type": _coerce_str(payload.get("overlay_type"), "none"),
        "overlay_regions": [r.to_dict() for r in normalize_overlay_regions(payload.get("overlay_regions"), 1)],
        "fatal_mismatch": _coerce_bool(payload.get("fatal_mismatch")),
        **({"domain_match": _coerce_bool(payload["domain_match"])} if "domain_match" in payload else {}),
        **({"scene_match": _coerce_bool(payload["scene_match"])} if "scene_match" in payload else {}),
        **({"event_mismatch": _coerce_bool(payload["event_mismatch"])} if "event_mismatch" in payload else {}),
        **({"location_mismatch": _coerce_bool(payload["location_mismatch"])} if "location_mismatch" in payload else {}),
        "confidence": _coerce_float(payload.get("confidence")),
        "final_score": _coerce_float(payload.get("final_score")),
        "reason": _coerce_str(payload.get("reason")),
        "scene_description": _coerce_str(payload.get("scene_description")),
        "motion_effect": _coerce_str(payload.get("motion_effect"), "none"),
        "text_safe_area": _coerce_str(payload.get("text_safe_area"), "none"),
        "subject_box": payload.get("subject_box") if isinstance(payload.get("subject_box"), dict) else {},
        "safe_text_regions": payload.get("safe_text_regions") if isinstance(payload.get("safe_text_regions"), list) else [],
        "visible_overlays": payload.get("visible_overlays") if isinstance(payload.get("visible_overlays"), list) else [],
        "_schema_present": schema_present,
        "_schema_errors": schema_errors,
    }


def _normalise_clip_response(payload: dict) -> dict:
    subjects = payload.get("subjects")
    if isinstance(subjects, str):
        subjects = [s.strip() for s in subjects.split(",") if s.strip()]
    elif not isinstance(subjects, list):
        subjects = []
    schema_present, schema_errors = _v2_schema_metadata(payload)
    return {
        **({'identity_confirmed': _coerce_bool(payload['identity_confirmed'])} if 'identity_confirmed' in payload else {}),
        "candidate_id": _coerce_str(payload.get("candidate_id")),
        "entity_conflict": _coerce_bool(payload.get("entity_conflict")),
        "identity_score": _coerce_float(payload.get("identity_score")),
        "action_score": _coerce_float(payload.get("action_score")),
        "context_score": _coerce_float(payload.get("context_score")),
        "scene_description": _coerce_str(payload.get("scene_description")),
        "category": _coerce_str(payload.get("category"), "other"),
        "subjects": [str(s) for s in subjects if str(s).strip()],
        "shot_type": _coerce_str(payload.get("shot_type"), "medium"),
        "story_relevance": _coerce_float(payload.get("story_relevance")),
        "visual_quality": _coerce_float(payload.get("visual_quality")),
        "quality": _coerce_float(payload.get("quality")),
        "documentary_value": _coerce_float(payload.get("documentary_value")),
        "emotional_score": _coerce_float(payload.get("emotional_score")),
        "cinematic_score": _coerce_float(payload.get("cinematic_score")),
        "watermark": _coerce_bool(payload.get("watermark")),
        "subtitle": _coerce_bool(payload.get("subtitle")),
        "has_text_overlay": _coerce_bool(payload.get("has_text_overlay")),
        "talking_head": _coerce_bool(payload.get("talking_head")),
        "talking_head_confidence": _coerce_float(payload.get("talking_head_confidence"),
                                                100.0 if _coerce_bool(payload.get("talking_head")) else 0.0),
        "source_presentation_type": _coerce_str(payload.get("source_presentation_type"), "unknown"),
        "synthetic_content": _coerce_bool(payload.get("synthetic_content") or payload.get("is_synthetic")
                                          or payload.get("cgi") or payload.get("rendered")),
        "unsafe_content": _coerce_bool(payload.get("unsafe_content") or payload.get("nsfw")),
        "suggestive_content": _coerce_bool(payload.get("suggestive_content")),
        "overlay_type": _coerce_str(payload.get("overlay_type"), "none"),
        "overlay_regions": [r.to_dict() for r in normalize_overlay_regions(payload.get("overlay_regions"), 3)],
        "fatal_mismatch": _coerce_bool(payload.get("fatal_mismatch")),
        **({"domain_match": _coerce_bool(payload["domain_match"])} if "domain_match" in payload else {}),
        **({"scene_match": _coerce_bool(payload["scene_match"])} if "scene_match" in payload else {}),
        **({"event_mismatch": _coerce_bool(payload["event_mismatch"])} if "event_mismatch" in payload else {}),
        **({"location_mismatch": _coerce_bool(payload["location_mismatch"])} if "location_mismatch" in payload else {}),
        "confidence": _coerce_float(payload.get("confidence")),
        "best_usage": _coerce_str(payload.get("best_usage"), "background"),
        "recommended_duration": _coerce_str(payload.get("recommended_duration"), "medium"),
        "transition": _coerce_str(payload.get("transition"), "cut"),
        "caption_position": _coerce_str(payload.get("caption_position"), "none"),
        "music": _coerce_str(payload.get("music"), "neutral"),
        "camera_movement": _coerce_str(payload.get("camera_movement"), "none"),
        "final_score": _coerce_float(payload.get("final_score")),
        "reason": _coerce_str(payload.get("reason") or payload.get("scene_description")),
        "subject_box": payload.get("subject_box") if isinstance(payload.get("subject_box"), dict) else {},
        "safe_text_regions": payload.get("safe_text_regions") if isinstance(payload.get("safe_text_regions"), list) else [],
        "visible_overlays": payload.get("visible_overlays") if isinstance(payload.get("visible_overlays"), list) else [],
        "_schema_present": schema_present,
        "_schema_errors": schema_errors,
    }


class QwenVisionClient:
    """Thin multimodal client over OpenRouter targeting Qwen Flash."""

    def __init__(
        self,
        api_key: str,
        model: str = "",
        *,
        max_attempts: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        fallback_models: Optional[Iterable[str]] = None,
        fast_mode: bool = False,
    ):
        from ..provider_selection import account_from_handle, ACCOUNT_PREFIX, CHATGPT_MODEL
        from ..job_context import current_job
        context = current_job()
        if context.get('llm_provider') == 'chatgpt':
            api_key = ACCOUNT_PREFIX + str(context.get('chatgpt_account_id') or account_from_handle(api_key))
        if str(api_key).startswith(ACCOUNT_PREFIX) and not account_from_handle(api_key):
            raise ValueError('Choose a connected ChatGPT account in Settings.')
        from ..agent_bridge import AGENT_HANDLE, AGENT_MODEL
        if context.get('llm_provider') == 'external_agent':
            api_key = AGENT_HANDLE + str(context.get('agent_owner_id') or '')
        if not api_key:
            raise VisionClientError("OpenRouter API key is required for Qwen Vision.")
        self.provider = 'external_agent' if str(api_key).startswith(AGENT_HANDLE) else ('chatgpt' if account_from_handle(api_key) else 'openrouter')
        self.api_key = api_key
        self.model = (model or _model_name()).strip() or DEFAULT_QWEN_VISION_MODEL
        self.max_attempts = int(max_attempts or _default_attempts())
        from ..job_context import current_job
        if current_job().get('cost_profile') == 'efficient':
            self.model = str(context.get('qwen_vision_model') or context.get('vision_model') or context.get('llm_model') or self.model)
            fallback_models = []
            self.max_attempts = min(self.max_attempts, 2)
        if self.provider == 'chatgpt':
            self.model = CHATGPT_MODEL
            fallback_models = [CHATGPT_MODEL]
        if self.provider == 'external_agent':
            self.model = AGENT_MODEL
            fallback_models = [AGENT_MODEL]
        elif context.get('llm_provider') == 'openrouter':
            self.model = str(context.get('qwen_vision_model') or context.get('vision_model') or context.get('llm_model') or self.model)
            fallback_models = []
        self.timeout_seconds = float(timeout_seconds or _default_timeout())
        self.fast_mode = bool(fast_mode)
        configured = fallback_models if fallback_models is not None else FREE_VISION_FALLBACK_MODELS
        self.fallback_models = []
        seen = {self.model}
        for value in configured or ():
            candidate = str(value or "").strip()
            if candidate and candidate not in seen:
                self.fallback_models.append(candidate)
                seen.add(candidate)
        self._telemetry = threading.local()
        self.last_usage: dict = {}
        self.last_cost: float = 0.0
        self.last_latency_ms: float = 0.0
        self.last_model: str = self.model

    @property
    def last_usage(self) -> dict:
        return dict(getattr(self._telemetry, "last_usage", {}) or {})

    @last_usage.setter
    def last_usage(self, value: dict) -> None:
        self._telemetry.last_usage = dict(value or {})

    @property
    def last_cost(self) -> float:
        return float(getattr(self._telemetry, "last_cost", 0.0) or 0.0)

    @last_cost.setter
    def last_cost(self, value: float) -> None:
        self._telemetry.last_cost = max(0.0, float(value or 0.0))

    @property
    def last_latency_ms(self) -> float:
        return float(getattr(self._telemetry, "last_latency_ms", 0.0) or 0.0)

    @last_latency_ms.setter
    def last_latency_ms(self, value: float) -> None:
        self._telemetry.last_latency_ms = max(0.0, float(value or 0.0))

    # -------- public -------------------------------------------------------

    def is_available(self) -> bool:
        """Return whether the client is locally configured, without a paid ping."""
        return bool(self.api_key and self.model)

    def probe(self) -> Tuple[bool, str]:
        """One cheap LIVE ping confirming the key + model + endpoint actually work.

        Sends a tiny synthetic image through the real ``analyze_image`` path. A
        well-formed dict response means auth, model id, connectivity, and JSON
        parsing are all good. Never raises — returns ``(ok, detail)`` where detail
        carries the model/latency on success or the failure reason (e.g. an
        HTTP 404 for a bad model id, 401 for a bad key) on failure.
        """
        import tempfile
        import shutil
        tmp_dir = None
        previous_bypass = getattr(self._telemetry, "cache_bypass", False)
        self._telemetry.cache_bypass = True
        try:
            try:
                from PIL import Image
                tmp_dir = tempfile.mkdtemp(prefix="vision_probe_")
                tmp_path = os.path.join(tmp_dir, "probe.jpg")
                Image.new("RGB", (64, 64), (120, 130, 140)).save(tmp_path, "JPEG", quality=70)
            except Exception as exc:
                return False, f"probe_image_setup_failed:{type(exc).__name__}:{exc}"

            candidate_models = [self.model, *self.fallback_models]
            original_model = self.model
            last_err = "unknown"
            for cand in candidate_models:
                self.model = cand
                try:
                    started = time.perf_counter()
                    result = self.analyze_image(
                        tmp_path,
                        "Connectivity test. Briefly describe this image as a JSON object.",
                        "You are a vision model. Always reply with a single JSON object.",
                    )
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    if isinstance(result, dict):
                        return True, f"model={self.model} latency={latency_ms:.0f}ms"
                except Exception as exc:
                    last_err = f"{type(exc).__name__}:{str(exc)[:200]}"
                    if any(marker in last_err.lower() for marker in ("402", "payment", "credits", "404")):
                        continue
                    break
            self.model = original_model
            return False, last_err
        finally:
            self._telemetry.cache_bypass = previous_bypass
            if tmp_dir:
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass

    @cache_vision_response
    def analyze_image(self, image_path: str, user_prompt: str, system_prompt: str) -> dict:
        """Run Qwen Vision on a single image. Returns normalised scores dict."""
        data_uri = _encode_image_to_data_uri(
            image_path,
            max_long_edge=_vision_image_probe_edge(),
            quality=72,
        )
        if not data_uri:
            raise VisionClientError(f"Could not encode image: {image_path}")
        content = [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
        raw = self._post_json(content, system=system_prompt)
        payload = self._parse_json(raw)
        if payload is None:
            raise VisionClientError("Qwen Vision returned non-JSON image analysis")
        return _normalise_image_response(payload)

    @cache_vision_response
    def analyze_clip_frames(self, frame_paths: List[str], user_prompt: str, system_prompt: str) -> dict:
        """Run Qwen Vision on a chronological sequence of frames from one clip.

        Frames are sent in order. Total payload is capped by
        ``VIDZEO_QWEN_VISION_MAX_CLIP_PAYLOAD_BYTES``. If the cap is exceeded
        we drop the weakest frames rather than fail.
        """
        if not frame_paths:
            raise VisionClientError("No frames provided for clip analysis")
        frames = self._fit_frames_to_payload(frame_paths)
        if not frames:
            raise VisionClientError("All clip frames failed to encode")
        content: List[dict] = [{"type": "text", "text": user_prompt}]
        for uri in frames:
            content.append({"type": "image_url", "image_url": {"url": uri}})
        raw = self._post_json(content, system=system_prompt)
        payload = self._parse_json(raw)
        if payload is None:
            tail = " ".join(str(raw)[-400:].split())
            raise VisionClientError(f"Qwen Vision returned non-JSON clip analysis: ...{tail}")
        return _normalise_clip_response(payload)

    @cache_vision_response
    def review_editorial_frames(self, frame_paths: List[str], context: str) -> dict:
        """Scene-local creative review, retaining actionable presentation patches."""
        if not frame_paths or len(frame_paths) > 4:
            raise VisionClientError('Editorial review requires 1–4 chronological frames')
        content = [{"type":"text", "text": (
            "Review these actual opening/reveal/hold/exit frames using the full story context. "
            "Check readability, collisions, subject protection, empty/generic explanation, "
            "contrast, oversized text backgrounds, repetition and useful visual grammar. "
            "Context.visual_grammar and niche define this film's editorial vocabulary. Use neighboring_edits to compare actual layout choices; matching signatures are a review hint, not an automatic defect or a demand to change every shot. "
            "Check narration_opportunities for overlooked explanatory dates, values or relationships. Select a graphic only when it adds clarity and its evidence is grounded; these spans do not authorize new overlay copy. Preserve meaningful names, places, units and qualifiers. "
            "Context.rendered_text_layout contains measured text bounds in canvas percentages and observed overflow. Treat actual pixels and those measurements as evidence; manifest placement or panel_style alone cannot disprove a visible collision, clipping or oversized backing. "
            "These frames may also contain the separately timed transcript subtitle track, usually fixed near the lower edge. Distinguish those word-level captions from scene.text and approved director copy: do not use caption placement to claim that a director overlay violates its requested position. Only flag an actual collision when the caption covers scene graphics or critical evidence. "
            "Frames include progressive motion states. Judge a phrase only in the sample inside its narration cue/hold window; an empty frame before the cue or partially revealed word/number is not a defect. A tight backing behind the exact approved emphasis span is intentional, not a full-screen panel. "
            "CSS capitalization and font styling do not rewrite the approved copy when the same words and emphasis span remain; do not flag uppercase display styling as a copy violation. "
            "Flag only observed defects, not hypothetical future subtitle collisions. "
            "Ignore source rights metadata for this aesthetic review. Never infer object identity "
            "from source titles: judge actual pixels and narration. "
            "Treat visual_intent as the director's concrete visual choice and honor it when it is supported by the narration. "
            "When context.previous_revision_feedback marks remotion_graphic_fallback, do not reuse or preserve the rejected or unavailable source treatment. "
            "Switch to an eligible full-canvas Remotion explanation when the scene facts support one; use only script-grounded relationships and exact approved copy, or show no added text. "
            "A visual_intent request for words does not authorize display text when approved_copy_available is false; represent its grounded geometry without adding words. "
            "Relevant documentary footage can support narration without illustrating each invisible process. "
            "For an unnamed sport/action explainer, relevant real training or match footage is illustrative. "
            "Do not demand a named match, or treat uniforms alone as an unspoken claim. "
            "Do not require overlays when the approved copy is empty, or a map unless explicitly requested. "
            "During an intentional count-up or score-bar reveal, interpolated numeric values and sequential label entries are motion states, not factual errors. "
            "Judge the completed hold frame for numeric grounding; do not request a redesign solely because an earlier reveal sample has not reached its final value or shown every paired label yet. "
            "A stat card's visible subject label and source context are sufficient when copy is unapproved/empty; never demand generic system kickers such as 'NARRATED FIGURE'. "
            "Do not report a generic kicker that is absent from the actual rendered pixels. Judge only legible visible text, not unused manifest props. "
            "When an approved tight highlight uses a small word-level backing, do not describe it as a full-width title panel; assess its measured pixel bounds. "
            "For a named-team score comparison, judge the numbers bound to their explicit team labels; score order in narration identifies the leader, not a left/right team order. "
            "For a map whose map_shot.route_status is not_established_by_script, do not demand a route, location pin, "
            "or region label: adding one would invent geography. Judge the globe/world overview for visual quality "
            "and smooth purposeful camera motion; a neutral world view is the grounded choice. "
            "Do not demand constant movement. Never change facts, numbers, geographic data, "
            "assets, words or source marks. Return JSON {status: pass|needs_revision, "
            "confidence:0..100, repair_kind:none|layout|design|media, issues:[concise specific findings], patch:null|{component_type,visual_role,variant,text_position, "
            "text_treatment:{panel_style,text_color,accent_color,panel_background}, "
            "recipe_geometry:[{layer_index,x,y,width,height}],template_recipe:{version,id,name,layers}}}. Patch fields are optional. "
            "Only choose an existing component variant; geometry uses percentages and existing "
            "layer indices. Colors must come from visual_dna.palette (the final art direction), "
            "not an older editing_profile palette. Omit text_treatment when approved_copy_available=false. A patch must address an "
            "observed defect; do not randomly restyle healthy scenes. Return at most five issues. "
            "When the chosen media or existing component cannot express the grounded visual_intent, you may switch only to a component listed in repair_component_options and supply a complete generated recipe from recipe_contract. "
            "An empty repair_component_options list means no component switch is eligible. Do not infer eligibility from the full allowed_components library. "
            "A Diagram recipe must use exact narration phrases as its actor labels and exact narration evidence for every link; it may use illustrative shapes, masks and connected lines, never measurements, invented geography, or new source marks. "
            "Use AnimatedMap only when repair_component_options includes AnimatedMap and the scene contains an enabled, grounded map_shot; AnimatedMap must use visual_role=map_canvas. Specialist chart/comparison/timeline recipes need their required validated data. "
            "Set visual_role=primary_explanation only when the grounded graphic is the evidence and needs the full frame; otherwise keep relevant media visible as media_overlay. "
            "For observed overlap with a face, focal subject, map, or evidence, reposition approved copy using text_position in top_left, top_right, bottom_left, bottom_right, middle_left, middle_right, or center; never change its words or approved emphasis spans. "
            "A valid component change and recipe are a complete revision; return patch null for unsupported facts or when neither grounded media nor a grounded graphic can repair the issue. "
            "Set repair_kind=media only for an observed wrong subject, burned-in teaching slide, presenter "
            "instead of the requested subject, or ownership watermark; design for missing explanatory geometry; "
            "layout for readability or framing. A healthy scene has repair_kind=none. "
            "Preserve narration-aligned cue timing and a readable hold. If the issue needs new "
            "media, research or semantic design, return needs_revision with patch null. "
            "Use scene.text for approved text, and preserve its exact words. Context:\n" + context
        )}]
        for index, path in enumerate(frame_paths):
            uri = _encode_image_to_data_uri(path, max_long_edge=768, quality=80)
            if not uri:
                raise VisionClientError('Editorial frame cannot be encoded')
            content.extend([{"type":"text","text":f"Sample {index}"},
                            {"type":"image_url","image_url":{"url":uri}}])
        raw = self._post(content, system='You are a documentary art director reviewing rendered pixels. Return JSON only.',
                         json_mode=True, max_tokens=1400)
        result = self._parse_json(raw)
        if result is None:
            raise VisionClientError('Invalid editorial review JSON')
        return result

    @cache_vision_response
    def compare_images(self, image_paths: List[str], user_prompt: str) -> dict:
        """Choose among labeled image candidates in one multimodal request."""
        if not image_paths:
            raise VisionClientError("No image candidates provided")
        content: List[dict] = [{"type": "text", "text": user_prompt}]
        valid_paths: List[str] = []
        for index, path in enumerate(image_paths[:4]):
            uri = _encode_image_to_data_uri(
                path,
                max_long_edge=min(576, _vision_image_probe_edge()),
                quality=68,
            )
            if not uri:
                continue
            valid_paths.append(path)
            content.append({"type": "text", "text": f"Candidate {len(valid_paths) - 1}"})
            content.append({"type": "image_url", "image_url": {"url": uri}})
        if not valid_paths:
            raise VisionClientError("All image candidates failed to encode")
        raw = self._post_json(
            content,
            system=(
                "You select documentary visuals. Return JSON only. Reject exact-entity, "
                "event, action, era, ownership-watermark, or unsafe mismatches."
            ),
        )
        payload = self._parse_json(raw)
        if payload is None:
            raise VisionClientError("Qwen Vision returned non-JSON comparison")
        required = ("selected_index", "acceptable", "reason", "confidence")
        schema_present = [key for key in required if key in payload]
        schema_errors = ["missing_" + key for key in required if key not in payload]
        raw_index = payload.get("selected_index")
        if not isinstance(raw_index, int) or isinstance(raw_index, bool) or not 0 <= raw_index < len(valid_paths):
            schema_errors.append("invalid_selected_index")
        if not isinstance(payload.get("acceptable"), bool):
            schema_errors.append("invalid_acceptable")
        if not isinstance(payload.get("reason"), str) or not payload.get("reason", "").strip():
            schema_errors.append("invalid_reason")
        confidence = payload.get("confidence")
        if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                or not math.isfinite(confidence) or not 0 <= confidence <= 100):
            schema_errors.append("invalid_confidence")
        try:
            selected = int(payload.get("selected_index", 0))
        except Exception:
            selected = 0
        selected = max(0, min(len(valid_paths) - 1, selected))
        rows = payload.get("candidates")
        if not isinstance(rows, list):
            rows = []
        accepted = payload.get("acceptable")
        return {
            "selected_index": selected,
            "confidence": _coerce_float(payload.get("confidence")),
            "reason": _coerce_str(payload.get("reason")),
            "candidates": rows[:len(valid_paths)],
            "acceptable": False if schema_errors or accepted is False else not _coerce_bool(payload.get("all_rejected")),
            "overlay_regions": [r.to_dict() for r in normalize_overlay_regions(payload.get("overlay_regions"), len(valid_paths))],
            "_schema_present": schema_present,
            "_schema_errors": schema_errors,
            "_candidate_count": len(valid_paths),
        }

    # -------- internals ----------------------------------------------------

    def _post_json(
        self,
        content: List[dict],
        *,
        system: str = "",
        budget: int = 1400,
        retry_budget: int = 2400,
    ) -> str:
        """Post with JSON mode and a lean token budget.

        Flash models can interleave reasoning prose before emitting the JSON
        object. A large max_tokens makes the
        gate spend tens of seconds per answer; with compact/degraded inputs
        the full verdict fits in ~1200 tokens, so keep the first shot lean
        and only retry once with more headroom when no JSON is extracted.
        """
        raw = self._post(
            content,
            system=system,
            json_mode=True,
            max_tokens=min(int(budget), 900) if self.fast_mode else budget,
        )
        if _extract_json_text(raw) is None:
            if self.fast_mode:
                # Mixed Media uses a bounded advisory gate. Give a malformed
                # JSON response one cheap plain-text repair shot, then let the
                # normal metadata/fallback policy decide; do not spend a third
                # network request on a single finalist.
                raw = self._post(
                    content,
                    system=system,
                    json_mode=False,
                    max_tokens=min(int(retry_budget), 1000),
                )
            else:
                raw = self._post(
                    content, system=system, json_mode=True, max_tokens=retry_budget
                )
        if not self.fast_mode and _extract_json_text(raw) is None:
            # Some providers route response_format/json_object to a strict
            # variant that truncates reasoning models. Plain text mode lets us
            # extract the JSON from the raw response.
            raw = self._post(
                content, system=system, json_mode=False, max_tokens=retry_budget
            )
        return raw

    def _fit_frames_to_payload(self, frame_paths: List[str]) -> List[str]:
        cap = _max_frame_tokens_hint()
        encoded: List[Tuple[str, int]] = []
        for path in frame_paths:
            # Clip frames are only a verification sample: degrade them hard so
            # the payload stays ~100KB total and the Flash tier answers fast.
            uri = _encode_image_to_data_uri(
                path,
                max_long_edge=_vision_frame_probe_edge(),
                quality=58,
            )
            if not uri:
                continue
            encoded.append((uri, len(uri)))
        # Keep chronological order but drop the largest payloads first when
        # we overflow the cap. Prefer keeping the start, middle and end.
        while encoded and sum(size for _, size in encoded) > cap:
            biggest = max(range(len(encoded)), key=lambda i: encoded[i][1])
            encoded.pop(biggest)
        return [uri for uri, _ in encoded]

    def _post_with_hard_timeout(self, headers: dict, payload: dict):
        from ..bounded_http import provider_post
        return provider_post(requests.post, OPENROUTER_API_URL, headers=headers,
                             json=payload, timeout=self.timeout_seconds,
                             usage_kind='vision', operation='visual_review')

    def _post(
        self,
        content: List[dict],
        *,
        system: str = "",
        json_mode: bool = False,
        max_tokens: int = 900,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        messages: List[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": int(max_tokens),
        }
        # Keep Qwen as the primary visual verifier, while allowing OpenRouter
        # to fail over to configured multimodal models on model/provider
        # outages. OpenRouter accepts at most three entries in its ``models``
        # fallback array, so long configured chains are sent as overlapping
        # groups across bounded retries instead of producing a deterministic
        # HTTP 400 before any vision request can run.
        model_chain = [self.model]
        for fallback_model in self.fallback_models:
            if fallback_model not in model_chain:
                model_chain.append(fallback_model)
        from ..job_context import current_job
        if current_job().get('cost_profile') == 'efficient':
            model_chain = [self.model]
            payload['model'] = model_chain[0]
            payload['reasoning'] = {'enabled': False}
        model_groups = [model_chain[:3]] if model_chain else [[]]
        if len(model_chain) > 3:
            # Overlap the last model in the first group so a transient failure
            # does not skip a still-eligible fallback when the second group is
            # attempted.
            model_groups.append(model_chain[2:5])
        if len(model_chain) > 5:
            model_groups.append(model_chain[4:7])
        if len(model_chain) > 1:
            payload["models"] = model_groups[0]
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # Qwen 3.x flash models default to thinking mode: the model emits a long
        # chain-of-thought ("reasoning") blob and leaves only "{}" / empty in
        # "content", which previously made every clip verdict burn 150-270s and
        # frequently ended as non-parseable output. Disable thinking so the JSON
        # lands directly in content: fast, cheap, reliable. Opt back in with
        # VIDZEO_QWEN_VISION_THINKING=1 if a reasoning-model verdict is needed.
        _thinking = str(os.environ.get("VIDZEO_QWEN_VISION_THINKING", "0")).strip().lower()
        if _thinking not in {"1", "true", "yes", "on"}:
            payload["reasoning"] = {"enabled": False}

        # The external agent (Muse's own vision) answers through the local
        # inbox; a missed deadline raises AgentBridgeError. Import it lazily
        # (same pattern as the AGENT_HANDLE import below) so a missing inbox
        # never breaks module import.
        try:
            from ..agent_bridge import AgentBridgeError as _AgentBridgeError
        except Exception:
            # Never raised: keeps the `except _AgentBridgeError` clause below
            # valid (an `except None` would raise TypeError) while matching
            # nothing when the bridge is unavailable.
            class _AgentBridgeError(Exception):
                pass

        last_error = ""
        structured_fallback_used = False
        attempts = min(self.max_attempts, 2) if current_job().get('cost_profile') == 'efficient' else self.max_attempts
        request_budget = attempts + (1 if json_mode else 0)
        verdict_deadline = time.monotonic() + max(0.1, self.timeout_seconds * max(2, request_budget) + 10.0)
        for attempt in range(request_budget):
            if time.monotonic() >= verdict_deadline:
                break
            if attempt >= attempts and not structured_fallback_used:
                break
            try:
                if len(model_groups) > 1:
                    group = model_groups[min(attempt, len(model_groups) - 1)]
                    payload["model"] = group[0]
                    payload["models"] = group
                started = time.perf_counter()
                from ..job_context import phase_scope
                with phase_scope(max(0.001, verdict_deadline - time.monotonic())):
                    response = self._post_with_hard_timeout(headers, payload)
                self.last_latency_ms = (time.perf_counter() - started) * 1000.0
                if (
                    response.status_code == 400
                    and json_mode
                    and "response_format" in payload
                    and not structured_fallback_used
                ):
                    # Provider routing differs. Retry once without structured
                    # output only for an explicit incompatibility response.
                    structured_fallback_used = True
                    payload.pop("response_format", None)
                    with phase_scope(max(0.001, verdict_deadline - time.monotonic())):
                        response = self._post_with_hard_timeout(headers, payload)
                if response.status_code == 401:
                    raise VisionClientError("Invalid OpenRouter API key for vision client. Add or replace it: https://openrouter.ai/keys")
                if response.status_code in {402, 404}:
                    if response.status_code == 404:
                        raise VisionClientError(
                            f"Vision model not available on OpenRouter: {self.model}"
                        )
                    elif response.status_code == 402:
                        raise VisionClientError("OpenRouter credits or the key spending limit are exhausted for Qwen Flash. Add credits: https://openrouter.ai/credits")
                if response.status_code in {429, 503}:
                    delay = _retry_after_seconds(response, (2 ** attempt) * 2)
                    time.sleep(min(delay, max(0.0, verdict_deadline - time.monotonic())))
                    last_error = "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    continue
                response.raise_for_status()
                data = response.json()
                resolved_model = str(data.get("model") or "").strip()
                if resolved_model:
                    self.last_model = resolved_model
                    if resolved_model != self.model and _verbose():
                        print(f"   [vision] OpenRouter fallback resolved {self.model} -> {resolved_model}")
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                self.last_usage = dict(usage or {})
                explicit_cost = (
                    usage.get("cost")
                    or usage.get("total_cost")
                    or data.get("cost")
                    or 0.0
                )
                try:
                    self.last_cost = max(0.0, float(explicit_cost or 0.0))
                except Exception:
                    self.last_cost = 0.0
                if not self.last_cost and self.model in {DEFAULT_QWEN_VISION_MODEL, "qwen/qwen3.5-flash-02-23", "nvidia/nemotron-3.5-super-vl:free"}:
                    try:
                        prompt_tokens = float(usage.get("prompt_tokens") or 0.0)
                        completion_tokens = float(usage.get("completion_tokens") or 0.0)
                        prompt_rate = 0.065
                        completion_rate = 0.26
                        self.last_cost = (
                            prompt_tokens * prompt_rate / 1_000_000.0
                            + completion_tokens * completion_rate / 1_000_000.0
                        )
                    except Exception:
                        self.last_cost = 0.0
                if "error" in data:
                    last_error = str(data["error"])[:300]
                    continue
                choices = data.get("choices") or []
                if not choices:
                    last_error = "no choices returned"
                    continue
                content_out = (choices[0].get("message") or {}).get("content") or ""
                if isinstance(content_out, list):
                    content_out = "".join(
                        str(part.get("text") or part.get("content") or "")
                        if isinstance(part, dict) else str(part or "")
                        for part in content_out
                    )
                if not str(content_out).strip():
                    if json_mode and not structured_fallback_used:
                        structured_fallback_used = True
                        payload.pop("response_format", None)
                        last_error = "empty content; retrying without response_format"
                        continue
                    last_error = "empty content"
                    continue
                return str(content_out)
            except VisionClientError:
                raise
            except _AgentBridgeError as exc:
                # The external agent did not answer this inbox request in
                # time. Retry once with a fresh inbox request instead of
                # failing the candidate closed: the agent may simply have
                # been busy answering another request.
                last_error = str(exc)
                if attempt + 1 < attempts:
                    time.sleep(min(2.0, max(0.0, verdict_deadline - time.monotonic())))
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt + 1 < self.max_attempts:
                    time.sleep(min((2 ** attempt) * 2, max(0.0, verdict_deadline - time.monotonic())))
        raise VisionClientError(last_error or "Qwen Vision request failed")

    @staticmethod
    def _parse_json(raw: str) -> Optional[dict]:
        extracted = _extract_json_text(raw)
        if not extracted:
            return None
        try:
            parsed = json.loads(extracted)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None


if __name__ == "__main__":
    # Standalone smoke test:
    #   python server/services/visual_intelligence/vision_client.py [model_id]
    # Resolves the OpenRouter key from the environment. If your key lives only
    # in the managed app config, export it first, e.g.:
    #   OPENROUTER_API_KEY=sk-or-... python .../vision_client.py
    import sys

    _key = (
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
        or ""
    ).strip()
    _model = sys.argv[1].strip() if len(sys.argv) > 1 else _model_name()
    if not _key:
        print(
            "[vision] no OpenRouter key found in env — set OPENROUTER_API_KEY "
            "to run this standalone probe."
        )
        raise SystemExit(2)
    print(f"[vision] probing model={_model} ...")
    _client = QwenVisionClient(api_key=_key, model=_model, max_attempts=_default_attempts(), timeout_seconds=_default_timeout())
    _ok, _detail = _client.probe()
    print(f"[vision] probe {'OK' if _ok else 'FAILED'} - {_detail}")
    raise SystemExit(0 if _ok else 1)
