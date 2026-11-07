"""Small yt-dlp runtime adapter for the bundled desktop Python."""

from __future__ import annotations

import importlib
import os
import threading
import sys
from importlib import metadata


def runtime_receipt():
    """Non-sensitive dependency versions for the durable provider audit."""
    result = {'python': '.'.join(map(str, sys.version_info[:3]))}
    for package, key in (('yt-dlp', 'yt_dlp'), ('yt-dlp-ejs', 'ejs')):
        try:
            result[key] = metadata.version(package)
        except metadata.PackageNotFoundError:
            result[key] = 'missing'
    return result


class YouTubeRuntimeLog:
    """Retain diagnostic categories without logging signed URLs or cookies."""
    def __init__(self):
        self.categories = []

    def debug(self, _message):
        pass

    def warning(self, message):
        text = str(message).casefold()
        category = next((key for key, markers in (
            ('javascript_runtime', ('javascript runtime', 'js runtime')),
            ('challenge_solver', ('challenge', 'ejs')),
            ('authentication', ('sign in', 'cookies', 'authentication')),
            ('rate_limited', ('429', 'rate limit', 'too many requests')),
            ('format_unavailable', ('format', 'sabr', 'missing url')),
        ) if any(marker in text for marker in markers)), 'provider_warning')
        if category not in self.categories:
            self.categories.append(category)

    error = warning


_LOCK = threading.Lock()
_PREPARED_PACKAGE = None


def load_yt_dlp():
    """Import yt-dlp and silence only its repeated Python-version banner.

    The bundled Python 3.10 runtime remains supported by the installed yt-dlp
    build, but each short-lived ``YoutubeDL`` instance prints the same upgrade
    advisory. A long job creates many instances, so the banner obscures real
    transport errors. Other yt-dlp warnings remain untouched.
    """

    global _PREPARED_PACKAGE
    package = importlib.import_module("yt_dlp")
    if _PREPARED_PACKAGE is package:
        return package
    with _LOCK:
        if _PREPARED_PACKAGE is not package:
            suppress = str(
                os.environ.get("VIDZEO_SUPPRESS_YTDLP_PYTHON_DEPRECATION", "1")
            ).strip().lower() in {"1", "true", "yes", "on"}
            # Keep the adapter transparent for embedders/tests that provide a
            # module-compatible ``yt_dlp`` object without making it a package.
            # A real yt-dlp install exposes ``__path__`` and the implementation
            # module whose single deprecation helper we intentionally patch.
            if suppress and getattr(package, "__path__", None):
                youtube_dl_module = importlib.import_module("yt_dlp.YoutubeDL")
                youtube_dl_module._get_system_deprecation = lambda: None
            _PREPARED_PACKAGE = package
    return package
