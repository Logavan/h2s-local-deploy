# Client Admin: License Runbook

Step-by-step instructions for installing HANACV2SQL Enterprise on a customer VM. The license is a vendor-signed JSON file bound to your VM's hardware fingerprint — same code path as production, no skip flags or dev-mode bypasses.

**Total time:** ~15 minutes, plus waiting on the vendor to sign your license.

---

## Before you start

You need:

- A Linux VM (Ubuntu 22.04+ recommended) with Docker Engine + Compose v2 installed
- A user account with `sudo` or root access
- Network access to pull the Docker image
- The vendor's contact email for license requests

You'll create one file (`license.json`) and run three commands.

---

## Step 1 — Install Docker (skip if already installed)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
docker --version   # confirm
```

---

## Step 2 — Pull the deployment files

You need the `docker-compose.enterprise.yml` and the `deploy/` directory from the vendor. Either clone the repo or copy them from the release bundle:

```bash
mkdir -p /opt/hanacv2sql
cd /opt/hanacv2sql
# Option A: clone the repo (you'll also need backend/ and frontend/ to build locally)
git clone <vendor-repo-url> .

# Option B: vendor sends you a release tarball
tar -xzf hanacv2sql-enterprise-<version>.tar.gz -C /opt/hanacv2sql
```

---

## Step 3 — Collect your VM's machine fingerprint

The license binds to this fingerprint. The vendor needs it before they can sign your license.

```bash
# Linux VM
cat /etc/machine-id
cat /sys/class/dmi/id/product_uuid

# The fingerprint combines both. Send BOTH values to the vendor.
```

Sample output:

```
$ cat /etc/machine-id
ab12cd34ef5678901234abcdef567890
$ cat /sys/class/dmi/id/product_uuid
A1B2C3D4-E5F6-7890-1234-567890ABCDEF
```

**Email the vendor both values**, along with:

- Your company name
- Desired `max_concurrent_users` (e.g. 5, 10, 50)
- Desired license duration (typically 1 year)
- Whether you want the bind to `/etc/machine-id` only, or include the DMI UUID (recommended)

The vendor will reply with a signed `license.json` within one business day.

---

## Step 4 — Place the signed license

When the vendor emails you `license.json`, save it on the host:

```bash
sudo mkdir -p /etc/hanacv2sql
sudo mv ~/Downloads/license.json /etc/hanacv2sql/license.json
sudo chmod 644 /etc/hanacv2sql/license.json
sudo chown root:root /etc/hanacv2sql/license.json
```

Verify the license is readable and well-formed:

```bash
cat /etc/hanacv2sql/license.json | python3 -m json.tool
# Should print valid JSON with: license_id, customer, issued_at,
# expires_at, machine_hash, signature
```

---

## Step 5 — Create the secrets file

```bash
sudo tee /etc/hanacv2sql/secrets.env > /dev/null <<'EOF'
# Gemini API key (https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=AIza...your-key-here

# HMAC signing key — generate with: openssl rand -base64 32
H2S_HMAC_KEY=<paste-output-of-openssl-rand-base64-32>

# Browser origins that may call the API. Comma-separated.
# Must include your public hostname (with scheme).
H2S_ALLOWED_ORIGINS=https://hanacv2sql.yourdomain.com
EOF

sudo chmod 600 /etc/hanacv2sql/secrets.env
sudo chown root:root /etc/hanacv2sql/secrets.env
```

Generate the HMAC key:

```bash
openssl rand -base64 32
# Copy that output into H2S_HMAC_KEY= above
```

---

## Step 6 — Edit `docker-compose.enterprise.yml`

Open `/opt/hanacv2sql/docker-compose.enterprise.yml` and verify the backend service has:

```yaml
  backend:
    environment:
      - GEMINI_API_KEY_FILE=/run/secrets/gemini_api_key
      - H2S_HMAC_KEY_FILE=/run/secrets/hmac_key
      - H2S_ALLOWED_ORIGINS=https://hanacv2sql.yourdomain.com
      # ... (other env vars as shipped)
    volumes:
      - /etc/machine-id:/host/etc/machine-id:ro
      - /sys/class/dmi/id/product_uuid:/host/sys/class/dmi/id/product_uuid:ro
      - /etc/hanacv2sql/license.json:/etc/license.d/license.json:ro
      - /etc/hanacv2sql/secrets.env:/run/secrets/all.env:ro
    secrets:
      - gemini_api_key
      - hmac_key
