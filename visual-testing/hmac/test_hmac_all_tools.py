#!/usr/bin/env python3
"""Cross-tool HMAC verification test.

Exercises every endpoint in the Converter / Mapper / Flattener tools with two
modes per endpoint:

  - unsigned:    no X-H2S-* headers. Expected: 401 if HMAC-required,
                 anything else (200/400/404/500) if HMAC-exempt.
  - signed:      canonical HMAC headers. Expected: never 401.

Reports a pass/fail table grouped by tool.

Run:  python test_hmac_all_tools.py [--base http://localhost:8080]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

BASE = "http://localhost:8080"

# ----- HMAC signing (mirrors backend/hmac_auth.py) ---------------------------

def canonical_string(ts: str, nonce: str, method: str, route: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{ts}\n{nonce}\n{method.upper()}\n{route}\n{body_hash}".encode("utf-8")


def split_path(path: str) -> str:
    """Strip query string — backend signs `request.path`, not the full URL."""
    return path.split("?", 1)[0]


def fetch_hmac_key() -> bytes:
    """Pull the ephemeral key the middleware is using right now."""
    req = urllib.request.Request(f"{BASE}/api/hmac/key")
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return base64.b64decode(data["key"])


def sign(key: bytes, method: str, route: str, body: bytes) -> dict[str, str]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = (uuid.uuid4().hex + uuid.uuid4().hex)[:32]
    canonical_route = split_path(route)
    msg = canonical_string(ts, nonce, method, canonical_route, body)
    sig = base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode("ascii")
    return {"X-H2S-Timestamp": ts, "X-H2S-Nonce": nonce, "X-H2S-Signature": sig}


# ----- HTTP helpers ----------------------------------------------------------

class Resp:
    __slots__ = ("status", "body", "error")

    def __init__(self, status: int, body: str, error: str | None = None):
        self.status = status
        self.body = body
        self.error = error

    @property
    def is_401(self) -> bool:
        return self.status == 401

    def short(self) -> str:
        if self.error:
            return f"{self.status} (conn: {self.error})"
        # Try to extract `error` field from JSON for a readable label.
        try:
            j = json.loads(self.body)
            label = j.get("error") or j.get("detail") or j.get("message") or ""
        except Exception:
            label = self.body[:60].replace("\n", " ")
        return f"{self.status} {label}"


def call(method: str, path: str, *, body: bytes = b"", headers: dict[str, str] | None = None,
         content_type: str | None = None) -> Resp:
    url = f"{BASE}{path}"
    merged = dict(headers or {})
    if content_type and "Content-Type" not in merged:
        merged["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body if body else None, method=method, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return Resp(r.status, r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read().decode("utf-8", "replace"))
    except Exception as e:
        return Resp(0, "", error=str(e))


def build_multipart(parts: list[tuple[str, str, bytes, str | None]]) -> tuple[bytes, str]:
    """Build a multipart/form-data body and return (body, content_type).

    Each part: (field_name, filename, content, content_type_or_None).
    """
    boundary = "----TestBoundary" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for field, filename, content, ctype in parts:
        chunks.append(f"--{boundary}\r\n".encode())
        disp = f'form-data; name="{field}"'
        if filename:
            disp += f'; filename="{filename}"'
        chunks.append(f"Content-Disposition: {disp}\r\n".encode())
        if ctype:
            chunks.append(f"Content-Type: {ctype}\r\n".encode())
        chunks.append(b"\r\n")
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def call_signed(key: bytes, method: str, path: str, *, body: bytes = b"",
                extra_headers: dict[str, str] | None = None,
                content_type: str | None = None,
                signed_body: bytes | None = None) -> Resp:
    """Sign with `signed_body` (the bytes the canonical hash covers) — defaults
    to `body` for non-multipart requests. For multipart requests, the
    frontend signs `sha256("")` per `lib/api.ts` since the body can't be
    reproduced; pass `signed_body=b""` to match that behavior."""
    headers = sign(key, method, path, signed_body if signed_body is not None else body)
    if extra_headers:
        headers.update(extra_headers)
    return call(method, path, body=body, headers=headers, content_type=content_type)


# ----- Test catalog ----------------------------------------------------------
#
# Each entry: (tool, name, method, path_template, body_factory, expect_unsigned)
# - expect_unsigned=True means the route is HMAC-required (unsigned should 401).
# - expect_unsigned=False means the route is multipart-exempt (unsigned should
#   reach the handler — accept any non-401 status).

XML_BODY = json.dumps({"xmlContent": "<root/>", "fileName": "t.xml", "email": "t@local"}).encode()
BULK_CONV_BODY = json.dumps({"files": [{"file_name": "t.xml", "content": "<root/>"}], "email": "t@local"}).encode()
NESTED_SESSION_BODY = json.dumps({"target_dialect": "bigquery", "output_format": "sql"}).encode()
MAPPING_APPLY_BODY = json.dumps({
    "updatedMappings": [{"sourceTable": "a", "sourceField": "x", "targetTable": "b", "targetField": "y"}],
    "fileName": "test.xlsx", "selectedPlatform": "bigquery", "sessionId": "fake", "outputFormat": "sql",
}).encode()

# A tiny ZIP for multipart tests — content doesn't matter, route will reject on
# parsing/decryption but we only need to prove the middleware let it through.
import zipfile


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.xml", "<root/>")
    return buf.getvalue()


def make_xlsx() -> bytes:
    # Minimal xlsx (PK signature + some bytes). Real decryption will fail but
    # route handler will run — that's all we need.
    return b"PK\x03\x04" + b"\x00" * 100


# Test catalog:
#   (tool, label, method, path, mode, body_or_parts, expect_unsigned_401)
#   mode: "raw" | "multipart"
#   body_or_parts: bytes (raw) | list[(field, filename, content, ctype)] (multipart)

RAW = "raw"
MP = "multipart"


TESTS: list[tuple[str, str, str, str, str, object, bool]] = [
    # ===== CONVERTER (HANA CV) =====
    ("converter", "POST /api/analyze (JSON)",               "POST", "/api/analyze",                     RAW, XML_BODY,                  True),
    ("converter", "POST /api/start-conversion (JSON)",      "POST", "/api/start-conversion",            RAW, XML_BODY,                  True),
    ("converter", "POST /api/validate (JSON)",              "POST", "/api/validate",                    RAW, XML_BODY,                  True),
    ("converter", "POST /api/bulk-analyze (multipart)",     "POST", "/api/bulk-analyze",                MP, [
        ("zipFile", "test.zip", make_zip(), "application/zip"),
        ("email",   "",        b"t@local", None),
    ], False),
    ("converter", "POST /api/bulk-conversion (JSON)",       "POST", "/api/bulk-conversion",             RAW, BULK_CONV_BODY,            True),
    ("converter", "GET  /api/conversion-status/<id>",       "GET",  "/api/conversion-status/abc",       RAW, b"",                       True),
    ("converter", "GET  /api/bulk-status/<id>",             "GET",  "/api/bulk-status/abc",             RAW, b"",                       True),
    ("converter", "GET  /api/bulk-download/<id>",          "GET",  "/api/bulk-download/abc",           RAW, b"",                       True),
    ("converter", "GET  /api/previous-conversions",         "GET",  "/api/previous-conversions",        RAW, b"",                       True),
    ("converter", "GET  /api/download/<id>",                "GET",  "/api/download/abc?type=sql",       RAW, b"",                       True),

    # ===== MAPPER =====
    ("mapper",    "POST /api/mapping/upload_and_generate_schema (multipart)",
                                                            "POST", "/api/mapping/upload_and_generate_schema",
                                                                                                    MP, [
        ("xlsxFile", "test.xlsx", make_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("selectedPlatform", "", b"bigquery", None),
    ], False),
    ("mapper",    "POST /api/mapping/apply_changes_and_generate_output (JSON)",
                                                            "POST", "/api/mapping/apply_changes_and_generate_output",
                                                                                                    RAW, MAPPING_APPLY_BODY,        True),

    # ===== FLATTENER (Nested CV) =====
    ("flattener", "POST /api/nested/sessions",              "POST", "/api/nested/sessions",             RAW, NESTED_SESSION_BODY,        True),
    ("flattener", "GET  /api/nested/sessions/<id>",         "GET",  "/api/nested/sessions/abc",        RAW, b"",                        True),
    ("flattener", "DELETE /api/nested/sessions/<id>",       "DELETE","/api/nested/sessions/abc",       RAW, b"",                        True),
    ("flattener", "POST /api/nested/sessions/<id>/cvs (JSON)", "POST", "/api/nested/sessions/abc/cvs",  RAW, b'{"file_content":"","file_name":"t.xlsx"}', True),
    ("flattener", "POST /api/nested/sessions/<id>/cvs/xlsx (multipart)",
                                                            "POST", "/api/nested/sessions/abc/cvs/xlsx",
                                                                                                    MP, [
        ("xlsxFile", "test.xlsx", make_xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ], False),
    ("flattener", "PATCH /api/nested/sessions/<id>/cvs/<aid>", "PATCH","/api/nested/sessions/abc/cvs/xyz", RAW, b'{"emission_mode":"cte"}', True),
    ("flattener", "DELETE /api/nested/sessions/<id>/cvs/<aid>","DELETE","/api/nested/sessions/abc/cvs/xyz", RAW, b"",                       True),
    ("flattener", "PUT  /api/nested/sessions/<id>/links",   "PUT",  "/api/nested/sessions/abc/links",   RAW, b'{"links":[]}',             True),
    ("flattener", "PUT  /api/nested/sessions/<id>/mappings","PUT",  "/api/nested/sessions/abc/mappings",RAW, b'{"mappings":[]}',           True),
    ("flattener", "POST /api/nested/sessions/<id>/validate","POST", "/api/nested/sessions/abc/validate",RAW, b"",                        True),
    ("flattener", "POST /api/nested/sessions/<id>/generate","POST", "/api/nested/sessions/abc/generate",RAW, b"",                        True),
    ("flattener", "GET  /api/nested/tasks/<id>",            "GET",  "/api/nested/tasks/abc",            RAW, b"",                        True),
    ("flattener", "GET  /api/nested/tasks/<id>/download",   "GET",  "/api/nested/tasks/abc/download",   RAW, b"",                        True),
    ("flattener", "DELETE /api/nested/tasks/<id>",          "DELETE","/api/nested/tasks/abc",           RAW, b"",                        True),
    ("flattener", "GET  /api/nested/previous_conversions/<id>/inspect",
                                                            "GET",  "/api/nested/previous_conversions/abc/inspect",
                                                                                                    RAW, b"",                        True),
]


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()
    BASE = args.base

    print(f"Fetching HMAC key from {BASE}/api/hmac/key ...")
    try:
        key = fetch_hmac_key()
        print(f"  Key length: {len(key)} bytes")
    except Exception as e:
        print(f"  Failed: {e}", file=sys.stderr)
        return 2

    print()
    header = f"{'Tool':<10} {'Unsigned':<12} {'Signed':<12}  Endpoint"
    print(header)
    print("-" * len(header) * 2)

    failures: list[str] = []
    by_tool: dict[str, list[tuple[str, str, str, str]]] = {}

    for tool, label, method, path, mode, body_or_parts, expect_unsigned_401 in TESTS:
        # Build the wire payload for both modes.
        if mode == MP:
            mp_body, mp_ct = build_multipart(body_or_parts)
            raw_body = mp_body
            content_type = mp_ct
            # Frontend signs sha256("") for multipart (see lib/api.ts). Mirror that.
            signed_body_for_canonical = b""
        else:
            raw_body = body_or_parts  # type: ignore[assignment]
            content_type = "application/json" if raw_body else None
            signed_body_for_canonical = raw_body

        # Unsigned request
        u = call(method, path, body=raw_body, content_type=content_type)
        if expect_unsigned_401:
            unsigned_ok = u.is_401
        else:
            unsigned_ok = not u.is_401
        unsigned_label = u.short()

        # Signed request
        s = call_signed(key, method, path, body=raw_body, content_type=content_type,
                        signed_body=signed_body_for_canonical)
        signed_ok = not s.is_401
        signed_label = s.short()

        status_u = "PASS" if unsigned_ok else "FAIL"
        status_s = "PASS" if signed_ok else "FAIL"
        if not unsigned_ok or not signed_ok:
            failures.append(f"{tool} {label}: unsigned={unsigned_label} signed={signed_label}")

        line = f"{tool:<10} {status_u+' '+unsigned_label:<40} {status_s+' '+signed_label:<40}  {label}"
        print(line)
        by_tool.setdefault(tool, []).append((label, unsigned_label, signed_label, status_u + "/" + status_s))

    print()
    if failures:
        print(f"FAIL — {len(failures)} endpoint(s) misbehaved:")
        for f in failures:
            print("  -", f)
        return 1
    print(f"OK — {len(TESTS)} endpoints across {len(by_tool)} tools all behave correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())