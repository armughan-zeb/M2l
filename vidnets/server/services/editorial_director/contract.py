"""Closed-vocabulary data contract for the Programmatic Documentary Editing Engine.

This module defines the strict, typed EditPlan JSON contract. The Editing Director LLM
produces structured decisions conforming to this schema, which Remotion (and FFmpeg)
then faithfully render without runtime JSX generation or code hallucinations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence


def has_approved_director_copy(scene: Mapping[str, Any]) -> bool:
    """Share the renderer's provenance rule with the timeline and critic."""
    contract = scene.get('editorial_contract') or {}
    provenance = contract.get('source') == 'llm' or (
        not contract.get('source') and contract.get('version') == 4
        and (scene.get('media_plan') or {}).get('decision_source') == 'llm_director')
    from .director import _valid_director_copy
    return bool(contract.get('approved') is True and provenance
                and contract.get('display_text') and contract.get('source_span')
                and _valid_director_copy(scene.get('narration', ''),
                                       contract['display_text'], contract['source_span']))


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = str(hex_color or "").lstrip("#")
    if len(value) != 6 or any(char not in "0123456789abcdefABCDEF" for char in value):
        value = "FFFFFF"
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _mix(a: str, b: str, fraction: float) -> str:
    ratio = max(0.0, min(1.0, fraction))
    return "#" + "".join(f"{round(left * (1 - ratio) + right * ratio):02X}" for left, right in zip(_rgb(a), _rgb(b)))


def _contrast(a: str, b: str) -> float:
    def luminance(color: str) -> float:
        channels = [component / 255 for component in _rgb(color)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))
    bright, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (bright + 0.05) / (dark + 0.05)


def _scene_palette(base: Mapping[str, Any], graphic_theme: str) -> Dict[str, str]:
    palette = dict(base or {})
    background = str(palette.get("background") or "#0B1720")
    film_accent = str(palette.get("accent") or "#D6AD5A")
    if graphic_theme == "editorial_light":
        paper_candidate = str(palette.get("primary") or "#F8F7F2")
        paper = paper_candidate if _contrast(paper_candidate, "#111827") >= 10 else "#F8F7F2"
        ink_candidate = background
        ink = ink_candidate if _contrast(ink_candidate, paper) >= 7 else "#111827"
        accent = film_accent
        for fraction in (0.0, 0.22, 0.44, 0.66, 0.82):
            accent = _mix(film_accent, ink, fraction)
            if _contrast(accent, paper) >= 3:
                break
        palette.update({
            "primary": ink,
            "secondary": _mix(ink, paper, 0.34),
            "background": paper,
            "surface": _mix(paper, "#FFFFFF", 0.6),
            "text_contrast": ink,
            "border": _mix(ink, paper, 0.72),
            "accent": accent,
        })
    elif graphic_theme == "accent_focus":
        accent2 = str(palette.get("accent2") or "")
        focus = accent2 if len(accent2) == 7 and accent2.startswith("#") and _contrast(accent2, background) >= 3 else film_accent
        if _contrast(focus, background) < 3:
            focus = _mix(film_accent, "#FFFFFF", 0.38)
        palette.update({
            "accent": focus,
            "surface": _mix(background, focus, 0.15),
            "border": _mix(background, focus, 0.46),
        })
    return palette


class VisualSceneType(str, Enum):
    """The 15 Universal Scene Systems replacing legacy hardcoded templates."""
    SMART_IMAGE = "SmartImage"
    SMART_VIDEO = "SmartVideo"
    KINETIC_TEXT = "KineticText"
    STAT_CARD = "StatCard"
    QUOTE_CARD = "QuoteCard"
    HEADLINE = "Headline"
    DOCUMENT_HIGHLIGHT = "DocumentHighlight"
    ANIMATED_MAP = "AnimatedMap"
    TIMELINE = "Timeline"
    CHART = "Chart"
    COMPARISON = "Comparison"
    COLLAGE = "Collage"
    PERSON_PROFILE = "PersonProfile"
    DIAGRAM = "Diagram"
    CHAPTER_TITLE = "ChapterTitle"


UNIVERSAL_SCENE_TYPES = tuple(t.value for t in VisualSceneType)

