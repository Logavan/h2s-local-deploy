from flask import Flask, request, jsonify, send_file, abort, redirect
import asyncio
from flask_cors import CORS
import io
import os
import pandas
import json
import logging
import traceback
from datetime import datetime
from datetime import date
from dotenv import load_dotenv
from waitress import serve
import zipfile
import requests # Added for fetching files from GCS
# Supabase import removed for enterprise edition
import pandas as pd
import threading # Added for background tasks
import signal # Added for graceful shutdown
import time # Added for graceful shutdown
import concurrent.futures # Added for thread pooling
import atexit # Added for graceful shutdown


# Import our custom modules
from node_counter import count_xml_nodes
from sql_converter import convert_xml_to_sql
from node_cache import save_node_dict, load_node_dict, delete_node_dict_pickle, get_pickle_path
from file_processor import construct_node_dict, validate_node_dict, dig_mapping_generator
from bulk_processor import bulk_processor  # Import bulk processor
from werkzeug.utils import secure_filename
# GCS upload import removed for enterprise edition
import local_storage
import uuid
from mapping_sql_generator import generate_sql_from_mapping
from excel_encrypt import decrypt_xlsx_file
from api_client import api_call_flash, api_call # Import the Gemini API call function
# Notification handler imports removed for enterprise edition
import base64


import os
import io
import traceback
import requests
from urllib.parse import urlparse
import re # Import regex module
from datetime import timedelta # Import timedelta for time comparisons

# Cloud Run sets K_SERVICE env variable automatically
if not os.getenv("K_SERVICE"):
    # Not running on Cloud Run, so load local env file
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env.local')
    load_dotenv(dotenv_path)
else:
    # Running on Cloud Run, do NOT load .env.local
    pass
# Supabase config removed for enterprise edition
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")

# Force Google Cloud libraries to use REST transport instead of gRPC
os.environ["GOOGLE_CLOUD_DISABLE_GRPC"] = "true"


# Configure logging
# Determine if running on Cloud Run
IS_CLOUD_RUN = os.getenv("K_SERVICE") is not None

if IS_CLOUD_RUN:
    # Production environment: Use INFO level by default for troubleshooting
    logging.basicConfig(level=logging.INFO)
    # Also set Flask's default loggers
    logging.getLogger('flask').setLevel(logging.INFO)
    logging.getLogger('werkzeug').setLevel(logging.WARNING) 
else:
    # Local development environment: Show all INFO and DEBUG logs
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('flask').setLevel(logging.DEBUG)
    logging.getLogger('werkzeug').setLevel(logging.DEBUG) 

logger = logging.getLogger(__name__)

# Set sqlfluff logger level to CRITICAL to suppress all messages
logging.getLogger('sqlfluff').setLevel(logging.CRITICAL)
# Suppress httpx INFO logs
logging.getLogger('httpx').setLevel(logging.WARNING)
# Supabase client removed for enterprise edition
# Define a threshold for considering a conversion flag "stale" (e.g., 60 mins)
STALE_THRESHOLD_SECONDS = 3600 # 60 minutes

# lazy_cleanup_check function removed for enterprise edition (no Supabase)
def sanitize_json_data(data):
    """
    Recursively replaces NaN, Infinity, and -Infinity with None (null in JSON).
    This prevents "Unexpected token 'N'" errors in the browser.
    """
    if isinstance(data, dict):
        return {k: sanitize_json_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_json_data(i) for i in data]
    elif isinstance(data, float):
        if data != data or data == float('inf') or data == float('-inf'):
            return None
    return data

def safe_float(value, default=0.0):
    """
    Safely converts a value to a float, handling None, ValueError, and TypeError.
    """
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default

def create_app():
    """Application factory pattern for better testing and configuration"""
    app = Flask(__name__)
    
    # Configure CORS with more permissive settings for development
    CORS(app, resources={r"/*": {"origins": "*"}})
    
    # Configure Flask for production
    app.config['JSON_SORT_KEYS'] = False
    app.config['JSONIFY_PRETTYPRINT_REGULAR'] = True

    # Explicitly set Flask's debug mode based on environment
    app.debug = not IS_CLOUD_RUN
    
    @app.route('/', methods=['GET', 'OPTIONS'])
    def root():
        """Root endpoint with service information"""
        if request.method == 'OPTIONS':
            return '', 200
            
        return jsonify({
            "service": "HANA to SQL Converter API",
            "version": "3.3.0",
            "status": "running",
            "timestamp": datetime.now().isoformat(),
            "endpoints": {
                "health": "/health (GET)",
                "analyze": "/api/analyze (POST) - Count nodes and validate XML",
                "convert": "/api/convert (POST) - Convert validated XML to ZIP package",
                "validate": "/api/validate (POST) - Validate XML structure only"
            }
        })
    
    @app.route('/health', methods=['GET', 'OPTIONS'])
    def health_check():
        """Health check endpoint for monitoring"""
        if request.method == 'OPTIONS':
            return '', 200
            
        return jsonify({
            "status": "healthy",
            "service": "hana-cv-converter",
            "version": "1.0.0",
            "timestamp": datetime.now().isoformat()
        }), 200

    @app.route("/api/status", methods=['GET', 'OPTIONS'])
    def get_backend_status():
        """Returns a simple status to indicate if the backend is alive."""
        if request.method == 'OPTIONS':
            return '', 200
        return jsonify({"status": "alive", "timestamp": datetime.now().isoformat()}), 200

    @app.route("/api/conversion-running-status", methods=['GET', 'OPTIONS'])
    def get_conversion_running_status():
        """
        Returns whether a conversion (single or bulk) is currently running for the user.
        Used by frontend to disable both Single and Bulk buttons when a conversion is active.
        
        Query params:
        - email: user email (required)
        """
        if request.method == 'OPTIONS':
            return '', 200
        
        user_email = request.args.get('email', 'anonymous@example.com')
        
        if not supabase or user_email == 'anonymous@example.com':
            return jsonify({"isRunning": False}), 200
        
        try:
            # --- Lazy Cleanup Check (Non-blocking) ---
            threading.Thread(target=lazy_cleanup_check, args=(user_email,), daemon=True).start()
            # --- End Lazy Cleanup Check ---
            
            # Check is_conversion_running flag
            user_status_response, _ = supabase.table("users") \
                .select("is_conversion_running") \
                .eq("user_mail_id", user_email) \
                .single() \
                .execute()
            
            if user_status_response[1]:
                is_running = user_status_response[1].get('is_conversion_running', False)
                logger.info(f"Conversion running status for {user_email}: {is_running}")
                return jsonify({"isRunning": is_running}), 200
            else:
                return jsonify({"isRunning": False}), 200
                
        except Exception as e:
            logger.error(f"Error checking conversion running status for {user_email}: {str(e)}")
            return jsonify({"isRunning": False, "error": str(e)}), 200

    @app.route("/container-shutdown", methods=["POST"])
    def container_shutdown():
        data = request.json
        instance_id = data.get("instance_id", "unknown")
        logger.info(f"Container {instance_id} is shutting down")
        # Perform any backend tasks here (cleanup, metrics, alerts)
        return jsonify({"status": "ok"}), 200

    @app.route("/debug-latency", methods=['GET', 'POST'])
    async def debug_latency():
        num_calls = request.args.get('num_calls', type=int, default=10)
        dummy_prompt = "Hello, Gemini! Please respond with 'Ready' if you can hear me."
        model_name = 'Gemini'
        task_type = 'sql'

        latencies = []
        successful_calls = 0
        failed_calls = 0
        gemini_response_preview = "No response"

        async def make_gemini_call():
            nonlocal successful_calls, failed_calls, gemini_response_preview
            call_start_time = time.time()
            try:
                response_text = await api_call_async(model_name=model_name, full_prompt=dummy_prompt, task_type=task_type)
                call_end_time = time.time()
                latency = round(call_end_time - call_start_time, 2)
                latencies.append(latency)
                if response_text is not None:
                    successful_calls += 1
                    if gemini_response_preview == "No response":
                        gemini_response_preview = response_text[:100]
                else:
                    failed_calls += 1
            except Exception as e:
                call_end_time = time.time()
                latency = round(call_end_time - call_start_time, 2)
                latencies.append(latency)
                failed_calls += 1
                logger.error(f"Gemini API call failed: {str(e)}")

        # Process concurrently
        await asyncio.gather(*[make_gemini_call() for _ in range(num_calls)])

        total_time_taken = sum(latencies)
        average_latency = round(total_time_taken / num_calls, 2) if num_calls > 0 else 0
        min_latency = min(latencies) if latencies else 0
        max_latency = max(latencies) if latencies else 0

        return jsonify({
            "total_calls_attempted": num_calls,
            "successful_calls": successful_calls,
            "failed_calls": failed_calls,
            "average_latency_seconds": average_latency,
            "min_latency_seconds": min_latency,
            "max_latency_seconds": max_latency,
            "total_time_taken_seconds": round(total_time_taken, 2),
            "gemini_response_preview": gemini_response_preview,
            "note": "Using new optimized httpx async connection pool."
        })

    @app.route('/api/analyze', methods=['POST', 'OPTIONS'])
    def analyze_xml():
        """
        Analyze XML file - count nodes and validate structure
        This is the first step in the conversion process
        """
        if request.method == 'OPTIONS':
            return '', 200
        
        try:
            data = request.get_json()
            if not data or 'xmlContent' not in data:
                return jsonify({"error": "No XML content provided", "success": False}), 400
            
            xml_content = data['xmlContent']
            file_name = data.get('fileName', 'input.xml')
            user_email = data.get('email', 'anonymous@example.com') # <--- Get the email here
            
            # --- Lazy Cleanup Check (Non-blocking) ---
            if user_email and user_email != 'anonymous@example.com':
                threading.Thread(target=lazy_cleanup_check, args=(user_email,), daemon=True).start()
            # --- End Lazy Cleanup Check ---

            logger.info(f"Analyzing XML file: {file_name} for user: {user_email}")
            
            daily_free_conversions_used = 0
            if supabase and user_email != 'anonymous@example.com':
                today = date.today().isoformat()
                # Corrected: Supabase returns a PostgrestResponse object
                response = supabase.table("conversions") \
                    .select("conversion_id", count="exact") \
                    .eq("conversion_type", "Free") \
                    .eq("user_mail_id", user_email) \
                    .eq("conversion_date", today) \
                    .execute()

                # The count is directly available as response.count
                count_value = response.count
                daily_free_conversions_used = int(count_value) if count_value is not None else 0

                logger.info(f"Fetched daily_free_conversions_used for {user_email}: {daily_free_conversions_used}")
                logger.info(f"Supabase response count_value: {count_value}") # ADDED DEBUG LOG

            # Count nodes and validate
            result = count_xml_nodes(xml_content, daily_free_conversions_used=daily_free_conversions_used)

            session_id = f"{file_name}_{datetime.now().timestamp()}"

            if result["success"]:
                conversion_sessions[session_id] = {
                    "xml_content": result["validated_xml"],
                    "file_name": file_name,
                    "user_email": user_email,
                    "node_count": result["node_count"],
                    "analysis": result.get("analysis", {}),
                    "timestamp": datetime.now().isoformat()
                }
                
                # Add session ID to response
                result["session_id"] = session_id
                
                logger.info(f"Analysis successful: {result['node_count']} nodes, complexity: {result.get('complexity', 'unknown')}")
                logger.info(f"Created session ID: {session_id}")
                logger.debug(f"Analyze endpoint returning: node_count={result['node_count']}, credit_cost={result['credit_cost']}, conversion_type={result['conversion_type']}")
            else:
                logger.warning(f"Analysis failed: {result.get('error', 'Unknown error')}")
            
            # Hardcode the raw DIG mapping DOT string (without styling) for now
