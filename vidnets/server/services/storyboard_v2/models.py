"""Versioned, source-neutral contracts for the Vidzeo V2 pipeline.

The classes in this module deliberately have no dependency on Flask, the queue
manager, or any media provider.  They are the boundary between script
understanding, audio timing, retrieval, editorial planning, and rendering.

All offsets are half-open: ``start <= offset < end``.  Character offsets refer
to ``ScriptPlanV1.source_script`` and token/word indexes follow the same
half-open convention.  Stable identifiers are content-derived and therefore do
not change when timing or media decisions are added later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "2.0"
PARSER_VERSION = "2.0.0"

SUPPORTED_FORMATS = frozenset(
    {
        "auto",
        "narrative",
        "listicle",
        "how_to",
        "comparison",
        "timeline",
        "q_and_a",
        "dialogue",
        "mixed_multi_subject",
    }
)


class ContractValidationError(ValueError):
    """Raised when a V2 contract violates an invariant."""


class FrozenMapping(Mapping[str, Any]):
    """Small recursively immutable mapping used inside frozen contracts."""

    __slots__ = ("_data",)

    def __init__(self, value: Mapping[str, Any] | None = None):
        self._data = {str(key): freeze_json(item) for key, item in (value or {}).items()}

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"FrozenMapping({self._data!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and dict(self.items()) == dict(other.items())

    def __deepcopy__(self, _memo):
        return self


def freeze_json(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(freeze_json(item) for item in value)
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_hash(value: Any) -> str:
    """Return a deterministic SHA-256 digest for JSON-compatible content."""

    payload = json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    digest = stable_hash(list(parts))[:20]
    return f"{prefix}_{digest}"


def _json_ready(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    return value


def _tuple_strings(value: Iterable[Any] | None) -> Tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item).strip())


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled", ""}:
        return False
    return default


@dataclass(frozen=True)
class GenerationRequestV2:
    """Normalized public generation request with safe legacy aliases.

    ``title`` and ``name`` historically drifted into one another.  V2 keeps the
    semantic content title and the filesystem/display output name separate.
    """

    script: str
    content_title: str = ""
    output_name: str = "Untitled Video"
    format_hint: str = "auto"
    language: str = "auto"
    scene_mode: str = "adaptive"
    media_mode: str = "automatic"
    youtube_rights_mode: str = "all_public"
    allow_relevant_stock: bool = True
    allow_stock_last_resort: bool = True
    qwen_model: str = "qwen/qwen3.5-flash-02-23"
    qwen_fallback_model: str = "qwen/qwen3.5-flash-02-23"
    qwen_concurrency: int = 4
    pipeline_v2_mode: str = "on"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.script, str) or not self.script.strip():
            raise ContractValidationError("GenerationRequestV2.script must not be empty")
        if self.format_hint not in SUPPORTED_FORMATS:
            raise ContractValidationError(f"Unsupported format_hint: {self.format_hint}")
        if self.qwen_concurrency < 1:
            raise ContractValidationError("qwen_concurrency must be at least 1")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "GenerationRequestV2":
        """Normalize a new or legacy queue/API request without conflating names."""

        content_title = str(
            raw.get("_topic_anchor")
            or raw.get("content_title")
            or raw.get("title")
            or ""
        ).strip()
        output_name = str(raw.get("output_name") or raw.get("name") or "").strip()
        if not output_name:
            output_name = content_title or "Untitled Video"
        format_hint = str(raw.get("format_hint") or "auto").strip().lower()
        scene_mode = str(raw.get("scene_mode") or "adaptive").strip()
        if scene_mode.lower().startswith("auto"):
            scene_mode = "adaptive"
        return cls(
            script=str(raw.get("script") or ""),
            content_title=content_title,
            output_name=output_name,
            format_hint=format_hint,
            language=str(raw.get("language") or "auto").strip() or "auto",
            scene_mode=scene_mode,
            media_mode=str(raw.get("media_mode") or raw.get("media_source") or "automatic").strip(),
            youtube_rights_mode=str(raw.get("youtube_rights_mode") or "all_public").strip(),
            allow_relevant_stock=_coerce_bool(raw.get("allow_relevant_stock"), default=True),
            allow_stock_last_resort=_coerce_bool(raw.get("allow_stock_last_resort"), default=True),
            qwen_model=str(
                raw.get("qwen_model")
                or raw.get("qwen_vision_model")
                or "qwen/qwen3.5-flash-02-23"
            ).strip(),
            qwen_fallback_model=str(
                raw.get("qwen_fallback_model")
                or raw.get("qwen_vision_fallback_model")
                or "qwen/qwen3.5-flash-02-23"
            ).strip(),
            qwen_concurrency=int(raw.get("qwen_concurrency") or 4),
            pipeline_v2_mode=str(raw.get("pipeline_v2_mode") or "on").strip(),
        )

    def to_mapping(self, *, include_legacy_aliases: bool = True) -> Dict[str, Any]:
        row = asdict(self)
        if include_legacy_aliases:
            row["title"] = self.content_title
            row["name"] = self.output_name
            row["media_source"] = self.media_mode
            row["qwen_vision_model"] = self.qwen_model
            row["qwen_vision_fallback_model"] = self.qwen_fallback_model
        return row

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GenerationRequestV2":
        return cls.from_mapping(raw)

    def to_dict(self) -> Dict[str, Any]:
        return self.to_mapping(include_legacy_aliases=False)


@dataclass(frozen=True)
class ContainerV1:
    container_id: str
    kind: str
    ordinal: Optional[int]
    canonical_subject: str
    char_start: int
    char_end: int
    text: str
    parent_container_id: Optional[str] = None
    marker_start: Optional[int] = None
    marker_end: Optional[int] = None
    explicit_marker: bool = False
    source_line_start: int = 1
    source_line_end: int = 1
    is_leaf: bool = True
    depth: int = 1

    def __post_init__(self) -> None:
        if not self.container_id:
            raise ContractValidationError("ContainerV1.container_id is required")
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ContractValidationError("ContainerV1 character span is invalid")
        if self.marker_start is not None and not self.char_start <= self.marker_start < self.char_end:
            raise ContractValidationError("ContainerV1 marker_start is outside its span")
        if self.marker_end is not None and not self.char_start < self.marker_end <= self.char_end:
            raise ContractValidationError("ContainerV1 marker_end is outside its span")
        if self.depth < 0:
            raise ContractValidationError("ContainerV1.depth must be non-negative")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ContainerV1":
        return cls(**dict(raw))


@dataclass(frozen=True)
class SceneContractV1:
    scene_id: str
    order: int
    container_id: str
    text: str
    char_start: int
    char_end: int
    token_start: int
    token_end: int
    word_start_index: Optional[int] = None
    word_end_index: Optional[int] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    timing_quality: str = "pending"
    alignment_confidence: float = 0.0
    visual_intent: str = "documentary_context"
    positive_subjects: Tuple[str, ...] = field(default_factory=tuple)
    forbidden_subjects: Tuple[str, ...] = field(default_factory=tuple)
    source_policy: Mapping[str, Any] = field(default_factory=dict)
    reuse_policy: Mapping[str, Any] = field(default_factory=dict)
    # Immutable identity-first media contract.  Providers may add retrieval
    # evidence, but they may not replace this requirement with a broad topic
    # or container label.
    visual_requirement: Mapping[str, Any] = field(default_factory=dict)
    contract_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "positive_subjects", _tuple_strings(self.positive_subjects))
        object.__setattr__(self, "forbidden_subjects", _tuple_strings(self.forbidden_subjects))
        object.__setattr__(self, "source_policy", freeze_json(self.source_policy))
        object.__setattr__(self, "reuse_policy", freeze_json(self.reuse_policy))
        object.__setattr__(self, "visual_requirement", freeze_json(self.visual_requirement))
        if not self.scene_id or not self.container_id:
            raise ContractValidationError("SceneContractV1 IDs are required")
        if self.order < 0 or self.char_start < 0 or self.char_end <= self.char_start:
            raise ContractValidationError("SceneContractV1 span/order is invalid")
        if self.token_start < 0 or self.token_end < self.token_start:
            raise ContractValidationError("SceneContractV1 token span is invalid")
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ContractValidationError("Scene timing must provide both start and end")
        if self.start_seconds is not None and self.end_seconds is not None:
            if self.start_seconds < 0 or self.end_seconds <= self.start_seconds:
                raise ContractValidationError("SceneContractV1 timing span is invalid")
        if not 0.0 <= float(self.alignment_confidence) <= 1.0:
            raise ContractValidationError("alignment_confidence must be between 0 and 1")

    @property
    def duration(self) -> Optional[float]:
        if self.start_seconds is None or self.end_seconds is None:
            return None
        return self.end_seconds - self.start_seconds

    def hash_payload(self) -> Dict[str, Any]:
        """Fields that determine whether a cached decision remains compatible."""

        return {
            "scene_id": self.scene_id,
            "container_id": self.container_id,
            "char_span": [self.char_start, self.char_end],
            "token_span": [self.token_start, self.token_end],
            "word_span": [self.word_start_index, self.word_end_index],
            "timing": [self.start_seconds, self.end_seconds, self.timing_quality],
            "visual_intent": self.visual_intent,
            "positive_subjects": list(self.positive_subjects),
            "forbidden_subjects": list(self.forbidden_subjects),
            "source_policy": self.source_policy,
            "reuse_policy": self.reuse_policy,
            "visual_requirement": self.visual_requirement,
        }

    def with_computed_hash(self) -> "SceneContractV1":
        return replace(self, contract_hash=stable_hash(self.hash_payload()))

    def with_timing(
        self,
        *,
        word_start_index: Optional[int],
        word_end_index: Optional[int],
        start_seconds: float,
        end_seconds: float,
        timing_quality: str,
        alignment_confidence: float,
    ) -> "SceneContractV1":
        timed = replace(
            self,
            word_start_index=word_start_index,
            word_end_index=word_end_index,
            start_seconds=float(start_seconds),
            end_seconds=float(end_seconds),
            timing_quality=str(timing_quality),
            alignment_confidence=max(0.0, min(1.0, float(alignment_confidence))),
            contract_hash="",
        )
        return timed.with_computed_hash()

    def to_dict(self) -> Dict[str, Any]:
        return _json_ready(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SceneContractV1":
        values = dict(raw)
        values["positive_subjects"] = _tuple_strings(values.get("positive_subjects"))
        values["forbidden_subjects"] = _tuple_strings(values.get("forbidden_subjects"))
        return cls(**values)


@dataclass(frozen=True)
class WordTimingV1:
    index: int
    text: str
    start_seconds: float
    end_seconds: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.index < 0 or self.start_seconds < 0 or self.end_seconds < self.start_seconds:
            raise ContractValidationError("WordTimingV1 contains an invalid index or timing")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, index: int, raw: Mapping[str, Any]) -> "WordTimingV1":
        return cls(
            index=int(raw.get("index", index)),
            text=str(raw.get("text") or raw.get("word") or ""),
            start_seconds=float(raw.get("start_seconds", raw.get("start", 0.0)) or 0.0),
            end_seconds=float(raw.get("end_seconds", raw.get("end", 0.0)) or 0.0),
            confidence=float(raw.get("confidence", raw.get("probability", 1.0)) or 0.0),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WordTimingV1":
        return cls.from_mapping(int(raw.get("index", 0)), raw)


@dataclass(frozen=True)
class MediaCandidateV1:
    candidate_id: str
    scene_id: str
    container_id: str
    provider: str
    source: str
    source_id: str = ""
    origin_url: str = ""
    local_path: str = ""
    media_type: str = "image"
    query: str = ""
    fingerprint: str = ""
    rights_status: str = "unknown"
    license: str = ""
    attribution: str = ""
    video_start_seconds: Optional[float] = None
    video_end_seconds: Optional[float] = None
    deterministic_scores: Mapping[str, float] = field(default_factory=dict)
    qwen_result: Mapping[str, Any] = field(default_factory=dict)
    gate_outcomes: Mapping[str, Any] = field(default_factory=dict)
    status: str = "discovered"
    requirement_hash: str = ""
    candidate_evidence: Mapping[str, Any] = field(default_factory=dict)
    verification_status: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "deterministic_scores", freeze_json(self.deterministic_scores))
        object.__setattr__(self, "qwen_result", freeze_json(self.qwen_result))
        object.__setattr__(self, "gate_outcomes", freeze_json(self.gate_outcomes))
        object.__setattr__(self, "candidate_evidence", freeze_json(self.candidate_evidence))
        if not self.candidate_id or not self.scene_id or not self.container_id:
            raise ContractValidationError("MediaCandidateV1 IDs are required")
        if (self.video_start_seconds is None) != (self.video_end_seconds is None):
            raise ContractValidationError("Media candidate video range must be complete")
        if self.video_start_seconds is not None and self.video_end_seconds is not None:
            if self.video_start_seconds < 0 or self.video_end_seconds <= self.video_start_seconds:
                raise ContractValidationError("Media candidate video range is invalid")

    def to_dict(self) -> Dict[str, Any]:
        return _json_ready(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MediaCandidateV1":
        return cls(**dict(raw))


@dataclass(frozen=True)
class EditorialCueV2:
    """Grounded editorial cue shell shared by planners and renderers."""

    cue_id: str
    scene_id: str
    container_id: str
    cue_type: str
    text: str
    char_start: int
    char_end: int
    token_start: int
    token_end: int
    word_start_index: Optional[int]
    word_end_index: Optional[int]
    scene_local_start_seconds: float
    scene_local_end_seconds: float
    absolute_start_seconds: float
    absolute_end_seconds: float
    timing_quality: str
    template: str = "generic_highlight"
    props: Mapping[str, Any] = field(default_factory=dict)
    placement: Mapping[str, Any] = field(default_factory=dict)
    audit_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "props", freeze_json(self.props))
        object.__setattr__(self, "placement", freeze_json(self.placement))
        if not self.cue_id or not self.scene_id or not self.container_id:
            raise ContractValidationError("EditorialCueV2 IDs are required")
        if self.char_start < 0 or self.char_end <= self.char_start:
            raise ContractValidationError("EditorialCueV2 source span is invalid")
        if self.token_start < 0 or self.token_end <= self.token_start:
            raise ContractValidationError("EditorialCueV2 token span is invalid")
        if self.scene_local_start_seconds < 0:
            raise ContractValidationError("EditorialCueV2 local start must be non-negative")
        if self.scene_local_end_seconds <= self.scene_local_start_seconds:
            raise ContractValidationError("EditorialCueV2 local timing is invalid")
        if self.absolute_end_seconds <= self.absolute_start_seconds:
            raise ContractValidationError("EditorialCueV2 absolute timing is invalid")

    def to_dict(self) -> Dict[str, Any]:
        return _json_ready(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EditorialCueV2":
        return cls(**dict(raw))


@dataclass(frozen=True)
class AttemptLedgerEntryV1:
    sequence: int
    timestamp: str
    event_type: str
    job_id: str
    run_id: str
    scene_id: Optional[str] = None
    container_id: Optional[str] = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", freeze_json(self.payload))

    def to_dict(self) -> Dict[str, Any]:
        return _json_ready(asdict(self))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AttemptLedgerEntryV1":
        return cls(**dict(raw))


@dataclass(frozen=True)
class ScriptPlanV1:
    script_hash: str
    source_script: str
    normalized_script: str
    normalization_to_original: Tuple[int, ...]
    detected_format: str
    format_confidence: float
    language: str
    containers: Tuple[ContainerV1, ...]
    scenes: Tuple[SceneContractV1, ...]
    parser_version: str = PARSER_VERSION
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalization_to_original", tuple(self.normalization_to_original))
        object.__setattr__(self, "containers", tuple(self.containers))
        object.__setattr__(self, "scenes", tuple(self.scenes))
        self.validate()

    def validate(self) -> None:
        if self.detected_format not in SUPPORTED_FORMATS - {"auto"}:
            raise ContractValidationError(f"Invalid detected format: {self.detected_format}")
        if not 0.0 <= float(self.format_confidence) <= 1.0:
            raise ContractValidationError("format_confidence must be between 0 and 1")
        if self.script_hash != hashlib.sha256(self.source_script.encode("utf-8")).hexdigest():
            raise ContractValidationError("script_hash does not match source_script")
        if len(self.normalization_to_original) != len(self.normalized_script) + 1:
            raise ContractValidationError("normalization map must contain every boundary")
        if self.normalization_to_original:
            if self.normalization_to_original[0] != 0:
                raise ContractValidationError("normalization map must begin at source offset zero")
            if self.normalization_to_original[-1] != len(self.source_script):
                raise ContractValidationError("normalization map must end at source length")
            if any(
                later < earlier
                for earlier, later in zip(
                    self.normalization_to_original,
                    self.normalization_to_original[1:],
                )
            ):
                raise ContractValidationError("normalization map must be monotonic")
        self._validate_container_coverage()
        self._validate_scene_coverage()

    def _validate_container_coverage(self) -> None:
        if not self.containers:
            raise ContractValidationError("ScriptPlanV1 requires at least one container")
        seen = set()
        for container in self.containers:
            if container.container_id in seen:
                raise ContractValidationError("Container IDs must be unique")
            seen.add(container.container_id)
            if container.text != self.source_script[container.char_start : container.char_end]:
                raise ContractValidationError("Container text does not match its exact source span")
        by_id = {container.container_id: container for container in self.containers}
        for container in self.containers:
            if container.parent_container_id:
                parent = by_id.get(container.parent_container_id)
                if parent is None:
                    raise ContractValidationError("Container references an unknown parent")
                if not (
                    parent.char_start <= container.char_start
                    and container.char_end <= parent.char_end
                    and parent.depth < container.depth
                ):
                    raise ContractValidationError("Container is outside its parent span/depth")
        cursor = 0
        leaves = sorted(
            (container for container in self.containers if container.is_leaf),
            key=lambda item: (item.char_start, item.char_end),
        )
        if not leaves:
            raise ContractValidationError("ScriptPlanV1 requires at least one leaf container")
        for container in leaves:
            if container.char_start != cursor:
                raise ContractValidationError(
                    "Leaf containers must cover the script without gaps or overlap"
                )
            cursor = container.char_end
        if cursor != len(self.source_script):
            raise ContractValidationError("Leaf containers do not cover the complete source script")

    def _validate_scene_coverage(self) -> None:
        if not self.scenes:
            raise ContractValidationError("ScriptPlanV1 requires at least one scene")
        container_by_id = {item.container_id: item for item in self.containers if item.is_leaf}
        cursor = 0
        seen = set()
        for order, scene in enumerate(self.scenes):
            if scene.scene_id in seen:
                raise ContractValidationError("Scene IDs must be unique")
            seen.add(scene.scene_id)
            if scene.order != order:
                raise ContractValidationError("Scene order must be contiguous and deterministic")
            if scene.char_start != cursor:
                raise ContractValidationError("Scenes must cover the script without gaps or overlap")
            if scene.text != self.source_script[scene.char_start : scene.char_end]:
                raise ContractValidationError("Scene text does not match its exact source span")
            container = container_by_id.get(scene.container_id)
            if container is None:
                raise ContractValidationError("Scene references an unknown container")
            if not (
                container.char_start <= scene.char_start < scene.char_end <= container.char_end
            ):
                raise ContractValidationError("A scene crosses its container boundary")
            cursor = scene.char_end
        if cursor != len(self.source_script):
            raise ContractValidationError("Scenes do not cover the complete source script")

    def with_scenes(self, scenes: Sequence[SceneContractV1]) -> "ScriptPlanV1":
        return replace(self, scenes=tuple(scenes))

    @property
    def leaf_containers(self) -> Tuple[ContainerV1, ...]:
        return tuple(
            sorted(
                (item for item in self.containers if item.is_leaf),
                key=lambda item: (item.char_start, item.char_end),
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "script_hash": self.script_hash,
            "source_script": self.source_script,
            "normalized_script": self.normalized_script,
            "normalization_to_original": list(self.normalization_to_original),
            "detected_format": self.detected_format,
            "format_confidence": self.format_confidence,
            "language": self.language,
            "containers": [item.to_dict() for item in self.containers],
            "scenes": [item.to_dict() for item in self.scenes],
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScriptPlanV1":
        return cls(
            script_hash=str(raw["script_hash"]),
            source_script=str(raw["source_script"]),
            normalized_script=str(raw["normalized_script"]),
            normalization_to_original=tuple(int(item) for item in raw["normalization_to_original"]),
            detected_format=str(raw["detected_format"]),
            format_confidence=float(raw["format_confidence"]),
            language=str(raw.get("language") or "auto"),
            containers=tuple(ContainerV1.from_dict(item) for item in raw.get("containers", [])),
            scenes=tuple(SceneContractV1.from_dict(item) for item in raw.get("scenes", [])),
            parser_version=str(raw.get("parser_version") or PARSER_VERSION),
            schema_version=str(raw.get("schema_version") or SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class StoryboardV2:
    job_id: str
    run_id: str
    request: GenerationRequestV2
    script_plan: ScriptPlanV1
    word_timings: Tuple[WordTimingV1, ...] = field(default_factory=tuple)
    media_candidates: Tuple[MediaCandidateV1, ...] = field(default_factory=tuple)
    editorial_cues: Tuple[EditorialCueV2, ...] = field(default_factory=tuple)
    scene_states: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    attempt_ledger: Tuple[AttemptLedgerEntryV1, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "word_timings", tuple(self.word_timings))
        object.__setattr__(self, "media_candidates", tuple(self.media_candidates))
        object.__setattr__(self, "editorial_cues", tuple(self.editorial_cues))
        object.__setattr__(self, "scene_states", freeze_json(self.scene_states))
        object.__setattr__(self, "attempt_ledger", tuple(self.attempt_ledger))
        if not self.job_id or not self.run_id:
            raise ContractValidationError("StoryboardV2 job_id and run_id are required")
        scene_ids = {item.scene_id for item in self.script_plan.scenes}
        for candidate in self.media_candidates:
            if candidate.scene_id not in scene_ids:
                raise ContractValidationError("Media candidate references an unknown scene")
            scene = next(item for item in self.script_plan.scenes if item.scene_id == candidate.scene_id)
            if candidate.container_id != scene.container_id:
                raise ContractValidationError("Media candidate crosses a scene container")
        for cue in self.editorial_cues:
            if cue.scene_id not in scene_ids:
                raise ContractValidationError("Editorial cue references an unknown scene")
            scene = next(item for item in self.script_plan.scenes if item.scene_id == cue.scene_id)
            if cue.container_id != scene.container_id:
                raise ContractValidationError("Editorial cue crosses a scene container")

    @classmethod
    def new(
        cls,
        *,
        job_id: str,
        run_id: str,
        request: GenerationRequestV2,
        script_plan: ScriptPlanV1,
    ) -> "StoryboardV2":
        now = utc_now_iso()
        return cls(
            job_id=job_id,
            run_id=run_id,
            request=request,
            script_plan=script_plan,
            created_at=now,
            updated_at=now,
        )

    def with_script_plan(
        self,
        script_plan: ScriptPlanV1,
        *,
        word_timings: Sequence[WordTimingV1] | None = None,
    ) -> "StoryboardV2":
        return replace(
            self,
            script_plan=script_plan,
            word_timings=tuple(word_timings if word_timings is not None else self.word_timings),
            updated_at=utc_now_iso(),
        )

    def with_attempt(self, entry: AttemptLedgerEntryV1) -> "StoryboardV2":
        if entry.job_id != self.job_id or entry.run_id != self.run_id:
            raise ContractValidationError("Attempt ledger entry belongs to another job/run")
        expected = len(self.attempt_ledger) + 1
        if entry.sequence != expected:
            raise ContractValidationError(
                f"Attempt sequence must be {expected}, received {entry.sequence}"
            )
        return replace(
            self,
            attempt_ledger=self.attempt_ledger + (entry,),
            updated_at=utc_now_iso(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "request": self.request.to_dict(),
            "script_plan": self.script_plan.to_dict(),
            "word_timings": [item.to_dict() for item in self.word_timings],
            "media_candidates": [item.to_dict() for item in self.media_candidates],
            "editorial_cues": [item.to_dict() for item in self.editorial_cues],
            "scene_states": _json_ready(self.scene_states),
            "attempt_ledger": [item.to_dict() for item in self.attempt_ledger],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StoryboardV2":
        return cls(
            job_id=str(raw["job_id"]),
            run_id=str(raw["run_id"]),
            request=GenerationRequestV2.from_dict(raw["request"]),
            script_plan=ScriptPlanV1.from_dict(raw["script_plan"]),
            word_timings=tuple(WordTimingV1.from_dict(item) for item in raw.get("word_timings", [])),
            media_candidates=tuple(
                MediaCandidateV1.from_dict(item) for item in raw.get("media_candidates", [])
            ),
            editorial_cues=tuple(
                EditorialCueV2.from_dict(item) for item in raw.get("editorial_cues", [])
            ),
            scene_states=dict(raw.get("scene_states") or {}),
            attempt_ledger=tuple(
                AttemptLedgerEntryV1.from_dict(item) for item in raw.get("attempt_ledger", [])
            ),
            created_at=str(raw.get("created_at") or utc_now_iso()),
            updated_at=str(raw.get("updated_at") or utc_now_iso()),
            schema_version=str(raw.get("schema_version") or SCHEMA_VERSION),
        )
