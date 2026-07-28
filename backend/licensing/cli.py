# licensing/cli.py
"""Vendor + admin CLI for the licensing module.

Run as `python -m licensing <subcommand>` from the backend/ directory.
Each subcommand returns an int suitable for sys.exit().

Subcommands:
    info              — print this machine's fingerprint + current license status
    verify            — re-run the full verification chain
    request-rebind    — output a signed rebind request file for the vendor
    apply <path>      — install a new signed license.json (admin only)
    sign <license.json> — vendor-only: sign a license with keys/private.pem
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sys
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from . import fingerprint as fp_mod
from .exceptions import LicenseError
from .license import License, canonical_payload_bytes, load_license, save_license
from .signer import (
    generate_keypair,
    load_private_key,
    load_public_key,
    sign_payload,
    verify_payload,
)
from .verifier import check_or_exit, quick_status, verify_or_raise


HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_DIR = os.path.join(HERE, "keys")
DEFAULT_PRIVATE_KEY = os.path.join(KEYS_DIR, "private.pem")
DEFAULT_PUBLIC_KEY = os.path.join(KEYS_DIR, "public.pem")


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_info(_args: argparse.Namespace) -> int:
    """Show this machine's fingerprint + the currently installed license."""
    print("=== Machine Fingerprint ===")
    print(f"Platform: {fp_mod.platform.system()}")
    print(f"Is container: {fp_mod.is_container()}")

    try:
        fp = fp_mod.compute_fingerprint()
    except fp_mod.FingerprintCollectionError as exc:
        print(f"[ERROR] Could not compute fingerprint: {exc}")
        return 2

    print(f"Fingerprint (full): {fp.full_hash}")
    print(f"Fingerprint (short): {fp.short}")
    print("\nIdentifiers collected:")
    for k, v in fp.identifiers.items():
        print(f"  {k:15s} = {v}")

    print("\n=== License Status ===")
    status = quick_status()
    print(json.dumps(status, indent=2))
    return 0 if status.get("status") == "valid" else 1


def cmd_verify(_args: argparse.Namespace) -> int:
    """Re-run the full verification chain (same code path as startup)."""
    try:
        result = verify_or_raise()
    except LicenseError as exc:
        print(f"[FAIL] License verification failed: {type(exc).__name__}: {exc}")
        return 1
    print("[OK] License is valid")
    print(json.dumps(result.__dict__, indent=2))
    return 0


def cmd_request_rebind(args: argparse.Namespace) -> int:
    """Write a signed rebind request the customer sends to the vendor."""
    try:
        current = fp_mod.compute_fingerprint()
    except fp_mod.FingerprintCollectionError as exc:
        print(f"[ERROR] {exc}")
        return 2

    payload = {
        "request_type": "license_rebind",
        "license_id": args.license_id,
        "previous_machine_hash": args.previous_machine_hash,
        "requested_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "new_fingerprint": {
            "full_hash": current.full_hash,
            "short": current.short,
            "platform": current.platform,
            "is_container": current.is_container,
            "identifiers": current.identifiers,
        },
    }

    out_path = args.output or f"rebind_request_{current.short}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    print(f"[OK] Rebind request written to {out_path}")
    print("    Email this file to the vendor to receive a new signed license.")
    print(f"    New machine fingerprint: {current.full_hash} ({current.short})")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Install a vendor-supplied license.json (admin only)."""
    if not os.path.isfile(args.path):
        print(f"[ERROR] License file not found: {args.path}")
        return 2
    try:
        new_license = load_license(args.path)
    except LicenseError as exc:
        print(f"[ERROR] License file is invalid: {exc}")
        return 2

    # Verify signature with bundled public key before installing
    pub = load_public_key(DEFAULT_PUBLIC_KEY)
    if not verify_payload(
        canonical_payload_bytes(new_license),
        new_license.signature,
        pub,
    ):
        print("[FAIL] License signature is invalid — refusing to install.")
        return 1

    target = args.target or os.path.join(
        os.path.dirname(os.path.dirname(HERE)), "license.json"
    )
    save_license(new_license, target)
    print(f"[OK] License installed at {target}")
    print(f"    License ID : {new_license.license_id}")
    print(f"    Customer   : {new_license.customer}")
    print(f"    Expires    : {new_license.expires_at}")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    """Vendor-only: sign a license.json using keys/private.pem."""
    if not os.path.isfile(args.license):
        print(f"[ERROR] License file not found: {args.license}")
        return 2
    if not os.path.isfile(args.private_key):
        print(f"[ERROR] Private key not found: {args.private_key}")
        return 2

    license_obj = load_license(args.license)
    # Force re-sign: clear existing signature then sign the canonical payload
    license_obj.signature = ""
    priv = load_private_key(args.private_key)
    sig = sign_payload(canonical_payload_bytes(license_obj), priv)
    license_obj.signature = sig
    save_license(license_obj, args.license)
    print(f"[OK] Signed license written back to {args.license}")
    print(f"    License ID : {license_obj.license_id}")
    print(f"    Customer   : {license_obj.customer}")
    print(f"    Signature  : {sig[:32]}...")
    return 0


def cmd_generate_keys(args: argparse.Namespace) -> int:
    """Vendor-only: create a fresh RSA keypair for signing."""
    out_dir = args.out_dir or KEYS_DIR
    priv, pub = generate_keypair(out_dir)
    print(f"[OK] Keypair generated:")
    print(f"    private: {priv}  (DO NOT COMMIT — already chmod 600)")
    print(f"    public : {pub}  (bundle this with the app)")
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m licensing",
        description="HANACV2SQL Enterprise license manager",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="show machine fingerprint + license status")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("verify", help="re-run the full license verification chain")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("request-rebind", help="generate a signed rebind request")
    p.add_argument(
        "--license-id",
        default=os.environ.get("H2S_LICENSE_ID", "UNKNOWN"),
        help="current license ID (from existing license.json)",
    )
    p.add_argument(
        "--previous-machine-hash",
        default=os.environ.get("H2S_PREV_MACHINE_HASH", "UNKNOWN"),
        help="machine hash from the license that's no longer valid",
    )
    p.add_argument(
        "--output",
        default=None,
        help="path to write the rebind request (default: rebind_request_<short>.json)",
    )
    p.set_defaults(func=cmd_request_rebind)

    p = sub.add_parser("apply", help="install a vendor-supplied license.json")
    p.add_argument("path", help="path to the vendor-supplied license.json")
    p.add_argument(
        "--target",
        default=None,
        help="destination path (default: <backend>/license.json)",
    )
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("sign", help="vendor: sign a license with the private key")
    p.add_argument("license", help="path to license.json (will be updated in place)")
    p.add_argument(
        "--private-key",
        default=DEFAULT_PRIVATE_KEY,
        help=f"path to vendor private key (default: {DEFAULT_PRIVATE_KEY})",
    )
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("generate-keys", help="vendor: create a new RSA keypair")
    p.add_argument(
        "--out-dir",
        default=None,
        help=f"output directory (default: {KEYS_DIR})",
    )
    p.set_defaults(func=cmd_generate_keys)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except LicenseError as exc:
        print(f"❌  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())