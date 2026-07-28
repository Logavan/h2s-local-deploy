# HANACV2SQL Enterprise Licensing — Plain-English Guide

This document explains **how licensing works** in the HANACV2SQL Enterprise deployment — the concepts, the moving parts, and what happens in real-world scenarios. It is written so a non-engineer can follow it, and so you (the author / vendor) can confidently explain it to a client, a salesperson, or anyone else who asks "but how do you stop people from just copying the license to another server?"

---

## TL;DR (30-second version)

> We sign every customer's `license.json` with our **private key** (kept offline on our side) and lock it to **one machine** by including that machine's hardware fingerprint in the signed payload. When the app starts, it reads the license, **verifies our signature** with our bundled **public key**, and **re-checks the fingerprint**. If the file is tampered with, expired, or copied to a different server — the app refuses to start. Customers whose hardware genuinely changes (new VM, new NIC, etc.) can request a re-signed license from us in minutes.

That is the whole idea. Everything below is detail on how the moving parts fit together.

---

## 1. The Problem We're Solving

The HANACV2SQL Enterprise edition is a self-contained Python app a customer runs **on their own machine or VM**. We deliver it as code — there is no SaaS backend we control. That is a feature (no cloud dependency, works fully air-gapped), but it creates a piracy risk:

- A customer could copy `license.json` to a second machine and run two installations from one paid license.
- A customer could edit `license.json` to extend the expiry date.
- A customer could share the license file with someone who never paid us.

Our licensing system defeats all three of these.

---

## 2. Who's Who — The Four Actors

```
┌─────────────────┐         ┌────────────────────────────┐
│     VENDOR      │         │         CUSTOMER            │
│   (you / us)    │         │                             │
│                 │         │   ┌─────────────────────┐   │
│ • Holds the     │  ─────► │   │  Their machine / VM │   │
│   private key   │  signs  │   │  ────────────────   │   │
│   offline       │ license │   │  runs HANACV2SQL    │   │
│ • Bundles the   │  files  │   │  Enterprise app     │   │
│   public key    │         │   └─────────────────────┘   │
│   in the app    │         │                             │
└─────────────────┘         └────────────────────────────┘
```

| Actor                | Role                                                                  | What they have                              |
|----------------------|------------------------------------------------------------------------|---------------------------------------------|
| **Vendor** (us)      | Issues and signs licenses; renews; unbinds                              | `private.pem` (NEVER sent to customers)     |
| **Customer**         | Receives signed license, installs it on their machine                  | `license.json`, the running app             |
| **Their machine**    | The single host the license is bound to                                | Hardware identifiers we fingerprint         |
| **The app**          | Verifies license + signature + machine on every startup                | Bundled `public.pem` (read-only)            |

The app contains our **public key**. The public key can *only verify* — it cannot *sign*. So even if a customer reverse-engineers the entire app, they cannot produce a license file we did not sign.

---

## 3. How It Works — The Plain-English Version

### 3.1 The "fingerprint" — what makes a machine unique

We don't try to identify a machine by a single brittle thing (just a MAC address, just a serial number). Instead we collect several stable identifiers from the operating system:

| Identifier              | Where it comes from                                       | Why it matters                              |
|-------------------------|-----------------------------------------------------------|---------------------------------------------|
| **machine-id / GUID**   | `/etc/machine-id` on Linux, registry on Windows, `ioreg` on macOS | Set when the OS was installed; survives reboots and NIC swaps |
| **DMI / motherboard UUID** | SMBIOS data (motherboard serial)                       | Tied to the physical/virtual hardware       |
| **MAC address**         | First non-virtual network interface                      | A weak signal alone, useful as a tiebreaker |
| **Hostname + OS + CPU** | Always available                                          | Last-resort fallback if the above fail      |

We combine all of them into a JSON document, then run it through **SHA-256**. The result is a 64-character hex string — the "machine hash". Any single change to the underlying identifiers produces a totally different hash. So a fresh VM, even one cloned from the same template, has its own machine-id and thus its own hash.

