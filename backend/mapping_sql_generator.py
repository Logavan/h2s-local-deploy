import concurrent.futures
from logger_setup import logger
import pandas as pd
import logging
import re
import os
import sqlparse
import sqlglot
import json
from sqlglot import exp
import sys
import asyncio
import pandas as pd
from file_processor import remove_before_first_select, remove_non_sql_context, remove_unwanted_patterns, remove_sql_comments,format_sql_query, is_valid_sql, api_call_with_retry_async
from pyspark import convert_cte_to_pyspark
# from api_call_for_mapping import api_call

logger = logging.getLogger(__name__)









# New global variable for mapping sessions
mapping_sessions = {}
import pandas as pd

# ---------------- Step 1: Convert input list into DataFrame ----------------
def convert_list_to_df(sql_info_list: list) -> pd.DataFrame:
    return pd.DataFrame(sql_info_list)


# ---------------- Step 2: Flatten the JSON mapping fields ----------------
import pandas as pd
import ast
msg = ""
def flatten_source_fields(sql_df: pd.DataFrame) -> pd.DataFrame:
    """
    Flatten the 'SourceTable_mapping_fields' column in a DataFrame into a list of DataFrames 
    per row, each containing 'SourceTable' and 'SourceColumn'.

    Handles both actual dicts and string representations of dicts.
    """
    flattened_sources = []
    # logger.info("Flattening SourceTable_mapping_fields for each row...")

    for idx, row in sql_df.iterrows():
        source_map = row.get('SourceTable_mapping_fields', {})

        # Convert string representation of dict to an actual dict
        if isinstance(source_map, str) and source_map.strip():
            try:
                source_map = ast.literal_eval(source_map)
            except (ValueError, SyntaxError):
                # logger.warning(f"Row {idx}: Failed to parse string to dict: {source_map}")
                source_map = {}

        # logger.info(f"Flattening source fields for row {idx}: {source_map}")

        # Flatten only if it's a dict with content
        if isinstance(source_map, dict) and source_map:
            flat_df = pd.DataFrame(
                [(table, col) for table, cols in source_map.items() for col in cols],
                columns=['SourceTable', 'SourceColumn']
            )
        else:
            flat_df = pd.DataFrame(columns=['SourceTable', 'SourceColumn'])

        # logger.info(f"Flattened source fields for row {idx}: {flat_df.shape[0]} rows")
        flattened_sources.append(flat_df)

    sql_df['Flattened_Source'] = flattened_sources
    # logger.info(f"Flattened source fields for all rows. DataFrame now has {len(sql_df)} rows.")

    return sql_df



# ---------------- Step 3: Find matched mapping ----------------
from collections import defaultdict

def match_mapping(sql_df: pd.DataFrame, mappings_list: list) -> pd.DataFrame:
    # Convert mappings list to DataFrame and clean column names
    mapping_df = pd.DataFrame(mappings_list)
    mapping_df.columns = mapping_df.columns.str.strip()
    
    packed_mappings_list = []

    for idx, row in sql_df.iterrows():
        # logger.info(f"Matching mapping for row {idx}...")
        flat_source = row['Flattened_Source']

        if not flat_source.empty:
            # Merge using the correct keys from your mapping JSON
            matched = pd.merge(
                flat_source,
                mapping_df,
                left_on=['SourceTable', 'SourceColumn'],
                right_on=['sourceTable', 'sourceField'],
                how='inner'
            )

            # Group by SourceTable to pack JSON
            grouped = defaultdict(lambda: {"targetTable": None, "columns": []})
            for _, m_row in matched.iterrows():
                grouped[m_row['SourceTable']]["targetTable"] = m_row['targetTable']
                grouped[m_row['SourceTable']]["columns"].append({
                    "sourceColumn": m_row['SourceColumn'],
                    "targetColumn": m_row['targetField']
                })

            # Convert to list of packed JSONs
            packed_mappings = [
                {"sourceTable": k, "targetTable": v["targetTable"], "columns": v["columns"]}
                for k, v in grouped.items()
            ]
        else:
            packed_mappings = []

        packed_mappings_list.append(packed_mappings)

    sql_df['Matched_Mapping'] = packed_mappings_list
    # logger.info(f"Matched mapping info for {len(packed_mappings_list)} rows.")
    return sql_df

def validate_query(sql_df):
    df = pd.DataFrame(sql_df)

    # Ensure 'Chunk Number' is integer
    df["Chunk Number"] = df["Chunk Number"].astype(int)

    # Sort by Chunk Number numerically
    df = df.sort_values("Chunk Number")

    # Generate SQL dynamically
    if len(df) == 1:
        row = df.iloc[0]
        sql_query = (
            f"SELECT {row['Chunk Number']} AS ord,"
            f"'{row['Node name']}' AS cte_name,"
            f"COUNT(*) AS row_count "
            f"FROM {row['Node name']} ;"
        )
    else:
        sql_parts = [
            f"SELECT {row['Chunk Number']} AS ord,"
            f"'{row['Node name']}' AS cte_name,"
            f"COUNT(*) AS row_count "
            f"FROM {row['Node name']}"
            for _, row in df.iterrows()
        ]
        sql_query = " \nUNION ALL\n".join(sql_parts) + "\nORDER BY ord;"

    sql_query = format_other_query(sql_query)

    commented_sql_query = f"/*\n{sql_query}\n*/"

    return commented_sql_query



def format_dsp_query(sql):
    formatted_sql = sqlparse.format(
    sql,
    reindent=True,
    keyword_case='upper',  # This changes keywords to uppercase
    identifier_case=None   # This preserves the case of field and table names
    )
    return formatted_sql

def format_other_query(sql):
    formatted_sql = sqlparse.format(
    sql,
    reindent=False,
    keyword_case='upper',  # This changes keywords to uppercase
    identifier_case='lower'   # This preserves the case of field and table names
    )
    return formatted_sql

def format_other_query_intent(sql):
    formatted_sql = sqlparse.format(
    sql,
    reindent=True,
    keyword_case='upper',  # This changes keywords to uppercase
    identifier_case='lower'   # This preserves the case of field and table names
    )
    return formatted_sql

from datetime import date
# ---------------- Step 4: Update SQL with matched mapping ----------------
def generate_sql_from_updated_info(sql_df: pd.DataFrame, database_name: str) -> str:
    """
    Generates SQL from a DataFrame and prepends a generic SQL header comment.
    Adds platform-specific CREATE VIEW statement.
    """
    # logger.info("Generating SQL from updated info...")
    # logger.info(sql_df.head(1))  # Debugging: print first few rows of DataFrame
    generated_sql = consolidated_sql_from_df(sql_df)  # Your SELECT statement
    generated_sql = format_other_query(generated_sql)
    validation_query = validate_query(sql_df)


    dsp_generated_sql = consolidated_sql_from_df_dsp(sql_df)  # Your SELECT statement for Datasphere
    dsp_generated_sql = format_dsp_query(dsp_generated_sql)

    

    view_name = "your_view_name"  # Replace with your actual view name logic
    today = date.today().strftime("%Y-%m-%d")
    
    # Determine platform-specific CREATE VIEW line

    if database_name.lower() in ["bigquery", "bq"]:
        create_view_line = f"CREATE OR REPLACE VIEW `project.dataset.{view_name}` AS\n"
    elif database_name.lower() in ["azure", "synapse", "fabric"]:
        create_view_line = f"CREATE OR ALTER VIEW dbo.{view_name} AS\n"
    elif database_name.lower() in ["snowflake", "redshift", "databricks"]:
        create_view_line = f"CREATE OR REPLACE VIEW public.{view_name} AS\n"
    elif database_name.lower() in ["datasphere", "sap datasphere"]:
        create_view_line = ""
    else:
        create_view_line = f"CREATE OR REPLACE VIEW {view_name} AS\n"  # Default fallback

    # Generic SQL header comment
    # Generic SQL header comment with decorative lines
    header_comment = f"""
    /******************************************************************************************
    *                                                                                        *
    *  Author      : HANACV2SQL                                                              *
    *  Date        : {today}                                                              *
    *  Project     : HANA CV Migration                                                       *
    *  Version     : 1.0                                                                     *
    *  Environment : <Environment Name>                                                      *
    *                                                                                        *
    *  Description : <Brief description of this SQL>                                         *
    *                                                                                        *
    *  Change Log :                                                                          *
    *      {today} - Initial version                                                      *
    *                                                                                        *
    ******************************************************************************************/
    """

    if database_name.lower() not in ["datasphere", "sap datasphere"]:
    # Combine header, CREATE VIEW line, and SELECT statement
        final_sql = (
    header_comment + "\n" + create_view_line + generated_sql + "\n\n\n"
    + "-- Validation:\n\n"
    + "-- This validation is based on the record count present at HANA CV node level\n"
    + "-- and the records present in the CTE of SQL generated by the Mapping Engine.\n\n"
    + "-- No of CTE in SQL will generally differ from no of nodes in CV,\n"
    + "-- as the HANACV2SQL drastically reduces the CTE to improve performance.\n\n"
    + "-- To get the node-level record count in a HANA Calculation View, follow this blog:\n"
    + "-- http://hanacv2sql.com/blog/sap-hana-node-record-counts\n\n"
    + "-- Record count at CTE level:\n"
    + validation_query
)

    else:

        final_sql = header_comment + "\n" + dsp_generated_sql 

    return final_sql

# Placeholder for generating SQL with Temp Tables
def generate_temp_table_sql(sql_info_list: list, mappings_list: list, database_name: str) -> str:
    """
    Placeholder function to simulate generating SQL with Temp Tables.
    In a real scenario, this would involve different SQL generation logic.
    """
    # logger.info("Generating placeholder SQL for CTE + Temp Tables version...")
    # For now, return a dummy SQL string
    dummy_sql = f"""
    -- This is a placeholder for CTE + Temp Tables version for {database_name}
    -- Based on sql_info: {len(sql_info_list)} entries
    -- And mappings: {len(mappings_list)} entries

    CREATE TEMPORARY TABLE TempTable1 AS
    SELECT
        'dummy_data_1' AS col1,
        'dummy_data_2' AS col2;

    CREATE TEMPORARY TABLE TempTable2 AS
    SELECT
        'more_dummy_data_3' AS col3,
        'more_dummy_data_4' AS col4
    FROM TempTable1;

    SELECT
        t1.col1,
        t2.col3
    FROM TempTable1 t1
    JOIN TempTable2 t2 ON t1.col1 = t2.col3;
    """
    return format_sql_query(dummy_sql)


