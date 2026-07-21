"""
PySpark DataFrame API Code Generator — Single CTE Converter.

Converts a SQL CTE (Common Table Expression) of the form:
    CTE_NAME AS ( SELECT ... FROM ... [JOIN...] [WHERE...] [GROUP BY...] ... )

into PySpark DataFrame API code using col(), lit(), expr(), F.when(), etc.

Supports:
    - SELECT / SELECT DISTINCT
    - FROM with alias
    - JOINs: INNER, LEFT, RIGHT, FULL, CROSS (multiple joins, multi-condition)
    - WHERE with AND/OR, IN, BETWEEN, LIKE, IS NULL, IS NOT NULL, comparison ops
    - GROUP BY with aggregate functions (COUNT, SUM, AVG, MIN, MAX, COUNT DISTINCT)
    - HAVING
    - ORDER BY (ASC/DESC)
    - LIMIT
    - UNION ALL / UNION
    - Window functions: ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD, SUM/AVG/etc. OVER
    - CASE WHEN ... THEN ... ELSE ... END
    - COALESCE, CAST, IFNULL/NVL, CONCAT, SUBSTRING, TRIM, UPPER, LOWER
    - String / Numeric / Boolean / NULL literals
    - Aliases with AS, quoted aliases

Assumes: Single CTE input, no subqueries.
"""

import re
import logging
from typing import List, Dict, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_cte_to_pyspark(cte_sql: str, base_tables: list = None) -> str:
    """
    Converts a SQL CTE to PySpark DataFrame API code.

    Args:
        cte_sql: SQL string of the form  `CTE_NAME AS ( SELECT ... )`

    Returns:
        PySpark DataFrame API code as a multi-line string.
    """
    if not cte_sql or not cte_sql.strip():
        return "# Error: Empty SQL input"

    try:
        # ── 1. Clean ──────────────────────────────────────────────────
        sql_clean = _strip_comments(cte_sql)

        # ── 2. Extract CTE name & inner SQL ───────────────────────────
        cte_name, inner_sql = _extract_cte(sql_clean)

        # ── 3. Handle UNION / UNION ALL ───────────────────────────────
        union_parts = _split_unions(inner_sql)
        if union_parts is not None:
            return _generate_union_code(cte_name, union_parts, base_tables=base_tables)

        # ── 4. Parse & generate ───────────────────────────────────────
        clauses = _parse_select(inner_sql)
        return _generate_dataframe_code(cte_name, clauses, base_tables=base_tables)

    except Exception as exc:
        logger.warning("CTE conversion failed: %s", exc)
        safe_sql = cte_sql.replace("'''", "\\'\\'\\'")
        return (
            f"# ⚠ Conversion failed: {exc}\n"
            f"# Falling back to spark.sql()\n"
            f"{_safe_name(cte_sql)} = spark.sql('''{safe_sql}''')"
        )


# ---------------------------------------------------------------------------
# CTE Extraction
# ---------------------------------------------------------------------------

def _strip_comments(sql: str) -> str:
    """Remove single-line (--) and block (/* */) comments."""
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.DOTALL)
    sql = re.sub(r'--.*?$', ' ', sql, flags=re.MULTILINE)
    return sql


def _extract_cte(sql: str) -> Tuple[str, str]:
    """
    Extract CTE name and the inner SELECT from  `NAME AS ( ... )`.
    Returns (cte_name, inner_sql).
    """
    m = re.search(r'(\w+)\s+AS\s*\(', sql, re.IGNORECASE)
    if not m:
        raise ValueError("Could not identify CTE (expected: NAME AS (SELECT ...))")
    cte_name = m.group(1)

    # Walk parens to find the matching close-paren
    start = sql.index('(', m.end() - 1)
    depth, idx = 1, start + 1
    while depth > 0 and idx < len(sql):
        ch = sql[idx]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        idx += 1

    if depth != 0:
        raise ValueError("Unbalanced parentheses in CTE")

    inner = sql[start + 1 : idx - 1].strip()
    if not inner:
        raise ValueError("Empty CTE body")
    return cte_name, inner


def _safe_name(sql: str) -> str:
    """Try to extract a CTE name for the fallback, or return 'df'."""
    m = re.search(r'(\w+)\s+AS\s*\(', sql, re.IGNORECASE)
    return m.group(1) if m else "df"


# ---------------------------------------------------------------------------
# UNION Handling
# ---------------------------------------------------------------------------

_UNION_RE = re.compile(r'\bUNION\s+ALL\b|\bUNION\b|\bINTERSECT\b|\bEXCEPT\b', re.IGNORECASE)


def _split_unions(sql: str) -> Optional[List[Tuple[str, str]]]:
    """
    If *sql* contains top-level UNION ALL / UNION / INTERSECT / EXCEPT,
    return a list of (operator, sql_fragment) tuples.
    The first element always has operator = '' (the first SELECT).
    Returns None if no set-operations found.
    """
    # Only split at top-level (depth == 0)
    parts: List[Tuple[str, str]] = []
    depth = 0
    last_end = 0

    for m in _UNION_RE.finditer(sql):
        # Check that match is at depth 0
        preceding = sql[last_end:m.start()]
        depth += preceding.count('(') - preceding.count(')')
        if depth == 0:
            fragment = sql[last_end:m.start()].strip()
            op = m.group(0).upper().strip()
            op_norm = re.sub(r'\s+', ' ', op)  # "UNION  ALL" → "UNION ALL"
            if parts:
                parts.append((op_norm, fragment))
            else:
                parts.append(('', fragment))
            last_end = m.end()

    if not parts:
        return None

    # Grab trailing fragment
    tail = sql[last_end:].strip()
    if tail:
        parts.append(('', tail))

    return parts if len(parts) >= 2 else None


