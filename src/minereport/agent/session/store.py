"""SQLite store for sessions, tasks, requests and tool-effect records.

Design rules enforced here:
* every write is a **short** transaction (BEGIN IMMEDIATE) - never held across a
  network call;
* step claiming uses a conditional UPDATE plus a lease, so two processes cannot
  both run the same step; the in-process asyncio lock only protects one process;
* conflicts surface as typed errors instead of silently overwriting state.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ...common import config

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_LEASE_SECONDS = 120
RESULT_INLINE_LIMIT = 256 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id       TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    intent        TEXT NOT NULL,
    status        TEXT NOT NULL,
    suspended     INTEGER NOT NULL DEFAULT 0,
    slots         TEXT NOT NULL DEFAULT '{}',
    missing_slots TEXT NOT NULL DEFAULT '[]',
    slot_sources  TEXT NOT NULL DEFAULT '{}',
    description   TEXT NOT NULL DEFAULT '',
    error         TEXT,
    result_json   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id, status);
CREATE TABLE IF NOT EXISTS requests (
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    reply      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, request_id)
);
CREATE TABLE IF NOT EXISTS effects (
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    tool_name        TEXT NOT NULL,
    args_hash        TEXT NOT NULL,
    session_id       TEXT,
    request_id       TEXT,
    tool_call_id     TEXT,
    status           TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    result_json      TEXT,
    result_ref       TEXT,
    result_digest    TEXT,
    error            TEXT,
    lease_owner      TEXT,
    lease_expires_at TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (task_id, step_id, tool_name, args_hash)
);
"""


class SessionBusyError(RuntimeError):
    """Another writer holds this session (or this step is already running)."""


class StoreBusyError(SessionBusyError):
    """SQLite refused to start a write transaction within the retry budget."""


class OwnershipError(PermissionError):
    """The requested session does not belong to this user."""


def now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def state_db_path() -> Path:
    """LangGraph checkpointer database (thread state only)."""

    override = os.environ.get("MINEREPORT_STATE_DB")
    if override:
        return Path(override).expanduser()
    return config.DATA_DIR / "sessions" / "agent_state.sqlite"


def business_db_path() -> Path:
    """Business database: sessions / tasks / requests / effects.

    Kept in its own file so the checkpointer's writes can never contend with the
    business store's writes (that contention produced ``database is locked``).
    """

    override = os.environ.get("MINEREPORT_BUSINESS_DB")
    if override:
        return Path(override).expanduser()
    return config.DATA_DIR / "sessions" / "agent_business.sqlite"


def effects_dir() -> Path:
    path = business_db_path().parent / "effects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def canonical_args(args: Any) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def args_hash(args: Any) -> str:
    return hashlib.sha256(canonical_args(args).encode("utf-8")).hexdigest()


def effect_key(task_id: str, step_id: str, tool_name: str, arguments: Any) -> tuple[str, str, str, str]:
    return (task_id, step_id, tool_name, args_hash(arguments))


