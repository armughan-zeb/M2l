import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

import config  # noqa: E402  (ensures .env is loaded before constants below are read)


SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
VIDZEO_ADMIN_EMAILS = os.environ.get("VIDZEO_ADMIN_EMAILS", "").strip()
VIDZEO_PACKAGED = os.environ.get("VIDZEO_PACKAGED", "").strip().lower() in {"1", "true", "yes"}
VIDZEO_ALLOW_PACKAGED_SERVICE_ROLE = (
    os.environ.get("VIDZEO_ALLOW_PACKAGED_SERVICE_ROLE", "").strip().lower() in {"1", "true", "yes"}
)

REST_BASE = f"{SUPABASE_URL}/rest/v1" if SUPABASE_URL else ""
AUTH_BASE = f"{SUPABASE_URL}/auth/v1" if SUPABASE_URL else ""

ACCOUNT_STATUS_VALUES = {"pending", "active", "suspended"}
PLAN_ASSIGNMENT_STATUS_VALUES = {"pending", "active", "expired", "revoked"}
SCRIPT_WORDS_PER_MINUTE = 156
_admin_seed_attempted = False

# ---- Short-lived entitlement cache ------------------------------------------
# The renderer polls /api/queue/status every 2s and syncs entitlement, and every
# internal API call runs verify_access_token + get_access_state_via_rpc, which is
# 2+ synchronous HTTP round trips to Supabase per request. On a slow connection
# this blocks Flask worker threads and makes the UI stall. Caching the computed
# state for a few seconds collapses that fan-out without delaying plan changes
# beyond one poll interval.
_VERIFY_CACHE_TTL_SECONDS = float(os.environ.get('VIDZEO_AUTH_VERIFY_CACHE_SECONDS', '25') or 25)
_STATE_CACHE_TTL_SECONDS = float(os.environ.get('VIDZEO_AUTH_STATE_CACHE_SECONDS', '15') or 15)
_MAX_CACHE_ENTRIES = 64
_verify_cache = {}
_state_cache = {}
_cache_lock = threading.Lock()


def _cache_get(cache: Dict[str, Any], key: str, ttl: float) -> Optional[Any]:
    with _cache_lock:
        entry = cache.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            cache.pop(key, None)
            return None
        return value


def _cache_put(cache: Dict[str, Any], key: str, value: Any, ttl: float) -> None:
    with _cache_lock:
        if len(cache) >= _MAX_CACHE_ENTRIES:
            # Prune expired entries first, then drop the oldest insertion.
            now = time.monotonic()
            stale = [k for k, (exp, _v) in cache.items() if now > exp]
            for k in stale:
                cache.pop(k, None)
            if len(cache) >= _MAX_CACHE_ENTRIES:
                oldest = min(cache.keys(), key=lambda k: cache[k][0])
                cache.pop(oldest, None)
        cache[key] = (time.monotonic() + ttl, value)

DEFAULT_PUBLIC_PLANS = [
    {
        "plan_key": "base_monthly",
        "name": "Base",
        "display_name": "Base",
        "billing_period": "monthly",
        "price_monthly_display": "$1/mo",
        "price_label": "$1 / Month",
        "video_limit_minutes": 5,
        "script_word_limit": 5 * SCRIPT_WORDS_PER_MINUTE,
        "unlimited_videos": False,
        "features": [
            "Videos up to 5 minutes",
            "10 Exports Per Month",
            "Audio Free",
            "1 Device Login",
        ],
        "feature_summary": [
            "Videos up to 5 minutes",
            "10 Exports Per Month",
            "Audio Free",
            "1 Device Login",
        ],
        "metadata": {"monthly_export_limit": 10},
        "sort_order": 10,
    },
    {
        "plan_key": "basic_monthly",
        "name": "Creator",
        "display_name": "Creator",
        "billing_period": "monthly",
        "price_monthly_display": "$19/mo",
        "price_label": "$19 / Month",
        "video_limit_minutes": 15,
        "script_word_limit": 15 * SCRIPT_WORDS_PER_MINUTE,
        "unlimited_videos": False,
        "features": [
            "Videos up to 15 minutes",
            "60 Exports Per Month",
            "Audio Free",
            "1 Device Login",
        ],
        "feature_summary": [
            "Videos up to 15 minutes",
            "60 Exports Per Month",
            "Audio Free",
            "1 Device Login",
        ],
        "metadata": {"monthly_export_limit": 60},
        "sort_order": 20,
    },
    {
        "plan_key": "plus_monthly",
        "name": "Pro",
        "display_name": "Pro",
        "billing_period": "monthly",
        "price_monthly_display": "$49/mo",
        "price_label": "$49 / Month",
        "video_limit_minutes": 35,
        "script_word_limit": 35 * SCRIPT_WORDS_PER_MINUTE,
        "unlimited_videos": True,
        "features": [
            "Unlimited Exports",
            "Videos up to 30+ minutes",
            "Audio Free (Full Catalog)",
            "2 Device Logins",
            "Priority Render Queue & 4K",
            "100% Monetization Rights",
            "Most Popular",
        ],
        "feature_summary": [
            "Unlimited Exports",
            "Videos up to 30+ minutes",
            "Audio Free (Full Catalog)",
            "2 Device Logins",
            "Priority Render Queue & 4K",
            "100% Monetization Rights",
            "Most Popular",
        ],
        "metadata": {"monthly_export_limit": None, "is_popular": True},
        "sort_order": 30,
    },
    {
        "plan_key": "basic_yearly",
        "display_name": "Basic Yearly",
        "billing_period": "yearly",
        "price_monthly_display": "$15/mo",
        "video_limit_minutes": 15,
        "script_word_limit": 15 * SCRIPT_WORDS_PER_MINUTE,
        "unlimited_videos": False,
        "features": [
            "Videos up to 15 minutes",
            "60 Exports Per Month",
            "Save 21% with yearly billing",
        ],
        "sort_order": 40,
    },
    {
        "plan_key": "plus_yearly",
        "display_name": "Plus Yearly",
        "billing_period": "yearly",
        "price_monthly_display": "$39/mo",
        "video_limit_minutes": 30,
        "script_word_limit": 30 * SCRIPT_WORDS_PER_MINUTE,
        "unlimited_videos": True,
        "features": [
            "Videos up to 30 minutes",
            "Unlimited Exports",
            "Save 20% with yearly billing",
        ],
        "sort_order": 50,
    },
    {
        "plan_key": "max_yearly",
        "display_name": "Max Yearly",
        "billing_period": "yearly",
        "price_monthly_display": "$79/mo",
        "video_limit_minutes": 180,
        "script_word_limit": 180 * SCRIPT_WORDS_PER_MINUTE,
        "unlimited_videos": True,
        "features": [
            "Everything in Plus",
            "Videos up to 2 hours+",
            "Unlimited Exports",
            "Up to 3 Devices",
            "Save 20% with yearly billing",
        ],
        "sort_order": 60,
    },
]


class AuthServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def auth_is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def service_role_is_configured() -> bool:
    if VIDZEO_PACKAGED and not VIDZEO_ALLOW_PACKAGED_SERVICE_ROLE:
        return False
    return bool(auth_is_configured() and SUPABASE_SERVICE_ROLE_KEY)


def public_auth_config() -> Dict[str, Any]:
    return {
        "enabled": auth_is_configured(),
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
        "google_client_id": GOOGLE_CLIENT_ID,
    }


def _extract_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return (
        payload.get("msg")
        or payload.get("message")
        or payload.get("error_description")
        or payload.get("error")
        or response.text
        or f"Supabase request failed with {response.status_code}"
    )


def _service_headers(prefer: Optional[str] = None) -> Dict[str, str]:
    if not service_role_is_configured():
        raise AuthServiceError("Supabase service role is not configured.")
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _auth_headers(access_token: str) -> Dict[str, str]:
    if not auth_is_configured():
        raise AuthServiceError("Supabase auth is not configured.")
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {access_token}",
    }


def _rest_request(
    method: str,
    resource: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Any] = None,
    prefer: Optional[str] = None,
) -> Any:
    try:
        response = requests.request(
            method,
            f"{REST_BASE}/{resource.lstrip('/')}",
            params=params,
            json=json_payload,
            headers=_service_headers(prefer),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AuthServiceError(
            "Supabase is temporarily unreachable. Check your internet connection.",
            status_code=503,
        ) from exc
    if response.status_code >= 400:
        raise AuthServiceError(_extract_error(response), status_code=response.status_code)
    if not response.text:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _rpc_request(
    access_token: str,
    function_name: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Any:
    try:
        response = requests.post(
            f"{REST_BASE}/rpc/{function_name}",
            json=payload or {},
            headers={
                **_auth_headers(access_token),
                "Content-Type": "application/json",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AuthServiceError(
            "Supabase is temporarily unreachable. Check your internet connection.",
            status_code=503,
        ) from exc
    if response.status_code >= 400:
        message = _extract_error(response)
        status_code = response.status_code
        if "DEVICE_MISMATCH" in message:
            message = "This account is already locked to another device. Contact support to reset the device binding."
            status_code = 403
        elif "DEVICE_LIMIT_REACHED" in message:
            message = "This Vidzeo account has reached the 2-device limit. Ask an admin to revoke an old device."
            status_code = 403
        elif "DEVICE_FINGERPRINT_REQUIRED" in message:
            message = "Device verification is required. Restart Vidnets Studio and sign in again."
            status_code = 403
        raise AuthServiceError(message, status_code=status_code)
    if not response.text:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _service_rpc_request(function_name: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    if not service_role_is_configured():
        raise AuthServiceError("Admin service role is not configured.", status_code=503)
    try:
        response = requests.post(
            f"{REST_BASE}/rpc/{function_name}",
            json=payload or {},
            headers={
                **_service_headers(),
                "Content-Type": "application/json",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise AuthServiceError(
            "Supabase is temporarily unreachable. Check your internet connection.",
            status_code=503,
        ) from exc
    if response.status_code >= 400:
        raise AuthServiceError(_extract_error(response), status_code=response.status_code)
    if not response.text:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def verify_access_token(access_token: str) -> Dict[str, Any]:
    if not access_token:
        raise AuthServiceError("Authentication required.", status_code=401)
    cached = _cache_get(_verify_cache, access_token, _VERIFY_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    max_attempts = 3
    last_error = None
    for attempt in range(max_attempts):
        try:
            response = requests.get(
                f"{AUTH_BASE}/user",
                headers=_auth_headers(access_token),
                timeout=15,
            )
        except requests.RequestException as exc:
            last_error = AuthServiceError(
                "Supabase is temporarily unreachable. Check your internet connection.",
                status_code=503,
            )
            if attempt < max_attempts - 1:
                time.sleep(1.0 * (2 ** attempt))
                continue
            raise last_error from exc

        if response.status_code == 429:
            if attempt < max_attempts - 1:
                backoff = 1.0 * (2 ** attempt)
                print(f"[auth] Supabase rate-limited (429), backing off {backoff:.0f}s")
                time.sleep(backoff)
                continue
            raise AuthServiceError("Supabase rate-limited. Try again later.", status_code=429)

        # Only genuine credential failures are 401. Transient 5xx must NOT log
        # the user out: the desktop keeps the session and retries later.
        if response.status_code in {400, 401, 403, 422}:
            raise AuthServiceError("Authentication required.", status_code=401)
        if response.status_code >= 500:
            raise AuthServiceError(
                "Supabase is temporarily unreachable. Check your internet connection.",
                status_code=503,
            )
        user = response.json()
        _cache_put(_verify_cache, access_token, user, _VERIFY_CACHE_TTL_SECONDS)
        return user
    raise last_error or AuthServiceError("Authentication required.", status_code=401)


def exchange_pkce_code(auth_code: str, code_verifier: str) -> Dict[str, Any]:
    """Exchange the one-time Supabase OAuth code used by the desktop flow.

    Supabase currently returns an OAuth ``code`` for Google sign-in even when
    the client starts the flow from a desktop shell.  Keeping the verifier in
    the short-lived server-side flow means the callback never has to depend on
    browser localStorage (which is unreliable when the user changes browsers
    or the callback opens in a fresh profile).
    """
    auth_code = str(auth_code or '').strip()
    code_verifier = str(code_verifier or '').strip()
    if not auth_code or not code_verifier:
        raise AuthServiceError("Google sign-in returned an incomplete OAuth code.", status_code=400)
    if not auth_is_configured():
        raise AuthServiceError("Supabase auth is not configured.", status_code=503)

    try:
        response = requests.post(
            f"{AUTH_BASE}/token?grant_type=pkce",
            json={
                "auth_code": auth_code,
                "code_verifier": code_verifier,
            },
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Content-Type": "application/json",
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise AuthServiceError(
            "Supabase is temporarily unreachable. Check your internet connection.",
            status_code=503,
        ) from exc

    if response.status_code >= 400:
        message = _extract_error(response)
        # Avoid leaking provider internals to the browser while keeping the
        # actionable OAuth failure visible in the desktop log.
        if response.status_code in {400, 401, 422}:
            message = "Google sign-in expired or was already used. Start sign-in again."
        raise AuthServiceError(message, status_code=response.status_code)

    try:
        payload = response.json() if response.text else {}
    except ValueError as exc:
        raise AuthServiceError("Supabase returned an invalid sign-in session.", status_code=502) from exc

    if not payload.get("access_token") or not payload.get("refresh_token"):
        raise AuthServiceError("Supabase did not return a complete sign-in session.", status_code=502)
    return payload


def _seed_admin_allowlist() -> None:
    global _admin_seed_attempted
    if _admin_seed_attempted or not service_role_is_configured():
        return
    _admin_seed_attempted = True
    emails = [item.strip().lower() for item in VIDZEO_ADMIN_EMAILS.split(",") if item.strip()]
    if not emails:
        return
    payload = [{"email": email, "role": "admin", "is_active": True} for email in emails]
    try:
        _rest_request(
            "POST",
            "admin_allowlist?on_conflict=email",
            json_payload=payload,
            prefer="resolution=merge-duplicates,return=representation",
        )
    except AuthServiceError:
        pass


def _coerce_plan_with_limits(plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    normalized = dict(plan)
    video_limit = normalized.get("video_limit_minutes")
    try:
        video_limit = int(video_limit) if video_limit is not None else None
    except (TypeError, ValueError):
        video_limit = None
    normalized["video_limit_minutes"] = video_limit

    script_word_limit = normalized.get("script_word_limit")
    try:
        script_word_limit = int(script_word_limit) if script_word_limit is not None else None
    except (TypeError, ValueError):
        script_word_limit = None
    if script_word_limit is None and video_limit:
        script_word_limit = int(video_limit * SCRIPT_WORDS_PER_MINUTE)
    normalized["script_word_limit"] = script_word_limit
    return normalized


def list_plans(*, include_unavailable: bool = False) -> List[Dict[str, Any]]:
    if not service_role_is_configured():
        try:
            response=requests.get(f'{SUPABASE_URL}/functions/v1/billing-catalog',headers={'apikey':SUPABASE_ANON_KEY},timeout=15)
            response.raise_for_status()
            catalog=response.json()
            return [_coerce_plan_with_limits({**p,'display_name':p['name'],
                'price_label':f"${p['monthly_equivalent']} / month" if p['billing_period']!='lifetime' else '$149 one-time',
                'metadata':{'monthly_export_limit':p.get('export_limit')},
                'feature_summary':[f"Up to {p['video_limit_minutes']} minutes per video",
                    'Unlimited exports' if p.get('export_limit') is None else f"{p['export_limit']} exports per month"]})
                for p in catalog.get('plans',[]) if p.get('verified') and p.get('available')]
        except (requests.RequestException,ValueError,KeyError):
            return []
    try:
        plans = _rest_request(
            "GET",
            "plans",
            params={
                "select": "*",
                "is_active": "eq.true",
                "order": "sort_order.asc",
            },
        ) or []
    except AuthServiceError as exc:
        if "temporarily unreachable" not in str(exc).lower():
            raise
        return []
    return [_coerce_plan_with_limits(plan) for plan in plans if include_unavailable or plan.get("is_available_for_purchase") is not False]


def get_plan(plan_key: str) -> Optional[Dict[str, Any]]:
    if not service_role_is_configured() or not plan_key:
        return next(
            (_coerce_plan_with_limits(plan) for plan in DEFAULT_PUBLIC_PLANS if plan.get("plan_key") == plan_key),
            None,
        )
    rows = _rest_request(
        "GET",
        "plans",
        params={
            "select": "*",
            "plan_key": f"eq.{plan_key}",
            "limit": "1",
        },
    ) or []
    return _coerce_plan_with_limits(rows[0]) if rows else None


def get_user_profile_by_id(profile_id: str) -> Optional[Dict[str, Any]]:
    if not service_role_is_configured() or not profile_id:
        return None
    rows = _rest_request(
        "GET",
        "user_profiles",
        params={
            "select": "*",
            "id": f"eq.{profile_id}",
            "limit": "1",
        },
    ) or []
    return rows[0] if rows else None


def get_user_profile_by_email(email: str) -> Optional[Dict[str, Any]]:
    if not service_role_is_configured() or not email:
        return None
    rows = _rest_request(
        "GET",
        "user_profiles",
        params={
            "select": "*",
            "email": f"eq.{email.strip().lower()}",
            "limit": "1",
        },
    ) or []
    return rows[0] if rows else None


def upsert_user_profile_from_auth(user: Dict[str, Any]) -> Dict[str, Any]:
    email = (user.get("email") or "").strip().lower()
    if not email:
        raise AuthServiceError("Google account email is required.")

    metadata = user.get("user_metadata") or {}
    fallback_name = email.split("@")[0].replace(".", " ").replace("_", " ").title()
    payload = {
        "auth_user_id": user.get("id"),
        "email": email,
        "full_name": metadata.get("full_name") or metadata.get("name") or fallback_name,
        "avatar_url": metadata.get("avatar_url") or metadata.get("picture") or "",
        "last_login_at": _now_iso(),
    }
    rows = _rest_request(
        "POST",
        "user_profiles?on_conflict=email",
        json_payload=[payload],
        prefer="resolution=merge-duplicates,return=representation",
    ) or []
    profile = rows[0] if rows else get_user_profile_by_email(email)
    if not profile:
        raise AuthServiceError("Could not load the signed-in user profile.")
    return profile


def _list_assignments_for_profile(profile_id: str, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if not service_role_is_configured() or not profile_id:
        return []
    params = {
        "select": "*,plans(*)",
        "user_profile_id": f"eq.{profile_id}",
        "order": "created_at.desc",
    }
    if statuses:
        joined = ",".join(statuses)
        params["status"] = f"in.({joined})"
    return _rest_request("GET", "user_plan_assignments", params=params) or []


def get_latest_assignment_for_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    active_rows = _list_assignments_for_profile(profile_id, ["active"])
    if active_rows:
        return active_rows[0]
    pending_rows = _list_assignments_for_profile(profile_id, ["pending"])
    if pending_rows:
        return pending_rows[0]
    any_rows = _list_assignments_for_profile(profile_id)
    return any_rows[0] if any_rows else None


def _coerce_access_state(profile: Dict[str, Any], assignment: Optional[Dict[str, Any]]) -> str:
    account_status = (profile.get("account_status") or "pending").strip().lower()
    if account_status == "suspended":
        return "suspended"
    if account_status == "active" and assignment and assignment.get("status") == "active":
        return "active"
    return "pending"


def _assignment_timestamp(assignment: Optional[Dict[str, Any]]) -> str:
    if not assignment:
        return ""
    return (
        assignment.get("updated_at")
        or assignment.get("starts_at")
        or assignment.get("created_at")
        or ""
    )


def _access_updated_at(profile: Dict[str, Any], assignment: Optional[Dict[str, Any]], pending_assignment: Optional[Dict[str, Any]] = None) -> str:
    candidates = [
        profile.get("updated_at") or "",
        _assignment_timestamp(assignment),
        _assignment_timestamp(pending_assignment),
    ]
_EXPORT_USAGE_FILE = os.path.join(
    getattr(config, 'TEMP_DIR', None) or os.path.join(os.path.dirname(os.path.dirname(__file__)), 'temp'),
    'export_usage.json'
)

def get_current_export_month() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m')

def get_plan_export_limit(plan_key: Optional[str]) -> Optional[int]:
    key = str(plan_key or '').strip().lower()
    if key in ('free_trial', 'free', 'free_starter'):
        return 5
    if key in ('base_monthly', 'base_yearly', 'base'):
        return 10
    if key in ('basic_monthly', 'basic_yearly', 'creator_monthly', 'creator'):
        return 60
    if key in ('plus_monthly', 'plus_yearly', 'pro_monthly', 'pro', 'max_monthly', 'max_yearly', 'max', 'lifetime_pro'):
        return None
    return 10

def get_user_monthly_export_usage(user_id: Optional[str], plan_key: Optional[str] = None, *, access_token: str = '') -> Dict[str, Any]:
    if access_token:
        result = _rpc_request(access_token, 'get_vidnets_export_usage')
        if not isinstance(result, dict):
            raise AuthServiceError('Export allowance could not be loaded.', status_code=503)
        return result
    user_key = str(user_id or 'local-user').strip()
    current_month = get_current_export_month()
    count = 0
    try:
        if os.path.exists(_EXPORT_USAGE_FILE):
            with open(_EXPORT_USAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                count = int(data.get(f"{user_key}:{current_month}", 0))
    except Exception:
        count = 0

    limit = get_plan_export_limit(plan_key)
    remaining = max(0, limit - count) if limit is not None else None
    return {
        "month": current_month,
        "used": count,
        "limit": limit,
        "remaining": remaining,
        "is_unlimited": limit is None,
    }

def record_user_export(user_id: Optional[str], plan_key: Optional[str] = None) -> int:
    user_key = str(user_id or 'local-user').strip()
    current_month = get_current_export_month()
    key = f"{user_key}:{current_month}"
    data = {}
    try:
        os.makedirs(os.path.dirname(_EXPORT_USAGE_FILE), exist_ok=True)
        if os.path.exists(_EXPORT_USAGE_FILE):
            with open(_EXPORT_USAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
    except Exception:
        data = {}

    data[key] = int(data.get(key, 0)) + 1
    try:
        with open(_EXPORT_USAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as exc:
        print(f"Failed saving export usage: {exc}")
    return data[key]


def build_auth_state(profile: Dict[str, Any], assignment: Optional[Dict[str, Any]], *, provider: str = "google") -> Dict[str, Any]:
    plan = _coerce_plan_with_limits(assignment.get("plans") if assignment else {})
    access_state = _coerce_access_state(profile, assignment)
    plan_key = assignment.get("plan_key") if assignment else None
    plan_status = assignment.get("status") if assignment else "pending"
    pending_plan_key = plan_key if plan_status == "pending" else None
    pending_plan_name = (plan.get("display_name") or plan.get("name") or pending_plan_key) if pending_plan_key else None
    plan_name = plan.get("display_name") or plan.get("name") or "No active plan"
    access_copy = (
        "Your studio access is active."
        if access_state == "active"
        else "Purchase Lifetime Pro at https://vidnets.com/#pricing, then refresh access. Private trials require an administrator."
        if access_state == "pending"
        else "Your account is suspended. Contact support or an administrator."
    )
    return {
        "signed_in": True,
        "provider": provider,
        "user_id": profile.get("id"),
        "auth_user_id": profile.get("auth_user_id"),
        "email": profile.get("email"),
        "name": profile.get("full_name") or profile.get("email") or "Vidzeo Creator",
        "avatar_url": profile.get("avatar_url") or "",
        "plan_key": plan_key,
        "plan_name": plan_name,
        "plan_status": plan_status,
        "pending_plan_key": pending_plan_key,
        "pending_plan_name": pending_plan_name,
        "requested_plan_key": pending_plan_key,
        "requested_plan_name": pending_plan_name,
        "access_state": access_state,
        "access_copy": access_copy,
        "account_status": profile.get("account_status") or "pending",
        "billing_period": plan.get("billing_period"),
        "max_video_minutes": plan.get("video_limit_minutes"),
        "max_script_words": plan.get("script_word_limit"),
        "price_monthly_display": plan.get("price_monthly_display"),
        "access_updated_at": _access_updated_at(profile, assignment),
        "device_fingerprint": profile.get("device_fingerprint") or "",
        "device_status": "active" if profile.get("device_fingerprint") else "",
        "device_count": None,
        "device_limit": 2,
        "monthly_export_limit": get_plan_export_limit(plan_key),
        "monthly_exports_used": get_user_monthly_export_usage(profile.get("id"), plan_key).get("used", 0),
        "monthly_exports_remaining": get_user_monthly_export_usage(profile.get("id"), plan_key).get("remaining"),
        "is_unlimited_exports": get_plan_export_limit(plan_key) is None,
    }


def _profile_from_auth_user(user: Dict[str, Any]) -> Dict[str, Any]:
    email = (user.get("email") or "").strip().lower()
    metadata = user.get("user_metadata") or {}
    fallback_name = email.split("@")[0].replace(".", " ").replace("_", " ").title() if email else "Vidzeo Creator"
    return {
        "id": user.get("id"),
        "auth_user_id": user.get("id"),
        "email": email,
        "full_name": metadata.get("full_name") or metadata.get("name") or fallback_name,
        "avatar_url": metadata.get("avatar_url") or metadata.get("picture") or "",
        "account_status": "pending",
    }


def _first_rpc_row(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else None
    return payload if isinstance(payload, dict) else None


def _state_from_access_rpc(row: Dict[str, Any], *, access_token: str, provider: str = "google") -> Dict[str, Any]:
    plan_key = row.get("plan_key")
    plan_name = row.get("plan_name") or ("No active plan" if not plan_key else plan_key)
    access_state = row.get("access_state") or "pending"
    access_copy = row.get("access_copy") or (
        "Your studio access is active."
        if access_state == "active"
        else "Purchase Lifetime Pro at https://vidnets.com/#pricing, then refresh access. Private trials require an administrator."
        if access_state == "pending"
        else "Your account is suspended. Contact support or an administrator."
    )
    usage = get_user_monthly_export_usage(row.get("user_id"), plan_key, access_token=access_token)
    state = {
        "signed_in": True,
        "provider": provider,
        "user_id": row.get("user_id"),
        "auth_user_id": row.get("auth_user_id"),
        "email": row.get("email"),
        "name": row.get("name") or row.get("email") or "Vidzeo Creator",
        "avatar_url": row.get("avatar_url") or "",
        "plan_key": plan_key,
        "plan_name": plan_name,
        "plan_status": row.get("plan_status") or "pending",
        "access_state": access_state,
        "access_copy": access_copy,
        "account_status": row.get("account_status") or "pending",
        "billing_period": row.get("billing_period"),
        "max_video_minutes": row.get("max_video_minutes"),
        "max_script_words": row.get("max_script_words"),
        "price_monthly_display": row.get("price_monthly_display"),
        "device_id": row.get("device_id"),
        "device_fingerprint": row.get("device_fingerprint"),
        "device_status": row.get("device_status") or "",
        "device_count": row.get("device_count"),
        "device_limit": row.get("device_limit"),
        "monthly_export_limit": usage.get("limit"),
        "monthly_exports_used": usage.get("used", 0),
        "monthly_exports_remaining": usage.get("remaining"),
        "is_unlimited_exports": usage.get("is_unlimited", False),
        "access_token": access_token,
    }
    if "pending_plan_key" in row or "pending_plan_name" in row:
        state["pending_plan_key"] = row.get("pending_plan_key")
        state["pending_plan_name"] = row.get("pending_plan_name")
        state["requested_plan_key"] = row.get("pending_plan_key")
        state["requested_plan_name"] = row.get("pending_plan_name")
    if "access_updated_at" in row:
        state["access_updated_at"] = row.get("access_updated_at")
    return state


def _sync_profile_mirror(profile: Optional[Dict[str, Any]]) -> None:
    if not profile or not service_role_is_configured():
        return
    auth_user_id = profile.get("auth_user_id")
    if not auth_user_id:
        return
    try:
        _service_rpc_request("sync_assignment_state_for_auth_user", {"p_auth_user_id": auth_user_id})
    except AuthServiceError:
        pass


def list_user_devices(target_user_profile_id: str) -> List[Dict[str, Any]]:
    if not service_role_is_configured() or not target_user_profile_id:
        return []
    try:
        result = _service_rpc_request("list_user_devices", {"p_user_id": target_user_profile_id})
        return result if isinstance(result, list) else []
    except AuthServiceError:
        try:
            return _rest_request(
                "GET",
                "user_devices",
                params={
                    "select": "*",
                    "user_profile_id": f"eq.{target_user_profile_id}",
                    "order": "last_seen_at.desc",
                },
            ) or []
        except AuthServiceError:
            return []


def get_access_state_via_rpc(
    access_token: str,
    *,
    device_fingerprint: str = "",
    device_info: Optional[Dict[str, Any]] = None,
    last_login_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not auth_is_configured():
        return None
    row = _first_rpc_row(_rpc_request(
        access_token,
        "get_vidzeo_access_state",
        {
            "p_device_fingerprint": device_fingerprint,
            "p_device_info": device_info or {},
            "p_last_login_ip": last_login_ip,
            "p_user_agent": user_agent,
        },
    ))
    return _state_from_access_rpc(row, access_token=access_token) if row else None


def sync_authenticated_user(
    access_token: str,
    *,
    device_fingerprint: str = "",
    device_info: Optional[Dict[str, Any]] = None,
    last_login_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    cache_key = f"{access_token}\0{device_fingerprint}"
    cached = _cache_get(_state_cache, cache_key, _STATE_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    user = verify_access_token(access_token)
    # Sign-in creates a profile only. Access requires payment or an admin grant.
    _rpc_request(access_token, 'ensure_vidzeo_user_profile')
    if device_fingerprint:
        try:
            rpc_state = get_access_state_via_rpc(
                access_token,
                device_fingerprint=device_fingerprint,
                device_info=device_info,
                last_login_ip=last_login_ip,
                user_agent=user_agent,
            )
            if rpc_state:
                _cache_put(_state_cache, cache_key, rpc_state, _STATE_CACHE_TTL_SECONDS)
                return rpc_state
        except AuthServiceError:
            if not service_role_is_configured():
                raise
            # Development/admin builds with service role can keep working even
            # before the latest DB RPC migration has been applied.
    if not service_role_is_configured():
        state = build_auth_state(_profile_from_auth_user(user), None)
        _cache_put(_state_cache, cache_key, state, _STATE_CACHE_TTL_SECONDS)
        return state
    profile = upsert_user_profile_from_auth(user)
    assignment = get_latest_assignment_for_profile(profile.get("id"))
    state = build_auth_state(profile, assignment)
    _cache_put(_state_cache, cache_key, state, _STATE_CACHE_TTL_SECONDS)
    return state


def request_plan(
    access_token: str,
    plan_key: str,
    *,
    device_fingerprint: str = "",
    device_info: Optional[Dict[str, Any]] = None,
    last_login_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    if plan_key != "lifetime_pro":
        raise AuthServiceError("Only Lifetime Pro is available. Private trials require an administrator.")
    plan = get_plan(plan_key)
    if not plan:
        raise AuthServiceError("Selected plan does not exist.")
    if plan.get("is_available_for_purchase") is False:
        raise AuthServiceError("This plan is no longer available for new purchases.")

    user = verify_access_token(access_token)
    if device_fingerprint:
        try:
            row = _first_rpc_row(_rpc_request(
                access_token,
                "request_vidzeo_plan",
                {
                    "p_plan_key": plan_key,
                    "p_device_fingerprint": device_fingerprint,
                    "p_device_info": device_info or {},
                    "p_last_login_ip": last_login_ip,
                    "p_user_agent": user_agent,
                },
            ))
            if row:
                state = _state_from_access_rpc(row, access_token=access_token)
                if state.get("plan_key") != plan_key or state.get("plan_status") != "active":
                    state["pending_plan_key"] = plan_key
                    state["requested_plan_key"] = plan_key
                    state["pending_plan_name"] = plan.get("display_name") or plan.get("name") or plan_key
                    state["requested_plan_name"] = state["pending_plan_name"]
                return state
        except AuthServiceError:
            if not service_role_is_configured():
                raise

    if not service_role_is_configured():
        state = build_auth_state(
            _profile_from_auth_user(user),
            {"plan_key": plan_key, "status": "pending", "plans": plan},
        )
        state["access_state"] = "pending"
        state["access_copy"] = "Plan selected. Admin activation is required before the studio unlocks."
        state["pending_plan_key"] = plan_key
        state["requested_plan_key"] = plan_key
        state["pending_plan_name"] = plan.get("display_name") or plan.get("name") or plan_key
        state["requested_plan_name"] = state["pending_plan_name"]
        return state

    profile = upsert_user_profile_from_auth(user)
    assignment = get_latest_assignment_for_profile(profile["id"])
    active_assignment = _list_assignments_for_profile(profile["id"], ["active"])
    active_assignment = active_assignment[0] if active_assignment else None

    if assignment and assignment.get("plan_key") == plan_key and assignment.get("status") in {"pending", "active"}:
        state = build_auth_state(profile, active_assignment or assignment)
        if assignment and assignment.get("status") == "pending":
            state["pending_plan_key"] = assignment.get("plan_key")
            state["requested_plan_key"] = assignment.get("plan_key")
            pending_plan = _coerce_plan_with_limits(assignment.get("plans") or plan)
            state["pending_plan_name"] = pending_plan.get("display_name") or pending_plan.get("name") or assignment.get("plan_key")
            state["requested_plan_name"] = state["pending_plan_name"]
        return state

    existing_pending = _list_assignments_for_profile(profile["id"], ["pending"])
    if existing_pending:
        ids = ",".join(item["id"] for item in existing_pending if item.get("id"))
        if ids:
            _rest_request(
                "PATCH",
                "user_plan_assignments",
                params={"id": f"in.({ids})"},
                json_payload={"status": "revoked", "updated_at": _now_iso()},
                prefer="return=representation",
            )

    rows = _rest_request(
        "POST",
        "user_plan_assignments",
        json_payload=[{
            "user_profile_id": profile["id"],
            "plan_key": plan_key,
            "status": "pending",
            "requested_by_email": profile.get("email"),
            "note": "Requested inside the Vidnets Studio v1 desktop app.",
        }],

        prefer="return=representation",
    ) or []
    new_assignment = rows[0] if rows else get_latest_assignment_for_profile(profile["id"])
    create_audit_log(
        actor_user_id=profile.get("id"),
        actor_email=profile.get("email"),
        target_user_profile_id=profile.get("id"),
        target_email=profile.get("email"),
        action="plan.requested",
        details={"plan_key": plan_key},
    )
    state = build_auth_state(profile, active_assignment or new_assignment)
    state["pending_plan_key"] = plan_key
    state["requested_plan_key"] = plan_key
    state["pending_plan_name"] = plan.get("display_name") or plan.get("name") or plan_key
    state["requested_plan_name"] = state["pending_plan_name"]
    return state


def get_admin_membership(email: str) -> Optional[Dict[str, Any]]:
    _seed_admin_allowlist()
    if not service_role_is_configured() or not email:
        return None
    rows = _rest_request(
        "GET",
        "admin_allowlist",
        params={
            "select": "*",
            "email": f"eq.{email.strip().lower()}",
            "is_active": "eq.true",
            "limit": "1",
        },
    ) or []
    return rows[0] if rows else None


def verify_admin(access_token: str, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = state or sync_authenticated_user(access_token)
    membership = get_admin_membership(state.get("email") or "")
    if not membership:
        raise AuthServiceError("Admin access denied.")
    return {
        **state,
        "admin_role": membership.get("role") or "admin",
    }


def create_audit_log(
    *,
    actor_user_id: Optional[str],
    actor_email: Optional[str],
    target_user_profile_id: Optional[str],
    target_email: Optional[str],
    action: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    if not service_role_is_configured():
        return
    try:
        _rest_request(
            "POST",
            "admin_audit_logs",
            json_payload=[{
                "actor_user_id": actor_user_id,
                "actor_email": (actor_email or "").lower(),
                "target_user_profile_id": target_user_profile_id,
                "target_email": (target_email or "").lower(),
                "action": action,
                "details": details or {},
            }],
            prefer="return=representation",
        )
    except AuthServiceError:
        return


def list_users(search: str = "", status: str = "") -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "select": "*",
        "order": "updated_at.desc",
    }
    if search:
        safe = search.replace(",", " ").strip()
        params["or"] = f"(email.ilike.*{safe}*,full_name.ilike.*{safe}*)"
    if status in ACCOUNT_STATUS_VALUES:
        params["account_status"] = f"eq.{status}"

    profiles = _rest_request("GET", "user_profiles", params=params) or []
    if not profiles:
        return []

    profile_ids = ",".join(item["id"] for item in profiles if item.get("id"))
    assignments = _rest_request(
        "GET",
        "user_plan_assignments",
        params={
            "select": "*,plans(*)",
            "user_profile_id": f"in.({profile_ids})",
            "order": "created_at.desc",
        },
    ) or []

    active_lookup: Dict[str, Dict[str, Any]] = {}
    pending_lookup: Dict[str, Dict[str, Any]] = {}
    latest_lookup: Dict[str, Dict[str, Any]] = {}
    for row in assignments:
        profile_id = row.get("user_profile_id")
        if not profile_id:
            continue
        latest_lookup.setdefault(profile_id, row)
        if row.get("status") == "active":
            active_lookup.setdefault(profile_id, row)
        elif row.get("status") == "pending":
            pending_lookup.setdefault(profile_id, row)

    results = []
    for profile in profiles:
        profile_id = profile.get("id")
        active_assignment = active_lookup.get(profile_id)
        pending_assignment = pending_lookup.get(profile_id)
        assignment = active_assignment or pending_assignment or latest_lookup.get(profile_id)
        results.append({
            **profile,
            "current_plan": assignment,
            "active_plan": active_assignment,
            "pending_plan": pending_assignment,
            "requested_plan": pending_assignment,
            "access_state": _coerce_access_state(profile, assignment),
            "access_updated_at": _access_updated_at(profile, active_assignment or assignment, pending_assignment),
        })
    return results


def get_user_detail(profile_id: str) -> Dict[str, Any]:
    profile = get_user_profile_by_id(profile_id)
    if not profile:
        raise AuthServiceError("User not found.")
    assignments = _list_assignments_for_profile(profile_id)
    try:
        audits = _rest_request(
            "GET",
            "admin_audit_logs",
            params={
                "select": "*",
                "target_user_profile_id": f"eq.{profile_id}",
                "order": "created_at.desc",
                "limit": "50",
            },
        ) or []
    except AuthServiceError:
        audits = []
    current_assignment = get_latest_assignment_for_profile(profile_id)
    active_assignment = next((item for item in assignments if item.get("status") == "active"), None)
    pending_assignment = next((item for item in assignments if item.get("status") == "pending"), None)
    return {
        "profile": profile,
        "current_plan": current_assignment,
        "active_plan": active_assignment,
        "pending_plan": pending_assignment,
        "requested_plan": pending_assignment,
        "plan_history": assignments,
        "devices": list_user_devices(profile_id),
        "audit_logs": audits,
        "access_state": _coerce_access_state(profile, current_assignment),
        "access_updated_at": _access_updated_at(profile, active_assignment or current_assignment, pending_assignment),
    }


def create_pending_user(
    email: str,
    full_name: str = "",
    note: str = "",
    *,
    actor_user_id: Optional[str] = None,
    actor_email: Optional[str] = None,
) -> Dict[str, Any]:
    clean_email = (email or "").strip().lower()
    if not clean_email:
        raise AuthServiceError("Email is required.")
    if "@" not in clean_email:
        raise AuthServiceError("A valid email address is required.")

    payload = {
        "email": clean_email,
        "full_name": full_name.strip() or clean_email.split("@")[0].replace(".", " ").title(),
        "account_status": "pending",
    }
    rows = _rest_request(
        "POST",
        "user_profiles?on_conflict=email",
        json_payload=[payload],
        prefer="resolution=merge-duplicates,return=representation",
    ) or []
    profile = rows[0] if rows else get_user_profile_by_email(clean_email)
    if not profile:
        raise AuthServiceError("Could not create the pending user.")
    create_audit_log(
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_user_profile_id=profile.get("id"),
        target_email=profile.get("email"),
        action="user.created_pending",
        details={"note": note},
    )
    return profile


def assign_plan_to_user(
    *,
    actor_user_id: Optional[str],
    actor_email: Optional[str],
    target_user_profile_id: str,
    plan_key: str,
    status: str,
    note: str = "",
) -> Dict[str, Any]:
    if status not in PLAN_ASSIGNMENT_STATUS_VALUES:
        raise AuthServiceError("Unsupported plan status.")
    profile = get_user_profile_by_id(target_user_profile_id)
    if not profile:
        raise AuthServiceError("User not found.")
    plan = get_plan(plan_key)
    if not plan:
        raise AuthServiceError("Plan not found.")

    existing_active = _list_assignments_for_profile(target_user_profile_id, ["active"])
    existing_pending = _list_assignments_for_profile(target_user_profile_id, ["pending"])
    if plan_key == "free_trial" and status in {"active", "pending"}:
        # Never replace a paid entitlement with a private trial by accident.
        if any(item.get("plan_key") != "free_trial" for item in existing_active):
            raise AuthServiceError("This account already has paid access. A trial cannot replace it.")
        note = "Admin trial: " + (note or "Granted from the owner admin panel.")

    if status in {"revoked", "expired"}:
        existing_open_assignments = [*existing_active, *existing_pending]
        ids = ",".join(item["id"] for item in existing_open_assignments if item.get("id"))
        if ids:
            _rest_request(
                "PATCH",
                "user_plan_assignments",
                params={"id": f"in.({ids})"},
                json_payload={"status": status, "updated_at": _now_iso()},
                prefer="return=representation",
            )
        if profile.get("account_status") != "suspended":
            _rest_request(
                "PATCH",
                "user_profiles",
                params={"id": f"eq.{target_user_profile_id}"},
                json_payload={"account_status": "pending", "updated_at": _now_iso()},
                prefer="return=representation",
            )
            profile["account_status"] = "pending"
        assignment = get_latest_assignment_for_profile(target_user_profile_id)
        create_audit_log(
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target_user_profile_id=target_user_profile_id,
            target_email=profile.get("email"),
            action=f"plan.{status}",
            details={"plan_key": plan_key, "note": note},
        )
        _sync_profile_mirror(profile)
        return {
            "profile": profile,
            "current_plan": assignment,
            "access_state": _coerce_access_state(profile, assignment),
        }

    assignments_to_close = existing_pending if status == "pending" else [*existing_active, *existing_pending]
    if assignments_to_close:
        ids = ",".join(item["id"] for item in assignments_to_close if item.get("id"))
        if ids:
            _rest_request(
                "PATCH",
                "user_plan_assignments",
                params={"id": f"in.({ids})"},
                json_payload={"status": "revoked", "updated_at": _now_iso()},
                prefer="return=representation",
            )

    created = _rest_request(
        "POST",
        "user_plan_assignments",
        json_payload=[{
            "user_profile_id": target_user_profile_id,
            "plan_key": plan_key,
            "status": status,
            "assigned_by_user_id": actor_user_id,
            "assigned_by_email": (actor_email or "").lower(),
            "note": note or "",
            "starts_at": _now_iso() if status == "active" else None,
        }],
        prefer="return=representation",
    ) or []

    next_account_status = profile.get("account_status") or "pending"
    if next_account_status != "suspended":
        next_account_status = "active" if (status == "active" or existing_active) else "pending"
        _rest_request(
            "PATCH",
            "user_profiles",
            params={"id": f"eq.{target_user_profile_id}"},
            json_payload={"account_status": next_account_status, "updated_at": _now_iso()},
            prefer="return=representation",
        )
        profile["account_status"] = next_account_status

    assignment = created[0] if created else get_latest_assignment_for_profile(target_user_profile_id)
    create_audit_log(
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_user_profile_id=target_user_profile_id,
        target_email=profile.get("email"),
        action="plan.assigned",
        details={
            "plan_key": plan_key,
            "status": status,
            "note": note,
            "kept_existing_active_during_pending": bool(status == "pending" and existing_active),
        },
    )
    _sync_profile_mirror(profile)
    return {
        "profile": profile,
        "current_plan": assignment,
        "access_state": _coerce_access_state(profile, assignment),
    }


def update_user_account_status(
    *,
    actor_user_id: Optional[str],
    actor_email: Optional[str],
    target_user_profile_id: str,
    account_status: str,
    note: str = "",
) -> Dict[str, Any]:
    if account_status not in ACCOUNT_STATUS_VALUES:
        raise AuthServiceError("Unsupported account status.")
    profile = get_user_profile_by_id(target_user_profile_id)
    if not profile:
        raise AuthServiceError("User not found.")

    _rest_request(
        "PATCH",
        "user_profiles",
        params={"id": f"eq.{target_user_profile_id}"},
        json_payload={"account_status": account_status, "updated_at": _now_iso()},
        prefer="return=representation",
    )
    profile["account_status"] = account_status

    if account_status == "suspended":
        active_assignments = _list_assignments_for_profile(target_user_profile_id, ["active"])
        if active_assignments:
            ids = ",".join(item["id"] for item in active_assignments if item.get("id"))
            if ids:
                _rest_request(
                    "PATCH",
                    "user_plan_assignments",
                    params={"id": f"in.({ids})"},
                    json_payload={"status": "revoked", "updated_at": _now_iso()},
                    prefer="return=representation",
                )

    assignment = get_latest_assignment_for_profile(target_user_profile_id)
    create_audit_log(
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_user_profile_id=target_user_profile_id,
        target_email=profile.get("email"),
        action="user.status_updated",
        details={"account_status": account_status, "note": note},
    )
    _sync_profile_mirror(profile)
    return {
        "profile": profile,
        "current_plan": assignment,
        "access_state": _coerce_access_state(profile, assignment),
    }


def reset_user_device_binding(
    *,
    actor_user_id: Optional[str],
    actor_email: Optional[str],
    target_user_profile_id: str,
    note: str = "",
) -> Dict[str, Any]:
    profile = get_user_profile_by_id(target_user_profile_id)
    if not profile:
        raise AuthServiceError("User not found.")

    auth_user_id = profile.get("auth_user_id")
    try:
        _service_rpc_request(
            "admin_reset_user_devices",
            {
                "p_user_id": target_user_profile_id,
                "p_admin_email": actor_email or "",
                "p_admin_note": note or "",
            },
        )
    except AuthServiceError as exc:
        if service_role_is_configured():
            raise AuthServiceError(f"Could not reset user devices: {exc}", status_code=getattr(exc, "status_code", 400)) from exc

    payload = {
        "device_fingerprint": None,
        "device_info": {},
        "last_login_ip": None,
        "user_agent": None,
        "updated_at": _now_iso(),
    }
    _rest_request(
        "PATCH",
        "user_profiles",
        params={"id": f"eq.{target_user_profile_id}"},
        json_payload=payload,
        prefer="return=representation",
    )
    if auth_user_id:
        _rest_request(
            "PATCH",
            "profiles",
            params={"id": f"eq.{auth_user_id}"},
            json_payload=payload,
            prefer="return=representation",
        )

    create_audit_log(
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_user_profile_id=target_user_profile_id,
        target_email=profile.get("email"),
        action="user.device_reset",
        details={"note": note},
    )
    updated = get_user_profile_by_id(target_user_profile_id) or profile
    assignment = get_latest_assignment_for_profile(target_user_profile_id)
    return {
        "profile": updated,
        "current_plan": assignment,
        "access_state": _coerce_access_state(updated, assignment),
    }


def revoke_user_device(
    *,
    actor_user_id: Optional[str],
    actor_email: Optional[str],
    target_device_id: str,
    note: str = "",
) -> Dict[str, Any]:
    if not target_device_id:
        raise AuthServiceError("Device id is required.")
    result = _service_rpc_request(
        "admin_revoke_user_device",
        {
            "p_device_id": target_device_id,
            "p_admin_email": actor_email or "",
            "p_admin_note": note or "",
        },
    )
    rows = result if isinstance(result, list) else ([result] if isinstance(result, dict) else [])
    device = rows[0] if rows else {}
    profile_id = device.get("user_profile_id")
    profile = get_user_profile_by_id(profile_id) if profile_id else None
    create_audit_log(
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_user_profile_id=profile_id,
        target_email=profile.get("email") if profile else "",
        action="user.device_revoked",
        details={"device_id": target_device_id, "note": note},
    )
    return {
        "device": device,
        "profile": profile,
        "devices": list_user_devices(profile_id) if profile_id else [],
    }


def update_user_profile(
    *,
    actor_user_id: Optional[str],
    actor_email: Optional[str],
    target_user_profile_id: str,
    email: str,
    full_name: str = "",
) -> Dict[str, Any]:
    profile = get_user_profile_by_id(target_user_profile_id)
    if not profile:
        raise AuthServiceError("User not found.")

    next_email = (email or "").strip().lower()
    next_name = (full_name or "").strip()
    if not next_email or "@" not in next_email:
        raise AuthServiceError("A valid email address is required.")

    if profile.get("auth_user_id") and next_email != (profile.get("email") or "").strip().lower():
        raise AuthServiceError("Email for Google-auth users is managed by sign-in and cannot be changed here.")

    existing_by_email = get_user_profile_by_email(next_email)
    if existing_by_email and existing_by_email.get("id") != target_user_profile_id:
        raise AuthServiceError("Another user already exists with that email.")

    payload = {
        "email": next_email,
        "full_name": next_name or next_email.split("@")[0].replace(".", " ").title(),
        "updated_at": _now_iso(),
    }

    rows = _rest_request(
        "PATCH",
        "user_profiles",
        params={"id": f"eq.{target_user_profile_id}"},
        json_payload=payload,
        prefer="return=representation",
    ) or []
    updated_profile = rows[0] if rows else get_user_profile_by_id(target_user_profile_id)
    if not updated_profile:
        raise AuthServiceError("Could not update the user profile.")

    create_audit_log(
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target_user_profile_id=target_user_profile_id,
        target_email=updated_profile.get("email"),
        action="user.profile_updated",
        details={"email": updated_profile.get("email"), "full_name": updated_profile.get("full_name")},
    )

    assignment = get_latest_assignment_for_profile(target_user_profile_id)
    return {
        "profile": updated_profile,
        "current_plan": assignment,
        "access_state": _coerce_access_state(updated_profile, assignment),
    }


def list_audit_logs(limit: int = 100) -> List[Dict[str, Any]]:
    try:
        return _rest_request(
            "GET",
            "admin_audit_logs",
            params={
                "select": "*",
                "order": "created_at.desc",
                "limit": str(max(1, min(limit, 250))),
            },
        ) or []
    except AuthServiceError:
        return []
