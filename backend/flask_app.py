# =============================================================================
# Performance: configure environment BEFORE importing heavy Google SDKs.
# The `google.cloud.bigquery` client is loaded transitively via
# `from file_processor import ...` below; setting GOOGLE_CLOUD_DISABLE_GRPC
# afterwards has no effect, so we set it here at the very top to skip the
# gRPC transport and use REST instead. The actual assignment is performed
# immediately after `import os` further down (you can't call `os.environ`
# before the module is bound). This is a no-op if the var is already set
# in the environment (e.g. in Cloud Run / Docker). All existing logic is
# preserved — the duplicate assignment lower in this file simply re-asserts
# the same value.
# =============================================================================

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

# (Performance) Apply the env var described in the header comment above.
# Runs as the first thing after `import os`, before any heavy Google SDK
# imports further below.
os.environ.setdefault("GOOGLE_CLOUD_DISABLE_GRPC", "true")

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
    OutputFormat,
)
from nested_cv.artifact_parser import infer_object_kind
from excel_encrypt import decrypt_xlsx_file
from api_client import api_call_flash, api_call
from licensing.verifier import check_or_exit, quick_status
from hmac_auth import install_hmac_middleware
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
    # License gate — runs before Flask is constructed so we never bind to a
    # port with an invalid license. Local development uses a license signed
    # for the dev's own machine fingerprint; production uses a customer-
    # bound license. Same code path either way — no dev-mode bypass.
    # `__file__` here is flask_app.py — the binary integrity check verifies
    # *this* file, not whatever __main__ happens to be (e.g. unittest's runner).
    _license_info = check_or_exit(entrypoint=__file__)

    app = Flask(__name__)
    app.config["LICENSE_INFO"] = _license_info

    # Configure CORS. Default is a permissive `*` only for local dev (no
    # K_SERVICE env); production deployments must set H2S_ALLOWED_ORIGINS
    # to a comma-separated list of allowed browser origins. The wildcard
    # default is unsafe because the HMAC signing key is fetched from
    # /api/hmac/key, which is exempt from HMAC — a permissive CORS policy
    # lets any browser-origin fetch it.
    _allowed_origins_env = os.getenv("H2S_ALLOWED_ORIGINS", "").strip()
    if _allowed_origins_env:
        _cors_origins = [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
    elif IS_CLOUD_RUN:
        # Cloud Run / production: refuse to start without explicit origins.
        # Falling back to `*` here would expose /api/hmac/key to any browser.
        logger.error(
            "H2S_ALLOWED_ORIGINS is not set in production. Refusing to start "
            "with permissive CORS. Set H2S_ALLOWED_ORIGINS to a comma-separated "
            "list of allowed origins (e.g. 'https://app.example.com')."
        )
        _cors_origins = []  # No origin allowed; effectively locked down.
    else:
        # Local development: keep permissive `*` for ease of testing.
        _cors_origins = "*"
    CORS(app, resources={r"/*": {"origins": _cors_origins}})

    # Configure Flask for production
    app.config['JSON_SORT_KEYS'] = False
    app.config['JSONIFY_PRETTYPRINT_REGULAR'] = True
    # Mirror the CORS config into app.config so the /api/hmac/key origin
    # guard can use the same allowlist. "*" is preserved as a literal so the
    # guard can recognise permissive-dev mode.
    if _cors_origins == "*":
        app.config["H2S_ALLOWED_ORIGINS"] = {"*"}
    elif isinstance(_cors_origins, (list, tuple, set)):
        app.config["H2S_ALLOWED_ORIGINS"] = set(_cors_origins)
    else:
        app.config["H2S_ALLOWED_ORIGINS"] = set()

    # Explicitly set Flask's debug mode based on environment
    app.debug = not IS_CLOUD_RUN

    # Install HMAC request-signing middleware on protected API routes.
    # Public endpoints (/health, /api/status) are exempt.
    install_hmac_middleware(app)

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
            "timestamp": datetime.now().isoformat(),
            "license": {
                "license_id": _license_info.license_id,
                "customer": _license_info.customer,
                "expires_at": _license_info.expires_at,
                "days_remaining": _license_info.days_remaining,
                "is_container": _license_info.is_container,
            },
        }), 200

    @app.route("/api/status", methods=['GET', 'OPTIONS'])
    def get_backend_status():
        """Returns a simple status to indicate if the backend is alive."""
        if request.method == 'OPTIONS':
            return '', 200
        return jsonify({"status": "alive", "timestamp": datetime.now().isoformat()}), 200

    @app.route("/api/hmac/key", methods=['GET', 'OPTIONS'])
    def get_hmac_key():
        """Hand the current HMAC signing key to the frontend.

        Endpoint is exempt from HMAC verification (it must be reachable before
        the frontend has a key to sign with). To mitigate browser-based key
        harvesting, this endpoint enforces an Origin/Referer check against the
        configured CORS allowlist. Requests with no Origin header and no
        Referer header are rejected in non-dev environments (browsers always
        send one or the other for cross-origin requests; direct curl/server
        calls must use a configured origin).
        """
        if request.method == 'OPTIONS':
            return '', 200

        # Origin / Referer guard. H2S_ALLOWED_ORIGINS is the source of truth;
        # the same set is used by flask-cors above.
        allowed_origins = app.config.get("H2S_ALLOWED_ORIGINS", set())
        request_origin = request.headers.get("Origin")
        referer = request.headers.get("Referer", "")

        # Permissive-dev mode: {"*"} means any origin is allowed (local dev only).
        if allowed_origins == {"*"} and not IS_CLOUD_RUN:
            pass  # fall through to key return below
        else:
            # Same-origin: Origin header is present and matches the request host.
            request_host = request.host_url.rstrip("/")
            same_origin = bool(request_origin) and (
                request_origin.rstrip("/") == request_host
            )

            # Cross-origin but on the allowlist
            on_allowlist = bool(request_origin) and (request_origin in allowed_origins)

            if not (same_origin or on_allowlist):
                if referer:
                    from urllib.parse import urlparse
                    ref_origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
                    if ref_origin in allowed_origins or ref_origin.rstrip("/") == request_host:
                        pass  # allow via Referer
                    else:
                        return jsonify({
                            "error": "Origin not allowed for HMAC key endpoint"
                        }), 403
                elif not IS_CLOUD_RUN:
                    # Dev convenience: no Origin/Referer (e.g. local curl, tests).
                    pass
                else:
                    return jsonify({
                        "error": "Origin not allowed for HMAC key endpoint"
                    }), 403

        # Pull the same key the middleware uses
        from hmac_auth import _get_signing_key
        import base64 as _b64
        key_bytes = _get_signing_key()
        return jsonify({
            "key": _b64.b64encode(key_bytes).decode("ascii"),
            "rotates_at": (datetime.now().timestamp() + 300) * 1000,  # ms epoch
        }), 200

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

    The task body is wrapped in a top-level try/except/finally so that any
    unhandled exception (raised here, raised deep inside the converter, or
    escaping a finally block) is captured into the task record as FAILED
    with the exception message. Without this, a thread that dies with an
    uncaught exception would leave the task in IN_PROGRESS forever and the
    polling client would loop indefinitely.
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

    try:
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
                    "mapping_download_name": f"{cv_object_name}.xlsx"
                }
            })
            logger.info(f"Task {task_id}: Background conversion completed successfully.")
        else:
            raise Exception(conversion_result.get("error", "Conversion failed"))

        # Clean up session data (xml_content is no longer needed)
        if task_id in conversion_sessions:
            del conversion_sessions[task_id]
        delete_node_dict_pickle(task_id)

    except Exception as exc:
        # Capture the failure into the task record so the polling client can
        # surface it instead of looping forever on IN_PROGRESS. Log the full
        # traceback once for the operator; expose only str(exc) to the client.
        logger.error(f"Task {task_id}: Background conversion crashed: {exc}")
        logger.error(traceback.format_exc())
        if task_id in conversion_tasks:
            conversion_tasks[task_id].update({
                "status": "FAILED",
                "progress": conversion_tasks[task_id].get("progress", 0),
                "message": f"Conversion failed: {exc}",
                "error": str(exc),
            })
        # Best-effort cleanup of per-task resources
        if task_id in conversion_sessions:
            del conversion_sessions[task_id]
        try:
            delete_node_dict_pickle(task_id)
        except Exception:
            pass

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
    Scans OUTPUT_DIR (or PREVIOUS_CONVERSIONS_DIR) for *.xlsx files.
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
                if fname.endswith(".xlsx") and not fname.startswith("_"):
                    mapping_file = fname
                    mapping_name = fname.replace(".xlsx", "")
                    break

            if mapping_file:
                mapping_path = os.path.join(task_dir, mapping_file)
                mtime = os.path.getmtime(mapping_path)
                # Read the manifest's task_id when one exists (so the LIST
                # response matches what DOWNLOAD / INSPECT look up via
                # local_storage.get_result_info). For legacy folders that
                # predate the manifest, fall back to the subfolder name —
                # their subfolder name IS the task_id by construction. Both
                # endpoints handle that case via direct path fallback.
                manifest_path = os.path.join(task_dir, "_manifest.json")
                real_task_id = task_id
                if os.path.isfile(manifest_path):
                    try:
                        with open(manifest_path, "r") as manifest_file:
                            manifest = json.load(manifest_file)
                        manifest_task_id = manifest.get("task_id")
                        if manifest_task_id:
                            real_task_id = manifest_task_id
                    except Exception as manifest_err:
                        logger.warning(
                            f"Failed to read manifest at {manifest_path}: {manifest_err}"
                        )
                conversions.append({
                    "task_id": real_task_id,
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

        # Start bulk conversion in BACKGROUND thread (don't block!).
        # The launcher wrapper guarantees that any uncaught exception escaping
        # convert_bulk is recorded against the bulk task as FAILED instead of
        # leaving it in PROCESSING forever (where the polling client would
        # loop indefinitely).
        def _run_bulk_safely():
            try:
                bulk_processor.convert_bulk(files, bulk_task_id)
            except Exception as exc:
                logger.error(f"Bulk task {bulk_task_id}: crashed: {exc}")
                logger.error(traceback.format_exc())
                if bulk_task_id in bulk_tasks:
                    bulk_tasks[bulk_task_id]["status"] = "FAILED"
                    bulk_tasks[bulk_task_id]["error"] = str(exc)
                    bulk_tasks[bulk_task_id]["message"] = (
                        f"Bulk conversion crashed before completion: {exc}"
                    )

        threading.Thread(target=_run_bulk_safely, daemon=True).start()

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

        # Run the inspect-only extraction. The same helper powers the
        # /api/nested/previous_conversions/<task_id>/inspect endpoint so the
        # parsed shape is guaranteed identical across both paths.
        inspect = _inspect_xlsx_workbook(xls)
        sql_info_records = inspect["sql_info"]
        mapping_records = inspect["mapping_info"]
        sql_info_df = pd.DataFrame(sql_info_records)

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
            # The selected_source filter is meant for root mode: when the user
            # picks one source as "the primary" source for the new root CV and
            # we only want that source's mapping info as the artifact's own
            # dependencies. In nested mode, selected_source is set to the
            # PARENT's source_ref (e.g. CV_INTERMEDIATE_SALES), which doesn't
            # match the nested CV's own sources (e.g. CV_BASE_SALES) — so the
            # filter would silently strip every dependency off the uploaded
            # nested artifact, leaving its linkage dropdown empty and breaking
            # 3+ level deep nesting. Skip the filter when parent_artifact_id is
            # set so the nested CV's full dependency set is preserved.
            if not parent_artifact_id:
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
                    "object_kind": infer_object_kind(src_table),
                    "referenced_by_node": str(row.get('Node name', row.get('node_name', 'unknown'))).strip(),
                    "required_columns": mapping_fields.get(src_table, []) if mapping_fields_raw and mapping_fields_raw != "nan" else []
                })

        logger.info(f"[XLSX] final dependencies count: {len(dependencies)}, deps: {dependencies}")

        # Build output_schema from the LAST chunk's SELECT aliases — those are
        # the actual columns the CV produces. The mapping_info sheet only lists
        # source-side columns (what the CV reads), not output columns, so
        # pulling from it would put the wrong column list on the artifact.
        output_schema = []
        for i, col_name in enumerate(inspect.get("output_columns") or []):
            col_name = str(col_name).strip()
            if not col_name or col_name == "nan":
                continue
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
            # All inspect-only extraction is delegated to _inspect_xlsx_workbook
            # so the same parsing powers both this endpoint and the history
            # inspect endpoint. The returned dict has the same shape the
            # frontend already expects: sql_info, mapping_info, source_tables,
            # output_columns, last_chunk_sql, last_chunk_sources.
            return jsonify(inspect), 200

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


