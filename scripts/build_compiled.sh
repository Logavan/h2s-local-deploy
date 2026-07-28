#!/usr/bin/env bash
# scripts/build_compiled.sh
# -----------------------------------------------------------------------------
# Vendor-only: compile the Python backend with Nuitka for ship-out.
#
# Produces a standalone native binary that's much harder to decompile than
# .pyc bytecode. The resulting binary is what gets baked into the Docker
# image (see backend/Dockerfile.enterprise — switch ENTRYPOINT to use the
# compiled artifact instead of `python flask_app.py`).
#
# Usage:
#     ./scripts/build_compiled.sh [output_dir]
#
# Requires:
#     pip install nuitka
# -----------------------------------------------------------------------------
set -euo pipefail

OUTPUT_DIR="${1:-dist}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../backend" && pwd)"

mkdir -p "${OUTPUT_DIR}"

echo "==> Compiling flask_app.py with Nuitka"
cd "${BACKEND_DIR}"

# --standalone       : produce a self-contained binary (no Python install needed)
# --onefile          : bundle everything into a single executable
# --include-package  : force inclusion of these (Nuitka can't always detect dynamic imports)
# --enable-plugin=anti-bloat : strip unused modules
# --remove-output   : keep build dir clean
# --show-Progress   : human-friendly progress
# --output-filename  : name the resulting binary
python -m nuitka \
    --standalone \
    --onefile \
    --output-filename=hanacv2sql-backend \
    --output-dir="${OUTPUT_DIR}" \
    --include-package=flask \
    --include-package=cryptography \
    --include-package=google.generativeai \
    --include-package=openai \
    --include-package=google.cloud.bigquery \
    --include-package=pandas \
    --include-package=sqlglot \
    --include-package=openpyxl \
    --include-package=licensing \
    --include-package=hmac_auth \
    --enable-plugin=anti-bloat \
    --show-Progress \
    --remove-output \
    flask_app.py

echo "==> Done. Binary at: ${OUTPUT_DIR}/hanacv2sql-backend"

# -----------------------------------------------------------------------------
# Verify the binary works
# -----------------------------------------------------------------------------
echo "==> Smoke test (--help)"
"${OUTPUT_DIR}/hanacv2sql-backend" --help 2>&1 | head -5 || true

echo ""
echo "==> Build complete. To use this binary in the Docker image:"
echo "    1. Copy ${OUTPUT_DIR}/hanacv2sql-backend into the build context"
echo "    2. Update backend/Dockerfile.enterprise ENTRYPOINT to:"
echo "       CMD [\"./hanacv2sql-backend\"]"
echo "    3. Compute SHA-256 for the license binary_sha256 field:"
echo "       sha256sum ${OUTPUT_DIR}/hanacv2sql-backend"
echo "    4. Vendor signs license.json with that SHA-256 in binary_sha256."
echo ""
echo "Note: customers who need to debug the binary can set"
echo "      H2S_DEBUG=1 to drop into a Python REPL on crash (default: off)."