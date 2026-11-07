"""
Web image sourcing via direct live image search and direct-image downloads.
Delegated to the services.web_image sub-package.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

from services.web_image import (
    resolve_web_images_for_video_fast,
    reset_web_runtime_state,
    generate_web_image_queries,
    _generate_queries_mechanical,
    _extract_main_subject,
    _detect_royal_family_anchor,
    _derive_effective_web_title,
    _ensure_query_diversity,
    _resolve_gallery_dl_command,
)