def _inspect_xlsx_workbook(xls) -> dict:
    """Run the inspect-only extraction over a decrypted XLSX workbook.

    Returns a dict with keys: sql_info, mapping_info, source_tables,
    output_columns, last_chunk_sql, last_chunk_sources. Used by both the
    upload endpoint (nested_add_cv_from_xlsx) and the history inspect
    endpoint (GET /api/nested/previous_conversions/<task_id>/inspect) so
    the parsing logic is identical in both paths.
    """
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
    # Normalize the mapping_info columns to the canonical sourceTable /
    # sourceField / targetTable / targetField shape. The upload handler
    # reads rows by these normalized keys, so without this rename every
    # row looks empty and gets dropped by the
    # `if src_table and src_col` filter — silently losing the user's
    # mapping data.
    mapping_info_df = mapping_info_df.rename(columns={
        "Original Table": "sourceTable",
        "Original Column": "sourceField",
        "New Table": "targetTable",
        "New Column": "targetField",
    })
    sql_info_records = sql_info_df.to_dict(orient='records')

    # source_tables from mapping_info (most reliable — every workbook has it)
    # NOTE: the rename above converted column names to sourceTable /
    # sourceField / targetTable. Reading by the OLD names (e.g. "Original Table")
    # here would always return "" and silently produce an empty source_tables
    # list for every workbook — use the renamed keys.
    seen_source_tables: set[str] = set()
    source_tables_list: list[dict] = []
    for row in mapping_info_df.to_dict(orient='records'):
        src_table = str(row.get("sourceTable", "")).strip()
        src_field = str(row.get("sourceField", "")).strip()
        tgt_table = str(row.get("targetTable", "")).strip()
        if not src_table or src_table == "nan" or src_table.upper() in seen_source_tables:
            continue
        seen_source_tables.add(src_table.upper())
        source_tables_list.append({
            "source_table_name": src_table,
            "source_field": src_field,
            "target_table": tgt_table,
        })

    # output_columns from last chunk's SELECT aliases
    output_columns: list[str] = []
    last_chunk_sql = ""
    if sql_info_records:
        def _chunk_num(row):
            try:
                return int(row.get("Chunk Number", 0) or 0)
            except (ValueError, TypeError):
                return 0
        sorted_chunks = sorted(sql_info_records, key=_chunk_num)
        last_row = sorted_chunks[-1] if sorted_chunks else {}
        last_chunk_sql = str(last_row.get("Chunk SQL Primary Optimized Base", "")).strip()
        if last_chunk_sql and last_chunk_sql != "nan":
            select_match = re.search(
                r'\bSELECT\b(.*?)\bFROM\b',
                last_chunk_sql,
                re.IGNORECASE | re.DOTALL,
            )
            select_clause = select_match.group(1) if select_match else last_chunk_sql
            alias_matches = re.findall(
                r'\bAS\s+"?([A-Za-z_][A-Za-z0-9_]*)"?\s*(?=[\s,)])',
                select_clause,
                re.IGNORECASE,
            )
            seen_aliases: set[str] = set()
            for alias in alias_matches:
                if alias.lower() not in seen_aliases:
                    seen_aliases.add(alias.lower())
                    output_columns.append(alias)

    # last_chunk_sources from FROM/JOIN clauses
    last_chunk_sources: list[str] = []
    if last_chunk_sql and last_chunk_sql != "nan":
        from_matches = re.findall(
            r'\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)',
            last_chunk_sql,
            re.IGNORECASE,
        )
        seen_src: set[str] = set()
        for src in from_matches:
            if src.lower() not in seen_src:
                seen_src.add(src.lower())
                last_chunk_sources.append(src)

    return {
        "sql_info": sql_info_records,
        "mapping_info": mapping_info_df.to_dict(orient='records'),
        "source_tables": source_tables_list,
        "output_columns": output_columns,
        "last_chunk_sql": last_chunk_sql,
        "last_chunk_sources": last_chunk_sources,
    }


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
        "session_id": task.session_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "phase": task.phase,  # Structured phase — see orchestrator.Phase
        "result_url": task.result_url,
        "result_content": task.result_content,  # In-memory content for editor display
        "output_format": task.output_format,  # "sql" or "pyspark"
        "result_filename": task.result_filename,  # Suggested download filename
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
        default_ext = "pyspark"
        default_name = f"nested_cv_{task_id[:8]}.pyspark"
        mimetype = 'text/plain'
    else:
        default_ext = "sql"
        default_name = f"nested_cv_{task_id[:8]}.sql"
        mimetype = 'text/x-sql'

    # Prefer the orchestrator's filename — the root CV's mapping workbook
    # stem (e.g. `cv_sales_fact.sql`) — so a multi-CV download matches what
    # the user actually uploaded. Still sanitise it: a stray `..` in a
    # mapping workbook name would otherwise break Content-Disposition.
    if getattr(task, "result_filename", None):
        suggested = secure_filename(task.result_filename)
        if suggested:
            if not suggested.lower().endswith("." + default_ext):
                suggested += "." + default_ext
            default_name = suggested[:200]

    # Honor an explicit ?filename= override from the frontend rename UI.
    # Sanitize: strip path separators, control chars, and cap length so a
    # malicious or accidental value can't escape the downloads directory
    # or produce an absurdly long header.
    requested_name = (request.args.get("filename") or "").strip()
    if requested_name:
        # Werkzeug's secure_filename strips path separators and unsafe chars.
        safe = secure_filename(requested_name)
        if safe:
            # Preserve the expected extension if the user omitted it.
            if not safe.lower().endswith(("." + (task.output_format or "sql"))):
                safe += "." + (task.output_format or "sql")
            download_name = safe[:200]  # cap length
        else:
            download_name = default_name
    else:
        download_name = default_name

    return send_file(
        io.BytesIO(content.encode('utf-8')),
        as_attachment=True,
        download_name=download_name,
        mimetype=mimetype,
    )


