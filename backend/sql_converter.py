# ---- Standard Library ----
import os
import re
import io
import csv
import zipfile
import base64
import logging
from datetime import datetime
from hashlib import sha256
from typing import Dict, Any

# ---- Third-Party Libraries ----
import numpy as np
import pandas as pd
import pyzipper
from cryptography.fernet import Fernet

# ---- Local Imports ----
from node_cache import load_node_dict
from file_processor import xml_to_sql_converter
from excel_encrypt import encrypt_xlsx_buffer

# ---- Configure Logging ----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Function to encrypt CSV using AES
def encrypt_csv_aes(data_bytes, password):
    key = sha256(password.encode()).digest()         # Derive 32-byte key from password
    fernet_key = base64.urlsafe_b64encode(key[:32])  # Fernet requires a 32-byte base64 key
    cipher = Fernet(fernet_key)
    encrypted_data = cipher.encrypt(data_bytes)
    return encrypted_data


# Main conversion function

# --------- Main Conversion ---------
async def convert_xml_to_sql(session_id, xml_content, file_name, password="YourStrongPassword123", target=None):
    try:
        base_name = os.path.splitext(file_name)[0]  # Trim extension

        # --- Convert XML -> SQL ---
        node_dict = await xml_to_sql_converter(xml_content, target=target)
        if not node_dict or not isinstance(node_dict, dict):
            raise ValueError("Failed to process XML File: Node dictionary is empty or invalid. Please check the XML structure.")
        sql_content = generate_formatted_sql(node_dict, target=target)
        sql_bytes = sql_content.encode("utf-8")

        # --- DataFrame for Excel ---
        selected_columns = [
            "Node name", "Node type", "Sources", "Fields", "No of formula",
            "Formula", "Filter Used", "Jointype", "Join Condition", "Aggregated columns"
        ]
        df_full = pd.DataFrame.from_dict(node_dict, orient="index")
        df_selected = df_full[selected_columns]

        # Replace [] with empty string (prevents NaN issues later)
        df_selected = df_selected.apply(lambda col: col.map(lambda x: '' if x == [] else x))
        df_selected = df_selected.fillna('')

        # --- Excel into memory ---
        xlsx_buffer = io.BytesIO()
        with pd.ExcelWriter(xlsx_buffer, engine="xlsxwriter") as writer:
            df_selected.to_excel(writer, index=False, sheet_name="Node level Logic")
        xlsx_buffer.seek(0)
        xlsx_bytes = xlsx_buffer.getvalue()

        # --- ZIP with SQL + Excel ---
        logger.info("Creating ZIP file with SQL and Excel content")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{base_name}.sql", sql_bytes)
            zf.writestr("Node_Logic.xlsx", xlsx_bytes)
        zip_buffer.seek(0)
        zip_data = zip_buffer.read()

        # --- AES Encrypted Excel ---
        logger.info("Encrypting Excel file")
        mapping_xlsx = export_node_dict_to_excel_buffer(node_dict)  # may return bytes or BytesIO
        encrypted_xlsx = encrypt_xlsx_buffer(mapping_xlsx, "mypassword123la")

        # --- Return both files ---
        return {
            "success": True,
            "zip_file_content": zip_data,
            "zip_filename": f"{base_name}_output.zip",
            "Data_mapping": encrypted_xlsx,
            "encrypted_csv_filename": "data_mapping.csv.aes",
            "view_name": base_name
        }

    except Exception as e:
        logger.error("Conversion failed: %s", str(e))
        return {
            "success": False,
            "error": str(e),
            "zip_file_content": b"",
            "Data_mapping": b"",
            "view_name": os.path.splitext(file_name)[0]
        }


