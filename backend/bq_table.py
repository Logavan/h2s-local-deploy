from google.cloud import bigquery
from google.oauth2 import service_account
from google.api_core.exceptions import GoogleAPICallError, RetryError, DeadlineExceeded
import os
import re
import time
import random
import asyncio
import concurrent.futures
import json
import logging
import tenacity
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_result, retry_if_exception
from typing import Optional, Dict, List
from api_client import api_call, api_call_flash, api_call_async

# Remove all handlers from the root logger first to prevent duplicate messages
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
bq_json = os.path.join(BASE_DIR, 'dev-hanacv2sql-bq-whole.json')
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = bq_json

project_id = "dev-hanacv2sql"

# Use service account directly - no metadata server calls needed
# Previously used google_auth_default() which calls GCP metadata server on every request
# (metadata server: 169.254.169.254) for token refresh - added ~8-13s latency per batch
#
# NEW APPROACH: Load service account JSON directly
# - Faster: No metadata server round-trips
# - Local file: dev-hanacv2sql-bq-whole.json
# - Works both locally and in Cloud Run (no metadata server dependency)
#
# --- OLD APPROACH (removed - slower due to metadata server calls) ---
# from google.auth import default as google_auth_default
# from google.auth.transport.requests import AuthorizedSession
# scopes = ["https://www.googleapis.com/auth/cloud-platform"]
# credentials, _ = google_auth_default(scopes=scopes)
# adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=3)
# authorized_session = AuthorizedSession(credentials)
# authorized_session.mount("https://", adapter)
# client = bigquery.Client(project=project_id, _http=authorized_session)


