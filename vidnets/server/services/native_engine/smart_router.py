"""
Smart Router — maps LLM editorial beats to Vox templates.

The editorial brain generates beats with:
  - emphasis: 1-3 words for display
  - treatment: keyword, date, location, person, counter, quote, chapter, cta
  - highlight_kind: milestone, scale, turning_point, warning, key_fact, human_stakes
  - role: intro, setup, evidence, reveal, statistic, conclusion, cta

This router maps these to the 35+ Vox templates, using the profile engine's
template_map for per-video variation.
"""

from __future__ import annotations
import hashlib
import re
from typing import Dict, List, Optional

# ═══════════════════════════════════════════════════════════════════
# Treatment → Template mapping
# ═══════════════════════════════════════════════════════════════════

TREATMENT_TEMPLATES = {
    "chapter":    ["cold_open", "split_statement", "kinetic_statement", "chapter_title", "bold_name_banner", "headline_center"],
    "keyword":    ["keyword_reveal", "kinetic_statement", "word_cascade", "typewriter_reveal", "headline_center", "card_overlay"],
    "date":       ["date_reveal", "calendar_rip", "timeline_trace", "vintage_film_card", "date_numeral", "timeline_strip"],
    "location":   ["location_label", "map_route", "field_caption", "location_title_card", "archival_caption", "map_card"],
    "person":     ["profile_dossier", "evidence_callout", "field_caption", "dramatic_portrait", "bold_name_banner", "profile_card", "subject_grid", "presenter_card", "polaroid_card"],
    "counter":    ["stat_counter", "money_counter", "percentage_gauge", "metric_grid", "step_progress"],
    "quote":      ["quote_card", "witness_quote", "source_citation", "bold_red_text"],
    "evidence":   ["evidence_callout", "case_file", "source_citation", "exhibit_label", "index_card", "evidence_highlight", "card_overlay"],
    "redaction":  ["redaction_reveal", "case_file", "stamp_mark", "passport_stamp"],
    "signal":     ["signal_scan", "typewriter_reveal", "countdown_clock"],
    "comparison": ["cause_effect", "before_after", "split_compare", "diagonal_split", "split_screen_doc", "listicle_steps"],
    "cta":        ["end_card", "cta_card", "kinetic_statement"],
    "timeline":   ["timeline_trace", "date_reveal", "calendar_rip", "vintage_headline", "timeline_strip", "step_progress"],
    "data":       ["stat_counter", "metric_grid", "bar_chart", "ranked_list", "money_counter", "listicle_steps", "step_progress"],
    "listicle":   ["listicle_steps", "step_progress", "metric_grid"],
    "document":   ["newsprint_reveal", "index_card", "exhibit_label", "case_file", "torn_paper_reveal", "paper_fold_reveal", "evidence_highlight", "archival_caption", "archive_canvas", "card_overlay"],
    "archive":    ["exhibit_label", "stamp_mark", "source_citation", "vintage_film_card", "passport_stamp", "photo_frame", "polaroid_card", "archive_canvas"],
}

# ═══════════════════════════════════════════════════════════════════
# Role → Template mapping (fallback)
# ═══════════════════════════════════════════════════════════════════

ROLE_TEMPLATES = {
    "intro":       ["cold_open", "kinetic_statement", "word_cascade", "chapter_title"],
    "setup":       ["kinetic_statement", "field_caption", "split_statement", "watercolor_reveal"],
    "evidence":    ["evidence_callout", "case_file", "source_citation", "exhibit_label", "card_overlay"],
    "reveal":      ["keyword_reveal", "redaction_reveal", "word_cascade", "cinematic_zoom_text"],
    "statistic":   ["stat_counter", "money_counter", "percentage_gauge", "metric_grid", "bar_chart", "step_progress"],
    "conclusion":  ["kinetic_statement", "word_cascade", "split_statement", "end_card"],
    "cta":         ["end_card", "cta_card"],
    "transition":  ["date_reveal", "location_label", "field_caption", "location_title_card", "map_card"],
    "quote":       ["quote_card", "witness_quote", "bold_red_text"],
    "chapter":     ["cold_open", "split_statement", "chapter_title", "bold_name_banner"],
    "context":     ["field_caption", "exhibit_label", "index_card", "newsprint_reveal", "archive_canvas"],
    "comparison":  ["cause_effect", "before_after", "split_compare", "diagonal_split"],
    "highlight":   ["kinetic_statement", "keyword_reveal", "bold_red_text", "typewriter_reveal", "card_overlay"],
}

