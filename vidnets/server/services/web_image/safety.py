"""Provider-independent safety checks for downloaded web fallback images."""
from __future__ import annotations

import os
import hashlib
import re
import threading
import urllib.parse
from pathlib import Path
from typing import Dict, Tuple
from functools import lru_cache

from PIL import Image, ImageFilter, ImageStat


_EXPLICIT_PATTERNS = (
    r"\bnsfw\b",
    r"\b(?:porn|porno|pornography|xxx)\b",
    r"\b(?:nude|nudity|naked|topless|bottomless)\b",
    r"\b(?:explicit|erotic|erotica|hentai|fetish)\b",
    r"\b(?:onlyfans|camgirl|escort|playboy)\b",
    r"\b(?:sex|sexual|sextape)\b",
    r"\bwardrobe\s+malfunction\b",
)

# Metadata is a cheap, provider-independent rejection layer.  Keep suggestive
# signals separate from explicit signals so neutral historical terms such as
# "Bikini Atoll" do not become false positives while model/lingerie listings
# are still rejected.
_SUGGESTIVE_PATTERNS = (
    r"\b(?:lingerie|thong|boudoir|pin[-\s]?up)\b",
    r"\b(?:sexy|seductive|busty|cleavage)\b",
    r"\b(?:hot\s+girl|adult\s+model|swimsuit\s+model)\b",
    r"\b(?:bikini|swimsuit)\s+(?:model|girl|woman|photo|portrait|shoot)\b",
    r"\b(?:model|girl|woman)\s+(?:in|wearing)\s+(?:a\s+)?(?:bikini|lingerie|thong)\b",
)

# Search providers commonly label computer-generated material inconsistently
# ("CGI", "rendered", "AI art", etc.).  These patterns intentionally target
# image/art descriptors rather than the generic word "model".
_SYNTHETIC_PATTERNS = (
    r"\b(?:3d|3-d)[-\s]?(?:render|rendered|rendering|model|art|animation)\b",
    r"\b(?:render|rendered|rendering)\s+(?:image|photo|scene|art|model|visual|avatar)\b",
    r"\b(?:render|rendered|rendering)\b",
    r"\b(?:cgi|computer[-\s]?generated|synthetic|ai[-\s]?generated|generative\s+ai)\b",
    r"\b(?:digital\s+art|digitalart|concept\s+art|illustration|fan[-\s]?art)\b",
    r"\b(?:anime|cartoon|clipart|vector\s+art)\b",
    r"\b(?:second\s+life|secondlife|imvu|the\s+sims|simulation|avatar)\b",
)

_ADULT_DOMAIN_TOKENS = (
    "pornhub", "xvideos", "xnxx", "xhamster", "redtube", "youporn",
    "onlyfans", "fansly", "brazzers", "chaturbate", "rule34", "hentai",
)

_WATERMARK_DOMAIN_TOKENS = (
    "gettyimages", "shutterstock", "alamy", "istockphoto", "dreamstime",
    "depositphotos", "123rf",
)

_PLACEHOLDER_PATTERNS = (
    r"\bimage\s+(?:is\s+)?not\s+found\b",
    r"\bfile\s+(?:is\s+)?not\s+found\b",
    r"\bphoto\s+(?:is\s+)?not\s+found\b",
    r"\bimage\s+unavailable\b",
    r"\bpreview\s+unavailable\b",
    r"\bmissing\s+image\b",
    r"\bno\s+image\s+available\b",
    r"\b404(?:\s+error)?\b",
    r"\baccess\s+denied\b",
    r"\bhotlink(?:ing)?\s+(?:denied|blocked)\b",
    r"\bplaceholder(?:[_\s-]+image)?\b",
)

_PERSON_HINTS = re.compile(
    r"\b(?:girl|woman|women|female|model|actress|influencer|celebrity|lady|portrait|person)\b",
    re.IGNORECASE,
)

_CACHE_LOCK = threading.Lock()
_IMAGE_CACHE: Dict[Tuple[str, int, int, bool], Tuple[bool, str]] = {}
_CONTENT_CACHE: Dict[Tuple[str, bool, bool], Tuple[bool, str]] = {}


def metadata_safety_reason(**metadata) -> str:
    # Candidate metadata is reused across scenes. Cache only bounded immutable
    # inputs; never cache a semantic acceptance or a provider decision.
    names = ('title', 'url', 'tags', 'source', 'description', 'source_url', 'page_url', 'preview_url')
    unknown = set(metadata) - set(names)
    if unknown:
        raise TypeError('Unexpected safety metadata: ' + ', '.join(sorted(unknown)))
    values = tuple(str(metadata.get(name) or '') for name in names)
    if sum(map(len, values)) > 131072:
        return _metadata_safety_reason(**dict(zip(names, values)))
    return _cached_metadata_safety_reason(values)


