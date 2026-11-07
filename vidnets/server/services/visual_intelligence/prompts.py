"""Qwen Vision prompts for the Visual Intelligence Pipeline.

Single-image and multi-frame (clip) prompts with strict JSON-only output
contracts. Tuned for Qwen 2.5 VL: short, explicit, with a forced JSON schema
to keep token usage low on the Flash tier.
"""
from __future__ import annotations

from typing import List


# =============================================================================
# IMAGE INTELLIGENCE PROMPT
# =============================================================================

IMAGE_SYSTEM_PROMPT = (
    "You are a documentary image selector. Analyze the provided image and "
    "return STRICT JSON only. No markdown, no prose. Evaluate against the "
    "intended scene topic. Be honest and consistent: 0 means absent/poor, "
    "100 means excellent. Never exceed 100 or go below 0."
)

IMAGE_USER_TEMPLATE = (
    "Scene topic: {topic}\n"
    "Scene narration: {narration}\n"
    "Documentary subject: {subject}\n\n"
    "Return this JSON object with numeric values from 0 to 100:\n"
    "{{\"relevance\":0,\"quality\":0,\"historical_match\":0,\"composition\":0,"
    "\"watermark\":false,\"has_text\":false,\"unsafe_content\":false,"
    "\"suggestive_content\":false,\"synthetic_content\":false,"
    "\"overlay_type\":\"none|subtitle|lower_third|creator_logo|ownership_watermark|other\","
    "\"overlay_regions\":[{{\"type\":\"subtitle|lower_third|creator_logo|ownership_watermark|title_card|ticker|other\",\"bbox\":[0,0,1,1],\"frame_indices\":[0],\"confidence\":0,\"moving\":false}}],"
    "\"fatal_mismatch\":false,\"confidence\":0,\"final_score\":0,"
    "\"reason\":\"short reason\",\"scene_description\":\"visible content\"}}\n"
    "Set fatal_mismatch when the main person, event, action, era, or location contradicts the narration. "
    "For a specifically dated historical event, same-team modern footage is a wrong era, not an archival near-match. "
    "Use historical_match below 50 and fatal_mismatch=true when the image visibly belongs to a different period; when the period is genuinely uncertain, do not invent a match. "
    "Set unsafe_content for adult/NSFW content, suggestive_content for sexually suggestive framing, and synthetic_content for AI, CGI, 3D renders, illustrations, cartoons, or anime. Treat creator logos and ownership marks as watermarks.\n"
    "Return JSON only."
)


# =============================================================================
# CLIP INTELLIGENCE PROMPT (multi-frame)
# =============================================================================

CLIP_SYSTEM_PROMPT = (
    "You are a professional documentary editor. You are given several frames "
    "extracted from the same video clip in chronological order. Return STRICT "
    "JSON only. No markdown, no prose. Judge the clip as a whole, not each "
    "frame. Be consistent: 0 means absent/poor, 100 means excellent."
)

CLIP_USER_TEMPLATE = (
    "Scene topic: {topic}\n"
    "Scene narration: {narration}\n"
    "Documentary subject: {subject}\n"
    "Number of frames: {frame_count} (in chronological order)\n\n"
    "Return this JSON object with numeric values from 0 to 100:\n"
    "{{\"scene_description\":\"visible action\",\"subjects\":[],"
    "\"story_relevance\":0,\"visual_quality\":0,\"documentary_value\":0,"
    "\"emotional_score\":0,\"cinematic_score\":0,\"watermark\":false,"
    "\"subtitle\":false,\"has_text_overlay\":false,\"talking_head\":false,\"is_animation_or_cartoon\":false,"
    "\"overlay_type\":\"none|subtitle|lower_third|creator_logo|ownership_watermark|other\","
    "\"overlay_regions\":[{{\"type\":\"subtitle|lower_third|creator_logo|ownership_watermark|title_card|ticker|other\",\"bbox\":[0,0,1,1],\"frame_indices\":[0,1],\"confidence\":0,\"moving\":false}}],"
    "\"fatal_mismatch\":false,\"confidence\":0,\"final_score\":0}}\n"
    "THEMATIC / NEAR-MATCH MANDATE FOR FOOTAGE: Accept genuinely relevant b-roll for a general topic beat. A named, dated historical event has a strict era contract: present-day footage of the same team/person/topic is not an archival near-match; set fatal_mismatch=true when the visible era conflicts. Do NOT fail a correctly period-matched clip merely because it shows a different moment within the same event. For scenes without a specific event/date, ordinary uniform or era differences remain acceptable. "
    "Set fatal_mismatch=true when frames show kids or amateurs playing in a park, video game simulations (FIFA/EAFC/PES/Roblox), 3D renders, or generic stock when authentic professional footage of the subject is required. "
    "Set is_animation_or_cartoon=true and fatal_mismatch=true when frames show 2D/3D cartoon animation, anime, drawings, or animated graphics instead of real camera footage. "
    "Treat creator logos and ownership marks as watermarks.\n"
    "Set talking_head=true when the frames feature any person speaking directly to the camera "
    "(news anchor, news desk, interviewer, interviewee talking to camera, commentator, vlog, streamer webcam/facecam, reaction, podcast host, or studio presenter) instead of "
    "documentary b-roll, archival footage, or real footage of the subject itself. A crowd, a person "
    "acting within a scene, an athlete playing, gameplay characters, or archival footage of a named historical figure doing actions is NOT a talking head. "
    "A small persistent webcam/facecam box over gameplay is talking_head=true even when most of the frame is gameplay.\n"
    "Return JSON only."
)


def build_image_prompt(topic: str, narration: str, subject: str = "") -> str:
    return IMAGE_USER_TEMPLATE.format(
        topic=(topic or "").strip() or "documentary",
        narration=(narration or "").strip()[:400],
        subject=(subject or "").strip() or "unspecified",
    )


def build_clip_prompt(topic: str, narration: str, subject: str = "", frame_count: int = 0) -> str:
    return CLIP_USER_TEMPLATE.format(
        topic=(topic or "").strip() or "documentary",
        narration=(narration or "").strip()[:400],
        subject=(subject or "").strip() or "unspecified",
        frame_count=int(frame_count or 0),
    )


# Keys we expect Qwen to return. Used to coerce partial/extra responses.
IMAGE_EXPECTED_KEYS = (
    "relevance", "quality", "historical_match", "composition",
    "watermark", "has_text", "final_score", "reason",
    "unsafe_content", "suggestive_content", "synthetic_content",
    "scene_description", "motion_effect", "text_safe_area",
    "overlay_type", "fatal_mismatch", "confidence",
)
CLIP_EXPECTED_KEYS = (
    "scene_description", "category", "subjects", "shot_type",
    "story_relevance", "visual_quality", "documentary_value",
    "emotional_score", "cinematic_score",
    "watermark", "subtitle", "has_text_overlay", "talking_head",
    "overlay_type", "fatal_mismatch", "confidence",
    "best_usage", "recommended_duration", "transition",
    "caption_position", "music", "camera_movement", "final_score",
)
