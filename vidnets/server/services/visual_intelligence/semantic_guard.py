"""Keep explicit pixel contradictions out of every media handoff."""
from collections.abc import Mapping
import re
from urllib.parse import unquote

VISUAL_REVIEW_POLICY_VERSION = 24


def exact_scene_identity_required(scene: Mapping | None) -> bool:
    """A grounded named subject keeps its identity even in the moving-clip lane."""
    if not isinstance(scene, Mapping):
        return False
    requirement = scene.get('visual_requirement') or {}
    if not isinstance(requirement, Mapping) or not requirement.get('strict_identity'):
        return False
    context = requirement.get('subject_context') or {}
    if isinstance(context, Mapping) and context.get('scene_local'):
        return context.get('identity_scope') != 'context'
    return str(requirement.get('subject_type') or '').casefold() in {
        'person', 'public_figure', 'person_or_group', 'named_person', 'celebrity', 'politician',
    }


def explicit_low_visual_match(verdict: Mapping | None) -> bool:
    """Do not promote a completed pixel review that found almost no subject match."""
    if not isinstance(verdict, Mapping) or not (verdict.get('visual_available') or verdict.get('visual_checked')):
        return False
    if verdict.get('identity_score') is None or verdict.get('final_score') is None:
        return False
    try:
        return float(verdict['identity_score']) < 35 and float(verdict['final_score']) < 35
    except (TypeError, ValueError):
        return False


def explicit_domain_conflict(story: str, evidence: str) -> str:
    """Veto clearly different domains, without treating imperfect shots as failures.

    Search queries are never evidence. Callers supply source metadata or a
    description of the pixels; the story supplies the intended meaning.
    """
    expected = str(story or '').casefold()
    observed = re.sub(r'[-_]+', ' ', unquote(str(evidence or '')).casefold())
    business_warrants = bool(re.search(r'\broyal warrants?\b', expected)
                             and re.search(r'\b(?:warrant holders?|suppliers?|brands?|commercial)\b', expected))
    pension_document = re.search(r'\b(?:ministry of pensions|retired pay|military pensions|pensions royal warrant)\b', observed)
    if business_warrants and pension_document and not re.search(r'\b(?:ministry of pensions|retired pay|military pensions)\b', expected):
        return 'military_pension_warrant_for_commercial_appointment'
    gaming_story = re.search(r'\b(?:gaming|gameplay|video games?|gta|grand theft auto|farming simulator|train simulator|train sim world|railworks|madden|nba 2k\d*|game mods?)\b', expected)
    simulation_source = re.search(r'\b(?:kingmods|farming simulator|train simulator|train sim world|railworks|fs(?:19|22|25)\s*(?:mods?)?|madden(?: nfl)?|nba 2k\d*|ea sports fc|game mods?|video.game simulation|simulated (?:diesel )?(?:locomotive|train|cab|environment)|computer generated (?:footage|scene|environment)|3d rendered (?:cab|train|locomotive))\b', observed)
    if simulation_source and not gaming_story:
        return 'simulation_for_real_world'
    american = bool(re.search(r'\b(?:nfl|american football|quarterback|touchdown|super bowl)\b', expected))
    association = r'\b(?:soccer|uefa|champions league|premier league|la liga|penalty kick|fc barcelona|real madrid)\b'
    if american and not re.search(association, expected) and re.search(association, observed):
        return 'different_sport'
    sport = american or bool(re.search(r'\b(?:basketball|baseball|tennis|cricket|hockey|soccer)\b', expected))
    hardware = r'\b(?:tilt and turn|tilt.{0,4}turn|double glazed|quadruple pane|window modes|ventilation|air tight seal)\b'
    if sport and not re.search(hardware, expected) and re.search(hardware, observed):
        return 'literal_hardware_for_sports_metaphor'
    return ''


