#!/usr/bin/env python3
"""
HANA to SQL Converter - Production Runner
Handles production deployment with proper configuration
"""

import os
import sys
import logging
from datetime import datetime
import threading
import time
from waitress import serve

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from flask_app import app
except Exception as e:
    import traceback
    print(f"CRITICAL: Failed to import Flask app: {e}")
    traceback.print_exc()
    sys.exit(1)

# Configure logging for production
def setup_logging():
    """Setup production logging configuration"""
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Force unbuffered output for local development
    sys.stdout.reconfigure(line_buffering=True)

    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('app.log') if os.environ.get('LOG_TO_FILE') else logging.NullHandler()
        ]
    )
    
    # Set specific loggers
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

def get_config():
    """Get application configuration from environment"""
    config = {
        'PORT': int(os.environ.get('PORT', 8080)),
        'HOST': os.environ.get('HOST', '0.0.0.0'),
        'THREADS': int(os.environ.get('THREADS', 32)),
        'CONNECTION_LIMIT': int(os.environ.get('CONNECTION_LIMIT', 200)),
        'ENV': os.environ.get('FLASK_ENV', 'production'),
        'MAX_CONTENT_LENGTH': int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))  # 16MB
    }
    
    return config

def validate_environment():
    """Validate required environment variables and dependencies"""
    logger = logging.getLogger(__name__)
    
    # Check Python version
    if sys.version_info < (3, 7):
        logger.error("Python 3.7 or higher is required")
        return False
    
    # Check required modules
    required_modules = ['flask', 'flask_cors', 'waitress']
    missing_modules = []
    
    for module in required_modules:
        try:
            __import__(module)
        except ImportError:
            missing_modules.append(module)
    
    if missing_modules:
        logger.error(f"Missing required modules: {', '.join(missing_modules)}")
        logger.error("Install with: pip install flask flask-cors waitress")
        return False
    
    return True

def setup_app_config(app, config):
    """Configure Flask app with production settings"""
    app.config.update({
        'MAX_CONTENT_LENGTH': config['MAX_CONTENT_LENGTH'],
        'JSON_SORT_KEYS': False,
        'JSONIFY_PRETTYPRINT_REGULAR': False if config['ENV'] == 'production' else True,
        'SEND_FILE_MAX_AGE_DEFAULT': 31536000,  # 1 year for static files
    })

def keep_alive_pinger():
    """Background task to print a heartbeat log every 5 seconds."""
    logger = logging.getLogger(__name__)
    while True:
        # logger.info(f"Keep-alive ping: {datetime.now().isoformat()}")
        time.sleep(5)

def main():
    """Main application entry point"""
    # Setup logging first
    setup_logging()
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 50)
    logger.info("HANA to SQL Converter API Starting...")
    logger.info(f"Start time: {datetime.now().isoformat()}")
    logger.info("=" * 50)
    
    # Validate environment
    if not validate_environment():
        logger.error("Environment validation failed")
        sys.exit(1)
    
    # Get configuration
    config = get_config()
    logger.info(f"Configuration: {config}")
    
    # Setup app configuration
    setup_app_config(app, config)
    
    # Log startup information
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Flask app: {app}")
    logger.info(f"Environment: {config['ENV']}")
    logger.info(f"Host: {config['HOST']}")
    logger.info(f"Port: {config['PORT']}")
    logger.info(f"Threads: {config['THREADS']}")
    
    # Start the keep-alive pinger
    logger.info("Starting keep-alive pinger thread...")
    threading.Thread(target=keep_alive_pinger, daemon=True).start()
    
    try:
        # Start the application with Waitress
        logger.info("Starting Waitress WSGI server on port %d...", config['PORT'])
        # Flush logs before starting
        for handler in logger.handlers:
            handler.flush()
        sys.stdout.flush()
        
        serve(
            app, 
            host=config['HOST'], 
            port=config['PORT'],
            threads=config['THREADS'],
            connection_limit=config['CONNECTION_LIMIT']
        )
            
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Application failed to start: {str(e)}")
        logger.error("Stack trace:", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("HANA to SQL Converter API Shutdown")
        logger.info("=" * 50)

if __name__ == '__main__':
    main()
