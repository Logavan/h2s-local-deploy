# licensing/exceptions.py
"""Exception hierarchy for the licensing module.

All licensing errors derive from LicenseError so callers can catch one
base type and still distinguish failure modes if needed.
"""

from __future__ import annotations


class LicenseError(Exception):
    """Base class for all licensing failures."""


class LicenseNotFoundError(LicenseError):
    """Raised when no license.json can be located on disk."""


class LicenseFormatError(LicenseError):
    """Raised when license.json is present but malformed (missing fields, bad JSON)."""


class InvalidSignatureError(LicenseError):
    """Raised when the RSA signature fails to verify against the bundled public key.

    Indicates tampering, corruption, or a license signed by a different vendor key.
    """


class ExpiredLicenseError(LicenseError):
    """Raised when the license's expires_at timestamp is in the past."""


class MachineMismatchError(LicenseError):
    """Raised when the current machine fingerprint does not match the bound machine_hash.

    This is the primary anti-copy signal — the license was issued for a different host.
    """


class FingerprintCollectionError(LicenseError):
    """Raised when none of the platform-specific fingerprint sources are available."""


class BinaryIntegrityError(LicenseError):
    """Raised when the running binary's SHA-256 does not match the license.

    Indicates the image has been modified or repackaged. Because
    binary_sha256 is part of the signed payload, it cannot be forged without
    invalidating the license signature.
    """


__all__ = [
    "LicenseError",
    "LicenseNotFoundError",
    "LicenseFormatError",
    "InvalidSignatureError",
    "ExpiredLicenseError",
    "MachineMismatchError",
    "FingerprintCollectionError",
    "BinaryIntegrityError",
]