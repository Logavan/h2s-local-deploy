import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables
if not os.getenv("K_SERVICE"):
    # Not running on Cloud Run
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    load_dotenv(dotenv_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Warning: GEMINI_API_KEY not found. Gemini API calls may fail.")

# Instructions
sql_instruction = "You are a helpful SQL assistant."
datatype_instruction = "You are a helpful Schema Datatype identifier. Return proper BigQuery datatype for the given column name."

# --- Unified Gemini API Call ---
def api_call(model_name, full_prompt, task_type='sql'):
    """
    Synchronous API call for Gemini models.
    Supports 'gemini' and 'flash' models.
    """
    if not GEMINI_API_KEY:
        print("Error: Gemini API key not configured.")
        return None
    print("API Called")
    system_instruction_to_use = sql_instruction if task_type == 'sql' else datatype_instruction
    print("System Instruction")
    
    # Initialize Client
    client = genai.Client(api_key=GEMINI_API_KEY, http_options={'api_version': 'v1beta'})
    
    print({model_name})
    if model_name.lower() == "gemini":
        model_id = "gemini-2.5-flash-lite"
    elif model_name.lower() == "flash":
        model_id = "gemini-2.5-flash"
    else:
        model_id = "gemini-2.5-flash-lite"

    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
    import httpx
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(lambda e: isinstance(e, (httpx.HTTPStatusError, Exception)) and ("503" in str(e) or "Service Unavailable" in str(e))),
        reraise=True
    )
    def _do_generate():
        print("line1")
        response = client.models.generate_content(
            model=model_id,
            contents=full_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction_to_use,
                temperature=0.8,
                top_p=0.9,
                top_k=50
            )
        )
        return response

    try:
        response = _do_generate()
        print("Line2")
        return response.text
    except Exception as e:
        print(f"{model_name} API error: {e}")
        return None
