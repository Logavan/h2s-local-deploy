"""
Local Filesystem Storage Module
Replaces GCS and Supabase storage with local filesystem storage for enterprise deployment.
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Output directory - can be overridden by environment variable
OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "outputs"))
OUTPUT_DIR = os.path.abspath(OUTPUT_DIR)


def _ensure_output_dir():
    """Ensure the output directory exists."""
    try:
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create output directory {OUTPUT_DIR}: {e}")
        raise


def save_result(task_id, zip_content, mapping_content, metadata=None):
    """
    Save conversion result files to the local filesystem.

    Args:
        task_id: Unique task/conversion ID
        zip_content: Bytes content of the ZIP file (SQL output)
        mapping_content: Bytes content of the mapping sheet (XLSX)
        metadata: Optional dict with extra info (e.g., cv_object_name, user_email)

    Returns:
        dict with keys:
            - sql_url: local file path for the SQL zip
            - data_mapping_url: local file path for the mapping sheet
            - sql_download_name: suggested download filename for SQL
            - mapping_download_name: suggested download filename for mapping
    """
    _ensure_output_dir()

    metadata = metadata or {}
    cv_object_name = metadata.get("cv_object_name", task_id)

    # Sanitize the object name for filesystem
    safe_name = cv_object_name.replace(" ", "_").replace("-", "_")

    # Use cv_object_name_<timestamp> as subfolder instead of task_id
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    subfolder_name = f"{safe_name}_{timestamp}"
    task_dir = os.path.join(OUTPUT_DIR, subfolder_name)
    try:
        Path(task_dir).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create task directory {task_dir}: {e}")
        raise

    sql_filename = f"{safe_name}.zip"
    mapping_filename = f"{safe_name}_mapping_sheet.xlsx"

    sql_path = os.path.join(task_dir, sql_filename)
    mapping_path = os.path.join(task_dir, mapping_filename)

    # Write the SQL zip file
    try:
        if zip_content is not None:
            with open(sql_path, "wb") as f:
                f.write(zip_content)
            logger.info(f"Saved SQL output to {sql_path}")
    except Exception as e:
        logger.error(f"Failed to write SQL file {sql_path}: {e}")
        raise

    # Write the mapping sheet
    try:
        if mapping_content is not None:
            with open(mapping_path, "wb") as f:
                f.write(mapping_content)
            logger.info(f"Saved mapping sheet to {mapping_path}")
    except Exception as e:
        logger.error(f"Failed to write mapping file {mapping_path}: {e}")
        raise

    # Write manifest file so lookups by task_id still work
    manifest = {
        "task_id": task_id,
        "cv_object_name": cv_object_name,
        "subfolder": subfolder_name,
        "saved_at": datetime.now().isoformat(),
    }
    manifest_path = os.path.join(task_dir, "_manifest.json")
    try:
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)
    except Exception as e:
        logger.warning(f"Failed to write manifest {manifest_path}: {e}")

    return {
        "sql_url": sql_path,
        "data_mapping_url": mapping_path,
        "subfolder": subfolder_name,
        "sql_download_name": f"{safe_name}_converted.zip",
        "mapping_download_name": f"{safe_name}_mapping_sheet.xlsx",
    }


def get_result_url(task_id, file_type="sql"):
    """
    Get the local file path for a saved result.
    Scans subdirectories for a _manifest.json to find the right folder.

    Args:
        task_id: Unique task/conversion ID
        file_type: 'sql' or 'mapping'

    Returns:
        str: local file path, or None if not found
    """
    if not os.path.isdir(OUTPUT_DIR):
        return None

    for subfolder in os.listdir(OUTPUT_DIR):
        manifest_path = os.path.join(OUTPUT_DIR, subfolder, "_manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
        except Exception:
            continue
        if manifest.get("task_id") != task_id:
            continue

        task_dir = os.path.join(OUTPUT_DIR, subfolder)
        if file_type == "mapping":
            for fname in os.listdir(task_dir):
                if fname.endswith("_mapping_sheet.xlsx"):
                    return os.path.join(task_dir, fname)
        else:
            for fname in os.listdir(task_dir):
                if fname.endswith(".zip"):
                    return os.path.join(task_dir, fname)
        return None

    return None


def get_result_info(task_id):
    """
    Get full result info (paths and download names) for a task.
    Scans subdirectories for a _manifest.json to find the right folder.

    Args:
        task_id: Unique task/conversion ID

    Returns:
        dict with sql_url, data_mapping_url, sql_download_name, mapping_download_name
        or None if not found
    """
    if not os.path.isdir(OUTPUT_DIR):
        return None

    # Scan all subdirectories looking for a manifest with matching task_id
    for subfolder in os.listdir(OUTPUT_DIR):
        manifest_path = os.path.join(OUTPUT_DIR, subfolder, "_manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
        except Exception:
            continue
        if manifest.get("task_id") != task_id:
            continue

        task_dir = os.path.join(OUTPUT_DIR, subfolder)
        sql_url = None
        data_mapping_url = None
        sql_download_name = None
        mapping_download_name = None

        for fname in os.listdir(task_dir):
            if fname == "_manifest.json":
                continue
            if fname.endswith(".zip"):
                sql_url = os.path.join(task_dir, fname)
                sql_download_name = f"{fname.replace('.zip', '')}_converted.zip"
            elif fname.endswith("_mapping_sheet.xlsx"):
                data_mapping_url = os.path.join(task_dir, fname)
                mapping_download_name = fname

        if not sql_url and not data_mapping_url:
            return None

        return {
            "sql_url": sql_url,
            "data_mapping_url": data_mapping_url,
            "sql_download_name": sql_download_name or f"{task_id}_converted.zip",
            "mapping_download_name": mapping_download_name or f"{task_id}_mapping_sheet.xlsx",
        }

    return None