# ═══════════════════════════════════════════════════════════════════
# Highlight kind → Template mapping
# ═══════════════════════════════════════════════════════════════════

HIGHLIGHT_TEMPLATES = {
    "milestone":     ["date_reveal", "calendar_rip", "timeline_trace", "vintage_headline", "date_numeral", "timeline_strip"],
    "scale":         ["stat_counter", "money_counter", "metric_grid", "bar_chart", "step_progress"],
    "turning_point": ["keyword_reveal", "kinetic_statement", "word_cascade", "bold_red_text"],
    "warning":       ["redaction_reveal", "countdown_clock", "evidence_callout", "stamp_mark"],
    "key_fact":      ["keyword_reveal", "evidence_callout", "case_file", "cinematic_zoom_text", "evidence_highlight", "card_overlay"],
    "human_stakes":  ["quote_card", "witness_quote", "profile_dossier", "dramatic_portrait", "profile_card", "subject_grid", "presenter_card", "polaroid_card"],
    "comparison":    ["cause_effect", "before_after", "split_compare", "diagonal_split"],
    "location":      ["location_label", "map_route", "field_caption", "location_title_card", "map_card"],
    "data":          ["stat_counter", "metric_grid", "bar_chart", "money_counter", "step_progress"],
    "document":      ["newsprint_reveal", "index_card", "source_citation", "torn_paper_reveal", "archive_canvas"],
    "signal":        ["signal_scan", "typewriter_reveal", "retro_tech_overlay"],
    "archive":       ["exhibit_label", "stamp_mark", "source_citation", "vintage_film_card", "photo_frame", "archive_canvas"],
    "conclusion":    ["end_card", "cta_card", "kinetic_statement", "split_statement"],
}


def route_beat(
    beat: dict,
    profile_template_map: Optional[Dict[str, str]] = None,
    used_recent: Optional[List[str]] = None,
    scene_index: int = 0,
    seed: int = 0,
    narration_text: str = "",
) -> str:
    """Route an LLM editorial beat to the best template.

    Priority:
      1. Profile engine's template_map (per-video variation)
      2. Treatment mapping
      3. Highlight kind mapping
      4. Role mapping
      5. Fallback to kinetic_statement

    Anti-repetition: avoids templates in used_recent.
    Variation: uses hash-based selection so templates vary per video seed.
    """
    used_recent = used_recent or []
    treatment = str(beat.get("treatment", "") or "").strip().lower()
    highlight = beat.get("highlight_kind", "")
    role = beat.get("role", "")
    emphasis = beat.get("emphasis", "")

    narration = str(narration_text or "")
    if re.search(
        r"\b(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+(?:18|19|20)\d{2})?|(?:18|19|20)\d{2})\b",
        narration,
        re.I,
    ):
        treatment = "date"
    elif treatment == "listicle" or re.search(
        r"\b(?:first|second|third|finally|step\s+\d+)\b|\b\d+\s+(?:ways|reasons|steps|things|lessons|facts)\b",
        narration,
        re.I,
    ):
        treatment = "listicle"

    hard_candidates = TREATMENT_TEMPLATES.get(treatment, []) if treatment in {"date", "listicle"} else []

    candidates = []

    # 1. Profile engine override
    if not hard_candidates and profile_template_map and treatment in profile_template_map:
        candidates.append(profile_template_map[treatment])

    # 2. Treatment mapping
    if hard_candidates:
        candidates.extend(hard_candidates)
    elif treatment in TREATMENT_TEMPLATES:
        candidates.extend(TREATMENT_TEMPLATES[treatment])

    # 3. Highlight kind mapping
    if not hard_candidates and highlight in HIGHLIGHT_TEMPLATES:
        candidates.extend(HIGHLIGHT_TEMPLATES[highlight])

    # 4. Role mapping
    if not hard_candidates and role in ROLE_TEMPLATES:
        candidates.extend(ROLE_TEMPLATES[role])

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    # Filter out recently used (preserve anti-repetition)
    available = [t for t in unique if t not in used_recent]

    if available:
        # Hash-based variation: combine scene_index + emphasis + seed for uniqueness
        try:
            hash_key = f"{seed}:{scene_index}:{emphasis}" if seed else f"{scene_index}:{emphasis}"
            var_idx = int(hashlib.sha256(hash_key.encode("utf-8")).hexdigest()[:12], 16) % len(available)
        except Exception:
            var_idx = scene_index % len(available)
        return available[var_idx]

    # Fallback: pick any candidate not in the last 3 recent
    fallback = [t for t in unique if t not in used_recent[-3:]]
    if fallback:
        var_idx = (seed + scene_index) % len(fallback) if seed else scene_index % len(fallback)
        return fallback[var_idx]

    # Last resort
    return unique[0] if unique else "kinetic_statement"


