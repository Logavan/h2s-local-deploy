# licensing/license.py
"""License dataclass + JSON load/save.

The on-disk format is a single JSON file (`license.json`) whose signed
fields are the canonical license metadata and whose `signature` field is
the base64-encoded RSA signature over the canonical JSON of every other
field. See `signer.py` for the signing/verification logic.

Schema (all fields except `signature` are part of the signed payload):

    {
      "license_id": "H2S-ENT-2026-XXXX-XXXX",
      "customer":   "Acme Corp",
      "issued_at":  "2026-07-28T00:00:00Z",
      "expires_at": "2027-07-28T00:00:00Z",
      "max_concurrent_users": 5,
      "machine_hash": "A73HF8X29P4QM0LR",
      "signature":   "<base64 RSA-2048 signature over canonical JSON>"
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .exceptions import LicenseFormatError, LicenseNotFoundError

REQUIRED_FIELDS = (
    "license_id",
    "customer",
    "issued_at",
    "expires_at",
    "max_concurrent_users",
    "machine_hash",
    "signature",
)

# Optional but recommended: when present, the signed payload also includes
# `binary_sha256` — the SHA-256 of the running binary. The verifier computes
# its own SHA-256 at startup and refuses to run if they differ. This stops
# customers from repackaging the binary with RE'd / modified code while
# keeping the original license.json intact.
OPTIONAL_FIELDS = (
    "binary_sha256",
    "image_digest",  # cosign-pinned image digest (sha256:...) — alternative to binary_sha256
    "secrets",       # signed blob of per-customer pre-authorized API keys (optional)
)

DEFAULT_LICENSE_FILENAME = "license.json"


@dataclass
class License:
    """Signed license metadata bound to a single machine."""

    license_id: str
    customer: str
    issued_at: str
    expires_at: str
    max_concurrent_users: int
    machine_hash: str
    signature: str = ""
    # Optional integrity fields. All participate in the signed payload when
    # present, so the customer cannot strip them out without invalidating
    # the signature.
    binary_sha256: Optional[str] = None
    image_digest: Optional[str] = None
    secrets: Optional[Dict[str, Any]] = None

    # --- helpers --------------------------------------------------------

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """True when expires_at is strictly before `now`."""
        ref = now or datetime.now(timezone.utc)
        try:
            exp = _parse_iso(self.expires_at)
        except ValueError:
            return True
        return exp <= ref

    def days_remaining(self, now: Optional[datetime] = None) -> int:
        """Whole days until expiry (negative if already expired)."""
        ref = now or datetime.now(timezone.utc)
        try:
            exp = _parse_iso(self.expires_at)
        except ValueError:
            return -1
        delta = exp - ref
        return int(delta.total_seconds() // 86400)

    def signed_payload(self) -> Dict[str, Any]:
        """Return the dict that was — or will be — signed.

        Excludes `signature` itself so signing and verification agree on
        the exact byte sequence being protected.
        """
        d = asdict(self)
        d.pop("signature", None)
        return d

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # --- factories ------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "License":
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise LicenseFormatError(f"Missing required license fields: {missing}")
        try:
            max_users = int(data["max_concurrent_users"])
        except (TypeError, ValueError) as exc:
            raise LicenseFormatError("max_concurrent_users must be an integer") from exc
        return cls(
            license_id=str(data["license_id"]),
            customer=str(data["customer"]),
            issued_at=str(data["issued_at"]),
            expires_at=str(data["expires_at"]),
            max_concurrent_users=max_users,
            machine_hash=str(data["machine_hash"]),
            signature=str(data["signature"]),
            binary_sha256=data.get("binary_sha256"),
            image_digest=data.get("image_digest"),
            secrets=data.get("secrets"),
        )


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

def _canonical_json(payload: Dict[str, Any]) -> bytes:
    """Stable JSON encoding used for both signing and verification."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_payload_bytes(license: License) -> bytes:
    """Return the exact bytes that should be signed/verified for this license."""
    return _canonical_json(license.signed_payload())


def find_license_file(explicit_path: Optional[str] = None) -> str:
    """Locate a license.json on disk.

    Lookup order:
        1. explicit_path arg
        2. LICENSE_PATH env var
        3. ./license.json (cwd)
        4. <licensing_pkg_parent>/license.json (alongside the app)

    Raises LicenseNotFoundError when none of these resolve.
    """
    candidates: List[str] = []
    if explicit_path:
        candidates.append(explicit_path)
    env_path = os.environ.get("LICENSE_PATH")
    if env_path:
        candidates.append(env_path)
    candidates.append(os.path.join(os.getcwd(), DEFAULT_LICENSE_FILENAME))
    # Two directories up from licensing/license.py → backend/
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(pkg_root, DEFAULT_LICENSE_FILENAME))

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise LicenseNotFoundError(
        f"No license file found. Looked in: {candidates}. "
        f"Set LICENSE_PATH or place license.json next to flask_app.py."
    )


def load_license(path: Optional[str] = None) -> License:
    """Read + parse a license.json from disk."""
    real_path = path or find_license_file()
    try:
        with open(real_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise LicenseFormatError(f"license.json is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise LicenseNotFoundError(f"Could not read license file: {exc}") from exc
    return License.from_dict(data)


def save_license(license: License, path: str) -> None:
    """Write a license to disk as pretty-printed JSON."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(license.to_dict(), fh, indent=2, sort_keys=True)
        fh.write("\n")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z'."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


__all__ = [
    "License",
    "REQUIRED_FIELDS",
    "DEFAULT_LICENSE_FILENAME",
    "canonical_payload_bytes",
    "find_license_file",
    "load_license",
    "save_license",
]