@lru_cache(maxsize=512)
def _cached_metadata_safety_reason(values) -> str:
    names = ('title', 'url', 'tags', 'source', 'description', 'source_url', 'page_url', 'preview_url')
    return _metadata_safety_reason(**dict(zip(names, values)))


def _metadata_safety_reason(
    *,
    title: str = "",
    url: str = "",
    tags: str = "",
    source: str = "",
    description: str = "",
    source_url: str = "",
    page_url: str = "",
    preview_url: str = "",
) -> str:
    """Return a stable rejection reason for unsafe candidate metadata.

    The function is deliberately conservative for explicit/suggestive and
    synthetic descriptors, while allowing neutral terms such as ``bikini`` in
    isolation (for example, ``Bikini Atoll``).
    """
    url_values = [url, source_url, page_url, preview_url]
    decoded_urls = [urllib.parse.unquote_plus(str(value or "")) for value in url_values]
    text = " ".join(
        (
            str(title or ""),
            *decoded_urls,
            str(tags or ""),
            str(source or ""),
            str(description or ""),
        )
    ).lower()
    for pattern in _PLACEHOLDER_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return "placeholder_metadata"
    if any(token in text for token in _ADULT_DOMAIN_TOKENS):
        return "adult_domain"
    for pattern in _EXPLICIT_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return "adult_metadata"
    is_gaming_or_virtual = bool(re.search(
        r"\b(?:game|gameplay|gaming|videogame|video\s+game|gta|rockstar|playstation|xbox|nintendo|concept\s+art|steam|trailer|screenshot)\b",
        text,
        flags=re.IGNORECASE,
    ))
    # Exclude CDN infrastructure tokens (e.g. Cloudflare /cdn-cgi/) and URL path artifacts
    # from triggering synthetic metadata rejections on genuine photographs.
    url_blob_sanitized = re.sub(r"(?:cdn-cgi|/image-render/|/render/\d+)", "", " ".join(decoded_urls), flags=re.IGNORECASE)
    synthetic_eval_text = " ".join((str(title or ""), url_blob_sanitized, str(tags or ""), str(source or ""), str(description or ""))).lower()
    if not is_gaming_or_virtual:
        for pattern in _SYNTHETIC_PATTERNS:
            if re.search(pattern, synthetic_eval_text, flags=re.IGNORECASE):
                return "synthetic_metadata"
    for pattern in _SUGGESTIVE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return "suggestive_metadata"

    # Watermarking is provenance-sensitive: reject the publisher page/domain,
    # but do not reject an ordinary image CDN merely because its URL contains a
    # provider name in an opaque path.
    for candidate_url in (source_url, page_url):
        try:
            host = (urllib.parse.urlparse(str(candidate_url or "")).netloc or "").lower()
        except Exception:
            host = ""
        if host and any(token in host for token in _WATERMARK_DOMAIN_TOKENS):
            return "ownership_watermark_source"
    if re.search(r"\b(?:watermark|watermarked)\b", text, flags=re.IGNORECASE):
        return "ownership_watermark_source"
    return ""


_IMAGE_DECODE_LOCK = threading.Lock()