# GET /api/nested/previous_conversions/<task_id>/inspect — Run the XLSX
# inspect pipeline against a previous conversion's stored mapping sheet.
# This is the server-side counterpart of the upload + inspectOnly flow,
# so the frontend doesn't have to download + re-upload the file just to
# populate the column-mapping UI.
@app.route('/api/nested/previous_conversions/<task_id>/inspect', methods=['GET', 'OPTIONS'])
def nested_inspect_previous_conversion(task_id):
    if request.method == 'OPTIONS':
        return '', 200
    try:
        # Resolve the mapping file. We try two lookup strategies in order:
        #   1. Manifest-aware: scan all subfolders for a _manifest.json whose
        #      task_id matches. This is the canonical path for any
        #      conversion that went through save_result() (which writes the
        #      manifest with the original UUID task_id). It works regardless
        #      of what the on-disk subfolder is named.
        #   2. Direct path: for legacy folders that predate the manifest, the
        #      subfolder name itself is the task_id, so join it onto the
        #      base directory and look for an .xlsx inside.
        # Falling back to (2) means legacy conversions remain selectable
        # from the history list — without this they would 404 even though
        # the LIST endpoint still shows them.
        mapping_path = None
        result_info = local_storage.get_result_info(task_id)
        if result_info:
            mapping_path = result_info.get("data_mapping_url")

        if not mapping_path or not os.path.isfile(mapping_path):
            base_dir = os.getenv("PREVIOUS_CONVERSIONS_DIR") or local_storage.OUTPUT_DIR
            legacy_task_dir = os.path.join(base_dir, task_id)
            if os.path.isdir(legacy_task_dir):
                for fname in os.listdir(legacy_task_dir):
                    if fname.endswith(".xlsx") and not fname.startswith("_"):
                        mapping_path = os.path.join(legacy_task_dir, fname)
                        break

        if not mapping_path or not os.path.isfile(mapping_path):
            return jsonify({"error": f"No previous conversion found for task_id: {task_id}"}), 404

        password = request.args.get("password", "mypassword123la")
        # Same decrypt helper used by the upload endpoint, but pointed at a
        # file on disk instead of request.files.
        with open(mapping_path, "rb") as fh:
            xls = decrypt_xlsx_file(fh, password)

        inspect = _inspect_xlsx_workbook(xls)
        # Carry file_name forward so the frontend can label the selection.
        file_name = os.path.basename(mapping_path).replace(".xlsx", "")
        return jsonify({
            "success": True,
            "file_name": file_name,
            **inspect,
        }), 200
    except Exception as e:
        logger.error(f"nested_inspect_previous_conversion error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# DELETE /api/nested/tasks/<task_id> — Cancel a running generation task
@app.route('/api/nested/tasks/<task_id>', methods=['DELETE', 'OPTIONS'])
def nested_cancel_task(task_id):
    """Mark a task as cancelled. The background worker checks this flag at
    each progress checkpoint and bails out cleanly. Tasks in terminal
    states (COMPLETED / FAILED / CANCELLED) cannot be cancelled — the
    endpoint returns 200 with a hint so the client can ignore it."""
    if request.method == 'OPTIONS':
        return '', 200
    store = get_session_store()
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    if task.status in ("COMPLETED", "FAILED", "CANCELLED"):
        return jsonify({
            "success": True,
            "cancelled": False,
            "status": task.status,
            "message": f"Task already in terminal state: {task.status}",
        }), 200
    store.request_cancel(task_id)
    return jsonify({
        "success": True,
        "cancelled": True,
        "task_id": task_id,
        "message": "Cancellation requested",
    }), 200


# GET /api/nested/tasks/<task_id>/cancel — alias for DELETE so curl users
# without -X DELETE can also cancel. Same handler.
@app.route('/api/nested/tasks/<task_id>/cancel', methods=['POST', 'OPTIONS'])
def nested_cancel_task_alias(task_id):
    return nested_cancel_task(task_id)

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