def calculate_youtube_weighted_score(
    *,
    vision_score: float = 0.0,
    vision_available: bool = False,
    transcript_score: float = 0.0,
    transcript_available: bool = False,
    metadata_score: float = 0.0,
    is_relaxed: bool = True,
) -> float:
    """Calculate a balanced composite score across vision, transcript, and metadata.

    Weights:
    - Vision verification: 0.40
    - Transcript match: 0.35
    - Metadata / Title match: 0.25

    Dynamically rebalances when vision or transcript is unavailable.
    """
    v_norm = min(100.0, max(0.0, float(vision_score or 0.0)))
    t_norm = min(100.0, max(0.0, float(transcript_score or 0.0)))
    m_norm = min(100.0, max(0.0, float(metadata_score or 0.0)))

    if vision_available and transcript_available:
        w_v, w_t, w_m = 0.40, 0.35, 0.25
    elif vision_available and not transcript_available:
        w_v, w_t, w_m = 0.55, 0.0, 0.45
    elif not vision_available and transcript_available:
        w_v, w_t, w_m = 0.0, 0.55, 0.45
    else:
        w_v, w_t, w_m = 0.0, 0.0, 1.0

    score = v_norm * w_v + t_norm * w_t + m_norm * w_m
    if is_relaxed and vision_available and v_norm >= 35.0:
        score = min(100.0, score + 5.0)
    return round(score, 2)


def explicit_scene_conflict(verdict: Mapping | None, *, relaxed: bool = False) -> bool:
    if not isinstance(verdict, Mapping):
        return False
    def yes(value):
        return value is True or str(value).strip().lower() in {"true", "1", "yes"}

    # Absolute non-negotiable safety vetoes apply everywhere
    if any(yes(verdict.get(key)) for key in ("unsafe_content", "suggestive_content")):
        return True

    # Domain contradiction is always fatal
    if verdict.get("domain_match") is False or str(verdict.get("domain_match")).strip().lower() == "false":
        return True

    reason = " ".join(str(verdict.get(key) or "") for key in (
        "reason", "rejection_reason", "status",
    )).lower()

    if relaxed:
        # In relaxed mode (YouTube motion lane / B-roll), minor matchup/era/event
        # differences within the same domain are advisory, not fatal rejections.
        spurious_tokens = (
            "era", "year", "season", "tenure", "older", "dated",
            "period", "roster", "transfer", "trade", "matchup", "uniform",
            "different player", "different team", "not named", "quarterback",
            "different event", "distinct events", "wrong event", "competing event",
            "wrong matchup", "different matchup", "different location", "wrong venue",
            "wrong entity", "different entity", "wrong subject", "different subject",
            "required entity is absent", "required subject is absent",
        )
        if yes(verdict.get("fatal_mismatch")):
            # If fatal_mismatch was triggered solely by a spurious token, do not veto
            if not any(token in reason for token in spurious_tokens):
                return True
        if yes(verdict.get("entity_conflict")):
            # An entity conflict in relaxed mode without domain match failure is advisory
            # unless an explicit wrong domain or different sport marker is present
            if any(marker in reason for marker in ("wrong domain", "different_sport")):
                return True

        # Check fatal relaxed markers (strict domain/simulation contradictions and era conflicts)
        fatal_relaxed_markers = (
            "game-engine", "video-game simulation", "simulation_for_real_world",
            "wrong domain", "different_sport", "literal_hardware_for_sports_metaphor",
            "wrong era", "different era", "anachronistic", "anachronism", "era mismatch",
        )
        for marker in fatal_relaxed_markers:
            for match in re.finditer(re.escape(marker), reason):
                prefix = re.split(r"[.!?;,\n]", reason[max(0, match.start() - 100):match.start()])[-1]
                negated = re.search(
                    r"\b(?:no|not|without|never)\s+"
                    r"(?:(?:a|an|any|the|evidence|indication|signs|of|showing|depicting|"
                    r"shows|depicts|indicating|indicates|being|having)\s+){0,6}$",
                    prefix,
                )
                if not negated:
                    return True
        return False

    # Strict mode checks
    if any(yes(verdict.get(key)) for key in (
        "fatal_mismatch", "entity_conflict", "event_mismatch", "location_mismatch",
        "era_mismatch", "year_mismatch",
    )):
        return True
    if any(key in verdict and (
        verdict[key] is False or str(verdict[key]).strip().lower() == "false"
    ) for key in ("scene_match", "domain_match")):
        return True

    markers = (
        "different event", "distinct events", "wrong event", "competing event",
        "contradictory event", "wrong location", "different location",
        "wrong venue", "different matchup", "wrong matchup", "game-engine",
        "video-game simulation", "wrong subject", "wrong entity", "wrong domain",
        "wrong era", "different era", "wrong year", "different year",
        "anachronistic", "anachronism", "historical mismatch",
    )
    for marker in markers:
        for match in re.finditer(re.escape(marker), reason):
            prefix = re.split(r"[.!?;,\n]", reason[max(0, match.start() - 100):match.start()])[-1]
            negated = re.search(
                r"\b(?:no|not|without|never)\s+"
                r"(?:(?:a|an|any|the|evidence|indication|signs|of|showing|depicting|"
                r"shows|depicts|indicating|indicates|being|having)\s+){0,6}$",
                prefix,
            )
            if not negated:
                return True
    return False