def _placeholder_card_reason(path: Path) -> str:
    """Detect provider error cards that are technically valid image files.

    Openverse and upstream catalog hosts occasionally return a large JPEG/PNG
    containing only a flat background plus ``image/file not found`` text.
    HTTP status, MIME type and dimensions all look valid, so this content gate
    identifies the characteristic flat-border + sparse high-contrast glyph
    layout without rejecting ordinary photographs or archival scans.
    """
    try:
        with _IMAGE_DECODE_LOCK:
            with Image.open(path) as source:
                source.verify()
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((192, 108), Image.Resampling.BILINEAR)

            width, height = image.size
            if width < 32 or height < 24:
                return "unreadable_image"

            # Quantization makes the metric stable for both PNG and JPEG cards.
            quantized = image.quantize(colors=32, method=Image.Quantize.MEDIANCUT)
            colors = quantized.getcolors(maxcolors=32) or []
            total = max(1, width * height)
            dominant_count = max((count for count, _index in colors), default=0)
            dominant_fraction = dominant_count / total

            gray = image.convert("L")
            entropy = float(gray.entropy())
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_pixels = list(edges.getdata())
            border = max(2, min(width, height) // 24)
            interior_edges = []
            border_values = []
            pixels = list(quantized.getdata())
            dominant_index = max(colors, default=(0, 0))[1]
            foreground = 0
            for y in range(height):
                row = y * width
                for x in range(width):
                    pos = row + x
                    if x < border or x >= width - border or y < border or y >= height - border:
                        border_values.append(pixels[pos])
                    else:
                        interior_edges.append(edge_pixels[pos])
                        if pixels[pos] != dominant_index:
                            foreground += 1

            border_background_fraction = (
                sum(1 for value in border_values if value == dominant_index)
                / max(1, len(border_values))
            )
            interior_count = max(1, (width - 2 * border) * (height - 2 * border))
            foreground_fraction = foreground / interior_count
            edge_fraction = sum(1 for value in interior_edges if value >= 42) / max(1, len(interior_edges))
            contrast = float(ImageStat.Stat(gray).stddev[0])

            if (
                dominant_fraction >= 0.70
                and border_background_fraction >= 0.82
                and 0.002 <= foreground_fraction <= 0.24
                and 0.001 <= edge_fraction <= 0.14
                and entropy <= 4.4
                and contrast >= 2.0
            ):
                return "placeholder_text_card"
            return ""
    except Exception:
        return "unreadable_image"


def candidate_is_safe(item: Dict) -> Tuple[bool, str]:
    reason = metadata_safety_reason(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or item.get("image_url") or ""),
        tags=str(item.get("tags") or item.get("categories") or ""),
        source=str(item.get("source") or item.get("domain") or ""),
        description=str(item.get("description") or ""),
        source_url=str(item.get("source_url") or ""),
        page_url=str(item.get("page_url") or ""),
        preview_url=str(item.get("preview_url") or item.get("thumbnail_url") or ""),
    )
    return (not reason, reason)


def _largest_component_ratio(mask: bytearray, width: int, height: int) -> float:
    """Measure the largest connected skin-colored region without OpenCV."""
    seen = bytearray(width * height)
    largest = 0
    for start, active in enumerate(mask):
        if not active or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        size = 0
        while stack:
            pos = stack.pop()
            size += 1
            x = pos % width
            y = pos // width
            if x and mask[pos - 1] and not seen[pos - 1]:
                seen[pos - 1] = 1
                stack.append(pos - 1)
            if x + 1 < width and mask[pos + 1] and not seen[pos + 1]:
                seen[pos + 1] = 1
                stack.append(pos + 1)
            if y and mask[pos - width] and not seen[pos - width]:
                seen[pos - width] = 1
                stack.append(pos - width)
            if y + 1 < height and mask[pos + width] and not seen[pos + width]:
                seen[pos + width] = 1
                stack.append(pos + width)
        largest = max(largest, size)
    return largest / max(width * height, 1)


