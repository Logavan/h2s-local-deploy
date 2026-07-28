# licensing/__init__.py
"""HANACV2SQL Enterprise license manager.

Public surface:

    fingerprint  — cross-platform machine ID collection
    license      — License dataclass + JSON load/save
    signer       — RSA-2048 sign / verify
    verifier     — startup gate: signature + expiry + machine binding
    exceptions   — typed errors for every failure mode
    cli          — `python -m licensing <subcommand>` tool

Typical use from flask_app.py:

    from licensing.verifier import check_or_exit
    check_or_exit()        # exits 1 if license is invalid
"""

from .exceptions import (
    BinaryIntegrityError,
    ExpiredLicenseError,
    FingerprintCollectionError,
    InvalidSignatureError,
    LicenseError,
    LicenseFormatError,
    LicenseNotFoundError,
    MachineMismatchError,
)
from .fingerprint import (
    Fingerprint,
    collect_identifiers,
    compute_fingerprint,
    is_container,
    short_hash,
)
from .license import (
    DEFAULT_LICENSE_FILENAME,
    License,
    canonical_payload_bytes,
    find_license_file,
    load_license,
    save_license,
)
from .signer import (
    generate_keypair,
    load_private_key,
    load_public_key,
    sign_payload,
    verify_payload,
)
from .verifier import (
    DEFAULT_PUBLIC_KEY_PATH,
    SKIP_ENV_VAR,
    VerificationResult,
    check_or_exit,
    quick_status,
    verify_or_raise,
)

__all__ = [
    # exceptions
    "LicenseError",
    "LicenseNotFoundError",
    "LicenseFormatError",
    "InvalidSignatureError",
    "ExpiredLicenseError",
    "MachineMismatchError",
    "FingerprintCollectionError",
    "BinaryIntegrityError",
    # fingerprint
    "Fingerprint",
    "collect_identifiers",
    "compute_fingerprint",
    "short_hash",
    "is_container",
    # license
    "License",
    "DEFAULT_LICENSE_FILENAME",
    "canonical_payload_bytes",
    "find_license_file",
    "load_license",
    "save_license",
    # signer
    "load_private_key",
    "load_public_key",
    "generate_keypair",
    "sign_payload",
    "verify_payload",
    # verifier
    "VerificationResult",
    "verify_or_raise",
    "check_or_exit",
    "quick_status",
    "DEFAULT_PUBLIC_KEY_PATH",
    "SKIP_ENV_VAR",
]