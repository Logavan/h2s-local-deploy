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
from .orchestrator import (
    compose_chained_sql,
    compose_chained_pyspark,
    compose_for_session,
    make_cte_name,
)
from .tasks import start_generation_task

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
    # orchestrator (multi-artifact chaining)
    "compose_chained_sql", "compose_chained_pyspark", "compose_for_session",
    "make_cte_name",
    # tasks
    "start_generation_task",
]
