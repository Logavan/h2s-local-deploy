# licensing/ — HANACV2SQL Enterprise License Manager

## Purpose

`backend/licensing/` transforms the local HANACV2SQL Enterprise deployment into a **licensed software product**. Each installation is bound to a single physical/virtual host via an RSA-signed `license.json`. The license is verified at Flask startup and re-checked whenever `/health` is polled.

Without a valid license, `flask_app.py` refuses to start. Copying a license to a different machine is rejected because the signed `machine_hash` no longer matches the current host's fingerprint.

## Architecture

```
license.json (on disk, customer-supplied)
        │
        ▼
verifier.check_or_exit()  ── called from create_app() BEFORE Flask starts
        │
        ├── 1. load_license()           — parse + schema-check
        ├── 2. verify_payload_or_raise() — RSA-2048 / PKCS1v15 / SHA-256
        ├── 3. license.is_expired()     — expires_at > now()
        └── 4. compute_fingerprint()    — SHA-256 of collected identifiers
                │
                └── compare to license.machine_hash  → match = run, mismatch = exit 1
```

### Check chain — failure modes

| Failure                  | Raised exception            | Operator sees                                          |
|--------------------------|-----------------------------|---------------------------------------------------------|
| `license.json` missing   | `LicenseNotFoundError`      | `License check failed: LicenseNotFoundError: ...`      |
| `license.json` malformed | `LicenseFormatError`        | `License check failed: LicenseFormatError: ...`        |
| Bad RSA signature        | `InvalidSignatureError`     | `License check failed: InvalidSignatureError: ...`      |
| Expired                  | `ExpiredLicenseError`       | `License check failed: ExpiredLicenseError: ...`        |
| Machine mismatch         | `MachineMismatchError`      | `License check failed: MachineMismatchError: ...`       |
| Public key missing       | `FileNotFoundError`         | `Bundled public key not found at ...`                   |

Every error is logged with the exception type and a single-line message; **the license payload, signature, and raw fingerprint identifiers are never written to logs**.

## Module map

```
backend/licensing/
├── __init__.py           # Public API re-exports
├── __main__.py           # `python -m licensing` entry point
├── exceptions.py         # LicenseError hierarchy
├── fingerprint.py        # Cross-platform machine ID collection
├── license.py            # License dataclass + JSON load/save
├── signer.py             # RSA-2048 sign / verify
├── verifier.py           # Startup gate + quick_status()
├── cli.py                # `python -m licensing <subcommand>` tooling
├── keys/
│   ├── public.pem        # Bundled with the app (committed)
│   ├── private.pem       # Vendor-only, NOT committed (see keys/.gitignore)
│   └── .gitignore        # Ignores private*.pem and *.key
├── tests/
│   └── test_licensing.py # Unit + integration tests (11 tests, all passing)
└── claude.md             # This file
```

## CLI — `python -m licensing <subcommand>`

| Subcommand         | Purpose                                                                | Audience   |
|--------------------|------------------------------------------------------------------------|------------|
| `info`             | Print machine fingerprint + current license status (JSON)              | Operator   |
| `verify`           | Re-run the full verification chain (no Flask restart)                  | Operator   |
| `request-rebind`   | Generate a signed request file the customer sends to the vendor       | Customer   |
| `apply <path>`     | Install a vendor-supplied license.json (verifies signature first)      | Admin      |
| `sign <license>`   | Sign a license.json with `keys/private.pem`                            | Vendor     |
| `generate-keys`    | Create a fresh RSA keypair                                             | Vendor     |

### Vendor onboarding flow

```bash
# 1. Vendor creates keypair (once, kept offline)
python -m licensing generate-keys --out-dir /secure/vendor_keys
# → private.pem (chmod 600), public.pem

# 2. Vendor crafts license.json (any text editor), e.g.:
cat > license.json <<EOF
{
  "license_id": "H2S-ENT-2026-AB12-CD34",
  "customer": "Acme Corp",
  "issued_at": "2026-07-28T00:00:00Z",
  "expires_at": "2027-07-28T00:00:00Z",
  "max_concurrent_users": 5,
  "machine_hash": "<customer's fingerprint.short or full_hash>",
  "signature": ""
}
EOF

# 3. Vendor signs it
python -m licensing sign license.json --private-key /secure/vendor_keys/private.pem

# 4. Customer installs
python -m licensing apply license.json

# 5. Customer restarts the app
python flask_app.py
```