SCENE_TYPE_VARIANTS: Dict[str, tuple[str, ...]] = {
    # Keep this catalogue in lockstep with SmartImage.jsx and the scene editor.
    # These variants are supported by the renderer and are part of normal
    # director/pixel-review framing repairs.
    VisualSceneType.SMART_IMAGE.value: (
        "full_bleed_cinematic", "editorial_crop", "framed_print",
        "archival_polaroid", "parallax_float", "spotlight_reveal",
    ),
    VisualSceneType.SMART_VIDEO.value: ("full_bleed", "pip_inset", "split_duo", "film_gate", "browser_frame", "evidence_clip"),
    VisualSceneType.KINETIC_TEXT.value: ("word_by_word_punch", "staggered_lines", "mask_slide_up", "bold_minimal_center", "tracking_expand", "split_counter_accent", "impact_slab"),
    VisualSceneType.STAT_CARD.value: ("hero_counter_large", "percentage_ring", "kpi_metric_card", "delta_growth_badge", "stat_with_context", "impact_slab"),
    VisualSceneType.QUOTE_CARD.value: ("editorial_large_quote", "quote_fragment_punch"),
    VisualSceneType.HEADLINE.value: ("newspaper_front_page", "digital_article_crop", "floating_press_clipping", "magnified_press_quote"),
    VisualSceneType.DOCUMENT_HIGHLIGHT.value: ("declassified_redaction", "yellow_marker_highlight", "magnifier_zoom_lens", "report_page_pan"),
    VisualSceneType.ANIMATED_MAP.value: (
        "route_flight_arc", "territory_zoom_highlight", "city_to_city_transit",
        "country_spotlight", "global_network",
    ),
    VisualSceneType.TIMELINE.value: ("vertical_chronology", "horizontal_milestone_trail", "date_flip_calendar", "era_bracket_card"),
    VisualSceneType.CHART.value: ("editorial_bar", "trend_line_with_markers", "area_growth_fill", "donut_distribution", "ranked_horizontal_bars", "before_after_delta", "distribution_histogram"),
    VisualSceneType.COMPARISON.value: ("split_slider_before_after", "side_by_side_cards", "versus_contrast_clash", "dual_metric_comparison"),
    VisualSceneType.COLLAGE.value: ("evidence_pinboard", "three_panel_asymmetric", "contact_sheet_grid", "scattered_photo_desk"),
    VisualSceneType.PERSON_PROFILE.value: ("cinematic_lower_third_card", "minimal_profile_split", "portrait_full_bleed"),
    VisualSceneType.DIAGRAM.value: ("cause_and_effect_flow", "step_by_step_process", "network_node_connect", "hierarchical_system_tree", "eclipse_alignment", "shadow_projection", "energy_conversion"),
    VisualSceneType.CHAPTER_TITLE.value: ("cinematic_minimal_act", "bold_editorial_kicker", "archival_slate_divider", "technical_timecode_chapter"),
}

ALLOWED_CAMERAS = {
    "static", "slow_push", "pan_left", "pan_right", "pan_up", "pan_down",
    "pull_out", "focus_zoom", "drift", "cinematic_parallax",
}

ALLOWED_TRANSITIONS = {
    "hard_cut", "cross_dissolve", "directional_slide_left", "directional_slide_right",
    "wipe_left", "wipe_right", "film_burn", "paper_tear", "light_leak", "zoom_cut",
    "dip_black", "dip_white",
}

ALLOWED_ENTRANCES = {
    "cut", "fade", "mask_reveal", "slide_up", "slide_left", "slide_right",
    "scale_pop", "spring_drop", "blur_in", "split_wipe", "redaction_wipe",
}

ALLOWED_TEXTURES = {
    "clean", "film_grain", "paper", "scanlines", "chromatic_aberration",
    "halftone", "vignette", "subtle_noise", "dust_and_scratch",
}

ALLOWED_GENRES = {
    "investigative-documentary", "technology", "royal-history", "historical-documentary", "military-tactical",
    "finance-explainer", "true-crime", "minimal-editorial", "science-nature",
    "gaming-blockbuster", "biographical-essay", "cultural-chronicle",
    "sports-documentary", "politics-explainer", "travelogue",
    "automotive-documentary", "health-science",
}