def _exposure_risk(path: Path, metadata_text: str = "") -> Tuple[bool, str]:
    """Conservative high-exposure detector applied after provider SafeSearch."""
    try:
        with _IMAGE_DECODE_LOCK:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((112, 112), Image.Resampling.BILINEAR)
                width, height = image.size
                pixels = list(image.getdata())
    except Exception:
        return False, ""

    if width < 24 or height < 24:
        return False, ""

    pixels_rgb = list(image.getdata())
    # Guard against warm-toned archival photography (sepia/duotone scans from
    # e.g. 1912 news archives) which the skin-hue mask would otherwise treat
    # as a single giant skin-colored region. Those images are monotone warm
    # - compute chroma spread per pixel; if the whole frame is one warm hue
    # family with no distinct second hue, it is a tone-shift, not exposure.
    hue_bins = [0] * 24
    high_sat = 0
    low_sat = 0
    for red, green, blue in pixels:
        maximum = max(red, green, blue)
        minimum = min(red, green, blue)
        satur = (maximum - minimum) / 255.0 if maximum else 0.0
        if satur >= 0.55:
            high_sat += 1
        elif satur <= 0.12:
            low_sat += 1
        if satur < 0.08:
            continue
        # Hue in 15-degree bins (0-345).
        d = maximum - minimum
        if maximum == red:
            h = 60.0 * ((green - blue) / d % 6)
        elif maximum == green:
            h = 60.0 * ((blue - red) / d + 2)
        else:
            h = 60.0 * ((red - green) / d + 4)
        hue_bins[int(h) // 15 % 24] += 1
    total_px = max(len(pixels), 1)
    top_two = 0
    for count in sorted(hue_bins, reverse=True)[:2]:
        top_two += count
    struct_px = total_px - low_sat
    has_person_hint = bool(_PERSON_HINTS.search(metadata_text or ""))
    if not has_person_hint and struct_px > 0 and top_two / struct_px >= 0.94:
        # Monochrome warm sepia / duotone scan: skin-mask would cover the
        # whole frame, producing the "high exposure" false positive. These
        # are archival historical stills, not exposure imagery. Person-tagged
        # frames stay on the conservative path below.
        return False, ""

    mask = bytearray(width * height)
    skin_count = 0
    lower_skin = 0
    lower_total = max(width * (height - height // 2), 1)
    for pos, (red, green, blue) in enumerate(pixels):
        maximum = max(red, green, blue)
        minimum = min(red, green, blue)
        cb = 128 - 0.168736 * red - 0.331264 * green + 0.5 * blue
        cr = 128 + 0.5 * red - 0.418688 * green - 0.081312 * blue
        is_skin = (
            red > 70 and green > 25 and blue > 15
            and maximum - minimum > 12
            and red > green * 0.92 and red > blue
            and 76 <= cb <= 137 and 132 <= cr <= 183
        )
        if is_skin:
            mask[pos] = 1
            skin_count += 1
            if pos // width >= height // 2:
                lower_skin += 1

    total = max(width * height, 1)
    skin_ratio = skin_count / total
    lower_ratio = lower_skin / lower_total
    if skin_ratio < 0.30:
        return False, ""

    largest_ratio = _largest_component_ratio(mask, width, height)
    if skin_ratio >= 0.62 and largest_ratio >= 0.40:
        return True, "high_exposure_visual"
    if skin_ratio >= 0.48 and lower_ratio >= 0.42 and largest_ratio >= 0.34:
        return True, "high_exposure_visual"
    if has_person_hint and skin_ratio >= 0.42 and lower_ratio >= 0.36 and largest_ratio >= 0.30:
        return True, "suggestive_person_visual"
    return False, ""


def image_is_safe(
    path: str | Path,
    *,
    title: str = "",
    url: str = "",
    tags: str = "",
    source: str = "",
    description: str = "",
    source_url: str = "",
    page_url: str = "",
    preview_url: str = "",
) -> Tuple[bool, str]:
    """Apply metadata and local visual checks to a downloaded image."""
    metadata_reason = metadata_safety_reason(
        title=title,
        url=url,
        tags=tags,
        source=source,
        description=description,
        source_url=source_url,
        page_url=page_url,
        preview_url=preview_url,
    )
    if metadata_reason:
        return False, metadata_reason

    file_path = Path(path)
    metadata_text = f"{title} {url} {source_url} {page_url} {tags} {source} {description}"
    person_hint = bool(_PERSON_HINTS.search(metadata_text))
    disabled = str(os.environ.get("VIDZEO_WEB_DISABLE_VISUAL_SAFETY", "0")).strip().lower() in {"1", "true", "yes", "on"}
    try:
        stat = file_path.stat()
        cache_key = (
            str(file_path.resolve()).lower(),
            int(stat.st_mtime_ns),
            int(stat.st_size),
            person_hint,
            int(stat.st_ctime_ns),
            disabled,
        )
    except Exception:
        return False, "unreadable_image"

    with _CACHE_LOCK:
        cached = _IMAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Scene snapshots often copy identical source bytes to different paths.
    # Share pixel heuristics by digest, with person context and safety mode
    # included. Metadata above still gets checked for every candidate.
    try:
        digest = hashlib.sha256()
        with file_path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        content_key = (digest.hexdigest(), person_hint, disabled)
    except OSError:
        return False, 'unreadable_image'
    with _CACHE_LOCK:
        cached = _CONTENT_CACHE.get(content_key)
        if cached is not None:
            if len(_IMAGE_CACHE) >= 2048:
                _IMAGE_CACHE.clear()
            _IMAGE_CACHE[cache_key] = cached
            return cached

    placeholder_reason = _placeholder_card_reason(file_path)
    if placeholder_reason:
        # Provider failure cards are never valid footage and cannot be made
        # eligible by disabling the adult-content visual heuristic.
        result = (False, placeholder_reason)
    elif disabled:
        result = (True, "")
    else:
        unsafe, reason = _exposure_risk(file_path, metadata_text)
        result = (not unsafe, reason)

    with _CACHE_LOCK:
        if len(_IMAGE_CACHE) >= 2048:
            _IMAGE_CACHE.clear()
        _IMAGE_CACHE[cache_key] = result
        if len(_CONTENT_CACHE) >= 1024:
            _CONTENT_CACHE.clear()
        _CONTENT_CACHE[content_key] = result
    return result