### Customer rebind flow (machine replaced, NIC swapped, etc.)

```bash
# Customer generates a rebind request
python -m licensing request-rebind \
    --license-id H2S-ENT-2026-AB12-CD34 \
    --previous-machine-hash A73HF8X29P4QM0LR
# → rebind_request_<short>.json

# Customer emails that file to vendor. Vendor signs a fresh license
# bound to the new fingerprint and returns the new license.json.

# Customer applies it
python -m licensing apply new_license.json
```

## Environment variables

| Variable                     | Required | Default                                            | Purpose                                                  |
|------------------------------|----------|----------------------------------------------------|----------------------------------------------------------|
| `LICENSE_PATH`               | No       | `./license.json`, then `backend/license.json`      | Override license location                                |
| `LICENSE_PUBLIC_KEY_PATH`    | No       | `licensing/keys/public.pem`                        | Override bundled public key (used by tests + smoke runs)  |
| `H2S_LICENSE_ID`             | No       | `UNKNOWN`                                          | Used by `request-rebind` if no license file exists       |
| `H2S_PREV_MACHINE_HASH`      | No       | `UNKNOWN`                                          | Used by `request-rebind` if no license file exists       |

Note: there is no `H2S_SKIP_LICENSE` escape hatch and no `dev_mode` license
flag. Every license — local development or production — is bound to a
specific machine fingerprint via the same code path. See "Solo-dev
onboarding" below for how local dev gets a license.

## Flask integration

`backend/flask_app.py` calls `check_or_exit()` at the very top of `create_app()`, **before Flask is constructed**. On failure the process exits 1 with a single-line message — never binds to a port with a bad license.

`/health` returns license info (license_id, customer, expires_at, days_remaining, is_container) but **never** the raw fingerprint or signature.

## Fingerprint composition

SHA-256 over a JSON dump (sort_keys=True) of:

| Source             | Windows                              | Linux (bare-metal)                       | Linux (Docker, with host-bind mounts)    | macOS                                   |
|--------------------|--------------------------------------|------------------------------------------|------------------------------------------|------------------------------------------|
| Primary (machine) | `HKLM\...\Cryptography\MachineGuid`  | `/etc/machine-id`                        | `/host/etc/machine-id` (bind-mounted from host) | `ioreg -rd1 -c IOPlatformExpertDevice`   |
| Secondary (DMI)    | `wmic csproduct get uuid`            | `/sys/class/dmi/id/product_uuid`         | `/host/sys/class/dmi/id/product_uuid` (bind-mounted from host) | (none)                                   |
| Tertiary (MAC)     | `uuid.getnode()` (skip VMware/HV)    | same                                     | same                                     | same                                     |
| Fallback           | hostname + OS + CPU + disk serial    | hostname + OS + CPU + `lsblk SERIAL`     | hostname + OS + CPU + disk serial        | hostname + OS + CPU                      |
| Container          | n/a                                  | n/a                                      | container ID from `/proc/1/cgroup` (fallback only — host-bind wins when present) | n/a |

Refuses to start when no primary identifier is present — an empty fingerprint would falsely match a license bound to `""`.

### Docker host-binding

HANACV2SQL Enterprise is delivered as a Docker image. Containers have ephemeral IDs that change on every restart, so we **bind to the host VM** instead. The deployment requires three read-only volume mounts (`docker-compose.enterprise.yml` wires these by default):

| Host path                                | Mounted into container as            | Read by                                         |
|------------------------------------------|--------------------------------------|-------------------------------------------------|
| `/etc/machine-id`                        | `/host/etc/machine-id:ro`            | `_read_host_machine_id()`                       |
| `/sys/class/dmi/id/product_uuid`         | `/host/sys/class/dmi/id/product_uuid:ro` | `_read_host_dmi_uuid()`                      |
| `./license.json`                         | `/etc/license.d/license.json:ro`     | `verifier.find_license_file()`                  |

