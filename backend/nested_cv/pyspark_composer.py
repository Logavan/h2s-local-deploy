# nested_cv/pyspark_composer.py
# PySpark DataFrame composition

import re
import uuid

from .models import CvArtifact, MappingEntry, Diagnostic, EmissionMode, ResolutionType, DependencyLink
from .dependency_graph import DependencyGraph


def _namespace_df(name: str, artifact_id: str) -> str:
    """Create a deterministic, collision-free DataFrame name."""
    short_id = artifact_id[:8]
    safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    return f"df_{safe}_{short_id}"


def _render_pyspark_body(
    artifact: CvArtifact,
    mappings: list[MappingEntry],
) -> str:
    """
    Render PySpark DataFrame definition for a single CV artifact.
    Returns Python code (not a SQL string).
    """
    if not artifact.sql_chunks:
        return f"# No SQL content for {artifact.cv_display_name}"

    # Build column mapping lookup
    col_map: dict[str, dict[str, str]] = {}  # target_table -> {src_col -> tgt_col}
    for m in mappings:
        if m.artifact_id != artifact.artifact_id:
            continue
        if m.target_table not in col_map:
            col_map[m.target_table] = {}
        col_map[m.target_table][m.source_column_raw] = m.target_column

    # Build DataFrame definition
    lines: list[str] = []
    df_name = _namespace_df(artifact.cv_display_name, artifact.artifact_id)

    # Determine source table reference
    # For now, use the first mapped table or placeholder
    source_table = "source_schema.source_table"
    for m in mappings:
        if m.artifact_id == artifact.artifact_id and m.target_table:
            source_table = m.target_table
            break

    # Get output columns from schema or default to *
    output_cols = [c.column_name for c in artifact.output_schema]

    if output_cols:
        col_list = ", ".join(
            f'col("{sc}").alias("{tc}")' if sc != tc else f'"{sc}"'
            for sc, tc in
            [(m.source_column_raw, m.target_column) for m in mappings
             if m.artifact_id == artifact.artifact_id]
        )
        if not col_list:
            col_list = "*"
        lines.append(f'{df_name} = spark.table("{source_table}") \\')
        lines.append(f"    .select({col_list})")
    else:
        lines.append(f"{df_name} = spark.table(\"{source_table}\")")

    return "\n".join(lines)


def compose_pyspark(
    artifacts: list[CvArtifact],
    links: list[DependencyLink],
    mappings: list[MappingEntry],
) -> tuple[list[str], list[Diagnostic]]:
    """
    Compose PySpark DataFrame code from CV artifacts.

    Returns (code_lines, diagnostics).
    """
    diagnostics: list[Diagnostic] = []

    if not artifacts:
        return [], diagnostics

    # Build dependency graph
    graph = DependencyGraph(artifacts, links)

    # Validate
    errors, warnings = graph.validate()
    diagnostics.extend(errors)
    diagnostics.extend(warnings)

    if errors:
        return [], diagnostics

    # Topological order (leaves first)
    topo_order = graph.topological_order()

    lines: list[str] = [
        "# Auto-generated PySpark code - Nested CV Flattener",
        "# Generated at: " + __import__('datetime').datetime.utcnow().isoformat(),
        "",
        "from pyspark.sql import SparkSession, functions as F",
        "",
        "spark = SparkSession.builder \\",
        '    .appName("NestedCVFlattener") \\',
        '    .getOrCreate()',
        "",
        "# ─── DataFrame definitions ───",
    ]

    # Track emitted DataFrames
    df_names: dict[str, str] = {}  # artifact_id -> df_name

    for aid in topo_order:
        artifact = graph.artifacts[aid]
        mode = artifact.emission_mode

        # Get producers
        producers = graph.get_producers(aid)

        # Get producer DataFrames
        prod_dfs: list[str] = []
        for producer_aid in producers:
            producer_art = graph.artifacts.get(producer_aid)
            if not producer_art:
                continue
            if producer_art.emission_mode == EmissionMode.INLINE_CTE.value:
                prod_dfs.append(df_names.get(producer_aid, ""))
            # If emit_view, consumer uses spark.table(view_name) later

        df_name = _namespace_df(artifact.cv_display_name, artifact.artifact_id)
        df_names[aid] = df_name

        # Build DataFrame body
        body_lines = _render_pyspark_body(artifact, mappings).split("\n")
        for line in body_lines:
            lines.append(line)

        if mode == EmissionMode.EMIT_VIEW.value:
            # Register as temp view
            lines.append(f"{df_name}.createOrReplaceTempView(\"{artifact.target_view_name}\")")
            lines.append("")

        lines.append("")

    # Final output (root DataFrames)
    roots = graph.find_roots()
    if roots:
        root_art = graph.artifacts.get(roots[0])
        if root_art:
            lines.append(f"# ─── Output ───")
            lines.append(f"# Final output DataFrame: {df_names.get(roots[0], 'unknown')}")
            lines.append(f'print("Generated {len(roots)} root view(s)")')

    return lines, diagnostics