# ---------------- PySpark Notebook Generation ----------------
def generate_pyspark_notebook(sql_df: pd.DataFrame, database_name: str, mappings_list: list = None) -> str:
    """
    Generates a Jupyter Notebook (.ipynb) JSON string from the processed
    CTE DataFrame. Each CTE is converted to PySpark DataFrame API code
    using convert_cte_to_pyspark(). Markdown cells with step comments
    are placed between code cells.

    Returns:
        A JSON string representing a valid .ipynb notebook (nbformat v4).
    """
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")

    cells = []

    # ── Cell 0: Header markdown ───────────────────────────────────────
    header_md = (
        "# HANA CV Migration — PySpark Notebook\n"
        "\n"
        f"| Field | Value |\n"
        f"| --- | --- |\n"
        f"| **Author** | HANACV2SQL |\n"
        f"| **Date** | {today} |\n"
        f"| **Project** | HANA CV Migration |\n"
        f"| **Version** | 1.0 |\n"
        f"| **Environment** | \<Environment Name\> |\n"
        "\n"
        f"> **Change Log**: {today} — Initial version"
    )
    cells.append(_make_nb_cell("markdown", header_md))

    # ── Cell 1: PySpark Imports ───────────────────────────────────────
    import_code = (
        "from pyspark.sql import SparkSession\n"
        "from pyspark.sql import functions as F\n"
        "from pyspark.sql.functions import col, lit, expr\n"
        "from pyspark.sql.window import Window\n"
        "\n"
        "# Initialize Spark session (update app name as needed)\n"
        "spark = SparkSession.builder.appName('HANA_CV_Migration').getOrCreate()"
    )
    cells.append(_make_nb_cell("code", import_code))

    # ── Cell 2: Load Source Tables ────────────────────────────────────
    if mappings_list:
        unique_tables = set()
        for m in mappings_list:
            t = m.get('targetTable') or m.get('sourceTable')
            if t:
                unique_tables.add(t.strip())
                
        if unique_tables:
            load_lines = []
            for t in sorted(unique_tables):
                var_name = t.split('.')[-1]
                load_lines.append(f'{var_name} = spark.table("{t}")')
                
            cells.append(_make_nb_cell("markdown", "## Load Source Tables\nLoad required tables into PySpark DataFrames."))
            cells.append(_make_nb_cell("code", "\n".join(load_lines)))

    # ── Filter valid rows ─────────────────────────────────────────────
    valid_df = sql_df[
        sql_df['Chunk SQL Primary Optimized Base']
        .str.contains('select', case=False, na=False)
    ].copy()

    if valid_df.empty:
        cells.append(_make_nb_cell("markdown", "⚠️ No valid SQL chunks found in the mapping data."))
        return _build_notebook_json(cells)

    valid_df['Chunk Number'] = pd.to_numeric(valid_df['Chunk Number'], errors='coerce')
    valid_df.dropna(subset=['Chunk Number'], inplace=True)
    valid_df = valid_df.sort_values('Chunk Number')
    valid_df['Cleaned SQL'] = valid_df['Chunk SQL Primary Optimized target'].str.rstrip(';: \t\n\r')

    total_rows = len(valid_df)

    for row_idx, (_, row) in enumerate(valid_df.iterrows()):
        chunk_num = int(row['Chunk Number'])
        comment = str(row.get('Chunk SQL Primary Comments', '')).strip()
        node_name = row['Node name']
        sql = row['Cleaned SQL']

        is_last = (row_idx == total_rows - 1)

        # ── Markdown comment cell ─────────────────────────────────────
        md_lines = []
        step_label = f"Step {chunk_num}"
        if is_last:
            step_label += " (Final SELECT)"
        if comment:
            md_lines.append(f"## {step_label}: {comment}")
        else:
            md_lines.append(f"## {step_label}")
        cells.append(_make_nb_cell("markdown", "\n".join(md_lines)))

        # ── PySpark code cell ─────────────────────────────────────────
        pyspark_code = row.get('LLM Refined PySpark')
        
        if not pyspark_code or not str(pyspark_code).strip():
            # Fallback if the LLM cell is empty for some reason
            cte_sql_str = f"{node_name} AS (\n{sql}\n)"
            try:
                pyspark_code = convert_cte_to_pyspark(cte_sql_str)
            except Exception as exc:
                logger.warning(f"PySpark conversion failed for {node_name}: {exc}")
                safe_sql = sql.replace("'''", "\\'\\'\\'")
                pyspark_code = (
                    f"# ⚠ Conversion failed for {node_name}: {exc}\n"
                    f"# Falling back to spark.sql()\n"
                    f"{node_name} = spark.sql('''{safe_sql}''')\n"
                )
        
        # Add step header comment for readability
        step_header = f"# ── Step {chunk_num}: {comment} ──" if comment else f"# ── Step {chunk_num} ──"
        if is_last:
            step_header = step_header.rstrip(" ──") + " (Final SELECT) ──"
        pyspark_code = step_header + "\n" + str(pyspark_code)
        
        cells.append(_make_nb_cell("code", pyspark_code))

    # ── Final markdown: display result ────────────────────────────────
    last_node = valid_df.iloc[-1]['Node name']
    cells.append(_make_nb_cell("markdown", "## Display Result"))
    cells.append(_make_nb_cell("code", f"display({last_node})  # Use {last_node}.show() if running outside Databricks"))

    return _build_notebook_json(cells)

def _make_nb_cell(cell_type: str, source: str) -> dict:
    """Create a single Jupyter notebook cell dict (nbformat v4)."""
    cell = {
        "cell_type": cell_type,
        "metadata": {},
        "source": source.splitlines(keepends=True),  # nbformat expects list of lines
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def _build_notebook_json(cells: list) -> str:
    """Wrap cells list into a complete .ipynb JSON string (nbformat v4)."""
    notebook = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3"
            },
            "language_info": {
                "name": "python",
                "version": "3.10.0"
            }
        },
        "cells": cells
    }
    return json.dumps(notebook, indent=1, ensure_ascii=False)


# ---------------- Final Function ----------------
async def generate_sql_from_mapping(sql_info_list: list, mappings_list: list, database_name: str, target: str = None, output_format: str = "sql") -> tuple[str, str]:
    """
    Full pipeline:
    1. Convert list -> DataFrame
    2. Flatten SourceTable_mapping_fields
    3. Match mapping info
    4. Update SQL info
    5. Generate SQL (CTE version and Temp Tables version)
       OR generate PySpark notebook when output_format == 'pyspark'
    """

    logger.info("Starting SQL generation from mapping...")
    logger.info(f"Database name: {database_name}, output_format: {output_format}")
    # Step 1
    sql_df = convert_list_to_df(sql_info_list)
    # Step 2
    sql_df = flatten_source_fields(sql_df)
    # Step 3
    sql_df = match_mapping(sql_df, mappings_list)
    
    # # Step 4 + 5: Generate SQL for CTE version
    sql_df_cte = await update_target_sql_parallel(sql_df.copy(), database_name, target=target) # Use a copy for CTE version
    sql_df_cte = await update_comments_parallel(sql_df_cte, database_name, target=target) # Use a copy for CTE version

    # ── PySpark notebook path ─────────────────────────────────────────
    if output_format == "pyspark":
        logger.info("Generating PySpark notebook with LLM refinement...")
        
        base_tables = []
        if mappings_list:
            unique_tables = set()
            for m in mappings_list:
                t = m.get('targetTable') or m.get('sourceTable')
                if t:
                    unique_tables.add(t.strip())
            base_tables = sorted(list(unique_tables))
        
        # We need 'Cleaned SQL' column available for the LLM refinement step before notebook generation
        sql_df_cte['Chunk Number'] = pd.to_numeric(sql_df_cte['Chunk Number'], errors='coerce')
        sql_df_cte['Cleaned SQL'] = sql_df_cte['Chunk SQL Primary Optimized target'].str.rstrip(';: \\t\\n\\r')
        
        # Extract CTE variables (DataFrame variables)
        cte_variables = []
        if 'Node name' in sql_df_cte.columns:
            cte_variables = sorted(sql_df_cte['Node name'].dropna().unique().tolist())
        
        sql_df_cte = await update_pyspark_code_parallel(sql_df_cte, database_name, target=target, base_tables=base_tables, cte_variables=cte_variables)
        notebook_json = generate_pyspark_notebook(sql_df_cte, database_name, mappings_list)
        logger.info("PySpark notebook generation completed.")
        return notebook_json, ""   # second value unused for PySpark

    # ── SQL path (existing behaviour) ─────────────────────────────────
    cte_sql_content = generate_sql_from_updated_info(sql_df_cte, database_name)
    if isinstance(cte_sql_content, list):
        cte_sql_content = "\n".join(cte_sql_content)

    # Generate SQL for Temp Tables version (using placeholder for now)
    temp_table_sql_content = generate_temp_table_sql(sql_info_list, mappings_list, database_name)

    logger.info("SQL generation completed.")
#---------------------------------------------------------------------------------
    # Get current directory of this script
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # Check type of sql_df and convert accordingly
    if isinstance(sql_df, dict):
        df = pd.DataFrame.from_dict(sql_df, orient='index').reset_index()
        df.rename(columns={'index': 'Node name'}, inplace=True)
    else:
        df = pd.DataFrame(sql_df)

#---------------------------------------------------------------------------------

    logger.info("SQL generation completed.")
    return cte_sql_content, temp_table_sql_content




# def update_target_sql_parallel(sql_df: pd.DataFrame, database_name: str) -> pd.DataFrame:

    
#     with concurrent.futures.ThreadPoolExecutor() as executor:
#         futures = {
#             executor.submit(update_target_sql, row, database_name): idx
#             for idx, row in sql_df.iterrows()
#             if pd.notna(row["Chunk SQL Primary Optimized Base"]) and row["Chunk SQL Primary Optimized Base"].strip()
#         }

#         for future in concurrent.futures.as_completed(futures):
#             idx = futures[future]
#             sql_df.loc[idx, "Chunk SQL Primary Optimized target"] = future.result()
    
#     return sql_df



