# nested_cv/sql_composer.py
# SQL composition: CTE namespacing, AST rewriting, view wrappers, dialect adapters

import re
import uuid
from typing import Optional

from .models import CvArtifact, MappingEntry, DependencyLink, Diagnostic, EmissionMode, ResolutionType
from .dependency_graph import DependencyGraph


# ---- Dialect adapters ----

class DialectAdapter:
    """Base class for target SQL dialect adapters."""

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def create_view(self, view_name: str, query: str, or_replace: bool = True) -> str:
        or_replace_kw = "OR REPLACE" if or_replace else ""
        return f"CREATE {or_replace_kw} VIEW {self.quote_identifier(view_name)} AS\n{query}"

    def cte_name(self, name: str) -> str:
        # Safe CTE alias
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        return f"cte_{safe}"

    def statement_terminator(self) -> str:
        return ";"


class BigQueryAdapter(DialectAdapter):
    def quote_identifier(self, name: str) -> str:
        return f"`{name}`"

    def create_view(self, view_name: str, query: str, or_replace: bool = True) -> str:
        or_replace_kw = "OR REPLACE" if or_replace else ""
        return f"CREATE {or_replace_kw} VIEW {self.quote_identifier(view_name)} AS\n{query}"


class SnowflakeAdapter(DialectAdapter):
    def quote_identifier(self, name: str) -> str:
        return f'"{name.upper()}"'

    def create_view(self, view_name: str, query: str, or_replace: bool = True) -> str:
        or_replace_kw = "OR REPLACE" if or_replace else ""
        return f"CREATE {or_replace_kw} VIEW {self.quote_identifier(view_name)} AS\n{query}"


class DatabricksAdapter(DialectAdapter):
    def quote_identifier(self, name: str) -> str:
        return f"`{name}`"

    def create_view(self, view_name: str, query: str, or_replace: bool = True) -> str:
        or_replace_kw = "OR REPLACE" if or_replace else ""
        return f"CREATE {or_replace_kw} VIEW {self.quote_identifier(view_name)} AS\n{query}"


class FabricAdapter(DialectAdapter):
    def quote_identifier(self, name: str) -> str:
        return f"[{name}]"

    def create_view(self, view_name: str, query: str, or_replace: bool = True) -> str:
        or_replace_kw = "OR REPLACE" if or_replace else ""
        return f"CREATE {or_replace_kw} VIEW {self.quote_identifier(view_name)} AS\n{query}"


class RedshiftAdapter(DialectAdapter):
    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def create_view(self, view_name: str, query: str, or_replace: bool = True) -> str:
        return f"CREATE VIEW {self.quote_identifier(view_name)} AS\n{query}"


class DatasphereAdapter(DialectAdapter):
    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def create_view(self, view_name: str, query: str, or_replace: bool = True) -> str:
        or_replace_kw = "OR REPLACE" if or_replace else ""
        return f"CREATE {or_replace_kw} VIEW {self.quote_identifier(view_name)} AS\n{query}"


DIALECT_ADAPTERS = {
    "bigquery": BigQueryAdapter(),
    "snowflake": SnowflakeAdapter(),
    "databricks": DatabricksAdapter(),
    "fabric": FabricAdapter(),
    "redshift": RedshiftAdapter(),
    "datasphere": DatasphereAdapter(),
}


def get_dialect_adapter(dialect: str) -> DialectAdapter:
    return DIALECT_ADAPTERS.get(dialect.lower(), DialectAdapter())


# ---- SQL Composer ----

def _namespace_cte(name: str, artifact_id: str) -> str:
    """Create a deterministic, collision-free CTE name."""
    short_id = artifact_id[:8]
    safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    return f"cte_{short_id}_{safe}"


def _namespace_dep_token(link_id: str) -> str:
    """Create a reserved dependency token for AST rewriting."""
    return f"__NCV_DEP_{link_id}__"


