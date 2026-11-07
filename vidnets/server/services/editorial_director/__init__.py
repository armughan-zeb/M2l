"""Editorial Director — Programmatic Documentary Editing Engine for VidZeo."""

from .contract import (
    EditPlan,
    VisualDNA,
    SceneDecision,
    PrimaryVisual,
    SecondaryVisual,
    MotionParameters,
    AudioPlan,
    PacingBeat,
    VisualSceneType,
    UNIVERSAL_SCENE_TYPES,
    validate_edit_plan,
)
from .visual_dna import (
    generate_visual_dna,
    compute_video_seed,
    detect_genre,
    GENRE_PRESETS,
    FONT_PERSONALITIES,
)
from .similarity_detector import (
    EditFingerprint,
    compute_edit_fingerprint,
    check_similarity,
    reroll_variations_if_similar,
)
from .script_analyzer import (
    analyze_sentence_triggers,
    compute_beat_pacing,
    SemanticTriggers,
    extract_documentary_highlight,
)
from .scene_grammar import enforce_scene_grammar, enforce_scene_dict_grammar
from .director import EditingDirector, DIRECTOR_SYSTEM_PROMPT
from .ai_tweak import tweak_edit_plan
from .sound_director import generate_sound_cues_for_scene, resolve_sfx_path, SFX_REGISTRY
from .audio_sync import align_scene_to_words
from .visual_critic import audit, audit_frames, audit_manifest
from .manager import MANAGER_RULES, apply_manager_corrections, manager_review, execute_reviewer_council
from .sequence_controller import SequenceController, audit_sequence_continuity
from .media_director import (
    MEDIA_FIRST_RULES,
    annotate_media_compositions,
    audit_media_compositions,
    choose_media_safe_region,
    plan_media_composition,
)
from .swarm import (
    AGENTS as DOCUMENTARY_SWARM_AGENTS,
    SWARM_RULES as DOCUMENTARY_SWARM_RULES,
    graphic_director,
    media_editor,
    motion_designer,
    run_documentary_swarm,
    source_analyzer,
)
from .investigative_agents import investigate_director_decisions
from .media_first import (
    COMPOSITION_LIBRARY,
    DESIGN_LIBRARY_VERSION,
    apply_media_first_direction,
    audit_editorial_contracts,
    normalize_editorial_controls,
)

__all__ = [
    "EditPlan",
    "VisualDNA",
    "SceneDecision",
    "PrimaryVisual",
    "SecondaryVisual",
    "MotionParameters",
    "AudioPlan",
    "PacingBeat",
    "VisualSceneType",
    "UNIVERSAL_SCENE_TYPES",
    "validate_edit_plan",
    "generate_visual_dna",
    "compute_video_seed",
    "GENRE_PRESETS",
    "FONT_PERSONALITIES",
    "EditFingerprint",
    "compute_edit_fingerprint",
    "check_similarity",
    "reroll_variations_if_similar",
    "analyze_sentence_triggers",
    "compute_beat_pacing",
    "SemanticTriggers",
    "enforce_scene_grammar",
    "EditingDirector",
    "DIRECTOR_SYSTEM_PROMPT",
    "tweak_edit_plan",
    "generate_sound_cues_for_scene",
    "resolve_sfx_path",
    "SFX_REGISTRY",
    "align_scene_to_words",
    "audit",
    "audit_frames",
    "audit_manifest",
    "MANAGER_RULES",
    "apply_manager_corrections",
    "manager_review",
    "execute_reviewer_council",
    "SequenceController",
    "audit_sequence_continuity",
    "MEDIA_FIRST_RULES",
    "annotate_media_compositions",
    "audit_media_compositions",
    "choose_media_safe_region",
    "plan_media_composition",
    "DOCUMENTARY_SWARM_AGENTS",
    "DOCUMENTARY_SWARM_RULES",
    "graphic_director",
    "media_editor",
    "motion_designer",
    "run_documentary_swarm",
    "source_analyzer",
    "investigate_director_decisions",
    "COMPOSITION_LIBRARY",
    "DESIGN_LIBRARY_VERSION",
    "apply_media_first_direction",
    "audit_editorial_contracts",
    "normalize_editorial_controls",
]


RemotionEditorialDirector = EditingDirector
__all__.append("RemotionEditorialDirector")