def create_zip_package(sql_content: str, view_name: str) -> bytes:
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        sql_filename = f"{view_name}.sql"
        zipf.writestr(sql_filename, sql_content)

    buffer.seek(0)  # Reset before sanity check

    # Sanity check the ZIP file before sending
    try:
        with zipfile.ZipFile(buffer, 'r') as test_zip:
            logger.info(f"ZIP is valid. Files: {test_zip.namelist()}")
    except zipfile.BadZipFile as e:
        logger.error("ZIP is corrupted: " + str(e))
        raise RuntimeError("Generated ZIP is corrupted")

    buffer.seek(0)  # 🔁 Reset again before reading
    return buffer.read()



def generate_sql_content(view_name: str) -> str:
    # Complex query with joins
    sql_lines = """
CREATE OR REPLACE VIEW {view_name} AS

    logger.info(f"ZIP conversion successful: ")
    return response"""

   

    return sql_lines.strip()


import sqlparse

def generate_formatted_sql(node_dict, target=None):
    # Step 1: Filter & sort valid nodes
    valid_nodes = [
        (name, info) for name, info in node_dict.items()
        if info.get('Chunk SQL Primary Optimized Base')
    ]
    sorted_nodes = sorted(valid_nodes, key=lambda x: x[1]['Chunk Number'])

    if not sorted_nodes:
        return ""

    # Step 2: Clean SQL chunks
    cleaned_chunks = [
        (name, info['Chunk SQL Primary Optimized Base'].rstrip(';: \t\n\r'))
        for name, info in sorted_nodes
    ]

    # Step 3: Build SQL (CTEs if multiple chunks)
    if len(cleaned_chunks) > 1:
        ctes = cleaned_chunks[:-1]
        cte_clauses = [f"{name} AS (\n{sql.strip()}\n)" for name, sql in ctes]
        sql = "WITH " + ",\n".join(cte_clauses) + "\n" + cleaned_chunks[-1][1].strip()
    else:
        sql = cleaned_chunks[0][1].strip()

    # Step 4: Remove unwanted quotes before formatting
    sql = sql.replace('"4', '').replace('"', '')

    # Step 5: Initial reindent with sqlparse
    formatted_sql = sqlparse.format(
        sql,
        keyword_case='upper',
        identifier_case='lower',
        reindent=True,
        indent_width=4
    )
    comments = """/*
──────────────────────────────────────────────────────────────────────────────
NOTE: SQL AUTO-GENERATION REFERENCE
──────────────────────────────────────────────────────────────────────────────
The SQL statements below are generated from XML and are for reference only.
For ACCURATE and SYNTAX-CORRECT SQL/PySpark, please follow these guidelines:

✔ Always use the SQL/PySpark Mapping Engine:
    🔗 https://hanacv2sql.com/?tab=mapper

   → It generates dynamic and syntactically correct SQL/PySpark for:
     • Snowflake
     • Databricks
     • SAP Datasphere
     • Redshift
     • BigQuery
     • Microsoft Fabric

✔ Download Mapping File(Excel) from Account section:
    → This must be used with the SQL/PySpark Mapping Engine to ensure correct SQL generation.
    👤 Available at: Profile > Account > Conversions
                    or
    🔗 Direct URL: https://hanacv2sql.com/account?tab=conversions
    🔐 The file is encrypted and can only be used by the SQL/PySpark Mapping Engine.
──────────────────────────────────────────────────────────────────────────────
*/"""

    # Convert BigQuery SQL to HANA SQL only if no target is specified (Legacy Default)
    if target is None:
        formatted_sql = convert_bq_to_hana(formatted_sql)

    # Prepend the comment with a newline
    formatted_sql = comments + "\n" + formatted_sql

    return formatted_sql



import pandas as pd
from collections import defaultdict

def consolidate_to_df(node_dict):
    """
    Consolidate SourceTable_mapping_fields from all nodes into a DataFrame
    with columns: Original Table, Original Column, New Table, New Column.
    """
    consolidated = defaultdict(set)  # Avoid duplicates
    
    # Step 1: Consolidate into a dict of {table: set(columns)}
    for node_name, node_data in node_dict.items():
        mapping = node_data.get("SourceTable_mapping_fields", {})
        if isinstance(mapping, dict):
            for table, fields in mapping.items():
                consolidated[table].update(fields)
    
    # Step 2: Flatten into list of rows
    rows = []
    for table, fields in consolidated.items():
        for col in sorted(fields):
            rows.append([table, col, table, col])
    
    # Step 3: Create DataFrame
    df = pd.DataFrame(rows, columns=["Original Table", "Original Column", "New Table", "New Column"])
    return df


