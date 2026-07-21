
# Set up your Gemini API key
from dotenv import load_dotenv
import os
import httpx
from openai import OpenAI

import asyncio
import httpx
from openai import AsyncOpenAI
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, retry_if_result

# --- Global Clients for Connection Pooling ---
# These will be initialized once and reused across requests.
_httpx_async_clients = {}
_genai_async_client = None
_gcp_region = os.getenv("GCP_REGION", "us-central1") # Standardize on a region

def get_httpx_async_client():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # If no loop is running, just return a new client (though this shouldn't happen for async code)
        return httpx.AsyncClient(
            http2=True,
            verify=True,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
    
    loop_id = id(loop)
    client = _httpx_async_clients.get(loop_id)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            http2=True,
            verify=True,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
        _httpx_async_clients[loop_id] = client
    
    # Optionally, we could clean up old loops from the dictionary, 
    # but in a short-lived dev server it's generally fine. 
    # In production with gunicorn it's also fine as processes recycle. 
    return client

_httpx_sync_client = None

def get_httpx_sync_client():
    global _httpx_sync_client
    if _httpx_sync_client is None or _httpx_sync_client.is_closed:
        _httpx_sync_client = httpx.Client(
            http2=True,
            verify=True,
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
    return _httpx_sync_client

def get_genai_async_client():
    global _genai_async_client
    if _genai_async_client is None:
        GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
        if GEMINI_API_KEY:
            _genai_async_client = genai.Client(api_key=GEMINI_API_KEY, http_options={'api_version': 'v1alpha'}) # or v1
    return _genai_async_client

def get_genai_sync_client():
    return get_genai_async_client()

# Cloud Run sets K_SERVICE env variable automatically
if not os.getenv("K_SERVICE"):
    # Not running on Cloud Run, so load local env file
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env.local')
    load_dotenv(dotenv_path)
else:
    # Running on Cloud Run, do NOT load .env.local
    pass

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")
DEEPSEEK_API_KEY = None
DEEPSEEK_BASE_URL = None

# ============================================================================
# Target-Specific SQL System Instructions
# ============================================================================
# Each target platform has its own syntax, type system, and best practices.
# The LLM uses these instructions to generate correct SQL for the target.

SQL_INSTRUCTIONS = {
    "bigquery": """You are a BigQuery SQL generation engine. Your ONLY output must be raw SQL.

STRICT RULES:
1. Output ONLY the SELECT statement - NO markdown, NO code blocks, NO explanations, NO comments
2. Use EXACT BigQuery syntax:
   - CAST(x AS INT64) not CAST(x AS INTEGER)
   - CAST(x AS FLOAT64) not CAST(x AS FLOAT)
   - SAFE_DIVIDE(a, b) for divisions that may have zero denominators
   - IFNULL(x, default) not ISNULL()
   - Use OFFSET(n) for array indexing, not [n]
   - Use backticks for reserved words as identifiers
3. Include ALL fields from the source XML ElementMapping - missing fields is a CRITICAL failure
4. Preserve EXACT alias names from ElementMapping targetName (SELECT sourceName AS targetName)
5. End query with semicolon
6. NO subqueries or CTEs - flatten to single SELECT with JOINs only
7. Maximum 1 JOIN per query
8. All string comparisons must be explicit (CAST if needed)

BANNED PATTERNS (will cause errors):
- WITH clause (CTEs)
- Nested SELECT in WHERE/FROM
- CAST(x AS INTEGER) - must use INT64
- CAST(x AS FLOAT) - must use FLOAT64  
- NUMERIC(p,s) - use plain NUMERIC with ROUND() if precision needed
- = NULL or <> NULL - must use IS NULL or IS NOT NULL
- SQL comments (-- or /* */)
- Markdown code fences""",

    "snowflake": """You are a Snowflake SQL generation engine. Your ONLY output must be raw SQL.

STRICT RULES:
1. Output ONLY the SELECT statement - NO markdown, NO code blocks, NO explanations, NO comments
2. Use EXACT Snowflake syntax:
   - CAST(x AS INTEGER) or CAST(x AS NUMBER) for integers
   - CAST(x AS FLOAT) or CAST(x AS DOUBLE) for decimals
   - NVL(x, default) or IFNULL(x, default) for null handling
   - DIV0(a, b) or DIV0NULL(a, b) for safe division
   - Use double quotes for case-sensitive identifiers
   - Use TO_DATE(), TO_TIMESTAMP() for date conversions
3. Include ALL fields from the source XML ElementMapping - missing fields is a CRITICAL failure
4. Preserve EXACT alias names from ElementMapping targetName (SELECT sourceName AS targetName)
5. End query with semicolon
6. NO subqueries or CTEs - flatten to single SELECT with JOINs only
7. Maximum 1 JOIN per query

BANNED PATTERNS (will cause errors):
- WITH clause (CTEs)
- Nested SELECT in WHERE/FROM
- = NULL or <> NULL - must use IS NULL or IS NOT NULL
- SQL comments (-- or /* */)
- Markdown code fences""",

    "databricks": """You are a Databricks SQL (Spark SQL) generation engine. Your ONLY output must be raw SQL.

STRICT RULES:
1. Output ONLY the SELECT statement - NO markdown, NO code blocks, NO explanations, NO comments
2. Use EXACT Databricks/Spark SQL syntax:
   - CAST(x AS INT) or CAST(x AS BIGINT) for integers
   - CAST(x AS DOUBLE) or CAST(x AS FLOAT) for decimals
   - COALESCE(x, default) or NVL(x, default) for null handling
   - Use backticks for identifiers with special characters
   - Use TO_DATE(), TO_TIMESTAMP() for date conversions
   - Use DATE_FORMAT() for date formatting
3. Include ALL fields from the source XML ElementMapping - missing fields is a CRITICAL failure
4. Preserve EXACT alias names from ElementMapping targetName (SELECT sourceName AS targetName)
5. End query with semicolon
6. NO subqueries or CTEs - flatten to single SELECT with JOINs only
7. Maximum 1 JOIN per query

BANNED PATTERNS (will cause errors):
- WITH clause (CTEs)
- Nested SELECT in WHERE/FROM
- = NULL or <> NULL - must use IS NULL or IS NOT NULL
- SQL comments (-- or /* */)
- Markdown code fences""",

    "redshift": """You are an Amazon Redshift SQL generation engine. Your ONLY output must be raw SQL.

STRICT RULES:
1. Output ONLY the SELECT statement - NO markdown, NO code blocks, NO explanations, NO comments
2. Use EXACT Redshift syntax:
   - CAST(x AS INTEGER) or CAST(x AS BIGINT) for integers
   - CAST(x AS FLOAT8) or CAST(x AS DECIMAL) for decimals
   - NVL(x, default) or COALESCE(x, default) for null handling
   - Use double quotes for case-sensitive identifiers
   - Use TO_DATE(), TO_TIMESTAMP() with format strings
   - Use GETDATE() for current timestamp
3. Include ALL fields from the source XML ElementMapping - missing fields is a CRITICAL failure
4. Preserve EXACT alias names from ElementMapping targetName (SELECT sourceName AS targetName)
5. End query with semicolon
6. NO subqueries or CTEs - flatten to single SELECT with JOINs only
7. Maximum 1 JOIN per query

BANNED PATTERNS (will cause errors):
- WITH clause (CTEs)
- Nested SELECT in WHERE/FROM
- = NULL or <> NULL - must use IS NULL or IS NOT NULL
- SQL comments (-- or /* */)
- Markdown code fences""",

    "synapse": """You are an Azure Synapse Analytics SQL generation engine. Your ONLY output must be raw SQL.

STRICT RULES:
1. Output ONLY the SELECT statement - NO markdown, NO code blocks, NO explanations, NO comments
2. Use EXACT Azure Synapse (T-SQL) syntax:
   - CAST(x AS INT) or CAST(x AS BIGINT) for integers
   - CAST(x AS FLOAT) or CAST(x AS DECIMAL) for decimals
   - ISNULL(x, default) or COALESCE(x, default) for null handling
   - Use square brackets [identifier] for reserved words
   - Use CONVERT() or CAST() for type conversions
   - Use GETDATE() for current timestamp
3. Include ALL fields from the source XML ElementMapping - missing fields is a CRITICAL failure
4. Preserve EXACT alias names from ElementMapping targetName (SELECT sourceName AS targetName)
5. End query with semicolon
6. NO subqueries or CTEs - flatten to single SELECT with JOINs only
7. Maximum 1 JOIN per query

BANNED PATTERNS (will cause errors):
- WITH clause (CTEs)
- Nested SELECT in WHERE/FROM
- = NULL or <> NULL - must use IS NULL or IS NOT NULL
- SQL comments (-- or /* */)
- Markdown code fences""",

    "hana": """You are a SAP HANA SQL generation engine. Your ONLY output must be raw SQL.

STRICT RULES:
1. Output ONLY the SELECT statement - NO markdown, NO code blocks, NO explanations, NO comments
2. Use EXACT SAP HANA syntax:
   - CAST(x AS INTEGER) or CAST(x AS BIGINT) for integers
   - CAST(x AS DOUBLE) or CAST(x AS DECIMAL) for decimals
   - IFNULL(x, default) or COALESCE(x, default) for null handling
   - Use double quotes for case-sensitive identifiers
   - Use TO_DATE(), TO_TIMESTAMP() for date conversions
   - NVARCHAR for Unicode strings
3. Include ALL fields from the source XML ElementMapping - missing fields is a CRITICAL failure
4. Preserve EXACT alias names from ElementMapping targetName (SELECT sourceName AS targetName)
5. End query with semicolon
6. NO subqueries or CTEs - flatten to single SELECT with JOINs only
7. Maximum 1 JOIN per query

BANNED PATTERNS (will cause errors):
- WITH clause (CTEs)
- Nested SELECT in WHERE/FROM
- = NULL or <> NULL - must use IS NULL or IS NOT NULL
- SQL comments (-- or /* */)
- Markdown code fences""",

    "datasphere": """You are a SAP Datasphere SQL generation engine. Your ONLY output must be raw SQL.

STRICT RULES:
1. Output ONLY the SELECT statement - NO markdown, NO code blocks, NO explanations, NO comments
2. Use EXACT SAP Datasphere (HANA-based) syntax:
   - CAST(x AS INTEGER) or CAST(x AS BIGINT) for integers
   - CAST(x AS DOUBLE) or CAST(x AS DECIMAL) for decimals
   - IFNULL(x, default) or COALESCE(x, default) for null handling
   - Use double quotes for case-sensitive identifiers
   - Use TO_DATE(), TO_TIMESTAMP() for date conversions
3. Include ALL fields from the source XML ElementMapping - missing fields is a CRITICAL failure
4. Preserve EXACT alias names from ElementMapping targetName (SELECT sourceName AS targetName)
5. End query with semicolon
6. NO subqueries or CTEs - flatten to single SELECT with JOINs only
7. Maximum 1 JOIN per query

BANNED PATTERNS (will cause errors):
- WITH clause (CTEs)
- Nested SELECT in WHERE/FROM
- = NULL or <> NULL - must use IS NULL or IS NOT NULL
- SQL comments (-- or /* */)
- Markdown code fences"""
}

# Datatype instructions for each target
DATATYPE_INSTRUCTIONS = {
    "bigquery": "You are a schema datatype identifier for BigQuery. Return proper BigQuery datatypes: INT64, FLOAT64, NUMERIC, STRING, BOOL, DATE, DATETIME, TIMESTAMP, BYTES, ARRAY, STRUCT.",
    "snowflake": "You are a schema datatype identifier for Snowflake. Return proper Snowflake datatypes: NUMBER, INTEGER, FLOAT, VARCHAR, BOOLEAN, DATE, TIMESTAMP, VARIANT, ARRAY, OBJECT.",
    "databricks": "You are a schema datatype identifier for Databricks. Return proper Spark SQL datatypes: INT, BIGINT, DOUBLE, STRING, BOOLEAN, DATE, TIMESTAMP, ARRAY, MAP, STRUCT.",
    "redshift": "You are a schema datatype identifier for Amazon Redshift. Return proper Redshift datatypes: INTEGER, BIGINT, FLOAT8, DECIMAL, VARCHAR, BOOLEAN, DATE, TIMESTAMP, SUPER.",
    "synapse": "You are a schema datatype identifier for Azure Synapse. Return proper T-SQL datatypes: INT, BIGINT, FLOAT, DECIMAL, VARCHAR, NVARCHAR, BIT, DATE, DATETIME2.",
    "hana": "You are a schema datatype identifier for SAP HANA. Return proper HANA datatypes: INTEGER, BIGINT, DOUBLE, DECIMAL, NVARCHAR, BOOLEAN, DATE, TIMESTAMP, ST_GEOMETRY.",
    "datasphere": "You are a schema datatype identifier for SAP Datasphere. Return proper datatypes: INTEGER, BIGINT, DOUBLE, DECIMAL, NVARCHAR, BOOLEAN, DATE, TIMESTAMP."
}

# Default target (for backward compatibility)
DEFAULT_TARGET = "bigquery"


def get_sql_instruction(target: str = None) -> str:
    """
    Get the SQL system instruction for the specified target platform.
    
    Args:
        target: Target platform name (bigquery, snowflake, databricks, redshift, synapse, hana, datasphere)
                If None or invalid, defaults to BigQuery.
    
    Returns:
        The system instruction string for the target platform.
    """
    if target is None:
        target = DEFAULT_TARGET
    target = target.lower().strip()
    return SQL_INSTRUCTIONS.get(target, SQL_INSTRUCTIONS[DEFAULT_TARGET])


def get_datatype_instruction(target: str = None) -> str:
    """
    Get the datatype identification instruction for the specified target platform.
    
    Args:
        target: Target platform name
    
    Returns:
        The datatype instruction string for the target platform.
    """
    if target is None:
        target = DEFAULT_TARGET
    target = target.lower().strip()
    return DATATYPE_INSTRUCTIONS.get(target, DATATYPE_INSTRUCTIONS[DEFAULT_TARGET])


# For backward compatibility - keep the original variable names pointing to BigQuery
sql_instruction = SQL_INSTRUCTIONS["bigquery"]
datatype_instruction = DATATYPE_INSTRUCTIONS["bigquery"]





# Unified API Call Function with error handling
# Unified API Call Function with error handling
def api_call(model_name, full_prompt, task_type='sql', target=None):
    """
    Make an API call to the specified model.
    
    Args:
        model_name: 'Gemini', 'Deepseek', or 'gemini-3.1-flash-lite-preview'
        full_prompt: The user prompt
        task_type: 'sql' or 'datatype'
        target: Target platform (bigquery, snowflake, databricks, redshift, synapse, hana, datasphere)
    """
    if task_type == 'sql':
        system_instruction_to_use = get_sql_instruction(target)
    else:
        system_instruction_to_use = get_datatype_instruction(target)

    try:
        if model_name == 'Deepseek':
            if not DEEPSEEK_API_KEY or not DEEPSEEK_BASE_URL:
                print("Error: DeepSeek API keys not configured. Cannot make DeepSeek API call.")
                return None
            
            # Using global client with pooling
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_instruction_to_use},
                    {"role": "user", "content": full_prompt},
                ],
                stream=False,
            )
            return response.choices[0].message.content

        elif model_name == 'Gemini':
            if not GEMINI_API_KEY:
                print("Error: Gemini API key not configured. Cannot make Gemini API call.")
                return None
            
            # OPTIMIZATION: Use optimized REST call with connection pooling
            # This replaces the unoptimized genai.Client usage
            model_id = "gemini-2.5-flash-lite" 
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
            params = {"key": GEMINI_API_KEY}
            
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": f"SYSTEM: You are a SQL engine. Output ONLY raw SQL.\n\nUSER: {full_prompt}"}]
                }],
                "generationConfig": {
                    "temperature": 0.1, 
                    "responseMimeType": "text/plain"
                }
            }
            
            # Get the pooled sync client
            http_client = get_httpx_sync_client()
            
            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception(lambda e: isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 503),
                reraise=True
            )
            def _do_sync_api_call():
                resp = http_client.post(url, json=payload, params=params)
                resp.raise_for_status()
                return resp.json()

            try:
                data = _do_sync_api_call()
                
                if "candidates" in data and data["candidates"]:
                    candidate = data["candidates"][0]
                    if "content" in candidate and "parts" in candidate["content"]:
                        return candidate["content"]["parts"][0]["text"]
                
                raise RuntimeError("Gemini returned empty or malformed response")
            except Exception as e:
                print(f"Gemini Sync API error: {e}")
                return None

        elif model_name == 'gemini-3.1-flash-lite-preview':
            genai_client = get_genai_sync_client()
            if not genai_client:
                print("Error: Gemini client not configured.")
                return None
            try:
                response = genai_client.models.generate_content(
                    model="gemini-3.1-flash-lite-preview",
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction_to_use,
                        temperature=0.1
                    ),
                    contents=full_prompt
                )
                return response.text
            except Exception as e:
                print(f"Gemini 3.1 Flash Lite API error: {e}")
                return None

        return None

    except Exception as e:
        # Always log error so Cloud Run logs show it
        print(f"Error during {model_name} API call: {str(e)}", flush=True)
        return None