### 3.2 The "signature" — how we prove a license is real

When we issue a license, we take the JSON document containing the customer's identity, the expiry date, and the machine hash — and we **sign** it with our private key. The signature is just another long string appended to the document:

```
license.json
─────────────────
{
  "license_id":   "H2S-ENT-2026-AB12-CD34",
  "customer":     "Acme Corp",
  "issued_at":    "2026-07-28T00:00:00Z",
  "expires_at":   "2027-07-28T00:00:00Z",
  "machine_hash": "a73hf8x29p4qm0lr...",     ← this customer's machine
  "max_concurrent_users": 5,
  "signature":    "FeStf2qOSCFI732M..."       ← our private key signed everything above
}
```

Signing works mathematically only one way: **the signature was produced by someone who held the private key**. We hold the private key; the customer never sees it. So when the customer's app uses the bundled public key to check the signature and it matches, we have cryptographic proof that:

1. The license was issued by us (the only party with the private key).
2. The license was not edited after we issued it (any edit would invalidate the signature).
3. The license was issued for this exact machine (because the machine hash is part of what we signed).

### 3.3 The startup check — what happens when the app boots

Every time `flask_app.py` starts, **before** Flask even constructs the application object, this chain runs:

```
   flask_app.py boots
         │
         ▼
   Read license.json from disk
         │
         ├─── missing ──────────►  ❌  "License check failed: LicenseNotFoundError"
         │
         ▼
   Verify the RSA signature with the bundled public key
         │
         ├─── bad signature ────►  ❌  "License check failed: InvalidSignatureError"
         │                          (file was tampered with OR signed by a different vendor)
         ▼
   Check expires_at > today's date
         │
         ├─── expired ──────────►  ❌  "License check failed: ExpiredLicenseError"
         ▼
   Compute this machine's fingerprint
         │
         ├─── can't compute ────►  ❌  "Could not collect any stable machine identifier"
         │
         ▼
   Compare license.machine_hash to current fingerprint
         │
         ├─── mismatch ─────────►  ❌  "License check failed: MachineMismatchError"
         │                          (license was copied to a different machine)
         ▼
   ✅  All checks passed → start Flask on port 8080
```

The whole check takes a few milliseconds. If anything fails, the process exits with code 1 and a single human-readable line on stderr. The license secrets themselves (raw fingerprint, signature blob) are **never logged** — only the exception name and a short summary.

---

## 4. The License File — Field by Field

| Field                     | What it means                                                                |
|---------------------------|-------------------------------------------------------------------------------|
| `license_id`              | Unique identifier we issue (e.g. `H2S-ENT-2026-AB12-CD34`). Used in support tickets. |
| `customer`                | The legal entity / team the license is issued to. Display-only.               |
| `issued_at`               | ISO-8601 timestamp; when we signed it.                                        |
| `expires_at`              | ISO-8601 timestamp; after this the app refuses to start.                      |
| `max_concurrent_users`    | Reserved for future floating-license enforcement. Currently informational.    |
| `machine_hash`            | The 16- or 64-character fingerprint of the host the license is locked to.    |
| `signature`               | Base64 RSA-2048 signature over all the above fields.                          |

**Important:** every field *except* `signature` is part of what we sign. If a customer changes even one character (e.g. extending `expires_at`), the signature no longer verifies, and the app refuses to start.

---

## 5. The Full Lifecycle

### 5.1 One-time setup (vendor side, done once per keypair rotation)

```bash
# Generate a fresh RSA-2048 keypair
python -m licensing generate-keys --out-dir /secure/vendor_keys

# → /secure/vendor_keys/private.pem    (NEVER leave this box)
# → /secure/vendor_keys/public.pem     (bundle this into the app)
```

We `chmod 600` the private key automatically. In production, store it on an air-gapped or HSM-backed machine. We rotate the keypair by re-signing every active license with the new key and shipping a new `public.pem` with an app update.

### 5.2 Per-customer onboarding