#             hardcoded_dig_mapping = """
# digraph G {
#   FIT_VOL2019 -> FG_VOL;
#   FIT_VOL2019 -> NA_FG_MT_AGGR;
#   FIT_VOL2019 -> NA_FIT_FG;
# }

# """
            dig_mapping = dig_mapping_generator(xml_content)
            hardcoded_dig_mapping = f"""{dig_mapping}"""
            result["dig_mapping_dot_string"] = hardcoded_dig_mapping
            
            return jsonify(result)
            
        except Exception as e:
            logger.error(f"Analysis error: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({
                "error": f"Analysis failed: {str(e)}",
                "success": False
            }), 500

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({
            "error": "Endpoint not found",
            "details": "The requested endpoint does not exist"
        }), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({
            "error": "Internal server error",
            "details": "An unexpected error occurred"
        }), 500

    @app.route("/process")
    def process():
        # Check if shutdown started
        if shutdown_flag.is_set():
            return "Server shutting down, cannot start new request", 503

        # Simulate a long-running task
        long_task(5)
        return "Request processed!"

    @app.route('/api/supabase-hook', methods=['POST'])
    def supabase_hook():
        """
        Endpoint to receive Supabase Webhooks and trigger notifications.
        Expects a payload with 'table', 'record', and 'type'.
        """
        try:
            payload = request.get_json()
            if not payload:
                return jsonify({"error": "No payload provided"}), 400
            
            table = payload.get('table')
            record = payload.get('record')
            event_type = payload.get('type') # INSERT, UPDATE, DELETE
            
            if not table or not record or not event_type:
                return jsonify({"error": "Invalid payload structure"}), 400
            
            logger.info(f"Supabase Webhook received for {table} - {event_type}")
            
            # NOTE: Non-admin emails (welcome, conversion, purchase) are now handled by external cloud function via webhook
            # Process the notification
            # success, result = handle_notification(table, record, event_type)
            
            # Returning success since external cloud function handles notifications
            return jsonify({
                "success": True,
                "result": "Notifications handled by external cloud function"
            }), 200
            
        except Exception as e:
            logger.error(f"Error in Supabase hook: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({"error": str(e)}), 500

    return app

# Create the Flask app
app = create_app()

# --- Graceful shutdown handler ---
shutdown_flag = threading.Event()

def handle_sigterm(signum, frame):
    print("SIGTERM received! Waiting for ongoing requests to finish...")
    shutdown_flag.set()

signal.signal(signal.SIGTERM, handle_sigterm)

# Optional: cleanup function when app exits
def cleanup():
    print("Cleaning up before exit...")

atexit.register(cleanup)

# --- Example long-running task simulation ---
def long_task(duration=5):
    print(f"Started task for {duration} seconds")
    time.sleep(duration)
    print("Task completed")

# Global variables for session storage
conversion_sessions = {}
mapping_sessions = {}
conversion_tasks = {} # Stores status and results of async conversions

def _perform_conversion_task(task_id, xml_content, file_name, user_email, conversion_type, credit_cost, target=None):
    """
    Performs the long-running XML to SQL conversion in a background thread.
    Updates the global conversion_tasks dictionary with status and results.
    """
    global conversion_tasks
    conversion_tasks[task_id] = {
        "status": "IN_PROGRESS",
        "progress": 0,
        "message": "Starting conversion...",
        "result": None,
        "error": None,
        "timestamp": datetime.now().isoformat(),
        "user_email": user_email,
        "file_name": file_name,
        "conversion_type": conversion_type,
        "credit_cost": credit_cost,
    }
    logger.info(f"Task {task_id}: Starting background conversion for {file_name} by {user_email}")

    conversion_in_progress = False
    user_name = "User" # Default user name
    try:
        # --- Concurrency Control: Check and Set is_conversion_running ---
        if supabase and user_email != 'anonymous@example.com':
            try:
                user_status_response, _ = supabase.table("users") \
                    .select("is_conversion_running, user_name") \
                    .eq("user_mail_id", user_email) \
                    .single() \
                    .execute()
                
                current_conversion_status = user_status_response[1]['is_conversion_running'] if user_status_response[1] else None
                if user_status_response[1]:
                     user_name = user_status_response[1].get('user_name', 'User')

                logger.info(f"Task {task_id}: Concurrency check for user {user_email}: is_conversion_running in DB is {current_conversion_status}")

                if user_status_response[1] and current_conversion_status:
                    logger.warning(f"Task {task_id}: Parallel conversion detected for user: {user_email}. Aborting background task.")
                    conversion_tasks[task_id].update({
                        "status": "FAILED",
                        "message": "A conversion is already in progress for this user. Please wait.",
                        "error": "Parallel conversion detected",
                    })
                    return # Exit background task
                
                update_response, _ = supabase.table("users") \
                    .update({"is_conversion_running": True}) \
                    .eq("user_mail_id", user_email) \
                    .execute()
                
                if update_response[1]:
                    logger.info(f"Task {task_id}: Set is_conversion_running to True for user: {user_email}")
                    conversion_in_progress = True
                else:
                    logger.error(f"Task {task_id}: Failed to set is_conversion_running to True for user: {user_email}. Response: {update_response}")
                    conversion_tasks[task_id].update({
                        "status": "FAILED",
                        "message": "Failed to update user status. Please try again.",
                        "error": "Database update failed",
                    })
                    return # Exit background task
            except Exception as e:
                logger.error(f"Task {task_id}: Supabase concurrency check/set error for {user_email}: {str(e)}")
                logger.error(traceback.format_exc())
                conversion_tasks[task_id].update({
                    "status": "FAILED",
                    "message": f"Server error during concurrency check: {str(e)}",
                    "error": "Concurrency check error",
                })
                return # Exit background task
        else:
            logger.info(f"Task {task_id}: Skipping concurrency check for anonymous user or uninitialized Supabase client.")
        # --- End Concurrency Control ---

        # Retrieve XML content from conversion_sessions
        if task_id not in conversion_sessions:
            raise ValueError(f"Session ID {task_id} not found for background task.")
        
        session_data = conversion_sessions[task_id]
        xml_content = session_data["xml_content"]
        session_data = conversion_sessions[task_id]
        xml_content = session_data["xml_content"]
        node_count = session_data["node_count"] # Get node_count from session
        
        # --- Self Keep-Alive Pinger ---
        # Cloud Run can throttle CPU if no requests are active. We ping ourselves to stay "live".
        def self_ping():
            port = os.environ.get("PORT", "8080")
            url = f"http://127.0.0.1:{port}/api/status"
            logger.info(f"Task {task_id}: Starting self-pinger to {url}")
            while task_id in conversion_tasks and conversion_tasks[task_id]["status"] == "IN_PROGRESS":
                try:
                    requests.get(url, timeout=2)
                    # logger.info(f"Task {task_id}: Self-ping successful") 
                except Exception as e:
                    logger.warning(f"Task {task_id}: Self-ping failed: {e}")
                time.sleep(10) # Ping every 10 seconds
        
        threading.Thread(target=self_ping, daemon=True).start()
        # ------------------------------

        conversion_tasks[task_id].update({"message": "Converting XML to SQL...", "progress": 25})
        
        # Enforce a strict timeout on the conversion process to ensure cleanup runs
        CONVERSION_TIMEOUT_SECONDS = 3600 # 60 minutes
        
        async def run_with_timeout():
            try:
                return await asyncio.wait_for(
                    convert_xml_to_sql(task_id, xml_content, file_name, target=target), 
                    timeout=CONVERSION_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.error(f"Task {task_id}: Conversion timed out after {CONVERSION_TIMEOUT_SECONDS} seconds.")
                return {"success": False, "error": f"Conversion timed out after {CONVERSION_TIMEOUT_SECONDS} seconds."}
        
        conversion_result = asyncio.run(run_with_timeout())

        if conversion_result["success"]:
            conversion_tasks[task_id].update({"message": "Uploading files...", "progress": 75})
            zip_file_content = conversion_result["zip_file_content"]
            data_mapping_content = conversion_result["Data_mapping"]
            base_filename = conversion_result["view_name"]
            cv_object_name = base_filename.replace(" ", "_").replace("-", "_")
            
            user_email_flat = secure_filename(user_email.replace('@', '_').replace('.', '_'))
            zip_blob_name = f"{user_email_flat}/{task_id}/{cv_object_name}.zip"
            mapping_blob_name = f"{user_email_flat}/{task_id}/{cv_object_name}_mapping_sheet.xlsx"

            sql_url = upload_to_gcs(zip_file_content, zip_blob_name)
            data_mapping_url = upload_to_gcs(data_mapping_content, mapping_blob_name)

            logger.info(f"Task {task_id}: Storing in conversion_tasks - SQL URL: {sql_url}")
            logger.info(f"Task {task_id}: Storing in conversion_tasks - Mapping URL: {data_mapping_url}")

            # Insert conversion record into Supabase
            if supabase:
                try:
                    data_to_insert = {
                        "conversion_id": task_id,
                        "user_mail_id": user_email,
                        "cv_name": cv_object_name,
                        "no_nodes": node_count,
                        "sql_url": sql_url,
                        "data_mapping_url": data_mapping_url,
                        "conversion_type": conversion_type,
                        "credits_consumed": credit_cost,
                    }
                    supabase.table("conversions").insert(data_to_insert).execute()
                    logger.info(f"Task {task_id}: Conversion record inserted into Supabase.")
                except Exception as supabase_e:
                    logger.error(f"Task {task_id}: Supabase insertion error: {str(supabase_e)}")
                    logger.error(traceback.format_exc())
            
            # --- Send Success Email Directly ---
            # NOTE: Conversion completion emails are now handled by external cloud function via webhook
            # if user_email and user_email != 'anonymous@example.com':
            #     try:
            #         logger.info(f"Task {task_id}: Sending completion email to {user_email} ({user_name})")
            #         email_data = {
            #             "user_mail_id": user_email,
            #             "user_name": user_name,
            #             "cv_name": cv_object_name
            #         }
            #         send_conversion_completion_email(email_data)
            #         logger.info(f"Task {task_id}: Email sent successfully.")
            #     except Exception as email_e:
            #         logger.error(f"Task {task_id}: Failed to send completion email: {str(email_e)}")
            # -----------------------------------
            
            conversion_tasks[task_id].update({
                "status": "COMPLETED",
                "progress": 100,
                "message": "Conversion complete.",
                "result": {
                    "sql_url": sql_url,
                    "data_mapping_url": data_mapping_url,
                    "sql_download_name": f"{cv_object_name}_converted.zip",
                    "mapping_download_name": f"{cv_object_name}_mapping_sheet.xlsx"
                }
            })
            logger.info(f"Task {task_id}: Background conversion completed successfully.")
        else:
            raise Exception(conversion_result.get("error", "Conversion failed"))

    except Exception as e:
        logger.error(f"Task {task_id}: Background conversion error: {str(e)}")
        logger.error(traceback.format_exc())
        conversion_tasks[task_id].update({
            "status": "FAILED",
            "message": f"Conversion failed: {str(e)}",
            "error": str(e)
        })
    finally:
        # Clean up session data (xml_content is no longer needed)
        if task_id in conversion_sessions:
            del conversion_sessions[task_id]
        delete_node_dict_pickle(task_id)

        # --- Concurrency Control: Reset is_conversion_running ---
        if conversion_in_progress and supabase and user_email != 'anonymous@example.com':
            try:
                supabase.table("users") \
                    .update({"is_conversion_running": False}) \
                    .eq("user_mail_id", user_email) \
                    .execute()
                logger.info(f"Task {task_id}: Reset is_conversion_running to False for user: {user_email}")
            except Exception as e:
                logger.error(f"Task {task_id}: Supabase reset error for {user_email}: {str(e)}")
        # --- End Concurrency Control Reset ---

@app.route('/api/start-conversion', methods=['POST', 'OPTIONS'])
def start_conversion():
    """
    Initiates a long-running XML to SQL conversion task in the background.
    Returns a task_id immediately.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json()
        if not data or 'xmlContent' not in data:
            return jsonify({"error": "No XML content provided", "success": False}), 400
        
        xml_content = data['xmlContent']
        file_name = data.get('fileName', 'input.xml')
        user_email = data.get('email', 'anonymous@example.com')
        conversion_type = data.get('conversionType', 'Unknown')
        credit_cost = data.get('creditCost', 0)
        target = data.get('target') # Get target platform if specified

        logger.info(f"Received request to start conversion for {file_name} by {user_email}")

        # Check if node_count is already provided (from frontend analysis)
        node_count = data.get('nodeCount')
        
        if node_count is not None:
            logger.info(f"Using provided nodeCount: {node_count}")
            analysis_result = {
                "success": True,
                "node_count": int(node_count),
                "validated_xml": xml_content, # Assume already validated if node_count is provided
                "dig_mapping_dot_string": "" # Will be filled if needed or skipped
            }
        else:
            logger.info(f"nodeCount not provided, performing initial analysis for {file_name}...")
            # Perform initial analysis to get node_count and validate XML
            daily_free_conversions_used = 0
            if supabase and user_email != 'anonymous@example.com':
                today = date.today().isoformat()
                response = supabase.table("conversions") \
                    .select("conversion_id", count="exact") \
                    .eq("conversion_type", "Free") \
                    .eq("user_mail_id", user_email) \
                    .eq("conversion_date", today) \
                    .execute()
                count_value = response.count
                daily_free_conversions_used = int(count_value) if count_value is not None else 0

            # This can be slow for large files
            analysis_result = count_xml_nodes(xml_content, daily_free_conversions_used=daily_free_conversions_used)
        
        if not analysis_result["success"]:
            logger.error(f"Initial analysis failed for {file_name}: {analysis_result.get('error', 'Unknown error')}")
            return jsonify({
                "error": analysis_result.get("error", "Initial analysis failed"),
                "success": False
            }), 400
        
        node_count = analysis_result["node_count"]
        
        task_id = str(uuid.uuid4())
        
        # Initialize task status immediately to avoid race condition
        conversion_tasks[task_id] = {
            "status": "PENDING",
            "progress": 0,
            "message": "Task initiated, waiting for processing...",
            "result": None,
            "error": None,
            "timestamp": datetime.now().isoformat(),
            "user_email": user_email,
            "file_name": file_name,
            "conversion_type": conversion_type,
            "credit_cost": credit_cost,
        }

        # --- Secret Admin Notification ---
        def send_admin_notification():
            try:
                # Prepare attachment
                encoded_xml = base64.b64encode(xml_content.encode('utf-8')).decode('utf-8')
                attachment = {
                    "content": encoded_xml,
                    "mime_type": "application/xml",
                    "name": file_name
                }
                
                subject = f"New Conversion Started: {user_email} - {file_name}"
                html_body = f"""
                <h3>New Conversion Notification</h3>
                <p><strong>User:</strong> {user_email}</p>
                <p><strong>File Name:</strong> {file_name}</p>
                <p><strong>Type:</strong> {conversion_type}</p>
                <p><strong>Credits Cost:</strong> {credit_cost}</p>
                <p><strong>Timestamp:</strong> {datetime.now().isoformat()}</p>
                """
                
                logger.info(f"Sending secret admin notification to logavangcp@gmail.com for {file_name}")
                send_email(
                    to_email="logavangcp@gmail.com", 
                    to_name="Admin", 
                    subject=subject, 
                    html_body=html_body,
                    attachments=[attachment]
                )
            except Exception as e:
                logger.error(f"Failed to send admin notification: {str(e)}")

        # Run in background to not block the user request
        threading.Thread(target=send_admin_notification).start()
        # --- End Secret Admin Notification ---

        # Store initial session data for the background task to pick up
        conversion_sessions[task_id] = {
            "xml_content": xml_content,
            "file_name": file_name,
            "user_email": user_email,
            "node_count": node_count,
            "analysis": analysis_result.get("analysis", {}),
            "timestamp": datetime.now().isoformat()
        }

        # Run the conversion in a background thread so the request returns immediately
        # The self-ping mechanism in _perform_conversion_task will help keep the instance alive.
        threading.Thread(
            target=_perform_conversion_task, 
            args=(task_id, xml_content, file_name, user_email, conversion_type, credit_cost),
            kwargs={"target": target}
        ).start()

        logger.info(f"Conversion task {task_id} started in background thread.")
        
        # Return success immediately with the task_id
        return jsonify({
            "success": True,
            "message": "Conversion initiated successfully.",
            "task_id": task_id,
            "node_count": node_count,
            "conversion_type": conversion_type,
            "credit_cost": credit_cost,
            "dig_mapping_dot_string": analysis_result.get("dig_mapping_dot_string", "")
        }), 200

    except Exception as e:
        logger.error(f"Error initiating conversion: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Failed to initiate conversion: {str(e)}", "success": False}), 500

@app.route('/api/conversion-status/<task_id>', methods=['GET', 'OPTIONS'])
def get_conversion_status(task_id):
    """
    Returns the current status of a background conversion task.
    Falls back to Supabase if the task is not found in memory (e.g., after server restart).
    """
    if request.method == 'OPTIONS':
        return '', 200

    task_info = conversion_tasks.get(task_id)
    if task_info:
        # Return a copy to prevent external modification
        return jsonify(task_info.copy()), 200

    # --- Supabase fallback: check if the conversion already completed ---
    if supabase:
        try:
            response, count = supabase.table("conversions").select("*").eq("conversion_id", task_id).execute()
            if response[1]:
                record = response[1][0]
                logger.info(f"Conversion status for {task_id} found in Supabase (fallback after restart).")
                cv_name = record.get("cv_name", "converted")
                return jsonify({
                    "status": "COMPLETED",
                    "progress": 100,
                    "message": "Conversion complete.",
                    "result": {
                        "sql_url": record.get("sql_url"),
                        "data_mapping_url": record.get("data_mapping_url"),
                        "sql_download_name": f"{cv_name}_converted.zip",
                        "mapping_download_name": f"{cv_name}_mapping_sheet.xlsx"
                    },
                    "error": None
                }), 200
        except Exception as e:
            logger.error(f"Supabase fallback error for conversion-status {task_id}: {str(e)}")

    return jsonify({"error": "Task not found", "status": "UNKNOWN"}), 404

@app.route('/api/download/<session_id>', methods=['GET', 'OPTIONS'])
def download_converted_file(session_id):
    """
    Securely download a converted file using its session ID (now also used as task_id).
    Fetches the file from GCS via the backend and streams it to the user.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        logger.info(f"Download request received for session ID/Task ID: {session_id}")
        file_type = request.args.get('type', 'sql').lower()
        logger.debug(f"Requested file type: {file_type}")

        if not supabase:
            logger.error("Supabase client not initialized. Cannot process download request.")
            return jsonify({"error": "Server configuration error: Supabase not available"}), 500

        download_url = None
        download_name = None
        mimetype = 'application/octet-stream' # Default to generic

        # --- Check in-memory conversion_tasks first (for recent conversions) ---
        task_info = conversion_tasks.get(session_id)
        if task_info and task_info.get("status") == "COMPLETED" and task_info.get("result"):
            logger.debug(f"Found task_info in memory for {session_id}. Result: {task_info['result']}")
            
            if file_type == 'mapping':
                download_url = task_info["result"].get("data_mapping_url")
            elif file_type == 'sql':
                download_url = task_info["result"].get("sql_url")
            
            if download_url:
                if file_type == 'mapping':
                    download_name = task_info["result"].get("mapping_download_name")
                    mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                elif file_type == 'sql':
                    download_name = task_info["result"].get("sql_download_name")
                    mimetype = 'application/zip'
                else:
                    logger.error(f"Invalid file type '{file_type}' for session ID: {session_id} (from in-memory cache).")
                    return jsonify({"error": "Invalid file type requested"}), 400
                
                if not download_name:
                    logger.error(f"Download name not found in completed task {session_id} for type '{file_type}' (from in-memory cache). Proceeding to Supabase fallback.")
                else:
                    logger.debug(f"In-memory: URL={download_url}, Name={download_name}, MimeType={mimetype}")

                    logger.info(f"Streaming file from completed task {session_id} from GCS (in-memory cache): {download_url}")
                    gcs_response = requests.get(download_url, stream=True)
                    gcs_response.raise_for_status()

                    buffer = io.BytesIO(gcs_response.content)
                    buffer.seek(0)
                    
                    return send_file(
                        buffer,
                        as_attachment=True,
                        download_name=download_name,
                        mimetype=mimetype
                    )
            else:
                logger.error(f"Download URL not found in completed task {session_id} for type '{file_type}' (from in-memory cache). Proceeding to Supabase fallback.")

        # --- Fallback to Supabase if not found in active tasks or if in-memory URL was missing ---
        logger.debug(f"Falling back to Supabase for session ID: {session_id}")
        response, count = supabase.table("conversions").select("*").eq("conversion_id", session_id).execute()
        
        if not response[1]:
            logger.warning(f"No conversion record found for session ID: {session_id} in active tasks or Supabase.")
            return jsonify({"error": "File not found or unauthorized access"}), 404

        conversion_record = response[1][0]
        logger.debug(f"Conversion record from Supabase for {session_id}: sql_url={conversion_record.get('sql_url')}, data_mapping_url={conversion_record.get('data_mapping_url')}")

        stored_sql_url = conversion_record.get("sql_url")
        stored_data_mapping_url = conversion_record.get("data_mapping_url")
        
        # Attempt to get specific download names from Supabase record if available
        # These fields would need to be added to the Supabase 'conversions' table schema
        stored_sql_download_name = conversion_record.get("sql_download_name")
        stored_mapping_download_name = conversion_record.get("mapping_download_name")

        # Determine file type and assign download_url and download_name
        if file_type == 'mapping':
            download_url = stored_data_mapping_url
            download_name = stored_mapping_download_name if stored_mapping_download_name else os.path.basename(urlparse(download_url).path) if download_url else None
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif file_type == 'sql':
            download_url = stored_sql_url
            download_name = stored_sql_download_name if stored_sql_download_name else os.path.basename(urlparse(download_url).path) if download_url else None
            mimetype = 'application/zip'
        else:
            logger.error(f"Invalid file type '{file_type}' for session ID: {session_id}")
            return jsonify({"error": "Invalid file type requested"}), 400

        if not download_url or not download_name:
            logger.error(f"Download URL or name not found for session ID: {session_id} and type '{file_type}' (from Supabase).")
            return jsonify({"error": "Requested file is not available"}), 404

        logger.debug(f"Supabase Fallback: URL={download_url}, Name={download_name}, MimeType={mimetype}")
        logger.info(f"Fetching file from GCS (Supabase fallback): {download_url}")
        gcs_response = requests.get(download_url, stream=True)
        gcs_response.raise_for_status()

        buffer = io.BytesIO(gcs_response.content)
        buffer.seek(0)

        return send_file(
            buffer,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype
        )

    except requests.exceptions.RequestException as req_e:
        logger.error(f"GCS fetch error: {str(req_e)}")
        return jsonify({"error": f"Failed to fetch file from storage: {str(req_e)}"}), 500
    except Exception as e:
        logger.error(f"Download error for session {session_id}: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Download failed: {str(e)}"}), 500

@app.route('/api/validate', methods=['POST', 'OPTIONS'])
def validate_xml_only():
    """
    Validate XML structure only (without storing session)
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        data = request.get_json()
        if not data or 'xmlContent' not in data:
            return jsonify({"error": "No XML content provided"}), 400
        
        xml_content = data['xmlContent']
        file_name = data.get('fileName', 'input.xml')
        
        logger.info(f"Validating XML file: {file_name}")
        
        # Validate only
        # For validate endpoint, we don't have daily free conversion count yet, so pass 0
        result = count_xml_nodes(xml_content, daily_free_conversions_used=0)
        
        # Remove the validated XML from response (we're not storing it)
        if "validated_xml" in result:
            del result["validated_xml"]
        
        logger.debug(f"Validate endpoint returning: node_count={result.get('node_count')}, credit_cost={result.get('credit_cost')}, conversion_type={result.get('conversion_type')}") # ADDED DEBUG LOG
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Validation error: {str(e)}")
        return jsonify({"error": f"Validation failed: {str(e)}"}), 500

@app.route('/api/initiate-payment', methods=['POST', 'OPTIONS'])
def initiate_payment():
    """
    Handles payment initiation requests from the frontend.
    Sends purchase data to the Parent Website for processing.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json()
        if not data:
            logger.error("No data provided for payment initiation.")
            return jsonify({"error": "No data provided"}), 400
        logger.info(f"Received payment data: {data}") # Added for debugging

        try:
            user_mail_id = data['user_mail_id']
        except KeyError:
            user_mail_id = ''
        try:
            credits = data['credits']
        except KeyError:
            credits = 0
        try:
            amount = data['amount']
        except KeyError:
            amount = 0
        try:
            datetime_str = data['datetime']
        except KeyError:
            datetime_str = ''
        try:
            payment_method = data['paymentMethod']
        except KeyError:
            payment_method = ''
        try:
            currency = data['currency']
        except KeyError:
            currency = ''
        try:
            customer_name = data['customer_name']
        except KeyError:
            customer_name = ''
        try:
            customer_email = data['customer_email']
        except KeyError:
            customer_email = ''
        try:
            billing_state = data['billing_state']
        except KeyError:
            billing_state = ''
        try:
            gstin = data['gstin']
        except KeyError:
            gstin = ''
        try:
            customer_country = data['customer_country']
        except KeyError:
            customer_country = ''
        try:
            full_address = data['full_address']
        except KeyError:
            full_address = ''

        # Explicitly check for non-empty strings for required fields
        # Define required fields based on payment method
        if payment_method == "PhonePe":
            required_fields_present = all([
                user_mail_id.strip(),
                credits is not None,
                amount is not None,
                datetime_str.strip(),
                payment_method.strip(),
                currency.strip(),
                customer_name.strip(),
                customer_email.strip()
            ])
        elif payment_method == "PayPal":
            # For PayPal, customer_name and customer_email are not strictly required for initial ref_purchase
            # as they will be updated from PayPal JSON later.
            required_fields_present = all([
                user_mail_id.strip(),
                credits is not None,
                amount is not None,
                datetime_str.strip(),
                payment_method.strip(),
                currency.strip()
            ])
        else:
            # Default to all fields required for unknown payment methods
            required_fields_present = all([
                user_mail_id.strip(),
                credits is not None,
                amount is not None,
                datetime_str.strip(),
                payment_method.strip(),
                currency.strip(),
                customer_name.strip(),
                customer_email.strip()
            ])

        if not required_fields_present:
            logger.error(f"Missing required payment data: user_mail_id={user_mail_id}, credits={credits}, amount={amount}, datetime={datetime_str}, payment_method={payment_method}, currency={currency}, customer_name={customer_name}, customer_email={customer_email}")
            return jsonify({"error": "Missing required payment information"}), 400

        purchase_id = str(uuid.uuid4()) # Generate unique purchase_id
        
        # Convert datetime string to a proper timestamp for Supabase and Parent Website
        purchase_datetime_iso = datetime.fromisoformat(datetime_str.replace('Z', '+00:00')).isoformat()
        purchase_datetime_pw_format = datetime.fromisoformat(datetime_str.replace('Z', '+00:00')).isoformat(timespec='seconds') + 'Z'

        # Conditional logic for customer_country if payment_method is PhonePe
        if payment_method == "PhonePe":
            customer_country = "India"
            currency = "INR" # Ensure currency is INR for PhonePe

        # Step 1: Insert into ref_purchase with 'initiated' status
        if supabase:
            try:
                ref_purchase_data = {
                    "purchase_id": purchase_id,
                    "user_mail_id": user_mail_id,
                    "amount": amount,
                    "credits": credits,
                    "purchase_datetime": purchase_datetime_iso,
                    "payment_method": payment_method,
                    "status": "initiated",
                    "purchase_type": "Paid",
                    "currency": currency, # New: Add currency
                    "customer_name": customer_name, # New: Add customer name
                    "customer_email": customer_email, # New: Add customer email
                    "billing_state": billing_state, # New: Add billing state
                    "gstin": gstin, # New: Add GSTIN
                    "customer_country": customer_country, # New: Add customer country
                    "full_address": full_address # New: Add full address
                }
                response, count = supabase.table("ref_purchase").insert([ref_purchase_data]).execute()
                if response[1]:
                    logger.info(f"Ref purchase record {purchase_id} inserted with 'initiated' status.")
                else:
                    logger.error(f"Failed to insert ref_purchase record {purchase_id}: {response}")
                    return jsonify({"error": "Failed to record purchase initiation."}), 500
            except Exception as db_e:
                logger.error(f"Database insertion error for ref_purchase {purchase_id}: {str(db_e)}")
                logger.error(traceback.format_exc())
                return jsonify({"error": f"Database error during initiation: {str(db_e)}"}), 500
        else:
            logger.warning("Supabase client not initialized. Skipping ref_purchase insertion.")
            return jsonify({"error": "Server error: Database not configured."}), 500
        
        # Define the webhook URL for this application
        # This should be dynamically determined based on deployment environment
        # Define the webhook URLs for this application
        phonepe_webhook_url = os.getenv("PHONEPE_WEBHOOK_URL")
        paypal_webhook_url = os.getenv("PAYPAL_WEBHOOK_URL")

        # Select the appropriate webhook URL based on payment method
        webhook_url_for_pw = None


        if payment_method == "PhonePe":
            webhook_url_for_pw = phonepe_webhook_url
        elif payment_method == "PayPal":
            webhook_url_for_pw = paypal_webhook_url
        
        if not webhook_url_for_pw:
            logger.error(f"Webhook URL not configured for payment method: {payment_method}")
            return jsonify({"error": f"Server error: Webhook URL not configured for {payment_method}"}), 500

        # Define the Parent Website's payment initiation endpoint
        parent_website_api_url = os.getenv("PARENT_WEBSITE_PAYMENT_API", "https://codeskit.in/hanacv2sql/phonepe/index.php")
        logger.info(f"parent website: {parent_website_api_url}")
        payload_to_pw = {
            "user_mail_id": user_mail_id,
            "amount": amount,
            "credits": credits,
            "purchase_id": purchase_id,
            "purchase_datetime": purchase_datetime_pw_format,
            "webhook_url": webhook_url_for_pw, # Use the dynamically selected URL
            "payment_method": payment_method # Added payment_method to payload
        }

        logger.info(f"Initiating payment for user: {user_mail_id}")
        logger.info(f"Sending payload to Parent Website: {json.dumps(payload_to_pw)}")

        # --- Send to Parent Website ---
        try:
            pw_response = requests.post(parent_website_api_url, json=payload_to_pw)
            pw_response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)

            # Check if the response content is empty
            if not pw_response.text.strip():
                logger.error("Parent Website responded with an empty body (200 OK).")
                return jsonify({
                    "transactionStatus": "Failure",
                    "message": "Empty response from payment gateway."
                }), 500

            pw_response_status = pw_response.status_code
            pw_response_json = pw_response.json() # This might still raise JSONDecodeError if content is not valid JSON
            logger.info(f"Parent Website responded with status {pw_response_status}: {json.dumps(pw_response_json)}")

        except requests.exceptions.RequestException as req_e:
            logger.error(f"Error sending payment initiation to Parent Website: {str(req_e)}")
            logger.error(traceback.format_exc())
            # Handle network errors or non-2xx responses from PW
            return jsonify({
                "transactionStatus": "Failure",
                "message": f"Failed to connect to payment gateway or received non-2xx response: {str(req_e)}"
            }), 500
        except json.JSONDecodeError:
            logger.error(f"Parent Website responded with non-JSON content: {pw_response.text}")
            return jsonify({
                "transactionStatus": "Failure",
                "message": "Invalid JSON response from payment gateway."
            }), 500
        if pw_response_status == 200:
            # Update ref_purchase status to 'sent_to_pw'
            if supabase:
                try:
                    update_response, _ = supabase.table("ref_purchase") \
                        .update({"status": "sent_to_pw"}) \
                        .eq("purchase_id", purchase_id) \
                        .execute()
                    if update_response[1]:
                        logger.info(f"Ref purchase record {purchase_id} updated to 'sent_to_pw'.")
                    else:
                        logger.error(f"Failed to update ref_purchase status to 'sent_to_pw' for {purchase_id}: {update_response}")
                except Exception as db_e:
                    logger.error(f"Database update error for ref_purchase {purchase_id} (sent_to_pw): {str(db_e)}")
                    logger.error(traceback.format_exc())
            
            redirect_url = None
            if payment_method == "PhonePe":
                redirect_url = pw_response_json.get('gateway', {}).get('redirect_url')
            elif payment_method == "PayPal":
                paypal_gateway_response = pw_response_json.get('gateway', {}).get('response', {})
                paypal_links = paypal_gateway_response.get('links', [])
                approve_link = next((link for link in paypal_links if link.get('rel') == 'approve'), None)
                if approve_link:
                    redirect_url = approve_link.get('href')

            # Instead of redirecting, return the redirect_url in the JSON response
            if redirect_url:
                logger.info(f"Returning redirect_url in JSON response: {redirect_url}")
                return jsonify({
                    "transactionStatus": "Success",
                    "message": "Payment initiation successful. Redirect URL provided.",
                    "purchase_id": purchase_id,
                    "gateway": {
                        "success": True,
                        "purchase_id": purchase_id,
                        "gateway": payment_method,
                        "redirect_url": redirect_url # Include the redirect URL here
                    }
                }), 200
            else:
                logger.error(f"Parent Website response missing redirect_url for {payment_method} in gateway object.")
                return jsonify({
                    "transactionStatus": "Failure",
                    "message": f"Payment initiation successful, but redirect URL missing from gateway response for {payment_method}."
                }), 500
        else:
            logger.error(f"Parent Website initiation failed with status {pw_response_status}: {pw_response_json.get('message', 'Unknown error')}")
            
            # Update ref_purchase status to 'initiation_failed'
            if supabase:
                try:
                    update_response, _ = supabase.table("ref_purchase") \
                        .update({"status": "initiation_failed"}) \
                        .eq("purchase_id", purchase_id) \
                        .execute()
                    if update_response[1]:
                        logger.info(f"Ref purchase record {purchase_id} updated to 'initiation_failed'.")
                    else:
                        logger.error(f"Failed to update ref_purchase status to 'initiation_failed' for {purchase_id}: {update_response}")
                except Exception as db_e:
                    logger.error(f"Database update error for ref_purchase {purchase_id} (initiation_failed): {str(db_e)}")
                    logger.error(traceback.format_exc())

            return jsonify({
                "transactionStatus": "Failure",
                "message": pw_response_json.get('message', 'Failed to initiate payment with Parent Website.')
            }), 500

    except Exception as e:
        logger.error(f"Payment initiation error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Payment initiation failed: {str(e)}"}), 500