def explicit_visual_facet_conflict(narration: str, description: str) -> str:
    """Reject a described different physical subsystem, not imperfect framing.

    Unknown descriptions stay unknown. Multiple subjects may show any one.
    This guard compares actual pixel descriptions, never search metadata.
    """
    story = str(narration or '').casefold()
    pixels = str(description or '').casefold()
    branding = bool(re.search(r'\b(?:logo|logos|emblem|badge|wordmark)\b', pixels))
    branding_requested = bool(re.search(r'\b(?:logo|logos|emblem|badge|branding|wordmark)\b', story))
    action = r'\b(?:throws?|throwing|passes?|passing|formations?|snaps?|running play|tackles?|tackling|accelerat\w*|brak\w*|taking off|takeoff|landing)\b'
    visible_action = r'\b(?:players?|quarterbacks?|tackl\w*|throw\w*|running|driving|moving|taking off|landing|formation|diagram|illustration)\b'
    if branding and not branding_requested and re.search(action, story) and not re.search(visible_action, pixels):
        return 'static_branding_for_action_scene'
    families = {
        'charging': r'\b(?:superchargers?|charging stations?|chargers?|charge ports?|charging sockets?|ev charging|recharging|plugged[- ]in|charging cables?)\b',
        'storage': r'\b(?:powerwalls?|megapacks?|stationary batter(?:y|ies)|battery storage|energy storage|home batter(?:y|ies)|battery units?|battery containers?)\b',
        'solar': r'\b(?:solar panels?|photovoltaic panels?|solar roofs?|solar installation)\b',
        'interface': r'\b(?:touchscreens?|dashboard displays?|infotainment|central screens?|control screens?)\b',
        'cockpit': r'\b(?:cockpits?|flight decks?|pilot instrument panels?)\b',
        'factory': r'\b(?:assembly lines?|manufacturing lines?|production lines?|factory floors?)\b',
        'engine': r'\b(?:engine bays?|engine interiors?|pistons?|cylinders?|combustion chambers?)\b',
        'court': r'\b(?:courtrooms?|court hearings?|witness stands?|judges? bench)\b',
    }
    expected = {name for name, pattern in families.items() if re.search(pattern, str(narration or ''), re.I)}
    # A montage that explicitly discusses vehicles as well as energy systems
    # may show a vehicle interior. Do not mistake that for a different system.
    vehicle_pattern = r'\b(?:vehicles?|cars?|sedans?|suvs?|roadsters?)\b'
    mentions_vehicle = bool(re.search(vehicle_pattern, str(narration or ''), re.I))
    vehicle_list = bool(re.search(r'\b(?:vehicles?|cars?)\s*,\s*charging', str(narration or ''), re.I))
    if mentions_vehicle and (not expected or vehicle_list):
        expected.update(('vehicle', 'interface', 'engine'))
    visible = {name for name, pattern in families.items() if re.search(pattern, str(description or ''), re.I)}
    if re.search(vehicle_pattern, str(description or ''), re.I):
        visible.add('vehicle')
    if expected and visible and not expected.intersection(visible):
        return 'different_visible_subsystem'
    return ''