```
   ┌──────────┐                              ┌──────────────┐
   │ CUSTOMER │                              │   VENDOR     │
   └────┬─────┘                              └──────┬───────┘
        │  1. Run "python -m licensing info"        │
        │     → sends back machine fingerprint      │
        │──────────────────────────────────────────►│
        │                                           │
        │                              2. Craft license.json
        │                                 with that fingerprint
        │                              3. Sign with private.pem
        │                                           │
        │  4. Receive signed license.json           │
        │◄──────────────────────────────────────────│
        │                                           │
   ┌────┴─────┐                                    │
   │ CUSTOMER │                                    │
   │  (cont.) │  5. python -m licensing apply       │
   │          │     license.json                    │
   │          │  6. python flask_app.py             │
   │          │     → app starts ✓                  │
   └──────────┘                                    │
```

### 5.3 Day-to-day operation

- The app verifies the license **once at startup**. There is no periodic phone-home, no online check, no internet required. This was a design goal — the app must work on air-gapped networks.
- `/health` returns license metadata (license ID, customer, expiry, days remaining) so monitoring tools can alert before expiry.

### 5.4 Renewal

30 days before `expires_at` we email the customer a freshly signed license.json (same machine hash, new expiry date). They drop it in place via `python -m licensing apply new_license.json` and restart.

### 5.5 Re-binding (machine changed)

Real reasons customers need to re-bind:

- They migrated to a new VM.
- They replaced a network card and the MAC changed.
- They re-installed the OS and the machine-id regenerated.
- They moved from on-prem to a cloud VM.

In every case the procedure is the same:

```bash
# Customer side
python -m licensing request-rebind \
    --license-id H2S-ENT-2026-AB12-CD34 \
    --previous-machine-hash A73HF8X29P4QM0LR

# → produces rebind_request_<short>.json containing the NEW fingerprint
# Customer emails it to us
```

We sign a new license with the **new** fingerprint and email it back. The customer applies it the same way as a renewal. Turnaround is typically a few minutes during business hours.

---

## 6. Deployment Models — Where Can It Run?

> **Primary deployment model:** HANACV2SQL Enterprise is delivered as a **Docker image** that customers run on their own server / VM. The license is bound to the **host machine** (the VM running Docker), not to the container. This section explains how.

### 6.1 The Docker-only model (default)

A container's own ID is ephemeral — it changes on every restart, image upgrade, or `docker compose down/up`. If we bound the license to the container ID, every restart would invalidate it. So instead, we **bind-mount two host files** into the container at known paths and read those instead:

