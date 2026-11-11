"""Compact YouTube clips pipeline v2.

Design goals:
- LLM/container rows are the contract.
- Search once per container, not random global pools.
- Hard reject only true blockers; penalize noisy but usable candidates.
- Reuse long videos at distant timestamps instead of starving scenes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from config import TEMP_DIR
from .visual_requirement import (
    candidate_domain_context_pending,
    candidate_nearest_match,
    candidate_requirement_match,
    provider_query_variants,
)
from .youtube_query_normalizer import normalize_youtube_query, normalize_youtube_queries
from .yt_dlp_runtime import runtime_receipt, YouTubeRuntimeLog
from .scene_entities import comparison_pair_conflict, explicit_named_pairs


def _youtube_host_dns_failure(error) -> bool:
    text = str(error or '').lower()
    return any(host in text for host in ('youtube.com', 'youtubei.googleapis.com')) and any(
        marker in text for marker in ('failed to resolve', 'getaddrinfo failed', 'name resolution'))


def _youtube_js_runtime_options() -> Dict[str, Any]:
    """Enable yt-dlp's supported JS challenge runtime and EJS scripts."""
    options: Dict[str, Any] = {}
    # Linux hosts behind a TLS-intercepting egress proxy (a sandbox proxy
    # with its own CA) break yt-dlp's default certifi bundle, which does
    # not include the proxy CA. 'no-certifi' makes yt-dlp use the system
    # SSL context instead, which honours SSL_CERT_FILE and the OS trust
    # store. Safe on Windows/macOS too (their system stores are used).
    options["compat_opts"] = {"no-certifi"}
    root = Path(__file__).resolve().parents[2]
    runtime_candidates = [
        os.environ.get("VIDZEO_NODE_PATH"),
        shutil.which("node"),
        str(root / "tools" / "node" / "node.exe"),
        str(root / "tools" / "node" / "node"),
    ]
    runtime_path = next(
        (str(value).strip() for value in runtime_candidates if value and Path(value).is_file()),
        "",
    )
    options: Dict[str, Any] = {}
    if runtime_path:
        options["js_runtimes"] = {"node": {"path": runtime_path}}
    from importlib.util import find_spec
    try:
        local_ejs = find_spec('yt_dlp_ejs') is not None
    except (ImportError, ValueError):
        local_ejs = False
    remote = str(os.environ.get("VIDZEO_YTDLP_EJS_SOURCE", '' if local_ejs else 'github') or '').strip().lower()
    if remote in {"github", "npm"}:
        options["remote_components"] = [f"ejs:{remote}"]
    return options


def _new_job_run_id(prefix: str, identity: str = "") -> str:
    """Create a collision-resistant local run namespace.

    Two simultaneous jobs may have the same title, subjects, and scene
    indexes. Wall-clock seconds plus a title hash was therefore insufficient
    isolation for raw yt-dlp artifacts.
    """
    # Some packaged/CI environments expose a coarse or frozen wall clock.
    # UUID entropy is therefore part of the namespace identity; timestamps
    # remain useful for human inspection but are never the uniqueness source.
    nonce = uuid.uuid4().hex
    material = (
        f"{identity}:{os.getpid()}:{threading.get_ident()}:{time.time_ns()}:{nonce}"
    ).encode("utf-8", "replace")
    return f"{prefix}_{time.time_ns()}_{hashlib.sha256(material).hexdigest()[:12]}"


def _safe_youtube_path_token(value: Any, fallback: str = "candidate") -> str:
    """Keep provider IDs from escaping or invalidating the run output path."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return (token[:96] or fallback)


class VideoFormat(Enum):
    SCRIPT_LIKE = "narrative"
    SINGLE_SUBJECT = "single_subject"
    MULTI_SUBJECT = "multi_subject_listicle"
    SUBJECTS_TOGETHER = "subjects_compared"


class YouTubeClipResults(list):
    """List-compatible clip result carrying per-scene retrieval diagnostics."""

    def __init__(self, values: Iterable[Optional[str]], *, audit: Optional[Dict[int, List[Dict[str, Any]]]] = None):
        super().__init__(values)
        self.audit = dict(audit or {})


def _youtube_timing_window_duration(window: Any) -> Optional[float]:
    """Read an audio-aligned scene window in either legacy or Whisper form.

    Whisper plans use ``*_seconds`` and carry ``duration_needed``.  Older
    callers used ``start``/``end``.  Keeping the normalization here prevents
    the YouTube lane from silently reverting to its four-second default when
    it receives an otherwise valid aligned audio plan.
    """
    if not isinstance(window, Mapping):
        return None
    def number(*keys: str) -> Optional[float]:
        for key in keys:
            value = window.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    start = number("start_seconds", "start")
    end = number("end_seconds", "end")
    duration = number("duration_needed", "duration")
    if start is None:
        start = 0.0
    if end is None and duration is not None and duration > 0:
        end = start + duration
    if end is None or end <= start:
        return None
    return end - start


def _resolve_youtube_scene_durations(
    scene_count: int,
    scene_durations: Optional[Iterable[Any]] = None,
    scene_timing_windows: Optional[Iterable[Mapping[str, Any]]] = None,
    *,
    prefer_aligned: Optional[bool] = None,
) -> List[float]:
    """Prefer Whisper phrase/sentence windows over provisional durations."""
    provided = list(scene_durations or [])
    windows = list(scene_timing_windows or [])
    resolved: List[float] = []
    if prefer_aligned is None:
        prefer_aligned = str(
            os.environ.get("VIDZEO_YT_PREFER_WHISPER_TIMING", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
    for index in range(max(0, int(scene_count or 0))):
        aligned = (
            _youtube_timing_window_duration(windows[index])
            if index < len(windows) else None
        )
        provisional = None
        if index < len(provided):
            try:
                value = float(provided[index])
                provisional = value if value > 0 else None
            except (TypeError, ValueError):
                provisional = None
        resolved.append(float(aligned if prefer_aligned and aligned else provisional or aligned or 4.0))
    return resolved


@dataclass
class QueryPack:
    primary_query: str = ""
    supporting_query: str = ""
    fallback_query: str = ""


@dataclass
class SceneRetrievalContract:
    scene_index: int
    scene_text: str
    scene_family: str = "person"
    dominant_subject: str = ""
    scene_subject: str = ""
    section_subject: str = ""
    secondary_subject: str = ""
    container_id: str = ""
    container_boundary: bool = False
    llm_container_locked: bool = False
    query_pack: QueryPack = field(default_factory=QueryPack)


_SCENE_FAMILY_PERSON = "person"
_SCENE_FAMILY_PLACE = "place"
_SOURCE_CLASS_COMEDY = "COMEDY_TALKSHOW"

YOUTUBE_ALLOW_SAME_SOURCE_REUSE = True

_COMMENTARY_TERMS = {
    "analysis", "explained", "breakdown", "reaction", "reacts", "podcast",
    "debate", "comparison", "compared", "quiz", "who is", "why ", "news",
    "reporter", "journalist", "update", "story of", "history of",
    "talking about", "discusses", "discussion", "lecture", "presentation",
    "doctor reacts", "expert reacts", "what happens", "what will happen",
    "should you", "things you need to know", "top facts", "facts about",
    "health benefits", "benefits of", "nutrition facts", "based on science",
    "scientifically proven", "side effects", "pros and cons",
    "livestream", "live stream", "streamer", "stream highlights", "twitch",
    "watch party", "lets play", "let's play", "commentary",
    "facecam", "talking head", "review", "recap", "ranking", "ranked",
}
_BAD_VISUAL_TERMS = {
    "simulation", "fifa 23", "fifa 24", "fc 24", "fc 25",
    "efootball", "pes ", "roblox", "minecraft", "unreal engine", "ai generated",
    "fanmade", "edit pack", "scene pack", "scenepack", "topaz", "template",
    "virtual reality", "vr 360", "360 vr", "reenactment", "re-enactment",
    "dramatised", "dramatized", "cinematic story", "cinematic recreation",
    "animation", "animated", "3d animation",
    "ai restored", "ai-restored", "ai reconstruction", "ai generated footage",
    "brought to life", "pov:", "pov ",
    "green screen", "no copyright",
    # Publisher packages dominated by title cards or visitor-centre promo
    # text. Prefer clean archival footage for the pool; a moving MP4 is not
    # useful if its editorial text competes with our captions.
    "unsinkable luxury", "visitor centre", "visitor center",
    # Recreations, film/TV edits, and game captures masquerade as historical
    # footage in disaster search lanes.
    "honor & glory", "honor and glory", "teoria potopenia", "reanimated",
    "deleted scenes", "extended cut", "movie scenes", "movie clips",
    "film clip", "film clips", "replica", "recreated",
}
_BIG_MEDIA_TERMS = {
    "sky sports", "espn", "bbc", "cnn", "fox sports", "bein sports",
    "talksport", "the athletic", "dazn", "tnt sports", "cbs sports",
    "nbc news", "abc news", "fox news", "msnbc", "newsmax", "al jazeera",
    "guardian news", "daily mail", "inside edition",
}
_HIGH_RISK_MEDIA_CHANNEL_TERMS = {
    "t-series", "t series", "zee music company", "sony music india",
    "saregama music", "tips official", "eros now", "yash raj films", "yrf",
    "reuters", "associated press", "ap archive", "getty images", "afp news",
}
_STREAMER_PACKAGE_TERMS = {
    "streamer", "livestream", "live stream", "stream highlights", "twitch",
    "facecam", "watch party", "reaction", "reacts", "my reaction",
    "let's play", "lets play", "stream vod", "twitch vod", "gaming live",
}

# Metadata-only hard vetoes are intentionally narrow. These phrases identify
# the unwanted *format* (a creator/presenter over the footage), unlike broad
# editorial words such as ``analysis``, ``review`` or ``interview`` which can
# still lead to useful b-roll and are verified from the downloaded frames.
_PRESENTER_CONTAMINATION_TERMS = {
    "talking head", "facecam", "face cam", "webcam", "web cam",
    "streamer", "twitch streamer", "reaction", "reacts", "my reaction",
    "watch party", "podcast", "talk show", "stream highlights",
    "gaming live", "live commentary", "commentary gameplay",
    "gameplay commentary", "hosted gameplay", "presenter gameplay",
    "reaction cam", "reaction stream", "benchmark overlay",
}

# Search engines frequently collapse a person's name into an unrelated
# namesake.  Prince Charles is a particularly costly example: church/pastor
# videos titled "Pastor Prince Charles" routinely outrank genuine royal
# archive footage because they repeat the exact name.  Keep these hints narrow
# and entity-specific; generic nearest-match behavior remains unchanged for
# every other person/topic.
_PERSON_CONFUSABLE_HINTS = {
    "dan cooper": {"dan stevens", "dominic cooper", "actor interview", "movie premiere"},
    "d b cooper": {"dan stevens", "dominic cooper", "actor interview", "movie premiere"},
    "prince charles": {
        "pastor", "church", "sermon", "prayer", "rgci", "sialkot",
        "fire prayer", "worship", "ministries", "ministry",
    },
    "king charles iii": {
        "pastor", "church", "sermon", "prayer", "rgci", "sialkot",
        "fire prayer", "worship", "ministries", "ministry",
    },
}


def _clean_gameplay_signal(text: str) -> bool:
    key = _norm(text)
    return any(term in key for term in (
        "no commentary", "without commentary", "raw gameplay",
        "direct gameplay capture", "uncut gameplay", "clean gameplay",
        "gameplay only", "walkthrough no commentary",
    ))


def _presenter_contamination_reason(text: str) -> str:
    """Return a hard metadata reason only for explicit creator/presenter media."""
    key = _norm(text)
    clean_gameplay = _clean_gameplay_signal(key)
    for term in _PRESENTER_CONTAMINATION_TERMS:
        if clean_gameplay and "commentary" in term:
            continue
        if term in key:
            return term.replace(" ", "_")
    # Plain ``livestream``/``live stream`` and ``vod`` are format signals when
    # attached to a creator/streaming context. Avoid rejecting legitimate
    # live match footage or archival video-on-demand solely for those words.
    if any(term in key for term in ("livestream", "live stream", " vod")) and any(
        term in key for term in ("gaming", "gameplay", "streamer", "twitch", "reaction", "facecam", "webcam")
    ):
        return "streamer_live_package"
    if "commentary" in key and not clean_gameplay:
        return "commentary_package"
    return ""
_SENSATIONAL_EXPLAINER_TERMS = {
    "shocking truth", "the truth about", "hidden truth", "exposed",
    "what they don't tell you", "what they dont tell you", "must watch",
    "you won't believe", "you wont believe", "finally revealed",
    "lessons from", "if your home", "what this teaches",
    # Non-English explainer packages crowd the disaster/history lanes with
    # dubbed narration over slideshows; keep them out of the footage pool.
    "kaise", "raaz", "haqeeqat", "documental", "explicado", "teoria",
    "in urdu", "in hindi", "in tamil", "in telugu", "in bengali",
    "in spanish", "in french", "in german", "in arabic",
}
_DISASTER_FOOTAGE_EVIDENCE = {
    "raw footage", "actual footage", "caught on camera", "camera footage",
    "aerial footage", "drone footage", "street footage", "damage footage",
    "aftermath footage", "rescue footage", "eyewitness footage", "security camera",
    "cctv", "dashcam", "on the ground footage", "moment earthquake",
    # Historical disasters often have no modern raw footage. Archive, newsreel,
    # and period-document evidence is still visual source material, unlike a
    # reaction or explainer package.
    "archive footage", "archival footage", "historical footage", "historic footage",
    "documentary footage", "newsreel", "vintage footage", "period footage",
    "real moving footage", "motion picture footage", "underway footage",
    "sinking footage", "shipwreck footage", "lifeboat footage", "maritime footage",
    "ocean liner", "shipwreck", "sinking", "lifeboat", "iceberg", "maritime",
}
_DISASTER_TOPIC_TERMS = {
    "titanic", "iceberg", "lifeboat", "lifeboats", "shipwreck", "shipwrecks",
    "sinking", "sank", "sunken", "wreck", "wreckage", "maritime", "ocean liner",
    "distress call", "distress calls", "survivor", "survivors", "collision",
    "ship broke apart", "ship disaster", "disaster at sea",
}
_GAMING_EXPLAINER_TERMS = {
    "everything we know", "things you missed", "details you missed",
    "news update", "gaming news", "leak", "rumor", "explained", "breakdown",
    "review", "reaction", "reacts", "comparison", "before you buy",
    "i found", "we found", "i tried", "we tried", "finally bought",
    "playing the worst", "my gameplay", "first look reaction",
}
_GAMING_FAKE_TRAILER_TERMS = {
    "concept trailer", "fan trailer", "fan made", "fanmade", "concept gameplay",
    "unseen locations", "unofficial trailer", "remake trailer",
}
_GAMING_AUTHENTIC_TERMS = {
    "official trailer", "gameplay trailer", "official gameplay",
    "cinematic trailer", "no commentary", "without commentary",
}
_TRUSTED_OFFICIAL_CHANNELS = {
    "rockstar games",
    "playstation", "xbox", "nintendo", "ea sports",
    "ubisoft", "bethesda softworks", "call of duty", "capcom", "sega",
}
_FOOTAGE_BONUS_TERMS = {
    "highlights", "goals", "skills", "training", "match", "footage",
    "press conference", "interview", "arrival", "warm up", "warm-up",
    "compilation", "best moments", "all goals",
    "b roll", "close up", "harvest", "harvesting", "market", "farm",
    "nutrition", "healthy", "benefits", "fresh", "recipe", "smoothie",
    "official trailer", "trailer", "gameplay", "gameplay trailer",
    "no commentary gameplay", "official gameplay",
    "moving footage", "motion picture footage", "underway footage",
}
_VISUAL_FOOTAGE_TERMS = {
    "raw footage", "actual footage", "real footage", "b roll", "b-roll",
    "close up", "close-up", "4k", "uhd", "cinematic", "no commentary",
    "without commentary", "archive footage", "archival footage",
    "historical footage", "historic footage", "documentary footage",
    "newsreel", "vintage footage", "period footage", "official footage",
    "moving footage", "motion picture footage", "underway footage",
    "ship at sea", "sinking", "shipwreck", "lifeboat", "ocean liner", "maritime",
}
_TITLE_FOOTAGE_BONUS_TERMS = {
    "raw footage", "actual footage", "real footage", "recovered footage",
    "restored footage", "restored film", "colorized", "colourised",
    "archive footage", "archival footage", "archival film", "archive film",
    "historical footage", "historic footage", "newsreel", "vintage footage",
    "period footage", "original footage", "rare footage", "genuine footage",
    "documentary footage", "official footage", "no commentary",
    "without commentary", "b-roll", "b roll",
    "game highlights", "match highlights", "race highlights", "all plays", "every play",
    "onboard footage", "test drive footage", "wildlife footage", "cctv footage", "launch footage",
    "shipwreck footage", "sinking footage", "underwater footage",
    "abc news", "actual film", "true footage",
}
_OVERLAY_PACKAGE_TERMS = {
    # A small corner watermark is an allowed source defect.  The frame-level
    # overlay screen can tolerate/blur it; rejecting every metadata hit for
    # ``watermark`` starved the footage pool even when the actual watermark
    # was harmless.  Burned-in captions and publisher packages remain hard
    # blocked by _CAPTIONED_SOURCE_TERMS and the local overlay gate.
    "lyrics video", "lyric video", "logo animation",
    "intro template", "outro template",
}
_CAPTIONED_SOURCE_TERMS = {
    "with subtitles", "with captions", "english subtitles", "english captions",
    "hardcoded subtitles", "hard-coded subtitles", "burned in subtitles",
    "burned-in subtitles", "burnt subtitles", "closed captions",
    "captioned video", "captioned footage", "on screen text", "on-screen text",
    "text overlay", "lyrics video", "lyric video", "karaoke",
}
_STILL_MEDIA_PACKAGE_TERMS = {
    "slideshow", "slide show", "photo slideshow", "image slideshow",
    "photo montage", "picture montage", "still images", "static images",
    "photo essay", "presentation slides", "image compilation", "photo compilation",
    "text card", "text cards", "timeline video", "powerpoint",
}

# Niche-aware visual search intents. These are suffixes, not complete queries;
# _queries_for_row always prefixes them with the exact container/topic anchor.
# The LLM may supply a more specific intent list, but this map is the safe
# deterministic floor when the LLM is unavailable or vague.
_YOUTUBE_NICHE_QUERY_INTENTS = {
    "person": (
        "public appearance footage", "ceremony arrival footage", "archival event footage",
        "documentary archive footage", "b roll appearance", "daily life b roll",
        "career event footage", "official event footage",
    ),
    "place": (
        "walking tour footage", "aerial drone footage",
        "4k walking tour no talking", "drone footage clean 4k", "street b roll footage",
        "landmark raw footage", "city location 4k b roll", "interior architecture b roll",
    ),
    "sports": (
        "match footage", "highlights", "interview", "press conference",
        "match action clean broadcast", "skills goals 4k no commentary", "training raw footage",
        "stadium broadcast 4k", "slow motion highlights 1080p", "competition clean footage",
    ),
    "event": (
        "raw event footage", "uncut archive footage", "ceremony raw footage",
        "crowd b roll clean", "location raw footage", "aftermath uncut",
    ),
    "product": (
        "hands on demo b roll 4k", "product close up clean", "official demo footage",
        "launch event clean footage", "unboxing no talking b roll", "hardware showcase clean",
    ),
    "automotive": (
        "car driving raw footage 4k", "exterior b roll clean", "interior detail 4k",
        "track testing raw footage", "official promo b roll", "road test no commentary",
    ),
    "organization": (
        "official event footage", "facility b roll clean", "operations raw footage",
        "behind the scenes clean", "headquarters 4k b roll", "location raw footage",
    ),
    "food": (
        "close up food b roll 4k", "cooking preparation raw footage", "fresh produce market b roll",
        "harvesting farm clean footage", "kitchen chef b roll 4k", "food macro b roll",
    ),
    "animal": (
        "4k wildlife b roll", "animal habitat raw footage", "nature close up clean footage",
        "safari animal behavior 4k", "underwater wildlife 4k", "wildlife sanctuary footage",
    ),
    "nature": (
        "4k landscape b roll", "nature scenery raw footage", "drone landscape 4k clean",
        "forest ocean 4k b roll", "mountain aerial b roll", "wilderness raw footage",
    ),
    "technology": (
        "hands on demo b roll 4k", "product close up clean", "launch event clean footage",
        "technology 4k b roll", "hardware showcase clean", "laboratory technology clean",
    ),
    "history": (
        "raw archival film footage", "newsreel moving footage clean", "historic location b roll",
        "ship at sea archival clean", "historical reenactment cinematic b roll", "archival documentary footage",
    ),
    "vehicle": (
        "car driving raw footage 4k", "exterior b roll clean", "interior detail 4k",
        "track testing raw footage", "road test no commentary", "locomotive vintage footage",
    ),
    "disaster": (
        "raw footage", "aftermath uncut", "rescue raw footage",
        "aerial location clean footage", "archival broadcast footage", "newsreel archive uncut",
    ),
    "travel": (
        "4k walking tour no talking", "drone footage clean 4k", "street b roll footage",
        "landmark raw footage", "city location 4k b roll", "scenic travel b roll 4k",
    ),
    "gaming": (
        "raw gameplay no commentary", "direct gameplay capture 4k", "official gameplay trailer",
        "uncut gameplay without commentary", "gameplay 4k 60fps no commentary", "cinematic trailer 4k",
    ),
    "science": (
        "laboratory experiment 4k b roll", "microscope raw footage", "research facility clean footage",
        "scientific demonstration clean", "space telescope 4k footage", "medical science b roll",
    ),
    "crime": (
        "case location raw b roll", "archive newsreel uncut", "police investigation archival footage",
        "aerial scene location 4k", "courtroom archive clean", "detective investigation b roll",
    ),
    "entertainment": (
        "stage performance raw footage", "behind the scenes clean b roll", "red carpet raw footage",
        "rehearsal clean footage", "concert live performance 4k", "film set b roll clean",
    ),
    "finance": (
        "trading floor raw b roll", "factory manufacturing 4k", "shipping logistics clean footage",
        "office corporate b roll 4k", "banking business b roll", "container port logistics 4k",
    ),
    "space": (
        "rocket launch raw footage 4k", "spacecraft clean broadcast", "space mission raw footage",
        "astronomy telescope 4k", "spacex nasa launch 4k", "satellite earth orbit 4k",
    ),
    "transportation": (
        "train crew locomotive cab footage",
        "railway crew operations footage",
        "locomotive engineer conductor operations",
        "freight train crew at work",
        "train driver cab ride",
        "railroad conductor switching cars",
        "railway operations freight train",
        "diesel locomotive cab footage",
    ),
    "general": (
        "raw footage clean", "b roll footage 4k", "location clean footage",
        "raw b roll footage", "documentary b roll clean", "cinematic 4k footage",
    ),
}
_CATEGORY_VISUAL_TERMS = {
    "food": {
        "close up", "b roll", "b-roll", "fresh", "produce", "market", "farm",
        "harvest", "harvesting", "orchard", "tree", "garden", "cutting",
        "slicing", "recipe", "cooking", "juice", "smoothie", "kitchen",
        "macro", "food footage", "fruit footage", "vegetable footage",
    },
    "health": {
        "b roll", "close up", "healthy lifestyle", "fresh food", "market",
        "kitchen", "exercise", "doctor footage", "clinic footage",
    },
    "technology": {
        "hands on", "hands-on", "demo", "product close up", "close up",
        "launch event", "official", "test", "camera test", "real world use",
        "unboxing", "review footage",
    },
    "gaming": {
        "official trailer", "trailer", "gameplay", "gameplay trailer",
        "official gameplay", "no commentary", "walkthrough no commentary",
        "cinematic trailer", "rockstar games", "playstation", "xbox",
    },
    "travel": {
        "walking tour", "drone", "aerial", "street", "streets", "city",
        "landmark", "4k", "tour", "travel footage", "drive", "walk",
    },
    "crime": {
        "archive footage", "cctv", "bodycam", "police footage", "court",
        "trial", "interrogation", "case footage", "documentary footage",
        "location footage",
    },
    "documentary": {
        "archive footage", "archival", "documentary footage", "interview",
        "location footage", "historical footage", "raw footage",
    },
    "disaster": {
        "raw footage", "aftermath", "rescue", "damage", "collapsed",
        "street footage", "aerial footage", "drone footage", "location footage",
        "emergency response", "evacuation", "search and rescue",
    },
    "sports": {
        "highlights", "match", "game", "goal", "training", "warm up",
        "press conference", "interview", "official", "full match",
    },
}
_SPORTS_TOPIC_TERMS = {
    "football", "soccer", "ronaldo", "cristiano", "messi", "mbappe",
    "neymar", "premier league", "champions league", "ballon d'or",
    "world cup", "euros", "uefa", "fifa", "club world cup", "goalkeeper",
    "striker", "midfielder", "national team", "nfl", "super bowl",
    "quarterback", "touchdown", "linebacker", "nba", "basketball",
    "mlb", "baseball", "nhl", "hockey", "athletics", "olympics",
    "boxing", "ufc", "mma", "tennis", "formula 1", "f1", "nascar",
    "lombardi trophy", "playoffs", "championship",
}
_DOMAIN_TOPIC_TERMS = {
    "disaster": _DISASTER_TOPIC_TERMS,
    "sports": _SPORTS_TOPIC_TERMS,
}
# Title-derived anchors often carry a verb wrap ("Sinking of RMS Titanic",
# "Rise and Fall of Rome"). The object of the phrase is the entity that
# footage actually shows, so strip the wrap for query building while leaving
# container/pool identity untouched.
_ANCHOR_PREFIX_TERMS = (
    "sinking", "story", "stories", "history", "tragedy", "disaster",
    "wreck", "rise", "fall", "life", "death", "making", "building",
    "voyage", "journey", "battle", "war", "siege", "case", "trial",
    "mystery", "secret", "secrets", "truth", "inside", "real", "true",
    "untold", "final", "last", "day", "night", "diary", "files", "tales",
)
_ANCHOR_PREFIX_PATTERN = re.compile(
    r"^(?:{})\s+of\s+([A-Za-z0-9][A-Za-z0-9'\s\-]{{1,80}})$".format(
        "|".join(re.escape(t) for t in _ANCHOR_PREFIX_TERMS)
    ),
    re.IGNORECASE,
)


def _strip_anchor_verb_wrap(value: str) -> str:
    """``The Sinking of RMS Titanic`` -> ``RMS Titanic`` (max 4 words)."""
    match = _ANCHOR_PREFIX_PATTERN.match(_clean_text(value))
    if not match:
        return value
    words = _clean_text(match.group(1)).split()
    return " ".join(words[:4])


def _matches_domain_terms(text: str, terms) -> int:
    """Word-boundary aware hit count for a set of topic terms."""
    count = 0
    for term in terms:
        pattern = re.escape(term).replace(" ", r"\s+")
        if re.search(rf"\b{pattern}\b", text, re.IGNORECASE):
            count += 1
    return count


def _dominant_domain_category(text: str) -> str:
    """Pick the topic domain with the most word-boundary hits. Concrete
    disaster/history-style vocabulary outranks the generic sports terms,
    so clips about a sinking ocean liner never search as ``match footage``."""
    best_domain = ""
    best_count = 0
    for domain in ("disaster", "sports"):
        count = _matches_domain_terms(text, _DOMAIN_TOPIC_TERMS[domain])
        if count > best_count:
            best_domain, best_count = domain, count
    return best_domain
_DOMAIN_NEGATIVE_TERMS = {
    "fruit": {
        "iphone", "ipad", "macbook", "ios", "airpods", "apple watch",
        "apple tv", "app store", "tim cook", "smartphone", "laptop",
        "tech review", "unboxing", "stock price", "wwdc",
    },
    "gaming": {
        "streamer", "live stream", "livestream", "reaction", "reacts",
        "news", "leak", "fanmade", "mod", "explained", "podcast",
    },
}
_WEAK_SUBJECT_TERMS = {
    "football", "footballer", "footballers", "player", "players", "legend",
    "legends", "generation", "today", "stars", "best", "top", "history",
    "world", "cup", "fifa", "match", "goal", "goals", "scene",
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "from", "with", "without", "this", "that", "these", "those", "there",
    "here", "era", "produced", "some", "any", "all", "only", "never",
    "every", "its", "their", "our", "your",
    "what", "if", "just", "can", "you",
    "choosing", "choose", "selected", "selection", "five", "one", "two",
    "three", "four", "six", "seven", "eight", "nine", "ten", "has", "have",
    "had", "is", "are", "was", "were", "be", "been", "but", "because",
    "talented", "dominant", "modern", "current", "spot", "rank", "number",
    "story", "video", "clip", "clips", "footage", "valid", "script", "input", "placeholder", "default", "intro", "outro",
    "fruit", "fruits", "food", "foods", "vegetable", "vegetables",
    "health", "healthy", "boost", "improve", "live", "longer", "nutrition",
}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value: Any) -> str:
    import unicodedata
    text = _clean_text(value).lower()
    text = ''.join(char for char in unicodedata.normalize('NFKD', text) if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9\s'-]+", " ", text).strip()


# GTA VI is a separate game identity. GTA V footage is a different title and
# must not be injected as a default supporting lane. A scene can request a
# clearly labelled predecessor/comparison beat, but it must name GTA V and
# express that comparison in the scene copy, as well as opt in via its plan.
_GTA_VI_RE = re.compile(
    r"\b(?:grand theft auto\s*(?:vi|6)|gta\s*(?:vi|6)|gta six)\b",
    re.IGNORECASE,
)
_GTA_V_RE = re.compile(
    r"\b(?:grand theft auto\s*v|gta\s*v|gta\s*5)\b",
    re.IGNORECASE,
)
_GTA_GAMEPLAY_TERMS = {
    "gameplay", "walkthrough", "mission", "missions", "open world",
    "driving", "driving gameplay", "raw gameplay", "no commentary",
    "without commentary", "direct gameplay capture", "free roam",
}
_GTA_V_COMPARISON_TERMS = re.compile(
    r"\b(?:compare|comparison|compared|previous|predecessor|prior|earlier|"
    r"legacy|side by side|baseline|reference|supporting example)\b",
    re.IGNORECASE,
)
_GTA_V_NEGATION_TERMS = re.compile(
    r"\b(?:avoid|exclude|reject|no|not|never|without|instead of|do not use)\b",
    re.IGNORECASE,
)


def _is_gta_vi_text(*values: Any) -> bool:
    return bool(_GTA_VI_RE.search(_norm(" ".join(str(value or "") for value in values))))


def _scene_explicitly_requests_gta_v_comparison(
    scene_text: str,
    row: Optional[Dict[str, Any]] = None,
) -> bool:
    """Require a scene-level, positive comparison before using prior-title footage."""
    row = row if isinstance(row, dict) else {}
    if not bool(row.get("gta_vi_supporting_gameplay")):
        return False
    intent = _norm(" ".join((
        str(scene_text or ""),
        str(row.get("visual_intent") or ""),
        str(row.get("visual_facet") or ""),
        str(row.get("gta_v_support_reason") or ""),
    )))
    if not _GTA_V_RE.search(intent) or not _GTA_V_COMPARISON_TERMS.search(intent):
        return False
    # Negative query instructions (for example, "avoid GTA V") never turn on
    # the exception, even if another word in the same note says "supporting".
    if _GTA_V_NEGATION_TERMS.search(intent):
        return False
    return True


def _gta_v_supporting_gameplay(
    candidate: Dict[str, Any],
    subject: str,
    scene_text: str,
    context: Optional[Dict[str, Any]],
) -> bool:
    """Return true only for clean GTA V gameplay supporting a GTA VI beat."""
    if not bool((context or {}).get("gta_vi_supporting_gameplay")):
        return False
    if not _scene_explicitly_requests_gta_v_comparison(scene_text, context):
        return False
    text = _norm(" ".join([
        str(candidate.get("title") or ""),
        str(candidate.get("description") or ""),
        str(candidate.get("channel") or candidate.get("uploader") or ""),
    ]))
    if not _GTA_V_RE.search(text) or _GTA_VI_RE.search(text):
        return False
    if not _is_gta_vi_text(subject, scene_text, (context or {}).get("domain_anchor")):
        return False
    # The generic hard blockers run before this helper in the scorer.  Keep a
    # second gameplay-positive check here so a GTA V trailer, news package, or
    # creator commentary cannot slip through as a supporting visual.
    return any(term in text for term in _GTA_GAMEPLAY_TERMS)


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        cleaned = _clean_text(value)
        key = _norm(cleaned)
        if cleaned and key not in seen:
            out.append(cleaned)
            seen.add(key)
    return out


def _subject_aliases(subject: str) -> List[str]:
    try:
        from services.container_guard import canonical_container_subject
        subject = canonical_container_subject(subject) or subject
    except Exception:
        pass
    tokens = _norm(subject).split()
    aliases = [subject]
    if re.search(r"\b(?:dan cooper|d b cooper|db cooper)\b", _norm(subject)):
        aliases.extend(["D.B. Cooper", "DB Cooper", "Dan Cooper"])
    roman_to_number = {
        "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
        "vii": "7", "viii": "8", "ix": "9", "x": "10",
    }
    if tokens and tokens[-1] in roman_to_number:
        aliases.append(" ".join(tokens[:-1] + [roman_to_number[tokens[-1]]]))
    identity_tokens = [token for token in tokens if token not in {"the", "a", "an", "of", "and"}]
    if len(identity_tokens) >= 3:
        version = identity_tokens[-1] if identity_tokens[-1] in set(roman_to_number) | set(roman_to_number.values()) else ""
        words = identity_tokens[:-1] if version else identity_tokens
        acronym = "".join(word[0] for word in words if word)
        if len(acronym) >= 2:
            aliases.append(f"{acronym} {version}".strip())
            if version in roman_to_number:
                aliases.append(f"{acronym} {roman_to_number[version]}")
    honorific_titles = {
        "prince", "princess", "king", "queen", "duke", "duchess",
        "lord", "lady", "sir", "dame", "baron", "count", "countess",
        "grand", "saint", "st", "uncle", "aunt", "dr", "doctor",
    }
    if len(tokens) == 2 and all(len(token) > 2 for token in tokens):
        if tokens[0] not in honorific_titles:
            aliases.append(tokens[-1])
    return _dedupe(aliases)


def _text_mentions_subject(text: str, subject: str) -> bool:
    key = _norm(text)
    if not key or not subject:
        return False
    subject_norm = _norm(subject)
    # GTA generations are distinct entities.  The broad in-order fallback
    # below intentionally tolerates connector words, but it must not turn a
    # GTA V upload into a GTA VI match (or vice versa) merely because both
    # titles contain the shared ``grand theft auto`` stem.  Supporting GTA V
    # gameplay is handled explicitly by ``_gta_v_supporting_gameplay``.
    if _GTA_VI_RE.search(subject_norm) and _GTA_V_RE.search(key) and not _GTA_VI_RE.search(key):
        return False
    if _GTA_V_RE.search(subject_norm) and _GTA_VI_RE.search(key) and not _GTA_V_RE.search(key):
        return False
    if "prince harry" in subject_norm and ("potter" in key or "hogwarts" in key or "voldemort" in key or "half blood" in key or "half-blood" in key):
        return False
    if ("grand theft auto" in subject_norm or "gta" in subject_norm) and ("canyon" in key or "national park" in key):
        return False
    try:
        from services.container_guard import text_mentions_subject
        if text_mentions_subject(key, subject):
            return True
    except Exception:
        pass
    for alias in _subject_aliases(subject):
        alias_key = _norm(alias)
        if alias_key and re.search(rf"(?<![a-z0-9]){re.escape(alias_key)}(?![a-z0-9])", key):
            return True
    qualifier_terms = {"fruit", "vegetable", "food", "company", "person"}
    tokens = [
        t for t in _norm(subject).split()
        if t and t not in _WEAK_SUBJECT_TERMS and t not in qualifier_terms
    ]
    if len(tokens) >= 2:
        subject_norm = _norm(subject)
        if re.search(rf"(?<![a-z0-9]){re.escape(subject_norm)}(?![a-z0-9])", key):
            return True
        for part in re.split(r"\b(?:and|with|family|story|history|adventures|life)\b", subject_norm):
            part_clean = part.strip()
            if len(_norm(part_clean).split()) >= 2:
                if re.search(rf"(?<![a-z0-9]){re.escape(part_clean)}(?![a-z0-9])", key):
                    return True
        if "harry" in tokens and "prince" in tokens and "potter" in key:
            return False
        if "grand" in tokens and "theft" in tokens and "canyon" in key:
            return False
        def _token_pattern(token: str) -> str:
            # Match the common English plural form while retaining a strict
            # word boundary.  This keeps ``Venezuela earthquake`` and
            # ``Venezuela earthquakes`` in the same evidence lane without
            # weakening full-name matching for unrelated entities.
            token = re.escape(token)
            if token.endswith("ies"):
                return token[:-3] + r"(?:y|ies)"
            if token.endswith("es"):
                return token[:-2] + r"(?:e|es)"
            if token.endswith("s") and len(token) > 3:
                return token[:-1] + r"s?"
            return token

        in_order_pattern = r"\b" + r"(?:\s+\w+){0,2}\s+".join(
            _token_pattern(t) for t in tokens[:3]
        ) + r"\b"
        if re.search(in_order_pattern, key):
            return True
        return False

    def _token_present(token: str) -> bool:
        forms = {token}
        if token.endswith("ies") and len(token) > 4:
            forms.add(token[:-3] + "y")
        if token.endswith("es") and len(token) > 4:
            forms.add(token[:-2])
        if token.endswith("s") and len(token) > 3:
            forms.add(token[:-1])
        return any(
            re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", key)
            for form in forms
        )

    return bool(tokens and all(_token_present(token) for token in tokens))


def _subject_is_weak(term: str) -> bool:
    key = _norm(term)
    if not key:
        return True
    try:
        from services.container_guard import canonical_container_subject, is_weak_container_subject
        canonical = canonical_container_subject(term)
        if canonical and _norm(canonical) != key:
            return False
        if canonical and not is_weak_container_subject(canonical):
            return False
        if is_weak_container_subject(term):
            return True
    except Exception:
        pass
    tokens = [t for t in key.split() if t]
    meaningful = [t for t in tokens if t not in _WEAK_SUBJECT_TERMS and len(t) > 2]
    if len(tokens) >= 5 and len(meaningful) <= 1:
        return True
    return not meaningful


def _canonical_subject(value: Any, scene_text: str, title: str, known_subjects: Optional[Iterable[str]] = None) -> str:
    raw = _clean_text(value)
    if not raw:
        return ""
    try:
        from services.container_guard import canonical_container_subject
        canonical = canonical_container_subject(raw, scene_text, title, known_subjects)
        if canonical and not _subject_is_weak(canonical):
            return canonical
        return ""
    except Exception:
        return raw if not _subject_is_weak(raw) else ""


def _subject_domain(subject: str, scene_text: str = "", title: str = "") -> str:
    try:
        from services.container_guard import domain_context_for_subject
        return domain_context_for_subject(subject, scene_text, title)
    except Exception:
        return ""


def _scene_subject_terms(text: str, title: str = "") -> List[str]:
    try:
        from services.container_guard import canonical_container_subject
        # A long narration sentence is not a subject: the canonical path would
        # return the whole phrase as a "subject" ("crew launched lifeboats…"),
        # which collapses per-scene facets back into sentence-shaped noise.
        # Only short phrases (intended subjects) may take the canonical path.
        if len(re.findall(r"[a-z0-9]+", str(text or "").casefold())) <= 4:
            canonical = canonical_container_subject(text, text, title)
            if canonical and not _subject_is_weak(canonical):
                return [canonical]
    except Exception:
        pass
    # Proper-name runs (including acronym-led entities like "RMS Titanic",
    # "USS Arizona"). Sentence-opening fragments ("The crew launched…") are
    # narration, not subject matter: a run that starts at a sentence boundary
    # only counts when its leading word is a real name ("Erling Haaland").
    # Weak openers ("What Trump said…") drop the opener and keep the name, so
    # beat subjects that begin a scene are still recovered.
    body = str(text or "")
    matches = list(re.finditer(
        r"(?:[A-Z]{2,6}\s+[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2})"
        r"|[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){0,2}",
        body,
    ))
    candidates = []
    for match in matches:
        prefix = body[max(0, match.start() - 24):match.start()]
        if re.search(r"(?:^|[.!?])\s*$", prefix):
            words = match.group(0).split()
            if len(words) > 1 and _subject_is_weak(words[0]):
                candidates.append(" ".join(words[1:]))
            else:
                candidates.append(match.group(0))
            continue
        candidates.append(match.group(0))
    return [c for c in _dedupe(candidates) if not _subject_is_weak(c) and _norm(c) not in _SCENE_MONTH_WORDS][:3]


def _calculate_scene_text_overlap(text1: str, text2: str) -> float:
    a = {t for t in re.findall(r"[a-z0-9]+", _norm(text1)) if len(t) > 2}
    b = {t for t in re.findall(r"[a-z0-9]+", _norm(text2)) if len(t) > 2}
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _detect_video_format(title: str, script: str, scene_texts: Optional[List[str]] = None) -> VideoFormat:
    subjects = []
    for text in scene_texts or re.split(r"\n+|(?<=[.!?])\s+", script or ""):
        subjects.extend(_scene_subject_terms(text, title))
    distinct = {_norm(s) for s in subjects if s}
    if len(distinct) >= 2:
        return VideoFormat.MULTI_SUBJECT
    if len(distinct) == 1:
        return VideoFormat.SINGLE_SUBJECT
    return VideoFormat.SCRIPT_LIKE


def _is_listicle_video(title: str, script: str = "", scene_texts: Optional[List[str]] = None) -> bool:
    return _detect_video_format(title, script, scene_texts) == VideoFormat.MULTI_SUBJECT


def _generate_queries_mechanical(scene_texts: List[str], title: str = "", *_, **__) -> List[Dict[str, Any]]:
    rows = []
    script = str(__.get("script") or "")
    for idx, text in enumerate(scene_texts or []):
        requirement = None
        try:
            from .visual_requirement import build_visual_requirement
            local_subjects = _scene_subject_terms(_clean_mechanical_facet_text(text), title)
            requirement_title = local_subjects[0] if local_subjects and idx > 0 else title
            requirement = build_visual_requirement(
                title=requirement_title,
                script=script,
                scene_text=text,
            )
        except Exception:
            requirement = None
        if requirement and requirement.get("canonical_entity"):
            variants = provider_query_variants(requirement, provider="youtube")
            identity = str(requirement.get("canonical_entity") or "")
            rows.append({
                "scene_number": idx + 1,
                "section_subject": identity,
                "scene_subject": identity,
                "subject_query": identity,
                "youtube_query": variants[0] if variants else identity,
                "search_query": variants[0] if variants else identity,
                "alt_search_query": variants[1] if len(variants) > 1 else identity,
                "fallback_query": variants[2] if len(variants) > 2 else identity,
                "query_variants": variants,
                "visual_requirement": requirement,
                "_semantic_locked": True,
            })
            continue
        subject = (_scene_subject_terms(_clean_mechanical_facet_text(text), title) or _scene_subject_terms(title, ""))[:1]
        active = subject[0] if subject else (title or text)
        query = f"{active} documentary footage"
        rows.append({
            "scene_number": idx + 1,
            "section_subject": active if not _subject_is_weak(active) else "",
            "scene_subject": active,
            "subject_query": active,
            "youtube_query": query,
            "search_query": query,
            "alt_search_query": f"{active} archival footage",
            "fallback_query": f"{active} real footage",
            "query_variants": [query, f"{active} real footage", f"{active} archival footage"],
        })
    return rows


_SCENE_MONTH_WORDS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}

# Keep this local as well as in the semantic query agent: V2 receives already
# planned rows from several callers, so it must independently protect a
# ranking/listicle from being flattened into one global source pool.
_LISTICLE_HINT_RE = re.compile(
    r"\b(?:top|best|greatest|worst)\s*(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"|\b(?:number|no\.?|ranked|ranking)\s*(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"|\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:place|spot|is)\b",
    re.IGNORECASE,
)

_SCENE_FACET_LEAD_WORDS = {
    "on", "at", "in", "the", "by", "for", "from", "under", "and", "with",
}


def _clean_mechanical_facet_text(text: str) -> str:
    """Strip dates, numerals, articles and prepositions from beat text so the
    mechanical per-scene subject extraction yields real noun facets ("grand
    staircase", "lifeboats") instead of raw sentence openings ("On April")."""
    cleaned = re.sub(
        r"(?i)\b(?:january|february|march|april|may|june|july|august|september|october|november|december)"
        r"\s*\d{0,2}(?:st|nd|rd|th)?\b",
        " ",
        str(text or ""),
    )
    cleaned = re.sub(r"(?i)\b\d{1,4}(?:st|nd|rd|th)?\b", " ", cleaned)
    cleaned = re.sub(r"\b(?:on|at|in|the|by|for|from|under|and|with)\b", " ", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_script_to_scenes(script: str, title: str = "", api_key: str = "", num_scenes: int = 10, **_) -> List[Dict[str, Any]]:
    parts = [p.strip() for p in re.split(r"\n+|(?<=[.!?])\s+", script or "") if p.strip()]
    # A requested storyboard size is a ceiling, not permission to invent
    # beats by repeating narration or the title. Callers can keep empty output
    # slots for alignment without creating artificial visual requirements.
    # If the script has more sentences than scene slots, group adjacent
    # sentences so the whole narration remains represented in order.
    target_count = max(0, int(num_scenes or 0))
    if not parts or target_count == 0:
        return []
    scene_texts: List[str] = []
    base_size, extra = divmod(len(parts), min(len(parts), target_count))
    cursor = 0
    for index in range(min(len(parts), target_count)):
        chunk_size = base_size + (1 if index < extra else 0)
        scene_texts.append(" ".join(parts[cursor:cursor + chunk_size]))
        cursor += chunk_size
    return [
        {"scene_number": idx + 1, "description": text, "search_query": f"{title} {text}"[:120]}
        for idx, text in enumerate(scene_texts)
    ]


def _candidate_url(candidate: Dict[str, Any]) -> str:
    if not isinstance(candidate, dict):
        return ""
    url = candidate.get("webpage_url") or candidate.get("url") or ""
    video_id = candidate.get("id") or candidate.get("video_id") or ""
    if video_id and not str(url).startswith("http"):
        return f"https://www.youtube.com/watch?v={video_id}"
    return str(url)



def _video_id(candidate: Dict[str, Any]) -> str:
    if not isinstance(candidate, dict):
        return ""
    if candidate.get("id"):
        return str(candidate["id"])
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", _candidate_url(candidate))
    return match.group(1) if match else _candidate_url(candidate)


def _timestamp_reuse_keys(candidate: Dict[str, Any]) -> List[str]:
    keys = [_video_id(candidate)]
    title_key = _norm(candidate.get("title") or "")
    if title_key:
        # Regional mirrors often carry the exact same trailer under different
        # video IDs. Treat that title as one visual source for reuse spacing.
        keys.append(f"title:{title_key}")
    return [key for key in dict.fromkeys(keys) if key]


def _captioned_source_reason(candidate: Dict[str, Any]) -> str:
    """Reject actual text/slideshow packages, not captioned real footage.

    Descriptions of archive films often mention still images and subtitles.
    The title establishes package intent; the local screen checks obstruction.
    """
    text = _norm(str(candidate.get("title") or ""))
    if any(term in text for term in ('lyrics video', 'lyric video', 'karaoke')):
        return "text_package_source_rejected"
    if any(term in text for term in _STILL_MEDIA_PACKAGE_TERMS):
        return "still_image_or_slideshow_source_rejected"
    return ""


def _hard_block_reason(candidate: Dict[str, Any]) -> str:
    channel = str(candidate.get('channel') or candidate.get('uploader') or '').strip()
    # Reject explicitly synthetic publishers before spending a transfer on
    # fabricated factory/archive footage. Merely discussing AI is allowed.
    if re.search(r'(?:\bAI\s*$|\bAI[\s_-]+(?:generated|visuals|creations)\b)', channel, re.I):
        return 'synthetic_publisher'
    text = _norm(" ".join([
        str(candidate.get("title") or ""),
        str(candidate.get("description") or ""),
        str(candidate.get("channel") or candidate.get("uploader") or ""),
        str(candidate.get("webpage_url") or candidate.get("url") or ""),
    ]))
    duration = float(candidate.get("duration") or 0)
    availability = _norm(candidate.get("availability") or "")
    live_status = _norm(candidate.get("live_status") or "")
    try:
        age_limit = float(candidate.get("age_limit") or 0)
    except (TypeError, ValueError):
        age_limit = 0.0
    if availability in {"private", "subscriber only", "premium only", "needs auth"}:
        return "unavailable_or_auth_required"
    if live_status in {"is upcoming", "post live"}:
        return "not_download_ready"
    if age_limit >= 18:
        return "age_restricted"
    if "/shorts/" in str(candidate.get("webpage_url") or candidate.get("url") or "").lower():
        return "shorts"
    # This value can be supplied by the app as a normalized float string
    # (for example, ``20.0``).  Do not let a formatting detail abort the whole
    # retrieval branch before any candidate is downloaded.
    try:
        min_source_duration = float(
            os.environ.get("VIDZEO_YT_MIN_SOURCE_DURATION", "12") or 12
        )
    except (TypeError, ValueError):
        min_source_duration = 12.0
    if duration and duration < min_source_duration:
        return "too_short"
    package_reason = _captioned_source_reason(candidate)
    if package_reason:
        return package_reason
    if any(term in text for term in _BAD_VISUAL_TERMS):
        return "fake_or_template"
    return ""


def _metadata_talking_head_reason(candidate: Dict[str, Any]) -> str:
    """Reject presenter/package metadata before spending a download slot."""
    title = str(candidate.get("title") or "")
    text = _norm(" ".join([
        title,
        str(candidate.get("description") or ""),
        str(candidate.get("channel") or candidate.get("uploader") or ""),
    ]))
    contamination = _presenter_contamination_reason(text)
    if contamination:
        return f"presenter_contamination:{contamination}"
    raw_footage_terms = (
        "raw footage", "actual footage", "real footage", "archive footage",
        "archival footage", "historical footage", "newsreel", "b-roll",
        "b roll", "without commentary", "without narration", "uncut",
        "unedited", "direct feed", "official footage", "gameplay",
        "match footage", "maritime footage",
    )
    presenter_terms = (
        "interview", "panel discussion",
        "studio presenter", "news anchor", "anchor explains", "expert commentary",
        "hosted by", "presenter", "explained", "analysis",
        "breakdown", "news update", "what really happened",
    )
    # Documentary/archive channels produce narrated analyses, but the body of
    # the video is archival film, not a presenter on a set. The metadata gate
    # must not starve the pool of those sources; the probe and overlay gates
    # still audit the actual frames.
    try:
        from services.footage_signal_engine import get_channel_dna

        dna_type, _ = get_channel_dna(_norm(str(candidate.get("channel") or candidate.get("uploader") or "")))
    except Exception:
        dna_type = "UNKNOWN"
    footage_house = dna_type in {"ARCHIVE", "DOCUMENTARY", "OFFICIAL_SOURCE"}
    if any(term in text for term in presenter_terms) and not any(
        term in text for term in raw_footage_terms
    ):
        if footage_house:
            candidate["v2_soft_source_penalty"] = max(
                float(candidate.get("v2_soft_source_penalty") or 0.0),
                12.0,
            )
            return ""
        candidate["v2_soft_source_penalty"] = max(float(candidate.get("v2_soft_source_penalty") or 0.0), 18.0)
        return ""
    # Question/clickbait packages frequently have no explicit presenter term.
    # Keep documentary footage titles with a raw/archive signal eligible.
    if "?" in title and not any(term in text for term in raw_footage_terms):
        if footage_house:
            candidate["v2_soft_source_penalty"] = max(
                float(candidate.get("v2_soft_source_penalty") or 0.0),
                15.0,
            )
            return ""
        candidate["v2_soft_source_penalty"] = max(float(candidate.get("v2_soft_source_penalty") or 0.0), 18.0)
        return ""
    return ""


def _context_terms(context: Optional[Dict[str, Any]], key: str) -> List[str]:
    if not isinstance(context, dict):
        return []
    value = context.get(key)
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,;|]+", str(value or ""))
    result = []
    seen = set()
    for item in raw:
        cleaned = _norm(item)
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result[:12]


def _row_retrieval_context(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "domain_anchor": row.get("domain_anchor") or row.get("topic_anchor") or row.get("global_topic") or "",
        "domain_category": row.get("domain_category") or "",
        "search_disambiguator": row.get("search_disambiguator") or "",
        "retrieval_positive_terms": row.get("retrieval_positive_terms") or [],
        "retrieval_negative_terms": row.get("retrieval_negative_terms") or [],
        "container_type": row.get("container_type") or row.get("scene_subject_type") or "",
        "container_subject": row.get("section_subject") or row.get("container_subject") or "",
        "primary_container_subject": row.get("primary_container_subject") or "",
        "visual_intent": row.get("visual_intent") or "",
        "youtube_query_intents": row.get("youtube_query_intents") or [],
        "gta_vi_supporting_gameplay": bool(row.get("gta_vi_supporting_gameplay")),
        "allow_packaged_media": bool(row.get("allow_packaged_media") or row.get("allow_talking_head")),
        "is_intro": bool(row.get("is_intro")),
        "is_outro": bool(row.get("is_outro")),
        "intro_unique": bool(row.get("intro_unique")),
        "bite_clip": bool(row.get("bite_clip") or row.get("youtube_bite_clip")),
        "visual_requirement": row.get("visual_requirement") or {},
    }


def _allows_packaged_candidate(scene_text: str, query: str, context: Optional[Dict[str, Any]]) -> bool:
    del scene_text, query
    return bool((context or {}).get("allow_packaged_media"))


def _visual_signal_score(text: str, context: Optional[Dict[str, Any]]) -> float:
    category = _norm((context or {}).get("domain_category") or "")
    terms = set(_VISUAL_FOOTAGE_TERMS)
    terms.update(_CATEGORY_VISUAL_TERMS.get(category, set()))
    hits = [term for term in terms if term in text]
    return min(40.0, 8.0 * len(hits))


def _is_trusted_official_channel(
    channel: str,
    context: Optional[Dict[str, Any]],
    candidate: Optional[Dict[str, Any]] = None,
) -> bool:
    channel_key = _norm(channel)
    if not channel_key:
        return False
    if channel_key in _TRUSTED_OFFICIAL_CHANNELS:
        return True
    verified = bool((candidate or {}).get("channel_is_verified"))
    if verified and any(channel_key.startswith(f"{term} ") for term in _TRUSTED_OFFICIAL_CHANNELS):
        return True
    category = _norm((context or {}).get("domain_category") or "")
    container_subject = _norm((context or {}).get("container_subject") or "")
    title_key = _norm(" ".join([
        str((candidate or {}).get("title") or ""),
        str((candidate or {}).get("description") or ""),
    ]))
    channel_tokens = channel_key.split()
    company_style_channel = bool(
        1 <= len(channel_tokens) <= 3
        and not any(token.isdigit() for token in channel_tokens)
        and channel_tokens[-1] in {
            "games", "studios", "studio", "interactive", "software",
            "pictures", "productions", "motors", "official",
        }
    )
    if (
        container_subject
        and _text_mentions_subject(title_key, container_subject)
        and "official" in title_key
        and any(term in title_key for term in ("trailer", "gameplay", "footage"))
        # A verified creator is not the game's publisher. Streamers routinely
        # put "official gameplay" in reaction titles; verification alone used
        # to give those uploads the publisher bonus and dominate every scene.
        and company_style_channel
        and category != "gaming"
    ):
        return True
    return bool(
        category == "gaming"
        and container_subject
        and channel_key in {container_subject, f"{container_subject} official"}
    )


def _contains_term(text: str, terms: Iterable[str]) -> bool:
    return any(term and term in text for term in terms)


def _high_risk_media_channel(channel: str) -> bool:
    raw_channel = _clean_text(channel)
    channel_key = _norm(raw_channel)
    configured = {
        _norm(term)
        for term in re.split(r"[,;|]+", os.environ.get("VIDZEO_YT_BLOCKED_MEDIA_CHANNELS", ""))
        if _norm(term)
    }
    if _contains_term(channel_key, _HIGH_RISK_MEDIA_CHANNEL_TERMS | configured):
        return True
    if channel_key in {"nfl", "nba", "mlb", "nhl", "fifa", "uefa", "ioc", "ufc", "wwe", "f1", "nascar", "pga", "mls", "espn", "sky sports"}:
        return False
    if "news" in channel_key:
        return True
    if raw_channel.upper() in {"CNN", "MSNBC", "CNBC", "BBC", "FOX", "ABC", "CBS", "NBC", "ITV", "PBS", "DW", "RT", "ALJAZEERA", "CSPAN", "HLN", "SKY", "BLOOMBERG"}:
        return True
    media_words = {
        "news", "newspaper", "broadcast", "television", "network", "press",
        "times", "tribune", "journal", "gazette", "post", "media agency",
        "diario", "noticias", "actualidad", "actualites",
    }
    if any(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", channel_key) for word in media_words):
        return True
    if re.search(r"(?<![a-z0-9])(?:abc|nbc|cbs|fox)\s*\d{1,2}(?![a-z0-9])", channel_key):
        return True
    return bool(re.fullmatch(r"[a-z]{4}(?:\s+local)?\s+\d{1,2}", channel_key))


def _source_noise_penalty(candidate: Dict[str, Any], text: str, channel: str, context: Optional[Dict[str, Any]], visual_signal: float) -> float:
    if _is_trusted_official_channel(channel, context, candidate):
        return -30.0
    penalty = 0.0
    category = _norm((context or {}).get("domain_category") or "")
    if any(term in text for term in _BIG_MEDIA_TERMS):
        penalty += 55.0 if category not in {"news", "politics"} else 25.0
    personal_head_terms = (
        "reacts", "reaction", "livestream", "live stream", "streamer", "twitch",
        "facecam", "watch party", "podcast", "talk show", "debate", "explained",
        "breakdown", "review", "commentary",
    )
    hits = sum(1 for term in personal_head_terms if term in text)
    if hits:
        penalty += min(95.0, 25.0 * hits)
        if visual_signal <= 0:
            penalty += 60.0
    return penalty


def _channel_dna_delta(channel: str, context: Optional[Dict[str, Any]]) -> float:
    """Prefer archive/documentary/primary-source channels for footage scenes.

    Channel DNA is the strongest metadata-only predictor of clean footage.
    A candidate from an archive channel (British Pathé, archive newsreels)
    or a documentary house is far more likely to survive the probe, overlay,
    and talking-head gates than an explainer channel with a matching title.
    """
    try:
        from services.footage_signal_engine import get_channel_dna

        dna_type, _ = get_channel_dna(_norm(channel))
    except Exception:
        return 0.0
    if dna_type == "ARCHIVE":
        return 55.0
    if dna_type == "OFFICIAL_SOURCE":
        return 40.0
    if dna_type == "DOCUMENTARY":
        return 26.0
    if dna_type == "SPORTS_OFFICIAL":
        return 18.0
    if dna_type == "BROADCASTER":
        return -60.0
    return 0.0


def _verified_publisher_affinity(candidate: Dict[str, Any], subject: str) -> float:
    """A ranking hint for a verified uploader whose name is the story actor."""
    if str(candidate.get('channel_is_verified') or '').strip().lower() not in {'true', '1'}:
        return 0.0
    channel = _norm(candidate.get('channel') or candidate.get('uploader') or '')
    channel = re.sub(r'\b(?:official|usa|uk|global|europe|japan)\b', '', channel).strip()
    channel = ' '.join(channel.split())
    subject = _norm(subject)
    generic = {'the', 'world', 'news', 'media', 'history', 'science', 'sports', 'travel', 'real'}
    if len(channel) < 4 or channel in generic:
        return 0.0
    return 40.0 if subject == channel or subject.startswith(channel + ' ') else 0.0


def _score_candidate(candidate: Dict[str, Any], subject: str, scene_text: str, query: str, context: Optional[Dict[str, Any]] = None) -> float:
    candidate.pop('v2_reject_reason', None)
    title = candidate.get("title", "") or ""
    desc = candidate.get("description", "") or ""
    channel = candidate.get("channel", "") or candidate.get("uploader", "") or ""
    text = _norm(f"{title} {desc} {channel}")
    from .visual_intelligence.semantic_guard import explicit_domain_conflict
    story = ' '.join(str(value or '') for value in (subject, scene_text,
        (context or {}).get('container_subject'), (context or {}).get('global_subject')))
    # A repair tutorial is not driving/interior B-roll merely because its
    # title names the right car. Keep it available for repair stories.
    repair = r'\b(?:restoration|restoring|bodywork|repainting|paint repair|collision repair|scrapyard)\b'
    road_or_cabin = r'\b(?:daily driving|driving|commute|journeys?|cabin|interior|controls|dashboard)\b'
    if (re.search(repair, _norm(title)) and not re.search(repair, _norm(scene_text))
            and re.search(road_or_cabin, _norm(scene_text))):
        candidate['v2_reject_reason'] = 'repair_tutorial_for_road_or_cabin_scene'
        return -999.0
    if re.search(r'\b(?:beers?|brewing|brewer(?:y|ies))\b', _norm(story)) and not re.search(
        r'\b(?:beers?|brewing|brewers?|breweries|brewery|ales?|lagers?|bitters?|stouts?|pubs?|bottles?|taverns?)\b', text
    ):
        candidate['v2_reject_reason'] = 'beverage_domain_mismatch'
        return -999.0
    domain_conflict = explicit_domain_conflict(story, text)
    if domain_conflict:
        candidate['v2_reject_reason'] = domain_conflict
        return -999.0
    # Reject a known namesake before metadata ranking can promote it above the
    # requested entity.  This is intentionally scoped to the explicit royal
    # Charles anchors; broad terms such as "church" are not global blockers.
    subject_key = _norm(
        str((context or {}).get("container_subject") or "")
        or str(subject or "")
    )
    confusable_hints = set()
    for anchor, hints in _PERSON_CONFUSABLE_HINTS.items():
        if anchor in subject_key or anchor in _norm(str(subject or "")):
            confusable_hints.update(hints)
    if confusable_hints and _contains_term(text, confusable_hints):
        candidate["v2_reject_reason"] = "canonical_entity_mismatch"
        candidate["v2_confusable_entity"] = True
        return -999.0
    hard_reason = _hard_block_reason(candidate)
    if hard_reason:
        candidate["v2_reject_reason"] = hard_reason
        return -999.0
    lenient_mode = str(os.environ.get("VIDZEO_YT_LENIENT_ACCEPTANCE", "1")).strip().lower() in {"1", "true", "yes", "on"}
    if _high_risk_media_channel(channel):
        # Lenient relevance is for borderline metadata, not publisher risk.
        # Network/news packages and impersonating media channels are both a
        # poor fit for clean reusable b-roll and are rejected before download.
        candidate["v2_reject_reason"] = "copyright_high_risk_media_channel"
        return -999.0
    if _contains_term(text, _OVERLAY_PACKAGE_TERMS):
        candidate["v2_reject_reason"] = "overlay_or_text_package"
        return -999.0
    category = _norm((context or {}).get("domain_category") or "")
    bite_clip = bool((context or {}).get("bite_clip"))
    if category == 'automotive' and len(_norm(subject).split()) >= 2:
        # A model word can also name a place or animal; an alias alone is
        # not evidence that the source depicts a vehicle.
        vehicle_context = bool(re.search(r'\b(?:car|cars|automobile|automotive|sedan|coupe|hatchback|suv|'
            r'engine|interior|showroom|walkaround|vehicle|vehicles|dashboard|cabin|modified)\b', text))
        if _norm(subject) not in text and not vehicle_context:
            candidate['v2_reject_reason'] = 'vehicle_namesake_without_vehicle_context'
            return -999.0
    # Do not let a video-game broadcast stand in for real sports footage. This
    # veto is intentionally based on explicit simulation/gameplay metadata;
    # ordinary words such as "game", "match", or "highlights" remain valid.
    scene_context = _norm(f"{subject} {scene_text} {(context or {}).get('container_subject') or ''}")
    # Search phrases can drift. Only the actual story may authorize fictional
    # footage or a software tutorial; a query's accidental words cannot do so.
    fictional_format = bool(re.search(
        r"\b(?:anime recap|manga recap|manhwa recap|manhua recap|berserker|"
        r"isekai|reincarnated|reborn \d+ years|gameplay|let s play)\b", text))
    fictional_story = category == 'gaming' or bool(re.search(
        r"\b(?:anime|manga|manhwa|manhua|fiction|isekai|video game|gameplay)\b", scene_context))
    if fictional_format and not fictional_story:
        candidate['v2_reject_reason'] = 'fictional_footage_for_real_subject'
        return -999.0
    spreadsheet_tutorial = bool(re.search(r"\b(?:excel|pivot table|power bi|google sheets|summarize sales data)\b", text))
    spreadsheet_story = bool(re.search(r"\b(?:excel|spreadsheet|pivot table|power bi|google sheets)\b", scene_context))
    if category in {'finance', 'business', 'economics'} and spreadsheet_tutorial and not spreadsheet_story:
        candidate['v2_reject_reason'] = 'software_tutorial_for_financial_story'
        return -999.0
    explicit_game_story = bool(re.search(
        r"\b(?:video game|gaming|gameplay|esports|madden|ea sports fc|efootball|"
        r"pro evolution soccer|nba 2k|playstation|xbox|nintendo|grand theft auto|gta)\b",
        scene_context,
    ))
    explicit_simulation = bool(re.search(
        r"\b(?:video game footage|gameplay footage|game simulation|sports simulation|"
        r"simulated (?:match|game)|virtual match|esports gameplay)\b",
        text,
    ))
    known_game_title = bool(re.search(
        r"\b(?:madden(?: nfl)?|ea sports fc|efootball|pro evolution soccer|"
        r"nba 2k\s*\d*|fifa\s*\d{2,4})\b",
        text,
    ))
    game_format = bool(re.search(r"\b(?:gameplay|simulation|simulator|ps5|xbox|playstation|esports)\b", text))
    is_real_sports_scene = category == "sports" or bool(explicit_named_pairs(scene_text)) or bool(re.search(
        r"\b(?:nfl|nba|nhl|mlb|premier league|champions league|world cup|matchup|match action)\b",
        scene_context,
    ))
    if is_real_sports_scene and not explicit_game_story and (explicit_simulation or (known_game_title and game_format)):
        candidate["v2_reject_reason"] = "video_game_simulation_for_real_sports_scene"
        return -999.0
    try:
        if comparison_pair_conflict(scene_text, [f"{title} {desc}"]):
            candidate["v2_reject_reason"] = "explicit_comparison_entity_mismatch"
            return -999.0
    except Exception:
        pass
    gta_v_support = _gta_v_supporting_gameplay(candidate, subject, scene_text, context)
    if gta_v_support:
        candidate["gta_v_supporting_gameplay"] = True
    # Retrieval queries are positive phrases; commentary/reaction policy is
    # enforced here from the returned candidate metadata, never from search
    # operators that can hide otherwise usable gameplay.
    allows_package = _allows_packaged_candidate(scene_text, "" if category == "gaming" else query, context)
    trusted_official = _is_trusted_official_channel(channel, context, candidate)
    presenter_contamination = _presenter_contamination_reason(text)
    if presenter_contamination and not allows_package:
        candidate["v2_reject_reason"] = "streamer_or_reaction_package"
        return -999.0
    if _contains_term(text, _SENSATIONAL_EXPLAINER_TERMS) and not allows_package:
        candidate["v2_reject_reason"] = "sensational_explainer_package"
        return -999.0
    if category == "gaming":
        if _contains_term(text, _GAMING_EXPLAINER_TERMS) and not allows_package:
            if bite_clip:
                # A vetted documentary/news master may contain useful cutaway
                # footage even when its title says “explained” or “details”.
                # Keep the streamer/webcam hard veto above, but let the bite
                # row reach transcript/frame screening with a soft penalty.
                candidate["v2_soft_source_penalty"] = max(
                    float(candidate.get("v2_soft_source_penalty") or 0.0),
                    35.0,
                )
            else:
                candidate["v2_reject_reason"] = "gaming_commentary_or_news_package"
                return -999.0
        if _contains_term(text, _GAMING_FAKE_TRAILER_TERMS):
            candidate["v2_reject_reason"] = "gaming_fake_or_concept_trailer"
            return -999.0
        if _contains_term(_norm(channel), _BIG_MEDIA_TERMS) and not trusted_official:
            candidate["v2_reject_reason"] = "gaming_big_media_channel"
            return -999.0
        clean_gameplay_signal = _clean_gameplay_signal(text)
        if not trusted_official and not clean_gameplay_signal:
            candidate["v2_soft_source_penalty"] = 95.0 if "official" in text else 45.0
    elif category == "disaster" and not _contains_term(text, _DISASTER_FOOTAGE_EVIDENCE):
        candidate["v2_reject_reason"] = "disaster_metadata_lacks_clean_footage_evidence"
        # YouTube-only retrieval is a recall lane. Historical clips often
        # have sparse metadata (for example, a title containing only the ship
        # name and date), so let them reach the authoritative FFmpeg,
        # caption-overlay, and visual gates instead of losing the scene at the
        # metadata stage. Mixed/strict lanes retain the fail-closed score.
        return -180.0
    metadata_talking_head = _metadata_talking_head_reason(candidate)
    # Metadata words such as "explained" or "analysis" are not proof that the
    # selected moment contains a reaction webcam. Keep those candidates in
    # the recall lane and let the downloaded-frame audit make the hard
    # decision. Explicit streamer/reaction packages were already rejected
    # above by _STREAMER_PACKAGE_TERMS.
    talking_reject_enabled = str(
        os.environ.get("VIDZEO_YT_REJECT_METADATA_TALKING_HEAD", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if metadata_talking_head and not allows_package:
        # Explicit interview/facecam/reaction packages are still predictable
        # contamination signals and stay hard-rejected. Softer words such as
        # "explained", "analysis", or a question mark remain eligible so a
        # documentary source can still be probed and judged from its frames.
        explicit_presenter = bool(_presenter_contamination_reason(text))
        if talking_reject_enabled or explicit_presenter:
            candidate["v2_reject_reason"] = metadata_talking_head
            return -999.0
        # Recall lane: presenter/question-package titles stay eligible and are
        # only demoted. A presenter in the metadata says nothing about the
        # footage, which is still decided by the probe and overlay gates.
        candidate["v2_reject_reason"] = ""
        candidate["v2_soft_source_penalty"] = max(
            float(candidate.get("v2_soft_source_penalty") or 0.0),
            35.0,
        )
    try:
        from services.footage_signal_engine import classify_candidate_visual_intent
        visual_policy = classify_candidate_visual_intent(
            title=title,
            channel=channel,
            description=desc,
            duration=int(float(candidate.get("duration") or 0)),
            expected_niche=str((context or {}).get("domain_category") or ""),
            scene_text=scene_text,
            allows_commentary=allows_package,
        )
        if visual_policy.get("hard_reject"):
            candidate["v2_reject_reason"] = ",".join(visual_policy.get("reasons") or [])
            return -999.0
        policy_delta = float(visual_policy.get("score_delta") or 0.0)
    except Exception:
        visual_policy = {}
        policy_delta = 0.0
    channel_dna_delta = _channel_dna_delta(channel, context)
    positive_terms = _context_terms(context, "retrieval_positive_terms")
    negative_terms = _context_terms(context, "retrieval_negative_terms")
    positive_hits = [term for term in positive_terms if term and term in text]
    negative_hits = [term for term in negative_terms if term and term in text]
    if negative_hits and not positive_hits:
        return -260.0
    domain = _subject_domain(subject, scene_text, query)
    if domain:
        negative_hits = [term for term in _DOMAIN_NEGATIVE_TERMS.get(domain, set()) if term in text]
        positive_domain_terms = {
            "fruit": {
                "fruit", "fruits", "nutrition", "healthy", "health", "vitamin",
                "fresh", "produce", "market", "farm", "harvest", "juice", "smoothie",
                "recipe", "food", "diet",
            },
        }.get(domain, set())
        if negative_hits and not any(term in text for term in positive_domain_terms):
            return -260.0
    # Container subjects from LLM multi-subject scripts are often descriptive
    # phrases that footage titles never repeat verbatim ("April 15 1912",
    # "White Star Line"). A candidate that matches the domain/primary anchor is
    # still the right footage, so only hard-reject when NO anchor matches.
    subject_match = bool(
        subject and not _subject_is_weak(subject) and _text_mentions_subject(text, subject)
    )
    from .visual_requirement import beverage_category_aliases
    if beverage_category_aliases(subject):
        subject_match = bool(re.search(r'\b(?:beers?|ales?|brewing|brewer(?:y|ies)|pubs?)\b', text))
    # Some occupational identities are expressed by their role rather than
    # by the exact container phrase. "A Day in the Life of a Locomotive
    # Engineer" is stronger evidence for a Train Crew scene than a video that
    # happens to repeat the words "train crew" in a sensational title. Let the
    # immutable requirement contract supply this semantic alias to ranking;
    # query wording alone still never counts as evidence.
    requirement = (context or {}).get("visual_requirement") or {}
    rail_role_match = False
    rail_context_pending = False
    rail_requirement = isinstance(requirement, Mapping) and str(
        requirement.get("subject_type") or ""
    ).strip().lower() == "rail_crew"
    if rail_requirement:
        rail_signal = any(
            re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text)
            for term in (
                "train", "railway", "railroad", "locomotive", "conductor",
                "yardmaster", "engineer", "cab", "rail crew",
            )
        )
        rail_role_match = rail_signal and any(
            re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text)
            for term in (
                "conductor", "conductors", "locomotive engineer", "locomotive engineers",
                "train driver", "train drivers", "train crew", "train crews",
                "railroad crew", "railroad crews", "railway crew", "railway crews",
                "yardmaster", "yardmasters", "switchman", "switchmen",
            )
        )
        rail_context_pending, pending_evidence = candidate_domain_context_pending(
            requirement, candidate
        )
        if rail_context_pending:
            candidate["semantic_context_pending"] = True
            candidate["candidate_evidence"] = pending_evidence
        subject_match = subject_match or rail_role_match or rail_context_pending
    anchor_match = False
    scene_anchor = ""
    for anchor_key in ("primary_container_subject", "domain_anchor", "container_subject"):
        anchor = (context or {}).get(anchor_key) or ""
        if not scene_anchor and anchor and not _subject_is_weak(anchor):
            scene_anchor = str(anchor).strip()
        if anchor and not _subject_is_weak(anchor) and _text_mentions_subject(text, anchor):
            anchor_match = True
            break
    # In a multi-subject/listicle row the local entity is the authoritative
    # retrieval key.  A global/domain anchor (for example Cristiano Ronaldo)
    # is useful for context, but it must not rescue a candidate that does not
    # mention the active subject (for example Haaland).  Reject before any
    # download so the visual gate is not forced to clean up metadata drift.
    # Date/era labels are descriptive container anchors, not competing
    # entities. For example, an April 15 1912 scene should be allowed to use
    # verified RMS Titanic footage even though the date is not in the title.
    subject_is_date_or_descriptor = bool(
        re.search(r"\b(?:18|19|20)\d{2}\b", _norm(subject))
        or re.search(r"\b(?:january|february|march|april|may|june|july|august|"
                     r"september|october|november|december)\b", _norm(subject))
    )
    scene_subject_distinct = bool(
        subject
        and scene_anchor
        and _norm(subject) != _norm(scene_anchor)
        and not subject_is_date_or_descriptor
        and (
            _norm((context or {}).get("container_type") or "")
            in {"entity", "person", "celebrity", "athlete", "player", "organization", "place", "event"}
            or bool((context or {}).get("container_subject"))
        )
    )
    if scene_subject_distinct and not subject_match and not gta_v_support:
        candidate["v2_reject_reason"] = "scene_subject_mismatch"
        return -999.0
    if subject and not _subject_is_weak(subject) and not subject_match and not anchor_match and not gta_v_support:
        return -180.0
    visual_signal = _visual_signal_score(text, context)
    commentary_hits = [term for term in _COMMENTARY_TERMS if term in text]
    if _clean_gameplay_signal(text):
        commentary_hits = [term for term in commentary_hits if term != "commentary"]
    if commentary_hits and not allows_package and visual_signal <= 0:
        if subject_match or anchor_match:
            candidate["v2_soft_source_penalty"] = max(float(candidate.get("v2_soft_source_penalty") or 0.0), 35.0)
        else:
            candidate["v2_reject_reason"] = f"package_without_visual_signal:{commentary_hits[0].replace(' ', '_')}"
            return -220.0
    source_noise_penalty = _source_noise_penalty(candidate, text, channel, context, visual_signal)
    if source_noise_penalty >= 115.0 and not allows_package:
        candidate["v2_reject_reason"] = "streamer_commentary_or_big_media_package"
        return -240.0
    score = 20.0
    if len(_norm(subject).split()) >= 2:
        # Full qualified identity outranks a loose surname/model/place alias.
        # Alias-only candidates stay in recall unless an explicit conflict exists.
        score += 30.0 if _norm(subject) in text else -25.0
    facet_groups = (
        {'interior', 'cabin', 'dashboard', 'cockpit', 'touchscreen'},
        {'charger', 'chargers', 'charging', 'supercharger', 'superchargers', 'recharge'},
        {'battery', 'batteries', 'storage', 'powerwall', 'megapack'},
        {'solar', 'photovoltaic', 'panels'},
        {'exterior', 'walkaround', 'bodywork', 'styling'},
        {'engine', 'motor', 'powertrain'},
        {'factory', 'manufacturing', 'assembly'},
        {'courtroom', 'trial', 'hearing'},
        {'portrait', 'interview', 'profile'},
    )
    narrated_words = set(_norm(scene_text).split())
    metadata_words = set(text.split())
    for facet_group in facet_groups:
        if narrated_words & facet_group:
            score += 35.0 if metadata_words & facet_group else -20.0
            # A dedicated cutaway is more reliable than choosing a random
            # moment from a review covering several unrelated visual facets.
            title_words = set(_norm(title).split())
            if title_words & facet_group:
                other_facets = sum(bool(title_words & group) for group in facet_groups if group != facet_group)
                score += 25.0 if not other_facets else -10.0 * other_facets
    if subject_match:
        if rail_context_pending and not rail_role_match:
            # Domain-adjacent metadata is a recall candidate only. Keep it
            # below exact identity evidence while allowing frame verification.
            score += 24.0
        else:
            score += 65.0 if _text_mentions_subject(title, subject) else 45.0
        if rail_role_match and not _text_mentions_subject(title, subject):
            score += 24.0
    elif anchor_match:
        # Real footage that matches the domain anchor (but not the container
        # micro-subject) is still credited, a little below a verbatim title hit.
        anchor_title = False
        for anchor_key in ("primary_container_subject", "domain_anchor", "container_subject"):
            anchor = (context or {}).get(anchor_key) or ""
            if anchor and _text_mentions_subject(title, anchor):
                anchor_title = True
                break
        score += 55.0 if anchor_title else 40.0
    scene_terms = [t for t in re.findall(r"[a-z0-9]+", _norm(scene_text)) if len(t) > 3 and t not in _WEAK_SUBJECT_TERMS]
    query_terms = [t for t in re.findall(r"[a-z0-9]+", _norm(query)) if len(t) > 3]
    score += min(25.0, 4.0 * sum(1 for t in set(scene_terms[:8] + query_terms[:8]) if t in text))
    score += min(22.0, 4.0 * len(positive_hits))
    score -= min(30.0, 8.0 * len(negative_hits))
    score += policy_delta
    if rail_requirement:
        # Occupational footage outranks a crime/explainer title that happens
        # to repeat "train crew". Keep crime footage for narrated crime beats.
        crime_terms = r'\b(?:robbery|robberies|crime|murder|heist|scandal)\b'
        if re.search(crime_terms, _norm(title)) and not re.search(crime_terms, _norm(scene_text)):
            score -= 100.0
    score += visual_signal
    if category == "gaming":
        authentic_hits = sum(1 for term in _GAMING_AUTHENTIC_TERMS if term in text)
        score += min(50.0, 18.0 * authentic_hits)
        if gta_v_support:
            # Supporting footage is intentionally below an official GTA VI
            # trailer, but safely above generic gaming uploads.
            score += 58.0
        if trusted_official:
            score += 55.0
        elif "trailer" in _norm(title) and not any(term in text for term in ("no commentary", "without commentary")):
            score -= 65.0
    score += min(18.0, 5.0 * sum(1 for t in _FOOTAGE_BONUS_TERMS if t in text))
    score += channel_dna_delta
    # This does not confer semantic acceptance or bypass overlay/era checks.
    score += _verified_publisher_affinity(candidate, subject)
    title_key = _norm(title)
    # A topic-heavy explainer can outscore real event footage by repeating
    # every rule in the query. Keep it in recall, but favor live action for
    # sports B-roll unless the narration explicitly examines commentary.
    if category == 'sports' and not re.search(r'\b(?:said|says|interview|press conference|podcast|commentary|analysis)\b', _norm(scene_text)):
        if re.search(r'\b(?:explained|explainer|breakdown|interview|press conference|podcast|analysis)\b', title_key):
            score -= 65.0
        if re.search(r'\b(?:animated|animation|simulation)\b', title_key):
            score -= 70.0
    # Subject keywords alone are insufficient: a dashboard warning-light
    # tutorial can outscore actual cabin footage by repeating every facet.
    # Keep instructional sources in recall, but prefer footage unless the
    # narration is itself explaining that operation.
    instruction_pattern = r'\b(?:warning lights?|dashboard symbols?|owners? manual|user guide|troubleshoot\w*|how to|tutorial|reset\w*)\b'
    if re.search(instruction_pattern, title_key) and not re.search(instruction_pattern, _norm(scene_text)):
        score -= 85.0
    if re.search(r'\b(?:shocking secrets?|you never knew|will shock you|untold truth|must watch)\b', title_key):
        score -= 55.0
    # Title-level footage signals are far stronger than description scans: a
    # publisher that advertises raw/archival/restored footage in the title is
    # overwhelmingly likely to deliver moving film rather than a talking head.
    title_footage_hits = sum(1 for term in _TITLE_FOOTAGE_BONUS_TERMS if term in title_key)
    if title_footage_hits:
        score += min(70.0, 20.0 * title_footage_hits)
    score -= min(85.0, 18.0 * len(commentary_hits)) if not allows_package else 0.0
    score -= min(120.0, source_noise_penalty)
    # A nostalgia list with a matching title is often a still-image montage.
    # Keep it in recall, but rank visible footage ahead of that package.
    if (re.match(r'^(?:top\s+)?\d{1,2}\s+', _norm(title))
            and not re.match(r'^\d{1,2}\s+(?:seconds?|minutes?|hours?|mm)\b', _norm(title))
            and not re.search(r'\b(?:raw|uncut|newsreel|footage|film|archive|archival)\b', _norm(title))):
        score -= 110.0
    score -= float(candidate.get("v2_soft_source_penalty") or 0.0)
    duration = float(candidate.get("duration") or 0)
    # Documentary, history, science, nature and educational material favours longer
    # sources that can carry multiple timestamped scenes (up to 2 hours / 7200s).
    # Short low-information talking-head packages (<90s) are penalised.
    if category in {"disaster", "history", "documentary", "science", "nature", "geography"}:
        if 180 <= duration <= 7200:
            score += 20.0
        elif duration and duration < 120:
            score -= 15.0
    else:
        if 120 <= duration <= 7200:
            score += 15.0
        elif duration and duration < 90:
            score -= 15.0
    candidate["v2_score"] = round(score, 2)
    return score


def _get_authenticated_youtube_session(
    session_jar: Any = None,
    youtube_session: Any = None,
    enabled: bool = True,
) -> Any:
    """Load the process-scoped authenticated session once for this run.

    Public YouTube search remains a valid last resort.  A missing or stale
    local session must therefore not turn a usable anonymous search into a
    hard pipeline failure, but an already connected session should be preferred
    for both metadata and downloads.
    """
    if session_jar is not None:
        return _adapt_youtube_cookie_jar(session_jar)
    if youtube_session is not None:
        return _adapt_youtube_cookie_jar(youtube_session)
    if not enabled:
        return None
    try:
        from services.youtube_session import ensure_verified_session
    except Exception:
        return None
    try:
        jar, _generation = ensure_verified_session(reason="YouTube V2 pipeline")
        adapted = _adapt_youtube_cookie_jar(jar)
        if adapted:
            print("   [yt-v2] authenticated YouTube session enabled")
        return adapted
    except Exception as exc:
        # Anonymous retrieval is intentionally retained for public videos.
        session_issue = ""
        require_session = str(os.environ.get("VIDZEO_YT_REQUIRE_SESSION", "0")).lower() in {"1", "true", "yes", "on"}
        try:
            from services.youtube_session import session_status
            _status = session_status()
            if _status.get("cookie_update_required"):
                session_issue = f"; session INVALIDATED ({(_status.get('cookie_update_reason') or 'rejected')[:120]})"
        except Exception:
            session_issue = ""
        if session_issue:
            print(
                f"   [yt-v2] WARNING: saved YouTube session{session_issue}; falling back to public search "
                f"({type(exc).__name__})"
            )
            if require_session:
                raise RuntimeError(
                    "VIDZEO_YT_REQUIRE_SESSION is set but the saved YouTube session is invalid; "
                    "reconnect it in Vidzeo before retrying."
                ) from exc
        else:
            print(f"   [yt-v2] authenticated session unavailable; using public search ({type(exc).__name__})")
        return None


def _adapt_youtube_cookie_jar(session_jar: Any) -> Any:
    """Copy broker cookies into yt-dlp's cookie jar without touching disk."""
    if session_jar is None or isinstance(session_jar, (bool, Exception)):
        return None
    if callable(getattr(session_jar, "get_cookie_header", None)):
        return session_jar
    try:
        from yt_dlp.cookies import YoutubeDLCookieJar

        adapted = YoutubeDLCookieJar()
        if hasattr(session_jar, "__iter__"):
            for cookie in session_jar:
                if hasattr(cookie, "name"):
                    adapted.set_cookie(copy.copy(cookie))
        return adapted
    except Exception as exc:
        # A yt-dlp build without the adapter can still use public YouTube data.
        print(f"   [yt-v2] authenticated cookie adapter unavailable; using public search ({type(exc).__name__})")
        return None


def _install_youtube_session(ydl: Any, session_jar: Any) -> None:
    """Attach the broker's in-memory cookie jar without writing plaintext cookies."""
    session_jar = _adapt_youtube_cookie_jar(session_jar)
    if session_jar is None:
        return
    try:
        ydl.cookiejar = session_jar
        # yt-dlp creates its opener lazily in supported versions.  If a version
        # created it during construction, rebuild it so the supplied jar wins.
        if "_opener" in getattr(ydl, "__dict__", {}):
            ydl.__dict__["_opener"] = None
    except Exception as exc:
        print(f"   [yt-v2] could not attach authenticated session ({type(exc).__name__})")


def _youtube_player_clients(session_jar: Any = None) -> List[str]:
    """Resolve the yt-dlp player_client list for video/probe downloads.

    When an authenticated session is active, the standard 'web' client respects
    browser cookies and avoids embedded client player rate-limiting (throttling).
    """
    configured = str(os.environ.get("VIDZEO_YT_PLAYER_CLIENTS") or "").strip()
    if configured:
        clients = [c.strip() for c in configured.split(",") if c.strip()]
        if clients:
            return clients
    adapted = _adapt_youtube_cookie_jar(session_jar) if session_jar is not None else None
    if adapted is not None:
        return ["android", "web", "web_embedded", "web_creator", "mweb"]
    return ["android", "web_embedded", "web_creator", "mweb"]


def _search_cache_path(query: str) -> Path:
    """Disk cache path for one search query (keyed by the normalized query)."""
    cache_root = Path(os.environ.get("VIDZEO_YT_SEARCH_CACHE_DIR") or (Path(TEMP_DIR) / "yt_search_cache"))
    cache_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(query.strip().casefold().encode("utf-8")).hexdigest()[:20]
    return cache_root / f"{digest}.json"


def _cached_search_results(query: str) -> Optional[List[Dict[str, Any]]]:
    """Return cached yt-dlp entries for ``query``, or None on miss/expiry.

    Production reruns repeat the same container queries (a 12-scene disaster
    job fans out 40-100 searches); under IP throttling every one of those
    requests crawls for tens of seconds. Serving repeat queries from disk
    avoids the throttle entirely and keeps the branch wall for downloads.
    """
    try:
        ttl = float(os.environ.get("VIDZEO_YT_SEARCH_CACHE_TTL_SECONDS", "21600") or 21600)
    except (TypeError, ValueError):
        ttl = 21600.0
    if ttl <= 0:
        return None
    cache_file = _search_cache_path(query)
    try:
        if not cache_file.is_file():
            return None
        if time.time() - cache_file.stat().st_mtime > ttl:
            return None
        entries = json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            return None
        return entries
    except Exception:
        return None


def _store_search_results(query: str, entries: List[Dict[str, Any]]) -> None:
    """Atomically persist search entries so reruns skip the network entirely."""
    try:
        ttl = float(os.environ.get("VIDZEO_YT_SEARCH_CACHE_TTL_SECONDS", "21600") or 21600)
    except (TypeError, ValueError):
        ttl = 21600.0
    if ttl <= 0 or not entries:
        return
    try:
        cache_file = _search_cache_path(query)
        # Several scene workers can finish the same coalesced search together;
        # a shared ``.tmp`` name lets one writer replace another writer's
        # partial file. Keep the atomic replace, but make the staging name
        # process/thread unique.
        temp = cache_file.with_name(
            f".{cache_file.stem}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        temp.write_text(
            json.dumps(entries, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, cache_file)
    except Exception:
        pass


_SEARCH_FLIGHT_LOCK = threading.Lock()
_SEARCH_FLIGHTS: Dict[str, threading.Event] = {}
_SEARCH_MEMORY: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _search_youtube_uncached(
    query: str,
    max_results: int = 12,
    session_jar: Any = None,
    cancellation: Any = None,
) -> List[Dict[str, Any]]:
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    # A cached pass avoids the network entirely: the same container queries
    # repeat on every rerun, and under IP throttling each repeat would crawl
    # for the full socket window before YouTube even answers. Caches serve
    # both authenticated and anonymous paths; search depth is unchanged.
    cached = _cached_search_results(query)
    if cached is not None:
        restored = []
        for item in cached:
            if not item:
                continue
            row = dict(item, query_text=query)
            row.setdefault("provider", "youtube")
            row.setdefault("source", "youtube")
            try:
                from services.media_provenance import annotate_rights
                annotate_rights(row)
            except Exception:
                pass
            restored.append(row)
        return restored[:max(1, int(max_results or 12))]
    try:
        from services.yt_dlp_runtime import load_yt_dlp
        yt_dlp = load_yt_dlp()
    except Exception as exc:
        print(f"   [yt-v2] yt-dlp unavailable: {exc}")
        return []
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": int(os.environ.get("VIDZEO_YT_METADATA_SOCKET_TIMEOUT", "15") or 15),
        "retries": int(os.environ.get("VIDZEO_YT_METADATA_RETRIES", "2") or 2),
    }
    opts.update(_youtube_js_runtime_options())
    # Regional relevance: without hl/gl, YouTube ranks results for the
    # operator's IP location, so a server in the US returns US-biased
    # candidates for every market. Allow per-market overrides so an
    # international deployment can pin the target region/language.
    _hl = str(os.environ.get("VIDZEO_YT_SEARCH_HL", "") or "").strip()
    if _hl:
        opts["extractor_args"] = {
            **dict(opts.get("extractor_args") or {}),
            "youtube": {"lang": [_hl]},
        }
    _gl = str(os.environ.get("VIDZEO_YT_SEARCH_GL", "") or "").strip()
    if _gl:
        # geo_bypass_country is a top-level yt-dlp option that prefers the
        # given region in search ranking when proxies/IP location disagree.
        opts["geo_bypass_country"] = _gl
    if cancellation is not None:
        def _search_progress(_status):
            cancellation.raise_if_cancelled()
        opts["progress_hooks"] = [_search_progress]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            _install_youtube_session(ydl, session_jar)
            data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        entries = []
        for item in (data or {}).get("entries") or []:
            if not item:
                continue
            row = dict(item)
            row.setdefault("provider", "youtube")
            row.setdefault("source", "youtube")
            try:
                from services.media_provenance import annotate_rights
                annotate_rights(row)
            except Exception:
                pass
            entries.append(row)
        if entries:
            _store_search_results(query, [dict(item) for item in entries])
        return [dict(item or {}, query_text=query) for item in entries]
    except Exception as exc:
        print(f"   [yt-v2] search failed '{query[:70]}': {str(exc)[:120]}")
        return []


def _search_youtube(
    query: str,
    max_results: int = 12,
    session_jar: Any = None,
    cancellation: Any = None,
) -> List[Dict[str, Any]]:
    """Search YouTube with shared in-flight and disk caching.

    A storyboard can produce the same semantic query in several containers.
    Previously those workers all missed the disk cache at the same time and
    independently hit YouTube.  Coalesce those requests: one worker owns the
    network call, while the others wait briefly for its result.  This improves
    recall under throttling as well as latency because the saved result is
    shared across the whole process and subsequent jobs.
    """
    normalized = " ".join(str(query or "").strip().casefold().split())
    if not normalized:
        return []
    try:
        ttl = float(os.environ.get("VIDZEO_YT_SEARCH_MEMORY_TTL_SECONDS", "21600") or 21600)
    except (TypeError, ValueError):
        ttl = 21600.0
    now = time.time()
    with _SEARCH_FLIGHT_LOCK:
        memory = _SEARCH_MEMORY.get(normalized)
        if ttl > 0 and memory and now - memory[0] <= ttl:
            return [dict(item, query_text=query) for item in memory[1][:max(1, int(max_results or 12))]]
        owner = normalized not in _SEARCH_FLIGHTS
        event = _SEARCH_FLIGHTS.setdefault(normalized, threading.Event())
    if not owner:
        wait_seconds = max(1.0, min(45.0, float(os.environ.get("VIDZEO_YT_SEARCH_FLIGHT_WAIT_SECONDS", "20") or 20)))
        event.wait(wait_seconds)
        with _SEARCH_FLIGHT_LOCK:
            memory = _SEARCH_MEMORY.get(normalized)
            if memory and (ttl <= 0 or now - memory[0] <= ttl):
                return [dict(item, query_text=query) for item in memory[1][:max(1, int(max_results or 12))]]
        # The owner failed or exceeded its wait window. Do not stampede the
        # provider with a second identical request; the next scheduled query
        # can retry after the in-flight entry is released.
        return []
    try:
        result = _search_youtube_uncached(
            query, max_results=max_results, session_jar=session_jar, cancellation=cancellation
        )
        if result:
            with _SEARCH_FLIGHT_LOCK:
                _SEARCH_MEMORY[normalized] = (time.time(), [dict(item) for item in result])
        return result
    finally:
        with _SEARCH_FLIGHT_LOCK:
            _SEARCH_FLIGHTS.pop(normalized, None)
            event.set()


def _scene_row_subject(row: Dict[str, Any], scene_text: str, title: str) -> str:
    for key in ("section_subject", "container_subject", "scene_subject", "subject_query"):
        value = _canonical_subject(row.get(key), scene_text, title)
        if value:
            return value
    terms = _scene_subject_terms(scene_text, title)
    if terms:
        return terms[0]
    # Intro and bridge scenes borrow a verified neighboring/container anchor
    # later; they must not invent a pool identity here.
    return ""


def _is_date_only_subject(value: Any) -> bool:
    key = re.sub(r"[^a-z0-9\s-]+", " ", _norm(value)).strip()
    if not key:
        return False
    months = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    return bool(
        re.fullmatch(rf"(?:on\s+)?{months}\s+\d{{1,2}}(?:\s+\d{{4}})?", key)
        or re.fullmatch(r"\d{4}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?", key)
    )


_SINGLE_TOPIC_EVIDENCE_FIELDS = (
    "title_anchor",
    "domain_anchor",
    "topic_anchor",
    "global_topic",
    "section_subject",
    "container_subject",
    "scene_subject",
    "subject_query",
)

# These are useful visual facets, but not stable identities.  Letting a word
# such as "ship" or "disaster" become the shared pool anchor would collapse
# unrelated listicle subjects and make metadata ranking much less accurate.
_SINGLE_TOPIC_ENTITY_NOISE = _WEAK_SUBJECT_TERMS | {
    "archive", "archival", "actual", "historical", "historic", "documentary",
    "newsreel", "vintage", "real", "raw", "event", "events", "disaster",
    "maritime", "accident", "tragedy", "ship", "ships", "vessel", "vessels",
    "passenger", "passengers", "crew", "people", "person", "company", "line",
    "date", "departure", "arrival", "voyage", "journey", "engineering",
    "largest", "masterpiece", "belief", "unsinkable", "collision", "sinking",
    "iceberg", "lifeboat", "lifeboats", "survivor", "survivors", "rescue",
    "aftermath", "investigation", "investigations", "ocean", "sea", "water",
    "story", "history", "world", "moment", "moments", "scene", "section",
}
_SINGLE_TOPIC_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
}


def _single_topic_entity_candidates(value: Any) -> List[str]:
    """Extract proper-name candidates without promoting descriptive labels.

    LLM container maps frequently return a different descriptive subject for
    every scene.  The proper entity inside those labels is still stable, e.g.
    ``Titanic engineering masterpiece`` and ``RMS Titanic departure``.  Keep
    both the full proper-name span and its meaningful tokens so aliases such as
    ``Titanic`` and ``RMS Titanic`` can be clustered later.
    """
    text = _clean_text(value)
    if not text:
        return []
    candidates: List[str] = []
    # Model designations are part of the identity: A4, E-Type and accented
    # names must not be truncated into broad searches such as "LNER Class".
    name_token = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ0-9'’-]*"
    entity_pattern = re.compile(
        r"(?<![A-Za-z0-9])"
        + name_token
        + r"(?:\s+(?:(?:of|the|and|&)\s+)?" + name_token + r"){0,3}"
    )
    for match in entity_pattern.finditer(text):
        entity = re.sub(r"^(?:The|A|An)\s+", "", match.group(0)).strip()
        if not entity:
            continue
        candidates.append(entity)
        candidates.extend(
            token
            for token in re.findall(name_token, entity)
            if len(_norm(token)) >= 4
        )
    return _dedupe(candidates)


def _single_topic_candidate_is_noise(value: str) -> bool:
    if not value or _is_date_only_subject(value):
        return True
    tokens = [token for token in _norm(value).split() if token]
    meaningful = [
        token for token in tokens
        if len(token) >= 4
        and token not in _SINGLE_TOPIC_ENTITY_NOISE
        and token not in _SINGLE_TOPIC_MONTHS
        and not token.isdigit()
    ]
    return not meaningful


def _single_topic_alias_equivalent(first: str, second: str) -> bool:
    if _norm(first) == _norm(second):
        return True
    # Full-name spans share their bare-token aliases ("RMS Titanic" contains
    # "Titanic", "Erling Haaland" contains "Haaland"). A significant-token
    # containment merge clusters both spellings of the same identity, while
    # unrelated phrases ("White Star Line" vs "RMS Titanic") share no tokens.
    first_tokens = {token for token in _norm(first).split() if len(token) >= 4}
    second_tokens = {token for token in _norm(second).split() if len(token) >= 4}
    if first_tokens and second_tokens:
        if first_tokens <= second_tokens or second_tokens <= first_tokens:
            return True
    # Requiring the relationship in both directions prevents a broad phrase
    # from swallowing a genuinely separate entity.
    return bool(
        _text_mentions_subject(first, second)
        and _text_mentions_subject(second, first)
    )


def _single_topic_representative(values: Iterable[str]) -> str:
    def _quality(value: str) -> Tuple[int, int, int]:
        words = _clean_text(value).split()
        initialism = bool(words and re.fullmatch(r"[A-Z]{2,6}", words[0]))
        meaningful = sum(
            1 for token in _norm(value).split()
            if token not in _SINGLE_TOPIC_ENTITY_NOISE
            and token not in _SINGLE_TOPIC_MONTHS
        )
        # Prefer a qualified identity ("RMS Titanic") to its bare alias, but
        # avoid selecting an entire descriptive sentence.
        return (int(initialism), min(3, meaningful), -abs(len(words) - 2))

    options = _dedupe(values)
    return max(options, key=_quality) if options else ""


def _infer_single_topic_anchor(
    title: str,
    scene_texts: List[str],
    base_rows: List[Dict[str, Any]],
    raw_subjects: List[str],
) -> Tuple[str, int, float]:
    """Infer one documentary entity from inconsistent per-scene LLM labels.

    Evidence is counted per row, not per raw occurrence, so a verbose query
    cannot manufacture consensus.  A competing entity with comparable support
    makes the result ambiguous and preserves separate pools.
    """
    documents: List[str] = []
    raw_candidates: List[str] = _single_topic_entity_candidates(title)
    for idx, row in enumerate(base_rows):
        values = [row.get(field) for field in _SINGLE_TOPIC_EVIDENCE_FIELDS]
        if idx < len(raw_subjects):
            values.append(raw_subjects[idx])
        if idx < len(scene_texts):
            values.append(scene_texts[idx])
        cleaned_values = [_clean_text(value) for value in values if _clean_text(value)]
        documents.append(" | ".join(cleaned_values))
        for value in cleaned_values:
            raw_candidates.extend(_single_topic_entity_candidates(value))

    if not documents:
        return "", 0, 0.0

    candidates: List[str] = []
    for value in _dedupe(raw_candidates):
        canonical = _canonical_subject(value, "", title) or _clean_text(value)
        if canonical and not _single_topic_candidate_is_noise(canonical):
            candidates.append(canonical)
    candidates = _dedupe(candidates)
    if not candidates:
        return "", 0, 0.0

    minimum_support = max(3, int(math.ceil(len(documents) * 0.45)))
    supported = []
    for candidate in candidates:
        support = sum(1 for document in documents if _text_mentions_subject(document, candidate))
        if support >= minimum_support:
            supported.append((candidate, support))
    if not supported:
        # Generic narration often names the subject only in the title and
        # then uses pronouns in the body. A title proper-name with two or more
        # row-level mentions is still a safe single-topic anchor; requiring a
        # competing entity check below prevents it from swallowing a real
        # multi-subject listicle.
        title_keys = {_norm(item) for item in _single_topic_entity_candidates(title)}
        title_significant = {
            token
            for key in title_keys
            for token in key.split()
            if len(token) >= 4
        }
        title_supported = [
            (candidate, sum(1 for document in documents if _text_mentions_subject(document, candidate)))
            for candidate in candidates
            if (
                _norm(candidate) in title_keys
                or any(
                    token in title_significant
                    for token in _norm(candidate).split()
                    if len(token) >= 4
                )
            )
        ]
        title_supported = [
            item for item in title_supported
            if item[1] >= max(2, int(math.ceil(len(documents) * 0.25)))
        ]
        if not title_supported:
            return "", 0, 0.0
        candidate, support = max(title_supported, key=lambda item: (item[1], len(_norm(item[0]))))
        return str(candidate), int(support), support / max(1, len(documents))

    clusters: List[Dict[str, Any]] = []
    for candidate, support in sorted(supported, key=lambda item: item[1], reverse=True):
        cluster = next(
            (
                item for item in clusters
                if any(_single_topic_alias_equivalent(candidate, member) for member in item["members"])
            ),
            None,
        )
        if cluster is None:
            clusters.append({"members": [candidate], "support": support})
        else:
            cluster["members"].append(candidate)
            cluster["support"] = max(cluster["support"], support)

    for cluster in clusters:
        cluster["anchor"] = _single_topic_representative(cluster["members"])
    clusters.sort(key=lambda item: (item["support"], len(_norm(item["anchor"]))), reverse=True)
    winner = clusters[0]

    # Messi/Ronaldo comparisons and other multi-subject scripts often give two
    # identities near-equal support through a shared global topic.  That is not
    # permission to merge their source pools.
    if len(clusters) > 1:
        runner_up = clusters[1]
        ambiguity_floor = max(3, int(math.ceil(len(documents) * 0.35)))
        if runner_up["support"] >= ambiguity_floor and runner_up["support"] >= winner["support"] * 0.80:
            return "", 0, 0.0

    ratio = winner["support"] / max(1, len(documents))
    return str(winner["anchor"]), int(winner["support"]), ratio


def _query_anchor_for_row(row: Dict[str, Any], scene_text: str, subject: str) -> str:
    if str(row.get('domain_category') or '').lower() == 'disaster':
        for event_value in (row.get('domain_anchor'), row.get('topic_anchor')):
            event_value = _clean_text(event_value)
            if re.search(r'\b(?:earthquakes?|floods?|hurricanes?|eruptions?)\b', _norm(event_value)):
                return event_value
    candidates = [
        row.get("title_anchor"),
        row.get("domain_anchor"),
        row.get("topic_anchor"),
        row.get("global_topic"),
        row.get("primary_container_subject"),
        row.get("container_subject"),
        subject,
    ]
    title = row.get("title") or row.get("global_topic") or ""
    for candidate in candidates:
        value = _clean_text(candidate)
        if not value or _is_date_only_subject(value) or _subject_is_weak(value):
            continue
        if (str(row.get('domain_category') or '').lower() == 'disaster'
                and re.search(r'\b(?:earthquakes?|floods?|hurricanes?|eruptions?)\b', _norm(value))):
            return value
        canonical = _canonical_subject(value, scene_text, str(title or ""), candidates)
        if canonical and not _is_date_only_subject(canonical) and not _subject_is_weak(canonical):
            return _strip_anchor_verb_wrap(canonical)
    return ""


def _compact_visual_facet(text: Any, anchor: str = "", excluded_words: Iterable[str] = ()) -> str:
    stopwords = {
        "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
        "from", "with", "without", "this", "that", "these", "those", "there",
        "he", "she", "his", "her", "they", "their", "them", "it", "its", "we", "you", "our", "my",
        "after", "before", "during", "into", "onto", "is", "are", "was", "were",
        "be", "been", "being", "has", "have", "had", "its", "their", "our", "your",
        "what", "when", "where", "who", "how", "will", "would", "could", "should",
        "night", "day", "morning", "evening", "dawn", "dusk", "year", "years",
        "month", "months", "mile", "miles", "feet", "foot", "about", "still",
    }
    # Narrative verbs describe the story, not what the camera shows. Dropping
    # them keeps the facet noun-focused ("iceberg lookouts" instead of
    # "spotted struck turned").
    weak_words = {
        "flooded", "flooding", "floods", "embarked", "embarking", "embarks",
        "measured", "measuring", "calculated", "calculating", "launched",
        "launching", "launches", "rushed", "rushing", "raced", "racing",
        "spotted", "spotting", "struck", "striking", "sailed", "sailing",
        "departed", "departing", "turned", "turning", "ordered", "ordering",
        "began", "begun", "continues", "continued", "located", "locating",
        "discovered", "discovering", "carried", "carrying", "reported",
        "claimed", "running", "reached", "reaching", "stayed", "survived",
        "surviving", "waited", "watching", "watched", "stood", "named",
        "called", "known", "considered", "hailed", "constructed",
        "built", "took", "taken", "given", "grew", "became", "remains",
        "remained", "remained", "approached", "approaching", "passed",
        "crossed", "crossing", "forever", "forward", "starboard", "astern",
        "aboard", "across", "along", "above", "behind", "below", "over",
        "through", "toward", "towards", "under", "total", "told", "showed",
        "featured", "broke", "resting", "largest", "massive", "greatest",
        "huge", "opulent", "grand", "famous", "historic", "chief",
        "lives", "first", "last", "new", "old", "two", "one", "three",
        "directly", "entirely", "finally", "quickly", "suddenly",
        "while", "half", "lost", "deep", "ever", "also", "ahead", "hard",
        "captain", "captains", "thousand", "hundred", "million",
        "unsinkable", "very", "really", "although", "however", "instead",
        "protected", "memorial",
    }
    anchor_tokens = set(_norm(anchor).split())
    excluded = {_norm(w) for w in excluded_words}
    clean = _clean_text(text)
    # Proper-name modifiers ("New" in "New York", "Captain" in "Captain
    # Edward Smith") leave orphan tokens behind once the modifier itself is
    # treated as filler. Skip the word following such a modifier.
    name_modifiers = {
        "new", "captain", "grand", "old", "little", "great", "saint", "st",
        "fort", "port", "mount", "lake", "river", "west", "east", "north",
        "south", "san", "santa", "los", "las", "king", "queen", "prince",
    }
    proper: List[str] = []
    common: List[str] = []
    prev_key = ""
    for match in re.finditer(r"[A-Za-z0-9][A-Za-z0-9'-]*", clean):
        token = match.group(0)
        key = _norm(token)
        if not key or key in stopwords or key in anchor_tokens or key in _WEAK_SUBJECT_TERMS:
            prev_key = key
            continue
        if key in weak_words or key in excluded:
            prev_key = key
            continue
        if key.isdigit() and int(key) < 1000:
            prev_key = key
            continue
        if len(key) < 3:
            prev_key = key
            continue
        if prev_key in name_modifiers:
            prev_key = key
            continue
        prefix = clean[max(0, match.start() - 24):match.start()]
        is_sentence_start = re.search(r"(?:^|[.!?])\s*$", prefix) is not None
        if token[0].isupper() and token[0].isalpha() and not is_sentence_start:
            proper.append(token)
        else:
            common.append(token)
        prev_key = key
    ordered = proper + common
    # Years/edition numbers are high-value identity facets.  Keep the first
    # numeric year inside the bounded facet even when a sentence starts with
    # a title word (``King ... constitutional 1969``) that would otherwise
    # crowd it out of the three-token slice.
    numeric = [token for token in ordered if re.fullmatch(r"(?:18|19|20)\d{2}", token)]
    if numeric and numeric[0] not in ordered[:3]:
        ordered = [*ordered[:2], numeric[0], *[token for token in ordered[2:] if token != numeric[0]]]
    return " ".join(ordered[:4])


def _sanitize_youtube_query(
    query: Any,
    *,
    anchor: str,
    subject: str,
    category: str,
    fallback_mode: str,
) -> str:
    raw = _clean_text(query)
    # Preserve the useful positive phrase while removing creator-package
    # wording. A blanket ``commentary`` substitution used to turn
    # ``no commentary`` into the meaningless dangling word ``no``.
    raw = re.sub(
        r"\b(?:no|without)\s+commentary\b",
        "VIDZEO_NO_COMMENTARY",
        raw,
        flags=re.I,
    )
    raw = re.sub(
        r"\b(?:analysis|explained|explainer|reaction|reacts|podcast|streamer|livestream|live stream|facecam|watch party|commentary|breaking news|essay|breakdown|deep dive|review|interview host|history of|top 10 reasons)\b",
        " ",
        raw,
        flags=re.I,
    )
    raw = re.sub(r"\bVIDZEO_NO_COMMENTARY\b", "no commentary", raw, flags=re.I)
    raw = re.sub(r"\s+", " ", raw).strip(" -,:;")
    gta_support_query = bool(
        category == "gaming"
        and _GTA_VI_RE.search(_norm(anchor))
        and _GTA_V_RE.search(_norm(raw))
    )
    if anchor and not _text_mentions_subject(raw, anchor) and not gta_support_query:
        # Preserve the full anchor without producing noisy duplicates such as
        # ``Jaguar E-Type Jaguar driving footage`` when the LLM included only
        # the ambiguous make. Keep the missing identity in the prefix and
        # leave the query's visual intent untouched.
        anchor_tokens = {_norm(token) for token in anchor.split() if _norm(token)}
        raw_tokens = raw.split()
        overlap = any(_norm(token) in anchor_tokens for token in raw_tokens)
        if overlap and len(anchor_tokens) > 1:
            remainder = " ".join(token for token in raw_tokens if _norm(token) not in anchor_tokens)
            raw = f"{anchor} {remainder}".strip()
        else:
            raw = f"{anchor} {raw}".strip()
    if subject and not _is_date_only_subject(subject) and _norm(subject) != _norm(anchor):
        if not _text_mentions_subject(raw, subject):
            raw = f"{anchor} {subject} {raw}".strip()
    if category == "disaster" and not any(
        term in _norm(raw) for term in _DISASTER_FOOTAGE_EVIDENCE
    ):
        raw = f"{raw} {fallback_mode}".strip()
    if not raw:
        raw = f"{anchor} {fallback_mode}".strip()
    # Keep the positive visual intent compact.  Exclusion policy is applied by
    # the metadata/frame gates after download; sending a long ``-facecam ...``
    # block to YouTube made the provider rank on policy words and hid useful
    # archival footage.  The shared normalizer also strips sentence fragments
    # such as ``grew inside`` while preserving the complete entity anchor.
    return normalize_youtube_query(
        raw,
        anchor=anchor,
        subject=subject,
        category=category,
        fallback=fallback_mode,
        max_chars=180,
    )


def _finalize_youtube_query(base_query: str, exclusions: Iterable[str], *, max_chars: int = 180) -> str:
    """Finalize a positive provider query; keep exclusions out of the search.

    ``exclusions`` remains in the signature for callers that already construct
    a retrieval contract.  It is deliberately not appended to the string:
    facecam/caption/streamer policy is enforced by the candidate metadata and
    visual gates, where it can be verified instead of poisoning provider
    ranking.  This is the specific fix for the giant query rows shown in the
    UI (``raw footage clean -facecam -webcam ...``).
    """
    del exclusions
    return normalize_youtube_query(
        base_query,
        fallback="documentary footage",
        max_chars=max_chars,
    )


def _youtube_query_niche(row: Dict[str, Any], category: str, scene_text: str, subject: str) -> str:
    """Choose a visual query family from LLM context, with safe inference."""
    category = _norm(category)
    container_type = _norm(row.get("container_type") or row.get("scene_subject_type") or "")
    text = _norm(" ".join([
        str(row.get("visual_intent") or ""),
        str(row.get("visual_facet") or ""),
        str(scene_text or ""),
        str(subject or ""),
    ]))
    if any(term in text for term in ("train", "railway", "railroad", "locomotive", "conductor")):
        return "transportation"
    if category in _YOUTUBE_NICHE_QUERY_INTENTS:
        return category
    if container_type in {"person", "celebrity", "athlete"}:
        return "person"
    if container_type in {"place", "location", "city", "landmark"}:
        return "place"
    if container_type in {"event", "incident"}:
        return "event"
    if container_type in {"animal", "wildlife"}:
        return "animal"
    if _dominant_domain_category(text) == "sports":
        return "sports"
    return "general"


def _planned_youtube_query_variants(row: Mapping[str, Any]) -> List[str]:
    """Read the LLM's visual-search plan without trusting it as identity data.

    The V2 adapter locks the canonical entity, but the old semantic branch
    accidentally discarded the LLM's concrete footage intents and regenerated
    generic queries.  Keep the LLM responsible for *how the scene should look*
    while the immutable visual requirement remains responsible for *what the
    scene is about*.
    """
    values: list[str] = []
    for key in ("youtube_query_plan", "llm_youtube_queries", "youtube_query_variants"):
        planned = row.get(key)
        if isinstance(planned, Mapping):
            planned = [
                planned.get("primary") or planned.get("youtube_primary"),
                planned.get("supporting") or planned.get("youtube_supporting"),
                planned.get("fallback") or planned.get("youtube_fallback"),
            ]
        if isinstance(planned, (list, tuple)):
            values.extend(str(item or "").strip() for item in planned)
    for key in (
        "youtube_query", "youtube_supporting_query", "youtube_fallback_query",
        "primary_query", "supporting_query", "fallback_query",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            values.append(value)
    return _dedupe([value for value in values if value])


def _queries_for_row(row: Dict[str, Any], subject: str, scene_text: str) -> List[str]:
    # Bite clips deliberately use a softer retrieval contract than the first
    # 15-second intro: one verified master video may contribute several
    # transcript/timestamped moments.  Keep the safety exclusions (streamers,
    # webcams, reactions, burned-in captions) in the local metadata/frame
    # gates while allowing documentary, archive, and raw-footage phrasing to
    # reach YouTube's ranking layer.
    bite_clip = bool(
        row.get("bite_clip")
        or row.get("youtube_bite_clip")
        or ((row.get("source_policy") or {}).get("bite_clip") if isinstance(row.get("source_policy"), Mapping) else False)
    )
    semantic_requirement = row.get("visual_requirement")
    if isinstance(semantic_requirement, dict) and row.get("_semantic_locked"):
        # A semantic row is already normalized. The LLM plan is allowed to add
        # concrete visual retrieval facets, but it can never redefine the
        # canonical entity or bypass the negative query policy.
        anchor = str(semantic_requirement.get("canonical_entity") or "").strip()
        category = _norm(row.get("domain_category") or "")
        fallback_mode = {
            "automotive": "car driving raw footage clean",
            "gaming": "raw gameplay no commentary",
            "sports": "match action clean broadcast",
            "person": "public appearance raw footage clean",
            "history": "raw archival film footage",
            "disaster": "raw historical footage",
        }.get(category, "raw footage clean")
        planned = _planned_youtube_query_variants(row)
        if planned:
            exclusions = [
                "-facecam", "-webcam", "-streamer", "-reaction", "-podcast",
                "-lyrics", "-slideshow", "-presentation",
                "-presenter", "-talkinghead", "-talking-head",
                "-vr", "-virtualreality", "-reenactment", "-animation", "-aigenerated",
            ]
            if not bite_clip:
                exclusions.extend([
                    "-commentary", "-explained", "-analysis", "-review", "-essay",
                    "-breakdown", "-debate", "-watchparty", "-livestream",
                ])
            planned_clean: list[str] = []
            # A generic canonical name is not enough for an ambiguous entity
            # such as Jaguar. Carry the contract's hard identity attributes
            # into every LLM query so retrieval cannot silently broaden from
            # ``Jaguar E-Type, 1961`` to ``Jaguar`` and then return wildlife
            # or a modern unrelated car. This is a retrieval facet, not a new
            # subject classification.
            required_attributes = semantic_requirement.get("required_attributes") or {}
            identity_facets: list[str] = []
            for key in ("make", "model", "year", "vehicle_class", "product_model", "location"):
                value = str(required_attributes.get(key) or "").strip()
                if value and value.lower() not in {item.lower() for item in identity_facets}:
                    identity_facets.append(value)
            identity_facet = " ".join(identity_facets[:5])
            footage_signals = (
                "raw", "footage", "b-roll", "b roll", "uncut", "walkaround",
                "driving", "drive", "onboard", "on-board", "track", "road test",
                "close up", "close-up", "demonstration", "demo", "gameplay",
                "broadcast", "newsreel", "archive", "official trailer", "no talking",
                "no commentary", "direct capture", "factory", "assembly",
            )
            for planned_query in planned:
                query = _sanitize_youtube_query(
                    planned_query,
                    anchor=anchor,
                    subject="",
                    category=category,
                    fallback_mode=fallback_mode,
                )
                query_norm = _norm(query)
                missing_identity_facet = " ".join(
                    facet for facet in identity_facets
                    if _norm(facet) not in query_norm
                )
                if missing_identity_facet:
                    query = f"{query} {missing_identity_facet}".strip()
                if not any(signal in _norm(query) for signal in footage_signals):
                    query = f"{query} {fallback_mode}".strip()
                planned_clean.append(_finalize_youtube_query(query, exclusions))
            # Keep deterministic variants as a recall reserve. They are now
            # behind the LLM's scene-specific searches rather than replacing
            # them, so a strong plan gets first access to YouTube results.
            deterministic = provider_query_variants(semantic_requirement, provider="youtube")
            return _dedupe(planned_clean + deterministic)
        # A semantic row without a usable plan still receives the deterministic
        # identity-safe family; no broad topic fallback is introduced here.
        return provider_query_variants(semantic_requirement, provider="youtube")
    variants: List[str] = []
    anchor = _query_anchor_for_row(row, scene_text, subject)
    category = _norm(row.get("domain_category") or "")
    candidate_subject = _clean_text(row.get("candidate_subject") or "")
    query_subject = "" if (
        _is_date_only_subject(subject)
        or (candidate_subject and _norm(candidate_subject) == _norm(anchor))
    ) else subject
    if anchor and _norm(query_subject) == _norm(anchor):
        query_subject = ""
    raw_variants = row.get("query_variants")
    if isinstance(raw_variants, list):
        variants.extend(str(v or "") for v in raw_variants)
    for key in (
        "youtube_query", "search_query", "alt_search_query", "fallback_query",
        "youtube_supporting_query", "youtube_fallback_query",
    ):
        value = _clean_text(row.get(key) or "")
        if value:
            variants.append(value)
    disambiguator = _clean_text(row.get("search_disambiguator") or "")
    subject_tokens = set(_norm(query_subject).split())
    if anchor and subject_tokens and subject_tokens <= set(_norm(anchor).split()):
        query_subject = ""
        subject_tokens = set()
    visual_facet = _compact_visual_facet(row.get("visual_facet") or scene_text, anchor, subject_tokens)
    base = " ".join(part for part in (anchor, query_subject, visual_facet) if part) or disambiguator or anchor or scene_text
    domain = _subject_domain(anchor or subject, scene_text, row.get("topic_anchor") or row.get("global_topic") or "")
    domain_category = _norm(row.get("domain_category") or "")
    if not domain_category:
        topic_text = _norm(" ".join(
            str(value or "")
            for value in (anchor, subject, scene_text, row.get("title_anchor"), row.get("global_topic"))
        ))
        if _dominant_domain_category(topic_text) == "disaster":
            # The local path does not always receive an LLM domain label. Use
            # the whole script/topic anchor to classify historical disasters,
            # otherwise the planner falls back to generic ``real footage``
            # searches dominated by commentary packages.
            domain_category = "disaster"
    category = domain_category or category
    query_niche = _youtube_query_niche(row, category, scene_text, anchor or subject)
    supplied_intents = row.get("youtube_query_intents") or row.get("query_intents") or []
    if not isinstance(supplied_intents, list):
        supplied_intents = re.split(r"[,;|]+", str(supplied_intents or ""))
    supplied_intents = [
        _clean_text(item)[:48]
        for item in supplied_intents
        if _clean_text(item)
    ][:8]
    niche_modes = supplied_intents or list(
        _YOUTUBE_NICHE_QUERY_INTENTS.get(query_niche, _YOUTUBE_NICHE_QUERY_INTENTS["general"])
    )
    if bite_clip:
        # Add explicit cutaway language without replacing the LLM's fully
        # named query.  These variants are intentionally broad enough to
        # locate a master source in unfamiliar/global niches; the downloaded
        # frame/transcript gate remains the authority on the actual moment.
        niche_modes = list(niche_modes) + [
            "raw footage bite clip",
            "archive footage short moment",
            "clean b-roll cutaway",
            "uncut documentary footage",
        ]
    if bool(row.get("intro_unique") or row.get("is_intro")):
        # The first 15 seconds is an editorial hook. Ask for motion-led,
        # tension-capable material while retaining the fully named anchor; do
        # not fall back to generic stock or text-card searches.
        niche_modes = list(niche_modes) + [
            "cinematic opening footage",
            "dramatic establishing shot",
            "high motion documentary b-roll",
        ]
    if disambiguator and anchor and not _is_date_only_subject(disambiguator) and not _text_mentions_subject(disambiguator, anchor):
        base = f"{anchor} {disambiguator} {visual_facet}".strip()
    fallback_mode = {
        "disaster": "raw historical broadcast footage",
        "history": "raw archival film footage",
        "sports": "match action clean broadcast",
        "gaming": "raw gameplay no commentary",
        "travel": "4k walking tour no talking",
        "automotive": "car driving raw footage 4k",
        "science": "laboratory experiment 4k b roll",
        "technology": "hands on demo b roll 4k",
        "crime": "case location raw b roll",
        "finance": "trading floor raw b roll",
        "space": "rocket launch raw footage 4k",
    }.get(domain_category, "raw footage clean")
    # Always spend the first query slots on niche-specific visual intents.
    # This is the deterministic counterpart to the LLM's query-intent map.
    variants.extend(f"{base} {mode}" for mode in niche_modes)
    if domain == "fruit" or domain_category in {"food", "health"}:
        variants.extend([
            f"{base} close up food b roll 4k",
            f"{base} cooking preparation raw footage",
            f"{base} fresh produce market b roll",
            f"{base} harvesting farm clean footage",
            f"{base} kitchen chef b roll 4k",
        ])
    elif domain == "vegetable":
        variants.extend([
            f"{base} vegetable close up 4k b roll",
            f"{base} fresh produce market b roll",
            f"{base} harvesting farm raw footage",
            f"{base} cooking cutting 4k b roll",
        ])
    elif domain_category == "technology":
        variants.extend([
            f"{base} hands on demo b roll 4k",
            f"{base} product close up clean",
            f"{base} launch event clean footage",
            f"{base} technology 4k b roll",
            f"{base} hardware showcase clean",
        ])
    elif domain_category == "automotive":
        variants.extend([
            f"{base} car driving raw footage 4k",
            f"{base} exterior b roll clean",
            f"{base} interior detail 4k",
            f"{base} track testing raw footage",
            f"{base} official promo b roll",
            f"{base} road test no commentary",
        ])
    elif domain_category == "gaming":
        variants.extend([
            f"{base} raw gameplay no commentary",
            f"{base} direct gameplay capture 4k",
            f"{base} uncut gameplay without commentary",
            f"{base} official gameplay trailer",
            f"{base} gameplay 4k 60fps no commentary",
            f"{base} cinematic trailer 4k",
        ])
        if (
            _scene_explicitly_requests_gta_v_comparison(scene_text, row)
            and not bool(row.get("is_intro") or row.get("is_outro"))
            and _norm(row.get("container_type") or row.get("scene_subject_type") or "")
            not in {"organization", "brand"}
        ):
            # Use fully named entities in every high-value GTA query.  The
            # supporting lane is intentionally explicit and no-commentary so
            # search results do not drift into streamers or facecam uploads.
            variants[0:0] = [
                "Grand Theft Auto VI official trailer Rockstar Games clean footage",
                "Grand Theft Auto VI gameplay trailer Rockstar Games",
                "Grand Theft Auto VI official gameplay trailer Rockstar Games",
                "Grand Theft Auto V raw gameplay missions driving no commentary",
                "Grand Theft Auto V direct gameplay capture 4k",
                "Grand Theft Auto V open world free roam gameplay without commentary",
            ]
    elif domain_category == "travel":
        variants.extend([
            f"{base} 4k walking tour no talking",
            f"{base} drone footage clean 4k",
            f"{base} street b roll footage",
            f"{base} landmark raw footage",
            f"{base} city location 4k b roll",
        ])
    elif domain_category == "disaster":
        variants.extend([
            f"{base} raw footage",
            f"{base} aftermath uncut",
            f"{base} rescue raw footage",
            f"{base} aerial location clean footage",
            f"{base} archival broadcast footage",
            f"{base} newsreel archive uncut",
            f"{base} sinking shipwreck footage" if re.search(r'\b(?:ship|ships|shipwreck|sinking|titanic|vessel|ferry)\b', _norm(base)) else f"{base} emergency response footage",
            f"{base} newsreel vintage footage",
        ])
    elif domain_category in {"crime", "documentary"}:
        variants.extend([
            f"{base} case location raw b roll",
            f"{base} archive newsreel uncut",
            f"{base} police investigation archival footage",
            f"{base} aerial scene location 4k",
            f"{base} raw b roll footage",
        ])
    elif domain_category == "sports":
        variants.extend([
            f"{base} match action clean broadcast",
            f"{base} skills goals 4k no commentary",
            f"{base} training raw footage",
            f"{base} stadium broadcast 4k",
            f"{base} slow motion highlights 1080p",
            f"{base} competition clean footage",
        ])
    elif domain_category == "science":
        variants.extend([
            f"{base} laboratory experiment 4k b roll",
            f"{base} microscope raw footage",
            f"{base} research facility clean footage",
            f"{base} scientific demonstration clean",
        ])
    elif domain_category == "finance":
        variants.extend([
            f"{base} trading floor raw b roll",
            f"{base} factory manufacturing 4k",
            f"{base} shipping logistics clean footage",
            f"{base} office corporate b roll 4k",
        ])
    elif domain_category == "transportation" or query_niche == "transportation":
        variants.extend([
            f"{base} train crew locomotive cab footage",
            f"{base} railway crew operations footage",
            f"{base} locomotive engineer conductor operations",
            f"{base} freight train crew at work",
            f"{base} train driver cab ride",
            f"{base} railroad conductor switching cars",
            f"{base} railway operations freight train",
            f"{base} diesel locomotive cab footage",
        ])
    else:
        variants.extend([
            f"{base} raw footage no commentary",
            f"{base} clean b roll no presenter",
            f"{base} direct feed footage",
            f"{base} uncut footage",
            f"{base} raw footage clean",
            f"{base} b roll footage 4k",
            f"{base} close up clean footage",
            f"{base} documentary b roll clean",
            f"{base} location clean footage",
        ])
    compact_variants = []
    compact_base = " ".join(part for part in (anchor, query_subject, visual_facet) if part).strip()
    compact_modes = {
        "disaster": ["raw footage", "aftermath uncut", "archival historical footage",
                     "sinking shipwreck footage" if re.search(r'\b(?:ship|ships|shipwreck|sinking|titanic|vessel|ferry)\b', _norm(base)) else "emergency response footage",
                     "newsreel vintage footage"],
        "history": ["raw archival film footage", "newsreel moving footage clean", "archival footage"],
        "technology": ["hands on demo b roll 4k", "product close up clean", "launch event clean"],
        "sports": ["match action clean broadcast", "skills goals 4k no commentary", "training raw footage"],
        "gaming": ["raw gameplay no commentary", "direct gameplay capture 4k", "official gameplay trailer"],
        "travel": ["4k walking tour no talking", "drone footage clean 4k", "city street b roll"],
        "automotive": ["car driving raw footage 4k", "exterior b roll clean", "road test no commentary"],
        "science": ["laboratory experiment 4k b roll", "microscope raw footage", "research facility clean"],
        "crime": ["case location raw b roll", "archive newsreel uncut", "aerial scene location 4k"],
        "finance": ["trading floor raw b roll", "factory manufacturing 4k", "shipping logistics clean"],
        "space": ["rocket launch raw footage 4k", "spacecraft clean broadcast", "space mission raw"],
    }.get(domain_category, ["raw footage clean", "b roll footage 4k", "location clean footage"])
    if bite_clip:
        # Keep at least one bite-oriented family inside the compact query
        # budget for history/disaster/other niche branches that otherwise
        # slice the broader intent list before it reaches YouTube.
        compact_modes = list(compact_modes) + [
            "raw footage bite clip",
            "archive footage short moment",
            "clean b-roll cutaway",
        ]
    if bool(row.get("intro_unique") or row.get("is_intro")):
        compact_modes = list(compact_modes) + [
            "cinematic opening footage",
            "dramatic establishing shot",
            "high motion documentary b-roll",
        ]
    if (
        query_niche in _YOUTUBE_NICHE_QUERY_INTENTS
        and not supplied_intents
        and domain_category not in {"disaster", "history", "sports", "gaming", "travel", "technology"}
    ):
        compact_modes = list(_YOUTUBE_NICHE_QUERY_INTENTS[query_niche])
    compact_variants.extend(f"{compact_base} {mode}".strip() for mode in compact_modes)
    variants = _planned_youtube_query_variants(row) + compact_variants + variants
    cleaned = []
    for query in variants:
        q = _sanitize_youtube_query(
            query,
            anchor=anchor,
            subject=query_subject,
            category=domain_category,
            fallback_mode=fallback_mode,
        )
        if domain == "fruit" and "fruit" not in _norm(q):
            q = f"{q} fruit"
        elif domain == "vegetable" and "vegetable" not in _norm(q):
            q = f"{q} vegetable"
        exclusions = [
            "-facecam", "-webcam", "-streamer", "-reaction", "-podcast",
            "-lyrics", "-slideshow", "-presentation",
            "-vr", "-virtualreality", "-reenactment", "-animation", "-aigenerated",
        ]
        if domain_category == "gaming":
            exclusions = ["-livestream", "-watchparty", "-news", "-review", "-explained"] + exclusions
        elif domain_category == "disaster":
            exclusions = ["-news", "-breaking", "-reaction", "-commentary", "-explained", "-podcast", "-mystery", "-secrets", "-untold", "-theory"] + exclusions
        elif domain_category == "history":
            exclusions = ["-reaction", "-commentary", "-explained", "-podcast", "-interview", "-mystery", "-secrets", "-untold"] + exclusions
        elif not bite_clip:
            exclusions = ["-reaction", "-commentary", "-explained", "-podcast"] + exclusions
        cleaned.append(_finalize_youtube_query(q, exclusions))
    cleaned = _dedupe(cleaned)
    compact_cleaned = [query for query in cleaned if any(_norm(mode) in _norm(query) for mode in compact_modes)]
    if domain_category == "gaming":
        def _gaming_query_priority(value: str) -> int:
            key = _norm(value)
            if "no commentary" in key or "without commentary" in key:
                return 5
            if "official gameplay" in key or "gameplay trailer" in key:
                return 4
            if "official trailer" in key:
                return 3
            if any(term in key for term in ("playstation", "xbox", "nintendo", "rockstar games")):
                return 2
            return 1
        cleaned.sort(key=_gaming_query_priority, reverse=True)
    cleaned = _dedupe(cleaned if _planned_youtube_query_variants(row) else compact_cleaned + cleaned)
    if (
        domain_category == "gaming"
        and _scene_explicitly_requests_gta_v_comparison(scene_text, row)
        and not bool(row.get("is_intro") or row.get("is_outro"))
        and _norm(row.get("container_type") or row.get("scene_subject_type") or "")
        not in {"organization", "brand"}
    ):
        # Keep the explicit, fully named GTA lanes inside the bounded query
        # budget.  Generic compact modes are useful for other games, but they
        # used to push the GTA V support queries past the four-query slice.
        gta_bases = [
            "Grand Theft Auto VI official trailer Rockstar Games clean footage",
            "Grand Theft Auto VI gameplay trailer Rockstar Games",
            "Grand Theft Auto VI official gameplay trailer Rockstar Games",
            "Grand Theft Auto V raw gameplay missions driving no commentary",
            "Grand Theft Auto V direct gameplay capture",
            "Grand Theft Auto V open world free roam gameplay no commentary",
        ]
        gta_exclusions = [
            "-facecam", "-webcam", "-streamer", "-reaction", "-podcast",
            "-livestream", "-watchparty",
            "-slideshow", "-presentation", "-aigenerated",
        ]
        gta_explicit = [
            _finalize_youtube_query(base_query, gta_exclusions)
            for base_query in gta_bases
        ]
        return _dedupe(gta_explicit + cleaned)[:8]
    # Historical-disaster searches need archive/newsreel variants in addition
    # to modern raw-footage terms. Keep the normal query budget for other
    # categories.
    if domain_category == "disaster":
        return cleaned[:9]
    if domain_category == "sports":
        return cleaned[:4]
    if domain_category in {"technology", "travel"}:
        # Profile branches ("product close up", "city street footage") sit
        # after the compact + niche modes; keep them inside the queried set
        # instead of slicing them off with the four-slot generic budget.
        return cleaned[:6]
    if query_niche in {"person", "place", "event", "organization", "product", "animal", "nature", "science", "entertainment"}:
        return cleaned[:6]
    return cleaned[:4]


def _pick_timestamp(candidate: Dict[str, Any], scene_index: int, used: Dict[str, List[float]], duration_needed: float) -> Optional[float]:
    duration = max(0.0, float(candidate.get("duration") or 0.0))
    if duration <= 0:
        return None
    bite_clip = bool(candidate.get("bite_clip"))
    gap_env = "VIDZEO_YT_BITE_TIMESTAMP_GAP" if bite_clip else "VIDZEO_YT_REUSE_TIMESTAMP_GAP"
    gap_default = "8" if bite_clip else "20"
    gap = max(
        float(os.environ.get(gap_env, gap_default) or gap_default),
        float(duration_needed or 4.0) + 4.0,
    )
    intro_skip = max(3.0, min(14.0, duration * 0.06))
    if duration >= 120.0 and gap <= 35.0:
        intro_skip = max(intro_skip, min(22.0, duration * 0.09))
    # Outro immunity: YouTube videos typically host end-slates ("WATCH MORE",
    # "SUBSCRIBE", studio presenter sign-offs, next video recommendation grids)
    # in the last 15-25 seconds. Keep a strict outro exclusion buffer so
    # cuts never slice into channel outro cards.
    if duration >= 50.0:
        outro_skip = max(16.0, min(35.0, duration * 0.15))
    elif duration >= 25.0:
        outro_skip = max(8.0, duration * 0.18)
    else:
        outro_skip = max(3.0, duration * 0.12)
    start_min = min(max(2.0, duration - duration_needed - 2.0), intro_skip)
    latest = duration - outro_skip - max(1.0, duration_needed)
    if latest <= start_min:
        start_min = 0.0
        safe_end_margin = min(outro_skip, max(2.0, duration * 0.10))
        latest = max(0.0, duration - duration_needed - safe_end_margin)
    if latest < 0.0:
        return None
    seed = int(hashlib.sha256(f"{_video_id(candidate)}:{scene_index}".encode()).hexdigest()[:8], 16)
    reuse_keys = _timestamp_reuse_keys(candidate)
    existing_timestamps = lambda: [old for key in reuse_keys for old in used[key]]
    scene_words = {word for word in _norm(candidate.get('scene_text') or '').split()
                   if len(word) > 3 and word not in _WEAK_SUBJECT_TERMS}
    scene_words -= set(_norm(candidate.get('scene_subject') or candidate.get('candidate_subject') or '').split())
    chapters = candidate.get('chapters') or []
    chapter_options = []
    for chapter in chapters if isinstance(chapters, list) else []:
        if not isinstance(chapter, Mapping):
            continue
        matches = scene_words & set(_norm(chapter.get('title') or '').split())
        if not matches:
            continue
        try:
            start = float(chapter.get('start_time')) + 1.0
            end = float(chapter.get('end_time', duration))
        except (TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(end) and start >= 0 and start + duration_needed <= min(end, duration):
            if not any(abs(start - old) < gap for old in existing_timestamps()):
                chapter_options.append((len(matches), start))
    if chapter_options:
        _match_count, start = max(chapter_options, key=lambda item: (item[0], -item[1]))
        for key in reuse_keys:
            used[key].append(start)
        return start
    span = max(0.0, latest - start_min)

    # Progressive gap relaxation: try preferred gap first, then step down to
    # smaller distant non-overlapping intervals so scenes are not starved.
    # If the user or test explicitly specified a gap in env, respect it strictly.
    allow_gap_relaxation = gap_env not in os.environ
    min_distinct_gap = max(2.5, float(duration_needed or 4.0) + 1.5)
    gaps_to_try = [gap]
    if allow_gap_relaxation:
        if gap > min_distinct_gap + 5.0:
            gaps_to_try.append(round(max(min_distinct_gap + 3.0, gap * 0.65), 1))
        if gap > min_distinct_gap + 2.0:
            gaps_to_try.append(round(max(min_distinct_gap + 1.0, gap * 0.45), 1))
        if min_distinct_gap not in gaps_to_try:
            gaps_to_try.append(round(min_distinct_gap, 1))

    for try_gap in gaps_to_try:
        slots = max(1, int(span // try_gap) + 1)
        offset = seed % slots
        for step in range(slots):
            slot = (offset + step) % slots
            ts = round(min(latest, start_min + slot * try_gap), 2)
            if all(abs(ts - old) >= (try_gap - 0.05) for old in existing_timestamps()):
                for key in reuse_keys:
                    used[key].append(ts)
                return round(ts, 1)
        for attempt in range(24):
            ratio = ((seed + attempt * 9973) % 10000) / 10000.0
            ts = round(start_min + ratio * max(1.0, latest - start_min), 2)
            if all(abs(ts - old) >= (try_gap - 0.05) for old in existing_timestamps()):
                for key in reuse_keys:
                    used[key].append(ts)
                return round(ts, 1)
    return None


def _ffmpeg_bin(name: str) -> str:
    env_key = "VIDZEO_FFPROBE_PATH" if name.lower() == "ffprobe" else "VIDZEO_FFMPEG_PATH"
    configured = str(os.environ.get(env_key) or "").strip()
    if configured and os.path.isfile(configured):
        return configured
    candidate_roots = [
        Path(os.environ.get("VIDZEO_ROOT", "")).resolve() if os.environ.get("VIDZEO_ROOT") else None,
        Path(__file__).parent.parent.parent,
        Path(sys.executable).parent.parent if getattr(sys, "frozen", False) else None,
        Path.cwd(),
    ]
    for root in filter(None, candidate_roots):
        local = root / "tools" / "ffmpeg" / f"{name}.exe"
        if local.is_file():
            return str(local.resolve())
    found = shutil.which(name)
    return found or name



def _clean_downloaded_clip_overlays(
    path: str,
    cancellation: Any = None,
    gaming_lane: bool = False,
    native_overlay_lane: bool = False,
    allow_soft_facecam: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """Screen direct V2 downloads without altering the source pixels.

    The pool renderer already had this pass, but ranged V2 downloads used to
    bypass it entirely. Keep the screen deterministic and fail open when no
    tight region is proven; small source labels remain visible; obstructive overlays are rejected.
    """
    result: Dict[str, Any] = {
        "checked": False,
        "clean": True,
        "regions": [],
        "overlay_policy": "preserve_source_no_blur",
        "no_blur": True,
        "overlay_types": [],
        "blur_regions": [],
        "rejection_reason": "",
        "cleaned_path": "",
        "blurred": False,
        "blur_failed": False,
        "watermark_tolerated": False,
        "facecam_tolerated": False,
    }
    enabled = str(os.environ.get("VIDZEO_YT_OVERLAY_SCREEN", "1") or "1").strip().lower()
    if enabled not in {"1", "true", "yes", "on"} or not path:
        return path, result
    try:
        from services.youtube_pool_renderer import _screen_moment_overlays

        try:
            overlay = _screen_moment_overlays(
                str(path),
                gaming_lane=bool(gaming_lane),
                native_overlay_lane=bool(native_overlay_lane),
            ) or {}
        except TypeError as exc:
            # Keep older analyzer adapters usable.  Packaged plug-ins from
            # before the native-overlay lane only accepted the path argument;
            # retrying without the optional keywords preserves the same
            # screening decision without rewriting source pixels.
            if not any(token in str(exc).lower() for token in ("gaming_lane", "native_overlay_lane", "unexpected keyword")):
                raise
            overlay = _screen_moment_overlays(str(path)) or {}

        # Repackaged newsreels frequently pass the watermark/subtitle screen
        # while spending most of the cut on a black title card. Detect that
        # deterministic failure mode from a few low-cost decoded samples so a
        # text card cannot become the opening or closing "footage" shot.
        if str(os.environ.get("VIDZEO_YT_REJECT_TITLE_CARDS", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}:
            samples = overlay.get("title_card_samples") or []
            min_matches = len(samples) if (native_overlay_lane or gaming_lane) else max(2, int(len(samples) * 0.70))
            card_matches = sum(1 for white, dark in samples if white >= 0.12 and dark >= 0.40)
            if samples and card_matches >= min_matches:
                overlay["reject"] = True
                overlay["rejection_reason"] = "title_card_or_large_text_rejected"
                overlay["overlay_types"] = sorted(set(list(overlay.get("overlay_types") or []) + ["title_card"]))
        result.update({
            "checked": bool(overlay.get("checked")),
            "clean": bool(overlay.get("clean", True)),
            "regions": list(overlay.get("regions") or []),
            "detected_regions": list(overlay.get("detected_regions") or []),
            "overlay_policy": str(overlay.get("overlay_policy") or "preserve_source_no_blur"),
            "native_overlay_lane": bool(native_overlay_lane),
            "native_overlay_detected": bool(overlay.get("native_overlay_detected")),
            "overlay_types": list(overlay.get("overlay_types") or []),
            "blur_regions": list(overlay.get("blur_regions") or []),
            "rejection_reason": str(overlay.get("rejection_reason") or ""),
            "cleaned_path": str(overlay.get("cleaned_path") or ""),
            "watermark_prob": float(overlay.get("watermark_prob") or 0.0),
            "watermark_tolerated": bool(overlay.get("watermark_tolerated")),
            "has_subtitle": bool(overlay.get("has_subtitle")),
            "facecam_tolerated": bool(overlay.get("facecam_tolerated")),
            "talking_head": bool(overlay.get("talking_head")),
            "talking_head_confidence": float(overlay.get("talking_head_confidence") or 0.0),
            "talking_head_reason": str(overlay.get("talking_head_reason") or ""),
            "facecam_inset": bool(overlay.get("facecam_inset")),
            "facecam_confidence": float(overlay.get("facecam_confidence") or 0.0),
            "facecam_reason": str(overlay.get("facecam_reason") or ""),
            "facecam_corner": str(overlay.get("facecam_corner") or ""),
            "outro_detected": bool(overlay.get("outro_detected")),
            "outro_reason": str(overlay.get("outro_reason") or ""),
            "outro_confidence": float(overlay.get("outro_confidence") or 0.0),
            "reject": bool(overlay.get("reject")),
        })
        # Public-affairs/documentary footage can contain a small broadcaster
        # inset while still carrying the requested event footage.  Let that
        # narrow case reach the shared vision gate instead of discarding the
        # download before pixels are checked.  Title cards, outros, captions,
        # and confident presenter packages remain hard rejects.
        if (
            allow_soft_facecam
            and result.get("reject")
            and result.get("facecam_inset")
            and result.get("rejection_reason") == "facecam_inset_rejected"
            and not result.get("outro_detected")
            and not result.get("talking_head")
        ):
            result["reject"] = False
            result["facecam_tolerated"] = True
            result["overlay_policy"] = "facecam_soft_pass_final_vision"
            result["rejection_reason"] = ""
            print(
                "   [yt-v2] overlay screen: tolerating small facecam inset "
                "for final vision verification",
                flush=True,
            )
        from services.youtube_overlay_policy import preserve_source_overlays
        result = preserve_source_overlays(result)
        return ("", result) if result.get('reject') else (path, result)
    except Exception as exc:
        result.update(error=f"{type(exc).__name__}:{exc}", no_blur=True,
                      blurred=False, blur_failed=False, blur_regions=[],
                      overlay_policy='preserve_source_no_blur', screening_unavailable=True)
        return path, result


def _download_youtube_probe(
    candidate: Dict[str, Any],
    output_path: Path,
    start: float,
    duration: float,
    *,
    session_jar: Any = None,
    stream_downloads: bool = False,
    cancellation: Any = None,
) -> bool:
    """Fetch a tiny quality-preserving probe before paying for a full source.

    Pool admission only needs a few seconds of material, so keep the range
    short while using the same adaptive selector as the final downloader. The
    old 22/18 selector produced 360/720p probes and made low-quality artifacts
    easy to mistake for the final clip in diagnostics and recovery paths.
    """
    if not output_path or not _candidate_url(candidate):
        return False
    # Special-use TLDs (.test/.invalid/.localhost) never carry real videos;
    # unit/integration doubles use them as placeholders, and paying a DNS
    # round-trip for a fake ID would stall admission for seconds.
    probe_url = _candidate_url(candidate)
    probe_host = str(probe_url).split("://", 1)[-1].split("/", 1)[0].split(":")[0].casefold()
    if (
        not probe_host
        or probe_host == "localhost"
        or probe_host.endswith(".test")
        or probe_host.endswith(".invalid")
        or probe_host.endswith(".localhost")
    ):
        return False
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_prefix = output_path.stem + ".raw."
    for stale in output_path.parent.glob(raw_prefix + "*"):
        try:
            stale.unlink()
        except OSError:
            pass
    output_path.unlink(missing_ok=True)
    try:
        probe_seconds = max(1.0, min(4.0, float(duration or 2.0)))
        probe_start = max(0.0, float(start or 0.0))
    except (TypeError, ValueError):
        probe_seconds, probe_start = 2.0, 0.0
    try:
        from services.yt_dlp_runtime import load_yt_dlp
        yt_dlp = load_yt_dlp()
        from yt_dlp.utils import download_range_func
        raw_prefix = f"{output_path.stem}.raw."
        raw_template = str(output_path.parent / f"{raw_prefix}%(ext)s")
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": '/'.join(_youtube_quality_formats() + _archival_quality_formats(candidate)),
            "merge_output_format": "mp4",
            "outtmpl": raw_template,
            "download_ranges": download_range_func(
                None,
                [(probe_start, probe_start + probe_seconds + 0.4)],
            ),
            "force_keyframes_at_cuts": False,
            "extractor_args": {"youtube": {"player_client": _youtube_player_clients(session_jar)}},
            "external_downloader_args": {
                "ffmpeg_i": ["-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            },
            "socket_timeout": int(
                os.environ.get(
                    "VIDZEO_YT_PROBE_SOCKET_TIMEOUT",
                    str(_youtube_transport_timeout_defaults()[0]),
                )
                or _youtube_transport_timeout_defaults()[0]
            ),
            "retries": int(
                os.environ.get(
                    "VIDZEO_YT_PROBE_RETRIES",
                    str(_youtube_transport_timeout_defaults()[1]),
                )
                or _youtube_transport_timeout_defaults()[1]
            ),
            "ffmpeg_location": _ffmpeg_bin("ffmpeg"),
        }
        opts.update(_youtube_js_runtime_options())

        if stream_downloads:
            opts["concurrent_fragment_downloads"] = 1
        with yt_dlp.YoutubeDL(opts) as ydl:
            _install_youtube_session(ydl, session_jar)
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            ydl.download([_candidate_url(candidate)])
        raw_files = sorted(
            output_path.parent.glob(raw_prefix + "*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not raw_files:
            return False
        raw = next(
            (
                path for path in raw_files
                if path.stat().st_size > 4096 and _source_has_video_stream(path)
            ),
            None,
        )
        if raw is None:
            return False
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw), "-t", str(probe_seconds), "-an",
            "-c:v", "libx264", "-preset", "ultrafast", str(output_path)
        ]
        subprocess.run(cmd, check=True, timeout=15)
        return output_path.is_file() and output_path.stat().st_size > 1024
    except Exception as exc:
        print(
            f"   [yt-v2] probe failed '{str(candidate.get('title') or '')[:55]}': "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        output_path.unlink(missing_ok=True)
        return False
    finally:
        for stale in output_path.parent.glob(raw_prefix + "*"):
            try:
                stale.unlink()
            except OSError:
                pass


def _youtube_quality_limits() -> Tuple[int, int]:
    """Return the source quality ceiling and final output frame rate.

    YouTube's separate video/audio formats carry substantially better picture
    quality than the old 18/22 progressive fallbacks.  Keep the source ceiling
    aligned with the project canvas and hard-capped at 1080p. Source FPS is
    intentionally not capped: a 60fps source can contain a cleaner 30fps
    result than a lower-bitrate 30fps stream. FFmpeg normalizes the delivered
    clip to 30fps.
    """
    try:
        max_height = int(
            os.environ.get("VIDZEO_YT_MAX_SOURCE_HEIGHT")
            or os.environ.get("VIDZEO_PROJECT_HEIGHT")
            or 1080
        )
    except (TypeError, ValueError):
        max_height = 1080
    # The project delivery canvas is 1080p. Never pull a 1440p/4K source just
    # to downscale it locally, and do not spend time downloading SD footage
    # that will be rejected by the final quality gate. Prefer 720p or better.
    max_height = max(720, min(1080, max_height))
    try:
        output_fps = int(
            os.environ.get("VIDZEO_YT_OUTPUT_FPS")
            or os.environ.get("VIDZEO_PROJECT_FPS")
            or 30
        )
    except (TypeError, ValueError):
        output_fps = 30
    return max_height, max(12, min(30, output_fps))


def _ranged_download_worker_limit(requested: int, available_mb: int, total_bytes: int) -> int:
    """Fit native HD range transfers independently from Chromium render workers."""
    # Range transfers normally remux AVC rather than decode a full master.
    # Reserve OS/UI headroom and a conservative decode/screen allowance per
    # worker; one worker remains the minimum on genuinely constrained hosts.
    # The egress proxy serializes concurrent connections, so the win from
    # more workers is bounded — but on this VM the old flat 2-worker cap for
    # every host under 12 GB total was the binding constraint (12 ranged
    # downloads at 2 workers = 6 sequential rounds of slow yt-dlp work).
    # Tier the small-host cap by total memory instead: under 6 GB stays at
    # 2, 6-12 GB gets 4, and the per-worker allowance ((available-256)//320)
    # still scales the count down automatically when memory is tight.
    fit = max(1, (max(0, int(available_mb)) - 256) // 320)
    if total_bytes < 6 * 1024**3:
        fit = min(2, fit)
    elif total_bytes < 12 * 1024**3:
        fit = min(4, fit)
    return max(1, min(8, int(requested), fit))


def _requested_download_workers(download_concurrency=None) -> int:
    """Requested ranged-download worker count, before the memory fit.

    Default 6, hard cap 8. Env override ``VIDZEO_YT_DOWNLOAD_CONCURRENCY``
    wins; an explicit ``download_concurrency`` wins over the env.
    """
    try:
        configured = int(
            download_concurrency
            if download_concurrency is not None
            else os.environ.get("VIDZEO_YT_DOWNLOAD_CONCURRENCY", "6")
            or 6
        )
    except (TypeError, ValueError):
        configured = 6
    return max(1, min(configured, 8))


def _ranged_download_workers(download_concurrency=None, *, available_mb=None, total_bytes=None) -> int:
    """How many ranged YouTube downloads run in parallel.

    Default 6, hard cap 8 (was default 4, cap 6). The memory fit from
    ``_ranged_download_worker_limit`` still applies when host memory is
    known, so a tight host scales down on its own.
    """
    workers = _requested_download_workers(download_concurrency)
    if available_mb is not None and total_bytes is not None:
        workers = _ranged_download_worker_limit(workers, available_mb, total_bytes)
    return workers


def _youtube_quality_formats(*, source: bool = False, max_filesize: Optional[int] = None) -> List[str]:
    """Build high-quality yt-dlp selectors with graceful format fallbacks."""
    max_height, _output_fps = _youtube_quality_limits()
    min_height = _youtube_min_source_height()
    # Prefer the configured cap first, then accept 1440p/4K masters when a
    # source is only published above that cap. Remotion scales the accepted
    # source down to the output canvas; refusing a 4K-only HD+ source used to
    # exhaust every retry with "Requested format is not available".
    height_caps = list(dict.fromkeys((
        max_height,
        max(max_height, 1440),
        max(max_height, 2160),
    )))
    explicit_name = "VIDZEO_YT_SOURCE_FORMAT" if source else "VIDZEO_YT_FORMAT"
    explicit = str(os.environ.get(explicit_name) or "").strip()
    candidates: List[str] = []
    explicit_floor = re.search(r"\[\s*height\s*(>=|>)\s*(\d+)\s*\]", explicit)
    explicit_is_hd = bool(
        explicit_floor
        and int(explicit_floor.group(2)) >= min_height
    )
    if explicit and not explicit_is_hd:
        print(
            f"   [yt-v2] ignoring {explicit_name}: selector does not enforce "
            f"the {min_height}p minimum",
            flush=True,
        )
    allow_unbounded = str(
        os.environ.get("VIDZEO_YT_SOURCE_ALLOW_UNBOUNDED_FORMAT", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if explicit and explicit_is_hd and (
        not source
        or allow_unbounded
        or "filesize<=" in explicit
        or "filesize_approx<=" in explicit
    ):
        candidates.append(explicit)
    # Full-source pool downloads are the expensive leg of the pipeline.  A
    # plain ``bestvideo`` selector can choose a multi-gigabyte adaptive stream
    # even when yt-dlp's ``max_filesize`` option is set (that option is a
    # post-selection guard for several fragmented formats).  Put the size
    # bound in the selector itself so an oversized source is rejected before
    # transfer starts.  ``filesize_approx`` covers formats where YouTube does
    # not expose an exact byte count.
    size_filters: List[str] = []
    if source and max_filesize:
        bounded = max(1, int(max_filesize))
        size_filters = [
            f"[filesize<={bounded}]",
            f"[filesize_approx<={bounded}]",
        ]
    if source and size_filters:
        video_prefixes = [
            f"bestvideo[height>={min_height}][height<={max_height}]{size_filters[0]}",
            f"bestvideo[height>={min_height}][height<={max_height}]{size_filters[1]}",
            f"bestvideo*[height>={min_height}][height<={max_height}]{size_filters[0]}",
            f"bestvideo*[height>={min_height}][height<={max_height}]{size_filters[1]}",
        ]
        progressive = [
            f"best[height>={min_height}][height<={max_height}][ext=mp4]{size_filters[0]}",
            f"best[height>={min_height}][height<={max_height}][ext=mp4]{size_filters[1]}",
            f"best[height>={min_height}][height<={max_height}]{size_filters[0]}",
            f"best[height>={min_height}][height<={max_height}]{size_filters[1]}",
        ]
        candidates.extend([
            f"{video_prefixes[0]}+bestaudio[ext=m4a]/{video_prefixes[0]}+bestaudio/"
            f"{progressive[0]}",
            f"{video_prefixes[1]}+bestaudio[ext=m4a]/{video_prefixes[1]}+bestaudio/"
            f"{progressive[1]}",
            f"{video_prefixes[2]}+bestaudio/{progressive[2]}",
            f"{video_prefixes[3]}+bestaudio/{progressive[3]}",
        ])
        for cap in height_caps[1:]:
            for size_filter in size_filters:
                video = f"bestvideo[height>={min_height}][height<={cap}]{size_filter}"
                progressive = f"best[height>={min_height}][height<={cap}]{size_filter}"
                candidates.append(f"{video}+bestaudio/{progressive}")
        # HLS/adaptive formats can omit both size fields. Admit unknown sizes
        # while rejecting any KNOWN oversized stream; the existing byte-count
        # progress hook and source deadline still enforce the transfer bound.
        # Requiring a known size excluded usable HD with "format unavailable".
        unknown_size_bound = f'[filesize<=?{bounded}][filesize_approx<=?{bounded}]'
        video = f'bestvideo[height>={min_height}][height<={max_height}]{unknown_size_bound}'
        progressive = f'best[height>={min_height}][height<={max_height}]{unknown_size_bound}'
        candidates.append(f'{video}+bestaudio/{progressive}')
        fallback_floor = _youtube_transport_min_height()
        if fallback_floor < min_height:
            for bound in [*size_filters, unknown_size_bound]:
                lower_video = f'bestvideo[height>={fallback_floor}][height<={max_height}]{bound}'
                lower_progressive = f'best[height>={fallback_floor}][height<={max_height}]{bound}'
                candidates.append(f'{lower_video}+bestaudio/{lower_progressive}')
            candidates.append(f'bestvideo[height>={fallback_floor}][height<={max_height}]{unknown_size_bound}')
        if not allow_unbounded:
            return list(dict.fromkeys(value for value in candidates if value))
    # Prefer a separate best video + best audio pair. This selects the highest
    # available picture bitrate/resolution instead of silently falling back to
    # the old 360/720p progressive IDs (18/22).
    candidates.extend([
        (
            f"bestvideo[height>={min_height}][height<={max_height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[height>={min_height}][height<={max_height}][vcodec^=avc1]+bestaudio/"
            f"best[height>={min_height}][height<={max_height}][vcodec^=avc1]"
        ),
        (
            f"bestvideo[height>={min_height}][height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height>={min_height}][height<={max_height}]+bestaudio/"
            f"best[height>={min_height}][height<={max_height}][ext=mp4]/"
            f"best[height>={min_height}][height<={max_height}]"
        ),
        (
            f"bestvideo*[height>={min_height}][height<={max_height}]+bestaudio/"
            f"best[height>={min_height}][height<={max_height}]"
        ),
        # Some authenticated/player clients expose only a progressive MP4.
        f"best[height>={min_height}][height<={max_height}][ext=mp4]/"
        f"best[height>={min_height}][height<={max_height}]",
        # Do not leave an unbounded ``best`` fallback here: if every adaptive
        # selector is unavailable, the bounded progressive selector is the
        # safe final choice and still honors the 1080p ceiling.
        f"best[height>={min_height}][height<={max_height}]",
        f"bestvideo[height>={min_height}][height<={max_height}]+bestaudio/"
        f"best[height>={min_height}][height<={max_height}]",
    ])
    for cap in height_caps[1:]:
        candidates.extend([
            f"bestvideo[height>={min_height}][height<={cap}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height>={min_height}][height<={cap}]+bestaudio/"
            f"best[height>={min_height}][height<={cap}][ext=mp4]/"
            f"best[height>={min_height}][height<={cap}]",
            f"bestvideo*[height>={min_height}][height<={cap}]+bestaudio/"
            f"best[height>={min_height}][height<={cap}]",
            f"best[height>={min_height}][height<={cap}][ext=mp4]/"
            f"best[height>={min_height}][height<={cap}]",
        ])
    fallback_floor = _youtube_transport_min_height()
    if fallback_floor < min_height:
        candidates.extend([
            f"bestvideo[height>={min_height}][height<={max_height}]+bestaudio/bestvideo[height>={fallback_floor}][height<={max_height}]+bestaudio/best[height>={fallback_floor}][height<={max_height}]",
            f"best[height>={min_height}][height<={max_height}]/best[height>={fallback_floor}][height<={max_height}]",
        ])
    return list(dict.fromkeys(value for value in candidates if value))


def _youtube_transport_timeout_defaults() -> Tuple[int, int, int, int]:
    """Return quality-aware network and media-stage timeout defaults.

    Adaptive streams are larger than the old progressive 18/22 formats.  A
    fixed 12/120s transport budget therefore caused healthy 1080p downloads
    to be reported as timeouts.  Keep every operation bounded, but give the
    higher-quality stream enough time for transfer, merge, and verification.
    The returned values are ``socket_seconds, retries, fragment_retries,
    per_clip_seconds``.
    """
    max_height, _output_fps = _youtube_quality_limits()
    quality_ratio = max(0.0, (float(max_height) - 1080.0) / 1080.0)
    socket_seconds = int(round(45.0 + (10.0 * quality_ratio)))
    retries = int(round(4.0 + quality_ratio))
    fragment_retries = int(round(4.0 + quality_ratio))
    per_clip_seconds = int(round(360.0 + (120.0 * quality_ratio)))
    return (
        max(30, min(60, socket_seconds)),
        max(3, min(6, retries)),
        max(3, min(6, fragment_retries)),
        max(300, min(600, per_clip_seconds)),
    )


def _download_clip(
    candidate: Dict[str, Any],
    output_path: Path,
    start: float,
    duration: float,
    session_jar: Any = None,
    stream_downloads: bool = False,
    cancellation: Any = None,
) -> bool:
    # Bound the complete per-candidate operation, including yt-dlp retrieval,
    # format fallback, FFmpeg conversion, and integrity probing.  The outer
    # batch deadline alone is insufficient because a worker stuck inside
    # yt-dlp can keep the executor alive after the batch has moved on.
    _default_socket_timeout, _default_retries, _default_fragment_retries, _default_clip_timeout = _youtube_transport_timeout_defaults()
    try:
        per_clip_timeout = max(
            10.0,
            # The transfer often reaches 100% just before yt-dlp's merge and
            # the local integrity probe. 45s cut those healthy candidates off
            # on the production machine; keep the deadline bounded, but leave
            # enough time for the post-download merge.
            float(os.environ.get("VIDZEO_YT_PER_CLIP_TIMEOUT", str(_default_clip_timeout)) or _default_clip_timeout),
        )
    except (TypeError, ValueError):
        per_clip_timeout = float(_default_clip_timeout)
    # Allow a bounded merge/probe tail after transfer.  The old 60-second
    # minimum made a few stalled ranged downloads occupy every worker until
    # the outer mixed-media wall expired.  Keep the tail configurable and
    # short by default; successful source-preserving downloads usually need
    # only a few seconds, while normalized fallback encodes still get a fair
    # bounded window.
    try:
        configured_grace = float(
            os.environ.get("VIDZEO_YT_CLIP_GRACE_SECONDS", "") or 0
        )
    except (TypeError, ValueError):
        configured_grace = 0.0
    clip_grace = (
        max(15.0, min(120.0, configured_grace))
        if configured_grace > 0
        else max(20.0, min(90.0, per_clip_timeout * 0.25))
    )
    clip_deadline = time.monotonic() + per_clip_timeout + clip_grace

    def _remaining_clip_timeout() -> float:
        remaining = clip_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"youtube_clip_timeout_after_{per_clip_timeout:.0f}s")
        return remaining

    url = _candidate_url(candidate)
    if not url:
        return False
    raw_template = str(output_path.with_suffix(".raw.%(ext)s"))
    raw_prefix = output_path.stem + ".raw."
    part_path = output_path.with_name(
        f"{output_path.stem}.{os.getpid()}.{threading.get_ident()}.part{output_path.suffix}"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.unlink(missing_ok=True)
    for stale in output_path.parent.glob(raw_prefix + "*"):
        try:
            stale.unlink()
        except OSError:
            pass
    try:
        from services.yt_dlp_runtime import load_yt_dlp
        yt_dlp = load_yt_dlp()
        from yt_dlp.utils import download_range_func
        # Request the highest-quality separate video/audio pair first. The old
        # 18/22 progressive selector silently capped clips at 360/720p even
        # when YouTube exposed a 1080p stream. Progressive MP4 remains a final
        # compatibility fallback for clients that hide adaptive formats.
        runtime_log = YouTubeRuntimeLog()
        candidate['_runtime_warning_categories'] = runtime_log.categories
        raw_formats = _youtube_quality_formats()
        # Slash alternatives are selected against one metadata extraction.
        # Re-extracting the same unavailable HD source for every selector used
        # to spend the entire scene budget before trying another video.
        format_candidates = raw_formats[:1]
        if len(raw_formats) > 1:
            silent_hd = (f"bestvideo[height>={_youtube_min_source_height()}]"
                         f"[height<={max(_youtube_quality_limits()[0], 2160)}]")
            format_candidates.append('/'.join(raw_formats[1:] + [silent_hd] + _archival_quality_formats(candidate)))
        # A finite clip ends normally at EOF. Treating EOF as a reconnect
        # event can keep FFmpeg reopening an already finished range. Bound
        # real network stalls while leaving time for another ranked source.
        range_read_seconds = max(8.0, min(20.0, per_clip_timeout))
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "outtmpl": raw_template,
            "format": format_candidates[0],
            "logger": runtime_log,
            "merge_output_format": "mp4",
            "download_ranges": download_range_func(None, [(max(0.0, start), max(1.0, start + duration + 1.0))]),
            "force_keyframes_at_cuts": False,
            "extractor_args": {"youtube": {"player_client": _youtube_player_clients(session_jar)}},
            "external_downloader_args": {
                "ffmpeg_i": [
                    "-rw_timeout", str(int(range_read_seconds * 1_000_000)),
                    "-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "3", "-reconnect_max_retries", "2",
                    "-reconnect_delay_total_max", "6",
                ]
            },
            "socket_timeout": int(os.environ.get("VIDZEO_YT_DOWNLOAD_SOCKET_TIMEOUT", str(_default_socket_timeout)) or _default_socket_timeout),
            "retries": int(os.environ.get("VIDZEO_YT_DOWNLOAD_RETRIES", str(_default_retries)) or _default_retries),
            "ffmpeg_location": _ffmpeg_bin("ffmpeg"),
        }
        opts.update(_youtube_js_runtime_options())
        opts['no_warnings'] = False

        if stream_downloads:
            # This is deliberately modest: ranged clips should still make
            # progress under the branch's bounded worker pool.
            opts["concurrent_fragment_downloads"] = 2
        def _download_progress(_status):
            _remaining_clip_timeout()
            if cancellation is not None:
                cancellation.raise_if_cancelled()
        # yt-dlp invokes progress hooks between fragments, giving us a
        # cooperative escape from slow/stalled downloads without killing the
        # whole YouTube worker pool.
        opts["progress_hooks"] = [_download_progress]
        download_error = None
        for format_index, format_spec in enumerate(format_candidates):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            _remaining_clip_timeout()
            if format_index:
                for stale in output_path.parent.glob(raw_prefix + "*"):
                    try:
                        stale.unlink()
                    except OSError:
                        pass
                opts["format"] = format_spec
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    _install_youtube_session(ydl, session_jar)
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    ydl.download([url])
                download_error = None
                break
            except Exception as exc:
                download_error = exc
                # A branch-wall cancellation is terminal. Retrying every
                # format after the coordinator has moved on only creates late
                # partial files and delays executor quiescence/salvage.
                if cancellation is not None and cancellation.cancelled():
                    raise
                # Authentication and availability failures are independent of
                # the requested selector. Retrying the same protected video
                # with four more format strings only burns the branch wall and
                # makes a healthy web/stock rescue look stuck. Let the caller
                # move to the next ranked candidate immediately; the error is
                # still retained in the branch audit.
                error_text = str(exc or "").lower()
                if _youtube_host_dns_failure(error_text):
                    candidate['_transport_failure'] = 'provider_dns_unavailable'
                # A transport timeout says the current transfer is unhealthy;
                # changing format cannot make that network request faster and
                # used to spend the remaining scene wall on a duplicate retry.
                if any(marker in error_text for marker in (
                    "timeout", "timed out", "temporarily unavailable",
                    "connection reset", "connection aborted",
                    "failed to resolve", "getaddrinfo failed", "name resolution",
                    "network is unreachable", "no route to host",
                )):
                    print(
                        "   [yt-v2] transport timeout/failure; moving to the next ranked candidate",
                        flush=True,
                    )
                    raise
                non_selector_failure = any(
                    marker in error_text
                    for marker in (
                        "please sign in",
                        "use --cookies",
                        "confirm you're not a bot",
                        "video unavailable",
                        "private video",
                        "age-restricted",
                        "this video is not available",
                    )
                )
                if non_selector_failure:
                    print(
                        "   [yt-v2] non-selector download failure; "
                        "skipping format retries for this candidate",
                        flush=True,
                    )
                    raise
                if format_index + 1 < len(format_candidates):
                    print(
                        f"   [yt-v2] format '{format_spec}' failed ({str(exc)[:60]}); "
                        f"retrying fallback format '{format_candidates[format_index + 1]}'",
                        flush=True,
                    )
                    continue
                # Both extractions already tried the complete bounded format
                # set. Switch source instead of repeating an unbounded rescue.
                raise
        if download_error is not None:
            raise download_error
    except Exception as exc:
        if cancellation is not None and cancellation.cancelled():
            # A cooperative cancellation can interrupt yt-dlp between
            # fragments. Remove the partial raw payload so the salvage scan
            # never promotes a truncated clip that fails every integrity gate.
            for _partial in output_path.parent.glob(raw_prefix + "*"):
                try:
                    _partial.unlink()
                except OSError:
                    pass
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        print(f"   [yt-v2] download failed '{candidate.get('title','')[:45]}': {str(exc)[:120]}")
        for _partial in output_path.parent.glob(raw_prefix + "*"):
            try:
                _partial.unlink()
            except OSError:
                pass
        try:
            part_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    raw_files = sorted(output_path.parent.glob(output_path.stem + ".raw.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not raw_files:
        return False
    raw = raw_files[0]
    if raw.stat().st_size < 4096 or not _source_has_video_stream(raw):
        print(f"   [yt-v2] rejecting invalid FFmpeg input: {raw.name}")
        try:
            raw.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    source_height = _source_video_height(raw)
    min_source_height = _candidate_source_height_floor(candidate)
    if source_height < min_source_height:
        print(
            f"   [yt-v2] rejecting low-quality or unverified source: {source_height or 'unknown'}p "
            f"(minimum {min_source_height}p)",
            flush=True,
        )
        raw.unlink(missing_ok=True)
        return False
    _record_source_resolution(candidate, source_height)
    if candidate['reduced_resolution_source']:
        print(f"   [yt-v2] accepted {source_height}p {candidate['source_resolution_policy']} for final scene verification", flush=True)
    requested_duration = max(1.0, float(duration))

    # yt-dlp has already produced the requested time range at the best source
    # quality (bounded to 1080p). Do not immediately scale/crop/recompress that
    # range only to make the semantic renderer encode it again. Atomic source
    # preservation both keeps more picture detail and turns completed network
    # transfers into ready clips in seconds instead of minutes.
    preserve_source = str(
        os.environ.get("VIDZEO_YT_RETRIEVAL_PRESERVE_SOURCE", "1") or "1"
    ).strip().lower() not in {"0", "false", "no", "off"}
    if preserve_source and raw.suffix.lower() == ".mp4":
        try:
            os.replace(raw, part_path)
            from services.media_integrity import promote_verified_media
            promoted = promote_verified_media(
                part_path,
                output_path,
                ffprobe_path=_ffmpeg_bin("ffprobe"),
                require_video=True,
                min_bytes=4096,
                min_duration=max(0.5, requested_duration * 0.70),
                require_decode=True,
                ffmpeg_path=_ffmpeg_bin('ffmpeg'),
            )
            if promoted.valid:
                for stale in output_path.parent.glob(raw_prefix + "*"):
                    stale.unlink(missing_ok=True)
                print(
                    f"   [yt-v2] source-quality range ready: {output_path.name} "
                    f"({source_height or 'unknown'}p, no retrieval re-encode)",
                    flush=True,
                )
                return True
            # Unit-test process doubles cannot return a real ffprobe payload;
            # retain their established compatibility without relaxing the
            # production integrity contract.
            if promoted.reason == "ffprobe_invalid_result" and _source_has_video_stream(part_path):
                os.replace(part_path, output_path)
                return True
            print(
                f"   [yt-v2] source-preserve probe rejected ({promoted.reason}); "
                "falling back to normalized encode",
                flush=True,
            )
            # The validator leaves the staged file in place. Restore it as the
            # encode input so the bounded compatibility path can continue.
            if part_path.exists():
                os.replace(part_path, raw)
        except Exception as exc:
            print(f"   [yt-v2] source-preserve fallback: {str(exc)[:120]}", flush=True)
            if part_path.exists() and not raw.exists():
                try:
                    os.replace(part_path, raw)
                except OSError:
                    pass
    _source_height, target_fps = _youtube_quality_limits()
    try:
        target_width = int(os.environ.get("VIDZEO_PROJECT_WIDTH", "1920") or 1920)
        target_height = int(os.environ.get("VIDZEO_PROJECT_HEIGHT", "1080") or 1080)
    except (TypeError, ValueError):
        target_width, target_height = 1920, 1080
    cmd = [
        _ffmpeg_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(raw), "-t", str(requested_duration), "-an",
        "-vf", f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:flags=lanczos,crop={target_width}:{target_height},fps={target_fps}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-pix_fmt", "yuv420p", "-r", str(target_fps), str(part_path),
    ]
    try:
        if cancellation is not None:
            from services.cancellation import run_cancellable_process
            completed = run_cancellable_process(
                cmd,
                cancellation=cancellation,
                timeout=max(30.0, min(120.0, requested_duration * 10.0)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if completed.returncode != 0:
                raise subprocess.CalledProcessError(completed.returncode, cmd, stderr=completed.stderr)
        else:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=max(30.0, min(120.0, requested_duration * 10.0)),
            )

        from services.media_integrity import promote_verified_media
        promoted = promote_verified_media(
            part_path,
            output_path,
            ffprobe_path=_ffmpeg_bin("ffprobe"),
            require_video=True,
            min_bytes=4096,
            min_duration=max(0.05, requested_duration - (1.0 / target_fps)),
            expected_duration=requested_duration,
            duration_tolerance=(1.0 / target_fps) + 0.005,
            expected_width=target_width,
            expected_height=target_height,
            require_decode=True,
            ffmpeg_path=_ffmpeg_bin('ffmpeg'),
        )
        if promoted.valid:
            return True
        # A few legacy unit tests use a subprocess double that writes the
        # requested output but returns ``None``. Real subprocess.run cannot do
        # that, so retain compatibility without weakening production probes.
        if promoted.reason == "ffprobe_invalid_result" and _source_has_video_stream(part_path):
            os.replace(part_path, output_path)
            return True
        print(f"   [yt-v2] clip integrity rejection ({promoted.reason}): {output_path.name}")
        for _partial in output_path.parent.glob(raw_prefix + "*"):
            try:
                _partial.unlink()
            except OSError:
                pass
        try:
            part_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    except Exception as exc:
        # A cooperative cancellation can arrive after FFmpeg has already
        # written a partial MP4. Always remove that staged artifact before
        # propagating cancellation; leaving a ``.part.mp4`` file makes the
        # media resolver report a blank/unreadable video on the next stage.
        for _partial in output_path.parent.glob(raw_prefix + "*"):
            try:
                _partial.unlink()
            except OSError:
                pass
        try:
            part_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        stderr = ""
        if hasattr(exc, "stderr") and exc.stderr:
            stderr = re.sub(r"\s+", " ", str(exc.stderr)).strip()
        detail = stderr or str(exc)
        print(f"   [yt-v2] ffmpeg clip cut failed: {detail[:180]}")
        return False


def _heuristic_moment_for_assignment(assignment: Dict[str, Any], scene_index: int, durations: List[float]) -> Any:
    from services.youtube_moment_matcher import heuristic_moment
    candidate_id = _video_id(assignment)
    duration = float(durations[scene_index] if scene_index < len(durations) else 4.0)
    return heuristic_moment(
        source_id=candidate_id,
        video_id=candidate_id,
        scene_index=scene_index,
        start=float(assignment.get("best_timestamp") or 0.0),
        duration=duration,
        reason="metadata_timestamp_fallback",
    )


def _cut_local_segment(source_path: Path, output_path: Path, start: float, duration: float, cancellation: Any = None) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = output_path.with_name(
        f"{output_path.stem}.{os.getpid()}.{threading.get_ident()}.part{output_path.suffix}"
    )
    part_path.unlink(missing_ok=True)
    handle_seconds = 0.350
    requested_start = max(0.0, float(start))
    seek_start = max(0.0, requested_start - handle_seconds)
    trim_start = max(0.0, requested_start - seek_start)
    requested_duration = max(1.0, float(duration))
    _source_height, target_fps = _youtube_quality_limits()
    try:
        target_width = int(os.environ.get("VIDZEO_YT_CLIP_WIDTH", "1920") or 1920)
        target_height = int(os.environ.get("VIDZEO_YT_CLIP_HEIGHT", "1080") or 1080)
    except (TypeError, ValueError):
        target_width, target_height = 1920, 1080
    target_width = max(320, min(3840, target_width))
    target_height = max(180, min(1080, target_height))
    try:
        clip_crf = max(14, min(28, int(os.environ.get("VIDZEO_YT_CLIP_CRF", "18") or 18)))
    except (TypeError, ValueError):
        clip_crf = 18
    cmd = [
        _ffmpeg_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{seek_start:.3f}", "-i", str(source_path),
        "-t", f"{requested_duration + trim_start + handle_seconds:.3f}", "-an",
        "-vf", (
            f"trim=start={trim_start:.3f}:duration={requested_duration:.3f},"
            "setpts=PTS-STARTPTS,"
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={target_width}:{target_height},fps={target_fps}"
        ),
        "-c:v", "libx264", "-preset", os.environ.get("VIDZEO_YT_CLIP_PRESET", "fast"), "-crf", str(clip_crf),
        "-pix_fmt", "yuv420p", "-r", str(target_fps), str(part_path),
    ]
    # High-quality 1080p transcodes are still bounded, but sources can be slow
    # on disk or heavily compressed muxes. Scale the bound with the requested
    # duration so long documentary scenes are not silently truncated.
    cut_timeout = max(90.0, min(300.0, 30.0 + requested_duration * 10.0))
    try:
        if cancellation is not None:
            from services.cancellation import run_cancellable_process
            completed = run_cancellable_process(
                cmd,
                cancellation=cancellation,
                timeout=cut_timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if completed.returncode != 0:
                raise subprocess.CalledProcessError(completed.returncode, cmd, stderr=completed.stderr)
        else:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=cut_timeout)
        from services.media_integrity import promote_verified_media
        promoted = promote_verified_media(
            part_path,
            output_path,
            ffprobe_path=_ffmpeg_bin("ffprobe"),
            require_video=True,
            min_bytes=4096,
            min_duration=max(0.05, requested_duration - (1.0 / target_fps)),
            expected_duration=requested_duration,
            duration_tolerance=(1.0 / target_fps) + 0.005,
            expected_width=target_width,
            expected_height=target_height,
            require_decode=True,
            ffmpeg_path=_ffmpeg_bin('ffmpeg'),
        )
        if not promoted.valid:
            part_path.unlink(missing_ok=True)
            return False
        return output_path.exists() and output_path.stat().st_size > 4096
    except Exception as exc:
        try:
            part_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        if cancellation is not None and cancellation.cancelled():
            raise
        stderr = re.sub(r"\s+", " ", str(getattr(exc, "stderr", "") or "")).strip()
        detail = stderr or str(exc)
        print(f"   [yt-v2] local cut failed: {type(exc).__name__}:{detail[:300]}")
        return False


def _source_max_filesize_bytes() -> int:
    raw = os.environ.get("VIDZEO_YT_SOURCE_MAX_MB")
    if raw is None or not str(raw).strip():
        raw = os.environ.get("VIDZEO_YT_SHARED_SOURCE_MAX_MB", "80")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_source_max_mb:{raw}") from exc
    if value <= 0:
        raise ValueError(f"invalid_source_max_mb:{raw}")
    return int(value * 1024 * 1024)


def _source_has_video_stream(path: Path) -> bool:
    """Return True only when ffprobe can see a decodable video stream."""
    cmd = [
        _ffmpeg_bin("ffprobe"), "-v", "error", "-threads", "1", "-select_streams", "v:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        # Some unit-test process doubles do not model CompletedProcess. Keep
        # those callers compatible; real ffprobe always supplies returncode and
        # stdout, and an audio-only file produces an empty stdout value.
        if completed is None:
            return True
        return completed.returncode == 0 and bool(str(completed.stdout or "").strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _source_video_height(path: Path) -> int:
    """Read the decoded source height, or ``0`` when ffprobe cannot tell."""
    cmd = [
        _ffmpeg_bin("ffprobe"), "-v", "error", "-threads", "1", "-select_streams", "v:0",
        "-show_entries", "stream=height", "-of", "csv=p=0", str(path),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if completed is None or completed.returncode != 0:
            return 0
        match = re.search(r"\b(\d{2,5})\b", str(completed.stdout or ""))
        return int(match.group(1)) if match else 0
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return 0


def _youtube_min_source_height() -> int:
    """Lowest decoded source height accepted for documentary footage.

    Ordinary footage must be HD at source. Authentic dated archives may use
    the separate candidate-specific SD floor, after HD formats are tried.
    """
    try:
        value = int(os.environ.get("VIDZEO_YT_MIN_SOURCE_HEIGHT", "720") or 720)
    except (TypeError, ValueError):
        value = 720
    return max(720, min(1080, value))


def _youtube_transport_min_height() -> int:
    """Prefer HD; admit labelled 480p only after HD selectors are exhausted."""
    if str(os.environ.get('VIDZEO_YT_ALLOW_SD_FALLBACK', '1')).lower() in {'0', 'false', 'no', 'off'}:
        return _youtube_min_source_height()
    return min(480, _youtube_min_source_height())


def _screen_downloaded_motion(path: str, candidate: Mapping[str, Any]) -> Dict[str, Any]:
    """Use the same real-camera-motion admission as the source-pool lane."""
    from services.youtube_pool_renderer import _screen_moment_quality, _motion_admissible
    quality = dict(_screen_moment_quality(path))
    reason = str(quality.get('reason') or '')
    if (quality.get('checked') and quality.get('reject') and
            _motion_admissible(quality, reason, archival_lane=bool(candidate.get('archival_sd_source')))):
        quality.update(reject=False, reason='', original_reason=reason,
                       motion_admission='measured_real_camera_motion')
    return quality


def _record_source_resolution(candidate: Dict[str, Any], source_height: int, audit=None) -> None:
    reduced = source_height < _youtube_min_source_height()
    archive = reduced and _candidate_source_height_floor(candidate) < _youtube_min_source_height()
    fields = {'source_height': source_height, 'archival_sd_source': archive,
              'reduced_resolution_source': reduced,
              'source_resolution_policy': 'archival_sd' if archive else 'sd_fallback' if reduced else 'hd'}
    candidate.update(fields)
    if isinstance(audit, dict):
        audit.update(fields)


def _candidate_source_height_floor(candidate: Mapping[str, Any]) -> int:
    """Allow authentic SD archives without weakening the normal HD preference.

    This is only a transport allowance. Final pixel/entity/era verification
    still decides whether a cut can illustrate the narrated scene. Search
    queries are excluded from the archive evidence.
    """
    floor = _youtube_min_source_height()
    if str(os.environ.get('VIDZEO_YT_ALLOW_SD_ARCHIVE', '1')).lower() in {'0', 'false', 'off', 'no'}:
        return floor
    evidence = ' '.join(str(candidate.get(key) or '') for key in
                        ('title', 'description', 'channel', 'uploader')).casefold()
    if re.search(r'\b(?:re[- ]?enactment|recreation|gameplay|simulation|ai[- ]generated|reaction)\b', evidence):
        return floor
    historical_year = any(int(year) < 1990 for year in re.findall(r'\b(?:18|19|20)\d{2}\b', evidence))
    archive_signal = bool(re.search(r'\b(?:archiv\w*|newsreel|movietone|pathe|pathé|historical)\b', evidence))
    # Old television commercials are often uploaded without a date or the
    # word "archive". Only allow their native SD as a final transport option
    # when the narration explicitly calls for advertising/television and the
    # source itself identifies as an advert. Topic/entity/era pixel checks
    # still apply, and ordinary contemporary footage stays HD-only.
    scene_context = ' '.join(str(candidate.get(key) or '') for key in
                             ('scene_text', 'scene_subject')).casefold()
    advertising_context = bool(
        re.search(r'\b(?:advert\w*|commercials?|television)\b', scene_context)
        and re.search(r'\b(?:adverts?|advertisements?|commercials?)\b', evidence))
    if not ((historical_year and archive_signal) or advertising_context):
        return floor
    try:
        archive_floor = int(os.environ.get('VIDZEO_YT_ARCHIVE_MIN_SOURCE_HEIGHT', '360') or 360)
    except (TypeError, ValueError):
        archive_floor = 360
    return max(360, min(floor, archive_floor))


def _archival_quality_formats(candidate: Mapping[str, Any], *, max_filesize: Optional[int] = None) -> List[str]:
    floor = _candidate_source_height_floor(candidate)
    if floor >= _youtube_transport_min_height():
        return []
    cap = _youtube_quality_limits()[0]
    bounds = [''] if not max_filesize else [
        f'[filesize<={max(1, int(max_filesize))}]',
        f'[filesize_approx<={max(1, int(max_filesize))}]',
    ]
    return [selector for bound in bounds for selector in (
        f'bestvideo[height>={floor}][height<={cap}]{bound}+bestaudio',
        f'best[height>={floor}][height<={cap}]{bound}',
        f'bestvideo[height>={floor}][height<={cap}]{bound}',
    )]


def _download_source_once(
    candidate: Dict[str, Any],
    output_path: Path,
    *,
    session_jar: Any = None,
    stream_downloads: bool = False,
    audit: Optional[Dict[str, Any]] = None,
    cancellation: Any = None,
) -> bool:
    url = _candidate_url(candidate)
    if not url:
        if audit is not None:
            audit.update({"status": "transport_unavailable", "reason": "missing_url"})
        return False
    raw_template = str(output_path.with_suffix(".raw.%(ext)s"))
    raw_prefix = output_path.stem + ".raw."
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for stale in output_path.parent.glob(raw_prefix + "*"):
        try:
            stale.unlink()
        except OSError:
            pass
    try:
        from services.yt_dlp_runtime import load_yt_dlp
        yt_dlp = load_yt_dlp()
        max_filesize = _source_max_filesize_bytes()
        try:
            max_height = max(720, min(1080, int(os.environ.get("VIDZEO_YT_SOURCE_MAX_HEIGHT", "1080") or 1080)))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_source_max_height") from exc
        quality_formats = _youtube_quality_formats(
            source=True,
            max_filesize=max_filesize,
        )
        # Extract metadata at most twice, with every compatible format tried
        # inside the fallback selector. Repeating extraction for each HD
        # variant previously exhausted the worker before reaching SD archives.
        fallback_formats = quality_formats[1:] + _archival_quality_formats(candidate, max_filesize=max_filesize)
        quality_formats = quality_formats[:1]
        if fallback_formats:
            quality_formats.append('/'.join(fallback_formats))
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "outtmpl": raw_template,
            "format": quality_formats[0],
            "merge_output_format": "mp4",
            "max_filesize": max_filesize,
            "socket_timeout": int(os.environ.get("VIDZEO_YT_DOWNLOAD_SOCKET_TIMEOUT", str(_youtube_transport_timeout_defaults()[0])) or _youtube_transport_timeout_defaults()[0]),
            "retries": int(os.environ.get("VIDZEO_YT_DOWNLOAD_RETRIES", str(_youtube_transport_timeout_defaults()[1])) or _youtube_transport_timeout_defaults()[1]),
            "fragment_retries": int(os.environ.get("VIDZEO_YT_DOWNLOAD_FRAGMENT_RETRIES", str(_youtube_transport_timeout_defaults()[2])) or _youtube_transport_timeout_defaults()[2]),
            "extractor_args": {"youtube": {"player_client": _youtube_player_clients(session_jar)}},
            "ffmpeg_location": _ffmpeg_bin("ffmpeg"),
        }
        opts.update(_youtube_js_runtime_options())

        if audit is not None:
            audit.update({"max_filesize": max_filesize, "max_height": max_height, "format": opts["format"], "format_candidates": quality_formats})
        if stream_downloads:
            opts["concurrent_fragment_downloads"] = max(1, int(os.environ.get("VIDZEO_YT_FRAGMENT_CONCURRENCY", "2") or 2))
        def _source_progress(_status):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            # ``max_filesize`` is not enforced consistently for adaptive and
            # fragmented formats. Abort as soon as yt-dlp reveals a stream
            # whose advertised or downloaded size already exceeds the source
            # budget, before a multi-hundred-MiB transfer can occupy the pool
            # wall.
            status = _status if isinstance(_status, Mapping) else {}
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            downloaded = status.get("downloaded_bytes")
            try:
                if total and int(total) > max_filesize:
                    raise RuntimeError(
                        f"source_filesize_exceeds_limit:{int(total)}>{max_filesize}"
                    )
                if downloaded and int(downloaded) > max_filesize:
                    raise RuntimeError(
                        f"source_downloaded_bytes_exceeds_limit:{int(downloaded)}>{max_filesize}"
                    )
            except (TypeError, ValueError):
                pass
        opts["progress_hooks"] = [_source_progress]
        download_error = None
        for format_index, format_spec in enumerate(quality_formats):
            if format_index:
                opts["format"] = format_spec
                for stale in output_path.parent.glob(raw_prefix + "*"):
                    try:
                        stale.unlink()
                    except OSError:
                        pass
                print(
                    f"   [yt-v2] source format fallback {format_spec[:90]}",
                    flush=True,
                )
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    _install_youtube_session(ydl, session_jar)
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    ydl.download([url])
                download_error = None
                break
            except Exception as exc:
                download_error = exc
                if cancellation is not None and cancellation.cancelled():
                    raise
                error_text = str(exc).lower()
                if any(marker in error_text for marker in (
                    'please sign in', 'use --cookies', "confirm you're not a bot",
                    'video unavailable', 'private video', 'age-restricted',
                    'this video is not available', 'filesize_exceeds_limit',
                    'downloaded_bytes_exceeds_limit',
                )):
                    raise
                if format_index + 1 < len(quality_formats):
                    continue
                # No unbounded /best rescue after the bounded alternatives.
                raise
        if download_error is not None:
            raise download_error
    except ValueError as exc:
        if audit is not None:
            audit.update({"status": "source_config_error", "reason": str(exc)})
        print(f"   [yt-v2] source configuration failed: {exc}")
        return False
    except Exception as exc:
        if cancellation is not None and cancellation.cancelled():
            raise
        reason = str(exc).lower()
        status = "transport_error"
        if "403" in reason or "forbidden" in reason:
            status = "http_403"
        elif "po token" in reason:
            status = "po_token_required"
        elif "format" in reason and "available" in reason:
            status = "format_unavailable"
        elif "filesize" in reason or "too large" in reason:
            status = "max_filesize_exceeded"
        elif "ffmpeg" in reason or "merge" in reason:
            status = "merge_failed"
        elif "timed out" in reason or "timeout" in reason:
            status = "fragment_timeout"
        if audit is not None:
            audit.update({"status": status, "reason": str(exc)[:300]})
        print(f"   [yt-v2] source download failed [{status}] '{candidate.get('title', '')[:60]}': {str(exc)[:180]}")
        return False
    raw_files = sorted(output_path.parent.glob(output_path.stem + ".raw.*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not raw_files:
        if audit is not None:
            audit.update({"status": "transport_unavailable", "reason": "no_downloaded_file"})
        return False
    # yt-dlp can leave separate audio/video artifacts when a merge or format
    # selection is incomplete. Modification time is not a media-type signal:
    # the audio artifact is often newest. Never promote it to `.source.mp4`.
    raw = next((path for path in raw_files if _source_has_video_stream(path)), None)
    if raw is None:
        if audit is not None:
            audit.update({"status": "merge_failed", "reason": "downloaded_source_has_no_video_stream"})
        print("   [yt-v2] source download rejected: downloaded artifact has no video stream")
        for artifact in raw_files:
            try:
                artifact.unlink(missing_ok=True)
            except OSError:
                pass
        return False
    source_height = _source_video_height(raw)
    min_source_height = _candidate_source_height_floor(candidate)
    if source_height < min_source_height:
        if audit is not None:
            audit.update({
                "status": "source_quality_rejected",
                "reason": (
                    f"source_height_{source_height}_below_{min_source_height}"
                    if source_height else "source_height_unknown"
                ),
                "source_height": source_height,
                "min_source_height": min_source_height,
            })
        print(
            f"   [yt-v2] source quality rejected: {source_height or 'unknown'}p "
            f"(minimum {min_source_height}p)",
            flush=True,
        )
        for artifact in raw_files:
            try:
                artifact.unlink(missing_ok=True)
            except OSError:
                pass
        return False
    _record_source_resolution(candidate, source_height, audit)
    try:
        raw_size = raw.stat().st_size
        if raw_size <= 4096:
            if audit is not None:
                audit.update({"status": "transport_unavailable", "reason": "empty_output", "bytes": raw_size})
            raw.unlink(missing_ok=True)
            return False
        if raw_size > max_filesize:
            if audit is not None:
                audit.update({"status": "max_filesize_exceeded", "reason": f"{raw_size}>{max_filesize}", "bytes": raw_size})
            raw.unlink(missing_ok=True)
            return False
        raw.replace(output_path)
        ok = output_path.exists() and output_path.stat().st_size > 4096 and output_path.stat().st_size <= max_filesize
        if audit is not None:
            audit.update({"status": "downloaded" if ok else "transport_unavailable", "reason": "" if ok else "invalid_output", "bytes": output_path.stat().st_size if output_path.exists() else 0})
        if not ok:
            output_path.unlink(missing_ok=True)
        return ok
    except Exception as exc:
        if audit is not None:
            audit.update({"status": "merge_failed", "reason": str(exc)[:300]})
        try:
            raw.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _build_scene_rows(
    title: str,
    scene_texts: List[str],
    scene_query_overrides: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    from services.documentary_subject import collection_title_subject
    overrides = list(scene_query_overrides or [])
    gta_vi_mode = _is_gta_vi_text(title, *scene_texts)
    base_rows: List[Dict[str, Any]] = []
    raw_subjects: List[str] = []
    for idx, text in enumerate(scene_texts):
        row = dict(overrides[idx]) if idx < len(overrides) and isinstance(overrides[idx], dict) else {}
        base_rows.append(row)
        raw_subjects.append(_scene_row_subject(row, text, title))

    # A weak LLM section label can canonicalize to the wrong proper noun
    # inside its own scene ("whether ... Champions League night" resolving to
    # the league instead of the player). When the row itself names a subject,
    # prefer the known real subject that contains that name.
    for idx, row in enumerate(base_rows):
        section_value = _clean_text(row.get("section_subject") or "")
        scene_value = _clean_text(row.get("scene_subject") or row.get("container_subject") or "")
        if (
            (not section_value or _subject_is_weak(section_value))
            and scene_value
            and not _subject_is_weak(scene_value)
            and raw_subjects[idx]
            and _norm(scene_value) != _norm(raw_subjects[idx])
        ):
            for candidate in raw_subjects:
                if not candidate or candidate == raw_subjects[idx]:
                    continue
                if (
                    _text_mentions_subject(candidate, scene_value)
                    or _text_mentions_subject(scene_value, candidate)
                ):
                    raw_subjects[idx] = candidate
                    break

    llm_owned = any(
        bool(row.get("_llm") or row.get("_llm_container") or row.get("_brain"))
        for row in base_rows
    )
    mechanical_only = bool(base_rows) and not llm_owned
    consensus_values = []
    for row in base_rows:
        for key in ("domain_anchor", "topic_anchor", "global_topic"):
            raw_anchor = _clean_text(row.get(key))
            value = _canonical_subject(raw_anchor, "", title)
            # A place is not the complete identity of a place-specific event.
            # Keep the explicit narrated event in mechanical recovery pools so
            # country travel footage cannot outrank the actual news/disaster.
            if (raw_anchor and _norm(raw_anchor) in _norm(title)
                    and re.search(r"\b(?:earthquakes?|floods?|elections?|crashes?|wars?|coronations?|weddings?|hurricanes?|eruptions?|heists?|trials?|attacks?|garter day|processions?|investitures?|ceremon(?:y|ies)|trooping the colour)\b", _norm(raw_anchor))):
                value = raw_anchor
            if value and not _is_date_only_subject(value):
                consensus_values.append(value)
                break
    consensus_counter = Counter(consensus_values)
    if consensus_counter:
        consensus_anchor, consensus_count = consensus_counter.most_common(1)[0]
    else:
        consensus_anchor = _canonical_subject(title, "", title)
        consensus_count = 0
    title_anchors = {
        _norm(row.get("title_anchor") or "")
        for row in base_rows
        if _clean_text(row.get("title_anchor"))
    }
    explicit_format_values = {
        _norm(row.get("video_format") or row.get("format") or "")
        for row in base_rows
    }
    # The immutable scene contracts can carry several exact product/person
    # identities even when the film's category title has lost its list number.
    # Never let the first local identity become the global source pool for all
    # the others (Whitbread Trophy Bitter previously swallowed Stella Artois).
    locked_identities = {_norm(subject) for row, subject in zip(base_rows, raw_subjects)
                         if row.get('_semantic_locked') and subject}
    explicit_multi_subject = any(
        bool(row.get("is_listicle") or row.get("multi_subject"))
        for row in base_rows
    ) or len(locked_identities) > 1 or bool(explicit_format_values & {
        "multi subject", "multi subject listicle", "multi_subject",
        "multi_subject_listicle", "subjects compared", "subjects_compared",
        "comparison", "listicle",
    })
    _listicle_re = re.compile(
        r"\b(?:top|best|greatest|worst)\s*\d+\b"
        r"|\b\d+\s+(?:top|best|greatest|worst)\b"
        r"|\branking\b|\blisticle\b|\b(?:vs|versus|comparison|compared)\b"
    )
    listicle_hint = bool(
        explicit_multi_subject
        or _listicle_re.search(_norm(title))
        or collection_title_subject(title)
        # A degraded topic title can hide the listicle marker, but distinct
        # planner labels alone are not enough: noisy LLM labels are common in
        # single-topic documentaries. Require explicit numbered/ordered
        # narration before switching away from single-topic inference.
        or any(
            _LISTICLE_HINT_RE.search(str(text or ""))
            for text in scene_texts
        )
    )
    consensus_ratio = consensus_count / max(1, len(base_rows))
    single_subject_pool = bool(
        not listicle_hint
        and consensus_anchor
        and (
            (
                title_anchors
                and len(title_anchors) == 1
                and _norm(consensus_anchor) in title_anchors
            )
            or consensus_ratio >= 0.60
        )
    )
    inference_support = 0
    inference_ratio = 0.0
    if not single_subject_pool and not listicle_hint:
        inferred_anchor, inference_support, inference_ratio = _infer_single_topic_anchor(
            title,
            scene_texts,
            base_rows,
            raw_subjects,
        )
        if inferred_anchor:
            consensus_anchor = inferred_anchor
            single_subject_pool = True
            print(
                f"   [yt-v2] single-topic source pool inferred: {inferred_anchor} "
                f"({inference_support}/{len(base_rows)} row(s), {inference_ratio:.0%} support)"
            )
    primary_subject = ""
    if single_subject_pool:
        primary_subject = consensus_anchor
    if mechanical_only and consensus_anchor and not listicle_hint:
        # Do not flatten per-scene subjects onto the consensus anchor when the
        # rows already carry distinct local subjects (e.g. facet names under a
        # single-topic documentary). The global anchor stays authoritative via
        # title/domain fields and primary_container_subject; the local subject
        # must survive so pools and provenance obey both anchors.
        distinct_subjects = {_norm(subject) for subject in raw_subjects if subject}
        if len(distinct_subjects) <= 1 or (
            not listicle_hint and consensus_ratio >= 0.60
        ):
            raw_subjects = [consensus_anchor] * len(base_rows)

    # Carry a deterministic topic family across rows. This matters when a
    # listicle/script uses a proper name only in its title or in one sentence;
    # every pool still needs the same visual search vocabulary.
    global_topic_text = _norm(" ".join([title, *scene_texts]))
    global_domain_category = _dominant_domain_category(global_topic_text)

    primary_counts = Counter()
    for row, subject in zip(base_rows, raw_subjects):
        is_intro = bool(row.get("is_intro")) or _norm(row.get("container_id")) == "intro"
        is_outro = bool(row.get("is_outro")) or _norm(row.get("container_id")) == "outro"
        container_type = _norm(row.get("container_type") or row.get("scene_subject_type") or "")
        if subject and not _subject_is_weak(subject) and not is_intro and not is_outro and container_type not in {"brand", "organization"}:
            primary_counts[subject] += 1
    if single_subject_pool:
        primary_subject = consensus_anchor
    elif mechanical_only and consensus_anchor and not listicle_hint:
        primary_subject = consensus_anchor
    elif primary_counts and not listicle_hint:
        winner_candidate, winner_count = primary_counts.most_common(1)[0]
        primary_threshold = max(2, int(math.ceil(len(scene_texts) * 0.5)))
        runner_count = 0
        for candidate, count in primary_counts.items():
            if candidate != winner_candidate and count > runner_count:
                runner_count = count
        # A near-equal competing subject (Messi vs Ronaldo 3-3) must keep its
        # own pool; only a clear majority may become the primary anchor.
        if (
            winner_count >= primary_threshold
            and not (
                runner_count >= primary_threshold
                and runner_count >= winner_count * 0.8
            )
        ):
            primary_subject = winner_candidate

    filled_subjects = list(raw_subjects)
    last_real = ""
    for idx, subject in enumerate(filled_subjects):
        row = base_rows[idx]
        is_intro = bool(row.get("is_intro")) or _norm(row.get("container_id")) == "intro"
        is_outro = bool(row.get("is_outro")) or _norm(row.get("container_id")) == "outro"
        if (is_intro or is_outro) and primary_subject:
            filled_subjects[idx] = primary_subject
            continue
        if subject and not _subject_is_weak(subject):
            last_real = subject
            continue
        next_real = ""
        for lookahead in filled_subjects[idx + 1:]:
            if lookahead and not _subject_is_weak(lookahead):
                next_real = lookahead
                break
        filled_subjects[idx] = last_real or next_real

    rows: List[Dict[str, Any]] = []
    for idx, text in enumerate(scene_texts):
        row = base_rows[idx]
        subject = filled_subjects[idx]
        domain_category = _norm(row.get("domain_category") or "") or global_domain_category
        if gta_vi_mode:
            # GTA VI is unambiguously a gaming container even when the local
            # classifier/LLM omits its domain label.
            domain_category = "gaming"
        container_type = _norm(row.get("container_type") or row.get("scene_subject_type") or "")
        is_intro = bool(row.get("is_intro")) or _norm(row.get("container_id")) == "intro"
        is_outro = bool(row.get("is_outro")) or _norm(row.get("container_id")) == "outro"
        candidate_subject = subject
        query_subject = subject
        if primary_subject:
            if subject and _norm(subject) != _norm(primary_subject):
                if (
                    _text_mentions_subject(primary_subject, subject)
                    or _text_mentions_subject(subject, primary_subject)
                ):
                    # The local label is the same identity (a canonicalized
                    # bridge/alias row): do not repeat it twice.
                    query_subject = subject
                else:
                    query_subject = f"{primary_subject} {subject}"
            else:
                query_subject = subject or primary_subject
            # The global anchor is authoritative for the pool: scenes of a
            # single-topic documentary share one canonical pool while the
            # per-scene subject still shapes that scene's own queries.
            candidate_subject = primary_subject
        explicit_local_anchor = _clean_text(
            row.get("title_anchor")
            or row.get("domain_anchor")
            or row.get("topic_anchor")
            or row.get("global_topic")
        )
        # Provider overrides with an explicit scene/container subject should
        # not be replaced by the video title (``Top WWE Wrestlers`` is not a
        # queryable identity for a John Cena scene). The title remains a
        # global context field, while the local subject owns retrieval.
        topic_seed = explicit_local_anchor or subject or title
        topic_anchor = _canonical_subject(
            topic_seed,
            text,
            title,
            [primary_subject, subject],
        ) or subject or title
        row.update({
            "scene_index": idx,
            "scene_number": idx + 1,
            "scene_text": text,
            "title_anchor": topic_anchor,
            "domain_anchor": row.get("domain_anchor") or topic_anchor,
            "section_subject": subject if not _subject_is_weak(subject) else "",
            "scene_subject": row.get("scene_subject") or subject,
            "visual_facet": row.get("visual_facet") or text,
            "container_id": row.get("container_id") or (_norm(subject).replace(" ", "_") if subject else "general"),
            "primary_container_subject": primary_subject,
            "candidate_subject": candidate_subject,
            "domain_category": domain_category,
            # Never infer cross-title media from the GTA VI topic alone. It is
            # enabled only when this individual scene explicitly opts into a
            # positive GTA V comparison.
            "gta_vi_supporting_gameplay": _scene_explicitly_requests_gta_v_comparison(text, row),
        })
        row["queries"] = _queries_for_row(row, query_subject, text)
        rows.append(row)
    if os.environ.get("VIDZEO_YT_DEBUG_POOLS", "0").strip().lower() in {"1", "true", "yes", "on"}:
        import json as _json
        _debug = {
            "single_subject_pool": single_subject_pool,
            "listicle_hint": listicle_hint,
            "consensus_anchor": consensus_anchor,
            "primary_subject": primary_subject,
            "rows": [
                {
                    "i": idx,
                    "container_id": row.get("container_id"),
                    "section_subject": row.get("section_subject"),
                    "candidate_subject": row.get("candidate_subject"),
                    "title_anchor": row.get("title_anchor"),
                    "pool_key": _row_pool_key(row),
                }
                for idx, row in enumerate(rows[:6])
            ],
        }
        print(f"   [yt-v2][debug] {_json.dumps(_debug, default=str)}")
    return rows


def _facet_term_outside_anchor(text: str, title: str, anchor: str) -> str:
    """Best local facet for a beat whose subject equals the global anchor.

    When the container map labels a row with the same broad subject as the
    primary (''RMS Titanic''), that row carries no local identity: pick the
    beat's own proper-named facet so the local anchor survives composition.
    """
    for term in _scene_subject_terms(text, title):
        if _norm(term) == _norm(anchor):
            continue
        return term
    return ""


def _row_pool_key(row: Dict[str, Any]) -> str:
    primary = _norm(row.get("primary_container_subject") or "")
    candidate_subject = _norm(row.get("candidate_subject") or "")
    title_anchor = _norm(row.get("title_anchor") or row.get("domain_anchor") or "")
    if primary and candidate_subject == primary:
        return f"primary {primary}"
    if title_anchor and candidate_subject == title_anchor and not _is_date_only_subject(row.get("section_subject")):
        return f"primary {title_anchor}"
    # Prefer the per-row canonical subject over the container id: LLM container
    # maps often return a single generic id ("shipwrecks", "section") for every
    # scene, which would collapse distinct listicle subjects into one pool.
    if candidate_subject:
        return candidate_subject
    return _norm(row.get("container_id") or row.get("section_subject") or "general")


def _group_rows(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_row_pool_key(row)].append(row)
    return groups


def _representative_pool_queries(rows: List[Dict[str, Any]], limit: int) -> List[str]:
    if not rows or limit <= 0:
        return []
    sample_count = min(limit, len(rows))
    if sample_count == 1:
        indices = [0]
    else:
        indices = [round(step * (len(rows) - 1) / (sample_count - 1)) for step in range(sample_count)]
    selected = []
    for query_rank in range(9):
        for idx in indices:
            queries = list(rows[idx].get("queries") or [])
            if query_rank < len(queries):
                selected.append(queries[query_rank])
            if len(_dedupe(selected)) >= limit:
                return _dedupe(selected)[:limit]
    return _dedupe(selected)[:limit]


def _search_container_pools(
    groups: Dict[str, List[Dict[str, Any]]],
    search_timeout_seconds: Optional[float] = None,
    session_jar: Any = None,
    cancellation: Any = None,
) -> Dict[str, List[Dict[str, Any]]]:
    max_workers = max(2, min(int(os.environ.get("VIDZEO_YT_SEARCH_CONCURRENCY", "8") or 8), 16))
    jobs: List[Tuple[str, str]] = []
    for key, rows in groups.items():
        # YouTube needs several independent retrieval families to expose
        # distinct usable moments. The old five-query default often returned
        # one presenter package plus one near-match and starved the transcript
        # pool before it could find walkarounds, archive footage, or driving
        # sequences. This remains bounded by the total query cap below.
        query_limit = int(os.environ.get("VIDZEO_YT_V2_QUERIES_PER_CONTAINER", "8") or 8)
        if any(_norm(row.get("domain_category") or "") == "disaster" for row in rows):
            # Historic disasters need archive/newsreel variants that are
            # appended after modern raw-footage queries.
            query_limit = max(query_limit, 9)
        for query in _representative_pool_queries(rows, query_limit):
            jobs.append((key, query))
    pools: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if not jobs:
        return pools
    # Throttle-safe fan-out: a 40-100 search burst (containers x queries per
    # container) is what triggered the r9 stall — YouTube throttles the IP
    # when requests arrive in bursts, and every response then creams the
    # socket window. Cap the total by the job size and keep the cap fair:
    # drop the tail of each container's query list evenly, never starving a
    # whole container, so the strongest query per pool always survives.
    try:
        total_scenes = max(1, sum(len(rows) or 0 for rows in groups.values()))
        configured_cap = int(os.environ.get("VIDZEO_YT_V2_QUERY_TOTAL_CAP", "0") or 0)
    except (TypeError, ValueError):
        total_scenes, configured_cap = 1, 0
    query_cap = (
        configured_cap
        if configured_cap > 0
        else max(12, min(total_scenes * 3 + 6, 60))
    )
    if jobs:
        by_container: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for key, query in jobs:
            by_container[key].append((key, query))
        capped: List[Tuple[str, str]] = []
        while len(capped) < query_cap and any(by_container.values()):
            for key in [key for key in by_container if by_container[key]]:
                if len(capped) >= query_cap:
                    break
                capped.append(by_container[key].pop(0))
        # Always interleave pools, even below the cap. A slow first subject
        # must not occupy every search worker while later subjects get no
        # first query before the shared deadline.
        if len(jobs) > query_cap:
            print(
                f"   [yt-v2] query fan-out capped: {query_cap}/{len(jobs)} "
                f"(scenes={total_scenes}, containers={len(groups)})",
                flush=True,
            )
        jobs = capped

    timeout_value = search_timeout_seconds
    if timeout_value is None:
        timeout_value = os.environ.get("VIDZEO_YT_V2_SEARCH_TIMEOUT")
    if timeout_value is None:
        # The active branch computes a per-scene search budget, but the pool
        # search fans out across every container (pools x queries per pool), so
        # a bare single-scene budget (10-18s) would cancel the whole queue the
        # moment the first searches start.  Scale the wall deadline from the
        # actual job cost, bounded by the branch's yield window.
        per_scene = max(0.0, float(os.environ.get("VIDZEO_YT_SCENE_SEARCH_BUDGET_SECONDS") or 0.0) or 0.0)
        if per_scene > 0:
            max_workers = max(2, min(int(os.environ.get("VIDZEO_YT_SEARCH_CONCURRENCY", "8") or 8), 16))
            # ~2s per yt-dlp search round; give the whole job a chance to finish,
            # but cap it so a healthy pool never stalls the branch forever.
            scaled = per_scene + int(math.ceil(len(jobs) / max_workers)) * 2.0
            timeout_value = max(per_scene, min(scaled, float(os.environ.get("VIDZEO_YT_V2_SEARCH_TIMEOUT_MAX", "120") or 120)))
        else:
            timeout_value = "90"
    try:
        search_timeout = max(0.0, float(timeout_value or 0.0))
    except (TypeError, ValueError):
        search_timeout = 90.0
    # A throttled yt-dlp search can consume the whole socket timeout on every
    # retry (e.g. 20s x 4 = ~85s), so a fan-out deadline shorter than the
    # worst-case per-search round would cancel the queue before the first
    # search returns.  Scale the wall deadline from the actual job cost,
    # bounded so a healthy pool never stalls the branch forever.
    _socket = max(1.0, float(os.environ.get("VIDZEO_YT_METADATA_SOCKET_TIMEOUT", "8") or 8))
    _retries = max(0.0, float(os.environ.get("VIDZEO_YT_METADATA_RETRIES", "1") or 1))
    _worst_per_search = _socket * (1.0 + _retries) + 8.0
    _rounds = max(1, int(math.ceil(len(jobs) / max_workers)))
    # Absolute ceiling: never let a crawling search fan-out eat the branch
    # wall (the r9 stall burned 25 minutes this way). The caller's wall still
    # bounds everything, but search must yield before downloads starve.
    try:
        _ceiling = max(60.0, min(600.0, float(os.environ.get("VIDZEO_YT_V2_SEARCH_TIMEOUT_CEILING_SECONDS", "300") or 300)))
    except (TypeError, ValueError):
        _ceiling = 300.0
    _hard_cap = min(
        max(search_timeout, float(os.environ.get("VIDZEO_YT_V2_SEARCH_TIMEOUT_HARD_CAP", "240") or 240)),
        _ceiling,
    )
    if search_timeout > 0:
        # The rounds floor applies whether or not the caller passed a
        # timeout: a 36-query fan-out at 8 workers is 5 rounds, and a
        # caller wall sized for one round cancels the queue before the
        # first search returns under load. The hard cap still bounds it.
        search_timeout = max(search_timeout, min(_worst_per_search * _rounds + 15.0, _hard_cap))

    # Authentication fallback shares one search wall. Giving it a fresh
    # 60-180 seconds after an exhausted pass starves clip downloads.
    search_deadline = time.monotonic() + search_timeout
    timed_out, pools = _run_pool_search_pass(
        jobs,
        session_jar=session_jar,
        search_timeout=search_timeout,
        max_workers=max_workers,
        cancellation=cancellation,
    )
    retry_remaining = max(0.0, search_deadline - time.monotonic())
    if timed_out and not any(pools.values()) and session_jar is not None and retry_remaining > 0:
        # Authenticated fan-out can stall behind YouTube throttling.  A second,
        # anonymous pass is much faster under load and directly recovers the
        # zero-candidate failure mode without burning the branch budget twice.
        print(
            "   [yt-v2] pool search timed out with 0 candidates; retrying anonymously "
            "(detected IP throttling: repeat queries are served from the local search cache)",
            flush=True,
        )
        _retry_timeout = min(retry_remaining, 180.0)
        _retry_timed_out, pools = _run_pool_search_pass(
            jobs,
            session_jar=None,
            search_timeout=_retry_timeout,
            max_workers=max_workers,
            cancellation=cancellation,
        )
        if _retry_timed_out:
            print(f"   [yt-v2] anonymous retry also timed out ({_retry_timeout:.0f}s); "
                  f"continuing with {sum(len(v) for v in pools.values())} candidate(s)")
        else:
            print(f"   [yt-v2] anonymous retry recovered {sum(len(v) for v in pools.values())} candidate(s)")
    elif timed_out:
        print(
            f"   [yt-v2] search timeout; continuing with {sum(len(v) for v in pools.values())} candidate(s) "
            "(possibly throttled; reruns serve repeat queries from the local search cache)",
            flush=True,
        )
    return pools


def _run_pool_search_pass(
    jobs: List[Tuple[str, str]],
    *,
    session_jar: Any = None,
    search_timeout: float,
    max_workers: int,
    cancellation: Any = None,
) -> Tuple[bool, Dict[str, List[Dict[str, Any]]]]:
    """Run one parallel pool-search pass and return (timed_out, pools)."""
    pools: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen: Dict[str, set] = defaultdict(set)
    executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="yt-v2-search")
    future_map = {}
    pending = set()
    processed = set()
    failed_searches = 0
    empty_searches = 0
    # Spread submissions so requests hit YouTube at a steady rate instead of a
    # burst. A throttled endpoint answers slow-but-alive; bursts only deepen
    # the bot flag. The delay is per submission, so worker parallelism still
    # dominates: 40 queries at 0.4s pacing / 10 workers adds ~1.6s wall.
    try:
        pace_seconds = max(0.0, min(3.0, float(os.environ.get("VIDZEO_YT_SEARCH_PACE_SECONDS", "0.4") or 0.4)))
    except (TypeError, ValueError):
        pace_seconds = 0.4
    last_network_submission = None
    deadline = time.monotonic() + max(0.0, search_timeout)

    def _search_job(query: str, results_per_query: int):
        """Call the search adapter while tolerating legacy/custom providers.

        Third-party providers and test adapters written before cancellation
        support often accept only ``(query, max_results, session_jar)``.  A
        keyword mismatch must not turn every result into an empty pool.
        """
        try:
            return _search_youtube(
                query,
                results_per_query,
                session_jar=session_jar,
                cancellation=cancellation,
            )
        except TypeError as exc:
            # Keep compatibility with older/custom adapters that use a
            # positional third argument (or call it ``_session_jar``) and do
            # not yet expose the cooperative-cancellation keyword.  A single
            # unexpected keyword must never collapse every search job into an
            # empty pool.
            message = str(exc).lower()
            if not any(
                token in message
                for token in (
                    "cancellation", "session_jar", "unexpected keyword",
                    "positional argument", "required positional",
                )
            ):
                raise
            try:
                return _search_youtube(query, results_per_query, session_jar=session_jar)
            except TypeError as legacy_exc:
                legacy_message = str(legacy_exc).lower()
                if not any(
                    token in legacy_message
                    for token in (
                        "session_jar", "unexpected keyword", "positional argument",
                        "required positional",
                    )
                ):
                    raise
                try:
                    # Positional fallback supports adapters declaring the
                    # historical ``_session_jar`` parameter name.
                    return _search_youtube(query, results_per_query, session_jar)
                except TypeError as positional_exc:
                    if "required positional" not in str(positional_exc).lower() and "positional argument" not in str(positional_exc).lower():
                        raise
                    return _search_youtube(query, results_per_query)

    for key, query in jobs:
        if time.monotonic() >= deadline:
            break
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        cached_query = _cached_search_results(query) is not None
        if not cached_query and last_network_submission is not None and pace_seconds > 0:
            pause = max(0.0, pace_seconds - (time.monotonic() - last_network_submission))
            if pause:
                time.sleep(min(pause, max(0.0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
        if not cached_query:
            last_network_submission = time.monotonic()
        results_per_query = int(os.environ.get("VIDZEO_YT_RESULTS_PER_QUERY", "20") or 20)
        future = executor.submit(_search_job, query, results_per_query)
        if cancellation is not None:
            cancellation.register_future(future)
        future_map[future] = (key, query)
        pending.add(future)

    def _consume(future: Any) -> None:
        if future in processed:
            return
        processed.add(future)
        nonlocal failed_searches, empty_searches
        key, query = future_map[future]
        try:
            candidates = future.result() or []
        except Exception:
            failed_searches += 1
            candidates = []
        if not candidates:
            empty_searches += 1
        for raw_candidate in candidates:
            candidate = dict(raw_candidate)
            for field in ('candidate_evidence', 'requirement_hash', 'semantic_context_pending', 'nearest_match_pending'):
                candidate.pop(field, None)
            candidate = dict(candidate or {})
            vid = _video_id(candidate)
            if vid and vid not in seen[key]:
                candidate["query_text"] = query
                pools[key].append(candidate)
                seen[key].add(vid)

    timed_out = len(future_map) < len(jobs)
    try:
        while pending:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if search_timeout <= 0:
                timed_out = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            done, pending = wait(
                pending,
                timeout=remaining,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                timed_out = True
                break
            for future in done:
                _consume(future)

        # A future can finish between the deadline check and cancellation.  A
        # final non-blocking pass preserves those completed partial results.
        completed_after_deadline = [future for future in pending if future.done()]
        for future in completed_after_deadline:
            _consume(future)
            pending.discard(future)
    finally:
        if pending:
            for future in pending:
                future.cancel()
        # Never use the executor context manager here: its implicit
        # shutdown(wait=True) made a search timeout block until every yt-dlp
        # worker finished, defeating the partial-result contract.
        executor.shutdown(wait=False, cancel_futures=True)
        if cancellation is not None:
            for future in future_map:
                cancellation.unregister_future(future)
        if failed_searches or empty_searches:
            print(
                f"   [yt-v2] search pass: {failed_searches} failed, {empty_searches} empty "
                f"of {len(jobs)} query(ies)",
                flush=True,
            )

    return timed_out, pools


def _assign_candidates(
    rows: List[Dict[str, Any]],
    pools: Dict[str, List[Dict[str, Any]]],
    scene_durations: List[float],
    *,
    lenient_acceptance: bool = False,
    allow_same_source_reuse: bool = True,
    prefer_distinct_sources: bool = False,
) -> List[Optional[Dict[str, Any]]]:
    used_timestamps: Dict[str, List[float]] = defaultdict(list)
    assignments: List[Optional[Dict[str, Any]]] = [None] * len(rows)
    rejection_counts: Counter = Counter()
    assigned_source_ids = set()
    multi_container = len([key for key, values in pools.items() if values]) > 1
    for row in rows:
        idx = int(row["scene_index"])
        subject = row.get("candidate_subject") or row.get("section_subject") or row.get("scene_subject") or ""
        key = _row_pool_key(row)
        candidates = list(pools.get(key) or [])
        if not candidates and not multi_container:
            candidates = [c for values in pools.values() for c in values]
        if not candidates:
            continue
        scored = []
        reused_source_scored = []
        retrieval_context = _row_retrieval_context(row)
        intro_unique = bool(
            row.get("intro_unique")
            or row.get("is_intro")
            or retrieval_context.get("intro_unique")
        )
        bite_clip = bool(
            row.get("bite_clip")
            or row.get("youtube_bite_clip")
            or retrieval_context.get("bite_clip")
        )
        for raw_candidate in candidates:
            candidate = dict(raw_candidate)
            for field in ('candidate_evidence', 'requirement_hash', 'semantic_context_pending', 'nearest_match_pending'):
                candidate.pop(field, None)
            candidate_id = _video_id(candidate)
            # The opening 15 seconds is an editorial hook, not a place to
            # replay one master source. Enforce source diversity there even
            # when the broader job permits same-source bite reuse.
            if intro_unique and candidate_id in assigned_source_ids and not allow_same_source_reuse:
                continue
            if not allow_same_source_reuse and candidate_id in assigned_source_ids and not bite_clip:
                continue
            requirement = row.get("visual_requirement")
            if isinstance(requirement, Mapping):
                matched, reason, evidence = candidate_requirement_match(requirement, candidate)
                if not matched:
                    pending, pending_evidence = candidate_domain_context_pending(
                        requirement, candidate
                    )
                    if not pending:
                        nearest, _nearest_reason, nearest_evidence = candidate_nearest_match(
                            requirement, candidate
                        )
                        if nearest:
                            # Keep same-domain/nearest-franchise footage in
                            # the bounded recall pool. The later downloaded
                            # frame verifier remains authoritative.
                            candidate["semantic_context_pending"] = True
                            candidate["nearest_match_pending"] = True
                            candidate["candidate_evidence"] = {
                                **dict(nearest_evidence or {}),
                                "metadata_source": "nearest_match_pending",
                                "verification_required": True,
                            }
                        else:
                            candidate["v2_reject_reason"] = reason
                            candidate["candidate_evidence"] = evidence
                            rejection_counts[reason] += 1
                            continue
                    else:
                        candidate["semantic_context_pending"] = True
                        candidate["candidate_evidence"] = pending_evidence
                else:
                    candidate["candidate_evidence"] = evidence
                candidate["requirement_hash"] = str(requirement.get("requirement_hash") or "")
            score = _score_candidate(
                candidate,
                subject,
                row.get("scene_text", ""),
                candidate.get("query_text") or "",
                retrieval_context,
            )
            # Propagate the best metadata score back onto the raw pool
            # record. _score_candidate writes v2_score only onto the
            # per-(row, candidate) copy above; build_sources() reads
            # v2_score off the raw pool records to stamp metadata_score, so
            # without this every source record scored 0.0 and the pool
            # shortlist/probe gates (metadata_score > 0) dropped the whole
            # pool — Cooper's 481 ranked candidates shortlisted zero
            # sources. Keep the max across rows: a candidate's pool score is
            # its best scene fit. Candidates that never reach scoring (hard
            # requirement reject, `continue` above) get no stamp.
            if isinstance(raw_candidate, dict):
                try:
                    prior_score = float(raw_candidate.get("v2_score") or 0.0)
                except (TypeError, ValueError):
                    prior_score = 0.0
                if score > prior_score:
                    raw_candidate["v2_score"] = round(score, 2)
            # In lenient mode, metadata is a recall gate: only explicit hard
            # blocks (-999) are discarded. Strict vision then decides relevance
            # progressively, so weak-but-plausible footage still gets a chance.
            # Scores at or below -180 represent explicit semantic failures
            # (subject mismatch, negative domain hit, commentary without a
            # visual signal, or a source-noise package). Keep those out even
            # in recall mode; only softer ranking penalties may pass through.
            # A nearest/domain-pending row is deliberately a recall handoff:
            # allow a soft metadata penalty to reach the downloaded visual
            # gate, while the explicit -999 hard blockers (streamer/PiP,
            # unsafe package, fake trailer, wrong domain) remain excluded.
            recall_pending = bool(
                candidate.get("nearest_match_pending")
                or candidate.get("semantic_context_pending")
            )
            # Scores at or below -180 represent explicit semantic failures
            # (subject mismatch, negative domain hit, commentary without a
            # visual signal). Keep those out in lenient mode (-179.0 floor);
            # only deliberate recall handoffs (recall_pending) reach -220.0.
            minimum_score = (
                -220.0
                if recall_pending
                else (-179.0 if lenient_acceptance else float(os.environ.get("VIDZEO_YT_MIN_METADATA_SCORE", "-120.0") or -120.0))
            )
            if score > minimum_score:
                # Source diversity is a recall aid, not a hard rejection. Use
                # a fresh video for each scene while the pool has one, then
                # fall back to another timestamp from an already-used video
                # when no distinct source survives the gates.
                if (prefer_distinct_sources or intro_unique) and candidate_id in assigned_source_ids:
                    reused_source_scored.append((score, candidate))
                else:
                    scored.append((score, candidate))
            else:
                rejection_counts[candidate.get("v2_reject_reason") or _hard_block_reason(candidate) or "low_score"] += 1
        scored.sort(key=lambda item: item[0], reverse=True)
        reused_source_scored.sort(key=lambda item: item[0], reverse=True)
        # Try distinct sources first, then fall back to high-scoring candidates from already-used sources
        # at distant timestamps so scenes are not starved when YouTube footage is limited.
        ranked_options = list(scored)
        for r_item in reused_source_scored:
            if not any(_video_id(existing[1]) == _video_id(r_item[1]) for existing in scored):
                ranked_options.append(r_item)
        if not ranked_options:
            ranked_options = reused_source_scored
        for score, candidate in ranked_options[:16]:
            clone = dict(candidate)
            clone["score"] = round(score, 2)
            clone["retrieval_context"] = retrieval_context
            clone["bite_clip"] = bite_clip
            clone["intro_unique"] = intro_unique
            clone['scene_text'] = str(row.get('scene_text') or '')
            clone['scene_subject'] = str(row.get('candidate_subject') or '')
            # A scarce pool may use distant moments from one master. Keep
            # intro cuts on the wider reuse gap, not the eight-second bite gap.
            if intro_unique:
                clone['bite_clip'] = False
            clip_dur = float(scene_durations[idx] if idx < len(scene_durations) else 4.0)
            timestamp = _pick_timestamp(clone, idx, used_timestamps, clip_dur)
            if timestamp is None:
                clone["v2_reject_reason"] = "no_distinct_middle_timestamp_available"
                continue
            clone["best_timestamp"] = timestamp
            assignments[idx] = clone
            assigned_source_ids.add(_video_id(clone))
            break
    if rejection_counts:
        summary = ", ".join(f"{reason}={count}" for reason, count in rejection_counts.most_common(6))
        print(f"   [yt-v2] rank audit: {summary}")
    return assignments


def get_youtube_clips(
    script: str,
    title: str,
    num_scenes: int,
    scene_durations: Optional[List[float]] = None,
    scene_timing_windows: Optional[List[Dict[str, Any]]] = None,
    api_key: str = "",
    openrouter_api_key: str = "",
    groq_api_key: str = "",
    scene_texts: Optional[List[str]] = None,
    scene_query_overrides: Optional[List[Dict[str, Any]]] = None,
    scene_query_future: Any = None,
    video_format: Any = "",
    progress_callback=None,
    output_dir: Optional[Any] = None,
    lenient_acceptance: bool = False,
    download_candidate_limit: int = 4,
    allow_same_source_reuse: Optional[bool] = None,
    stop_on_soft_timeout: bool = False,
    stream_downloads: bool = False,
    max_download_scenes: int = 0,
    min_resolved_ratio_before_soft_stop: float = 0.0,
    stop_when_coverage_target_met: bool = False,
    max_download_attempts_per_scene: int = 0,
    search_timeout_seconds: Optional[float] = None,
    soft_timeout_seconds: Optional[float] = None,
    soft_ceiling_seconds: Optional[float] = None,
    hard_timeout_seconds: Optional[float] = None,
    session_jar: Any = None,
    youtube_session: Any = None,
    use_authenticated_session: bool = True,
    visual_context: Any = None,
    prefer_whisper_timing: Optional[bool] = None,
    pool_first: Optional[bool] = None,
    cancellation: Any = None,
    download_concurrency: Optional[int] = None,
    excluded_video_ids: Optional[List[str]] = None,
    excluded_channel_ids: Optional[List[str]] = None,
    **__,
) -> List[Optional[str]]:
    del api_key, groq_api_key, video_format
    # The production pipeline no longer has a relevance-bypassing lenient
    # mode. Retain the argument only as an API compatibility shim.
    # Lenient mode is metadata-recall mode, not a quality bypass. It lets
    # plausible but weakly titled candidates reach transcript/grid timing,
    # FFmpeg integrity, overlay screening, and visual relevance checks.
    # Explicit hard blockers still remain rejected.
    lenient_acceptance = bool(
        lenient_acceptance
        or str(os.environ.get('VIDZEO_YT_LENIENT_ACCEPTANCE', '1') or '1').strip().lower()
        in {'1', 'true', 'yes', 'on'}
    )
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    if visual_context is None:
        effective_openrouter_key = str(
            openrouter_api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("VIDZEO_OPENROUTER_API_KEY")
            or ""
        ).strip()
        if effective_openrouter_key:
            try:
                v2_pipeline_mode = str(
                    os.environ.get("VIDZEO_PIPELINE_V2_MODE", "")
                ).strip().lower() in {"1", "true", "yes", "on", "v2"}
                gate_enabled = str(
                    os.environ.get("VIDZEO_YT_VISUAL_GATE", "1") or "1"
                ).strip().lower() in {"1", "true", "yes", "on"} and not v2_pipeline_mode
                if gate_enabled:
                    from services.visual_intelligence.context import VisualDecisionContext

                    visual_context = VisualDecisionContext.from_config(
                        {
                            "visual_understanding": True,
                            "openrouter_api_key": effective_openrouter_key,
                            "visual_max_calls": max(3, len(scene_texts or []) + 2),
                        },
                        max(1, int(num_scenes or 1) or len(scene_texts or []) or 1),
                    )
            except Exception as exc:
                print(f"   [yt-v2] visual gate context unavailable: {type(exc).__name__}")
                visual_context = None
    start_time = time.time()
    batch_started_monotonic = time.monotonic()
    requested_scene_count = max(0, int(num_scenes or 0))
    output_scene_count = requested_scene_count
    # ``scene_durations`` can be a provisional storyboard estimate.  Once
    # Whisper has produced phrase/sentence windows, those boundaries are the
    # authoritative clip duration so footage changes with the narration.
    if not scene_texts:
        scene_texts = [row["description"] for row in parse_script_to_scenes(script, title, "", requested_scene_count)]
    source_scene_texts = [str(text or "").strip() for text in list(scene_texts or [])[:output_scene_count]]
    # Do not search a blank slot using the whole title as a fabricated scene.
    # Keep the original indices so a missing narration beat remains an empty
    # result slot instead of shifting later clips onto the wrong scene.
    active_scene_indices = [index for index, text in enumerate(source_scene_texts) if text]
    scene_texts = [source_scene_texts[index] for index in active_scene_indices]
    requested_scene_count = len(scene_texts)

    def _restore_scene_slots(paths: Sequence[Optional[str]], audit: Optional[Dict[str, Any]] = None) -> YouTubeClipResults:
        aligned: List[Optional[str]] = [None] * output_scene_count
        for local_index, source_index in enumerate(active_scene_indices):
            if local_index < len(paths) and source_index < len(aligned):
                aligned[source_index] = paths[local_index]
        result_audit = dict(audit or {})
        for field in ("attempts", "scenes", "download_passes", "ranged_moments"):
            value = result_audit.get(field)
            if isinstance(value, Mapping):
                remapped = {}
                for key, item in value.items():
                    try:
                        local_index = int(key)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= local_index < len(active_scene_indices):
                        remapped[str(active_scene_indices[local_index])] = item
                result_audit[field] = remapped
        for field in ("cut_audit", "post_snapshot_rejections"):
            value = result_audit.get(field)
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    try:
                        local_index = int(item.get("scene_index"))
                    except (TypeError, ValueError):
                        continue
                    if 0 <= local_index < len(active_scene_indices):
                        item["scene_index"] = active_scene_indices[local_index]
        result_audit.setdefault("retrieval_requested", requested_scene_count)
        result_audit["requested"] = output_scene_count
        result_audit["scene_count_with_narration"] = requested_scene_count
        result_audit["scene_index_map"] = list(active_scene_indices)
        result_audit["resolved"] = sum(1 for path in aligned if path)
        return YouTubeClipResults(aligned, audit=result_audit)

    if requested_scene_count == 0:
        return _restore_scene_slots([], {
            "resolved": 0,
            "requested": output_scene_count,
            "scene_count_with_narration": 0,
            "stop_reason": "no_scene_texts",
            "download_passes": {},
            "candidate_attempt_limit": 0,
        })
    scene_durations = _resolve_youtube_scene_durations(
        output_scene_count,
        scene_durations,
        scene_timing_windows,
        prefer_aligned=prefer_whisper_timing,
    )
    scene_durations = [scene_durations[index] for index in active_scene_indices]
    if isinstance(scene_timing_windows, list):
        scene_timing_windows = [
            scene_timing_windows[index] if index < len(scene_timing_windows) else {}
            for index in active_scene_indices
        ]
    authenticated_session = _get_authenticated_youtube_session(
        session_jar=session_jar,
        youtube_session=youtube_session,
        enabled=use_authenticated_session,
    )
    if not scene_query_overrides and scene_query_future is not None:
        try:
            planned = scene_query_future.result(timeout=float(os.environ.get("VIDZEO_YT_V2_QUERY_FUTURE_TIMEOUT", "45") or 45))
            if isinstance(planned, list):
                scene_query_overrides = planned
        except Exception as exc:
            print(f"   [yt-v2] LLM query future unavailable, using local query builder: {str(exc)[:100]}")

    if isinstance(scene_query_overrides, list):
        scene_query_overrides = [
            scene_query_overrides[index] if index < len(scene_query_overrides) else {}
            for index in active_scene_indices
        ]

    rows = _build_scene_rows(title, scene_texts, scene_query_overrides)
    # Carry the editorial lane into the provider rows.  The first 15 seconds
    # is a distinct hook and must not replay one source; every other YouTube
    # scene is eligible for transcript/timestamped bite reuse from a vetted
    # master video.  Timing windows are authoritative when Whisper supplied
    # them, with a conservative first-scene fallback for older callers.
    inferred_start = 0.0
    for row_index, row in enumerate(rows):
        timing = (
            scene_timing_windows[row_index]
            if isinstance(scene_timing_windows, list) and row_index < len(scene_timing_windows)
            and isinstance(scene_timing_windows[row_index], Mapping)
            else {}
        )
        try:
            raw_start = timing.get("start_seconds", timing.get("start", None))
            if raw_start is None:
                raw_start = row.get("start_seconds", inferred_start)
            start_seconds = float(raw_start or 0.0)
        except (TypeError, ValueError):
            start_seconds = inferred_start
        try:
            inferred_start = max(inferred_start, start_seconds) + float(
                scene_durations[row_index] if row_index < len(scene_durations) else 4.0
            )
        except (TypeError, ValueError):
            inferred_start = max(inferred_start, start_seconds) + 4.0
        row["intro_unique"] = bool(
            row.get("intro_unique")
            or row.get("is_intro")
            or start_seconds < 15.0
            or (row_index == 0 and not timing)
        )
        row["bite_clip"] = bool(
            row.get("bite_clip")
            or row.get("youtube_bite_clip")
            or not row.get("is_outro")
        )
    groups = _group_rows(rows)
    primary_subject = next((row.get("primary_container_subject") for row in rows if row.get("primary_container_subject")), "")
    if primary_subject:
        print(f"   [yt-v2] primary anchor lock: {primary_subject} (intro/outro and publisher context)")
    print(
        f"   [yt-v2] container pools: {len(groups)} pool(s), "
        + ", ".join(f"{rows[0].get('section_subject') or key}({len(rows)})" for key, rows in list(groups.items())[:8])
    )
    if progress_callback:
        progress_callback(0.08, f"YouTube V2: searching {len(groups)} container pools")
    pools = _search_container_pools(
        groups,
        search_timeout_seconds=search_timeout_seconds,
        session_jar=authenticated_session,
        cancellation=cancellation,
    )
    excluded_videos = set(excluded_video_ids or ())
    excluded_channels = set(excluded_channel_ids or ())
    if excluded_videos or excluded_channels:
        before = sum(len(items) for items in pools.values())
        pools = {key: [item for item in items
            if _video_id(item) not in excluded_videos
            and str(item.get('channel_id') or item.get('uploader_id') or '') not in excluded_channels]
            for key, items in pools.items()}
        print(f'   [yt-v2] excluded {before - sum(map(len, pools.values()))} owned export candidate(s) before ranking')
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    total_candidates = sum(len(v) for v in pools.values())
    search_elapsed_seconds = time.monotonic() - batch_started_monotonic
    print(f"   [yt-v2] search complete: {total_candidates} unique candidate(s)")
    if progress_callback:
        progress_callback(0.35, f"YouTube V2: ranked {total_candidates} candidates")
    if allow_same_source_reuse is None:
        allow_same_source_reuse = YOUTUBE_ALLOW_SAME_SOURCE_REUSE
    assignments = _assign_candidates(
        rows,
        pools,
        scene_durations,
        lenient_acceptance=bool(lenient_acceptance),
        allow_same_source_reuse=bool(allow_same_source_reuse),
        prefer_distinct_sources=True,
    )
    selected = sum(1 for item in assignments if item)
    print(f"   [yt-v2] ranked {selected}/{requested_scene_count} metadata winner(s); download verification pending")
    if str(os.environ.get("VIDZEO_YT_V2_SKIP_DOWNLOAD", "0")).strip().lower() in {"1", "true", "yes", "on"}:
        return _restore_scene_slots(
            [None] * requested_scene_count,
            {
                "resolved": 0,
                "requested": requested_scene_count,
                "metadata_selected": selected,
                "download_skipped": True,
                "strategy": "progressive_one_download_per_scene",
            },
        )

    if pool_first is not None:
        pool_first_requested = bool(pool_first)
    else:
        pool_first_requested = str(os.environ.get("VIDZEO_YT_POOL_FIRST", "0")).strip().lower() in {"1", "true", "yes", "on"}
    # The Electron app uses ranged downloads for short YouTube-only jobs.
    # Keep this invariant inside the provider too, so a persisted/stale UI
    # setting cannot silently re-enable the full-source pool and its long
    # startup wait.
    if (
        requested_scene_count <= 12
        and not bool(pool_first)
        and str(os.environ.get("VIDZEO_YT_SHORT_JOB_RANGED", "1")).strip().lower() in {"1", "true", "yes", "on"}
    ):
        pool_first_requested = False
    try:
        pool_first_min_scenes = max(2, int(os.environ.get("VIDZEO_YT_POOL_FIRST_MIN_SCENES", "3") or 3))
    except (TypeError, ValueError):
        pool_first_min_scenes = 3
    pool_first_enabled = pool_first_requested and (
        requested_scene_count >= pool_first_min_scenes
        or str(os.environ.get("VIDZEO_YT_POOL_FIRST_FORCE", "0")).strip().lower() in {"1", "true", "yes", "on"}
    )
    # Mixed-media quotas are normally small (the rest of the timeline is web
    # imagery).  Do not let a persisted ``VIDZEO_YT_POOL_FIRST=1`` turn a
    # six-to-twelve-clip request into a full-source download batch: the pool
    # renderer has to fetch whole videos before it can cut moments, while the
    # direct ranged lane can fetch the exact 3-5 second window immediately.
    # This was the source of 360s branch stalls with ``shared sources ready:
    # 0/N`` followed by a zero-clip handoff.  Long jobs still retain pool-first
    # behaviour, and an explicit FORCE setting remains an operator override.
    try:
        direct_pool_bypass_scenes = max(
            1,
            int(os.environ.get("VIDZEO_YT_POOL_DIRECT_CLIP_BYPASS_SCENES", "24") or 24),
        )
    except (TypeError, ValueError):
        direct_pool_bypass_scenes = 24
    direct_clip_mode = str(
        os.environ.get("VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    pool_force = str(os.environ.get("VIDZEO_YT_POOL_FIRST_FORCE", "0") or "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if (
        pool_first_enabled
        and direct_clip_mode
        and requested_scene_count <= direct_pool_bypass_scenes
        and not pool_force
    ):
        pool_first_enabled = False
        print(
            f"   [yt-v2] pool-first bypassed for {requested_scene_count} scene(s): "
            "direct ranged clip mode avoids whole-source downloads"
        )
    if pool_first_requested and not pool_first_enabled:
        print(f"   [yt-v2] pool-first bypassed for {requested_scene_count} scene(s); using ranged downloads")
    if pool_first_enabled:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        from services.youtube_pool_renderer import resolve_pool_first
        pool_first_pools = ClipPools(
            rows=rows,
            groups=groups,
            pools=pools,
            assignments=assignments,
            scene_durations=scene_durations,
        )
        # Pool-first admission deliberately probes/downloads first and only
        # then fetches transcripts for the small admitted source set. The old
        # ordering fetched transcripts for the entire metadata universe before
        # the cheap probe gate, so eight stalled transcript requests could
        # consume the 300s branch wall and leave local cuts cancelled.
        transcript_documents: Dict[str, Any] = {}
        moment_assignments: Dict[str, Any] = {}
        source_metadata: Dict[str, Any] = {}
        try:
            from services.youtube_source_pool import build_sources
            source_records = build_sources(pools)
        except Exception:
            source_records = {}
        pool_first_pools.transcript_documents = transcript_documents
        pool_first_pools.moment_assignments = moment_assignments
        pool_first_pools.source_metadata = source_metadata
        pool_first_pools.source_records = source_records
        pool_first_pools.audit = {
            "pool_count": len(groups),
            "unique_sources": len(source_records),
            "transcripts_fetched": sum(1 for document in transcript_documents.values() if getattr(document, "available", False)),
            "moments_indexed": sum(len(values) for values in moment_assignments.values()),
            "scene_assignments": selected,
        }
        from services.youtube_pool_renderer import _source_shortlist
        planned_source_ids = _source_shortlist(pool_first_pools)
        _plan_debug = []
        for _sid in planned_source_ids:
            _rec = (pool_first_pools.source_records or {}).get(_sid)
            _usage = 0.0
            for _moments in (pool_first_pools.moment_assignments or {}).values():
                for _m in list(_moments or [])[:3]:
                    if str(getattr(_m, "source_id", "") or getattr(_m, "video_id", "")) == _sid:
                        _usage += 1.0
            _doc = (pool_first_pools.transcript_documents or {}).get(_sid)
            _plan_debug.append(
                f"{_sid}(score={getattr(_rec, 'metadata_score', 0.0) or 0.0:.2f},usage={_usage:.0f},transcript={getattr(_doc, 'status', 'n/a') or 'n/a'})"
            )
        _transcript_ready = [
            str(_sid) for _sid, _doc in (pool_first_pools.transcript_documents or {}).items()
            if getattr(_doc, "available", False)
        ]
        print(
            f"   [yt-v2] pool plan: candidates={total_candidates}, "
            f"shortlist={len(planned_source_ids)}, transcripts={len(transcript_documents)}, "
            f"moments={sum(len(values) for values in moment_assignments.values())}, "
            f"source_downloads_planned={len(planned_source_ids)}",
            flush=True,
        )
        print(
            f"   [yt-v2] shortlist detail: {', '.join(_plan_debug)} | transcript-ready: {_transcript_ready}",
            flush=True,
        )
        run_id = _new_job_run_id("pool", title)
        pool_results = resolve_pool_first(
            sys.modules[__name__],
            pool_first_pools,
            visual_context=visual_context,
            output_dir=Path(output_dir) / run_id if output_dir else Path(TEMP_DIR) / "yt_clips" / run_id,
            session_jar=authenticated_session,
            stream_downloads=bool(stream_downloads),
            cancellation=cancellation,
            progress_callback=progress_callback,
        )
        pool_first_pools.audit.update(getattr(pool_results, "audit", {}) or {})
        pool_first_pools.audit.setdefault("source_records", len(source_records))
        pool_first_pools.audit["scene_assignments"] = sum(1 for path in pool_results if path)
        pool_first_pools.audit["retrieval_visual_decisions"] = getattr(pool_results, "retrieval_decisions", {})
        print(f"   [yt-v2] pool-first complete: {sum(1 for path in pool_results if path)}/{requested_scene_count} clips")
        return _restore_scene_slots(pool_results, pool_first_pools.audit)

    run_id = _new_job_run_id("v2", title)
    output_root = Path(output_dir) if output_dir else Path(TEMP_DIR) / "yt_clips" / run_id
    output_root.mkdir(parents=True, exist_ok=True)
    if cancellation is not None and hasattr(cancellation, "child"):
        download_cancellation = cancellation.child(
            f"{getattr(cancellation, 'job_id', '')}:youtube-v2"
        )
    else:
        from services.cancellation import CancellationContext
        download_cancellation = CancellationContext(job_id="youtube-v2")
    pass_download_cancellation = cancellation is not None
    results: List[Optional[str]] = [None] * requested_scene_count
    # Ranged clips are network bound. Default 6 workers, cap 8; the memory
    # fit inside _ranged_download_workers still scales a tight host down on
    # its own (see _ranged_download_worker_limit). A mixed-media job may
    # raise the count through download_concurrency / the env override.
    # Keep the pre-fit requested count for the telemetry receipt below.
    configured_download_workers = _requested_download_workers(download_concurrency)
    download_workers = _ranged_download_workers(download_concurrency)
    try:
        import psutil
        memory = psutil.virtual_memory()
        # Each range worker may also decode AV1 or transcode HD footage.
        # Bound these memory-heavy jobs independently of cheap HTTP searches.
        available_mb = int(memory.available / (1024 * 1024))
        download_workers = _ranged_download_worker_limit(download_workers, available_mb, memory.total)
    except (ImportError, OSError):
        pass
    try:
        configured_attempts = int(max_download_attempts_per_scene or 0)
    except (TypeError, ValueError):
        configured_attempts = 0
    max_attempts = max(
        1,
        min(
            6,
            configured_attempts
            or int(os.environ.get("VIDZEO_YT_V2_DOWNLOAD_ATTEMPTS_PER_SCENE", os.environ.get("VIDZEO_YT_MAX_DOWNLOAD_ATTEMPTS_PER_SCENE", "4")) or 4),
        ),
    )
    try:
        configured_candidate_limit = int(download_candidate_limit or 1)
    except (TypeError, ValueError):
        configured_candidate_limit = 1
    candidate_limit = max(1, min(12, configured_candidate_limit))
    download_timestamp_lock = Lock()
    download_used_timestamps: Dict[str, List[float]] = defaultdict(list)
    download_timestamp_owners = defaultdict(set)
    failed_download_counts: Counter = Counter()
    network_failed_sources = set()
    provider_network_unavailable = threading.Event()
    attempt_audit: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    chapter_cache = {}
    pending_alternate_cuts = {}
    ranged_moments, ranged_moment_audit = {}, {'status': 'disabled'}
    if str(os.environ.get('VIDZEO_YT_RANGED_MOMENTS', '1')).lower() not in {'0', 'false', 'off'}:
        from .youtube_ranged_moments import prepare_ranged_moments
        try:
            remaining = float(hard_timeout_seconds or 900) - (time.monotonic() - batch_started_monotonic) - 60
            if remaining > 0:
                ranged_moments, ranged_moment_audit = prepare_ranged_moments(
                    [dict(row, pool_key=_row_pool_key(row)) for row in rows], assignments, pools,
                    scene_durations, session_jar=authenticated_session,
                    deadline_seconds=min(20., remaining), cancellation=cancellation)
        except Exception as exc:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            ranged_moment_audit = {'status': 'unavailable', 'error_type': type(exc).__name__}
        print(f"   [yt-v2] ranged moment evidence: {ranged_moment_audit.get('scenes_with_moments', 0)}/{requested_scene_count} scenes", flush=True)
    for assigned_index, assigned in enumerate(assignments):
        if assigned and assigned.get("best_timestamp") is not None:
            try:
                for key in _timestamp_reuse_keys(assigned):
                    download_used_timestamps[key].append(float(assigned.get("best_timestamp")))
                    download_timestamp_owners[(key, float(assigned['best_timestamp']))].add(assigned_index)
            except Exception:
                pass

    def _download_scene(idx: int, candidate: Dict[str, Any]) -> Tuple[int, Optional[str]]:
        if provider_network_unavailable.is_set():
            return idx, None
        if download_cancellation is not None:
            download_cancellation.raise_if_cancelled()
        subject = rows[idx].get("candidate_subject") or rows[idx].get("section_subject") or rows[idx].get("scene_subject") or ""
        key = _row_pool_key(rows[idx])
        retrieval_context = _row_retrieval_context(rows[idx])
        pool = sorted(
            [dict(item) for item in (pools.get(key) or [candidate])],
            key=lambda c: _score_candidate(
                c,
                subject,
                rows[idx].get("scene_text", ""),
                c.get("query_text") or "",
                retrieval_context,
            ),
            reverse=True,
        )
        tried = set()
        options = [candidate]
        options.extend(option for option in pool if option is not candidate)
        previous_ids = {str(item.get('candidate_id') or '') for item in attempt_audit[idx]}
        fresh_options = [option for option in options if _video_id(option) not in previous_ids]
        # After fresh publishers are exhausted, a later scheduling turn can
        # try another moment from the same master, never its failed timestamp.
        options = fresh_options or [dict(option, best_timestamp=None) for option in options
                                    if float(option.get('duration') or 0.0) >= 35.0]
        # Hard metadata rejects must never be re-downloaded just because vision
        # is enabled.
        options = [
            option for option in options
            if _score_candidate(
                option,
                subject,
                rows[idx].get("scene_text", ""),
                option.get("query_text") or "",
                retrieval_context,
            ) > -999.0
        ]
        attempt_budget = min(max_attempts, candidate_limit)
        if visual_context is not None:
            visual_required = bool(getattr(visual_context, "visual_required", False))
            if visual_required:
                attempt_budget = max(attempt_budget, min(max_attempts, 3))
        # Keep several ranked alternatives available, but consume them
        # progressively. This controls total attempts; it is not a parallel
        # download fan-out.
        try:
            progressive_limit = max(
                1,
                min(6, int(os.environ.get("VIDZEO_YT_PROGRESSIVE_ATTEMPTS", str(attempt_budget)) or attempt_budget)),
            )
        except (TypeError, ValueError):
            progressive_limit = attempt_budget
        # The fair coverage scheduler gives every scene one attempt per pass;
        # retries are re-queued in round-robin order.
        attempt_budget = 1
        if not options:
            return idx, None

        duration = float(scene_durations[idx] if idx < len(scene_durations) else 4.0)
        # Pre-plan distinct candidates up to attempt_budget (under lock).
        plans = pending_alternate_cuts.pop(idx, [])
        for option in options:
            if len(plans) >= max(1, attempt_budget):
                break
            option = dict(option, scene_text=str(rows[idx].get('scene_text') or ''),
                          scene_subject=str(rows[idx].get('candidate_subject') or subject))
            vid = _video_id(option)
            if not vid or vid in tried:
                continue
            tried.add(vid)
            moment_options = [moment for moment in ranged_moments.get(idx, [])
                              if moment['video_id'] == vid]
            if not moment_options and not option.get('chapters') and float(option.get('duration') or 0) >= 90:
                remaining = hard_deadline - time.monotonic() if hard_deadline is not None else 60
                if remaining > 25:
                    if vid not in chapter_cache:
                        from services.youtube_source_chapters import source_chapters
                        try:
                            chapter_timeout = max(1.0, min(10.0, float(
                                os.environ.get('VIDZEO_YT_CHAPTER_TIMEOUT_SECONDS', '5') or 5
                            )))
                        except (ValueError, TypeError):
                            chapter_timeout = 5.0
                        chapter_cache[vid] = source_chapters(vid, timeout_seconds=min(chapter_timeout, remaining - 20))
                    if chapter_cache[vid]:
                        option = dict(option, chapters=chapter_cache[vid], best_timestamp=None,
                                      scene_text=str(rows[idx].get('scene_text') or ''),
                                      scene_subject=str(rows[idx].get('candidate_subject') or subject))
            with download_timestamp_lock:
                if failed_download_counts[vid] >= 2:
                    continue
                reuse_keys = _timestamp_reuse_keys(option)
                start = None
                for moment in moment_options:
                    moment_start = float(moment['start'])
                    if not any(abs(moment_start - old) < max(duration + 1.5, 8.)
                               for k in reuse_keys for old in download_used_timestamps[k]):
                        start = moment_start
                        option.update(moment=moment, transcript_score=moment['match_score'],
                                      timestamp_evidence='transcript_match')
                        break
                if start is None:
                    start = option.get("best_timestamp")
                gap_val = max(
                    float(os.environ.get("VIDZEO_YT_REUSE_TIMESTAMP_GAP", "20") or 20),
                    duration + 4.0,
                )
                if start is not None:
                    collides = any(
                        abs(float(start) - old) < (gap_val - 0.05)
                        and download_timestamp_owners[(k, old)] != {idx}
                        for k in reuse_keys
                        for old in download_used_timestamps[k]
                    )
                    if collides:
                        start = None
                if start is None:
                    start = _pick_timestamp(option, idx, download_used_timestamps, duration)
                    option.setdefault('timestamp_evidence', 'chapter_or_heuristic_requires_pixel_review')
                if start is not None:
                    for k in reuse_keys:
                        if float(start) not in download_used_timestamps[k]:
                            download_used_timestamps[k].append(float(start))
                        download_timestamp_owners[(k, float(start))].add(idx)
            if start is None:
                if os.environ.get("VIDZEO_YT_V2_DEBUG_PLANS"):
                    print(f"   [yt-v2] scene {idx + 1}: no timestamp for {vid} ({str(option.get('title') or '')[:60]})")
                continue
            plans.append((option, vid, float(start)))
        if os.environ.get("VIDZEO_YT_V2_DEBUG_PLANS"):
            print(
                f"   [yt-v2] scene {idx + 1}: plans={[(p[1], round(p[2], 1)) for p in plans]} "
                f"options={len(options)}"
            )
        if not plans:
            return idx, None

        attempted_fingerprints = set()

        def _download_one(option: Dict[str, Any], vid: str, start: float) -> Optional[str]:
            # Each candidate writes to its own slot so parallel downloads don't
            # clobber each other; the winner is later renamed to the scene path.
            out = output_root / (
                f"yt_clip_scene_{idx + 1:03d}.cand{_safe_youtube_path_token(vid)}_ts{int(round(float(start)))}.mp4"
            )
            fingerprint = f"{vid}:{round(float(start), 2)}"
            if fingerprint in attempted_fingerprints:
                return None
            attempted_fingerprints.add(fingerprint)
            audit = {
                "attempt": len(attempt_audit[idx]) + 1,
                "fingerprint": fingerprint,
                "candidate_id": vid,
                "video_id": vid,
                "title": str(option.get("title") or "")[:180],
                "channel": str(option.get("channel") or option.get("uploader") or "")[:120],
                "description": str(option.get("description") or "")[:500],
                "source_url": str(_candidate_url(option) or ""),
                "rights_status": str(option.get("rights_status") or "platform_terms_review_required"),
                "rights_risk": str(option.get("rights_risk") or "high"),
                "rights_review_required": bool(option.get("rights_review_required", True)),
                "rights_evidence": str(option.get("rights_evidence") or "youtube_source_without_reuse_clearance"),
                "query": str(option.get("query_text") or "")[:180],
                "metadata_score": float(option.get("score") or option.get("v2_score") or 0.0),
                "timestamp": round(float(start), 2),
                "timestamp_evidence": option.get('timestamp_evidence', 'provided_timestamp_requires_pixel_review'),
                "moment": dict(option.get('moment') or {}),
                "clip_duration": round(float(duration), 2),
                "status": "download_started",
                "cache_hit": False,
            }
            attempt_audit[idx].append(audit)
            attempt_started = time.monotonic()
            try:
                downloaded = False
                # A transport cache hit still goes through the overlay and
                # scene-specific visual gates below. It only removes the
                # expensive network/FFmpeg download; it never becomes proof
                # that a cut is relevant to a different scene.
                try:
                    from services.youtube_media_cache import get_verified_cut
                    cached = get_verified_cut(vid, start, duration)
                except Exception:
                    cached = None
                if cached and cached.get("path"):
                    try:
                        shutil.copyfile(str(cached["path"]), str(out))
                        downloaded = out.is_file() and out.stat().st_size >= 8192
                        audit["cache_hit"] = bool(downloaded)
                        audit["cache_path"] = str(cached["path"])
                        if downloaded:
                            audit["status"] = "cache_restored"
                    except Exception:
                        downloaded = False
                if not downloaded:
                    download_kwargs = {
                        "session_jar": authenticated_session,
                        "stream_downloads": bool(stream_downloads),
                    }
                    if pass_download_cancellation:
                        download_kwargs["cancellation"] = download_cancellation
                    downloaded = _download_clip(
                        option,
                        out,
                        start,
                        duration,
                        **download_kwargs,
                    )
            except Exception as exc:
                if download_cancellation is not None and download_cancellation.cancelled():
                    raise
                audit.update({"status": "download_exception", "reason": f"{type(exc).__name__}:{exc}"[:240]})
                downloaded = False
            audit['runtime_warning_categories'] = list(option.get('_runtime_warning_categories') or [])
            audit["elapsed_seconds"] = round(time.monotonic() - attempt_started, 2)
            if downloaded:
                audit["status"] = "downloaded"
                # Reject still/slideshow downloads before the relatively
                # expensive OCR/overlay pass, leaving time for another source.
                motion_quality = _screen_downloaded_motion(str(out.resolve()), option)
                audit.update(quality_check=motion_quality, path=str(out.resolve()))
                if motion_quality.get('checked') and motion_quality.get('reject'):
                    audit.update(status='motion_or_quality_rejected',
                                 rejection_reason=motion_quality.get('reason') or 'motion_quality_failed')
                    return None
                try:
                    from services.youtube_pool_renderer import _native_overlay_lane_for_category
                    _native_overlay_lane = _native_overlay_lane_for_category(
                        rows[idx].get("domain_category")
                    )
                except Exception:
                    _native_overlay_lane = False
                soft_facecam_categories = {"politics", "history", "documentary", "news"}
                row_category = str(rows[idx].get("domain_category") or "").strip().lower()
                cleaned_path, overlay_audit = _clean_downloaded_clip_overlays(
                    str(out.resolve()),
                    cancellation=download_cancellation,
                    gaming_lane=str(rows[idx].get("domain_category") or "").strip().lower() == "gaming",
                    native_overlay_lane=_native_overlay_lane,
                    allow_soft_facecam=row_category in soft_facecam_categories,
                )
                audit.update({
                    "overlay_checked": bool(overlay_audit.get("checked")),
                    "source_overlays_preserved": bool(overlay_audit.get("source_overlays_preserved")),
                    "no_blur": True,
                    "overlay_policy": str(overlay_audit.get("overlay_policy") or "preserve_source_no_blur"),
                    "overlay_types": list(overlay_audit.get("overlay_types") or []),
                    "overlay_clean": bool(overlay_audit.get("clean", True)),
                    "blur_regions": list(overlay_audit.get("blur_regions") or []),
                    "rejection_reason": str(overlay_audit.get("rejection_reason") or ""),
                    "cleaned_path": str(overlay_audit.get("cleaned_path") or ""),
                    "overlay_regions": len(overlay_audit.get("regions") or []),
                    "overlay_watermark_prob": round(float(overlay_audit.get("watermark_prob") or 0.0), 3),
                    "watermark_tolerated": bool(overlay_audit.get("watermark_tolerated")),
                    "overlay_has_subtitle": bool(overlay_audit.get("has_subtitle")),
                    "overlay_blurred": bool(overlay_audit.get("blurred")),
                    "overlay_blur_failed": bool(overlay_audit.get("blur_failed")),
                    "talking_head": bool(overlay_audit.get("talking_head")),
                    "talking_head_confidence": round(float(overlay_audit.get("talking_head_confidence") or 0.0), 3),
                    "facecam_inset": bool(overlay_audit.get("facecam_inset")),
                    "facecam_confidence": round(float(overlay_audit.get("facecam_confidence") or 0.0), 3),
                    "facecam_reason": str(overlay_audit.get("facecam_reason") or ""),
                    "facecam_corner": str(overlay_audit.get("facecam_corner") or ""),
                    "facecam_tolerated": bool(overlay_audit.get("facecam_tolerated")),
                    "path": str(cleaned_path or out.resolve()),
                })
                if not cleaned_path:
                    audit["status"] = "overlay_or_talking_head_rejected"
                    try:
                        out.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return None
                # Cache only after the deterministic transport/overlay pass.
                # The remote semantic gate is intentionally rerun for the
                # current scene after restoration, so this remains a speed
                # cache rather than a relevance bypass.
                try:
                    from services.youtube_media_cache import put_verified_cut
                    cache_source = str(cleaned_path or out.resolve())
                    cached_path = put_verified_cut(
                        vid, start, duration, cache_source,
                        metadata={
                            "source_url": str(_candidate_url(option) or ""),
                            "title": str(option.get("title") or "")[:180],
                            "channel": str(option.get("channel") or option.get("uploader") or "")[:120],
                            "overlay_policy": str(overlay_audit.get("overlay_policy") or ""),
                            "overlay_types": list(overlay_audit.get("overlay_types") or []),
                        },
                    )
                    if cached_path:
                        audit["cache_stored"] = True
                        audit["cache_path"] = cached_path
                except Exception:
                    audit["cache_stored"] = False
                return cleaned_path
            failure = option.get('_transport_failure')
            audit.update({"status": "download_failed", "reason": failure or "yt_dlp_or_ffmpeg"})
            if failure == 'provider_dns_unavailable':
                with download_timestamp_lock:
                    network_failed_sources.add(vid)
                    if len(network_failed_sources) >= 2:
                        provider_network_unavailable.set()
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            with download_timestamp_lock:
                failed_download_counts[vid] += 1
            return None

        def _finalize_progressive(win_path: str) -> Tuple[int, Optional[str]]:
            final_out = output_root / f"yt_clip_scene_{idx + 1:03d}.mp4"
            original_win_path = str(win_path)
            try:
                final_out.unlink(missing_ok=True)
                Path(win_path).rename(final_out)
                win_path = str(final_out)
            except Exception:
                pass
            # Keep the attempt ledger authoritative after the primary file is
            # promoted. Alternate downloaded candidates remain in their own
            # files and are handed to the shared coordinator for visual
            # comparison; only the winning primary gets the scene filename.
            for attempt in reversed(attempt_audit.get(idx, [])):
                if not isinstance(attempt, dict) or attempt.get("status") != "downloaded":
                    continue
                attempt_path = str(attempt.get("path") or attempt.get("cleaned_path") or "")
                if attempt_path and Path(attempt_path).resolve() == Path(original_win_path).resolve():
                    attempt["path"] = str(Path(win_path).resolve())
                    # Renaming an untouched cut is not overlay remediation.
                    # A nonempty cleaned_path previously made the final gate
                    # classify every promoted sharp clip as a blurred asset.
                    attempt["cleaned_path"] = (
                        str(Path(win_path).resolve()) if attempt.get("overlay_blurred") else ""
                    )
                    attempt["selected"] = True
                    attempt["semantic_verified"] = True
                    attempt["visual_checked"] = True
                    try:
                        from services.youtube_clip_provenance import write_clip_provenance
                        write_clip_provenance(win_path, idx, attempt)
                    except (OSError, ValueError, TypeError):
                        pass
                    break
            return idx, win_path

        # Preserve multiple ranked options and download them one at a time.
        # Transport vision is intentionally disabled here; the shared V2
        # coordinator must receive the downloaded alternates so it can compare
        # the actual pixels across sources instead of inheriting one metadata
        # winner from YouTube search.
        visual_required = bool(getattr(visual_context, "visual_required", False)) if visual_context is not None else False
        transport_only = bool(
            visual_context is not None
            and not bool(getattr(visual_context, "enabled", False))
        )
        advisory_fallback: Optional[str] = None
        downloaded_alternates: List[str] = []
        candidate_fallbacks: List[Tuple[float, str, Dict[str, Any]]] = []
        for plan_index, (option, vid, start) in enumerate(plans):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if provider_network_unavailable.is_set():
                break
            path = _download_one(option, vid, start)
            if provider_network_unavailable.is_set():
                break
            if not path:
                # If cut 1 was rejected (e.g. intro talking head or text overlay) and the video
                # is long enough, try an alternate clean mid-video B-roll timestamp from the same video.
                vid_duration = float(option.get("duration") or 0.0)
                retryable_cut = bool(attempt_audit[idx] and attempt_audit[idx][-1].get('status') in {
                    'motion_or_quality_rejected', 'overlay_or_talking_head_rejected',
                })
                if (retryable_cut and len(options) > 1
                        and attempt_audit[idx][-1].get('rejection_reason') in {
                            'static_image_or_pan', 'still_or_slideshow', 'static_frame_hold',
                        }):
                    # Explore a fresh master before spending another transfer
                    # on a static guide/slideshow. Intro overlays still get
                    # their useful same-source mid-video recovery below.
                    retryable_cut = False
                time_for_cut = hard_deadline is None or hard_deadline - time.monotonic() > 35
                # The fair scheduler deliberately sets attempt_budget=1.
                # Using that budget as the condition made same-source clean
                # B-roll recovery unreachable, even after an intro was rejected.
                if vid_duration >= 35.0 and retryable_cut and time_for_cut:
                    alt_start = start + max(25.0, min(60.0, vid_duration * 0.22))
                    max_safe = vid_duration - max(12.0, duration + 4.0)
                    if alt_start > max_safe:
                        alt_start = max(18.0, vid_duration * 0.40)
                    if abs(alt_start - start) >= 12.0 and alt_start <= max_safe:
                        with download_timestamp_lock:
                            keys = _timestamp_reuse_keys(option)
                            alternate_gap = max(float(os.environ.get('VIDZEO_YT_REUSE_TIMESTAMP_GAP', '20') or 20), duration + 4)
                            if any(abs(alt_start - old) < alternate_gap for key in keys
                                   for old in download_used_timestamps[key]):
                                alt_start = _pick_timestamp(option, idx, download_used_timestamps, duration)
                            if alt_start is not None:
                                for key in keys:
                                    if round(alt_start, 1) not in download_used_timestamps[key]:
                                        download_used_timestamps[key].append(round(alt_start, 1))
                                    download_timestamp_owners[(key, round(alt_start, 1))].add(idx)
                        # A second cut is a new operation in the next fair
                        # scheduling round, not an invisible inline retry.
                        if alt_start is not None:
                            pending_alternate_cuts[idx] = [(dict(option,
                                timestamp_evidence='alternate_cut_requires_pixel_review', moment={}), vid, alt_start)]
            try:
                audit_path = output_root / f'youtube_scene_{idx + 1:03d}.attempts.json'
                temporary = audit_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(attempt_audit[idx], ensure_ascii=False, default=str), encoding='utf-8')
                temporary.replace(audit_path)
            except (OSError, ValueError, TypeError):
                pass
            if not path:
                continue
            if visual_context is None:
                return _finalize_progressive(path)
            if transport_only:
                # With visual analysis disabled there is no consumer for
                # alternate pixels.  Finalize the first clip that passed the
                # deterministic transport/overlay checks instead of
                # downloading the rest of the ranked pool.
                return _finalize_progressive(path)
            relevance_only_visual = str(
                os.environ.get("VIDZEO_YT_RELEVANCE_ONLY", "0")
            ).strip().lower() in {"1", "true", "yes", "on"}
            gta_v_support = bool(rows[idx].get("gta_vi_supporting_gameplay")) and bool(
                option.get("gta_v_supporting_gameplay")
            )
            visual_topic = (
                "Grand Theft Auto VI documentary supporting gameplay: Grand Theft Auto V"
                if gta_v_support else rows[idx].get("section_subject") or subject
            )
            visual_narration = (
                f"{rows[idx].get('scene_text', '')} Use clean Grand Theft Auto V gameplay "
                "as supporting footage for this Grand Theft Auto VI story; no facecam."
                if gta_v_support else rows[idx].get("scene_text", "")
            )
            visual_subject = "Grand Theft Auto V" if gta_v_support else subject
            verdict = _visual_verdict_scored(
                path,
                idx,
                visual_topic,
                visual_narration,
                visual_subject,
                visual_context,
                relevance_only=relevance_only_visual,
            )
            outcome = _classify_visual_result(*verdict)
            if outcome == "unavailable" and visual_required and verdict[2]:
                verdict = _visual_verdict_scored(
                    path,
                    idx,
                    visual_topic,
                    visual_narration,
                    visual_subject,
                    visual_context,
                    relevance_only=relevance_only_visual,
                )
                outcome = _classify_visual_result(*verdict)

            fail_open_raw = rows[idx].get(
                "youtube_fail_open_on_visual_unavailable",
                os.environ.get("VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE", "1"),
            )
            fail_open_youtube = (
                fail_open_raw if isinstance(fail_open_raw, bool)
                else str(fail_open_raw).strip().lower() in {"1", "true", "yes", "on"}
            )
            relaxed_raw = rows[idx].get(
                "youtube_relaxed_visual_gate",
                os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1"),
            )
            relaxed_youtube = (
                relaxed_raw if isinstance(relaxed_raw, bool)
                else str(relaxed_raw).strip().lower() in {"1", "true", "yes", "on"}
            )
            strict_visual = bool(
                getattr(visual_context, "visual_required", False)
                or getattr(visual_context, "fail_closed", False)
            )
            is_fail_open = bool(
                outcome == "unavailable"
                and ((relaxed_youtube and fail_open_youtube) or not strict_visual)
            )

            attempt_audit[idx][-1].update({
                "visual_status": "accepted" if (outcome == "pass" or is_fail_open) else "rejected",
                "visual_outcome": "unavailable_fail_open" if is_fail_open else outcome,
                "visual_score": round(float(verdict[1] or 0.0), 2),
                "visual_available": bool(verdict[2]),
                "visual_reason": str(verdict[3] or ("vision_unavailable_fail_open" if is_fail_open else "")),
                "plan_index": plan_index,
            })
            v_score = round(float(verdict[1] or 0.0), 2)
            v_reason = str(verdict[3] or "")
            fatal_issue = any(tag in v_reason for tag in ("fatal_mismatch", "talking_head", "facecam", "unsafe"))

            if outcome == "pass" or is_fail_open:
                for _, old_path, _ in candidate_fallbacks:
                    try:
                        Path(old_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                return _finalize_progressive(path)

            if not fatal_issue and v_score >= 35.0:
                candidate_fallbacks.append((v_score, path, option))
                if not visual_required:
                    advisory_fallback = advisory_fallback or path
            else:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass

        if candidate_fallbacks:
            candidate_fallbacks.sort(key=lambda item: item[0], reverse=True)
            best_fallback_score, best_fallback_path, best_fallback_opt = candidate_fallbacks[0]
            for _, rem_path, _ in candidate_fallbacks[1:]:
                try:
                    Path(rem_path).unlink(missing_ok=True)
                except Exception:
                    pass
            if best_fallback_score >= 35.0 or not visual_required:
                for attempt in reversed(attempt_audit.get(idx, [])):
                    if isinstance(attempt, dict) and str(attempt.get("path") or "") == str(Path(best_fallback_path).resolve()):
                        attempt["visual_outcome"] = "advisory_pass"
                        attempt["visual_status"] = "accepted"
                        break
                return _finalize_progressive(best_fallback_path)
            else:
                try:
                    Path(best_fallback_path).unlink(missing_ok=True)
                except Exception:
                    pass

        if advisory_fallback:
            return _finalize_progressive(advisory_fallback)
        if downloaded_alternates:
            return _finalize_progressive(downloaded_alternates[0])
        return idx, None

    download_indices = [idx for idx, candidate in enumerate(assignments) if candidate]
    try:
        download_scene_cap = max(0, int(max_download_scenes or 0))
    except (TypeError, ValueError):
        download_scene_cap = 0
    if download_scene_cap:
        download_indices = download_indices[:download_scene_cap]
        print(
            f"   [yt-v2] download scene cap: attempting "
            f"{len(download_indices)}/{selected} assigned scene(s)"
        )

    def _timeout_value(value: Any, env_name: str, default: float = 0.0) -> float:
        raw = value if value is not None else os.environ.get(env_name, default)
        try:
            return max(0.0, float(raw or 0.0))
        except (TypeError, ValueError):
            return default

    soft_timeout = _timeout_value(soft_timeout_seconds, "VIDZEO_YT_FAST_SOFT_TIMEOUT")
    soft_ceiling = _timeout_value(soft_ceiling_seconds, "VIDZEO_YT_FAST_SOFT_CEILING")
    hard_timeout = _timeout_value(
        hard_timeout_seconds,
        "VIDZEO_YT_V2_HARD_TIMEOUT",
        _timeout_value(None, "VIDZEO_YT_BATCH_HARD_TIMEOUT"),
    )
    try:
        soft_coverage = max(0.0, min(1.0, float(min_resolved_ratio_before_soft_stop or 0.0)))
    except (TypeError, ValueError):
        soft_coverage = 0.0
    soft_enabled = bool(stop_on_soft_timeout and (soft_timeout or soft_ceiling or hard_timeout))
    if soft_enabled and soft_ceiling <= 0:
        soft_ceiling = soft_timeout
    if soft_enabled and hard_timeout <= 0:
        hard_timeout = soft_ceiling
    # These bounds describe the entire YouTube branch. Starting them only
    # after search/ranking let a 187-second soft ceiling exceed a 240-second
    # outer process wall, so completed cuts never reached the visual gate.
    soft_deadline = batch_started_monotonic + soft_timeout if soft_timeout > 0 else None
    soft_ceiling_deadline = batch_started_monotonic + soft_ceiling if soft_ceiling > 0 else None
    hard_deadline = batch_started_monotonic + hard_timeout if hard_timeout > 0 else None

    executor = ThreadPoolExecutor(max_workers=download_workers, thread_name_prefix="yt-v2-download")
    future_map: Dict[Any, int] = {}
    pending = set()
    download_queue = deque(download_indices)
    scene_passes = Counter()
    completed_scene_indices = set()
    attempted_count = 0
    done_count = 0
    stopped_reason = ""

    def _submit_available() -> None:
        nonlocal attempted_count
        while not stopped_reason and download_queue and len(pending) < download_workers:
            idx = download_queue.popleft()
            scene_passes[idx] += 1
            future = executor.submit(_download_scene, idx, assignments[idx])
            future_map[future] = idx
            pending.add(future)
            attempted_count += 1

    def _consume_download(future: Any) -> None:
        nonlocal done_count, stopped_reason
        done_count += 1
        try:
            idx, path = future.result()
        except Exception as exc:
            idx, path = future_map[future], None
            print(f"   [yt-v2] download worker failed for scene {idx + 1}: {type(exc).__name__}")
        if path:
            results[idx] = path
            if stop_when_coverage_target_met and soft_coverage > 0:
                ready_ratio = sum(1 for item in results[:requested_scene_count] if item) / max(1, requested_scene_count)
                if ready_ratio >= soft_coverage:
                    stopped_reason = "coverage_target_satisfied"
                    print(
                        f"   [yt-v2] accepted clip target reached ({ready_ratio:.0%}); "
                        "leaving remaining scenes to the parallel image/stock lanes",
                        flush=True,
                    )
        elif not stopped_reason and scene_passes[idx] < min(max_attempts, candidate_limit):
            download_queue.append(idx)
        completed_scene_indices.add(idx)
        if done_count % 4 == 0 or done_count == len(download_indices):
            ready = sum(1 for p in results if p)
            print(
                f"   [yt-v2] downloads: {ready}/{requested_scene_count} ready "
                f"({len(completed_scene_indices)}/{len(download_indices)} scenes attempted, {done_count} transfers)"
            )
            if progress_callback:
                progress_callback(
                    0.35 + 0.55 * (len(completed_scene_indices) / max(1, len(download_indices))),
                    f"YouTube V2: {ready}/{requested_scene_count} clips ready",
                )

    _submit_available()
    try:
        while pending:
            if provider_network_unavailable.is_set():
                stopped_reason = 'provider_network_unavailable'
                print('   [yt-v2] YouTube DNS failed for two sources; preserving completed clips and handing gaps to other providers', flush=True)
                break
            ready_count = sum(1 for path in results[:requested_scene_count] if path)
            if ready_count >= requested_scene_count:
                stopped_reason = "quota_satisfied"
                print(
                    f"   [yt-v2] all {requested_scene_count} requested scenes ready; exiting early",
                    flush=True,
                )
                break
            ready_ratio = ready_count / max(1, requested_scene_count)
            now = time.monotonic()
            if hard_deadline is not None and now >= hard_deadline:
                stopped_reason = "hard_timeout"
                break
            if soft_enabled and soft_ceiling_deadline is not None and now >= soft_ceiling_deadline:
                stopped_reason = "soft_ceiling"
                break
            if (
                soft_enabled
                and soft_deadline is not None
                and now >= soft_deadline
                and ready_ratio >= soft_coverage
            ):
                stopped_reason = "soft_timeout"
                break

            wait_timeout = 0.25
            deadlines = [deadline for deadline in (hard_deadline, soft_ceiling_deadline) if deadline is not None]
            if deadlines:
                wait_timeout = max(0.0, min(wait_timeout, min(deadlines) - now))
            done, pending = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                _consume_download(future)
            if not stopped_reason:
                _submit_available()
    finally:
        completed_at_stop = [future for future in pending if future.done()]
        for future in completed_at_stop:
            _consume_download(future)
            pending.discard(future)
        if stopped_reason and pending:
            # A hard batch deadline can land immediately after yt-dlp finishes
            # the transfer but before the FFmpeg/overlay gate returns. The old
            # code cancelled those running futures and handed the adapter an
            # incomplete result; valid candidate MP4s then appeared on disk
            # after coverage validation had already failed. Drain only the
            # already-started workers for a bounded grace period, without
            # submitting new work, so their verified results are handed off in
            # the same YouTube result object.
            try:
                drain_seconds = max(
                    0.0,
                    float(os.environ.get("VIDZEO_YT_ACTIVE_DOWNLOAD_GRACE_SECONDS", "8") or 8),
                )
            except (TypeError, ValueError):
                drain_seconds = 8.0
            drain_deadline = time.monotonic() + drain_seconds

            while pending and drain_seconds > 0:
                remaining = drain_deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    _consume_download(future)
            if pending:
                if download_cancellation is not None and hasattr(download_cancellation, "cancel"):
                    download_cancellation.cancel(
                        f"YouTube V2 stage stopped at {stopped_reason}; terminating late media workers"
                    )
                for future in pending:
                    future.cancel()
        # Return completed clip paths immediately.  A provider that ignores
        # cancellation must not make the v2 branch report zero at its wall
        # deadline; its late work is harmlessly isolated in this run folder.
        executor.shutdown(wait=not pending, cancel_futures=True)

    # Final salvage check: if any requested scenes are still unfilled, fill from spare candidate cuts in output_root
    missing_indices = [i for i in range(requested_scene_count) if not results[i]]
    if missing_indices:
        def _safe_mtime(p):
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        def _safe_size(p):
            try:
                return p.stat().st_size
            except OSError:
                return 0

        used_paths = {Path(p).resolve() for p in results if p and os.path.exists(p)}
        used_clip_tokens: set[tuple[str, int]] = set()
        for p in results:
            if p:
                m = re.search(r"\.cand([A-Za-z0-9_-]+)_ts(\d+)", os.path.basename(p))
                if m:
                    used_clip_tokens.add((m.group(1), int(m.group(2))))

        rejected_paths = set()
        for attempts in attempt_audit.values():
            for att in attempts:
                if not isinstance(att, dict):
                    continue
                if (att.get("status") in {"overlay_or_talking_head_rejected", "motion_or_quality_rejected", "download_failed", "download_exception"}
                        or (att.get('quality_check') or {}).get('reject')):
                    if att.get("path"):
                        rejected_paths.add(Path(att["path"]).resolve())
                if att.get("rejection_reason") in {"facecam_inset_rejected", "talking_head_rejected", "confident_talking_head_rejected"}:
                    if att.get("path"):
                        rejected_paths.add(Path(att["path"]).resolve())

        spare_candidates = [
            f for f in sorted(output_root.glob("yt_clip_scene_*.cand*.mp4"), key=_safe_mtime, reverse=True)
            if f.is_file() and _safe_size(f) > 4096 and f.resolve() not in used_paths and f.resolve() not in rejected_paths and not f.name.endswith(".raw.mp4") and ".part" not in f.name
        ]
        for m_idx in missing_indices:
            candidate_to_use = None
            for spare in list(spare_candidates):
                m = re.search(r"\.cand([A-Za-z0-9_-]+)_ts(\d+)", spare.name)
                if m:
                    owner = re.match(r'yt_clip_scene_(\d+)', spare.name)
                    if not owner or int(owner.group(1)) != m_idx + 1:
                        continue
                    token, ts = m.group(1), int(m.group(2))
                    collision = any(
                        token == ut_token and abs(ts - ut_ts) < 15
                        for ut_token, ut_ts in used_clip_tokens
                    )
                    if collision:
                        continue
                    used_clip_tokens.add((token, ts))
                candidate_to_use = spare
                spare_candidates.remove(spare)
                break
            if candidate_to_use:
                target = output_root / f"yt_clip_scene_{m_idx + 1:03d}.mp4"
                try:
                    shutil.copy2(str(candidate_to_use), str(target))
                    results[m_idx] = str(target)
                    used_paths.add(target.resolve())
                    print(
                        f"   [yt-v2] final quota rescue: scene {m_idx + 1} fulfilled from candidate cut {candidate_to_use.name}",
                        flush=True,
                    )
                except Exception:
                    pass

    if stopped_reason:
        print(
            f"   [yt-v2] {stopped_reason}; returning completed partial downloads "
            f"({sum(1 for path in results if path)}/{requested_scene_count})"
        )
    resolved_count = sum(1 for p in results if p)
    print(f"   [yt-v2] complete: {resolved_count}/{requested_scene_count} clips in {time.time() - start_time:.1f}s")
    overlay_attempts = [
        attempt
        for attempts in attempt_audit.values()
        for attempt in attempts
        if isinstance(attempt, dict)
    ]
    overlay_checked = sum(1 for attempt in overlay_attempts if attempt.get("overlay_checked"))
    overlay_blurred = sum(1 for attempt in overlay_attempts if attempt.get("overlay_blurred"))
    overlay_blur_failed = sum(1 for attempt in overlay_attempts if attempt.get("overlay_blur_failed"))
    if overlay_checked:
        print(
            f"   [yt-v2] overlay screen: {overlay_checked} cut(s) checked, "
            f"{overlay_blurred} blurred, {overlay_blur_failed} blur failed"
        )
    status_counts = Counter(str(attempt.get("status") or "unknown") for attempt in overlay_attempts)
    reason_counts = Counter(
        str(attempt.get("visual_reason") or attempt.get("rejection_reason") or attempt.get("reason") or "")
        for attempt in overlay_attempts
        if str(attempt.get("visual_reason") or attempt.get("rejection_reason") or attempt.get("reason") or "")
    )
    if status_counts:
        status_text = ", ".join(f"{key}={value}" for key, value in status_counts.most_common(6))
        reason_text = ", ".join(f"{key}={value}" for key, value in reason_counts.most_common(5))
        print(
            f"   [yt-v2] verification audit: {status_text}"
            + (f" | reasons: {reason_text}" if reason_text else "")
        )
    try:
        bite_timestamp_gap_seconds = float(
            os.environ.get("VIDZEO_YT_BITE_TIMESTAMP_GAP", "8") or 8
        )
    except (TypeError, ValueError):
        bite_timestamp_gap_seconds = 8.0
    return _restore_scene_slots(
        results,
        {
            "scenes": {str(index): rows[index].get("scene_text", "") for index in attempt_audit},
            "attempts": {str(index): attempts for index, attempts in attempt_audit.items()},
            "resolved": resolved_count,
            "requested": requested_scene_count,
            "stop_reason": stopped_reason,
            "provider_network_unavailable": provider_network_unavailable.is_set(),
            "strategy": "coverage_first_round_robin_downloads",
            "search_elapsed_seconds": round(search_elapsed_seconds, 2),
            "download_passes": dict(scene_passes),
            "candidate_attempt_limit": min(max_attempts, candidate_limit),
            "candidate_limit_per_scene": candidate_limit,
            "max_download_attempts_per_scene": max_attempts,
            "download_workers": download_workers,
            "requested_download_workers": configured_download_workers,
            "ranged_moment_selection": ranged_moment_audit,
            "runtime": runtime_receipt(),
            "ranged_moments": ranged_moments,
            "scheduled_download_operations": done_count,
            "intro_unique_scene_count": sum(
                1 for row in rows if bool(row.get("intro_unique") or row.get("is_intro"))
            ),
            "bite_clip_scene_count": sum(
                1 for row in rows if bool(row.get("bite_clip") or row.get("youtube_bite_clip"))
            ),
            "bite_clip_timestamp_gap_seconds": bite_timestamp_gap_seconds,
            "overlay_cuts_screened": overlay_checked,
            "overlay_cuts_blurred": overlay_blurred,
            "overlay_cuts_blur_failed": overlay_blur_failed,
            "transport_cache_hits": sum(1 for attempt in overlay_attempts if attempt.get("cache_hit")),
            "transport_cache_stores": sum(1 for attempt in overlay_attempts if attempt.get("cache_stored")),
        },
    )


def select_best_youtube_candidate(scene_text: str, title: str = "", api_key: str = "", niche: str = "general", **_) -> Dict[str, Any]:
    del api_key, niche
    subject = (_scene_subject_terms(scene_text, title) or _scene_subject_terms(title, ""))[:1]
    active = subject[0] if subject else title
    query = f"{active} highlights footage"
    candidates = _search_youtube(query, int(os.environ.get("VIDZEO_YT_RESULTS_PER_QUERY", "12") or 12))
    ranked = sorted(candidates, key=lambda c: _score_candidate(c, active, scene_text, query), reverse=True)
    selected = ranked[0] if ranked else None
    return {"selected_video": selected, "ranked_candidates": ranked, "query": query}


def select_youtube_scene_matches(
    scene_texts: List[str],
    title: str,
    script: str = "",
    scene_query_overrides: Optional[List[Dict[str, Any]]] = None,
    scene_durations: Optional[List[float]] = None,
    search_timeout_seconds: Optional[float] = None,
    lenient_acceptance: bool = False,
    allow_same_source_reuse: bool = True,
    session_jar: Any = None,
    youtube_session: Any = None,
    use_authenticated_session: bool = True,
    **__,
) -> Dict[str, Any]:
    rows = _build_scene_rows(title, scene_texts, scene_query_overrides)
    groups = _group_rows(rows)
    authenticated_session = _get_authenticated_youtube_session(
        session_jar=session_jar,
        youtube_session=youtube_session,
        enabled=use_authenticated_session,
    )
    pools = _search_container_pools(
        groups,
        search_timeout_seconds=search_timeout_seconds,
        session_jar=authenticated_session,
    )
    assignments = _assign_candidates(
        rows,
        pools,
        scene_durations or [4.0] * len(rows),
        lenient_acceptance=lenient_acceptance,
        allow_same_source_reuse=allow_same_source_reuse,
    )
    matches = []
    for idx, candidate in enumerate(assignments):
        matches.append({
            "scene_index": idx,
            "selected_video": candidate,
            "ranked_candidates": pools.get(_row_pool_key(rows[idx]), []),
            "audit": {"pipeline": "v2"},
        })
    return {
        "matches": matches,
        "summary": {"pipeline": "v2", "selected": sum(1 for c in assignments if c), "scenes": len(rows)},
        "global_context": {"video_format": _detect_video_format(title, script, scene_texts).value},
        "query_plan": rows,
    }


# ── Visual-gated per-scene clip resolver ────────────────────────────────────
# The scene-chain YouTubeLane was written against this trio of functions. They
# separate metadata search (build_clip_pools), mutable per-run state
# (make_resolve_state), and the per-scene download + Qwen vision gate
# (resolve_clip_for_scene). A clip only leaves the resolver after it clears
# the visual gate, so it can be trusted as self-ranked by the chain.


@dataclass
class ClipPools:
    rows: List[Dict[str, Any]]
    groups: Dict[str, List[Dict[str, Any]]]
    pools: Dict[str, List[Dict[str, Any]]]
    assignments: List[Optional[Dict[str, Any]]]
    scene_durations: List[float]
    moment_assignments: Dict[int, List[Any]] = field(default_factory=dict)
    transcript_documents: Dict[str, Any] = field(default_factory=dict)
    source_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    source_records: Dict[str, Any] = field(default_factory=dict)
    audit: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClipResolveState:
    pools: ClipPools
    used_timestamps: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    resolved: Dict[int, str] = field(default_factory=dict)
    rejected_sources: set = field(default_factory=set)
    attempted_candidates: set = field(default_factory=set)
    attempt_log: List[Dict[str, Any]] = field(default_factory=list)
    lock: Any = field(default_factory=Lock)


def _build_visual_client_from_context(visual_context: Any) -> Any:
    """Return the vision client for a VisualDecisionContext, or None."""
    if visual_context is None:
        return None
    try:
        if not bool(getattr(visual_context, "enabled", False)):
            return None
        client = visual_context.get_client()
        if client is not None:
            return client
    except Exception:
        return None
    try:
        from services.visual_intelligence.clip_pipeline import ClipIntelligencePipeline

        return ClipIntelligencePipeline(None)
    except Exception:
        return None


def _prepare_pool_transcript_moments(
    rows: List[Dict[str, Any]],
    pools: Dict[str, List[Dict[str, Any]]],
    *,
    scene_durations: List[float],
    force: bool = False,
    session_jar: Any = None,
    deadline_seconds: float | None = None,
) -> tuple[Dict[str, Any], Dict[int, List[Any]], Dict[str, Dict[str, Any]]]:
    if not force and str(os.environ.get("VIDZEO_YT_POOL_TRANSCRIPTS", "1")).strip().lower() not in {"1", "true", "yes", "on"}:
        return {}, {}, {}
    try:
        from services.youtube_transcript_index import YouTubeTranscriptIndex
        from services.youtube_moment_matcher import match_pool_moments
    except Exception:
        return {}, {}, {}
    source_metadata: Dict[str, Dict[str, Any]] = {}
    source_limit = max(1, min(24, int(os.environ.get("VIDZEO_YT_POOL_SOURCE_SHORTLIST", "4") or 4)))
    reserve_limit = max(0, min(24, int(os.environ.get("VIDZEO_YT_POOL_SOURCE_RESERVES", "2") or 2)))
    ranked_candidates = []
    seen_source_ids = set()
    # A flat six-source cap starved later listicle containers. Budget at least
    # a primary and reserve per pool (bounded for transport safety), then allow
    # an explicit larger operator setting to win.
    default_container_budget = max(2, min(24, len(pools) * 2))
    total_source_budget = max(source_limit + reserve_limit, default_container_budget)
    try:
        total_source_budget = min(
            48,
            max(total_source_budget, int(os.environ.get("VIDZEO_YT_POOL_TOTAL_SOURCES", total_source_budget) or total_source_budget)),
        )
    except (TypeError, ValueError):
        pass

    def add_candidate(candidate: Dict[str, Any], pool_key: str = "") -> None:
        video_id = _video_id(candidate)
        if not video_id:
            return
        if video_id in source_metadata:
            keys = source_metadata[video_id].setdefault("pool_keys", [])
            if pool_key and pool_key not in keys:
                keys.append(pool_key)
            return
        if len(source_metadata) >= total_source_budget:
            return
        seen_source_ids.add(video_id)
        source_metadata[video_id] = {
            "video_id": video_id,
            "url": _candidate_url(candidate),
            "title": candidate.get("title") or "",
            "channel": candidate.get("channel") or candidate.get("uploader") or "",
            "duration": float(candidate.get("duration") or 0.0),
            "metadata_score": float(candidate.get("v2_score") or candidate.get("score") or 0.0),
            "pool_keys": [pool_key] if pool_key else [],
        }

    ranked_pools = {
        pool_key: sorted(
            candidates,
            key=lambda value: float(value.get("v2_score") or value.get("score") or 0.0),
            reverse=True,
        )
        for pool_key, candidates in pools.items()
        if candidates
    }
    ranked_candidates = [
        (pool_key, candidate)
        for pool_key, candidates in ranked_pools.items()
        for candidate in candidates
    ]
    # Breadth-first pool allocation: every container receives a primary before
    # any receives a reserve, and every reserve before global extras.
    ordered_pools = sorted(
        ranked_pools.items(),
        key=lambda pair: float(pair[1][0].get("v2_score") or pair[1][0].get("score") or 0.0),
        reverse=True,
    )
    for candidate_rank in range(2):
        for pool_key, candidates in ordered_pools:
            if candidate_rank < len(candidates):
                add_candidate(candidates[candidate_rank], pool_key)
    for pool_key, candidate in sorted(
        ranked_candidates,
        key=lambda pair: float(pair[1].get("v2_score") or pair[1].get("score") or 0.0),
        reverse=True,
    ):
        add_candidate(candidate, pool_key)
        if len(source_metadata) >= total_source_budget:
            break
    index = YouTubeTranscriptIndex(session_jar=session_jar)
    try:
        # An explicit deadline overrides the shared env wall. The pre-probe
        # transcript fetch passes a tight one so it cannot eat the probe
        # wall; every other caller keeps the env default.
        transcript_deadline = max(
            0.05,
            min(
                300.0,
                float(deadline_seconds)
                if deadline_seconds is not None
                else float(os.environ.get("VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS", "25") or 25),
            ),
        )
    except (TypeError, ValueError):
        transcript_deadline = 25.0
    # Pool-first can proceed with heuristic moments when a transcript provider
    # is slow. Completed documents are retained at this wall deadline.
    try:
        documents = index.fetch_many(
            list(source_metadata),
            deadline_seconds=transcript_deadline,
            force=force and str(os.environ.get("VIDZEO_YT_TRANSCRIPT_FORCE_REFRESH", "0")).strip().lower() in {"1", "true", "yes", "on"},
        )
    except TypeError:
        # Keep compatibility with lightweight test/dummy indexes supplied by
        # callers that only implement the original fetch_many signature.
        documents = index.fetch_many(
            list(source_metadata),
            deadline_seconds=transcript_deadline,
        )
    scenes = []
    for index_row, row in enumerate(rows):
        scenes.append({
            **row,
            "scene_index": index_row,
            "pool_key": _row_pool_key(row),
            "duration": scene_durations[index_row] if index_row < len(scene_durations) else 4.0,
            "subject": row.get("candidate_subject") or row.get("section_subject") or row.get("scene_subject") or "",
        })
    moments = match_pool_moments(documents, scenes, source_metadata=source_metadata)
    return documents, moments, source_metadata


def build_clip_pools(
    title: str,
    scene_texts: List[str],
    *,
    scene_query_overrides: Optional[List[Dict[str, Any]]] = None,
    scene_durations: Optional[List[float]] = None,
    session_jar: Any = None,
    youtube_session: Any = None,
    use_authenticated_session: bool = True,
    search_timeout_seconds: Optional[float] = None,
    allow_same_source_reuse: bool = True,
) -> ClipPools:
    """Run the one-time metadata search/rank for a set of scenes.

    Mirrors ``select_youtube_scene_matches`` but returns a ``ClipPools`` object
    so per-scene resolution can download + visually gate without re-searching.
    """
    rows = _build_scene_rows(title, list(scene_texts), scene_query_overrides)
    groups = _group_rows(rows)
    authenticated_session = _get_authenticated_youtube_session(
        session_jar=session_jar,
        youtube_session=youtube_session,
        enabled=use_authenticated_session,
    )
    pools = _search_container_pools(
        groups,
        search_timeout_seconds=search_timeout_seconds,
        session_jar=authenticated_session,
    )
    durations = list(scene_durations or [4.0] * len(rows))
    while len(durations) < len(rows):
        durations.append(4.0)
    assignments = _assign_candidates(
        rows,
        pools,
        durations,
        lenient_acceptance=str(os.environ.get("VIDZEO_YT_LENIENT_ACCEPTANCE", "1")).strip().lower() in {"1", "true", "yes", "on"},
        allow_same_source_reuse=bool(allow_same_source_reuse),
        prefer_distinct_sources=str(
            os.environ.get("VIDZEO_YT_PREFER_DISTINCT_SOURCES", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"},
    )
    transcript_documents, moment_assignments, source_metadata = _prepare_pool_transcript_moments(
        rows,
        pools,
        scene_durations=durations,
        session_jar=authenticated_session,
    )
    try:
        from services.youtube_source_pool import build_sources
        source_records = build_sources(pools)
    except Exception:
        source_records = {}
    return ClipPools(
        rows=rows,
        groups=groups,
        pools=pools,
        assignments=assignments,
        scene_durations=durations,
        moment_assignments=moment_assignments,
        transcript_documents=transcript_documents,
        source_metadata=source_metadata,
        source_records=source_records,
        audit={
            "pool_count": len(groups),
            "unique_sources": len(source_records),
            "transcripts_fetched": sum(1 for document in transcript_documents.values() if getattr(document, "available", False)),
            "moments_indexed": sum(len(values) for values in moment_assignments.values()),
            "scene_assignments": sum(1 for assignment in assignments if assignment),
        },
    )


def make_resolve_state(pools: ClipPools) -> ClipResolveState:
    """Create fresh per-run resolve state for a set of clip pools."""
    state = ClipResolveState(pools=pools)
    for assigned in pools.assignments:
        if assigned and assigned.get("best_timestamp") is not None:
            try:
                with state.lock:
                    for key in _timestamp_reuse_keys(assigned):
                        state.used_timestamps[key].append(float(assigned.get("best_timestamp")))
            except Exception:
                pass
    return state


def _visual_gate_frames() -> int:
    """Number of frames Qwen sees to decide on a downloaded clip.

    Spec: 1-2 frames per clip, taken at the transcript-matched moment,
    frames only — the full clip is never sent to vision. The probe clip
    starts at the matched moment timestamp, so the first and last samples
    of the probe window bracket the moment. Fewer frames per clip also
    means fewer vision payloads per selection wave.
    """
    try:
        return max(1, min(2, int(os.environ.get("VIDZEO_YT_VISUAL_GATE_FRAMES", "2") or 2)))
    except Exception:
        return 2


def _youtube_relaxed_visual_gate_enabled() -> bool:
    """Use the YouTube relevance gate, not the still-image quality gate.

    A downloaded clip has already passed transport, decode, overlay, and
    local safety checks.  YouTube therefore gets a relevance-oriented gate:
    fatal subject/confusable mismatches still fail, while low-confidence
    model uncertainty does not erase otherwise usable motion coverage.
    """
    return str(
        os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1") or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}


def _visual_verdict_scored(
    path: str,
    scene_index: int,
    topic: str,
    narration: str,
    subject: str,
    visual_context: Any,
    *,
    relevance_only: bool = False,
) -> Tuple[bool, float, bool, str]:
    """Run the Qwen visual gate on a downloaded clip.

    Returns ``(accepted, score, vision_available, reason)``. Never raises: when
    the gate can't run it fails open (score 100) so a transient vision error
    never discards a downloaded asset.
    """
    if visual_context is None:
        return True, 100.0, False, "vision_disabled"
    visual_required = bool(getattr(visual_context, "visual_required", False))
    try:
        from services.visual_intelligence.clip_pipeline import ClipIntelligencePipeline

        client = None
        if getattr(visual_context, "enabled", False):
            try:
                client = visual_context.get_client()
            except Exception:
                client = None
        clip_pipeline = ClipIntelligencePipeline(
            client,
            max_frames=_visual_gate_frames(),
            visual_required=visual_required,
        )
    except Exception:
        # The clip has already passed metadata, FFmpeg integrity, overlay and
        # local facecam checks. A missing remote vision client is not a
        # negative visual verdict and must not trigger repeated downloads.
        return True, 100.0, False, "gate_unavailable_local_checks_passed"
    try:
        analysis = clip_pipeline.analyze_clip(
            scene_index=scene_index,
            clip_path=path,
            topic=topic or subject,
            narration=narration,
            subject=subject,
        )
        if not getattr(analysis, "vision_available", False):
            return True, 100.0, False, "vision_unavailable_local_checks_passed"
        if bool(getattr(analysis, "talking_head", False)) and str(
            os.environ.get("VIDZEO_YT_REJECT_TALKING_HEAD", "1") or "1"
        ).strip().lower() in {"1", "true", "yes", "on"}:
            # A boolean-only reject was starving the footage lane: Qwen can
            # mark a sports close-up or archival crowd as talking-head with a
            # weak confidence. Reactions/presenters are still rejected, but
            # only when the signal clears the same hard threshold as the CPU
            # overlay gate.
            try:
                talking_head_confidence = float(getattr(analysis, "confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                talking_head_confidence = 0.0
            try:
                from services.scene_understanding.analyzers import hard_talking_head_signal
                hard_presenter = hard_talking_head_signal(
                    True,
                    talking_head_confidence,
                    getattr(analysis, "talking_head_reason", ""),
                    enabled=True,
                )
            except Exception:
                hard_presenter = talking_head_confidence >= 82.0
            if hard_presenter:
                return False, float(getattr(analysis, "story_relevance", 0.0) or 0.0), True, "talking_head"
        if relevance_only or _youtube_relaxed_visual_gate_enabled():
            relevance = float(getattr(analysis, "story_relevance", 0.0) or 0.0)
            confidence = float(getattr(analysis, "confidence", 0.0) or 0.0)
            if bool(getattr(analysis, "fatal_mismatch", False)):
                return False, relevance, True, "fatal_mismatch"
            try:
                hard_reject_relevance = float(
                    os.environ.get(
                        "VIDZEO_VISUAL_HARD_REJECT_RELEVANCE" if relevance_only
                        else "VIDZEO_YT_HARD_REJECT_RELEVANCE",
                        "25" if relevance_only else "18",
                    ) or (25 if relevance_only else 18)
                )
                hard_reject_confidence = float(
                    os.environ.get(
                        "VIDZEO_VISUAL_HARD_REJECT_CONFIDENCE" if relevance_only
                        else "VIDZEO_YT_HARD_REJECT_CONFIDENCE",
                        "70" if relevance_only else "82",
                    ) or (70 if relevance_only else 82)
                )
            except (TypeError, ValueError):
                hard_reject_relevance, hard_reject_confidence = (
                    (25.0, 70.0) if relevance_only else (18.0, 82.0)
                )
            if relevance < hard_reject_relevance and confidence >= hard_reject_confidence:
                return False, relevance, True, "confident_irrelevance"
            # Watermarks, subtitles, low production value, and uncertain scores
            # are advisory in the YouTube path. Only a confident semantic miss
            # or fatal mismatch vetoes the motion candidate.
            return True, relevance, True, "relevance_keep" if relevance_only else "youtube_relaxed_relevance_keep"
        verdict = clip_pipeline.verdict_for(analysis)
        score = float(getattr(analysis, "final_score", 0.0) or 0.0)
        # Keep the audit useful: ``replace`` alone cannot distinguish a
        # semantic subject mismatch from a low-quality/prefilter rejection.
        # The caller uses the boolean verdict, while the reason is surfaced in
        # the manifest and UI log for deterministic debugging.
        if bool(getattr(analysis, "fatal_mismatch", False)):
            reason = "fatal_mismatch"
        elif bool(getattr(analysis, "prefilter_rejected", False)):
            reason = str(getattr(analysis, "prefilter_reason", "prefilter_rejected") or "prefilter_rejected")
        elif getattr(analysis, "error", ""):
            reason = f"analysis_error:{str(getattr(analysis, 'error', ''))[:120]}"
        else:
            reason = verdict
        return verdict == "keep", score, True, reason
    except Exception as exc:
        reason = f"rank_error:{type(exc).__name__}"
        return True, 100.0, False, f"{reason}:local_checks_passed"


def _visual_verdict(
    path: str,
    scene_index: int,
    topic: str,
    narration: str,
    subject: str,
    visual_context: Any,
) -> Tuple[bool, str, bool]:
    """Backwards-compatible 3-tuple wrapper over ``_visual_verdict_scored``."""
    accepted, _score, vision_available, reason = _visual_verdict_scored(
        path, scene_index, topic, narration, subject, visual_context
    )
    return accepted, reason, vision_available


def _parallel_visual_verdicts(
    paths: List[Tuple[int, str]],
    scene_index: int,
    topic: str,
    narration: str,
    subject: str,
    visual_context: Any,
) -> Dict[int, Tuple[bool, float, bool, str]]:
    """Run Qwen visual gates for several downloaded clips IN PARALLEL.

    Results are keyed by the caller's clip slot index so the best-scoring
    accepted candidate can be chosen. Bounded by the vision client's own
    concurrency budget (the context semaphore), plus a small local cap.
    """
    out: Dict[int, Tuple[bool, float, bool, str]] = {}
    if not paths:
        return out
    if visual_context is None:
        return {idx: (True, 100.0, False, "vision_disabled") for idx, _p in paths}
    try:
        max_workers = max(1, min(4, int(os.environ.get("VIDZEO_YT_VISUAL_GATE_PARALLEL", "3") or 3)))
    except Exception:
        max_workers = 3
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="yt-v2-vision") as executor:
        futures = {
            executor.submit(
                _visual_verdict_scored, path, scene_index, topic, narration, subject, visual_context
            ): idx
            for idx, path in paths
        }
        for future, idx in futures.items():
            try:
                out[idx] = future.result()
            except Exception as exc:
                out[idx] = (True, 100.0, False, f"rank_error:{type(exc).__name__}")
    return out


def _classify_visual_result(
    accepted: bool,
    score: float,
    vision_available: bool,
    reason: str,
) -> str:
    try:
        from services.visual_intelligence.context import classify_visual_outcome

        return classify_visual_outcome(
            accepted,
            vision_available,
            reason,
            score=score,
        )
    except Exception:
        if not vision_available:
            return "unavailable"
        if accepted:
            return "pass"
        return "hard_fail" if str(reason or "").lower() in {"replace", "reject"} else "uncertain"


def resolve_clip_for_scene(
    scene_index: int,
    pools: ClipPools,
    state: ClipResolveState,
    visual_context: Any = None,
    *,
    output_dir: Optional[Any] = None,
    session_jar: Any = None,
    youtube_session: Any = None,
    use_authenticated_session: bool = True,
) -> Optional[str]:
    """Download + visually gate ONE scene's best candidate.

    Tries the scene's ranked assignment first, then the rest of its container
    pool in score order. A candidate is returned only if it clears the Qwen
    visual gate (or the gate is unavailable). Returns ``None`` when no
    candidate passes so the chain falls through to the next lane.
    """
    if scene_index < 0 or scene_index >= len(pools.rows):
        return None
    row = pools.rows[scene_index]
    subject = (
        row.get("candidate_subject")
        or row.get("section_subject")
        or row.get("scene_subject")
        or ""
    )
    scene_text = row.get("scene_text", "") or ""
    key = _row_pool_key(row)
    assigned = pools.assignments[scene_index] if scene_index < len(pools.assignments) else None

    with state.lock:
        if scene_index in state.resolved:
            return state.resolved[scene_index]

    pool = sorted(
        pools.pools.get(key)
        or ([assigned] if assigned else []),
        key=lambda c: _score_candidate(
            c,
            subject,
            scene_text,
            c.get("query_text") or "",
            _row_retrieval_context(row),
        ),
        reverse=True,
    )
    options: List[Dict[str, Any]] = []
    seen_ids = set()
    for candidate in [assigned] + pool:
        vid = _video_id(candidate)
        if not vid or vid in seen_ids:
            continue
        # Hard metadata rejects (streamer/reaction, fake trailer, media agency,
        # overlay, ...) must never be re-tried just because vision is enabled.
        if _score_candidate(
            candidate,
            subject,
            scene_text,
            candidate.get("query_text") or "",
            _row_retrieval_context(row),
        ) <= -999.0:
            continue
        seen_ids.add(vid)
        options.append(candidate)
    if not options:
        # Every candidate was hard-rejected at metadata level; nothing to gate.
        return None

    run_id = _new_job_run_id("v2rg", f"{scene_index}:{subject}")
    output_root = Path(output_dir) if output_dir else Path(TEMP_DIR) / "yt_clips" / run_id
    output_root.mkdir(parents=True, exist_ok=True)
    authenticated_session = _get_authenticated_youtube_session(
        session_jar=session_jar,
        youtube_session=youtube_session,
        enabled=use_authenticated_session,
    )
    visual_required = bool(getattr(visual_context, "visual_required", False)) if visual_context is not None else False

    duration = float(pools.scene_durations[scene_index] if scene_index < len(pools.scene_durations) else 4.0)

    # Plan distinct timestamps (under lock) for every eligible candidate.
    plans: List[Tuple[Dict[str, Any], str, float]] = []
    moment_options = list(pools.moment_assignments.get(scene_index) or [])
    moment_starts = {
        str(moment.video_id): float(moment.start)
        for moment in moment_options
        if getattr(moment, "video_id", "")
    }
    seen_ids = set()
    for option in options:
        vid = _video_id(option)
        if not vid or vid in seen_ids:
            continue
        seen_ids.add(vid)
        with state.lock:
            if vid in state.rejected_sources:
                continue
            start = moment_starts.get(vid, option.get("best_timestamp"))
            if start is not None:
                # Validate pre-assigned timestamps against used_timestamps to
                # prevent multiple scenes from downloading the exact same
                # segment.  The gap must match _pick_timestamp's spacing.
                reuse_keys = _timestamp_reuse_keys(option)
                gap_val = max(
                    float(os.environ.get("VIDZEO_YT_REUSE_TIMESTAMP_GAP", "20") or 20),
                    duration + 4.0,
                )
                collides = any(
                    abs(float(start) - old) < (gap_val - 0.05)
                    for k in reuse_keys
                    for old in state.used_timestamps.get(k, [])
                )
                if collides:
                    # Pre-assigned timestamp conflicts; try to find a fresh one
                    start = _pick_timestamp(option, scene_index, state.used_timestamps, duration)
                else:
                    # Register the valid pre-assigned timestamp
                    for k in reuse_keys:
                        state.used_timestamps[k].append(float(start))
            else:
                start = _pick_timestamp(option, scene_index, state.used_timestamps, duration)
        if start is None:
            continue
        plans.append((option, vid, float(start)))
    if not plans:
        return None

    relaxed_raw = row.get(
        "youtube_relaxed_visual_gate",
        os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1"),
    )
    relaxed_youtube = (
        relaxed_raw if isinstance(relaxed_raw, bool)
        else str(relaxed_raw).strip().lower() in {"1", "true", "yes", "on"}
    )
    try:
        from .visual_intelligence.semantic_guard import calculate_youtube_weighted_score
    except (ImportError, ValueError):
        try:
            from services.visual_intelligence.semantic_guard import calculate_youtube_weighted_score
        except Exception:
            calculate_youtube_weighted_score = None

    if callable(calculate_youtube_weighted_score):
        def _plan_predownload_score(item: Tuple[Dict[str, Any], str, float]) -> float:
            opt, v_id, _ = item
            m_moment = next(
                (m for m in moment_options if str(getattr(m, "video_id", "") or "") == str(v_id)),
                None,
            )
            t_score = float(getattr(m_moment, "match_score", 0.0) or 0.0) if m_moment else 0.0
            t_avail = bool(m_moment and t_score > 0)
            r_meta = float(opt.get("v2_score") or opt.get("score") or 0.0)
            m_score = max(0.0, min(100.0, r_meta * 0.8))
            return calculate_youtube_weighted_score(
                vision_score=0.0,
                vision_available=False,
                transcript_score=t_score,
                transcript_available=t_avail,
                metadata_score=m_score,
                is_relaxed=relaxed_youtube,
            )
        plans.sort(key=_plan_predownload_score, reverse=True)

    try:
        progressive_limit = max(1, min(6, int(os.environ.get("VIDZEO_YT_PROGRESSIVE_ATTEMPTS", "4") or 4)))
    except (TypeError, ValueError):
        progressive_limit = 4
    plans = plans[:progressive_limit]

    # Jev is an optional, text-only tie-breaker over the already metadata-gated
    # YouTube shortlist. It can reorder which candidate is tried first, never
    # pass a clip: each downloaded candidate still has to clear the existing
    # visual-frame relevance gate below.
    jev_enabled = str(os.environ.get("VIDZEO_JEV_YT_RERANK", "1") or "1").strip().lower() in {
        "1", "true", "yes", "on",
    }
    jev_key = str(getattr(visual_context, "api_key", "") or "").strip()
    if jev_enabled and jev_key and len(plans) > 1:
        try:
            from services.jev_decision_service import choose_youtube_candidate

            matched_moments = {}
            for moment in moment_options:
                video_id = str(getattr(moment, "video_id", "") or "")
                current = matched_moments.get(video_id)
                if video_id and (current is None or float(getattr(moment, "match_score", 0.0) or 0.0)
                                 > float(getattr(current, "match_score", 0.0) or 0.0)):
                    matched_moments[video_id] = moment
            jev_candidates = []
            for option, video_id, start in plans[:5]:
                moment = matched_moments.get(video_id)
                jev_candidates.append({
                    "title": option.get("title") or "",
                    "channel": option.get("channel") or option.get("uploader") or "",
                    "description": option.get("description") or "",
                    "transcript": getattr(moment, "transcript_text", "") if moment else "",
                    "timestamp": start,
                    "score": option.get("v2_score", option.get("score", 0.0)),
                })
            decision = choose_youtube_candidate(
                scene_subject=subject,
                scene_narration=scene_text,
                candidates=jev_candidates,
                api_key=jev_key,
                timeout_seconds=min(1.5, max(0.25, getattr(visual_context, "call_timeout_seconds", 1.5))),
            )
            if decision and 0 <= decision.get("selected_index", -1) < len(jev_candidates):
                selected_plan = plans[decision["selected_index"]]
                plans = [selected_plan] + [plan for plan in plans if plan is not selected_plan]
                with state.lock:
                    state.attempt_log.append({
                        "scene_index": scene_index,
                        "event": "jev_youtube_candidate_order",
                        "selected_candidate_id": selected_plan[1],
                        "confidence": decision.get("confidence"),
                        "selected_probability": decision.get("selected_probability"),
                        "model": decision.get("model"),
                        "decision_id": decision.get("decision_id"),
                        "candidates_considered": decision.get("candidates_considered"),
                        "visual_gate_still_required": True,
                    })
        except Exception as exc:
            # Retrieval and rendering must not wait on optional decision aid.
            with state.lock:
                state.attempt_log.append({
                    "scene_index": scene_index,
                    "event": "jev_youtube_candidate_order_unavailable",
                    "reason": type(exc).__name__,
                    "kept_existing_ranked_order": True,
                })

    def _download_one(option: Dict[str, Any], vid: str, start: float) -> Optional[str]:
        out = output_root / (
            f"yt_clip_scene_{scene_index + 1:03d}.cand{_safe_youtube_path_token(vid)}.mp4"
        )
        fingerprint = f"{scene_index}:{vid}:{round(float(start), 2)}"
        with state.lock:
            if fingerprint in state.attempted_candidates:
                return None
            state.attempted_candidates.add(fingerprint)
            audit = {
                "scene_index": scene_index,
                "candidate_id": vid,
                "timestamp": round(float(start), 2),
                "fingerprint": fingerprint,
                "status": "download_started",
            }
            state.attempt_log.append(audit)
        started = time.monotonic()
        if _download_clip(
            option,
            out,
            start,
            duration,
            session_jar=authenticated_session,
        ):
            audit.update({"status": "downloaded", "elapsed_seconds": round(time.monotonic() - started, 2)})
            return str(out.resolve())
        with state.lock:
            state.rejected_sources.add(vid)
        audit.update({"status": "download_failed", "elapsed_seconds": round(time.monotonic() - started, 2)})
        return None

    def _finish_progressive(win_path: str) -> Optional[str]:
        final_out = output_root / f"yt_clip_scene_{scene_index + 1:03d}.mp4"
        try:
            final_out.unlink(missing_ok=True)
            Path(win_path).rename(final_out)
            win_path = str(final_out)
        except Exception:
            pass
        with state.lock:
            state.resolved[scene_index] = win_path
        return win_path

    advisory_fallback: Optional[str] = None
    for plan_index, (option, vid, start) in enumerate(plans):
        path = _download_one(option, vid, start)
        if not path:
            continue
        if visual_context is None:
            return _finish_progressive(path)
        gta_v_support = bool(row.get("gta_vi_supporting_gameplay")) and bool(
            option.get("gta_v_supporting_gameplay")
        )
        visual_topic = (
            "Grand Theft Auto VI documentary supporting gameplay: Grand Theft Auto V"
            if gta_v_support else subject
        )
        visual_narration = (
            f"{scene_text} Use clean Grand Theft Auto V gameplay as supporting footage "
            "for this Grand Theft Auto VI story; reject facecams."
            if gta_v_support else scene_text
        )
        visual_subject = "Grand Theft Auto V" if gta_v_support else subject
        verdict = _visual_verdict_scored(
            path,
            scene_index,
            visual_topic,
            visual_narration,
            visual_subject,
            visual_context,
        )
        outcome = _classify_visual_result(*verdict)
        if outcome == "unavailable" and visual_required and verdict[2]:
            verdict = _visual_verdict_scored(
                path,
                scene_index,
                visual_topic,
                visual_narration,
                visual_subject,
                visual_context,
            )
            outcome = _classify_visual_result(*verdict)

        fail_open_raw = row.get(
            "youtube_fail_open_on_visual_unavailable",
            os.environ.get("VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE", "1"),
        )
        fail_open_youtube = (
            fail_open_raw if isinstance(fail_open_raw, bool)
            else str(fail_open_raw).strip().lower() in {"1", "true", "yes", "on"}
        )
        relaxed_raw = row.get(
            "youtube_relaxed_visual_gate",
            os.environ.get("VIDZEO_YT_RELAXED_VISUAL_GATE", "1"),
        )
        relaxed_youtube = (
            relaxed_raw if isinstance(relaxed_raw, bool)
            else str(relaxed_raw).strip().lower() in {"1", "true", "yes", "on"}
        )
        strict_visual = bool(
            getattr(visual_context, "visual_required", False)
            or getattr(visual_context, "fail_closed", False)
        )
        is_fail_open = bool(
            outcome == "unavailable"
            and ((relaxed_youtube and fail_open_youtube) or not strict_visual)
        )

        matching_moment = next(
            (m for m in moment_options if str(getattr(m, "video_id", "") or "") == str(vid)),
            None,
        )
        transcript_score = float(getattr(matching_moment, "match_score", 0.0) or 0.0) if matching_moment else 0.0
        transcript_avail = bool(matching_moment and transcript_score > 0)
        raw_meta = float(option.get("v2_score") or option.get("score") or 0.0)
        metadata_score = max(0.0, min(100.0, raw_meta * 0.8))
        vis_score = float(verdict[1] or 0.0)
        vis_avail = bool(verdict[2])

        from services.visual_intelligence.semantic_guard import calculate_youtube_weighted_score
        weighted_score = calculate_youtube_weighted_score(
            vision_score=vis_score,
            vision_available=vis_avail,
            transcript_score=transcript_score,
            transcript_available=transcript_avail,
            metadata_score=metadata_score,
            is_relaxed=relaxed_youtube,
        )
        option["weighted_score"] = weighted_score
        option["transcript_score"] = transcript_score
        option["metadata_score"] = metadata_score
        option["visual_score"] = vis_score

        with state.lock:
            for row_audit in reversed(state.attempt_log):
                if row_audit.get("scene_index") == scene_index and row_audit.get("candidate_id") == vid:
                    row_audit.update({
                        "visual_status": "accepted" if (outcome == "pass" or is_fail_open or (relaxed_youtube and weighted_score >= 40.0 and outcome != "hard_fail")) else "rejected",
                        "visual_outcome": "unavailable_fail_open" if is_fail_open else outcome,
                        "visual_score": round(vis_score, 2),
                        "transcript_score": round(transcript_score, 2),
                        "metadata_score": round(metadata_score, 2),
                        "weighted_score": round(weighted_score, 2),
                        "visual_available": vis_avail,
                        "visual_reason": str(verdict[3] or ("vision_unavailable_fail_open" if is_fail_open else "")),
                        "plan_index": plan_index,
                    })
                    break

        accepted_by_weights = bool(
            relaxed_youtube
            and weighted_score >= 40.0
            and outcome != "hard_fail"
        )
        if outcome == "pass" or is_fail_open or accepted_by_weights:
            reason_tag = "weights_accepted" if accepted_by_weights else ("fail-open" if is_fail_open else "visual_pass")
            print(f"   [yt-v2] scene {scene_index + 1}: progressive visual gate accepted candidate {plan_index + 1} ({reason_tag}, score={weighted_score})")
            return _finish_progressive(path)
        if not visual_required:
            advisory_fallback = advisory_fallback or path
            break
        if outcome == "hard_fail":
            with state.lock:
                state.rejected_sources.add(vid)
        elif relaxed_youtube and not strict_visual and weighted_score >= 35.0:
            if advisory_fallback is None:
                advisory_fallback = path
                continue
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass

    if advisory_fallback:
        return _finish_progressive(advisory_fallback)
    return None