@dataclass
class PacingBeat:
    """Emotional and pacing intensity metrics for a scene beat (0.0 to 1.0)."""
    importance: float = 0.5
    emotion: float = 0.5
    energy: float = 0.5
    complexity: float = 0.5
    continuation: bool = False
    continuous_reveal: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "importance": round(max(0.0, min(1.0, float(self.importance))), 2),
            "emotion": round(max(0.0, min(1.0, float(self.emotion))), 2),
            "energy": round(max(0.0, min(1.0, float(self.energy))), 2),
            "complexity": round(max(0.0, min(1.0, float(self.complexity))), 2),
        }
        if self.continuation:
            d["continuation"] = True
        if self.continuous_reveal:
            d["continuous_reveal"] = True
        return d


@dataclass
class MotionParameters:
    """Parametric camera and animation physics."""
    camera: str = "slow_push"
    speed: float = 1.0
    spring_stiffness: float = 120.0
    spring_damping: float = 18.0
    overshoot: float = 0.0
    entrance: str = "mask_reveal"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera": self.camera if self.camera in ALLOWED_CAMERAS else "slow_push",
            "speed": round(float(self.speed), 2),
            "spring_stiffness": round(float(self.spring_stiffness), 1),
            "spring_damping": round(float(self.spring_damping), 1),
            "overshoot": round(float(self.overshoot), 2),
            "entrance": self.entrance if self.entrance in ALLOWED_ENTRANCES else "fade",
        }


@dataclass
class SoundCue:
    """Precise sound effect cue keyed to visual/narration events."""
    sfx_id: str
    time_local: float = 0.0
    volume: float = 0.35
    duration: float = 0.5
    tag: str = ""
    src: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "sfx_id": str(self.sfx_id),
            "time_local": round(max(0.0, float(self.time_local)), 3),
            "volume": round(max(0.0, min(1.0, float(self.volume))), 2),
            "duration": round(max(0.05, float(self.duration)), 3),
            "tag": str(self.tag or ""),
        }
        if self.src:
            d["src"] = self.src
        return d


@dataclass
class CaptionConfig:
    """Documentary caption options for the scene."""
    enabled: bool = True
    mode: str = "subtitle"  # "subtitle", "emphasis", "none", "karaoke"
    highlight_words: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "mode": self.mode,
            "highlight_words": list(self.highlight_words),
        }


@dataclass
class PrimaryVisual:
    """Primary foreground or hero graphic system."""
    type: str = VisualSceneType.SMART_IMAGE.value
    variant: str = "full_bleed_cinematic"
    props: Dict[str, Any] = field(default_factory=dict)
    asset: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type if self.type in UNIVERSAL_SCENE_TYPES else VisualSceneType.SMART_IMAGE.value,
            "variant": str(self.variant or "default"),
            "props": dict(self.props or {}),
            "asset": dict(self.asset or {}) if self.asset else None,
        }


@dataclass
class SecondaryVisual:
    """Subordinate graphic callout, citation, coordinate badge, or tag."""
    type: str
    props: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": str(self.type),
            "props": dict(self.props or {}),
        }


@dataclass
class VisualDNA:
    """Unified aesthetic identity for the documentary project."""
    genre: str = "investigative-documentary"
    palette: Dict[str, str] = field(default_factory=lambda: {
        "primary": "#ffffff",
        "secondary": "#d9e2ec",
        "accent": "#c53030",
        "background": "#0b0f19",
        "surface": "#121826",
        "text_contrast": "#ffffff",
        "border": "#243b53",
        "glow": "rgba(197, 48, 48, 0.35)",
    })
    typography: Dict[str, str] = field(default_factory=lambda: {
        "display": "Montserrat",
        "body": "Inter",
        "numbers": "Inter",
        "personality": "neoGrotesk",
    })
    geometry: str = "sharp"  # sharp, rounded, framed, brutalist, editorial_fine
    texture: str = "clean"
    motion_style: str = "restrained"
    caption_style: str = "documentary_small"
    map_style: str = "dark_desaturated"
    chart_style: str = "editorial"
    image_treatment: str = "cinematic_grade"
    transition_grammar: str = "mostly_cuts"
    corner_radius: int = 4
    stroke_width: int = 2
    seed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "genre": self.genre if self.genre in ALLOWED_GENRES else "investigative-documentary",
            "palette": dict(self.palette),
            "typography": dict(self.typography),
            "geometry": self.geometry,
            "texture": self.texture if self.texture in ALLOWED_TEXTURES else "clean",
            "motion_style": self.motion_style,
            "caption_style": self.caption_style,
            "map_style": self.map_style,
            "chart_style": self.chart_style,
            "image_treatment": self.image_treatment,
            "transition_grammar": self.transition_grammar,
            "corner_radius": int(self.corner_radius),
            "stroke_width": int(self.stroke_width),
            "seed": int(self.seed),
        }


