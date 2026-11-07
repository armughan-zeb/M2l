# =============================================================================
# SCENE ALIGNMENT SERVICE - Zero-Misleading Images System
# =============================================================================
# Core principle: Image models understand only objects, people, actions,
# environments, lighting, and camera angles. They do NOT understand
# psychology, motivation, realization, success, failure, or mindset.
#
# This module ensures every scene is converted into a visually photographable
# reality before image generation.
# =============================================================================

import re
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    ABSTRACT_TO_VISUAL_METAPHORS,
    FORBIDDEN_PROMPT_WORDS,
    MANDATORY_NEGATIVE_PROMPT,
    DEFAULT_CHARACTER_ANCHOR,
    CAMERA_ANGLES,
    ENVIRONMENTS,
    LIGHTING_OPTIONS,
    # 4-Layer NSFW Protection
    RISKY_WORDS,
    SAFE_REPLACEMENTS,
    YOUTUBE_SAFE_NEGATIVE_PROMPT,
    MINIMUM_SUBJECT_AGE,
    RESTRICT_WOMAN_CLOSEUPS,
    ALLOWED_WOMAN_CONTEXTS,
)


# =============================================================================
# LAYER 1: PROMPT SANITIZER (YOUTUBE-SAFE)
# =============================================================================

def sanitize_prompt_for_youtube(prompt: str) -> str:
    """
    LAYER 1: Sanitize prompt to remove words that trigger NSFW hallucination.
    
    This function:
    1. Replaces risky words with safe alternatives
    2. Removes remaining risky words
    3. Enforces age lock (≥25)
    4. Blocks woman close-ups unless professional context
    
    This is NON-NEGOTIABLE for YouTube-safe content.
    """
    result = prompt
    
    # Step 1: Apply safe replacements (order matters - longer phrases first)
    # Sort by length descending to replace longer phrases first
    sorted_replacements = sorted(SAFE_REPLACEMENTS.items(), key=lambda x: len(x[0]), reverse=True)
    for risky, safe in sorted_replacements:
        result = re.sub(rf'\b{re.escape(risky)}\b', safe, result, flags=re.IGNORECASE)
    
    # Step 2: Remove any remaining risky words that weren't replaced
    for risky_word in RISKY_WORDS:
        if risky_word.lower() not in [r.lower() for r in SAFE_REPLACEMENTS.keys()]:
            result = re.sub(rf'\b{re.escape(risky_word)}\b', '', result, flags=re.IGNORECASE)
    
    # Step 3: Enforce age lock - if no age specified, add it
    if 'age' not in result.lower() and ('woman' in result.lower() or 'man' in result.lower() or 'person' in result.lower()):
        # Add age specification
        result = result.replace('adult woman', f'adult woman (age {MINIMUM_SUBJECT_AGE}+)')
        result = result.replace('adult man', f'adult man (age {MINIMUM_SUBJECT_AGE}+)')
        result = result.replace('person', f'adult person (age {MINIMUM_SUBJECT_AGE}+)')
    
    # Step 4: Woman close-up restriction (70% of strikes come from this)
    if RESTRICT_WOMAN_CLOSEUPS and 'woman' in result.lower():
        is_professional = any(ctx in result.lower() for ctx in ALLOWED_WOMAN_CONTEXTS)
        if not is_professional:
            # Block close-ups, force medium/wide shot
            result = re.sub(r'close-up', 'medium shot', result, flags=re.IGNORECASE)
            result = re.sub(r'closeup', 'medium shot', result, flags=re.IGNORECASE)
            # Add professional context
            if 'professional' not in result.lower():
                result = result.replace('woman', 'professional adult woman')
    
    # Step 5: Clean up
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r',\s*,', ',', result)
    result = result.strip(' ,')
    
    return result


def get_youtube_safe_negative_prompt() -> str:
    """Return the YouTube-safe negative prompt (LAYER 2)."""
    return YOUTUBE_SAFE_NEGATIVE_PROMPT


def validate_age_lock(prompt: str) -> bool:
    """Check if prompt has proper age lock (≥25)."""
    # Check for any age mention under 25
    age_pattern = r'age\s*[:\-]?\s*(\d+)'
    matches = re.findall(age_pattern, prompt.lower())
    for age in matches:
        if int(age) < MINIMUM_SUBJECT_AGE:
            return False
    return True


