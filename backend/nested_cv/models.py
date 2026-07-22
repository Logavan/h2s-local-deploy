# nested_cv/models.py
# Typed domain models for the Nested CV Flattener

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class NestedPhase(str, Enum):
    INTRO = "intro"
    UPLOADING = "uploading"
    RESOLVING = "resolving"
    MAPPING = "mapping"
    VALIDATING = "validating"
    GENERATING = "generating"
    DONE = "done"
    ERROR = "error"


class OutputFormat(str, Enum):
    SQL = "sql"
    PYSPARK = "pyspark"


class EmissionMode(str, Enum):
    INLINE_CTE = "inline_cte"
    EMIT_VIEW = "emit_view"


class ObjectKind(str, Enum):
    PHYSICAL_TABLE = "physical_table"
    CALCULATION_VIEW = "calculation_view"
    UNKNOWN = "unknown"


class ResolutionType(str, Enum):
    UPLOADED_CV = "uploaded_cv"
    PHYSICAL_TABLE = "physical_table"
    EXTERNAL_CV = "external_cv"


@dataclass
class OutputColumn:
    ordinal: int
    column_name: str
    data_type: Optional[str] = None
    nullable: Optional[bool] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class SourceReference:
    source_ref_raw: str
    source_ref_canonical: str
    object_kind: str  # ObjectKind value
    referenced_by_node: str
    required_columns_json: str  # JSON string

    def to_dict(self):
        return asdict(self)


@dataclass
class SqlChunk:
    chunk_id: str
    sql_content: str
    node_name: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class Diagnostic:
    level: str  # "error" | "warning" | "info"
    code: str
    message: str
    field: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class MappingEntry:
    source_ref_canonical: str
    source_column_raw: str
    target_table: str
    target_column: str
    artifact_id: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class DependencyLink:
    consumer_artifact_id: str
    source_ref_canonical: str
    resolution: str  # ResolutionType value
    producer_artifact_id: Optional[str] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class CvArtifact:
    artifact_id: str
    cv_canonical_id: Optional[str]
    cv_display_name: str
    file_name: str
    format_version: int
    emission_mode: str  # EmissionMode value
    target_view_name: str
    sql_chunks: list
    dependencies: list
    output_schema: list
    mapping_rows: list
    warnings: list

    def to_dict(self):
        return {
            "artifact_id": self.artifact_id,
            "cv_canonical_id": self.cv_canonical_id,
            "cv_display_name": self.cv_display_name,
            "file_name": self.file_name,
            "format_version": self.format_version,
            "emission_mode": self.emission_mode,
            "target_view_name": self.target_view_name,
            "sql_chunks": [c.to_dict() if hasattr(c, 'to_dict') else c for c in self.sql_chunks],
            "dependencies": [d.to_dict() if hasattr(d, 'to_dict') else d for d in self.dependencies],
            "output_schema": [o.to_dict() if hasattr(o, 'to_dict') else o for o in self.output_schema],
            "mapping_rows": [m.to_dict() if hasattr(m, 'to_dict') else m for m in self.mapping_rows],
            "warnings": [w.to_dict() if hasattr(w, 'to_dict') else w for w in self.warnings],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CvArtifact":
        return cls(
            artifact_id=d["artifact_id"],
            cv_canonical_id=d.get("cv_canonical_id"),
            cv_display_name=d["cv_display_name"],
            file_name=d["file_name"],
            format_version=d.get("format_version", 1),
            emission_mode=d.get("emission_mode", "inline_cte"),
            target_view_name=d.get("target_view_name", ""),
            sql_chunks=[SqlChunk(**c) if isinstance(c, dict) else c for c in d.get("sql_chunks", [])],
            dependencies=[SourceReference(**dep) if isinstance(dep, dict) else dep for dep in d.get("dependencies", [])],
            output_schema=[OutputColumn(**col) if isinstance(col, dict) else col for col in d.get("output_schema", [])],
            mapping_rows=[MappingEntry(**m) if isinstance(m, dict) else m for m in d.get("mapping_rows", [])],
            warnings=[Diagnostic(**w) if isinstance(w, dict) else w for w in d.get("warnings", [])],
        )


@dataclass
class GraphSummary:
    nodes: list  # [{artifact_id, display_name, emission_mode}]
    edges: list  # [{from, to}]
    roots: list  # artifact_ids
    topological_order: list  # artifact_ids
    has_cycles: bool

    def to_dict(self):
        return asdict(self)


@dataclass
class NestedSession:
    session_id: str
    target_dialect: str
    phase: str  # NestedPhase value
    revision: int
    output_format: str  # OutputFormat value
    artifacts: dict = field(default_factory=dict)  # artifact_id -> CvArtifact
    dependency_links: list = field(default_factory=list)  # DependencyLink[]
    global_mappings: list = field(default_factory=list)  # MappingEntry[]
    graph_summary: Optional[dict] = None
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.utcnow().isoformat()
        if not self.expires_at:
            from datetime import timedelta
            self.expires_at = (datetime.utcnow() + timedelta(hours=24)).isoformat()

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "target_dialect": self.target_dialect,
            "phase": self.phase,
            "revision": self.revision,
            "output_format": self.output_format,
            "artifacts": {k: v.to_dict() if hasattr(v, 'to_dict') else v for k, v in self.artifacts.items()},
            "dependency_links": [l.to_dict() if hasattr(l, 'to_dict') else l for l in self.dependency_links],
            "global_mappings": [m.to_dict() if hasattr(m, 'to_dict') else m for m in self.global_mappings],
            "graph_summary": self.graph_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NestedSession":
        artifacts = {}
        for k, v in d.get("artifacts", {}).items():
            if isinstance(v, dict):
                artifacts[k] = CvArtifact.from_dict(v)
            else:
                artifacts[k] = v
        return cls(
            session_id=d["session_id"],
            target_dialect=d["target_dialect"],
            phase=d.get("phase", "intro"),
            revision=d.get("revision", 1),
            output_format=d.get("output_format", "sql"),
            artifacts=artifacts,
            dependency_links=[DependencyLink(**l) if isinstance(l, dict) else l for l in d.get("dependency_links", [])],
            global_mappings=[MappingEntry(**m) if isinstance(m, dict) else m for m in d.get("global_mappings", [])],
            graph_summary=d.get("graph_summary"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            expires_at=d.get("expires_at", ""),
        )


@dataclass
class NestedTask:
    task_id: str
    session_id: str
    status: str  # "PENDING" | "IN_PROGRESS" | "COMPLETED" | "FAILED" | "CANCELLED"
    progress: int
    message: str
    result_url: Optional[str] = None
    diagnostics: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()
        if not self.updated_at:
            self.updated_at = datetime.utcnow().isoformat()

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result_url": self.result_url,
            "diagnostics": [d.to_dict() if hasattr(d, 'to_dict') else d for d in self.diagnostics],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def new_artifact_id() -> str:
    return str(uuid.uuid4())


def new_session_id() -> str:
    return str(uuid.uuid4())


def new_task_id() -> str:
    return str(uuid.uuid4())