@dataclass
class SceneDecision:
    """Scene-by-scene directorial decisions produced by LLM Director."""
    scene_id: str
    scene_index: int
    start: float
    end: float
    duration: float
    narration: str
    visual_intent: str
    primary_visual: PrimaryVisual
    secondary_visuals: List[SecondaryVisual] = field(default_factory=list)
    text: Dict[str, Any] = field(default_factory=dict)
    motion: MotionParameters = field(default_factory=MotionParameters)
    pacing: PacingBeat = field(default_factory=PacingBeat)
    transition_out: str = "hard_cut"
    transition_duration: float = 0.25
    sound_cues: List[SoundCue] = field(default_factory=list)
    captions: CaptionConfig = field(default_factory=CaptionConfig)
    locked: bool = False
    display_text: str = ""
    source_span: str = ""
    emphasis_spans: List[Dict[str, int]] = field(default_factory=list)
    template_id: str = ""
    template_recipe: Dict[str, Any] = field(default_factory=dict)
    map_shot: Dict[str, Any] = field(default_factory=dict)
    motion_cues: List[Dict[str, Any]] = field(default_factory=list)
    media_plan: Dict[str, Any] = field(default_factory=dict)
    graphic_theme: str = "cinematic_dark"
    visual_role: str = "media_overlay"
    visual_role_source: str = "system_default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scene_id": str(self.scene_id),
            "scene_index": int(self.scene_index),
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "duration": round(float(self.duration), 3),
            "narration": str(self.narration or ""),
            "visual_intent": str(self.visual_intent or "narrative_beat"),
            "primary_visual": self.primary_visual.to_dict(),
            "secondary_visuals": [sv.to_dict() for sv in self.secondary_visuals],
            "text": dict(self.text or {}),
            "motion": self.motion.to_dict(),
            "pacing": self.pacing.to_dict(),
            "transition_out": self.transition_out if self.transition_out in ALLOWED_TRANSITIONS else "hard_cut",
            "transition_duration": round(float(self.transition_duration), 3),
            "sound_cues": [sc.to_dict() for sc in self.sound_cues],
            "captions": self.captions.to_dict(),
            "locked": bool(self.locked),
            "display_text": str(self.display_text or ""),
            "source_span": str(self.source_span or ""),
            "emphasis_spans": [dict(span) for span in self.emphasis_spans if isinstance(span, Mapping)],
            "template_id": str(self.template_id or ""),
            "template_recipe": dict(self.template_recipe or {}),
            "map_shot": dict(self.map_shot or {}),
            "motion_cues": [dict(cue) for cue in self.motion_cues if isinstance(cue, Mapping)],
            "media_plan": dict(self.media_plan or {}),
            "graphic_theme": str(self.graphic_theme or "cinematic_dark"),
            "visual_role": str(self.visual_role or "media_overlay"),
            "visual_role_source": str(self.visual_role_source or "system_default"),
        }


@dataclass
class AudioPlan:
    """Authoritative audio plan driving visual edit synchronization."""
    duration: float = 0.0
    voiceover: Dict[str, Any] = field(default_factory=dict)
    music: Dict[str, Any] = field(default_factory=dict)
    narration_timestamps: List[Dict[str, Any]] = field(default_factory=list)
    beats: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration": round(float(self.duration), 3),
            "voiceover": dict(self.voiceover),
            "music": dict(self.music),
            "narration_timestamps": list(self.narration_timestamps),
            "beats": list(self.beats),
        }