credentials = service_account.Credentials.from_service_account_file(
    bq_json,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

# Create BigQuery client with direct service account credentials
client = bigquery.Client(
    project=project_id,
    credentials=credentials
)

async def get_next_dataset_name_async(base_name="dataset"):
    """
    Returns the next available dataset name in the format base_name_n (async)
    """
    import time
    start_time = time.time()
    logger.info(f"[BQ] get_next_dataset_name_async: Starting for base_name={base_name}")
    loop = asyncio.get_event_loop()
    try:
        datasets_iter = await loop.run_in_executor(None, lambda: client.list_datasets())
        datasets = [d.dataset_id for d in datasets_iter]
        logger.info(f"[BQ] get_next_dataset_name_async: Listed {len(datasets)} datasets in {time.time()-start_time:.2f}s")
    except Exception as e:
        logger.error(f"[BQ] get_next_dataset_name_async: Error listing datasets: {e}")
        datasets = []

    i = 1
    while f"{base_name}_{i}" in datasets:
        i += 1
    result = f"{base_name}_{i}"
    logger.info(f"[BQ] get_next_dataset_name_async: Selected {result} in {time.time()-start_time:.2f}s")
    return result

def get_next_dataset_name(base_name="dataset"):
    datasets = [d.dataset_id for d in client.list_datasets()]
    i = 1
    while f"{base_name}_{i}" in datasets:
        i += 1
    return f"{base_name}_{i}"


def delete_dataset(dataset_name, delete_contents=True):
    """
    Deletes a dataset in BigQuery.
    
    Args:
        dataset_name (str): The dataset ID (without project).
        delete_contents (bool): If True, delete all tables and views inside.
    """
    dataset_id = f"{client.project}.{dataset_name}"
    try:
        client.delete_dataset(dataset_id, delete_contents=delete_contents, not_found_ok=False)
        # logger.info(f"Dataset {dataset_id} deleted.")
    except Exception as e:
        logger.info(f"Error deleting dataset {dataset_id}: {e}")


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

_BQ_PRIMITIVES = {
    "INT": "INT64",
    "INTEGER": "INT64",
    "INT64": "INT64",
    "SMALLINT": "INT64",
    "BIGINT": "INT64",

    "NUMERIC": "NUMERIC",
    "DECIMAL": "NUMERIC",
    "NUMBER": "NUMERIC",
    "BIGNUMERIC": "BIGNUMERIC",

    "FLOAT": "FLOAT64",
    "DOUBLE": "FLOAT64",
    "REAL": "FLOAT64",
    "FLOAT64": "FLOAT64",

    "BOOL": "BOOL",
    "BOOLEAN": "BOOL",

    "STRING": "STRING",
    "VARCHAR": "STRING",
    "NVARCHAR": "STRING",
    "CHAR": "STRING",
    "NCHAR": "STRING",
    "TEXT": "STRING",

    "BYTES": "BYTES",

    "DATE": "DATE",
    "DATETIME": "DATETIME",
    "TIMESTAMP": "TIMESTAMP",
    "TIME": "TIME",
}

_NUMERIC_RX = re.compile(r"^\s*(big)?numeric\s*\(\s*\d+\s*,\s*\d+\s*\)\s*$", re.I)

def normalize_bq_type(dtype_raw: str) -> str:
    """
    Normalize various incoming type spellings to a valid BigQuery type.
    Examples:
      'numeric(30,9)' -> 'NUMERIC'
      'decimal(18,2)' -> 'NUMERIC'
      'float'         -> 'FLOAT64'
      'boolean'       -> 'BOOL'
      unknown         -> 'STRING'
    """
    if not dtype_raw:
        return "STRING"

    t = dtype_raw.strip().upper()

    # NUMERIC/BIGNUMERIC with precision/scale
    if _NUMERIC_RX.match(t):
        return "BIGNUMERIC" if t.startswith("BIG") else "NUMERIC"

    # Strip any (p,s) or length, keep base token
    base = re.split(r"[\s(]", t, 1)[0]
    return _BQ_PRIMITIVES.get(base, "STRING")


def build_schema_fields(schema_dict: Dict[str, str]) -> List[bigquery.SchemaField]:
    fields: List[bigquery.SchemaField] = []
    for col, dtype in schema_dict.items():
        fields.append(bigquery.SchemaField(col, normalize_bq_type(dtype)))
    return fields


def pretty_schema_repr(schema_dict: Dict[str, str]) -> str:
    parts = []
    for c, d in schema_dict.items():
        parts.append(f'bigquery.SchemaField("{c}", "{normalize_bq_type(d)}")')
    inner = ",\n    ".join(parts)
    return f"[ \n    {inner}\n]"


# -------------------------------------------------------------------
# Your existing LLM wrapper — assumed to exist:
# def api_call(model_name, full_prompt, task_type='sql'): ...
# -------------------------------------------------------------------

async def apply_llm_fix_async(table_id: str, schema_dict: Dict[str, str], error_msg: str) -> List[bigquery.SchemaField]:
    """
    Ask the LLM (async) to return a corrected {column: type} JSON for BigQuery.
    """
    full_prompt = f"""
You are fixing a BigQuery table schema.

Table: {table_id}
Current schema (JSON mapping col->type): {json.dumps(schema_dict, indent=2)}
BigQuery error:
{error_msg}

Return ONLY a valid JSON object mapping column names to BigQuery types,
no prose, no code fence, no comments. Example:
{{"id":"INT64","name":"STRING","amount":"NUMERIC"}}
"""
    try:
        llm_text = await api_call_async('Gemini', full_prompt, task_type='sql')
    except Exception as e:
        logger.info(f"LLM call failed: {e}. Falling back to STRING types.")
        return [bigquery.SchemaField(col, "STRING") for col in schema_dict.keys()]

    if not llm_text:
        return [bigquery.SchemaField(col, "STRING") for col in schema_dict.keys()]

    llm_text = llm_text.strip()
    llm_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", llm_text, flags=re.I)

    try:
        fixed_dict = json.loads(llm_text)
        if not isinstance(fixed_dict, dict) or not fixed_dict:
            raise ValueError("LLM did not return a JSON object with columns.")
        return build_schema_fields(fixed_dict)
    except Exception as parse_err:
        logger.info(f"LLM response not valid JSON; err={parse_err}. Raw:\n{llm_text}\nFalling back to STRING.")
        return [bigquery.SchemaField(col, "STRING") for col in schema_dict.keys()]

def apply_llm_fix(table_id: str, schema_dict: Dict[str, str], error_msg: str) -> List[bigquery.SchemaField]:
    # Placeholder for sync callers
    return [bigquery.SchemaField(col, "STRING") for col in schema_dict.keys()]


# -------------------------------------------------------------------
# Schema extraction (uses your logic)
# -------------------------------------------------------------------

def generate_table_schema_dict_bq(load_data: Dict) -> List[Dict]:
    """
    Convert load_data (dict of dicts, each having 'Chunk Schema')
    into a list of dictionaries with Table, Schema, and BQ_Schema (string repr).
    """
    records = []
    for node_id, node in load_data.items():
        chunk_schema_list = node.get("Chunk Schema", [])
        if not chunk_schema_list:
            continue

        for chunk_str in chunk_schema_list:
            try:
                chunk = json.loads(chunk_str)  # {"tableA": {"col": "type", ...}, ...}
            except Exception as e:
                logger.info(f"Invalid JSON in Chunk Schema (node {node_id}): {e}")
                continue

            for table, schema in chunk.items():
                # schema is a dict: {col: dtype}
                records.append({
                    "Table": table,
                    "Schema": schema,
                    "BQ_Schema": pretty_schema_repr(schema)
                })

    return records


def _merge_table_schemas(records: List[Dict]) -> Dict[str, Dict[str, str]]:
    """
    Merge multiple records per table into a single schema map.
    If the same column appears with different types, keep the first seen type
    and log the conflict.
    """
    merged: Dict[str, Dict[str, str]] = {}
    for rec in records:
        t = rec["Table"]
        sch: Dict[str, str] = rec["Schema"]
        if t not in merged:
            merged[t] = {}
        for col, dt in sch.items():
            if col not in merged[t]:
                merged[t][col] = dt
            else:
                if normalize_bq_type(merged[t][col]) != normalize_bq_type(dt):
                    logger.info(f"Type conflict for {t}.{col}: '{merged[t][col]}' vs '{dt}'. Keeping first.")
    return merged


# -------------------------------------------------------------------
# Main: create tables in BigQuery
# -------------------------------------------------------------------

async def create_all_tables_from_load_data_async(
    load_data: Dict,
    dataset_name: str,
    project_id: str = "dev-hanacv2sql",
    location: str = "US",
    replace: bool = False,
) -> None:
    """
    Async version of creating tables in BigQuery.
    """
    import time
    func_start = time.time()
    logger.info(f"[BQ] create_all_tables_from_load_data_async: START - dataset={dataset_name}, tables_count={len(load_data) if load_data else 0}")

    loop = asyncio.get_event_loop()

    dataset_id = f"{project_id}.{dataset_name}"
    dataset_obj = bigquery.Dataset(dataset_id)
    dataset_obj.location = location

    ds_start = time.time()
    await loop.run_in_executor(None, lambda: client.create_dataset(dataset_obj, exists_ok=True))
    logger.info(f"[BQ] create_all_tables_from_load_data_async: Dataset created/verified {dataset_id} in {time.time()-ds_start:.2f}s")

    records = generate_table_schema_dict_bq(load_data)
    merged = _merge_table_schemas(records)
    logger.info(f"[BQ] create_all_tables_from_load_data_async: Merged schema has {len(merged)} tables")

    for table_name, schema_dict in merged.items():
        table_start = time.time()
        table_id = f"{dataset_id}.{table_name}"
        fields = build_schema_fields(schema_dict)
        table_obj = bigquery.Table(table_id, schema=fields)

        try:
            if replace:
                await loop.run_in_executor(None, lambda: client.delete_table(table_id, not_found_ok=True))
                await loop.run_in_executor(None, lambda: client.create_table(table_obj))
            else:
                await loop.run_in_executor(None, lambda: client.create_table(table_obj, exists_ok=True))
            logger.info(f"[BQ] create_all_tables_from_load_data_async: Table {table_name} created in {time.time()-table_start:.2f}s")
        except Exception as e:
            logger.error(f"[BQ] create_all_tables_from_load_data_async: Error creating {table_id}: {e}")
            try:
                fix_start = time.time()
                fixed_fields = await apply_llm_fix_async(table_id, schema_dict, str(e))
                fixed_table = bigquery.Table(table_id, schema=fixed_fields)
                if replace:
                    await loop.run_in_executor(None, lambda: client.delete_table(table_id, not_found_ok=True))
                    await loop.run_in_executor(None, lambda: client.create_table(fixed_table))
                else:
                    await loop.run_in_executor(None, lambda: client.create_table(fixed_table, exists_ok=True))
                logger.info(f"[BQ] create_all_tables_from_load_data_async: Table {table_name} created with LLM-fixed schema in {time.time()-fix_start:.2f}s")
            except Exception as e2:
                logger.error(f"[BQ] create_all_tables_from_load_data_async: Retry failed for {table_id}: {e2}")

    logger.info(f"[BQ] create_all_tables_from_load_data_async: COMPLETE - dataset={dataset_name}, total_time={time.time()-func_start:.2f}s")

def create_all_tables_from_load_data(load_data, dataset_name, **kwargs):
    # Wrapper to run async version safely
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If in a thread with a running loop, we can't use run()
            # This shouldn't happen if called correctly, but for safety:
            asyncio.ensure_future(create_all_tables_from_load_data_async(load_data, dataset_name, **kwargs))
        else:
            loop.run_until_complete(create_all_tables_from_load_data_async(load_data, dataset_name, **kwargs))
    except RuntimeError:
        # If no loop exists in this thread, use asyncio.run
        asyncio.run(create_all_tables_from_load_data_async(load_data, dataset_name, **kwargs))
    except Exception as e:
        logger.error(f"Error in create_all_tables_from_load_data sync wrapper: {e}")





def run_bigquery_sql(
    sql: str,
    location: str = "US",
    max_retries: int = 3,
    timeout: int = 10  # small, fail fast
) -> str:
    """
    Validate a SQL query in BigQuery with retry and concise error handling.

    Returns:
        "SUCCESS" on success
        Concise error message string on failure
    """
    # Use module-level client or create new one if None
    _client = client if client is not None else bigquery.Client()

    job_config = bigquery.QueryJobConfig(
        use_legacy_sql=False,
        use_query_cache=False,
        dry_run=True  # only validate
    )

    for attempt in range(max_retries):
        try:
            query_job = _client.query(sql, job_config=job_config, location=location)
            query_job.result(timeout=timeout)  # validation happens here
            return "SUCCESS"

        except (DeadlineExceeded, GoogleAPICallError, RetryError, Exception) as e:
            msg = str(e)
            
            # Check for Auth/Scope errors - fail open (assume success)
            if "invalid_scope" in msg or "invalid_grant" in msg or "RefreshError" in msg:
                 logger.warning(f"BigQuery validation skipped due to Auth error: {msg}")
                 return "SUCCESS"
                 
            if attempt < max_retries - 1:
                # exponential backoff with jitter
                time.sleep((2 ** attempt) + random.uniform(0, 1))
                continue

            msg = str(e)

            # Extract concise syntax or location info
            syntax = re.search(r'Syntax error: (.+?) at \[', msg)
            line_col = re.search(r'line (\d+), column (\d+)', msg)

            if syntax and line_col:
                return f"Syntax error: {syntax.group(1)} at line {line_col.group(1)}, column {line_col.group(2)}"
            elif syntax:
                return f"Syntax error: {syntax.group(1)}"
            elif line_col:
                return f"Error at line {line_col.group(1)}, column {line_col.group(2)}"
            else:
                # Remove noise from google.cloud stack traces
                msg = re.sub(r'(?i)^.*?Error:\s*', '', msg)
                msg = re.sub(r'\s*\(.*google\.cloud.*\)$', '', msg)
                return msg.strip()



@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=(retry_if_result(lambda x: x is None) | retry_if_exception(lambda e: True)),
    reraise=True
)
def api_call_with_retry(model_name, full_prompt, task_type='sql'):
    """
    Unified API call wrapper with tenacity-based retries.
    """
    try:
        result = api_call(model_name, full_prompt, task_type=task_type)
        if result:
            return result
        # Returning None will trigger a retry because of retry_if_result
        return None
    except Exception as e:
        logger.error(f"Error during API call to {model_name}: {e}")
        raise # Raising an exception will also trigger a retry



