# licensing/verifier.py
"""Startup license gate.

`verify_or_raise()` is the single entry point called by `flask_app.py`
before the app starts. It chains all checks in order:

    1. license file exists and parses
    2. signature verifies against the bundled public key
    3. expires_at is in the future
    4. current machine fingerprint matches the bound machine_hash

On any failure a specific exception is raised so the operator can
diagnose exactly what went wrong without leaking the signed payload.

The CLI (`python -m licensing verify`) calls the same code path with a
human-friendly summary.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import fingerprint as fp_mod
from .exceptions import (
    BinaryIntegrityError,
    ExpiredLicenseError,
    FingerprintCollectionError,
    LicenseError,
    LicenseFormatError,
    LicenseNotFoundError,
    MachineMismatchError,
)
from .license import (
    License,
    canonical_payload_bytes,
    find_license_file,
    load_license,
)
from .signer import load_public_key, verify_payload_or_raise

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_KEY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "keys", "public.pem"
)

# Allow opting out of the gate for local dev with H2S_SKIP_LICENSE=1
SKIP_ENV_VAR = "H2S_SKIP_LICENSE"


@dataclass
class VerificationResult:
    """Human-readable summary of a successful verification."""

    license_id: str
    customer: str
    expires_at: str
    days_remaining: int
    machine_hash_short: str
    is_container: bool


# ---------------------------------------------------------------------------
# Core check chain
# ---------------------------------------------------------------------------

def _resolve_public_key_path(explicit_path: Optional[str]) -> str:
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path
    env_path = os.environ.get("LICENSE_PUBLIC_KEY_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    if os.path.isfile(DEFAULT_PUBLIC_KEY_PATH):
        return DEFAULT_PUBLIC_KEY_PATH
    raise FileNotFoundError(
        f"Bundled public key not found at {DEFAULT_PUBLIC_KEY_PATH}. "
        "Re-install the licensing module or set LICENSE_PUBLIC_KEY_PATH."
    )


def verify_or_raise(
    license_path: Optional[str] = None,
    public_key_path: Optional[str] = None,
    *,
    skip_machine_check: bool = False,
    entrypoint: Optional[str] = None,
) -> VerificationResult:
    """Run the full verification chain. Raise on any failure.

    Args:
        license_path:        Optional override for the license.json location.
        public_key_path:     Optional override for the vendor public key.
        skip_machine_check:  Internal escape hatch for tests. NEVER True in
                             production — the verifier rejects the app on
                             mismatch in the normal path.
    """
    # 0. Operator escape hatch
    if os.environ.get(SKIP_ENV_VAR) in {"1", "true", "yes"}:
        logger.warning("License verification SKIPPED via %s", SKIP_ENV_VAR)
        return VerificationResult(
            license_id="SKIPPED",
            customer="n/a",
            expires_at="n/a",
            days_remaining=-1,
            machine_hash_short="n/a",
            is_container=fp_mod.is_container(),
        )

    # 1. Locate + parse
    real_path = license_path or find_license_file()
    license = load_license(real_path)

    # 2. Signature
    pub_key = load_public_key(_resolve_public_key_path(public_key_path))
    verify_payload_or_raise(
        canonical_payload_bytes(license),
        license.signature,
        pub_key,
    )

    # 3. Expiry
    if license.is_expired():
        raise ExpiredLicenseError(
            f"License {license.license_id} expired at {license.expires_at} "
            f"({abs(license.days_remaining())} day(s) ago). Renew at vendor."
        )

    # 4. Machine binding
    if not skip_machine_check:
        try:
            current = fp_mod.compute_fingerprint()
        except FingerprintCollectionError as exc:
            # Refusing to start is the correct response — empty fingerprint
            # would falsely match an empty hash in a license.
            raise
        if not _machine_hash_matches(license.machine_hash, current.full_hash):
            raise MachineMismatchError(
                "License is bound to a different machine. "
                f"License machine: {license.machine_hash[:8]}... "
                f"Current machine:  {current.short}... "
                "Run `python -m licensing request-rebind` to obtain a "
                "new license for this host."
            )

    # 5. Binary integrity — when the license carries binary_sha256, the
    #    running binary must match. Catches repackaging with RE'd code.
    if license.binary_sha256:
        running_hash = compute_running_binary_sha256(entrypoint=entrypoint)
        if running_hash is None:
            raise BinaryIntegrityError(
                "License requires binary_sha256 verification but the running "
                "binary's SHA-256 could not be computed. This usually means "
                "the license was issued for a different build (Nuitka vs "
                "interpreted). Re-issue with the correct build."
            )
        if running_hash.lower() != license.binary_sha256.strip().lower():
            raise BinaryIntegrityError(
                "Running binary SHA-256 does not match the license. The "
                "image has been modified or repackaged. Re-deploy the "
                "vendor-signed image to restore operation."
            )

    return VerificationResult(
        license_id=license.license_id,
        customer=license.customer,
        expires_at=license.expires_at,
        days_remaining=license.days_remaining(),
        machine_hash_short=license.machine_hash[:16].upper(),
        is_container=fp_mod.is_container(),
    )


def _machine_hash_matches(bound: str, current_full: str) -> bool:
    """Compare a stored (short or full) hash against a freshly computed one.

    Licenses may store either the full SHA-256 (newer) or just the short
    16-char prefix (legacy / human-friendly). Accept either.
    """
    bound_norm = bound.strip().lower()
    current_short = current_full[:16].lower()
    if len(bound_norm) == 16:
        return bound_norm == current_short
    return bound_norm == current_full.lower()


def compute_running_binary_sha256(entrypoint: Optional[str] = None) -> Optional[str]:
    """SHA-256 of the file backing the running entrypoint.

    Pass `entrypoint` explicitly when calling from a multi-file app — the
    caller knows its own `__file__` reliably, unlike `__main__.__file__`
    which can be unittest's runner / a different module under tooling.

    Returns None when the file cannot be resolved (e.g. inside a frozen
    binary where __file__ is not meaningful).
    """
    target = entrypoint
    if target is None:
        try:
            import __main__  # type: ignore
            target = getattr(__main__, "__file__", None)
        except Exception:
            target = None
    if not target:
        return None
    try:
        import hashlib
        h = hashlib.sha256()
        with open(target, "rb") as fh:
            for chunk in iter(lambda: fh.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Flask-friendly helpers
# ---------------------------------------------------------------------------

def check_or_exit(
    license_path: Optional[str] = None,
    entrypoint: Optional[str] = None,
) -> VerificationResult:
    """Verify or print a clean error and `sys.exit(1)`.

    Designed to be the first thing called inside `create_app()`.
    Never raises — converts every exception into a logged error + exit.

    Pass `entrypoint=__file__` from flask_app.py so binary integrity
    verification checks the actual application entrypoint rather than
    whatever __main__ happens to be (e.g. unittest's runner).
    """
    try:
        result = verify_or_raise(license_path=license_path, entrypoint=entrypoint)
    except LicenseError as exc:
        # Redact any signature detail from logs but keep the message short.
        logger.error("[LICENSE] %s: %s", type(exc).__name__, exc)
        print(f"\n❌  License check failed: {exc}\n", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as exc:
        logger.error("[LICENSE] %s", exc)
        print(f"\n❌  {exc}\n", file=sys.stderr)
        sys.exit(1)
    else:
        logger.info(
            "[LICENSE] OK — %s for %s (expires %s, %d day(s) remaining, host %s)",
            result.license_id,
            result.customer,
            result.expires_at,
            result.days_remaining,
            result.machine_hash_short,
        )
        return result


def quick_status() -> dict:
    """Return a /health-friendly dict without raising."""
    try:
        license = load_license()
        return {
            "status": "valid",
            "license_id": license.license_id,
            "customer": license.customer,
            "expires_at": license.expires_at,
            "days_remaining": license.days_remaining(),
            "machine_hash": license.machine_hash[:16].upper() + "...",
            "is_container": fp_mod.is_container(),
        }
    except LicenseNotFoundError:
        return {"status": "missing", "error": "license.json not found"}
    except LicenseFormatError as exc:
        return {"status": "malformed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — status only
        return {"status": "invalid", "error": str(exc)}


__all__ = [
    "VerificationResult",
    "verify_or_raise",
    "check_or_exit",
    "quick_status",
    "SKIP_ENV_VAR",
    "DEFAULT_PUBLIC_KEY_PATH",
]