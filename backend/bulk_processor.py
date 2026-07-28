"""
Bulk Conversion Processor using ThreadPoolExecutor
Reuses existing file_processor functions for individual file processing
"""

import io
import zipfile
import logging
import uuid
from datetime import datetime
from local_storage import save_result
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Import existing functions to reuse
from node_counter import count_xml_nodes
from sql_converter import convert_xml_to_sql
from werkzeug.utils import secure_filename


def analyze_single_file(file_content: str, file_name: str) -> Dict[str, Any]:
    """
    Analyze a single XML/TXT file - reuses existing count_xml_nodes function
    """
    try:
        result = count_xml_nodes(file_content)
        return {
            "success": True,
            "file_name": file_name,
            "node_count": result.get("node_count", 0),
            "complexity": result.get("complexity", "unknown"),
            "validated_xml": result.get("validated_xml", ""),
            "error": None
        }
    except Exception as e:
        logger.error(f"Error analyzing file {file_name}: {str(e)}")
        return {
            "success": False,
            "file_name": file_name,
            "node_count": 0,
            "error": str(e)
        }


def convert_single_file(file_content: str, file_name: str, task_id: str, node_count: int, complexity: str = "unknown", analysis_id: str = "") -> Dict[str, Any]:
    """
    Convert a single XML file to SQL - reuses existing convert_xml_to_sql function
    Returns results in-memory instead of writing to database
    """
    import asyncio
    import time

    total_start = time.time()
    try:
        logger.info(f"[BULK] convert_single_file: START - {file_name} (task: {task_id})")

        # Run async conversion synchronously
        async def run_conversion():
            return await asyncio.wait_for(
                convert_xml_to_sql(task_id, file_content, file_name),
                timeout=3600  # 60 minute timeout
            )

        conv_start = time.time()
        conversion_result = asyncio.run(run_conversion())
        logger.info(f"[BULK] convert_single_file: convert_xml_to_sql DONE in {time.time()-conv_start:.2f}s")

        if not conversion_result.get("success"):
            raise Exception(conversion_result.get("error", "Conversion failed"))

        # No GCS - store file bytes directly
        zip_file_content = conversion_result["zip_file_content"]
        data_mapping_content = conversion_result["Data_mapping"]
        base_filename = conversion_result["view_name"]
        cv_object_name = base_filename.replace(" ", "_").replace("-", "_")

        # Save to disk via local_storage
        sql_url = None
        data_mapping_url = None
        try:
            saved = save_result(
                task_id,
                zip_file_content,
                data_mapping_content,
                metadata={"cv_object_name": cv_object_name}
            )
            sql_url = saved.get("sql_url")
            data_mapping_url = saved.get("data_mapping_url")
            logger.info(f"[BULK] convert_single_file: Saved to disk at {sql_url}")
        except Exception as e:
            logger.warning(f"[BULK] convert_single_file: Failed to save to disk: {e}")

        logger.info(f"[BULK] convert_single_file: COMPLETE - {file_name} in {time.time()-total_start:.2f}s")
        return {
            "success": True,
            "file_name": file_name,
            "task_id": task_id,
            "analysis_id": analysis_id,
            "no_nodes": node_count,
            "complexity": complexity,
            "sql_content": zip_file_content,
            "mapping_content": data_mapping_content,
            "sql_url": sql_url,
            "data_mapping_url": data_mapping_url,
            "download_name": f"{cv_object_name}_converted.zip",
            "mapping_download_name": f"{cv_object_name}.xlsx"
        }

    except Exception as e:
        logger.error(f"[BULK] convert_single_file: ERROR - {file_name}: {str(e)} in {time.time()-total_start:.2f}s")
        return {
            "success": False,
            "file_name": file_name,
            "task_id": task_id,
            "error": str(e)
        }


