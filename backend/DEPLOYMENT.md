# DEPLOYMENT.md — Admin Team Runbook

> **Audience:** the customer's IT / DevOps team performing the one-time installation of HANACV2SQL Enterprise on the customer's VM.
>
> After this runbook is complete, **regular business users do not need to do anything** to use the app — they just open it in a browser. Configuration changes are always done by the admin team via this runbook.

---

## 0. Prerequisites

- A Linux VM (Ubuntu 20.04+, RHEL 8+, Debian 11+) or Windows Server 2019+
- Docker Engine 24+ and Docker Compose v2+
- Outbound HTTPS to `generativelanguage.googleapis.com` (Gemini API)
- Outbound HTTPS to your container registry (for image pulls)
- 2 vCPU, 4 GB RAM minimum (8 GB recommended for bulk conversions)
- 20 GB free disk space
- Admin / sudo access on the VM

If you don't have a VM yet, see the vendor's [VM Sizing Guide](link).

---

## 1. Get Your Machine Fingerprint

The vendor will issue a license bound to a specific VM. They need to know which VM you're going to use.

```bash
# On the target VM, run:
cat /etc/machine-id
sudo cat /sys/class/dmi/id/product_uuid

# Send both values to the vendor (via your secure channel — not over chat).
# Vendor replies with a signed `license.json` file.
```

Save the `license.json` to `/etc/hanacv2sql/license.json` on the VM (`chmod 644`).

---

## 2. Choose a Secret-Injection Method

You need to provide the app with at minimum:

- `GEMINI_API_KEY` — Google AI Studio API key

Optionally:

- `H2S_HMAC_KEY` — random 32-byte base64 string for HMAC signing (auto-generated if absent)

Pick ONE of the three methods below.

### Method A — env-file (simplest)

Best for: small deployments, single VM, single admin.

```bash
# Create the secrets file
sudo mkdir -p /etc/hanacv2sql
sudo tee /etc/hanacv2sql/secrets.env > /dev/null <<EOF
GEMINI_API_KEY=AIza...your-key-here...
H2S_HMAC_KEY=$(openssl rand -base64 32)
EOF
sudo chmod 600 /etc/hanacv2sql/secrets.env
sudo chown root:root /etc/hanacv2sql/secrets.env
```

### Method B — Docker secrets (Swarm / Compose v3.5+)

Best for: Swarm clusters, Compose files deployed to multiple hosts, ops teams already familiar with Docker secrets.

```bash
echo "AIza...your-key-here..." | docker secret create gemini_api_key -
openssl rand -base64 32 | docker secret create hmac_key -
```

These are mounted into the container at `/run/secrets/<name>` automatically.

### Method C — External secret manager (Vault, AWS SM, Azure KV)

Best for: enterprise secret-management setups with audit trails and automatic rotation.

Out of scope for this runbook — see your vault-agent documentation. The backend supports `*_FILE` env vars that point at mounted files; configure your vault sidecar to write to those paths.

---

## 3. Place the License

```bash
# Save the vendor-supplied license.json
sudo cp license.json /etc/hanacv2sql/license.json
sudo chmod 644 /etc/hanacv2sql/license.json

# Verify
cat /etc/hanacv2sql/license.json | python -m json.tool
```

---

## 4. First Launch

Pull the image from your registry:

```bash
# Login to your registry (one-time)
docker login your-registry.example.com

# Pull the images
docker compose -f docker-compose.enterprise.yml pull
```

If using **Method A** (env-file), edit `docker-compose.enterprise.yml` and uncomment the env-file volume mount:

```yaml
volumes:
  - /etc/hanacv2sql/secrets.env:/run/secrets/all.env:ro
```

Then update the `environment:` block to load from the file:

```yaml
environment:
  - GEMINI_API_KEY_FILE=/run/secrets/all.env
```

(See `docker-compose.enterprise.yml` for the exact lines.)

Start the stack:

```bash
docker compose -f docker-compose.enterprise.yml up -d
```

---

## 5. Smoke Test

```bash
# Wait 30 seconds for startup, then:
curl http://localhost:8080/health

# Expected response (excerpt):
# {
#   "status": "healthy",
#   "license": {
#     "license_id": "H2S-ENT-...",
#     "customer": "Acme Corp",
#     "expires_at": "2027-...",
#     "days_remaining": 360
#   }
# }

# If days_remaining < 0 → license expired; contact vendor.
# If status != "healthy" → see Troubleshooting section below.
```

Open `http://<vm-hostname-or-ip>:3000` in a browser. You should see the HANACV2SQL home page.