def _render_sql_body(
    artifact: CvArtifact,
    mappings: list[MappingEntry],
    dialect: DialectAdapter,
) -> str:
    """
    Render the SQL body for a single CV artifact using its mapping entries.
    Returns a pure SQL query (no CREATE VIEW wrapper).
    """
    if not artifact.sql_chunks:
        return "-- No SQL content available"

    # Build a simple mapping lookup: source_col -> target_col for each table
    table_mappings: dict[str, dict[str, str]] = {}
    for m in mappings:
        if m.artifact_id != artifact.artifact_id:
            continue
        if m.target_table not in table_mappings:
            table_mappings[m.target_table] = {}
        table_mappings[m.target_table][m.source_column_raw] = m.target_column

    # Combine all SQL chunks (simple concatenation for now)
    # In a full implementation this would parse the SQL AST
    chunks = []
    for chunk in artifact.sql_chunks:
        sql = chunk.sql_content
        # Simple identifier rewriting based on mapping
        for target_table, col_map in table_mappings.items():
            for src_col, tgt_col in col_map.items():
                # Replace source.column with target.column
                pattern = re.compile(rf'\b{re.escape(src_col)}\b', re.IGNORECASE)
                sql = pattern.sub(tgt_col, sql)
        chunks.append(sql)

    return "\n\n---\n\n".join(chunks)


def compose_sql(
    artifacts: list[CvArtifact],
    links: list[DependencyLink],
    mappings: list[MappingEntry],
    target_dialect: str,
) -> tuple[list[str], list[Diagnostic]]:
    """
    Compose SQL statements from CV artifacts.

    Returns (statements, diagnostics).
    Each statement is a complete CREATE VIEW ... AS SELECT...;
    """
    diagnostics: list[Diagnostic] = []
    dialect = get_dialect_adapter(target_dialect)

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

    # Get topological order (leaves first, roots last)
    topo_order = graph.topological_order()

    # Build link lookup for dependency resolution
    link_by_consumer: dict[str, DependencyLink] = {}
    for link in links:
        if link.resolution == ResolutionType.UPLOADED_CV.value:
            link_by_consumer[link.consumer_artifact_id] = link

    # Track emitted view names
    emitted_views: dict[str, str] = {}  # artifact_id -> view_name

    # Track namespace aliases for emitted CTEs
    cte_aliases: dict[str, str] = {}  # artifact_id -> cte_name

    statements: list[str] = []

    for aid in topo_order:
        artifact = graph.artifacts[aid]
        mode = artifact.emission_mode

        # Get producer CTEs needed by this artifact
        producers = graph.get_producers(aid)

        # Build WITH clause
        cte_defs: list[str] = []

        # Add producer CTEs
        for producer_aid in producers:
            producer_art = graph.artifacts.get(producer_aid)
            if not producer_art:
                continue

            if producer_art.emission_mode == EmissionMode.INLINE_CTE.value:
                # Inline as CTE
                producer_query = _render_sql_body(producer_art, mappings, dialect)
                cte_name = _namespace_cte(producer_art.cv_display_name, producer_aid)
                cte_aliases[producer_aid] = cte_name
                cte_defs.append(f"{cte_name} AS (\n{_indent(producer_query)}\n)")
            # If emit_view, consumer references it by view name instead

        # Add own internal CTEs if any
        for chunk in artifact.sql_chunks:
            if chunk.node_name:
                cte_name = _namespace_cte(chunk.node_name, artifact.artifact_id)
                cte_defs.append(f"{cte_name} AS (\n{_indent(chunk.sql_content)}\n)")

        # Build final SELECT (using mapped output columns)
        final_query = _render_sql_body(artifact, mappings, dialect)

        # Resolve dependency tokens
        for producer_aid in producers:
            producer_art = graph.artifacts.get(producer_aid)
            if not producer_art:
                continue
            if producer_art.emission_mode == EmissionMode.EMIT_VIEW.value:
                # Replace token with view name reference
                view_ref = dialect.quote_identifier(producer_art.target_view_name)
                final_query = final_query.replace(
                    _namespace_dep_token(producer_aid),
                    view_ref,
                )

        # Assemble final query
        if cte_defs:
            query = f"WITH\n{', '.join(cte_defs)}\n{final_query}"
        else:
            query = final_query

        if mode == EmissionMode.EMIT_VIEW.value:
            view_name = artifact.target_view_name
            emitted_views[aid] = view_name
            stmt = dialect.create_view(view_name, query)
            statements.append(stmt + "\n" + dialect.statement_terminator())
        else:
            # Inline - just add to consuming statement
            # If this is a root with inline_cte, emit it anyway as a view
            if not producers:
                view_name = artifact.target_view_name
                stmt = dialect.create_view(view_name, query)
                statements.append(stmt + "\n" + dialect.statement_terminator())
            # else: will be inlined into consumer

    return statements, diagnostics


def _indent(sql: str, spaces: int = 4) -> str:
    lines = sql.split("\n")
    indented = [(" " * spaces) + line if line.strip() else line for line in lines]
    return "\n".join(indented)