Path overrides for unusual deployments:

| Env var                          | Default                                                |
|----------------------------------|--------------------------------------------------------|
| `LICENSE_HOST_MACHINE_ID_PATH`   | `/host/etc/machine-id`                                 |
| `LICENSE_HOST_DMI_UUID_PATH`     | `/host/sys/class/dmi/id/product_uuid`                  |
| `LICENSE_PATH`                   | `./license.json`, then `backend/license.json`          |

Without host-bind mounts the container falls back to the container ID as a weak primary identifier — this works for dev/CI but is **not stable across restarts** and should not be relied on for production.

## Security notes

1. **Private key never leaves the vendor box.** `keys/.gitignore` excludes `private*.pem` and `*.key`. The bundled `public.pem` is the only key in the repo.
2. **PKCS#1v15 + SHA-256.** Broadest support across HSMs / runtimes. Don't switch to PSS without re-signing every active license.
3. **Signature detail is redacted from logs.** Operators see the exception type + one-line summary; the signature blob is never logged.
4. **Stable hash ordering.** The canonical payload uses `json.dumps(sort_keys=True, separators=(",", ":"))` so signer and verifier agree on bytes across Python versions.
5. **No plaintext license logging.** `/health` exposes `license_id`, `customer`, `expires_at`, and `machine_hash[:16]+"..."`. Raw fingerprint, signature, and identifiers are never returned over HTTP.
6. **No license-skip escape hatch and no dev-mode bypass.** Earlier versions exposed `H2S_SKIP_LICENSE=1` for local dev, and later a signed `dev_mode: true` flag; both have been removed. Every license is bound to a specific machine fingerprint — local dev included. See "Solo-dev onboarding" below.

## Solo-dev onboarding (`laptop-license.json`)

For local development, the repo ships a vendor-signed `laptop-license.json` at the repo root bound to the developer's machine fingerprint. It is verified through the same code path as a production license — RSA signature, expiry, and machine binding all enforced.

```bash
# Clone + run — works out of the box for THIS laptop
git clone <repo>
cd h2s-local-deploy
docker compose -f docker-compose.enterprise.yml up --build
# /health returns: license_id=H2S-LAPTOP-..., customer=Logavan (...)
```

If you clone onto a different laptop, the existing `laptop-license.json` will be rejected (`MachineMismatchError`). To get a working license on the new host:

```bash
# 1. Generate the new machine fingerprint
cd backend
python -m licensing info

# 2. Edit laptop-license.json — set machine_hash to the new short hash,
#    bump expires_at, leave signature as ""

# 3. Re-sign with the vendor private key
python -m licensing sign ../laptop-license.json --private-key ../vendor-keys/private.pem
```

For customer deployments, the customer runs `python -m licensing info` on their VM and emails the short fingerprint to the vendor; the vendor signs `license.json` against it and the customer mounts it at `/etc/license.d/license.json` per the docker-compose file.

## Tests

`backend/tests/test_licensing.py` — 15 tests covering:

- Fingerprint stability (deterministic across calls)
- SHA-256 length (16-char short, 64-char full)
- RSA round-trip (sign → verify → reject tampered)
- Signed license survives a disk round-trip
- Tampered license (modified `customer`) fails signature verification
- `verify_or_raise()` accepts valid license
- `verify_or_raise()` rejects expired license
- `verify_or_raise()` rejects machine mismatch (mocked fingerprint)
- Binary integrity: matching sha256 passes
- Binary integrity: mismatching sha256 fails

```bash
cd backend
python tests/test_licensing.py
# OR
python -m pytest tests/test_licensing.py -v
```

## Acceptance criteria

- [x] `python -m licensing info` prints stable fingerprint on same machine across reboots
- [x] Fingerprint differs on a different VM (verified via test)
- [x] Tampered `license.json` → app refuses to start with clear log
- [x] Expired license → app refuses to start
- [x] Valid license on correct machine → app starts, `/health` shows license info
- [x] `request-rebind` → outputs vendor-processable file
- [x] Public key bundled, private key NOT in repo (`.gitignore` enforced)
- [x] No license secrets logged in plaintext