# nested_cv/__init__.py
from .models import (
    NestedSession, NestedTask, CvArtifact, CvArtifact,
    DependencyLink, MappingEntry, Diagnostic, GraphSummary,
    SqlChunk, SourceReference, OutputColumn,
    NestedPhase, OutputFormat, EmissionMode, ObjectKind, ResolutionType,
    new_session_id, new_task_id, new_artifact_id,
)
from .session_store import NestedSessionStore, get_session_store
from .artifact_parser import parse_mapping_content
from .dependency_graph import build_graph, auto_resolve_links, DependencyGraph
from .mapping_service import MappingService
from .sql_composer import compose_sql, get_dialect_adapter
from .pyspark_composer import compose_pyspark
from .tasks import start_generation_task, get_generation_result

__all__ = [
    # models
    "NestedSession", "NestedTask", "CvArtifact", "DependencyLink",
    "MappingEntry", "Diagnostic", "GraphSummary",
    "SqlChunk", "SourceReference", "OutputColumn",
    "NestedPhase", "OutputFormat", "EmissionMode", "ObjectKind", "ResolutionType",
    "new_session_id", "new_task_id", "new_artifact_id",
    # store
    "NestedSessionStore", "get_session_store",
    # parser
    "parse_mapping_content",
    # graph
    "build_graph", "auto_resolve_links", "DependencyGraph",
    # mapping
    "MappingService",
    # composers
    "compose_sql", "get_dialect_adapter",
    "compose_pyspark",
    # tasks
    "start_generation_task", "get_generation_result",
]
