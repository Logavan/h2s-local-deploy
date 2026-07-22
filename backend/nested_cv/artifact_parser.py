# nested_cv/artifact_parser.py
# Parses mapping workbooks (v1 and v2 formats)

import json
import re
import uuid
from datetime import datetime
from typing import Optional

from .models import (
    CvArtifact, SqlChunk, SourceReference, OutputColumn,
    MappingEntry, Diagnostic, ObjectKind, EmissionMode,
    new_artifact_id,
)


def _safe_json_parse(content: str) -> Optional[dict]:
    """Try to parse content as JSON, return None if it fails."""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None


def _infer_object_kind(ref: str) -> str:
    """Try to deterministically infer whether a reference is a table or CV."""
    # Patterns that suggest a calculation view
    cv_indicators = ["_CV", "_cv", "CALC_VIEW", ".cv", "/cv/"]
    for indicator in cv_indicators:
        if indicator in ref.upper():
            return ObjectKind.CALCULATION_VIEW.value

    # Patterns that suggest a physical table
    table_indicators = ["_T", "_tbl", "TABLE", ".table", "/tab/"]
    for indicator in table_indicators:
        if indicator in ref.upper():
            return ObjectKind.PHYSICAL_TABLE.value

    return ObjectKind.UNKNOWN.value


def _canonicalize_ref(ref: str) -> str:
    """Normalize an identifier for comparison."""
    # Remove quotes, normalize case
    ref = ref.strip().strip('"').strip("'")
    return ref.upper()


def _sanitize_view_name(name: str) -> str:
    """Make a safe target view name."""
    # Remove invalid characters, replace spaces/special chars with underscore
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return name.upper() or "VIEW"


def parse_mapping_content(
    content: str,
    file_name: str,
    password: Optional[str] = None,
) -> CvArtifact:
    """
    Parse mapping workbook content and return a CvArtifact.
    Handles both:
    - v2 structured format (JSON with metadata sheets)
    - v1 legacy format (loose JSON/text)
    """
    artifact_id = new_artifact_id()
    warnings: list[Diagnostic] = []
    data = _safe_json_parse(content)

    if not data:
        # Legacy/v1 format - minimal info
        cv_name = file_name.replace(".xlsx", "").replace(".json", "").replace(".txt", "")
        display_name = _sanitize_view_name(cv_name)
        warnings.append(Diagnostic(
            level="warning",
            code="LEGACY_FORMAT",
            message=f"Legacy workbook format detected. Some metadata may be incomplete.",
        ))

        # Try to extract what we can
        dependencies = _extract_legacy_dependencies(data or content)
        output_schema = _extract_legacy_output_schema(data or content)
        sql_chunks = [SqlChunk(chunk_id=str(uuid.uuid4()), sql_content=str(content), node_name="legacy")]

        return CvArtifact(
            artifact_id=artifact_id,
            cv_canonical_id=None,
            cv_display_name=display_name,
            file_name=file_name,
            format_version=1,
            emission_mode=EmissionMode.INLINE_CTE.value,
            target_view_name=f"TGT_{display_name}",
            sql_chunks=sql_chunks,
            dependencies=dependencies,
            output_schema=output_schema,
            mapping_rows=[],
            warnings=warnings,
        )

    # v2 format
    format_version = data.get("schema_version", data.get("format_version", 1))
    manifest = data.get("artifact_manifest", {})

    cv_canonical_id = manifest.get("cv_canonical_id")
    cv_display_name = manifest.get("cv_display_name") or _sanitize_view_name(
        data.get("cv_name", file_name)
    )

    if format_version < 2:
        warnings.append(Diagnostic(
            level="warning",
            code="LEGACY_FORMAT",
            message=f"Format version {format_version} detected. Please re-export from latest converter for best results.",
        ))

    # Parse dependencies
    dependencies = []
    deps_data = data.get("dependencies", [])
    if isinstance(deps_data, list):
        for dep in deps_data:
            if isinstance(dep, dict):
                ref_raw = dep.get("source_ref_raw", "")
                dependencies.append(SourceReference(
                    source_ref_raw=ref_raw,
                    source_ref_canonical=dep.get("source_ref_canonical", _canonicalize_ref(ref_raw)),
                    object_kind=dep.get("object_kind", _infer_object_kind(ref_raw)),
                    referenced_by_node=dep.get("referenced_by_node", ""),
                    required_columns_json=json.dumps(dep.get("required_columns", [])),
                ))

    # Parse output schema
    output_schema = []
    schema_data = data.get("output_schema", [])
    if isinstance(schema_data, list):
        for i, col in enumerate(schema_data):
            if isinstance(col, dict):
                output_schema.append(OutputColumn(
                    ordinal=col.get("ordinal", i),
                    column_name=col.get("column_name", f"COL_{i}"),
                    data_type=col.get("data_type"),
                    nullable=col.get("nullable"),
                ))
            elif isinstance(col, str):
                output_schema.append(OutputColumn(ordinal=i, column_name=col))

    # Parse SQL chunks
    sql_chunks = []
    chunks_data = data.get("sql_chunks", data.get("sql_info", []))
    if isinstance(chunks_data, list):
        for i, chunk in enumerate(chunks_data):
            if isinstance(chunk, dict):
                sql_chunks.append(SqlChunk(
                    chunk_id=chunk.get("chunk_id", str(uuid.uuid4())),
                    sql_content=chunk.get("sql_content", chunk.get("sql", "")),
                    node_name=chunk.get("node_name"),
                ))
            elif isinstance(chunk, str):
                sql_chunks.append(SqlChunk(chunk_id=str(uuid.uuid4()), sql_content=chunk, node_name=None))

    # Parse mapping rows
    mapping_rows = []
    mapping_data = data.get("mapping_info", data.get("mappings", []))
    if isinstance(mapping_data, list):
        for m in mapping_data:
            if isinstance(m, dict):
                mapping_rows.append(MappingEntry(
                    source_ref_canonical=m.get("source_ref_canonical", ""),
                    source_column_raw=m.get("source_column", m.get("source_column_raw", "")),
                    target_table=m.get("target_table", ""),
                    target_column=m.get("target_column", ""),
                    artifact_id=artifact_id,
                ))

    target_view_name = manifest.get("default_output_view_name") or data.get("target_view_name")
    if not target_view_name:
        target_view_name = f"TGT_{cv_display_name}"

    return CvArtifact(
        artifact_id=artifact_id,
        cv_canonical_id=cv_canonical_id,
        cv_display_name=cv_display_name,
        file_name=file_name,
        format_version=int(format_version),
        emission_mode=data.get("emission_mode", EmissionMode.INLINE_CTE.value),
        target_view_name=target_view_name,
        sql_chunks=sql_chunks,
        dependencies=dependencies,
        output_schema=output_schema,
        mapping_rows=mapping_rows,
        warnings=warnings,
    )


