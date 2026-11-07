"""Verified source-page capture for documentary evidence scenes.

Captures only a caller-provided public page and a caller-provided exact passage.
If the passage cannot be verified and highlighted, the renderer receives an
attributed source card with no unverified quotation or screenshot.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urljoin, urlsplit


_MAX_PAGE_BYTES = 6 * 1024 * 1024
_REDIRECT_CODES = {301, 302, 303, 307, 308}


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.text_parts = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if not self._skip_depth:
            self.text_parts.append(data)


def _validated_public_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source URL must be a public HTTP or HTTPS page.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Source URL has an invalid port.") from exc
    if port and port != (443 if parsed.scheme == "https" else 80):
        raise ValueError("Only standard HTTP/HTTPS source ports are allowed.")
    try:
        records = socket.getaddrinfo(parsed.hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Source domain could not be resolved.") from exc
    if not records:
        raise ValueError("Source domain has no public address.")
    for record in records:
        address = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValueError("Source URL resolves to a non-public network address.")
    return parsed.geturl()


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip().casefold()


def _fetch_public_html(url: str) -> tuple[str, str]:
    import requests

    current = _validated_public_url(url)
    headers = {"User-Agent": "VidzeoEvidenceCapture/1.0 (+local documentary editing)", "Accept": "text/html,application/xhtml+xml"}
    for _ in range(5):
        response = requests.get(current, headers=headers, timeout=(5, 12), allow_redirects=False, stream=True)
        try:
            if response.status_code in _REDIRECT_CODES:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Source page returned an invalid redirect.")
                current = _validated_public_url(urljoin(current, location))
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml")):
                raise ValueError("Source URL did not return an HTML article page.")
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                data.extend(chunk)
                if len(data) > _MAX_PAGE_BYTES:
                    raise ValueError("Source page is larger than the capture limit.")
            encoding = response.encoding or "utf-8"
            return data.decode(encoding, errors="replace"), current
        finally:
            response.close()
    raise ValueError("Source page redirected too many times.")


def capture_source_evidence(url: str, excerpt: str, output_dir: str | Path) -> Dict[str, Any]:
    url = _validated_public_url(url)
    excerpt = re.sub(r"\s+", " ", str(excerpt or "")).strip()
    if not excerpt or len(excerpt) > 500:
        raise ValueError("Passage must be between 1 and 500 characters.")

    parsed = urlsplit(url)
    domain = parsed.hostname or "source"
    filename = f"source-evidence-{uuid.uuid4().hex}.png"
    output_root = Path(output_dir).resolve()
    capture_root = output_root / "source-captures"
    capture_root.mkdir(parents=True, exist_ok=True)
    screenshot_path = capture_root / filename

    article_title = Path(parsed.path.rstrip("/")).name.replace("-", " ").replace("_", " ") or domain
    publication = domain
    final_url = url
    page_verified = False
    reason = "page_unavailable"
    try:
        document, final_url = _fetch_public_html(url)
        parser = _PageText()
        parser.feed(document)
        candidate_title = " ".join(parser.title_parts).strip()
        if candidate_title:
            article_title = candidate_title[:240]
        page_verified = bool(_normalize_text(excerpt) and _normalize_text(excerpt) in _normalize_text(" ".join(parser.text_parts)))
        reason = "exact_passage_not_found" if not page_verified else "browser_capture_unavailable"
    except Exception as exc:
        reason = f"page_unavailable:{type(exc).__name__}"

    browser_verified = False
    if page_verified:
        node = os.environ.get("VIDZEO_NODE_PATH") or shutil.which("node")
        script = Path(__file__).resolve().parents[2] / "scripts" / "capture-source-evidence.js"
        if node and script.is_file():
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
                    json.dump({"url": final_url, "excerpt": excerpt, "output_path": str(screenshot_path)}, handle)
                    temp_path = handle.name
                result = subprocess.run(
                    [node, str(script), temp_path],
                    capture_output=True,
                    text=True,
                    timeout=40,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    check=False,
                )
                if result.returncode == 0 and screenshot_path.is_file() and screenshot_path.stat().st_size > 10_000:
                    browser_payload = json.loads(result.stdout or "{}")
                    browser_verified = bool(browser_payload.get("success") and browser_payload.get("highlighted"))
                    article_title = str(browser_payload.get("title") or article_title)[:240]
                    publication = str(browser_payload.get("publication") or domain)[:120]
                    final_url = str(browser_payload.get("canonical") or browser_payload.get("final_url") or final_url)
                else:
                    screenshot_path.unlink(missing_ok=True)
                    reason = "browser_capture_failed"
            except Exception:
                screenshot_path.unlink(missing_ok=True)
                reason = "browser_capture_failed"
            finally:
                if temp_path:
                    Path(temp_path).unlink(missing_ok=True)

    quoted_text = excerpt if page_verified else ""
    has_image = browser_verified and screenshot_path.is_file()
    if has_image:
        status = "verified_capture"
        reason = ""
    elif page_verified:
        status = "verified_quote_card"
    else:
        status = "attributed_card"

    return {
        "status": status,
        "verification_status": "exact_passage_verified" if page_verified else "source_unverified",
        "reason": reason,
        "url": url,
        "canonical_url": final_url,
        "domain": domain,
        "publication": publication,
        "article_title": article_title,
        "exact_excerpt": quoted_text,
        "screenshot_path": str(screenshot_path) if has_image else "",
        "image_path": str(screenshot_path) if has_image else "",
        "image_url": f"/output/source-captures/{filename}" if has_image else "",
        "capture_method": "public_page_exact_text" if has_image else "designed_attributed_card",
    }


__all__ = ["capture_source_evidence"]