```

If the compose file uses a single `secrets.env` file (one file, all env vars), point `GEMINI_API_KEY_FILE` and `H2S_HMAC_KEY_FILE` at that one file — the app reads each var from the file by its env var name + `_FILE` suffix.

---

## Step 7 — Create the named Docker secrets

For each secret in the `secrets:` block, create a file containing the value:

```bash
# gemini_api_key — just the Gemini key
echo "AIza...your-key-here" | sudo tee /etc/hanacv2sql/gemini_api_key.txt > /dev/null
sudo chmod 600 /etc/hanacv2sql/gemini_api_key.txt

# hmac_key — the base64 string from Step 5
echo "<paste-H2S_HMAC_KEY-value>" | sudo tee /etc/hanacv2sql/hmac_key.txt > /dev/null
sudo chmod 600 /etc/hanacv2sql/hmac_key.txt
```

Then declare them in the compose file (top-level, outside any service):

```yaml
secrets:
  gemini_api_key:
    file: /etc/hanacv2sql/gemini_api_key.txt
  hmac_key:
    file: /etc/hanacv2sql/hmac_key.txt
```

(Or use Docker Swarm's `docker secret create` if you're on Swarm — see compose comments.)

---

## Step 8 — Start the stack

```bash
cd /opt/hanacv2sql
docker compose -f docker-compose.enterprise.yml up -d
```

Watch the backend logs to confirm the license passed:

```bash
docker compose -f docker-compose.enterprise.yml logs -f backend
```

You should see:

```
[LICENSE] OK — H2S-ENT-<your-customer>-<year>-<hash> for <Your Company>
           (expires <date>, <N> day(s) remaining, host <your-fingerprint>)
```

If you see any of these instead, see Troubleshooting below:

- `LicenseNotFoundError` → `license.json` missing or path wrong
- `InvalidSignatureError` → file corrupted; request a fresh one from vendor
- `ExpiredLicenseError` → license expired; request renewal
- `MachineMismatchError` → fingerprint drift; usually means `/etc/machine-id` changed (reboot shouldn't cause this — it persists)

---

## Step 9 — Verify /health

```bash
curl -s http://localhost:8080/health | python3 -m json.tool
```

Expected response:

```json
{
  "license": {
    "license_id": "H2S-ENT-...",
    "customer": "Your Company",
    "expires_at": "2027-XX-XXT00:00:00Z",
    "days_remaining": 360,
    "is_container": true
  },
  "service": "hana-cv-converter",
  "status": "healthy",
  "version": "1.0.0"
}
```

`status: "healthy"` and `license.license_id` populated means the gate passed.

---

## Step 10 — Set up TLS (production)

The backend listens on plain HTTP. Don't expose `:8080` to the internet — put nginx in front:

See `deploy/README.md` for the three options:

- **Option A** — system nginx on the host (simplest)
- **Option B** — Let's Encrypt via certbot (auto-renewing)
- **Option C** — nginx as a Docker sidecar

The customer's actual hostname (e.g. `hanacv2sql.yourdomain.com`) is what users put in their browser.

---

## Renewal (every year, when `expires_at` approaches)

About 2 weeks before expiry, you'll see `days_remaining` drop below 14 in `/health`. Renew before it hits 0:

1. Email the vendor: "Please renew license `<license_id>` for another year, same machine."
2. Vendor re-signs the same `license.json` content with a new `expires_at` and emails it back.
3. Replace the file: `sudo cp ~/new-license.json /etc/hanacv2sql/license.json`
4. Restart: `docker compose -f docker-compose.enterprise.yml restart backend`
5. Verify: `curl -s http://localhost:8080/health | grep days_remaining`

No machine rebinding needed — the fingerprint stays the same.

---

## Rebind (VM replaced, NIC swapped, OS reinstalled)

If your VM's primary identifier changes (`/etc/machine-id` regenerates after a wipe, or DMI UUID changes on new hardware), the old license will reject:

```bash
docker compose -f docker-compose.enterprise.yml logs backend | grep -i mismatch
# MachineMismatchError: License is bound to a different machine.
```

Steps:

