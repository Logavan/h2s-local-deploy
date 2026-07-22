#!/usr/bin/env python3
"""
Test Gemini API key is working.
Run: python test_gemini.py
"""
import sys
import os

# Ensure we load from backend/.env
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

from api_client import api_call, api_call_flash

def test_gemini():
    test_prompt = "Write a simple SELECT statement that casts a column named 'user_id' as STRING and uses IFNULL to replace nulls with 'unknown'. Output only raw SQL."

    # Test 1: Gemini via REST (gemini-2.5-flash-lite) - used in file_processor for main conversion
    print("=" * 60)
    print("TEST 1: api_call('Gemini', ...) — REST path (gemini-2.5-flash-lite)")
    print("=" * 60)
    key = os.getenv("GEMINI_API_KEY")
    print(f"GEMINI_API_KEY loaded: {'YES' if key else 'NO'}")
    if key:
        masked = key[:12] + "..." + key[-4:]
        print(f"Key prefix: {masked}")

    try:
        result = api_call('Gemini', test_prompt, task_type='sql', target='bigquery')
        if result:
            print(f"[PASS] SUCCESS\nResponse:\n{result[:500]}")
        else:
            print("[FAIL] FAILED: api_call returned None")
    except Exception as e:
        print(f"[FAIL] FAILED: {type(e).__name__}: {e}")

    print()

    # Test 2: gemini-3.1-flash-lite-preview via genai.Client SDK path
    print("=" * 60)
    print("TEST 2: api_call('gemini-3.1-flash-lite-preview', ...) — SDK path")
    print("=" * 60)
    try:
        result2 = api_call('gemini-3.1-flash-lite-preview', test_prompt, task_type='sql', target='bigquery')
        if result2:
            print(f"[PASS] SUCCESS\nResponse:\n{result2[:500]}")
        else:
            print("[FAIL] FAILED: api_call returned None (check genai.Client init)")
    except Exception as e:
        print(f"[FAIL] FAILED: {type(e).__name__}: {e}")

    print()

    # Test 3: api_call_flash (Gemini alias)
    print("=" * 60)
    print("TEST 3: api_call_flash('Gemini', ...) — flash alias path")
    print("=" * 60)
    try:
        result3 = api_call_flash('Gemini', test_prompt, task_type='sql', target='bigquery')
        if result3:
            print(f"[PASS] SUCCESS\nResponse:\n{result3[:500]}")
        else:
            print("[FAIL] FAILED: api_call_flash returned None")
    except Exception as e:
        print(f"[FAIL] FAILED: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test_gemini()