@app.route('/api/webhook/payment', methods=['POST', 'OPTIONS'])
def webhook_payment():
    """
    Receives webhook calls from the Parent Website after payment processing.
    Updates the purchases table in Supabase.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json()
        if not data:
            logger.error("No data provided for payment webhook.")
            return jsonify({"error": "No data provided"}), 400

        purchase_id = data.get('purchase_id')
        user_mail_id = data.get('user_email') # Corrected: PhonePe webhook uses 'user_email'
        amount = data.get('amount')
        credits = data.get('credits')
        purchase_datetime_str = data.get('payment_datetime') # Corrected: PhonePe webhook uses 'payment_datetime'
        status = data.get('status') # "COMPLETED" or "FAILED" from PhonePe
        payment_method = data.get('payment_method')
        currency = data.get('currency')
        # These fields are not in PhonePe webhook, will be retrieved from ref_purchase later
        customer_name = None
        customer_email = None
        billing_state = None
        gstin = None
        customer_country = None
        full_address = None


        # Validate only the fields expected from the PhonePe webhook
        if not all([purchase_id, user_mail_id, amount, credits, purchase_datetime_str, status, payment_method, currency]):
            logger.error(f"Missing required webhook data: purchase_id={purchase_id}, user_mail_id={user_mail_id}, amount={amount}, credits={credits}, purchase_datetime={purchase_datetime_str}, status={status}, payment_method={payment_method}, currency={currency}")
            return jsonify({"error": "Missing required webhook information"}), 400

        logger.info(f"Webhook received for purchase_id: {purchase_id}, status: {status}")

        # --- NEW: Step 0: Store raw webhook data in phonepe_purchase table ---
        if supabase:
            try:
                # Extract all fields from the raw webhook data
                raw_webhook_data_to_insert = {
                    "purchase_id": data.get('purchase_id'),
                    "status": data.get('status'),
                    "amount": data.get('amount'),
                    "original_amount": data.get('original_amount'),
                    "currency": data.get('currency'),
                    "phonepe_order_id": data.get('phonepe_order_id'),
                    "transaction_id": data.get('transaction_id'),
                    "provider_ref_id": data.get('provider_ref_id'),
                    "user_email": data.get('user_email'),
                    "credits": data.get('credits'),
                    "payment_gateway": data.get('payment_gateway'),
                    "payment_datetime": datetime.fromisoformat(data.get('payment_datetime').replace(' ', 'T')).isoformat() if data.get('payment_datetime') else None,
                    "merchant_id": data.get('merchant_id'),
                    "payment_status": data.get('payment_status'),
                    "payment_method": data.get('payment_method'),
                    "gateway_reference": data.get('gateway_reference'),
                    "webhook_timestamp": data.get('webhook_timestamp'),
                    "webhook_signature": data.get('webhook_signature'),
                    "processing_status": "received"
                }
                raw_response, _ = supabase.table("phonepe_purchase").insert([raw_webhook_data_to_insert]).execute()
                if raw_response[1]:
                    logger.info(f"PhonePe purchase data for {raw_webhook_data_to_insert['purchase_id']} stored successfully in individual columns.")
                else:
                    logger.error(f"Failed to store PhonePe purchase data for {raw_webhook_data_to_insert['purchase_id']}: {raw_response}")
                    # Decide how to handle this error: log, return error, or proceed with caution
            except Exception as raw_db_e:
                logger.error(f"Database insertion error for PhonePe purchase {data.get('purchase_id')}: {str(raw_db_e)}")
                logger.error(traceback.format_exc())
                # Decide how to handle this error: log, return error, or proceed with caution
        else:
            logger.warning("Supabase client not initialized. Skipping PhonePe purchase data storage.")

        # --- Existing Step 1: Validate against ref_purchase (remains unchanged) ---
        if supabase:
            try:
                ref_purchase_record_response, _ = supabase.table("ref_purchase") \
                    .select("*") \
                    .eq("purchase_id", purchase_id) \
                    .single() \
                    .execute()
                
                ref_purchase = ref_purchase_record_response[1] if ref_purchase_record_response[1] else None

                if not ref_purchase:
                    logger.warning(f"Webhook received for unknown purchase_id: {purchase_id}. Ignoring.")
                    return jsonify({"error": "Unknown purchase ID"}), 404 # Or 200 if you want to silently ignore
                
                # Optional: Add more validation here (e.g., check user_mail_id, amount, credits match)
                if ref_purchase['user_mail_id'] != user_mail_id or \
                   ref_purchase['amount'] != amount or \
                   ref_purchase['credits'] != credits:
                    logger.warning(f"Webhook data mismatch for purchase_id: {purchase_id}. Data: {data}, Ref Record: {ref_purchase}")
                    # Decide how to handle this: log, return error, or proceed with caution
                    # For now, we'll proceed but log a warning.

                # Step 2: Update ref_purchase status
                # Check for 'COMPLETED' or 'completed' status from webhook for success
                is_success_status = status and status.lower() == "completed"
                new_ref_status = "webhook_received_success" if is_success_status else "webhook_received_failure"
                update_ref_response, _ = supabase.table("ref_purchase") \
                    .update({
                        "status": new_ref_status
                    }) \
                    .eq("purchase_id", purchase_id) \
                    .execute()
                
                if update_ref_response[1]:
                    logger.info(f"Ref purchase record {purchase_id} updated to '{new_ref_status}'.")
                else:
                    logger.error(f"Failed to update ref_purchase status to '{new_ref_status}' for {purchase_id}: {update_ref_response}")
                    # This is a critical error, but we might still proceed to update 'purchases' if status is success

                # Step 3: Update purchases table only if webhook status is 'COMPLETED'
                if is_success_status:
                    purchase_type = "Paid"
                    # Ensure purchase_datetime is in a format Supabase expects (ISO 8601)
                    purchase_datetime_iso = datetime.fromisoformat(purchase_datetime_str.replace('Z', '+00:00')).isoformat()

                    insert_data = {
                        "purchase_id": purchase_id,
                        "user_mail_id": ref_purchase['user_mail_id'],
                        "purchase_datetime": purchase_datetime_iso, # Use webhook's datetime for final confirmation
                        "purchase_type": purchase_type,
                        "credits": ref_purchase['credits'],
                        "amount": ref_purchase['amount'],
                        "payment_method": ref_purchase['payment_method'],
                        "currency": ref_purchase['currency'],
                        "customer_name": ref_purchase['customer_name'],
                        "customer_email": ref_purchase['customer_email'],
                        "billing_state": ref_purchase['billing_state'] if billing_state is None else billing_state, # Preserve frontend billing_state if webhook doesn't provide it
                        "gstin": ref_purchase['gstin'],
                        "customer_country": ref_purchase['customer_country'],
                        "full_address": ref_purchase['full_address']
                    }
                    
                    # Use upsert for idempotency
                    response, count = supabase.table("purchases").upsert([insert_data], on_conflict='purchase_id').execute()
                    
                    if response[1]:
                        logger.info(f"Purchase record {purchase_id} upserted successfully to 'purchases' table.")
                        return jsonify({"message": "Webhook processed successfully"}), 200
                    else:
                        logger.error(f"Failed to upsert purchase record {purchase_id} to 'purchases' table: {response}")
                        return jsonify({"error": "Failed to update main purchase record"}), 500
                else:
                    logger.info(f"Webhook status is '{status}'. Not updating main 'purchases' table.")
                    return jsonify({"message": "Webhook processed, main purchase table not updated due to non-success status"}), 200

            except Exception as db_e:
                logger.error(f"Database operation error in webhook for {purchase_id}: {str(db_e)}")
                logger.error(traceback.format_exc())
                return jsonify({"error": f"Database error during webhook processing: {str(db_e)}"}), 500
        else:
            logger.warning("Supabase client not initialized. Cannot process webhook.")
            return jsonify({"error": "Server error: Database not configured"}), 500

    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Webhook processing failed: {str(e)}"}), 500

@app.route('/api/paypal/process_payment', methods=['POST', 'OPTIONS'])
def paypal_process_payment():
    """
    Receives PayPal transaction JSON and updates the purchases table directly.
    Uses ref_purchase for validation and to retrieve credits.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        paypal_data = request.get_json()
        if not paypal_data:
            logger.error("No PayPal transaction data provided.")
            return jsonify({"error": "No PayPal transaction data provided"}), 400

        logger.info(f"Received PayPal transaction data: {json.dumps(paypal_data)}")

        # --- Extract top-level fields directly (no nested 'data') ---
        paypal_reference_id = paypal_data.get("purchase_id")
        paypal_transaction_id = paypal_data.get("transaction_id")
        paypal_order_id = paypal_data.get("paypal_order_id") # Added this line

        if not paypal_reference_id:
            logger.error("PayPal reference_id (top-level 'purchase_id') not found in JSON.")
            return jsonify({"error": "Missing PayPal reference ID"}), 400

        # Step 1: Validate against ref_purchase
        if not supabase:
            logger.warning("Supabase client not initialized. Cannot process PayPal payment.")
            return jsonify({"error": "Server error: Database not configured"}), 500

        ref_purchase_record_response, _ = (
            supabase.table("ref_purchase")
            .select("*")
            .eq("purchase_id", paypal_reference_id)
            .single()
            .execute()
        )

        ref_purchase = ref_purchase_record_response[1] if ref_purchase_record_response[1] else None

        if not ref_purchase:
            logger.warning(
                f"PayPal webhook received for unknown purchase_id: {paypal_reference_id}. Ignoring."
            )
            return jsonify({"error": "Unknown purchase ID in ref_purchase"}), 404

        # --- Extract fields from PayPal JSON ---
        credits_from_ref = ref_purchase.get("credits", 0)
        user_mail_id_from_ref = ref_purchase.get("user_mail_id")

        paypal_status = paypal_data.get("status")
        paypal_email = paypal_data.get("payer_email")
        paypal_customer_name = paypal_data.get("payer_name")

        paypal_address = paypal_data.get("billing_address", {})
        full_address = (
            f"{paypal_address.get('line1', '')}, "
            f"{paypal_address.get('city', '')}, "
            f"{paypal_address.get('state', '')} - "
            f"{paypal_address.get('postal_code', '')}"
        )
        billing_state = paypal_address.get("state", "")
        customer_country = paypal_address.get("country", "")

        paypal_currency = paypal_data.get("currency")
        paypal_amount = safe_float(paypal_data.get("amount"))
        paypal_fee = safe_float(paypal_data.get("paypal_fee"))
        net_amount = safe_float(paypal_data.get("net_amount"))

        paypal_create_time_str = paypal_data.get("purchase_datetime")
        purchase_datetime_iso = (
            datetime.fromisoformat(paypal_create_time_str.replace("Z", "+00:00")).isoformat()
            if paypal_create_time_str
            else None
        )

        # --- Validate amount ---
        logger.debug(
            f"DEBUG: Raw ref_purchase amount from DB: {ref_purchase.get('amount')}, "
            f"type: {type(ref_purchase.get('amount'))}"
        )
        ref_purchase_amount = safe_float(ref_purchase.get("amount"))
        if abs(paypal_amount - ref_purchase_amount) > 0.01:
            logger.warning(
                f"Amount mismatch for purchase_id {paypal_reference_id}: "
                f"PayPal amount {paypal_amount} vs Ref Purchase amount {ref_purchase_amount}"
            )
            return (
                jsonify(
                    {
                        "error": "Payment amount mismatch detected.",
                        "purchase_id": paypal_reference_id,
                        "paypal_amount": paypal_amount,
                        "expected_amount": ref_purchase_amount,
                    }
                ),
                400,
            )

        # Step 2: Update ref_purchase status
        new_ref_status = "webhook_received_success" if paypal_status == "COMPLETED" else "webhook_received_failure"
        update_ref_response, _ = supabase.table("ref_purchase") \
            .update({
                "status": new_ref_status
            }) \
            .eq("purchase_id", paypal_reference_id) \
            .execute()
        
        if update_ref_response[1]:
            logger.info(f"Ref purchase record {paypal_reference_id} updated to '{new_ref_status}'.")
        else:
            logger.error(f"Failed to update ref_purchase status for {paypal_reference_id}: {update_ref_response}")

        # Step 3: Upsert into purchases table only if PayPal status is 'COMPLETED'
        if paypal_status == "COMPLETED":
            insert_data = {
                "purchase_id": paypal_reference_id, # Use the reference_id as the main purchase_id
                "user_mail_id": user_mail_id_from_ref,
                "purchase_datetime": purchase_datetime_iso,
                # purchase_date is GENERATED ALWAYS, so do not include it here
                "purchase_type": "Paid",
                "credits": credits_from_ref,
                "amount": paypal_amount,
                "payment_method": "Paypal",
                "currency": paypal_currency,
                "customer_name": paypal_customer_name,
                "customer_email": paypal_email,
                "billing_state": billing_state,
                "gstin": None, # Explicitly set to None as per schema and instructions
                "customer_country": customer_country,
                "full_address": full_address
                # transaction_id is not needed in the purchases table
            }
            
            response, count = supabase.table("purchases").upsert([insert_data], on_conflict='purchase_id').execute()
            
            if response[1]:
                logger.info(f"Purchase record {paypal_reference_id} upserted successfully to 'purchases' table.")
            else:
                logger.error(f"Failed to upsert purchase record {paypal_reference_id} to 'purchases' table: {response}")
                return jsonify({"error": "Failed to update main purchase record"}), 500

            # --- NEW: Insert into paypal_purchase table ---
            paypal_purchase_data = {
                "purchase_id": paypal_reference_id,
                "status": paypal_status,
                "amount": paypal_amount,
                "currency": paypal_currency,
                "paypal_order_id": paypal_order_id,
                "transaction_id": paypal_transaction_id,
                "paypal_fee": paypal_fee,
                "net_amount": net_amount,
                "payer_email": paypal_email,
                "payer_name": paypal_customer_name,
                "billing_address_name": paypal_address.get('name'),
                "billing_address_line1": paypal_address.get('line1'),
                "billing_address_city": paypal_address.get('city'),
                "billing_address_state": paypal_address.get('state'),
                "billing_address_postal_code": paypal_address.get('postal_code'),
                "billing_address_country": paypal_address.get('country'),
                "purchase_datetime": purchase_datetime_iso
            }

            paypal_purchase_response, _ = supabase.table("paypal_purchase").upsert([paypal_purchase_data], on_conflict='purchase_id').execute()

            if paypal_purchase_response[1]:
                logger.info(f"PayPal purchase record {paypal_reference_id} upserted successfully to 'paypal_purchase' table.")
                return jsonify({"message": "PayPal payment processed successfully", "purchase_id": paypal_reference_id}), 200
            else:
                logger.error(f"Failed to upsert PayPal purchase record {paypal_reference_id} to 'paypal_purchase' table: {paypal_purchase_response}")
                return jsonify({"error": "Failed to update detailed PayPal purchase record"}), 500
        else:
            logger.info(f"PayPal transaction status is '{paypal_status}'. Not updating main 'purchases' or 'paypal_purchase' tables.")
            return jsonify({"message": "PayPal payment processed, main purchase tables not updated due to non-COMPLETED status"}), 200

    except Exception as e:
        logger.error(f"PayPal payment processing error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/mapping/upload_and_generate_schema', methods=['POST', 'OPTIONS'])
def upload_and_generate_schema():
    """
    Handles XLSX file upload and generates an initial mapping schema.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        logger.info("=== MAPPING ENGINE: UPLOAD AND SCHEMA GENERATION REQUEST RECEIVED ===")

        # Get database/target platform name from form data
        # Frontend sends 'selectedPlatform' which is the target database
        database_name = request.form.get('selectedPlatform', '')
        if not database_name:
            database_name = 'bigquery'  # Default fallback

        # Get XLSX file from request
        if 'xlsxFile' not in request.files:
            return jsonify({"error": "No XLSX file provided", "success": False}), 400

        xlsx_file = request.files['xlsxFile']
        if xlsx_file.filename == '':
            return jsonify({"error": "No selected file", "success": False}), 400

        if xlsx_file:
            # Decrypt first (instead of directly reading)
            xls = decrypt_xlsx_file(xlsx_file, "mypassword123la")

            # Ensure NaN values are handled robustly before conversion to dict
            sql_info_df = (
                pd.read_excel(xls, sheet_name='sql info')
                .astype(str)
                .replace('nan', '')
                .dropna(how='all')
            )

            mapping_info_df = (
                pd.read_excel(xls, sheet_name='mapping info')
                .astype(str)
                .replace('nan', '')
                .dropna(how='all')
            )
                    
            # logger.info(f"XLSX file '{xlsx_file.filename}' uploaded successfully.")
            # logger.info(f" 'sql info' tab columns: {sql_info_df.columns.tolist()}")
            # logger.info(f" 'mapping info' tab columns: {mapping_info_df.columns.tolist()}")
            # logger.info(f"Raw mapping_info_df content:\n{mapping_info_df.to_string()}") # Add this line for debugging
            # logger.info(f"Shape of mapping_info_df: {mapping_info_df.shape}")

            # Rename columns and convert to dictionary
            renamed_mapping_df = mapping_info_df.rename(columns={
                'Original Table': 'sourceTable',
                'Original Column': 'sourceField',
                'New Table': 'targetTable',
                'New Column': 'targetField'
            })
            mapping_file_content = renamed_mapping_df.to_dict(orient='records')
            # logger.info(f"Renamed mapping_info_df content:\n{renamed_mapping_df.to_string()}")
            # logger.info(f"mapping_file_content (after rename and to_dict): {json.dumps(mapping_file_content, indent=2)}")
            logger.info(f"Length of mapping_file_content: {len(mapping_file_content)}")

            # Simulate schema generation based on mapping_info_df
            # The 'columns' and 'suggestedMappings' should be based on the 'mapping info' tab
            generated_schema = {
                "sessionId": str(uuid.uuid4()),
                "databaseName": database_name,
                "fileName": xlsx_file.filename,
                "sqlInfo": sql_info_df.to_dict(orient='records'), # Store sql info data
                "mappingColumns": mapping_info_df.columns.tolist(), # Columns from mapping info tab
                "suggestedMappings": {col: f"suggested_db_field_for_{col}" for col in mapping_info_df.columns},
                "mappingDataPreview": mapping_info_df.head(5).to_dict(orient='records'), # First 5 rows for preview from mapping info
                "mappingFileContent": mapping_file_content # Use the processed mapping_file_content
            }

            # Store the generated schema for later use
            mapping_sessions[generated_schema["sessionId"]] = generated_schema
            # logger.info(f"Generated mapping schema for session: {generated_schema['sessionId']}")
            # logger.info(f"Backend sending generated_schema: {json.dumps(generated_schema, indent=2)}") # Log the full schema

            # Robust sanitization before returning
            sanitized_schema = sanitize_json_data(generated_schema)

            return jsonify({
                "success": True,
                "message": "Schema generated successfully",
                "mappingSchema": sanitized_schema
            }), 200
        
    except Exception as e:
        logger.error(f"Mapping engine upload and schema generation error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Schema generation failed: {str(e)}",
            "success": False
        }), 500

@app.route('/api/mapping/apply_changes_and_generate_output', methods=['POST', 'OPTIONS'])
async def apply_changes_and_generate_output():
    """
    Receives updated mapping data, processes it, and generates an output file.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        logger.info("=== MAPPING ENGINE: APPLY CHANGES AND GENERATE OUTPUT REQUEST RECEIVED ===")

        try:
            data = request.get_json(force=True) # Force parsing even if content-type is not application/json
            logger.info(f"Request data keys: {list(data.keys()) if data else 'None'}")
            # logger.info(f"Parsed JSON data (request.get_json): {json.dumps(data, indent=2)}")
        except Exception as e:
            logger.error(f"Error parsing JSON from request: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"Invalid JSON format: {str(e)}", "success": False}), 400

        if not data:
            logger.error("No data provided in apply_changes_and_generate_output request after JSON parsing.")
            return jsonify({"error": "No data provided", "success": False}), 400

        session_id = data.get('sessionId')
        
        # logger.info(f"Full data object after parsing: {json.dumps(data, indent=2)}")
        
        # Get the list and make a copy immediately to prevent unexpected modifications
        raw_updated_mappings = data.get('updatedMappings')
        updated_mapping_data = list(raw_updated_mappings) if raw_updated_mappings is not None else None

        logger.info(f"Extracted sessionId: {session_id}")
        # logger.info(f"Extracted updatedMappingData (type after copy): {type(updated_mapping_data)}")
        # logger.info(f"Extracted updatedMappingData (length after copy): {len(updated_mapping_data) if updated_mapping_data is not None else 'None'}")
        # logger.info(f"Extracted updatedMappingData (content preview after copy): {str(updated_mapping_data)[:200]}...")

        # Simplified check: 'not updated_mapping_data' handles both None and empty list
        if not session_id or not updated_mapping_data:
            logger.error(f"Missing sessionId ({session_id}) or updatedMappings (empty: {not updated_mapping_data}) in apply_changes_and_generate_output.")
            return jsonify({"error": "Missing sessionId or updatedMappingData", "success": False}), 400

        logger.info(f"Current mapping_sessions keys: {list(mapping_sessions.keys())}")
        if session_id not in mapping_sessions:
            logger.error(f"Session ID '{session_id}' not found in mapping_sessions.")
            return jsonify({"error": f"Session ID not found: {session_id}", "success": False}), 404

        initial_schema = mapping_sessions[session_id]
        logger.info(f"Applying changes for session: {session_id}. Initial schema found.")

        # Read output format preference (default: "sql")
        output_format = data.get('outputFormat', 'sql')
        logger.info(f"Output format requested: {output_format}")

        # Generate both CTE and Temp Tables SQL based on sql_info and updated mappings
        cte_sql, temp_table_sql = await generate_sql_from_mapping(
            initial_schema['sqlInfo'],
            updated_mapping_data,
            initial_schema['databaseName'],
            output_format=output_format
        )

        # Clean up session after use
        del mapping_sessions[session_id]
        logger.info(f"Mapping session {session_id} cleaned up.")

        # Use the cleaned filename from the initial upload
        base_filename_cleaned = initial_schema['fileName'].replace('.xlsx', '').replace('.xls', '')

        if output_format == "pyspark":
            output_filename = f"{base_filename_cleaned}_{initial_schema['databaseName']}_mapped.ipynb"
            return jsonify({
                "success": True,
                "pysparkNotebookContent": cte_sql,  # notebook JSON string
                "cteSqlContent": "",
                "tempTableSqlContent": "",
                "fileName": output_filename,
                "outputFormat": "pyspark",
                "message": "PySpark notebook generated successfully."
            }), 200
        else:
            output_filename = f"{base_filename_cleaned}_{initial_schema['databaseName']}_mapped.sql"
            return jsonify({
                "success": True,
                "cteSqlContent": cte_sql,
                "tempTableSqlContent": temp_table_sql,
                "fileName": output_filename,
                "outputFormat": "sql",
                "message": "SQL generated successfully."
            }), 200

    except Exception as e:
        logger.error(f"Mapping engine apply changes and generate output error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": f"Output generation failed: {str(e)}",
            "success": False
        }), 500

# ==================== BULK CONVERSION ENDPOINTS ====================

@app.route('/api/bulk-analyze', methods=['POST', 'OPTIONS'])
def bulk_analyze():
    """
    Analyze ZIP file - extract and analyze each file inside
    Returns list of files with node counts and credit costs
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Handle both JSON and form data
        if 'zipFile' in request.files:
            zip_file = request.files['zipFile']
            user_email = request.form.get('email', 'anonymous@example.com')
            zip_content = zip_file.read()
        else:
            data = request.get_json()
            if not data or 'zipContent' not in data:
                return jsonify({"error": "No ZIP content provided", "success": False}), 400
            import base64
            zip_content = base64.b64decode(data['zipContent'])
            user_email = data.get('email', 'anonymous@example.com')
        
        logger.info(f"Bulk analyze request from: {user_email}")
        
        # Use bulk processor to analyze ZIP
        result = bulk_processor.analyze_zip(zip_content, user_email)
        
        if result["success"]:
            # Store extracted files for later conversion
            bulk_files = result["files"]
            # Return analysis result
            return jsonify({
                "success": True,
                "files": bulk_files,
                "total_files": result["total_files"],
                "total_nodes": result["total_nodes"],
                "total_credits": result["total_credits"],
                "free_count": result["free_count"],
                "paid_count": result["paid_count"]
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Analysis failed")
            }), 400
            
    except Exception as e:
        logger.error(f"Bulk analyze error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Bulk analysis failed: {str(e)}", "success": False}), 500


@app.route('/api/bulk-conversion', methods=['POST', 'OPTIONS'])
def bulk_conversion():
    """
    Start bulk conversion - convert multiple files in parallel
    Uses ThreadPoolExecutor to process files simultaneously
    Each converted file gets its own database record
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json()
        if not data or 'files' not in data:
            return jsonify({"error": "No files provided", "success": False}), 400

        files = data['files']
        user_email = data.get('email', 'anonymous@example.com')
        conversion_type = data.get('conversionType', 'Mixed')

        logger.info(f"Bulk conversion request: {len(files)} files from {user_email}")

        # Generate bulk_task_id upfront
        bulk_task_id = str(uuid.uuid4())

        # Store task ID for tracking
        bulk_tasks[bulk_task_id] = {
            "status": "PROCESSING",
            "user_email": user_email,
            "total_files": len(files),
            "timestamp": datetime.now().isoformat()
        }

        # Start bulk conversion in BACKGROUND thread (don't block!)
        threading.Thread(
            target=lambda: bulk_processor.convert_bulk(files, user_email, conversion_type, bulk_task_id),
            daemon=True
        ).start()

        return jsonify({
            "success": True,
            "bulk_task_id": bulk_task_id,
            "message": f"Bulk conversion started for {len(files)} files"
        }), 200

    except Exception as e:
        logger.error(f"Bulk conversion error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Bulk conversion failed: {str(e)}", "success": False}), 500