# Unified API Call Function for Flash Models
def api_call_flash(model_name, full_prompt, task_type='sql', target=None):
    if task_type == 'sql':
        system_instruction_to_use = get_sql_instruction(target)
    else:
        system_instruction_to_use = get_datatype_instruction(target)

    if model_name == 'Deepseek':
        if not DEEPSEEK_API_KEY or not DEEPSEEK_BASE_URL:
            print("Error: DeepSeek API keys not configured. Cannot make DeepSeek API call.")
            return None
        
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_instruction_to_use},
                {"role": "user", "content": full_prompt},
            ],
            stream=False,
        )
        return response.choices[0].message.content

    elif model_name == 'Gemini':
        # OPTIMIZATION: Replaced with optimized pooled REST call
        return api_call('Gemini', full_prompt, task_type, target=target)

    return None

# Async version of api_call_flash
async def api_call_flash_async(model_name, full_prompt, task_type='sql', target=None):
    if task_type == 'sql':
        system_instruction_to_use = get_sql_instruction(target)
    else:
        system_instruction_to_use = get_datatype_instruction(target)

    if model_name == 'Deepseek':
        if not DEEPSEEK_API_KEY or not DEEPSEEK_BASE_URL:
            # print("Error: DeepSeek API keys not configured.")
            return None
        
        # Use simple non-streaming async call (assuming OpenAI client supports it or we use httpx directly if needed)
        # For now, if referencing 'client', ensure it is an AsyncOpenAI client or similar
        # Since 'client' variable isn't clearly defined as async in this snippet, we might need a workaround or assume existence.
        # However, typically Deepseek is less critical here. Let's fallback to standard api_call_async logic or implement if needed.
        # Ideally:
        # response = await async_client.chat.completions.create(...)
        pass 

    elif model_name == 'Gemini':
        # OPTIMIZATION: Replaced with optimized pooled REST call
        return await api_call_async('Gemini', full_prompt, task_type, target=target)

    return None





