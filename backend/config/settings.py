"""
Configuration module for environment variables and application settings.
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
        dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env.local')
        load_dotenv(dotenv_path)

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")

# Payment Configuration
PHONEPE_WEBHOOK_URL = os.getenv("PHONEPE_WEBHOOK_URL")
PAYPAL_WEBHOOK_URL = os.getenv("PAYPAL_WEBHOOK_URL")
PARENT_WEBSITE_PAYMENT_API = os.getenv(
    "PARENT_WEBSITE_PAYMENT_API",
    "https://codeskit.in/hanacv2sql/phonepe/index.php"
)

# Conversion Settings
STALE_THRESHOLD_SECONDS = 3600  # 60 minutes
CONVERSION_TIMEOUT_SECONDS = 3600  # 60 minutes


# Initialize on module import
load_environment()