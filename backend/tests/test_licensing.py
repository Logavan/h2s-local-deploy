# tests/test_licensing.py
"""Unit + integration tests for the licensing module.

Run with:
    cd backend && python -m pytest tests/test_licensing.py -v
or simply:
    cd backend && python tests/test_licensing.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

# Make `backend/` importable when running this file directly
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from licensing import fingerprint as fp_mod          # noqa: E402
from licensing.exceptions import (                  # noqa: E402
    BinaryIntegrityError,
    ExpiredLicenseError,
    InvalidSignatureError,
    MachineMismatchError,
)
from licensing.license import (                      # noqa: E402
    License,
    canonical_payload_bytes,
    load_license,
    save_license,
)
from licensing.signer import (                       # noqa: E402
    generate_keypair,
    load_public_key,
    sign_payload,
    verify_payload,
    verify_payload_or_raise,
)
from licensing.verifier import verify_or_raise       # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_keypair(tmp: str) -> tuple[str, str]:
    return generate_keypair(tmp)


def _make_signed_license(
    tmp: str,
    priv_path: str,
    *,
    machine_hash: str,
    expired: bool = False,
    binary_sha256: str | None = None,
) -> str:
    """Create a signed license.json in `tmp` and return its path."""
    from licensing.signer import load_private_key

    license = License(
        license_id="H2S-ENT-TEST-0001",
        customer="Test Customer",
        issued_at="2026-01-01T00:00:00Z",
        expires_at="2099-12-31T00:00:00Z" if not expired else "2020-01-01T00:00:00Z",
        max_concurrent_users=5,
        machine_hash=machine_hash,
        binary_sha256=binary_sha256,
        signature="",
    )
    license.signature = sign_payload(
        canonical_payload_bytes(license), load_private_key(priv_path)
    )
    path = os.path.join(tmp, "license.json")
    save_license(license, path)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class FingerprintTests(unittest.TestCase):
    def test_identifiers_are_dict(self):
        ids = fp_mod.collect_identifiers()
        self.assertIsInstance(ids, dict)
        self.assertGreater(len(ids), 0, "expected at least one identifier")

    def test_compute_is_deterministic(self):
        a = fp_mod.compute_fingerprint()
        b = fp_mod.compute_fingerprint()
        self.assertEqual(a.full_hash, b.full_hash)
        self.assertEqual(a.short, b.short)

    def test_short_is_16_chars(self):
        fp = fp_mod.compute_fingerprint()
        self.assertEqual(len(fp.short), 16)
        self.assertEqual(len(fp.full_hash), 64)

    def test_host_machine_id_takes_priority_over_container_id(self):
        """When host machine-id is bind-mounted into the container, it should
        appear in the identifiers as `host_machine_id` and the container_id
        should be treated as a weaker (fallback) signal.
        """
        with tempfile.TemporaryDirectory() as tmp:
            host_id_path = os.path.join(tmp, "machine-id")
            with open(host_id_path, "w", encoding="utf-8") as fh:
                fh.write("deadbeefcafe00000000deadbeefcafe\n")
            with patch.dict(os.environ, {"LICENSE_HOST_MACHINE_ID_PATH": host_id_path}):
                # Simulate a container so the host-bind path is consulted
                with patch.object(fp_mod, "is_container", return_value=True):
                    ids = fp_mod.collect_identifiers()
                    self.assertIn("host_machine_id", ids)
                    self.assertEqual(ids["host_machine_id"], "deadbeefcafe00000000deadbeefcafe")

    def test_different_host_machine_id_produces_different_fingerprint(self):
        """Two VMs with different host machine-ids must produce different
        fingerprints — that's the whole point of host binding.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path_a = os.path.join(tmp, "id_a")
            path_b = os.path.join(tmp, "id_b")
            with open(path_a, "w", encoding="utf-8") as fh:
                fh.write("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
            with open(path_b, "w", encoding="utf-8") as fh:
                fh.write("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n")

            with patch.dict(os.environ, {"LICENSE_HOST_MACHINE_ID_PATH": path_a}):
                with patch.object(fp_mod, "is_container", return_value=True):
                    fp_a = fp_mod.compute_fingerprint()
            with patch.dict(os.environ, {"LICENSE_HOST_MACHINE_ID_PATH": path_b}):
                with patch.object(fp_mod, "is_container", return_value=True):
                    fp_b = fp_mod.compute_fingerprint()
            self.assertNotEqual(fp_a.full_hash, fp_b.full_hash)
            self.assertNotEqual(fp_a.short, fp_b.short)


class SignerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.priv, self.pub = _make_keypair(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip(self):
        priv = __import__("licensing.signer", fromlist=["load_private_key"]).load_private_key(self.priv)
        msg = b"hello world"
        sig = sign_payload(msg, priv)
        pub = load_public_key(self.pub)
        self.assertTrue(verify_payload(msg, sig, pub))

    def test_bad_signature_fails(self):
        priv = __import__("licensing.signer", fromlist=["load_private_key"]).load_private_key(self.priv)
        sig = sign_payload(b"original", priv)
        pub = load_public_key(self.pub)
        self.assertFalse(verify_payload(b"tampered", sig, pub))

    def test_verify_raises(self):
        priv = __import__("licensing.signer", fromlist=["load_private_key"]).load_private_key(self.priv)
        sig = sign_payload(b"original", priv)
        pub = load_public_key(self.pub)
        with self.assertRaises(InvalidSignatureError):
            verify_payload_or_raise(b"tampered", sig, pub)


class LicenseRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.priv, self.pub = _make_keypair(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_signed_license_verifies(self):
        path = _make_signed_license(self.tmp, self.priv, machine_hash="ABCDEF1234567890")
        lic = load_license(path)
        pub = load_public_key(self.pub)
        self.assertTrue(
            verify_payload(canonical_payload_bytes(lic), lic.signature, pub)
        )

    def test_tampered_license_fails_signature(self):
        path = _make_signed_license(self.tmp, self.priv, machine_hash="ABCDEF1234567890")
        # Tamper with the customer field after signing
        with open(path, "r+", encoding="utf-8") as fh:
            data = json.load(fh)
            data["customer"] = "Evil Corp"
            fh.seek(0)
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.truncate()
        lic = load_license(path)
        pub = load_public_key(self.pub)
        self.assertFalse(
            verify_payload(canonical_payload_bytes(lic), lic.signature, pub)
        )


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.priv, self.pub = _make_keypair(self.tmp)
        # Patch the bundled key path to point at our test public key
        self._real_default = os.environ.get("LICENSE_PUBLIC_KEY_PATH")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._real_default is None:
            os.environ.pop("LICENSE_PUBLIC_KEY_PATH", None)
        else:
            os.environ["LICENSE_PUBLIC_KEY_PATH"] = self._real_default

    def test_happy_path_with_mocked_fingerprint(self):
        os.environ["LICENSE_PUBLIC_KEY_PATH"] = self.pub
        path = _make_signed_license(self.tmp, self.priv, machine_hash="abc123")
        # Skip machine check since we can't easily fake the host fingerprint here
        result = verify_or_raise(license_path=path, skip_machine_check=True)
        self.assertEqual(result.license_id, "H2S-ENT-TEST-0001")
        self.assertGreater(result.days_remaining, 0)

    def test_expired_license_rejected(self):
        os.environ["LICENSE_PUBLIC_KEY_PATH"] = self.pub
        path = _make_signed_license(self.tmp, self.priv, machine_hash="abc123", expired=True)
        with self.assertRaises(ExpiredLicenseError):
            verify_or_raise(license_path=path, skip_machine_check=True)

    def test_machine_mismatch_rejected(self):
        os.environ["LICENSE_PUBLIC_KEY_PATH"] = self.pub
        # Sign with a fake machine hash, then provide a different one at runtime
        path = _make_signed_license(self.tmp, self.priv, machine_hash="0000000000000000")
        fake_ids = {"machine_id": "deadbeefcafe", "mac": "aa:bb:cc:dd:ee:ff",
                    "hostname": "test", "os": "linux test", "cpu": "test"}
        with patch.object(fp_mod, "compute_fingerprint", return_value=fp_mod.Fingerprint(
            full_hash="1111111111111111" + "0" * 48, identifiers=fake_ids, platform="Linux"
        )):
            with self.assertRaises(MachineMismatchError):
                verify_or_raise(license_path=path, skip_machine_check=False)

    def test_binary_integrity_match_passes(self):
        """License with binary_sha256 matching the running test binary passes."""
        import hashlib
        # Use this test file's path — explicit entrypoint makes the check
        # robust against unittest's __main__ being the runner, not this file.
        sha = hashlib.sha256(open(__file__, "rb").read()).hexdigest()
        os.environ["LICENSE_PUBLIC_KEY_PATH"] = self.pub
        path = _make_signed_license(self.tmp, self.priv,
                                    machine_hash="abc123",
                                    binary_sha256=sha)
        result = verify_or_raise(
            license_path=path, skip_machine_check=True, entrypoint=__file__,
        )
        self.assertEqual(result.license_id, "H2S-ENT-TEST-0001")

    def test_binary_integrity_mismatch_rejected(self):
        """License with wrong binary_sha256 must fail."""
        os.environ["LICENSE_PUBLIC_KEY_PATH"] = self.pub
        path = _make_signed_license(self.tmp, self.priv,
                                    machine_hash="abc123",
                                    binary_sha256="0" * 64)
        with self.assertRaises(BinaryIntegrityError):
            verify_or_raise(
                license_path=path, skip_machine_check=True, entrypoint=__file__,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)