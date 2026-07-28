#!/usr/bin/env python3
"""End-to-end Mapper happy-path.

1. Fetch HMAC key.
2. POST multipart /api/mapping/upload_and_generate_schema (HMAC-exempt) using
   a real sample XLSX — proves the multipart fix works for a real upload.
3. POST signed /api/mapping/apply_changes_and_generate_output (JSON, signed)
   with the sessionId returned in step 2 — proves the signed path still
   works for non-multipart endpoints.

Run:  python test_e2e_mapper.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import uuid
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "http://localhost:8080"
SAMPLE_XLSX = "backend/nested_cv/input_files_test/cv_base_sales.xlsx"


# ----- HMAC signing ----------------------------------------------------------

def fetch_key() -> bytes:
    req = urllib.request.Request(f"{BASE}/api/hmac/key")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return base64.b64decode(json.load(resp)["key"])


def sign(key: bytes, method: str, route: str, body: bytes) -> dict[str, str]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = (uuid.uuid4().hex + uuid.uuid4().hex)[:32]
    route_clean = route.split("?", 1)[0]
    body_hash = hashlib.sha256(body).hexdigest()
    msg = f"{ts}\n{nonce}\n{method.upper()}\n{route_clean}\n{body_hash}".encode()
    sig = base64.b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode()
    return {"X-H2S-Timestamp": ts, "X-H2S-Nonce": nonce, "X-H2S-Signature": sig}


# ----- Multipart builder -----------------------------------------------------

def build_multipart(parts):
    boundary = "----TestBoundary" + uuid.uuid4().hex
    chunks = []
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
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


# ----- HTTP helpers ----------------------------------------------------------

def post_raw(path: str, body: bytes = b"", headers: dict[str, str] | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(f"{BASE}{path}", data=body or None, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def post_signed_json(key: bytes, path: str, payload: dict) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode()
    headers = sign(key, "POST", path, body)
    headers["Content-Type"] = "application/json"
    return post_raw(path, body, headers)


# ----- Main ------------------------------------------------------------------

def main() -> int:
    print("Step 1: fetch HMAC key ...")
    key = fetch_key()
    print(f"  ok (len={len(key)})")

    print("\nStep 2: upload sample XLSX (multipart, HMAC-exempt) ...")
    with open(SAMPLE_XLSX, "rb") as fh:
        xlsx_bytes = fh.read()
    body, ctype = build_multipart([
        ("xlsxFile", "cv_base_sales.xlsx", xlsx_bytes,
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("selectedPlatform", "", b"bigquery", None),
    ])
    status, resp = post_raw("/api/mapping/upload_and_generate_schema", body,
                            {"Content-Type": ctype})
    print(f"  HTTP {status}")
    if status != 200 or not isinstance(resp, dict) or not resp.get("success"):
        print(f"  FAIL: {resp}")
        return 1
    schema = resp["mappingSchema"]
    session_id = schema["sessionId"]
    file_name = schema["fileName"]
    n_rows = len(schema.get("mappingFileContent", []))
    print(f"  ok — sessionId={session_id[:8]}…  fileName={file_name}  rows={n_rows}")

    print("\nStep 3: signed apply_changes_and_generate_output ...")
    # Send the schema's mapping rows back unchanged as `updatedMappings`.
    updated = schema["mappingFileContent"][:5] if n_rows else [{"sourceTable": "t", "sourceField": "c"}]
    payload = {
        "updatedMappings": updated,
        "fileName": file_name + ".xlsx",
        "selectedPlatform": schema["databaseName"],
        "sessionId": session_id,
        "outputFormat": "sql",
    }
    status, resp = post_signed_json(key, "/api/mapping/apply_changes_and_generate_output", payload)
    print(f"  HTTP {status}")
    if status != 200 or not isinstance(resp, dict) or not resp.get("success"):
        print(f"  FAIL: {resp}")
        return 1
    sql = resp.get("cteSqlContent") or ""
    print(f"  ok — {len(sql)} chars of CTE SQL, fileName={resp.get('fileName')}")
    if sql:
        print(f"  preview: {sql.splitlines()[0][:80]}…")

    print("\nALL STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())