def _generate_union_code(cte_name: str, parts: List[Tuple[str, str]], base_tables: list = None) -> str:
    """Generate PySpark code for UNION / UNION ALL / INTERSECT / EXCEPT."""
    lines = [_imports_comment()]
    df_names: List[str] = []

    part_idx = 0
    for op, fragment in parts:
        if not fragment.strip():
            continue
        var = f"{cte_name}_part{part_idx}"
        try:
            clauses = _parse_select(fragment)
            lines.append(_generate_dataframe_code(var, clauses, base_tables=base_tables))
        except Exception:
            safe = fragment.replace("'''", "\\'\\'\\'")
            lines.append(f"{var} = spark.sql('''{safe}''')")
        df_names.append((op, var))
        part_idx += 1

    # Chain them
    if not df_names:
        return f"# Error: no UNION parts found\n{cte_name} = spark.emptyDataFrame"

    chain = df_names[0][1]
    for op, name in df_names[1:]:
        if op == "UNION ALL":
            chain = f"{chain}.unionAll({name})"
        elif op == "UNION":
            chain = f"{chain}.union({name}).distinct()"
        elif op == "INTERSECT":
            chain = f"{chain}.intersect({name})"
        elif op == "EXCEPT":
            chain = f"{chain}.exceptAll({name})"
        else:
            chain = f"{chain}.unionAll({name})"

    lines.append(f"\n{cte_name} = {chain}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SQL Clause Parsing
# ---------------------------------------------------------------------------

def _parse_select(sql: str) -> Dict[str, Any]:
    """
    Parse a single SELECT statement into its constituent clauses.

    Returns dict with keys:
        distinct, select, from_table, from_alias,
        joins, where, group_by, having, order_by, limit
    """
    # Normalize whitespace (but keep case for identifiers)
    norm = " ".join(sql.split())

    result: Dict[str, Any] = {
        "distinct": False,
        "select": "",
        "from_table": "",
        "from_alias": "",
        "joins": [],
        "where": "",
        "group_by": "",
        "having": "",
        "order_by": "",
        "limit": "",
    }

    # ── DISTINCT ──────────────────────────────────────────────────────
    if re.match(r'SELECT\s+DISTINCT\s+', norm, re.IGNORECASE):
        result["distinct"] = True

    # ── SELECT ... FROM ───────────────────────────────────────────────
    # We must find the FROM that is at the top level, not inside an EXTRACT or SUBQUERY
    from_pos = _find_top_level_keyword(norm, "FROM")
    if from_pos == -1:
        raise ValueError("Could not find top-level FROM clause")
        
    select_part = norm[:from_pos].strip()
    sel_m = re.match(r'SELECT\s+(DISTINCT\s+)?(.*)', select_part, re.IGNORECASE | re.DOTALL)
    if not sel_m:
        raise ValueError("Could not parse SELECT clause")
    result["distinct"] = bool(sel_m.group(1))
    result["select"] = sel_m.group(2).strip()

    # Everything after FROM
    after_from = norm[from_pos + 5 :].strip()

    # ── FROM table alias ──────────────────────────────────────────────
    # A table name can be followed by an alias, or it could be a subquery (starts with '(')
    if after_from.startswith('('):
         from_m = None # Subquery case - will likely fail but handled by fallback
    else:
         from_m = re.match(r'([\w\.]+)(?:\s+(?:AS\s+)?(\w+))?(?=\s|$)', after_from, re.IGNORECASE)
        
    if not from_m:
        # If it's a subquery or complex join, we'll try to find the table name crudely 
        # but the actual join parsing later might handle it or we fallback to spark.sql
        table_match = re.match(r'([\w\.]+)', after_from, re.IGNORECASE)
        result["from_table"] = table_match.group(1) if table_match else "unknown_table"
        result["from_alias"] = ""
        rest = after_from[table_match.end():].strip() if table_match else ""
    else:
        result["from_table"] = from_m.group(1)
        alias_cand = from_m.group(2)
        if alias_cand and alias_cand.upper() in ('WHERE', 'GROUP', 'HAVING', 'ORDER', 'LIMIT', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'CROSS', 'FULL'):
            # Rejected alias candidate, it's actually part of the rest
            result["from_alias"] = ""
            # Re-match only the table name part
            table_only_m = re.match(r'[\w\.]+', after_from)
            rest = after_from[table_only_m.end():].strip()
        else:
            result["from_alias"] = alias_cand or ""
            rest = after_from[from_m.end():].strip()

    # Extract JOIN blocks first (they live between FROM and WHERE/GROUP/ORDER/LIMIT)
    result["joins"] = _parse_joins(rest)

    # Strip the JOIN portions out to parse remaining clauses
    rest_no_joins = _strip_join_blocks(rest)

    # WHERE
    where_m = re.search(r'\bWHERE\s+(.*?)(?=\s+GROUP\s+BY\b|\s+HAVING\b|\s+ORDER\s+BY\b|\s+LIMIT\b|$)',
                        rest_no_joins, re.IGNORECASE | re.DOTALL)
    if where_m:
        result["where"] = where_m.group(1).strip()

    # GROUP BY
    gb_m = re.search(r'\bGROUP\s+BY\s+(.*?)(?=\s+HAVING\b|\s+ORDER\s+BY\b|\s+LIMIT\b|$)',
                     rest_no_joins, re.IGNORECASE | re.DOTALL)
    if gb_m:
        result["group_by"] = gb_m.group(1).strip()

    # HAVING
    hav_m = re.search(r'\bHAVING\s+(.*?)(?=\s+ORDER\s+BY\b|\s+LIMIT\b|$)',
                      rest_no_joins, re.IGNORECASE | re.DOTALL)
    if hav_m:
        result["having"] = hav_m.group(1).strip()

    # ORDER BY
    ob_m = re.search(r'\bORDER\s+BY\s+(.*?)(?=\s+LIMIT\b|$)',
                     rest_no_joins, re.IGNORECASE | re.DOTALL)
    if ob_m:
        result["order_by"] = ob_m.group(1).strip()

    # LIMIT
    lim_m = re.search(r'\bLIMIT\s+(\d+)', rest_no_joins, re.IGNORECASE)
    if lim_m:
        result["limit"] = lim_m.group(1)

    return result


# ---------------------------------------------------------------------------
# JOIN Parsing
# ---------------------------------------------------------------------------

_JOIN_PATTERN = re.compile(
    r'(?P<type>LEFT\s+OUTER\s+|RIGHT\s+OUTER\s+|FULL\s+OUTER\s+|LEFT\s+|RIGHT\s+|INNER\s+|CROSS\s+|FULL\s+)?'
    r'JOIN\s+(?P<table>[\w\.]+)\s+(?:AS\s+)?(?P<alias>\w+)\s+ON\s+(?P<cond>.*?)(?=\s+(?:LEFT|RIGHT|INNER|CROSS|FULL)\s+JOIN\b|\s+(?:JOIN)\b|\s+WHERE\b|\s+GROUP\s+BY\b|\s+HAVING\b|\s+ORDER\s+BY\b|\s+LIMIT\b|$)',
    re.IGNORECASE | re.DOTALL
)


def _parse_joins(rest: str) -> List[Dict[str, str]]:
    """Extract all JOINs with type, table, alias, condition."""
    joins = []
    for m in _JOIN_PATTERN.finditer(rest):
        j_type = (m.group('type') or 'INNER').strip().upper()
        # Normalize: "LEFT OUTER" → "LEFT", etc.
        j_type = re.sub(r'\s+OUTER', '', j_type).strip()
        j_type = j_type if j_type else "INNER"
        joins.append({
            "type": j_type,
            "table": m.group('table').strip(),
            "alias": m.group('alias').strip(),
            "condition": m.group('cond').strip(),
        })
    return joins


def _strip_join_blocks(rest: str) -> str:
    """Remove JOIN blocks so remaining clause parsing doesn't get confused."""
    return _JOIN_PATTERN.sub('', rest).strip()


# ---------------------------------------------------------------------------
# Code Generation
# ---------------------------------------------------------------------------

def _imports_comment() -> str:
    return "# Imports: from pyspark.sql import functions as F; from pyspark.sql.functions import col, lit, expr"


def _generate_dataframe_code(var_name: str, c: Dict[str, Any], base_tables: list = None) -> str:
    """
    Generate PySpark DataFrame API code from parsed clauses.
    """
    lines = [_imports_comment(), f"{var_name} = ("]

    has_joins = len(c["joins"]) > 0
    table_alias = c["from_alias"]

    def _resolve_table(t_name: str) -> str:
        if base_tables and t_name in base_tables:
            return f'spark.table("{t_name}")'
        return t_name

    # ── FROM ──────────────────────────────────────────────────────────
    alias_part = f'.alias("{c["from_alias"]}")' if c["from_alias"] else ""
    lines.append(f'    {_resolve_table(c["from_table"])}{alias_part}')

    # ── JOINs ─────────────────────────────────────────────────────────
    for j in c["joins"]:
        raw_cond = j["condition"]
        # Handle 'ON 1=1' which means cross join or inner with no restrictions
        if raw_cond == "1=1" or raw_cond == "1 = 1":
            cond_code = "lit(1) == lit(1)"
        else:
            cond_code = _convert_join_condition(raw_cond, has_joins=has_joins, table_alias=table_alias)
        
        alias_part_j = f'.alias("{j["alias"]}")' if j["alias"] else ""
        lines.append(f'    .join(')
        lines.append(f'        {_resolve_table(j["table"])}{alias_part_j},')
        lines.append(f'        {cond_code},')
        lines.append(f'        how="{j["type"].lower()}"')
        lines.append(f'    )')

    # ── WHERE ─────────────────────────────────────────────────────────
    if c["where"]:
        where_code = _convert_filter_expr(c["where"], has_joins=has_joins, table_alias=table_alias)
        lines.append(f'    .filter({where_code})')

    # ── SELECT ────────────────────────────────────────────────────────
    columns = _split_top_level(c["select"], ",")

    has_group_by = bool(c["group_by"])
    agg_cols = []
    select_cols = []

    for col_str in columns:
        col_str = col_str.strip()
        if not col_str:
            continue
        if has_group_by and _is_aggregate(col_str):
            agg_cols.append(col_str)
        else:
            select_cols.append(col_str)

    if has_group_by:
        # GROUP BY columns
        gb_items = _split_top_level(c["group_by"], ",")
        
        gb_parts = []
        for g in gb_items:
            g = g.strip()
            if not g: continue
            # Handle numeric literal group by (e.g. GROUP BY 2) which is common in SQL but bad in DF API
            if re.match(r'^\d+$', g):
                # We don't have the SELECT list index easily, but we can try to look at SELECT columns
                # For safety, just use the string literal or a lit() if it's a constant
                gb_parts.append(f'lit({g})')
            else:
                gb_parts.append(_convert_expression(g, has_joins=has_joins, table_alias=table_alias))
                
        gb_code = ", ".join(gb_parts)
        lines.append(f'    .groupBy({gb_code})')

        # Aggregate functions
        if agg_cols:
            lines.append(f'    .agg(')
            for ac in agg_cols:
                lines.append(f'        {_convert_select_item(ac, has_joins=has_joins, table_alias=table_alias)},')
            lines.append(f'    )')
        else:
            pass
            
        # Optimization: Only add .select() if the output columns differ from purely the groupBy + agg result
        # or if specific aliasing/ordering is requested that isn't already handled.
        # But for reliability in these complex HANA migrations, we'll keep the select if any aliases exist.
        if columns:
             # Check if we actually need this select. 
             # For now, we'll keep it to ensure order and final aliasing matches SQL expectations.
             lines.append(f'    .select(')
             for sc in columns:
                 sc = sc.strip()
                 if not sc: continue
                 lines.append(f'        {_convert_select_item(sc, has_joins=has_joins, table_alias=table_alias)},')
             lines.append(f'    )')
    else:
        # Regular SELECT
        lines.append(f'    .select(')
        for sc in columns:
            sc = sc.strip()
            if not sc:
                continue
            lines.append(f'        {_convert_select_item(sc, has_joins=has_joins, table_alias=table_alias)},')
        lines.append(f'    )')

    # ── DISTINCT ──────────────────────────────────────────────────────
    if c["distinct"]:
        lines.append(f'    .distinct()')

    # ── HAVING ────────────────────────────────────────────────────────
    if c["having"]:
        having_code = _convert_filter_expr(c["having"], has_joins=has_joins, table_alias=table_alias)
        lines.append(f'    .filter({having_code})  # HAVING')

    # ── ORDER BY ──────────────────────────────────────────────────────
    if c["order_by"]:
        ob_items = _split_top_level(c["order_by"], ",")
        ob_parts = []
        for item in ob_items:
            item = item.strip()
            if not item:
                continue
            desc_match = re.search(r'\s+(DESC|ASC)\s*$', item, re.IGNORECASE)
            if desc_match:
                col_name = item[:desc_match.start()].strip()
                direction = desc_match.group(1).upper()
                if direction == "DESC":
                    ob_parts.append(f'{_convert_expression(col_name, has_joins=has_joins, table_alias=table_alias)}.desc()')
                else:
                    ob_parts.append(f'{_convert_expression(col_name, has_joins=has_joins, table_alias=table_alias)}.asc()')
            else:
                ob_parts.append(_convert_expression(item, has_joins=has_joins, table_alias=table_alias))
        lines.append(f'    .orderBy({", ".join(ob_parts)})')

    # ── LIMIT ─────────────────────────────────────────────────────────
    if c["limit"]:
        lines.append(f'    .limit({c["limit"]})')

    lines.append(f')')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Expression Conversion
# ---------------------------------------------------------------------------

def _convert_select_item(item: str, has_joins: bool = False, table_alias: str = "") -> str:
    """Convert a single SELECT column/expression to PySpark code."""
    item = item.strip()
    if not item:
        return ""

    # ── Extract alias ─────────────────────────────────────────────────
    alias = None
    alias_match = re.search(
        r'''\s+AS\s+(?:"([^"]+)"|`([^`]+)`|(\w+))\s*$''',
        item, re.IGNORECASE
    )
    if alias_match:
        alias = alias_match.group(1) or alias_match.group(2) or alias_match.group(3)
        item = item[:alias_match.start()].strip()

    code = _convert_expression(item, has_joins=has_joins, table_alias=table_alias)

    if alias:
        code = f'{code}.alias("{alias}")'

    return code


def _convert_expression(expr_str: str, has_joins: bool = False, table_alias: str = "") -> str:
    """Convert a SQL expression to PySpark expression code."""
    expr_str = expr_str.strip()
    if not expr_str:
        return 'lit(None)'

    # ── Wildcard ──────────────────────────────────────────────────────
    if expr_str == '*':
        return '"*"'

    # ── CASE WHEN ─────────────────────────────────────────────────────
    if re.match(r'CASE\b', expr_str, re.IGNORECASE):
        return _convert_case_when(expr_str, has_joins=has_joins, table_alias=table_alias)

    # ── CAST(expr AS type) ────────────────────────────────────────────
    cast_m = re.match(r'CAST\s*\(\s*(.+?)\s+AS\s+(\w+(?:\s*\([\d,\s]+\))?)\s*\)', expr_str, re.IGNORECASE)
    if cast_m:
        inner_expr = _convert_expression(cast_m.group(1), has_joins=has_joins, table_alias=table_alias)
        cast_type = cast_m.group(2).strip().lower()
        return f'{inner_expr}.cast("{cast_type}")'

    # ── COALESCE / IFNULL / NVL ───────────────────────────────────────
    coal_m = re.match(r'^(?:COALESCE|IFNULL|NVL)\s*\(\s*(.*)\s*\)$', expr_str, re.IGNORECASE | re.DOTALL)
    if coal_m:
        args = _split_top_level(coal_m.group(1), ",")
        converted = ", ".join(_convert_expression(a.strip(), has_joins=has_joins, table_alias=table_alias) for a in args)
        return f'F.coalesce({converted})'

    # ── Common SQL Functions → F.xxx() ────────────────────────────────
    # Negative sign handling (e.g. -g1c_gval)
    if expr_str.startswith('-') and not expr_str.startswith('- '):
        inner_val = expr_str[1:].strip()
        # If it's a simple column, negate it
        if re.match(r'^[\w\.]+$', inner_val):
            return f'-{_convert_expression(inner_val, has_joins=has_joins, table_alias=table_alias)}'

    func_result = _try_convert_function(expr_str, has_joins=has_joins, table_alias=table_alias)
    if func_result is not None:
        return func_result

    # ── Window function (anything with OVER) ──────────────────────────
    if re.search(r'\bOVER\s*\(', expr_str, re.IGNORECASE):
        return f'expr("{_escape_quotes(expr_str)}")'

    # ── Literal detection ─────────────────────────────────────────────
    # String literals (single-quoted)
    if re.match(r"^'.*'$", expr_str):
        return f'lit({expr_str})'

    # Numeric literals (int, float, negative, scientific)
    if re.match(r'^-?\d+(\.\d+)?([eE][+-]?\d+)?$', expr_str):
        return f'lit({expr_str})'

    # Boolean
    if expr_str.upper() in ('TRUE', 'FALSE'):
        return f'lit({expr_str.capitalize()})'

    # NULL
    if expr_str.upper() == 'NULL':
        return 'lit(None)'

    # ── Arithmetic / Complex expression (contains operators) ──────────
    if re.search(r'[\+\-\*/]', expr_str) and not re.match(r'^[\w\.]+$', expr_str):
        return f'expr("{_escape_quotes(expr_str)}")'

    # ── Simple column reference (a.col or col) ───────────────────────
    if re.match(r'^[\w\.]+$', expr_str):
        if not has_joins and '.' in expr_str:
             return f'col("{expr_str.split(".", 1)[1]}")'
        return f'col("{expr_str}")'

    # ── Fallback: use expr() ──────────────────────────────────────────
    return f'expr("{_escape_quotes(expr_str)}")'


def _try_convert_function(expr_str: str, has_joins: bool = False, table_alias: str = "") -> Optional[str]:
    """
    Try to convert known SQL aggregate/scalar functions to F.xxx().
    Returns None if not a recognized function pattern.
    """
    func_m = re.match(r'^(\w+)\s*\(\s*(.*)\s*\)$', expr_str, re.IGNORECASE | re.DOTALL)
    if not func_m:
        return None

    func_name = func_m.group(1).upper()
    inner = func_m.group(2).strip()

    # ── Aggregate functions ───────────────────────────────────────────
    agg_map = {
        'COUNT': 'F.count',
        'SUM': 'F.sum',
        'AVG': 'F.avg',
        'MIN': 'F.min',
        'MAX': 'F.max',
        'STDDEV': 'F.stddev',
        'VARIANCE': 'F.variance',
        'COLLECT_LIST': 'F.collect_list',
        'COLLECT_SET': 'F.collect_set',
        'FIRST': 'F.first',
        'LAST': 'F.last',
    }

    if func_name in agg_map:
        pyspark_fn = agg_map[func_name]
        # COUNT(*)
        if func_name == 'COUNT' and inner == '*':
            return f'{pyspark_fn}("*")'
        # COUNT(DISTINCT col)
        if func_name == 'COUNT' and re.match(r'DISTINCT\s+', inner, re.IGNORECASE):
            distinct_col = re.sub(r'^DISTINCT\s+', '', inner, flags=re.IGNORECASE).strip()
            return f'F.countDistinct("{distinct_col}")'
        # Normal aggregate
        return f'{pyspark_fn}({_convert_expression(inner, has_joins=has_joins, table_alias=table_alias)})'

    # ── String functions ──────────────────────────────────────────────
    str_map = {
        'UPPER': 'F.upper',
        'LOWER': 'F.lower',
        'TRIM': 'F.trim',
        'LTRIM': 'F.ltrim',
        'RTRIM': 'F.rtrim',
        'LEFT': 'F.left',
        'RIGHT': 'F.right',
        'LENGTH': 'F.length',
        'REVERSE': 'F.reverse',
        'ABS': 'F.abs',
        'CEIL': 'F.ceil',
        'CEILING': 'F.ceil',
        'FLOOR': 'F.floor',
        'ROUND': 'F.round',
        'SQRT': 'F.sqrt',
    }

    if func_name in str_map:
        pyspark_fn = str_map[func_name]
        args = _split_top_level(inner, ",")
        converted = ", ".join(_convert_expression(a.strip(), has_joins=has_joins, table_alias=table_alias) for a in args)
        return f'{pyspark_fn}({converted})'

    # ── CONCAT ────────────────────────────────────────────────────────
    if func_name == 'CONCAT':
        args = _split_top_level(inner, ",")
        converted = ", ".join(_convert_expression(a.strip(), has_joins=has_joins, table_alias=table_alias) for a in args)
        return f'F.concat({converted})'

    # ── CONCAT_WS ─────────────────────────────────────────────────────
    if func_name == 'CONCAT_WS':
        args = _split_top_level(inner, ",")
        converted = ", ".join(_convert_expression(a.strip(), has_joins=has_joins, table_alias=table_alias) for a in args)
        return f'F.concat_ws({converted})'

    # ── SUBSTRING ─────────────────────────────────────────────────────
    if func_name in ('SUBSTRING', 'SUBSTR'):
        args = _split_top_level(inner, ",")
        if len(args) >= 2:
            col_arg = _convert_expression(args[0].strip(), has_joins=has_joins, table_alias=table_alias)
            pos = args[1].strip()
            length = args[2].strip() if len(args) >= 3 else "2147483647"
            return f'F.substring({col_arg}, {pos}, {length})'

    # ── DATE functions ────────────────────────────────────────────────
    if func_name in ('YEAR', 'MONTH', 'DAY', 'DAYOFMONTH', 'DAYOFWEEK', 'DAYOFYEAR',
                      'HOUR', 'MINUTE', 'SECOND', 'QUARTER', 'WEEKOFYEAR'):
        pyspark_fn = f'F.{func_name.lower()}'
        return f'{pyspark_fn}({_convert_expression(inner, has_joins=has_joins, table_alias=table_alias)})'

    if func_name in ('DATE_FORMAT', 'TO_DATE', 'TO_TIMESTAMP', 'DATEDIFF', 'DATE_ADD',
                      'DATE_SUB', 'ADD_MONTHS', 'MONTHS_BETWEEN'):
        args = _split_top_level(inner, ",")
        converted = ", ".join(_convert_expression(a.strip(), has_joins=has_joins, table_alias=table_alias) for a in args)
        return f'F.{func_name.lower()}({converted})'

    if func_name == 'CURRENT_DATE':
        return 'F.current_date()'
    if func_name == 'CURRENT_TIMESTAMP':
        return 'F.current_timestamp()'

    # ── IF(cond, true_val, false_val) ─────────────────────────────────
    if func_name == 'IF':
        args = _split_top_level(inner, ",")
        if len(args) == 3:
            cond = _convert_filter_expr(args[0].strip(), has_joins=has_joins, table_alias=table_alias)
            t_val = _convert_expression(args[1].strip(), has_joins=has_joins, table_alias=table_alias)
            f_val = _convert_expression(args[2].strip(), has_joins=has_joins, table_alias=table_alias)
            return f'F.when({cond}, {t_val}).otherwise({f_val})'

    # ── NULLIF(a, b) ──────────────────────────────────────────────────
    if func_name == 'NULLIF':
        args = _split_top_level(inner, ",")
        if len(args) == 2:
            a = _convert_expression(args[0].strip(), has_joins=has_joins, table_alias=table_alias)
            b = _convert_expression(args[1].strip(), has_joins=has_joins, table_alias=table_alias)
            return f'F.when({a} == {b}, lit(None)).otherwise({a})'

    # ── DECODE → chained when ─────────────────────────────────────────
    if func_name == 'DECODE':
        return f'expr("{_escape_quotes(expr_str)}")'

    # Not a recognized function, but it IS a function call → use expr()
    return f'expr("{_escape_quotes(expr_str)}")'


def _convert_case_when(expr_str: str, has_joins: bool = False, table_alias: str = "") -> str:
    """
    Convert CASE WHEN ... THEN ... [WHEN ...] [ELSE ...] END to
    F.when(...).when(...).otherwise(...).
    """
    # Simple CASE: CASE expr WHEN val THEN ... (rewrite as searched CASE for uniformity)
    simple_m = re.match(r'CASE\s+(\w[\w\.]*)\s+WHEN\b', expr_str, re.IGNORECASE)
    if simple_m:
        # It's a simple CASE — use expr() for safety
        return f'expr("{_escape_quotes(expr_str)}")'

    # Searched CASE: CASE WHEN cond THEN result [WHEN ...] [ELSE ...] END
    # Extract WHEN ... THEN ... pairs
    when_pattern = re.compile(r'WHEN\s+(.+?)\s+THEN\s+(.+?)(?=\s+WHEN\b|\s+ELSE\b|\s+END\b)',
                              re.IGNORECASE | re.DOTALL)
    else_pattern = re.compile(r'ELSE\s+(.+?)\s+END', re.IGNORECASE | re.DOTALL)

    whens = when_pattern.findall(expr_str)
    else_m = else_pattern.search(expr_str)

    if not whens:
        return f'expr("{_escape_quotes(expr_str)}")'

    # Build F.when() chain
    try:
        parts = []
        for i, (cond, result) in enumerate(whens):
            cond_code = _convert_filter_expr(cond.strip(), has_joins=has_joins, table_alias=table_alias)
            result_code = _convert_expression(result.strip(), has_joins=has_joins, table_alias=table_alias)
            if i == 0:
                parts.append(f'F.when({cond_code}, {result_code})')
            else:
                parts.append(f'.when({cond_code}, {result_code})')

        if else_m:
            else_code = _convert_expression(else_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)
            parts.append(f'.otherwise({else_code})')

        return "".join(parts)
    except Exception:
        # Fallback if parsing nested logic failed
        return f'expr("{_escape_quotes(expr_str)}")'


# ---------------------------------------------------------------------------
# Filter / Condition Conversion
# ---------------------------------------------------------------------------

def _convert_filter_expr(condition: str, has_joins: bool = False, table_alias: str = "") -> str:
    """
    Convert a SQL WHERE/HAVING/ON condition to PySpark filter expression.
    Handles AND, OR, comparisons, IN, BETWEEN, LIKE, IS NULL, IS NOT NULL.
    """
    condition = condition.strip()
    if not condition:
        return 'lit(True)'

    # ── Split by top-level OR ─────────────────────────────────────────
    or_parts = _split_top_level_keyword(condition, 'OR')
    if len(or_parts) > 1:
        converted = " | ".join(f'({_convert_filter_expr(p, has_joins=has_joins, table_alias=table_alias)})' for p in or_parts)
        return converted

    # ── Split by top-level AND ────────────────────────────────────────
    and_parts = _split_top_level_keyword(condition, 'AND')
    if len(and_parts) > 1:
        converted = " & ".join(f'({_convert_filter_expr(p, has_joins=has_joins, table_alias=table_alias)})' for p in and_parts)
        return converted

    # ── Parenthesized expression ──────────────────────────────────────
    if condition.startswith('(') and condition.endswith(')'):
        inner = condition[1:-1].strip()
        # Verify it's a complete paren group
        depth = 0
        valid = True
        for ch in inner:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            if depth < 0:
                valid = False
                break
        if valid and depth == 0:
            return f'({_convert_filter_expr(inner, has_joins=has_joins, table_alias=table_alias)})'

    # ── NOT ───────────────────────────────────────────────────────────
    not_m = re.match(r'^NOT\s+(.+)$', condition, re.IGNORECASE)
    if not_m:
        return f'~({_convert_filter_expr(not_m.group(1), has_joins=has_joins, table_alias=table_alias)})'

    # ── IS NOT NULL ───────────────────────────────────────────────────
    isnull_m = re.match(r'^(.+?)\s+IS\s+NOT\s+NULL\s*$', condition, re.IGNORECASE)
    if isnull_m:
        return f'{_convert_expression(isnull_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)}.isNotNull()'

    # ── IS NULL ───────────────────────────────────────────────────────
    isnull_m = re.match(r'^(.+?)\s+IS\s+NULL\s*$', condition, re.IGNORECASE)
    if isnull_m:
        return f'{_convert_expression(isnull_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)}.isNull()'

    # ── NOT IN ────────────────────────────────────────────────────────
    notin_m = re.match(r'^(.+?)\s+NOT\s+IN\s*\(\s*(.+?)\s*\)$', condition, re.IGNORECASE | re.DOTALL)
    if notin_m:
        col_code = _convert_expression(notin_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)
        vals = _split_top_level(notin_m.group(2), ",")
        # Need to parse each literal or expression in the IN clause
        val_list = ", ".join(_convert_expression(v.strip(), has_joins=has_joins, table_alias=table_alias) for v in vals)
        return f'~({col_code}.isin({val_list}))'

    # ── IN ────────────────────────────────────────────────────────────
    in_m = re.match(r'^(.+?)\s+IN\s*\(\s*(.+?)\s*\)$', condition, re.IGNORECASE | re.DOTALL)
    if in_m:
        col_code = _convert_expression(in_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)
        vals = _split_top_level(in_m.group(2), ",")
        val_list = ", ".join(_convert_expression(v.strip(), has_joins=has_joins, table_alias=table_alias) for v in vals)
        return f'{col_code}.isin({val_list})'

    # ── NOT BETWEEN ───────────────────────────────────────────────────
    nbetween_m = re.match(r'^(.+?)\s+NOT\s+BETWEEN\s+(.+?)\s+AND\s+(.+?)$', condition, re.IGNORECASE)
    if nbetween_m:
        col_code = _convert_expression(nbetween_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)
        lo = _convert_expression(nbetween_m.group(2).strip(), has_joins=has_joins, table_alias=table_alias)
        hi = _convert_expression(nbetween_m.group(3).strip(), has_joins=has_joins, table_alias=table_alias)
        return f'~{col_code}.between({lo}, {hi})'

    # ── BETWEEN ───────────────────────────────────────────────────────
    between_m = re.match(r'^(.+?)\s+BETWEEN\s+(.+?)\s+AND\s+(.+?)$', condition, re.IGNORECASE)
    if between_m:
        col_code = _convert_expression(between_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)
        lo = _convert_expression(between_m.group(2).strip(), has_joins=has_joins, table_alias=table_alias)
        hi = _convert_expression(between_m.group(3).strip(), has_joins=has_joins, table_alias=table_alias)
        return f'{col_code}.between({lo}, {hi})'

    # ── NOT LIKE ──────────────────────────────────────────────────────
    nlike_m = re.match(r'^(.+?)\s+NOT\s+LIKE\s+(.+?)$', condition, re.IGNORECASE)
    if nlike_m:
        col_code = _convert_expression(nlike_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)
        pattern = nlike_m.group(2).strip()
        if pattern.startswith("'") and pattern.endswith("'"):
            # Optimization: check if we can use startswith, endswith, or contains
            pat_inner = pattern[1:-1]
            if pat_inner.endswith('%') and not pat_inner[:-1].count('%') and not pat_inner.count('_'):
                return f'~{col_code}.startswith("{pat_inner[:-1]}")'
            elif pat_inner.startswith('%') and not pat_inner[1:].count('%') and not pat_inner.count('_'):
                return f'~{col_code}.endswith("{pat_inner[1:]}")'
            elif pat_inner.startswith('%') and pat_inner.endswith('%') and not pat_inner[1:-1].count('%') and not pat_inner.count('_'):
                return f'~{col_code}.contains("{pat_inner[1:-1]}")'
        return f'~{col_code}.like({pattern})'

    # ── LIKE ──────────────────────────────────────────────────────────
    like_m = re.match(r'^(.+?)\s+LIKE\s+(.+?)$', condition, re.IGNORECASE)
    if like_m:
        col_code = _convert_expression(like_m.group(1).strip(), has_joins=has_joins, table_alias=table_alias)
        pattern = like_m.group(2).strip()
        if pattern.startswith("'") and pattern.endswith("'"):
            # Optimization: check if we can use startswith, endswith, or contains
            pat_inner = pattern[1:-1]
            if pat_inner.endswith('%') and not pat_inner[:-1].count('%') and not pat_inner.count('_'):
                return f'{col_code}.startswith("{pat_inner[:-1]}")'
            elif pat_inner.startswith('%') and not pat_inner[1:].count('%') and not pat_inner.count('_'):
                return f'{col_code}.endswith("{pat_inner[1:]}")'
            elif pat_inner.startswith('%') and pat_inner.endswith('%') and not pat_inner[1:-1].count('%') and not pat_inner.count('_'):
                return f'{col_code}.contains("{pat_inner[1:-1]}")'
        return f'{col_code}.like({pattern})'

    # ── Comparison operators: !=, <>, >=, <=, >, <, = ─────────────────
    # Find top-level comparison operator
    depth = 0
    in_str = False
    str_char = ''
    op = None
    op_start = -1
    op_end = -1
    
    ops = ['!=', '<>', '>=', '<=', '>', '<', '=']
    
    i = 0
    while i < len(condition):
        char = condition[i]
        
        # String literal parsing
        if char in ("'", '"'):
            if not in_str:
                in_str = True
                str_char = char
            elif str_char == char:
                # Check for escaped quote
                if i > 0 and condition[i - 1] == '\\':
                    pass
                else:
                    in_str = False
        
        if not in_str:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif depth == 0:
                # Check for operators
                for cand in ops:
                    if condition[i:].startswith(cand):
                        op = cand
                        op_start = i
                        op_end = i + len(cand)
                        break
                if op:
                    break  # Break out of while loop
        i += 1

    if op:
        left_str = condition[:op_start].strip()
        right_str = condition[op_end:].strip()
        left = _convert_expression(left_str, has_joins=has_joins, table_alias=table_alias)
        right = _convert_expression(right_str, has_joins=has_joins, table_alias=table_alias)
        py_op = {'=': '==', '!=': '!=', '<>': '!=', '>': '>', '<': '<', '>=': '>=', '<=': '<='}.get(op, '==')
        return f'{left} {py_op} {right}'

    # ── Fallback ──────────────────────────────────────────────────────
    return f'expr("{_escape_quotes(condition)}")'


def _convert_join_condition(condition: str, has_joins: bool = False, table_alias: str = "") -> str:
    """
    Convert a JOIN ON condition. Handles AND-separated equi-joins
    and more complex conditions.
    """
    condition = condition.strip()
    if not condition:
        return 'lit(True)'

    and_parts = _split_top_level_keyword(condition, 'AND')
    if len(and_parts) > 1:
        converted = [_convert_single_condition(p.strip(), has_joins=has_joins, table_alias=table_alias) for p in and_parts]
        return " & ".join(f'({c})' for c in converted)

    return _convert_single_condition(condition, has_joins=has_joins, table_alias=table_alias)


def _convert_single_condition(cond: str, has_joins: bool = False, table_alias: str = "") -> str:
    """Convert a single join/filter condition (e.g., a.id = b.id)."""
    cond = cond.strip()

    # Find top-level comparison operator
    depth = 0
    in_str = False
    str_char = ''
    op = None
    op_start = -1
    op_end = -1
    
    ops = ['!=', '<>', '>=', '<=', '>', '<', '=']
    
    i = 0
    while i < len(cond):
        char = cond[i]
        
        # String literal parsing
        if char in ("'", '"'):
            if not in_str:
                in_str = True
                str_char = char
            elif str_char == char:
                # Check for escaped quote
                if i > 0 and cond[i - 1] == '\\':
                    pass
                else:
                    in_str = False
        
        if not in_str:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif depth == 0:
                # Check for operators
                for cand in ops:
                    if cond[i:].startswith(cand):
                        op = cand
                        op_start = i
                        op_end = i + len(cand)
                        break
                if op:
                    break  # Break out of while loop
        i += 1

    if op:
        left_str = cond[:op_start].strip()
        right_str = cond[op_end:].strip()
        left = _convert_expression(left_str, has_joins=has_joins, table_alias=table_alias)
        right = _convert_expression(right_str, has_joins=has_joins, table_alias=table_alias)
        py_op = {'=': '==', '!=': '!=', '<>': '!=', '>': '>', '<': '<', '>=': '>=', '<=': '<='}.get(op, '==')
        return f'{left} {py_op} {right}'

    return f'expr("{_escape_quotes(cond)}")'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_top_level(text: str, delimiter: str) -> List[str]:
    """
    Split *text* by *delimiter* only when at parenthesis depth 0.
    Respects parentheses and single-quoted strings.
    """
    results = []
    current: List[str] = []
    depth = 0
    in_quote = False

    i = 0
    while i < len(text):
        ch = text[i]

        if ch == "'" and not in_quote:
            in_quote = True
            current.append(ch)
        elif ch == "'" and in_quote:
            in_quote = False
            current.append(ch)
        elif in_quote:
            current.append(ch)
        elif ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif depth == 0 and text[i:i+len(delimiter)] == delimiter:
            results.append("".join(current))
            current = []
            i += len(delimiter)
            continue
        else:
            current.append(ch)
        i += 1

    if current:
        results.append("".join(current))

    return results


def _split_top_level_keyword(text: str, keyword: str) -> List[str]:
    """
    Split *text* by a SQL keyword (AND / OR) at top level (depth 0),
    requiring word boundaries. Case-insensitive.
    """
    pattern = re.compile(r'\b' + keyword + r'\b', re.IGNORECASE)
    parts = []
    depth = 0
    in_quote = False
    last_end = 0

    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'" and (i == 0 or text[i-1] != '\\'):
            in_quote = not in_quote
        elif not in_quote:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif depth == 0:
                m = pattern.match(text, i)
                if m:
                    parts.append(text[last_end:i].strip())
                    last_end = m.end()
                    i = m.end()
                    continue
        i += 1

    tail = text[last_end:].strip()
    if tail:
        parts.append(tail)

    return parts if parts else [text]


def _is_aggregate(col_expr: str) -> bool:
    """Check if a column expression contains a SQL aggregate function."""
    agg_funcs = ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'STDDEV', 'VARIANCE',
                 'COLLECT_LIST', 'COLLECT_SET', 'FIRST', 'LAST')
    upper = col_expr.upper().strip()
    for fn in agg_funcs:
        if re.search(r'\b' + fn + r'\s*\(', upper):
            return True
    return False


def _escape_quotes(s: str) -> str:
    """Escape double quotes inside a string for embedding in an f-string."""
    return s.replace('\\', '\\\\').replace('"', '\\"')


def _find_top_level_keyword(sql: str, keyword: str) -> int:
    """Find the index of a keyword (e.g., 'FROM', 'AND') that is not inside parentheses."""
    depth = 0
    pattern = re.compile(rf'\b{keyword}\b', re.IGNORECASE)
    for m in pattern.finditer(sql):
        # Calculate depth at the start of the match
        snippet = sql[:m.start()]
        depth = snippet.count('(') - snippet.count(')')
        if depth == 0:
            return m.start()
    return -1