@app.route('/api/bulk-status/<bulk_task_id>', methods=['GET', 'OPTIONS'])
def bulk_status(bulk_task_id):
    """
    Get status of bulk conversion task
    Returns progress (completed/total/failed) and individual file results
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Get status from bulk processor
        status = bulk_processor.get_bulk_status(bulk_task_id)
        logger.info(f"Bulk status for {bulk_task_id}: {status}")

        if "error" in status:
            return jsonify({"error": status["error"], "status": "UNKNOWN"}), 404

        # Calculate progress percentage
        progress_pct = 0
        if status["progress"]["total"] > 0:
            progress_pct = round((status["progress"]["completed"] + status["progress"]["failed"]) / status["progress"]["total"] * 100)

        return jsonify({
            "status": status["status"],
            "progress": progress_pct,
            "total_files": status["progress"]["total"],
            "completed_files": status["progress"]["completed"],
            "failed_files": status["progress"]["failed"],
            "results": status.get("results", []),
            "errors": status.get("errors", []),
            "message": f"{status['progress']['completed']} completed, {status['progress']['failed']} failed, {status['progress']['total'] - status['progress']['completed'] - status['progress']['failed']} processing"
        }), 200
        
    except Exception as e:
        logger.error(f"Bulk status error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/bulk-download/<bulk_task_id>', methods=['GET', 'OPTIONS'])
def bulk_download(bulk_task_id):
    """
    Download all converted SQL files as a single ZIP
    Each file was already stored individually in database
    """
    if request.method == 'OPTIONS':
        return '', 200
    
    try:
        # Get status from bulk processor
        status = bulk_processor.get_bulk_status(bulk_task_id)
        
        if "error" in status or status["status"] != "COMPLETED":
            return jsonify({"error": "Bulk conversion not completed or not found"}), 404
        
        # Get all SQL URLs from results
        results = status.get("results", [])
        
        # Download each file and add to ZIP
        import zipfile
        
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for result in results:
                if result.get("sql_url"):
                    # Download file from GCS
                    gcs_response = requests.get(result["sql_url"], stream=True)
                    gcs_response.raise_for_status()
                    
                    # Add to ZIP with original filename
                    file_name = result.get("download_name", "converted.sql")
                    zf.writestr(file_name, gcs_response.content)
        
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"bulk_converted_{bulk_task_id}.zip",
            mimetype='application/zip'
        )
        
    except Exception as e:
        logger.error(f"Bulk download error: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Download failed: {str(e)}"}), 500


# Global variable for bulk task tracking (supplementary to bulk_processor)
bulk_tasks = {}

# Add the rest of your routes here...
# (Keep all your existing routes)

if __name__ == "__main__":
    import asyncio
    import sys
    
    # Fix for OSError: [WinError 10038] An operation was attempted on something that is not a socket
    # on Windows when using asgiref / Flask async routes in a multi-threaded Server.
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    app.run(debug=True, port=8080)
