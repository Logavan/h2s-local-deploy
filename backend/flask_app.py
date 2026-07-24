from flask import Flask, request, jsonify, send_file, abort, redirect
import asyncio
from flask_cors import CORS
import io
import os
import json
import re
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

# Load .env BEFORE any application imports that might read config/settings
if not os.getenv("K_SERVICE"):
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path)

# Import our custom modules
from node_counter import count_xml_nodes
from sql_converter import convert_xml_to_sql
from node_cache import save_node_dict, load_node_dict, delete_node_dict_pickle
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
    NestedSession,
    CvArtifact,
    DependencyLink,
    MappingEntry,
    EmissionMode,
    OutputFormat,
)
from excel_encrypt import decrypt_xlsx_file
from api_client import api_call_flash, api_call
import base64

from urllib.parse import urlparse

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

            # Sanitize filename to prevent path traversal in session_id
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', file_name)
            session_id = f"{safe_name}_{datetime.now().timestamp()}"

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
        if output_format == "pyspark" and target_dialect not in ("azure", "databricks"):
            return jsonify({
                "error": "PySpark output is only available for Microsoft Fabric (azure) and Databricks."
            }), 400

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


# POST /api/nested/sessions/<session_id>/cvs — Add CV (JSON content)
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


# POST /api/nested/sessions/<session_id>/cvs/xlsx — Add CV from XLSX file
@app.route('/api/nested/sessions/<session_id>/cvs/xlsx', methods=['POST', 'OPTIONS'])
def nested_add_cv_from_xlsx(session_id):
    """
    Accepts an XLSX mapping file upload, processes it through the mapping engine
    to extract sqlInfo and mappingInfo, then creates a CvArtifact from it.
    Reuses the same XLSX processing logic as the Mapping Tool.
    """
    if request.method == 'OPTIONS':
        return '', 200
    session = _session_or_404(session_id)
    if isinstance(session, tuple):
        return session
    try:
        if 'xlsxFile' not in request.files:
            return jsonify({"error": "No XLSX file provided"}), 400

        xlsx_file = request.files['xlsxFile']
        if xlsx_file.filename == '':
            return jsonify({"error": "No selected file"}), 400

        password = request.form.get('password', 'mypassword123la')
        parent_source_ref = request.form.get('parentSourceRef', '').strip()
        parent_artifact_id = request.form.get('parentArtifactId', '').strip()
        selected_source = request.form.get('selectedSource', '').strip()
        inspect_only = request.form.get('inspectOnly', '').lower() == 'true'

        if bool(parent_source_ref) != bool(parent_artifact_id):
            return jsonify({"error": "parentSourceRef and parentArtifactId must be provided together"}), 400
        if parent_artifact_id and parent_artifact_id not in session.artifacts:
            return jsonify({"error": "Parent artifact not found"}), 404

        # Decrypt and read the XLSX (same as mapping engine)
        xls = decrypt_xlsx_file(xlsx_file, password)

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

        sql_info_records = sql_info_df.to_dict(orient='records')
        renamed_mapping_df = mapping_info_df.rename(columns={
            'Original Table': 'sourceTable',
            'Original Column': 'sourceField',
            'New Table': 'targetTable',
            'New Column': 'targetField'
        })
        mapping_records = renamed_mapping_df.to_dict(orient='records')

        logger.info(f"[XLSX] sql_info columns: {list(sql_info_df.columns)}")
        logger.info(f"[XLSX] sql_info_records[0] keys: {list(sql_info_records[0].keys()) if sql_info_records else 'empty'}")
        logger.info(f"[XLSX] mapping_records[0]: {mapping_records[0] if mapping_records else 'empty'}")

        # Build a synthetic JSON structure compatible with parse_mapping_content (v2 format)
        # The sql_info tab has 'SourceTable_mapping_fields' as a string dict like "{'scalmonth': ['col1', 'col2']}"
        # We parse it to extract unique source tables.
        # Fallback: also extract FROM clause from SQL to get table names.
        import ast, re
        dependencies = []
        seen_sources = set()
        for row in sql_info_records:
            mapping_fields_raw = str(row.get('SourceTable_mapping_fields', '')).strip()
            sql_content = str(row.get('Chunk SQL Primary Optimized Base', ''))
            # Try SourceTable_mapping_fields first
            tables_found = set()
            mapping_fields_dict = {}
            if mapping_fields_raw and mapping_fields_raw != 'nan':
                try:
                    mapping_fields = ast.literal_eval(mapping_fields_raw)
                    if isinstance(mapping_fields, dict):
                        for src_table in mapping_fields.keys():
                            tables_found.add(str(src_table).strip())
                except (ValueError, SyntaxError):
                    pass
            # Fallback: extract FROM table_name from SQL
            if not tables_found and sql_content:
                from_matches = re.findall(
                    r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                    sql_content, re.IGNORECASE
                )
                tables_found.update(from_matches)
            source_matches_selection = not selected_source or any(
                selected_source.upper() in {
                    str(source).upper(),
                    str(source).split(".")[-1].upper(),
                }
                for source in tables_found
            )
            if not source_matches_selection:
                continue
            for src_table in tables_found:
                if not src_table or src_table in seen_sources:
                    continue
                seen_sources.add(src_table)
                dependencies.append({
                    "source_ref_raw": src_table,
                    "source_ref_canonical": src_table.upper(),
                    "object_kind": _infer_object_kind(src_table),
                    "referenced_by_node": str(row.get('Node name', row.get('node_name', 'unknown'))).strip(),
                    "required_columns": mapping_fields.get(src_table, []) if mapping_fields_raw and mapping_fields_raw != "nan" else []
                })

        logger.info(f"[XLSX] final dependencies count: {len(dependencies)}, deps: {dependencies}")

        # Build output_schema from mapping_info distinct columns
        output_schema = []
        for i, row in enumerate(mapping_records):
            col_name = str(row.get('sourceField', row.get('source_column', f'COL_{i}'))).strip()
            if col_name and col_name != 'nan':
                output_schema.append({
                    "ordinal": i,
                    "column_name": col_name,
                    "data_type": None,
                    "nullable": True
                })

        # Build sql_chunks from sql_info
        sql_chunks = []
        for i, row in enumerate(sql_info_records):
            node_name = str(row.get('Node name', row.get('node_name', f'node_{i}'))).strip()
            sql_content = str(row.get('Chunk SQL Primary Optimized Base', row.get('sql_statement', '-- No SQL'))).strip()
            if sql_content and sql_content != 'nan':
                sql_chunks.append({
                    "chunk_id": str(uuid.uuid4()),
                    "sql_content": sql_content,
                    "node_name": node_name
                })

        # Build mapping_rows from mapping_records
        mapping_rows = []
        for row in mapping_records:
            src_table = str(row.get('sourceTable', '')).strip()
            src_col = str(row.get('sourceField', '')).strip()
            tgt_table = str(row.get('targetTable', '')).strip()
            tgt_col = str(row.get('targetField', '')).strip()
            if src_table and src_col:
                mapping_rows.append({
                    "source_ref_canonical": src_table.upper(),
                    "source_column_raw": src_col,
                    "target_table": tgt_table,
                    "target_column": tgt_col,
                    "artifact_id": None
                })

        # Assemble into v2-compatible artifact JSON
        cv_display_name = xlsx_file.filename.replace('.xlsx', '').replace('.xls', '')
        artifact_json = json.dumps({
            "schema_version": 2,
            "artifact_manifest": {
                "cv_canonical_id": None,
                "cv_display_name": cv_display_name
            },
            "cv_name": cv_display_name,
            "dependencies": dependencies,
            "output_schema": output_schema,
            "sql_chunks": sql_chunks,
            "mapping_info": mapping_rows
        })

        # Reuse the existing parse_mapping_content to build the CvArtifact
        artifact = parse_mapping_content(artifact_json, xlsx_file.filename, password)
        # Store raw sql_info for reuse by generate_sql_from_mapping (parity with Mapping Tool)
        artifact.sql_info_raw = sql_info_records
        # Override mapping_rows with the ones we extracted from the XLSX
        artifact.mapping_rows = []
        for m in mapping_rows:
            entry = MappingEntry(
                source_ref_canonical=m["source_ref_canonical"],
                source_column_raw=m["source_column_raw"],
                target_table=m["target_table"],
                target_column=m["target_column"],
                artifact_id=artifact.artifact_id
            )
            artifact.mapping_rows.append(entry)

        if inspect_only:
            # Build a de-duplicated list of source tables from the mapping_info sheet.
            # This is the most reliable source: every workbook has a mapping_info sheet
            # with sourceTable/sourceField columns, even when the sql_info sheet is empty
            # or has unexpected column names.
            seen_source_tables: set[str] = set()
            source_tables_list: list[dict] = []
            for row in mapping_records:
                src_table = str(row.get("sourceTable", row.get("Original Table", ""))).strip()
                src_field = str(row.get("sourceField", row.get("Original Column", ""))).strip()
                tgt_table = str(row.get("targetTable", row.get("New Table", ""))).strip()
                if not src_table or src_table == "nan" or src_table.upper() in seen_source_tables:
                    continue
                seen_source_tables.add(src_table.upper())
                source_tables_list.append({
                    "source_table_name": src_table,
                    "source_field": src_field,
                    "target_table": tgt_table,
                })

            # Extract output columns from the LAST chunk SQL (which is the final output
            # of this CV that the parent CV will reference). The columns come from
            # SELECT aliases: e.g. "SELECT SUBSTR(...) AS datafield_year, ... AS calmonth_s".
            # These are the actual linkage columns between parent and nested CV.
            output_columns: list[str] = []
            last_chunk_sql = ""
            if sql_info_records:
                # Sort by chunk number ascending, pick the last one
                def _chunk_num(row):
                    try:
                        return int(row.get("Chunk Number", 0) or 0)
                    except (ValueError, TypeError):
                        return 0
                sorted_chunks = sorted(sql_info_records, key=_chunk_num)
                last_row = sorted_chunks[-1] if sorted_chunks else {}
                last_chunk_sql = str(last_row.get("Chunk SQL Primary Optimized Base", "")).strip()
                if last_chunk_sql and last_chunk_sql != "nan":
                    # Extract ONLY the SELECT clause (between SELECT and FROM) so we
                    # don't pick up table aliases like `scalmonth AS t1` from FROM/JOIN.
                    select_match = re.search(
                        r'\bSELECT\b(.*?)\bFROM\b',
                        last_chunk_sql,
                        re.IGNORECASE | re.DOTALL
                    )
                    select_clause = select_match.group(1) if select_match else last_chunk_sql
                    # Match aliases: `AS <name>` (case-insensitive, optional double-quotes)
                    alias_matches = re.findall(
                        r'\bAS\s+"?([A-Za-z_][A-Za-z0-9_]*)"?\s*(?=[\s,)])',
                        select_clause,
                        re.IGNORECASE
                    )
                    # Dedupe while preserving order
                    seen_aliases = set()
                    for alias in alias_matches:
                        if alias.lower() not in seen_aliases:
                            seen_aliases.add(alias.lower())
                            output_columns.append(alias)

            # Also extract unique source tables from the last chunk's FROM clause
            # (in case mapping_info is missing/empty but SQL has them)
            last_chunk_sources: list[str] = []
            if last_chunk_sql and last_chunk_sql != "nan":
                from_matches = re.findall(
                    r'\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)',
                    last_chunk_sql,
                    re.IGNORECASE
                )
                seen_src = set()
                for src in from_matches:
                    if src.lower() not in seen_src:
                        seen_src.add(src.lower())
                        last_chunk_sources.append(src)

            return jsonify({
                "success": True,
                "sql_info": sql_info_records,
                "mapping_info": mapping_records,
                "source_tables": source_tables_list,
                "output_columns": output_columns,
                "last_chunk_sql": last_chunk_sql,
                "last_chunk_sources": last_chunk_sources,
            }), 200

        if parent_artifact_id:
            canonical_parent_ref = parent_source_ref.upper()
            parent_artifact = session.artifacts[parent_artifact_id]
            matching_dependency = any(
                dep.source_ref_canonical.upper() == canonical_parent_ref
                for dep in parent_artifact.dependencies
            )
            if not matching_dependency:
                return jsonify({"error": "Parent source reference was not found on the parent artifact"}), 400

        # Auto-resolve links with existing artifacts
        all_artifacts = list(session.artifacts.values()) + [artifact]
        proposed_links = auto_resolve_links(all_artifacts)

        # Add artifact to session
        session.artifacts[artifact.artifact_id] = artifact

        # Merge heuristic links, excluding the dependency explicitly resolved below
        for link in proposed_links:
            if (
                parent_artifact_id
                and link.consumer_artifact_id == parent_artifact_id
                and link.source_ref_canonical == parent_source_ref.upper()
            ):
                continue
            existing = [l for l in session.dependency_links
                        if l.consumer_artifact_id == link.consumer_artifact_id
                        and l.source_ref_canonical == link.source_ref_canonical]
            if not existing:
                session.dependency_links.append(link)

        if parent_artifact_id:
            canonical_parent_ref = parent_source_ref.upper()
            session.dependency_links = [
                link for link in session.dependency_links
                if not (
                    link.consumer_artifact_id == parent_artifact_id
                    and link.source_ref_canonical.upper() == canonical_parent_ref
                )
            ]
            session.dependency_links.append(DependencyLink(
                consumer_artifact_id=parent_artifact_id,
                source_ref_canonical=canonical_parent_ref,
                resolution="uploaded_cv",
                producer_artifact_id=artifact.artifact_id,
            ))

        # Add mappings from this artifact
        for m in artifact.mapping_rows:
            if m.artifact_id is None:
                m.artifact_id = artifact.artifact_id
        session.global_mappings.extend(artifact.mapping_rows)

        store = get_session_store()
        store.update_session(session)

        # Enrich sql_info records with source_table_name so frontend can display the tree
        import ast, re
        for row in sql_info_records:
            mapping_fields_raw = str(row.get("SourceTable_mapping_fields", "")).strip()
            if mapping_fields_raw and mapping_fields_raw != "nan":
                try:
                    mapping_fields = ast.literal_eval(mapping_fields_raw)
                    if isinstance(mapping_fields, dict) and mapping_fields:
                        row["source_table_name"] = list(mapping_fields.keys())[0]
                        continue
                except (ValueError, SyntaxError):
                    pass
            # Fallback: extract first table from SQL FROM clause
            sql_content = str(row.get("Chunk SQL Primary Optimized Base", ""))
            from_matches = re.findall(
                r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)',
                sql_content, re.IGNORECASE
            )
            row["source_table_name"] = from_matches[0].lower() if from_matches else ""

        return jsonify({
            "success": True,
            "artifact": artifact.to_dict(),
            "session": session.to_dict(),
            "sql_info": sql_info_records,
            "mapping_info": mapping_records
        }), 200

    except Exception as e:
        logger.error(f"nested_add_cv_from_xlsx error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


def _infer_object_kind(ref: str) -> str:
    """Infer whether a reference is a table or calculation view."""
    upper = ref.upper()
    cv_indicators = ["_CV", "_cv", "CALC_VIEW", ".cv", "/cv/"]
    table_indicators = ["_T", "_tbl", "TABLE", ".table", "/tab/"]
    for indicator in cv_indicators:
        if indicator in upper:
            return "calculation_view"
    for indicator in table_indicators:
        if indicator in upper:
            return "physical_table"
    return "unknown"


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
        "result_content": task.result_content,  # In-memory content for editor display
        "diagnostics": [d.to_dict() if hasattr(d, 'to_dict') else d for d in task.diagnostics],
    }), 200


# GET /api/nested/tasks/<task_id>/download — Download result
@app.route('/api/nested/tasks/<task_id>/download', methods=['GET', 'OPTIONS'])
def nested_download(task_id):
    if request.method == 'OPTIONS':
        return '', 200
    store = get_session_store()
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task.status != "COMPLETED":
        return jsonify({"error": "Task not completed"}), 400
    if not task.result_content:
        return jsonify({"error": "Result content not found"}), 404

    # Determine extension and mimetype from task.output_format
    content = task.result_content
    if task.output_format == OutputFormat.PYSPARK.value:
        ext = ".pyspark"
        mimetype = 'text/plain'
        download_name = f"nested_cv_{task_id[:8]}.pyspark"
    else:
        ext = ".sql"
        mimetype = 'text/x-sql'
        download_name = f"nested_cv_{task_id[:8]}.sql"

    return send_file(
        io.BytesIO(content.encode('utf-8')),
        as_attachment=True,
        download_name=download_name,
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