# =============================================================================
# TITLE VISUAL INTENT LOCK
# =============================================================================

def lock_title_visual_intent(title: str, groq_api_key: str = None, openrouter_api_key: str = None) -> dict:
    """
    Step 1: Lock the visual domain for the entire video based on title.
    Uses OpenRouter when available.
    """
    # ``groq_api_key`` remains in the signature for saved-project compatibility;
    # all brain calls are OpenRouter-only.
    api_key = openrouter_api_key
    if not api_key:
        return _default_title_intent()

    intent = None
    if openrouter_api_key:
        try:
            from services.openrouter_llm import OpenRouterLLM
            client = OpenRouterLLM(api_key=openrouter_api_key, model="qwen/qwen3.5-flash-02-23")
            result_text = client.chat(
                user_prompt=f"Analyze this video title and determine appropriate visual content:\n\n\"{title}\"",
                system_prompt="""Return a JSON object with keys: visual_domain, allowed_locations, allowed_subjects, forbidden_visuals. Return ONLY the JSON.""",
                temperature=0.3,
                max_tokens=1000,
                json_mode=True,
            ).strip()
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            intent = json.loads(result_text.strip())
        except Exception as e:
            print(f"   OpenRouter title intent failed: {e}")
            return _default_title_intent()

    if not intent:
        return _default_title_intent()

    for key in ["visual_domain", "allowed_locations", "allowed_subjects", "forbidden_visuals"]:
        if key not in intent:
            intent[key] = [] if key != "visual_domain" else "general"
    forbidden = intent.get("forbidden_visuals", [])
    if isinstance(forbidden, list):
        forbidden.extend(["brains", "diagrams", "charts", "symbols", "floating text", "thought bubbles"])
        intent["forbidden_visuals"] = forbidden
    print(f"   Title Visual Intent: {intent.get('visual_domain', 'N/A')}")
    return intent


def _default_title_intent() -> dict:
    return {
        "visual_domain": "everyday human interactions and situations",
        "allowed_locations": ["home", "office", "cafe", "street", "park"],
        "allowed_subjects": ["single person", "two people", "small group"],
        "forbidden_visuals": ["brains", "diagrams", "charts", "symbols", "floating text", "thought bubbles"]
    }


# =============================================================================
# SCENE CLASSIFICATION
# =============================================================================

def classify_scene(scene_text: str) -> str:
    """
    Step 2: Classify scene as VISUAL or ABSTRACT.
    
    VISUAL: A real photo could capture it (someone sitting, person speaking, phone on table)
    ABSTRACT: A feeling, thought, or concept (feels insecure, realizes mistake)
    
    Returns: "visual" or "abstract"
    """
    scene_lower = scene_text.lower()
    
    # Abstract indicators - concepts that can't be photographed
    abstract_indicators = [
        # Mental states
        "feels", "feeling", "thinks", "thinking", "realizes", "realizing",
        "understands", "understanding", "knows", "knowing",
        "believes", "believing", "imagines", "imagining",
        "remembers", "remembering", "forgets", "forgetting",
        
        # Emotions as states (not expressions)
        "is happy", "is sad", "is angry", "is afraid", "is anxious",
        "is confident", "is insecure", "is motivated", "is unmotivated",
        "becomes aware", "gains awareness", "loses confidence",
        
        # Abstract concepts
        "psychology", "psychologically", "mentally", "emotionally",
        "motivation", "realization", "awareness", "consciousness",
        "success", "failure", "achievement", "growth",
        "mindset", "personality", "character",
        
        # Storytelling abstractions
        "represents", "symbolizes", "means", "indicates",
        "shows that", "demonstrates", "implies",
    ]
    
    # Check for abstract indicators
    for indicator in abstract_indicators:
        if indicator in scene_lower:
            return "abstract"
    
    # Check for forbidden words
    for word in FORBIDDEN_PROMPT_WORDS:
        if word.lower() in scene_lower:
            return "abstract"
    
    # Visual indicators - things that CAN be photographed
    visual_indicators = [
        # Physical actions
        "walking", "sitting", "standing", "running", "lying",
        "holding", "touching", "looking at", "reaching",
        "writing", "typing", "reading", "eating", "drinking",
        
        # Physical descriptions
        "wearing", "dressed in", "has", "with",
        "tall", "short", "young", "old",
        
        # Locations
        "in the", "at the", "near the", "beside",
        "room", "street", "park", "office", "home",
        
        # Objects
        "phone", "computer", "book", "table", "chair",
        "window", "door", "wall", "car", "bag",
    ]
    
    # If we find visual indicators and no abstract ones, it's visual
    has_visual = any(ind in scene_lower for ind in visual_indicators)
    
    if has_visual:
        return "visual"
    
    # Default to abstract if unclear - safer to convert
    return "abstract"