class Store:
    """Synchronous SQLite access; async callers wrap calls in ``asyncio.to_thread``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else business_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()
        self._migrate_from_legacy()

    def _migrate_from_legacy(self) -> None:
        """One-time, non-destructive copy of business tables from the old
        combined database (``agent_state.sqlite``) into the new business file."""

        legacy = state_db_path()
        if not legacy.exists() or legacy == self.path:
            return
        with self._read() as probe:
            already = probe.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            already += probe.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if already:
            return
        try:
            legacy_conn = sqlite3.connect(str(legacy))
            found = legacy_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone()
            if not found:
                legacy_conn.close()
                return
            for table in ("sessions", "tasks", "requests", "effects"):
                cursor = legacy_conn.execute(f"SELECT * FROM {table}")
                if cursor.description is None:
                    continue
                columns = [item[0] for item in cursor.description]
                rows = cursor.fetchall()
                if not rows:
                    continue
                placeholders = ",".join("?" for _ in columns)
                with self._write() as target:
                    target.executemany(
                        f"INSERT OR IGNORE INTO {table}({','.join(columns)}) VALUES ({placeholders})",
                        rows,
                    )
            legacy_conn.close()
            migrated = True
        except Exception:  # noqa: BLE001 - migration must never block startup
            migrated = False
        if migrated:
            print(
                f"[存储] 已从旧库 {legacy.name} 迁移会话/任务数据到 {self.path.name}（旧库保留不动）",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------ basics
    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _init_schema(self) -> None:
        # executescript manages its own transaction, so it must not be nested
        # inside BEGIN IMMEDIATE.
        self._connect().executescript(SCHEMA)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Cursor]:
        """Short write transaction: BEGIN IMMEDIATE ... COMMIT."""

        conn = self._connect()
        cursor = conn.cursor()
        # Short, retried write transaction. The retry covers another process (or
        # the checkpointer) holding the write lock for a moment; the budget stays
        # small so callers fail fast with a typed error instead of hanging.
        last_error: Exception | None = None
        for attempt, delay in enumerate((0.0, 0.2, 0.5, 1.0, 2.0)):
            if delay:
                time.sleep(delay)
            try:
                cursor.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc):
                    cursor.close()
                    raise
                last_error = exc
        else:
            cursor.close()
            raise StoreBusyError(
                f"business database is busy after retries: {last_error}"
            ) from last_error
        try:
            yield cursor
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            cursor.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Cursor]:
        cursor = self._connect().cursor()
        try:
            yield cursor
        finally:
            cursor.close()

    # ----------------------------------------------------------------- sessions
    def ensure_session(self, session_id: str, user_id: str) -> dict[str, Any]:
        with self._write() as cursor:
            cursor.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO sessions(session_id, user_id, created_at, updated_at) VALUES (?,?,?,?)",
                    (session_id, user_id, now(), now()),
                )
            elif row["user_id"] != user_id:
                raise OwnershipError(
                    f"session {session_id} belongs to another user; refusing to attach"
                )
            else:
                cursor.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now(), session_id))
        return self.get_session(session_id)  # type: ignore[return-value]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._read() as cursor:
            cursor.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        with self._read() as cursor:
            cursor.execute(
                "SELECT * FROM sessions WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def assert_owner(self, session_id: str, user_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise OwnershipError(f"unknown session {session_id}")
        if session["user_id"] != user_id:
            raise OwnershipError(
                f"session {session_id} does not belong to user {user_id}"
            )
        return session

    # -------------------------------------------------------------------- tasks
    def create_task(
        self,
        *,
        session_id: str,
        user_id: str,
        intent: str,
        slots: dict[str, Any],
        missing: list[str],
        description: str = "",
        status: str = "created",
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        with self._write() as cursor:
            cursor.execute(
                "INSERT INTO tasks(task_id, session_id, user_id, intent, status, suspended,"
                " slots, missing_slots, slot_sources, description, created_at, updated_at)"
                " VALUES (?,?,?,?,?,0,?,?,'{}',?,?,?)",
                (
                    task_id, session_id, user_id, intent, status,
                    json.dumps(slots, ensure_ascii=False),
                    json.dumps(missing, ensure_ascii=False),
                    description, now(), now(),
                ),
            )
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._read() as cursor:
            cursor.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,))
            row = cursor.fetchone()
        return self._task_row(row) if row else None

    def list_tasks(self, session_id: str, *, user_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks WHERE session_id=?"
        params: list[Any] = [session_id]
        if user_id:
            query += " AND user_id=?"
            params.append(user_id)
        query += " ORDER BY created_at DESC"
        with self._read() as cursor:
            cursor.execute(query, params)
            return [self._task_row(row) for row in cursor.fetchall()]

    def list_resumable(self, session_id: str, user_id: str) -> list[dict[str, Any]]:
        with self._read() as cursor:
            cursor.execute(
                "SELECT * FROM tasks WHERE session_id=? AND user_id=?"
                " AND status NOT IN ('completed','cancelled','failed')"
                " ORDER BY created_at DESC",
                (session_id, user_id),
            )
            return [self._task_row(row) for row in cursor.fetchall()]

    def update_task(
        self,
        task_id: str,
        *,
        intent: str | None = None,
        status: str | None = None,
        suspended: bool | None = None,
        slots: dict[str, Any] | None = None,
        missing: list[str] | None = None,
        slot_sources: dict[str, str] | None = None,
        error: str | None = None,
        result: Any | None = None,
    ) -> dict[str, Any]:
        sets: list[str] = ["updated_at=?"]
        params: list[Any] = [now()]
        if intent is not None:
            sets.append("intent=?"); params.append(intent)
        if status is not None:
            sets.append("status=?"); params.append(status)
        if suspended is not None:
            sets.append("suspended=?"); params.append(1 if suspended else 0)
        if slots is not None:
            sets.append("slots=?"); params.append(json.dumps(slots, ensure_ascii=False))
        if missing is not None:
            sets.append("missing_slots=?"); params.append(json.dumps(missing, ensure_ascii=False))
        if slot_sources is not None:
            sets.append("slot_sources=?"); params.append(json.dumps(slot_sources, ensure_ascii=False))
        if error is not None:
            sets.append("error=?"); params.append(error)
        if result is not None:
            sets.append("result_json=?"); params.append(json.dumps(result, ensure_ascii=False))
        params.append(task_id)
        with self._write() as cursor:
            cursor.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=?", params)
            if cursor.rowcount == 0:
                raise KeyError(f"unknown task {task_id}")
        return self.get_task(task_id)  # type: ignore[return-value]

    @staticmethod
    def _task_row(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        task["slots"] = json.loads(task.get("slots") or "{}")
        task["missing_slots"] = json.loads(task.get("missing_slots") or "[]")
        task["slot_sources"] = json.loads(task.get("slot_sources") or "{}")
        task["suspended"] = bool(task.get("suspended"))
        if task.get("result_json"):
            task["result"] = json.loads(task["result_json"])
        return task

    # ----------------------------------------------------------------- requests
    def get_request(self, session_id: str, request_id: str) -> dict[str, Any] | None:
        with self._read() as cursor:
            cursor.execute(
                "SELECT * FROM requests WHERE session_id=? AND request_id=?",
                (session_id, request_id),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def begin_request(self, session_id: str, request_id: str, task_id: str) -> tuple[bool, dict[str, Any]]:
        """Return ``(is_new, record)``; the caller must not re-execute a replay."""

        with self._write() as cursor:
            cursor.execute(
                "SELECT * FROM requests WHERE session_id=? AND request_id=?",
                (session_id, request_id),
            )
            row = cursor.fetchone()
            if row is not None:
                return False, dict(row)
            cursor.execute(
                "INSERT INTO requests(session_id, request_id, task_id, status, created_at, updated_at)"
                " VALUES (?,?,?,'running',?,?)",
                (session_id, request_id, task_id, now(), now()),
            )
        return True, {"session_id": session_id, "request_id": request_id, "task_id": task_id, "status": "running"}

    def finish_request(
        self, session_id: str, request_id: str, *, status: str, task_id: str | None = None, reply: str | None = None
    ) -> None:
        sets = ["status=?", "updated_at=?"]
        params: list[Any] = [status, now()]
        if task_id is not None:
            sets.append("task_id=?"); params.append(task_id)
        if reply is not None:
            sets.append("reply=?"); params.append(reply)
        params.extend([session_id, request_id])
        with self._write() as cursor:
            cursor.execute(f"UPDATE requests SET {', '.join(sets)} WHERE session_id=? AND request_id=?", params)

    # ------------------------------------------------------------------ effects
    def get_step(self, key: tuple[str, str, str, str]) -> dict[str, Any] | None:
        with self._read() as cursor:
            cursor.execute(
                "SELECT * FROM effects WHERE task_id=? AND step_id=? AND tool_name=? AND args_hash=?",
                key,
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def claim_step(
        self,
        key: tuple[str, str, str, str],
        *,
        session_id: str,
        request_id: str,
        tool_call_id: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        allow_retry: bool = True,
        max_attempts: int = 2,
    ) -> tuple[bool, dict[str, Any]]:
        """Claim a step for execution. ``(claimed, record)``.

        ``claimed=False`` means somebody else is running it (or a terminal record
        exists that must not be re-run).
        """

        task_id, step_id, tool_name, _ = key
        expires = (datetime.now(CN_TZ) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        existing = self.get_step(key)
        if existing is None:
            with self._write() as cursor:
                cursor.execute(
                    "INSERT OR IGNORE INTO effects(task_id, step_id, tool_name, args_hash, session_id,"
                    " request_id, tool_call_id, status, attempts, lease_owner, lease_expires_at,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?,'running',1,?,?,?,?)",
                    (*key, session_id, request_id, tool_call_id, tool_call_id, expires, now(), now()),
                )
                if cursor.rowcount == 1:
                    return True, self.get_step(key)  # type: ignore[return-value]
            existing = self.get_step(key)

        assert existing is not None
        if existing["status"] == "completed":
            return False, existing
        if existing["status"] == "running" and (existing.get("lease_expires_at") or "") > now():
            return False, existing
        if existing["status"] == "failed" and not allow_retry:
            return False, existing
        if int(existing.get("attempts") or 0) >= max_attempts:
            return False, existing

        with self._write() as cursor:
            cursor.execute(
                "UPDATE effects SET status='running', lease_owner=?, lease_expires_at=?,"
                " attempts=attempts+1, error=NULL, updated_at=?"
                " WHERE task_id=? AND step_id=? AND tool_name=? AND args_hash=?"
                " AND (status!='running' OR lease_expires_at<=?)",
                (tool_call_id, expires, now(), *key, now()),
            )
            claimed = cursor.rowcount == 1
        record = self.get_step(key)
        return claimed, record  # type: ignore[return-value]

    def complete_step(
        self,
        key: tuple[str, str, str, str],
        *,
        result: Any,
        tool_call_id: str,
    ) -> dict[str, Any]:
        payload = json.dumps(result, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        result_json: str | None = payload
        result_ref: str | None = None
        if len(payload.encode("utf-8")) > RESULT_INLINE_LIMIT:
            path = effects_dir() / f"{'-'.join(key)}.json"
            path.write_text(payload, encoding="utf-8")
            result_ref, result_json = str(path), None
        with self._write() as cursor:
            cursor.execute(
                "UPDATE effects SET status='completed', result_json=?, result_ref=?, result_digest=?,"
                " error=NULL, tool_call_id=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=?"
                " WHERE task_id=? AND step_id=? AND tool_name=? AND args_hash=?",
                (result_json, result_ref, digest, tool_call_id, now(), *key),
            )
        return self.get_step(key)  # type: ignore[return-value]

    def fail_step(self, key: tuple[str, str, str, str], *, error: str) -> dict[str, Any]:
        with self._write() as cursor:
            cursor.execute(
                "UPDATE effects SET status='failed', error=?, lease_owner=NULL, lease_expires_at=NULL,"
                " updated_at=? WHERE task_id=? AND step_id=? AND tool_name=? AND args_hash=?",
                (error[:500], now(), *key),
            )
        return self.get_step(key)  # type: ignore[return-value]

    def restore_result(self, record: dict[str, Any]) -> Any | None:
        """Return the persisted tool result, verifying file-backed payloads."""

        if record.get("result_json"):
            return json.loads(record["result_json"])
        ref = record.get("result_ref")
        if not ref:
            return None
        path = Path(ref)
        if not path.exists():
            return None
        payload = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if digest != record.get("result_digest"):
            return None
        return json.loads(payload)


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "StoreBusyError",
    "business_db_path",
    "RESULT_INLINE_LIMIT",
    "OwnershipError",
    "SessionBusyError",
    "Store",
    "args_hash",
    "canonical_args",
    "effect_key",
    "effects_dir",
    "now",
    "state_db_path",
]