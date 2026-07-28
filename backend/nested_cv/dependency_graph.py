# nested_cv/dependency_graph.py
# Dependency graph construction, topological sort, cycle detection

from typing import Optional
from .models import CvArtifact, DependencyLink, GraphSummary, Diagnostic, ResolutionType


class DependencyGraph:
    """
    Builds and validates a directed dependency graph from CV artifacts.
    Edges are producer -> consumer (child CV feeds parent CV).
    """

    def __init__(self, artifacts: list[CvArtifact], links: list[DependencyLink]):
        self.artifacts = {a.artifact_id: a for a in artifacts}
        self.links = links
        # Build adjacency: producer -> list of consumers
        self._adj: dict[str, list[str]] = {a.artifact_id: [] for a in artifacts}
        # Build reverse adjacency: consumer -> list of producers
        self._rev: dict[str, list[str]] = {a.artifact_id: [] for a in artifacts}
        self._build_graph()

    def _build_graph(self):
        for link in self.links:
            if link.resolution == ResolutionType.UPLOADED_CV.value and link.producer_artifact_id:
                producer = link.producer_artifact_id
                consumer = link.consumer_artifact_id
                if producer in self._adj and consumer in self._rev:
                    self._adj[producer].append(consumer)
                    self._rev[consumer].append(producer)

    def get_producers(self, artifact_id: str) -> list[str]:
        """Return artifact IDs that this artifact depends on (uses)."""
        return self._rev.get(artifact_id, [])

    def get_consumers(self, artifact_id: str) -> list[str]:
        """Return artifact IDs that depend on this artifact."""
        return self._adj.get(artifact_id, [])

    def find_roots(self) -> list[str]:
        """Find root nodes (no incoming edges)."""
        roots = []
        for aid in self._adj:
            if not self._rev.get(aid):
                roots.append(aid)
        return roots

    def topological_sort(self) -> list[str]:
        """
        Return artifacts in topological order (leaves first, roots last).
        Aliases: callers can also use `topological_order()`.
        Uses Kahn's algorithm.
        """
        # in-degree (number of producers)
        in_deg: dict[str, int] = {aid: len(self._rev.get(aid, [])) for aid in self._adj}
        # Start with leaves (in-degree 0)
        queue = [aid for aid, deg in in_deg.items() if deg == 0]
        result = []

        while queue:
            # Take a leaf
            leaf = queue.pop(0)
            result.append(leaf)
            # Reduce in-degree of consumers
            for consumer in self._adj.get(leaf, []):
                in_deg[consumer] -= 1
                if in_deg[consumer] == 0:
                    queue.append(consumer)

        if len(result) != len(self._adj):
            # Cycle detected - return what we have
            return result
        return result

    def detect_cycles(self) -> list[list[str]]:
        """Detect all cycles using DFS. Returns list of cycles."""
        cycles = []
        visited: set[str] = set()
        rec_stack: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for consumer in self._adj.get(node, []):
                if consumer not in visited:
                    if dfs(consumer):
                        return True
                elif consumer in rec_stack:
                    # Found cycle - extract it
                    cycle_start = path.index(consumer)
                    cycle = path[cycle_start:] + [consumer]
                    cycles.append(cycle)
                    return True

            path.pop()
            rec_stack.remove(node)
            return False

        for aid in self._adj:
            if aid not in visited:
                dfs(aid)

        return cycles

    def validate(self) -> tuple[list[Diagnostic], list[Diagnostic]]:
        """
        Validate the graph.
        Returns (errors, warnings).
        """
        errors: list[Diagnostic] = []
        warnings: list[Diagnostic] = []

        # Check for cycles
        cycles = self.detect_cycles()
        if cycles:
            for cycle in cycles:
                errors.append(Diagnostic(
                    level="error",
                    code="GRAPH_CYCLE",
                    message=f"Cycle detected: {' -> '.join(cycle[:5])}" + ("..." if len(cycle) > 5 else ""),
                ))

        # Check roots have valid emission modes
        roots = self.find_roots()
        for aid in roots:
            artifact = self.artifacts.get(aid)
            if not artifact:
                continue
            if artifact.emission_mode == "inline_cte":
                # Root cannot be inlined - it has no consumer to inline into
                warnings.append(Diagnostic(
                    level="warning",
                    code="ROOT_INLINE",
                    message=f"Root CV '{artifact.cv_display_name}' is set to inline CTE but has no consumers. It will be emitted as a view instead.",
                ))

        # Check for missing producers
        for link in self.links:
            if link.resolution == ResolutionType.UPLOADED_CV.value:
                if not link.producer_artifact_id:
                    errors.append(Diagnostic(
                        level="error",
                        code="MISSING_PRODUCER",
                        message=f"Unresolved uploaded CV reference: {link.source_ref_canonical}",
                    ))

        # Check for duplicate view names
        view_names: dict[str, str] = {}
        for aid, artifact in self.artifacts.items():
            if artifact.emission_mode == "emit_view":
                vname = artifact.target_view_name
                if vname in view_names:
                    errors.append(Diagnostic(
                        level="error",
                        code="DUPLICATE_VIEW_NAME",
                        message=f"Duplicate target view name '{vname}' across artifacts '{view_names[vname]}' and '{aid}'",
                    ))
                view_names[vname] = aid

        return errors, warnings

    def build_summary(self) -> GraphSummary:
        """Build a GraphSummary for the UI."""
        has_cycles = len(self.detect_cycles()) > 0
        return GraphSummary(
            nodes=[
                {
                    "artifact_id": aid,
                    "display_name": self.artifacts[aid].cv_display_name,
                    "emission_mode": self.artifacts[aid].emission_mode,
                }
                for aid in self._adj
            ],
            edges=[
                {"from": producer, "to": consumer}
                for producer, consumers in self._adj.items()
                for consumer in consumers
            ],
            roots=self.find_roots(),
            topological_order=self.topological_sort(),
            has_cycles=has_cycles,
        )


def build_graph(
    artifacts: list[CvArtifact],
    links: list[DependencyLink],
) -> DependencyGraph:
    return DependencyGraph(artifacts, links)


def auto_resolve_links(
    artifacts: list[CvArtifact],
) -> list[DependencyLink]:
    """
    Automatically resolve dependency links based on exact canonical ID match.
    Returns proposed links for confirmation.
    """
    links: list[DependencyLink] = []

    # Build a map of canonical_id -> artifact_id
    canonical_map: dict[str, str] = {}
    for a in artifacts:
        if a.cv_canonical_id:
            canonical_map[a.cv_canonical_id.upper()] = a.artifact_id

    for artifact in artifacts:
        for dep in artifact.dependencies:
            canonical = dep.source_ref_canonical.upper()
            if canonical in canonical_map and canonical_map[canonical] != artifact.artifact_id:
                links.append(DependencyLink(
                    consumer_artifact_id=artifact.artifact_id,
                    source_ref_canonical=dep.source_ref_canonical,
                    resolution=ResolutionType.UPLOADED_CV.value,
                    producer_artifact_id=canonical_map[canonical],
                ))

    return links