1. On the new VM, collect the new fingerprint (Step 3 above).
2. Email the vendor: "Please rebind license `<old_license_id>` to this new fingerprint."
3. Vendor signs a new `license.json` bound to the new fingerprint.
4. Replace and restart (same as renewal above).

There's a CLI shortcut inside the running container if you have access:

```bash
docker compose -f docker-compose.enterprise.yml exec backend \
    python -m licensing request-rebind \
    --license-id H2S-ENT-... \
    --previous-machine-hash A73HF8X29P4QM0LR \
    --output /tmp/rebind.json

# Copy /tmp/rebind.json out of the container and email to vendor
docker cp $(docker compose -f docker-compose.enterprise.yml ps -q backend):/tmp/rebind.json ./rebind.json
```

---

## Troubleshooting

### "LicenseNotFoundError: No license file found"

The compose volume mount isn't reaching the container. Verify:

```bash
docker compose -f docker-compose.enterprise.yml exec backend ls -la /etc/license.d/
# Should show: license.json (read-only)

docker compose -f docker-compose.enterprise.yml exec backend cat /etc/license.d/license.json | head -3
# Should show JSON content
```

If empty, check the host path:

```bash
ls -la /etc/hanacv2sql/license.json
# Should exist and be readable
```

### "InvalidSignatureError"

The license file was modified after signing, or it was signed by a different vendor key. **Do not edit the file** — even whitespace changes break the signature. Request a fresh signed license from the vendor.

### "ExpiredLicenseError"

Your license has passed its `expires_at`. Request a renewal (see above).

### "MachineMismatchError"

The fingerprint doesn't match. Three common causes:

1. **You installed on a different VM than the one whose fingerprint you sent.** Fix: send the current VM's fingerprint to the vendor for a rebind.
2. **The host-bind mounts are missing from `docker-compose.yml`.** Without `/etc/machine-id` and `/sys/class/dmi/id/product_uuid` mounted into the container, the verifier falls back to the ephemeral container ID, which differs every restart. Fix: add the mounts (Step 6).
3. **`/etc/machine-id` was regenerated on the host.** This happens on OS reinstall or if systemd's machine-id file is deleted. Fix: rebind with the new fingerprint.

### Frontend shows blank page

Check the frontend container logs — usually a CORS issue:

```bash
docker compose -f docker-compose.enterprise.yml logs frontend
```

If you see "Refusing to start with permissive CORS" or CORS errors in the browser dev tools, verify `H2S_ALLOWED_ORIGINS` in your secrets file matches the public hostname users actually visit (with scheme, no trailing slash).

### "Connection refused" on `:8080` or `:3000`

These ports are NOT meant to be exposed publicly. Use nginx on `:443` instead. For local debugging you can `ssh -L 8080:localhost:8080 user@vm` to forward the port over SSH.

---

## Quick reference — file locations on the host VM

| Path | Contents | Permissions |
|---|---|---|
| `/etc/hanacv2sql/license.json` | Vendor-signed license | `644`, `root:root` |
| `/etc/hanacv2sql/secrets.env` | All env-var secrets | `600`, `root:root` |
| `/etc/hanacv2sql/gemini_api_key.txt` | Gemini key file (compose secrets) | `600`, `root:root` |
| `/etc/hanacv2sql/hmac_key.txt` | HMAC key file (compose secrets) | `600`, `root:root` |
| `/etc/machine-id` | Host fingerprint primary (read by container) | (system) |
| `/sys/class/dmi/id/product_uuid` | Host fingerprint secondary (read by container) | (system) |
| `/opt/hanacv2sql/` | docker-compose.yml + (optional) repo checkout | `755`, `root:root` |
| `/data/outputs/` | HANA CV Converter output ZIPs (mounted into backend container) | (compose-managed) |

---

## What the vendor does NOT need to know

- Your server's IP address or hostname
- Your OS patch level
- Your users' email addresses
- Any data flowing through the application

The license binds to hardware fingerprint only. The vendor signs a JSON file. That's the entire integration.

---

## What the vendor CAN do if you go dark

If you stop paying / lose contact, your existing license keeps working until `expires_at`. There is no phone-home revocation. To revoke early, the vendor rotates their signing keypair — but that invalidates ALL active licenses (yours and others'), so it's a last-resort option. Plan renewals rather than relying on revocation.