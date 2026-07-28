# SECURITY.md — HANACV2SQL Enterprise

> **Audience:** vendor operators, customer security/admin teams, and end users.
>
> This document is the single source of truth for:
> 1. **How reverse-engineering is prevented** (the seven defense layers)
> 2. **Who does what** — vendor, client admin team, and end users
>
> We are honest about what we protect against and what we don't. There is no "unbreakable" claim anywhere in this document.

---

## Table of Contents

1. [Overview — The Three Roles](#1-overview--the-three-roles)
2. [The 7 Layers of Reverse-Engineering Defense](#2-the-7-layers-of-reverse-engineering-defense)
3. [End-to-End Timeline: Who Does What, When](#3-end-to-end-timeline-who-does-what-when)
4. [What the Vendor (You) Do](#4-what-the-vendor-you-do)
5. [What the Client Admin Team Does](#5-what-the-client-admin-team-does)
6. [What End Users Do](#6-what-end-users-do)
7. [What Happens When Things Go Wrong](#7-what-happens-when-things-go-wrong)
8. [Cryptography Inventory](#8-cryptography-inventory)
9. [Operational Practices](#9-operational-practices)
10. [Honest Limitations](#10-honest-limitations)
11. [Customer Self-Audit](#11-customer-self-audit)
12. [Responsible Disclosure](#12-responsible-disclosure)
13. [Compliance Notes](#13-compliance-notes)

---

## 1. Overview — The Three Roles

HANACV2SQL Enterprise is delivered as Docker images that customers run on **their own VMs**. This creates a clean separation of responsibility:

```
┌─────────────────┐          ┌──────────────────────────┐          ┌──────────────┐
│     VENDOR      │          │  CLIENT ADMIN TEAM       │          │ END USERS    │
│     (you)       │          │  (customer's IT / DevOps)│          │ (analysts)   │
│                 │          │                          │          │              │
│  Builds images  │  ──img──▶│  Pulls, configures, runs │  ─URL──▶ │  Open browser│
│  Signs licenses │  ─lic───▶│  the container           │          │  Use the app │
│  Holds private  │          │  Manages secrets         │          │  Generate SQL│
│  keys offline   │          │  Backs up data           │          │              │
└─────────────────┘          └──────────────────────────┘          └──────────────┘
        │                              │                                  │
        │   Once per release           │   Once per deployment           │   Every session
        │   (~few hours of work)       │   (~30 min first time)          │   (~0 work)
        │                              │   Then: maintain + rotate       │
```

**The roles do not overlap:**

| Concern                              | Who handles it            |
|--------------------------------------|---------------------------|
| Building & signing the image          | Vendor                    |
| Issuing licenses                     | Vendor                    |
| Deploying to the customer's VM        | Client admin team         |
| Setting up secrets                   | Client admin team         |
| Backup & secret rotation             | Client admin team         |
| Using the app to generate SQL        | End users                 |
| Setting environment variables         | **Nobody** — not even admins during operation |
| Editing license.json                 | **Nobody** — vendor-signs, customer-mounts |

If a user asks "how do I configure X?" the answer is always "ask your admin." If an admin asks "how do I change X?" the answer is always in this document or `DEPLOYMENT.md`.

---

## 2. The 7 Layers of Reverse-Engineering Defense

The deployment has seven independent protection layers. A breach of any one does not collapse the others.

```
   Layer 1 — No secrets in image               (defense against image inspection)
   Layer 2 — Machine-bound licensing           (defense against binary redistribution)
   Layer 3 — HMAC request signing              (defense against rewritten frontends)
   Layer 4 — Container hardening               (defense against runtime tampering)
   Layer 5 — Image signing (cosign) + SBOM     (defense against image substitution)
   Layer 6 — Compiled backend (Nuitka)         (defense against source-level RE)
   Layer 7 — Legal contract (EULA)             (defense against commercial misuse)
```

### Layer 1 — No Secrets in the Image

The Docker image is **secret-free**. Verified by `tests/test_secrets.py` on every commit, and by the admin's own `docker save | grep` audit.

| Secret type            | Where it lives                                  |
|------------------------|--------------------------------------------------|
| Gemini API key         | Runtime mount (env-file / Docker secret / vault) |
| HMAC signing key       | Runtime mount                                    |
| License JSON           | Runtime mount                                    |
| BigQuery service acct  | Runtime mount                                    |
| Any future secret      | Runtime mount                                    |

```bash
# Verify your deployed image:
docker save your-registry/hanacv2sql-backend:v1.2.3 | tar -xO | \
  grep -E '(AIza[0-9A-Za-z_-]{35}|sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36})'
# expected: zero matches
```

**What this stops:** someone `docker save`-ing the image and grepping for keys.
**What this doesn't stop:** a sophisticated RE of the binary itself — handled by Layer 6.

### Layer 2 — Machine-Bound Licensing (`backend/licensing/`)

Every installation is bound to a single host VM via RSA-signed `license.json`.

**What gets signed by the vendor's private key:**
- `license_id` — unique per customer
- `customer` — legal entity name
- `expires_at` — when the license stops working
- `machine_hash` — fingerprint of the customer's VM (read from host-bind mounts)
- `max_concurrent_users` — license tier
- `binary_sha256` — SHA-256 of the running backend binary (catches repackaging)
- `image_digest` — cosign-pinned image digest (alternative to binary_sha256)
- `secrets` — optional vendor-signed per-customer API keys

**What the app checks at every startup:**

```
1. license.json exists                  → LicenseNotFoundError if missing
2. RSA signature verifies against       → InvalidSignatureError if tampered
   bundled public.pem
3. expires_at > today                   → ExpiredLicenseError if past
4. Current host fingerprint matches     → MachineMismatchError if different VM
   license.machine_hash
5. SHA-256 of running binary matches    → BinaryIntegrityError if repackaged
   license.binary_sha256
6. /health returns license summary      → operator-visible status
```

Any failure → process exits 1 with a single human-readable line on stderr. Never binds to a port with a bad license.

See [`backend/licensing/guide.md`](licensing/guide.md) for the full design.

**What this stops:** copying the license to a second VM, editing the expiry, sharing with a non-paying party, repackaging the binary.
**What this doesn't stop:** cloning the entire VM (mitigation: regenerate `machine-id` post-clone).

### Layer 3 — HMAC Request Signing (`backend/hmac_auth.py`)

Every mutating API request must carry three headers proving it came from the legitimate frontend:

```
X-H2S-Timestamp:  2026-07-28T12:34:56Z      (within ±5 min of server clock)
X-H2S-Nonce:      <32 hex chars>            (unique per request — replay protection)
X-H2S-Signature:  <base64 HMAC-SHA256>     (over canonical timestamp\nonce\nMETHOD\nroute\nbody-hash)
```

The signing key is fetched once at app startup from `/api/hmac/key` (which is itself exempt from signing). The key is held in memory only — never written to `localStorage` or `sessionStorage`, so XSS extraction can't steal it.

**Exempt routes** (public, no signature needed): `/health`, `/api/status`, `/api/hmac/key`, `/container-shutdown`, `/debug-latency`.

**What this stops:** someone reading the frontend bundle, writing their own client, calling our API directly. They get `401 Invalid signature` on every call because they don't have the signing key.
**What this doesn't stop:** reading the frontend to understand what data shapes we accept — that's not what HMAC is for.

### Layer 4 — Container Hardening (`backend/Dockerfile.enterprise`)

| Setting                      | Value                              | Purpose                              |
|------------------------------|------------------------------------|--------------------------------------|
| Base image                   | `python:3.11-slim`                 | Minimal attack surface               |
| Build stages                 | Multi-stage                        | No compiler toolchain in runtime     |
| User                         | `hanacv2sql` (uid 10001)           | Never runs as root                   |
| Root filesystem              | Read-only                          | Tampering confined to declared vols  |
| Writable paths               | `/data/outputs`, `/tmp` (tmpfs)    | Only what's needed                   |
| Capabilities                 | `cap_drop: ALL`                    | No privileges by default             |
| New privileges               | `no-new-privileges`                | Can't escalate via setuid            |
| Healthcheck                  | Wires to `/health` (license check) | Restart on failure                   |

```yaml
# docker-compose.enterprise.yml
backend:
  read_only: true
  tmpfs: [/tmp:size=64m, /data/outputs:size=512m]
  cap_drop: [ALL]
  security_opt: [no-new-privileges:true]
```

**What this stops:** even if an attacker exploits some other bug to get code execution, they're trapped in a sandbox with no compiler, no shell tools, no way to write to disk outside declared volumes.
**What this doesn't stop:** root-level VM escape (customer's responsibility to harden the host).

### Layer 5 — Image Signing (cosign) + SBOM (syft)

Every release is signed with `cosign`. Customers verify on pull:

```bash
cosign verify --key cosign.pub your-registry/hanacv2sql-backend:v1.2.3
```

A Software Bill of Materials is generated per release with `syft`:

```bash
syft your-registry/hanacv2sql-backend:v1.2.3 -o spdx-json > sbom.spdx.json
```

Customers can independently audit what's inside the image and scan for known CVEs:

```bash
trivy image your-registry/hanacv2sql-backend:v1.2.3
```

**What this stops:** the customer's IT team pulling a tampered image from a compromised registry, or unknowingly running an image with known critical CVEs.
**What this doesn't stop:** someone who has physical access to the customer's registry credentials.

### Layer 6 — Compiled Backend (Nuitka)

The Python backend is compiled to native code with [Nuitka](https://nuitka.net/) at build time.

```bash
./scripts/build_compiled.sh
# → dist/hanacv2sql-backend  (native ELF / .exe, no Python source readable)
```

Decompilation requires reverse-engineering C output, not Python bytecode.

| What they get              | With pure Python        | With Nuitka compiled            |
|----------------------------|--------------------------|----------------------------------|
| `strings binary`           | Readable Python source   | Mangled C strings                |
| `dis.dis(module)`          | Full bytecode            | No bytecode — it's machine code  |
| Easy modifications         | Edit .py, restart        | Patch ELF, reverse-engineer C    |
| Time to RE                 | Hours to days            | Weeks to months                  |

**What this stops:** casual source-level RE — the typical "let me read this and learn how it works" attack.
**What this doesn't stop:** a determined attacker with weeks of effort and C skills.

### Layer 7 — Legal Contract (EULA)

The End-User License Agreement explicitly prohibits:

- Reverse engineering, decompilation, disassembly
- Modification of the binary
- Use on machines other than the licensed host
- Sharing the license file with third parties

It grants the vendor **audit rights** and reserves **liquidated damages** for license violations (typical: 10× annual license fee).

**What this stops:** a customer who would happily pirate software but won't risk a contract dispute.
**What this doesn't stop:** bad-faith actors who don't care about legal consequences.

---

## 3. End-to-End Timeline: Who Does What, When

| Step | Who | What | When | Time cost |
|------|-----|------|------|-----------|
| 1 | **Vendor** | Build & sign Docker image | Per release (~monthly) | Hours |
| 2 | **Vendor** | Generate / rotate signing keypair | Once per year | Minutes |
| 3 | **Customer admin** | Provision VM (or pick existing) | Once | Hours |
| 4 | **Customer admin** | Send machine fingerprint to vendor | Once | Minutes |
| 5 | **Vendor** | Craft + sign `license.json` | Per customer onboarding | Minutes |
| 6 | **Customer admin** | Place license + secrets on VM | Once | Minutes |
| 7 | **Customer admin** | `docker compose up -d` | Once | Minutes |
| 8 | **Customer admin** | Smoke-test `/health` | Once | Minutes |
| 9 | **Customer admin** | Tell users the URL | Once | Minutes |
| 10 | **End users** | Open browser, use the app | Every session | Zero config |
| 11 | **Customer admin** | Rotate secrets when needed | Per rotation | Minutes |
| 12 | **Customer admin** | Upgrade image: `docker compose pull && up -d` | Per release | Minutes |
| 13 | **Customer admin** | Renew license annually | Per year | Minutes |
| 14 | **Vendor** | Re-issue renewed license | Per renewal | Minutes |

The customer's admin team touches the system ~3-4 times per year (initial setup + upgrades + renewals). End users touch it every session — but only by opening a browser.

---

## 4. What the Vendor (You) Do

This section is your operational checklist.

### One-time setup

- [ ] Generate the license signing keypair (RSA-2048, kept offline):
      ```bash
      python -m licensing generate-keys --out-dir /secure/vendor_keys
      ```
- [ ] Generate the cosign keypair (for image signing):
      ```bash
      cosign generate-key-pair
      # Store cosign.key offline; ship cosign.pub to customers
      ```
- [ ] Bundle `licensing/keys/public.pem` into the app image.
- [ ] Ship `cosign.pub` via your customer portal.
- [ ] Draft the EULA with your lawyer (prohibits RE, grants audit rights, sets liquidated damages).

### Per release

- [ ] Build the compiled backend (Nuitka):
      ```bash
      ./scripts/build_compiled.sh
      ```
- [ ] Compute its SHA-256 (you'll embed this in every new license):
      ```bash
      sha256sum dist/hanacv2sql-backend
      ```
- [ ] Build + tag the Docker image:
      ```bash
      docker build -f backend/Dockerfile.enterprise -t your-registry/hanacv2sql-backend:v1.2.3 .
      ```
- [ ] Sign the image + generate SBOM:
      ```bash
      ./scripts/sign_image.sh your-registry/hanacv2sql-backend:v1.2.3
      ```
- [ ] Push to your registry:
      ```bash
      docker push your-registry/hanacv2sql-backend:v1.2.3
      ```
- [ ] Publish release notes (admin-facing): image digest, license re-issuance policy, breaking changes.

### Per customer onboarding

- [ ] Receive their machine fingerprint:
      ```bash
      cat /etc/machine-id
      sudo cat /sys/class/dmi/id/product_uuid
      ```
- [ ] Craft a `license.json`:
      ```json
      {
        "license_id": "H2S-ENT-2026-AB12-CD34",
        "customer": "Acme Corp",
        "issued_at": "2026-07-28T00:00:00Z",
        "expires_at": "2027-07-28T00:00:00Z",
        "max_concurrent_users": 5,
        "machine_hash": "<their fingerprint>",
        "binary_sha256": "<sha256 of your compiled binary>",
        "secrets": {"hmac_key": "<base64 32 random bytes>"},
        "signature": ""
      }
      ```
- [ ] Sign it:
      ```bash
      python -m licensing sign license.json
      ```
- [ ] Email it back through your secure channel.
- [ ] Track in your customer database: license_id, customer, expiry, machine_hash short, contact email.

### Per renewal (annually)

- [ ] ~30 days before `expires_at`, email customer a freshly signed license with new `expires_at`.
- [ ] Same machine hash. No rebind needed.
- [ ] Update your customer database.

### Per rebind request

- [ ] Customer runs `python -m licensing request-rebind` and emails you the resulting JSON.
- [ ] Verify the request makes sense (right customer, expected timing).
- [ ] Sign a new license with the new fingerprint.
- [ ] Email back. Same `license_id`, same `customer`, new `machine_hash`, new `expires_at`.

### Per incident (rare)

- [ ] If `private.pem` compromised: see `§9 — Key Rotation (Vendor)` below.
- [ ] If a customer reports suspicious activity: pull logs, review access patterns, rotate HMAC key.

---

## 5. What the Client Admin Team Does

The customer admin team is responsible for everything that happens between "we have a VM" and "users can use the app". This is a one-time ~30 min setup, then minimal ongoing maintenance.

Full runbook in [`DEPLOYMENT.md`](DEPLOYMENT.md). Summary:

### Initial setup (one time, ~30 min)

1. **Provision a VM** — Linux (Ubuntu 20.04+, RHEL 8+, Debian 11+) or Windows Server 2019+. 2 vCPU / 4 GB RAM minimum. Docker Engine 24+ + Compose v2+.

2. **Get the machine fingerprint** — the vendor needs this to bind the license:
   ```bash
   cat /etc/machine-id
   sudo cat /sys/class/dmi/id/product_uuid
   ```
   Send both values to the vendor via your secure channel.

3. **Receive the signed `license.json`** from the vendor. Save it to `/etc/hanacv2sql/license.json`.

4. **Choose a secret-injection method** (pick ONE):

   - **Method A — env-file** (simplest):
     ```bash
     sudo mkdir -p /etc/hanacv2sql
     sudo tee /etc/hanacv2sql/secrets.env > /dev/null <<EOF
     GEMINI_API_KEY=AIza...
     H2S_HMAC_KEY=$(openssl rand -base64 32)
     EOF
     sudo chmod 600 /etc/hanacv2sql/secrets.env
     ```
     Uncomment the env-file volume mount in `docker-compose.enterprise.yml`.

   - **Method B — Docker secrets** (for Swarm / multi-host):
     ```bash
     echo "AIza..." | docker secret create gemini_api_key -
     openssl rand -base64 32 | docker secret create hmac_key -
     ```

   - **Method C — Vault** (for enterprise secret management):
     Configure your vault-agent sidecar to write to `/run/secrets/gemini_api_key` and `/run/secrets/hmac_key`. Backend reads via `*_FILE` env vars.

5. **Pull and start**:
   ```bash
   docker compose -f docker-compose.enterprise.yml pull
   docker compose -f docker-compose.enterprise.yml up -d
   ```

6. **Smoke test**:
   ```bash
   curl http://localhost:8080/health
   # expect: status "healthy", license.days_remaining > 0
   ```

7. **Set up HTTPS termination** (recommended): put nginx / Caddy / Traefik in front.

8. **Tell users the URL** — `http://hanacv2sql.your-domain:3000`. Done.

### Ongoing maintenance (a few minutes per quarter)

| Task | Frequency | Command |
|------|-----------|---------|
| Update image | Per release | `docker compose pull && docker compose up -d` |
| Rotate Gemini key | Per Google quota refresh | `vim /etc/hanacv2sql/secrets.env && docker compose restart backend` |
| Renew license | Annually | Vendor sends new license; place file; restart |
| Rebind license | On VM migration | Customer generates rebind request; vendor signs; place file; restart |
| Backup | Per backup policy | `tar -czf backup.tar.gz /etc/hanacv2sql/ data/outputs/` |

### What the admin team does NOT do

- Does not edit `license.json` (signed; editing invalidates it)
- Does not bake secrets into a new image
- Does not edit `docker-compose.yml` to remove the host-bind mounts
- Does not skip the license check via any environment variable or flag (none exist by design — license verification is mandatory)
- Does not modify the running container (`docker exec` should be rare and audited)

---

## 6. What End Users Do

End users have exactly one job: use the app.

### Their workflow

1. Open the URL their admin gave them (e.g. `https://hanacv2sql.your-domain:3000`).
2. Use the three tools:
   - **HANA CV Converter** — upload a SAP HANA Calculation View XML, get platform-specific SQL back
   - **SQL/PySpark Mapping Engine** — upload a mapping XLSX, edit mappings, generate SQL or PySpark
   - **Nested CV Flattener** — paste multiple CV JSONs, resolve dependencies, generate merged SQL
3. Download the output.

That's it. No login, no config, no env vars, no setup.

### What end users do NOT do

This is what you tell every user when you onboard them:

> **Users do not:**
> - Set environment variables
> - Edit configuration files
> - Restart the container
> - Modify the license file
> - Open DevTools to inspect / tamper with API calls (the backend will reject unsigned requests anyway)
> - Run the app from anywhere except `https://hanacv2sql.your-domain:3000`
>
> **If the app is misbehaving, contact your administrator.** The administrator manages the deployment and can resolve issues without involving the vendor in most cases.

### What users will see (and not see)

**Visible:**
- Their browser session, the three tools, generated SQL/PySpark output
- Standard browser DevTools (they can look at the JS, but it's minified and stripped of console.*)

**Invisible (and they should never see):**
- `license.json` content (no UI for it)
- HMAC signing key (held in memory only, never exposed)
- Gemini API key (never sent to the browser — all AI calls go through our backend)
- Backend Docker container internals (no `docker exec` access — they're not admin)
- Other users' data (sessions are per-tab; we don't multi-tenant user data)

---

## 7. What Happens When Things Go Wrong

### Vendor-side incidents

| Symptom | Cause | Fix |
|---------|-------|-----|
| `private.pem` compromised | Insider leak, hacked laptop | Rotate keypair, re-sign every active license, force customer upgrade (see §9) |
| cosign key compromised | Same | Re-publish `cosign.pub`, force customer re-verify |
| Gemini key leaked | Someone committed it to a public repo | Rotate in Google AI Studio, update customers' env files, no app update needed |
| License issued with wrong fingerprint | Operator error | Issue corrected license; customer applies; restart |

### Customer-side incidents

| Symptom (admin sees) | Cause | Fix |
|----------------------|-------|-----|
| App won't start: `LicenseNotFoundError` | `license.json` not mounted correctly | Check compose `volumes:` for license mount |
| App won't start: `ExpiredLicenseError` | License past `expires_at` | Request renewal from vendor |
| App won't start: `MachineMismatchError` | VM migrated without rebind | Run `request-rebind`, send to vendor, apply new license |
| App won't start: `BinaryIntegrityError` | Image was modified or replaced with non-vendor build | Re-pull official image, restart |
| App won't start: `InvalidSignatureError` | `license.json` was tampered or signed by wrong key | Request fresh signed license from vendor |
| `401 Invalid signature` from backend | Frontend can't reach `/api/hmac/key` | Check `NEXT_PUBLIC_API_BASE_URL`, check network ACLs |
| `/health` non-200 | Check logs: `docker compose logs backend --tail=50` | Most common: missing/placeholder GEMINI_API_KEY |
| User reports "I can't access the app" | Wrong URL, expired session, browser cache | Admin verifies URL, asks user to clear cache / try incognito |

### User-side reports

| User says | Admin does |
|-----------|------------|
| "The app is slow" | Check `/health`, check Gemini quota |
| "I can't upload my file" | Check file size limits, check disk space on VM |
| "I get an error" | Get exact error message; check `docker compose logs backend` |
| "I lost my generated SQL" | Check `data/outputs/` on the VM |
| "I need to use the app from a new laptop" | Browser-only — no setup needed; just give them the URL |

---

## 8. Cryptography Inventory

| Purpose                | Algorithm        | Key size | Source                                          |
|------------------------|------------------|----------|--------------------------------------------------|
| License signing        | RSA              | 2048-bit | `backend/licensing/keys/private.pem` (vendor-only) |
| License verification   | RSA + SHA-256    | 2048-bit | `backend/licensing/keys/public.pem` (bundled)     |
| API request signing    | HMAC-SHA-256     | 256-bit  | Per-customer, derived from license secrets or env |
| Machine fingerprint    | SHA-256          | 256-bit  | Computed at startup from host identifiers         |
| Binary integrity       | SHA-256          | 256-bit  | Computed at startup from entrypoint file          |
| Image signing          | cosign (Sigstore)| ECDSA P-256 | Vendor-managed keypair                         |

PKCS#1v15 padding for RSA. The `cryptography` library handles all primitives — no hand-rolled crypto.

---

## 9. Operational Practices

### Secret Rotation (Customer)

```bash
# 1. Update the secret in your store
sudo vim /etc/hanacv2sql/secrets.env

# 2. Restart the backend — picks up new value, no rebuild
docker compose -f docker-compose.enterprise.yml restart backend

# 3. Verify
docker compose logs backend --tail=20
```

No image rebuild. No vendor involvement. ~10 seconds of downtime.

### Key Rotation (Vendor)

`backend/licensing/keys/public.pem` ships with the app. To rotate the signing keypair:

1. Generate new keypair: `python -m licensing generate-keys --out-dir /secure/new_keys`.
2. Re-sign every active license with the new private key.
3. Ship a new `public.pem` in the next app release.
4. Force-upgrade window: every customer must update within X days.
5. Document in customer release notes.

### Incident Response: Private Key Compromised

If the vendor's `private.pem` is compromised:

1. **Immediately** generate a new keypair.
2. Re-sign and re-issue every active license (target: < 24 hours).
3. Force-upgrade the `public.pem` in the app — every customer must update.
4. Communicate transparently with all customers.
5. Investigate root cause and patch.
6. Old licenses stop working (their old `public.pem` no longer matches the new one) — this is by design.

### Incident Response: Customer Deployment Compromised

If a customer suspects their deployment is compromised:

1. Pull the latest image, restart from a clean `license.json`.
2. Rotate the HMAC signing key (`H2S_HMAC_KEY` env var).
3. Rotate the Gemini API key in Google AI Studio.
4. Review access logs for unfamiliar requests.
5. Contact vendor support.

---

## 10. Honest Limitations

| Threat                                              | Stopped? |
|-----------------------------------------------------|----------|
| Customer runs `docker save` and inspects layers      | ❌ Image contents are visible by design |
| Sophisticated RE engineer spends months decompiling  | ❌ Possible, just unprofitable |
| Customer rewrites frontend (no HMAC key)             | ✅ All `/api/*` routes require valid HMAC |
| Customer edits `license.json`                       | ✅ Signature invalidates                |
| Customer copies license to second VM                | ✅ Fingerprint mismatch                 |
| Customer repackages image (changes binary)          | ✅ `binary_sha256` mismatch in license  |
| Customer clones the entire VM (same fingerprint)    | ⚠️ Mitigation: regenerate `machine-id` post-clone |
| Insider leaks vendor's `private.pem`                 | ❌ Game over — re-sign everything       |
| Nation-state attacker                                | ❌ Out of scope; customer's ops responsibility |
| Clean-room RE that re-implements algorithms          | ❌ Legal in most jurisdictions           |

**The honest framing for sales / leadership:**

> "We don't try to make reverse engineering impossible. We make it unprofitable. Our license system ensures the binary has no commercial value without a per-machine license we control. Our hardening raises the cost of RE above the cost of just licensing the product. Anyone determined to RE it anyway is unlikely to have been a paying customer in the first place."

---

## 11. Customer Self-Audit

A customer's security team can independently verify the deployment at any time:

```bash
# 1. Verify no secrets in the running image
docker exec hanacv2sql-backend env | grep -E '(KEY|TOKEN|SECRET)'
# expected: only NON-secret vars (PORT, FLASK_ENV, OUTPUT_DIR, license host-bind paths)

# 2. Verify license integrity
curl http://localhost:8080/health | jq .license
# expected: license_id, customer, expires_at, days_remaining

# 3. Verify image signature
cosign verify --key cosign.pub your-registry/hanacv2sql-backend@sha256:abc...

# 4. Scan for known CVEs in image dependencies
trivy image your-registry/hanacv2sql-backend:v1.2.3

# 5. Check for unexpected processes / network connections
docker exec hanacv2sql-backend ps aux
docker exec hanacv2sql-backend netstat -tlnp 2>/dev/null || ss -tlnp
# expected: only the Flask process listening on port 8080, outbound to generativelanguage.googleapis.com
```

---

## 12. Responsible Disclosure

If you discover a security issue in HANACV2SQL Enterprise:

- Email: security@your-vendor-domain
- PGP key: posted at https://your-vendor-domain/.well-known/pgp-key.txt
- We aim to acknowledge within 24 hours and patch within 7 days for critical issues.
- Bounty: $500–$5,000 depending on severity (no bounty for license bypasses — these are contract violations, not security issues).

---

## 13. Compliance Notes

This deployment supports common enterprise compliance requirements:

- **Data residency**: all data stays on the customer's VM. No data leaves their environment except outbound HTTPS calls to Google AI Studio (Gemini API).
- **Right to audit**: customer's security team can `docker exec` and inspect the running container at any time.
- **Network egress**: documented in `backend/config/settings.py`; outbound-firewall config templates available on request.
- **Vulnerability scanning**: SBOM published per release; `trivy` / `grype` recommended in customer's CI.

For specific compliance frameworks (SOC2, ISO 27001, HIPAA), contact the vendor for an architecture review.

---

## Document Versioning

- **v1.0** — initial release with binary integrity + HMAC + machine binding + role separation
- See git history for prior versions.

---

## Quick-Reference: One Picture for Sales Calls

```
   VENDOR (you)                              CUSTOMER's VM (runs Docker)
   ───────────                              ─────────────────────────────

   ┌────────────────┐                            ┌─────────────────┐
   │  private.pem   │ ──── signs ──────►          │  /etc/machine-id│
   │  (kept offline)│      license.json           │  /sys/.../dmi   │
   └────────────────┘                            └────────┬────────┘
   ┌────────────────┐                                     │ bind-mount
   │  cosign.key    │ ──── signs ─────►           ┌────────▼────────┐
   │  (kept offline)│      Docker image           │ Docker container│
   └────────────────┘                             │ HANACV2SQL      │
                                                  │                 │
   ┌────────────────┐                             │ /host/.../*     │ ◄── host fingerprint
   │  public.pem    │ ──── bundled in ────►      │ /etc/license.d/ │ ◄── vendor-signed license
   │  (in image)    │      the Docker image      │     license.json│
   └────────────────┘                             │                 │
                                                  │ HMAC key (mem)  │ ◄── per-customer
                                                  └────────┬────────┘
                                                           │
                                            App startup:                 │
                                              1. Read license.json        │
                                              2. Verify signature         │
                                              3. Check expires_at         │
                                              4. Recompute fingerprint   │ ◄── reads /host/* not container ID
                                              5. Compare to license       │
                                              6. Verify binary integrity  │
                                                           │
                                            ✅ → start Flask on :8080   │
                                            ❌ → print error, exit 1     │
```

If a prospect asks "but couldn't someone just copy the file?" — point at step 4. The file is useless on a machine whose host fingerprint does not match the one we signed. To get a working file for a second VM, they have to come back to us, and we have every reason to charge them for a second license.