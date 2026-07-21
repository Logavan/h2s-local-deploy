# Standard Library Imports
import os
import re
import json
import time
import random
import zipfile
import sqlite3
import threading
import traceback
import csv
import logging
import asyncio
import concurrent.futures
import pickle
import ast
import defusedxml.ElementTree as ET
import xml.etree.ElementTree as _ET_ORIG
ET.register_namespace = _ET_ORIG.register_namespace
from datetime import datetime
from textwrap import dedent
from copy import deepcopy
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple, Union

# Third-party Libraries
import pandas as pd
import httpx
import sqlparse
from sqlparse.sql import Identifier, IdentifierList, TokenList
from sqlparse.tokens import DML, Keyword, Literal, Name, Punctuation, Whitespace
from sqlfluff.core import Linter
from sqlfluff.core.errors import SQLBaseError
import sqlglot
from sqlglot import exp, parse as sqlglot_parse
from sqlglot.errors import ParseError
from openai import OpenAI
from google.cloud import bigquery
from google.api_core.exceptions import GoogleAPICallError, RetryError, DeadlineExceeded

# Local Imports
from logger_setup import logger
from api_client import api_call, api_call_flash, api_call_async, api_call_flash_async
from node_cache import load_node_dict, save_node_dict
from bq_table import (
    get_next_dataset_name, 
    get_next_dataset_name_async,
    create_all_tables_from_load_data, 
    create_all_tables_from_load_data_async,
    run_bigquery_sql, 
    run_bigquery_sql_async, 
    delete_dataset
)
from bq_error_fixer import fix_bigquery_error, fix_all_common_errors, get_structured_error_context

# Suppress all messages from sqlfluff
for logger_name in ['sqlfluff', 'sqlfluff.linter', 'sqlfluff.templater', 'sqlfluff.rules']:
    l = logging.getLogger(logger_name)
    l.setLevel(logging.CRITICAL)
    l.propagate = False
    for handler in l.handlers[:]:
        l.removeHandler(handler)
    l.addHandler(logging.NullHandler())

# Remove all handlers from the root logger first to prevent duplicate messages
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(level=logging.INFO)
# Note: using 'logger' imported from logger_setup (line 42)

async def construct_node_dict(xml_content: str) -> Dict[str, Any]:
    return await xml_to_sql_converter(xml_content)


def dict_maker(xml_content):

    return xml_to_sql_converter_initial(xml_content, {})




def clean_orphan_nodes(node_dict):
    """
    Remove orphan nodes (nodes with 0 occurrences that are not Aggregation/Projection type).
    Iteratively cleans the node dictionary until no more orphan nodes remain.
    
    This handles cases where:
    - A node has no incoming references from other nodes
    - It's not an output node (Aggregation/Projection)
    - After removing orphans, other nodes may become orphans too
    
    Returns:
        dict: Cleaned node dictionary
    """
    if not node_dict:
        return node_dict
    
    max_iterations = 20  # Increased safety limit to handle complex dependencies
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Build set of all node names (case-insensitive)
        all_node_names = {name.lower() for name in node_dict.keys()}
        
        # Calculate occurrence count for each node (case-insensitive matching)
        occurrence_count = {name.lower(): 0 for name in node_dict}
        for node_name, node_data in node_dict.items():
            sources = node_data.get("Sources", [])
            if isinstance(sources, str):
                sources = [s.strip("[]'\" ").lower() for s in sources.split(",")]
            else:
                sources = [str(s).strip("[]'\" ").lower() for s in sources]

            for source in sources:
                if source.lower() in occurrence_count:
                    occurrence_count[source.lower()] += 1
        
        # Find orphan nodes - keep Aggregation/Projection as they are final output nodes
        # Remove only non-Agg/Proj nodes with 0 occurrences
        orphans_to_remove = []
        for node_name, node_data in node_dict.items():
            node_type = node_data.get("Node type", "")
            occ = occurrence_count.get(node_name.lower(), 0)
            
            # Only keep nodes named exactly "aggregation" or "projection" - all other orphans removed
            if str(node_name).lower() in ("aggregation", "projection"):
                continue
                
            # Keep non-aggregation nodes that have occurrences
            if occ > 0:
                continue
                
            # This is an orphan (non-Agg/Proj with 0 occ) - mark for removal
            orphans_to_remove.append(node_name)
        
        if not orphans_to_remove:
            # No more orphans to remove, we're done
            break
        
        print(f"Iteration {iteration}: Removing {len(orphans_to_remove)} orphan nodes: {orphans_to_remove}")
        
        # Remove orphan nodes from dictionary
        for orphan in orphans_to_remove:
            node_dict.pop(orphan, None)
    
    # Final pass: Identify and handle any remaining zero-occurrence nodes
    # that are not output nodes (these should be removed as they have no dependencies)
    # Use lowercase keys for case-insensitive matching with Sources
    occurrence_count = {name.lower(): 0 for name in node_dict}
    for node_name, node_data in node_dict.items():
        sources = node_data.get("Sources", [])
        if isinstance(sources, str):
            sources = [s.strip("[]'\" ").lower() for s in sources.split(",")]
        else:
            sources = [str(s).strip("[]'\" ").lower() for s in sources]
        
        for source in sources:
            if source.lower() in occurrence_count:
                occurrence_count[source.lower()] += 1
    
    # Find remaining orphans
    final_orphans = []
    for node_name, node_data in node_dict.items():
        node_type = node_data.get("Node type", "")
        occ = occurrence_count.get(node_name.lower(), 0)
        
        # Skip output nodes
        if node_type in ("Aggregation", "Projection"):
            continue
        
        # Remove nodes with zero occurrences
        if occ == 0:
            final_orphans.append(node_name)
    
    if final_orphans:
        print(f"Final cleanup: Removing {len(final_orphans)} remaining orphan nodes: {final_orphans}")
        for orphan in final_orphans:
            node_dict.pop(orphan, None)
    
    return node_dict


def validate_node_dict(node_dict):
    valid_node_types = {
        "Projection": 1,
        "Aggregation": 1,
        "Union": ">=1",
        "Rank": 1,
        "JoinNode": 2
    }

    errors = set()
    zero_occurrence_nodes = []

    for node_name, node_data in node_dict.items():
        node_data = {k.strip(): v for k, v in node_data.items()}

        if node_data.get("No of Occurances") == 0:
            zero_occurrence_nodes.append(node_name)


        node_type = node_data.get("Node type")
        if node_type not in valid_node_types:
            errors.add(f"Something error found in '{node_name}'")
            continue

        expected = valid_node_types[node_type]
        actual_sources = node_data.get("No of sources")

        if expected == ">=1":
            if not isinstance(actual_sources, int) or actual_sources < 1:
                errors.add(f"Something error found in '{node_name}'")
        else:
            if actual_sources != expected:
                errors.add(f"Something error found in '{node_name}'")

    if len(zero_occurrence_nodes) != 1:
        for n in zero_occurrence_nodes:
            if str(n).lower() not in ('aggregation', 'projection'):
                errors.add(f"Something error found in '{n}'- This must be mapped to a target")

    return list(errors)

def validity_check(xml_content):
    try:

        node_dict = dict_maker(xml_content)
        
        # Clean orphan nodes (nodes with 0 occurrences that are not Aggregation/Projection)
        node_dict = clean_orphan_nodes(node_dict)
        
        errors = validate_node_dict(node_dict)
        # logger.info(f"Errors found: {errors}")
        if errors:
            return False, errors
        return True, []
    except Exception:
        return False, ["Something error found in XML parsing."]


node_dict = {}



async def xml_to_sql_converter(xml_content, node_dict=None, target=None):
    # return

    if node_dict is None:
        node_dict = {}
    logger.info("Starting XML to SQL conversion...")

    logger.info(f"XML Content Length: {len(xml_content)} characters")

    xml_content = switch_date_functions_in_xml(xml_content)

    input_text,node_dict = process_xml_to_nodes(xml_content, node_dict)
   
    logger.info(f"process_xml_to_nodes completed with {len(node_dict)} nodes.")

    update_node_xml(input_text, node_dict)

    node_dict = {
            k.lower(): v for k, v in node_dict.items()
        }

    update_node_dict_XML(node_dict)

    logger.info("Node dictionary updated with XML data.")

    # Bic reference
    update_bic_references(node_dict)

    logger.info("BIC references updated in node dictionary.")


    # step 5: update_datasource_details
    update_datasource_details(node_dict)
    logger.info("Datasource details updated in node dictionary.")

    # step 6: update fields
    update_node_fields(node_dict)

    logger.info("Node fields updated in node dictionary.")

    # step 7: update join conditions
    Update_join_details(node_dict)
    logger.info("Join conditions updated in node dictionary.")

    # step 8: update aggregate values
    update_aggregate_values(node_dict)
    logger.info("Aggregate values updated in node dictionary.")

    transform_data_structure(node_dict)

    logger.info("Data structure transformed in node dictionary.")

    build_prompts_for_all_nodes(node_dict)

    logger.info("Prompts built for all nodes in node dictionary.")

    update_chunk_info(node_dict)

    logger.info("Chunk information updated in node dictionary.")

    # #save_to_pickle(node_dict, filename="node_autorca_0", directory="pickle_files")
   
    # save_to_pickle(node_dict, filename="BenchMarking_1", directory="pickle_files")
# ##-----------------------------------------------------------------------------------------------------------

    # process_nodes_xml_sql_parallel(node_dict)
    await process_nodes_xml_sql_parallel_async(node_dict, target=target)
    logger.info("Node SQL processed in parallel.")
    logger.info(count_node_sql(node_dict))
    logger.info("XML to SQL processing completed for all nodes.")

    # save_to_pickle(node_dict, filename="BenchMarking_2", directory="pickle_files")

    await fill_node_sql_async(node_dict)
    logger.info("Node SQL filled in node dictionary.")
    logger.info(count_node_sql(node_dict))
# # ##-----------------------------------------------------------------------------------------------------------
#Commenting this block for testing

    logger.info(count_node_sql(node_dict))
    enhance_node_dict(node_dict)
    logger.info("Node dictionary enhanced with additional data.")
    logger.info(count_node_sql(node_dict))

    format_all_node_sql(node_dict)
    logger.info("All node SQL formatted.") 
    logger.info(count_node_sql(node_dict))
    
    await process_all_json_data(node_dict)
    logger.info("All JSON data processed in node dictionary.")
    logger.info(count_node_sql(node_dict))

    get_chunkwise_external_sources_and_schema(node_dict)
    logger.info("Chunkwise external sources and schema retrieved.")
    logger.info(count_node_sql(node_dict))

    await process_temp_table_parallel_async(node_dict)
    logger.info("Temporary tables processed in node dictionary.")

    await process_nodes_sql_gcp_validation_parallel_async(node_dict) #check this function
    logger.info(count_node_sql(node_dict))

    logger.info("Node SQL GCP validation processed in parallel.")

    await process_temp_table_parallel_for_chunks_async(node_dict)
    logger.info(count_node_sql(node_dict))

    logger.info("Temporary tables processed for chunks in node dictionary.")
    logger.info("Node SQL count after processing temp tables: %s", count_node_sql(node_dict))

    enhance_node_dict(node_dict)

    logger.info("Node dictionary enhanced with additional data after chunk processing.")

    node_dict = lowercase_selected_fields(node_dict, keys_to_lower)
    logger.info(count_node_sql(node_dict))

    logger.info("Selected fields in node dictionary converted to lowercase.")

    actual_source_chunk = find_actual_sources_chunkwise(node_dict)
    logger.info("Actual sources chunkwise found in node dictionary.")
    logger.info(f"Actual sources chunkwise: {actual_source_chunk}")

    node_dict = {k: v for k, v in node_dict.items() if k not in ["Node XML", "Node Prompt"]}


# --------------------------------------------------------------------------------------
    import time
    bq_start = time.time()
    logger.info(f"[FILE_PROC] BigQuery section START - dataset allocation")

    ds_name = await get_next_dataset_name_async("dataset")

    await create_all_tables_from_load_data_async(node_dict, ds_name) # parallel

# ##-----------------------------------------------------------------------------------------------------------
    await process_nodes_xml_sql_parallel_for_rank_node_async(node_dict)


# #  #-----------------------------------------------------------------------------------------------------------


    await process_all_chunks_async(ds_name, node_dict) # parallel
    logger.info("All chunks processed in node dictionary.")



    await fill_chunk_sql_primary_async(node_dict, ds_name) # filling
    logger.info("Chunk SQL primary filled in node dictionary.")

    validation_result = validate_chunk_count(node_dict, "Chunk SQL Primary")
    if validation_result != "ok":
        delete_dataset(ds_name, delete_contents=True) # clean up dataset
        return 

    ## save_to_pickle(node_dict, filename="gr_ir_flow_2", directory="pickle_files")

# # # # --------------------------------------------------------------------------------------
  
    await add_optimized_column_parallel(ds_name, node_dict) # parallel
    logger.info("Optimized column added in parallel for primary nodes in node dictionary.")
    
    await fill_add_optimized_column_parallel(ds_name, node_dict)#filling

    validation_result = validate_chunk_count(node_dict, "Chunk SQL Primary Optimized")

    if validation_result != "ok":
        delete_dataset(ds_name, delete_contents=True) # clean up dataset
        return 

    ## save_to_pickle(node_dict, filename="gr_ir_flow_3", directory="pickle_files")


# # # --------------------------------------------------------------------------------------

    original_source_schema = build_leaf_nodes_schema_info(node_dict)

    # logger.info(f"original_source_schema: {original_source_schema}")

    await base_alias_table_parallel(node_dict, original_source_schema) # parallel
    logger.info("Base alias table processed in parallel for primary nodes in node dictionary.")

    await fill_base_alias_table_parallel(node_dict, original_source_schema) # filling

    validation_result = validate_chunk_count(node_dict, "Chunk SQL Primary Optimized Base")

    if validation_result != "ok":
        delete_dataset(ds_name, delete_contents=True) # clean up dataset
        return 
# # # --------------------------------------------------------------------------------------
 

    await extract_sourcetable_fields_parallel(node_dict, original_source_schema) # parallel
    logger.info("Source table fields extracted in parallel for primary nodes in node dictionary.")

    await fill_extract_sourcetable_fields_parallel(node_dict, original_source_schema)


    # save_to_pickle(node_dict, filename="gr_ir_flow_4", directory="pickle_files")
# --------------------------------------------------------------------------------------

    logger.info(f"[FILE_PROC] BigQuery section COMPLETE - total time: {time.time()-bq_start:.2f}s")
    delete_dataset(ds_name, delete_contents=True) # clean up dataset

    return node_dict

#---------------------------------------------------------------------------------------------

# Define your base directory
BASE_DIR__dict_load = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_node_data(filename):
    """Load node_dict.pkl from the pickle_files directory"""
    pickle_dir = os.path.join(BASE_DIR__dict_load, 'pickle_files')
    return load_from_pickle(filename, pickle_dir)

def parse_sources_for_graph(s):
    """Parse the 'Sources' field into a list of node names."""
    if s is None:
        return []
    try:
        if isinstance(s, str):
            s = s.strip()
            if s.startswith('[') and s.endswith(']'):
                return ast.literal_eval(s)
            elif s:
                return [s.strip("'\" ")]
        elif isinstance(s, list):
            return s
        return []
    except:
        return []
    


# Keywords that indicate incomplete SQL if they are at the end
INCOMPLETE_KEYWORDS = {"WHERE", "AND", "OR", "GROUP BY", "HAVING", "ON", "JOIN", "ORDER BY"}



def is_valid_sql(sql: str, dialect: str = "bigquery", allow_multiple_statements: bool = False) -> Tuple[bool, str]:
    """
    Validates SQL using sqlglot for robust parsing with enhanced error detection.
    Returns (is_valid, error_message) tuple.
    """
    if not sql or not sql.strip():
        return False, "Parse error: SQL query is empty or contains only whitespace."

    # Remove trailing semicolons and clean up
    clean_sql = sql.strip().rstrip(";").strip()
    
    # Quick sanity checks before parsing
    sql_upper = clean_sql.upper()
    
    # Must start with SELECT (after removing any leading whitespace)
    if not sql_upper.lstrip().startswith("SELECT"):
        return False, "Parse error: SQL must start with SELECT statement."
    
    # Check for common BigQuery-specific syntax errors
    if dialect == "bigquery":
        # Wrong data types
        if "CAST(" in sql_upper:
            if " AS INTEGER)" in sql_upper:
                return False, "BigQuery error: Use INT64 instead of INTEGER in CAST."
            if " AS FLOAT)" in sql_upper and " AS FLOAT64)" not in sql_upper:
                return False, "BigQuery error: Use FLOAT64 instead of FLOAT in CAST."
        
        # NULL comparison errors
        if "= NULL" in sql_upper or "<> NULL" in sql_upper:
            return False, "BigQuery error: Use IS NULL or IS NOT NULL instead of = NULL."
        
        # Check for incomplete SQL patterns - must be whole words, not part of identifiers
        # Use regex with word boundary to avoid false positives like column names ending with ON
        last_word_match = re.search(r'\b(\w+)\s*$', sql_upper.rstrip())
        if last_word_match:
            last_word = last_word_match.group(1)
            if last_word in {"WHERE", "AND", "OR", "ON", "JOIN", "FROM", "BY", "HAVING", "SELECT", "SET"}:
                return False, "Parse error: SQL appears incomplete - ends with a keyword."

    try:
        # Use sqlglot for proper dialect-aware parsing
        parsed = sqlglot_parse(clean_sql, read=dialect)
        
        if not parsed:
            return False, "Parse error: Could not parse SQL structure."
            
        if not allow_multiple_statements and len(parsed) > 1:
            return False, "Parse error: Multiple SQL statements are not allowed."
        
        # Verify it's a SELECT statement
        first_stmt = parsed[0]
        if not hasattr(first_stmt, 'find') or first_stmt.find(exp.Select) is None:
            # Check if the root itself is a Select
            if not isinstance(first_stmt, exp.Select):
                return False, "Parse error: Query must be a SELECT statement."
        
        return True, "Valid SQL"

    except ParseError as e:
        error_msg = str(e)
        # Make error messages more actionable
        if "Expecting" in error_msg:
            return False, f"SQL Syntax Error: {error_msg}. Check for missing or misplaced keywords."
        return False, f"SQL Syntax Error: {error_msg}"
    except Exception as e:
        return False, f"Validation Error: {str(e)}"

    
def build_digraph_from_dict(node_dict):
    """
    Build Graphviz digraph text from node dictionary using only Primary nodes.
    - One primary per chunk
    - Deduplicates edges
    - Capitalizes node names
    - No double quotes in DOT output
    - Orders edges by chunk numbers
    """
    df = pd.DataFrame.from_dict(node_dict, orient='index')

    # Ensure required columns exist
    for col in ['Node name', 'Sources', 'Chunk Number', 'Is Primary']:
        if col not in df.columns:
            df[col] = None

    df = df.dropna(subset=['Chunk Number'])

    # Normalize names (capitalize)
    df['Node name'] = df['Node name'].astype(str).str.upper()

    # Identify primary nodes (one per chunk)
    primary_nodes = (
        df[df['Is Primary'].str.upper() == "YES"]
        .groupby('Chunk Number')
        .first()['Node name']
        .to_dict()
    )

    # Build edges only between primaries
    edges = set()
    for _, row in df.iterrows():
        current_chunk = row['Chunk Number']
        sources = parse_sources_for_graph(row['Sources'])
        for src in sources:
            src_row = df[df['Node name'].str.lower() == str(src).lower()]
            if not src_row.empty:
                src_chunk = src_row['Chunk Number'].values[0]
                if src_chunk != current_chunk:
                    # Use primary names only
                    if src_chunk in primary_nodes and current_chunk in primary_nodes:
                        src_name = primary_nodes[src_chunk]
                        tgt_name = primary_nodes[current_chunk]
                        edges.add((src_chunk, current_chunk, src_name, tgt_name))

    # Sort edges by chunk numbers
    sorted_edges = sorted(edges, key=lambda x: (x[0], x[1]))

    # Build DOT text
    dot_lines = ["digraph G {"]
    for _, _, src, tgt in sorted_edges:
        dot_lines.append(f"  {src} -> {tgt};")
    dot_lines.append("}")
    return "\n".join(dot_lines)


def dig_mapping_generator(xml_content, node_dict=None):
    if node_dict is None:
        node_dict = {}
    logger.info(f"XML Content Length: {len(xml_content)} characters")
    input_text,node_dict = process_xml_to_nodes(xml_content, node_dict)
   
    logger.info(f"process_xml_to_nodes completed with {len(node_dict)} nodes.")
    # # Step 3: Update Node XML
    update_node_xml(input_text, node_dict)

    node_dict = {
            k.lower(): v for k, v in node_dict.items()
        }

    update_node_dict_XML(node_dict)
 
    # logger.info("Node dictionary updated with XML data.")

    # Bic reference
    update_bic_references(node_dict)

    # logger.info("BIC references updated in node dictionary.")

    # file_save_test(node_dict)

    # step 5: update_datasource_details
    update_datasource_details(node_dict)
    # logger.info("Datasource details updated in node dictionary.")

    # step 6: update fields
    update_node_fields(node_dict)
  
    # logger.info("Node fields updated in node dictionary.")

    # step 7: update join conditions
    Update_join_details(node_dict)
    # logger.info("Join conditions updated in node dictionary.")
  
    # step 8: update aggregate values
    update_aggregate_values(node_dict)
    # logger.info("Aggregate values updated in node dictionary.")
    transform_data_structure(node_dict)

    # logger.info("Data structure transformed in node dictionary.")

    build_prompts_for_all_nodes(node_dict)

    update_chunk_info(node_dict)

    columns_graph = ['Node name', 'Sources', 'Chunk Number']
    dot_string = build_digraph_from_dict(node_dict )
    

    return dot_string




def load_from_pickle(filename, directory=None):
    """
    Load data from a pickle file.
    
    Args:
        filename (str): Name of the pickle file to load, or full path
        directory (str): Optional directory path. If None, uses current script directory.
    
    Returns:
        object: The loaded data, or None if failed
    """
    try:
        # If filename already contains a path, extract directory and filename
        if os.path.dirname(filename) and directory is None:
            # filename is already a full path
            file_path = filename
        else:
            # Determine directory
            if directory is None:
                directory = os.path.dirname(os.path.abspath(__file__))
            
            # Add .pkl extension if missing
            if not filename.endswith('.pkl'):
                filename += '.pkl'
            
            # Create full file path
            file_path = os.path.join(directory, filename)
        
        # Check if file exists
        if not os.path.exists(file_path):
            logger.info(f"File not found: {file_path}")
            return None
        
        # Load data
        with open(file_path, 'rb') as file:
            data = pickle.load(file)
        
        logger.info(f"Data successfully loaded from: {file_path}")
        return data
        
    except FileNotFoundError:
        logger.info(f"File not found: {file_path}")
        return None
    except Exception as e:
        logger.info(f"Error loading from pickle: {e}")
        return None
#---------------------------------------------------------------------------------------------

def file_save_test(node_dict, filename):
    # Get current directory of this script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Define output CSV file path
    output_path = os.path.join(current_dir, filename)
    
    # Convert node_dict to DataFrame with node names as a column
    df = pd.DataFrame.from_dict(node_dict, orient='index').reset_index()
    df.rename(columns={'index': 'Node name'}, inplace=True)
    
    # Save DataFrame to CSV without the DataFrame index
    df.to_csv(output_path, index=False, encoding='utf-8')
    
    logger.info(f"CSV file saved at: {output_path}")

# pickle, os, datetime already imported at top of file

def count_node_sql(node_dict):
    """
    Counts how many nodes have non-blank Node SQL values.
    
    Args:
        node_dict (dict): Dictionary of nodes with their properties
        
    Returns:
        int: Number of nodes with non-blank Node SQL
    """
    count = 0
    for node_name, node in node_dict.items():
        sql = node.get("Node SQL")
        if sql and str(sql).strip():  # Check if not None and not empty/whitespace-only
            count += 1
    
    logger.info(f"[COUNT] {count} nodes have non-blank SQL out of {len(node_dict)} total nodes")
    return count
def validate_chunk_count(data: dict, chunk_field: str) -> str:
    """
    Check if all primary nodes have the specified chunk field filled.
    Returns 'ok' if all primary nodes have this field populated, else error message.
    """
    try:
        primary_nodes_with_missing_field = []
        
        # Iterate through all nodes
        for node_name, node_content in data.items():
            if isinstance(node_content, dict):
                # Check if this node is primary
                if node_content.get("Is Primary") == "Yes":
                    chunk_value = node_content.get(chunk_field)
                    
                    # Field is missing or empty
                    if chunk_value is None:
                        primary_nodes_with_missing_field.append(node_name)
                    elif isinstance(chunk_value, str) and chunk_value.strip() == "":
                        primary_nodes_with_missing_field.append(node_name)
                    elif isinstance(chunk_value, (list, dict)) and not chunk_value:
                        primary_nodes_with_missing_field.append(node_name)
        
        if primary_nodes_with_missing_field:
            return f"Error: Primary nodes missing {chunk_field}: {', '.join(primary_nodes_with_missing_field)}"
        else:
            return "ok"   # ✅ make sure to return ok

    except Exception as e:
        return f"Something Error Occurred...Retry: {str(e)}"


def save_to_pickle(data, filename=None, directory=None):
    """
    Save data to a pickle file.
    
    Args:
        data: The data to be saved (any Python object)
        filename (str): Optional custom filename. If None, uses timestamp.
        directory (str): Optional directory path. If None, uses current script directory.
    
    Returns:
        str: Full path to the saved file, or None if failed
    """
    try:
        # Determine directory
        if directory is None:
            directory = os.path.dirname(os.path.abspath(__file__))
        
        # Ensure directory exists
        os.makedirs(directory, exist_ok=True)
        
        # Generate filename if not provided
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"data_node_dict.pkl"
        elif not filename.endswith('.pkl'):
            filename += '.pkl'
        
        # Create full file path
        file_path = os.path.join(directory, filename)
        
        # Save data
        with open(file_path, 'wb') as file:
            pickle.dump(data, file)
        
        logger.info(f"Data successfully saved to: {file_path}")
        return file_path
        
    except Exception as e:
        logger.info(f"Error saving to pickle: {e}")
        return None

def xml_to_sql_converter_initial(xml_content, node_dict=None):

    if node_dict is None:
        node_dict = {}

    input_text,node_dict = process_xml_to_nodes(xml_content, node_dict)

    # # Step 3: Update Node XML
    update_node_xml(input_text, node_dict)

    node_dict = {
            k.lower(): v for k, v in node_dict.items()
        }

    update_node_dict_XML(node_dict)

    # Bic reference
    update_bic_references(node_dict)

    # step 5: update_datasource_details
    update_datasource_details(node_dict)

    # step 6: update fields
    update_node_fields(node_dict)

    # step 7: update join conditions
    Update_join_details(node_dict)

    # step 8: update aggregate values
    update_aggregate_values(node_dict)

    transform_data_structure(node_dict)

    build_prompts_for_all_nodes(node_dict)

    update_chunk_info(node_dict)

    return node_dict





async def extract_sourcetable_fields_parallel(node_dict, original_source_schema):
    """ Extracts source table fields in parallel for primary nodes."""
    # Submit tasks only for nodes that meet all conditions
    tasks = []
    for node_name, node_data in node_dict.items():
        # Check all conditions before submitting
        chunk_number = node_data.get("Chunk Number")
        
        # Condition 1: Skip if not primary
        if str(node_data.get("Is Primary", "")).lower() == "no":
            continue
            
        # Condition 2: Check if chunk number exists in source schema
        chunk_numbers = list({v.get("Chunk Number") for v in original_source_schema.values()})
        if chunk_number not in chunk_numbers:
            continue
            
        # Condition 3: Check if base_table_list is not empty
        base_table_list = {
            tbl: cols
            for v in original_source_schema.values()
            if v.get("Chunk Number") == chunk_number
            for tbl, cols in v["original_leaf_node_columns"].items()
        }
        if not base_table_list:
            continue
            
        # Condition 4: Check if SQL exists
        original_sql = node_data.get("Chunk SQL Primary Optimized Base")
        if not original_sql:
            continue
            
        # All conditions passed, add to tasks
        tasks.append(base_sourcetable_fields(node_name, node_data, original_source_schema))

    # Wait for all tasks to complete
    if tasks:
        await asyncio.gather(*tasks)


async def base_sourcetable_fields(node_name, node_data, original_source_schema):
    """Process a single node to extract original base table names and fields."""
    chunk_number = node_data.get("Chunk Number")
    
    # Get base table list for this chunk
    base_table_list = {
        tbl: cols
        for v in original_source_schema.values()
        if v.get("Chunk Number") == chunk_number
        for tbl, cols in v["original_leaf_node_columns"].items()
    }

    original_sql = node_data.get("Chunk SQL Primary Optimized Base")

    optimize_prompt = f"""
                        You are given a SQL query and a list of available tables and their columns. Your task is to extract all table and field names from the SQL query that are present in the provided list.

                        ### Instructions:
                        - Use this list of available tables and their columns: {json.dumps(base_table_list, indent=2)}
                        - Scan the SQL query for all table and column references.
                        - Create a JSON object where each key is a table name from the provided list, and the value is a list of its columns used in the query.
                        - Only include tables and columns that are explicitly listed in the available tables list.
                        - If no matching tables or columns are found, return an empty JSON object: {{}}
                        - Your output must be valid JSON in the format: {{"table_name": ["field1", "field2", ...]}}

                        ### Now process this SQL query:
                        {original_sql}
                        """

    try:
        sql_text = await api_call_with_retry_async("Gemini", optimize_prompt, task_type="sql")
        if sql_text:
            sql_text = sql_text.strip()
            # Extract the first {...} block from the output
            match = re.search(r"\{[\s\S]*\}", sql_text)
            if match:
                sql_text = match.group(0).strip()

            try:
                source_mapping = json.loads(sql_text)
                # Wrap list in table name if needed
                if isinstance(source_mapping, list) and len(base_table_list) == 1:
                    source_mapping = {list(base_table_list.keys())[0]: source_mapping}
            except json.JSONDecodeError:
                source_mapping = {"error": f"Invalid JSON from model: {sql_text}"}
        else:
            source_mapping = {"error": "No response from Gemini"}

        node_data["SourceTable_mapping_fields"] = source_mapping
    except Exception as e:
        node_data["SourceTable_mapping_fields"] = {"error": str(e)}

    return node_name


async def fill_extract_sourcetable_fields_parallel(node_dict, original_source_schema, max_concurrent=20):
    """
    Fill missing 'SourceTable_mapping_fields' for primary nodes in parallel.
    """
    logger = logging.getLogger(__name__)

    # Identify valid nodes to process
    nodes_to_process = []
    for node_name, node_data in node_dict.items():
        if str(node_data.get("Is Primary", "")).lower() != "yes" or node_data.get("SourceTable_mapping_fields"):
            continue
            
        chunk_number = node_data.get("Chunk Number")
        chunk_numbers = list({v.get("Chunk Number") for v in original_source_schema.values()})
        if chunk_number not in chunk_numbers:
            continue
            
        if not node_data.get("Chunk SQL Primary Optimized Base"):
            continue
            
        nodes_to_process.append((node_name, node_data))

    if not nodes_to_process:
        logger.info("No valid nodes to process.")
        return

    logger.info(f"Processing {len(nodes_to_process)} nodes asynchronously")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(node_name, node_data):
        async with semaphore:
            await base_sourcetable_fields(node_name, node_data, original_source_schema)

    tasks = [run_with_semaphore(name, data) for name, data in nodes_to_process]
    await asyncio.gather(*tasks)

    # Log results
    successful = sum(1 for node_name, _ in nodes_to_process 
                    if node_dict[node_name].get("SourceTable_mapping_fields"))
    logger.info(f"Successfully processed {successful}/{len(nodes_to_process)} nodes")




def find_missing_sources(node_dict):
    # Extract all node names (keys of the dictionary)
    all_node_names = set(node_dict.keys())

    # Collect all unique sources from all nodes
    all_sources = set()
    for node_data in node_dict.values():
        all_sources.update(node_data.get("Sources", []))

    # Find sources not present in node names
    missing_sources = list(all_sources - all_node_names)
    return missing_sources


def find_columns_details_from_base_tables(sql: str, base_tables: list) -> dict:
    """
    Extracts columns used from the given base tables in an SQL query.

    Args:
        sql (str): The SQL query.
        base_tables (list): List of base table names.

    Returns:
        dict: {table_name: [list of columns used]}
    """
    # Normalize SQL for easier parsing
    parsed_sql = sqlparse.format(sql, keyword_case='upper', identifier_case='lower')
    
    results = {table: set() for table in base_tables}
    
    # Regex to match table.column or alias.column
    pattern = re.compile(r'\b([a-z_][a-z0-9_]*)\s*\.\s*([a-z_][a-z0-9_]*)', re.IGNORECASE)

    # Find all table.column references
    for match in pattern.finditer(parsed_sql):
        table_or_alias, column = match.groups()
        
        # If the table_or_alias matches the actual base table, add the column
        if table_or_alias in base_tables:
            results[table_or_alias].add(column)
    
    # Convert sets to sorted lists
    return {table: sorted(list(cols)) for table, cols in results.items()}





async def process_all_json_data(node_dict):

    node_dict = {
        k.lower(): v for k, v in node_dict.items()
    }
    add_node_columns_schema(node_dict)
    logger.info("Node columns schema added.")

    await process_json_datatype_parallel_async(node_dict)
    logger.info("JSON datatype processed in parallel.")

    fill_node_json(node_dict)
    logger.info("Node JSON filled.")

    add_source_columns_schema(node_dict)
    logger.info("Source columns schema added.")

    await process_sources_json_datatype_parallel_async(node_dict)
    logger.info("Sources JSON datatype processed in parallel.")

    fill_node_source_datatype_json(node_dict)
    logger.info("Addes missing source datatype ")

    append_source_schemas_compact(node_dict)




keys_to_lower = [
    "Node Name", "Sources", "Fields", "Formula", "Filter Used",
    "Aggregated Columns", "Merged Nodes", "Chunk Sources", "Chunk_leaf_sources", "All Fields",
    "Node Schema", "Node Schema JSON", "Node Schema w/ datatype JSON",
    "Source Schema JSON", "Source Schema w/ datatype JSON", "Chunk Schema",
    "Temp table", "Temp table for Chunks"
]


def lowercase_selected_fields(data, keys_to_lower):
    sql_format_keys = ["Direct SQL", "Node SQL", "Chunk SQL"]

    def to_lowercase_sql(sql: str) -> str:
        parsed = sqlparse.parse(sql)
        if not parsed:
            return sql
        statement = parsed[0]

        def process_tokens(token_list: TokenList):
            for token in token_list.tokens:
                if token.is_group:
                    process_tokens(token)
                elif token.ttype in (Keyword, DML, Name):
                    token.value = token.value.lower()
                elif isinstance(token, IdentifierList):
                    for identifier in token.get_identifiers():
                        identifier.value = identifier.value.lower()
                elif isinstance(token, Identifier):
                    token.value = token.value.lower()
                # Avoid changing Literal.String.Single — preserves string literals like 'LA'

        process_tokens(statement)
        return str(statement)

    def force_lower(val):
        if isinstance(val, dict):
            return {k: force_lower(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [force_lower(v) for v in val]
        elif isinstance(val, str):
            return val.lower()
        else:
            return str(val).lower()

    for node_name, node_content in data.items():
        if not isinstance(node_content, dict):
            continue
        for key in list(node_content.keys()):
            value = node_content[key]
            if key in keys_to_lower:
                node_content[key] = force_lower(value)
            elif key in sql_format_keys:
                if isinstance(value, list):
                    node_content[key] = [to_lowercase_sql(v) for v in value if isinstance(v, str)]
                elif isinstance(value, str):
                    node_content[key] = to_lowercase_sql(value)
    return data





def append_source_schemas_compact(node_dict):
    for node_name, node_info in node_dict.items():
        # Skip if already filled
        if node_info.get("Source Schema w/ datatype JSON"):
            continue

        sources = node_info.get("Sources", [])
        schema_blocks = []

        for source_name in sources:
            source_node = node_dict.get(source_name)
            if not source_node:
                continue

            source_schema_raw = source_node.get("Node Schema w/ datatype JSON")

            if source_schema_raw:
                # Parse schema if it's a string
                if isinstance(source_schema_raw, str):
                    try:
                        source_schema_dict = json.loads(source_schema_raw)
                    except json.JSONDecodeError:
                        continue
                elif isinstance(source_schema_raw, dict):
                    source_schema_dict = source_schema_raw
                else:
                    continue

                # Append compact JSON string
                compact_schema_str = json.dumps(source_schema_dict, separators=(',', ':'))
                schema_blocks.append(compact_schema_str)

        if schema_blocks:
            node_info["Source Schema w/ datatype JSON"] = schema_blocks




def create_temp_table_bq_sql_single(node_dict):
    for node_name, node_info in node_dict.items():
        sources = node_info.get("Sources", [])
        schema = node_info.get("Source Schema w/ datatype JSON", [])
        full_prompt = f"""Create temp tables for the {sources} sources with the following schema: {schema}."""
        temp_table = api_call_with_retry('Gemini', full_prompt, task_type= 'sql')





def get_chunkwise_external_sources_and_schema(node_dict):
    # Step 1: Build source → SQL mapping
    node_dict = {
        k.lower(): v for k, v in node_dict.items()
    }

    source_sql_map = {}
    for node_name, node_data in node_dict.items():
        if not isinstance(node_data, dict):
            continue
        sources = node_data.get("Sources", [])
        node_name = node_name.lower()
        # logger.info(f"Processing node: {node_name} with sources: {sources}")
        node_sql = node_data.get("Node SQL", "")
        for source in sources:
            source = source.strip()
            if source in node_dict:
                src_data = node_dict[source]
                is_primary = src_data.get("Is Primary", "").lower()
                sql = src_data.get("Direct SQL", "") if is_primary == "yes" else src_data.get("Node SQL", "")
                source_sql_map[source] = sql
            else:
                if source not in source_sql_map:
                    source_sql_map[source] = node_sql  # fallback

    # Step 2: Build chunk_schema based on Chunk_leaf_sources
    chunk_schema = defaultdict(lambda: defaultdict(set))

    for node_name, node_data in node_dict.items():
        if not isinstance(node_data, dict):
            continue

        chunk = node_data.get("Chunk Number")
        chunk_leaf_sources = node_data.get("Chunk_leaf_sources", [])

        for source in chunk_leaf_sources:
            sql = source_sql_map.get(source, "")
            matches = re.findall(r'\b(\w+)\.(\w+)\b', sql)
            for table_or_alias, column in matches:
                chunk_schema[chunk][table_or_alias.lower()].add(column.lower())

    # Step 3: Convert sets to sorted lists
    for chunk, tables in chunk_schema.items():
        chunk_schema[chunk] = {
            table: sorted(list(columns))
            for table, columns in tables.items()
        }

    # # Step 4: Attach schema back to all nodes in each chunk
    # for node_data in node_dict.values():
    #     if not isinstance(node_data, dict):
    #         continue
    #     chunk = node_data.get("Chunk Number")
    #     if chunk in chunk_schema:
    #     #     # node_data["Chunk Schema"] = chunk_schema[chunk]
    #         logger.info(f"Chunk {chunk} schema: {chunk_schema[chunk]}")

    fill_chunk_schema_with_datatype(node_dict)
    # logger.info("Chunk schemas filled with datatype.")

    # return node_dict  # optional, if you want to use the modified dict




# json already imported at top of file

def _load_schema_data(raw):
    """Normalize schema field which can be str, list or dict -> return dict."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(raw, list):
        merged = {}
        for item in raw:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except Exception:
                    continue
            if isinstance(item, dict):
                merged.update(item)
        return merged
    if isinstance(raw, dict):
        return raw
    return {}

def _find_key_case_insensitive(dct, key):
    """Return the actual key in dct whose lowercase equals key.lower(), or None."""
    if not isinstance(dct, dict):
        return None
    low = key.lower()
    for k in dct.keys():
        if isinstance(k, str) and k.lower() == low:
            return k
    return None

def fill_chunk_schema_with_datatype(node_dict):
    """
    For each node, populate 'Chunk Schema' as:
      - step 1: schema(s) from a node if `source` is itself a node (Node Schema w/ datatype JSON)
      - step 2: union of provider source-schemas from (a) other nodes and (b) current node's Source Schema w/ datatype JSON
    The provider schemas gathered in step 2 are deduplicated.
    """
    for node_name, node_attrs in node_dict.items():
        # if no Chunk Sources, set empty list and continue
        if "Chunk Sources" not in node_attrs:
            continue

        chunk_sources = node_attrs.get("Chunk Sources") or []
        if not chunk_sources:
            node_attrs["Chunk Schema"] = []
            continue

        collected_schemas = []

        for source in chunk_sources:
            # keep original source for matching (do not mutate)
            orig_source = source

            # === Step 1: check if the source is a node and pull its Node Schema ===
            step1_entries = []
            if orig_source in node_dict:
                source_node = node_dict[orig_source]
                if "Node Schema w/ datatype JSON" in source_node:
                    raw = source_node["Node Schema w/ datatype JSON"]
                    schema_data = _load_schema_data(raw)
                    if isinstance(schema_data, dict) and schema_data:
                        # prefer exact (case-insensitive) match to the source key
                        matched_key = _find_key_case_insensitive(schema_data, orig_source)
                        if matched_key:
                            step1_entries.append({matched_key: schema_data[matched_key]})
                        else:
                            # fallback: take first dict value
                            for k, v in schema_data.items():
                                if isinstance(v, dict):
                                    step1_entries.append({k: v})
                                    break

            # append step1 entries to collected_schemas (as JSON strings)
            for entry in step1_entries:
                try:
                    collected_schemas.append(json.dumps(entry))
                except Exception:
                    pass

            # === Step 2: gather provider Source Schemas and union them ===
            provider_entries = []

            # 2.a - other nodes that list `source` in their "Sources"
            for provider_node_name, provider_attrs in node_dict.items():
                # include all nodes (including current) in 2.a search? 
                # As per your instruction, 2.a is other nodes (so skip current node here)
                if provider_node_name == node_name:
                    continue
                if "Sources" in provider_attrs and orig_source in provider_attrs["Sources"]:
                    if "Source Schema w/ datatype JSON" in provider_attrs:
                        raw = provider_attrs["Source Schema w/ datatype JSON"]
                        schema_data = _load_schema_data(raw)
                        if isinstance(schema_data, dict):
                            matched_key = _find_key_case_insensitive(schema_data, orig_source)
                            if matched_key:
                                provider_entries.append({matched_key: schema_data[matched_key]})

            # 2.b - current node's Source Schema (if current node exposes the source)
            if "Sources" in node_attrs and orig_source in node_attrs["Sources"]:
                if "Source Schema w/ datatype JSON" in node_attrs:
                    raw = node_attrs["Source Schema w/ datatype JSON"]
                    schema_data = _load_schema_data(raw)
                    if isinstance(schema_data, dict):
                        matched_key = _find_key_case_insensitive(schema_data, orig_source)
                        if matched_key:
                            provider_entries.append({matched_key: schema_data[matched_key]})

            # Deduplicate provider_entries (union) using canonical JSON (sorted keys)
            unique_provider_entries = []
            seen = set()
            for item in provider_entries:
                try:
                    s = json.dumps(item, sort_keys=True)
                except Exception:
                    continue
                if s not in seen:
                    seen.add(s)
                    unique_provider_entries.append(item)

            # Append provider entries (as JSON strings) after deduplication
            for entry in unique_provider_entries:
                try:
                    collected_schemas.append(json.dumps(entry))
                except Exception:
                    pass

        # Assign collected schemas list to node_attrs
        node_attrs["Chunk Schema"] = collected_schemas


def build_leaf_nodes_schema_info(nodes):
    """
    Build dictionary of leaf nodes with:
      - Chunk Number
      - is_leafnode
      - Chunk_leaf_sources
      - original_leaf_node
      - Chunk Schema
      - original_leaf_node_columns (structured as {table: {column: dtype}})
    """

    # Build lookup of node names (lowercased → original key)
    all_node_names = {v["Node name"].lower(): k for k, v in nodes.items() if "Node name" in v}

    leaf_nodes = {}
    for k, v in nodes.items():
        if v.get("is_leafnode") == "Yes":
            sources = v.get("Chunk_leaf_sources", [])
            if isinstance(sources, str):
                sources = [s.strip() for s in sources.split(",")]

            # Identify original sources (not present in node names)
            original_leaf = [src for src in sources if src.lower() not in all_node_names]

            # Collect schema for original leaf nodes in structured format
            original_leaf_node_columns = {}
            
            # Parse Chunk Schema (which is a list of JSON strings)
            chunk_schema_list = v.get("Chunk Schema", [])
            parsed_schemas = {}
            
            for schema_str in chunk_schema_list:
                try:
                    schema_dict = json.loads(schema_str)
                    parsed_schemas.update(schema_dict)
                except (json.JSONDecodeError, TypeError):
                    # If it's not a JSON string, try to use it as-is
                    if isinstance(schema_str, dict):
                        parsed_schemas.update(schema_str)
            
            # Now extract columns from original leaf nodes
            for table in original_leaf:
                if table in parsed_schemas:
                    # Keep the schema structure as {column: dtype}
                    original_leaf_node_columns[table] = parsed_schemas[table]
                else:
                    # fallback if not found → mark table with None
                    original_leaf_node_columns[table] = None

            leaf_nodes[k] = {
                "Chunk Number": v.get("Chunk Number"),
                "is_leafnode": v.get("is_leafnode"),
                "Chunk_leaf_sources": sources,
                "original_leaf_node": original_leaf,
                "Chunk Schema": v.get("Chunk Schema"),
                "original_leaf_node_columns": original_leaf_node_columns,
            }

    return leaf_nodes


def consolidated_sql(node_dict):

    # Filter and sort valid nodes
    valid_nodes = [
        (name, info) for name, info in node_dict.items() 
        if info.get('Chunk SQL Primary Optimized') is not None
    ]
    sorted_nodes = sorted(
    valid_nodes, 
    key=lambda x: int(x[1]['Chunk Number'])  # Explicit conversion to int
)

    # Clean SQL chunks and prepare for formatting
    cleaned_chunks = [
        (name, info['Chunk SQL Primary Optimized'].rstrip(';: \t\n\r'))
        for name, info in sorted_nodes
    ]

    if not cleaned_chunks:
        return ""

    # Format CTEs with proper spacing
    if len(cleaned_chunks) > 1:
        ctes = cleaned_chunks[:-1]
        cte_clauses = [f"{name} AS ({sql})" for name, sql in ctes]
        formatted_ctes = "WITH " + ",\n\n     ".join(cte_clauses)
        main_query = cleaned_chunks[-1][1]
        return f"{formatted_ctes}\n\n{main_query}"

    return cleaned_chunks[0][1]





def write_sql_to_zip(node_dict):
    # Get SQL string
    sql = consolidated_sql(node_dict)

    # Write to sqlfile.sql
    with open("sqlfile.sql", "w") as f:
        f.write(sql)

    # Create a zip file and add sqlfile.sql to it
    with zipfile.ZipFile("sqlfile.zip", "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write("sqlfile.sql")

    # logger.info("SQL written to sqlfile.sql and compressed as sqlfile.zip")




# sql = consolidated_sql(node_dict)
# logger.info("Consolidated SQL:")
# logger.info(sql)






# def format_sql_query(query):
#     # Format the SQL query with sqlparse
#     formatted = sqlparse.format(
#         query,
#         keyword_case='upper',  # Uppercase keywords (SELECT, FROM, etc.)
#         identifier_case='lower',  # Lowercase identifiers (column/table names)
#         reindent=True,  # Add basic indentation
#         indent_width=4,  # 4 spaces per indent level
#         wrap_after=80,  # Line width
#         comma_first=False,  # Commas at end of line
#         use_space_around_operators=True,  # Spaces around =, +, etc.
#         reindent_aligned=True 
#     )
#     return formatted.strip()

# sql = consolidated_sql(node_dict)
# # Add the new logic to replace quotes
# sql = sql.replace('"4', '')  # First replace all "4 with blank
# sql = sql.replace('"', '')   # Then replace all " with blank
# sql = sql.strip().strip('"').rstrip(";")
# formatted_query = format_sql_query(sql)
# logger.info(formatted_query)





async def add_optimized_column_parallel(ds_name, node_dict, max_concurrent=20):
    """Add optimized column to primary nodes in parallel (updates original dict)"""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(node_name, node_data):
        async with semaphore:
            await optimize_primary_sql(ds_name, node_name, node_data)

    tasks = [
        run_with_semaphore(node_name, node_data)
        for node_name, node_data in node_dict.items()
        if node_data.get("Is Primary", "").lower() != "no"
    ]
    await asyncio.gather(*tasks)




async def fill_add_optimized_column_parallel(ds_name, node_dict, max_iterations=3, max_concurrent=20):
    logger = logging.getLogger(__name__)
    iteration = 0
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(node_name, node_data):
        async with semaphore:
            await optimize_primary_sql(ds_name, node_name, node_data)

    while iteration < max_iterations:
        iteration += 1
        chunks_to_process = set()

        nodes_missing_before = {
            k for k, v in node_dict.items()
            if str(v.get("Is Primary", "")).lower() == "yes" and not v.get("Chunk SQL Primary Optimized")
        }

        for node_name in nodes_missing_before:
            chunk_num = node_dict[node_name].get("Chunk Number")
            if chunk_num:
                chunks_to_process.add(chunk_num)

        if not chunks_to_process:
            break

        chunks = {}
        for node_name, node_data in node_dict.items():
            chunk_num = node_data.get("Chunk Number")
            if chunk_num in chunks_to_process:
                chunks.setdefault(chunk_num, []).append(node_name)

        tasks = []
        for chunk_num, nodes in chunks.items():
            for node_name in nodes:
                node_data = node_dict[node_name]
                tasks.append(run_with_semaphore(node_name, node_data))

        await asyncio.gather(*tasks)

        nodes_missing_after = {
            k for k, v in node_dict.items()
            if str(v.get("Is Primary", "")).lower() == "yes" and not v.get("Chunk SQL Primary Optimized")
        }

        if nodes_missing_after == nodes_missing_before:
            break




async def base_alias_table_parallel(node_dict, original_source_schema, max_concurrent=20):
    """Add optimized column to primary nodes in parallel (updates original dict)"""
    logger.info("-------------------------------------------------------------------------------")
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(node_name, node_data):
        async with semaphore:
            await base_alias_table(node_name, node_data, original_source_schema)

    tasks = [
        run_with_semaphore(node_name, node_data)
        for node_name, node_data in node_dict.items()
        if node_data.get("Is Primary", "").lower() != "no"
    ]
    await asyncio.gather(*tasks)



async def base_alias_table(node_name, node_data, original_source_schema):
    """Process a single node to fix table alias usage based on base tables."""

    chunk_number = node_data.get("Chunk Number")
    base_table_list = {
        tbl: cols
        for v in original_source_schema.values()
        if v.get("Chunk Number") == chunk_number
        for tbl, cols in v["original_leaf_node_columns"].items()
    }
    chunk_numbers = list({v.get("Chunk Number") for v in original_source_schema.values()})

    original_sql = node_data.get("Chunk SQL Primary Optimized")
    if not original_sql:
        return node_name

    # If base_table_list is empty or chunk_number not found, just copy SQL
    if chunk_number not in chunk_numbers:
        node_data["Chunk SQL Primary Optimized Base"] = original_sql
        return node_name

    if node_data.get("Is Primary", "").lower() != "no":
        optimized_sql = original_sql
        last_valid_sql = original_sql
        error_feedback = ""
        optimize_prompt = f"""
        Given a SQL query, your task is to correct table alias usage based on a provided list of base tables.

        ### Instructions:
        - You are given a list of base tables: {base_table_list}.
        - Only these tables are of interest.
        - If any of these tables are used with an alias, replace **all occurrences** of the alias (in SELECT, JOIN, WHERE, GROUP BY, ORDER BY, etc.) with the full table name.
        - Do not modify aliases for any tables not present in the base table list.
        - Return only the corrected SQL query as plain text. Do not include explanations or comments.
        - If a field name and its alias are the same, do not force an alias. 
            Example: SELECT name AS name -> keep as SELECT name.

        Important Note:
        -- Focus on completeness of SQL query. It means SQL must not end with {INCOMPLETE_KEYWORDS}. 

        ### Now process this SQL query:
        {optimized_sql}
        """

        optimize_prompt_base = optimize_prompt
        for attempt in range(3):
            # API call
            sql_text = await api_call_with_retry_async("Gemini", optimize_prompt, task_type="sql")
            if not sql_text:
                continue

            # Clean output
            sql_text = remove_before_first_select(sql_text)
            sql_text = remove_non_sql_context(sql_text)
            sql_text = remove_unwanted_patterns(sql_text)
            cleaned_lines = remove_sql_comments(sql_text.splitlines())
            optimized_sql = "\n".join(cleaned_lines).strip()

            # ✅ Validate SQL
            valid, msg = is_valid_sql(optimized_sql)
            if valid:
                last_valid_sql = optimized_sql
                break
            else:
                logger.info(f"Attempt {attempt+1} failed for {node_name}: {msg}")
                error_feedback = f"\n\n⚠️ The previous SQL had issues: {msg}. Please fix this. Lst returned SQL was:\n{optimized_sql}"
                optimize_prompt = optimize_prompt_base + error_feedback

        node_data["Chunk SQL Primary Optimized Base"] = optimized_sql if valid else last_valid_sql

    return node_name


async def fill_base_alias_table_parallel(node_dict, original_source_schema, max_iterations=3, max_concurrent=20):
    """
    Fill missing 'Chunk SQL Primary Optimized Base' for primary nodes in parallel
    using base_alias_table function.
    """
    logger = logging.getLogger(__name__)
    iteration = 0
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(node_name, node_data):
        async with semaphore:
            await base_alias_table(node_name, node_data, original_source_schema)

    while iteration < max_iterations:
        iteration += 1

        nodes_missing_before = {
            k for k, v in node_dict.items()
            if str(v.get("Is Primary", "")).lower() == "yes" 
            and not v.get("Chunk SQL Primary Optimized Base")
        }

        if not nodes_missing_before:
            break

        tasks = [
            run_with_semaphore(node_name, node_dict[node_name])
            for node_name in nodes_missing_before
        ]
        await asyncio.gather(*tasks)

        nodes_missing_after = {
            k for k, v in node_dict.items()
            if str(v.get("Is Primary", "")).lower() == "yes" 
            and not v.get("Chunk SQL Primary Optimized Base")
        }

        if nodes_missing_after == nodes_missing_before:
            break


async def optimize_primary_sql(ds_name: str, node_name: str, node_data: dict) -> str:
    """
    Process a single node to add optimized SQL column if it's marked as primary.
    - Removes dataset prefix from SQL.
    - Validates SQL syntax.
    - Retries up to 5 times with error feedback if invalid.
    """

    optimize_prompt = f"""**Instructions:**
            Analyze the provided SQL query and remove the dataset {ds_name} prefix from all table references. 
            This applies to tables in both the FROM and JOIN clauses.

            **Rules:**
            - If a field name and its alias are the same after mapping, do not force an alias. 
              Example: SELECT name AS name -> keep as SELECT name.
            - Don't modify any other logic.

            **Transformation Rule:**
            - Input format: project.{ds_name}.tablename or {ds_name}.tablename
            - Output format: tablename

            **Example:**
            Before: SELECT * FROM `{ds_name}.user_events`;
            After:  SELECT * FROM user_events;

            **Three primary activities:**
            - Remove {ds_name}. from all table references.
            - Ensure no backticks around table names.
            - Fix aliases as per rule above.
            """
    optimize_prompt_base = optimize_prompt

    if node_data.get("Is Primary", "").lower() != "no" and "Chunk SQL Primary" in node_data:

        original_sql = node_data["Chunk SQL Primary"]
        optimized_sql = original_sql
        last_valid_sql = original_sql  # fallback to the last valid SQL
        error_feedback = ""

        for attempt in range(5):
            # Make API call to optimize SQL
            full_prompt = f"{optimize_prompt}\n\n{optimized_sql}"
            try:
                sql_text = await api_call_with_retry_async("Gemini", full_prompt, task_type="sql")
            except Exception as e:
                logger.error(f"Error inside api_call_with_retry_async: {e}")
                continue

            if not sql_text:
                continue

            # Cleaning steps
            sql_text = remove_before_first_select(sql_text)
            sql_text = remove_non_sql_context(sql_text)
            sql_text = remove_unwanted_patterns(sql_text)
            cleaned_lines = remove_sql_comments(sql_text.splitlines())
            optimized_sql = "\n".join(cleaned_lines).strip()
            # ✅ Validate SQL
            valid, msg = is_valid_sql(optimized_sql)

            if f"{ds_name}." not in optimized_sql and valid:
                last_valid_sql = optimized_sql
                break
            else:
                logger.warning(f"Attempt {attempt+1} failed for {node_name}: {msg}")
                if valid:
                    last_valid_sql = optimized_sql

                error_feedback = f"\n\n⚠️ The previous SQL had issues: {msg}. Please fix this. My previous SQL was:\n{optimized_sql}"
                optimize_prompt = optimize_prompt_base + error_feedback

        # Save optimized SQL back into node_data
        node_data["Chunk SQL Primary Optimized"] = optimized_sql if valid else last_valid_sql

    return node_name






async def bigquery_sql_parallel(node_dict, max_concurrent=20):
    """Add optimized column to primary nodes in parallel (updates original dict)"""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(node_name, node_data):
        async with semaphore:
            await generate_bigquery_sql(node_name, node_data)

    tasks = [
        run_with_semaphore(node_name, node_data)
        for node_name, node_data in node_dict.items()
        if node_data.get("Is Primary", "").lower() != "no"
    ]
    await asyncio.gather(*tasks)







async def generate_bigquery_sql(node_name, node_data):
    """Process a single node to add optimized SQL column if it's marked as primary"""
    if node_data.get("Is Primary", "").lower() != "no" and "Chunk SQL Primary" in node_data:
        
        optimize_prompt = """Convert the following BigQuery SQL query to proper Google Cloud BigQuery syntax using BigQuery-specific functions and standards.

                            - Correct any syntax errors and remove redundancies.  
                            - Do **not** change the order of fields, their aliases, or table aliases.  
                            - Convert any functions or syntax to standard BigQuery equivalents.  
                            - Preserve the original logic, field order, and function of the query.  
                            - Refer to BigQuery documentation for any specific functions or syntax changes.
                            - Handle any specific BigQuery features or limitations.
                            - Handle date conversions and functions to ensure BigQuery compatibility. 
                            - string functions should be converted to BigQuery string functions.
                            - date functions should be converted to BigQuery date functions.
                            - timestamp functions should be converted to BigQuery timestamp functions.
                            - **Do not** include explanations—return only the optimized SQL output.
                        """

        # Get the original SQL
        original_sql = node_data["Chunk SQL Primary Optimized"]
        # Make API call to optimize SQL
        full_prompt = f"{optimize_prompt}\n\n{original_sql}"
        sql_text = await api_call_with_retry_async('Gemini', full_prompt, task_type='sql')

        if sql_text:
            sql_text = remove_before_first_select(sql_text)
            sql_text = remove_non_sql_context(sql_text)
            sql_text = remove_unwanted_patterns(sql_text)
            cleaned_lines = remove_sql_comments(sql_text.splitlines())
            optimized_sql = "\n".join(cleaned_lines)

            # Update the node data in-place
            node_data["Chunk SQL Primary BigQuery"] = optimized_sql

    return node_name

async def construct_sql_corrected_chunk_for_union(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, base, derived, ds_name, chunk_schema):
    project_id = 'dev-hanacvsql'
    prompt = f"""You are an expert SQL validator and transformer for constructing UNION SQL queries. Your task is to correct and optimize the provided **Manually Converted SQL** to ensure it accurately reflects the logic of the **Source SQL** while adhering to the structure of the **Target Structure SQL**.



### 📌 Inputs

1. **Source SQL**  This contains business logic: filters, joins, expressions.
```sql
{source_sql}
```

2. **Target Structure SQL**  This shows the expected structure, aliases, aggregation, and final format.
```sql
{target_structure_sql}
```
3. **My Attempt (Manually Converted SQL)**  This is my draft combining the two above.
```sql
{manually_converted_sql}
```


---

### 🎯 Objective:

Fix the **Manually Converted SQL** to meet these conditions:

1. **Preserve Logic** from the Source SQL:
   - All JOINs, WHERE conditions, calculations, aliasing, and field-level granularity must be retained.

2. **Match the Structure** of the Target SQL:
   - Follow column layout, aggregations, GROUP BY, and HAVING clauses.
   - Use the column aliases defined in the Target SQL.

3. **Use Field Names from Source SQL**:
   - Replace field names from the Target SQL with the corresponding Source SQL field names but keep targer sql's fields alais names.

4. **Adhere to Schema**:
   - All fields and conditions must refer to the table `{schema}`.
   - If anything is outside the schema, correct it by referring to the Target SQL.

5. **No Subqueries or CTEs**:
   - Do not use nested SELECT statements or WITH clauses.

6. **Validation Rule**:
   - If the Manually Converted SQL is already correct, return it without any changes.

7. **Select Correct fields from correct table**:
   - Sometime you construct query from many tables. So pick fields from exact tables.

---

### ⚠ Rules:

- Do **not** skip any business logic from the Source SQL.
- Do **not** return any explanation or commentary.
- Output only a **clean, complete, syntactically correct SQL query** with the correct structure and logic.
- This output Union SQL must contain all underlying Source SQL logic. Don't miss any logic.
- Ensure that the number of columns in each SELECT statement within a UNION is the same. If a column is missing in any SELECT, assign it a NULL so that the column count matches across all SELECT statements


### Joining Condition Rule for Derived Columns:
    If a JOIN condition references a column that does not exist in the base table but is derived in table table (e.g., table2.dummy = 45), then replace that reference(dummy) with the original expression(45) (constant or calculation).
    Example: table.value = table2.dummy should become table.value = 45.

####Important Instruction:
    When generating SQL queries, never use tables from {derived} as base tables. These tables are intermediate or derived, not intended to be queried directly.
    Instead, always construct queries using only the true base tables listed in {base}, and their columns.
    Use simple aliase table names for tables in the final SQL.
    Ensure the logic is rooted in the schema and relationships of the base tables, not the derived ones. Your generated query must contain fields only from {base}.
    This most needed instuction. If you directly the derived table instead of derived, final sql will throw error. This will lead system failure. I hope you understand the schmea and base tables for Bigquery output.
    Always keep to maintain target sql field structure, order and alias names. This generated sql will be used in other sql queries. So failing to keep alias names will cause runtime unavailability issue. 
    All the base tables are present in Bigquery under dataset: {ds_name}
    Make sure to use these {ds_name}.table as base tables and available columns in the final sql. Table format: `{ds_name}.tablename` . Otherwise sql will throw error.
    Never skip any constant mapping in the final sql. Constant mapping is important. Never miss any filter condition from source sql.
    Make Sure that you don't miss any joining conditions if source sql has any.
    Give top most priority for fitting alias names.

    ** Ensure that the number of columns in each SELECT statement within a UNION is the same. If a column is missing in any SELECT, assign it a NULL so that the column count matches across all SELECT statements
""" 

    attempt = 0
    base_prompt = prompt
    syntax_error = ""
    deepseek_output = ""
    subquery_found = False

    for attempt in range(5):
        # logger.info(attempt)
        # 
        sql_text = await api_call_with_retry_async('Gemini', prompt,task_type= 'sql')  # Capture the return value
        prompt = ""

        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)
        if attempt == 4:
            return cleaned_sql
        # Validate SQL
        valid, msg = is_valid_sql(cleaned_sql)
        if not valid:
            syntax_error = (
                f"\nThe previous attempt failed to generate valid SQL. "
                f"Please retry with the same prompt ensuring the SQL is valid. "
                f"Previous attempt:  (Error: {msg})"
            )
            logger.info(f"Node:{node_name} attempt {attempt} msg {msg}")
        else:
            syntax_error = ""


        # subquery_found = (
        #     has_subquery_sqlglot(cleaned_sql) if not syntax_error else False
        # )

        cleaned_sql = cleaned_sql.strip()
        sql_input = cleaned_sql.replace("\n", " ")

        # chunkwise_error = await asyncio.to_thread(run_bigquery_sql, sql_input)
        chunkwise_error = await run_bigquery_sql_async(sql_input)

        if chunkwise_error == "SUCCESS":
            chunkwise_error = None
        else:
            logger.info(f"Chunkwise SQL Error: {chunkwise_error}")

        # Combine errors safely
        if syntax_error and chunkwise_error:
            syntax_error += "\n" + chunkwise_error
        elif not syntax_error:
            syntax_error = chunkwise_error
        else:
            syntax_error = None

        not_found_match = None
        missing_column = None
        missing_grouped_column = None

        not_found_pattern = r"not found inside"
        if chunkwise_error:
            not_found_match = re.search(not_found_pattern, chunkwise_error)
                # Deepseek error dertermine
        if syntax_error and not not_found_match:
            deepseek_input = (
                f"{sql_input}\n"
                f"When I execute the above SQL in BigQuery editor, I get the below error:\n"
                f"{syntax_error}\n"
                f"Give a detailed explanation about the error and how it can be fixed properly."
                f"Also suggest some fix based on your understanding."
                f"You must suggest fixes only related to SQL syntax errors. Do not suggest any changes related to business logic."
                f"Do not suggest any changes related to subquery or CTE."
            )
            deepseek_output = await api_call_with_retry_async('gemini-3.1-flash-lite-preview', deepseek_input, task_type='sql')
        elif not_found_match:
            deepseek_output = f"The error is related to missing column.This column may be renamed between source and target structure. So please fix it.Refer to the schema: {chunk_schema}"
        # Exit conditions
        if not syntax_error:
            logger.info(f"Intermediate valid SQL for {node_name} on attempt {attempt+1}.")
            break

        # Prepare error feedback for next attempt
        error_messages = []
        if syntax_error:
            error_messages.append(f"SQL Syntax Error: {syntax_error}")


        prompt = (
            f"{base_prompt}\n\n"
            f"Previous attempt resulted in:\n{cleaned_sql}\n\n"
            f"Errors detected:\n{' | '.join(error_messages)}\n\n"
            f"Make adjustments and generate valid sql without any error\n\n"
            f"I suggest you some fix based on my understanding{deepseek_output}"
            f"Make sure datatypes of column used in select statements with UNION ALL aligned. Becasue datatype mismatch will throw error. So typecast it properly if needed(CAST or SAFE_CAST)."
            f"No of columns within SELECT statments within UNION ALL should match exactly.If not found, assign NULL to the column count."
            f"Queries in UNION ALL have matched column count"
        )
    
    return cleaned_sql  # Return the cleaned SQL if no errors are found

def sql_formatting(text):
        sql_text = text
        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)
        return cleaned_sql

not_found_match = None
missing_column = None
missing_grouped_column = None
async def construct_sql_corrected_chunk(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, df, base, derived, ds_name, tnf, chunk_schema):
    project_id = 'dev-hanacvsql'

    prompt = f"""You are an expert SQL validator and transformer. Your task is to correct and optimize the provided **Manually Converted SQL** to ensure it accurately reflects the logic of the **Source SQL** while adhering to the structure of the **Target Structure SQL**.

    Important Note: No of columns in the final SQL must be {tnf}. Target structure sql contains {tnf} columns. So final sql must contain {tnf} columns.

---

### 📌 Inputs

1. **Source SQL**  This contains business logic: filters, joins, expressions.
```sql
{source_sql}
```

2. **Target Structure SQL**  This shows the expected structure, aliases, aggregation, and final format.
```sql
{target_structure_sql}
```
3. **My Attempt (Manually Converted SQL)**  This is my draft combining the two above.
```sql
{manually_converted_sql}
```
4. **Field Mapping DataFrame**  Maps Source SQL field names to Target SQL aliases:
```python
{df}
```

---

### 🎯 Objective:

Fix the **Manually Converted SQL** to meet these conditions:

1. **Preserve Logic** from the Source SQL:
   - All JOINs, WHERE conditions, calculations, aliasing, and field-level granularity must be retained.

2. **Match the Structure** of the Target SQL:
   - Follow column layout, aggregations, GROUP BY, and HAVING clauses.
   - Use the column aliases defined in the Target SQL.

3. **Use Field Names from Source SQL**:
   - Replace field names from the Target SQL with the corresponding Source SQL field names using the provided DataFrame.

4. **Adhere to Schema**:
   - All fields and conditions must refer to the table `{schema}`.
   - If anything is outside the schema, correct it by referring to the Target SQL.

5. **No Subqueries or CTEs**:
   - Do not use nested SELECT statements or WITH clauses.

6. **Validation Rule**:
   - If the Manually Converted SQL is already correct, return it without any changes.

7. **Select Correct fields from correct table**:
   - Sometime you construct query from many tables. So pick fields from exact tables.

8. **If Target sql is Join / Projection nodes - 
    **Ignore fields from source sql which are not present in target structure sql**
    - If any field is present in source sql but not in target structure sql, ignore it.
    - Ignored field must not be present in final sql.( Agrregtion and Group by too)
    - Unnecessary of ignored field's group by and aggregation must be removed. They will lead issue.

9. **Ensure that the number of columns in the final SQL matches the Target Structure SQL**:
   - Ensure you keep formula and calculated columns
---

### ⚠ Rules:

- Do **not** skip any business logic from the Source SQL.
- Ignore fields from source sql which are not present in target structure sql( Aggregation and Group by too)
- Do **not** return any explanation or commentary.
- Output only a **clean, complete, syntactically correct SQL query** with the correct structure and logic.
- Output SQL must contain all underlying Source SQL logic. Don't miss any logic.

### Joining Condition Rule for Derived Columns:
    If a JOIN condition references a column that does not exist in the base table but is derived in table table (e.g., table2.dummy = 45), then replace that reference(dummy) with the original expression(45) (constant or calculation).
    Example: table.value = table2.dummy should become table.value = 45.

####Important Instruction:
    When generating SQL queries, never use tables from {derived} as base tables. These tables are intermediate or derived, not intended to be queried directly.
    Instead, always construct queries using only the true base tables listed in {base}, and their columns.
    Use simple aliase table names for tables in the final SQL.
    Ensure the logic is rooted in the schema and relationships of the base tables, not the derived ones. Your generated query must contain fields only from {base}.
    This most needed instuction. If you directly the derived table instead of derived, final sql will throw error. This will lead system failure. I hope you understand the schmea and base tables for Bigquery output.
    Always keep to maintain target sql field structure, order and alias names. This generated sql will be used in other sql queries. So failing to keep alias names will cause runtime unavailability issue. 
    All the base tables are present in Bigquery under dataset: {ds_name}
    Make sure to use these {ds_name}.table as base tables and available columns in the final sql. Table format: `{ds_name}.tablename` . Otherwise sql will throw error.
    Make Sure that you don't miss any joining conditions if source sql has any.
    Give top most priority for fitting alias names.
""" 
    base_prompt = prompt
    deepseek_output = ""
    attempt = 0
# Logavan
    for attempt in range(5):
        syntax_error = ""
        chunkwise_error = ""
        logger.info(f"Node:{node_name} attempt {attempt}")


        sql_text = await api_call_with_retry_async('Gemini', prompt, task_type='sql')  # Capture the return value
        # logger.info(f"Node:{node_name} attempt {attempt} sql :{sql_text}")
        prompt = " "

        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)
        # Validate SQL
        if attempt == 4:
            return cleaned_sql
        
        valid, msg = is_valid_sql(cleaned_sql)
        # logger.info(f"Node:{node_name} attempt {attempt} valid :{valid} msg :{msg}")
        if not valid:
            syntax_error = (
                f"\nThe previous attempt failed to generate valid SQL. "
                f"Please retry with the same prompt ensuring the SQL is valid. "
                f"Previous attempt:  (Error: {msg})"
            )
            logger.info(f"Node:{node_name} attempt {attempt} msg {msg}")
            logger.info(f"Node:{node_name} attempt {attempt} INVALID SQL:\n{cleaned_sql}")
        else:
            syntax_error = None

        subquery_found = has_subquery_sqlglot(cleaned_sql) 


        cleaned_sql = cleaned_sql.strip()
        sql_input = cleaned_sql.replace("\n", " ")
        # logger.info(f"Node:{node_name} attempt {attempt} sql input for bq :{sql_input}")

        # chunkwise_error = await asyncio.to_thread(run_bigquery_sql, sql_input)
        chunkwise_error = await run_bigquery_sql_async(sql_input)
                
        if chunkwise_error == "SUCCESS":
            chunkwise_error = None
        else:
            logger.info(f"Node:{node_name} attempt {attempt} chunkwise_error :{chunkwise_error}")
            logger.info(f"Node:{node_name} attempt {attempt} INVALID BQ SQL:\n{sql_input}")
            
            # === DETERMINISTIC FIX SUGGESTION ===
            # Instead of fixing directly, we suggest the fix to the LLM
            # to avoid loops and ensure the LLM integrates it correctly.
            deterministic_suggestion = ""
            fixed_sql, was_fixed, fix_desc = fix_bigquery_error(cleaned_sql, chunkwise_error)
            if was_fixed:
                logger.info(f"Node:{node_name} - Found deterministic fix suggestion: {fix_desc}")
                deterministic_suggestion = f"\n\n🛠️ Suggested Fix: {fix_desc}\nConsider this corrected SQL fragment as a hint: \n{fixed_sql}"
                # We don't overwrite cleaned_sql here - we let the LLM do it in the next attempt

  

        if syntax_error and chunkwise_error:
            syntax_error = f"{syntax_error}\n{chunkwise_error}"
        elif syntax_error and not chunkwise_error:
            syntax_error = syntax_error
        elif chunkwise_error and not syntax_error:
            syntax_error = chunkwise_error
        else:
            syntax_error = None


        if not syntax_error and chunkwise_error is None and not subquery_found:
            logger.info("___________________________________")
            logger.info(f"Intermediate Exit {attempt} sql :{node_name}")
            return cleaned_sql  


    #------------------------------------------Subquery fix loop
        if (chunkwise_error == "SUCCESS" or chunkwise_error is None) and subquery_found:
            count = 0
            correct_sql = cleaned_sql
            for count in range(5):
                subquery_prompt = f"""
                I have an SQL query that currently uses subqueries or CTEs. 
                Rewrite the query as a **single-level flattened query** without subqueries or WITH clauses.  

                🚫 Restrictions:
                - Do NOT use subqueries in the FROM, JOIN, WHERE, or SELECT clause.  
                - Do NOT use Common Table Expressions (CTEs) with WITH.  
                - Keep everything inside one main SELECT with explicit JOINs.  
                - Do NOT change the logic, field order, or alias names.

                ✅ Requirements:
                1. Replace subqueries and CTEs with JOIN-based logic.  
                2. Move computed fields directly into JOIN conditions or SELECT list.  
                3. Keep all original columns and preserve the same logic.  
                4. Ensure the output is valid BigQuery SQL.  
                5. Query should be easy to analyze, transform, and debug.  

                🔍 Why is this required?  
                Flattened queries are easier to analyze and transform automatically. Subqueries and CTEs add complexity and ambiguity in data lineage, making it difficult for automated tools to process.  

                🛠️ How to fix:  
                Here are some practical transformations you must follow:  

                🔸 **Example 1: Simple Subquery (IN clause)**  
                ❌ Original:  
                SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers);  
                ✅ Rewrite:  
                SELECT orders.* FROM orders  
                JOIN customers ON orders.customer_id = customers.id;  

                🔸 **Example 2: UNION inside Subquery**  
                ❌ Original:  
                SELECT * FROM orders WHERE product_id IN (  
                    SELECT product_id FROM table_a  
                    UNION  
                    SELECT product_code FROM table_b  
                );  
                ✅ Rewrite:  
                SELECT orders.* FROM orders JOIN table_a ON orders.product_id = table_a.product_id  
                UNION  
                SELECT orders.* FROM orders JOIN table_b ON orders.product_id = table_b.product_code;  

                🔸 **Example 3: EXISTS clause**  
                ❌ Original:  
                SELECT * FROM employees e WHERE EXISTS (SELECT 1 FROM salaries s WHERE s.emp_id = e.id);  
                ✅ Rewrite:  
                SELECT DISTINCT e.* FROM employees e  
                JOIN salaries s ON e.id = s.emp_id;  

                🔸 **Example 4: CTE (WITH clause)**  
                ❌ Original:  
                WITH recent_orders AS (SELECT * FROM orders WHERE order_date > '2023-01-01')  
                SELECT * FROM recent_orders;  
                ✅ Rewrite:  
                SELECT * FROM orders WHERE order_date > '2023-01-01';  

                🔸 **Example 5: Subquery to JOIN**  
                ❌ Original:  
                SELECT order_id, customer_id, order_date  
                FROM orders  
                WHERE customer_id = 123  
                AND order_id IN (  
                    SELECT order_id  
                    FROM orders  
                    WHERE order_date > '2023-01-01'  
                );  

                ✅ Rewrite:  
                SELECT o1.order_id, o1.customer_id, o1.order_date  
                FROM orders o1  
                JOIN orders o2 ON o1.order_id = o2.order_id  
                WHERE o1.customer_id = 123  
                AND o2.order_date > '2023-01-01';  

                💡 Tip: When flattening logic, always validate results remain consistent. Use JOINs carefully to avoid duplicates or incorrect filters.  

                📌 Now rewrite this query into a **flattened single-level BigQuery SQL** without subqueries
                """
            
            explain_in_detail = f"""
            Explain in detail how to fix the subquery issue in the above SQL """

            deepseek_input_for_subquery_fix = f"""{subquery_prompt}\n{cleaned_sql}\n{explain_in_detail}"""

            subquery_fix = await api_call_with_retry_async('Gemini', deepseek_input_for_subquery_fix, task_type='sql')
            # logger.info(f"Node:{node_name} attempt {attempt} subquery_fix :{subquery_fix}")
            gemini_fix_prompt = f"""Rewrite the below SQL query by fixing subquery issue.
            - Do not use any subquery or CTE. Return only single level query.
            - Do not change any logic, field order or alias names. My previous error free. Only issue is subquery.
            Bigquer SQL: {cleaned_sql}
              My Suggestion: {subquery_fix}
              Return only sql. No explanation needed."""
            
            subquery_fix_api = await api_call_flash_async('Gemini', gemini_fix_prompt, task_type='sql')
            # logger.info(f"Node:{node_name} attempt {attempt} subquery_fix_api :{subquery_fix_api}")

            cleaned_sql = sql_formatting(subquery_fix_api)
            logger.info(f"Node:{node_name} attempt {attempt} subquery fixed sql :{cleaned_sql}")
            error_after = await run_bigquery_sql_async(cleaned_sql)
            if error_after == "SUCCESS":
                if not has_subquery_sqlglot(cleaned_sql):
                    logger.info("Breaking from subquery fix loop as no subquery found")
                    subquery_found = False
                    logger.info("___________________________________")
                    logger.info(f"Intermediate Exit {attempt} sql :{node_name}")
                    return cleaned_sql  # Return the cleaned SQL if no errors are found

            else:
                subquery_prompt = f"""The subquery fix you provided is giving error in Bigquery. Fix the below error by rewriting the sql without subquery or CTE.
                My previous error free sql: {correct_sql}
                Your subquery fixed sql: {cleaned_sql}. But error is there: {error_after}"""


                # Regex to match "not found inside"
        
        not_found_match = None
        missing_column = None
        missing_grouped_column = None
        not_found_pattern = r"not found inside"

        if chunkwise_error:
            not_found_match = re.search(not_found_pattern, chunkwise_error)


        # Deepseek error dertermine
    
        if syntax_error and not not_found_match:
            deepseek_input = (
                f"{sql_input}\n"
                f"When I execute the above SQL in BigQuery editor, I get the below error:\n"
                f"{syntax_error}\n"
                f"{base} - These are base tables present in Bigquery under dataset {ds_name}. Use these tables only in the final sql.\n"
                f"Give an explanation about the error and how it can be fixed properly."
                f"Your output must not have any subquery or CTE."
                f"Focus on fix only error."
            )
            deepseek_output = await api_call_with_retry_async('gemini-3.1-flash-lite-preview', deepseek_input, task_type='sql')
            # logger.info(f"Deepseek output:{deepseek_output}")
        elif not_found_match:
            deepseek_output = f"The error is related to missing column.This column may be renamed between source and target structure. So please fix it.Refer to the schema: {chunk_schema}"
        

        # Prepare error feedback for next attempt
        error_messages = []
        if syntax_error:
            error_messages.append(f"SQL Syntax Error: {syntax_error}")

        prompt = (
            f"{base_prompt}\n\n"
            f"Previous attempt resulted in sql:\n{cleaned_sql}\n\n"
            f"Fix this previous attmept query. Because this is formatted and previous error removed.\n\n"
            f"Errors detected:\n{' | '.join(error_messages)}\n\n"
            f"Make adjustments and generate valid sql without any error\n\n"
            f"I suggest you some fix based on my understanding\n"
            f"{deepseek_output}"
            f"{deterministic_suggestion if 'deterministic_suggestion' in locals() else ''}"
        )

        if chunkwise_error:

            match = re.search(r"Name (\w+) not found", chunkwise_error)
            missing_column = match.group(1) if match else None

            # Regex to capture column name before "which is neither grouped nor aggregated"
            match_grouped_column = re.search(r"references ([\w\.]+) which is neither grouped nor aggregated", chunkwise_error)

            missing_grouped_column = match_grouped_column.group(1) if match_grouped_column else None

        # Generate a prompt for LLM
        llm_prompt = ""
        if attempt > 3 and missing_column:
            llm_prompt = (
                f""
                f"The SQL query is failing because the column '{missing_column}' is missing. "
                f"Please modify the SELECT statement to assign NULL for '{missing_column}' (E.g) select Null as {missing_column} ......"
                f"if it does not exist, so that missing columns are handled gracefully."
                f"""Joining Condition Rule for Derived Columns:
                    If a JOIN condition references a column that does not exist in the base table but is derived in table table (e.g., table2.dummy = 45), then replace that reference(dummy) with the original expression(45) (constant or calculation).
                    Example: table.value = table2.dummy should become table.value = 45."""
            )
            prompt += llm_prompt
                # Generate a prompt for LLM
        

        if attempt == 1 and missing_grouped_column:
            llm_prompt = (
                f""
                f"The SQL query is failing because the column '{missing_grouped_column}' is neither grouped nor aggregated. "
                f"This usually happens when a column is aggregated but also referenced directly in another calculated column (formula), "
                f"or when a non-aggregated column is missing from the GROUP BY clause. "
                f"For example, the query: SELECT id, SUM(value), score * value AS total FROM sales GROUP BY id "
                f"will fail because 'value' is used outside of the aggregation. "
                f"The correct version is: SELECT id, SUM(value), score * SUM(value) AS total FROM sales GROUP BY id. "
                f"Always ensure that non-aggregated columns are included in the GROUP BY clause, "
                f"and that calculated columns use aggregated values properly."
            )

            prompt += llm_prompt

        if attempt >= 2 and missing_grouped_column:
            llm_prompt = (
                f""
                f"FIX REQUIRED: Column '{missing_grouped_column}' is neither grouped nor aggregated in BigQuery. "
                f"IMPORTANT: Use ANY_VALUE() for any column that is NOT in GROUP BY and NOT an aggregate function. "
                f"For example: "
                f"WRONG:  SELECT department, name, SUM(salary) FROM employees GROUP BY department "
                f"CORRECT: SELECT department, ANY_VALUE(name), SUM(salary) FROM employees GROUP BY department "
                f"Apply ANY_VALUE() to '{missing_grouped_column}' or any other non-grouped columns. "
                f"Rewrite the entire SQL with this fix applied."
            )

            prompt += llm_prompt
        
        if chunkwise_error:
            # Regex pattern to capture expected and got parts
            match = re.search(r"Expected\s+\"([^\"]+)\"\s+but got keyword\s+(\w+)", chunkwise_error)

            expected_token = match.group(1) if match else None
            got_token = match.group(2) if match else None

            if attempt > 3 and expected_token and got_token:
                llm_prompt = (
                    f""
                    f"The parser expected '{expected_token}' but instead found '{got_token}'. "
                    f"This usually indicates that a parenthesis is missing or misplaced, "
                    f"or there is an extra comma before the {got_token} clause. "
                    f"To resolve this: "
                    f"1. Check that every opening parenthesis has a matching closing parenthesis. "
                    f"2. Ensure there are no trailing commas before FROM, GROUP BY, or ORDER BY. "
                    f"3. Verify that functions like CAST, LEFT, RIGHT, SUBSTR, and CASE are properly closed. "
                    f"4. Confirm that the SQL syntax near the {got_token} clause matches the expected structure. "
                    f"5. Make sure the token '{expected_token}' is correctly placed before '{got_token}'. "
                    f"Correct the SQL accordingly so that the parser receives the expected syntax."
                )

            prompt += llm_prompt

        if syntax_error and "Parameterized types are not allowed in CAST expressions" in syntax_error:
            err = syntax_error.split("Parameterized types are not allowed in CAST expressions")[-1].strip()
            if attempt > 3 and err:
                llm_prompt = (
                        f"{sql_input}\n\n"
                        f"I got this BigQuery error:\n{err}\n\n"
                        f"Explain why this happens and provide the corrected SQL.\n\n"
                        f"Important:\n"
                        f"- BigQuery does not allow CAST with precision/scale like NUMERIC(17,3).\n"
                        f"- Use CAST(... AS NUMERIC) instead.\n"
                        f"- If decimals are needed, wrap with ROUND(..., 3).\n\n"
                        f"Return only the corrected SQL without any explanation."
                    )   
            
                prompt += llm_prompt
        # logger.info(f"Node:{node_name} attempt {attempt} final prompt :{prompt}")   
    
        
    
    logger.info(f"final {attempt} sql is cleaned sql:{node_name}")

    return cleaned_sql  # Return the cleaned SQL if no errors are found



async def construct_sql_corrected_chunk_aggr_rank(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, df, base, derived, ds_name, tnf, chunk_schema):
    project_id = 'dev-hanacvsql'
     
    prompt = f"""You are an expert SQL validator and transformer. Your task is to correct and optimize the provided **Manually Converted SQL** to ensure it accurately reflects the logic of the **Source SQL** while adhering to the structure of the **Target Structure SQL**.

    Important Note: No of columns in the final SQL must be {tnf}. Target structure sql contains {tnf} columns. So final sql must contain {tnf} columns.

---

### 📌 Inputs

1. **Source SQL**  This contains business logic: filters, joins, expressions.
```sql
{source_sql}
```

2. **Target Structure SQL**  This shows the expected structure, aliases, aggregation, and final format.
```sql
{target_structure_sql}
```
3. **My Attempt (Manually Converted SQL)**  This is my draft combining the two above.
```sql
{manually_converted_sql}
```
4. **Field Mapping DataFrame**  Maps Source SQL field names to Target SQL aliases:
```python
{df}
```

---

### 🎯 Objective:

Fix the **Manually Converted SQL** to meet these conditions:

1. **Preserve Logic** from the Source SQL:
   - All JOINs, WHERE conditions, calculations, aliasing, and field-level granularity must be retained.

2. **Match the Structure** of the Target SQL:
   - Follow column layout, aggregations, GROUP BY, and HAVING clauses.
   - Use the column aliases defined in the Target SQL.

3. **Use Field Names from Source SQL**:
   - Replace field names from the Target SQL with the corresponding Source SQL field names using the provided DataFrame.

4. **Adhere to Schema**:
   - All fields and conditions must refer to the table `{schema}`.
   - If anything is outside the schema, correct it by referring to the Target SQL.

5. Better to avoid Subqueries or CTEs:
   - Do not use nested WITH clauses.For unavoidable subquery, keep it minimum.

6. **Validation Rule**:
   - If the Manually Converted SQL is already correct, return it without any changes.

7. **Select Correct fields from correct table**:
   - Sometime you construct query from many tables. So pick fields from exact tables.

8. **If Target sql is Join / Projection nodes - 
    **Ignore fields from source sql which are not present in target structure sql**
    - If any field is present in source sql but not in target structure sql, ignore it.
    - Ignored field must not be present in final sql.( Agrregtion and Group by too)
    - Unnecessary of ignored field's group by and aggregation must be removed. They will lead issue.

9. **Ensure that the number of columns in the final SQL matches the Target Structure SQL**:
   - Ensure you keep formula and calculated columns
---

### ⚠ Rules:

- Do **not** skip any business logic from the Source SQL.
- Ignore fields from source sql which are not present in target structure sql( Aggregation and Group by too)
- Do **not** return any explanation or commentary.
- Output only a **clean, complete, syntactically correct SQL query** with the correct structure and logic.
- Output SQL must contain all underlying Source SQL logic. Don't miss any logic.

### Joining Condition Rule for Derived Columns:
    If a JOIN condition references a column that does not exist in the base table but is derived in table table (e.g., table2.dummy = 45), then replace that reference(dummy) with the original expression(45) (constant or calculation).
    Example: table.value = table2.dummy should become table.value = 45.

####Important Instruction:
    When generating SQL queries, never use tables from {derived} as base tables. These tables are intermediate or derived, not intended to be queried directly.
    Instead, always construct queries using only the true base tables listed in {base}, and their columns.
    Use simple aliase table names for tables in the final SQL.
    Ensure the logic is rooted in the schema and relationships of the base tables, not the derived ones. Your generated query must contain fields only from {base}.
    This most needed instuction. If you directly the derived table instead of derived, final sql will throw error. This will lead system failure. I hope you understand the schmea and base tables for Bigquery output.
    Always keep to maintain target sql field structure, order and alias names. This generated sql will be used in other sql queries. So failing to keep alias names will cause runtime unavailability issue. 
    All the base tables are present in Bigquery under dataset: {ds_name}
    Make sure to use these {ds_name}.table as base tables and available columns in the final sql. Table format: `{ds_name}.tablename` . Otherwise sql will throw error.
    Make Sure that you don't miss any joining conditions if source sql has any.
    Give top most priority for fitting alias names.

""" 
    rank_prompt = extract_rank_function_structure(target_structure_sql)
    if rank_prompt:
        prompt += rank_prompt

    base_prompt = prompt
    deepseek_output = ""
    attempt = 0
    # logger.info(f"prompt_____------:{prompt}")
# Logavan - Optimized retry logic
    # STRICTLY LIMIT TO 3 RETRIES
    MAX_RETRIES = 3 
    for attempt in range(MAX_RETRIES):
        syntax_error = ""
        chunkwise_error = ""
        logger.info(f"Node:{node_name} attempt {attempt+1}/{MAX_RETRIES}")

        sql_text = await api_call_with_retry_async('Gemini', prompt, task_type='sql')  # Capture the return value
        # logger.info(f"Node:{node_name} attempt {attempt+1} sql :{sql_text}")
        prompt = " " # Clear prompt to save tokens for next iteration construction if needed

        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)
        # logger.info(f"Node:{node_name} attempt {attempt+1} cleaned sql :{cleaned_sql}")
        # Validate SQL
   
        # If this is the last attempt and we still have issues, we might return what we have
        # But ideally we want to catch success earlier.
        
        valid, msg = is_valid_sql(cleaned_sql)
        # logger.info(f"Node:{node_name} attempt {attempt} valid :{valid} msg :{msg}")
        if not valid:
            syntax_error = (
                f"SQL Syntax Error: {msg}"
            )
            logger.info(f"Node:{node_name} attempt {attempt+1} Syntax Error: {msg}")
            logger.info(f"Node:{node_name} attempt {attempt+1} INVALID SQL:\n{cleaned_sql}")
        else:
            syntax_error = None

        cleaned_sql = cleaned_sql.strip()
        sql_input = cleaned_sql.replace("\n", " ")
        
        # Only run BigQuery validation if local syntax is valid
        if not syntax_error:
            chunkwise_error = await run_bigquery_sql_async(sql_input)
                    
            if chunkwise_error == "SUCCESS":
                chunkwise_error = None
            else:
                logger.info(f"Node:{node_name} attempt {attempt+1} BQ Error: {chunkwise_error}")
        
        # Consolidated Error Handling
        current_errors = []
        if syntax_error:
            current_errors.append(syntax_error)
        if chunkwise_error:
            current_errors.append(chunkwise_error)
            
        final_error_msg = "\n".join(current_errors)

        if not final_error_msg:
            logger.info("___________________________________")
            logger.info(f"Success at attempt {attempt+1} for node :{node_name}")
            return cleaned_sql  # Return the cleaned SQL if no errors are found

        # If we reached max retries, we return the best we have (or the last failed one)
        if attempt == MAX_RETRIES - 1:
            logger.warning(f"Node:{node_name} failed after {MAX_RETRIES} attempts. Returning last generated SQL.")
            return cleaned_sql

        # --- Smart Error Feedback Generation (Effective Passing) ---
        not_found_match = None
        not_found_pattern = r"not found inside"
        if chunkwise_error:
            not_found_match = re.search(not_found_pattern, chunkwise_error)

        # Use gemini-3.1-flash-lite-preview/Deepseek only for complex logic errors, not simple missing columns
        deepseek_output = ""
        if final_error_msg and not not_found_match:
            deepseek_input = (
                f"{sql_input}\n"
                f"BigQuery Error Report:\n"
                f"{final_error_msg}\n"
                f"Dataset: {ds_name}. Base Tables: {base}\n"
                f"Explain the error and provide a FIXED SQL query. No explanation text, just SQL."
            )
            # Use a faster, lighter call if possible or just rely on the smart model
            deepseek_output = await api_call_with_retry_async('gemini-3.1-flash-lite-preview', deepseek_input, task_type='sql')
        elif not_found_match:
             deepseek_output = f"Check column names against schema: {chunk_schema}"

        # Construct concise prompt for next retry
        prompt = (
            f"{base_prompt}\n\n"
            f"--- PREVIOUS ATTEMPT FAILED ---\n"
            f"FAILED SQL:\n{cleaned_sql}\n\n"
            f"ERROR:\n{final_error_msg}\n\n"
            f"SUGGESTED FIX:\n{deepseek_output}\n\n"
            f"TASK: Generate corrected SQL. Ensure column existence and valid syntax."
        )

        if chunkwise_error:

            match = re.search(r"Name (\w+) not found", chunkwise_error)
            missing_column = match.group(1) if match else None

            # Regex to capture column name before "which is neither grouped nor aggregated"
            match_grouped_column = re.search(r"references ([\w\.]+) which is neither grouped nor aggregated", chunkwise_error)

            missing_grouped_column = match_grouped_column.group(1) if match_grouped_column else None

        # Generate a prompt for LLM
        llm_prompt = ""
        if attempt > 3 and missing_column:
            llm_prompt = (
                f""
                f"The SQL query is failing because the column '{missing_column}' is missing. "
                f"Please modify the SELECT statement to assign NULL for '{missing_column}' (E.g) select Null as {missing_column} ......"
                f"if it does not exist, so that missing columns are handled gracefully."
                f"""Joining Condition Rule for Derived Columns:
                    If a JOIN condition references a column that does not exist in the base table but is derived in table table (e.g., table2.dummy = 45), then replace that reference(dummy) with the original expression(45) (constant or calculation).
                    Example: table.value = table2.dummy should become table.value = 45."""
            )
            prompt += llm_prompt
                # Generate a prompt for LLM
        

        if attempt == 1 and missing_grouped_column:
            llm_prompt = (
                f""
                f"The SQL query is failing because the column '{missing_grouped_column}' is neither grouped nor aggregated. "
                f"This usually happens when a column is aggregated but also referenced directly in another calculated column (formula), "
                f"or when a non-aggregated column is missing from the GROUP BY clause. "
                f"For example, the query: SELECT id, SUM(value), score * value AS total FROM sales GROUP BY id "
                f"will fail because 'value' is used outside of the aggregation. "
                f"The correct version is: SELECT id, SUM(value), score * SUM(value) AS total FROM sales GROUP BY id. "
                f"Always ensure that non-aggregated columns are included in the GROUP BY clause, "
                f"and that calculated columns use aggregated values properly."
            )

            prompt += llm_prompt

        if attempt >= 2 and missing_grouped_column:
            llm_prompt = (
                f""
                f"FIX REQUIRED: Column '{missing_grouped_column}' is neither grouped nor aggregated in BigQuery. "
                f"IMPORTANT: Use ANY_VALUE() for any column that is NOT in GROUP BY and NOT an aggregate function. "
                f"For example: "
                f"WRONG:  SELECT department, name, SUM(salary) FROM employees GROUP BY department "
                f"CORRECT: SELECT department, ANY_VALUE(name), SUM(salary) FROM employees GROUP BY department "
                f"Apply ANY_VALUE() to '{missing_grouped_column}' or any other non-grouped columns. "
                f"Rewrite the entire SQL with this fix applied."
            )

            prompt += llm_prompt
        
        if chunkwise_error:
            # Regex pattern to capture expected and got parts
            match = re.search(r"Expected\s+\"([^\"]+)\"\s+but got keyword\s+(\w+)", chunkwise_error)

            expected_token = match.group(1) if match else None
            got_token = match.group(2) if match else None

            if attempt > 3 and expected_token and got_token:
                llm_prompt = (
                    f""
                    f"The parser expected '{expected_token}' but instead found '{got_token}'. "
                    f"This usually indicates that a parenthesis is missing or misplaced, "
                    f"or there is an extra comma before the {got_token} clause. "
                    f"To resolve this: "
                    f"1. Check that every opening parenthesis has a matching closing parenthesis. "
                    f"2. Ensure there are no trailing commas before FROM, GROUP BY, or ORDER BY. "
                    f"3. Verify that functions like CAST, LEFT, RIGHT, SUBSTR, and CASE are properly closed. "
                    f"4. Confirm that the SQL syntax near the {got_token} clause matches the expected structure. "
                    f"5. Make sure the token '{expected_token}' is correctly placed before '{got_token}'. "
                    f"Correct the SQL accordingly so that the parser receives the expected syntax."
                )

            prompt += llm_prompt

        if syntax_error and "Parameterized types are not allowed in CAST expressions" in syntax_error:
            err = syntax_error.split("Parameterized types are not allowed in CAST expressions")[-1].strip()
            if attempt > 3 and err:
                llm_prompt = (
                        f"{sql_input}\n\n"
                        f"I got this BigQuery error:\n{err}\n\n"
                        f"Explain why this happens and provide the corrected SQL.\n\n"
                        f"Important:\n"
                        f"- BigQuery does not allow CAST with precision/scale like NUMERIC(17,3).\n"
                        f"- Use CAST(... AS NUMERIC) instead.\n"
                        f"- If decimals are needed, wrap with ROUND(..., 3).\n\n"
                        f"Return only the corrected SQL without any explanation."
                    )   
            
                prompt += llm_prompt
        # logger.info(f"Node:{node_name} attempt {attempt} final prompt :{prompt}")   
    
        
    
    logger.info(f"final {attempt} sql is cleaned sql:{node_name}")

    return cleaned_sql  # Return the cleaned SQL if no errors are found








# Create tables in in-memory SQLite database
def create_tables_chunkwise(schema: Dict[str, List[str]]) -> sqlite3.Connection:
    def has_alpha(s: str) -> bool:
        return any(c.isalpha() for c in s)

    try:
        conn = sqlite3.connect(':memory:')
        cursor = conn.cursor()

        for table, columns in schema.items():
            if not has_alpha(table):
                continue  # Skip table if it has no alphabetic character

            # Filter out fields that are purely non-alphabetic
            valid_columns = [col for col in columns if has_alpha(col)]
            if not valid_columns:
                continue  # Skip table if no valid columns

            columns_def = ', '.join(f'"{col}" TEXT' for col in valid_columns)
            create_table_sql = f'CREATE TABLE "{table}" ({columns_def})'
            cursor.execute(create_table_sql)

        conn.commit()
        return conn
    except sqlite3.Error as e:
        raise Exception(f"Failed to create tables: {str(e)}")


# Analyze SQLite error messages
def analyze_sqlite_error_chunkwise(error: str, query: str) -> str:
    original_error = error.strip()
    error = error.lower()

    if "no such table" in error:
        table_name = error.split("no such table: ")[-1].strip()
        high_level_message = f"Error: Table '{table_name}' does not exist. Check table names in: {query}"
    elif "no such column" in error:
        column_name = error.split("no such column: ")[-1].strip()
        high_level_message = f"Error: Column '{column_name}' not found. Verify column names in: {query}"
    # elif "syntax error" in error:
    #     high_level_message = f"Error: Syntax error in query: {query}. Check syntax near '{error.split('near')[-1].strip()}'"
    elif "not unique" in error or "duplicate column" in error:
        high_level_message = f"Error: Duplicate column name detected: {error}. Ensure unique column names in: {query}"
    # else:
    #     high_level_message = f"Error: Unexpected issue: {error}. Review query: {query}"
    else:
        return None

    return f"{high_level_message}\nDetailed Error: {original_error}"


# Check for duplicate columns in SELECT clause
# Check for duplicate columns in SELECT clause
def check_duplicate_columns(query: str) -> Tuple[bool, str]:

    # Extract the SELECT clause (from SELECT to FROM)
    select_match = re.search(r'SELECT\s+(.+?)\s+FROM', query, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return (True, "No SELECT clause found or invalid query structure")

    select_clause = select_match.group(1).strip()

    # --- Smart split logic to handle nested expressions ---
    def split_columns(select_clause: str) -> List[str]:
        columns = []
        current = ''
        depth = 0
        in_string = False
        quote_char = ''

        for char in select_clause:
            if char in ('"', "'"):
                if in_string and char == quote_char:
                    in_string = False
                elif not in_string:
                    in_string = True
                    quote_char = char
            elif not in_string:
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                elif char == ',' and depth == 0:
                    columns.append(current.strip())
                    current = ''
                    continue
            current += char

        if current:
            columns.append(current.strip())
        return columns

    columns = split_columns(select_clause)

    # Extract aliases or column names
    column_names = []
    for col in columns:
        upper_col = col.upper()
        if ' AS ' in upper_col:
            alias = re.split(r'\s+AS\s+', col, flags=re.IGNORECASE)[-1].strip()
            column_names.append(alias)
        else:
            column_names.append(col.strip())

    seen = set()
    duplicates = [col for col in column_names if col in seen or seen.add(col)]
    if duplicates:
        return (False, f"Error: Duplicate column names or aliases found: {', '.join(duplicates)}")
    return (True, "No duplicate columns")




def validate_query_chunkwise(conn: sqlite3.Connection, query: str) -> Optional[str]:
    """
    Validates a SQL query using the provided SQLite connection.

    Returns:
        None if the query is valid.
        Error message (str) if the query has issues.
    """
    try:
        # Step 1: Check for duplicate column names or aliases
        is_valid, message = check_duplicate_columns(query)
        if not is_valid:
            return message

        # Step 2: Normalize and validate syntax using EXPLAIN
        try:
            cursor = conn.cursor()
            normalized_query = normalize_for_sqlite(query)
            cursor.execute("EXPLAIN " + normalized_query)
            return None  # Query is valid
        except sqlite3.Error as e:
            return analyze_sqlite_error_chunkwise(str(e), query)
        finally:
            cursor.close()

    except Exception as setup_error:
        return f"Validation setup failed: {str(setup_error)}"







def normalize_for_sqlite(query: str) -> str:
    # LEFT(x, y) -> SUBSTR(x, 1, y)
    query = re.sub(r'LEFT\s*\(\s*([^)]+?)\s*,\s*(\d+)\s*\)', r'SUBSTR(\1, 1, \2)', query, flags=re.IGNORECASE)

    # RIGHT(x, y) -> SUBSTR(x, -y)
    query = re.sub(r'RIGHT\s*\(\s*([^)]+?)\s*,\s*(\d+)\s*\)', r'SUBSTR(\1, -\2)', query, flags=re.IGNORECASE)

    # LTRIM(x, y) -> TRIM(y FROM x)
    query = re.sub(r'LTRIM\s*\(\s*([^)]+?)\s*,\s*([^)]+?)\s*\)', r'TRIM(\2 FROM \1)', query, flags=re.IGNORECASE)

    # RTRIM(x, y) -> TRIM(TRAILING y FROM x)
    query = re.sub(r'RTRIM\s*\(\s*([^)]+?)\s*,\s*([^)]+?)\s*\)', r'TRIM(TRAILING \2 FROM \1)', query, flags=re.IGNORECASE)

    # RANK() OVER (...) AS alias -> 0 AS alias (skip window functions for validation)
    query = re.sub(r'RANK\s*\(\s*\)\s*OVER\s*\((.*?)\)\s+AS\s+(\w+)', r'0 AS \2', query, flags=re.IGNORECASE | re.DOTALL)

    # NOW() -> CURRENT_TIMESTAMP
    query = re.sub(r'\bNOW\s*\(\s*\)', r'CURRENT_TIMESTAMP', query, flags=re.IGNORECASE)

    # ILIKE → LIKE (case-insensitive LIKE isn't supported natively in SQLite)
    query = re.sub(r'\bILIKE\b', r'LIKE', query, flags=re.IGNORECASE)

    # COALESCE(x, y) remains the same, but just to be safe, standardize casing
    query = re.sub(r'\bCOALESCE\b', r'COALESCE', query, flags=re.IGNORECASE)

    # CAST(x AS TYPE) → just x (SQLite may not recognize types)
    query = re.sub(r'CAST\s*\(\s*(.*?)\s+AS\s+\w+\s*\)', r'\1', query, flags=re.IGNORECASE)

    # DATE_TRUNC('month', x) → SUBSTR(x, 1, 7) for YYYY-MM dates
    query = re.sub(r"DATE_TRUNC\s*\(\s*'month'\s*,\s*([^)]+?)\s*\)", r'SUBSTR(\1, 1, 7)', query, flags=re.IGNORECASE)

    # ARRAY[...] → '[]' (ignore arrays)
    query = re.sub(r'ARRAY\s*\[.*?\]', r"'[]'", query, flags=re.IGNORECASE | re.DOTALL)

    # Remove double colon casting (e.g., column::TEXT)
    query = re.sub(r'::\s*\w+', '', query)

    # ADDITIONAL FUNCTIONALITIES:

    # STRING_AGG(x, y) → GROUP_CONCAT(x, y)
    query = re.sub(r'\bSTRING_AGG\s*\(\s*([^)]+?)\s*,\s*([^)]+?)\s*\)', r'GROUP_CONCAT(\1, \2)', query, flags=re.IGNORECASE)

    # TO_CHAR(x, 'YYYY-MM-DD') → STRFTIME('%Y-%m-%d', x)
    query = re.sub(r'\bTO_CHAR\s*\(\s*([^)]+?)\s*,\s*\'([^\']+?)\'\s*\)', r"STRFTIME('\2', \1)", query, flags=re.IGNORECASE)

    # TO_DATE(x, 'YYYY-MM-DD') → STRFTIME('%Y-%m-%d', x)
    query = re.sub(r'\bTO_DATE\s*\(\s*([^)]+?)\s*,\s*\'([^\']+?)\'\s*\)', r"STRFTIME('\2', \1)", query, flags=re.IGNORECASE)

    # EXTRACT(YEAR FROM x) → STRFTIME('%Y', x)
    query = re.sub(r'\bEXTRACT\s*\(\s*(\w+)\s*FROM\s*([^)]+?)\s*\)', r"STRFTIME('%\1', \2)", query, flags=re.IGNORECASE)

    # JSONB functions like JSONB_ARRAY_ELEMENTS, JSONB_OBJECT_KEYS
    query = re.sub(r'\bJSONB_ARRAY_ELEMENTS\b', 'JSON_ARRAY', query, flags=re.IGNORECASE)
    query = re.sub(r'\bJSONB_OBJECT_KEYS\b', 'JSON_OBJECT_KEYS', query, flags=re.IGNORECASE)

    # DISTINCT ON (columns) → DISTINCT (works the same)
    query = re.sub(r'\bDISTINCT\s+ON\s*\(\s*([^)]+?)\s*\)', r'DISTINCT \1', query, flags=re.IGNORECASE)

    # GENERATE_SERIES(start, end) → No equivalent in SQLite (Consider handling via subqueries)
    query = re.sub(r'\bGENERATE_SERIES\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*\)', r'-- GENERATE_SERIES: No SQLite Equivalent', query, flags=re.IGNORECASE)

    # STRING_TO_ARRAY(x, y) → split(x, y) or just use SUBSTR logic (SQLite lacks array functions)
    query = re.sub(r'\bSTRING_TO_ARRAY\s*\(\s*([^)]+?)\s*,\s*([^)]+?)\s*\)', r'SUBSTR(\1, \2)', query, flags=re.IGNORECASE)

    # FLOOR(x) → FLOOR(x) (SQLite supports it)
    query = re.sub(r'\bFLOOR\s*\(\s*([^)]+?)\s*\)', r'FLOOR(\1)', query, flags=re.IGNORECASE)

    # CEIL(x) → CEIL(x) (SQLite supports it)
    query = re.sub(r'\bCEIL\s*\(\s*([^)]+?)\s*\)', r'CEIL(\1)', query, flags=re.IGNORECASE)

    # NULLIF(x, y) → NULLIF(x, y) (SQLite supports it)
    query = re.sub(r'\bNULLIF\s*\(\s*([^)]+?)\s*,\s*([^)]+?)\s*\)', r'NULLIF(\1, \2)', query, flags=re.IGNORECASE)

    # CONCAT_WS(x, y, z, ...) → CONCAT(x, y, z, ...) in SQLite (same functionality)
    query = re.sub(r'\bCONCAT_WS\s*\(\s*([^)]+?)\s*\)', r'CONCAT(\1)', query, flags=re.IGNORECASE)

    return query






def format_sql_query(query):
    # Format the SQL query with sqlparse
    formatted = sqlparse.format(
        query,
        keyword_case='upper',  # Uppercase keywords (SELECT, FROM, etc.)
        identifier_case='lower',  # Lowercase identifiers (column/table names)
        reindent=True,  # Add basic indentation
        indent_width=4,  # 4 spaces per indent level
        wrap_after=80,  # Line width
        comma_first=False,  # Commas at end of line
        use_space_around_operators=True  # Spaces around =, +, etc.
    )
    return formatted.strip()




def enhance_node_dict(node_dict):
    """
    Enhances a node dictionary by adding:
    - Direct SQL (SELECT fields FROM node)
    - is_leafnode (Yes/No based on sources)
    - Chunk SQL (Node SQL if leaf, Direct SQL if primary, else blank)
    """


    # 1. Add Direct SQL for each node
    for node_name, node_info in node_dict.items():
        sql = node_info.get("Node SQL", "")
        Node_SQL = sql.strip().strip('"').rstrip(";")
        node_name_actual = node_info.get("Node name", "").lower()  # lowercase the node name

        # Extract aliases using regex
        aliases = re.findall(r'\bAS\s+(\w+)', Node_SQL, re.IGNORECASE)

        # Lowercase and prefix each alias with the lowercase node name
        prefixed_aliases = [f"{node_name_actual}.{alias.lower()}" for alias in aliases]

        # Construct the final SQL
        if prefixed_aliases:
            node_info["Direct SQL"] = f"SELECT {', '.join(prefixed_aliases)} FROM {node_name_actual}"
        else:
            node_info["Direct SQL"] = None



    # 2. Determine leaf nodes
    all_node_names = {name.lower() for name in node_dict.keys()}  # make all node names lowercase

    for node_name, node_info in node_dict.items():
        sources = node_info.get("Sources", [])
        is_leaf = True

        # Convert string sources to list and lowercase all sources
        if isinstance(sources, str):
            sources = [s.strip("[]'\" ").lower() for s in sources.split(",")]
        else:
            sources = [str(s).strip("[]'\" ").lower() for s in sources]

        for cleaned_source in sources:
            if cleaned_source in all_node_names:
                is_leaf = False
                break

        node_info["is_leafnode"] = "Yes" if is_leaf else "No"



    # 1. Group nodes by Chunk Number
    chunk_groups = defaultdict(dict)
    for node_name, node_info in node_dict.items():
        chunk_num = node_info.get("Chunk Number")
        chunk_groups[chunk_num][node_name] = node_info

    # 2. Determine leaf nodes within each chunk
    # 2. Determine leaf nodes within each chunk
    for chunk_num, nodes_in_chunk in chunk_groups.items():
        # Convert all keys (node names) to lowercase for comparison
        all_node_names_in_chunk = {k.lower() for k in nodes_in_chunk.keys()}

        for node_name, node_info in nodes_in_chunk.items():
            sources = node_info.get("Sources", [])
            is_leaf = True

            # Handle case where Sources might be a string representation of a list
            if isinstance(sources, str):
                sources = [s.strip("[]'\" ").lower() for s in sources.split(",")]
            else:
                sources = [str(s).strip("[]'\" ").lower() for s in sources]

            for cleaned_source in sources:
                if cleaned_source in all_node_names_in_chunk:
                    is_leaf = False
                    break

            node_info["is_leafnode_chunkwise"] = "Yes" if is_leaf else "No"



    # Group nodes by Chunk Number
    chunk_groups = defaultdict(dict)

    for node_name, node_info in node_dict.items():
        chunk = node_info.get("Chunk Number", "Unknown")
        chunk_groups[chunk][node_name] = node_info

    # Process each chunk
    for chunk, nodes_in_chunk in chunk_groups.items():
        all_node_names_in_chunk = set(nodes_in_chunk.keys())
        all_sources = []

        # Collect all sources used in this chunk
        for node_info in nodes_in_chunk.values():
            sources = node_info.get("Sources", [])
            if isinstance(sources, str):
                sources = [s.strip("[]'\" ") for s in sources.split(",")]
            all_sources.extend(sources)

        # Clean and filter the sources to find leaf-level ones (external to this chunk)
        chunk_leaf_sources = []
        for source in all_sources:
            cleaned = source.strip("[]'\" ")
            if cleaned and cleaned not in all_node_names_in_chunk:
                chunk_leaf_sources.append(cleaned)

        # Deduplicate
        chunk_leaf_sources = list(set(chunk_leaf_sources))

        # Assign the same list to every node in this chunk
        # for node_info in nodes_in_chunk.values():
        #     node_info["Chunk_leaf_sources"] = chunk_leaf_sources
        for node_info in nodes_in_chunk.values():
            node_info["Chunk_leaf_sources"] = [source.lower() for source in chunk_leaf_sources]


    # 3. Determine Chunk SQL (FIXED LOGIC)
    for node_name, node_info in node_dict.items():
        if node_info.get("is_leafnode") == "Yes":
            node_info["Chunk SQL"] = node_info.get("Node SQL")
        elif node_info.get("Is Primary") == "Yes":
            node_info["Chunk SQL"] = node_info.get("Direct SQL")
        else:
            node_info["Chunk SQL"] = ""




# Step 1: Get all unique node names (case-insensitive)
all_node_names_lower = {str(name).lower() for name in node_dict.keys()}

# Step 2: Collect all sources from all nodes and remove duplicates
all_sources = set()
for node_data in node_dict.values():
    all_sources.update(str(source).lower() for source in node_data["Sources"])


from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

def format_all_node_sql(node_dict, use_process_pool=False):
    # pick executor depending on workload type
    Executor = ProcessPoolExecutor if use_process_pool else ThreadPoolExecutor

    def format_node_sql(item):
        node_name, node_data = item
        for key in ("Node SQL", "Direct SQL", "Chunk SQL"):
            sql = node_data.get(key)
            if sql:
                node_data[key] = format_sql_query(sql)

    with Executor() as executor:
        executor.map(format_node_sql, node_dict.items())





# def fill_node_sql(node_dict, max_attempts=3):
#     # Initialize - track which nodes need processing
#     nodes_needing_processing = set()
#     for node_name, node in node_dict.items():
#         node["_attempts"] = 0
#         sql = node.get("Node SQL")
#         is_valid = sql and is_valid_sql_single(sql)
        
#         if not is_valid:
#             nodes_needing_processing.add(node_name)
        
#         # logger.info(f"[INIT] Node={node_name}, HasSQL={sql is not None}, Valid={is_valid}")

#     # Process only nodes that need SQL (with retries)
#     for attempt in range(max_attempts):
#         if not nodes_needing_processing:
#             logger.info(f"[DONE] All nodes have valid SQL after {attempt} attempts")
#             break

#         logger.info(f"[ATTEMPT {attempt + 1}] Processing {len(nodes_needing_processing)} nodes: {sorted(nodes_needing_processing)}")

#         # Create temporary dict with only nodes needing processing
#         temp_dict = {k: v for k, v in node_dict.items() if k in nodes_needing_processing}
        
#         # Process these nodes in parallel
#         process_nodes_xml_sql_parallel_async(temp_dict)
        
#         # Check results and update tracking
#         still_failing = set()
#         for node_name in list(nodes_needing_processing):
#             node = node_dict[node_name]
#             node["_attempts"] += 1
            
#             sql = node.get("Node SQL")
#             is_valid = sql and is_valid_sql_single(sql)
            
#             logger.info(f"[AFTER PROCESS] Node={node_name}, Attempt={node['_attempts']}, HasSQL={sql is not None}, Valid={is_valid}")
            
#             if is_valid:
#                 logger.info(f"[VALID] Node={node_name} now has valid SQL")
#                 # This node is done - remove from processing set
#             else:
#                 logger.info(f"[INVALID] Node={node_name} still has invalid/missing SQL")
#                 node["Node SQL"] = None  # Clear invalid SQL
#                 still_failing.add(node_name)

#         # Update set for next iteration
#         nodes_needing_processing = still_failing

#     # Final cleanup and validation
#     for node_name, node in node_dict.items():
#         node.pop("_attempts", None)  # Remove temporary tracking
#         sql = node.get("Node SQL")
#         is_valid = sql and is_valid_sql_single(sql)
        
#         if not is_valid:
#             logger.info(f"[FINAL INVALID] Node={node_name} has invalid/missing SQL")
#             node["Node SQL"] = sql
#         # else:
#         #     logger.info(f"[FINAL VALID] Node={node_name} has valid SQL")

#     return node_dict

import asyncio
import logging

logger = logging.getLogger(__name__)

async def fill_node_sql_async(node_dict, max_attempts=3, max_concurrent=50):
    """
    Asynchronously fills missing/invalid Node SQL for nodes in node_dict using retries.
    
    :param node_dict: Dictionary of nodes to process.
    :param max_attempts: Number of retries for nodes with invalid SQL.
    :param max_concurrent: Maximum number of concurrent tasks in async processing.
    :return: Updated node_dict with valid SQL where possible.
    """

    # Initialize nodes needing processing
    nodes_needing_processing = set()
    for node_name, node in node_dict.items():
        node["_attempts"] = 0
        sql = node.get("Node SQL")
        is_valid = sql and is_valid_sql_single(sql)
        
        if not is_valid:
            nodes_needing_processing.add(node_name)

    # Retry loop for nodes needing SQL
    for attempt in range(max_attempts):
        if not nodes_needing_processing:
            logger.info(f"[DONE] All nodes have valid SQL after {attempt} attempts")
            break

        logger.info(f"[ATTEMPT {attempt + 1}] Processing {len(nodes_needing_processing)} nodes: {sorted(nodes_needing_processing)}")

        # Temporary dict of nodes to process
        temp_dict = {k: v for k, v in node_dict.items() if k in nodes_needing_processing}

        # Process nodes asynchronously
        await process_nodes_xml_sql_parallel_async(temp_dict, max_concurrent=max_concurrent)

        # Check results and update tracking
        still_failing = set()
        for node_name in list(nodes_needing_processing):
            node = node_dict[node_name]
            node["_attempts"] += 1
            
            sql = node.get("Node SQL")
            is_valid = sql and is_valid_sql_single(sql)
            
            logger.info(f"[AFTER PROCESS] Node={node_name}, Attempt={node['_attempts']}, HasSQL={sql is not None}, Valid={is_valid}")
            
            if is_valid:
                logger.info(f"[VALID] Node={node_name} now has valid SQL")
            else:
                logger.info(f"[INVALID] Node={node_name} still has invalid/missing SQL")
                node["Node SQL"] = None  # Clear invalid SQL
                still_failing.add(node_name)

        nodes_needing_processing = still_failing

    # Final cleanup and validation
    for node_name, node in node_dict.items():
        node.pop("_attempts", None)
        sql = node.get("Node SQL")
        is_valid = sql and is_valid_sql_single(sql)
        if not is_valid:
            logger.info(f"[FINAL INVALID] Node={node_name} has invalid/missing SQL")

    return node_dict


def fill_node_json(node_dict):
    while True:
        # Filter nodes where:
        # - The field is missing or empty
        # - OR the field exists but doesn't contain any alphabet characters
        nodes_to_process = {
            k: v for k, v in node_dict.items()
            if not v.get("Node Schema w/ datatype JSON") or
               not re.search(r'[A-Za-z]', str(v.get("Node Schema w/ datatype JSON")))
        }

        if nodes_to_process:
            process_json_datatype_parallel(nodes_to_process)
        else:
            break





def fill_node_source_datatype_json(node_dict):
    while True:
        # Filter nodes where:
        # - The field is missing OR
        # - The field exists but doesn't contain any alphabetic characters
        nodes_to_process = {
            k: v for k, v in node_dict.items()
            if not v.get("Source Schema w/ datatype JSON") or
               not re.search(r'[A-Za-z]', str(v.get("Source Schema w/ datatype JSON")))
        }

        if nodes_to_process:
            process_sources_json_datatype_parallel(nodes_to_process)
        else:
            break






def has_subquery_sqlglot(sql):
    """
    Detect subqueries and CTEs using sqlglot AST for accuracy, with regex fallback.
    Returns True if subqueries or CTEs are found.
    UNION ALL at top level is allowed and should NOT be flagged.
    """
    if not sql or not sql.strip():
        return False
        
    sql_clean = sql.strip()
    sql_lower = sql_clean.lower()
    
    # Quick CTE check (WITH ... AS)
    if re.match(r'^\s*with\s+\w+\s+as\s*\(', sql_lower):
        return True
    
    # Try sqlglot AST parsing for accurate detection
    try:
        parsed = sqlglot_parse(sql_clean, read='bigquery')
        if parsed:
            for statement in parsed:
                # Check for CTEs (WITH clause)
                if statement.find(exp.With):
                    return True
                
                # Check for subqueries in FROM clause
                for subquery in statement.find_all(exp.Subquery):
                    return True
                
                # Check for nested SELECT - but allow UNION/INTERSECT/EXCEPT
                for select in statement.find_all(exp.Select):
                    # Skip the main SELECT (root statement)
                    if select is statement:
                        continue
                    
                    # Check if this SELECT is part of a top-level Union/Intersect/Except
                    parent = select.parent
                    is_union_part = False
                    while parent is not None:
                        if isinstance(parent, (exp.Union, exp.Intersect, exp.Except)):
                            # Check if this union is at the top level (parent is statement or has no parent above)
                            if parent is statement or parent.parent is None or parent.parent is statement:
                                is_union_part = True
                                break
                        if parent is statement:
                            break
                        parent = parent.parent
                    
                    # If not part of a top-level union, it's a subquery
                    if not is_union_part:
                        return True
                    
    except Exception:
        pass  # Fall back to regex
    
    # Regex fallback for edge cases
    
    # Detect subqueries inside parentheses: (SELECT ...)
    # But NOT if it's just a UNION ALL with line breaks
    if re.search(r'\(\s*select\b', sql_lower):
        return True
    
    # Check for EXISTS/IN/NOT IN with SELECT
    if re.search(r'\b(exists|not\s+in|in)\s*\(\s*select\b', sql_lower):
        return True
    
    return False





# process_all_chunks(node_dict)


def sum_number(a, b):
    """
    Simple function to sum two numbers.
    """
    return a + b


from collections import deque
from collections import deque
import logging






async def process_chunk(chunk_number, node_names, node_dict, ds_name):
    ds_name = ds_name.lower()

    # -------------------------------
    # Initialize schema from primary node
    # -------------------------------
    schema = []
    for node in node_names:
        node_info = node_dict.get(node, {})
        if node_info.get("Is Primary", "").lower() == "yes":
            schema = node_info.get("Chunk Schema", [])
            break
            
    # word to add before table name
    prefix = f"{ds_name}."

    updated_schemas = []
    for schema_str in schema:
        try:
            parsed = json.loads(schema_str)                # convert string → dict
            new_parsed = {}
            for table, cols in parsed.items():            # get table names
                new_parsed[prefix + table] = cols         # rename key
            updated_schemas.append(json.dumps(new_parsed)) # convert back to string
        except Exception as e:
            logger.error(f"Error parsing schema in chunk {chunk_number}: {e}")

    schema = updated_schemas
    # -------------------------------
    # Compute base and derived sources
    # -------------------------------
    chunk_nodes = [n for n in node_names if node_dict[n]["Chunk Number"] == chunk_number]

    # Collect all sources in chunk nodes
    all_sources = set()
    for node in chunk_nodes:
        all_sources.update(node_dict[node].get("Sources", []))

    # Use 'Chunk Sources' from any node in the chunk (first one found)
    chunk_sources = []
    for node in chunk_nodes:
        chunk_sources = node_dict[node].get("Chunk Sources", [])
        if chunk_sources:
            break

    base = chunk_sources
    derived = [src for src in all_sources if src not in base]

    # -------------------------------
    # Bottom-up traversal within chunk
    # -------------------------------
    # Internal dependencies only (inside this chunk)
    internal_sources = {node: [s for s in node_dict[node].get("Sources", []) if s in chunk_nodes] for node in chunk_nodes}

    # Count unresolved dependencies per node
    dependency_count = {node: len(internal_sources[node]) for node in chunk_nodes}

    # Nodes that depend on a given node
    depend_on_me = {node: [] for node in chunk_nodes}
    for node, sources in internal_sources.items():
        for src in sources:
            depend_on_me[src].append(node)

    # Start with leaf nodes (no internal dependencies)
    queue = deque([node for node, count in dependency_count.items() if count == 0])

    visited = set()

    logger.info(f"Processing Chunk: {chunk_number}")
    while queue:
        current_node = queue.popleft()
        if current_node in visited:
            continue
        visited.add(current_node)

        logger.info(f"Current_node: {current_node}")
        node_type = node_dict[current_node]["Node type"]
        
        try:
            if node_type == 'Projection':
                await process_projection_node_sql(node_dict[current_node], node_dict, schema, base, derived, ds_name)
            elif node_type == 'JoinNode':
                await process_join_node_sql(node_dict[current_node], node_dict, schema, base, derived, ds_name)
            elif node_type == 'Aggregation':
                await process_aggregation_node_sql(node_dict[current_node], node_dict, schema, base, derived, ds_name)
            elif node_type == 'Rank':
                await process_rank_node_sql(node_dict[current_node], node_dict, schema, base, derived, ds_name)
            elif node_type == 'Union':
                await process_union_node_sql(node_dict[current_node], node_dict, schema, base, derived, ds_name)
        except Exception as e:
            logger.error(f"Error processing node {current_node} in chunk {chunk_number}: {e}")
            logger.error(traceback.format_exc())

        for dependent_node in depend_on_me[current_node]:
            dependency_count[dependent_node] -= 1
            if dependency_count[dependent_node] == 0:
                queue.append(dependent_node)

    # Check if all nodes were processed
    if len(visited) != len(chunk_nodes):
        unprocessed = set(chunk_nodes) - visited
        logger.warning(f"Unprocessed nodes in chunk {chunk_number}: {unprocessed}")



# def process_all_chunks(ds_name, node_dict):
# 
#     node_dict = {
#     k.lower(): v for k, v in node_dict.items()
#     }
#     # Group nodes by chunk number
#     chunks = {}
#     for node_name, node in node_dict.items():
#         chunk_number = node.get("Chunk Number")
#         if chunk_number is None:
#             raise ValueError(f"Node '{node_name}' is missing 'Chunk Number'")
#         chunks.setdefault(chunk_number, []).append(node_name)
# 
#     # Process chunks in parallel
#     with concurrent.futures.ThreadPoolExecutor() as executor:
#         futures = [
#             executor.submit(process_chunk, chunk_number, node_names, node_dict, ds_name)
#             for chunk_number, node_names in chunks.items()
#         ]
#         concurrent.futures.wait(futures)


import asyncio

async def process_all_chunks_async(ds_name, node_dict):
    node_dict = {k.lower(): v for k, v in node_dict.items()}

    # Group nodes by chunk number
    chunks = {}
    for node_name, node in node_dict.items():
        chunk_number = node.get("Chunk Number")
        if chunk_number is None:
            raise ValueError(f"Node '{node_name}' is missing 'Chunk Number'")
        chunks.setdefault(chunk_number, []).append(node_name)

    # Schedule all chunks concurrently with a limit to avoid rate limits
    # Default to 20, but allow override via env var
    max_concurrent_chunks = int(os.getenv("MAX_CONCURRENT_CHUNKS", "20"))
    semaphore = asyncio.Semaphore(max_concurrent_chunks)

    async def semaphore_process_chunk(chunk_number, node_names, node_dict, ds_name):
        async with semaphore:
            await process_chunk(chunk_number, node_names, node_dict, ds_name)

    tasks = [
        semaphore_process_chunk(chunk_number, node_names, node_dict, ds_name)
        for chunk_number, node_names in chunks.items()
    ]
    
    # Wait for all chunks to complete
    await asyncio.gather(*tasks)

import asyncio
import logging

async def fill_chunk_sql_primary_async(node_dict, ds_name, max_iterations=3, stall_limit=None):
    """
    Iteratively fills 'Chunk SQL Primary' for nodes in node_dict asynchronously.

    Args:
        node_dict (dict): Node metadata dictionary.
        ds_name (str): Dataset name.
        max_iterations (int): Max iterations before stopping.
        stall_limit (int or None): Number of stalled iterations allowed before breaking.
    """
    logger = logging.getLogger(__name__)
    iteration = 0
    stall_count = 0

    while iteration < max_iterations:
        iteration += 1

        # Nodes missing Chunk SQL Primary before processing
        nodes_missing_before = {
            k for k, v in node_dict.items()
            if str(v.get("Is Primary", "")).lower() == "yes" and not v.get("Chunk SQL Primary")
        }

        # Collect chunks that still need processing
        chunks_to_process = {
            node_dict[node_name].get("Chunk Number")
            for node_name in nodes_missing_before
            if node_dict[node_name].get("Chunk Number")
        }

        logger.info(f"Iteration {iteration}: Chunks to process: {chunks_to_process}")

        if not chunks_to_process:
            logger.info("All primary nodes have Chunk SQL Primary filled. Exiting.")
            break

        # Group nodes by chunk
        chunks = {}
        for node_name, node in node_dict.items():
            if (chunk_num := node.get("Chunk Number")) in chunks_to_process:
                chunks.setdefault(chunk_num, []).append(node_name)

        # Process all chunks concurrently
        tasks = [
            process_chunk(chunk_num, nodes, node_dict, ds_name)
            for chunk_num, nodes in chunks.items()
        ]
        await asyncio.gather(*tasks)

        logger.info(f"Completed processing chunks for iteration {iteration}")

        # Nodes still missing after processing
        nodes_missing_after = {
            k for k, v in node_dict.items()
            if str(v.get("Is Primary", "")).lower() == "yes" and not v.get("Chunk SQL Primary")
        }

        # Detect progress
        if nodes_missing_after == nodes_missing_before:
            stall_count += 1
            logger.warning(f"No progress made in iteration {iteration} (stall {stall_count})")
            if stall_limit is not None and stall_count >= stall_limit:
                logger.info("Too many stalled iterations. Exiting early to avoid infinite loop.")
                break
        else:
            stall_count = 0  # reset if progress is made
    else:
        logger.warning(f"Reached max iterations ({max_iterations}) without completing all nodes")


# def fill_chunk_sql_primary(node_dict, ds_name, max_iterations=3, stall_limit=None):
#     """
#     Iteratively fills 'Chunk SQL Primary' for nodes in node_dict.
# 
#     Args:
#         node_dict (dict): Node metadata dictionary.
#         ds_name (str): Dataset name.
#         max_iterations (int): Max iterations before stopping.
#         stall_limit (int or None): Number of stalled iterations allowed before breaking.
#                                    If None, will retry until max_iterations regardless of progress.
#     """
#     logger = logging.getLogger(__name__)
#     iteration = 0
#     stall_count = 0
# 
#     while iteration < max_iterations:
#         iteration += 1
# 
#         # Nodes missing Chunk SQL Primary before processing
#         nodes_missing_before = {
#             k for k, v in node_dict.items()
#             if str(v.get("Is Primary", "")).lower() == "yes" and not v.get("Chunk SQL Primary")
#         }
# 
#         # Collect chunks that still need processing
#         chunks_to_process = {
#             node_dict[node_name].get("Chunk Number")
#             for node_name in nodes_missing_before
#             if node_dict[node_name].get("Chunk Number")
#         }
# 
#         logger.info(f"Iteration {iteration}: Chunks to process: {chunks_to_process}")
# 
#         if not chunks_to_process:
#             logger.info("All primary nodes have Chunk SQL Primary filled. Exiting.")
#             break
# 
#         # Process each chunk in parallel
#         with concurrent.futures.ThreadPoolExecutor() as executor:
#             chunks = {}
#             for node_name, node in node_dict.items():
#                 if (chunk_num := node.get("Chunk Number")) in chunks_to_process:
#                     chunks.setdefault(chunk_num, []).append(node_name)
# 
#             futures = [
#                 executor.submit(process_chunk, chunk_num, nodes, node_dict, ds_name)
#                 for chunk_num, nodes in chunks.items()
#             ]
# 
#             concurrent.futures.wait(futures)
# 
#         logger.info(f"Completed processing chunks for iteration {iteration}")
# 
#         # Nodes still missing after processing
#         nodes_missing_after = {
#             k for k, v in node_dict.items()
#             if str(v.get("Is Primary", "")).lower() == "yes" and not v.get("Chunk SQL Primary")
#         }
# 
#         # Detect progress
#         if nodes_missing_after == nodes_missing_before:
#             stall_count += 1
#             logger.warning(f"No progress made in iteration {iteration} (stall {stall_count})")
#             if stall_limit is not None and stall_count >= stall_limit:
#                 logger.info("Too many stalled iterations. Exiting early to avoid infinite loop.")
#                 break
#         else:
#             stall_count = 0  # reset if progress is made
#     else:
#         logger.warning(f"Reached max iterations ({max_iterations}) without completing all nodes")

# 1. First ensure your DataFrame has the required columns
async def prepare_mapping_df(df):
    """Prepare the mapping DataFrame with all required columns"""
    mapping_df = df.copy()

    # Create normalized versions if they don't exist
    if 'TargetField' not in mapping_df.columns:
        mapping_df['TargetField'] = mapping_df['TargetName'] + '.' + mapping_df['TargetAlias']

    if 'SourceField' not in mapping_df.columns:
        mapping_df['SourceField'] = mapping_df.apply(
            lambda r: r['Field'] if pd.isna(r['SourceTable']) or not r['SourceTable']
            else f"{r['SourceTable']}.{r['Field']}",
            axis=1
        )

    # Add normalized columns for case-insensitive matching
    mapping_df['Target_alias_final_lower'] = mapping_df['TargetField'].str.lower().str.strip()
    mapping_df['Source_Field_Combined'] = mapping_df['SourceField'].str.strip()
    mapping_df['Target_name_lower'] = mapping_df['TargetName'].str.lower().str.strip()
    mapping_df['FROM_lower'] = mapping_df['FromClause'].str.strip()
    return mapping_df

# 2. Your transformation functions (unchanged)
async def replace_sql_using_df(sql, df):
    for _, row in df.iterrows():
        # Original replacement logic
        target_expr = row["Target_alias_final_lower"]
        source_field = row["Source_Field_Combined"]
        # Standard replacement (for cases like N_Select.quantity -> sales_details.quantity)
        sql = re.sub(rf'\b{re.escape(target_expr)}\b', source_field, sql, flags=re.IGNORECASE)

        # Special case: Handle n_select.field pattern when TargetAlias exists
        if pd.notna(row['TargetAlias']):
            alt_target = f"{row['TargetName'].lower()}.{row['Field'].lower()}"
            sql = re.sub(rf'\b{re.escape(alt_target)}\b', source_field, sql, flags=re.IGNORECASE)

    return sql

async def replace_table_names(sql, df_table_mapping):
    for _, row in df_table_mapping.iterrows():
        target_name = row["Target_name_lower"]
        from_clause = row["FROM_lower"]
        # Global replacement with word boundaries to handle multiline SQL and all occurrences
        sql = re.sub(rf'\b{re.escape(target_name)}\b', from_clause, sql, flags=re.IGNORECASE)
    return sql

async def add_where_clause(sql, df):
    if "WhereClause" in df.columns:
        # Pick distinct, non-null, stripped values
        where_values = df["WhereClause"].dropna().astype(str).str.strip().unique()
        where_values = [w for w in where_values if w]  # remove empty strings

        if where_values:
            combined_where = " AND ".join(where_values)

            # Check if WHERE clause exists
            where_match = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
            
            if not where_match:
                # Insert WHERE before GROUP BY, HAVING, ORDER BY, LIMIT, WINDOW, UNION, or End of String
                # Use a positive lookahead to find the insertion point
                insertion_point = re.search(r"(?i)\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|WINDOW|UNION)\b", sql)
                if insertion_point:
                    # Insert before the first matching clause
                    sql = sql[:insertion_point.start()] + f"\nWHERE {combined_where}\n" + sql[insertion_point.start():]
                else:
                    # Append to end if no such clauses found
                    sql += f"\nWHERE {combined_where}"
            else:
                # Append to existing WHERE clause
                # We need to insert AND condition exactly after the WHERE <existing_conditions>
                # and before any subsequent clauses (GROUP BY, etc.)
                
                # Regex explain:
                # 1. Match WHERE keyword and everything after it until...
                # 2. Lookahead for next major clause or End of String
                
                # Actually, simpler: just substitute valid syntax
                # Pattern: (WHERE\s+.*?)(?=\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|WINDOW|UNION)\b|$)
                # Note: .*? is non-greedy, so it stops at the first lookahead match
                
                pattern = r"(?si)(\bWHERE\b\s+.*?)(?=\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|WINDOW|UNION)\b|$)"
                
                def replacer(match):
                    return f"{match.group(1)} AND {combined_where} "
                
                sql = re.sub(pattern, replacer, sql, count=1)

    return sql



async def transform_sql_query_using_df(sql_query, df):
    # Step 1: Prepare the mapping DataFrame
    mapping_df = await prepare_mapping_df(df)

    # Step 2: Create table mapping for FROM/JOIN replacement
    table_mapping = mapping_df[["Target_name_lower", "FROM_lower"]].drop_duplicates()

    # Step 3: Apply transformations
    transformed_sql = await replace_sql_using_df(sql_query, mapping_df)

    transformed_sql = await replace_table_names(transformed_sql, table_mapping)

    transformed_sql = await add_where_clause(transformed_sql, mapping_df)
 

    # logger.info("Transformed SQL Query:")
    # logger.info(transformed_sql)
    return transformed_sql







def is_literal(expr: str) -> bool:
    """Check if expression is a numeric or quoted string literal"""
    expr = expr.strip()
    return (re.match(r'^\d+$', expr) is not None or
            (expr.startswith("'") and expr.endswith("'")) or
            (expr.startswith('"') and expr.endswith('"')))

def clean_quoted_text(text: str) -> str:
    """
    Clean spaces inside quoted strings while perfectly preserving quotes
    Handles both single and double quotes, including escaped quotes
    """
    def replacer(match: re.Match) -> str:
        quote = match.group(1)
        content = match.group(2)
        return f"{quote}{content.strip()}{quote}"

    # Pattern explanation:
    # (['"]) - matches either single or double quote
    # ((?:\\\1|(?!\1).)*?) - matches content including escaped quotes
    # \1 - matches the closing quote
    pattern = r"""(['"])((?:\\\1|(?!\1).)*?)\1"""
    return re.sub(pattern, replacer, text, flags=re.DOTALL)

def parse_sql_chunk(sql: str, target_name: str) -> Tuple[List[str], List[Tuple[str, str, str]], List[str], str]:
    """Parse SQL query into structured components"""
    # Normalize SQL
    sql = ' '.join(sql.strip().split())

    try:
        parsed_list = sqlparse.parse(sql)
        if not parsed_list:
            raise ValueError("Empty SQL query or failed to parse.")
        parsed = parsed_list[0]
    except Exception as e:
        raise ValueError(f"SQL parsing failed: {str(e)}")


    # Initialize data structures
    tables = []
    alias_map = {}
    fields = []
    where_conditions = []
    from_join_clause = ""

    # Extract tables and aliases
    for token in parsed.tokens:
        if isinstance(token, sqlparse.sql.IdentifierList):
            for identifier in token.get_identifiers():
                if identifier.get_real_name():
                    tables.append(identifier.get_real_name())
                    if identifier.get_alias():
                        alias_map[identifier.get_alias().lower()] = identifier.get_real_name()
        elif isinstance(token, sqlparse.sql.Identifier):
            if token.get_real_name():
                tables.append(token.get_real_name())
                if token.get_alias():
                    alias_map[token.get_alias().lower()] = token.get_real_name()

    # Remove duplicate tables while preserving order
    seen = set()
    tables = [t for t in tables if not (t in seen or seen.add(t))]

    # Extract FROM/JOIN clause
    from_match = re.search(
        r'\bFROM\b(.+?)(?=\bWHERE\b|\bGROUP BY\b|\bHAVING\b|\bORDER BY\b|\bLIMIT\b|$)',
        sql,
        re.IGNORECASE | re.DOTALL
    )
    if from_match:
        from_join_clause = from_match.group(1).strip()

    # Extract columns
    in_select = False
    for token in parsed.tokens:
        if token.ttype is sqlparse.tokens.DML and token.value.upper() == 'SELECT':
            in_select = True
            continue
        if in_select:
            if isinstance(token, sqlparse.sql.IdentifierList):
                for identifier in token.get_identifiers():
                    process_column(identifier, tables, alias_map, fields)
            elif isinstance(token, sqlparse.sql.Identifier):
                process_column(token, tables, alias_map, fields)
            elif token.value.upper() == 'FROM':
                break

    # Process WHERE clause
    where_clause = next((t for t in parsed.tokens if isinstance(t, sqlparse.sql.Where)), None)
    if where_clause:
        where_str = where_clause.value[5:].strip()  # Remove "WHERE"
        where_conditions = [
            clean_quoted_text(cond.strip()) 
            for cond in re.split(r'\s+AND\s+(?![^(]*\))', where_str, flags=re.IGNORECASE) 
            if cond.strip()
        ]

    return tables, fields, where_conditions, from_join_clause

def process_column(column, tables: List[str], alias_map: Dict[str, str], fields: List[Tuple[str, str, str]]):
    """Process individual column definition"""
    col_str = column.value.strip()

    # Handle AS aliases
    if ' as ' in col_str.lower():
        parts = re.split(r'\s+as\s+', col_str, flags=re.IGNORECASE)
        expr = parts[0].strip()
        alias = parts[1].strip()
    else:
        # Handle implicit aliases
        if '(' not in col_str and ')' not in col_str:
            parts = col_str.split()
            if len(parts) > 1:
                expr = ' '.join(parts[:-1]).strip()
                alias = parts[-1].strip()
            else:
                expr = col_str
                alias = expr.split('.')[-1] if '.' in expr else expr
        else:
            expr = col_str
            alias = expr

    # Determine field table and name
    if re.search(r'[^\w\.]', expr) or is_literal(expr):
        field_table = ""
        field_name = expr
    else:
        if '.' in expr:
            table_part, field_name = expr.split('.', 1)
            field_table = alias_map.get(table_part.lower(), table_part)
        else:
            field_table = tables[0] if tables else ""
            field_name = expr

    # Clean alias
    if '.' in alias:
        alias = alias.split('.')[-1]

    fields.append((field_table, field_name, alias))


def generate_output(input_data: List[Dict[str, str]]) -> List[List[Union[str, List[str]]]]:
    """Generate structured output from input data"""
    results = []
    for item in input_data:
        sql = item.get('sql', '')
        target = item.get('target_name', '')

        try:
            tables, fields, where_conds, from_clause = parse_sql_chunk(sql, target)
            where_clause = ' AND '.join(where_conds) if where_conds else ''

            for table, field, alias in fields:
                results.append([
                    target,
                    table,
                    field,
                    alias,
                    from_clause,
                    where_clause
                ])
        except Exception as e:
            # logger.info(f"Error processing query for target {target}: {str(e)}")
            continue

    return results

def create_final_dataframe(input_data: List[Dict[str, str]]) -> pd.DataFrame:
    """Create final cleaned DataFrame"""
    data = generate_output(input_data)
    # logger.info(f"created {len(data)} rows in the final DataFrame")
    df = pd.DataFrame(data, columns=[
        'TargetName', 
        'SourceTable', 
        'Field', 
        'TargetAlias', 
        'FromClause', 
        'WhereClause'
    ])

    df['TargetName'] = df['TargetName'].str.lower()
    # Final cleaning
    df['WhereClause'] = df['WhereClause'].apply(
        lambda x: clean_quoted_text(str(x)) if pd.notna(x) and str(x).strip() else x
    )

    # Add derived columns
    df['TargetField'] = df['TargetName'] + '.' + df['TargetAlias']
    df['SourceField'] = df.apply(
        lambda r: r['Field'] if not r['SourceTable'] or pd.isna(r['SourceTable'])
        else f"{r['SourceTable']}.{r['Field']}",
        axis=1
    )

    return df




async def build_source_chunk_sql_list(node_dict, node_name):
    source_chunk_sql_list = []
    sources = node_dict.get(node_name, {}).get("Sources", [])

    for source in sources:
        if source not in node_dict:
            # Skip missing source
            logger.info(f"⚠️ Source '{source}' not found in node_dict, skipping...")
            continue
        
        if node_dict[source].get("Is Primary") == "Yes":
            # Primary nodes: try "Chunk SQL Primary" first, fallback to "Direct SQL"
            sql_value = node_dict[source].get("Chunk SQL Primary") or node_dict[source].get("Direct SQL")
        else:
            sql_value = node_dict[source].get("Chunk SQL")
        
        if sql_value is not None:
            source_chunk_sql_list.append({
                "sql": sql_value,
                "target_name": source
            })

    return source_chunk_sql_list




async def process_projection_node_sql(node, node_dict, schema, base, derived, ds_name):

    node_name = node["Node name"]
    sql_query = node["Node SQL"]
    chunk_schema = node["Chunk Schema"]

    tnf = node["No of Fields"] + node["No of formula"]
    # logger.info(f"TNF for {node_name}: {tnf}")
    
    # logger.info(node["is_leafnode_chunkwise"])
    # logger.info(node["Is Primary"])

    # logging.info(f"Processing Projection node: {node_name}")
    if node["is_leafnode_chunkwise"] == 'Yes' and node["Is Primary"] == "Yes":
        node["Chunk SQL Primary"] = sql_query
        # logger.info(f"Primary node {node_name} with leaf status 'Yes' processed.")
        # logger.info(sql_query)
        # logger.info(f"Primary filled for {node_name}")
    elif node["is_leafnode"] == 'Yes':
        #  logger.info(f"Primary filled for {node_name}")
         return node
    else:

        source_list = await build_source_chunk_sql_list(node_dict, node_name)
        # logger.info(f"source_list: {source_list}")
        # Generate and display results
        df = await asyncio.to_thread(create_final_dataframe, source_list)

        final_sql  = await transform_sql_query_using_df(sql_query, df)

        final_sql = await process_from_where_sql_projection(final_sql)

        source_sql = source_list
        # logger.info(f"Source SQL for {node_name}:{source_sql}")
        target_structure_sql = sql_query
        # logger.info(f"Target structure SQL for {node_name}:{target_structure_sql}")
        manually_converted_sql = final_sql
        # logger.info(f"manually_converted_sql for {node_name}:{manually_converted_sql}")
        


        final_sql = await construct_sql_corrected_chunk(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, df, base, derived, ds_name, tnf, chunk_schema)
       

        # logger.info("Final comes here:")
        # logger.info(final_sql)
        # logger.info(f"Is primary: {node['Is Primary']}")
        if node["Is Primary"] == "Yes":
                # logger.info(f"Primary node {node_name} with leaf status 'No' processed.")
                # final_sql = parse_and_reorder_sql(final_sql)
                node["Chunk SQL Primary"] = final_sql
                # logger.info(final_sql)
                # logger.info(f"Primary filled for {node_name}")
        else:
            node["Chunk SQL"] = final_sql
            # logger.info(f"Node:{node_name}")
            # logger.info(final_sql)

        return node  




async def process_join_node_sql(node, node_dict, schema, base, derived, ds_name):


    node_name = node["Node name"]
    sql_query = node["Node SQL"]
    join_type = node.get("Jointype") or "leftOuter"
    tnf = node["No of Fields"] + node["No of formula"]
    chunk_schema = node["Chunk Schema"]
    logger.info(node_name)



    if node["is_leafnode_chunkwise"] == 'Yes' and node["Is Primary"] == "Yes":
        node["Chunk SQL Primary"] = sql_query
        # logger.info(f"Primary filled for {node_name}")
    elif node["is_leafnode"] == 'Yes':
        #  logger.info(f"Primary filled for {node_name}")
         return node
    else:
        source_list = await build_source_chunk_sql_list(node_dict, node_name)
        df = await asyncio.to_thread(create_final_dataframe, source_list)
        final_sql  = await transform_sql_query_using_df(sql_query, df)
        final_sql = await process_from_where_sql(final_sql, df, join_type)
        # logger.info(f"After from where processing for {node_name}:{final_sql}")
        source_sql = source_list
        # logger.info(f"Source SQL for {node_name}:{source_sql}")
        target_structure_sql = sql_query
        # logger.info(f"Target structure SQL for {node_name}:{target_structure_sql}")
        manually_converted_sql = final_sql
        # logger.info(f"manually_converted_sql for {node_name}:{manually_converted_sql}")
        
        final_sql = await construct_sql_corrected_chunk(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, df, base, derived, ds_name, tnf, chunk_schema)
        

        if node["Is Primary"] == "Yes":
                # final_sql = parse_and_reorder_sql(final_sql)
                node["Chunk SQL Primary"] = final_sql
                # logger.info(f"Primary filled for {node_name}")
        else:
            node["Chunk SQL"] = final_sql
            # logger.info(f"Node:{node_name}")
            # logger.info(final_sql)

        return node 




async def process_from_where_sql(final_sql, df, join_type):


    source_from = await extract_distinct_from_clauses(df)

    try:
        my_current_from = await extract_full_from_clause(final_sql)
    except ValueError:
        logger.warning("No FROM clause found in SQL, skipping FROM/WHERE processing")
        return final_sql

    join_type = join_type.lower()
    count_join = my_current_from.lower().split().count("join")


    # Generate corrected FROM clause
    from_prompt = f"""Task: Correct the SQL 'FROM' clause in 'my current output' based on the provided 'source FROM'. The join types {join_type}.

        Context:
        The 'source FROM' lists the intended primary tables and their immediate joins, including the specific join types used. 'my current output' contains the SQL 'FROM' clause that needs to be corrected.

        source FROM:
        {chr(10).join([f"- {item}" for item in source_from])}

        My Current SQL 'FROM' Clause:
        {my_current_from}

        Instructions:
        1. Analyze 'source FROM': Identify the base tables and their direct join relationships, paying close attention to the specified join types (`LEFT OUTER JOIN`, `INNER JOIN`, etc.) and join conditions.
        2. Compare with 'My Current SQL 'FROM' Clause':
            - Check if all tables and joins (including join types and conditions) from 'source FROM' are present in 'My Current SQL 'FROM' Clause'.
            - Note any discrepancies in table order, join types, or join conditions.
        3. Prioritize Retention: Retain all tables and JOIN clauses already present. Do NOT remove any.
        4. Incorporate 'source FROM' if missing joins exist, without disrupting current logic.
        5. Ensure the corrected clause is syntactically correct and complete.
        6. Output ONLY the corrected SQL 'FROM' clause as a single string.
        7. No Subqueries or Comments.
        8. The expected number of joins is approximately {count_join}.
    """
    from_text = await api_call_with_retry_async('Gemini', from_prompt,task_type= 'sql')
    from_text = remove_before_first_from(from_text)
    from_text = remove_non_sql_context(from_text)
    from_text = remove_unwanted_patterns(from_text)
    from_text = '\n'.join(remove_sql_comments(from_text.splitlines()))
    # Validate LLM output before replacing
    if from_text and re.search(r'\bFROM\b', from_text, re.IGNORECASE):
        final_sql = await replace_from_clause(final_sql, from_text)
    else:
        logger.warning("LLM FROM clause output invalid, keeping original FROM clause")

    # Extract and Optimize WHERE clause
    my_current_where = await extract_where_clause(final_sql)

    if my_current_where:
        where_prompt = f"""Task: Optimize the following SQL WHERE clause to eliminate redundancy while preserving all filtering logic.
        WHERE conditions may include AND, OR, NOT, and parentheses.
        Format of the WHERE condition must be sourceTableName.fieldName = value.

        WHERE Clause to Optimize:
        {my_current_where}

        Output E.g: WHERE sourceTableName.fieldName = value AND sourceTableName2.fieldName2 = value2

        Instructions:
        1. Remove duplicate conditions or logically equivalent filters.
        2. Simplify overly complex expressions (like unnecessary parentheses or repeated ANDs/ORs).
        3. Keep the structure readable and valid SQL.
        4. Do NOT remove any filters unless they are exact duplicates.
        5. Output ONLY the optimized SQL WHERE clause. No explanation or extra formatting.
        """
        optimized_where = await api_call_with_retry_async('Gemini', where_prompt,task_type= 'sql')
        optimized_where = remove_non_sql_context(optimized_where)
        optimized_where = remove_unwanted_patterns(optimized_where)
        optimized_where = '\n'.join(remove_sql_comments(optimized_where.splitlines()))
        # Validate LLM output before replacing
        if optimized_where and re.search(r'\bWHERE\b', optimized_where, re.IGNORECASE):
            final_sql = await replace_where_clause(final_sql, optimized_where)
        else:
            logger.warning("LLM WHERE clause output invalid, keeping original WHERE clause")

    return final_sql




async def process_from_where_sql_projection(final_sql):

    
    # Extract FROM

    # Extract and Optimize WHERE clause
    my_current_where = await extract_where_clause(final_sql)
    if my_current_where:
        where_prompt = f"""Task: Optimize the following SQL WHERE clause to eliminate redundancy while preserving all filtering logic.
        WHERE conditions may include AND, OR, NOT, and parentheses.
        Format of the WHERE condition must be sourceTableName.fieldName = value.

        WHERE Clause to Optimize:
        {my_current_where}

        Output E.g: WHERE sourceTableName.fieldName = value AND sourceTableName2.fieldName2 = value2

        Instructions:
        1. Remove duplicate conditions or logically equivalent filters.
        2. Simplify overly complex expressions (like unnecessary parentheses or repeated ANDs/ORs).
        3. Keep the structure readable and valid SQL.
        4. Do NOT remove any filters unless they are exact duplicates.
        5. Output ONLY the optimized SQL WHERE clause. No explanation or extra formatting.
        """
        optimized_where = await api_call_with_retry_async('Gemini', where_prompt,task_type= 'sql')
        optimized_where = remove_non_sql_context(optimized_where)
        optimized_where = remove_unwanted_patterns(optimized_where)
        optimized_where = '\n'.join(remove_sql_comments(optimized_where.splitlines()))
        final_sql = await replace_where_clause(final_sql, optimized_where)

    return final_sql





async def extract_full_from_clause(sql_query):
    """Extract the entire FROM clause including all JOINs."""
    sql_query = re.sub(r'\s+', ' ', sql_query.strip(), flags=re.DOTALL)

    # Pattern to match FROM + all JOINs until the next major clause
    from_pattern = r'(FROM\s.+?)(?=\s(?:WHERE|GROUP BY|HAVING|ORDER BY)|$)'

    match = re.search(from_pattern, sql_query, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    else:
        raise ValueError("No FROM clause found in the SQL query")





async def extract_where_clause(sql_query):
    """Extract the WHERE clause including all conditions. Returns None if not found."""
    sql_query = re.sub(r'\s+', ' ', sql_query.strip(), flags=re.DOTALL)

    # Pattern to match WHERE until the next major clause or end
    where_pattern = r'(WHERE\s.+?)(?=\s(?:GROUP BY|HAVING|ORDER BY)|$)'

    match = re.search(where_pattern, sql_query, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    else:
        return None





async def replace_from_clause(sql_query, new_from_clause):
    """
    Replace the existing FROM clause (including JOINs) with the new one.
    """
    # Normalize SQL query
    sql_query = re.sub(r'\s+', ' ', sql_query.strip(), flags=re.DOTALL)

    # Regex pattern to find the full FROM clause
    from_pattern = r'(FROM\s.+?)(?=\s(?:WHERE|GROUP BY|HAVING|ORDER BY|LIMIT|UNION|INTERSECT|EXCEPT)\b|$)'

    # Replace the matched FROM clause with the new one
    modified_query = re.sub(from_pattern, new_from_clause, sql_query, flags=re.IGNORECASE)

    return modified_query





async def replace_where_clause(sql_query, new_where_clause):
    """
    Replace the existing WHERE clause with a new one.
    If no WHERE clause exists, leave the SQL query unchanged.
    """
    # Normalize SQL query
    sql_query = re.sub(r'\s+', ' ', sql_query.strip(), flags=re.DOTALL)

    # Pattern to match WHERE clause
    where_pattern = r'(WHERE\s.+?)(?=\s(?:GROUP BY|HAVING|ORDER BY|LIMIT|UNION|INTERSECT|EXCEPT)\b|$)'

    if re.search(where_pattern, sql_query, flags=re.IGNORECASE):
        # If WHERE clause exists, replace it
        modified_query = re.sub(where_pattern, new_where_clause, sql_query, flags=re.IGNORECASE)
    else:
        # If no WHERE clause is found, return the original query as is
        modified_query = sql_query

    return modified_query





async def extract_distinct_from_clauses(df) -> str:
    # Check if the 'FromClause' column exists
    if 'FromClause' not in df.columns:
        return ""

    # Extract the 'FromClause' column, drop NaN values, get unique values, and take the first 2
    distinct_clauses = df['FromClause'].dropna().unique()

    # Format the distinct clauses into a numbered list
    if distinct_clauses.size > 0:
        formatted_clauses = "\n".join([f"{i+1}. {clause}" for i, clause in enumerate(distinct_clauses)])
        return formatted_clauses
    else:
        return ""




async def process_aggregation_node_sql(node, node_dict, schema, base, derived, ds_name):
    node_name = node["Node name"]
    sql_query = node["Node SQL"]
    chunk_schema = node["Chunk Schema"]
    # logger.info(node_name)
    schema = node["Chunk Schema"]
    tnf = node["No of Fields"] + node["No of formula"]

    if node["is_leafnode_chunkwise"] == 'Yes' and node["Is Primary"] == "Yes":
        node["Chunk SQL Primary"] = sql_query
        # logger.info(f"Primary filled for {node_name}")
    elif node["is_leafnode"] == 'Yes':
        #  logger.info(f"Primary filled for {node_name}")
         return node
    else:

        source_list = await build_source_chunk_sql_list(node_dict, node_name)
        # Generate and display results
        df = await asyncio.to_thread(create_final_dataframe, source_list)

        final_sql  = await transform_sql_query_using_df(sql_query, df)

        final_sql = await parse_and_reorder_sql(final_sql)

        source_sql = source_list
        target_structure_sql = sql_query
        manually_converted_sql = final_sql
        final_sql = await construct_sql_corrected_chunk_aggr_rank(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, df, base, derived, ds_name, tnf, chunk_schema)

        if node["Is Primary"] == "Yes":
                # final_sql = parse_and_reorder_sql(final_sql)
                node["Chunk SQL Primary"] = final_sql
                # logger.info("Final SQL for Primary Node:")
                # logger.info(final_sql)
                # logger.info(f"Primary filled for {node_name}")
                # logger.info(final_sql)
        else:
            node["Chunk SQL"] = final_sql
            # logger.info(f"Node:{node_name}")
            # logger.info(final_sql)

        return node  




async def process_union_node_sql(node, node_dict, schema, base, derived, ds_name):

    node_name = node["Node name"]
    sql_query = node["Node SQL"]
    chunk_schema = node["Chunk Schema"]
    # logger.info(node_name)
    # schema = node["Chunk Schema"]

    if node["is_leafnode_chunkwise"] == 'Yes' and node["Is Primary"] == "Yes":
        node["Chunk SQL Primary"] = sql_query
        # logger.info(f"Primary filled for {node_name}")
    elif node["is_leafnode"] == 'Yes':
        #  logger.info(f"Primary filled for {node_name}")
         return node
    else:




        source_chunk_sql_list = await build_source_chunk_sql_list(node_dict, node_name)

        # Extract only the SQL statements
        processed_statements = [item["sql"] for item in source_chunk_sql_list if item.get("sql")]

        # Merge with UNION ALL
        if processed_statements:
            final_result = " UNION ALL ".join(processed_statements) if len(processed_statements) > 1 else processed_statements[0]
        else:
            final_result = None  # Or handle empty case


        # logger.info("Final Result for Union Node:")
        # logger.info(final_result) 

        source_sql = source_chunk_sql_list
        # logger.info("Source SQL for Union Node:")
        # logger.info(source_sql)
        target_structure_sql = sql_query
        # logger.info("Target Structure SQL for Union Node:")
        # logger.info(target_structure_sql)
        # manually_converted_sql = final_result
        # logger.info("Manually Converted SQL for Union Node:")
        # logger.info(manually_converted_sql)

        manually_converted_sql = f"""{final_result}
                                This is Union All sql query. It has been constructed with source sql but with alias names of target sql."""

        final_sql = await construct_sql_corrected_chunk_for_union(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, base, derived, ds_name, chunk_schema)  
        




        
          
        if node["Is Primary"] == "Yes":
                final_result = final_sql
                node["Chunk SQL Primary"] = final_result
                # logger.info("Final SQL for Primary Node:")
                # logger.info(final_result)
                # logger.info(f"Primary filled for {node_name}")
        else:
            node["Chunk SQL"] = final_sql

        return node 





def extract_sql_statements(sql):
    # Parse the SQL query
    parsed = sqlglot.parse_one(sql)

    result = []

    # Check if this is a UNION ALL query
    if isinstance(parsed, exp.Union):
        # Get all queries in the UNION
        for query in parsed.flatten():
            if isinstance(query, exp.Select):
                result.append(query.sql())
    else:
        # Handle single SELECT queries
        result.append(parsed.sql())

    return result







def get_table_chunks_from_select(sql_query, node_dict):
    """Extract tables from a single SELECT query and get their Chunk SQL from node_dict"""
    # Parse the SQL to find all tables
    parsed = sqlglot.parse_one(sql_query)

    # Get unique tables from the query
    tables = {table.name for table in parsed.find_all(exp.Table)}

    # Create a case-insensitive mapping of node_dict keys
    node_dict_case_insensitive = {k.lower(): v for k, v in node_dict.items()}

    # Prepare result with Chunk SQL from node_dict
    result = []
    for table in tables:
        lower_table = table.lower()
        if lower_table in node_dict_case_insensitive and node_dict_case_insensitive[lower_table].get("Chunk SQL"):
            result.append({
                "sql": node_dict_case_insensitive[lower_table]["Chunk SQL"],
                "target_name": table  # Keep the original case from the query if needed
                # Alternatively, use the dictionary key's original case:
                # "target_name": next(k for k in node_dict if k.lower() == lower_table)
            })
    return result




async def process_rank_node_sql(node, node_dict, schema, base, derived, ds_name):
    # logger.info("Processing Rank Node SQL")

    node_name = node["Node name"]
    sql_query = node["Node SQL"]
    # logger.info(node_name)
    tnf = node["No of Fields"] + node["No of formula"]
    chunk_schema = node["Chunk Schema"]

    if node["is_leafnode_chunkwise"] == 'Yes' and node["Is Primary"] == "Yes":
        node["Chunk SQL Primary"] = sql_query
        # logger.info(f"Primary filled for {node_name}")
    elif node["is_leafnode"] == 'Yes':
        #  logger.info(f"Primary filled for {node_name}")
         return node
    else:


        source_list = await build_source_chunk_sql_list(node_dict, node_name)
        # Generate and display results
        df = await asyncio.to_thread(create_final_dataframe, source_list)

        final_sql  = await transform_sql_query_using_df(sql_query, df)
 
        final_sql = await process_from_where_sql_projection(final_sql)


        source_sql = source_list
        target_structure_sql = sql_query
        manually_converted_sql = final_sql
        final_sql = await construct_sql_corrected_chunk_aggr_rank(node_name, source_sql, target_structure_sql, manually_converted_sql, schema, df, base, derived, ds_name, tnf, chunk_schema)


        if node["Is Primary"] == "Yes":
                # final_sql = parse_and_reorder_sql(final_sql)
                node["Chunk SQL Primary"] = final_sql
                # logger.info(f"Primary filled for {node_name}")
        else:
            node["Chunk SQL"] = final_sql
            # logger.info(f"Node:{node_name}")

        return node  





async def parse_and_reorder_sql(sql_query):
    """Parse SQL query and return properly ordered SQL without duplicates."""
    # Normalize whitespace
    sql_query = re.sub(r'\s+', ' ', sql_query.strip(), flags=re.DOTALL)

    # Correct clause order for a SQL SELECT query
    clause_order = ['SELECT', 'FROM', 'JOIN', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY']
    clause_patterns = {
        'SELECT': r'(SELECT\s.+?)(?=\sFROM|\sJOIN|\sWHERE|\sGROUP BY|\sHAVING|\sORDER BY|$)',
        'FROM': r'(FROM\s.+?)(?=\sJOIN|\sWHERE|\sGROUP BY|\sHAVING|\sORDER BY|$)',
        'JOIN': r'((?:LEFT|RIGHT|INNER|OUTER|CROSS)?\s*JOIN\s.+?\s(?:ON|USING)\s.+?)(?=\sJOIN|\sWHERE|\sGROUP BY|\sHAVING|\sORDER BY|$)',
        'WHERE': r'(WHERE\s.+?)(?=\sGROUP BY|\sHAVING|\sORDER BY|$)',
        'GROUP BY': r'(GROUP BY\s.+?)(?=\sHAVING|\sORDER BY|$)',
        'HAVING': r'(HAVING\s.+?)(?=\sORDER BY|$)',
        'ORDER BY': r'(ORDER BY\s.+?$)'
    }

    remaining_sql = sql_query
    clauses = {}

    for clause, pattern in clause_patterns.items():
        match = re.search(pattern, remaining_sql, re.IGNORECASE)
        if match:
            if clause == 'JOIN':
                # Multiple joins possible
                clauses[clause] = []
                while match:
                    join_clause = match.group(1).strip()
                    clauses[clause].append(join_clause)
                    remaining_sql = remaining_sql.replace(join_clause, '', 1)
                    match = re.search(pattern, remaining_sql, re.IGNORECASE)
            else:
                clauses[clause] = match.group(1).strip()
                remaining_sql = remaining_sql.replace(match.group(1), '', 1)

    # Check mandatory clauses
    if 'SELECT' not in clauses or 'FROM' not in clauses:
        raise ValueError("Invalid SQL: Missing SELECT or FROM clause")

    # Rebuild SQL in proper order
    result = []
    for clause in clause_order:
        if clause == 'JOIN' and clause in clauses:
            result.extend(clauses[clause])
        elif clause in clauses:
            result.append(clauses[clause])

    return ' '.join(result)







def compute_parent_counts(nodes):
    node_dict = {node["Node name"]: node for node in nodes}
    for node in nodes:
        node["parent_count"] = 0
    for node in nodes:
        for src in node["Sources"]:
            if src in node_dict:
                node_dict[src]["parent_count"] += 1
    return node_dict


def group_nodes_into_chunks(nodes):
    node_dict = compute_parent_counts(nodes)
    chunks = []
    processed = set()
    chunk_map = {}
    original_order = {node['Node name']: idx for idx, node in enumerate(nodes)}

    # Phase 1: Process multi-parent nodes first
    for node in sorted(nodes, key=lambda x: original_order[x['Node name']]):
        name = node['Node name']
        if name in processed:
            continue
        if node['parent_count'] > 1 and node['Node type'] in ('Projection', 'JoinNode'):
            chunk = {
                'primary': name,
                'merged_nodes': [name],
                'chunk_sources': list(node['Sources'])
            }
            chunks.append(chunk)
            processed.add(name)
            chunk_map[name] = len(chunks)-1

    # Phase 2: Process primary nodes (Aggregation, Rank, Union)
    for node in sorted(nodes, key=lambda x: original_order[x['Node name']]):
        name = node['Node name']
        if name in processed:
            continue
        if node['Node type'] not in ('Aggregation', 'Rank', 'Union'):
            continue

        chunk = {
            'primary': name,
            'merged_nodes': [name],
            'chunk_sources': []
        }
        queue = deque(node['Sources'])
        processed.add(name)

        while queue:
            src = queue.popleft()
            if src in processed:
                chunk['chunk_sources'].append(src)
                continue
            if src not in node_dict:
                chunk['chunk_sources'].append(src)
                continue

            src_node = node_dict[src]
            if src_node['parent_count'] == 1 and src_node['Node type'] in ('Projection', 'JoinNode'):
                chunk['merged_nodes'].append(src)
                processed.add(src)
                queue.extend(src_node['Sources'])
            else:
                chunk['chunk_sources'].append(src)

        chunks.append(chunk)
        chunk_map[name] = len(chunks)-1

    # Phase 3: Process remaining nodes
    for node in sorted(nodes, key=lambda x: original_order[x['Node name']]):
        name = node['Node name']
        if name in processed:
            continue

        chunk = {
            'primary': name,
            'merged_nodes': [name],
            'chunk_sources': list(node['Sources'])
        }
        chunks.append(chunk)
        processed.add(name)
        chunk_map[name] = len(chunks)-1

    # Resolve chunk sources to primary nodes
    for chunk in chunks:
        resolved = []
        for src in chunk['chunk_sources']:
            if src in chunk_map:
                resolved.append(chunks[chunk_map[src]]['primary'])
            else:
                resolved.append(src)
        # Deduplicate preserving order
        seen = set()
        chunk['chunk_sources'] = [x for x in resolved if not (x in seen or seen.add(x))]

    # Assign chunk numbers based on original order of primaries
    primaries = [chunk['primary'] for chunk in chunks]
    ordered_primaries = sorted(primaries, key=lambda x: original_order[x])
    chunk_numbers = {primary: i+1 for i, primary in enumerate(ordered_primaries)}
    for chunk in chunks:
        chunk['chunk_number'] = chunk_numbers[chunk['primary']]






thinking_instruction = (
    "You are a helpful SQL assistant. Think step-by-step before providing the final answer. "
    "Read and understand the Prompts line by line carefully. Analyze the user's request thoroughly "
    "based on prompt and return only SQL no explanation."
)




def update_node_names(node_dict):
    # Helper function for node renaming
    def generate_unique_node_name(base_name, node_type, existing_names):
        prefix = node_type[0].upper() + "_"
        new_name = prefix + base_name
        counter = 1
        while new_name in existing_names:
            new_name = prefix + str(counter) + "_" + base_name
            counter += 1
        return new_name

    # First pass: Node renaming
    existing_node_names = set(node_dict.keys())
    node_name_mapping = {}

    for node_name, node_info in list(node_dict.items()):
        if node_name and node_name[0].isdigit() and node_info["Node type"]:
            new_name = generate_unique_node_name(
                node_name, node_info["Node type"], existing_node_names
            )
            node_name_mapping[node_name] = new_name
            existing_node_names.add(new_name)

    # Update node references
    for node_name, node_info in list(node_dict.items()):
        if node_name in node_name_mapping:
            new_name = node_name_mapping[node_name]
            node_dict[new_name] = node_dict.pop(node_name)
            node_info = node_dict[new_name]
            node_info["Node name"] = new_name.lower()

        for key in ["Sources", "Join Condition", "Node XML"]:
            if key in node_info and node_info[key]:
                if isinstance(node_info[key], list):
                    node_info[key] = [
                        node_name_mapping.get(item, item) for item in node_info[key]
                    ]
                elif isinstance(node_info[key], str):
                    for old, new in node_name_mapping.items():
                        node_info[key] = node_info[key].replace(old, new)

    # Second pass: Field/formula renaming
    all_fields = set()
    for node in node_dict.values():
        all_fields.update(node["Fields"])
        all_fields.update(node["Formula"])
        all_fields.update(node["Aggregated columns"])

    fields_to_rename = [f for f in all_fields if f and f[0].isdigit()]
    field_mapping = {}
    used_names = set(all_fields)

    for field in fields_to_rename:
        base = f"datafield_{field}"
        new_name = base
        counter = 1
        while new_name in used_names:
            new_name = f"datafield_{counter}_{field}"
            counter += 1
        field_mapping[field] = new_name
        used_names.add(new_name)
    # Apply field renaming
    for node in node_dict.values():
        # Update lists
        for key in ["Fields", "Formula", "Aggregated columns"]:
            node[key] = [field_mapping.get(item, item) for item in node[key]]

        # Update XML with whole-word replacement
        if node["Node XML"]:
            xml = node["Node XML"]
            for old, new in field_mapping.items():
                xml = re.sub(rf"\b{re.escape(old)}\b", new, xml)
                node["Node XML"] = xml

    # Clean orphan nodes after renaming but before field transformations
    # This ensures nodes renamed in Phase 1 are properly cleaned up
    node_dict = clean_orphan_nodes(node_dict)

    return node_dict




def clean_custom_string(text: str) -> str:
    if "FACT___" in text:
        return ""
    return text

def process_content(content: str) -> str:
    return re.sub(
        r'(<element\s+name=")([^"]*)(")',
        lambda m: f'{m.group(1)}{clean_custom_string(m.group(2))}{m.group(3)}',
        content
    )


def process_xml_to_nodes(xml_content, node_dict):
    """Process XML file and update node_dict with specific node metadata."""
    # with open(input_file, "r") as file:
    #     input_text = file.read()

    content = xml_content
    content = '\n'.join(line for line in content.splitlines() if '<type xsi:type=' not in line)
    content = process_content(content)
    content = re.sub(r'(<element name="|Name=")\d', r'\1', content)
    content = re.sub(r'(<element name="|Name=")\d', r'\1Z', content)

    input_text = content 
    # XML processing logic (unchanged from original)
    xml_declaration = re.search(r"^<\?xml.*?\?>", input_text, re.DOTALL)
    prolog = xml_declaration.group(0) if xml_declaration else ""

    root_tag_match = re.search(r"<View:ColumnView\s+([^>]*)>", input_text, re.DOTALL)
    if not root_tag_match:
        raise ValueError("Root View:ColumnView element not found")
    namespace_attrs = root_tag_match.group(1)

    content_match = re.search(
        r"<View:ColumnView[^>]*>(.*?)</View:ColumnView>", input_text, re.DOTALL
    )
    if not content_match:
        raise ValueError("Could not find root element content")
    main_content = content_match.group(1).strip()

    viewnode_sections = []
    first_viewnode = re.search(r"<viewNode\b", main_content, re.IGNORECASE)
    if first_viewnode:
        nodes_content = main_content[first_viewnode.start() :]
        split_points = re.finditer(
            r"</viewNode>\s*<viewNode", nodes_content, re.DOTALL | re.IGNORECASE
        )
        last_pos = 0

        for match in split_points:
            end = match.end() - len("<viewNode")
            viewnode_sections.append(nodes_content[last_pos:end].strip())
            last_pos = end

        if last_pos < len(nodes_content):
            viewnode_sections.append(nodes_content[last_pos:].strip())

    # Initialize node number
    node_number = 1  # Sequential node numbering

    for section in viewnode_sections:
        if not section.startswith("<viewNode"):
            continue

        # Extract name attribute
        name_match = re.search(r'name=["\'](.*?)["\']', section, re.IGNORECASE)
        if not name_match:
            continue

        node_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name_match.group(1))

        # Extract node type
        node_type = "Unknown"
        type_match = re.search(r'xsi:type=["\'](.*?)["\']', section, re.IGNORECASE)
        if type_match:
            node_type = type_match.group(1).replace("View:", "")

        # Update node_dict directly (only specific columns)
        if node_name not in node_dict:
            # If the node doesn't exist, initialize it with all fields
            node_dict[node_name] = {
                "Node Number": None,
                "Node name": None,
                "Node type": None,
                "Sources": [],
                "No of sources": None,
                "No of Fields": None,
                "Fields": [],
                "No of formula": None,
                "Formula": [],
                "Filter Used": None,
                "Jointype": None,
                "Join Condition": None,
                "Aggregated columns": [],
                "Node XML": None,
                "Node Prompt": None,
                "Node SQL": None,
                "Chunk Number": None,
                "Is Primary": None,
                "Merged Nodes": [],
                "Chunk Sources": [],
                "Chunk SQL": None,
            }

        # Update only the specified columns
        node_dict[node_name]["Node Number"] = node_number
        node_dict[node_name]["Node name"] = node_name.lower()
        # logger.info(node_dict[node_name]["Node name"])
        node_dict[node_name]["Node type"] = node_type

        node_number += 1
    return input_text, node_dict




# from pprint import pprint
# plogger.info(node_dict)




# Make lowercase_keys globally available
def lowercase_keys(obj):
    if isinstance(obj, dict):
        return {k.lower(): lowercase_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [lowercase_keys(i) for i in obj]
    else:
        return obj

def lowercase_keys_inplace(d):
    updated = lowercase_keys(d)
    d.clear()
    d.update(updated)




# lowercase_keys(node_dict)





# Helper function to transform to JSON string
def transform_to_json(input_dict):
    structured = {
        key: {col: "<Datatype>" for col in cols}
        for key, cols in input_dict.items()
    }
    return json.dumps(structured, indent=2)

# Main function to enhance each node
def add_node_columns_schema(node_dict):
    for node_name, node_data in node_dict.items():
        # Combine Fields and Formula (if they exist)
        fields = node_data.get("Fields", [])
        formulas = node_data.get("Formula", [])
        all_fields = fields + formulas

        # Save all fields
        node_data["All Fields"] = all_fields

        # Create schema with <Datatype> placeholders
        schema = {field: "<Datatype>" for field in all_fields}
        node_data["Node Schema"] = {node_name: schema}

        # Create pretty JSON string
        node_data["Node Schema JSON"] = json.dumps(
            {node_name: schema},
            indent=2
        )




# add_source_columns_schema(node_dict)





def add_source_columns_schema(node_dict):
    for node_name, node_data in node_dict.items():
        sql = node_data.get("Node SQL", [])
        # sources = node_data.get("Sources", [])
        result = extract_tables_and_fields_source(sql)
        # # Save all fields
        # node_data["All Fields only"] = fields

        # # Create schema with <Datatype> placeholders
        # schema = {field: "<Datatype>" for field in fields}

        # # Create schema mapping for each source
        # source_schema = {source: schema for source in sources}
        # node_data["Source Schema"] = source_schema

        # Create pretty JSON string using actual source names
        node_data["Source Schema JSON"] = json.dumps(
            result,
            indent=2
        )
        # logger.info(f"Source Schema JSON for {node_name}:\n{node_data['Source Schema JSON']}\n")




def find_actual_sources_chunkwise(node_dict):
    # First, prepare a big list of all sources chunk number wise
    chunk_sources = {}

    # Build a set of all node names for quick lookup
    all_node_names = set()
    for node_data in node_dict.values():
        if node_data["Node name"] is not None:
            all_node_names.add(node_data["Node name"])

    # Process each node in the dictionary
    for node_name, node_data in node_dict.items():
        chunk_num = node_data["Chunk Number"]
        sources = node_data["Sources"]

        if chunk_num is not None:
            if chunk_num not in chunk_sources:
                chunk_sources[chunk_num] = []
            chunk_sources[chunk_num].extend(sources)

    # Now check which sources are not present in any node name
    result = {}

    for chunk_num, sources in chunk_sources.items():
        missing_sources = []
        for source in sources:
            if source not in all_node_names:
                missing_sources.append(source)

        if missing_sources:
            result[chunk_num] = missing_sources
    # logger.info(f"Missing sources chunkwise: {result}")
    return result




import re
import sqlglot
from sqlglot.expressions import Table, Column

def extract_tables_and_fields_source(sql):
    sql = sql.strip().strip('"').rstrip(";")
    # Fix raw strings like r'abc'
    sql = re.sub(r"\br'([^']*)'", r"'\1'", sql)

    try:
        parsed = sqlglot.parse_one(sql, read="bigquery")
    except Exception as e:
        logger.info(f"Parse failed: {e}")
        return {}

    if not parsed:
        return {}

    # Collect alias → base table mapping
    table_alias_map = {}
    for table in parsed.find_all(Table):
        base_name = table.name
        alias = table.alias_or_name
        table_alias_map[alias] = base_name

    # Collect fields per base table
    table_fields = {tbl: set() for tbl in table_alias_map.values()}
    for col in parsed.find_all(Column):
        prefix = col.table
        column = col.name
        if prefix and prefix in table_alias_map:
            base_table = table_alias_map[prefix]
            table_fields[base_table].add(column)

    return {
        table: {field: "<datatype>" for field in sorted(fields)}
        for table, fields in table_fields.items() if fields
    }



async def process_sources_json_datatype_parallel_async(node_dict, max_concurrent=50):
    """
    Process all source nodes in parallel using asyncio with concurrency control.

    :param node_dict: Dictionary of nodes to process.
    :param max_concurrent: Maximum number of concurrent tasks to avoid overloading.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_node(name):
        async with semaphore:
            try:
                await process_single_source_json_datatype_async(
                    name,
                    node_dict[name],
                    node_dict[name].get("Source Schema JSON")
                )
            except Exception as e:
                # Optionally log or handle errors per node
                pass

    # Filter all nodes that have Node XML
    node_names = [n for n in node_dict if node_dict[n].get("Node XML")]

    # Create async tasks
    tasks = [run_node(name) for name in node_names]

    # Run tasks concurrently
    await asyncio.gather(*tasks)


def process_sources_json_datatype_parallel(node_dict):
    """Process nodes with rate limiting and parallel execution."""
    node_names = list(node_dict.keys())

    # Rate limiting configuration
    CHUNK_SIZE = 20
    chunks = [
        node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
        for chunk in chunks:
            futures = {
                executor.submit(
                    process_single_source_json_datatype,
                    name,
                    node_dict[name],
                    node_dict[name].get("Source Schema JSON"),
                ): name
                for name in chunk
                if node_dict[name].get("Node XML")
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    # logger.info(f"Critical failure processing {name}: {e}")
                    pass




def process_single_source_json_datatype(node_name, node, node_schema_json):
    """Process a single node's JSON datatype."""
    xml_content = node["Node XML"]
    is_leafnode = node["is_leafnode"]
    # if node_name != 'business_t':
    #     return
    # if is_leafnode == 'No':
    #     return  # Skip leaf nodes
    json = node_schema_json
    if not json:
        return  # Skip if no JSON schema is available

    original_prompt = f"""
    Given this SAP HANA table definition in XML:
    {xml_content}

    json template = {json}

    Convert it to a BigQuery schema by performing ONLY these actions:
    1. Map each HANA data type to the correct BigQuery type (see mapping below)
    2. Do not explicitely mention Adjust precision/scale NUMERIC.
    3. Remove duplicate columns if any exist
    4. Enforce BigQuery naming conventions
    

    STRICT DATA TYPE MAPPING RULES:
    - NVARCHAR/VARCHAR/CHAR → STRING
    - INTEGER/BIGINT → INT64  
    - DECIMAL(p,s) → NUMERIC
    - SMALLDECIMAL → NUMERIC or FLOAT64 if unknown precision
    - REAL/DOUBLE → FLOAT64
    - DATE → DATE
    - TIME → STRING (or TIME if explicitly time-only)
    - TIMESTAMP/SECONDDATE → TIMESTAMP
    - BOOLEAN → BOOL
    - BLOB/VARBINARY → BYTES

    NUMERIC PRECISION RULES:
    - If p > 38: Use BIGNUMERIC
    - Default to NUMERIC for financial data
    - Use FLOAT64 only for approximate values

    OUTPUT REQUIREMENTS:
    - Return ONLY the JSON schema with updated types
    - Do NOT add explanations, notes, or comments
    - Preserve ALL original field names and structure exactly as given in the provided JSON template
    - Do NOT rename inner field names; only replace "<datatype>" with the correct BigQuery type
    - Do NOT replace  keys from the provided JSON template (e.g., if the input has "tg1c_prh3", it must remain "tg1c_prh3"). Ignore XML table or view names and do not use them as replacements.You muust consider XML content only for DataTypes.
    - Only update the datatype placeholders with the mapped BigQuery type


    IMPORTANT:
    - If any field cannot be mapped, use STRING as fallback
    - Skip all other suggestions/improvements - ONLY update data types within json template shared.
    """

    text = api_call_with_retry('Gemini', original_prompt,task_type= 'data_type')  # Capture the return value

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        json_op = match.group(0)

    node["Source Schema w/ datatype JSON"] = json_op



async def process_single_source_json_datatype_async(node_name, node, node_schema_json):
    """Process a single node's JSON datatype."""
    xml_content = node["Node XML"]
    is_leafnode = node["is_leafnode"]
    # if node_name != 'business_t':
    #     return
    # if is_leafnode == 'No':
    #     return  # Skip leaf nodes
    json = node_schema_json
    if not json:
        return  # Skip if no JSON schema is available

    original_prompt = f"""
    Given this SAP HANA table definition in XML:
    {xml_content}

    json template = {json}

    Convert it to a BigQuery schema by performing ONLY these actions:
    1. Map each HANA data type to the correct BigQuery type (see mapping below)
    2. Do not explicitely mention Adjust precision/scale NUMERIC.
    3. Remove duplicate columns if any exist
    4. Enforce BigQuery naming conventions
    

    STRICT DATA TYPE MAPPING RULES:
    - NVARCHAR/VARCHAR/CHAR → STRING
    - INTEGER/BIGINT → INT64  
    - DECIMAL(p,s) → NUMERIC
    - SMALLDECIMAL → NUMERIC or FLOAT64 if unknown precision
    - REAL/DOUBLE → FLOAT64
    - DATE → DATE
    - TIME → STRING (or TIME if explicitly time-only)
    - TIMESTAMP/SECONDDATE → TIMESTAMP
    - BOOLEAN → BOOL
    - BLOB/VARBINARY → BYTES

    NUMERIC PRECISION RULES:
    - If p > 38: Use BIGNUMERIC
    - Default to NUMERIC for financial data
    - Use FLOAT64 only for approximate values

    OUTPUT REQUIREMENTS:
    - Return ONLY the JSON schema with updated types
    - Do NOT add explanations, notes, or comments
    - Preserve ALL original field names and structure exactly as given in the provided JSON template
    - Do NOT rename inner field names; only replace "<datatype>" with the correct BigQuery type
    - Do NOT replace  keys from the provided JSON template (e.g., if the input has "tg1c_prh3", it must remain "tg1c_prh3"). Ignore XML table or view names and do not use them as replacements.You muust consider XML content only for DataTypes.
    - Only update the datatype placeholders with the mapped BigQuery type


    IMPORTANT:
    - If any field cannot be mapped, use STRING as fallback
    - Skip all other suggestions/improvements - ONLY update data types within json template shared.
    """

    text = await api_call_with_retry_async('Gemini', original_prompt,task_type= 'data_type')  # Capture the return value

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        json_op = match.group(0)

    node["Source Schema w/ datatype JSON"] = json_op




def process_json_datatype_parallel(node_dict):
    """Process nodes with rate limiting and parallel execution."""
    node_names = list(node_dict.keys())

    # Rate limiting configuration
    CHUNK_SIZE = 20
    chunks = [
        node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
        for chunk in chunks:
            futures = {
                executor.submit(
                    process_single_json_datatype,
                    name,
                    node_dict[name],
                    node_dict[name].get("Node Schema JSON"),
                ): name
                for name in chunk
                if node_dict[name].get("Node XML")
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    # logger.info(f"Critical failure processing {name}: {e}")
                    pass



async def process_json_datatype_parallel_async(node_dict, max_concurrent=50):
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_node(name):
        async with semaphore:
            await process_single_json_datatype_async(
                name,
                node_dict[name],
                node_dict[name].get("Node Schema JSON")
            )

    node_names = [n for n in node_dict if node_dict[n].get("Node XML")]

    tasks = [run_node(name) for name in node_names]
    await asyncio.gather(*tasks)



def process_temp_table_parallel_for_chunks(node_dict):
    """Process nodes with rate limiting and parallel execution."""
    node_names = list(node_dict.keys())

    # Rate limiting configuration
    CHUNK_SIZE = 20
    chunks = [
        node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
        for chunk in chunks:
            futures = {
                executor.submit(
                    process_single_temp_table_for_chunks,
                    name,
                    node_dict[name],
                    node_dict[name].get("Chunk Schema"),
                ): name
                for name in chunk
                if node_dict[name].get("Node XML")
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    # logger.info(f"Critical failure processing {name}: {e}")
                    pass




async def process_temp_table_parallel_for_chunks_async(node_dict, max_concurrent=50):
    """
    Process temp table nodes (chunked) in parallel using asyncio with concurrency control.

    :param node_dict: Dictionary of nodes to process.
    :param max_concurrent: Maximum number of concurrent tasks to avoid overloading.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_node(name):
        async with semaphore:
            try:
                await process_single_temp_table_for_chunks_async(
                    name,
                    node_dict[name],
                    node_dict[name].get("Chunk Schema")
                )
            except Exception as e:
                # Optionally log or handle errors per node
                pass

    # Filter all nodes that have Node XML
    node_names = [n for n in node_dict if node_dict[n].get("Node XML")]

    # Create async tasks
    tasks = [run_node(name) for name in node_names]

    # Run tasks concurrently
    await asyncio.gather(*tasks)



def process_single_temp_table_for_chunks(node_name, node, chunk_schema_json):
    """Process a single node's JSON datatype and create temp tables for multiple sources."""

    node_name = node["Node name"]
    json_schema = chunk_schema_json

    if not json_schema:
        return  # Skip if no JSON schema is available

    # logger.info(f"Processing temp table for node: {node_name}")

    # Updated prompt to use CREATE OR REPLACE TEMP TABLE
    prompt = f"""You are a BigQuery SQL generator. Given the following JSON schema which contains one or more table definitions, generate CREATE OR REPLACE TEMP TABLE statements for each table. If columns with Exact same name exists in same table, then remove duplicate columns and keep only one column with that name. Otherwise this will cause an error in BigQuery.

Format:
CREATE OR REPLACE TEMP TABLE `<table_name>` (
    `column_name` DATA_TYPE,
    ...
);

JSON Schema:
{json_schema}
"""

    # Get SQL from LLM API
    response_sql = api_call_with_retry('Gemini', prompt, task_type='sql')

    # Updated regex to capture "CREATE OR REPLACE TEMP TABLE" as well
    temp_table_sqls = re.findall(r'CREATE (?:OR REPLACE )?TEMP TABLE .*?\);', response_sql, flags=re.DOTALL)

    # Join all temp table SQL statements
    node["Temp table for Chunks"] = "\n\n".join(temp_table_sqls)

    # logger.info(f"Processed temp table SQL for {node_name}:\n{node['Temp table for Chunks']}\n")




async def process_single_temp_table_for_chunks_async(node_name, node, chunk_schema_json):
    """Process a single node's JSON datatype and create temp tables for multiple sources."""

    node_name = node["Node name"]
    json_schema = chunk_schema_json

    if not json_schema:
        return  # Skip if no JSON schema is available

    # logger.info(f"Processing temp table for node: {node_name}")

    # Updated prompt to use CREATE OR REPLACE TEMP TABLE
    prompt = f"""You are a BigQuery SQL generator. Given the following JSON schema which contains one or more table definitions, generate CREATE OR REPLACE TEMP TABLE statements for each table. If columns with Exact same name exists in same table, then remove duplicate columns and keep only one column with that name. Otherwise this will cause an error in BigQuery.

Format:
CREATE OR REPLACE TEMP TABLE `<table_name>` (
    `column_name` DATA_TYPE,
    ...
);

JSON Schema:
{json_schema}
"""

    # Get SQL from LLM API
    response_sql = await api_call_with_retry_async('Gemini', prompt, task_type='sql')

    # Updated regex to capture "CREATE OR REPLACE TEMP TABLE" as well
    temp_table_sqls = re.findall(r'CREATE (?:OR REPLACE )?TEMP TABLE .*?\);', response_sql, flags=re.DOTALL)

    # Join all temp table SQL statements
    node["Temp table for Chunks"] = "\n\n".join(temp_table_sqls)

    # logger.info(f"Processed temp table SQL for {node_name}:\n{node['Temp table for Chunks']}\n")




def process_temp_table_parallel(node_dict):
    """Process nodes with rate limiting and parallel execution."""
    node_names = list(node_dict.keys())

    # Rate limiting configuration
    CHUNK_SIZE = 20
    chunks = [
        node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
        for chunk in chunks:
            futures = {
                executor.submit(
                    process_single_temp_table,
                    name,
                    node_dict[name],
                    node_dict[name].get("Source Schema w/ datatype JSON"),
                ): name
                for name in chunk
                if node_dict[name].get("Node XML")
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    # logger.info(f"Critical failure processing {name}: {e}")
                    pass



async def process_temp_table_parallel_async(node_dict, max_concurrent=50):
    """
    Process temp table nodes in parallel using asyncio with concurrency control.

    :param node_dict: Dictionary of nodes to process.
    :param max_concurrent: Maximum number of concurrent tasks to avoid overloading.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_node(name):
        async with semaphore:
            try:
                await process_single_temp_table_async(
                    name,
                    node_dict[name],
                    node_dict[name].get("Source Schema w/ datatype JSON")
                )
            except Exception as e:
                # Optionally log or handle errors per node
                pass

    # Filter all nodes that have Node XML
    node_names = [n for n in node_dict if node_dict[n].get("Node XML")]

    # Create async tasks
    tasks = [run_node(name) for name in node_names]

    # Run tasks concurrently
    await asyncio.gather(*tasks)




def process_single_temp_table(node_name, node, node_schema_json):
    """Process a single node's JSON datatype and create temp tables for multiple sources."""

    node_name = node["Node name"]
    sources = node["Sources"]
    json_schema = node_schema_json

    if not json_schema:
        return  # Skip if no JSON schema is available

    temp_table_sqls = []

    if not isinstance(sources, list):
        sources = [sources]  # Ensure sources is always a list

    for source in sources:
        prompt = f"""You are a BigQuery SQL generator. Generate a CREATE OR REPLACE TEMP TABLE statement using the following schema.
Table name: {source} (keep in lowercase)
Schema: {json_schema}

Format:
CREATE OR REPLACE TEMP TABLE {source} (
    column_name DATA_TYPE,
    ...
);
"""

        # Get SQL using your API call
        response_sql = api_call_with_retry('Gemini', prompt, task_type='sql')

        # Updated regex to match optional OR REPLACE
        match = re.search(
            rf'CREATE\s+(?:OR\s+REPLACE\s+)?TEMP\s+TABLE\s+{re.escape(source)}\s*\(.*?\);',
            response_sql,
            re.DOTALL | re.IGNORECASE
        )

        if match:
            temp_table_sqls.append(match.group(0))
        else:
            temp_table_sqls.append(f"-- ❌ Failed to parse SQL for {source}")

    # Join all temp table SQL statements
    node["Temp table"] = "\n\n".join(temp_table_sqls)


async def process_single_temp_table_async(node_name, node, node_schema_json):
    """Process a single node's JSON datatype and create temp tables for multiple sources."""

    node_name = node["Node name"]
    sources = node["Sources"]
    json_schema = node_schema_json

    if not json_schema:
        return  # Skip if no JSON schema is available

    temp_table_sqls = []

    if not isinstance(sources, list):
        sources = [sources]  # Ensure sources is always a list

    for source in sources:
        prompt = f"""You are a BigQuery SQL generator. Generate a CREATE OR REPLACE TEMP TABLE statement using the following schema.
Table name: {source} (keep in lowercase)
Schema: {json_schema}

Format:
CREATE OR REPLACE TEMP TABLE {source} (
    column_name DATA_TYPE,
    ...
);
"""

        # Get SQL using your API call
        response_sql = await api_call_with_retry_async('Gemini', prompt, task_type='sql')

        # Updated regex to match optional OR REPLACE
        match = re.search(
            rf'CREATE\s+(?:OR\s+REPLACE\s+)?TEMP\s+TABLE\s+{re.escape(source)}\s*\(.*?\);',
            response_sql,
            re.DOTALL | re.IGNORECASE
        )

        if match:
            temp_table_sqls.append(match.group(0))
        else:
            temp_table_sqls.append(f"-- ❌ Failed to parse SQL for {source}")

    # Join all temp table SQL statements
    node["Temp table"] = "\n\n".join(temp_table_sqls)




async def process_single_json_datatype_async(node_name, node, node_schema_json):
    """Process a single node's JSON datatype."""
    node_name = node["Node name"]
    logger.info(f"Processing node: {node_name}")
    xml_content = node["Node XML"]
    json = node_schema_json
    if not json:
        return  # Skip if no JSON schema is available
    original_prompt = f"""{xml_content}
                        Above is the XML.
                        Update target column data types for all fields.
                        I’m migrating data models and table structures from SAP HANA  to Google BigQuery. Please help me:
                        Convert SAP HANA-specific data types to valid BigQuery types
                        Validate and correct all BigQuery data types and constraints
                        Fix precision/scale issues for NUMERICs
                        Follow BigQuery naming conventions
                        Suggest BigQuery-native modeling improvements where applicable

                        🧠 1. SAP HANA to BigQuery Type Mapping
                        Map these commonly used HANA types:

                        HANA Type	BigQuery Type	Notes
                        NVARCHAR(n)	STRING	Length not required in BigQuery
                        VARCHAR(n)	STRING	Same
                        CHAR(n)	STRING	Pad logic (if any) must be migrated manually
                        INTEGER	INT64	Direct
                        BIGINT	INT64	Same
                        DECIMAL(p,s)	NUMERIC	Ensure p ≤ 15, s ≤ 3; else use BIGNUMERIC
                        SMALLDECIMAL	FLOAT64 or NUMERIC	Prefer NUMERIC if precision is known
                        REAL, DOUBLE	FLOAT64	
                        DATE	DATE	Direct
                        TIME	STRING or TIME	BigQuery doesn't support TIME in all contexts
                        SECONDDATE, TIMESTAMP	TIMESTAMP	UTC format
                        BOOLEAN	BOOL	
                        BLOB, VARBINARY	BYTES	For binary columns

                        ✅ Avoid using unsupported types like TEXT, CLOB, or custom domains — convert to STRING.

                        🛠️ 2. NUMERIC & BIGNUMERIC Rules in BigQuery
                        For NUMERIC(p,s): p must be between 1 and 15, s between 0 and 3
                        If scale s > 0, then precision p must be ≤ 15
                        For high-precision HANA values like DECIMAL(38, 15), switch to BIGNUMERIC
                        If unsure, default to:
                        NUMERIC for financial/volume values
                        FLOAT64 for non-critical approximations
                        🧱 3. Data Modeling Best Practices in BigQuery
                        Flatten star schemas where appropriate; denormalize if performance requires it
                        Use partitioning and clustering based on access patterns (reporting_date, activation_date)
                        Avoid surrogate keys unless required for joins; BigQuery is columnar
                        Use standard SQL only, no legacy SQL

                        🛡️ 5. Migration Sanity Checks
                        ✅ Validate all NUMERIC are within BigQuery’s limits
                        ✅ Replace unsupported data types

                        Suggest best data types, rename columns clearly

                        Below JSON is my desired output:
                        {json}
                        Please update the JSON with the correct data types relavant to Bigquery SQL based on the XML content.
                        Output only the updated JSON with correct data types of BigQuery Compatible.
                        Strictly follow the below format:
                        {json}"""
    text = await api_call_with_retry_async('Gemini', original_prompt,task_type= 'data_type')  # Capture the return value

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        json_op = match.group(0)

    node["Node Schema w/ datatype JSON"] = json_op  




def process_single_json_datatype(node_name, node, node_schema_json):
    """Process a single node's JSON datatype."""
    node_name = node["Node name"]
    # logger.info(f"Processing node: {node_name}")
    xml_content = node["Node XML"]
    json = node_schema_json
    if not json:
        return  # Skip if no JSON schema is available
    original_prompt = f"""{xml_content}
                        Above is the XML.
                        Update target column data types for all fields.
                        I’m migrating data models and table structures from SAP HANA  to Google BigQuery. Please help me:
                        Convert SAP HANA-specific data types to valid BigQuery types
                        Validate and correct all BigQuery data types and constraints
                        Fix precision/scale issues for NUMERICs
                        Follow BigQuery naming conventions
                        Suggest BigQuery-native modeling improvements where applicable

                        🧠 1. SAP HANA to BigQuery Type Mapping
                        Map these commonly used HANA types:

                        HANA Type	BigQuery Type	Notes
                        NVARCHAR(n)	STRING	Length not required in BigQuery
                        VARCHAR(n)	STRING	Same
                        CHAR(n)	STRING	Pad logic (if any) must be migrated manually
                        INTEGER	INT64	Direct
                        BIGINT	INT64	Same
                        DECIMAL(p,s)	NUMERIC	Ensure p ≤ 15, s ≤ 3; else use BIGNUMERIC
                        SMALLDECIMAL	FLOAT64 or NUMERIC	Prefer NUMERIC if precision is known
                        REAL, DOUBLE	FLOAT64	
                        DATE	DATE	Direct
                        TIME	STRING or TIME	BigQuery doesn't support TIME in all contexts
                        SECONDDATE, TIMESTAMP	TIMESTAMP	UTC format
                        BOOLEAN	BOOL	
                        BLOB, VARBINARY	BYTES	For binary columns

                        ✅ Avoid using unsupported types like TEXT, CLOB, or custom domains — convert to STRING.

                        🛠️ 2. NUMERIC & BIGNUMERIC Rules in BigQuery
                        For NUMERIC(p,s): p must be between 1 and 15, s between 0 and 3
                        If scale s > 0, then precision p must be ≤ 15
                        For high-precision HANA values like DECIMAL(38, 15), switch to BIGNUMERIC
                        If unsure, default to:
                        NUMERIC for financial/volume values
                        FLOAT64 for non-critical approximations
                        🧱 3. Data Modeling Best Practices in BigQuery
                        Flatten star schemas where appropriate; denormalize if performance requires it
                        Use partitioning and clustering based on access patterns (reporting_date, activation_date)
                        Avoid surrogate keys unless required for joins; BigQuery is columnar
                        Use standard SQL only, no legacy SQL

                        🛡️ 5. Migration Sanity Checks
                        ✅ Validate all NUMERIC are within BigQuery’s limits
                        ✅ Replace unsupported data types

                        Suggest best data types, rename columns clearly

                        Below JSON is my desired output:
                        {json}
                        Please update the JSON with the correct data types relavant to Bigquery SQL based on the XML content.
                        Output only the updated JSON with correct data types of BigQuery Compatible.
                        Strictly follow the below format:
                        {json}"""
    text = api_call_with_retry('Gemini', original_prompt,task_type= 'data_type')  # Capture the return value

    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        json_op = match.group(0)

    node["Node Schema w/ datatype JSON"] = json_op  








def update_correct_node_numbers(node_dict):
    """
    Update the 'Node Number' in the given node_dict based on topological order.

    Args:
        node_dict (dict): A dictionary where each key is a node name and each value is a dictionary containing
                          "Node Number", "Node name", "Node type", and "Sources".
    Returns:
        dict: The updated node_dict with 'Node Number' fields populated.
    """
    # Build a dependency graph based on the "Sources" field.
    graph = defaultdict(list)
    in_degree = {node: 0 for node in node_dict}  # incoming edge count for each node

    for node, details in node_dict.items():
        for source in details["Sources"]:
            if source in node_dict:  # only consider if the source is a node
                graph[source].append(node)
                in_degree[node] += 1

    # Perform topological sort using Kahn's algorithm.
    queue = deque([node for node in node_dict if in_degree[node] == 0])
    order = 1

    while queue:
        current = queue.popleft()
        # Update the Node Number in node_dict with the current order.
        node_dict[current]["Node Number"] = order
        order += 1

        for neighbor in graph[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Check for cycles (if topological sort is incomplete)
    if any(in_degree[node] > 0 for node in node_dict):
        raise ValueError("A cycle exists in the dependency graph.")

    # Return the updated dictionary with node numbers.
    return node_dict





def replace_formula_placeholders(xml):
    formula_pattern = r'(<formula>)(.*?)(</formula>)'

    def replacer(match):
        formula_text = match.group(2)
        # Replace patterns like '$$...$$' or "$$...$$" with ''
        modified_formula = re.sub(r"(['\"])\$\$.*?\$\$\1", "''", formula_text)
        return f"{match.group(1)}{modified_formula}{match.group(3)}"

    return re.sub(formula_pattern, replacer, xml, flags=re.DOTALL)






def update_node_xml(input_text, node_dict):

    # with open(file, "r") as file:
    #     input_text = file.read()

    # # Remove unwanted characters
    # input_text = re.sub(r'=".*?___', '="', input_text)  # Remove unwanted characters

    # Extract XML declaration
    xml_declaration = re.search(r"^<\?xml.*?\?>", input_text, re.DOTALL)
    prolog = xml_declaration.group(0) if xml_declaration else ""

    # Extract root element attributes
    root_tag_match = re.search(r"<View:ColumnView\s+([^>]*)>", input_text, re.DOTALL)
    if not root_tag_match:
        raise ValueError("Root View:ColumnView element not found")
    namespace_attrs = root_tag_match.group(1)

    # Extract content inside root element
    content_match = re.search(
        r"<View:ColumnView[^>]*>(.*?)</View:ColumnView>", input_text, re.DOTALL
    )
    if not content_match:
        raise ValueError("Could not find root element content")
    main_content = content_match.group(1).strip()

    # Split into sections
    viewnode_sections = []
    preamble_content = None

    # Find first viewNode occurrence
    first_viewnode = re.search(r"<viewNode\b", main_content, re.IGNORECASE)
    if first_viewnode:
        preamble_candidate = main_content[: first_viewnode.start()].strip()

        if preamble_candidate and re.search(
            r"<viewNode\b", preamble_candidate, re.IGNORECASE
        ):
            preamble_content = preamble_candidate

        nodes_content = main_content[first_viewnode.start() :]
        split_points = re.finditer(
            r"</viewNode>\s*<viewNode", nodes_content, re.DOTALL | re.IGNORECASE
        )
        last_pos = 0

        for match in split_points:
            end = match.end() - len("<viewNode")
            viewnode_sections.append(nodes_content[last_pos:end].strip())
            last_pos = end

        if last_pos < len(nodes_content):
            viewnode_sections.append(nodes_content[last_pos:].strip())

    # Update Node XML in the dictionary
    updated_nodes = 0
    for section in viewnode_sections:
        if not section.lower().startswith("<viewnode"):
            continue

        name_match = re.search(r'name=["\'](.*?)["\']', section, re.IGNORECASE)
        if not name_match:
            continue

        node_name = name_match.group(1)
        if node_name in node_dict:
            wrapped_content = f"{prolog}\n<root {namespace_attrs}>\n{section}\n</root>"
            node_dict[node_name]["Node XML"] = wrapped_content
            updated_nodes += 1

        # step 4: alias adjustment
    node_dict = {
        k.lower(): v for k, v in node_dict.items()
    }






def update_datasource_details(node_dict):
    """Updates node_dict with source information from XML content"""
    # Extract XML content for processing
    nodes_xml = {
        node_name: node_info["Node XML"] for node_name, node_info in node_dict.items()
    }

    # Process XML to get sources for each node
    processed_sources = process_nodes(nodes_xml)

    # Update each node's source information
    for node_name, node_info in node_dict.items():
        sources = processed_sources.get(node_name)

        # Normalize to list format based on node type
        if isinstance(sources, str):
            sources_list = [sources]  # Single source
        elif isinstance(sources, list):
            sources_list = sources  # Multiple sources
        else:
            sources_list = []  # No sources (should never occur due to validation)

        # Update node entry
        node_info["Sources"] = [s.lower() for s in sources_list]
        node_info["No of sources"] = len(sources_list)


def process_nodes(nodes_dict):
    """Process nodes to extract inputs with special handling for Join and Union nodes"""
    output = {}
    for node_name, xml_content in nodes_dict.items():
        try:
            root = ET.fromstring(xml_content)
            inputs = []
            is_join = False
            is_union = False

            # Find all input elements
            for input_elem in root.findall(".//input"):
                entity = input_elem.find("entity")
                if entity is not None and entity.text:
                    inputs.append(entity.text.strip())
                    continue

                view_node = input_elem.find("viewNode")
                if view_node is not None and view_node.text:
                    inputs.append(view_node.text.strip())
                    node_type = view_node.get("xsi:type", "")
                    if "Join" in node_type:
                        is_join = True
                    elif "Union" in node_type:
                        is_union = True

            # Process input names
            processed_inputs = []
            for input_text in inputs:
                clean_text = re.sub(r'#//|"', "", input_text)
                input_name = extract_input_name(clean_text)
                if input_name:
                    processed_inputs.append(input_name)

            # Validation and formatting
            if is_join:
                if len(processed_inputs) != 2:
                    raise ValueError(
                        f"Join node {node_name} must have exactly 2 inputs"
                    )
                output[node_name] = processed_inputs
            elif is_union:
                if len(processed_inputs) < 1:
                    raise ValueError(f"Union node {node_name} must have ≥1 input")
                output[node_name] = processed_inputs
            else:
                if not processed_inputs:
                    raise ValueError(f"Node {node_name} must have ≥1 input")
                output[node_name] = (
                    processed_inputs[0]
                    if len(processed_inputs) == 1
                    else processed_inputs
                )

        except ET.ParseError as e:
            raise ValueError(f"Invalid XML for node {node_name}: {str(e)}")
    return output


def extract_input_name(text):
    """Extract the last segment from paths containing ::, /, or .
    For SQL references, returns the table name rather than column name."""
    if not isinstance(text, str):
        return None

    parts = re.split(r"(?:::|/|\.)", text)
    non_empty = [p for p in parts if p]

    # For SQL references with exactly 3 parts (db.schema.table or schema.table.column)
    if len(non_empty) >= 3 and not ('/' in text or '::' in text):
        return non_empty[-2]  # Return the table name (second to last)
    return non_empty[-1] if non_empty else None






def update_node_fields(node_dict):
    for node_name, node_data in node_dict.items():
        xml_content = node_data.get("Node XML")

        if not xml_content:
            continue

        root = ET.fromstring(xml_content)

        # Initialize lists to separate formula and non-formula elements
        field_names = []
        formula_names = []

        # Iterate over all elements
        for elem in root.findall(".//element"):
            name = elem.get("name")
            if not name:
                continue  # Skip elements without a name

            # Check if the element has a formula in its calculationDefinition
            if elem.find("calculationDefinition/formula") is not None:
                formula_names.append(name)
            else:
                field_names.append(name)

        # Check for filter expressions
        filter_formula = root.find(".//filterExpression/formula")
        filter_yn = "Yes" if filter_formula is not None else "No"

        # Update dictionary values
        node_data.update(
            {
                "Node name": node_name.lower(),
                "No of Fields": len(field_names),
                "Fields": field_names,
                "No of formula": len(formula_names),
                "Formula": formula_names,
                "Filter Used": filter_yn,
            }
        )






def read_prompt(prompt_file):
    """Read the prompt from a file."""
    with open(prompt_file, "r") as file:
        return file.read()


def remove_before_first_select(sql_text):
    """
    Enhanced extraction of SQL from LLM output.
    Removes markdown code blocks, common prefixes, and isolates the SELECT statement.
    """
    if not sql_text:
        return ""
    
    # Remove markdown code blocks first (```sql or ```bigquery or just ```)
    sql_text = re.sub(r'```(?:sql|bigquery)?\s*\n?', '', sql_text, flags=re.IGNORECASE)
    sql_text = re.sub(r'```\s*$', '', sql_text, flags=re.MULTILINE)
    sql_text = re.sub(r'```', '', sql_text)
    
    # Remove common LLM prefixes/explanations
    prefixes_to_remove = [
        r'^Here is the (?:refined |optimized |final )?SQL.*?:\s*',
        r'^The (?:refined |optimized |final )?SQL.*?:\s*',
        r'^SQL:?\s*',
        r'^Output:?\s*',
        r'^Result:?\s*',
        r'^Query:?\s*',
        r'^BigQuery SQL:?\s*',
        r'^\*\*SQL\*\*:?\s*',
    ]
    for prefix in prefixes_to_remove:
        sql_text = re.sub(prefix, '', sql_text, flags=re.IGNORECASE | re.MULTILINE)
    
    # Find SELECT and trim everything before
    select_index = sql_text.upper().find("SELECT")
    if select_index != -1:
        return sql_text[select_index:].strip()
    return sql_text.strip()

def remove_before_first_from(sql_text):
    """Remove all text before the first occurrence of the FROM keyword."""
    select_index = sql_text.upper().find("FROM")
    if select_index != -1:
        return sql_text[select_index:]
    return sql_text


def remove_unwanted_patterns(sql_text):
    """Remove unwanted patterns like ''', '''sql, and "sql."""
    sql_text = sql_text.replace("```sql", "").replace("```sql", "")
    sql_text = sql_text.replace('"sql', "").replace('"SQL', "")
    sql_text = sql_text.replace("```", "").replace("```", "")
    return sql_text


def fix_cast_syntax(sql_text):
    """
    Fix malformed CAST expressions where AS keyword is missing.
    Converts CAST(expr TYPE) to CAST(expr AS TYPE).
    
    Examples:
        CAST(column INT64) -> CAST(column AS INT64)
        CAST(value NUMERIC) -> CAST(value AS NUMERIC)
    """
    # Common BigQuery types that might appear without AS
    bq_types = r'(INT64|INT|INTEGER|FLOAT64|FLOAT|NUMERIC|BIGNUMERIC|STRING|BOOL|BOOLEAN|DATE|DATETIME|TIMESTAMP|TIME|BYTES|ARRAY|STRUCT)'
    
    # Pattern: CAST( ... <space> TYPE ) where AS is missing
    # Match CAST(anything TYPE) but not CAST(anything AS TYPE)
    pattern = rf'CAST\s*\(\s*([^)]+?)\s+(?!AS\s)({bq_types})\s*\)'
    replacement = r'CAST(\1 AS \2)'
    
    # Apply fix (case-insensitive for CAST keyword but preserve type case)
    fixed_sql = re.sub(pattern, replacement, sql_text, flags=re.IGNORECASE)
    
    return fixed_sql

import sqlparse

def remove_non_sql_context(sql_text: str) -> str:
    """
    Removes non-SQL content after the last valid SQL statement.
    Cleans comments, empty lines, and keeps all SQL statements.
    Returns a single string containing clean SQL.
    """
    # 1. Parse SQL statements using sqlparse
    statements = sqlparse.parse(sql_text)

    cleaned_statements = []
    for stmt in statements:
        # Skip empty statements
        if not stmt.tokens:
            continue
        
        # Clean statement: remove comments and re-indent
        cleaned = sqlparse.format(str(stmt), strip_comments=True, reindent=True).strip()
        if cleaned:
            cleaned_statements.append(cleaned)
    
    # 2. If no statements found, try heuristic: take lines until last semicolon
    if not cleaned_statements:
        lines = sql_text.splitlines()
        end_index = len(lines)
        for i in reversed(range(len(lines))):
            line = lines[i].strip()
            if not line:
                continue
            if line.endswith(";"):
                end_index = i + 1
                break
        cleaned_statements.append("\n".join(lines[:end_index]).strip())
    
    # 3. Return all statements as a single string
    return "\n\n".join(cleaned_statements)


def remove_non_sql_context_dont_use(sql_text):
    """
    Removes trailing non-SQL content after the last valid SQL statement,
    while preserving valid SQL lines.
    """
    if not sql_text or not sql_text.strip():
        return sql_text
        
    lines = sql_text.splitlines()
    if not lines:
        return sql_text
        
    end_index = len(lines)
    found_sql_content = False

    # Keywords that require arguments (invalid if dangling)
    clause_keywords = {
        "WHERE", "JOIN", "ON", "ORDER", "GROUP", "HAVING",
        "LIMIT", "OFFSET", "INTO", "VALUES", "SET",
        "AS", "WHEN", "THEN", "ELSE", "BY", "AND", "OR"
    }

    # Keywords that can stand alone or start a statement
    sql_keywords = {
        "SELECT", "FROM", "WITH", "WITH RECURSIVE",
        "INSERT", "UPDATE", "DELETE", "MERGE",
        "CREATE", "DROP", "ALTER", "TRUNCATE",
        "UNION", "UNION ALL", "EXCEPT", "INTERSECT",
        "BEGIN", "COMMIT", "ROLLBACK", "START", "DECLARE"
    }

    # Combined set for quick lookup
    all_keywords = clause_keywords.union(sql_keywords)

    for i in reversed(range(len(lines))):
        line = lines[i].strip()
        if not line:
            continue  # skip empty lines

        upper_line = line.upper()
        
        # ✅ Case 1: explicit terminator (most reliable)
        if line.rstrip().endswith(";"):
            end_index = i + 1
            found_sql_content = True
            break

        # Split into tokens for analysis
        tokens = upper_line.split()
        if not tokens:
            continue

        first_token = tokens[0]
        
        # ✅ Case 2: clause keywords must have arguments
        if first_token in clause_keywords:
            if len(tokens) > 1:
                # Valid clause with arguments
                end_index = i + 1
                found_sql_content = True
                break
            else:
                # Dangling clause - remove this line and continue searching
                end_index = i
                continue

        # ✅ Case 3: statement keywords are valid stops
        if first_token in sql_keywords:
            end_index = i + 1
            found_sql_content = True
            break
            
        # ✅ Case 4: check if line contains any SQL keywords (not just at start)
        if any(keyword in upper_line for keyword in all_keywords):
            # Line contains SQL content, keep it and everything before
            end_index = i + 1
            found_sql_content = True
            break

        # ✅ Case 5: stop if comment/markdown line (only if we haven't found SQL content yet)
        if not found_sql_content and line[0] in "-/*#`<*!":
            # Only treat as non-SQL if it's at the end and we haven't found real SQL
            if i == len(lines) - 1 or not any(
                any(kw in ln.upper() for kw in all_keywords) 
                for ln in lines[i+1:end_index]
            ):
                end_index = i
                continue

        # ✅ Case 6: stop if junk underscored line
        if line.startswith("_") and (len(line) == 1 or not line[1].isalnum()):
            end_index = i
            continue

        # ✅ Case 7: if we reach here and the line looks like it could be SQL continuation
        # (contains operators, commas, etc.), consider it SQL content
        if any(char in line for char in [',', '=', '>', '<', '(', ')', '+', '-', '*', '/']):
            if not found_sql_content:
                # This might be a continuation of SQL above
                end_index = i + 1
                found_sql_content = True
                # Don't break - continue searching upward for the beginning

    # If no SQL content was found, return empty string or original if it's short
    if not found_sql_content and end_index < len(lines) // 2:
        return "\n".join(lines[:end_index]).rstrip()
        
    cleaned_lines = lines[:end_index]
    result = "\n".join(cleaned_lines).rstrip()
    
    # Final cleanup: ensure we don't leave dangling clauses
    lines_result = result.splitlines()
    if lines_result:
        last_line = lines_result[-1].strip().upper()
        tokens = last_line.split()
        if tokens and tokens[0] in clause_keywords and len(tokens) == 1:
            # Remove dangling clause at the very end
            return "\n".join(lines_result[:-1]).rstrip()
    
    return result



def remove_sql_comments(sql_lines, return_removed_lines=False):
    """
    Removes SQL comments from a list of lines.

    Supports:
    - Single-line comments (-- ...)
    - Block comments (/* ... */), even across multiple lines
    - Inline comments within SQL lines
    - Optionally returns line numbers of removed/modified lines
    """
    in_block_comment = False
    non_comment_lines = []
    removed_line_indices = []

    for idx, line in enumerate(sql_lines):
        original_line = line
        line = line.rstrip()

        # Skip lines if inside a block comment
        if in_block_comment:
            end_idx = line.find("*/")
            if end_idx != -1:
                line = line[end_idx + 2:]
                in_block_comment = False
            else:
                removed_line_indices.append(idx)
                continue

        while "/*" in line:
            start_idx = line.find("/*")
            end_idx = line.find("*/", start_idx + 2)
            if end_idx != -1:
                # Remove inline block comment
                line = line[:start_idx] + line[end_idx + 2:]
            else:
                # Start of multi-line block comment
                line = line[:start_idx]
                in_block_comment = True
                break

        # Remove single-line comments
        line_before_single = line
        line = re.sub(r"--.*", "", line)

        # Track removed or modified lines
        if (original_line != line.strip()) or not line.strip():
            removed_line_indices.append(idx)

        # Keep non-empty lines after stripping
        if line.strip():
            non_comment_lines.append(line.strip())

    if return_removed_lines:
        return non_comment_lines, removed_line_indices
    return non_comment_lines





def is_valid_sql_single(query):
    """
    Validates if a SQL query is a basic SELECT statement.
    """
    if not query or not isinstance(query, str):
        return False

    cleaned = query.strip().upper()
    
    # Remove comments and excessive whitespace for better checking
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned)  # Remove /* */ comments
    cleaned = re.sub(r'--.*$', '', cleaned)      # Remove -- comments
    cleaned = re.sub(r'\s+', ' ', cleaned)       # Normalize whitespace
    
    # Check for basic SELECT structure
    has_select = cleaned.startswith('SELECT')
    has_from = 'FROM' in cleaned
    
    # Additional check: SELECT and FROM should be in reasonable positions
    if has_select and has_from:
        select_pos = 0  # SELECT is at start
        from_pos = cleaned.find('FROM')
        # Basic sanity check: FROM should come after SELECT
        return from_pos > len('SELECT')
    
    return False


def validate_field_coverage(sql: str, xml_content: str) -> List[str]:
    """
    Ensure all targetName fields from XML ElementMapping appear in the generated SQL.
    Returns list of missing field names.
    
    This catches the "missing fields/formula" class of errors before they reach BigQuery.
    """
    if not sql or not xml_content:
        return []
    
    missing = []
    
    # Extract targetName values from XML ElementMapping tags
    # Pattern matches: targetName="fieldname" or targetName='fieldname'
    target_names = re.findall(r'targetName\s*=\s*["\']([^"\']+)["\']', xml_content, re.IGNORECASE)
    
    # Also check for <targetName>fieldname</targetName> style
    target_names += re.findall(r'<targetName>([^<]+)</targetName>', xml_content, re.IGNORECASE)
    
    # Deduplicate while preserving case
    seen = set()
    unique_targets = []
    for t in target_names:
        t_lower = t.lower().strip()
        if t_lower and t_lower not in seen:
            seen.add(t_lower)
            unique_targets.append(t.strip())
    
    sql_upper = sql.upper()
    for target in unique_targets:
        target_upper = target.upper()
        # Check if field appears in SQL (as alias using AS keyword, or as direct column reference)
        # Pattern: AS fieldname or AS `fieldname` or fieldname, or fieldname as part of expression
        if target_upper not in sql_upper:
            # Also check with backticks
            if f"`{target_upper}`" not in sql_upper:
                missing.append(target)
    
    return missing



# Global variables for rate limiting
api_call_timestamps = []
rate_limit_lock = threading.Lock()  # Now this will work

api_call_timestamps = deque()
rate_limit_lock = threading.Lock()






avoidable_rules = [
    "ST01",  # structure.else_null
    "ST02",  # structure.simple_case
    "ST03",  # structure.unused_cte
    "ST06",  # structure.column_order
    "ST07",  # structure.using
    "ST08",  # structure.distinct
    "ST10", 
    "RF04" # structure.constant_expression
]

def validate_node_sql(cleaned_lines):
    """
    Validate BigQuery SQL syntax & style:
      1) Fatal syntax via sqlglot.parse
      2) Detailed linting via sqlfluff

    Returns:
      - [] if no issues
      - Otherwise: list of dicts {
            'type': 'Syntax' | 'Lint',
            'line': int, 'col': int,
            'code': str,    # For lint: e.g. 'L003'; for syntax: 'SQLPARSE'
            'message': str
        }
    """
    sql = "\n".join(cleaned_lines).strip()
    errors = []

    # Check for empty SQL input
    if not sql:
        errors.append({
            "type": "Syntax",
            "line": None,
            "col": None,
            "code": "SQLPARSE",
            "message": "Empty SQL query"
        })
        return errors

    # 1) Validate syntax using sqlglot
    try:
        sqlglot_parse(sql, read="bigquery")
    except ParseError as e:
        for error in e.errors:
            line = error.get('line')
            col = error.get('col')
            message = error.get('description', 'Syntax error')
            errors.append({
                "type": "Syntax",
                "line": line,
                "col": col,
                "code": "SQLPARSE",
                "message": message
            })
        return errors  # Stop on syntax errors
    except Exception as e:
        # Check if it's the "Critical failure processing" error
        if 'Critical failure processing' in str(e):
            errors.append({
                "type": "Syntax",
                "line": None,
                "col": None,
                "code": "SQLPARSE",
                "message": "Critical error processing the SQL query"
            })
        else:
            # Otherwise, return the specific error message
            errors.append({
                "type": "Syntax",
                "line": None,
                "col": None,
                "code": "SQLPARSE",
                "message": str(e)
            })
        return errors

    # 2) Perform linting using sqlfluff
    linter = Linter(dialect="bigquery")
    lint_result = linter.lint_string(sql)

    for violation in lint_result.get_violations():
        # Handle bound method or string attribute
        rule_code = violation.rule_code() if callable(violation.rule_code) else violation.rule_code

        # Skip rules that start with AL, LT, or CP, or are considered avoidable


        if rule_code.startswith(("AL", "LT", "CP")) or rule_code in avoidable_rules:
            continue



        errors.append({
            "type": "Lint",
            "line": violation.line_no,
            "col": violation.line_pos,
            "code": rule_code,
            "message": violation.description
        })

    return errors

def validate_union_all_columns(sql):
    """
    Validates that both sides of UNION/UNION ALL have the same number of columns.
    Returns None if valid, or a detailed error message describing the mismatch string if invalid.
    Includes simplified analysis of missing columns based on aliases if possible.
    """
    if not sql or not sql.strip():
        return None

    try:
        parsed = sqlglot_parse(sql.strip(), read='bigquery')
        if not parsed:
            return None
            
        errors = []
        for statement in parsed:
            # sqlglot represents chained unions as a tree. We need to traverse it.
            # Find all UNION nodes
            for union in statement.find_all(exp.Union):
                left = union.this
                right = union.expression
                
                # We expect both sides to be SELECT statements usually
                if not isinstance(left, exp.Select) or not isinstance(right, exp.Select):
                    continue
                    
                left_cols = left.expressions
                right_cols = right.expressions
                
                if len(left_cols) != len(right_cols):
                    msg = f"🚫 UNION Column Mismatch Detected:\n   - Left query has {len(left_cols)} columns\n   - Right query has {len(right_cols)} columns\n"
                    
                    # Try to identify potential missing columns by alias
                    left_aliases = [c.alias_or_name for c in left_cols]
                    right_aliases = [c.alias_or_name for c in right_cols]
                    
                    # Simple set difference to find potential missing aliases
                    missing_in_right = set(left_aliases) - set(right_aliases)
                    missing_in_left = set(right_aliases) - set(left_aliases)
                    
                    if missing_in_right:
                        msg += f"   - Likely missing in Right side: {', '.join(missing_in_right)}\n"
                    if missing_in_left:
                        msg += f"   - Likely missing in Left side: {', '.join(missing_in_left)}\n"
                        
                    msg += "   Please ensure both SELECT statements in the UNION have the exact same number of columns in the same order."
                    errors.append(msg)
                    
        if errors:
            return "\n\n".join(errors)
            
    except Exception as e:
        # If we can't parse it deeply, skip this validation to avoid noise
        pass
        
    return None


def has_at_most_one_join(sql):
    """
    Check for at most one JOIN using sqlglot AST for accuracy, with regex fallback.
    Returns True if query has 0 or 1 JOINs, False if more than 1.
    """
    if not sql or not sql.strip():
        return True  # Empty SQL has no joins
    
    sql_clean = sql.strip()
    
    # Try sqlglot AST parsing for accurate join count
    try:
        parsed = sqlglot_parse(sql_clean, read='bigquery')
        if parsed:
            join_count = 0
            for statement in parsed:
                # Count all JOIN expressions in the AST
                for join in statement.find_all(exp.Join):
                    join_count += 1
                    if join_count > 1:
                        return False
            return True
    except Exception:
        pass  # Fall back to regex
    
    # Regex fallback - count JOIN keywords (LEFT/RIGHT/INNER/OUTER/CROSS/FULL)
    # Match "JOIN" but not "JOIN" inside strings or comments
    sql_lower = sql_clean.lower()
    
    # Remove string literals to avoid false positives
    sql_no_strings = re.sub(r"'[^']*'", '', sql_lower)
    sql_no_strings = re.sub(r'"[^"]*"', '', sql_no_strings)
    
    # Count JOIN occurrences (with optional prefix like LEFT, RIGHT, etc.)
    join_pattern = r'\b(?:left\s+outer\s+|right\s+outer\s+|full\s+outer\s+|left\s+|right\s+|inner\s+|cross\s+|full\s+)?join\b'
    joins = re.findall(join_pattern, sql_no_strings)
    
    return len(joins) <= 1



# def process_nodes_xml_sql_parallel(node_dict):
#     for node in node_dict:
#         # Lowercase "Node name"
#         node_name = node_dict[node].get("Node name")
#         if node_name is not None:
#             node_dict[node]["Node name"] = node_name.lower()
#         # logger.info(node_dict[node]["Node name"])
#         # Lowercase each source in "Sources"
#         sources = node_dict[node].get("Sources")
#         if sources is not None and isinstance(sources, list):
#             node_dict[node]["Sources"] = [s.lower() for s in sources if isinstance(s, str)]

#     node_dict = {
#         k.lower(): v for k, v in node_dict.items()
#     }

#     node_names = list(node_dict.keys())

#     # Rate limiting configuration
#     CHUNK_SIZE = 20
#     chunks = [
#         node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
#     ]

#     with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
#         for chunk in chunks:
#             futures = {
#                 executor.submit(
#                     process_single_node,
#                     name,
#                     node_dict[name],
#                     node_dict[name].get("Node Prompt"),
#                 ): name
#                 for name in chunk
#                 if node_dict[name].get("Node XML")
#             }
#             for future in concurrent.futures.as_completed(futures):
#                 name = futures[future]
#                 try:
#                     future.result()
#                 except Exception as e:
#                     # logger.info(f"Critical failure processing {name}: {e}")
#                     pass








async def process_nodes_xml_sql_parallel_async(node_dict, max_concurrent=50, target=None):
    """
    Processes all nodes in parallel using asyncio, limiting concurrency with a semaphore.
    
    :param node_dict: Dictionary of nodes to process.
    :param max_concurrent: Maximum concurrent API calls to avoid overloading.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_node(name):
        async with semaphore:
            await process_single_node_async(
                name,
                node_dict[name],
                node_dict[name].get("Node Prompt"),
                target=target
            )

    # Process all nodes with XML content
    node_names = [n for n in node_dict if node_dict[n].get("Node XML")]

    # Create tasks for all nodes
    tasks = [run_node(name) for name in node_names]

    # Run tasks concurrently
    await asyncio.gather(*tasks)


def process_nodes_xml_sql_parallel_for_rank_node(node_dict):
    for node in node_dict:
        # Lowercase "Node name"
        node_name = node_dict[node].get("Node name")
        if node_name is not None:
            node_dict[node]["Node name"] = node_name.lower()
        # logger.info(node_dict[node]["Node name"])
        # Lowercase each source in "Sources"
        sources = node_dict[node].get("Sources")
        if sources is not None and isinstance(sources, list):
            node_dict[node]["Sources"] = [s.lower() for s in sources if isinstance(s, str)]

    node_dict = {
        k.lower(): v for k, v in node_dict.items()
    }

    node_names = list(node_dict.keys())

    # Rate limiting configuration
    CHUNK_SIZE = 10
    chunks = [
        node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
        for chunk in chunks:
            futures = {
                executor.submit(
                    process_single_node_rank,
                    name,
                    node_dict[name],
                    node_dict[name].get("Node Prompt"),
                ): name
                for name in chunk
                if node_dict[name].get("Node XML")
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    # logger.info(f"Critical failure processing {name}: {e}")
                    pass



async def process_nodes_xml_sql_parallel_for_rank_node_async(node_dict, max_concurrent=50):
    """
    Process nodes in parallel for rank node using asyncio with concurrency control.

    :param node_dict: Dictionary of nodes to process.
    :param max_concurrent: Maximum concurrent tasks to avoid overloading.
    """
    # Lowercase "Node name" and sources
    for node in node_dict:
        node_name = node_dict[node].get("Node name")
        if node_name is not None:
            node_dict[node]["Node name"] = node_name.lower()
        sources = node_dict[node].get("Sources")
        if sources is not None and isinstance(sources, list):
            node_dict[node]["Sources"] = [s.lower() for s in sources if isinstance(s, str)]

    # Lowercase node dictionary keys
    node_dict = {k.lower(): v for k, v in node_dict.items()}

    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_node(name):
        async with semaphore:
            try:
                await process_single_node_rank_async(
                    name,
                    node_dict[name],
                    node_dict[name].get("Node Prompt")
                )
            except Exception as e:
                # Optionally log or handle errors per node
                pass

    # Filter all nodes that have Node XML
    node_names = [n for n in node_dict if node_dict[n].get("Node XML")]

    # Create async tasks
    tasks = [run_node(name) for name in node_names]

    # Run tasks concurrently
    await asyncio.gather(*tasks)


# process_nodes_xml_sql_parallel(node_dict)


import logging
from textwrap import dedent

# Assume these helper functions are available globally or imported
# from .utils import (
#     remove_before_first_select, remove_non_sql_context, remove_unwanted_patterns,
#     remove_sql_comments, xml_contains_formula, has_subquery_sqlglot,

#     has_at_most_one_join, is_valid_sql, validate_node_sql, api_call
# )




# Assume all your helper functions like api_call, remove_before_first_select, 
# has_subquery_sqlglot, is_valid_sql, etc., are available.


def basic_sql_validator(sql: str, node: dict, target_name: str = "BigQuery") -> dict:
    """
    Basic validation of SQL against XML expectations using node dictionary metadata.
    Returns dict with:
      - is_valid: bool
      - issues: list of specific problems found
      - expected_columns: int (from node metadata)
      - actual_columns: int (from SQL SELECT)
      - has_expected_joins: bool
      - has_formulas: bool
    """
    result = {
        "is_valid": True,
        "issues": [],
        "expected_columns": 0,
        "actual_columns": 0,
        "has_expected_joins": True,
        "formula_check": None,
    }
    
    if not sql or not sql.strip():
        result["is_valid"] = False
        result["issues"].append("SQL is empty or only whitespace")
        return result
    
    sql_clean = sql.strip()
    sql_upper = sql_clean.upper()
    
    # --- 1. Count expected columns from Node Dictionary ---
    # Expected columns = standard Fields + Formula fields
    fields = node.get("Fields", []) or []
    formulas = node.get("Formula", []) or []
    expected_count = len(fields) + len(formulas)
    result["expected_columns"] = expected_count
    
    # --- 2. Count actual columns in SQL SELECT ---
    try:
        # Use sqlglot for accurate column counting (handles functions/parentheses correctly)
        parsed = sqlglot.parse_one(sql_clean)
        if parsed and isinstance(parsed, sqlglot.exp.Select):
            actual_cols = len(parsed.expressions)
        else:
            # Fallback to regex if not a standard SELECT
            select_match = re.search(r'\bSELECT\b(.*?)\bFROM\b', sql_upper, re.DOTALL)
            if select_match:
                # Rough count by commas, ignoring content inside parentheses
                # Remove content inside parens to avoid counting function commas
                param_less = re.sub(r'\(.*?\)', '', select_match.group(1))
                actual_cols = len([c.strip() for c in param_less.split(',') if c.strip()])
            else:
                actual_cols = 0

        result["actual_columns"] = actual_cols
        
        # Check column count mismatch (with 20% tolerance for leniency)
        if expected_count > 0:
            if actual_cols < expected_count * 0.8:  # Very lenient
                result["is_valid"] = False
                result["issues"].append(
                    f"Column count mismatch: Expected ~{expected_count} columns, "
                    f"but SQL has only {actual_cols}. Verify all fields and formulas are included."
                )
    except Exception:
        pass
    
    # --- 3. Check for expected JOIN from Node Dictionary ---
    # Check if this is a join node or has join conditions
    join_type = node.get("Jointype")
    join_conditions = node.get("Join Condition", []) or []
    
    # Expect joins if Jointype is present or conditions exist
    expect_join = bool(join_type or join_conditions)
    
    if expect_join:
        # Count joins in SQL
        sql_joins = len(re.findall(r'\bJOIN\b', sql_upper))
        
        if sql_joins == 0:
            result["is_valid"] = False
            result["has_expected_joins"] = False
            result["issues"].append(
                f"Node is defined as a JOIN node ({join_type}), but SQL has no JOIN clause."
            )
            
    # --- 4. Check formulas from Node Dictionary ---
    if formulas:
        result["formula_check"] = f"Node has {len(formulas)} formula field(s): {', '.join(formulas[:5])}..."
        # Check if SQL contains at least some formula patterns (CASE, COALESCE, etc.)
        # Expanded list to cover string functions, date functions, type casting, etc.
        formula_keywords = [
            'CASE', 'WHEN', 'THEN', 'ELSE', 'COALESCE', 'IFNULL', 'NULLIF', 'IIF', 'ISNULL', 'IF',
            '+', '-', '*', '/', '%', '||',
            'LTRIM', 'RTRIM', 'TRIM', 'LEFT', 'RIGHT', 'SUBSTR', 'SUBSTRING', 
            'CONCAT', 'REPLACE', 'UPPER', 'LOWER', 'LENGTH', 'LEN',
            'CAST', 'CONVERT', 'DATE', 'YEAR', 'MONTH', 'DAY', 'TO_', 'ROUND', 'ABS', 'FLOOR', 'CEIL'
        ]
        has_formula_sql = any(kw in sql_upper for kw in formula_keywords)
        if not has_formula_sql:
            result["is_valid"] = False
            result["issues"].append(
                f"Node has {len(formulas)} formula(s) defined, but SQL contains no formula expressions (CASE, COALESCE, arithmetic)."
            )
    
    # --- 5. Basic syntax checks ---
    if not sql_upper.strip().startswith("SELECT"):
        result["is_valid"] = False
        result["issues"].append("SQL must start with SELECT keyword")
    
    # Check for subqueries (quick regex check)
    if re.search(r'\(\s*SELECT\b', sql_upper):
        result["is_valid"] = False
        result["issues"].append("Subquery detected (SELECT inside parentheses). Flatten to single SELECT with JOINs.")
    
    # Check for CTE
    if sql_upper.strip().startswith("WITH"):
        result["is_valid"] = False
        result["issues"].append("CTE (WITH clause) detected. Flatten to single SELECT statement.")
    
    return result


async def process_single_node_async(node_name: str, node: dict, prompt: str, target: str = None):
    """
    Processes an individual node to generate valid SQL using a corrected, unified
    retry loop while preserving the original, full-text prompts.
    """
    if "Node SQL" in node and node["Node SQL"]:
        logger.info(f"Node {node_name} already has SQL. Skipping.")
        return

    # --- 1. SETUP ---
    xml_content = node["Node XML"]
    node_name = node_name.lower()
    
    # Determine target name for prompts and sqlglot dialect dynamically
    TARGET_MAP = {
        "bigquery": ("BigQuery", "bigquery"),
        "snowflake": ("Snowflake", "snowflake"),
        "databricks": ("Databricks", "databricks"),
        "redshift": ("Redshift", "redshift"),
        "synapse": ("Azure Synapse", "tsql"),
        "hana": ("SAP HANA", "hana"),
        "datasphere": ("SAP Datasphere", "hana"),
    }
    
    target_key = (target or "bigquery").lower().strip()
    target_name, sql_dialect = TARGET_MAP.get(target_key, ("BigQuery", "bigquery"))

    # if node_name != 'per_total_sku':
    #     return  
    final_sql = ""

    # Prepare the initial base prompt with enhanced target-specific instructions
    base_original_prompt = f"""You are a {target_name} SQL expert. Convert this HANA Calculation View XML to a valid {target_name} SELECT statement.

CRITICAL REQUIREMENTS:
1. Extract ALL fields from <element> tags - missing ANY field is a CRITICAL failure
2. Use targetName as alias: sourceName AS targetName (from ElementMapping)
3. Convert ALL formulas in <calculationDefinition> to {target_name} SQL expressions
4. Handle JOINs from <join> elements correctly
5. Apply filters from <filterExpression> if present
6. Output ONLY pure SQL - no markdown, no comments, no explanations
7. End with semicolon
8. NO subqueries or CTEs - flatten everything to single SELECT with JOINs

{prompt}

Below is the XML:
{xml_content}"""

    formula_result = xml_contains_formula(xml_content)
    if formula_result:
        base_original_prompt += f"""

FORMULA CONVERSION REQUIRED:
The XML contains formula fields that MUST be translated to {target_name} SQL expressions.
These require careful conversion - verify each formula is correctly translated.
Formula details: {formula_result}"""
    
    current_prompt = base_original_prompt

    # --- 2. UNIFIED GENERATION & VALIDATION LOOP ---
    for attempt in range(3):
        logger.info(f"Attempt {attempt + 1}/3 for node: {node_name}")
        
        # --- I. GENERATE SQL ---
        if attempt == 0:
            # First attempt uses your special multi-model, multi-prompt strategy
            logger.info(f"attempt: {attempt+1} ---- {node_name} ---- start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            # 1a. First call (Initial Generation)
            sql_text_1 = await api_call_with_retry_async('Gemini', current_prompt, task_type='sql', target=target)
            logger.info(f"attempt: {attempt+1} ---- {node_name} step 1---- Gemini Finished time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 1b. INTERMEDIATE VALIDATION - Check Gemini's output before passing to gemini-3.1-flash-lite-preview
            intermediate_validation = basic_sql_validator(sql_text_1 or "", node, target_name)
            validation_feedback = ""
            if not intermediate_validation["is_valid"]:
                validation_feedback = "\n\n⚠️ VALIDATION ISSUES FOUND IN PREVIOUS SQL:\n"
                for issue in intermediate_validation["issues"]:
                    validation_feedback += f"  - {issue}\n"
                validation_feedback += f"\nExpected columns from XML: {intermediate_validation['expected_columns']}"
                validation_feedback += f"\nActual columns in SQL: {intermediate_validation['actual_columns']}"
                if intermediate_validation["formula_check"]:
                    validation_feedback += f"\nFormulas to convert: {intermediate_validation['formula_check']}"
                validation_feedback += "\n\nFIX THESE ISSUES in your output."
                logger.info(f"{node_name} intermediate validation found issues: {intermediate_validation['issues']}")

            # 1c. Second call (Refinement with gemini-3.1-flash-lite-preview) - Pass validation feedback
            refine_prompt_1 = (
                f"{prompt}\n\n"
                f"XML Content:\n{xml_content}\n\n"
                f"Previous SQL generated:\n{sql_text_1}\n\n"
                f"{validation_feedback}\n\n"
                f"CRITICAL INSTRUCTIONS:\n"
                f"1. Carefully analyze the XML Element mappings to identify source-to-target field mappings\n"
                f"2. For each field, use the targetName as the column alias (e.g., sourceName AS targetName)\n"
                f"3. Maintain exact alias names from the Element mapping - incorrect aliases will cause runtime errors\n"
                f"4. If aliases are already correctly defined, preserve them as-is\n"
                f"5. Optimize the SQL query for {target_name} performance and correctness\n"
                f"6. Validate syntax thoroughly against {target_name}'s official documentation\n"
                f"7. Output ONLY the final SELECT SQL statement without any comments or explanations\n"
                f"8. Ensure the query follows {target_name} best practices and standards\n\n"
                f"9. No fields and formula should be missed from the XML(check field by field to ensure this)\n"
                f"Remember: You are specialized in {target_name} and must produce production-ready SQL.\n"
                f"Return only pure sql without comments and ending with semicolon"
            )
            sql_text_2 = await api_call_with_retry_async('gemini-3.1-flash-lite-preview', refine_prompt_1, task_type='sql', target=target)
            logger.info(f"attempt: {attempt+1} ---- {node_name} step 2---- Gemini 3.1 Flash Lite Finished time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            # sql_text_2 = remove_before_first_select(sql_text_2)
            # sql_text_2 = remove_non_sql_context(sql_text_2)
            # sql_text_2 = remove_unwanted_patterns(sql_text_2)
            # sql_text_2 = "\n".join(remove_sql_comments(sql_text_2.splitlines()))

            # 1c. Third call (Final Polish with Gemini) - YOUR FULL PROMPT
            refine_prompt_2 = f"""
            Please refine the provided SQL query for use with {target_name}. The original prompt and query, along with an XML document containing schema mappings, are provided below.

            The **goal** is to ensure the SQL is syntactically correct, optimized for {target_name}, and accurately uses the column aliases defined in the XML.

            ### **Context**

            * **Prompt:** {prompt}
            * **XML Schema Mapping:** {xml_content}
            * **Original SQL:** {sql_text_2}

            ### **Instructions**

            1.  **Identify Aliases:** Use the `<ElementMapping>` tags in the XML to identify the correct `sourceName` and `targetName`. The `targetName` **must** be used as the alias for its corresponding `sourceName` in the `SELECT` statement (e.g., `sourceName` as `targetName`). If an alias is already correct, leave it as is. This is critical for downstream processes.
            2.  **Optimize:** Refine the SQL for optimal performance on {target_name}. But never miss the fields and formula from the XML. Ensure all fields and formulas are included.
            3.  **Validate:** Ensure the query is syntactically correct, checking it letter by letter against {target_name}'s official documentation.
            4.  **Output:** Provide **only** the refined `SELECT` SQL statement. Do not include any comments or additional text.

            Please ensure the final output is a single, clean SQL query that meets all the above requirements.
            Return only pure sql without comments and ending with semicolon
            """
            raw_sql = await api_call_with_retry_async('Gemini', refine_prompt_2, task_type='sql', target=target)
            logger.info(f"attempt: {attempt+1} ---- {node_name} ---- End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            # Subsequent attempts use the generated feedback prompt
            raw_sql = await api_call_with_retry_async('Gemini', current_prompt, task_type='sql', target=target)

        # --- II. CLEAN THE GENERATED SQL ---
        # logger.info(f"1----{raw_sql}")
        cleaned_sql = remove_before_first_select(raw_sql)
        # logger.info(f"2----{cleaned_sql}")
        cleaned_sql = remove_non_sql_context(cleaned_sql)
        # logger.info(f"3----{cleaned_sql}")
        cleaned_sql = remove_unwanted_patterns(cleaned_sql)
        # logger.info(f"4----{cleaned_sql}")
        cleaned_sql = "\n".join(remove_sql_comments(cleaned_sql.splitlines()))
        
        # Apply deterministic BigQuery fixes BEFORE validation
        # Only use these BQ-specific fixes if target is BigQuery
        if target_name == "BigQuery":
            cleaned_sql = fix_all_common_errors(cleaned_sql)
        # logger.info(f"----{cleaned_sql}")

        
        # --- III. VALIDATE THE CLEANED SQL AGAINST ALL RULES ---
        error_messages = []
        if has_subquery_sqlglot(cleaned_sql):
            # YOUR FULL, UNTRIMMED SUBQUERY ERROR PROMPT
            error_messages.append(
                "🚫 Subquery Detected:\n"
                "Your query includes nested SELECT statements (subqueries) or Common Table Expressions (CTEs), "
                "which are not supported by this tool. Please rewrite your SQL as a **single-level flattened query** "
                "using explicit JOINs and WHERE clauses instead.\n\n"
                "🔍 Why is this required?\n"
                "Flattened queries are easier to analyze and transform automatically. Subqueries can introduce complexity "
                "and ambiguity in data lineage, making it difficult for the tool to process.\n\n"
                "🛠️ How to fix:\n"
                "Replace subqueries and CTEs with JOIN-based logic. Here are some practical examples:\n\n"
                "🔸 **Example 1: Simple Subquery (IN clause)**\n"
                "❌ Original:\n"
                "  SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers);\n"
                "✅ Rewrite:\n"
                "  SELECT orders.* FROM orders\n"
                "  JOIN customers ON orders.customer_id = customers.id;\n\n"
                "🔸 **Example 2: UNION inside Subquery**\n"
                "❌ Original:\n"
                "  SELECT * FROM orders WHERE product_id IN (\n"
                "    SELECT product_id FROM table_a\n"
                "    UNION\n"
                "    SELECT product_code FROM table_b\n"
                "  );\n"
                "✅ Rewrite:\n"
                "  SELECT orders.* FROM orders JOIN table_a ON orders.product_id = table_a.product_id\n"
                "  UNION\n"
                "  SELECT orders.* FROM orders JOIN table_b ON orders.product_id = table_b.product_code;\n\n"
                "🔸 **Example 3: EXISTS clause**\n"
                "❌ Original:\n"
                "  SELECT * FROM employees e WHERE EXISTS (SELECT 1 FROM salaries s WHERE s.emp_id = e.id);\n"
                "✅ Rewrite:\n"
                "  SELECT DISTINCT e.* FROM employees e\n"
                "  JOIN salaries s ON e.id = s.emp_id;\n\n"
                "🔸 **Example 4: CTE (WITH clause)**\n"
                "❌ Original:\n"
                "  WITH recent_orders AS (SELECT * FROM orders WHERE order_date > '2023-01-01')\n"
                "  SELECT * FROM recent_orders;\n"
                "✅ Rewrite:\n"
                "  SELECT * FROM orders WHERE order_date > '2023-01-01';\n\n"
                "🔸 **Example 5: Subquery to JOIN**\n\n❌ Original:\n  SELECT order_id, customer_id, order_date\n  FROM orders\n  WHERE customer_id = 123\n  AND order_id IN (\n      SELECT order_id\n      FROM orders\n      WHERE order_date > '2023-01-01'\n  );\n\n✅ Rewrite:\n  SELECT o1.order_id, o1.customer_id, o1.order_date\n  FROM orders o1\n  JOIN orders o2 ON o1.order_id = o2.order_id\n  WHERE o1.customer_id = 123\n  AND o2.order_date > '2023-01-01';\n"
                "💡 Tip: When flattening logic, always validate that your results remain consistent. "
                "Use JOINs thoughtfully to avoid duplications or incorrect filters.\n\n"
                "📌 Please revise your query and try again."
            )
        if not has_at_most_one_join(cleaned_sql):
            # YOUR FULL, UNTRIMMED JOIN ERROR PROMPT
            error_messages.append(
                "Multiple JOINs detected. This tool allows a maximum of one JOIN statement per query. "
                "Please simplify your query to include only one JOIN operation. "
                " This is simple XML statement which has at max one join statement"
                "Read join properly"
                "Make adjustments and generate valid sql without any error"
            )
            
        # Check for UNION column mismatch
        union_error = validate_union_all_columns(cleaned_sql)
        if union_error:
            error_messages.append(union_error)
            
    #september    
        # is_valid_sql check - NON-BLOCKING (treat as hint, not stopper)
        # Sometimes sqlglot gives false positives, so we don't block on this
        is_valid_sql_warning = ""
        valid, msg = is_valid_sql(cleaned_sql, dialect=sql_dialect)
        if not valid:
            # Log as warning, not error - this is a hint for refinement, not a stopper
            is_valid_sql_warning = f"Syntax hint (may be false positive): {msg}"
            logger.warning(f"[{node_name}] is_valid_sql warning (non-blocking): {msg}")
            
        if not is_valid_sql_single(cleaned_sql):
            error_messages.append(f"SQL is not starting with SELECT Keyword")
        
        # Check that all fields from XML ElementMapping appear in the SQL
        missing_fields = validate_field_coverage(cleaned_sql, xml_content)
        if missing_fields and len(missing_fields) <= 10:  # Only report if reasonable number
            error_messages.append(
                f"CRITICAL: Missing fields from XML ElementMapping: {', '.join(missing_fields[:5])}. "
                f"You MUST include ALL targetName fields from the XML. Check the ElementMapping carefully."
            )


        # --- IV. HANDLE THE OUTCOME ---
        # IMPORTANT: is_valid_sql warnings are NON-BLOCKING
        # We only block on structural errors (subquery, multiple joins, missing fields)
        blocking_errors = [e for e in error_messages if 'Syntax hint' not in e]
        
        if not blocking_errors:
            # Success! Even if there's a is_valid_sql warning, we proceed
            if is_valid_sql_warning:
                logger.info(f"[{node_name}] Accepting SQL despite syntax warning (may be sqlglot false positive)")
            final_sql = cleaned_sql
            logger.info(f"{node_name} ---- Exit time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            break  # Success! Exit the loop.
        else:
            logger.info(f"Validation failed for {node_name}: {' | '.join(error_messages)}")
            logger.info(f"INVALID SQL for {node_name} attempt {attempt+1}:\n{cleaned_sql}")
            
            # Try deterministic fixes as SUGGESTIONS BEFORE using LLM retry
            deterministic_suggestions = []
            for error_msg in error_messages:
                if 'CRITICAL: Missing fields' not in error_msg:
                    if target_name == "BigQuery":
                        fixed_sql, was_fixed, fix_desc = fix_bigquery_error(cleaned_sql, error_msg)
                        if was_fixed:
                            logger.info(f"Found auto-fix suggestion for {node_name}: {fix_desc}")
                            deterministic_suggestions.append(f"Suggestion: {fix_desc}. Possible fix: {fixed_sql}")
            
            # Combine suggestions into the error feedback
            if deterministic_suggestions:
                error_messages.extend(deterministic_suggestions)

            # Re-validate initial SQL (no overwriting)
            error_messages_after_fix = error_messages
            
            if attempt < 2:
                # Build feedback with blocking errors + syntax hints
                all_feedback = blocking_errors.copy()
                if is_valid_sql_warning:
                    all_feedback.append(f"(HINT - may be false positive) {is_valid_sql_warning}")
                if deterministic_suggestions:
                    all_feedback.extend(deterministic_suggestions)
                
                # YOUR FULL, UNTRIMMED FEEDBACK PROMPT
                current_prompt = (
                    f"{base_original_prompt}\n\nFor this XML:\n{xml_content}\n\n"
                    f"Previous attempt resulted in:\n{cleaned_sql}\n\n"
                    f"Issues detected:\n{' | '.join(all_feedback)}\n\n"
                    f"Make adjustments and generate valid sql without any error. "
                    f"Note: Some syntax warnings may be false positives from the validator - focus on the CRITICAL errors first. "
                    f"trailing junk after numeric literal at or near - detected. you just remove the erroneous character and proceed further"
                )
            else:
                logger.info(f"Failed to generate valid SQL for {node_name} after 3 attempts.")

        
    final_sql = format_actual_sql(final_sql)
    cleaned_sql = format_actual_sql(cleaned_sql)
    # --- 3. FINALIZE AND STORE RESULT ---
    if final_sql:
        node["Node SQL"] = final_sql.replace('"4', '').replace('"', '').replace(";", " ")
    else:
        final_sql = cleaned_sql
        node["Node SQL"] = final_sql.replace('"4', '').replace('"', '').replace(";", " ")
        
    


def process_single_node_old(node_name, node, prompt):

    """Process individual nodes with up to 15 retries for valid SQL generation."""
    if "Node SQL" in node and node["Node SQL"]:
        return
    
    # if node_name != 'union_2':
    #     logger.info(node["Node name"])
    #     return
    
    xml_content = node["Node XML"]
    node_name = node_name.lower()

    formula_result = xml_contains_formula(xml_content)
    if formula_result:
        prompt += f"""\n\nNote: The XML contains formula fields. Ensure these are correctly translated into SQL expressions.
                    They may require special handling to ensure accurate calculations in the final query.
                    {formula_result}"""

    original_prompt = f"{prompt}\n\nBelow is the XML:\n{xml_content}"
    final_sql = ""
    cleaned_sql = ""
    base_original_prompt = original_prompt
    for attempt in range(5):
        logger.info(f"Attempt {attempt + 1}: Nodename: {node_name}")
        # 
        if attempt == 0:
            # First call: Prompt + XML maddy
            # logger.info(f"Before1 | nodename: {node_name}, attempt: {attempt}, timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            sql_text_1 = api_call_with_retry('Gemini', original_prompt, task_type='sql')
            # logger.info(f"After1 | nodename: {node_name}, attempt: {attempt}, timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            sql_text_1 = remove_before_first_select(sql_text_1)
            sql_text_1 = remove_non_sql_context(sql_text_1)
            sql_text_1 = remove_unwanted_patterns(sql_text_1)
            sql_text_1 = "\n".join(remove_sql_comments(sql_text_1.splitlines()))

            # logger.info("Attempt 1")
            # logger.info(sql_text_1)

            # Second call: Prompt + XML + First SQL
            refine_prompt_1 = (
                f"{prompt}\n\n"
                f"XML Content:\n{xml_content}\n\n"
                f"Previous SQL generated by ChatGPT:\n{sql_text_1}\n\n"
                f"CRITICAL INSTRUCTIONS:\n"
                f"1. Carefully analyze the XML Element mappings to identify source-to-target field mappings\n"
                f"2. For each field, use the targetName as the column alias (e.g., sourceName AS targetName)\n"
                f"3. Maintain exact alias names from the Element mapping - incorrect aliases will cause runtime errors\n"
                f"4. If aliases are already correctly defined, preserve them as-is\n"
                f"5. Optimize the SQL query for BigQuery performance and correctness\n"
                f"6. Validate syntax thoroughly against BigQuery's official documentation\n"
                f"7. Output ONLY the final SELECT SQL statement without any comments or explanations\n"
                f"8. Ensure the query follows BigQuery best practices and standards\n\n"
                f"9. No fields and formula should be missed from the XML(check field by field to ensure this)\n"
                f"Remember: You are specialized in BigQuery and must produce production-ready SQL.\n"
                f"Return only pure sql without comments and ending with semicolon"
            )
            # logger.info(f"Before2 | nodename: {node_name}, attempt: {attempt}, timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            sql_text_2 = api_call_with_retry('Gemini', refine_prompt_1, task_type='sql')
            # logger.info(f"After2 | nodename: {node_name}, attempt: {attempt}, timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            sql_text_2 = remove_before_first_select(sql_text_2)
            sql_text_2 = remove_non_sql_context(sql_text_2)
            sql_text_2 = remove_unwanted_patterns(sql_text_2)
            sql_text_2 = "\n".join(remove_sql_comments(sql_text_2.splitlines()))

            # logger.info("Attempt 2")
            # logger.info(sql_text_2)

            # Third call: Prompt + XML + Second SQL

            refine_prompt_2 = f"""
            Please refine the provided SQL query for use with Google's BigQuery. The original prompt and query, along with an XML document containing schema mappings, are provided below.

            The **goal** is to ensure the SQL is syntactically correct, optimized for BigQuery, and accurately uses the column aliases defined in the XML.

            ### **Context**

            * **Prompt:** {prompt}
            * **XML Schema Mapping:** {xml_content}
            * **Original SQL:** {sql_text_2}

            ### **Instructions**

            1.  **Identify Aliases:** Use the `<ElementMapping>` tags in the XML to identify the correct `sourceName` and `targetName`. The `targetName` **must** be used as the alias for its corresponding `sourceName` in the `SELECT` statement (e.g., `sourceName` as `targetName`). If an alias is already correct, leave it as is. This is critical for downstream processes.
            2.  **Optimize:** Refine the SQL for optimal performance on BigQuery. But never miss the fields and formula from the XML. Ensure all fields and formulas are included.
            3.  **Validate:** Ensure the query is syntactically correct, checking it letter by letter against BigQuery's official documentation.
            4.  **Output:** Provide **only** the refined `SELECT` SQL statement. Do not include any comments or additional text.

            Please ensure the final output is a single, clean SQL query that meets all the above requirements.

            Return only pure sql without comments and ending with semicolon
            """

            # Now you can send 'refine_prompt' to the Gemini API
            # response = model.generate_content(refine_prompt)
            # logger.info(f"Before3 | nodename: {node_name}, attempt: {attempt}, timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            sql_text = api_call_with_retry('Gemini', refine_prompt_2, task_type='sql')
            # logger.info(f"After3 | nodename: {node_name}, attempt: {attempt}, timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        else:
            # Normal single API call for later attempts
            sql_text = api_call_with_retry('Gemini', original_prompt, task_type='sql')
        # Clean SQL

        # logger.info(f"SQL text received: {sql_text}...")


        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)

        # Validate SQL
        # syntax_error = validate_node_sql(cleaned_lines)
        subquery_found = (
            has_subquery_sqlglot(cleaned_sql) 
        )
        join_count_valid = (
            has_at_most_one_join(cleaned_sql) 
        )

        # Exit conditions
        if  not subquery_found and join_count_valid:
            final_sql = cleaned_sql
            logger.info(f"Node SQL(Half way exit):{node_name}")
            logger.info(f"Node SQL(Half way exit)-----------------------------------:{final_sql}")

            break

        # Prepare error feedback for next attempt
        error_messages = []
        if subquery_found:
            logger.info(f"node_name(subquery found):{node_name}")
            error_messages.append(
                "🚫 Subquery Detected:\n"
                "Your query includes nested SELECT statements (subqueries) or Common Table Expressions (CTEs), "
                "which are not supported by this tool. Please rewrite your SQL as a **single-level flattened query** "
                "using explicit JOINs and WHERE clauses instead.\n\n"

                "🔍 Why is this required?\n"
                "Flattened queries are easier to analyze and transform automatically. Subqueries can introduce complexity "
                "and ambiguity in data lineage, making it difficult for the tool to process.\n\n"

                "🛠️ How to fix:\n"
                "Replace subqueries and CTEs with JOIN-based logic. Here are some practical examples:\n\n"

                "🔸 **Example 1: Simple Subquery (IN clause)**\n"
                "❌ Original:\n"
                "  SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers);\n"
                "✅ Rewrite:\n"
                "  SELECT orders.* FROM orders\n"
                "  JOIN customers ON orders.customer_id = customers.id;\n\n"

                "🔸 **Example 2: UNION inside Subquery**\n"
                "❌ Original:\n"
                "  SELECT * FROM orders WHERE product_id IN (\n"
                "    SELECT product_id FROM table_a\n"
                "    UNION\n"
                "    SELECT product_code FROM table_b\n"
                "  );\n"
                "✅ Rewrite:\n"
                "  SELECT orders.* FROM orders JOIN table_a ON orders.product_id = table_a.product_id\n"
                "  UNION\n"
                "  SELECT orders.* FROM orders JOIN table_b ON orders.product_id = table_b.product_code;\n\n"

                "🔸 **Example 3: EXISTS clause**\n"
                "❌ Original:\n"
                "  SELECT * FROM employees e WHERE EXISTS (SELECT 1 FROM salaries s WHERE s.emp_id = e.id);\n"
                "✅ Rewrite:\n"
                "  SELECT DISTINCT e.* FROM employees e\n"
                "  JOIN salaries s ON e.id = s.emp_id;\n\n"

                "🔸 **Example 4: CTE (WITH clause)**\n"
                "❌ Original:\n"
                "  WITH recent_orders AS (SELECT * FROM orders WHERE order_date > '2023-01-01')\n"
                "  SELECT * FROM recent_orders;\n"
                "✅ Rewrite:\n"
                "  SELECT * FROM orders WHERE order_date > '2023-01-01';\n\n"

                "🔸 **Example 5: Subquery to JOIN**\n\n❌ Original:\n  SELECT order_id, customer_id, order_date\n  FROM orders\n  WHERE customer_id = 123\n  AND order_id IN (\n      SELECT order_id\n      FROM orders\n      WHERE order_date > '2023-01-01'\n  );\n\n✅ Rewrite:\n  SELECT o1.order_id, o1.customer_id, o1.order_date\n  FROM orders o1\n  JOIN orders o2 ON o1.order_id = o2.order_id\n  WHERE o1.customer_id = 123\n  AND o2.order_date > '2023-01-01';\n"

                "💡 Tip: When flattening logic, always validate that your results remain consistent. "
                "Use JOINs thoughtfully to avoid duplications or incorrect filters.\n\n"

                "📌 Please revise your query and try again."
            )

        if not join_count_valid:
            error_messages.append(
                "Multiple JOINs detected. This tool allows a maximum of one JOIN statement per query. "
                "Please simplify your query to include only one JOIN operation. "
                " This is simple XML statement which has at max one join statement"
                "Read join properly"
                "Make adjustments and generate valid sql without any error"
            )
        generated_sql = cleaned_sql
        valid, msg = is_valid_sql(generated_sql)
        if not valid:
            error_messages.append(
                f"\nThe previous attempt failed to generate valid SQL. "
                f"Please retry with the same prompt ensuring the SQL is valid. "
                f"Previous attempt:  (Error: {msg})"
            )
            logger.info(f"INVALID SQL for {node_name} attempt {attempt+1}:\n{generated_sql}")


        if attempt < 10:
            original_prompt = (
                f"{base_original_prompt}\n\nFor this XML:\n{xml_content}\n\n"
                f"Previous attempt resulted in:\n{cleaned_sql}\n\n"
                f"Errors detected:\n{' | '.join(error_messages)}\n\n"
                f"Make adjustments and generate valid sql without any error"
                f"trailing junk after numeric literal at or near - detected. you just remove the erroneous character and proceed further"
                f"Make adjustments and generate valid sql without any error"

            )



    error_messages = []
    # Loop to validate the SQL and retry if necessary
    for i in range(8):
        cleaned_lines = cleaned_sql.splitlines()
        error_messages = validate_node_sql(cleaned_lines)


        # ✅ Break the loop if no errors found
        if not error_messages:
            logger.info(f"Processed node second loop (Node SQL): {node_name}")
            break
        logger.info(f"Attempt {i + 1}: Errors detected: {error_messages}")
        error_prompt = f"{cleaned_sql}\n\nErrors detected:\n" + \
                    " | ".join(
                        f"{e['type']} at line {e['line']}, col {e['col']}: {e['message']}"
                        for e in error_messages
                    ) + "\n\n"
        error_prompt += "Please fix the errors and generate valid SQL without any errors.If Any reserved keyword is used in the sql then rename it and proceed further.Make sure all the fields have alais names ( FullTable_name.fiels as Field_Alias_name). No Explanation is needed. Just give the sql without any explanation. "
        if i > 5:
            error_prompt = original_prompt + "\n\n" + error_prompt

        # logger.info(error_prompt)

        sql_text = api_call_with_retry('Gemini', error_prompt,'sql') 
        logger.info(f"Retrying node: {node_name}, attempt: {i + 1}, SQL: {sql_text}")

        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)



    final_sql = final_sql.replace('"4', '').replace('"', '')
    cleaned_sql = cleaned_sql.replace('"4', '').replace('"', '')
    node["Node SQL"] = final_sql or cleaned_sql
    logger.info(f"Node SQL(final exit):{node_name}")
    # logger.info(final_sql)
    node["Node SQL"] = node["Node SQL"].replace(";", " ")




def process_single_node_rank(node_name: str, node: dict, prompt: str):
    """
    Processes 'rank' nodes by applying rank logic to a pre-existing SQL query,
    using a corrected, unified retry loop and the original prompts.
    """
    node_type = node.get("Node type", "").lower()
    if node_type != 'rank':
        return

    # --- 1. SETUP ---
    previously_generated_sql = node.get("Node SQL")
    if not previously_generated_sql:
        logger.info(f"Cannot process rank node '{node_name}' without pre-existing SQL. Skipping.")
        return

    xml_content = node["Node XML"]
    final_sql = ""

    # This is your full, original prompt for rank nodes.
    base_original_prompt = f"""You are given Calculation View XML fragments with <viewNode xsi:type="View:Rank"> definitions. 
Convert each Rank node into an equivalent Bigquery SQL snippet with these rules:

1. Use the input view/table from <input><viewNode ...> as the FROM source.
2. For each <partitionElement>, add it as PARTITION BY in the window function.
3. For each <order byElement>, add it as ORDER BY in the window function, respecting ASC/DESC.
4. The <rankElement> becomes a new output column defined by the window function.
5. If a <rankThreshold><constantValue>N</constantValue> is provided, wrap the query in a subquery 
   and add a WHERE clause filtering only rows with rank_column = N.
6. Use RANK() unless ROW_NUMBER() is explicitly needed. Keep tied rows when using RANK().

Example transformation:
---------------------------------
XML:
<viewNode xsi:type="View:Rank" name="Worst_vendor">
  <element name="Worst_vendor"/>
  <element name="Vendor_ID"/>
  <element name="On_time_delivery_per"/>
  <element name="Rank_Column"/>
  <input><viewNode>Aggr_vendor_PO</viewNode></input>
  <windowFunction>
    <partitionElement>#//Worst_vendor/Worst_vendor</partitionElement>
    <order byElement="#//Worst_vendor/On_time_delivery_per" direction="DESC"/>
    <rankThreshold><constantValue>1</constantValue></rankThreshold>
    <rankElement>#//Worst_vendor/Rank_Column</rankElement>
  </windowFunction>
</viewNode>

SQL:
SELECT *
FROM (
    SELECT
        Aggr_vendor_PO.Worst_vendor as Worst_vendor,
        Aggr_vendor_PO.Vendor_ID as Vendor_ID,
        Aggr_vendor_PO.On_time_delivery_per as On_time_delivery_per,
        RANK() OVER (
            PARTITION BY Aggr_vendor_PO.Worst_vendor
            ORDER BY Aggr_vendor_PO.On_time_delivery_per DESC
        ) AS Rank_Column
    FROM Aggr_vendor_PO 
) ranked
WHERE Rank_Column = 1;
---------------------------------

I already wrote SQL for this Rank node without Subquery and Rank_Column filter. 
My Bigquery SQL is:{previously_generated_sql}
That SQL is already BigQuery compatible, error-free, and includes all required columns.  
Your output must be based on that SQL. All fields must be prefixed with the full source table name and all fields must be aliased with same name as in XML. 
You don't worry about the correctness of that SQL. I already validated that SQL. Just add the Rank logic and Rank_Column filter as per above rules.
Always use Subquery for this type of Rank Nodes. Windows function must not be outside of SELECT statement. SubQuery must be created to apply outside WHERE condition filters if any.
The RANK function cannot be used directly in the WHERE clause. Instead, wrap your query in a subquery, apply RANK there, and then filter on the result.
    

Below is the XML:
{xml_content}

Return only pure sql without comments and ending with semicolon
"""

    current_prompt = base_original_prompt

    # --- 2. UNIFIED GENERATION & VALIDATION LOOP ---
    for attempt in range(5):
        logger.info(f"Attempt {attempt + 1}/10 for rank node: {node_name}")

        # --- I. GENERATE SQL (Single, direct call is sufficient for this task) ---
        raw_sql = api_call_with_retry('Gemini', current_prompt, task_type='sql')

        # --- II. CLEAN THE GENERATED SQL ---
        cleaned_sql = remove_before_first_select(raw_sql)
        cleaned_sql = remove_non_sql_context(cleaned_sql)
        cleaned_sql = remove_unwanted_patterns(cleaned_sql)
        cleaned_sql = "\n".join(remove_sql_comments(cleaned_sql.splitlines()))

        # --- III. VALIDATE THE CLEANED SQL ---
        error_messages = []
        valid, msg = is_valid_sql(cleaned_sql)
        if not valid:
            error_messages.append(
                f"\nThe previous attempt failed to generate valid SQL. "
                f"Please retry with the same prompt ensuring the SQL is valid. "
                f"Previous attempt:  (Error: {msg})"
            )
        if not is_valid_sql_single:
            error_messages.append(f"SQL is not starting with SELECT Keyword")

        # --- IV. HANDLE THE OUTCOME ---
        if not error_messages:
            logger.info(f"Successfully generated valid SQL for rank node: {node_name} on attempt {attempt + 1}")
            final_sql = cleaned_sql
            logger.info(f"Final SQL processing for rank node {node_name} is complete(Half way).")
            break  # Success! Exit the loop.
        else:
            logger.warning(f"Validation failed for {node_name}: {' | '.join(error_messages)}")
            if attempt < 9:
                # Prepare the feedback prompt for the next attempt, using your original logic
                current_prompt = (
                    f"{base_original_prompt}\n\n"
                    f"Previous attempt resulted in:\n{cleaned_sql}\n\n"
                    f"Errors detected:\n{' | '.join(error_messages)}\n\n"
                    f"Make adjustments and generate valid sql without any error"
                )
            else:
                logger.info(f"Failed to generate valid SQL for {node_name} after 10 attempts.")

    final_sql = format_actual_sql(final_sql)
    cleaned_sql = format_actual_sql(cleaned_sql)
    # --- 3. FINALIZE AND STORE RESULT ---
    if final_sql:
        node["Node SQL"] = final_sql.replace('"4', '').replace('"', '').replace(";", " ")
    else:
        final_sql = cleaned_sql
        node["Node SQL"] = final_sql.replace('"4', '').replace('"', '').replace(";", " ")
        
    



async def process_single_node_rank_async(node_name: str, node: dict, prompt: str):
    """
    Processes 'rank' nodes by applying rank logic to a pre-existing SQL query,
    using a corrected, unified retry loop and the original prompts.
    """
    node_type = node.get("Node type", "").lower()
    if node_type != 'rank':
        return

    # --- 1. SETUP ---
    previously_generated_sql = node.get("Node SQL")
    if not previously_generated_sql:
        logger.info(f"Cannot process rank node '{node_name}' without pre-existing SQL. Skipping.")
        return

    xml_content = node["Node XML"]
    final_sql = ""

    # This is your full, original prompt for rank nodes.
    base_original_prompt = f"""You are given Calculation View XML fragments with <viewNode xsi:type="View:Rank"> definitions. 
Convert each Rank node into an equivalent Bigquery SQL snippet with these rules:

1. Use the input view/table from <input><viewNode ...> as the FROM source.
2. For each <partitionElement>, add it as PARTITION BY in the window function.
3. For each <order byElement>, add it as ORDER BY in the window function, respecting ASC/DESC.
4. The <rankElement> becomes a new output column defined by the window function.
5. If a <rankThreshold><constantValue>N</constantValue> is provided, wrap the query in a subquery 
   and add a WHERE clause filtering only rows with rank_column = N.
6. Use RANK() unless ROW_NUMBER() is explicitly needed. Keep tied rows when using RANK().

Example transformation:
---------------------------------
XML:
<viewNode xsi:type="View:Rank" name="Worst_vendor">
  <element name="Worst_vendor"/>
  <element name="Vendor_ID"/>
  <element name="On_time_delivery_per"/>
  <element name="Rank_Column"/>
  <input><viewNode>Aggr_vendor_PO</viewNode></input>
  <windowFunction>
    <partitionElement>#//Worst_vendor/Worst_vendor</partitionElement>
    <order byElement="#//Worst_vendor/On_time_delivery_per" direction="DESC"/>
    <rankThreshold><constantValue>1</constantValue></rankThreshold>
    <rankElement>#//Worst_vendor/Rank_Column</rankElement>
  </windowFunction>
</viewNode>

SQL:
SELECT *
FROM (
    SELECT
        Aggr_vendor_PO.Worst_vendor as Worst_vendor,
        Aggr_vendor_PO.Vendor_ID as Vendor_ID,
        Aggr_vendor_PO.On_time_delivery_per as On_time_delivery_per,
        RANK() OVER (
            PARTITION BY Aggr_vendor_PO.Worst_vendor
            ORDER BY Aggr_vendor_PO.On_time_delivery_per DESC
        ) AS Rank_Column
    FROM Aggr_vendor_PO 
) ranked
WHERE Rank_Column = 1;
---------------------------------

I already wrote SQL for this Rank node without Subquery and Rank_Column filter. 
My Bigquery SQL is:{previously_generated_sql}
That SQL is already BigQuery compatible, error-free, and includes all required columns.  
Your output must be based on that SQL. All fields must be prefixed with the full source table name and all fields must be aliased with same name as in XML. 
You don't worry about the correctness of that SQL. I already validated that SQL. Just add the Rank logic and Rank_Column filter as per above rules.
Always use Subquery for this type of Rank Nodes. Windows function must not be outside of SELECT statement. SubQuery must be created to apply outside WHERE condition filters if any.
The RANK function cannot be used directly in the WHERE clause. Instead, wrap your query in a subquery, apply RANK there, and then filter on the result.
    

Below is the XML:
{xml_content}

Return only pure sql without comments and ending with semicolon
"""

    current_prompt = base_original_prompt

    # --- 2. UNIFIED GENERATION & VALIDATION LOOP ---
    for attempt in range(5):
        logger.info(f"Attempt {attempt + 1}/10 for rank node: {node_name}")

        # --- I. GENERATE SQL (Single, direct call is sufficient for this task) ---
        raw_sql = await api_call_with_retry_async('Gemini', current_prompt, task_type='sql')

        # --- II. CLEAN THE GENERATED SQL ---
        cleaned_sql = remove_before_first_select(raw_sql)
        cleaned_sql = remove_non_sql_context(cleaned_sql)
        cleaned_sql = remove_unwanted_patterns(cleaned_sql)
        cleaned_sql = "\n".join(remove_sql_comments(cleaned_sql.splitlines()))

        # --- III. VALIDATE THE CLEANED SQL ---
        error_messages = []
        valid, msg = is_valid_sql(cleaned_sql)
        if not valid:
            error_messages.append(
                f"\nThe previous attempt failed to generate valid SQL. "
                f"Please retry with the same prompt ensuring the SQL is valid. "
                f"Previous attempt:  (Error: {msg})"
            )
        if not is_valid_sql_single:
            error_messages.append(f"SQL is not starting with SELECT Keyword")

        # --- IV. HANDLE THE OUTCOME ---
        if not error_messages:
            logger.info(f"Successfully generated valid SQL for rank node: {node_name} on attempt {attempt + 1}")
            final_sql = cleaned_sql
            logger.info(f"Final SQL processing for rank node {node_name} is complete(Half way).")
            break  # Success! Exit the loop.
        else:
            logger.warning(f"Validation failed for {node_name}: {' | '.join(error_messages)}")
            if attempt < 9:
                # Prepare the feedback prompt for the next attempt, using your original logic
                current_prompt = (
                    f"{base_original_prompt}\n\n"
                    f"Previous attempt resulted in:\n{cleaned_sql}\n\n"
                    f"Errors detected:\n{' | '.join(error_messages)}\n\n"
                    f"Make adjustments and generate valid sql without any error"
                )
            else:
                logger.info(f"Failed to generate valid SQL for {node_name} after 10 attempts.")

    final_sql = format_actual_sql(final_sql)
    cleaned_sql = format_actual_sql(cleaned_sql)
    # --- 3. FINALIZE AND STORE RESULT ---
    if final_sql:
        node["Node SQL"] = final_sql.replace('"4', '').replace('"', '').replace(";", " ")
    else:
        final_sql = cleaned_sql
        node["Node SQL"] = final_sql.replace('"4', '').replace('"', '').replace(";", " ")
        
    
       
    

def process_single_node_rank_old(node_name, node, prompt):

    """Process individual nodes with up to 15 retries for valid SQL generation."""

    node_type = node["Node type"].lower()
    if node_type != 'rank':
        return
    
    previously_generated_sql = node.get("Node SQL")
    prompt = ""
    prompt = f"""You are given Calculation View XML fragments with <viewNode xsi:type="View:Rank"> definitions. 
Convert each Rank node into an equivalent Bigquery SQL snippet with these rules:

1. Use the input view/table from <input><viewNode ...> as the FROM source.
2. For each <partitionElement>, add it as PARTITION BY in the window function.
3. For each <order byElement>, add it as ORDER BY in the window function, respecting ASC/DESC.
4. The <rankElement> becomes a new output column defined by the window function.
5. If a <rankThreshold><constantValue>N</constantValue> is provided, wrap the query in a subquery 
   and add a WHERE clause filtering only rows with rank_column = N.
6. Use RANK() unless ROW_NUMBER() is explicitly needed. Keep tied rows when using RANK().

Example transformation:
---------------------------------
XML:
<viewNode xsi:type="View:Rank" name="Worst_vendor">
  <element name="Worst_vendor"/>
  <element name="Vendor_ID"/>
  <element name="On_time_delivery_per"/>
  <element name="Rank_Column"/>
  <input><viewNode>Aggr_vendor_PO</viewNode></input>
  <windowFunction>
    <partitionElement>#//Worst_vendor/Worst_vendor</partitionElement>
    <order byElement="#//Worst_vendor/On_time_delivery_per" direction="DESC"/>
    <rankThreshold><constantValue>1</constantValue></rankThreshold>
    <rankElement>#//Worst_vendor/Rank_Column</rankElement>
  </windowFunction>
</viewNode>

SQL:
SELECT *
FROM (
    SELECT
        Aggr_vendor_PO.Worst_vendor as Worst_vendor,
        Aggr_vendor_PO.Vendor_ID as Vendor_ID,
        Aggr_vendor_PO.On_time_delivery_per as On_time_delivery_per,
        RANK() OVER (
            PARTITION BY Aggr_vendor_PO.Worst_vendor
            ORDER BY Aggr_vendor_PO.On_time_delivery_per DESC
        ) AS Rank_Column
    FROM Aggr_vendor_PO 
) ranked
WHERE Rank_Column = 1;
---------------------------------

I already wrote SQL for this Rank node without Subquery and Rank_Column filter. 
My Bigquery SQL is:{previously_generated_sql}
That SQL is already BigQuery compatible, error-free, and includes all required columns.  
Your output must be based on that SQL. All fields must be prefixed with the full source table name and all fields must be aliased with same name as in XML. 
You don't worry about the correctness of that SQL. I already validated that SQL. Just add the Rank logic and Rank_Column filter as per above rules.
Always use Subquery for this type of Rank Nodes. Windows function must not be outside of SELECT statement. SubQuery must be created to apply outside WHERE condition filters if any.
    """
    
    xml_content = node["Node XML"]
    node_name = node_name.lower()
    original_prompt = f"{prompt}\n\nBelow is the XML:\n{xml_content}"
    final_sql = ""
    cleaned_sql = ""
    base_original_prompt = original_prompt
    for attempt in range(5):
        # 
        if attempt == 0:
            # First call: Prompt + XML
            sql_text_1 = api_call_with_retry('Gemini', original_prompt, task_type='sql')
            sql_text_1 = remove_before_first_select(sql_text_1)
            sql_text_1 = remove_non_sql_context(sql_text_1)
            sql_text_1 = remove_unwanted_patterns(sql_text_1)
            sql_text_1 = "\n".join(remove_sql_comments(sql_text_1.splitlines()))

            # logger.info("Attempt 1")
            # logger.info(sql_text_1)

            # Second call: Prompt + XML + First SQL
            refine_prompt_1 = (
                f"{prompt}\n\n"
                f"XML Content:\n{xml_content}\n\n"
                f"Previous SQL generated by ChatGPT:\n{sql_text_1}\n\n"
                f"CRITICAL INSTRUCTIONS:\n"
                f"1. Carefully analyze the XML Element mappings to identify source-to-target field mappings\n"
                f"2. For each field, use the targetName as the column alias (e.g., sourceName AS targetName)\n"
                f"3. Maintain exact alias names from the Element mapping - incorrect aliases will cause runtime errors\n"
                f"4. If aliases are already correctly defined, preserve them as-is\n"
                f"5. Optimize the SQL query for BigQuery performance and correctness\n"
                f"6. Validate syntax thoroughly against BigQuery's official documentation\n"
                f"7. Output ONLY the final SELECT SQL statement without any comments or explanations\n"
                f"8. Ensure the query follows BigQuery best practices and standards\n\n"
                f"9. No fields and formula should be missed from the XML(check field by field to ensure this)\n"
                f"Remember: You are specialized in BigQuery and must produce production-ready SQL.\n"
                f"Return only pure sql without comments and ending with semicolon"
            )
            sql_text_2 = api_call_with_retry('Gemini', refine_prompt_1, task_type='sql')
            sql_text_2 = remove_before_first_select(sql_text_2)
            sql_text_2 = remove_non_sql_context(sql_text_2)
            sql_text_2 = remove_unwanted_patterns(sql_text_2)
            sql_text_2 = "\n".join(remove_sql_comments(sql_text_2.splitlines()))

            # logger.info("Attempt 2")
            # logger.info(sql_text_2)

            # Third call: Prompt + XML + Second SQL

            refine_prompt_2 = f"""
            Please refine the provided SQL query for use with Google's BigQuery. The original prompt and query, along with an XML document containing schema mappings, are provided below.

            The **goal** is to ensure the SQL is syntactically correct, optimized for BigQuery, and accurately uses the column aliases defined in the XML.

            ### **Context**

            * **Prompt:** {prompt}
            * **XML Schema Mapping:** {xml_content}
            * **Original SQL:** {sql_text_2}

            ### **Instructions**

            1.  **Identify Aliases:** Use the `<ElementMapping>` tags in the XML to identify the correct `sourceName` and `targetName`. The `targetName` **must** be used as the alias for its corresponding `sourceName` in the `SELECT` statement (e.g., `sourceName` as `targetName`). If an alias is already correct, leave it as is. This is critical for downstream processes.
            2.  **Optimize:** Refine the SQL for optimal performance on BigQuery. But never miss the fields and formula from the XML. Ensure all fields and formulas are included.
            3.  **Validate:** Ensure the query is syntactically correct, checking it letter by letter against BigQuery's official documentation.
            4.  **Output:** Provide **only** the refined `SELECT` SQL statement. Do not include any comments or additional text.

            Please ensure the final output is a single, clean SQL query that meets all the above requirements.

            Return only pure sql without comments and ending with semicolon"
            """

            # Now you can send 'refine_prompt' to the Gemini API
            # response = model.generate_content(refine_prompt)
            sql_text = api_call_with_retry('Gemini', refine_prompt_2, task_type='sql')
            # logger.info(f"Third API call: for  {node_name}...")  # Log first 100 chars for brevity
            # logger.info("Attempt 3")
            # logger.info(sql_text)
        else:
            # Normal single API call for later attempts
            sql_text = api_call_with_retry('Gemini', original_prompt, task_type='sql')
        # Clean SQL

        # logger.info(f"SQL text received: {sql_text[:100]}...")  # Log first 100 chars for brevity


        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)

        # Validate SQL
        # syntax_error = validate_node_sql(cleaned_lines)
        # subquery_found = (
        #     has_subquery_sqlglot(cleaned_sql) 
        # )
        join_count_valid = (
            has_at_most_one_join(cleaned_sql) 
        )

        # Exit conditions
        if join_count_valid:
            final_sql = cleaned_sql
            break

        # Prepare error feedback for next attempt
        error_messages = []
        

        if not join_count_valid:
            error_messages.append(
                "Multiple JOINs detected. This tool allows a maximum of one JOIN statement per query. "
                "Please simplify your query to include only one JOIN operation. "
                " This is simple XML statement which has at max one join statement"
                "Read join properly"
                "Make adjustments and generate valid sql without any error"
            )
        generated_sql = cleaned_sql
        valid, msg = is_valid_sql(generated_sql)
        if not valid:
            error_messages.append(
                f"\nThe previous attempt failed to generate valid SQL. "
                f"Please retry with the same prompt ensuring the SQL is valid. "
                f"Previous attempt:  (Error: {msg})"
            )
        # logger.info(node_name)
        # logger.info(cleaned_sql)
        # logger.info(error_messages)

        if attempt < 10:
            original_prompt = (
                f"{base_original_prompt}\n\nFor this XML:\n{xml_content}\n\n"
                f"Previous attempt resulted in:\n{cleaned_sql}\n\n"
                f"Errors detected:\n{' | '.join(error_messages)}\n\n"
                f"Make adjustments and generate valid sql without any error"
                f"trailing junk after numeric literal at or near - detected. you just remove the erroneous character and proceed further"
                f"Make adjustments and generate valid sql without any error"

            )



    error_messages = []
    # Loop to validate the SQL and retry if necessary
    for i in range(8):
        

        cleaned_lines = cleaned_sql.splitlines()
        error_messages = validate_node_sql(cleaned_lines)


        # ✅ Break the loop if no errors found
        if not error_messages:
            # logger.info(f"Processed node (Node SQL): {node_name}")
            break
        # logger.info(f"Attempt {i + 1}: Errors detected: {error_messages}")
        error_prompt = f"{cleaned_sql}\n\nErrors detected:\n" + \
                    " | ".join(
                        f"{e['type']} at line {e['line']}, col {e['col']}: {e['message']}"
                        for e in error_messages
                    ) + "\n\n"
        error_prompt += "Please fix the errors and generate valid SQL without any errors.If Any reserved keyword is used in the sql then rename it and proceed further.Make sure all the fields have alais names ( FullTable_name.fiels as Field_Alias_name). No Explanation is needed. Just give the sql without any explanation. "
        if i > 5:
            error_prompt = original_prompt + "\n\n" + error_prompt

        # logger.info(error_prompt)

        sql_text = api_call_with_retry('Gemini', error_prompt,'sql') 
        logger.info(f"Retrying node: {node_name}, attempt: {i + 1}, SQL: {sql_text}")

        sql_text = remove_before_first_select(sql_text)
        sql_text = remove_non_sql_context(sql_text)
        sql_text = remove_unwanted_patterns(sql_text)
        cleaned_lines = remove_sql_comments(sql_text.splitlines())
        cleaned_sql = "\n".join(cleaned_lines)



    final_sql = final_sql.replace('"4', '').replace('"', '')
    cleaned_sql = cleaned_sql.replace('"4', '').replace('"', '')
    node["Node SQL"] = final_sql or cleaned_sql
    # logger.info(f"Node SQL:{node_name}")
    logger.info(node_name)
    # logger.info(final_sql)
    node["Node SQL"] = node["Node SQL"].replace(";", " ")
    # logger.info(f"Processed node (Node SQL): {node_name} with SQL: {node['Node SQL']}")
    # logger.info("_________________________________________________________________________________________________________")






async def process_single_node_validation(node_sql):
    current_sql = node_sql.strip()
    validated = False
    validation_prompt = f"""
    **BigQuery SQL Validation Task**
    - Examine this SQL query for ANY errors (syntax, semantic, reference, or logic errors):
    - Error in Bigquery keywords, Error in Bigquery formulas, Invalid Join/Union/Rank functions.
    - Ignore duplicate column names, as they are not an issue in BigQuery.
    - Ensure my table is 100% correct. Ideally, I should not get any error when executing this in bq console.
    - My SQL will not have projectID/dataset. I'll use only table names. Ignore ProjectID/dataset in the sql.
    ```sql
    {current_sql}
    ```

    **Response Requirements:**
    1. If query is PERFECT, respond ONLY with: "VALID"
    2. If ANY number issues found, respond with: "INVALID: [detail all error descriptions and Let me know what/where/why/how about the error. I'll fix on my own.]"
    3. Never add explanations or additional text
    4. Don't retrun SQL. Let me know what/where/why/how about the error. I'll fix on my own.

    """

    response = await api_call_with_retry_async('Gemini', validation_prompt, task_type='sql')
    if response:
        response = response.strip()

    # Strict response parsing
    if response == "VALID":
        validated = True
        return None

    elif response and response.startswith("INVALID:"):
        error_desc = response[8:].strip()  # Extract error description
        return error_desc
    
    return "Error: No response from validation API"




def second_validation_node_sql(node_dict):
    for node in node_dict:
        # Lowercase "Node name"
        node_name = node_dict[node].get("Node name")
        if node_name is not None:
            node_dict[node]["Node name"] = node_name.lower()

        # Lowercase each source in "Sources"
        sources = node_dict[node].get("Sources")
        if sources is not None and isinstance(sources, list):
            node_dict[node]["Sources"] = [s.lower() for s in sources if isinstance(s, str)]

    node_dict = {
        k.lower(): v for k, v in node_dict.items()
    }

    node_names = list(node_dict.keys())

    # Rate limiting configuration
    CHUNK_SIZE = 20
    chunks = [
        node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
        for chunk in chunks:
            futures = {
                executor.submit(
                    process_single_node_second_validation,
                    name,
                    node_dict[name],
                    node_dict[name].get("Node SQL"),
                ): name
                for name in chunk
                if node_dict[name].get("Node XML")
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    # logger.info(f"Critical failure processing {name}: {e}")
                    pass





async def process_single_node_second_validation_async(node_name, node, node_sql):
    MAX_ATTEMPTS = 3 # Maximum attempts for validation and correction
    current_sql = node_sql.strip()
    validated = False

    for attempt in range(MAX_ATTEMPTS + 1):  # +1 for initial validation
        # Strict validation prompt
        validation_prompt = f"""
        **BigQuery SQL Validation Task**
        - Examine this SQL query for ANY errors (syntax, semantic, reference, or logic errors):
        - Duplicate columns, Error in Bigquery keywords, Error in Bigquery formulas, Invalid Join/Union/Rank functions.
        - Ensure my table is 100% correct. Ideally, I should not get any error when executing this in bq console.
        - My SQL will not have projectID/dataset. I'll use only table names. Ignore ProjectID/dataset in the sql.
        ```sql
        {current_sql}
        ```

        **Response Requirements:**
        1. If query is PERFECT, respond ONLY with: "VALID"
        2. If ANY issues found, respond with: "INVALID: [concise error description]"
        3. Never add explanations or additional text
        4. Don't retrun SQL. Let me know what/where/why/how about the error. I'll fix on my own.
        """

        response = await api_call_with_retry_async('Gemini', validation_prompt, task_type='sql')
        if response:
            response = response.strip()

        # Strict response parsing
        if response == "VALID":
            validated = True
            break

        elif response and response.startswith("INVALID:"):
            error_desc = response[8:].strip()  # Extract error description

            # Correction phase only if not last attempt
            if attempt < MAX_ATTEMPTS:
                correction_prompt = f"""
                **SQL Correction Task**
                - Fix this invalid SQL (Error: {error_desc}):
                ```sql
                {current_sql}
                ```

                **Strict Requirements:**
                1. PRESERVE all field names and aliases
                2. MAINTAIN original query logic
                3. OUTPUT ONLY corrected SQL (no text, no markdown)
                4. Use STRICTLY BigQuery-compatible syntax
                5. Do not modify cases in the sql. Keep as it is.
                """

                sql_text = await api_call_with_retry_async('Gemini', correction_prompt, task_type='sql')
                if sql_text:
                    sql_text = sql_text.strip()
                    sql_text = remove_before_first_select(sql_text)
                    sql_text = remove_non_sql_context(sql_text)
                    sql_text = remove_unwanted_patterns(sql_text)
                    cleaned_lines = remove_sql_comments(sql_text.splitlines())
                    current_sql = "\n".join(cleaned_lines)
        else:
            # Handle unexpected response format or None
            error_desc = f"Unexpected validator response: '{response}'"

    # Final update with result tracking
    node["Node SQL"] = current_sql








# async def process_nodes_sql_gcp_validation_parallel_async(node_dict, max_concurrent=50):
#     """
#     Process nodes for SQL GCP validation in parallel using asyncio with concurrency control.

#     :param node_dict: Dictionary of nodes to process.
#     :param max_concurrent: Maximum number of concurrent tasks to avoid overloading.
#     """
#     semaphore = asyncio.Semaphore(max_concurrent)

#     async def run_node(name):
#         async with semaphore:
#             try:
#                 await process_single_node_gcp_validation_async(
#                     name,
#                     node_dict[name],
#                     node_dict[name].get("Temp table")
#                 )
#             except Exception as e:
#                 # Optionally log or handle errors per node
#                 pass

#     # Filter all nodes that have Node XML
#     node_names = [n for n in node_dict if node_dict[n].get("Node XML")]

#     # Create async tasks
#     tasks = [run_node(name) for name in node_names]

#     # Run tasks concurrently
#     await asyncio.gather(*tasks)



def process_nodes_sql_gcp_validation_parallel(node_dict):
    """Process nodes with rate limiting and parallel execution."""
    node_names = list(node_dict.keys())

    # Rate limiting configuration
    CHUNK_SIZE = 20
    chunks = [
        node_names[i : i + CHUNK_SIZE] for i in range(0, len(node_names), CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_SIZE) as executor:
        for chunk in chunks:
            futures = {
                executor.submit(
                    process_single_node_gcp_validation,
                    name,
                    node_dict[name],
                    node_dict[name].get("Temp table"),
                ): name
                for name in chunk
                if node_dict[name].get("Node XML")
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    # logger.info(f"Critical failure processing {name}: {e}")
                    pass



# async def process_single_node_gcp_validation_async(node_name, node, temp_table):
#     """
#     Validates and fixes SQL for a single node using GCP validation with DeepSeek for error analysis
#     and Gemini for SQL fixing. Ensures non-blank SQL is always returned in SELECT format.
#     """
#     node_sql_val = node.get("Node SQL", "").strip()
#     original_sql = node_sql_val
#     display_name = node.get("Node name", node_name)
#     xml_content = node.get("Node XML", "")
#     direct_conversion_prompt = node.get("Node Prompt", "")
#     final_sql = ""
#     cleaned_sql = ""
    
#     # logger.info(f"[GCP VALIDATION START] Node={display_name}, SQL length={len(node_sql_val)}")
    
#     # If original SQL is empty, return early
#     if not node_sql_val:
#         logger.warning(f"[SKIP] Node={display_name} has empty SQL")
#         node["Node SQL"] = node_sql_val
#         return node

#     # Attempt to validate and fix SQL
#     for attempt in range(5):
#         
#         sql_input = f"{temp_table}\n{node_sql_val}"
#         gcp_validation = await run_bigquery_sql_async(sql_input)

#         if gcp_validation == "SUCCESS":
#             # logger.info(f"Node {display_name} SQL validated successfully on attempt {attempt + 1}.")
#             final_sql = node_sql_val
#             break
#         logger.info(f"Attempt {attempt + 1} validation for {display_name}: {gcp_validation}")

#         try:
#             # DeepSeek prompt for detailed error analysis
#             to_get_detail_prompt = f"""SQL with error:
#             {node_sql_val}

#             BigQuery Error:
#             {gcp_validation}

#             Please provide why/what/where detailed error messages for the SQL errors detected. 
#             I don't need sql, just detailed error messages. 
#             Let me know how to fix the issue.
#             My SQL will not have project_id and dataset names. Ignore them. Tables only available."""

#             error_details = await api_call_with_retry_async('Gemini', to_get_detail_prompt, task_type='sql')
            
#             if error_details is None:
#                 error_details = ""
            
#             # STRONG Gemini prompt that INSISTS on SELECT format
#             prompt_to_use = f"""CRITICAL INSTRUCTION: YOU MUST RETURN ONLY SQL CODE THAT STARTS WITH SELECT. NO EXPLANATIONS, NO COMMENTS, NO MARKDOWN.

#             SQL to fix:
#             {node_sql_val}

#             Errors detected:
#             {gcp_validation}

#             Error Analysis:
#             {error_details}

#             IMPORTANT RULES YOU MUST FOLLOW:
#             1. RETURN ONLY SQL CODE THAT STARTS WITH SELECT - NO OTHER TEXT
#             2. Fix all syntax and semantic errors completely
#             3. Ensure the SQL is valid BigQuery syntax
#             4. Use proper alias names for all fields (TableName.field AS Field_Alias_Name)
#             5. Avoid all reserved keywords as identifiers - rename them if needed
#             6. Ensure the SQL is complete and executable
#             7. Do not include project or dataset names
#             8. No explanations, no comments, no markdown formatting
#             9. The response must begin with SELECT and be pure SQL only
#             10. My table name will not have project_id and dataset names. Ignore them. Tables only available.
#             11. No Subqueries, no CTEa used.

#             IMP: Use existing SQL structure as a base, but ensure it has no errrors.

#             FAILURE TO RETURN PURE SQL STARTING WITH SELECT WILL RESULT IN TASK FAILURE."""

#             if attempt > 5:
#                 # If more than 5 attempts, include the original prompt for context
#                 prompt_to_use = f"{direct_conversion_prompt}\n\n{prompt_to_use}"
#                 prompt_to_use = (
#                     f"{prompt_to_use}\n\nRefer below xml content for reference:\n{xml_content}\n\n"
#                     f"Previous SQL was:\n{original_sql}\n\n"
#                 )


#             # Call Gemini API to regenerate SQL
#             sql_text = await api_call_with_retry_async('Gemini', prompt_to_use, task_type='sql')
            
#             # ENFORCE SELECT format strictly
#             if sql_text:
#                 # Remove any non-SQL content and ensure it starts with SELECT
#                 sql_text = remove_before_first_select(sql_text)
#                 if not sql_text.strip().upper().startswith('SELECT'):
#                     logger.warning(f"API response doesn't start with SELECT: {sql_text[:100]}...")
#                     # Try to extract SELECT statement
#                     select_match = re.search(r'(SELECT\s+.+?)(?=;|$)', sql_text, re.IGNORECASE | re.DOTALL)
#                     if select_match:
#                         sql_text = select_match.group(1)
#                     else:
#                         # Create a basic SELECT as fallback
#                         sql_text = f"SELECT * FROM {display_name}_table"
            
#             # Clean the SQL
#             sql_text = remove_non_sql_context(sql_text)
#             sql_text = remove_unwanted_patterns(sql_text)
#             cleaned_lines = remove_sql_comments(sql_text.splitlines())
#             cleaned_sql = "\n".join(cleaned_lines).strip()

#             # Verify it starts with SELECT after cleaning
#             if cleaned_sql and not cleaned_sql.upper().startswith('SELECT'):
#                 logger.warning(f"Cleaned SQL doesn't start with SELECT: {cleaned_sql[:100]}...")
#                 # Force it to be a SELECT statement
#                 cleaned_sql = f"SELECT * FROM ({cleaned_sql}) AS fixed_query"

#             # Check for complex structures
#             if has_subquery_sqlglot(cleaned_sql):
#                 logger.info(f"Subquery detected in {display_name}, continuing to next attempt")
#                 continue
#             if not has_at_most_one_join(cleaned_sql):
#                 logger.info(f"Multiple JOINs detected in {display_name}, continuing to next attempt")
#                 continue

#             # Use cleaned SQL for next attempt
#             node_sql_val = cleaned_sql

#         except Exception as ex:
#             logger.info(f"Error during API call at attempt {attempt + 1}: {str(ex)}")
#             continue

#     # Final validation fallback for cleaned SQL
#     if not final_sql and cleaned_sql:
#         logger.info(f"Using fallback validation for {display_name}")
#         for i in range(3):  # Reduced fallback attempts
#             
            
#             # ENSURE it starts with SELECT
#             if not cleaned_sql.upper().startswith('SELECT'):
#                 cleaned_sql = f"SELECT * FROM ({cleaned_sql}) AS validated_query"
            
#             error_messages = validate_node_sql(cleaned_sql.splitlines())

#             if not error_messages:
#                 final_sql = cleaned_sql
#                 logger.info(f"Final SQL validated for: {display_name}")
#                 break

#             logger.info(f"Validation Attempt {i + 1}: Errors: {error_messages}")

#     # Final assignment with STRICT SELECT enforcement
#     candidate_sql = final_sql or cleaned_sql or node_sql_val or ""
    
#     # ENFORCE SELECT format in final output
#     if candidate_sql:
#         # Clean and ensure SELECT format
#         candidate_sql = candidate_sql.replace('"4', '').replace('"', '').replace(";", " ").strip()
        
#         if not candidate_sql.upper().startswith('SELECT'):
#             logger.warning(f"Final SQL doesn't start with SELECT, wrapping: {candidate_sql[:100]}...")
#             candidate_sql = f"SELECT * FROM ({candidate_sql}) AS final_query"
#     else:
#         logger.warning(f"All attempts failed for {display_name}, using default SELECT")
#         candidate_sql = f"SELECT * FROM {display_name}_table"

#     # One final validation to ensure it's proper SQL
#     if not candidate_sql.upper().startswith('SELECT'):
#         candidate_sql = "SELECT NULL AS placeholder"

#     node["Node SQL"] = candidate_sql
#     # logger.info(f"[FINAL] Node={display_name}, SQL starts with SELECT: {candidate_sql[:50].replace(chr(10), ' ')}...")
    
#     return node
import asyncio
import re
import logging

logger = logging.getLogger(__name__)

async def process_single_node_gcp_validation_async(node_name, node, temp_table):
    """
    Optimized: Two-step LLM workflow for validating and fixing SQL for a single node.
    Step 1: Deepseek determines the issue.
    Step 2: Gemini fixes the SQL based on the error analysis.
    """
    node_sql_val = node.get("Node SQL", "").strip()
    original_sql = node_sql_val
    display_name = node.get("Node name", node_name)
    xml_content = node.get("Node XML", "")
    direct_conversion_prompt = node.get("Node Prompt", "")

    final_sql = ""
    cleaned_sql = ""
    missing_column = None
    missing_grouped_column = None
    deepseek_output = ""

    if not node_sql_val:
        logger.warning(f"[SKIP] Node={display_name} has empty SQL")
        node["Node SQL"] = node_sql_val
        return node

    max_attempts = 3
    for attempt in range(max_attempts):
        
        sql_input = f"{temp_table}\n{node_sql_val}"
        gcp_validation = await run_bigquery_sql_async(sql_input)

        if gcp_validation == "SUCCESS":
            final_sql = node_sql_val
            logger.info(f"{node_name} SQL successfully validated")
            break

        logger.info(f"Attempt {attempt + 1} validation for {display_name}: {gcp_validation}")

        # Step 1: Deepseek determines the issue
        if not deepseek_output:
            try:
                deepseek_prompt = (
                    f"{direct_conversion_prompt}\n\n"
                    f"SQL with error:\n{node_sql_val}\n\n"
                    f"BigQuery Error:\n{gcp_validation}\n\n"
                    f"XML content for reference:\n{xml_content}\n\n"
                    f"Provide a detailed explanation of the SQL error and how to fix it.\n"
                    f"Do not provide SQL, only explanation and fix strategy.\n"
                    f"Focus on missing columns, syntax, and aggregation issues."
                )

                deepseek_output = await api_call_with_retry_async('Gemini', deepseek_prompt, task_type='sql')
                if deepseek_output is None:
                    deepseek_output = ""

                # Optional: parse for missing columns / aggregation errors
                match = re.search(r"Name (\w+) not found", gcp_validation)
                missing_column = match.group(1) if match else None

                match_grouped = re.search(r"references ([\w\.]+) which is neither grouped nor aggregated", gcp_validation)
                missing_grouped_column = match_grouped.group(1) if match_grouped else None

            except Exception as e:
                logger.info(f"Deepseek API error: {e}")
                deepseek_output = ""

        # Step 2: Gemini fixes the SQL based on Deepseek output
        try:
            gemini_prompt = (
                f"{direct_conversion_prompt}\n\n"
                f"Original SQL:\n{node_sql_val}\n\n"
                f"BigQuery Error:\n{gcp_validation}\n\n"
                f"Error Analysis from Deepseek:\n{deepseek_output}\n\n"
                f"XML content:\n{xml_content}\n\n"
                f"IMPORTANT RULES:\n"
                f"1. Return only SQL starting with SELECT.\n"
                f"2. Fix all syntax and semantic errors completely.\n"
                f"3. Avoid subqueries and CTEs.\n"
                f"4. Use proper alias names for all fields.\n"
                f"5. Avoid reserved keywords.\n"
                f"6. SQL must be valid BigQuery syntax.\n"
                f"7. Do not include project/dataset names.\n"
                f"8. No explanations, comments, or markdown.\n"
                f"9. Use existing SQL structure as base but fix errors.\n"
            )

            sql_text = await api_call_with_retry_async('Gemini', gemini_prompt, task_type='sql')

            if sql_text:
                # Clean SQL
                sql_text = remove_before_first_select(sql_text)
                sql_text = remove_non_sql_context(sql_text)
                sql_text = remove_unwanted_patterns(sql_text)
                sql_text = fix_cast_syntax(sql_text)  # Fix CAST(x TYPE) -> CAST(x AS TYPE)
                cleaned_lines = remove_sql_comments(sql_text.splitlines())
                cleaned_sql = "\n".join(cleaned_lines).strip()

                if not cleaned_sql.upper().startswith("SELECT"):
                    cleaned_sql = f"SELECT * FROM ({cleaned_sql}) AS fixed_query"

                node_sql_val = cleaned_sql

            # Handle missing column / grouped column automatically if still present
            if missing_column:
                node_sql_val = f"SELECT NULL AS {missing_column}, * FROM ({node_sql_val}) AS temp"
                missing_column = None

            if missing_grouped_column:
                node_sql_val = f"SELECT {missing_grouped_column}, * FROM ({node_sql_val}) AS temp"
                missing_grouped_column = None

        except Exception as e:
            logger.info(f"Gemini API error: {e}")
            continue

    # Final validation using your validate_node_sql()
    candidate_sql = final_sql or cleaned_sql or node_sql_val or ""
    candidate_sql = candidate_sql.replace('"4', '').replace('"', '').replace(";", " ").strip()

    error_messages = validate_node_sql(candidate_sql.splitlines())
    if error_messages:
        logger.warning(f"Validation errors remain for {display_name}: {error_messages}")
        candidate_sql = f"SELECT * FROM ({candidate_sql}) AS final_query"

    if not candidate_sql.upper().startswith("SELECT"):
        candidate_sql = "SELECT NULL AS placeholder"

    node["Node SQL"] = candidate_sql
    return node

import asyncio

# async def run_bigquery_sql_async(sql: str):
#     return await asyncio.to_thread(run_bigquery_sql, sql)

async def process_nodes_sql_gcp_validation_parallel_async(node_dict, max_concurrent=20):
    """
    Process multiple nodes concurrently with semaphore control.
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_node(name):
        async with semaphore:
            try:
                await process_single_node_gcp_validation_async(
                    name,
                    node_dict[name],
                    node_dict[name].get("Temp table")
                )
            except Exception as e:
                logger.error(f"Node {name} failed: {e}")
                node_dict[name]["Node SQL"] = node_dict[name].get("Node SQL", f"SELECT * FROM {name}_table")

    node_names = [n for n in node_dict if node_dict[n].get("Node SQL") or node_dict[n].get("Node XML")]
    tasks = [run_node(name) for name in node_names]

    await asyncio.gather(*tasks)
    return node_dict


async def process_single_node_gcp_validation(node_name, node, temp_table):
    """
    Validates and fixes SQL for a single node using GCP validation with DeepSeek for error analysis
    and Gemini for SQL fixing. Ensures non-blank SQL is always returned in SELECT format.
    """
    node_sql_val = node.get("Node SQL", "").strip()
    original_sql = node_sql_val
    display_name = node.get("Node name", node_name)
    xml_content = node.get("Node XML", "")
    direct_conversion_prompt = node.get("Node Prompt", "")
    final_sql = ""
    cleaned_sql = ""
    
    # logger.info(f"[GCP VALIDATION START] Node={display_name}, SQL length={len(node_sql_val)}")
    
    # If original SQL is empty, return early
    if not node_sql_val:
        logger.warning(f"[SKIP] Node={display_name} has empty SQL")
        node["Node SQL"] = node_sql_val
        return node

    # Attempt to validate and fix SQL
    for attempt in range(5):
        
        sql_input = f"{temp_table}\n{node_sql_val}"
        gcp_validation = await run_bigquery_sql_async(sql_input)

        if gcp_validation == "SUCCESS":
            # logger.info(f"Node {display_name} SQL validated successfully on attempt {attempt + 1}.")
            final_sql = node_sql_val
            break
        logger.info(f"Attempt {attempt + 1} validation for {display_name}: {gcp_validation}")

        try:
            # DeepSeek prompt for detailed error analysis
            to_get_detail_prompt = f"""SQL with error:
{node_sql_val}

BigQuery Error:
{gcp_validation}

Please provide why/what/where detailed error messages for the SQL errors detected. 
I don't need sql, just detailed error messages. 
Let me know how to fix the issue.
My SQL will not have project_id and dataset names. Ignore them. Tables only available."""

            error_details = api_call_with_retry('Gemini', to_get_detail_prompt, task_type='sql')
            
            if error_details is None:
                error_details = ""
            
            # STRONG Gemini prompt that INSISTS on SELECT format
            prompt_to_use = f"""CRITICAL INSTRUCTION: YOU MUST RETURN ONLY SQL CODE THAT STARTS WITH SELECT. NO EXPLANATIONS, NO COMMENTS, NO MARKDOWN.

SQL to fix:
{node_sql_val}

Errors detected:
{gcp_validation}

Error Analysis:
{error_details}

IMPORTANT RULES YOU MUST FOLLOW:
1. RETURN ONLY SQL CODE THAT STARTS WITH SELECT - NO OTHER TEXT
2. Fix all syntax and semantic errors completely
3. Ensure the SQL is valid BigQuery syntax
4. Use proper alias names for all fields (TableName.field AS Field_Alias_Name)
5. Avoid all reserved keywords as identifiers - rename them if needed
6. Ensure the SQL is complete and executable
7. Do not include project or dataset names
8. No explanations, no comments, no markdown formatting
9. The response must begin with SELECT and be pure SQL only
10. My table name will not have project_id and dataset names. Ignore them. Tables only available.
11. No Subqueries, no CTEa used.

IMP: Use existing SQL structure as a base, but ensure it has no errrors.

FAILURE TO RETURN PURE SQL STARTING WITH SELECT WILL RESULT IN TASK FAILURE."""

            if attempt > 5:
                # If more than 5 attempts, include the original prompt for context
                prompt_to_use = f"{direct_conversion_prompt}\n\n{prompt_to_use}"
                prompt_to_use = (
                    f"{prompt_to_use}\n\nRefer below xml content for reference:\n{xml_content}\n\n"
                    f"Previous SQL was:\n{original_sql}\n\n"
                )


            # Call Gemini API to regenerate SQL
            sql_text = api_call_with_retry('Gemini', prompt_to_use, task_type='sql')
            
            # ENFORCE SELECT format strictly
            if sql_text:
                # Remove any non-SQL content and ensure it starts with SELECT
                sql_text = remove_before_first_select(sql_text)
                if not sql_text.strip().upper().startswith('SELECT'):
                    logger.warning(f"API response doesn't start with SELECT: {sql_text[:100]}...")
                    # Try to extract SELECT statement
                    select_match = re.search(r'(SELECT\s+.+?)(?=;|$)', sql_text, re.IGNORECASE | re.DOTALL)
                    if select_match:
                        sql_text = select_match.group(1)
                    else:
                        # Create a basic SELECT as fallback
                        sql_text = f"SELECT * FROM {display_name}_table"
            
            # Clean the SQL
            sql_text = remove_non_sql_context(sql_text)
            sql_text = remove_unwanted_patterns(sql_text)
            cleaned_lines = remove_sql_comments(sql_text.splitlines())
            cleaned_sql = "\n".join(cleaned_lines).strip()

            # Verify it starts with SELECT after cleaning
            if cleaned_sql and not cleaned_sql.upper().startswith('SELECT'):
                logger.warning(f"Cleaned SQL doesn't start with SELECT: {cleaned_sql[:100]}...")
                # Force it to be a SELECT statement
                cleaned_sql = f"SELECT * FROM ({cleaned_sql}) AS fixed_query"

            # Check for complex structures
            if has_subquery_sqlglot(cleaned_sql):
                logger.info(f"Subquery detected in {display_name}, continuing to next attempt")
                continue
            if not has_at_most_one_join(cleaned_sql):
                logger.info(f"Multiple JOINs detected in {display_name}, continuing to next attempt")
                continue

            # Use cleaned SQL for next attempt
            node_sql_val = cleaned_sql

        except Exception as ex:
            logger.info(f"Error during API call at attempt {attempt + 1}: {str(ex)}")
            continue

    # Final validation fallback for cleaned SQL
    if not final_sql and cleaned_sql:
        logger.info(f"Using fallback validation for {display_name}")
        for i in range(3):  # Reduced fallback attempts
            
            
            # ENSURE it starts with SELECT
            if not cleaned_sql.upper().startswith('SELECT'):
                cleaned_sql = f"SELECT * FROM ({cleaned_sql}) AS validated_query"
            
            error_messages = validate_node_sql(cleaned_sql.splitlines())

            if not error_messages:
                final_sql = cleaned_sql
                logger.info(f"Final SQL validated for: {display_name}")
                break

            logger.info(f"Validation Attempt {i + 1}: Errors: {error_messages}")

    # Final assignment with STRICT SELECT enforcement
    candidate_sql = final_sql or cleaned_sql or node_sql_val or ""
    
    # ENFORCE SELECT format in final output
    if candidate_sql:
        # Clean and ensure SELECT format
        candidate_sql = candidate_sql.replace('"4', '').replace('"', '').replace(";", " ").strip()
        
        if not candidate_sql.upper().startswith('SELECT'):
            logger.warning(f"Final SQL doesn't start with SELECT, wrapping: {candidate_sql[:100]}...")
            candidate_sql = f"SELECT * FROM ({candidate_sql}) AS final_query"
    else:
        logger.warning(f"All attempts failed for {display_name}, using default SELECT")
        candidate_sql = f"SELECT * FROM {display_name}_table"

    # One final validation to ensure it's proper SQL
    if not candidate_sql.upper().startswith('SELECT'):
        candidate_sql = "SELECT NULL AS placeholder"

    node["Node SQL"] = candidate_sql
    # logger.info(f"[FINAL] Node={display_name}, SQL starts with SELECT: {candidate_sql[:50].replace(chr(10), ' ')}...")
    
    return node



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(BASE_DIR, 'dev-hanacv2sql-bq-whole.json')
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = json_path








def Update_join_details(node_dict):
    for node_name, node_data in node_dict.items():
        xml_content = node_data.get("Node XML")
        if not xml_content:
            continue

        root = ET.fromstring(xml_content)

        # Initialize defaults
        node_data["Jointype"] = None
        node_data["Join Condition"] = []

        # Find the viewNode element
        view_node = root.find(".//viewNode")
        if view_node is not None:
            # Check if it's a JoinNode
            xsi_type = view_node.get("{http://www.w3.org/2001/XMLSchema-instance}type")
            if xsi_type and "JoinNode" in xsi_type.split(":"):
                # Find the join element
                join_elem = view_node.find("join")
                if join_elem is not None:
                    # Extract joinType
                    node_data["Jointype"] = join_elem.get("joinType")

                    # Get left/right input paths and extract table names
                    left_input = join_elem.get("leftInput", "")
                    right_input = join_elem.get("rightInput", "")
                    left_table = (
                        extract_input_name(left_input) or "left_table"
                    )  # Use your function
                    right_table = (
                        extract_input_name(right_input) or "right_table"
                    )  # Use your function

                    # Extract elements and build conditions
                    left_elements = [
                        e.text for e in join_elem.findall("leftElementName") if e.text
                    ]
                    right_elements = [
                        e.text for e in join_elem.findall("rightElementName") if e.text
                    ]

                    if len(left_elements) == len(right_elements):
                        node_data["Join Condition"] = [
                            f"{left_table}.{left} = {right_table}.{right}"
                            for left, right in zip(left_elements, right_elements)
                        ]




def update_aggregate_values(node_dict):
    for node_name, node_data in node_dict.items():
        xml_content = node_data.get("Node XML")
        if not xml_content:
            continue

        root = ET.fromstring(xml_content)
        aggregated_columns = []

        # Check if the node is an Aggregation type
        view_node = root.find(".//viewNode")
        if view_node is not None:
            xsi_type = view_node.get("{http://www.w3.org/2001/XMLSchema-instance}type")
            if xsi_type and "Aggregation" in xsi_type.split(":"):
                # Find all elements and filter out NONE aggregation
                elements = view_node.findall(".//element")
                for elem in elements:
                    agg_behavior = elem.get("aggregationBehavior")
                    elem_name = elem.get("name")
                    if agg_behavior and agg_behavior != "NONE" and elem_name:
                        aggregated_columns.append(f"{agg_behavior}({elem_name})")

                # Update node_data
                node_data["Aggregated columns"] = aggregated_columns








def process_xml(xml_content):
    # Define namespaces
    namespaces = {
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "Column": "http://www.sap.com/ndb/DataModelFilter.ecore",
        "Type": "http://www.sap.com/ndb/DataModelType.ecore",
        "View": "http://www.sap.com/ndb/ViewModelView.ecore",
    }

    # Remove layout elements
    cleaned_lines = [
        line for line in xml_content.split("\n") if "<layout xCoordinate" not in line
    ]

    # Parse XML with namespace handling
    root = ET.fromstring("\n".join(cleaned_lines))

    # Build alias map and clean entities
    alias_map = {}
    for input_elem in root.findall(".//input"):
        # Process entity
        entity = input_elem.find("entity")
        if entity is not None and entity.text:
            cleaned = extract_input_name(entity.text)
            entity.text = cleaned

            # Record alias mapping
            if "alias" in input_elem.attrib:
                alias = input_elem.get("alias")
                alias_map[alias] = cleaned
                del input_elem.attrib["alias"]  # Remove alias attribute

    # Process viewNode elements with type View:Union
    for view_node in root.findall(
        './/viewNode[@{{{}}}type="View:Union"]'.format(namespaces["xsi"])
    ):
        if view_node.text:
            view_node.text = extract_input_name(view_node.text)

    # Process join elements
    for join_elem in root.findall(".//join"):
        for attr in ["leftInput", "rightInput"]:
            val = join_elem.get(attr)
            if val:
                # Split path and replace aliases
                parts = val.split("/")
                cleaned_parts = [alias_map.get(p, p) for p in parts if p]
                # Extract final segment
                join_elem.set(attr, extract_input_name("/".join(cleaned_parts)))

    # Register namespaces for output
    for prefix, uri in namespaces.items():
        ET.register_namespace(prefix, uri)

    # Generate XML with proper declaration
    xml_str = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml_str.decode("utf-8")


def update_node_dict_XML(node_dict):
    """Process and update the 'Node XML' in the node_dict."""
    for node_name, node_data in node_dict.items():
        if "Node XML" in node_data:
            # Process the XML content
            processed_xml = process_xml(node_data["Node XML"])
            processed_xml = replace_formula_placeholders(processed_xml)
            # Update the dictionary with the processed XML
            node_dict[node_name]["Node XML"] = processed_xml
    return node_dict






def update_bic_references(node_dict):
    """Replace all instances of /BIC/ with _BIC_ in XML content"""
    for node_name, node_data in node_dict.items():
        if "Node XML" in node_data:
            xml_content = node_data["Node XML"]

            # 1. Replace /BIC/ in element names (e.g., <element name="FACTS___/BIC/G1C_ACC">)
            xml_content = re.sub(
                r'(<element name=")([^"]*)/BIC/([^"]*")',
                lambda m: f"{m.group(1)}{m.group(2)}_BIC_{m.group(3)}",
                xml_content,
            )

            # 2. Replace /BIC/ in attribute values (e.g., targetName="FACTS___/BIC/G1C_ACC")
            xml_content = re.sub(
                r'(targetName|sourceName)="([^"]*)/BIC/([^"]*)"',
                lambda m: f'{m.group(1)}="{m.group(2)}_BIC_{m.group(3)}"',
                xml_content,
            )

            # 3. Replace /BIC/ in join elements (e.g., <leftElementName>/BIC/G1C_ACC</leftElementName>)
            xml_content = re.sub(
                r"(<leftElementName>|<rightElementName>)([^<]*)/BIC/([^<]*)(</leftElementName>|</rightElementName>)",
                lambda m: f"{m.group(1)}{m.group(2)}_BIC_{m.group(3)}{m.group(4)}",
                xml_content,
            )
            # 4. Replace name="0 with name=" and Name="0 with Name="
            xml_content = re.sub(
                r'(name|Name)="0([^"]*)"',
                lambda m: f'{m.group(1)}="{m.group(2)}"',
                xml_content,
            )

            # Update the dictionary with the modified XML
            node_data["Node XML"] = xml_content

    return node_dict




def build_node_prompt(node_dict):
    # Extract values directly from the dictionary
    node_type = node_dict.get("Node type")
    sources = node_dict.get("Sources", [])
    no_of_sources = node_dict.get("No of sources")
    no_of_fields = node_dict.get("No of Fields")
    fields = node_dict.get("Fields", [])
    no_of_formula = node_dict.get("No of formula", 0)
    formulas = node_dict.get("Formula", [])
    filter_used = node_dict.get("Filter Used")
    join_condition = node_dict.get("Join Condition")
    aggregated_columns = node_dict.get("Aggregated columns", [])





    numeric_types = {
        "TINYINT", "SMALLINT", "INTEGER", "BIGINT",
        "SMALLDECIMAL", "DECIMAL", "REAL", "DOUBLE"
    }

    xml_content = node_dict.get("Node XML")
    if xml_content:
        root = ET.fromstring(xml_content)

        for element in root.findall(".//element"):
            name = element.attrib.get("name")
            formula_elem = element.find(".//calculationDefinition/formula")
            data_type_elem = element.find(".//inlineType")

            if formula_elem is not None:
                primitive_type = (
                    data_type_elem.attrib.get("primitiveType")
                    if data_type_elem is not None else "UNKNOWN"
                )

                if primitive_type in numeric_types:
                    aggregated_columns.append(f"SUM({name})")


    # Common rules template
    prompt = """Generate BigQuery SQL-compliant SQL from SAP HANA Calculation View XML with strict adherence to these rules:

Field Handling
- Never omit/combine fields (calculated or raw)
- Remove all quotes from identifiers/table names


🔸 HANA SQL to BigQuery SQL Conversion Rules (BigQuery SQL Only)

Function and Syntax Conversions:
- rightstr(string, n) → RIGHT(string, n)
- leftstr(string, n) → LEFT(string, n)
- ltrim(string) → LTRIM(string)
- rtrim(string) → RTRIM(string)
- CAST(column AS DECIMAL(p,s)) → CAST(column AS NUMERIC)
- CASE WHEN condition THEN value ELSE default END → ✅ No change needed (BigQuery SQL-compliant)
- add_days(date, n) → DATE_ADD(date, INTERVAL n DAY)
- to_date('string', 'format') → PARSE_DATE('format', 'string') (ensure format is BigQuery SQL-compatible)
- current_date() → CURRENT_DATE()
- current_timestamp() → CURRENT_TIMESTAMP()
- ifnull(expr, value) → COALESCE(expr, value)
- length(string) → LENGTH(string)
- substring(string, start, length) → SUBSTR(string, start, length)
- replace(string, search, replace) → REPLACE(string, search, replace)
- concat(string1, string2) → CONCAT(string1, string2)
- to_char(expression, 'format') → FORMAT_DATE('format', expression) for DATE type; use FORMAT_TIMESTAMP for TIMESTAMP/DATETIME types; for other types, use CAST(expression AS STRING) if direct formatting isn't required.
- to_number(string) → CAST(string AS NUMERIC) or CAST(string AS BIGNUMERIC) or CAST(string AS INT64) (choose based on required precision/scale)
- seconds_between(date1, date2) → TIMESTAMP_DIFF(date2, date1, SECOND)
- locate(substring, string) → STRPOS(string, substring)
- lpad(string, length, char) → LPAD(string, length, char)
- rpad(string, length, char) → RPAD(string, length, char)

Additional Notes:
- CEIL(value) → CEILING(value)
- Table functions → Rewrite as views or subqueries
- Window functions (ROW_NUMBER(), RANK(), etc.) → BigQuery SQL-compliant; verify PARTITION BY / ORDER BY syntax


Structural Requirements
- Single SELECT only (no CTE/subqueries except Unions)
- Full table names always (no aliases)
- Maintain proper field aliases

GROUP BY & Column Requirements (CRITICAL)
- All columns in SELECT must be in GROUP BY OR wrapped in aggregate functions (SUM, AVG, COUNT, MAX, MIN)
- If a column is NOT in GROUP BY and NOT aggregated, use ANY_VALUE(column) instead
- DO NOT leave columns that are neither grouped nor aggregated - this causes BigQuery errors
- Example CORRECT: SELECT department, ANY_VALUE(name), SUM(salary) FROM employees GROUP BY department
- Example WRONG: SELECT department, name, SUM(salary) FROM employees GROUP BY department -- name not grouped!

Nested Aggregate Prohibition (CRITICAL)
- DO NOT use nested aggregates like: SUM(AVG(...)), COUNT(SUM(...)), AVG(COUNT(...))
- BigQuery does NOT support aggregations of aggregations
- Instead of SUM(AVG(...)), either:
  a) Use window functions: AVG(...) OVER (PARTITION BY ...) then SUM(...) in outer query
  b) Pre-calculate in source query and join
  c) Use subquery approach if available (CTE, derived table)

Column Name Validation (CRITICAL)
- Only use column names that exist in the provided schema/fields
- Do NOT invent or assume column aliases (e.g., _bic_g1c_cdesc) unless explicitly in schema
- Verify column names match the datasource before using them

Validation & Failure Conditions
- Terminate if:
  - Field count mismatch
  - Missing calculations
  - HANA-specific functions remain
  - Invalid BigQuery SQL syntax
  - Aggregations of aggregations detected
  - Columns neither grouped nor aggregated
- Verify element count match
- Check function conversions
- Confirm original field order

Output Format
Return only valid SQL code with no explanations.

"""

    # Node-specific logic
    if node_type == "Projection":
        prompt += f"This is a projection node (simple SELECT). Source: {sources[0] if sources else 'N/A'}\n"
        prompt += f"Fields ({no_of_fields}): {', '.join(fields)}\n"
        if no_of_formula > 0:
            prompt += f"Formulas ({no_of_formula}): {', '.join(formulas)}\n"
        if filter_used:
            prompt += f"Filter: {filter_used}\n"
        prompt += "<sourceTable>.<sourceName> AS <targetName>    - I need output in this format. Refer ---<mapping xsi:type=Type:ElementMapping > for the source and target names\n"
        prompt += "WHERE also should be in <sourceTable>.<sourceName> format\n"
        prompt += "Verify the SQL having no error before returning."

    elif node_type == "JoinNode":
        prompt += f"This is a JOIN node. Sources: {', '.join(sources)}\n"
        prompt += f"Join Condition: {join_condition}\n"
        prompt += f"Fields ({no_of_fields}): {', '.join(fields)}\n"
        if no_of_formula > 0:
            prompt += f"Formulas ({no_of_formula}): {', '.join(formulas)}\n"
        if filter_used:
            prompt += f"Filter: {filter_used}\n"
        prompt += "<sourceTable>.<sourceName> AS <targetName>    - I need output in this format. Refer ---<mapping xsi:type=Type:ElementMapping > for the source and target names\n"
        prompt += "WHERE also should be in <sourceTable>.<sourceName> format\n"
        prompt += "Verify the SQL having no error before returning."

    elif node_type == "Aggregation":
        prompt += (
            f"This is an AGGREGATION node. Source: {sources[0] if sources else 'N/A'}\n"
        )
        prompt += f"Fields ({no_of_fields}): {', '.join(fields)}\n"
        prompt += f"Aggregated Columns: {', '.join(aggregated_columns)}\n"
        if no_of_formula > 0:
            prompt += f"Formulas ({no_of_formula}): {', '.join(formulas)}\n"
        if filter_used:
            prompt += f"Filter: {filter_used}\n"
        prompt += "Include proper GROUP BY clause\n"
        prompt += "<sourceTable>.<sourceName> AS <targetName>    - I need output in this format. Refer ---<mapping xsi:type=Type:ElementMapping > for the source and target names\n"
        prompt += "WHERE also should be in <sourceTable>.<sourceName> format\n"
        prompt += "Verify the SQL having no error before returning."

    elif node_type == "Rank":
        prompt += f"This is a RANK node. Source: {sources[0] if sources else 'N/A'}\n"
        prompt += f"Fields ({no_of_fields}): {', '.join(fields)}\n"
        if no_of_formula > 0:
            prompt += f"Ranking Formulas ({no_of_formula}): {', '.join(formulas)}\n"
        if filter_used:
            prompt += f"Filter: {filter_used}\n"
        prompt += "Include appropriate window functions (RANK/DENSE_RANK/ROW_NUMBER)\n"
        prompt += "<sourceTable>.<sourceName> AS <targetName>    - I need output in this format. Refer ---<mapping xsi:type=Type:ElementMapping > for the source and target names\n"
        prompt += "WHERE also should be in <sourceTable>.<sourceName> format\n"
        prompt += "Verify the SQL having no error before returning."

    elif node_type == "Union":
        prompt += (
            f"This is a UNION node. Sources ({no_of_sources}): {', '.join(sources)}.\n"
        )
        prompt += "No of columns should be same while Unioning the sources\n"
        prompt += f"Fields ({no_of_fields}): {', '.join(fields)}\n"
        prompt += "Maintain consistent column aliases across UNION statements\n"
        prompt += "If any constant mapping, hard code that value for the field\n"
        prompt += "<sourceTable>.<sourceName> AS <targetName>    - I need output in this format. Refer ---<mapping xsi:type=Type:ElementMapping > for the source and target names. It is exceptional for Formulas and constant mapping:  <mapping xsi:type=Type:ConstantElementMapping targetName-> maintain constant valur for it (static).\n"
        prompt += f"No of UNION ALL function used will be ({no_of_sources-1}) between the {sources}"
        prompt += "Verify the SQL having no error before returning."
        prompt += "Constant mappings must be assigned to constant values (E.g) 'E50' as Alias_files."
        prompt += "Each SELECT statement must have FROM clause.\n"
    else:
        prompt += "Unsupported node type detected\n"

    # Common footer
    prompt += f"\nGenerate SQL for: {node_type} node\n"
    prompt += f"Source Tables: {', '.join(sources) if sources else 'No sources'}\n"
    prompt += f"Date columns is at 'YYYYMMDD' format ---> Use PARSE, EXTARCT incase any formula using date columns"
    prompt += "Required: Strict BigQuery SQL compliance, exact field order preservation,no table & Fields quotes in identifiers."
    prompt += "<sourceTable>.<sourceName> AS <targetName>    - I need output in this format(All fields in the select statement must be with Alias). Refer ---<mapping xsi:type=Type:ElementMapping > for the source and target names. It is exceptional for Formulas and constant mapping.\n"
    prompt += "Verify the SQL having no syntax error and invalid logic error before returning value.Make adjustments what you need to make work according to the prompt.Use your SQL knowldge to correct them."
    prompt += "Make all SQL have FROM clause."
    prompt += "This must returns fields in the format of ***table.field AS field_alias***, eventhough if it selects from single table."
    prompt += "Revalidate before returning the SQL"
    prompt += "If there any special characters in Field/table name in XML other than underscore(_),then replace them with underscore(_) in the SQL.E.g. ( /,-,.,#,$,%,@,!,^,&,*,(,) etc) all need to be replaced with underscore(_)."
    prompt += "Format the SQL in one line without any new lines or spaces in between the SQL statements."
    prompt += "Ignore all project_id & Dataset in Table names. Keep only table names"
    prompt += "Properly identify the Element mapping, this is crucial for defining alias names for fields. e.g: sourcenName as targetName"
    prompt += "Refer HANA SQL Script official documents and Bigquery official documentation for proper formula/function conversion"
    # prompt += '{\n  "sql": "<your SQL query here>"\n}'

    # Update the dictionary directly
    node_dict["Node Prompt"] = prompt


def build_prompts_for_all_nodes(node_dict):
    for node_key, node_value in node_dict.items():
        build_node_prompt(node_value)





# SQL reserved keywords (comprehensive list across multiple databases)
reserved_keywords = {
    'select', 'from', 'where', 'group by', 'order by', 'having', 'join', 'on', 'inner', 'outer',
    'left', 'right', 'full', 'cross', 'as', 'distinct', 'limit', 'offset', 'insert', 'into',
    'values', 'update', 'set', 'delete', 'create', 'alter', 'drop', 'truncate', 'union', 'all',
    'case', 'when', 'then', 'else', 'end', 'like', 'in', 'between', 'exists', 'null', 'is',
    'not', 'and', 'or', 'xor', 'default', 'check', 'constraint', 'index', 'primary', 'foreign',
    'key', 'references', 'unique', 'view', 'trigger', 'procedure', 'function', 'declare',
    'cursor', 'fetch', 'begin', 'commit', 'rollback', 'savepoint', 'grant', 'revoke', 'show',
    'describe', 'explain', 'database', 'table', 'column', 'schema', 'engine', 'auto_increment',
    'serial', 'returning', 'do', 'ilike', 'rownum', 'sysdate', 'level', 'connect by', 'top',
    'merge', 'output', 'identity', 'nvarchar', 'varchar', 'text', 'int', 'bigint', 'smallint',
    'decimal', 'numeric', 'float', 'double', 'real', 'boolean', 'date', 'datetime', 'timestamp',
    'interval', 'time', 'array', 'json', 'cast', 'convert', 'coalesce', 'nvl', 'nullif', 'lead',
    'lag', 'partition', 'over', 'window', 'with', 'recursive', 'materialized', 'unnest', 'lateral',
    'struct', 'except', 'intersect', 'apply', 'pivot', 'unpivot', 'session_user', 'current_user',
    'localtime', 'localtimestamp', 'at time zone', 'offset fetch', 'regexp', 'string', 'byte',
    'safe_cast', 'safe_convert', 'rank', 'cluster', 'copy', 'file', 'stage', 'stream', 'task',
    'warehouse', 'try_convert', 'try_cast', 'sequence', 'spatial', 'rowversion', 'diststyle', 'distkey',
    'sortkey', 'encode', 'interleaved', 'backup', 'appendonly', 'apply', 'merge', 'cross', 'rows',
    'lateral', 'grouping', 'pivot', 'unpivot', 'filegroup', 'type', 'bytea', 'jsonb', 'timestamptz',
    'money', 'bigserial', 'uuid', 'interval', 'text', 'returning', 'do', 'ilike', 'schema', 'engine','rank', 'source','info',

    # Additional SQL Functions:
    'concat', 'substring', 'length', 'upper', 'lower', 'round', 'floor', 'ceiling', 'abs', 'mod',
    'sign', 'ascii', 'char', 'ascii', 'char_length', 'current_date', 'current_time', 'current_timestamp',
    'now', 'datediff', 'adddate', 'subdate', 'dateadd', 'datediff', 'date_trunc', 'date_format', 'extract',
    'to_char', 'to_date', 'to_number', 'from_unixtime', 'unix_timestamp', 'json_extract', 'json_value',
    'json_object', 'json_array', 'json_agg', 'json_each', 'json_parse', 'jsonb_extract_path',
    'jsonb_each_text', 'jsonb_array_elements', 'jsonb_to_record', 'case_when', 'row_number', 'rank',
    'dense_rank', 'ntile', 'first_value', 'last_value', 'lead', 'lag', 'correlation', 'covar_pop',
    'covar_samp', 'regr_slope', 'regr_intercept', 'regr_count', 'regr_avgx', 'regr_avgy', 'st_distance',
    'st_astext', 'st_geometrytype', 'st_area', 'st_buffer', 'st_length', 'st_intersects', 'st_within',
    'st_union', 'st_intersection', 'st_difference', 'st_makepoint', 'st_point', 'st_geomfromtext',
    'st_setsrid', 'st_transform', 'uuid_generate_v4', 'md5', 'sha256', 'sha512', 'encode', 'decode',
    'uuid_generate_v4', 'hash', 'hash_text', 'gzip', 'uncompress', 'length', 'trim', 'reverse', 'lpad',
    'rpad', 'translate', 'split_part', 'array_length', 'array_agg', 'array_append', 'array_prepend',
    'array_to_string', 'json_object_agg', 'jsonb_object_agg', 'xmlagg', 'string_agg', 'listagg', 
    'corr', 'stddev_pop', 'stddev_samp', 'variance', 'variance_pop', 'variance_samp', 'sqrt',
    'select', 'from', 'where', 'group', 'by', 'as', 'join', 'on', 'and', 'or',
    'insert', 'update', 'delete', 'create', 'drop', 'alter', 'table', 'database',
    'case', 'when', 'then', 'else', 'end', 'not', 'null', 'into', 'having',
    'order', 'limit', 'offset', 'distinct', 'union', 'all', 'exists', 'between',
    'in', 'like', 'is', 'cast', 'if', 'inner', 'outer', 'left', 'right',
    'primary', 'key', 'foreign', 'values', 'int', 'varchar', 'numeric', 'boolean',
    'current_date', 'current_time', 'current_timestamp', 'true', 'false', 'info', 'source', 'target',
    'schema', 'view', 'procedure', 'function', 'trigger', 'cursor', 'fetch',

    'account','all','alter','and','any','as','between','by','case','cast','check','column','connect','connection','constraint',
    'create','cross','current','current_date','current_time','current_timestamp','current_user','database','delete','distinct',
    'drop','else','exists','false','following','for','from','full','grant','group','gscluster','having','ilike','in','increment',
    'inner','insert','intersect','into','is','issue','join','lateral','left','like','localtime','localtimestamp','minus','natural',
    'not','null','of','on','or','order','organization','qualify','regexp','revoke','right','rlike','row','rows','sample','schema',
    'select','set','some','start','table','tablesample','then','to','trigger','true','try_cast','union','unique','update','using',
    'values','view','when','whenever','where','window','with',
    'add','all','and','as','by','create','delete','desc','drop','exists','group','insert','into',
    'join','like','not','null','or','select','set','table','union','update','values','where',


    # Data Types:
    'int', 'bigint', 'smallint', 'decimal', 'numeric', 'float', 'double', 'real', 'boolean', 'date',
    'datetime', 'timestamp', 'time', 'varchar', 'nvarchar', 'text', 'char', 'uuid', 'money', 'bit',
    'binary', 'varbinary', 'clob', 'blob', 'json', 'jsonb', 'xml', 'varchar(max)', 'nchar', 'nvarchar(max)',
    'rowversion', 'timestamp', 'geography', 'geometry', 'point', 'linestring', 'polygon', 'interval', 
    'timestamp with time zone', 'timestamp without time zone', 'time with time zone', 'time without time zone',
    'bytea', 'hstore', 'set', 'enum', 'tinyint', 'mediumint', 'year', 'bit', 'serial', 'bigserial',
    'range', 'float8', 'float4', 'real8', 'real4', 'json_object', 'json_array', 'json_agg', 'int8', 'int4'
}



def transform_data_structure(node_dict):
    # Helper function to generate unique names
    def generate_unique_name(base, prefix, existing):
        new_name = f"{prefix}_{base}" if prefix else base
        count = 1
        while new_name.lower() in {n.lower() for n in existing}:
            new_name = f"{prefix}{count}_{base}" if prefix else f"{base}_{count}"
            count += 1
        return new_name

    # Phase 1: Node renaming (unchanged)
    node_mapping = {}
    existing_nodes = set(node_dict.keys())

    # Process nodes for reserved keywords first
    for node_name in list(node_dict.keys()):
        if node_name.lower() in reserved_keywords:
            new_name = generate_unique_name(node_name, 'N', existing_nodes)
            node_mapping[node_name] = new_name
            existing_nodes.add(new_name)

    # Then process numeric starting nodes
    for node_name in list(node_dict.keys()):
        node = node_dict[node_name]
        if node_name not in node_mapping and node_name and node_name[0].isdigit() and node["Node type"]:
            prefix = node["Node type"][0].upper()
            new_name = generate_unique_name(node_name, prefix, existing_nodes)
            node_mapping[node_name] = new_name
            existing_nodes.add(new_name)

    # Apply node renaming (unchanged)
    for old_name, new_name in node_mapping.items():
        node_dict[new_name] = node_dict.pop(old_name)
        node_dict[new_name]["Node name"] = new_name

    # Update node references (unchanged)
    for node in node_dict.values():
        node["Sources"] = [node_mapping.get(n, n) for n in node["Sources"]]

        if join_cond := node.get("Join Condition"):
            if isinstance(join_cond, str):
                for old, new in node_mapping.items():
                    join_cond = re.sub(rf"(?i)\b{re.escape(old)}\b", new, join_cond)
                node["Join Condition"] = join_cond
            elif isinstance(join_cond, list):
                new_conditions = []
                for condition in join_cond:
                    if isinstance(condition, str):
                        for old, new in node_mapping.items():
                            condition = re.sub(rf"(?i)\b{re.escape(old)}\b", new, condition)
                    new_conditions.append(condition)
                node["Join Condition"] = new_conditions

        if xml := node.get("Node XML"):
            if isinstance(xml, str):
                for old, new in node_mapping.items():
                    xml = re.sub(rf"(?i)\b{re.escape(old)}\b", new, xml)
                node["Node XML"] = xml

    # Phase 2: Field transformations (UPDATED with DataField_ prefix)
    field_mapping = {}
    all_fields = set()

    # Collect all fields from all sources
    for node in node_dict.values():
        all_fields.update(node["Fields"])
        all_fields.update(node["Formula"])
        all_fields.update(node["Aggregated columns"])

        if join_cond := node.get("Join Condition"):
            if isinstance(join_cond, str):
                matches = re.findall(r'(?<=\.)\d*\w+', join_cond)
                all_fields.update(matches)
            elif isinstance(join_cond, list):
                for condition in join_cond:
                    if isinstance(condition, str):
                        matches = re.findall(r'(?<=\.)\d*\w+', condition)
                        all_fields.update(matches)

    # Process each field with DataField_ prefix
    for original in all_fields:
        if not isinstance(original, str):
            continue

        new_name = original

        # Rule 1: Remove leading zeros
        if new_name.startswith('0'):
            new_name = new_name.lstrip('0') or '0'

        # Rule 2: Prefix other numeric starts with DataField_
        if new_name and new_name[0].isdigit() and not new_name.startswith('0'):
            new_name = f"datafield_{new_name}"  # CHANGED FROM F to DataField_

        # Rule 3: Prefix reserved keywords with DataField_
        if new_name.lower() in reserved_keywords:
            new_name = f"datafield_{new_name}"  # CHANGED FROM F to DataField_

        if new_name != original:
            field_mapping[original] = new_name

    # Apply field transformations
    for node in node_dict.values():
        # Update standard field lists
        for field_type in ["Fields", "Formula", "Aggregated columns"]:
            node[field_type] = [field_mapping.get(name, name) for name in node[field_type]]

        # Update counts
        node["No of Fields"] = len(node["Fields"])
        node["No of formula"] = len(node["Formula"])

        # Update Join Conditions
        if join_cond := node.get("Join Condition"):
            if isinstance(join_cond, str):
                parts = re.split(r'([=<>]+)', join_cond)
                for i in range(len(parts)):
                    if i % 2 == 0:  # Only process field references
                        parts[i] = re.sub(
                            r'(?<=\.)(\w+)',
                            lambda m: field_mapping.get(m.group(1), m.group(1)),
                            parts[i]
                        )
                node["Join Condition"] = ''.join(parts)
            elif isinstance(join_cond, list):
                new_conditions = []
                for condition in join_cond:
                    if isinstance(condition, str):
                        parts = re.split(r'([=<>]+)', condition)
                        for i in range(len(parts)):
                            if i % 2 == 0:
                                parts[i] = re.sub(
                                    r'(?<=\.)(\w+)',
                                    lambda m: field_mapping.get(m.group(1), m.group(1)),
                                    parts[i]
                                )
                        condition = ''.join(parts)
                    new_conditions.append(condition)
                node["Join Condition"] = new_conditions

        # Update Node XML
        if xml := node.get("Node XML"):
            if isinstance(xml, str):
                for old, new in field_mapping.items():
                    xml = re.sub(
                        rf'(?<=[>"\s]){re.escape(old)}(?=[<"\s])',
                        new,
                        xml
                    )
                node["Node XML"] = xml

    return node_dict











def update_chunk_info(node_dict):

    # Step 1: Determine the last node number
    last_node_number = max(node_dict[node_name]["Node Number"] for node_name in node_dict)
    # logger.info(last_node_number)
    # Step 1: Count occurrences of each node name in all "Sources" lists.
    for node in node_dict:
        occurrence_count = 0
        for other_node in node_dict:
            # Compare all in lowercase
            sources_lower = [s.lower() for s in node_dict[other_node]["Sources"]]
            occurrence_count += sources_lower.count(node.lower())
        node_dict[node]["No of Occurances"] = occurrence_count


    # Step 2: Determine "Is Primary" using your logic.
    for node in node_dict:
        node_type = node_dict[node]["Node type"]
        occ_count = node_dict[node]["No of Occurances"]
        node_number = node_dict[node]["Node Number"]

        if node_type in {"Aggregation", "Union", "Rank"}:
            is_primary = "Yes"
        elif node_type in {"Projection", "JoinNode"} and occ_count > 1:
            is_primary = "Yes"
        else:
            is_primary = "No"

        if occ_count == 0:
            is_primary = "Yes"

        node_dict[node]["Is Primary"] = is_primary

    # Step 3: Assign "Chunk Number" for primary nodes in the sequence based on sorted "Node Number".
    # Function to update correct node number
    update_correct_node_numbers(node_dict)

    chunk_number = 1
    # Sort nodes by the numeric value of "Node Number"
    for node in sorted(node_dict, key=lambda n: node_dict[n]["Node Number"]):
        if node_dict[node]["Is Primary"] == "Yes":
            node_dict[node]["Chunk Number"] = chunk_number
            chunk_number += 1
        else:
            node_dict[node]["Chunk Number"] = None


    update_merged_nodes(node_dict)
    update_nonprimary_chunk_numbers(node_dict)
    update_chunk_sources(node_dict)








def update_chunk_sources(node_dict):
    # Step 1: Group nodes by Chunk Number
    chunk_dict = {}
    for node_name, details in node_dict.items():
        chunk_number = details.get("Chunk Number")
        if chunk_number not in chunk_dict:
            chunk_dict[chunk_number] = []
        chunk_dict[chunk_number].append(node_name)

    # Step 2: Iterate through each chunk to update non-node sources
    for chunk_number, nodes in chunk_dict.items():
        # Collect all node names in the chunk
        node_names = set(nodes)
        all_sources = set()

        # Collect all sources in the chunk
        for node_name in nodes:
            sources = set(node_dict[node_name].get("Sources", []))
            all_sources.update(sources)

        # Identify sources that are not part of node names
        non_node_sources = list(all_sources - node_names)

        # Update Chunk Sources for each node in the chunk
        # for node_name in nodes:
        #     node_dict[node_name]["Chunk Sources"] = non_node_sources
        for node_name in nodes:
            node_dict[node_name]["Chunk Sources"] = [source.lower() for source in non_node_sources]


    return node_dict




def get_merged_nonprimary_nodes(node_name, node_dict, visited=None):

    node_name = node_name.lower()  # Ensure lowercase

    if visited is None:
        visited = set()
    if node_name in visited:
        return []
    visited.add(node_name)

    sources = node_dict.get(node_name, {}).get("Sources", [])
    # logger.info(f"Sources for {node_name}: {sources}")
    if isinstance(sources, str):
        sources = [s.strip("[]'\" ").lower() for s in sources.split(",")]
    else:
        sources = [str(s).strip("[]'\" ").lower() for s in sources]

    if not sources:
        return [node_name]

    # logger.info(f"Processing node: {node_name}, Sources: {sources}")

    all_primary = True
    collected = []
    for src in sources:
        if src not in node_dict:
            continue
        if node_dict[src].get("Is Primary") == "Yes":
            continue
        else:
            all_primary = False
            collected.extend(get_merged_nonprimary_nodes(src, node_dict, visited.copy()))

    if all_primary:
        return [node_name]
    else:
        return [node_name] + collected


def get_merged_nodes_for_primary(primary_node, node_dict):
    primary_node = primary_node.lower()
    result = [primary_node]

    # Get and normalize the sources of the primary node
    sources = node_dict.get(primary_node, {}).get("Sources", [])
    # logger.info(f"Sources for primary node {primary_node}: {sources}")
    if isinstance(sources, str):
        sources = [s.strip("[]'\" ").lower() for s in sources.split(",")]
    else:
        sources = [str(s).strip("[]'\" ").lower() for s in sources]

    for src in sources:
        if src not in node_dict:
            continue
        if node_dict[src].get("Is Primary") == "Yes":
            continue
        else:
            result.extend(get_merged_nonprimary_nodes(src, node_dict))
            # collected.extend(get_merged_nonprimary_nodes(src, node_dict, visited))


    return result



def update_merged_nodes(node_dict):
    node_dict = {
        k.lower(): v for k, v in node_dict.items()
    }
    for node in node_dict:
        if node_dict[node].get("Is Primary") == "Yes":
            merged = get_merged_nodes_for_primary(node, node_dict)
            node_dict[node]["Merged Nodes"] = merged
        else:
            node_dict[node]["Merged Nodes"] = []
    # logger.info("Merged nodes updated successfully.")
    # logger.info("Merged Nodes:", {k: v["Merged Nodes"] for k, v in node_dict.items() if v["Merged Nodes"]})







def update_nonprimary_chunk_numbers(node_dict):
    """
    For each primary node in node_dict, update the "Chunk Number" of all
    nodes in its merged nodes list (except itself) to be the same as the primary node's chunk number.

    Only update if the merged node exists in node_dict.
    """
    node_dict = {
        k.lower(): v for k, v in node_dict.items()
    }
    for node in node_dict:
        node_name = node_dict[node].get("Node name")
        if node_name is not None:
            node_dict[node]["Node name"] = node_name.lower()
        if node_dict[node].get("Is Primary") == "Yes":

            primary_chunk = node_dict[node].get("Chunk Number")
            merged_list = node_dict[node].get("Merged Nodes", [])
            # logger.info(f"Primary Node: {node}, Chunk Number: {primary_chunk}, Merged Nodes: {merged_list}")
            # Process each merged node in the list (skip the primary itself)
            for merged_node in merged_list:
                if merged_node != node and merged_node in node_dict:
                    merged_node = merged_node.lower()
                    # logger.info(f"Updating Chunk Number for merged node: {merged_node}")
                    # Update the chunk number for the merged node to the primary node's chunk number
                    node_dict[merged_node]["Chunk Number"] = primary_chunk






hana_to_bigquery_functions = {
    "functions": [
        {
            "Function": "utctolocal",
            "Syntax": "utctolocal(datearg, timezonearg)",
            "Purpose": "Interprets datearg (a date, without timezone) as utc and convert it to the timezone named by timezonearg (a string)",
            "BigQuery Equivalent with Example": "**`DATETIME(timestamp_expression, timezone)`** <br> Example: `DATETIME(TIMESTAMP(\"2025-09-20 10:00:00\", \"UTC\"), \"America/New_York\")` returns the datetime in the New York timezone."
        },
        {
            "Function": "localtoutc",
            "Syntax": "localtoutc(datearg, timezonearg)",
            "Purpose": "Converts the local datetime datearg to the timezone specified by the string timezonearg, return as a date",
            "BigQuery Equivalent with Example": "**`DATETIME(timestamp_expression, \"UTC\")`** <br> Example: `DATETIME(TIMESTAMP(\"2025-09-20 10:00:00\", \"America/New_York\"), \"UTC\")` returns the UTC datetime."
        },
        {
            "Function": "weekday",
            "Syntax": "weekday(date)",
            "Purpose": "Returns the weekday as an integer in the range 0..6, 0 is Monday.",
            "BigQuery Equivalent with Example": "**`MOD(EXTRACT(DAYOFWEEK FROM date) + 5, 7)`** <br> BigQuery's `EXTRACT` returns 1 for Sunday. This formula adjusts it to match the 0=Monday convention. <br> Example: `MOD(EXTRACT(DAYOFWEEK FROM DATE '2025-09-22') + 5, 7)` returns `0` for Monday."
        },
        {
            "Function": "now",
            "Syntax": "now()",
            "Purpose": "Returns the current date and time (localtime of the server timezone) as date",
            "BigQuery Equivalent with Example": "**`CURRENT_DATETIME([timezone])`** <br> Example: `CURRENT_DATETIME()` returns the current UTC datetime. `CURRENT_DATETIME(\"Asia/Kolkata\")` returns the current datetime in India."
        },
        {
            "Function": "daysbetween",
            "Syntax": "daysbetween(date1, date2)<br>daysbetween(daydate1, daydate2)<br>daysbetween(seconddate1, seconddate2)<br>daysbetween(longdate1, longdate2)",
            "Purpose": "Returns the number of days (integer) between date1 and date2. The first version is an alternative to date2 - date1. Instead of rounding or checking for exactly 24 hours distance, this truncates both date values today precision and subtract the resulting day numbers, meaning that if arg2 is not the calendar day following arg1, daysbetween returns 1 regardless of the time components of arg1 and arg2.",
            "BigQuery Equivalent with Example": "**`DATE_DIFF(date_expression_1, date_expression_2, DAY)`** <br>. Same order compared to HANA. Example: `DATE_DIFF(DATE '2025-09-20', DATE '2025-09-30', DAY)` returns `10`. => date_expression_2 - date_expression_1"
        },
        {
            "Function": "secondsbetween",
            "Syntax": "secondsbetween(seconddate1, seconddate2)<br>secondsbetween(longdate1, longdate2)",
            "Purpose": "Returns the number of seconds the first to the second arg, as a fixed point number. The returned value is positive if the first argument is less than the second. The return values are fixed18.0 in both cases (note that it may prove more useful to use fixed11.7 in case of longdate arguments).",
            "BigQuery Equivalent with Example": "**`TIMESTAMP_DIFF(timestamp_expression_1, timestamp_expression_2, SECOND)`** <br>. Same order compared to HANA. Example: `TIMESTAMP_DIFF(TIMESTAMP \"2025-09-20 12:00:00\", TIMESTAMP \"2025-09-20 12:01:00\", SECOND)` returns `60`.=> timestamp_expression_2 - timestamp_expression_1 "
        },
        {
            "Function": "component",
            "Syntax": "component(date, int)",
            "Purpose": "The int argument may be int the range 1..6, the values mean year, month, day, hour, minute, second, respectively. If a component is not set in the date, the component function returns a default value, 1 for the month or the day, 0 for other components. You can also apply the component function to longdate and time types.",
            "BigQuery Equivalent with Example": "**`EXTRACT(part FROM date_expression)`** <br> Example (to get the month): `EXTRACT(MONTH FROM DATE '2025-09-20')` returns `9`."
        },
        {
            "Function": "addseconds",
            "Syntax": "addseconds(date, int)<br>addseconds(seconddate, decfloat)<br>addseconds(longdate, decfloat)",
            "Purpose": "Return a date plus a number of seconds. Fractional seconds are used in case of longdate. If any argument is null, then null handling is (in opposition to the default done with adds) to return null.",
            "BigQuery Equivalent with Example": "**`DATETIME_ADD(datetime_expression, INTERVAL integer SECOND)`** <br> Example: `DATETIME_ADD(DATETIME \"2025-09-20 12:00:00\", INTERVAL 45 SECOND)` returns `2025-09-20 12:00:45`."
        },
        {
            "Function": "adddays",
            "Syntax": "adddays(date, int)<br>adddays(daydate, int)<br>adddays(seconddate, int)<br>adddays(longdate, int)",
            "Purpose": "Return a date plus a number of days. If any argument is null, then null handling is (in opposition to the default done with adds) to return null.",
            "BigQuery Equivalent with Example": "**`DATE_ADD(date_expression, INTERVAL integer DAY)`** <br> Example: `DATE_ADD(DATE \"2025-09-20\", INTERVAL 5 DAY)` returns `2025-09-25`."
        },
        {
            "Function": "quarter",
            "Syntax": "quarter(date)<br>quarter(date, month)",
            "Purpose": "Return a string 'yyyy-Qn', yyyy being the year of the quarter and n the quarter of the year. An optional start month (of the fiscal year) may be supplied. For example, quarter(date('2011-01-01'), 6) is '2010-Q3' and quarter(date('2011-06-01'), 6) is '2011-Q1'.",
            "BigQuery Equivalent with Example": "**`FORMAT_DATE(\"%Y-Q%Q\", date_expression)`** <br> This handles standard calendar quarters. Fiscal quarters require custom logic. <br> Example: `FORMAT_DATE(\"%Y-Q%Q\", DATE '2025-08-15')` returns `2025-Q3`."
        },
        {
            "Function": "format",
            "Syntax": "format(longdate, string)",
            "Purpose": "Date values may be used together with format strings, as described elsewhere in the NewDb documentation (look for descriptions of the TO_DATE and TO_CHAR SQL functions) . For example, format(longdate('2011-06-09 20:20:13.1234567'), 'YYYY/MM/DD\"T\"HH24:MI:SS.FF7')",
            "BigQuery Equivalent with Example": "**`FORMAT_DATETIME(format_string, datetime_expression)`** <br> The format string elements differ from HANA. <br> Example: `FORMAT_DATETIME(\"%Y/%m/%d %H:%M:%S\", DATETIME '2025-09-20 16:30:00')` returns `\"2025/09/20 16:30:00\"`."
        }
    ]
}


def xml_contains_formula(xml_content):
    """
    Extracts formula names from XML content and returns their BigQuery equivalents.
    
    Args:
        xml_content (str): XML string containing formula tags
        
    Returns:
        str: Formatted string with formula information, or empty string if none found
    """
    # Extract all formula names using regex
    matches = re.findall(r'<formula>(\w+)\(', xml_content)
    if not matches:
        return ""
    
    formula_info = []
    for formula_name in matches:
        # Check if the formula exists in our dictionary
        for func in hana_to_bigquery_functions["functions"]:
            if func["Function"] == formula_name:
                formula_info.append(f"""
Formula: {func['Function']}
Syntax: {func['Syntax']}
Purpose: {func['Purpose']}
BigQuery Equivalent: {func['BigQuery Equivalent with Example']}
""")
                break
    
    if formula_info:
        return "FORMULAS FOUND IN XML:\n" + "\n".join(formula_info)
    else:
        return ""



import re

def extract_rank_function_structure(sql):
    """
    Extracts the RANK() window function structure and WHERE condition from SQL.
    
    Args:
        sql (str): SQL query string
        
    Returns:
        str: Formatted RANK function structure with dynamic WHERE condition
    """
    # Pattern to match RANK() function
    rank_pattern = r'RANK\s*\(\s*\)\s*OVER'
    
    rank_match = re.search(rank_pattern, sql, re.IGNORECASE)

    
    if rank_match:
        return f"""
        --RANK Node Detected
        --Always use Subquery for this type of Rank Nodes. 
        --Windows function must not be outside of SELECT statement. 
        --SubQuery must be created to apply outside WHERE condition filters if any. Look the example for clarification.


        E.g:    
        SELECT *
        FROM (
            SELECT
                column1 as alaisname1,
                column2 as alaisname2
                RANK() OVER (
                    PARTITION BY column1 
                    ORDER BY column2 DESC
                ) AS Rank_Column
            FROM Table
        ) ranked
        WHERE Rank_Column = 1;

"""
    
    return ""



def switch_date_functions_in_xml(xml_string):
    """
    Switch arguments in daysbetween and secondsbetween functions in an XML string containing formula tags
    """
    # Pattern to match the formula content
    pattern = r'(<formula>)(.*?)(</formula>)'
    
    def process_formula(match):
        opening_tag = match.group(1)
        formula_content = match.group(2)
        closing_tag = match.group(3)
        
        # Switch daysbetween arguments
        modified_formula = re.sub(
            r'(daysbetween|secondsbetween)\(([^,]+),\s*([^)]+)\)',
            lambda m: f'{m.group(1)}({m.group(3).strip()}, {m.group(2).strip()})',
            formula_content
        )
        
        return f"{opening_tag}{modified_formula}{closing_tag}"
    
    # Process all formula tags in the XML
    result = re.sub(pattern, process_formula, xml_string, flags=re.DOTALL)
    return result




def format_actual_sql(sql):
    sql_text_1 = remove_before_first_select(sql)
    sql_text_1 = remove_non_sql_context(sql_text_1)
    sql_text_1 = remove_unwanted_patterns(sql_text_1)
    sql_text_1 = "\n".join(remove_sql_comments(sql_text_1.splitlines()))
    sql_text_1 = format_other_query_intent(sql_text_1)
    return sql_text_1


def format_other_query_intent(sql):
    formatted_sql = sqlparse.format(
    sql,
    reindent=True,
    keyword_case='upper',  # This changes keywords to uppercase
    identifier_case='lower'   # This preserves the case of field and table names
    )
    return formatted_sql





import time
import logging

logger = logging.getLogger(__name__)
def api_call_with_retry(model_name, full_prompt, task_type='sql'):
    timeout = 60
    max_retries = 3
    delay = 5

    for attempt in range(1, max_retries + 1):
        start_time = time.time()
        result = None

        try:
            # First (max_retries - 1) attempts → respect timeout
            if attempt < max_retries:
                while time.time() - start_time < timeout:
                    try:
                        result = api_call(model_name, full_prompt, task_type=task_type)
                        if result:
                            return result
                    except Exception as e:
                        logger.error(f"Error on attempt {attempt} for {model_name}: {str(e)}", exc_info=True)
                        break  # break inner loop so retry can happen
                    time.sleep(1)

                logger.warning(
                    f"No response within {timeout} seconds on attempt {attempt}. Retrying after {delay}s..."
                )

                # Update prompt for retry
                full_prompt = (
                    f"{full_prompt}\n\n"
                    f"(Note: Attempt {attempt} timed out after {timeout} seconds. "
                    f"Please respond within a minute on retry, but ensure accuracy.)"
                )

                time.sleep(delay)

            else:
                # Final attempt → wait indefinitely but still handle errors
                logger.info("Final attempt: waiting indefinitely until API responds...")
                while not result:
                    try:
                        result = api_call(model_name, full_prompt, task_type=task_type)
                        if result:
                            return result
                    except Exception as e:
                        logger.error(f"Final attempt error for {model_name}: {str(e)}", exc_info=True)
                        # wait a bit before retrying again indefinitely
                        time.sleep(5)
                    time.sleep(2)

        except Exception as e:
            logger.error(f"Unexpected error in retry logic (attempt {attempt}): {str(e)}", exc_info=True)

    logger.error(f"All {max_retries} attempts failed for {model_name}.")
    return None


async def api_call_with_retry_async(model_name, full_prompt, task_type="sql", target=None):
    max_retries = 2
    delay = 2
    original_model = model_name

    for attempt in range(1, max_retries + 1):
        # Switch model after first failure
        if attempt == 2 and original_model.lower() == "gemini-3.1-flash-lite-preview":
            model_name = "Gemini"
            logger.info(f"Switching model to {model_name} on attempt {attempt}.")

        # Set timeout based on current model
        timeout = 60 if model_name.lower() == "gemini-3.1-flash-lite-preview" else 120

        try:
            result = await asyncio.wait_for(
                api_call_async(model_name, full_prompt, task_type=task_type, target=target),
                timeout=timeout
            )
            if result:
                return result
        except asyncio.TimeoutError:
            logger.warning(f"{model_name} attempt {attempt} timed out after {timeout}s.")
        except Exception as e:
            logger.warning(f"{model_name} API error on attempt {attempt}: {e}")

        if attempt < max_retries:
            # backoff before retry
            await asyncio.sleep(delay)

    logger.error(f"{model_name} failed after {max_retries} attempts.")
    return None