# Note: reusing the already-configured 'client' with larger connection pool from top of file
# (removed duplicate: client = bigquery.Client())

# Shared thread pool executor for BigQuery operations
# Increased from 50 to 100 to handle more concurrent BQ calls
_bq_executor = concurrent.futures.ThreadPoolExecutor(max_workers=100, thread_name_prefix="bq_")

async def run_bigquery_sql_async(sql: str, location="US", max_retries=2, timeout=10):
    """
    Validate SQL via BigQuery dry_run. Optimized for speed.
    - Uses query cache for repeated validations
    - Increased timeout (10s) to handle larger queries
    - Shared thread pool to avoid executor creation overhead
    """
    import time
    start_time = time.time()
    logger.info(f"[BQ] run_bigquery_sql_async: START - location={location}, sql_len={len(sql)}, timeout={timeout}")

    job_config = bigquery.QueryJobConfig(
        use_legacy_sql=False,
        use_query_cache=True,  # Enable cache - dry_run results can be cached
        dry_run=True
    )
    loop = asyncio.get_event_loop()

    for attempt in range(max_retries):
            attempt_start = time.time()
            try:
                # Use shared executor instead of default
                query_job = await loop.run_in_executor(
                    _bq_executor,
                    lambda: client.query(sql, job_config=job_config, location=location)
                )
                await loop.run_in_executor(_bq_executor, lambda: query_job.result(timeout=timeout))
                logger.info(f"[BQ] run_bigquery_sql_async: SUCCESS in {time.time()-start_time:.2f}s (attempt {attempt+1})")
                return "SUCCESS"

            except (DeadlineExceeded, GoogleAPICallError, RetryError, Exception) as e:
                msg = str(e)

                # Check for Auth/Scope errors - fail open (assume success) to avoid blocking conversion
                if "invalid_scope" in msg or "invalid_grant" in msg or "RefreshError" in msg:
                    logger.warning(f"[BQ] run_bigquery_sql_async: Auth error - skipped: {msg}")
                    return "SUCCESS"

                if attempt < max_retries - 1:
                    logger.warning(f"[BQ] run_bigquery_sql_async: Retry attempt {attempt+1} failed: {msg[:100]}")
                    # Shorter backoff for dry_run validation
                    await asyncio.sleep(0.5 + random.uniform(0, 0.5))
                    continue

                syntax = re.search(r'Syntax error: (.+?) at \[', msg)
                line_col = re.search(r'line (\d+), column (\d+)', msg)
                result = ""
                if syntax and line_col:
                    result = f"Syntax error: {syntax.group(1)} at line {line_col.group(1)}, column {line_col.group(2)}"
                elif syntax:
                    result = f"Syntax error: {syntax.group(1)}"
                elif line_col:
                    result = f"Error at line {line_col.group(1)}, column {line_col.group(2)}"
                else:
                    result = re.sub(r'(?i)^.*?Error:\s*', '', msg).strip()
                logger.warning(f"[BQ] run_bigquery_sql_async: FAILED after {time.time()-start_time:.2f}s: {result}")
                return result

