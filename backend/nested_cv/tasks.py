# nested_cv/tasks.py
# Async generation task lifecycle

import threading
import os
import uuid
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

# Ensure .env is loaded so background threads see OUTPUT_DIR
load_dotenv()

from .models import NestedTask, NestedSession, OutputFormat
from .session_store import get_session_store
from .sql_composer import compose_sql
from .pyspark_composer import compose_pyspark


def _run_generation(task_id: str, session_id: str):
    """Background thread worker for SQL/PySpark generation."""
    store = get_session_store()
    task = store.get_task(task_id)
    session = store.get_session(session_id)

    if not task or not session:
        return

    # Mark IN_PROGRESS
    task.status = "IN_PROGRESS"
    task.progress = 5
    task.message = "Starting generation..."
    store.update_task(task)

    try:
        # Collect artifacts and mappings
        artifacts = list(session.artifacts.values())
        links = session.dependency_links
        mappings = session.global_mappings

        if not artifacts:
            task.status = "FAILED"
            task.message = "No artifacts in session"
            task.progress = 0
            store.update_task(task)
            return

        task.progress = 20
        task.message = "Building dependency graph..."
        store.update_task(task)

        task.progress = 40
        task.message = "Composing SQL..."
        store.update_task(task)

        # Compose based on output format
        if session.output_format == OutputFormat.PYSPARK.value:
            lines, diags = compose_pyspark(artifacts, links, mappings)
            task.diagnostics = diags
            content = "\n".join(lines) if lines else "# No content generated"
            ext = ".pyspark"
        else:
            stmts, diags = compose_sql(artifacts, links, mappings, session.target_dialect)
            task.diagnostics = diags
            content = "\n\n".join(stmts) if stmts else "-- No SQL generated"
            ext = ".sql"

        task.progress = 80
        task.message = "Writing output file..."
        store.update_task(task)

        # Write to output directory
        output_dir = os.environ.get("OUTPUT_DIR", "/tmp/h2s_output")
        os.makedirs(output_dir, exist_ok=True)

        filename = f"nested_cv_{task_id[:8]}{ext}"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        task.progress = 100
        task.status = "COMPLETED"
        task.message = "Generation complete"
        task.result_url = f"/api/nested/tasks/{task_id}/download"
        store.update_task(task)

    except Exception as e:
        task.status = "FAILED"
        task.message = f"Generation failed: {str(e)}"
        task.progress = 0
        store.update_task(task)


def start_generation_task(session: NestedSession) -> NestedTask:
    """Create and start an async generation task for a session."""
    store = get_session_store()

    # Check if there's already a running task for this session
    # (idempotency)
    # For now, just create a new one
    task = store.create_task(session.session_id)
    task.status = "PENDING"
    task.message = "Task queued"
    store.update_task(task)

    # Start background thread
    thread = threading.Thread(
        target=_run_generation,
        args=(task.task_id, session.session_id),
        daemon=True,
    )
    thread.start()

    return task


def get_generation_result(task: NestedTask) -> Optional[str]:
    """Get the file path for a completed task."""
    if task.status != "COMPLETED":
        return None

    output_dir = os.environ.get("OUTPUT_DIR", "/tmp/h2s_output")
    filename = f"nested_cv_{task.task_id[:8]}"
    # Check both .sql and .pyspark extensions
    for ext in (".pyspark", ".sql"):
        filepath = os.path.join(output_dir, filename + ext)
        if os.path.exists(filepath):
            return filepath
    return None
