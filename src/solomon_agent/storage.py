"""Separate persistent conversation history; final request states are immutable."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class SessionStore:
    def __init__(
        self,
        data_dir: Path,
        voice_retention_days: int = 30,
        draft_retention_days: int | None = None,
    ):
        if voice_retention_days <= 0 or (
            draft_retention_days is not None and draft_retention_days <= 0
        ):
            raise ValueError("Retention days must be positive")
        root = Path(data_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "sessions.sqlite3"
        if self.db_path.is_symlink():
            raise ValueError("Session database must not be a symbolic link")
        self.retention = {"voice": voice_retention_days, "draft": draft_retention_days}
        with self._connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
                raise ValueError("Unsupported session database version")
            db.execute(
                "CREATE TABLE IF NOT EXISTS requests (request_id TEXT PRIMARY KEY, "
                "session_id TEXT NOT NULL, mode TEXT NOT NULL, user_text TEXT NOT NULL, "
                "assistant_text TEXT, status TEXT NOT NULL, created_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS session_history ON requests(session_id,mode,created_at)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS archived_sessions (session_id TEXT PRIMARY "
                "KEY, archived_at REAL NOT NULL)"
            )
            db.execute("PRAGMA user_version = 1")
            # A process restart cannot resume an in-flight model stream.
            db.execute("UPDATE requests SET status='interrupted' WHERE status='running'")
        self.prune()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def prune(self):
        with self._connect() as db:
            for mode, days in self.retention.items():
                if days is not None:
                    db.execute(
                        "DELETE FROM requests WHERE mode=? AND created_at<?",
                        (mode, time.time() - days * 86400),
                    )

    def has_request(self, request_id: str) -> bool:
        with self._connect() as db:
            return (
                db.execute("SELECT 1 FROM requests WHERE request_id=?", (request_id,)).fetchone()
                is not None
            )

    def is_session_archived(self, session_id: str) -> bool:
        with self._connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM archived_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                is not None
            )

    def start_request(self, request_id: str, session_id: str, mode: str, user_text: str):
        if mode not in self.retention or not request_id or not session_id:
            raise ValueError("Invalid request identity or mode")
        self.prune()
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM archived_sessions WHERE session_id=?", (session_id,)
            ).fetchone():
                raise ValueError("Session is archived; create a new session ID")
            db.execute(
                "INSERT INTO requests VALUES (?,?,?,?,NULL,'running',?)",
                (request_id, session_id, mode, user_text, time.time()),
            )

    def finish_request(
        self, request_id: str, assistant_text: str | None = None, status: str = "completed"
    ) -> bool:
        if status not in {"completed", "cancelled", "error"}:
            raise ValueError("Invalid final request status")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE requests SET status=?,assistant_text=? WHERE request_id=? AND "
                "status='running'",
                (status, assistant_text if status == "completed" else None, request_id),
            )
            return cursor.rowcount == 1

    def recent(self, session_id: str, mode: str, limit: int = 12) -> list[dict[str, str]]:
        self.prune()
        if mode not in self.retention:
            raise ValueError("Unknown conversation mode")
        if self.is_session_archived(session_id):
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT user_text,assistant_text FROM requests WHERE session_id=? AND "
                "mode=? AND status='completed' AND length(assistant_text)>0 ORDER BY "
                "created_at DESC,rowid DESC LIMIT ?",
                (session_id, mode, max(limit // 2, 0)),
            ).fetchall()
        messages = []
        for user, assistant in reversed(rows):
            messages.append({"role": "user", "content": user})
            if assistant:
                messages.append({"role": "assistant", "content": assistant})
        return messages

    def clear(self, session_id: str):
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO archived_sessions VALUES (?,?)", (session_id, time.time())
            )
            db.execute(
                "UPDATE requests SET status='cancelled',assistant_text=NULL WHERE "
                "session_id=? AND status='running'",
                (session_id,),
            )