async def update_target_sql_parallel(sql_df: pd.DataFrame, database_name: str, target: str = None, max_concurrent: int = 50) -> pd.DataFrame:
    """
    Updates target SQL for all rows in parallel using asyncio.
    Limits concurrency with a semaphore.
    """

    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_update(idx, row):
        async with semaphore:
            sql_text = row.get("Chunk SQL Primary Optimized Base", "")
            if pd.notna(sql_text) and sql_text.strip():
                result = await update_target_sql(row, database_name, target=target)
                sql_df.loc[idx, "Chunk SQL Primary Optimized target"] = result

    # Create async tasks for valid rows
    tasks = [
        run_update(idx, row)
        for idx, row in sql_df.iterrows()
        if pd.notna(row["Chunk SQL Primary Optimized Base"]) and row["Chunk SQL Primary Optimized Base"].strip()
    ]

    # Run all tasks concurrently
    await asyncio.gather(*tasks)

    return sql_df

# def update_comments_parallel(sql_df: pd.DataFrame, database_name: str) -> pd.DataFrame:

    
#     with concurrent.futures.ThreadPoolExecutor() as executor:
#         futures = {
#             executor.submit(update_comments, row, database_name): idx
#             for idx, row in sql_df.iterrows()
#             if pd.notna(row["Chunk SQL Primary Optimized Base"]) and row["Chunk SQL Primary Optimized Base"].strip()
#         }

#         for future in concurrent.futures.as_completed(futures):
#             idx = futures[future]
#             sql_df.loc[idx, "Chunk SQL Primary Comments"] = future.result()
    
#     return sql_df




async def update_comments_parallel(sql_df: pd.DataFrame, database_name: str, target: str = None, max_concurrent: int = 50) -> pd.DataFrame:
    """
    Updates comments for all rows in parallel using asyncio.
    Limits concurrency with a semaphore.
    """

    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_update(idx, row):
        async with semaphore:
            # Only process if SQL text is present
            sql_text = row.get("Chunk SQL Primary Optimized Base", "")
            if pd.notna(sql_text) and sql_text.strip():
                result = await update_comments(row, database_name, target=target)
                sql_df.loc[idx, "Chunk SQL Primary Comments"] = result

    # Create async tasks for valid rows
    tasks = [
        run_update(idx, row)
        for idx, row in sql_df.iterrows()
        if pd.notna(row["Chunk SQL Primary Optimized Base"]) and row["Chunk SQL Primary Optimized Base"].strip()
    ]

    # Run all tasks concurrently
    await asyncio.gather(*tasks)

    return sql_df

    
def clean_comment(text: str) -> str:
    # Remove leading comment symbols
    text = re.sub(r'^(--|#|//)\s*', '', text.strip())
    # Keep only one line
    text = text.splitlines()[0]
    # Remove unwanted punctuation (optional)
    text = re.sub(r'[^\w\s.,]', '', text)
    return text.strip()

async def update_comments(row: pd.Series, database_name: str, target: str = None) -> str:
    sql_base = row["Chunk SQL Primary Optimized Base"]
    promt = f"""I will provide one SQL CTE at a time. The SQL may contain technical field names (like KUNNR, MATNR, VBELN, BUKRS). 
Return only a one-line comment in plain English describing the purpose of the CTE. 
Expand technical names into business-friendly terms where possible. 
Do not include -- or any symbols. Keep it short, clear, and maximum 8 words.
"""
    comments = await api_call_with_retry_async('Gemini', f"{promt} Here is the SQL: {sql_base}", task_type='sql', target=target)

    comments = clean_comment(comments)
    return comments.strip()

async def update_pyspark_code_parallel(sql_df: pd.DataFrame, database_name: str, target: str = None, max_concurrent: int = 50, base_tables: list = None, cte_variables: list = None) -> pd.DataFrame:
    """
    Refines the manually generated PySpark code using an LLM in parallel.
    Limits concurrency with a semaphore.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_update(idx, row):
        async with semaphore:
            # We only generate PySpark for rows with valid SQL
            sql_text = row.get("Cleaned SQL", "")
            node_name = row.get("Node name", "")
            matched_mapping = row.get("Matched_Mapping", [])
            if pd.notna(sql_text) and sql_text.strip():
                result = await update_pyspark(sql_text, node_name, target=target, base_tables=base_tables, cte_variables=cte_variables, mapping_fields=matched_mapping)
                sql_df.loc[idx, "LLM Refined PySpark"] = result

    tasks = [
        run_update(idx, row)
        for idx, row in sql_df.iterrows()
        if pd.notna(row.get("Cleaned SQL", "")) and str(row.get("Cleaned SQL", "")).strip()
    ]

    await asyncio.gather(*tasks)
    return sql_df

def _clean_pyspark_output(code: str) -> str:
    """
    Robust post-processor for LLM-generated PySpark code.
    Strips markdown fences, deduplicates imports, removes SparkSession
    boilerplate, and normalises whitespace for notebook embedding.
    """
    if not code:
        return code

    text = code.strip()

    # ── 1. Strip markdown code fences ─────────────────────────────────
    # Handle ```python ... ```, ```py ... ```, ``` ... ```
    fence_re = re.compile(
        r'^\s*```(?:python|py)?\s*\n?(.*?)\n?\s*```\s*$',
        re.DOTALL
    )
    m = fence_re.match(text)
    if m:
        text = m.group(1).strip()
    else:
        # Fallback: strip leading/trailing fences individually
        for prefix in ('```python', '```py', '```'):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

    # ── 2. Remove conversational preamble / postscript ────────────────
    lines = text.splitlines()
    # Drop leading lines that look like prose (no =, no (, no import, no #)
    while lines and not re.match(
        r'^\s*(#|import |from |[A-Za-z_]\w*\s*=|[A-Za-z_]\w*\.)', lines[0]
    ):
        lines.pop(0)
    # Drop trailing prose lines
    while lines and not re.match(
        r'^\s*(#|import |from |[A-Za-z_]\w*|\)|\]|\}|\.)', lines[-1]
    ):
        lines.pop()

    # ── 3. Remove all import lines (already in Cell 1) ─────────────────
    deduped: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(('import ', 'from pyspark', 'from pyspark.')):
            continue  # skip — imports are centralised in Cell 1
        deduped.append(line)
    lines = deduped

    # ── 3b. Remove all comment-only lines ─────────────────────────────
    lines = [ln for ln in lines if not ln.strip().startswith('#')]

    # ── 4. Remove SparkSession creation (already in Cell 1) ──────────
    cleaned: list[str] = []
    skip_spark_builder = False
    for line in lines:
        s = line.strip()
        # skip lines like: spark = SparkSession.builder...
        if 'SparkSession.builder' in s:
            skip_spark_builder = True
            continue
        if skip_spark_builder:
            # continuation lines of builder chain
            if s.startswith('.') or s.startswith(')'):
                if '.getOrCreate()' in s:
                    skip_spark_builder = False
                continue
            skip_spark_builder = False
        cleaned.append(line)
    lines = cleaned

    # ── 5. Normalise whitespace ───────────────────────────────────────
    # Strip trailing spaces, collapse 3+ consecutive blank lines to 2
    result: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip() == '':
            blank_count += 1
            if blank_count <= 2:
                result.append('')
        else:
            blank_count = 0
            result.append(line.rstrip())

    return '\n'.join(result).strip()


async def update_pyspark(sql_text: str, node_name: str, target: str = None, base_tables: list = None, cte_variables: list = None, mapping_fields: list = None) -> str:
    from pyspark import convert_cte_to_pyspark
    
    # 1. Get the manual regex conversion as a baseline
    cte_sql_str = f"{node_name} AS (\n{sql_text}\n)"
    try:
        baseline_code = convert_cte_to_pyspark(cte_sql_str, base_tables=base_tables)
    except Exception as e:
        safe_sql = sql_text.replace("'''", "\\'\\'\\'")
        baseline_code = f"# Fallback applied during initial translation\n{node_name} = spark.sql('''{safe_sql}''')"

    # 2. Ask Gemini to refine it with a production-grade prompt
    base_tables_list = ", ".join([f"'{t}'" for t in base_tables]) if base_tables else "None"
    cte_var_list = ", ".join([f"'{v}'" for v in cte_variables]) if cte_variables else "None"
    
    # Build column mapping context for the prompt
    mapping_context = ""
    if mapping_fields:
        mapping_lines = []
        for table_mapping in mapping_fields:
            src_table = table_mapping.get("sourceTable", "")
            tgt_table = table_mapping.get("targetTable", "")
            mapping_lines.append(f"- Table: `{src_table}` → `{tgt_table}`")
            for col in table_mapping.get("columns", []):
                src_col = col.get("sourceColumn", "")
                tgt_col = col.get("targetColumn", "")
                if src_col != tgt_col:
                    mapping_lines.append(f"  - Column: `{src_col}` → `{tgt_col}` (use `F.col(\"{tgt_col}\")` then `.alias(\"{src_col}\")`)")
        if mapping_lines:
            mapping_context = f"""

### Column Mapping (CRITICAL — READ CAREFULLY)
The SQL above uses `targetColumn AS sourceColumn` pattern (e.g., `mono AS calmonth`).
This means the **physical column** in the target database table is the TARGET name, and the alias preserves the old HANA name for downstream compatibility.

You MUST:
1. Use the **target (new) column name** when reading from physical base tables with `F.col()` (e.g., `F.col("mono")`, NOT `F.col("calmonth")`).
2. Alias the output back to the **source (old) column name** using `.alias()` (e.g., `F.col("mono").alias("calmonth")`) so downstream cells can reference the old name.
3. In WHERE/filter conditions, use the **target column name** (e.g., `F.col("mono").startswith("2")`).

Mapping details:
{chr(10).join(mapping_lines)}
"""
    
    prompt = f"""You are a senior PySpark engineer. Your task is to produce **production-ready** PySpark DataFrame API code.

### Original SQL (CTE: `{node_name}`)
```sql
{sql_text}
```

### Baseline PySpark (auto-generated — may contain errors)
```python
{baseline_code}
```

{mapping_context}
### Table vs Variable Resolution (CRITICAL)
You must carefully distinguish between physical base tables and intermediate DataFrame variables (CTEs):
- **Base Tables**: [{base_tables_list}] -> These are physical tables in the catalog.
- **DataFrame Variables (CTEs)**: [{cte_var_list}] -> These are Python variables representing intermediate dataframes.

### Your Instructions
Compare the baseline PySpark code against the original SQL and produce a corrected, production-quality version.