def build_props_from_beat(beat: dict, narration_text: str = "") -> dict:
    """Extract props from an LLM beat for template rendering.

    The beat contains emphasis (1-3 words) and treatment info.
    We map these to the template's expected props.
    """
    emphasis = beat.get("emphasis", "")
    detail = beat.get("detail", "")
    highlight_kind = beat.get("highlight_kind", "")
    role = beat.get("role", "")
    treatment = beat.get("treatment", "")

    # Ground the LLM emphasis back into the narration while preserving a
    # short 1–4 word phrase.  The native and Remotion renderers receive the
    # same phrase and a separate Whisper timing span, so a named entity or
    # meaningful statistic does not collapse into an arbitrary single word.
    import re
    source_text = str(narration_text or "").strip()
    emphasis_text = str(emphasis or "").strip()
    source_words = re.findall(r"[A-Za-z0-9']+", source_text)
    emphasis_words = re.findall(r"[A-Za-z0-9']+", emphasis_text)
    grounded = ""
    low_value = {
        "a", "an", "and", "as", "at", "before", "but", "by", "for", "from", "he", "her",
        "his", "in", "into", "it", "its", "of", "on", "or", "she", "that", "the", "their",
        "them", "they", "this", "to", "was", "were", "with", "would", "you", "your",
        "now", "then", "there", "here", "company", "team", "founder", "crew", "people",
    }
    signal_words = {
        "collision", "danger", "deadliest", "disaster", "discovered", "emergency", "explosion",
        "flooded", "impact", "lifeboat", "rescue", "revealed", "sank", "sinking", "struck",
        "survived", "warning", "wreck", "breakthrough", "record", "secret", "collapse", "victims",
        "launched", "vanished", "escaped", "evacuated", "million", "billion", "percent",
    }
    if source_words and emphasis_words:
        source_lower = [word.lower() for word in source_words]
        for span_size in range(min(4, len(emphasis_words)), 0, -1):
            needle = [word.lower() for word in emphasis_words[:span_size]]
            for start in range(0, max(0, len(source_lower) - span_size + 1)):
                if source_lower[start:start + span_size] == needle:
                    candidate = source_words[start:start + span_size]
                    if any(word.lower() not in low_value for word in candidate):
                        grounded = " ".join(candidate)
                        break
            if grounded:
                break
    if not grounded and source_words:
        candidates = [
            (
                (4 if re.search(r"\d|%", word) else 0)
                + (3 if word.lower() in signal_words else 0)
                + (2 if word[:1].isupper() else 0),
                -index,
                word,
            )
            for index, word in enumerate(source_words)
            if word.lower() not in low_value
            and (re.search(r"\d|%", word) or word.lower() in signal_words or word[:1].isupper())
        ]
        if candidates:
            grounded = max(candidates)[2]
        else:
            # Every card still needs a grounded token when the narration has
            # no number, power word, or named entity. This is preferable to
            # silently emitting an empty overlay after an LLM emphasis miss.
            grounded = next(
                (word for word in source_words if word.lower() not in low_value),
                "",
            )
    grounded = " ".join(grounded.split()[:4])[:48]

    # Base props use the same grounded phrase everywhere; no invented detail.
    props = {
        "text": grounded,
        "detail": "",
        "title": grounded,
        "kicker": "",
        "subtitle": "",
        "label": "",
        "value": "",
        "stamp": "",
    }

    # Role-based prop filling
    if role == "intro":
        props["kicker"] = ""
        props["subtitle"] = ""
    elif role == "statistic":
        # Try to extract numbers from narration
        import re
        nums = re.findall(r'[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|thousand|%|percent))?',
                          narration_text, re.IGNORECASE)
        if nums:
            props["value"] = nums[0].strip()
        else:
            props["value"] = grounded
        props["label"] = grounded
    elif role == "quote":
        props["quote"] = grounded
        props["attribution"] = ""
    elif role == "cta":
        props["title"] = grounded or "FOLLOW THE STORY"
        props["subtitle"] = ""
    elif role == "chapter":
        props["kicker"] = ""
        props["title"] = grounded
        props["subtitle"] = ""
    elif highlight_kind == "scale":
        import re
        nums = re.findall(r'[\d,]+(?:\.\d+)?', narration_text)
        props["value"] = nums[0] if nums else grounded
        props["label"] = grounded
    elif highlight_kind == "warning":
        props["stamp"] = "WARNING"
        props["label"] = ""
    elif highlight_kind == "milestone":
        import re
        dates = re.findall(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,?\s*\d{4})?\b',
                          narration_text, re.IGNORECASE)
        props["date"] = dates[0] if dates else grounded
        props["kicker"] = ""
    elif highlight_kind == "location":
        props["location"] = grounded
        props["detail"] = ""
    elif highlight_kind == "human_stakes":
        props["quote"] = grounded
        props["attribution"] = ""
    elif highlight_kind == "document":
        props["stamp"] = "EVIDENCE"
        props["label"] = ""
    elif highlight_kind == "signal":
        props["message"] = grounded
        props["label"] = ""
    elif highlight_kind == "archive":
        props["stamp"] = "ARCHIVE"
        props["subtitle"] = ""

    # Hard semantic props are recovered from the narration even when an LLM
    # supplied only a role or a generic emphasis.
    date_match = re.search(
        r"\b(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+(?:18|19|20)\d{2})?|(?:18|19|20)\d{2})\b",
        source_text,
        re.I,
    )
    if treatment == "date" or date_match:
        date_value = date_match.group(0).upper() if date_match else str(grounded or "DATE").upper()
        props.update({"date": date_value, "value": date_value, "label": date_value, "title": ""})
    if treatment == "listicle":
        items = [
            item.strip(" -–—,.;:").upper()
            for item in re.split(r"(?:;|,\s+(?:and|or)\s+|\band\b)", source_text)
            if len(item.strip()) >= 4
        ][:3]
        props.update({
            "line1": items[0] if items else "KEY POINT",
            "line2": items[1] if len(items) > 1 else "WHAT HAPPENED",
            "line3": items[2] if len(items) > 2 else "WHY IT MATTERED",
        })

    return props


def route_scene(
    scene: dict,
    profile_template_map: Optional[Dict[str, str]] = None,
    used_recent: Optional[List[str]] = None,
    scene_index: int = 0,
) -> dict:
    """Route a full scene: return (template_id, props) pair.

    Input scene can have:
      - graphic.template_id + graphic.props (from semantic engine)
      - beat: {emphasis, treatment, highlight_kind, role, detail}
      - narration: text string
    """
    used_recent = used_recent or []

    # Check if scene already has a template assignment
    graphic = scene.get("graphic", {})
    if graphic.get("template_id"):
        template_id = graphic["template_id"]
        props = graphic.get("props", {})
    else:
        # Route from beat
        beat = scene.get("beat", {})
        narration = scene.get("narration", "")
        template_id = route_beat(beat, profile_template_map, used_recent, scene_index, narration_text=narration)
        props = build_props_from_beat(beat, narration)

    # Pass profile fonts to props
    if profile_template_map:
        for key in ["_font", "_body_font", "_accent_font"]:
            if key in profile_template_map:
                props[key] = profile_template_map[key]

    return {"template_id": template_id, "props": props}
