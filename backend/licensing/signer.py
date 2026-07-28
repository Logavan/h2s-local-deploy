# licensing/signer.py
"""RSA-2048 sign / verify for license.json.

The vendor holds `private.pem` offline. The app bundles `public.pem` and
uses it to verify every license it loads. We use PKCS#1v1.5 with SHA-256
because it's the broadest-supported padding across Python runtimes and
HSMs.

Public API:

    sign_license_payload(payload_bytes, private_key_path) -> str  # base64 sig
    verify_license_payload(payload_bytes, signature_b64, public_key_path) -> bool
    load_private_key(path, password=None) -> RSAPrivateKey
    load_public_key(path) -> RSAPublicKey
    generate_keypair(out_dir, key_size=2048) -> (private_path, public_path)
"""

from __future__ import annotations

import base64
import os
from typing import Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    RSAPublicKey,
)

from .exceptions import InvalidSignatureError


# ---------------------------------------------------------------------------
# Key I/O
# ---------------------------------------------------------------------------

def load_private_key(path: str, password: Optional[bytes] = None) -> RSAPrivateKey:
    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=password)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError(f"{path} does not contain an RSA private key")
    return key


def load_public_key(path: str) -> RSAPublicKey:
    with open(path, "rb") as fh:
        key = serialization.load_pem_public_key(fh.read())
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError(f"{path} does not contain an RSA public key")
    return key


def generate_keypair(out_dir: str, key_size: int = 2048) -> Tuple[str, str]:
    """Create a fresh RSA keypair. Returns (private_path, public_path).

    Used by the vendor onboarding script — never called by the app at runtime.
    """
    os.makedirs(out_dir, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path = os.path.join(out_dir, "private.pem")
    pub_path = os.path.join(out_dir, "public.pem")
    with open(priv_path, "wb") as fh:
        fh.write(private_pem)
    with open(pub_path, "wb") as fh:
        fh.write(public_pem)
    # private.pem should NEVER be world-readable on shared systems
    try:
        os.chmod(priv_path, 0o600)
    except OSError:
        pass
    return priv_path, pub_path


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------

def sign_payload(payload_bytes: bytes, private_key: RSAPrivateKey) -> str:
    """Return base64-encoded signature over `payload_bytes`."""
    sig = private_key.sign(
        payload_bytes,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")


def verify_payload(
    payload_bytes: bytes,
    signature_b64: str,
    public_key: RSAPublicKey,
) -> bool:
    """Return True iff `signature_b64` is a valid signature over `payload_bytes`.

    Never raises on a bad signature — returns False so callers can decide
    whether to log / re-raise / treat as license error.
    """
    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError):
        return False
    try:
        public_key.verify(
            sig,
            payload_bytes,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def verify_payload_or_raise(
    payload_bytes: bytes,
    signature_b64: str,
    public_key: RSAPublicKey,
) -> None:
    """Like verify_payload but raises InvalidSignatureError on mismatch."""
    if not verify_payload(payload_bytes, signature_b64, public_key):
        raise InvalidSignatureError(
            "License signature verification failed — license is tampered or "
            "signed by an unknown key."
        )


__all__ = [
    "load_private_key",
    "load_public_key",
    "generate_keypair",
    "sign_payload",
    "verify_payload",
    "verify_payload_or_raise",
]