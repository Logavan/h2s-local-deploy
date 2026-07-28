# tests/test_hmac_auth.py
"""Unit tests for backend/hmac_auth.py."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
import unittest
from datetime import datetime, timezone

from flask import Flask

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import hmac_auth  # noqa: E402


class ComputeSignatureTests(unittest.TestCase):
    def test_signature_is_deterministic(self):
        key = b"supersecret"
        ts = "2026-07-28T12:00:00Z"
        nonce = "abcd1234"
        a = hmac_auth.compute_signature(key, ts, nonce, "POST", "/api/test", b"{}")
        b = hmac_auth.compute_signature(key, ts, nonce, "POST", "/api/test", b"{}")
        self.assertEqual(a, b)

    def test_signature_changes_with_method(self):
        key = b"supersecret"
        ts, nonce = "2026-07-28T12:00:00Z", "abcd1234"
        a = hmac_auth.compute_signature(key, ts, nonce, "POST", "/api/test", b"{}")
        b = hmac_auth.compute_signature(key, ts, nonce, "GET", "/api/test", b"{}")
        self.assertNotEqual(a, b)

    def test_signature_changes_with_body(self):
        key = b"supersecret"
        ts, nonce = "2026-07-28T12:00:00Z", "abcd1234"
        a = hmac_auth.compute_signature(key, ts, nonce, "POST", "/api/test", b'{"a":1}')
        b = hmac_auth.compute_signature(key, ts, nonce, "POST", "/api/test", b'{"a":2}')
        self.assertNotEqual(a, b)


class RouteClassificationTests(unittest.TestCase):
    def test_health_exempt(self):
        self.assertFalse(hmac_auth._route_requires_signature("/health"))
        self.assertFalse(hmac_auth._route_requires_signature("/health?foo=bar"))

    def test_status_exempt(self):
        self.assertFalse(hmac_auth._route_requires_signature("/api/status"))

    def test_hmac_key_endpoint_exempt(self):
        self.assertFalse(hmac_auth._route_requires_signature("/api/hmac/key"))

    def test_api_requires_signature(self):
        self.assertTrue(hmac_auth._route_requires_signature("/api/analyze"))
        self.assertTrue(hmac_auth._route_requires_signature("/api/start-conversion"))


class MiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        hmac_auth.install_hmac_middleware(self.app)

        @self.app.route("/api/test", methods=["POST"])
        def protected():
            return {"ok": True}

        @self.app.route("/health")
        def health():
            return {"ok": True}

        self.client = self.app.test_client()

    def _signed_headers(self, method, route, body, key):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce = "abcd1234efgh5678"
        sig = hmac_auth.compute_signature(
            key, ts, nonce, method, route, body.encode("utf-8"),
        )
        return {
            "X-H2S-Timestamp": ts,
            "X-H2S-Nonce": nonce,
            "X-H2S-Signature": sig,
        }

    def test_health_does_not_require_signature(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_protected_route_rejects_unsigned_request(self):
        resp = self.client.post("/api/test", json={})
        self.assertEqual(resp.status_code, 401)

    def test_protected_route_rejects_bad_signature(self):
        resp = self.client.post(
            "/api/test",
            json={},
            headers={
                "X-H2S-Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "X-H2S-Nonce": "abcd1234",
                "X-H2S-Signature": "bogus",
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_protected_route_rejects_old_timestamp(self):
        key = b"test-key"
        body = "{}"
        ts = "2020-01-01T00:00:00Z"  # way outside the 5-min window
        nonce = "abcd1234"
        sig = hmac_auth.compute_signature(key, ts, nonce, "POST", "/api/test", body.encode())
        resp = self.client.post(
            "/api/test",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-H2S-Timestamp": ts,
                "X-H2S-Nonce": nonce,
                "X-H2S-Signature": sig,
            },
        )
        self.assertEqual(resp.status_code, 401)

    def test_protected_route_accepts_valid_signature(self):
        # 32-byte key — realistic size; the middleware expects base64-encoded
        # values for env-var/file injection (raw 16+ byte keys get base64-decoded).
        key = b"test-key-that-is-32-bytes-longXX"  # 32 bytes
        body = '{"hello":"world"}'
        headers = self._signed_headers("POST", "/api/test", body, key)
        # Inject the key the middleware will read (base64-encoded)
        os.environ["H2S_HMAC_KEY"] = base64.b64encode(key).decode()
        try:
            resp = self.client.post(
                "/api/test",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    **headers,
                },
            )
            self.assertEqual(resp.status_code, 200)
        finally:
            os.environ.pop("H2S_HMAC_KEY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)