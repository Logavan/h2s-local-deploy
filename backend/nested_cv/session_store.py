# nested_cv/session_store.py
# Thread-safe in-memory session and task storage

import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from .models import NestedSession, NestedTask, new_session_id, new_task_id


class NestedSessionStore:
    """
    Thread-safe in-memory store for nested CV sessions and tasks.
    In production this would be backed by Redis/DB.
    """

    def __init__(self, session_ttl_hours: int = 24, task_ttl_hours: int = 1):
        self._sessions: dict[str, NestedSession] = {}
        self._tasks: dict[str, NestedTask] = {}
        self._lock = threading.RLock()
        self._session_ttl = session_ttl_hours * 3600
        self._task_ttl = task_ttl_hours * 3600

    # ---- Session CRUD ----

    def create_session(self, target_dialect: str, output_format: str) -> NestedSession:
        with self._lock:
            session_id = new_session_id()
            session = NestedSession(
                session_id=session_id,
                target_dialect=target_dialect,
                phase="intro",
                revision=1,
                output_format=output_format,
            )
            self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> Optional[NestedSession]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                # Check expiry
                if datetime.utcnow() > datetime.fromisoformat(session.expires_at):
                    del self._sessions[session_id]
                    return None
            return session

    def update_session(self, session: NestedSession) -> NestedSession:
        with self._lock:
            session.revision += 1
            session.updated_at = datetime.utcnow().isoformat()
            self._sessions[session.session_id] = session
            return session

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    def cleanup_expired(self):
        """Remove expired sessions and tasks. Call periodically."""
        with self._lock:
            now = datetime.utcnow()
            expired_sessions = [
                sid for sid, s in self._sessions.items()
                if now > datetime.fromisoformat(s.expires_at)
            ]
            for sid in expired_sessions:
                del self._sessions[sid]

            expired_tasks = [
                tid for tid, t in self._tasks.items()
                if now > datetime.fromisoformat(t.updated_at) + timedelta(seconds=self._task_ttl)
            ]
            for tid in expired_tasks:
                del self._tasks[tid]

    # ---- Task CRUD ----

    def create_task(self, session_id: str) -> NestedTask:
        with self._lock:
            task_id = new_task_id()
            task = NestedTask(
                task_id=task_id,
                session_id=session_id,
                status="PENDING",
                progress=0,
                message="Task queued",
            )
            self._tasks[task_id] = task
            return task

    def get_task(self, task_id: str) -> Optional[NestedTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def update_task(self, task: NestedTask) -> NestedTask:
        with self._lock:
            task.updated_at = datetime.utcnow().isoformat()
            self._tasks[task.task_id] = task
            return task

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
            return False


# Global singleton
_store = NestedSessionStore()


def get_session_store() -> NestedSessionStore:
    return _store