| Host path (customer's VM)                | Mounted into container as               | Why we read it                                          |
|------------------------------------------|------------------------------------------|---------------------------------------------------------|
| `/etc/machine-id`                        | `/host/etc/machine-id` (read-only)       | Stable across reboots, NIC swaps, OS patches. Set when the VM was created. |
| `/sys/class/dmi/id/product_uuid`         | `/host/sys/class/dmi/id/product_uuid` (read-only) | SMBIOS motherboard UUID. Survives even disk replacement. |

The customer's signed `license.json` is mounted read-only into the container at `/etc/license.d/license.json`.

`docker-compose.enterprise.yml` already wires all three mounts. The relevant snippet:

```yaml
services:
  backend:
    volumes:
      # 1. Host machine-id
      - /etc/machine-id:/host/etc/machine-id:ro
      # 2. Host motherboard UUID
      - /sys/class/dmi/id/product_uuid:/host/sys/class/dmi/id/product_uuid:ro
      # 3. Signed license.json (place next to docker-compose.yml, or change path)
      - ./license.json:/etc/license.d/license.json:ro
```

For plain `docker run` (no compose):

```bash
docker run -d \
  --name hanacv2sql-backend \
  -p 8080:8080 \
  -v /etc/machine-id:/host/etc/machine-id:ro \
  -v /sys/class/dmi/id/product_uuid:/host/sys/class/dmi/id/product_uuid:ro \
  -v /path/to/license.json:/etc/license.d/license.json:ro \
  your-registry/hanacv2sql-backend:latest
```

### 6.2 What this means in practice

| Scenario                                            | What happens                                                                  |
|-----------------------------------------------------|--------------------------------------------------------------------------------|
| Customer restarts the container                      | ✅ License still valid (fingerprint comes from the host, not the container)    |
| Customer upgrades the image (`docker compose pull`) | ✅ License still valid                                                        |
| Customer reboots the host VM                         | ✅ License still valid                                                        |
| Customer replaces the NIC                            | ✅ License still valid (host machine-id is unchanged)                          |
| Customer clones the VM and starts a second one      | ❌ Second VM has its own `/etc/machine-id` → different fingerprint → needs a second license (this is the system working as intended) |
| Customer runs the container on a different VM        | ❌ Different host machine-id → different fingerprint → needs a rebind          |
| Customer runs multiple containers on the same VM     | ✅ All containers see the same host fingerprint → share one license           |

### 6.3 Other deployment modes (legacy / special cases)

| Deployment                | Supported? | Notes                                                                                       |
|---------------------------|------------|----------------------------------------------------------------------------------------------|
| Docker on customer VM     | ✅ Primary | As above — bind mounts required.                                                            |
| Kubernetes (single pod pinned to a node) | ✅ | Same mounts work via `hostPath` volumes. Fingerprint is effectively the node's.        |
| Kubernetes (multiple pods on one node)   | ✅ | All pods share the same fingerprint via the node's host-bind. One license per node.     |
| Bare-metal Linux (no Docker)            | ✅ | App reads `/etc/machine-id` and `/sys/class/dmi/id/product_uuid` directly. No mounts needed. |
| Windows Server / desktop  | ✅         | Reads `MachineGuid` from the registry. Survives reboots, NIC swaps, even most Windows updates. |
| macOS workstation         | ✅         | Reads `IOPlatformUUID`. (Less common — Docker-on-macOS is dev-only.)                       |
| AWS / GCP / Azure VM      | ✅         | Cloud-init regenerates `/etc/machine-id` per instance — same model as any Linux VM.        |
| Local dev / laptop (Docker or bare-metal) | ✅         | Same code path as production. The dev laptop must have its own vendor-signed license (`laptop-license.json` bound to its fingerprint). There is no skip flag or dev-mode bypass. |

### 6.4 What is *not* used as a primary identifier, and why

| Source                      | Why we don't rely on it alone                                       |
|-----------------------------|-----------------------------------------------------------------------|
| Public IP address            | Changes every time a customer re-IPs their NAT, no relation to the machine. |
| Volume serial number         | Customers reattach disks freely; doesn't identify the host.            |
| TPM / Secure Enclave         | Excellent but not universally available; we treat it as a bonus signal when present. |
| Software-only "hardware ID"  | Trivially patchable; useless against a determined attacker.            |
| Container ID alone           | Ephemeral — changes on every restart. We only fall back to it when no host bind mount is configured. |

We use *what the OS reports about itself* — the same data every enterprise management tool (Active Directory, Jamf, MDM) uses to identify machines.

---

## 7. What Happens When Things Go Wrong

These are the scenarios you will actually encounter. Every one has a clear resolution.

### 7.1 "The app won't start — LicenseNotFoundError"

**Cause:** `license.json` is not where the app expects it.

**Fix:**
- Place `license.json` next to `flask_app.py` (i.e. in `backend/`), **or**
- Set `LICENSE_PATH=/full/path/to/license.json` in the environment.

### 7.2 "The app won't start — InvalidSignatureError"

**Cause:** the file has been edited, corrupted in transit, or signed by a different vendor key.

**Fix:** request a fresh signed license from the vendor. Do not try to repair the file.

### 7.3 "The app won't start — ExpiredLicenseError"

**Cause:** `expires_at` is in the past.

**Fix:** request a renewal. The vendor re-signs the same license with a new `expires_at` and emails it back. No machine rebinding needed.

### 7.4 "The app won't start — MachineMismatchError"

**Cause:** the machine fingerprint does not match what the license was signed for.

**This is the most common support ticket.** Typical reasons:

| Reason                                                  | What to do                                                       |
|---------------------------------------------------------|------------------------------------------------------------------|
| Customer moved the app to a new VM                      | Run `python -m licensing request-rebind`, email vendor.          |
| NIC was replaced                                        | Same as above (MAC is part of the fingerprint).                  |
| OS was reinstalled                                      | Same as above (machine-id regenerates).                          |
| Customer accidentally copied license.json to a second box | **This is the system working as intended.** They need a second license. |
| Dev environment was cloned from production              | The dev clone needs its own vendor-signed license (run `python -m licensing info` on the dev host and request a fresh signing). There is no skip flag. |
| **Docker: host-bind mounts missing**                    | Add the three bind mounts shown in §6.1 to `docker-compose.yml` and restart. The app will then read the host's machine-id instead of the ephemeral container ID. |
| **Docker: image moved to a new VM**                     | Same as "moved the app to a new VM" above — host machine-id changed, request a rebind. |

### 7.5 "The fingerprint is unstable on my machine"

**Cause:** the host's primary identifier keeps changing (e.g. a container that restarts with a fresh ID every time).

**Fix:** bind the license to a *persistent* host (a VM, a bare-metal server, a Kubernetes node) rather than to an ephemeral container. Add the three bind mounts shown in §6.1 to `docker-compose.yml` and restart. Short-lived dev containers must still have a license — the bundled `laptop-license.json` works on any laptop it was signed for.

### 7.6 "I lost my private key"

**This is a vendor-only disaster scenario.**

**Consequence:** every license signed by that key becomes unverifiable. Customers will see `InvalidSignatureError`.

**Fix:**
1. Generate a fresh keypair: `python -m licensing generate-keys`.
2. Re-sign every active license with the new private key.
3. Ship a new `public.pem` with an app update (must coordinate a forced upgrade window).
4. Email every customer their new `license.json`.

**Prevention:** keep `private.pem` in at least two secure offline locations. Back it up. Treat it like a root CA key.

---

## 8. Honest Limitations — What This System Does *Not* Stop

Anyone telling you their licensing is "unbreakable" is lying. Here's what ours does and doesn't do:

| Threat                                              | Stopped? | Why / why not                                                              |
|-----------------------------------------------------|----------|-----------------------------------------------------------------------------|
| Customer copies license.json to a 2nd VM            | ✅       | Different fingerprint, signature mismatch.                                  |
| Customer edits `expires_at` to extend the license  | ✅       | Edit invalidates the signature.                                             |
| Customer shares `license.json` with a non-customer | ✅       | Recipient's machine has a different fingerprint.                            |
| Customer clones the entire VM (same fingerprint)    | ⚠️      | Cloning preserves the fingerprint. Mitigation: instruct customers to use unique hostnames / regenerate `machine-id` post-clone. |
| Sophisticated attacker patches the binary to skip the check | ⚠️      | Possible in theory. Mitigation: ship as compiled/wrapped code; legal contract; periodic integrity checks via `/health`. |
| Attacker spoofs the MAC address                     | ⚠️      | Possible but unusual; DMI UUID and machine-id provide redundancy.           |
| Insider leaks the vendor's `private.pem`            | ❌       | Game over for everyone — same as any PKI root compromise. Mitigated by keeping `private.pem` offline + access-controlled. |

This is the same threat model every commercial software vendor uses (Microsoft, Oracle, Adobe all rely on hardware-bound activation). The goal is to **raise the cost of piracy above the cost of a license** — not to make piracy mathematically impossible.

---

## 9. Frequently Asked Questions

**Q: Does the app need internet access?**
A: No. All license checks are local. The app works fully air-gapped.

**Q: Can the app "phone home" to check the license?**
A: No, by design. The signature is checked locally with the bundled public key.

**Q: What if a customer's clock is wrong?**
A: `expires_at` is compared against the system clock. If the customer's clock is set years in the past they could bypass the expiry check — but only locally; on next restart with a corrected clock the check re-runs. We recommend NTP.

**Q: Can we move from RSA-2048 to RSA-4096 / PSS / Ed25519 later?**
A: Yes, but it requires re-signing every active license. PKCS#1v15 + SHA-256 was chosen for broadest support. Do not change the algorithm without a coordinated migration plan.

**Q: Why SHA-256 over a JSON blob and not a custom binary format?**
A: JSON is auditable. A customer (or auditor) can read the license and verify by hand what fields are signed. Binary formats are opaque and create support tickets.

**Q: Does this work with Kubernetes / auto-scaling?**
A: Each pod inherits its node's fingerprint via the host-bind mount, so auto-scaling groups work as long as pods stay on the same node, or you license per-node. For per-pod licensing in K8s we recommend licensing the **node** and letting pods scale freely underneath.

**Q: We deploy via Docker. Does the license survive a container restart?**
A: Yes — that's exactly why we bind to the host. See §6.1 for the three required bind-mounts. As long as the host VM is the same, the fingerprint is unchanged across `docker restart`, `docker compose down/up`, and image upgrades.

**Q: We're running a container orchestrator (K8s, ECS, Nomad). Does the license survive a pod migration?**
A: Only if the pod lands on a node with the same host-bind mounts pointing to the same host files. In practice, license per-node (one license per host VM) and let pods scale freely underneath. Migrating pods to a new node will trigger MachineMismatchError — that's intentional.

**Q: What's the operational overhead per customer?**
A: One license per machine, one renewal per year, occasional re-binds on hardware changes. There is no live dashboard; we issue by email.

**Q: Can a customer self-service the rebind?**
A: They can run `request-rebind` themselves and email us the file, but the new license still has to come from us (because signing needs our private key). Turnaround is the bottleneck.

---

## 10. Summary Diagram — One Picture to Show People

```
   VENDOR (us)                              CUSTOMER's VM (runs Docker)
   ───────────                              ─────────────────────────────

   ┌────────────────┐                            ┌─────────────────┐
   │  private.pem   │ ──── signs ──────►          │  /etc/machine-id│
   │  (kept offline)│      license.json           │  /sys/.../dmi   │
   └────────────────┘                            └────────┬────────┘
                                                         │ bind-mount
   ┌────────────────┐                                    ▼
   │  public.pem    │ ──── bundled in ────►   ┌──────────────────────┐
   │  (read-only)   │      the Docker image  │ Docker container     │
   └────────────────┘                        │  HANACV2SQL backend  │
                                            │                      │
                                            │  /host/etc/machine-id│ ◄── host fingerprint
                                            │  /host/.../dmi_uuid  │
                                            │  /etc/license.d/     │ ◄── vendor-signed license.json
                                            │     license.json     │
                                            └──────────┬───────────┘
                                                       │
                                            App startup:                │
                                              1. Read license.json       │
                                              2. Verify signature        │
                                              3. Check expires_at        │
                                              4. Recompute fingerprint  │ ◄── reads /host/* not container ID
                                              5. Compare to license      │
                                                       │
                                            ✅ → start Flask on :8080  │
                                            ❌ → print error, exit 1     │
```

That is the whole system. If a prospect asks "but couldn't someone just copy the file?" — point at step 5. The file is useless on a machine whose host fingerprint does not match the one we signed. To get a working file for a second VM, they have to come back to us, and we have every reason to charge them for a second license.

---

## 11. Quick Reference — Commands You'll Actually Use

**Customer-facing commands:**

```bash
python -m licensing info                    # show fingerprint + license status
python -m licensing verify                  # re-run the full check chain
python -m licensing apply <license.json>    # install a signed license
python -m licensing request-rebind          # generate a file to email us
```

**Vendor-only commands:**

```bash
python -m licensing generate-keys           # create a new RSA keypair
python -m licensing sign <license.json>     # sign a license file
```

For the deep technical reference (module structure, env vars, fingerprint sources per OS, security notes, test suite) see [`claude.md`](./claude.md).