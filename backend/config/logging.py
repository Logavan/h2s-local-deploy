"""
Logging configuration for the application.
"""
import logging
import os

# Suppress noisy third-party loggers
logging.getLogger('sqlfluff').setLevel(logging.CRITICAL)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)


def setup_logging():
    """Configure logging based on environment (Cloud Run vs local)."""
    from config.settings import IS_CLOUD_RUN
    
    if IS_CLOUD_RUN:
        # Production: Use INFO level
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logging.getLogger('flask').setLevel(logging.INFO)
    else:
        # Development: Show all logs
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logging.getLogger('flask').setLevel(logging.DEBUG)
        logging.getLogger('werkzeug').setLevel(logging.DEBUG)
    
    return logging.getLogger(__name__)


# Create logger instance
logger = setup_logging()