# =============================================================================
# ABSTRACT → VISUAL METAPHOR CONVERSION
# =============================================================================

def convert_to_visual_metaphor(abstract_scene: str, groq_api_key: str = None, openrouter_api_key: str = None) -> str:
    """
    Step 3: Convert abstract scene into concrete visual metaphor.
    Uses predefined mappings first, then OpenRouter, then deterministic stripping.
    """
    scene_lower = abstract_scene.lower()
    
    visual_parts = []
    for abstract_concept, visual_metaphor in ABSTRACT_TO_VISUAL_METAPHORS.items():
        if abstract_concept.lower() in scene_lower:
            visual_parts.append(visual_metaphor)
    
    if visual_parts:
        visual_context = _extract_visual_context(abstract_scene)
        combined = ", ".join(visual_parts)
        return f"{visual_context}, {combined}" if visual_context else combined
    
    api_key = openrouter_api_key
    if api_key:
        return _llm_convert_to_visual(abstract_scene, None, openrouter_api_key)
    
    return _strip_abstract_words(abstract_scene)


def _extract_visual_context(scene: str) -> str:
    """Extract any existing visual elements from a scene description."""
    # Keep location, subject type, and physical descriptions
    visual_words = []
    words = scene.split()
    
    keep_next = False
    for word in words:
        word_lower = word.lower().strip(".,!?")
        
        # Keep location indicators
        if word_lower in ["in", "at", "near", "beside", "inside", "outside"]:
            keep_next = True
            visual_words.append(word)
        elif keep_next:
            visual_words.append(word)
            keep_next = False
        # Keep physical descriptors
        elif word_lower in ["wearing", "dressed", "holding", "sitting", "standing", "walking"]:
            visual_words.append(word)
    
    return " ".join(visual_words) if visual_words else ""


def _strip_abstract_words(scene: str) -> str:
    """Remove forbidden/abstract words and return remaining text."""
    result = scene
    for word in FORBIDDEN_PROMPT_WORDS:
        result = re.sub(rf'\b{re.escape(word)}\b', '', result, flags=re.IGNORECASE)
    
    # Clean up extra spaces
    result = re.sub(r'\s+', ' ', result).strip()
    
    # If almost nothing left, provide a generic visual
    if len(result) < 20:
        return "person in contemplative pose, natural indoor setting"
    
    return result


def _llm_convert_to_visual(abstract_scene: str, groq_api_key: str = None, openrouter_api_key: str = None) -> str:
    """Use OpenRouter to convert an abstract scene to a visual metaphor."""
    api_key = openrouter_api_key
    if not api_key:
        return _strip_abstract_words(abstract_scene)

    system_prompt = """Convert abstract concepts into concrete visual descriptions for AI image generation.
CRITICAL: Only describe objects, people, actions, environments, lighting. No psychology, emotions, or concepts.
Return ONLY the visual description."""

    user_prompt = f"Convert this abstract scene into a concrete visual description:\n\n\"{abstract_scene}\""

    if openrouter_api_key:
        try:
            from services.openrouter_llm import OpenRouterLLM
            client = OpenRouterLLM(api_key=openrouter_api_key, model="qwen/qwen3.5-flash-02-23")
            return client.chat(
                user_prompt=user_prompt, system_prompt=system_prompt,
                temperature=0.5, max_tokens=500,
            ).strip()
        except Exception as e:
            print(f"   OpenRouter conversion failed: {e}")

    return _strip_abstract_words(abstract_scene)