**Mandatory rules:**
1. Use ONLY the native PySpark DataFrame API — `.select()`, `.filter()` / `.where()`, `.join()`, `.groupBy()`, `.agg()`, `.withColumn()`, `.orderBy()`, `.limit()`, `.distinct()`, `.union()` / `.unionAll()`, etc.
2. NEVER use `spark.sql("...")` unless the SQL contains truly untranslatable syntax (recursive CTEs, LATERAL, UNPIVOT, etc.).
3. Always qualify column references with `F.col("column")` to avoid ambiguity.
4. Use the `pyspark.sql.functions` namespace as `F` (e.g. `F.sum()`, `F.coalesce()`, `F.when()`, `F.lit()`, `F.col()`, `F.concat()`, `F.date_format()`, `F.row_number()`, etc.).
5. For window functions use `Window.partitionBy(...).orderBy(...)` from `pyspark.sql.window`.
6. Map SQL constructs correctly:
   - `CASE WHEN ... THEN ... ELSE ... END` → `F.when(..., ...).when(..., ...).otherwise(...)`
   - `COALESCE(a, b)` → `F.coalesce(F.col("a"), F.col("b"))`
   - `CAST(x AS type)` → `.cast("type")` or `F.col("x").cast("type")`
   - `IFNULL / NVL` → `F.coalesce()`
   - `COUNT(DISTINCT x)` → `F.countDistinct("x")`
   - `CONCAT(a, b)` → `F.concat(F.col("a"), F.col("b"))`
   - `SUBSTRING(x, s, l)` → `F.substring(F.col("x"), s, l)`
   - `TRIM / UPPER / LOWER / LENGTH` → `F.trim() / F.upper() / F.lower() / F.length()`
   - `LEFT JOIN` → `.join(..., ..., "left")`
   - `UNION ALL` → `.unionAll()` or `.union()`
   - `GROUP BY + aggregates` → `.groupBy(...).agg(F.sum(...).alias("..."), ...)`
   - **CRITICAL**: Non-aggregate columns must go inside `groupBy()` or be included via `.select()` AFTER aggregation. DO NOT put `F.substring()`, `F.col()` or any other non-aggregate functions inside `.agg()`. Only aggregate functions (like `F.sum`, `F.count`) are allowed inside `.agg()`.
   - `HAVING` → `.filter(...)` after `.agg()`
   - `ORDER BY x DESC` → `.orderBy(F.col("x").desc())`
   - `LIMIT n` → `.limit(n)`
   - `IS NULL / IS NOT NULL` → `.isNull()` / `.isNotNull()`
   - `IN (...)` → `.isin([...])`
   - `BETWEEN a AND b` → `(F.col("x") >= a) & (F.col("x") <= b)`
   - `LIKE '%pattern%'` → `.like("%pattern%")` or `F.col("x").like("%pattern%")`
7. Physical Tables vs DataFrame Variables:
   - If a table name is in the **Base Tables** list: You MUST read it using `spark.table("table_name")`.
   - If a table name is in the **DataFrame Variables (CTEs)** list: You MUST reference it directly as a Python variable (e.g., `variable_name.alias("t1")`). DO NOT use `spark.table()` for these.
   - Never call `spark.table()` on a DataFrame variable.
8. Column Naming & Expressions: DO NOT guess, rename, or approximate column names. If a Column Mapping section is provided above, follow it strictly — use TARGET column names for `F.col()` and alias back to SOURCE names. If no mapping is provided, use the EXACT column names as they appear in the original SQL. Use native PySpark string functions (like `F.substring("col", 1, 4)`) instead of `F.expr("left(col, 4)")` where possible.
9. Filter Optimization: Avoid redundant filters. If a filter condition is already logically applied to a base DataFrame, do not re-apply the exact same condition later when joining that DataFrame.
10. Filter Chaining: NEVER call `.filter()` directly on a Column object (e.g., `F.col("x").isin(...).filter(...)` is WRONG). Combine conditions using bitwise operators `&` and `|` inside a SINGLE `.filter()` call: `.filter((F.col("x").isin(...)) & (F.col("y") > 5))`.
11. DRY & Performance:
    - If a logic (like `F.when(...)`) is repeated across multiple columns, calculate a single intermediate column first or use an explicit join if it represents a mapping table.
    - If calculating the CURRENT YEAR or other static values, do not use `F.year(F.current_date())` inside `.select()` on every row. Assume a global Python variable `curr_yr = datetime.now().year` exists and use `F.lit(curr_yr)`.
    - Do NOT manually calculate averages like `F.sum(x) / F.sum(y)`. Always use `F.avg(x)`.
12. Maintainability: If there is a massive hardcoded list of IDs (e.g., IN ('id1', 'id2', '...')), pull them out into a well-named standard Python list parameter at the top of the generated code block (e.g., `TARGET_KPI_IDS = ['id1', 'id2']`) and use `.isin(TARGET_KPI_IDS)`.
13. Joins: Explicitly specify join types (e.g., `how="left"` or `how="inner"`) instead of relying purely on implicit logic.
14. Assign the final DataFrame to a variable named `{node_name}` (the CTE name). Do NOT leave assignment incomplete (e.g. `spark.table().groupBy().agg().select() as alias`).
15. If another CTE is referenced in FROM or JOIN, assume a DataFrame with that name already exists.
16. Do NOT add any `#` comments or explanatory text — no inline comments, no block comments, no docstrings. Output pure code only.
17. Do NOT include any `import` statements, `SparkSession` creation, or `.show()` / `.display()` calls — those are handled elsewhere in the notebook.