class BulkProcessor:
    """
    Handles bulk file processing with ThreadPoolExecutor
    """

    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.bulk_tasks = {}  # Store bulk task status

    def extract_zip_files(self, zip_file_content: bytes) -> List[Dict[str, Any]]:
        """
        Extract files from ZIP and return list of file contents
        Supports XML and TXT files with various encoding handling
        """
        files = []
        all_files_found = []  # For debugging
        skipped_reasons = []  # For debugging

        try:
            with zipfile.ZipFile(io.BytesIO(zip_file_content)) as zf:
                for file_name in zf.namelist():
                    all_files_found.append(file_name)

                    # Skip directories
                    if file_name.endswith('/') or file_name.startswith('__'):
                        skipped_reasons.append(f"{file_name}: is directory or macos metadata")
                        continue

                    # Get just the filename without path
                    base_name = file_name.split('/')[-1] if '/' in file_name else file_name
                    base_name = base_name.split('\\')[-1] if '\\' in base_name else base_name

                    # Preserve subfolder path for disambiguation (e.g., "models_sales" from "models/sales.xml")
                    path_prefix = file_name.replace('\\', '/').rsplit('/', 1)[0] if '/' in file_name else ''
                    if path_prefix:
                        disambiguated_name = f"{path_prefix.replace('/', '_')}_{base_name}"
                    else:
                        disambiguated_name = base_name

                    # Only allow .xml and .txt files (case-insensitive)
                    name_lower = base_name.lower()
                    if not (name_lower.endswith('.xml') or name_lower.endswith('.txt')):
                        skipped_reasons.append(f"{file_name}: not .xml or .txt")
                        continue

                    # Try to extract with different encodings
                    content = None
                    raw_content = None
                    encodings_to_try = ['utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'latin-1', 'cp1252', 'iso-8859-1', 'ascii', 'cp437']

                    for encoding in encodings_to_try:
                        try:
                            with zf.open(file_name) as f:
                                raw_content = f.read()
                                # First try strict decoding
                                try:
                                    content = raw_content.decode(encoding)
                                except UnicodeDecodeError:
                                    # If strict fails but encoding matches family, try with errors='replace'
                                    content = raw_content.decode(encoding, errors='replace')
                                break
                        except (UnicodeDecodeError, UnicodeError, LookupError):
                            continue

                    if content is None and raw_content is not None:
                        # Last resort: try binary detection and decode appropriately
                        try:
                            # Try to detect if it's actually binary content
                            if self._is_likely_binary(raw_content):
                                skipped_reasons.append(f"{file_name}: appears to be binary content")
                                continue
                            content = raw_content.decode('utf-8', errors='replace')
                        except Exception:
                            content = str(raw_content, errors='replace')

                    # Validate content is actually text (not binary garbage)
                    if raw_content is not None and self._is_likely_binary(raw_content):
                        skipped_reasons.append(f"{file_name}: detected as binary content")
                        continue

                    # Clean up content - remove null bytes and other control characters
                    if content:
                        # Remove null bytes which can cause XML parsing issues
                        content = content.replace('\x00', '')
                        # Normalize line endings
                        content = content.replace('\r\n', '\n').replace('\r', '\n')

                    # Only add if content is not empty and has reasonable content
                    if content and len(content.strip()) > 0:
                        # Additional check: ensure content looks like XML/text, not garbage
                        if self._is_valid_text_content(content):
                            files.append({
                                "name": disambiguated_name,
                                "content": content,
                                "size": len(content),
                                "original_path": file_name
                            })
                        else:
                            skipped_reasons.append(f"{file_name}: content doesn't appear to be valid XML/text")
                    else:
                        skipped_reasons.append(f"{file_name}: empty content")

            logger.info(f"ZIP extraction summary: Total files in ZIP: {len(all_files_found)}, Extracted XML/TXT: {len(files)}")
            if all_files_found and not files:
                logger.warning(f"No XML/TXT files extracted. All files found: {all_files_found}")
                logger.warning(f"Skipped reasons: {skipped_reasons}")
            return files

        except zipfile.BadZipFile:
            logger.error("Invalid ZIP file: File is corrupted or not a valid ZIP archive")
            return []
        except Exception as e:
            logger.error(f"Error extracting ZIP: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def _is_likely_binary(self, content: bytes, threshold: float = 0.30) -> bool:
        """
        Check if content is likely binary based on null byte presence and control characters.
        """
        if not content:
            return True

        # Check for null bytes (strong indicator of binary)
        null_count = content.count(b'\x00')
        if null_count > 0:
            return True

        # Check ratio of non-printable characters
        # Text files typically have < 10% non-printable chars (excluding common whitespace)
        size = len(content)
        non_printable = sum(1 for byte in content if byte < 32 and byte not in (9, 10, 13))  # Allow tab, newline, CR

        if size > 0 and (non_printable / size) > threshold:
            return True

        return False

    def _is_valid_text_content(self, content: str, sample_size: int = 500) -> bool:
        """
        Check if content appears to be valid text/XML content, not random garbage.
        """
        if not content or len(content.strip()) == 0:
            return False

        # Check if content starts with common text/XML markers
        content_start = content.strip()[:100].lower()

        # Common valid start patterns
        valid_starts = [
            '<?xml', '<view:', '<columnview', '<?xml version',
            '<?xml encoding', '<?doctype', '<!doctype',
            '<repository', '<designtime', '<hana', '<calculation'
        ]

        for pattern in valid_starts:
            if content_start.startswith(pattern):
                return True

        # Check for reasonable character distribution
        # Valid text/XML should have a good mix of letters and some special chars
        letter_count = sum(1 for c in content[:sample_size] if c.isalpha())
        special_count = sum(1 for c in content[:sample_size] if c in '<>=";\':/')

        total = min(sample_size, len(content))
        if total == 0:
            return False

        # If too few letters or special chars, might be garbage
        if letter_count / total < 0.1:
            return False

        # Should have at least some XML-like characters
        if special_count < 2:
            return False

        # Check for balanced angle brackets (basic XML structure check)
        open_brackets = content.count('<')
        close_brackets = content.count('>')
        if abs(open_brackets - close_brackets) > 5 and open_brackets > 5:
            # Might still be valid if most content is within tags
            pass

        return True

    def analyze_zip(self, zip_file_content: bytes) -> Dict[str, Any]:
        """
        Analyze all files in a ZIP - extract and analyze each file
        Returns results in-memory without database writes
        """
        # Extract files from ZIP
        extracted_files = self.extract_zip_files(zip_file_content)

        if not extracted_files:
            return {
                "success": False,
                "error": "No valid XML/TXT files found in ZIP",
                "files": []
            }

        # Analyze each file
        analyzed_files = []
        for file_data in extracted_files:
            analysis = analyze_single_file(
                file_data["content"],
                file_data["name"]
            )
            analyzed_files.append({
                "id": str(uuid.uuid4()),
                "file_name": analysis["file_name"],
                "node_count": analysis["node_count"],
                "complexity": analysis.get("complexity", "unknown"),
                "original_path": file_data.get("original_path", ""),
                "content": analysis.get("validated_xml", ""),  # Pass through content for conversion
                "status": "pending"
            })

        # Calculate totals
        total_nodes = sum(f["node_count"] for f in analyzed_files)

        return {
            "success": True,
            "files": analyzed_files,
            "total_files": len(analyzed_files),
            "total_nodes": total_nodes
        }

    def convert_bulk(self, files: List[Dict[str, Any]], bulk_task_id: str = None) -> str:
        """
        Convert multiple files in parallel using ThreadPoolExecutor
        Returns bulk_task_id for polling

        MANAGES in-memory bulk_tasks dict for status tracking
        """
        # Use provided bulk_task_id or generate new one
        if bulk_task_id is None:
            bulk_task_id = str(uuid.uuid4())

        # Initialize bulk task status
        self.bulk_tasks[bulk_task_id] = {
            "status": "PROCESSING",
            "progress": {"completed": 0, "total": len(files), "failed": 0},
            "results": [],
            "errors": [],
            "timestamp": datetime.now().isoformat()
        }

        # Convert files in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all conversion tasks
            future_to_file = {}
            for file_data in files:
                future = executor.submit(
                    convert_single_file,
                    file_data["content"],
                    file_data.get("file_name", file_data.get("name", "")),
                    bulk_task_id,
                    file_data.get("node_count", 0),
                    file_data.get("complexity", "unknown"),
                    file_data.get("id", "")
                )
                future_to_file[future] = file_data.get("file_name", file_data.get("name", ""))

            # Process results as they complete
            for future in as_completed(future_to_file):
                file_name = future_to_file[future]
                try:
                    result = future.result()

                    if result["success"]:
                        self.bulk_tasks[bulk_task_id]["results"].append({
                            "file_name": file_name,
                            "analysis_id": result.get("analysis_id", ""),
                            "no_nodes": result.get("no_nodes", 0),
                            "complexity": result.get("complexity", "unknown"),
                            "status": "completed",
                            "sql_url": result.get("sql_url"),
                            "download_name": result.get("download_name")
                        })
                        self.bulk_tasks[bulk_task_id]["progress"]["completed"] += 1
                    else:
                        self.bulk_tasks[bulk_task_id]["errors"].append({
                            "file": file_name,
                            "error": result.get("error", "Unknown error")
                        })
                        self.bulk_tasks[bulk_task_id]["progress"]["failed"] += 1

                except Exception as e:
                    logger.error(f"Error processing {file_name}: {str(e)}")
                    self.bulk_tasks[bulk_task_id]["errors"].append({
                        "file": file_name,
                        "error": str(e)
                    })
                    self.bulk_tasks[bulk_task_id]["progress"]["failed"] += 1

        # Update final status
        total = len(files)
        completed = self.bulk_tasks[bulk_task_id]["progress"]["completed"]
        failed = self.bulk_tasks[bulk_task_id]["progress"]["failed"]

        if failed == total:
            self.bulk_tasks[bulk_task_id]["status"] = "FAILED"
        elif completed == total:
            self.bulk_tasks[bulk_task_id]["status"] = "COMPLETED"
        else:
            self.bulk_tasks[bulk_task_id]["status"] = "PARTIAL"

        return bulk_task_id

    def get_bulk_status(self, bulk_task_id: str) -> Dict[str, Any]:
        """
        Get status of bulk conversion task
        """
        if bulk_task_id not in self.bulk_tasks:
            return {"error": "Task not found", "status": "UNKNOWN"}

        task = self.bulk_tasks[bulk_task_id]
        return {
            "status": task["status"],
            "progress": task["progress"],
            "results": task["results"],
            "errors": task["errors"]
        }

    def process_bulk_zip(self, zip_file_content: bytes) -> Dict[str, Any]:
        """
        Process a bulk ZIP file: analyze and convert all files
        Returns results directly instead of writing to database

        Returns:
            Dict with:
                - success: bool
                - status: "COMPLETED" | "PARTIAL" | "FAILED"
                - total_files: int
                - completed: int
                - failed: int
                - results: list of file results
                - errors: list of errors
        """
        # First analyze the ZIP
        analysis = self.analyze_zip(zip_file_content)

        if not analysis["success"]:
            return {
                "success": False,
                "status": "FAILED",
                "error": analysis["error"],
                "total_files": 0,
                "completed": 0,
                "failed": 0,
                "results": [],
                "errors": [{"file": "ZIP", "error": analysis["error"]}]
            }

        # Extract files to convert
        files_to_convert = analysis["files"]

        if not files_to_convert:
            return {
                "success": False,
                "status": "FAILED",
                "error": "No valid files to convert",
                "total_files": 0,
                "completed": 0,
                "failed": 0,
                "results": [],
                "errors": [{"file": "ZIP", "error": "No valid files to convert"}]
            }

        # Start bulk conversion
        bulk_task_id = self.convert_bulk(files_to_convert)

        # Wait for completion by getting status
        # In a real async scenario, this would be polled
        # For now, we return immediately and let caller poll
        status = self.get_bulk_status(bulk_task_id)

        return {
            "success": status["status"] in ("COMPLETED", "PARTIAL"),
            "status": status["status"],
            "bulk_task_id": bulk_task_id,
            "total_files": len(files_to_convert),
            "completed": status["progress"]["completed"],
            "failed": status["progress"]["failed"],
            "results": status["results"],
            "errors": status["errors"]
        }


# Global instance
bulk_processor = BulkProcessor(max_workers=5)
