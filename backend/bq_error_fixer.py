"""
BigQuery Error Fixer - Deterministic SQL fixes for common BigQuery errors.

This module provides pattern-based fixes for BigQuery SQL errors without
requiring LLM calls. It parses error messages and applies targeted fixes.
"""

import re
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)


def strip_ansi_codes(text: str) -> str:
    """
    Remove ANSI escape sequences (e.g., [4m, [0m) from text.
    BigQuery error messages often contain these for terminal formatting.
    """
    if not text:
        return text
    # Pattern for ANSI escape sequences
    ansi_escape = re.compile(r'(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)


# ============================================================================
# Error Pattern Registry
# ============================================================================

class BQErrorFix:
    """Represents a BigQuery error pattern and its fix strategy."""
    
    def __init__(self, pattern: str, fixer_func, description: str):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.fixer_func = fixer_func
        self.description = description
    
    def matches(self, error_msg: str) -> Optional[re.Match]:
        return self.pattern.search(error_msg)
    
    def apply(self, sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
        return self.fixer_func(sql, match, error_msg)


# ============================================================================
# Individual Fixer Functions
# ============================================================================

def fix_cast_missing_as(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: CAST(expr TYPE) -> CAST(expr AS TYPE)
    Error: "Expected AS after CAST"
    """
    bq_types = r'(INT64|INT|INTEGER|FLOAT64|FLOAT|NUMERIC|BIGNUMERIC|STRING|BOOL|BOOLEAN|DATE|DATETIME|TIMESTAMP|TIME|BYTES|ARRAY|STRUCT)'
    
    # Improved pattern: 
    # 1. Matches CAST(expr type)
    # 2. Uses negative lookahead to ensure we don't match if 'AS' is already there
    # 3. Ensures we don't accidentally match parts of words
    pattern = rf'\bCAST\s*\(\s*(.+?)\s+(?!AS\b)({bq_types})\s*\)'
    
    def replacer(m):
        expr = m.group(1).strip()
        dtype = m.group(2).strip()
        # Double check if expression ends with AS (case insensitive)
        if expr.upper().endswith(' AS'):
            return m.group(0)
        return f"CAST({expr} AS {dtype})"

    fixed = re.sub(pattern, replacer, sql, flags=re.IGNORECASE)
    return fixed, fixed != sql


def fix_parameterized_numeric(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: CAST(x AS NUMERIC(p,s)) -> ROUND(CAST(x AS NUMERIC), s)
    Error: "Parameterized types are not allowed in CAST"
    """
    # Pattern: CAST(expr AS NUMERIC(precision, scale))
    pattern = r'CAST\s*\(\s*(.+?)\s+AS\s+(?:BIG)?NUMERIC\s*\(\s*\d+\s*,\s*(\d+)\s*\)\s*\)'
    
    def replacer(m):
        expr = m.group(1)
        scale = m.group(2)
        return f'ROUND(CAST({expr} AS NUMERIC), {scale})'
    
    fixed = re.sub(pattern, replacer, sql, flags=re.IGNORECASE)
    return fixed, fixed != sql


def fix_column_not_found(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Add NULL AS column_name for missing columns.
    Error: "Name X not found" or "Unrecognized name: X"
    """
    column_name = match.group(1)
    
    # Find the SELECT clause and check if column is in it
    select_match = re.search(r'\bSELECT\s+(.*?)\s+FROM\b', sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return sql, False
    
    select_clause = select_match.group(1)
    
    # Check if column is already aliased
    if re.search(rf'\bAS\s+{re.escape(column_name)}\b', select_clause, re.IGNORECASE):
        return sql, False
    
    # Check if column is referenced but table prefix is missing
    # Try to add table alias if we can determine it
    tables_match = re.findall(r'\bFROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?', sql, re.IGNORECASE)
    
    if not tables_match:
        # Fallback: Add NULL AS column_name before FROM
        new_select = select_clause.rstrip() + f', NULL AS {column_name}'
        fixed = sql.replace(select_clause, new_select, 1)
        return fixed, True
    
    return sql, False


def fix_not_in_group_by(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Add column to GROUP BY or wrap in ANY_VALUE().
    Error: "SELECT list expression references column X which is neither grouped nor aggregated"
    """
    column_ref = match.group(1)
    
    # Strategy 1: Check if GROUP BY exists
    group_by_match = re.search(r'\bGROUP\s+BY\s+(.+?)(?:\s+HAVING|\s+ORDER|\s+LIMIT|\s*$)', sql, re.IGNORECASE | re.DOTALL)
    
    if group_by_match:
        group_by_clause = group_by_match.group(1)
        
        # Check if column already in GROUP BY
        if re.search(rf'\b{re.escape(column_ref)}\b', group_by_clause, re.IGNORECASE):
            return sql, False
        
        # Add column to GROUP BY
        new_group_by = group_by_clause.rstrip() + f', {column_ref}'
        fixed = sql[:group_by_match.start(1)] + new_group_by + sql[group_by_match.end(1):]
        return fixed, True
    
    # Strategy 2: Wrap in ANY_VALUE() if no GROUP BY can be modified
    # Find the column in SELECT and wrap it
    pattern = rf'(?<![A-Z_])({re.escape(column_ref)})(?![A-Z_\(])'
    
    def wrap_any_value(m):
        return f'ANY_VALUE({m.group(1)})'
    
    # Only wrap in SELECT clause, not in FROM/WHERE
    select_end = sql.upper().find(' FROM ')
    if select_end > 0:
        select_part = sql[:select_end]
        rest_part = sql[select_end:]
        fixed_select = re.sub(pattern, wrap_any_value, select_part, count=1, flags=re.IGNORECASE)
        fixed = fixed_select + rest_part
        return fixed, fixed != sql
    
    return sql, False


def fix_unbalanced_parentheses(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Balance parentheses by adding missing ones.
    Error: "Expected ')' but got X" or "Unmatched parenthesis"
    """
    open_count = sql.count('(')
    close_count = sql.count(')')
    
    if open_count == close_count:
        return sql, False
    
    if open_count > close_count:
        # Add closing parens at end before any ORDER BY, LIMIT, or end
        end_match = re.search(r'\s+(ORDER\s+BY|LIMIT|$)', sql, re.IGNORECASE)
        if end_match:
            insert_pos = end_match.start()
            fixed = sql[:insert_pos] + ')' * (open_count - close_count) + sql[insert_pos:]
        else:
            fixed = sql + ')' * (open_count - close_count)
        return fixed, True
    else:
        # More closing than opening - remove extra closing parens from end
        diff = close_count - open_count
        # Remove from the end
        fixed = sql
        for _ in range(diff):
            last_paren = fixed.rfind(')')
            if last_paren > 0:
                fixed = fixed[:last_paren] + fixed[last_paren+1:]
        return fixed, fixed != sql


def fix_type_coercion(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Add explicit CAST for type mismatches.
    Error: "No matching signature for operator" or "Cannot coerce"
    """
    # Extract the types from error message
    # Pattern: "No matching signature for operator = for argument types: STRING, INT64"
    types_match = re.search(r'argument types:\s*(\w+),\s*(\w+)', error_msg, re.IGNORECASE)
    
    if not types_match:
        return sql, False
    
    type1, type2 = types_match.groups()
    
    # Common fix: if comparing STRING to INT64, cast one to STRING
    if type1.upper() == 'STRING' and type2.upper() in ('INT64', 'FLOAT64', 'NUMERIC'):
        # Find comparison operators and wrap the right side
        pattern = r"=\s*(\d+)"
        fixed = re.sub(pattern, r"= CAST('\1' AS STRING)", sql)
        return fixed, fixed != sql
    elif type2.upper() == 'STRING' and type1.upper() in ('INT64', 'FLOAT64', 'NUMERIC'):
        # Wrap string in CAST to number
        pattern = r"=\s*'([^']+)'"
        fixed = re.sub(pattern, r'= CAST(\1 AS INT64)', sql, count=1)
        return fixed, fixed != sql
    
    return sql, False


def fix_duplicate_column(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Add unique alias for duplicate column names.
    Error: "Duplicate column name"
    """
    dup_col = match.group(1) if match.lastindex else None
    
    if not dup_col:
        # Try to extract from generic error
        dup_match = re.search(r"Duplicate column name[:\s]+['\"]?(\w+)['\"]?", error_msg, re.IGNORECASE)
        if dup_match:
            dup_col = dup_match.group(1)
    
    if not dup_col:
        return sql, False
    
    # Find all occurrences in SELECT clause
    select_match = re.search(r'\bSELECT\s+(.*?)\s+FROM\b', sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return sql, False
    
    select_clause = select_match.group(1)
    
    # Find occurrences of this column without alias
    pattern = rf'\b({re.escape(dup_col)})\s*(?:,|\s+FROM)'
    
    counter = [0]
    def add_alias(m):
        counter[0] += 1
        if counter[0] > 1:
            return f'{m.group(1)} AS {dup_col}_{counter[0]}' + (', ' if ',' in m.group(0) else ' FROM')
        return m.group(0)
    
    fixed_select = re.sub(pattern, add_alias, select_clause, flags=re.IGNORECASE)
    fixed = sql.replace(select_clause, fixed_select, 1)
    
    return fixed, fixed != sql


def fix_trailing_comma(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Remove trailing comma before FROM, WHERE, GROUP BY, etc.
    Error: "Expected column expression but got keyword FROM"
    """
    # Remove comma followed by keyword
    pattern = r',\s*(FROM|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|UNION|INTERSECT|EXCEPT)\b'
    fixed = re.sub(pattern, r' \1', sql, flags=re.IGNORECASE)
    return fixed, fixed != sql


def fix_reserved_keyword_as_alias(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Quote reserved keywords used as column aliases.
    Error: "Syntax error: Expected X but got reserved keyword"
    """
    keyword = match.group(1) if match.lastindex else None
    
    if not keyword:
        return sql, False
    
    # Common BigQuery reserved keywords that might be used as aliases
    reserved = {'SELECT', 'FROM', 'WHERE', 'GROUP', 'BY', 'ORDER', 'LIMIT', 'JOIN', 
                'ON', 'AND', 'OR', 'NOT', 'IN', 'IS', 'NULL', 'TRUE', 'FALSE',
                'AS', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'BETWEEN', 'LIKE',
                'ALL', 'ANY', 'EXISTS', 'UNION', 'INTERSECT', 'EXCEPT', 'DISTINCT',
                'PARTITION', 'OVER', 'WINDOW', 'ROWS', 'RANGE', 'UNBOUNDED',
                'PRECEDING', 'FOLLOWING', 'CURRENT', 'ROW', 'ARRAY', 'STRUCT',
                'DATE', 'TIME', 'TIMESTAMP', 'DATETIME', 'INTERVAL'}
    
    if keyword.upper() not in reserved:
        return sql, False
    
    # Quote the keyword as alias using backticks
    pattern = rf'\bAS\s+({re.escape(keyword)})\b'
    fixed = re.sub(pattern, rf'AS `\1`', sql, flags=re.IGNORECASE)
    
    return fixed, fixed != sql


def fix_invalid_table_reference(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Add dataset prefix to table names if missing.
    Error: "Table not found" or "Not found: Table"
    """
    table_name = match.group(1) if match.lastindex else None
    
    if not table_name:
        return sql, False
    
    # Check if table already has dataset prefix
    if '.' in table_name:
        return sql, False
    
    # This is a placeholder - in real usage, you'd want to inject the correct dataset
    # For now, we just flag that we couldn't fix it
    return sql, False


def fix_division_by_zero(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Wrap division in SAFE_DIVIDE or add NULLIF.
    Error: "division by zero"
    """
    # Pattern: expr / expr -> SAFE_DIVIDE(expr, expr)
    # This is complex because we need to parse expressions
    # Simple approach: wrap all divisions
    
    pattern = r'(\([^)]+\)|[\w.]+)\s*/\s*(\([^)]+\)|[\w.]+)'
    
    def safe_divide(m):
        return f'SAFE_DIVIDE({m.group(1)}, {m.group(2)})'
    
    fixed = re.sub(pattern, safe_divide, sql)
    return fixed, fixed != sql


def fix_ambiguous_column(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Column is ambiguous - try to add table alias.
    Error: "Column name X is ambiguous"
    """
    column_name = match.group(1) if match.lastindex else None
    
    if not column_name:
        return sql, False
    
    # Find table aliases from FROM clause
    from_match = re.search(r'\bFROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?', sql, re.IGNORECASE)
    if not from_match:
        return sql, False
    
    table_alias = from_match.group(2) or from_match.group(1)
    
    # Add table alias prefix to the ambiguous column in SELECT
    select_end = sql.upper().find(' FROM ')
    if select_end > 0:
        select_part = sql[:select_end]
        rest_part = sql[select_end:]
        
        # Add alias to unqualified column references
        pattern = rf'(?<![.\w])({re.escape(column_name)})(?![.\w])'
        fixed_select = re.sub(pattern, f'{table_alias}.\\1', select_part, flags=re.IGNORECASE)
        fixed = fixed_select + rest_part
        return fixed, fixed != sql
    
    return sql, False


def fix_invalid_date_format(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Convert invalid date literals to proper format.
    Error: "Invalid date" or "Could not parse date"
    """
    # Common patterns: 'DD-MM-YYYY' -> 'YYYY-MM-DD'
    # Match dates in DD-MM-YYYY or DD/MM/YYYY format
    pattern = r"'(\d{2})[-/](\d{2})[-/](\d{4})'"
    
    def fix_date(m):
        day, month, year = m.groups()
        return f"'{year}-{month}-{day}'"
    
    fixed = re.sub(pattern, fix_date, sql)
    return fixed, fixed != sql


def fix_window_function_order(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Window functions require ORDER BY in OVER clause.
    Error: "Window function requires ORDER BY"
    """
    # Find OVER() without ORDER BY
    pattern = r'(ROW_NUMBER|RANK|DENSE_RANK|LEAD|LAG|FIRST_VALUE|LAST_VALUE|NTH_VALUE)\s*\(\s*\)\s*OVER\s*\(\s*(PARTITION\s+BY\s+[^)]+)?\s*\)'
    
    def add_order_by(m):
        func = m.group(1)
        partition = m.group(2) or ''
        if partition:
            return f'{func}() OVER ({partition} ORDER BY 1)'
        return f'{func}() OVER (ORDER BY 1)'
    
    fixed = re.sub(pattern, add_order_by, sql, flags=re.IGNORECASE)
    return fixed, fixed != sql


def fix_string_aggregation(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: STRING_AGG requires separator.
    Error: "STRING_AGG requires separator"
    """
    pattern = r'STRING_AGG\s*\(\s*([^,)]+)\s*\)'
    fixed = re.sub(pattern, r"STRING_AGG(\1, ', ')", sql, flags=re.IGNORECASE)
    return fixed, fixed != sql


def fix_array_subscript(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Array subscript must use OFFSET or ORDINAL.
    Error: "Array element access requires OFFSET or ORDINAL"
    """
    # Convert array[0] to array[OFFSET(0)]
    pattern = r'(\w+)\[(\d+)\]'
    fixed = re.sub(pattern, r'\1[OFFSET(\2)]', sql)
    return fixed, fixed != sql


def fix_null_comparison(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: NULL comparisons should use IS NULL / IS NOT NULL.
    Error: "Cannot compare to NULL"
    """
    # = NULL -> IS NULL
    sql = re.sub(r'\s*=\s*NULL\b', ' IS NULL', sql, flags=re.IGNORECASE)
    # <> NULL or != NULL -> IS NOT NULL
    sql = re.sub(r'\s*(<>|!=)\s*NULL\b', ' IS NOT NULL', sql, flags=re.IGNORECASE)
    return sql, True


def fix_safe_cast(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: Use SAFE_CAST for potentially failing conversions.
    Error: "Bad value" or "Could not cast"
    """
    # Replace CAST with SAFE_CAST for the problematic expression
    pattern = r'\bCAST\s*\('
    fixed = re.sub(pattern, 'SAFE_CAST(', sql, flags=re.IGNORECASE)
    return fixed, fixed != sql


def fix_cross_join_syntax(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: CROSS JOIN should not have ON clause.
    Error: "CROSS JOIN cannot have ON clause"
    """
    pattern = r'\bCROSS\s+JOIN\s+(\w+)\s+ON\b'
    fixed = re.sub(pattern, r'CROSS JOIN \1 WHERE', sql, flags=re.IGNORECASE)
    return fixed, fixed != sql


def fix_distinct_order_by(sql: str, match: re.Match, error_msg: str) -> Tuple[str, bool]:
    """
    Fix: ORDER BY column must be in SELECT DISTINCT.
    Error: "ORDER BY expression references column X which is not in the SELECT list"
    """
    # Extract the order by column
    order_col_match = re.search(r'references (?:column )?[\'"]?(\w+)[\'"]?', error_msg, re.IGNORECASE)
    if not order_col_match:
        return sql, False
    
    order_col = order_col_match.group(1)
    
    # Check if DISTINCT is used
    if not re.search(r'\bSELECT\s+DISTINCT\b', sql, re.IGNORECASE):
        return sql, False
    
    # Add column to SELECT if not present
    select_match = re.search(r'\bSELECT\s+DISTINCT\s+(.*?)\s+FROM\b', sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return sql, False
    
    select_clause = select_match.group(1)
    
    # Check if column already in select
    if re.search(rf'\b{re.escape(order_col)}\b', select_clause, re.IGNORECASE):
        return sql, False
    
    # Add column
    new_select = select_clause.rstrip() + f', {order_col}'
    fixed = sql.replace(select_clause, new_select, 1)
    return fixed, True


# ============================================================================
# Error Pattern Registry
# ============================================================================

ERROR_PATTERNS: List[BQErrorFix] = [
    # Syntax errors
    BQErrorFix(r'Expected\s+AS\s+after\s+CAST', fix_cast_missing_as, "CAST missing AS keyword"),
    BQErrorFix(r'Parameterized types are not allowed', fix_parameterized_numeric, "Parameterized NUMERIC type"),
    BQErrorFix(r"Expected\s+[\"']?\)\s*[\"']?\s+but\s+got", fix_unbalanced_parentheses, "Unbalanced parentheses"),
    BQErrorFix(r'Expected\s+column.*but\s+got\s+keyword\s+(FROM|WHERE)', fix_trailing_comma, "Trailing comma"),
    BQErrorFix(r'reserved\s+keyword\s+[\'"]?(\w+)', fix_reserved_keyword_as_alias, "Reserved keyword as alias"),
    
    # Column/table errors
    BQErrorFix(r'Name\s+(\w+)\s+not\s+found', fix_column_not_found, "Column not found"),
    BQErrorFix(r'Unrecognized\s+name:\s*(\w+)', fix_column_not_found, "Unrecognized column name"),
    BQErrorFix(r'([\w.]+)\s+which\s+is\s+neither\s+grouped\s+nor\s+aggregated', fix_not_in_group_by, "Column not in GROUP BY"),
    BQErrorFix(r'Column\s+name\s+(\w+)\s+is\s+ambiguous', fix_ambiguous_column, "Ambiguous column"),
    BQErrorFix(r'Duplicate\s+column\s+name[:\s]+[\'"]?(\w+)', fix_duplicate_column, "Duplicate column name"),
    
    # Type errors
    BQErrorFix(r'No\s+matching\s+signature.*argument\s+types', fix_type_coercion, "Type mismatch"),
    BQErrorFix(r'Cannot\s+coerce', fix_type_coercion, "Cannot coerce types"),
    BQErrorFix(r'Bad\s+(?:int64|float64|numeric|string)\s+value', fix_safe_cast, "Bad cast value"),
    BQErrorFix(r'Could\s+not\s+cast', fix_safe_cast, "Cast failed"),
    
    # Date/time errors
    BQErrorFix(r'Invalid\s+date', fix_invalid_date_format, "Invalid date format"),
    BQErrorFix(r'Could\s+not\s+parse\s+date', fix_invalid_date_format, "Could not parse date"),
    
    # NULL handling
    BQErrorFix(r'Cannot\s+compare\s+to\s+NULL', fix_null_comparison, "Invalid NULL comparison"),
    
    # Aggregation/window errors
    BQErrorFix(r'Window\s+function.*requires\s+ORDER\s+BY', fix_window_function_order, "Window function needs ORDER BY"),
    BQErrorFix(r'STRING_AGG.*requires\s+separator', fix_string_aggregation, "STRING_AGG needs separator"),
    BQErrorFix(r'ORDER\s+BY\s+expression\s+references.*not\s+in\s+the\s+SELECT', fix_distinct_order_by, "ORDER BY column not in SELECT"),
    
    # Array errors
    BQErrorFix(r'Array\s+element\s+access.*OFFSET|ORDINAL', fix_array_subscript, "Array subscript syntax"),
    
    # Division
    BQErrorFix(r'division\s+by\s+zero', fix_division_by_zero, "Division by zero"),
    
    # JOIN errors
    BQErrorFix(r'CROSS\s+JOIN\s+cannot\s+have\s+ON', fix_cross_join_syntax, "CROSS JOIN with ON"),
    
    # Table errors (can't always fix, but log)
    BQErrorFix(r'Not\s+found:\s*Table\s+(\S+)', fix_invalid_table_reference, "Table not found"),
]


# ============================================================================
# Main API
# ============================================================================

def fix_bigquery_error(sql: str, error_msg: str) -> Tuple[str, bool, str]:
    """
    Attempt to fix SQL based on BigQuery error message.
    
    Args:
        sql: The SQL query that produced the error
        error_msg: The BigQuery error message
        
    Returns:
        Tuple of (fixed_sql, was_fixed, fix_description)
        - fixed_sql: The potentially fixed SQL (original if no fix applied)
        - was_fixed: True if a fix was applied
        - fix_description: Description of what fix was applied (empty if none)
    """
    if not sql or not error_msg:
        return sql, False, ""
    
    # Strip ANSI escape codes from error message before matching
    clean_error_msg = strip_ansi_codes(error_msg)
    
    # Try each error pattern
    for error_fix in ERROR_PATTERNS:
        match = error_fix.matches(clean_error_msg)
        if match:
            try:
                fixed_sql, was_fixed = error_fix.apply(sql, match, clean_error_msg)
                if was_fixed:
                    logger.info(f"Applied fix: {error_fix.description}")
                    return fixed_sql, True, error_fix.description
            except Exception as e:
                logger.warning(f"Error applying fix '{error_fix.description}': {e}")
                continue
    
    # No pattern matched - return original
    return sql, False, ""


def fix_in_function_syntax(sql: str, match=None, error_msg: str = "") -> Tuple[str, bool]:
    """
    Fix SAP HANA-style IN() function to BigQuery IN operator.
    
    Converts:
        IN(column, 'val1', 'val2', ...) -> column IN ('val1', 'val2', ...)
        IF(IN(...), ...) pattern corrections
    
    This is a common LLM error when converting HANA Calculation Views.
    """
    if not sql:
        return sql, False
    
    original_sql = sql
    
    # Pattern: IN(column_expr, val1, val2, ...) where it's used as a function
    # We need to handle nested cases like IF(IN(col, 'a', 'b'), ...)
    # Match IN( followed by column reference, then comma-separated values )
    
    def replace_in_func(m):
        inner = m.group(1)
        # Split on first comma to get column and values
        # Be careful with nested parentheses
        paren_depth = 0
        first_comma = -1
        for i, char in enumerate(inner):
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
            elif char == ',' and paren_depth == 0:
                first_comma = i
                break
        
        if first_comma == -1:
            return m.group(0)  # No comma found, return original
        
        column = inner[:first_comma].strip()
        values = inner[first_comma + 1:].strip()
        
        return f"{column} IN ({values})"
    
    # Pattern matches IN( not preceded by NOT or space-IN (which would be valid SQL)
    # Use negative lookbehind to avoid matching valid "NOT IN" or "column IN"
    pattern = r'\bIN\s*\(\s*([^()]+(?:\([^()]*\)[^()]*)*)\s*\)'
    
    # Only replace if it looks like function syntax (has multiple comma-separated values after first arg)
    # Check for patterns like IN(col, 'val', 'val2')
    
    def smart_replace(m):
        full_match = m.group(0)
        inner = m.group(1)
        
        # Check if this looks like HANA IN() function (column followed by values)
        # Valid BigQuery: col IN (val1, val2) - this wouldn't match our pattern
        # HANA style: IN(col, val1, val2) - this is what we want to fix
        
        # Count commas at depth 0
        paren_depth = 0
        comma_count = 0
        first_comma_pos = -1
        for i, char in enumerate(inner):
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
            elif char == ',' and paren_depth == 0:
                if first_comma_pos == -1:
                    first_comma_pos = i
                comma_count += 1
        
        # Need at least 2 commas (col, val1, val2) or 1 comma with quoted values
        if comma_count >= 1 and first_comma_pos > 0:
            column = inner[:first_comma_pos].strip()
            values = inner[first_comma_pos + 1:].strip()
            
            # Validate that first part looks like a column reference
            # and subsequent parts look like values (quoted or numeric)
            if column and values:
                return f"{column} IN ({values})"
        
        return full_match
    
    sql = re.sub(pattern, smart_replace, sql, flags=re.IGNORECASE)
    
    return sql, sql != original_sql


def fix_double_else_in_case(sql: str, match=None, error_msg: str = "") -> Tuple[str, bool]:
    """
    Fix malformed CASE statements with double ELSE.
    
    LLM sometimes generates:
        CASE WHEN ... THEN ... ELSE value WHEN ... ELSE ... END
    
    Should be:
        CASE WHEN ... THEN value WHEN ... ELSE ... END
    
    The pattern "ELSE <value> WHEN" is invalid - ELSE must be last before END.
    """
    if not sql:
        return sql, False
    
    original_sql = sql
    
    # Pattern: ELSE <simple_value> WHEN -> just WHEN (removes the misplaced ELSE)
    # This handles cases where LLM put ELSE before more WHEN clauses
    pattern = r'\bELSE\s+(\d+(?:\.\d+)?|\'[^\']*\'|"[^"]*"|\w+)\s+(WHEN\b)'
    
    sql = re.sub(pattern, r'\2', sql, flags=re.IGNORECASE)
    
    return sql, sql != original_sql


def fix_unicode_alias_garbage(sql: str, match=None, error_msg: str = "") -> Tuple[str, bool]:
    """
    Remove non-ASCII garbage characters from SQL column aliases.
    
    Sometimes LLM generates aliases with Unicode garbage like:
        GSPLATFRM AS GSP প্ল্যাটFRM  (Bengali characters)
    
    This cleans up aliases to only contain valid ASCII characters.
    """
    if not sql:
        return sql, False
    
    original_sql = sql
    
    # Pattern: AS <alias_with_unicode>
    # Find AS followed by words that contain non-ASCII
    def clean_alias(m):
        prefix = m.group(1)  # "AS " or "AS\n" etc.
        alias = m.group(2)
        
        # Remove non-ASCII characters from the alias
        cleaned = ''.join(c for c in alias if ord(c) < 128)
        
        # If the alias is now empty or invalid, generate a placeholder
        if not cleaned or not cleaned[0].isalpha():
            cleaned = 'COLUMN_ALIAS'
        
        return f"{prefix}{cleaned}"
    
    # Match AS followed by identifier that may contain unicode
    # Using [^\s,)\n]+ to match the alias (anything until whitespace, comma, paren, or newline)
    pattern = r'(\bAS\s+)([^\s,)\n]+)'
    
    def selective_clean(m):
        prefix = m.group(1)
        alias = m.group(2)
        
        # Only clean if alias contains non-ASCII
        if any(ord(c) >= 128 for c in alias):
            cleaned = ''.join(c for c in alias if ord(c) < 128)
            if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == '_'):
                cleaned = 'CLEANED_ALIAS'
            return f"{prefix}{cleaned}"
        return m.group(0)
    
    sql = re.sub(pattern, selective_clean, sql, flags=re.IGNORECASE)
    
    return sql, sql != original_sql


def fix_missing_case_end(sql: str, match=None, error_msg: str = "") -> Tuple[str, bool]:
    """
    Fix missing END in nested CASE statements.
    
    Pattern like:
        CASE WHEN x THEN CASE WHEN y THEN a ELSE b ELSE c END
    Should be:
        CASE WHEN x THEN CASE WHEN y THEN a ELSE b END ELSE c END
    """
    if not sql:
        return sql, False
    
    original_sql = sql
    
    # Count CASE and END keywords
    case_count = len(re.findall(r'\bCASE\b', sql, re.IGNORECASE))
    end_count = len(re.findall(r'\bEND\b', sql, re.IGNORECASE))
    
    if case_count > end_count:
        # Pattern: ELSE <value> ELSE -> ELSE <value> END ELSE
        # This indicates a missing END between nested CASE
        pattern = r'(\bELSE\s+(?:\d+(?:\.\d+)?|\'[^\']*\'|"[^"]*"|\w+))\s+(ELSE\b)'
        sql = re.sub(pattern, r'\1 END \2', sql, flags=re.IGNORECASE)
    
    return sql, sql != original_sql


def fix_all_common_errors(sql: str) -> str:
    """
    Apply all generic fixes that don't require error context.
    Useful as a preprocessing step before validation.
    
    Args:
        sql: The SQL query to fix
        
    Returns:
        Fixed SQL with common issues corrected
    """
    # Fix CAST syntax
    sql, _ = fix_cast_missing_as(sql, None, "")
    
    # Fix parameterized NUMERIC
    sql, _ = fix_parameterized_numeric(sql, None, "")
    
    # Fix NULL comparisons
    sql, _ = fix_null_comparison(sql, None, "")
    
    # Fix trailing commas
    sql = re.sub(r',\s*(FROM|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY)\b', r' \1', sql, flags=re.IGNORECASE)
    
    # Fix SAP HANA-style IN() function syntax
    sql, _ = fix_in_function_syntax(sql, None, "")
    
    # Fix double ELSE in CASE statements
    sql, _ = fix_double_else_in_case(sql, None, "")
    
    # Fix missing END in nested CASE
    sql, _ = fix_missing_case_end(sql, None, "")
    
    # Fix Unicode garbage in aliases
    sql, _ = fix_unicode_alias_garbage(sql, None, "")
    
    return sql


# ============================================================================
# Utility Functions
# ============================================================================

def get_error_type(error_msg: str) -> str:
    """
    Classify the BigQuery error type for logging/metrics.
    """
    classifications = [
        (r'Syntax error', 'SYNTAX'),
        (r'not found|Unrecognized name', 'COLUMN_NOT_FOUND'),
        (r'neither grouped nor aggregated', 'AGGREGATION'),
        (r'Type mismatch|Cannot coerce|No matching signature', 'TYPE_ERROR'),
        (r'division by zero', 'ARITHMETIC'),
        (r'Table.*not found|Not found.*Table', 'TABLE_NOT_FOUND'),
        (r'ambiguous', 'AMBIGUOUS'),
        (r'Window function', 'WINDOW_FUNCTION'),
        (r'Invalid date|parse date', 'DATE_FORMAT'),
    ]
    
    for pattern, error_type in classifications:
        if re.search(pattern, error_msg, re.IGNORECASE):
            return error_type
    
    return 'UNKNOWN'


def get_structured_error_context(error_msg: str, sql: str) -> dict:
    """
    Parse error message into structured context for LLM.
    Use this when deterministic fix fails and LLM fallback is needed.
    """
    context = {
        'error_type': get_error_type(error_msg),
        'raw_error': error_msg,
        'suggested_fixes': [],
        'problematic_elements': []
    }
    
    # Extract specific elements from error
    col_match = re.search(r'(?:Name|column)\s+[\'"]?(\w+)[\'"]?', error_msg, re.IGNORECASE)
    if col_match:
        context['problematic_elements'].append(f"Column: {col_match.group(1)}")
    
    table_match = re.search(r'Table\s+[\'"]?(\S+)[\'"]?', error_msg, re.IGNORECASE)
    if table_match:
        context['problematic_elements'].append(f"Table: {table_match.group(1)}")
    
    # Add fix suggestions based on error type
    fix_suggestions = {
        'SYNTAX': [
            "Check for missing parentheses or commas",
            "Verify CAST syntax uses AS keyword",
            "Ensure no trailing commas before keywords"
        ],
        'COLUMN_NOT_FOUND': [
            "Check column spelling and case",
            "Verify table alias is correct",
            "Add NULL AS column_name if column doesn't exist"
        ],
        'AGGREGATION': [
            "Add column to GROUP BY clause",
            "Wrap column in aggregation function (SUM, MAX, ANY_VALUE)",
            "Remove column from SELECT if not needed"
        ],
        'TYPE_ERROR': [
            "Add explicit CAST to convert types",
            "Use SAFE_CAST for potentially failing conversions",
            "Check if comparing numbers to strings"
        ]
    }
    
    context['suggested_fixes'] = fix_suggestions.get(context['error_type'], ["Review SQL syntax"])
    
    return context