@dataclass
class EditPlan:
    """Root contract for the Programmatic Documentary Editor."""
    title: str
    topic: str
    category: str
    seed: int
    visual_dna: VisualDNA
    scenes: List[SceneDecision]
    audio_plan: AudioPlan
    channel_brand: Optional[Dict[str, Any]] = None
    version: str = "2.0"
    fps: int = 30
    width: int = 1920
    height: int = 1080
    metrics: Dict[str, Any] = field(default_factory=dict)
    director_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "title": str(self.title),
            "topic": str(self.topic),
            "category": str(self.category),
            "seed": int(self.seed),
            "fps": int(self.fps),
            "width": int(self.width),
            "height": int(self.height),
            "visual_dna": self.visual_dna.to_dict(),
            "channel_brand": dict(self.channel_brand) if self.channel_brand else None,
            "scenes": [s.to_dict() for s in self.scenes],
            "audio_plan": self.audio_plan.to_dict(),
            "metrics": dict(self.metrics),
            "edit_plan_schema_version": 4,
            "director": dict(self.director_metadata),
        }

    def to_remotion_manifest(self) -> Dict[str, Any]:
        """Convert EditPlan into a Remotion composition-ready manifest dictionary."""
        total_duration = sum(s.duration for s in self.scenes) if self.scenes else self.audio_plan.duration
        total_frames = int(round(total_duration * self.fps))
        manifest_scenes = []
        for s in self.scenes:
            sd = s.to_dict()
            sd["id"] = s.scene_id
            sd["visual_role_contract_version"] = 2
            sd["duration_in_frames"] = int(round(s.duration * self.fps))
            scene_seed = int.from_bytes(
                __import__("hashlib").sha256(f"{self.seed}|{s.scene_id}|remotion-scene".encode("utf-8")).digest()[:4],
                "big",
            )
            sd["design"] = {
                **(sd.get("design") if isinstance(sd.get("design"), Mapping) else {}),
                "seed": scene_seed,
                "scene_seed": scene_seed,
                "locked": bool(s.locked),
            }
            sd["primary"] = s.primary_visual.to_dict()
            graphic_theme = s.graphic_theme if s.graphic_theme in {"editorial_light", "cinematic_dark", "accent_focus"} else "cinematic_dark"
            if s.primary_visual.type == VisualSceneType.ANIMATED_MAP.value and graphic_theme == "editorial_light":
                graphic_theme = "cinematic_dark"
            sd["graphic_theme"] = graphic_theme
            scene_dna = self.visual_dna.to_dict()
            scene_dna["palette"] = _scene_palette(scene_dna.get("palette") or {}, graphic_theme)
            scene_dna["chart_style"] = "editorial_white" if graphic_theme == "editorial_light" else "dark_modern"
            if graphic_theme == "accent_focus":
                scene_dna["stroke_width"] = max(3, int(scene_dna.get("stroke_width") or 2))
            sd["visual_dna"] = scene_dna
            sd["design"]["graphic_theme"] = graphic_theme
            recipe = sd.get("template_recipe") if isinstance(sd.get("template_recipe"), Mapping) else {}
            layers = recipe.get("layers") if isinstance(recipe.get("layers"), list) else []
            allowed_recipe_colors = {
                str(value).lower() for value in (scene_dna.get("palette") or {}).values()
                if isinstance(value, str) and len(value) == 7 and value.startswith("#")
            }
            fallback_recipe_color = str((scene_dna.get("palette") or {}).get("accent") or "#D6AD5A").lower()
            for layer in layers:
                if isinstance(layer, dict) and str(layer.get("color") or "").lower() not in allowed_recipe_colors:
                    layer["color"] = fallback_recipe_color
            if s.display_text:
                sd["editorial_contract"] = {
                    **(sd.get("editorial_contract") if isinstance(sd.get("editorial_contract"), Mapping) else {}),
                    "version": 4,
                    "source": "llm" if s.text.get('copy_source') == 'llm' else "local",
                    "display_text": s.display_text,
                    "source_span": s.source_span,
                    "emphasis_spans": [dict(span) for span in s.emphasis_spans],
                    "approved": True,
                }
            if s.primary_visual.asset:
                asset_dict = dict(s.primary_visual.asset)
                if asset_dict.get("path") and not asset_dict.get("src"):
                    asset_dict["src"] = asset_dict["path"]
                sd["background"] = asset_dict
                sd["media"] = asset_dict
            elif not sd.get("media") and s.primary_visual.asset:
                sd["media"] = dict(s.primary_visual.asset)

            # Backward-compatible graphic mapping for legacy/FFmpeg renderers
            pv_type = s.primary_visual.type
            pv_props = dict(s.primary_visual.props or {})
            if pv_type == "Chart":
                pv_props["card_theme"] = "white_editorial" if graphic_theme == "editorial_light" else "dark_modern"
                sd["primary_visual"]["props"] = dict(pv_props)
            template_id_map = {
                "Chart": "bar-chart",
                "AnimatedMap": "map-route",
                "QuoteCard": "quote-card",
                "ChapterTitle": "chapter-opener",
                "Timeline": "timeline-trace",
                "DocumentHighlight": "source-highlight",
                "Headline": "kinetic-statement",
                "KineticText": "kinetic-statement",
                "Comparison": "split-screen",
                "Diagram": "cause-effect",
                "StatCard": "stat-counter",
                "PersonProfile": "lower-third",
                "Collage": "gallery-stack",
                "MapComparison": "map-comparison",
                "MetricGrid": "metric-grid-enhanced",
                "DossierCard": "dossier-card",
                "ProcessFlow": "process-flow",
                "MediaFocus": "media-focus",
                "MediaFocusCard": "media-focus",
            }
            template_recipe = s.template_recipe if isinstance(s.template_recipe, Mapping) else {}
            if template_recipe.get("layers"):
                graphic_template_id = "generated-recipe"
            elif s.template_id:
                graphic_template_id = s.template_id
            elif pv_type == "KineticText":
                kinetic_variant_map = {
                    "word_by_word_punch": "keyword-reveal",
                    "staggered_lines": "kinetic-statement",
                    "mask_slide_up": "kinetic-text",
                    "bold_minimal_center": "headline-center",
                    "tracking_expand": "kinetic-statement",
                    "split_counter_accent": "keyword-reveal",
                    "impact_slab": "keyword-reveal",
                }
                graphic_template_id = kinetic_variant_map.get(s.primary_visual.variant, "kinetic-statement")
            elif pv_type == "StatCard":
                stat_variant_map = {
                    "hero_counter_large": "stat-counter",
                    "kpi_metric_card": "metric-grid-enhanced",
                    "percentage_ring": "stat-counter",
                    "delta_growth_badge": "stat-counter",
                    "stat_with_context": "stat-counter",
                    "impact_slab": "stat-counter",
                }
                graphic_template_id = stat_variant_map.get(s.primary_visual.variant, "stat-counter")
            elif pv_type == "AnimatedMap":
                map_variant_map = {
                    "route_flight_arc": "map-comparison",
                    "city_to_city_transit": "map-comparison",
                    "territory_zoom_highlight": "map-route",
                }
                graphic_template_id = map_variant_map.get(s.primary_visual.variant, "map-route")
            else:
                graphic_template_id = template_id_map.get(pv_type)
            if graphic_template_id:
                sd["template_id"] = graphic_template_id
                sd["graphic"] = {
                    "template_id": graphic_template_id,
                    "props": pv_props,
                    "scene_local_start": 0.0,
                    "scene_local_end": s.duration,
                }
            else:
                sd["template_id"] = "full-bleed"
                sd["graphic"] = {"template_id": "full-bleed", "props": {}}

            sd["camera"] = {
                "motion": s.motion.camera,
                "speed": s.motion.speed,
                "entrance": s.motion.entrance,
            }
            sd["transition"] = {
                "template_id": s.transition_out,
                "duration": s.transition_duration,
            }
            manifest_scenes.append(sd)

        manifest = {
            "version": 2,
            "edit_plan_schema_version": 4,
            "template_catalog_version": int(self.director_metadata.get("template_catalog_version") or 1),
            "director": dict(self.director_metadata),
            "title": self.title,
            "topic": self.topic,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "duration": round(total_duration, 3),
            "duration_in_frames": total_frames,
            "visual_dna": self.visual_dna.to_dict(),
            "theme": {
                "palette": self.visual_dna.palette,
                "typography": self.visual_dna.typography,
                "font_family": self.visual_dna.typography.get("body", "Inter"),
            },
            "scenes": manifest_scenes,
            "audio": {
                "voiceover": self.audio_plan.voiceover,
                "music": self.audio_plan.music,
            },
            "audio_plan": self.audio_plan.to_dict(),
            "metrics": {
                "total_scenes": len(self.scenes),
                "total_duration": total_duration,
                "variation_audit": {
                    "status": "pass",
                    "warnings": [],
                },
                **self.metrics,
            },
        }
        return manifest