def _extract_legacy_dependencies(content: str | dict) -> list[SourceReference]:
    """Try to extract dependency references from legacy format."""
    deps: list[SourceReference] = []

    if isinstance(content, dict):
        # Look for any table/view references in the data
        for key in ["dependencies", "tables", "views", "sources"]:
            if key in content:
                items = content[key]
                if isinstance(items, list):
                    for ref in items:
                        ref_str = str(ref) if not isinstance(ref, str) else ref
                        deps.append(SourceReference(
                            source_ref_raw=ref_str,
                            source_ref_canonical=_canonicalize_ref(ref_str),
                            object_kind=_infer_object_kind(ref_str),
                            referenced_by_node="legacy",
                            required_columns_json="[]",
                        ))
    elif isinstance(content, str):
        # Simple regex extraction for table/view patterns
        table_patterns = re.findall(r'[A-Z_]+\.[A-Z0-9_]+|[A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*', content)
        for ref in set(table_patterns):
            deps.append(SourceReference(
                source_ref_raw=ref,
                source_ref_canonical=_canonicalize_ref(ref),
                object_kind=_infer_object_kind(ref),
                referenced_by_node="legacy",
                required_columns_json="[]",
            ))

    return deps


def _extract_legacy_output_schema(content: str | dict) -> list[OutputColumn]:
    """Try to extract output columns from legacy format."""
    cols: list[OutputColumn] = []

    if isinstance(content, dict):
        for key in ["output_schema", "columns", "output_columns"]:
            if key in content:
                items = content[key]
                if isinstance(items, list):
                    for i, col in enumerate(items):
                        col_name = str(col) if not isinstance(col, str) else col
                        if isinstance(col, dict):
                            col_name = col.get("column_name", col.get("name", f"COL_{i}"))
                        cols.append(OutputColumn(ordinal=i, column_name=col_name))

    return cols
