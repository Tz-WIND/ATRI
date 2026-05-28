"""Shared constants and utilities for dashboard route modules."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import unquote

AUTH_COOKIE = "atri_dashboard_session"

AUTH_EXEMPT_API_PATHS = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/logout",
    "/api/config/schema",
}

LOCAL_BRIDGE_API_PATHS = {
    "/api/agent-mode",
    "/api/chat/cancel",
    "/api/daw-agent/chat",
    "/api/status",
    "/api/provider/select",
    "/api/music/studio/project",
    "/api/music/studio/export",
    "/api/music/studio/bridge/status",
    "/api/music/studio/bridge/export",
    "/api/music/studio/bridge/export/latest",
}

DASHBOARD_CSP = (
    "default-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "worker-src 'self' blob:; "
    "connect-src 'self' ws: wss:; "
    "img-src 'self' data: blob:"
)

# ── Rate limiting ──

_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX_FAILURES = 10
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(ip: str) -> bool:
    """Return True if the IP is rate-limited."""
    now = time.time()
    bucket = _rate_limit_buckets[ip]
    bucket[:] = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW]
    return len(bucket) >= _RATE_LIMIT_MAX_FAILURES


def record_failure(ip: str) -> None:
    _rate_limit_buckets[ip].append(time.time())


# ── Password hashing (PBKDF2) ──

PBKDF2_PREFIX = "pbkdf2:"
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_HASH = "sha256"
_PBKDF2_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256, returning a storable string."""
    salt = os.urandom(_PBKDF2_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_HASH, password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{PBKDF2_PREFIX}{salt.hex()}${dk.hex()}"


def verify_password(stored: str, candidate: str) -> bool:
    """Verify a password against a hashed or legacy plaintext storage string."""
    if not stored or not candidate:
        return False
    if stored.startswith(PBKDF2_PREFIX):
        try:
            prefixed_salt, dk_hex = stored.split("$", 1)
        except ValueError:
            return False
        salt_hex = prefixed_salt[len(PBKDF2_PREFIX) :]
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        dk = hashlib.pbkdf2_hmac(_PBKDF2_HASH, candidate.encode(), salt, _PBKDF2_ITERATIONS)
        return hmac.compare_digest(dk, expected)
    # Legacy plaintext comparison — upgrade on next successful login
    return hmac.compare_digest(stored, candidate)


# ── Misc helpers ──


def cookie_value(cookie_header: str, name: str) -> str:
    if not cookie_header:
        return ""
    try:
        cookies = SimpleCookie(cookie_header)
    except Exception:
        return ""
    morsel = cookies.get(name)
    return morsel.value if morsel else ""


def parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_loopback_address(address: str | None) -> bool:
    value = str(address or "").strip().lower()
    if not value:
        return False
    if value.startswith("::ffff:"):
        value = value.removeprefix("::ffff:")
    return value in {"127.0.0.1", "::1", "localhost"}


def request_from_loopback(request_obj: Any) -> bool:
    return is_loopback_address(getattr(request_obj, "remote_addr", ""))


def local_bridge_api_allowed(path: str, request_obj: Any) -> bool:
    if not request_from_loopback(request_obj):
        return False
    if path in LOCAL_BRIDGE_API_PATHS:
        return True
    if str(getattr(request_obj, "method", "")).upper() == "GET" and path.startswith(
        "/api/sessions/"
    ):
        session_id = unquote(path.removeprefix("/api/sessions/"))
        return session_id.startswith("daw_agent:friend:")
    return False


def websocket_from_loopback(websocket_obj: Any) -> bool:
    scope = getattr(websocket_obj, "scope", {}) or {}
    client = scope.get("client") if isinstance(scope, dict) else None
    if isinstance(client, (tuple, list)) and client:
        return is_loopback_address(str(client[0]))
    return is_loopback_address(getattr(websocket_obj, "remote_addr", ""))


def resolve_workspace_path(workspace: str, rel_path: str) -> tuple[Path, Path]:
    ws = Path(workspace or ".").resolve()
    target = (ws / (rel_path or "")).resolve()
    try:
        target.relative_to(ws)
    except ValueError as e:
        raise PermissionError("path outside workspace") from e
    return ws, target