# DeepSeek is disabled - using Gemini only
# if not DEEPSEEK_API_KEY or not DEEPSEEK_BASE_URL:
#     print("Warning: DEEPSEEK_API_KEY or DEEPSEEK_BASE_URL not found in environment variables. DeepSeek API calls may fail.")

# Initialize client only if keys are present to avoid errors
client = None
# DeepSeek client disabled
# if DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL:
#     client = OpenAI(
#         api_key=DEEPSEEK_API_KEY,
#         base_url=DEEPSEEK_BASE_URL,
#         http_client=httpx.Client(
#             verify=True,  # Enable SSL verification for production
#             timeout=60.0,  # Set a default timeout for requests (e.g., 30 seconds)
#             limits=httpx.Limits(max_connections=200, max_keepalive_connections=100) # Align with Cloud Run concurrency
#         ),
#     )
# else:
#     print("DeepSeek client not initialized due to missing API key or base URL.")


import asyncio
from openai import AsyncOpenAI, OpenAI
import httpx

# --- Load API Keys from environment variables ---
# Best practice: store your keys in .env file or environment variables




## Async Unified API Call Function
async def api_call_async(model_name, full_prompt, task_type='sql', target=None):
    """
    Calls the specified model API asynchronously.
    
    Args:
        model_name: 'Gemini', 'Deepseek', or 'gemini-3.1-flash-lite-preview'
        full_prompt: The user prompt
        task_type: 'sql' or 'datatype'
        target: Target platform (bigquery, snowflake, databricks, redshift, synapse, hana, datasphere)
    """
    if task_type == 'sql':
        system_instruction_to_use = get_sql_instruction(target)
    else:
        system_instruction_to_use = get_datatype_instruction(target)

    if model_name == 'Deepseek':
        if not DEEPSEEK_API_KEY or not DEEPSEEK_BASE_URL:
            print("Error: DeepSeek API keys not configured.")
            return None
        
        url = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_instruction_to_use},
                {"role": "user", "content": full_prompt}
            ],
            "stream": False
        }
        try:
            client = get_httpx_async_client()
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Deepseek API error: {e}")
            return None

    elif model_name == 'Gemini':
        if not GEMINI_API_KEY:
            print("Error: Gemini API key not configured.")
            return None
        
        try:
            # OPTIMIZATION: Using the user's preferred model for accuracy
            # gemini-2.5-flash-lite provides better correctness for this task
            model_id = "gemini-2.5-flash-lite" 
            
            # OPTIMIZATION: In Cloud Run, we prefer the Vertex AI endpoints if possible,
            # but if using API Key, stick to the standard endpoint but ensure HTTP/2
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
            params = {"key": GEMINI_API_KEY}
            
            # OPTIMIZATION: Using a more efficient system instruction wrapper
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": f"SYSTEM: You are a SQL engine. Output ONLY raw SQL.\n\nUSER: {full_prompt}"}]
                }],
                "generationConfig": {
                    "temperature": 0.1, # Faster/more deterministic for SQL
                    "responseMimeType": "text/plain"
                }
            }
            
            client = get_httpx_async_client()
            
            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception(lambda e: isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 503),
                reraise=True
            )
            async def _do_async_api_call():
                resp = await client.post(url, json=payload, params=params)
                resp.raise_for_status()
                return resp.json()

            # Use HTTP/2 and pool-reused connections
            data = await _do_async_api_call()
            
            if "candidates" in data and data["candidates"]:
                candidate = data["candidates"][0]
                if "content" in candidate and "parts" in candidate["content"]:
                    return candidate["content"]["parts"][0]["text"]
            
            raise RuntimeError("Gemini returned empty or malformed response")
        except Exception as e:
            # CRITICAL: Detailed logging for Cloud Run troubleshooting
            print(f"Gemini API error (Region: {_gcp_region}): {e}")
            return None

    elif model_name == 'gemini-3.1-flash-lite-preview':
        genai_client = get_genai_async_client()
        if not genai_client:
            print("Error: Gemini client not configured.")
            return None
        try:
            response = await genai_client.aio.models.generate_content(
                model="gemini-3.1-flash-lite-preview",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction_to_use,
                    temperature=0.1
                ),
                contents=full_prompt
            )
            return response.text
        except Exception as e:
            print(f"Gemini 3.1 Flash Lite API error: {e}")
            return None

    else:
        print(f"Unknown model: {model_name}")
        return None