import pandas as pd
import numpy as np
import io


def export_node_dict_to_excel_buffer(node_dict):
    """
    Returns Excel content in memory (bytes) with:
    - Sheet 1: SQL Info (filtered where 'Is Primary' == 'yes', case-insensitive)
    - Sheet 2: Mapping Info
    """
    # --- Sheet 1: SQL Info ---
    columns_for_mapping = [
        "Node name", 
        "Chunk Number", 
        "Chunk SQL Primary Optimized Base", 
        "SourceTable_mapping_fields",
        "Is Primary"  # Ensure this column is present
    ]

    df_full = pd.DataFrame.from_dict(node_dict, orient='index')
    df_selected = df_full.reindex(columns=columns_for_mapping)
    df_selected = df_selected.map(lambda x: '' if x == [] else x)
    df_selected = df_selected.fillna('')

    # --- Apply filter: keep only rows where 'Is Primary' == 'yes' (case-insensitive) ---
    if "Is Primary" in df_selected.columns:
        df_selected = df_selected[
            df_selected["Is Primary"].astype(str).str.lower() == "yes"
        ]

    # --- Sheet 2: Mapping Info ---
    mapping_df = consolidate_to_df(node_dict)

    # Create an in-memory binary stream
    excel_buffer = io.BytesIO()

    # Write Excel file into buffer
    with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
        df_selected.to_excel(writer, sheet_name="sql info", index=False)
        mapping_df.to_excel(writer, sheet_name="mapping info", index=False)

    # Get binary content of the Excel file
    excel_content = excel_buffer.getvalue()

    return excel_content


def convert_bq_to_hana(bq_sql: str) -> str:
    """
    Converts a BigQuery SQL query by replacing BigQuery data types with
    their common SAP HANA equivalents.

    Args:
        bq_sql: A string containing the BigQuery SQL query.

    Returns:
        A new string with the data types converted for SAP HANA.
    """
    # A dictionary to map BigQuery data types to SAP HANA data types.
    # This is a general-purpose mapping and may need to be adjusted
    # for specific use cases (e.g., precision for NUMERIC).
    data_type_mapping = {
        # Numeric types
        r'\bINT64\b': 'BIGINT',
        r'\bFLOAT64\b': 'DOUBLE',
        r'\bNUMERIC\b': 'DECIMAL',
        r'\bBIGNUMERIC\b': 'DECIMAL',
        # String and binary types
        r'\bSTRING\b': 'NVARCHAR',
        r'\bBYTES\b': 'VARBINARY',
        # Boolean type
        r'\bBOOL\b': 'BOOLEAN',
        # Date/Time types
        r'\bDATE\b': 'DATE',
        r'\bTIME\b': 'TIME',
        r'\bDATETIME\b': 'TIMESTAMP',
        r'\bTIMESTAMP\b': 'TIMESTAMP',
        # Complex types
        r'\bGEOGRAPHY\b': 'ST_GEOMETRY',
        # Note: BigQuery's ARRAY and STRUCT have no direct equivalent in HANA SQL.
        # They are often handled through data modeling changes (normalization) or
        # specific tools during migration, and are left as-is for manual review.
    }

    hana_sql = bq_sql

    # Replace each BigQuery data type with its SAP HANA equivalent.
    for bq_type, hana_type in data_type_mapping.items():
        # Using re.IGNORECASE to handle both lowercase and uppercase types.
        hana_sql = re.sub(bq_type, hana_type, hana_sql, flags=re.IGNORECASE)

    return hana_sql