# =============================================================================
# VISUAL BLUEPRINT CONSTRUCTION
# =============================================================================

def build_visual_blueprint(
    scene_id: int,
    scene_text: str,
    character_anchor: dict = None,
    style: str = "cinematic realism"
) -> dict:
    """
    Step 4: Construct complete visual blueprint for image generation.
    
    Returns schema with all required fields for consistent image generation.
    """
    if character_anchor is None:
        character_anchor = DEFAULT_CHARACTER_ANCHOR.copy()
    
    # Classify the scene
    scene_type = classify_scene(scene_text)
    
    # Convert if abstract
    if scene_type == "abstract":
        visual_metaphor = convert_to_visual_metaphor(scene_text)
    else:
        visual_metaphor = scene_text
    
    # Extract components from the visual description
    camera = _detect_camera_angle(visual_metaphor)
    environment = _detect_environment(visual_metaphor)
    lighting = _detect_lighting(visual_metaphor)
    time_of_day = _detect_time(visual_metaphor)
    
    blueprint = {
        "scene_id": scene_id,
        "scene_type": scene_type,
        "original_scene": scene_text,
        "visual_metaphor": visual_metaphor,
        "subject": _build_subject_description(character_anchor),
        "action_posture": _extract_action(visual_metaphor),
        "camera": camera,
        "environment": environment,
        "lighting": lighting,
        "time": time_of_day,
        "style": style
    }
    
    return blueprint


def _detect_camera_angle(scene: str) -> str:
    """Detect appropriate camera angle from scene description."""
    scene_lower = scene.lower()
    
    if "face" in scene_lower or "expression" in scene_lower:
        return "close-up"
    elif "full body" in scene_lower or "walking" in scene_lower or "standing" in scene_lower:
        return "medium wide shot"
    elif "group" in scene_lower or "people" in scene_lower:
        return "wide shot"
    elif "hands" in scene_lower:
        return "extreme close-up"
    else:
        return "medium shot"


def _detect_environment(scene: str) -> str:
    """Detect environment from scene description."""
    scene_lower = scene.lower()
    
    for env in ENVIRONMENTS:
        if env.replace("indoor ", "").replace("outdoor ", "") in scene_lower:
            return env
    
    # Default based on indoor/outdoor keywords
    if any(word in scene_lower for word in ["outside", "street", "park", "nature"]):
        return "outdoor urban"
    return "indoor room"


def _detect_lighting(scene: str) -> str:
    """Detect lighting from scene description."""
    scene_lower = scene.lower()
    
    for light in LIGHTING_OPTIONS:
        if light.split()[0] in scene_lower:
            return light
    
    return "soft natural light"


def _detect_time(scene: str) -> str:
    """Detect time of day from scene description."""
    scene_lower = scene.lower()
    
    if any(word in scene_lower for word in ["night", "dark", "evening", "midnight"]):
        return "night"
    elif any(word in scene_lower for word in ["sunset", "dusk", "golden"]):
        return "sunset"
    elif any(word in scene_lower for word in ["morning", "dawn", "sunrise"]):
        return "morning"
    return "day"


def _build_subject_description(anchor: dict) -> str:
    """Build subject description from character anchor."""
    parts = []
    
    if anchor.get("age_range"):
        parts.append(anchor["age_range"] + " year old")
    if anchor.get("gender") and anchor["gender"] != "neutral":
        parts.append(anchor["gender"])
    if anchor.get("ethnicity") and anchor["ethnicity"] != "diverse/unspecified":
        parts.append(anchor["ethnicity"])
    
    parts.append("person")
    
    if anchor.get("hair"):
        parts.append(f"with {anchor['hair']}")
    if anchor.get("clothing"):
        parts.append(f"wearing {anchor['clothing']}")
    
    return " ".join(parts)


def _extract_action(visual: str) -> str:
    """Extract the main action/posture from visual description."""
    # Return up to 100 chars of the visual description as action
    return visual[:100] if len(visual) > 100 else visual