def validate_edit_plan(data: Mapping[str, Any]) -> EditPlan:
    """Validate a raw dictionary against the EditPlan schema.
    
    Raises ValueError with descriptive context if contract violations are detected.
    """
    if not isinstance(data, Mapping):
        raise ValueError("EditPlan payload must be a key-value mapping")

    title = str(data.get("title") or "Documentary Edit")
    topic = str(data.get("topic") or "")
    category = str(data.get("category") or "general")
    seed = int(data.get("seed") or 0)
    fps = max(1, min(120, int(data.get("fps") or 30)))
    width = max(320, min(7680, int(data.get("width") or 1920)))
    height = max(320, min(7680, int(data.get("height") or 1080)))

    # Parse VisualDNA
    raw_dna = dict(data.get("visual_dna") or {})
    raw_typography = raw_dna.get("typography")
    if isinstance(raw_typography, Mapping):
        # Keep the director on the two-family documentary contract. Numbers
        # can use the body family with a different weight and tracking, so a
        # third font cannot invalidate an otherwise usable edit plan.
        raw_typography = dict(raw_typography)
        raw_typography["numbers"] = raw_typography.get("body") or raw_typography.get("display") or "Inter"
        raw_dna["typography"] = raw_typography
    dna = VisualDNA(
        genre=str(raw_dna.get("genre") or "investigative-documentary"),
        palette=dict(raw_dna.get("palette") or {}),
        typography=dict(raw_dna.get("typography") or {}),
        geometry=str(raw_dna.get("geometry") or "sharp"),
        texture=str(raw_dna.get("texture") or "clean"),
        motion_style=str(raw_dna.get("motion_style") or "restrained"),
        caption_style=str(raw_dna.get("caption_style") or "documentary_small"),
        map_style=str(raw_dna.get("map_style") or "dark_desaturated"),
        chart_style=str(raw_dna.get("chart_style") or "editorial"),
        image_treatment=str(raw_dna.get("image_treatment") or "cinematic_grade"),
        transition_grammar=str(raw_dna.get("transition_grammar") or "mostly_cuts"),
        corner_radius=int(raw_dna.get("corner_radius", 4)),
        stroke_width=int(raw_dna.get("stroke_width", 2)),
        seed=int(raw_dna.get("seed", seed)),
    )

    # Parse AudioPlan
    raw_audio = data.get("audio_plan") or {}
    audio_plan = AudioPlan(
        duration=float(raw_audio.get("duration") or 0.0),
        voiceover=dict(raw_audio.get("voiceover") or {}),
        music=dict(raw_audio.get("music") or {}),
        narration_timestamps=list(raw_audio.get("narration_timestamps") or []),
        beats=list(raw_audio.get("beats") or []),
    )

    # Parse Scenes
    raw_scenes = data.get("scenes") or []
    if not isinstance(raw_scenes, Sequence) or not raw_scenes:
        raise ValueError("EditPlan must contain at least one scene decision")

    scenes: List[SceneDecision] = []
    for idx, s in enumerate(raw_scenes):
        if not isinstance(s, Mapping):
            continue
        scene_id = str(s.get("scene_id") or f"scene_{idx+1}")
        scene_index = int(s.get("scene_index", idx))
        start = float(s.get("start") or 0.0)
        duration = max(0.1, float(s.get("duration") or 3.0))
        end = float(s.get("end") or (start + duration))
        narration = str(s.get("narration") or "")
        visual_intent = str(s.get("visual_intent") or "narrative_beat")

        # Primary Visual
        pv_raw = s.get("primary_visual") or {}
        pv_type = str(pv_raw.get("type") or VisualSceneType.SMART_IMAGE.value)
        if pv_type not in UNIVERSAL_SCENE_TYPES:
            pv_type = VisualSceneType.SMART_IMAGE.value
        pv = PrimaryVisual(
            type=pv_type,
            variant=str(pv_raw.get("variant") or "default"),
            props=dict(pv_raw.get("props") or {}),
            asset=dict(pv_raw.get("asset") or {}) if pv_raw.get("asset") else None,
        )

        # Secondary Visuals
        sec_visuals = []
        for sv_raw in s.get("secondary_visuals") or []:
            if isinstance(sv_raw, Mapping):
                sec_visuals.append(SecondaryVisual(
                    type=str(sv_raw.get("type") or ""),
                    props=dict(sv_raw.get("props") or {}),
                ))

        # Motion & Pacing
        m_raw = s.get("motion") or {}
        motion = MotionParameters(
            camera=str(m_raw.get("camera") or "slow_push"),
            speed=float(m_raw.get("speed", 1.0)),
            spring_stiffness=float(m_raw.get("spring_stiffness", 120.0)),
            spring_damping=float(m_raw.get("spring_damping", 18.0)),
            overshoot=float(m_raw.get("overshoot", 0.0)),
            entrance=str(m_raw.get("entrance") or "fade"),
        )
        p_raw = s.get("pacing") or {}
        pacing = PacingBeat(
            importance=float(p_raw.get("importance", 0.5)),
            emotion=float(p_raw.get("emotion", 0.5)),
            energy=float(p_raw.get("energy", 0.5)),
            complexity=float(p_raw.get("complexity", 0.5)),
        )

        # Sound Cues
        sound_cues = []
        for sc_raw in s.get("sound_cues") or []:
            if isinstance(sc_raw, Mapping) and sc_raw.get("sfx_id"):
                sound_cues.append(SoundCue(
                    sfx_id=str(sc_raw["sfx_id"]),
                    time_local=float(sc_raw.get("time_local", 0.0)),
                    volume=float(sc_raw.get("volume", 0.35)),
                    duration=float(sc_raw.get("duration", 0.5)),
                    tag=str(sc_raw.get("tag", "")),
                ))

        # Captions
        c_raw = s.get("captions") or {}
        captions = CaptionConfig(
            enabled=bool(c_raw.get("enabled", True)),
            mode=str(c_raw.get("mode", "subtitle")),
            highlight_words=list(c_raw.get("highlight_words") or []),
        )

        scenes.append(SceneDecision(
            scene_id=scene_id,
            scene_index=scene_index,
            start=start,
            end=end,
            duration=duration,
            narration=narration,
            visual_intent=visual_intent,
            primary_visual=pv,
            secondary_visuals=sec_visuals,
            text=dict(s.get("text") or {}),
            motion=motion,
            pacing=pacing,
            transition_out=str(s.get("transition_out") or "hard_cut"),
            transition_duration=float(s.get("transition_duration", 0.25)),
            sound_cues=sound_cues,
            captions=captions,
            locked=bool(s.get("locked", False)),
            display_text=str(s.get("display_text") or ""),
            source_span=str(s.get("source_span") or ""),
            emphasis_spans=[
                {"start": int(span.get("start", 0)), "end": int(span.get("end", 0))}
                for span in (s.get("emphasis_spans") or [])
                if isinstance(span, Mapping)
            ],
            template_id=str(s.get("template_id") or ""),
            template_recipe=dict(s.get("template_recipe") or {}),
            map_shot=dict(s.get("map_shot") or {}),
            motion_cues=[dict(cue) for cue in (s.get("motion_cues") or []) if isinstance(cue, Mapping)][:3],
            media_plan=dict(s.get("media_plan") or {}) if isinstance(s.get("media_plan"), Mapping) else {},
            graphic_theme=str(s.get("graphic_theme") or "cinematic_dark"),
            visual_role=str(s.get("visual_role") or "media_overlay"),
            visual_role_source=str(s.get("visual_role_source") or "system_default"),
        ))

    return EditPlan(
        title=title,
        topic=topic,
        category=category,
        seed=seed,
        fps=fps,
        width=width,
        height=height,
        visual_dna=dna,
        scenes=scenes,
        audio_plan=audio_plan,
        channel_brand=dict(data.get("channel_brand")) if data.get("channel_brand") else None,
        version=str(data.get("version") or "2.0"),
        metrics=dict(data.get("metrics") or {}),
        director_metadata=dict(data.get("director") or data.get("director_metadata") or {}),
    )
