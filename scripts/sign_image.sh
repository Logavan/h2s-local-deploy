#!/usr/bin/env bash
# scripts/sign_image.sh
# -----------------------------------------------------------------------------
# Vendor-only: sign a Docker image with cosign and generate an SBOM.
#
# Customers verify on pull:
#     cosign verify --key cosign.pub your-registry/hanacv2sql-backend:v1.2.3
#
# Requires:
#     - cosign  (https://github.com/sigstore/cosign)
#     - syft    (https://github.com/anchore/syft)
#
# First-time setup:
#     cosign generate-key-pair   # creates cosign.key + cosign.pub
#     Store cosign.key OFFLINE (HSM, encrypted vault).
#     Ship cosign.pub to customers via the vendor portal.
# -----------------------------------------------------------------------------
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <image:tag> [cosign-key-path]"
    echo "  image:tag           e.g. your-registry/hanacv2sql-backend:v1.2.3"
    echo "  cosign-key-path     defaults to ./cosign.key"
    exit 1
fi

IMAGE="$1"
KEY_PATH="${2:-cosign.key}"
PUB_PATH="${KEY_PATH%.key}.pub"

if [[ ! -f "${KEY_PATH}" ]]; then
    echo "ERROR: cosign key not found at ${KEY_PATH}"
    echo "Run 'cosign generate-key-pair' first."
    exit 1
fi

# -----------------------------------------------------------------------------
# 1. Sign the image
# -----------------------------------------------------------------------------
echo "==> Signing ${IMAGE} with cosign"
cosign sign \
    --key "${KEY_PATH}" \
    --output-signature "${IMAGE}.sig" \
    --output-certificate "${IMAGE}.cert" \
    "${IMAGE}"

# -----------------------------------------------------------------------------
# 2. Generate SBOM
# -----------------------------------------------------------------------------
echo "==> Generating SBOM (CycloneDX format)"
syft \
    --output cyclonedx-json="${IMAGE}.sbom.json" \
    "${IMAGE}"

# Also produce a human-readable SPDX for audit logs
syft \
    --output spdx="${IMAGE}.sbom.spdx" \
    "${IMAGE}" || true

# -----------------------------------------------------------------------------
# 3. Attach the SBOM to the image as an attestation
# -----------------------------------------------------------------------------
echo "==> Attaching SBOM as in-toto attestation"
cosign attest \
    --key "${KEY_PATH}" \
    --predicate "${IMAGE}.sbom.json" \
    --type cyclonedx \
    "${IMAGE}" || {
        echo "WARNING: cosign attest failed. SBOM is still on disk."
    }

echo ""
echo "==> Done. Artifacts:"
echo "    Image signature : ${IMAGE}.sig"
echo "    Certificate     : ${IMAGE}.cert"
echo "    SBOM (json)     : ${IMAGE}.sbom.json"
echo "    SBOM (spdx)     : ${IMAGE}.sbom.spdx"

# -----------------------------------------------------------------------------
# 4. Print verification command for the customer
# -----------------------------------------------------------------------------
echo ""
echo "==> Customer verification command:"
echo "    cosign verify --key ${PUB_PATH} ${IMAGE}"
echo "    cosign verify-attestation --key ${PUB_PATH} --type cyclonedx ${IMAGE}"