# =============================================================================
# LOCKED PROMPT CONSTRUCTION
# =============================================================================

def construct_locked_prompt(blueprint: dict) -> str:
    """
    Step 5: Assemble final image prompt from blueprint.
    
    Formula: [Subject] + [Action/Posture] + [Environment] + [Camera] + [Lighting] + [Style]
    """
    parts = []
    
    # Subject with action
    if blueprint.get("subject"):
        parts.append(blueprint["subject"])
    
    if blueprint.get("action_posture"):
        parts.append(blueprint["action_posture"])
    
    # Environment
    if blueprint.get("environment"):
        parts.append(blueprint["environment"])
    
    # Camera angle
    if blueprint.get("camera"):
        parts.append(f"{blueprint['camera']} camera angle")
    
    # Lighting
    if blueprint.get("lighting"):
        parts.append(blueprint["lighting"])
    
    # Time
    if blueprint.get("time"):
        parts.append(f"time of day: {blueprint['time']}")
    
    # Style
    if blueprint.get("style"):
        parts.append(blueprint["style"])
    
    prompt = ", ".join(parts)
    
    # Final validation - remove any remaining forbidden words
    for word in FORBIDDEN_PROMPT_WORDS:
        prompt = re.sub(rf'\b{re.escape(word)}\b', '', prompt, flags=re.IGNORECASE)
    
    # Clean up
    prompt = re.sub(r'\s+', ' ', prompt)
    prompt = re.sub(r',\s*,', ',', prompt)
    prompt = prompt.strip(" ,")
    
    return prompt


def get_negative_prompt() -> str:
    """Return the mandatory negative prompt for image generation."""
    return MANDATORY_NEGATIVE_PROMPT


# =============================================================================
# SCENE VALIDATION
# =============================================================================

def validate_scene(scene_prompt: str, title_intent: dict = None) -> tuple:
    """
    Step 6: Validate scene against alignment rules.
    
    Returns: (is_valid: bool, rejection_reason: str or None)
    
    A scene is REJECTED if:
    - It contains emotions without posture/action
    - It references thoughts or psychology
    - It includes symbols, diagrams, or text
    - It violates title visual intent
    """
    scene_lower = scene_prompt.lower()
    
    # Check for forbidden words
    for word in FORBIDDEN_PROMPT_WORDS:
        if word.lower() in scene_lower:
            return False, f"Contains forbidden word: '{word}'"
    
    # Check for abstract emotion words without physical descriptors
    emotion_words = ["happy", "sad", "angry", "afraid", "anxious", "confident", "insecure"]
    physical_words = ["posture", "looking", "shoulders", "standing", "sitting", "hands", "arms", "face"]
    
    has_emotion = any(emo in scene_lower for emo in emotion_words)
    has_physical = any(phys in scene_lower for phys in physical_words)
    
    if has_emotion and not has_physical:
        return False, "Contains emotion without physical descriptor"
    
    # Check for forbidden visual elements
    forbidden_visuals = ["diagram", "chart", "graph", "symbol", "text", "words", "brain", "thought bubble"]
    for forbidden in forbidden_visuals:
        if forbidden in scene_lower:
            return False, f"Contains forbidden visual: '{forbidden}'"
    
    # Check against title intent if provided
    if title_intent and title_intent.get("forbidden_visuals"):
        for forbidden in title_intent["forbidden_visuals"]:
            if forbidden.lower() in scene_lower:
                return False, f"Violates title intent - forbidden visual: '{forbidden}'"
    
    return True, None


# =============================================================================
# CHARACTER ANCHOR GENERATION
# =============================================================================