---

## 6. Provide Access to Users

Tell users: **"go to http://hanacv2sql.your-domain:3000"**. They do not need any credentials, env vars, or configuration. If they ask how to configure something, the answer is "ask your admin" — not "edit the env vars".

For HTTPS termination, put a reverse proxy (nginx, Caddy, Traefik) in front of the app. Recommended:

```nginx
server {
    listen 443 ssl;
    server_name hanacv2sql.your-domain;

    ssl_certificate /etc/letsencrypt/live/hanacv2sql.your-domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hanacv2sql.your-domain/privkey.pem;

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/ {
        proxy_pass http://localhost:8080/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 7. Secret Rotation

When a key rotates (Gemini quota refresh, employee offboarding, etc.):

```bash
# 1. Update the secret in your store
sudo vim /etc/hanacv2sql/secrets.env

# 2. Restart the backend — picks up new value, no rebuild
docker compose -f docker-compose.enterprise.yml restart backend

# 3. Verify
docker compose -f docker-compose.enterprise.yml logs backend | tail -20
```

No image rebuild. No vendor involvement. ~10 seconds of downtime.

---

## 8. Backup

Back up:

- `/etc/hanacv2sql/license.json` — losing this requires a vendor reissue
- `/etc/hanacv2sql/secrets.env` — losing this requires regenerating the API key in Google AI Studio
- `./data/outputs/` — converted SQL outputs (if you want to preserve past conversions)

Do NOT back up:

- The Docker image — re-pull from registry
- Container volumes other than `data/outputs`

```bash
# Example backup script
tar -czf hanacv2sql-backup-$(date +%Y%m%d).tar.gz \
    /etc/hanacv2sql/ \
    ./data/outputs/
```

---

## 9. Updates

```bash
# Pull the latest image
docker compose -f docker-compose.enterprise.yml pull

# Restart with the new image (license is preserved, secrets are preserved)
docker compose -f docker-compose.enterprise.yml up -d

# Verify
curl http://localhost:8080/health
```

A major version bump (e.g. v1 → v2) may require a license reissue. Minor/patch versions never do.

---

## 10. Troubleshooting

### "License check failed: LicenseNotFoundError"

- `license.json` is not where the app expects it.
- Fix: place it at `/etc/hanacv2sql/license.json` AND verify the `volumes:` mount in `docker-compose.enterprise.yml` is correct.

### "License check failed: ExpiredLicenseError"

- `expires_at` is in the past.
- Fix: request a renewal from the vendor (typically a 24-hour turnaround).

### "License check failed: MachineMismatchError"

- The license was issued for a different VM (you moved the deployment).
- Fix: run `docker compose -f docker-compose.enterprise.yml exec backend python -m licensing request-rebind --license-id <id> --previous-machine-hash <hash>`, send the resulting `rebind_request_*.json` to the vendor, get a new license back.

### "License check failed: BinaryIntegrityError"

- The deployed image has been modified or replaced with a non-vendor build.
- Fix: re-pull the official image (`docker compose pull`) and restart.

### "Missing or invalid HMAC signature" (401)

- The frontend can't talk to the backend.
- Fix: ensure the frontend can reach `/api/hmac/key` on the backend (check `NEXT_PUBLIC_API_BASE_URL` in frontend env, check network ACLs).

### The app is up but `/health` returns non-healthy

```bash
# Check the backend logs
docker compose -f docker-compose.enterprise.yml logs backend --tail=50

# Common issues:
# - "GEMINI_API_KEY is missing or set to a placeholder value"
#   → see Method A/B/C above; ensure the key is actually provided at start.
# - "Could not collect any stable machine identifier"
#   → ensure /etc/machine-id is bind-mounted (see docker-compose.yml).
```

---

## 11. What Users Should NOT Do

This is what you tell your business users when you onboard them:

> **Users do not:**
> - Set environment variables
> - Edit configuration files
> - Restart the container
> - Modify the license file
> - Run the app from anywhere except http://hanacv2sql.your-domain:3000
>
> **If the app is misbehaving, contact your administrator.** The administrator manages the deployment and can resolve issues without involving the vendor in most cases.

This single paragraph eliminates 90% of confused-user support tickets.

---

## 12. Out-of-Scope for This Runbook

- Setting up the VM itself (see VM Sizing Guide)
- Configuring HTTPS / TLS certificates
- Setting up SSO / SAML / OIDC
- Database backup strategies for the customer's other systems
- Network firewall configuration

For these, engage your standard IT procedures or contact the vendor for a deployment-services quote.