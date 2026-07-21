<arg_value>"""
HANACV2SQL Enterprise Edition - Flask Application
Clean fork without Supabase, GCS, credits, or payments.
Uses local filesystem storage for outputs.
"""

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
import concurrent.futures
import atexit
import uuid
import base64
import re
from datetime import timedelta
from urllib.parse import urlparse

# Import our custom modules
from node_counter import count_xml_nodes
from sql_converter import convert_xml_to_sql
from node_cache import save_node_dict, load_node_dict, delete_node_dict_pickle, get_pickle_path
from file_processor import construct_node_dict, validate_node_dict, dig_mapping_generator
from bulk_processor import bulk_processor  # Import bulk processor
from werkzeug.utils import secure_filename
from mapping_sql_generator import generate_sql_from_mapping
from excel_encrypt import decrypt_xlsx_file
from api_client import api_call_flash, api_call
import local_storage

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), '.env.enterprise')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")

# Force Google Cloud libraries to use REST transport instead of gRPC
os.environ["GOOGLE_CLOUD_DISABLE_GRPC"] = "true"

# Configure logging
logging.basicConfig(level=logging.INFO)
logging.getLogger('flask').setLevel(logging.INFO)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Set sqlfluff logger level to CRITICAL to suppress all messages
logging.getLogger('sqlfluff').setLevel(logging.CRITICAL)
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
    app.debug = False

    @app.route('/', methods=['GET', 'OPTIONS'])
    def root():
        """Root endpoint with service information"""
        if request.method == 'OPTIONS':
            return '', 200

        return jsonify({
            "service": "HANA to SQL Converter API (Enterprise)",
            "version": "4.0.0",
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
            "service": "hana-cv-converter-enterprise",
            "version": "4.0.0",
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
        Returns whether a conversion (single or bulk) is currently running.
        Enterprise edition: always returns False (no concurrency control without DB).
        """
        if request.method == 'OPTIONS':
            return '', 200
        return jsonify({"isRunning": False}), 200

    @app.route("/container-shutdown", methods=["POST"])
    def container_shutdown():
        data = request.json
        instance_id = data.get("instance_id", "unknown")
        logger.info(f"Container {instance_id} is shutting down")
        return jsonify({"status": "ok"}), 200

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

            logger.info(f"Analyzing XML file: {file_name}")

            # Enterprise: no daily free conversion count - always pass 0
            daily_free_conversions_used = 0

            # Count nodes and validate
            result = count_xml_nodes(xml_content, daily_free_conversions_used=daily_free_conversions_used)

            session_id = f"{file_name}_{datetime.now().timestamp()}"

            if result["success"]:
                conversion_sessions[session_id] = {
                    "xml_content": result["validated_xml"],
                    "file_name": file_name,
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

    @app.route('/api/supabase-hook', methods=['POST'])
    def supabase_hook():
        """Stub - no Supabase in enterprise edition."""
        return jsonify({"success": True, "result": "No Supabase in enterprise edition"}), 200

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
    # Clean up old results on exit
    try:
        local_storage.cleanup_old_results(max_age_hours=24)
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

atexit.register(cleanup)

# Global variables for session storage
conversion_sessions = {}
mapping_sessions = {}
conversion_tasks = {} # Stores status and results of async conversions
bulk_tasks = {}


def _perform_conversion_task(task_id, xml_content, file_name, conversion_type, credit_cost, target=None):
    """
    Performs the long-running XML to SQL conversion in a background thread.
    Updates the global conversion_tasks dictionary with status and results.
    Enterprise edition: no concurrency control, no DB inserts, uses local storage.
    """
    global conversion_tasks
    conversion_tasks[task_id] = {
        "status": "IN_PROGRESS",
        "progress": 0,
        "message": "Starting conversion...",
        "result": None,
        "error": None,
        "timestamp": datetime.now().isoformat(),
        "file_name": file_name,
        "conversion_type": conversion_type,
        "credit_cost": credit_cost,
    }
    logger.info(f"Task {task_id}: Starting background conversion for {file_name}")

    try:
        # Retrieve XML content from conversion_sessions
        if task_id not in conversion_sessions:
            raise ValueError(f"Session ID {task_id} not found for background task.")

        session_data = conversion_sessions[task_id]
        xml_content = session_data["xml_content"]
        node_count = session_data["node_count"]

        # --- Self Keep-Alive Pinger ---
        def self_ping():
            port = os.environ.get("PORT", "8080")
            url = f"http://127.0.0.1:{port}/api/status"
            logger.info(f"Task {task_id}: Starting self-pinger to {url}")
            while task_id in conversion_tasks and conversion_tasks[task_id]["status"] == "IN_PROGRESS":
                try:
                    requests.get(url, timeout=2)
                except Exception as e:
                    logger.warning(f"Task {task_id}: Self-ping failed: {e}")
                time.sleep(10)

        threading.Thread(target=self_ping, daemon=True).start()
        # ------------------------------

        conversion_tasks[task_id].update({"message": "Converting XML to SQL...", "progress": 25})

        # Enforce a strict timeout on the conversion process to ensure cleanup runs
        CONVERSION_TIMEOUT_SECONDS = 3600  # 60 minutes

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
            conversion_tasks[task_id].update({"message": "Saving files...", "progress": 75})
            zip_file_content = conversion_result["zip_file_content"]
            data_mapping_content = conversion_result["Data_mapping"]
            base_filename = conversion_result["view_name"]
            cv_object_name = base_filename.replace(" ", "_").replace("-", "_")

            # Save to local filesystem instead of GCS
            storage_result = local_storage.save_result(
                task_id,
                zip_file_content,
                data_mapping_content,
                metadata={"cv_object_name": cv_object_name}
            )

            sql_url = storage_result["sql_url"]
            data_mapping_url = storage_result["data_mapping_url"]
            sql_download_name = storage_result["sql_download_name"]
            mapping_download_name = storage_result["mapping_download_name"]

            logger.info(f"Task {task_id}: Saved SQL to {sql_url}")
            logger.info(f"Task {task_id}: Saved mapping to {data_mapping_url}")

            conversion_tasks[task_id].update({
                "status": "COMPLETED",
                "progress": 100,
                "message": "Conversion complete.",
                "result": {
                    "sql_url": sql_url,
                    "data_mapping_url": data_mapping_url,
                    "sql_download_name": sql_download_name,
                    "mapping_download_name": mapping_download_name
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


@app.route('/api/start-conversion', methods=['POST', 'OPTIONS'])
def start_conversion():
    """
    Initiates a long-running XML to SQL conversion task in the background.
    Returns a task_id immediately.
    Enterprise edition: no admin notification, no concurrency control.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json()
        if not data or 'xmlContent' not in data:
            return jsonify({"error": "No XML content provided", "success": False}), 400

        xml_content = data['xmlContent']
        file_name = data.get('fileName', 'input.xml')
        conversion_type = data.get('conversionType', 'Unknown')
        credit_cost = data.get('creditCost', 0)
        target = data.get('target')

        logger.info(f"Received request to start conversion for {file_name}")

        # Check if node_count is already provided (from frontend analysis)
        node_count = data.get('nodeCount')

        if node_count is not None:
            logger.info(f"Using provided nodeCount: {node_count}")
            analysis_result = {
                "success": True,
                "node_count": int(node_count),
                "validated_xml": xml_content,
                "dig_mapping_dot_string": ""
            }
        else:
            logger.info(f"nodeCount not provided, performing initial analysis for {file_name}...")
            analysis_result = count_xml_nodes(xml_content, daily_free_conversions_used=0)

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
            "file_name": file_name,
            "conversion_type": conversion_type,
            "credit_cost": credit_cost,
        }

        # Store initial session data for the background task to pick up
        conversion_sessions[task_id] = {
            "xml_content": xml_content,
            "file_name": file_name,
            "node_count": node_count,
            "analysis": analysis_result.get("analysis", {}),
            "timestamp": datetime.now().isoformat()
        }

        # Run the conversion in a background thread so the request returns immediately
        threading.Thread(
            target=_perform_conversion_task,
            args=(task_id, xml_content, file_name, conversion_type, credit_cost),
            kwargs={"target": target}
        ).start()

        logger.info(f"Conversion task {task_id} started in background thread.")

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
    Enterprise edition: checks in-memory tasks and local filesystem (no Supabase fallback).
    """
    if request.method == 'OPTIONS':
        return '', 200

    task_info = conversion_tasks.get(task_id)
    if task_info:
        return jsonify(task_info.copy()), 200

    # Enterprise fallback: check local filesystem for completed conversions
    result_info = local_storage.get_result_info(task_id)
    if result_info:
        logger.info(f"Conversion status for {task_id} found in local storage (fallback after restart).")
        return jsonify({
            "status": "COMPLETED",
            "progress": 100,
            "message": "Conversion complete.",
            "result": {
                "sql_url": result_info.get("sql_url"),
                "data_mapping_url": result_info.get("data_mapping_url"),
                "sql_download_name": result_info.get("sql_download_name"),
                "mapping_download_name": result_info.get("mapping_download_name")
            },
            "error": None
        }), 200

    return jsonify({"error": "Task not found", "status": "UNKNOWN"}), 404


@app.route('/api/download/<session_id>', methods=['GET', 'OPTIONS'])
def download_converted_file(session_id):
    """
    Download a converted file using its session ID (now also used as task_id).
    Enterprise edition: serves files from local filesystem (no GCS, no Supabase).
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        logger.info(f"Download request received for session ID/Task ID: {session_id}")
        file_type = request.args.get('type', 'sql').lower()
        logger.debug(f"Requested file type: {file_type}")

        download_path = None
        download_name = None
        mimetype = 'application/octet-stream'

        # --- Check in-memory conversion_tasks first (for recent conversions) ---
        task_info = conversion_tasks.get(session_id)
        if task_info and task_info.get("status") == "COMPLETED" and task_info.get("result"):
            logger.debug(f"Found task_info in memory for {session_id}.")

            if file_type == 'mapping':
                download_path = task_info["result"].get("data_mapping_url")
                download_name = task_info["result"].get("mapping_download_name")
                mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif file_type == 'sql':
                download_path = task_info["result"].get("sql_url")
                download_name = task_info["result"].get("sql_download_name")
                mimetype = 'application/zip'
            else:
                logger.error(f"Invalid file type '{file_type}' for session ID: {session_id} (from in-memory cache).")
                return jsonify({"error": "Invalid file type requested"}), 400

        # --- Fallback to local filesystem if not found in active tasks ---
        if not download_path:
            logger.debug(f"Falling back to local storage for session ID: {session_id}")
            result_info = local_storage.get_result_info(session_id)

            if not result_info:
                logger.warning(f"No conversion record found for session ID: {session_id} in active tasks or local storage.")
                return jsonify({"error": "File not found or unauthorized access"}), 404

            if file_type == 'mapping':
                download_path = result_info.get("data_mapping_url")
                download_name = result_info.get("mapping_download_name")
                mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif file_type == 'sql':
                download_path = result_info.get("sql_url")
                download_name = result_info.get("sql_download_name")
                mimetype = 'application/zip'
            else:
                logger.error(f"Invalid file type '{file_type}' for session ID: {session_id}")
                return jsonify({"error": "Invalid file type requested"}), 400

        if not download_path or not os.path.exists(download_path):
            logger.error(f"Download file not found on disk for session ID: {session_id} and type '{file_type}'.")
            return jsonify({"error": "Requested file is not available"}), 404

        logger.info(f"Streaming file from local storage: {download_path}")

        return send_file(
            download_path,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype
        )

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

        result = count_xml_nodes(xml_content, daily_free_conversions_used=0)

        if "validated_xml" in result:
            del result["validated_xml"]

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

        database_name = request.form.get('selectedPlatform', '')
        if not database_name:
            database_name = 'bigquery'

        if 'xlsxFile' not in request.files:
            return jsonify({"error": "No XLSX file provided", "success": False}), 400

        xlsx_file = request.files['xlsxFile']
        if xlsx_file.filename == '':
            return jsonify({"error": "No selected file", "success": False}), 400

        if xlsx_file:
            xls = decrypt_xlsx_file(xlsx_file, "mypassword123la")

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

            renamed_mapping_df = mapping_info_df.rename(columns={
                'Original Table': 'sourceTable',
                'Original Column': 'sourceField',
                'New Table': 'targetTable',
                'New Column': 'targetField'
            })
            mapping_file_content = renamed_mapping_df.to_dict(orient='records')
            logger.info(f"Length of mapping_file_content: {len(mapping_file_content)}")

            generated_schema = {
                "sessionId": str(uuid.uuid4()),
                "databaseName": database_name,
                "fileName": xlsx_file.filename,
                "sqlInfo": sql_info_df.to_dict(orient='records'),
                "mappingColumns": mapping_info_df.columns.tolist(),
                "suggestedMappings": {col: f"suggested_db_field_for_{col}" for col in mapping_info_df.columns},
                "mappingDataPreview": mapping_info_df.head(5).to_dict(orient='records'),
                "mappingFileContent": mapping_file_content
            }

            mapping_sessions[generated_schema["sessionId"]] = generated_schema

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
            data = request.get_json(force=True)
            logger.info(f"Request data keys: {list(data.keys()) if data else 'None'}")
        except Exception as e:
            logger.error(f"Error parsing JSON from request: {str(e)}")
            logger.error(traceback.format_exc())
            return jsonify({"error": f"Invalid JSON format: {str(e)}", "success": False}), 400

        if not data:
            logger.error("No data provided in apply_changes_and_generate_output request after JSON parsing.")
            return jsonify({"error": "No data provided", "success": False}), 400

        session_id = data.get('sessionId')

        raw_updated_mappings = data.get('updatedMappings')
        updated_mapping_data = list(raw_updated_mappings) if raw_updated_mappings is not None else None

        if not session_id or not updated_mapping_data:
            logger.error(f"Missing sessionId ({session_id}) or updatedMappings (empty: {not updated_mapping_data}) in apply_changes_and_generate_output.")
            return jsonify({"error": "Missing sessionId or updatedMappingData", "success": False}), 400

        logger.info(f"Current mapping_sessions keys: {list(mapping_sessions.keys())}")
        if session_id not in mapping_sessions:
            logger.error(f"Session ID '{session_id}' not found in mapping_sessions.")
            return jsonify({"error": f"Session ID not found: {session_id}", "success": False}), 404

        initial_schema = mapping_sessions[session_id]
        logger.info(f"Applying changes for session: {session_id}. Initial schema found.")

        output_format = data.get('outputFormat', 'sql')
        logger.info(f"Output format requested: {output_format}")

        cte_sql, temp_table_sql = await generate_sql_from_mapping(
            initial_schema['sqlInfo'],
            updated_mapping_data,
            initial_schema['databaseName'],
            output_format=output_format
        )

        del mapping_sessions[session_id]
        logger.info(f"Mapping session {session_id} cleaned up.")

        base_filename_cleaned = initial_schema['fileName'].replace('.xlsx', '').replace('.xls', '')

        if output_format == "pyspark":
            output_filename = f"{base_filename_cleaned}_{initial_schema['databaseName']}_mapped.ipynb"
            return jsonify({
                "success": True,
                "pysparkNotebookContent": cte_sql,
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
        if 'zipFile' in request.files:
            zip_file = request.files['zipFile']
            zip_content = zip_file.read()
        else:
            data = request.get_json()
            if not data or 'zipContent' not in data:
                return jsonify({"error": "No ZIP content provided", "success": False}), 400
            zip_content = base64.b64decode(data['zipContent'])

        logger.info("Bulk analyze request received")

        # Enterprise: pass anonymous email (no user tracking)
        result = bulk_processor.analyze_zip(zip_content, 'anonymous@example.com')

        if result["success"]:
            bulk_files = result["files"]
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
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        data = request.get_json()
        if not data or 'files' not in data:
            return jsonify({"error": "No files provided", "success": False}), 400

        files = data['files']
        conversion_type = data.get('conversionType', 'Mixed')

        logger.info(f"Bulk conversion request: {len(files)} files")

        bulk_task_id = str(uuid.uuid4())

        bulk_tasks[bulk_task_id] = {
            "status": "PROCESSING",
            "total_files": len(files),
            "timestamp": datetime.now().isoformat()
        }

        # Start bulk conversion in BACKGROUND thread (don't block!)
        threading.Thread(
            target=lambda: bulk_processor.convert_bulk(files, 'anonymous@example.com', conversion_type, bulk_task_id),
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
        status = bulk_processor.get_bulk_status(bulk_task_id)
        logger.info(f"Bulk status for {bulk_task_id}: {status}")

        if "error" in status:
            return jsonify({"error": status["error"], "status": "UNKNOWN"}), 404

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
    Enterprise edition: reads from local filesystem.
    """
    if request.method == 'OPTIONS':
        return '', 200

    try:
        status = bulk_processor.get_bulk_status(bulk_task_id)

        if "error" in status or status["status"] != "COMPLETED":
            return jsonify({"error": "Bulk conversion not completed or not found"}), 404

        results = status.get("results", [])

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for result in results:
                sql_path = result.get("sql_url")
                if sql_path and os.path.exists(sql_path):
                    file_name = result.get("download_name", "converted.sql")
                    with open(sql_path, 'rb') as f:
                        zf.writestr(file_name, f.read())

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


if __name__ == "__main__":
    import sys

    # Fix for OSError on Windows when using async routes
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host='0.0.0.0', port=port)