**Output format:**
- Return ONLY the executable Python code.
- No markdown fences (``` python ```), no explanations, no commentary, no `#` comments.
- Clean, readable formatting with consistent 4-space indentation.
"""
    refined_code = await api_call_with_retry_async('Gemini', prompt, task_type='sql', target=target)
    
    # 3. Clean the LLM output with robust post-processing
    if refined_code:
        refined_code = _clean_pyspark_output(refined_code)
    else:
        # LLM failed entirely — use the baseline
        refined_code = baseline_code
        
    return refined_code

def consolidated_sql_from_df(df: pd.DataFrame) -> str:
    """
    Consolidates SQL chunks from a DataFrame into a single, well-formatted SQL 
    statement using CTEs with properly placed comments.
    """
    # Filter for valid rows that contain 'select'
    valid_df = df[df['Chunk SQL Primary Optimized Base']
                  .str.contains('select', case=False, na=False)].copy()
    if valid_df.empty:
        return ""

    # Prepare and sort the DataFrame by chunk number
    valid_df['Chunk Number'] = pd.to_numeric(valid_df['Chunk Number'], errors='coerce')
    valid_df.dropna(subset=['Chunk Number'], inplace=True)
    valid_df = valid_df.sort_values('Chunk Number')
    valid_df['Cleaned SQL'] = valid_df['Chunk SQL Primary Optimized target'].str.rstrip(';: \t\n\r')

    # If there's only one SQL chunk, return it directly
    if len(valid_df) == 1:
        sql = valid_df.iloc[0]['Cleaned SQL']
        return sql + ";"

    # --- Build the CTEs part of the query ---
    ctes = []
    # Iterate over all rows except the last one to build CTEs
    for _, row in valid_df.iloc[:-1].iterrows():
        chunk_num = int(row['Chunk Number'])
        comment = str(row['Chunk SQL Primary Comments']).strip()
        node_name = row['Node name']
        sql = row['Cleaned SQL']
        
        # Build each CTE string with its comment on a preceding line
        cte_str = ""
        if comment:
            cte_str += f"-- Step {chunk_num}: {comment}\n"
        
        cte_str += f"{node_name} AS (\n{sql}\n)"
        ctes.append(cte_str)

    # Join the CTEs, starting with the WITH clause on a new line
    formatted_ctes = "WITH\n" + ",\n\n".join(ctes)

    # --- Build the final SELECT statement ---
    final_row = valid_df.iloc[-1]
    final_num = int(final_row['Chunk Number'])
    final_comment = str(final_row['Chunk SQL Primary Comments']).strip()
    final_sql = final_row['Cleaned SQL']

    final_query = ""
    if final_comment:
        # Add the comment for the final SELECT statement on its own line
        final_query += f"-- Step {final_num}: {final_comment}\n"
    
    final_query += final_sql

    # Combine the CTEs and the final query into a single statement
    full_sql = f"{formatted_ctes}\n\n{final_query};"

    return full_sql


def consolidated_sql_from_df_dsp(df: pd.DataFrame) -> str:
    # 1. Filter for valid rows that contain a 'select' statement in their base query.
    if 'Chunk SQL Primary Optimized Base' not in df.columns:
        return ""

    valid_df = df[df['Chunk SQL Primary Optimized Base'].str.contains('select', case=False, na=False)].copy()

    if valid_df.empty:
        return ""

    # 2. Convert 'Chunk Number' to numeric and sort
    valid_df['Chunk Number'] = pd.to_numeric(valid_df['Chunk Number'], errors='coerce')
    valid_df.dropna(subset=['Chunk Number'], inplace=True)
    valid_df = valid_df.sort_values('Chunk Number')

    if valid_df.empty:
        return ""

    # 3. Handle the simple case of a single SQL chunk.
    if len(valid_df) == 1:
        sql = valid_df.iloc[0]['Chunk SQL Primary Optimized target'].rstrip(';: \t\n\r')
        return f"return {sql};"

    # 4. Process multiple SQL chunks with comments
    statements = []
    for i, row in valid_df.iterrows():
        chunk_num = int(row['Chunk Number'])
        comment = row.get('Chunk SQL Primary Comments', '')
        sql = row['Chunk SQL Primary Optimized target'].rstrip(';: \t\n\r')

        # Add comment line if more than one chunk exists
        if comment:
            statements.append(f"-- Step {chunk_num}: {comment}")

        if i != valid_df.index[-1]:  # Not the last row
            node_name = row['Node name']
            statements.append(f"{node_name} = {sql};")
        else:  # Last row
            statements.append(f"return {sql};")

    # 5. Join with spacing
    return "\n\n".join(statements)




async def update_target_sql(row: pd.Series, database_name: str, target: str = None) -> str:
    """Generate the 'twice' version of SQL if not blank."""
    sql_base = row["Chunk SQL Primary Optimized Base"]
    sql_base = format_other_query_intent(sql_base)
    mapping_fields = row.get("Matched_Mapping", {})
    
    # logger.info("Issue")
    if database_name != 'datasphere':
        find_sql_functions = await api_call_with_retry_async('Gemini', f"Find all SQL functions used in this SQL: {sql_base}", task_type='sql', target=target)
        
        get_new_func_prompt = f"Given the following SQL functions used in a SQL query: {find_sql_functions}, provide their equivalent functions in {database_name} if they exist. If a function does not have an equivalent, respond with 'N/A'. Return the results in JSON format with 'original_function' and 'equivalent_function' keys and explanation."
        
        new_sql_functions = await api_call_with_retry_async('Gemini', get_new_func_prompt, task_type='sql', target=target) # fix flash'
        
    
    if database_name == 'datasphere':
        find_sql_functions = await api_call_with_retry_async(
            'Gemini', 
            f"""
            Find all SQL functions used in the following SQL query: {sql_base}.
            Additionally, I have prepared a generic list of functions: {bq_to_hana_functions}.
            If any functions from this list are found in the SQL, add more details from this JSON to the output.
            If none of these functions are found, return only the other functions used in the SQL.
            """,
            task_type='sql',
            target=target
        )

        get_new_func_prompt = f"Given the following SQL functions used in a SQL query: {find_sql_functions}, provide their equivalent functions in {database_name} if they exist. If a function does not have an equivalent, respond with 'N/A'. Return the results in JSON format with 'original_function' and 'equivalent_function' keys and explanation."
        new_sql_functions = await api_call_with_retry_async('Gemini', get_new_func_prompt, task_type='sql', target=target) # fix flash

    # print("Table alias new:", table_alias_new)
    # logger.info(f"database_name:{database_name}, api call suceess {new_sql_functions}")

    # Database specific Qualify and GROUP BY
    additional_prompt = ""
    additional_prompt_check = check_sql_patterns_additional_prompt(sql_base, database_name)
    if additional_prompt_check:
        additional_prompt = additional_prompt_check


    if database_name != 'datasphere':
        table_alias_finder_prompt =f""" Extract all table names from the following SQL (they usually appear after FROM or JOIN clauses) and suggest shorter aliases (maximum 2 characters). 
        Never add SQL Keywords like AS, ON..etc as Alias name. It is erroneous.
        Return the results in JSON format with keys 'table_name' and 'alias'. SQL: {sql_base}"""
        
        table_alias_new = await api_call_with_retry_async("Gemini", table_alias_finder_prompt, task_type='sql', target=target)


    if not (pd.notna(sql_base) and sql_base.strip()):
        return

    target_db = database_name.lower()

    # Map fabric to azure (frontend renamed platform but backend still uses azure)
    if target_db == 'fabric':
        target_db = 'azure'

    prompt = ""  # Initialize to avoid UnboundLocalError

    if not mapping_fields:
        if target_db == 'bigquery':
            prompt = f"""
    I have a BigQuery SQL query that needs to be formatted.
    Please format the following query while strictly adhering to these rules:
    - Do not change the original query logic.
    - Do not change field alias names.
    - Keep the original table names unchanged, 
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
        Adjust table aliases in fields, e.g., projection.name -> p1.name
    - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).
    - Do not create any CTEs .
    - Do not use double quotes for table or field names.
    - The output must be only the formatted SQL query.
    - The SELECT statement must be the first line of the output.
    - Return only SQL (SELECT...) without any comments.

    Original SQL: {sql_base}
    """
            



        elif target_db == 'datasphere':
            table_alias_finder_prompt =f""" Extract all table names from the following SQL (they usually appear after FROM or JOIN clauses) and suggest shorter aliases (maximum 2 characters). 
            Never add SQL Keywords like AS, ON..etc as Alias name. It is erroneous.
            1. Do NOT use SQL keywords (e.g., AS, ON, JOIN, etc.) as alias names.  
            Return the results in JSON format with keys 'table_name' and 'alias'. 
            table_name in JSON must be with colon(:). Because table name prefixed with colon is standard practice in Datasphere.
            E.g.    :table1 as t1
            
            SQL: {sql_base}

            Alias names AS, ON, etc are not allowd"""
    
            table_alias_new = await api_call_with_retry_async("Gemini", table_alias_finder_prompt, task_type='sql', target=target)

            prompt = f"""
    Convert the following BigQuery SQL query to SAP Datasphere SQL.
    Focus on converting BigQuery-specific functions and structures:
    - {new_sql_functions}

    Strictly adhere to these rules:
    - Do not change the original query logic.
    - Avoid datetime parsing, Datasphere will internally handle it.
    - Do not change field alias names.
    - Keep the original table names unchanged.
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
    - Adjust table aliases in all field references (e.g., projection.name -> p1.name).
    - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).
    - Do not create any CTEs .

    - Datatype and formula must be converted HANASQL supported datatypes and formula. If any type casting and formula used, take extra care for conversion.

    - Do not use double quotes for alaised table or field names.
    - The SELECT statement must be the first line of the output.
    - Return only SQL (SELECT...) without any comments.
    - Keep colon before to all table names ( FROM / JOIN). It is one timeactivity. Later this table will be aliased with shorter name.
     (E.g) 1. select name from :users
           2. select c.id, a.name from :customer as c left join :area as a on c.country = a.country
    - If field and its alias are same, do not force alias. Keep as it is.
    - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in SAP Datasphere: {new_sql_functions}
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}

    Original SQL: {sql_base}
    """
        elif target_db == 'azure':
            prompt = f"""
    Convert the following BigQuery SQL query to Azure SQL (Synapse).
    Focus on converting BigQuery-specific functions and structures:
    - {new_sql_functions}

    Strictly adhere to these rules:
    - Do not change the original query logic.
    - Do not change field alias names.
    - Keep the original table names unchanged.(must be with colon(:))
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
    - Adjust table aliases in all field references (e.g., projection.name -> p1.name).
    - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).
    - Do not create any CTEs .
    - Do not use double quotes for table or field names.
    - The SELECT statement must be the first line of the output.
    - Return only SQL (SELECT...) without any comments.
    - If field and its alias are same, do not force alias. Keep as it is.
    - There are many Bigquery supported functions and datatypes not supported in Synapse. If any type casting and formula used, take extra care for conversion.
    - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in Synapse: {new_sql_functions}
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
    
      Original SQL: {sql_base}
    """
        elif target_db == 'redshift':
            prompt = f"""
    Convert the following BigQuery SQL query to Amazon Redshift SQL.
    Focus on converting BigQuery-specific functions and structures:
    {new_sql_functions}

    Strictly adhere to these rules:
    - Do not change the original query logic.
    - Do not change field alias names.
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
    - Adjust table aliases in all field references (e.g., projection.name -> p1.name).
    - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).
    - Do not create any CTEs .
    - Do not use double quotes for table or field names.
    - The SELECT statement must be the first line of the output.
    - Return only SQL (SELECT...) without any comments.
    - There are many Bigquery supported functions and datatypes not supported in Redshift. If any type casting and formula used, take extra care for conversion.
    - If field and its alias are same, do not force alias. Keep as it is.
    - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in Redshift: {new_sql_functions}
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}

    Original SQL: {sql_base}
    """
        elif target_db in ['snowflake', 'databricks']:  # Added 'databricks' here
            prompt = f"""
    Convert the following BigQuery SQL query to {database_name} SQL.
    Focus on converting BigQuery-specific functions and structures: (Mainly date/time columns)
    {new_sql_functions}

    Strictly adhere to these rules:
    - Do not change the original query logic.
    - Do not change field alias names.
    - Keep the original table names unchanged.
    - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
        
    - Adjust table aliases in all field references (e.g., projection.name -> p1.name).
    - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).
    - Do not create any CTEs .
    - Do not use double quotes for table or field names.
    - The SELECT statement must be the first line of the output.
    - Return only SQL (SELECT...) without any comments.
    - If field and its alias are same, do not force alias. Keep as it is.
    - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in {database_name}: {new_sql_functions}
    - **Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}

    Original SQL: {sql_base}
    """


    else:
        # logger.info(f"Mapping fields for available")
        mapping = generate_column_mappings(mapping_fields)
        # logger.info(f"Mapping fields: {mapping_fields}")
        # logger.info(f"Mapping : {mapping}")
        base_tables = extract_target_tables(mapping_fields)
        # logger.info(f"Base tables: {base_tables}")

        # Transform mapping_fields into the format expected by refactor_sql
        sqlglot_mappings = _transform_mapping_for_sqlglot(mapping_fields)
        manually_converted_sql = refactor_sql(sql_base, sqlglot_mappings, dialect="bigquery")
        # logger.info(f"""manually converted sql: {manually_converted_sql}""")
        
        # 1. BigQuery
        if target_db == 'bigquery':
            prompt = f"""
        I have a BigQuery SQL query that needs to be formatted and have its tables and columns remapped.
        Use the provided mapping information to replace fields and tables in the SQL query.

        Mapping info: {mapping_fields}
        Columns to alias: {mapping}

        Notes on mapping info:
        - Mapping info may contain target table names in one of the following formats: `projectid.dataset.tableName`, `dataset.tableName`, or `tableName`.
        - Tables listed in mapping info must be treated as **fully qualified tables** in FROM/JOIN with backticks and shortened aliases (e.g., `projectid.dataset.salesorder` AS s1).

        Strictly adhere to these rules:
        - Do not change the original query logic.
        - Maintain original aliases for columns that were already explicitly aliased (e.g., `customer.fullname AS name` should keep the alias `name`). 
        - Do not create any new CTEs .
        - The output must be only the formatted SQL query.
        - The SELECT statement must be the first line of the output.
        - Keep the original field sequence.
        - Return only SQL (SELECT ...) without any special comments.


        Example mapping:
        [
        "sourceField": "price",
        "sourceTable": "salesorder",
        "targetField": "z_price",
        "targetTable": `projectid.dataset.salesorder`
        ]
        SQL snippet after mapping:
        SELECT s1.z_price AS price, c1.region
        FROM `projectid.dataset.salesorder` AS s1
        LEFT JOIN customer c1 ON s1.name = c1.name
        -- Here salesorder is a fully qualified table from mapping info; customer is a non-mapping table / CTE.

        I manually mapped old columns to new columns with mapping. Below is the SQL for alias reference. Use the reference column alias:
        {manually_converted_sql}

        Original SQL: {sql_base}


        - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).
        - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
        - ** My base tables are : {base_tables}. Others are CTEs or non-mapping tables.
        """

        # 2. SAP Datasphere
        elif target_db == 'datasphere':
            
            mapping = generate_column_mappings_dsp(mapping_fields)

            base_tables_dsp = extract_base_tables_dsp(mapping_fields)
            # print(base_tables_dsp)
            mapping_fields = format_mapping_fields_to_json(mapping_fields)


            # logger.info(f"Mapping fields: {mapping_fields}")
            # logger.info(f"Mapping : {mapping}")

            table_alias_finder_prompt = f"""
            Extract all table names from the following SQL (they usually appear after FROM or JOIN clauses) and suggest shorter aliases (maximum 2 characters).

            Rules:
            1. Do NOT use SQL keywords (e.g., AS, ON, JOIN, etc.) as alias names.  
            2. Tables present in the base tables list {base_tables_dsp} must be wrapped in double quotes.  
            3. Tables NOT in the base tables list must be prefixed with a colon (:) and should not be wrapped in double quotes. 

            Example:  
            Base tables list = [Table1, Table2]  
            SQL contains: Table1, Table3  
            Output: "Table1" as t1, :Table3 as t2  

            Return the results strictly in JSON format with the following keys:  
            - 'table_name'  
            - 'alias'  

            - Alias names ON and AS, etc keywords not allowed
            SQL: {sql_base}
            """

    
            table_alias_new = await api_call_with_retry_async("Gemini", table_alias_finder_prompt, task_type='sql', target=target)





            prompt = f"""
        Convert the following BigQuery SQL query to SAP Datasphere SQL and apply the provided column mappings.
        Use the mapping info to replace fields and tables in the query.

        Mapping info: {mapping_fields}
        Columns to alias: {mapping}

        Strictly adhere to these rules:
        - Do not change the original query logic.
        - Mapping info may contain target table names in one of the following formats: "space"."tableName", "tableName".
        - All column names must be prefixed with table alias names without double quotes.
        - If all columns in **Mapping Info** must be aliased . All source_Fields in the *Mapping Info* must be with double quotes and case sensitive. But their alias must not be with double quotes.
        - Maintain original aliases for columns that were already explicitly aliased in Original (e.g., "fullname" AS name should keep the alias name). 
        - Fully qualified tables and fields name must have double quotes "" as datasphere SQL standard. but their field alais/ table alias names must not be in double quotes.
        - Avoid datetime parsing, Datasphere will internally handle it. 

        - Handle BigQuery-specific functions  with Datasphere equivalents.{new_sql_functions}
        - Datatype and formula must be converted HANASQL supported datatypes and formula. If any type casting and formula used, take extra care for conversion.
        - Do not create any CTEs .
        - Output must be only the converted SQL query.
        - SELECT statement must be the first line of the output.

        - **Keep colon(:) before to table for tables not maintained in Mapping Info ( FROM / JOIN). Other base tables must not have colon(:) but have double quotes as they are database tables.
        (E.g) 1. select c."id", a."name" from "customer" as c left join :area as a on c.country = a.country 
            Here customer is a database tble. So It is with " " and area is not part of Mapping Info. So it is with colon(:). But table alias name is nowhere with double quotes.
        - All fields must be with double quoation (").

        Example mapping:
        [
        "sourceField": "price",
        "sourceTable": "salesorder",
        "targetField": "Z_price",
        "targetTable": "dsp"."salesorder"
        ]
        SQL snippet after mapping: 

        SELECT s1."Z_price" AS price, c1.region
        FROM "dsp"."salesorder" AS s1
        LEFT JOIN :customer c1 ON s1.name = c1.name
        **Important note: Here s1 and c1 are plain without double quotes.

        -- Here "salesorder" is a fully qualified table from mapping info; customer is a non-mapping table. So it hs colon.
        -- Fully qualified tables and fields name must have double quotes "" as datasphere SQL standard.
        -- Target field must be in the same case of field mintained Mapping Info.(E.g) NAME -> NAME, Name -> Name, name -> name.

        Original SQL: {sql_base}
        - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in SAP Datasphere: {new_sql_functions}

        - **Target field must be in the same case of field mintained Mapping Info.(E.g) NAME -> NAME, Name -> Name, name -> name. Because Datasphere is case sensitive.
        - **There are many Bigquery supported functions and datatypes not supported in Datasphere. If any type casting and formula used, take extra care for conversion.
        - ** My base tables are : {base_tables_dsp}. Others are CTEs or non-mapping tables.
        - - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}. These alaised name must not be with double quotes("). They are plain. 
        -   Focus on function handling and table and column alias accoring to rules.
        """

        # 3. Azure Synapse
        elif target_db == 'azure':
            prompt = f"""
        Convert the following BigQuery SQL query to Azure SQL (Synapse) and apply the provided column mappings.
        Use the mapping info to replace fields and tables in the query.

        Mapping info: {mapping_fields}
        Columns to alias: {mapping}

        Strictly adhere to these rules:
        - Mapping info may contain target table names in one of the following formats: `database.schema.tableName`, `schema.tableName`, or `tableName`.
        - Do not change the original query logic.
        - Maintain original aliases for columns that were already explicitly aliased (e.g., customer.fullname AS name should keep the alias name). If a column was not explicitly aliased or its alias was the same as its name, use the original column name as the alias for the new mapped column.
        - If a column is remapped, use targetField AS sourceField (e.g., z_price AS price).
        - Handle BigQuery-specific functions to Azure equivalents.{new_sql_functions}
        - Do not create any CTEs .
        - Output must be only the converted SQL query.
        - SELECT statement must be the first line of the output.
        - Use square brackets for table and column names where necessary.

        Example mapping:
        [
        "sourceField": "price",
        "sourceTable": "salesorder",
        "targetField": "z_price",
        "targetTable": "SalesDB.dbo.salesorder"
        ]
        SQL snippet after mapping: 
        SELECT s1.z_price AS price, c1.region
        FROM SalesDB.dbo.salesorder AS s1
        LEFT JOIN customer c1 ON s1.name = c1.name
        -- Here dbo.salesorder is a fully qualified table from mapping info; customer is a non-mapping table / CTE.

        I manually mapped old columns to new columns with mapping. Below is the SQL for alias reference. Use the reference column alias:
        {manually_converted_sql}

        Original SQL: {sql_base}
        - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in Synapse: {new_sql_functions}
        - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).
        - There are many Bigquery supported functions and datatypes not supported in Synapse. If any type casting and formula used, take extra care for conversion.
        - My base tables are : {base_tables}. Others are CTEs or non-mapping tables.
        -- Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
        """

        # 4. Amazon Redshift
        elif target_db == 'redshift':
            prompt = f"""
        Convert the following BigQuery SQL query to Amazon Redshift SQL and apply the provided column mappings.
        Use the mapping info to replace fields and tables in the query.

        Mapping info: {mapping_fields}
        Columns to alias: {mapping}

        Strictly adhere to these rules:
        - Do not change the original query logic.
        - Mapping info may contain target table names in one of the following formats: `schmema.tableName` or `tableName`.
        - Maintain original aliases for columns that were already explicitly aliased (e.g., customer.fullname AS name should keep the alias name). If a column was not explicitly aliased or its alias was the same as its name, use the original column name as the alias for the new mapped column.
        - If a column is remapped, use targetField AS sourceField (e.g., z_price AS price).
        - Handle BigQuery-specific functions to equivalent functions:{new_sql_functions}
        - Do not create any CTEs .
        - Output must be only the converted SQL query.
        - SELECT statement must be the first line of the output.
        - Quotes are needed for table and column names.

        Example mapping:
        [
        "sourceField": "price",
        "sourceTable": "salesorder",
        "targetField": "z_price",
        "targetTable": "dev.public.salesorder"
        ]
        SQL snippet after mapping: 
        SELECT s1.z_price AS price, c1.region
        FROM public.salesorder AS s1
        LEFT JOIN customer c1 ON s1.name = c1.name
        -- Here salesorder is a fully qualified table from mapping info; customer is a non-mapping table / CTE.

        I manually mapped old columns to new columns with mapping. Below is the SQL for alias reference. Use the reference column alias:
        {manually_converted_sql}

        Original SQL: {sql_base}

        - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in Redshift: {new_sql_functions}

        - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).

        - There are many Bigquery supported functions and datatypes not supported in Redshift. If any type casting and formula used, take extra care for conversion.
        - My base tables(fully qualified tables) are : {base_tables}.Others are CTEs or non-mapping tables.
        - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
        """

        # 5. Snowflake and Databricks
        elif target_db in ['snowflake', 'databricks']:  # Added 'databricks' here
            prompt = f"""
        Convert the following BigQuery SQL query to {database_name} SQL and apply the provided column mappings.
        Use the mapping info to replace fields and tables in the query.

        Mapping info: {mapping_fields}
        Columns to alias: {mapping}

        Strictly adhere to these rules:
        - Do not change the original query logic.
        - Mapping info may contain target table names in one of the following formats: `database.schmea.tableName`, `schema.tableName`, or `tableName`.
        - Maintain original aliases for columns that were already explicitly aliased (e.g., customer.fullname AS name should keep the alias name). If a column was not explicitly aliased or its alias was the same as its name, use the original column name as the alias for the new mapped column.
        - If a column is remapped, use targetField AS sourceField (e.g., z_price AS price).
        - Handle BigQuery-specific functions to equivalent functions: {new_sql_functions}
        - Do not create any CTEs .
        - Output must be only the converted SQL query.
        - SELECT statement must be the first line of the output.
       

        Example mapping:
        [
        "sourceField": "price",
        "sourceTable": "salesorder",
        "targetField": "z_price",
        "targetTable": "ANALYTICS.PUBLIC.salesorder"
        ]
        SQL snippet after mapping: 
        SELECT s1.z_price AS price, c1.region
        FROM PUBLIC.salesorder AS s1
        LEFT JOIN customer c1 ON s1.name = c1.name
        -- Here s1 is a fully qualified table from mapping info; c1 is a non-mapping table / CTE.
        
        I manually mapped old columns to new columns with mapping. Below is the SQL for alias reference. Use the reference column alias:
        {manually_converted_sql}

        Original SQL: {sql_base}

        - If any functions used in SQL, take extra care for conversion. Here are the functions used in SQL and their equivalent functions in {database_name}: {new_sql_functions}

        - ** If a field name and its alias are the same, do not force an alias. For example:
        ❌ SELECT name AS name from table
        ✅ SELECT name from table (without alias because they are same).

        - My base tables are : {base_tables}. Others are CTEs or non-mapping tables.
        - Shrink all table names with short table alais names. Here are the table names and their aliases: {table_alias_new}
        - ** Never use double quotes(") for table names and field names..This is not acceptable in Snowflake.
        """

    prompt += f"If any WHERE/GROUP/Joining condtion present in BQ SQL, make sure you return complete {database_name} sql. Incomplete WHERE/GROUP/Joining condtion will return error when I execute your output sql in {database_name}\n"
    
    prompt += additional_prompt

    any_datatype_mapping = map_datatypes_in_sql(sql_base, database_name)
    if any_datatype_mapping != "[]":  # Check if not empty array
        prompt += f"""
    There are some DataType conversions from BigQuery to {database_name} happening. Please follow these mappings:
    {any_datatype_mapping}

    Ensure you use the appropriate {database_name} data types in the generated SQL instead of BigQuery data types.
    """
        
    if "rank() over" in sql_base.lower():
        prompt += (
            "\nThe RANK function cannot be used directly in the WHERE clause. "
            "Instead, wrap your query in a subquery, apply RANK there, and then filter on the result."
        )

    
    import re

    # Regex to match aggregate functions without alias (case-insensitive)
    pattern_aggr = r"((sum|avg|min|max)\s*\([^\)]+\))(?!\s+as\s+\w+)"
    matches_aggr = re.findall(pattern_aggr, sql_base, flags=re.IGNORECASE)

    if matches_aggr:
        # Extract the full function calls without alias (preserving original case)
        missing_alias_fields_aggr = [match[0] for match in matches_aggr]
        fields_str_aggr = ", ".join(missing_alias_fields_aggr)
        
        prompt += (
            f"\nThe following aggregate fields are missing aliases: {fields_str_aggr}. "
            "Always add an alias for these fields. "
            "For example, `SUM(salary)` should be written as `SUM(salary) AS salary`. "
            "If an alias is already provided, keep it as is."
        )


    prompt = f"""You are a {database_name} SQL generator. Return only pure sql without comments and ending with semicolon". Do not exclude any columns that are present in the original Bigquery SQL query.
            {prompt}"""

    attempt = 0
    max_attempts = 10  # Set a limit for retries

    while attempt < max_attempts:
        full_prompt = prompt.strip()
        sql_text = await api_call_with_retry_async('Gemini', full_prompt, task_type='sql', target=target)
        prompt = ""

        if attempt > 2:
            print(f"SQLTEXT:--------------{sql_text}")
        # Clean SQL
        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        optimized_sql = "\n".join(cleaned_lines)
        generated_sql = optimized_sql.strip()



        # If mapping fields are required, check them
        valid, msg = False, ""  # Initialize to avoid UnboundLocalError
        if mapping_fields:
            mapping_alias = await has_mapping_alias(mapping_fields, generated_sql, target=target)
            if not mapping_alias:
                prompt = f"""Generated SQL does not contain any mapping alias from the provided mapping fields.
                Mapping fields: {mapping_fields}.
                SQL query: {generated_sql}"""

            else:
                # Validate SQL properly
                valid, msg = is_valid_sql(generated_sql)

                if valid:
                    generated_sql = format_other_query_intent(generated_sql)
                    return generated_sql
                else:
                    prompt = prompt + (
                        f"\nThe previous attempt failed to generate valid SQL. "
                        f"Please retry with the same prompt ensuring the SQL is valid. "
                        f"Previous attempt: {generated_sql} (Error: {msg})"
                    )

        else:
            # No mapping required → just validate SQL
            valid, msg = is_valid_sql(generated_sql)
            if valid:
        
                generated_sql = format_other_query_intent(generated_sql)
                return generated_sql
            else:
                prompt = prompt + (
                    f"\nThe previous attempt failed to generate valid SQL. "
                    f"Please retry with the same prompt ensuring the SQL is valid. "
                    f"Previous attempt: {generated_sql} (Error: {msg})"
                )
               

    
        fix_prompt = f"""I have a Bigquery SQl:{sql_base}. I generated {database_name} SQL:{generated_sql}. I found one of errors is {msg}. 
        Give suggestion on how to fix it."""
        fix_gemini = await api_call_with_retry_async("Gemini", fix_prompt, task_type="sql", target=target)

        print(f"Valid:{valid}Error{msg}")
        print(f"Generated sql:{generated_sql}")
        print(f"_-----------------attempt:{attempt}")

        prompt += fix_gemini


    attempt += 1


    generated_sql = format_other_query_intent(generated_sql)
    return generated_sql


    
async def has_mapping_alias(mapping_fields, sql_text, target: str = None):
    prompt = f"""
    Check whether the SQL query uses any alias corresponding to the provided mapping fields. 
    Note: Sometimes these fields may not appear in the SELECT statement but can exist in JOIN conditions. (In this case return TRUE)
    
    Mapping fields: {mapping_fields}  
    SQL query: {sql_text}  
    
    Return True if any mapping alias is found in the SQL query, otherwise return False.
    """
    response = await api_call_with_retry_async("Gemini", prompt, task_type="sql", target=target)
    return "true" in response.strip().lower()



def is_sql_query(sql: str) -> bool:
    try:
        parsed = sqlparse.parse(sql)
        return len(parsed) > 0
    except Exception:
        return False



def generate_column_mappings(mappings):
    result = []
    for mapping in mappings:
        for col in mapping["columns"]:
            source = col["sourceColumn"]
            target = col["targetColumn"]
            if source != target:
                result.append(f"{target} AS {source}")
            # else:
            #     result.append(source)
    return ", ".join(result)

def generate_column_mappings_dsp(mappings):
    result = []
    for mapping in mappings:
        for col in mapping["columns"]:
            source = col["sourceColumn"]
            target = col["targetColumn"]
            # Always include alias, and quote the target
            result.append(f'"{target}" AS {source}')
    return ", ".join(result)

def _transform_mapping_for_sqlglot(mapping_fields: list[dict]) -> list[dict]:
    """
    Transforms the mapping_fields structure into the format expected by refactor_sql.
    """
    transformed_mappings = []
    for table_mapping in mapping_fields:
        source_table = table_mapping["sourceTable"]
        target_table = table_mapping["targetTable"]
        for col_mapping in table_mapping["columns"]:
            transformed_mappings.append({
                "old_table": source_table,
                "old_col": col_mapping["sourceColumn"],
                "new_table": target_table,
                "new_col": col_mapping["targetColumn"]
            })
    return transformed_mappings

def _process_sql_expression(expression: exp.Expression, mappings: list[dict], dialect: str, schema: dict) -> exp.Expression:
    """
    Helper function to qualify and transform a single SQL expression.
    Renames columns and tables based on mappings, but only adds aliases
    if the output name differs from the original.
    """
    table_map = {}
    col_map = {}
    for m in mappings:
        old_table = m["old_table"]
        old_col = m["old_col"]
        new_table = m["new_table"]
        new_col = m["new_col"]

        table_map[old_table] = new_table
        col_map[(old_table, old_col)] = new_col

    # Map aliases to their original table names
    alias_to_old_table = {
        t.alias_or_name: t.this.name
        for t in expression.find_all(exp.Table)
    }

    # Qualify the expression
    qualified_expression = expression.qualify(schema=schema) if hasattr(expression, "qualify") else expression

    # Iterate through SELECT expressions and rename columns
    for select_expr in qualified_expression.find_all(exp.Column):
        table_name = select_expr.table or alias_to_old_table.get(select_expr.table)
        old_col_name = select_expr.name
        new_col_name = col_map.get((table_name, old_col_name), old_col_name)

        # Only add AS alias if the column name actually changes
        if old_col_name != new_col_name:
            select_expr.replace(exp.alias_(select_expr, new_col_name))

    # Rename tables
    for table in qualified_expression.find_all(exp.Table):
        table_name = table.name
        new_table_name = table_map.get(table_name, table_name)
        table.set("this", exp.to_identifier(new_table_name))

    return qualified_expression


    def transformer(node):
        # Rename Tables
        if isinstance(node, exp.Table):
            old_table_name = node.this.name
            if old_table_name in table_map:
                new_table_name = table_map[old_table_name]
                node.set('this', exp.to_identifier(new_table_name))
            return node

        # Rename Columns
        if isinstance(node, exp.Column):
            table_alias = node.table
            old_col_name = node.this.name

            original_table = alias_to_old_table.get(table_alias)

            if original_table and (original_table, old_col_name) in col_map:
                new_col_name = col_map[(original_table, old_col_name)]
                node.set('this', exp.to_identifier(new_col_name))

                parent = node.parent
                if isinstance(parent, exp.Select) and new_col_name != old_col_name:
                    return node.as_(old_col_name)

        return node

    return qualified_expression.transform(transformer)


def refactor_sql(sql_query: str, mappings: list[dict], dialect: str = "bigquery") -> str:
    """
    Refactors a SQL query by renaming tables and columns based on provided mappings.
    This function will never raise an error; if anything fails, it returns an empty string.

    Args:
        sql_query: The SQL query string to refactor.
        mappings: A list of dictionaries, each defining a mapping from an
                  old table/column to a new table/column.
        dialect: The SQL dialect to use for parsing and generation.

    Returns:
        The refactored SQL query string, or empty string if parsing/refactoring fails.
    """
    import sqlglot
    from sqlglot import exp
    import logging

    logger = logging.getLogger(__name__)

    try:
        schema = {}
        for m in mappings:
            old_table = m.get("old_table")
            old_col = m.get("old_col")
            if old_table:
                schema.setdefault(old_table, {})[old_col] = "UNKNOWN"

        parsed = sqlglot.parse_one(sql_query, read=dialect)
        
        def safe_process(expr):
            try:
                return _process_sql_expression(expr, mappings, dialect, schema)
            except Exception as e:
                logger.warning(f"Failed to process expression: {e}")
                return expr  # fallback to original expression

        if isinstance(parsed, exp.Union):
            all_expressions = [parsed.this] + list(parsed.expressions)
            transformed_expressions = [safe_process(expr) for expr in all_expressions]

            if len(transformed_expressions) == 1:
                refactored_tree = transformed_expressions[0]
            else:
                try:
                    refactored_tree = exp.Union(
                        this=transformed_expressions[0],
                        expressions=transformed_expressions[1:]
                    )
                except Exception as e:
                    logger.warning(f"Failed to rebuild Union: {e}")
                    return ""  # return empty if Union rebuild fails
        else:
            refactored_tree = safe_process(parsed)

        return refactored_tree.sql(dialect=dialect, pretty=True)
    
    except Exception as e:
        logger.warning(f"Failed to parse or refactor SQL: {e}")
        return ""  # return empty on any failure


def extract_target_tables(json_data):
    """
    Extracts target tables from the given JSON mapping.
    
    Args:
        json_data (list or str): JSON list or string containing mapping dictionaries.
    
    Returns:
        list: List of target table names.
    """
    # If it's a JSON string, parse it
    if isinstance(json_data, str):
        json_data = json.loads(json_data)
    
    return [item.get("targetTable") for item in json_data if "targetTable" in item]

def map_datatypes_in_sql(sql, database_name):
    """
    Finds BigQuery data types in SQL and maps them to the equivalent in the target database.
    
    Args:
        sql (str): SQL query string
        database_name (str): Target database name (snowflake, redshift, azure, datasphere, HANA)
        
    Returns:
        str: JSON string with mapping information, or empty list if no matches found
    """
    # Convert database name to lowercase for case-insensitive matching
    db_name_lower = database_name.lower()
    
    # Find all potential data type matches in the SQL (case-sensitive)
    matches = []
    
    # Check each BigQuery data type in the SQL
    for bq_type in datatype_mapping.keys():
        # Use regex to find the exact word (case-sensitive)
        pattern = r'\b' + re.escape(bq_type) + r'\b'
        if re.search(pattern, sql):
            if db_name_lower in datatype_mapping[bq_type]:
                target_type = datatype_mapping[bq_type][db_name_lower]
                matches.append({
                    "bigquery_datatype": bq_type,
                    f"{database_name}_datatype": target_type,
                    "message": f"{database_name} datatype: {target_type} is equivalent to BigQuery {bq_type}. So use {target_type}."
                })
            else:
                # Database not supported for this data type
                pass
    
    if matches:
        return json.dumps(matches, indent=4)
    else:
        return "[]"

datatype_mapping = {
    "INT64": {
        "snowflake": "BIGINT",
        "redshift": "BIGINT",
        "azure": "BIGINT",
        "HANA": "BIGINT",
        "databricks": "BIGINT"
    },
    "FLOAT64": {
        "snowflake": "FLOAT",
        "redshift": "DOUBLE PRECISION",
        "azure": "FLOAT",
        "HANA": "DOUBLE",
        "databricks": "DOUBLE"
    },
    "NUMERIC": {
        "snowflake": "NUMBER",
        "redshift": "DECIMAL",
        "azure": "DECIMAL",
        "datasphere": "DECIMAL",
        "databricks": "DECIMAL"
    },
    "BIGNUMERIC": {
        "snowflake": "NUMBER",
        "redshift": "DECIMAL",
        "azure": "DECIMAL",
        "datasphere": "DECIMAL",
        "databricks": "DECIMAL"
    },
    "BOOL": {
        "snowflake": "BOOLEAN",
        "redshift": "BOOLEAN",
        "azure": "BIT",
        "datasphere": "BOOLEAN",
        "databricks": "BOOLEAN"
    },
    "STRING": {
        "snowflake": "VARCHAR",
        "redshift": "VARCHAR",
        "azure": "NVARCHAR",
        "datasphere": "NVARCHAR",
        "databricks": "STRING"
    },
    "DATE": {
        "snowflake": "DATE",
        "redshift": "DATE",
        "azure": "DATE",
        "datasphere": "DATE",
        "databricks": "DATE"
    },
    "TIME": {
        "snowflake": "TIME",
        "redshift": "TIME",
        "azure": "TIME",
        "datasphere": "TIME",
        "databricks": "TIME"
    },
    "DATETIME": {
        "snowflake": "TIMESTAMP_NTZ",
        "redshift": "TIMESTAMP WITHOUT TIME ZONE",
        "azure": "DATETIME2",
        "datasphere": "SECONDDATE",
        "databricks": "TIMESTAMP_NTZ"
    },
    "TIMESTAMP": {
        "snowflake": "TIMESTAMP_NTZ",
        "redshift": "TIMESTAMP",
        "azure": "DATETIMEOFFSET",
        "datasphere": "TIMESTAMP",
        "databricks": "TIMESTAMP_NTZ"
    }
}






def check_sql_patterns_additional_prompt(sql_base, database_name):
    """
    Checks for specific SQL patterns and returns corresponding database-specific tips,
    including a generic tip for other databases.
    """
    additional_prompts = []
    db_name_lower = database_name.lower()

    # Check for GROUP BY pattern (case-insensitive)
    group_by_pattern = r'\bgroup\s+by\b'
    if re.search(group_by_pattern, sql_base, re.IGNORECASE):
        if db_name_lower in ('snowflake', 'bigquery'):
            group_by_tip = f"""
            Additional TIP for GROUP BY function in {database_name}:
            In {database_name}, aliases ARE allowed in the GROUP BY clause. You do not need to repeat the entire expression;
            you can simply use the alias defined in the SELECT clause.

            Example:
                SELECT LEFT(product_id, 3) AS product_category,
                       CONCAT(firstname, secondname) as fullname
                    COUNT(*) AS total_sales
                FROM sales
                GROUP BY product_category, fullname;
            """
            additional_prompts.append(group_by_tip)
        else:
            # Generic tip for other databases where aliases are not allowed
            generic_group_by_tip = f"""
            Additional TIP for GROUP BY function in {database_name}:
            In {database_name} and many other SQL dialects, aliases are generally NOT allowed in the GROUP BY clause.
            You must repeat the full expression instead of using the alias defined in the SELECT clause.

            Example:
                SELECT LEFT(product_id, 3) AS product_category,
                CONCAT(firstname, secondname) as fullname
                    COUNT(*) AS total_sales
                FROM sales
                GROUP BY LEFT(product_id, 3), 
                         CONCAT(firstname, secondname);
            """
            additional_prompts.append(generic_group_by_tip)

    # Check for RANK pattern (case-insensitive)
    rank_pattern = r'\brank\s*\(\s*\)\s*over\s*\('
    if re.search(rank_pattern, sql_base, re.IGNORECASE) and db_name_lower in ('snowflake', 'bigquery'):
        rank_tip = f"""
        Additional TIP for RANK function in {database_name}:
        In {database_name}, instead of wrapping a RANK() query inside a subquery,
        you can use the QUALIFY clause directly to filter the results.

        Example:
            SELECT customer_id,
                order_date,
                RANK() OVER (PARTITION BY customer_id ORDER BY order_date DESC) AS rnk
            FROM orders
            QUALIFY rnk = 1;

        This avoids the need for an extra subquery to filter on window function results.
        """
        additional_prompts.append(rank_tip)
    
    return "\n".join(additional_prompts).strip()






def extract_base_tables_dsp(json_data):
    """
    Extracts base tables from the given JSON mapping and wraps them in double quotes.
    
    Args:
        json_data (list or str): JSON list or string containing mapping dictionaries.
    
    Returns:
        list: List of base table names wrapped in double quotes.
    """
    if isinstance(json_data, str):
        json_data = json.loads(json_data)
    
    return [f'"{item.get("targetTable")}"' for item in json_data if "targetTable" in item]



import json

def format_mapping_fields_to_json(json_data):
    """
    Wraps targetTable and targetColumn values in double quotes 
    and returns the result as a JSON string with plain quotes.
    
    Args:
        json_data (list or str): JSON list or string containing mapping dictionaries.

    Returns:
        str: JSON string with targetTable and targetColumn wrapped in double quotes.
    """
    if isinstance(json_data, str):
        json_data = json.loads(json_data)

    for item in json_data:
        if "targetTable" in item:
            item["targetTable"] = f'{item["targetTable"]}'
        if "columns" in item:
            for col in item["columns"]:
                if "targetColumn" in col:
                    col["targetColumn"] = f'{col["targetColumn"]}'

    # serialize with ensure_ascii=False to avoid escaping
    return json.dumps(json_data, indent=4)



bq_to_hana_functions = {
  "bq_to_hana_datetime_functions": {
    "current_date_time": {
      "bigquery": [
        "CURRENT_DATE()",
        "CURRENT_TIME()",
        "CURRENT_DATETIME()",
        "CURRENT_TIMESTAMP()"
      ],
      "hana_sql": [
        "CURRENT_DATE",
        "CURRENT_TIME",
        "CURRENT_TIMESTAMP",
        "NOW()"
      ]
    },
    "add_subtract_intervals": {
      "bigquery": "DATETIME_ADD(date_expression, INTERVAL int_expression part)",
      "hana_sql_equivalents": [
        "ADD_DAYS(date, days)",
        "ADD_MONTHS(date, months)",
        "ADD_YEARS(date, years)",
        "ADD_SECONDS(time, seconds)"
      ],
      "example_translation": {
        "bq": "DATETIME_ADD(current_timestamp(), INTERVAL 7 DAY)",
        "hana": "ADD_DAYS(current_timestamp, 7)"
      }
    },
    "date_time_difference": {
      "bigquery": "DATETIME_DIFF(datetime1, datetime2, part)",
      "hana_sql_equivalents": [
        "DAYS_BETWEEN(date1, date2)",
        "MONTHS_BETWEEN(date1, date2)"
      ],
      "example_translation": {
        "bq": "DATETIME_DIFF(datetime2, datetime1, DAY)",
        "hana": "DAYS_BETWEEN(datetime1, datetime2)"
      }
    },
    "extract_date_parts": {
      "bigquery": "EXTRACT(part FROM datetime_expression)",
      "hana_sql_equivalents": [
        "EXTRACT(part FROM datetime_expression)",
        "YEAR(date)",
        "MONTH(date)",
        "DAY(date)",
        "HOUR(time)",
        "MINUTE(time)",
        "SECOND(time)",
        "WEEKDAY(date)"
      ],
      "example_translation": {
        "bq": "EXTRACT(YEAR FROM my_date_column)",
        "hana": "YEAR(my_date_column)"
      }
    },
    "formatting_parsing": {
      "bigquery": {
        "format": "FORMAT_DATETIME(format_string, datetime_expression)",
        "parse": "PARSE_DATETIME(format_string, datetime_string)"
      },
      "hana_sql_equivalents": {
        "format": "TO_NVARCHAR(datetime, format_string)",
        "parse": [
          "TO_DATE(string, format_string)",
          "TO_TIME(string, format_string)",
          "TO_TIMESTAMP(string, format_string)"
        ]
      },
      "example_translation": {
        "parse_example": {
          "bq": "PARSE_DATE('%Y-%m-%d', '2023-10-26')",
          "hana": "TO_DATE('2023-10-26', 'YYYY-MM-DD')"
        },
        "format_example": {
          "bq": "FORMAT_DATE('%Y/%m/%d', my_date_column)",
          "hana": "TO_NVARCHAR(my_date_column, 'YYYY/MM/DD')"
        }
      }
    }
  }
}