def generate_character_anchor(title: str, groq_api_key: str = None, openrouter_api_key: str = None) -> dict:
    """
    Generate consistent character description based on video title.
    Uses OpenRouter when available.
    """
    api_key = openrouter_api_key
    if not api_key:
        return DEFAULT_CHARACTER_ANCHOR.copy()

    if openrouter_api_key:
        try:
            from services.openrouter_llm import OpenRouterLLM
            client = OpenRouterLLM(api_key=openrouter_api_key, model="qwen/qwen3.5-flash-02-23")
            result_text = client.chat(
                user_prompt=f"What character type would be most appropriate for this video?\n\nTitle: \"{title}\"",
                system_prompt="""Return a JSON object with keys: gender, age_range, ethnicity, hair, clothing, body_type. Return ONLY the JSON.""",
                temperature=0.3,
                max_tokens=500,
                json_mode=True,
            ).strip()
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
            anchor = json.loads(result_text.strip())
            for key in DEFAULT_CHARACTER_ANCHOR:
                if key not in anchor:
                    anchor[key] = DEFAULT_CHARACTER_ANCHOR[key]
            return anchor
        except Exception as e:
            print(f"   OpenRouter character anchor failed: {e}")

    return DEFAULT_CHARACTER_ANCHOR.copy()


# =============================================================================
# MAIN PIPELINE FUNCTION
# =============================================================================

def align_scenes_for_image_generation(
    title: str,
    raw_scenes: list,
    groq_api_key: str = None,
    openrouter_api_key: str = None,
    style: str = "cinematic realism"
) -> list:
    """
    Main entry point: Process all scenes through the alignment pipeline.
    Uses OpenRouter for all LLM alignment work.
    """
    print(f"\nScene Alignment Pipeline: {len(raw_scenes)} scenes")
    
    print("   Locking title visual intent...")
    title_intent = lock_title_visual_intent(title, openrouter_api_key=openrouter_api_key)
    
    print("   Generating character anchor...")
    character_anchor = generate_character_anchor(title, openrouter_api_key=openrouter_api_key)
    
    # Enforce minimum age in character anchor
    if character_anchor.get("age_range"):
        # Parse and enforce minimum age
        age_str = character_anchor["age_range"]
        try:
            if "-" in age_str:
                min_age = int(age_str.split("-")[0])
                if min_age < MINIMUM_SUBJECT_AGE:
                    character_anchor["age_range"] = f"{MINIMUM_SUBJECT_AGE}-{int(age_str.split('-')[1])}"
            else:
                age = int(age_str)
                if age < MINIMUM_SUBJECT_AGE:
                    character_anchor["age_range"] = f"{MINIMUM_SUBJECT_AGE}-35"
        except:
            character_anchor["age_range"] = f"{MINIMUM_SUBJECT_AGE}-35"
    else:
        character_anchor["age_range"] = f"{MINIMUM_SUBJECT_AGE}-35"
    
    # Step 3: Process each scene
    aligned_prompts = []
    abstract_count = 0
    rejected_count = 0
    
    for i, scene in enumerate(raw_scenes):
        scene_type = classify_scene(scene)
        if scene_type == "abstract":
            abstract_count += 1
        
        # Build blueprint (handles conversion internally)
        blueprint = build_visual_blueprint(
            scene_id=i + 1,
            scene_text=scene,
            character_anchor=character_anchor,
            style=style
        )
        
        # Construct locked prompt
        locked_prompt = construct_locked_prompt(blueprint)
        
        # Validate
        is_valid, rejection_reason = validate_scene(locked_prompt, title_intent)
        
        if not is_valid:
            print(f"   ⚠️ Scene {i+1} rejected: {rejection_reason}")
            # Try to salvage with more aggressive conversion
            locked_prompt = convert_to_visual_metaphor(scene, openrouter_api_key=openrouter_api_key)
            is_valid, _ = validate_scene(locked_prompt, title_intent)
            if not is_valid:
                rejected_count += 1
                # Use generic fallback
                locked_prompt = f"{_build_subject_description(character_anchor)}, neutral expression, {blueprint['environment']}, {blueprint['camera']}, {blueprint['lighting']}"
        
        aligned_prompts.append(locked_prompt)
    
    # Step 4: Apply 4-Layer NSFW Protection to ALL prompts
    print("   🔒 Applying 4-Layer NSFW Protection...")
    sanitized_prompts = []
    for prompt in aligned_prompts:
        sanitized = sanitize_prompt_for_youtube(prompt)
        sanitized_prompts.append(sanitized)
    
    print(f"   ✅ Aligned & Sanitized {len(sanitized_prompts)} scenes ({abstract_count} abstract→visual, {rejected_count} rejected & salvaged)")
    
    return sanitized_prompts
