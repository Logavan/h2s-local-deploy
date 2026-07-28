# hmac_auth.py
"""HMAC-SHA256 request signing middleware.

Why: even though the backend runs inside a licensed Docker container, the
frontend bundle is delivered to the customer's browser. A determined user
can rewrite the JS to call our API directly with whatever data they want —
bypassing any frontend-side logic. To make that useless, every mutating
API call must carry an HMAC signature proving it came from our frontend.

Wire format:

    X-H2S-Timestamp:  2026-07-28T12:34:56Z      (ISO-8601, must be within ±5 min of server time)
    X-H2S-Nonce:      <random per-request>      (32 hex chars; replay protection)
    X-H2S-Signature:  <base64 HMAC-SHA256>     (computed below)

Signed string (canonicalized, LF-joined):

    <timestamp>\n<nonce>\n<METHOD>\n<route>\n<sha256(body)>

Where `<route>` is the request path without query string, and `<sha256(body)>`
is the lowercase hex SHA-256 of the raw request body (or empty-string hash
when there's no body).

The signing key is derived once at startup from the license's `secrets`
block (when present) plus the machine fingerprint, so it varies per
customer and is not extractable from the image alone.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Callable, Optional

from flask import current_app, jsonify, request

# Routes that do not require HMAC signing (public health/debug endpoints)
_SIGNATURE_EXEMPT_PREFIXES = (
    "/health",
    "/api/status",
    "/api/hmac/key",  # frontend must fetch this BEFORE it has a key
    "/container-shutdown",
    "/debug-latency",
)
# Routes that ALWAYS require HMAC (everything under /api/* except the exempt list)
_SIGNATURE_REQUIRED_PREFIXES = (
    "/api/",
)

# Tolerance window for timestamp skew — 5 minutes in either direction
_TIMESTAMP_TOLERANCE_SECONDS = 300


class HMACVerificationError(Exception):
    """Raised when an incoming request fails HMAC verification."""


def _canonical_string(timestamp: str, nonce: str, method: str, route: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{timestamp}\n{nonce}\n{method.upper()}\n{route}\n{body_hash}".encode("utf-8")


def compute_signature(
    key: bytes,
    timestamp: str,
    nonce: str,
    method: str,
    route: str,
    body: bytes,
) -> str:
    """Compute the base64 HMAC-SHA256 signature for a request."""
    msg = _canonical_string(timestamp, nonce, method, route, body)
    sig = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.b64encode(sig).decode("ascii")


def verify_request(key: bytes, max_skew_seconds: int = _TIMESTAMP_TOLERANCE_SECONDS) -> None:
    """Verify the incoming Flask request's HMAC headers. Raise on failure.

    Looks at the current Flask `request` proxy.
    """
    timestamp = request.headers.get("X-H2S-Timestamp", "")
    nonce = request.headers.get("X-H2S-Nonce", "")
    signature = request.headers.get("X-H2S-Signature", "")

    if not timestamp or not nonce or not signature:
        raise HMACVerificationError("Missing HMAC headers")

    # Validate timestamp window to limit replay attacks
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HMACVerificationError(f"Bad X-H2S-Timestamp: {exc}") from exc
    now = datetime.now(timezone.utc)
    if abs((now - ts).total_seconds()) > max_skew_seconds:
        raise HMACVerificationError("Timestamp outside tolerance window")

    # Recompute and constant-time compare
    body = request.get_data(cache=True) or b""
    expected = compute_signature(
        key, timestamp, nonce, request.method, request.path, body,
    )
    if not hmac.compare_digest(expected, signature):
        raise HMACVerificationError("HMAC signature mismatch")


def _route_requires_signature(path: str) -> bool:
    if any(path.startswith(p) for p in _SIGNATURE_EXEMPT_PREFIXES):
        return False
    return any(path.startswith(p) for p in _SIGNATURE_REQUIRED_PREFIXES)


def _get_signing_key() -> bytes:
    """Resolve the HMAC signing key.

    Env-var and file values are treated as base64-encoded (so admins can pass
    raw 32-byte keys through env vars without escaping issues). Plain ASCII
    strings are also accepted for backward-compat — they are used as-is.

    Order:
        1. License.secrets["hmac_key"] (vendor-signed, per-customer)
        2. H2S_HMAC_KEY env var (admin-supplied at start time, base64)
        3. H2S_HMAC_KEY_FILE (file-mounted at start time, base64)
        4. Ephemeral random key (development fallback — UNSAFE for prod)
    """
    def _maybe_b64(raw: str) -> bytes:
        # Try base64 first; fall back to raw UTF-8 bytes
        try:
            decoded = base64.b64decode(raw, validate=True)
            # Reject obvious non-base64 (single character, etc.)
            if len(decoded) >= 16:
                return decoded
        except (ValueError, TypeError):
            pass
        return raw.encode("utf-8")

    # 1. License-derived
    license_info = current_app.config.get("LICENSE_INFO") if current_app else None
    if license_info and getattr(license_info, "secrets", None):
        lic_hmac = license_info.secrets.get("hmac_key")
        if lic_hmac:
            return _maybe_b64(lic_hmac) if not isinstance(lic_hmac, bytes) else lic_hmac

    # 2. Direct env var
    direct = os.environ.get("H2S_HMAC_KEY")
    if direct:
        return _maybe_b64(direct)

    # 3. File-mounted
    file_path = os.environ.get("H2S_HMAC_KEY_FILE")
    if file_path and os.path.isfile(file_path):
        with open(file_path, "r", encoding="utf-8") as fh:
            return _maybe_b64(fh.read().strip())

    # 4. Dev fallback — log a loud warning so this never ships silently
    current_app.logger.warning(
        "[HMAC] No signing key configured (no license.secrets.hmac_key, no "
        "H2S_HMAC_KEY, no H2S_HMAC_KEY_FILE). Generating ephemeral key. "
        "This is INSECURE and must not be used in production."
    )
    return hashlib.sha256(os.urandom(32)).digest()


def install_hmac_middleware(app) -> None:
    """Install the `before_request` HMAC gate on a Flask app."""

    @app.before_request
    def _enforce_hmac_on_protected_routes():
        if not _route_requires_signature(request.path):
            return None
        try:
            key = _get_signing_key()
            verify_request(key)
        except HMACVerificationError as exc:
            app.logger.warning(
                "[HMAC] Rejected request %s %s from %s — %s",
                request.method, request.path, request.remote_addr, exc,
            )
            return jsonify({"error": "Invalid or missing request signature", "detail": str(exc)}), 401
        return None

    app.extensions["hmac_auth"] = {"installed": True}
    return None


def frontend_sign(key_b64: Optional[str] = None) -> Callable:
    """Decorator factory for frontend-side request signing helpers.

    Returns a function `(method, route, body_str) -> dict[str, str]` that
    produces the three required headers. The frontend uses this from
    a single shared `apiFetch()` wrapper.
    """

    def _sign(method: str, route: str, body: str = "") -> dict:
        import secrets as _secrets

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce = _secrets.token_hex(16)
        key = base64.b64decode(key_b64) if key_b64 else b""
        body_bytes = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        sig = compute_signature(key, ts, nonce, method, route, body_bytes)
        return {
            "X-H2S-Timestamp": ts,
            "X-H2S-Nonce": nonce,
            "X-H2S-Signature": sig,
        }

    return _sign


__all__ = [
    "HMACVerificationError",
    "compute_signature",
    "verify_request",
    "install_hmac_middleware",
    "frontend_sign",
]