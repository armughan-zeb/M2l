"""Authenticated, provenance-preserving Openverse image source.

Openverse permits both anonymous requests and OAuth2 client-credentials
requests. Credentials and bearer tokens stay inside this source instance;
candidate rows contain only public work/license provenance and are therefore
safe for the job discovery cache and Storyboard V2 ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import re
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

import requests

from .base_source import BaseSource, ImageCandidate


class OpenverseSource(BaseSource):
    name = "openverse"
    trust_score = 0.8
    tier = 1
    max_results_per_query = 30
    timeout = 10.0

    API_URL = "https://api.openverse.org/v1/images/"
    TOKEN_URL = "https://api.openverse.org/v1/auth_tokens/token/"

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        *,
        config: Optional[Dict[str, Any]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        settings = dict(config or {})
        secret_keys = {
            "client_id", "client_secret", "openverse_client_id",
            "openverse_client_secret", "access_token", "token",
        }
        super().__init__(config={
            key: value for key, value in settings.items()
            if str(key).lower() not in secret_keys
        })
        self._client_id = str(
            client_id
            or settings.get("client_id")
            or settings.get("openverse_client_id")
            or os.environ.get("VIDZEO_OPENVERSE_CLIENT_ID")
            or os.environ.get("OPENVERSE_CLIENT_ID")
            or ""
        ).strip()
        self._client_secret = str(
            client_secret
            or settings.get("client_secret")
            or settings.get("openverse_client_secret")
            or os.environ.get("VIDZEO_OPENVERSE_CLIENT_SECRET")
            or os.environ.get("OPENVERSE_CLIENT_SECRET")
            or ""
        ).strip()
        self.api_url = str(settings.get("api_url") or self.API_URL).strip()
        self.token_url = str(settings.get("token_url") or self.TOKEN_URL).strip()
        verify_setting = settings.get("verify_license_pages", False)
        self.verify_license_pages = (
            verify_setting
            if isinstance(verify_setting, bool)
            else str(verify_setting).strip().lower() in {"1", "true", "yes", "on"}
        )
        try:
            self.license_verification_limit = max(
                0,
                min(3, int(settings.get("license_verification_limit", 3) or 3)),
            )
        except (TypeError, ValueError):
            self.license_verification_limit = 3
        self.session = session if session is not None else self._create_session()

        # Bearer tokens are cached only in memory on this source instance.
        # Neither the client secret nor token is placed in session headers,
        # candidates, telemetry, or any process-global discovery cache.
        self._access_token = ""
        self._token_expires_at = 0.0
        self._auth_state = "anonymous"
        self._auth_retry_at = 0.0
        self._auth_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_request = 0.0

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "VidzeoLocal/2.0 (vidzeo.app; media pipeline)",
            "Accept": "application/json",
        })
        return session

    @staticmethod
    def _retry_after_seconds(response: Any, fallback: float) -> float:
        raw = ""
        try:
            raw = str(response.headers.get("Retry-After", "") or "").strip()
        except Exception:
            raw = ""
        if raw:
            try:
                return max(0.0, min(30.0, float(raw)))
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    return max(0.0, min(30.0, seconds))
                except (TypeError, ValueError, OverflowError):
                    pass
        return max(0.0, min(30.0, float(fallback)))

    def _rate_limit(self) -> None:
        # Protect the timestamp so concurrent scene workers cannot all pass the
        # same stale value and burst this provider at once.
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < 0.5:
                time.sleep(0.5 - elapsed)
            self._last_request = time.monotonic()

    def _credentials_state(self) -> str:
        if self._client_id and self._client_secret:
            return "configured"
        if self._client_id or self._client_secret:
            return "incomplete"
        return "anonymous"

    def _invalidate_token(self) -> None:
        with self._auth_lock:
            self._access_token = ""
            self._token_expires_at = 0.0
            self._auth_retry_at = 0.0

    def _get_access_token(self, *, force_refresh: bool = False) -> Tuple[str, str]:
        """Return ``(token, state)`` without exposing credentials externally.

        States are ``authenticated``, ``anonymous``, ``transient_failure``, or
        ``invalid_credentials``. Only transient token-service failure may fall
        back to the API's documented anonymous mode. Incomplete or rejected
        credentials fail closed so a deployment mistake is not hidden.
        """
        credentials_state = self._credentials_state()
        if credentials_state == "anonymous":
            self._auth_state = "anonymous"
            return "", "anonymous"
        if credentials_state == "incomplete":
            self._auth_state = "invalid_credentials"
            return "", "invalid_credentials"

        with self._auth_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._access_token
                and now < self._token_expires_at
            ):
                self._auth_state = "authenticated"
                return self._access_token, "authenticated"
            if (
                not force_refresh
                and self._auth_state in {"transient_failure", "invalid_credentials"}
                and now < self._auth_retry_at
            ):
                return "", self._auth_state

            self._access_token = ""
            self._token_expires_at = 0.0
            state = "transient_failure"
            for attempt in range(2):
                try:
                    response = self.session.post(
                        self.token_url,
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self._client_id,
                            "client_secret": self._client_secret,
                        },
                        headers={"Accept": "application/json"},
                        timeout=self.timeout,
                    )
                except requests.RequestException:
                    if attempt == 0:
                        time.sleep(0.25)
                        continue
                    break

                status = int(getattr(response, "status_code", 0) or 0)
                if status == 200:
                    try:
                        payload = response.json()
                    except (TypeError, ValueError):
                        payload = {}
                    token = str((payload or {}).get("access_token") or "").strip()
                    token_type = str((payload or {}).get("token_type") or "Bearer").strip().lower()
                    if token and token_type == "bearer":
                        try:
                            lifetime = max(1.0, float((payload or {}).get("expires_in") or 3600.0))
                        except (TypeError, ValueError):
                            lifetime = 3600.0
                        refresh_skew = min(60.0, max(1.0, lifetime * 0.10), lifetime * 0.50)
                        self._access_token = token
                        self._token_expires_at = time.monotonic() + max(1.0, lifetime - refresh_skew)
                        self._auth_state = "authenticated"
                        self._auth_retry_at = 0.0
                        return token, "authenticated"
                    state = "transient_failure"
                    break
                if status in {400, 401, 403}:
                    state = "invalid_credentials"
                    break
                if status in {429, 500, 502, 503, 504} and attempt == 0:
                    time.sleep(self._retry_after_seconds(response, 0.5))
                    continue
                state = "transient_failure" if status >= 500 or status in {0, 408, 429} else "invalid_credentials"
                break

            self._auth_state = state
            self._auth_retry_at = time.monotonic() + (
                300.0 if state == "invalid_credentials" else 15.0
            )
            return "", state

    def _search_response(self, params: Dict[str, Any]) -> Optional[Any]:
        token, auth_state = self._get_access_token()
        if auth_state == "invalid_credentials":
            return None
        # Anonymous fallback is documented by Openverse and is used only when
        # credentials are absent or the token service itself is transiently
        # unavailable. We never downgrade an authenticated 401/403/429.
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        refreshed_after_unauthorized = False
        for attempt in range(2):
            try:
                self._rate_limit()
                response = self.session.get(
                    self.api_url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException:
                if attempt == 0:
                    time.sleep(0.25)
                    continue
                return None

            status = int(getattr(response, "status_code", 0) or 0)
            if status == 200:
                self.record_success()
                return response
            if status == 401 and token and not refreshed_after_unauthorized:
                refreshed_after_unauthorized = True
                self._invalidate_token()
                token, refreshed_state = self._get_access_token(force_refresh=True)
                if refreshed_state != "authenticated" or not token:
                    self.record_failure()
                    return None
                headers = {"Authorization": f"Bearer {token}"}
                continue
            if status in {429, 503} and attempt == 0:
                time.sleep(self._retry_after_seconds(response, 0.5))
                continue
            self.record_failure()
            return None
        self.record_failure()
        return None

    def search(
        self,
        query: str,
        limit: int = 20,
        filters: Optional[Dict] = None,
    ) -> List[ImageCandidate]:
        filters = filters or {}
        clean = self._clean_query(query, max_words=8)
        if not clean or len(clean) < 2 or not self.is_available():
            return []

        params: Dict[str, Any] = {
            "q": clean,
            "page_size": min(max(1, int(limit or 20)), 100),
            "page": 1,
            "mature": "false",
        }
        license_filter = filters.get("license", "")
        if license_filter:
            params["license"] = license_filter
        source_filter = filters.get("source", "")
        if source_filter:
            params["source"] = source_filter
        aspect_ratio = filters.get("aspect_ratio", "")
        if aspect_ratio:
            params["aspect_ratio"] = aspect_ratio

        response = self._search_response(params)
        if response is None:
            return []
        try:
            payload = response.json()
        except (TypeError, ValueError):
            self.record_failure()
            return []
        results = payload.get("results", []) if isinstance(payload, Mapping) else []
        candidates: List[ImageCandidate] = []
        for item in results if isinstance(results, list) else []:
            if len(candidates) >= max(0, int(limit or 0)):
                break
            if not isinstance(item, Mapping) or item.get("mature") is True:
                continue
            candidate = self._item_to_candidate(dict(item))
            if candidate:
                candidate.query = clean
                if (
                    self.verify_license_pages
                    and len(candidates) < self.license_verification_limit
                ):
                    verified, evidence = self._verify_source_page_license(dict(item))
                    candidate.metadata["license_verified"] = bool(verified)
                    candidate.metadata["license_verification_evidence"] = evidence
                    candidate.metadata["license_verification_required"] = not bool(verified)
                    candidate.metadata["rights_status"] = (
                        "source_page_verified" if verified else "openverse_reported_unverified"
                    )
                candidates.append(candidate)
        return candidates

    def _verify_source_page_license(self, item: Mapping[str, Any]) -> Tuple[bool, str]:
        """Confirm Openverse's reported license on the independent origin page."""
        landing_url = str(item.get("foreign_landing_url") or "").strip()
        if not re.match(r"^https?://", landing_url, flags=re.IGNORECASE):
            return False, "missing_public_source_page"
        license_code = str(item.get("license") or "").strip().lower()
        license_version = str(item.get("license_version") or "").strip().lower()
        license_url = str(item.get("license_url") or "").strip().lower().rstrip("/")
        if not license_code and not license_url:
            return False, "missing_reported_license"

        response = None
        for attempt in range(2):
            try:
                self._rate_limit()
                response = self.session.get(
                    landing_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                    },
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            except requests.RequestException:
                response = None
            if response is None:
                if attempt == 0:
                    time.sleep(0.25)
                    continue
                return False, "source_page_transport_error"
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 200:
                break
            if status in {429, 503} and attempt == 0:
                time.sleep(self._retry_after_seconds(response, 0.5))
                continue
            return False, f"source_page_http_{status or 'error'}"
        if response is None or int(getattr(response, "status_code", 0) or 0) != 200:
            return False, "source_page_unavailable"

        page_text = str(getattr(response, "text", "") or "")[:1_000_000].lower()
        compact = re.sub(r"\s+", " ", page_text)
        needles = []
        if license_url:
            needles.extend({license_url, re.sub(r"^https?://", "", license_url)})
        if license_code:
            version_suffix = f"/{license_version}" if license_version else ""
            needles.extend({
                f"creativecommons.org/licenses/{license_code}{version_suffix}",
                f"cc {license_code.replace('-', ' ')}",
                f"cc {license_code}",
            })
        if any(needle and needle in compact for needle in needles):
            return True, "source_page_license_match"
        return False, "source_page_license_not_found"

    def _item_to_candidate(self, item: Dict) -> Optional[ImageCandidate]:
        url = str(item.get("url") or "").strip()
        if not url:
            return None

        preview_url = str(item.get("thumbnail") or "").strip()
        if not preview_url:
            thumbnails = item.get("thumbnails") or []
            if isinstance(thumbnails, list) and thumbnails and isinstance(thumbnails[0], Mapping):
                preview_url = str(thumbnails[0].get("url") or "")

        tags = []
        for tag in (item.get("tags") or [])[:5]:
            if isinstance(tag, Mapping):
                name = str(tag.get("name") or "").strip()
            else:
                name = str(tag or "").strip()
            if name:
                tags.append(name)

        work_id = str(item.get("id") or "")
        license_code = str(item.get("license") or "")
        try:
            width = max(0, int(item.get("width") or 0))
        except (TypeError, ValueError):
            width = 0
        try:
            height = max(0, int(item.get("height") or 0))
        except (TypeError, ValueError):
            height = 0
        return ImageCandidate(
            id=f"openverse_{work_id[:36]}",
            source="openverse",
            title=str(item.get("title") or "")[:200],
            description=str(item.get("description") or "")[:300],
            download_url=self._normalize_url(url),
            preview_url=self._normalize_url(preview_url or url),
            width=width,
            height=height,
            media_type="image",
            license=license_code,
            categories=tags,
            metadata={
                "openverse_id": work_id,
                "provider": str(item.get("provider") or ""),
                "catalog_source": str(item.get("source") or ""),
                "foreign_url": str(item.get("foreign_landing_url") or ""),
                "detail_url": str(item.get("detail_url") or ""),
                "creator": str(item.get("creator") or ""),
                "creator_url": str(item.get("creator_url") or ""),
                "license": license_code,
                "license_version": str(item.get("license_version") or ""),
                "license_url": str(item.get("license_url") or ""),
                "attribution": str(item.get("attribution") or ""),
                "rights_status": "openverse_reported_unverified",
                "license_verification_required": True,
                "filetype": str(item.get("filetype") or ""),
                "filesize": item.get("filesize"),
            },
        )


def search_openverse(query: str, limit: int = 20) -> List[Dict]:
    source = OpenverseSource()
    return [item.to_dict() for item in source.search(query, limit=limit)]


if __name__ == "__main__":
    source = OpenverseSource()
    results = source.search("historical railway station", limit=3)
    print(f"Found {len(results)} Openverse result(s)")
