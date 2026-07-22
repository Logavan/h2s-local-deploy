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
from dotenv import load_dotenv
from waitress import serve
import zipfile
import requests
import pandas as pd
import threading
import signal
import time
import atexit

# Import our custom modules
from node_counter import count_xml_nodes
from sql_converter import convert_xml_to_sql
from node_cache import save_node_dict, load_node_dict, delete_node_dict_pickle, get_pickle_path
from file_processor import construct_node_dict, validate_node_dict, dig_mapping_generator
from bulk_processor import bulk_processor
from werkzeug.utils import secure_filename
import local_storage
import uuid
from mapping_sql_generator import generate_sql_from_mapping
from nested_cv import (
    get_session_store,
    parse_mapping_content,
    build_graph,
    auto_resolve_links,
    MappingService,
    start_generation_task,
    get_generation_result,
    NestedSession,
    CvArtifact,
    DependencyLink,
    MappingEntry,
    EmissionMode,
)
from excel_encrypt import decrypt_xlsx_file
from api_client import api_call_flash, api_call
import base64

from urllib.parse import urlparse
import re
from datetime import timedelta

# Cloud Run sets K_SERVICE env variable automatically
if not os.getenv("K_SERVICE"):
    # Not running on Cloud Run, so load local env file
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path)
else:
    # Running on Cloud Run, do NOT load .env
    pass

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

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

            logger.info(f"Analyzing XML file: {file_name} for user: {user_email}")

            # Count nodes and validate
            result = count_xml_nodes(xml_content)

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

