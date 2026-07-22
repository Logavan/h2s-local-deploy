# nested_cv/mapping_service.py
# Mapping deduplication, conflict detection, and aggregation

from typing import Optional
from .models import CvArtifact, MappingEntry, Diagnostic


class MappingService:
    """
    Aggregates and deduplicates mappings from multiple CV artifacts.
    Detects conflicts and provides consolidated mapping entries.
    """

    def __init__(self, artifacts: list[CvArtifact], global_mappings: list[MappingEntry]):
        self.artifacts = {a.artifact_id: a for a in artifacts}
        self.global_mappings = list(global_mappings)
        self._build_index()

    def _build_index(self):
        # key: (source_ref_canonical, source_column_raw)
        self._entry_index: dict[tuple, list[MappingEntry]] = {}
        for m in self.global_mappings:
            key = (m.source_ref_canonical, m.source_column_raw)
            if key not in self._entry_index:
                self._entry_index[key] = []
            self._entry_index[key].append(m)

    def add_artifact_mappings(self, artifact: CvArtifact):
        """Add all mapping rows from an artifact to the global set."""
        for m in artifact.mapping_rows:
            if m.artifact_id is None:
                m.artifact_id = artifact.artifact_id
            self.global_mappings.append(m)
            key = (m.source_ref_canonical, m.source_column_raw)
            if key not in self._entry_index:
                self._entry_index[key] = []
            self._entry_index[key].append(m)

    def get_consolidated_mappings(self) -> list[MappingEntry]:
        """Return deduplicated list of all mapping entries."""
        # For each unique key, if there are multiple with different targets, that's a conflict
        # For now, just deduplicate by keeping the first
        seen: set[tuple] = set()
        result: list[MappingEntry] = []
        for m in self.global_mappings:
            key = (m.source_ref_canonical, m.source_column_raw, m.target_table, m.target_column)
            if key not in seen:
                seen.add(key)
                result.append(m)
        return result

    def detect_conflicts(self) -> list[Diagnostic]:
        """Find mapping entries with the same source but different targets."""
        errors: list[Diagnostic] = []

        # Group by source (ref + column)
        by_source: dict[tuple, list[MappingEntry]] = {}
        for m in self.global_mappings:
            key = (m.source_ref_canonical, m.source_column_raw)
            if key not in by_source:
                by_source[key] = []
            by_source[key].append(m)

        for key, entries in by_source.items():
            if len(entries) < 2:
                continue
            # Check if all have the same target
            targets = {(e.target_table, e.target_column) for e in entries}
            if len(targets) > 1:
                source_ref, source_col = key
                t1 = entries[0]
                t2 = entries[1]
                errors.append(Diagnostic(
                    level="error",
                    code="CONFLICTING_MAPPING",
                    message=f"Conflicting mappings for source '{source_ref}.{source_col}': "
                            f"one maps to '{t1.target_table}.{t1.target_column}', "
                            f"another maps to '{t2.target_table}.{t2.target_column}'",
                    field=f"{source_ref}.{source_col}",
                ))

        return errors

    def detect_invalid_identifiers(self) -> list[Diagnostic]:
        """Check for empty or invalid target identifiers."""
        errors: list[Diagnostic] = []
        import re

        for m in self.global_mappings:
            if not m.target_table or not m.target_table.strip():
                errors.append(Diagnostic(
                    level="error",
                    code="EMPTY_TARGET_TABLE",
                    message=f"Empty target table for source column '{m.source_ref_canonical}.{m.source_column_raw}'",
                    field="target_table",
                ))
            if not m.target_column or not m.target_column.strip():
                errors.append(Diagnostic(
                    level="error",
                    code="EMPTY_TARGET_COLUMN",
                    message=f"Empty target column for source '{m.source_ref_canonical}.{m.source_column_raw}'",
                    field="target_column",
                ))
            # Check for invalid SQL identifier chars
            if m.target_table and not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', m.target_table):
                errors.append(Diagnostic(
                    level="warning",
                    code="INVALID_TARGET_TABLE_NAME",
                    message=f"Target table name '{m.target_table}' may contain invalid characters",
                    field="target_table",
                ))

        return errors

    def validate(self) -> tuple[list[Diagnostic], list[Diagnostic]]:
        """Full validation of consolidated mappings."""
        errors = self.detect_conflicts()
        errors.extend(self.detect_invalid_identifiers())
        warnings: list[Diagnostic] = []
        return errors, warnings
