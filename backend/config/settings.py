"""
Configuration module for environment variables and application settings.
Enterprise edition — no Supabase, no payments.
"""
import os
from datetime import timedelta

# Cloud Run detection
IS_CLOUD_RUN = os.getenv("K_SERVICE") is not None

# Environment loading
def load_environment():
    """Load environment variables based on deployment context."""
    if not IS_CLOUD_RUN:
        from dotenv import load_dotenv
        dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
        load_dotenv(dotenv_path)

# Gemini Model Configuration — single model, used by both REST and SDK paths
# Change at runtime via environment variable to switch models instantly
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()


# Conversion Settings
STALE_THRESHOLD_SECONDS = 3600  # 60 minutes
CONVERSION_TIMEOUT_SECONDS = 3600  # 60 minutes

# Initialize on module import
load_environment()

# Gemini API Key — required
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable is required")