def _perform_conversion_task(task_id, xml_content, file_name, user_email, target=None):
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
    }
    logger.info(f"Task {task_id}: Starting background conversion for {file_name} by {user_email}")

    # Retrieve XML content from conversion_sessions
    if task_id not in conversion_sessions:
        raise ValueError(f"Session ID {task_id} not found for background task.")

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
            except Exception as e:
                logger.warning(f"Task {task_id}: Self-ping failed: {e}")
            time.sleep(10) # Ping every 10 seconds

    threading.Thread(target=self_ping, daemon=True).start()

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
        conversion_tasks[task_id].update({"message": "Storing files...", "progress": 75})
        zip_file_content = conversion_result["zip_file_content"]
        data_mapping_content = conversion_result["Data_mapping"]
        base_filename = conversion_result["view_name"]
        cv_object_name = base_filename.replace(" ", "_").replace("-", "_")

        # Save to disk via local_storage
        try:
            saved = local_storage.save_result(
                task_id,
                zip_file_content,
                data_mapping_content,
                metadata={"cv_object_name": cv_object_name}
            )
            logger.info(f"Task {task_id}: Saved to disk at {saved['sql_url']}")
        except Exception as e:
            logger.warning(f"Task {task_id}: Failed to save to disk: {e}")

        conversion_tasks[task_id].update({
            "status": "COMPLETED",
            "progress": 100,
            "message": "Conversion complete.",
            "result": {
                "sql_content": zip_file_content,
                "mapping_content": data_mapping_content,
                "sql_download_name": f"{cv_object_name}_converted.zip",
                "mapping_download_name": f"{cv_object_name}_mapping_sheet.xlsx"
            }
        })
        logger.info(f"Task {task_id}: Background conversion completed successfully.")
    else:
        raise Exception(conversion_result.get("error", "Conversion failed"))

    # Clean up session data (xml_content is no longer needed)
    if task_id in conversion_sessions:
        del conversion_sessions[task_id]
    delete_node_dict_pickle(task_id)

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
            analysis_result = count_xml_nodes(xml_content)

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
        }

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
            args=(task_id, xml_content, file_name, user_email),
            kwargs={"target": target}
        ).start()

        logger.info(f"Conversion task {task_id} started in background thread.")

        # Return success immediately with the task_id
        return jsonify({
            "success": True,
            "message": "Conversion initiated successfully.",
            "task_id": task_id,
            "node_count": node_count,
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
    """
    if request.method == 'OPTIONS':
        return '', 200

    task_info = conversion_tasks.get(task_id)
    if task_info:
        # Recursively strip bytes from nested dicts so everything is JSON-serializable
        def strip_bytes(obj):
            if isinstance(obj, dict):
                return {k: strip_bytes(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [strip_bytes(x) for x in obj]
            elif isinstance(obj, bytes):
                return None
            return obj
        return jsonify(strip_bytes(task_info)), 200

    return jsonify({"error": "Task not found", "status": "UNKNOWN"}), 404

@app.route('/api/previous-conversions', methods=['GET', 'OPTIONS'])
def list_previous_conversions():
    """
    List all available mapping files from previous conversions.
    Scans OUTPUT_DIR (or PREVIOUS_CONVERSATIONS_DIR) for *_mapping_sheet.xlsx files.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        # Use PREVIOUS_CONVERSATIONS_DIR if set, otherwise fall back to OUTPUT_DIR
        base_dir = os.getenv("PREVIOUS_CONVERSATIONS_DIR") or local_storage.OUTPUT_DIR
        logger.info(f"Scanning for previous conversions in: {base_dir}")

        conversions = []

        if not os.path.isdir(base_dir):
            logger.warning(f"Previous conversions directory does not exist: {base_dir}")
            return jsonify({"success": True, "conversions": []}), 200

        for task_id in os.listdir(base_dir):
            task_dir = os.path.join(base_dir, task_id)
            if not os.path.isdir(task_dir):
                continue

            # Look for mapping sheet file
            mapping_file = None
            mapping_name = None
            for fname in os.listdir(task_dir):
                if fname.endswith("_mapping_sheet.xlsx"):
                    mapping_file = fname
                    mapping_name = fname.replace("_mapping_sheet.xlsx", "")
                    break

            if mapping_file:
                mapping_path = os.path.join(task_dir, mapping_file)
                mtime = os.path.getmtime(mapping_path)
                conversions.append({
                    "task_id": task_id,
                    "file_name": mapping_name,
                    "mapping_file": mapping_file,
                    "modified_at": datetime.fromtimestamp(mtime).isoformat()
                })

        # Sort by modified time, newest first
        conversions.sort(key=lambda x: x["modified_at"], reverse=True)

        logger.info(f"Found {len(conversions)} previous conversion(s)")
        return jsonify({"success": True, "conversions": conversions}), 200

    except Exception as e:
        logger.error(f"Error listing previous conversions: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Failed to list conversions: {str(e)}"}), 500

@app.route('/api/download/<session_id>', methods=['GET', 'OPTIONS'])
def download_converted_file(session_id):
    """
    Securely download a converted file using its session ID (now also used as task_id).
    Serves files stored in-memory after conversion completes.
    Falls back to local disk (OUTPUT_DIR) for previously completed conversions.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        logger.info(f"Download request received for session ID/Task ID: {session_id}")
        file_type = request.args.get('type', 'sql').lower()
        logger.debug(f"Requested file type: {file_type}")

        download_url = None
        download_name = None
        mimetype = 'application/octet-stream' # Default to generic

        # --- Check in-memory conversion_tasks first (for recent conversions) ---
        task_info = conversion_tasks.get(session_id)
        if task_info and task_info.get("status") == "COMPLETED" and task_info.get("result"):
            logger.debug(f"Found task_info in memory for {session_id}. Result: {task_info['result']}")

            if file_type == 'mapping':
                download_url = task_info["result"].get("data_mapping_url")
                download_name = task_info["result"].get("mapping_download_name")
                mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif file_type == 'sql':
                file_content = task_info["result"].get("sql_content")
                download_name = task_info["result"].get("sql_download_name")
                mimetype = 'application/zip'
            else:
                logger.error(f"Invalid file type '{file_type}' for session ID: {session_id} (from in-memory cache).")
                return jsonify({"error": "Invalid file type requested"}), 400

            if not download_name:
                logger.error(f"Download name not found in completed task {session_id} for type '{file_type}' (from in-memory cache).")
            elif file_type == 'mapping' and download_url:
                # Mapping: serve from path directly
                logger.debug(f"In-memory mapping: serving from path {download_url}")
                return send_file(
                    download_url,
                    as_attachment=True,
                    download_name=download_name,
                    mimetype=mimetype
                )
            elif file_content:
                # SQL: serve from bytes buffer
                logger.debug(f"In-memory SQL: Name={download_name}, MimeType={mimetype}")
                buffer = io.BytesIO(file_content)
                buffer.seek(0)
                return send_file(
                    buffer,
                    as_attachment=True,
                    download_name=download_name,
                    mimetype=mimetype
                )
            else:
                logger.error(f"File content/path not found in completed task {session_id} for type '{file_type}' (from in-memory cache).")

        # --- Fall back to local disk (for completed conversions from previous sessions) ---
        logger.debug(f"Checking local disk for {session_id}...")
        result_info = local_storage.get_result_info(session_id)

        if result_info:
            if file_type == 'mapping':
                download_url = result_info.get("data_mapping_url")
                download_name = result_info.get("mapping_download_name")
                mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif file_type == 'sql':
                download_url = result_info.get("sql_url")
                download_name = result_info.get("sql_download_name")
                mimetype = 'application/zip'

            if download_url and os.path.isfile(download_url):
                logger.debug(f"Serving {file_type} file from local disk: {download_url}")
                return send_file(
                    download_url,
                    as_attachment=True,
                    download_name=download_name,
                    mimetype=mimetype
                )
            else:
                logger.warning(f"Local file not found for {session_id}: {download_url}")

        logger.warning(f"No conversion record found for session ID: {session_id} in active tasks.")
        return jsonify({"error": "File not found or unauthorized access"}), 404

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
        result = count_xml_nodes(xml_content)

        # Remove the validated XML from response (we're not storing it)
        if "validated_xml" in result:
            del result["validated_xml"]

        logger.debug(f"Validate endpoint returning: node_count={result.get('node_count')}")

        return jsonify(result)

    except Exception as e:
        logger.error(f"Validation error: {str(e)}")
        return jsonify({"error": f"Validation failed: {str(e)}"}), 500

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
    Returns list of files with node counts
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
        result = bulk_processor.analyze_zip(zip_content)

        if result["success"]:
            # Store extracted files for later conversion
            bulk_files = result["files"]
            # Return analysis result
            return jsonify({
                "success": True,
                "files": bulk_files,
                "total_files": result["total_files"],
                "total_nodes": result["total_nodes"]
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
            target=lambda: bulk_processor.convert_bulk(files, bulk_task_id),
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

        # Strip bytes from results before JSON serialization
        clean_results = [
            {k: v for k, v in r.items() if not isinstance(v, bytes)}
            for r in status.get("results", [])
        ]
        return jsonify({
            "status": status["status"],
            "progress": progress_pct,
            "total_files": status["progress"]["total"],
            "completed_files": status["progress"]["completed"],
            "failed_files": status["progress"]["failed"],
            "results": clean_results,
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
                if result.get("sql_content"):
                    # Add to ZIP with original filename
                    file_name = result.get("download_name", "converted.sql")
                    zf.writestr(file_name, result["sql_content"])

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

# ==================== NESTED CV FLATTENER ENDPOINTS ====================

def _session_or_404(session_id: str):
    store = get_session_store()
    session = store.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return session


# POST /api/nested/sessions — Create session
@app.route('/api/nested/sessions', methods=['POST', 'OPTIONS'])
def nested_create_session():
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.get_json() or {}
        target_dialect = data.get("target_dialect", "bigquery")
        output_format = data.get("output_format", "sql")
        if output_format not in ("sql", "pyspark"):
            return jsonify({"error": "Invalid output_format. Must be 'sql' or 'pyspark'"}), 400

        store = get_session_store()
        session = store.create_session(target_dialect, output_format)
        return jsonify({"success": True, "session": session.to_dict()}), 200
    except Exception as e:
        logger.error(f"nested_create_session error: {e}")
        return jsonify({"error": str(e)}), 500


# GET /api/nested/sessions/<session_id> — Get session
@app.route('/api/nested/sessions/<session_id>', methods=['GET', 'OPTIONS'])
def nested_get_session(session_id):
    if request.method == 'OPTIONS':
        return '', 200
    result = _session_or_404(session_id)
    if isinstance(result, tuple):
        return result
    return jsonify({"success": True, "session": result.to_dict()}), 200


# DELETE /api/nested/sessions/<session_id> — Delete session
@app.route('/api/nested/sessions/<session_id>', methods=['DELETE', 'OPTIONS'])
def nested_delete_session(session_id):
    if request.method == 'OPTIONS':
        return '', 200
    store = get_session_store()
    deleted = store.delete_session(session_id)
    if not deleted:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"success": True}), 200


# POST /api/nested/sessions/<session_id>/cvs — Add CV
@app.route('/api/nested/sessions/<session_id>/cvs', methods=['POST', 'OPTIONS'])
def nested_add_cv(session_id):
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    try:
        data = request.get_json() or {}
        file_content = data.get("file_content", "")
        file_name = data.get("file_name", "uploaded.xlsx")
        password = data.get("password")

        if not file_content:
            return jsonify({"error": "No file content provided"}), 400

        artifact = parse_mapping_content(file_content, file_name, password)

        # Auto-resolve links
        all_artifacts = list(session.artifacts.values()) + [artifact]
        proposed_links = auto_resolve_links(all_artifacts)

        # Add artifact to session
        session.artifacts[artifact.artifact_id] = artifact

        # Add auto-resolved links
        for link in proposed_links:
            existing = [l for l in session.dependency_links
                        if l.consumer_artifact_id == link.consumer_artifact_id
                        and l.source_ref_canonical == link.source_ref_canonical]
            if not existing:
                session.dependency_links.append(link)

        # Auto-add mappings from this artifact
        ms = MappingService(list(session.artifacts.values()), session.global_mappings)
        for m in artifact.mapping_rows:
            if m.artifact_id is None:
                m.artifact_id = artifact.artifact_id
        session.global_mappings.extend(artifact.mapping_rows)

        store = get_session_store()
        store.update_session(session)

        return jsonify({"success": True, "artifact": artifact.to_dict()}), 200
    except Exception as e:
        logger.error(f"nested_add_cv error: {e}")
        return jsonify({"error": str(e)}), 500


# PATCH /api/nested/sessions/<session_id>/cvs/<artifact_id> — Update CV
@app.route('/api/nested/sessions/<session_id>/cvs/<artifact_id>', methods=['PATCH', 'OPTIONS'])
def nested_update_cv(session_id, artifact_id):
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    try:
        data = request.get_json() or {}
        artifact = session.artifacts.get(artifact_id)
        if not artifact:
            return jsonify({"error": "Artifact not found"}), 404

        if "emission_mode" in data:
            artifact.emission_mode = data["emission_mode"]
        if "target_view_name" in data:
            artifact.target_view_name = data["target_view_name"]
        if "cv_display_name" in data:
            artifact.cv_display_name = data["cv_display_name"]

        store = get_session_store()
        store.update_session(session)
        return jsonify({"success": True, "artifact": artifact.to_dict()}), 200
    except Exception as e:
        logger.error(f"nested_update_cv error: {e}")
        return jsonify({"error": str(e)}), 500


# DELETE /api/nested/sessions/<session_id>/cvs/<artifact_id> — Remove CV
@app.route('/api/nested/sessions/<session_id>/cvs/<artifact_id>', methods=['DELETE', 'OPTIONS'])
def nested_delete_cv(session_id, artifact_id):
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    if artifact_id in session.artifacts:
        del session.artifacts[artifact_id]
    # Remove affected links
    session.dependency_links = [
        l for l in session.dependency_links
        if l.consumer_artifact_id != artifact_id and l.producer_artifact_id != artifact_id
    ]
    # Remove affected mappings
    session.global_mappings = [
        m for m in session.global_mappings if m.artifact_id != artifact_id
    ]
    store = get_session_store()
    store.update_session(session)
    return jsonify({"success": True}), 200


# PUT /api/nested/sessions/<session_id>/links — Save dependency resolutions
@app.route('/api/nested/sessions/<session_id>/links', methods=['PUT', 'OPTIONS'])
def nested_resolve_links(session_id):
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    try:
        data = request.get_json() or {}
        links_data = data.get("links", [])
        session.dependency_links = [
            DependencyLink(
                consumer_artifact_id=l["consumer_artifact_id"],
                source_ref_canonical=l["source_ref_canonical"],
                resolution=l["resolution"],
                producer_artifact_id=l.get("producer_artifact_id"),
            )
            for l in links_data
        ]
        store = get_session_store()
        store.update_session(session)
        return jsonify({"success": True}), 200
    except Exception as e:
        logger.error(f"nested_resolve_links error: {e}")
        return jsonify({"error": str(e)}), 500


# PUT /api/nested/sessions/<session_id>/mappings — Save unified mappings
@app.route('/api/nested/sessions/<session_id>/mappings', methods=['PUT', 'OPTIONS'])
def nested_update_mappings(session_id):
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    try:
        data = request.get_json() or {}
        mappings_data = data.get("mappings", [])
        session.global_mappings = [
            MappingEntry(
                source_ref_canonical=m["source_ref_canonical"],
                source_column_raw=m["source_column_raw"],
                target_table=m["target_table"],
                target_column=m["target_column"],
                artifact_id=m.get("artifact_id"),
            )
            for m in mappings_data
        ]
        store = get_session_store()
        store.update_session(session)
        return jsonify({"success": True}), 200
    except Exception as e:
        logger.error(f"nested_update_mappings error: {e}")
        return jsonify({"error": str(e)}), 500


# POST /api/nested/sessions/<session_id>/validate — Validate graph
@app.route('/api/nested/sessions/<session_id>/validate', methods=['POST', 'OPTIONS'])
def nested_validate(session_id):
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    try:
        artifacts = list(session.artifacts.values())
        links = session.dependency_links
        mappings = session.global_mappings

        if not artifacts:
            return jsonify({
                "success": False,
                "valid": False,
                "errors": [{"level": "error", "code": "NO_ARTIFACTS", "message": "No CVs added to session"}],
                "warnings": [],
            }), 200

        # Build graph and validate
        graph = build_graph(artifacts, links)
        graph_errors, graph_warnings = graph.validate()

        # Validate mappings
        ms = MappingService(artifacts, mappings)
        map_errors, map_warnings = ms.validate()

        all_errors = graph_errors + map_errors
        all_warnings = graph_warnings + map_warnings

        summary = graph.build_summary().to_dict() if not all_errors else None

        return jsonify({
            "success": True,
            "valid": len(all_errors) == 0,
            "errors": [e.to_dict() if hasattr(e, 'to_dict') else e for e in all_errors],
            "warnings": [w.to_dict() if hasattr(w, 'to_dict') else w for w in all_warnings],
            "graph_summary": summary,
        }), 200
    except Exception as e:
        logger.error(f"nested_validate error: {e}")
        return jsonify({"error": str(e)}), 500


# POST /api/nested/sessions/<session_id>/generate — Start generation
@app.route('/api/nested/sessions/<session_id>/generate', methods=['POST', 'OPTIONS'])
def nested_generate(session_id):
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    try:
        task = start_generation_task(session)
        return jsonify({"success": True, "task_id": task.task_id}), 202
    except Exception as e:
        logger.error(f"nested_generate error: {e}")
        return jsonify({"error": str(e)}), 500


# GET /api/nested/tasks/<task_id> — Get task status
@app.route('/api/nested/tasks/<task_id>', methods=['GET', 'OPTIONS'])
def nested_get_task(task_id):
    if request.method == 'OPTIONS':
        return '', 200
    store = get_session_store()
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify({
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "result_url": task.result_url,
        "diagnostics": [d.to_dict() if hasattr(d, 'to_dict') else d for d in task.diagnostics],
    }), 200


# GET /api/nested/tasks/<task_id>/download — Download result
@app.route('/api/nested/tasks/<task_id>/download', methods=['GET', 'OPTIONS'])
def nested_download(task_id):
    if request.method == 'OPTIONS':
        return '', 200
    import os as _os
    from dotenv import load_dotenv
    load_dotenv()
    output_dir = _os.environ.get("OUTPUT_DIR", "/tmp/h2s_output")
    filename = f"nested_cv_{task_id[:8]}"
    logger.info(f"[DOWNLOAD] task_id={task_id} output_dir={output_dir}")
    for ext in ('.pyspark', '.sql'):
        fp = _os.path.join(output_dir, filename + ext)
        logger.info(f"[DOWNLOAD] checking {fp} exists={_os.path.exists(fp)}")
    store = get_session_store()
    task = store.get_task(task_id)
    logger.info(f"[DOWNLOAD] task={'found' if task else 'NOT FOUND'} status={task.status if task else None}")
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task.status != "COMPLETED":
        return jsonify({"error": "Task not completed"}), 400

    filepath = get_generation_result(task)
    logger.info(f"[DOWNLOAD] get_generation_result returned filepath={filepath}")
    if not filepath or not _os.path.exists(filepath):
        logger.info(f"[DOWNLOAD] filepath not found on disk")
        return jsonify({"error": "Result file not found"}), 404

    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1]
    mimetype = 'text/x-sql' if ext == '.sql' else 'text/plain'

    return send_file(
        filepath,
        as_attachment=True,
        download_name=filename,
        mimetype=mimetype,
    )

# Add the rest of your routes here...
# (Keep all your existing routes)

if __name__ == "__main__":
    import asyncio
    import sys

    # Fix for OSError: [WinError 10038] An operation was attempted on something that is not a socket
    # on Windows when using asgiref / Flask async routes in a multi-threaded Server.
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app.run(debug=True, port=8080, use_reloader=False)
