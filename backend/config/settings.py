"""
Configuration module for environment variables and application settings.
Enterprise edition — no Supabase, no payments.

Secret loading convention (Twelve-Factor `_FILE` pattern):

    For each secret `FOO`, the app consults, in order:
        1. The `FOO` environment variable (literal value).
        2. The `FOO_FILE` environment variable (path to a mounted file
           containing the value, stripped of whitespace).
        3. None — the app fails to start if the secret is required.

This lets the admin team inject secrets by ANY of these methods at container
start time without rebuilding the image:

    --env-file ./secrets.env          # env var = literal value
    FOO_FILE=/run/secrets/foo         # Docker secret mounted as a file
    vault-agent → /tmp/foo             # vault sidecar writes to tmpfs
    --secret id=foo,src=...            # pod-level secret in K8s

Users never set env vars. Only the customer's admin team does, once, when
the container is first started.
"""
import os
from datetime import timedelta

# Cloud Run detection
IS_CLOUD_RUN = os.getenv("K_SERVICE") is not None

# Placeholder / default-looking values that must never ship in production.
# We refuse to start when a required secret resolves to one of these — better
# to fail fast with a clear admin-facing message than to silently talk to a
# broken / fake API.
_PLACEHOLDER_SECRETS = frozenset({
    "",
    "your-key-here",
    "your-api-key",
    "changeme",
    "change-me",
    "change_me",
    "replace-me",
    "todo",
    "tbd",
    "sk-test",
    "test",
    "dummy",
    "xxx",
    "00000000",
})


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------

def load_environment():
    """Load environment variables based on deployment context."""
    if not IS_CLOUD_RUN:
        from dotenv import load_dotenv
        dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
        load_dotenv(dotenv_path)


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------

def get_secret(env_var_name: str, required: bool = True) -> str | None:
    """Resolve a secret from an env var or `*_FILE` mounted file.

    Resolution order:
        1. The literal env var `FOO`.
        2. The file-path env var `FOO_FILE` — read file contents, strip.
        3. None (caller decides whether to fail).
    """
    direct = os.environ.get(env_var_name)
    if direct is not None and direct != "":
        return direct

    file_path = os.environ.get(f"{env_var_name}_FILE")
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
                if value:
                    return value
        except OSError as exc:
            raise RuntimeError(
                f"{env_var_name}_FILE points at {file_path!r} but the file "
                f"could not be read: {exc}"
            ) from exc

    if required:
        raise RuntimeError(
            f"Required secret {env_var_name} is not set. "
            f"The admin team must provide it at container start time, either "
            f"as the {env_var_name} environment variable or via "
            f"{env_var_name}_FILE pointing at a mounted file. "
            f"See backend/DEPLOYMENT.md."
        )
    return None


def assert_real_secret(name: str, value: str | None) -> str:
    """Refuse to start when a required secret is missing or is a placeholder."""
    if value is None or value == "":
        raise RuntimeError(
            f"{name} is missing. The admin team must provide it at container "
            f"start. See backend/DEPLOYMENT.md."
        )
    if value.strip().lower() in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            f"{name} is set to a placeholder value ({value!r}). "
            f"The admin team must replace it with a real value. "
            f"See backend/DEPLOYMENT.md §3 (Secret Injection)."
        )
    return value


# ---------------------------------------------------------------------------
# Conversion Settings (non-secret constants)
# ---------------------------------------------------------------------------

STALE_THRESHOLD_SECONDS = 3600  # 60 minutes
CONVERSION_TIMEOUT_SECONDS = 3600  # 60 minutes

# Gemini Model — NOT a secret. Default model; admin can override at start time.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()


# ---------------------------------------------------------------------------
# Initialize secrets at module import — fail fast if misconfigured
# ---------------------------------------------------------------------------

load_environment()

# Required secret. We refuse to start without a real-looking value.
GEMINI_API_KEY = assert_real_secret(
    "GEMINI_API_KEY",
    get_secret("GEMINI_API_KEY", required=True),
)

# Optional secrets — load if present, otherwise None.
# Add new optional secrets here as the app grows.
BIGQUERY_CREDENTIALS_FILE = get_secret("BIGQUERY_CREDENTIALS_FILE", required=False)
SNOWFLAKE_PRIVATE_KEY_FILE = get_secret("SNOWFLAKE_PRIVATE_KEY_FILE", required=False)
DATABRICKS_TOKEN_FILE = get_secret("DATABRICKS_TOKEN_FILE", required=False)