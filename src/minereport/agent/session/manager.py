"""Local user identity, session ownership and checkpointer construction."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from ...common import config
from .store import OwnershipError, Store, state_db_path

DEFAULT_HISTORY_LIMIT = 24


class UserIdentityError(RuntimeError):
    """``--user-id`` disagreed with the persisted local identity."""


def user_file_path() -> Path:
    path = state_db_path().parent / "user.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_user_id() -> str | None:
    path = user_file_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("user_id")
    except (OSError, json.JSONDecodeError):
        return None


def write_user_id(user_id: str) -> None:
    user_file_path().write_text(
        json.dumps({"user_id": user_id}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def resolve_user_id(
    requested: str | None = None, *, switch: bool = False, store: Store | None = None
) -> tuple[str, list[str]]:
    """Persist and reuse the local CLI identity.

    The identity is written once and reused; a mismatching ``--user-id`` is
    refused unless the caller passes the explicit ``--switch-user`` flag, so an
    existing session ownership record can never be silently invalidated.
    """

    notes: list[str] = []
    stored = read_user_id()

    if stored is None:
        user_id = requested or str(uuid.uuid4())
        write_user_id(user_id)
        notes.append(f"首次运行：已生成并保存本地 user_id={user_id}（文件 {user_file_path()}）")
        return user_id, notes

    if requested is None or requested == stored:
        return stored, notes

    if not switch:
        raise UserIdentityError(
            f"--user-id={requested} 与本地记录 {stored} 不一致；如需切换请显式使用 --switch-user"
        )

    owned = len(store.list_sessions(stored)) if store is not None else 0
    notes.append(
        f"切换用户：原 user_id={stored}（名下 {owned} 个会话仍归属旧用户）→ 新 user_id={requested}"
    )
    write_user_id(requested)
    return requested, notes


class SessionManager:
    def __init__(
        self,
        store: Store,
        *,
        persist: str = "sqlite",
        checkpoint_path: Path | None = None,
    ) -> None:
        self.store = store
        self.persist = persist
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else state_db_path()
        self._saver: Any = None
        self._ctx: Any = None

    # ----------------------------------------------------------- persistence
    async def checkpointer(self) -> Any:
        """Async checkpointer (the graph is driven with ``ainvoke``).

        ``SqliteSaver`` is synchronous and rejects async calls, so persistence
        uses ``AsyncSqliteSaver``; the context manager is kept open for the
        manager's lifetime and released by ``aclose()``.
        """

        if self.persist == "memory":
            from langgraph.checkpoint.memory import InMemorySaver

            return InMemorySaver()

        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        if self._saver is None:
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._ctx = AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path))
            self._saver = await self._ctx.__aenter__()
            # ``from_conn_string`` hands back a plain aiosqlite connection: its
            # journal mode is "delete" and its 5s busy timeout is only the sqlite3
            # default. Apply the same pragmas the business store uses, so a second
            # CLI process or container sharing this state file cannot trip over a
            # rollback journal (the aiosqlite half of the reported lock conflict).
            conn = self._saver.conn
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.commit()
        return self._saver

    async def aclose(self) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None
            self._saver = None

    def close(self) -> None:
        self._saver = None

    # --------------------------------------------------------------- sessions
    def ensure_session(self, session_id: str | None, user_id: str) -> str:
        sid = session_id or str(uuid.uuid4())
        self.store.ensure_session(sid, user_id)
        return sid

    def assert_owner(self, session_id: str, user_id: str) -> dict[str, Any]:
        return self.store.assert_owner(session_id, user_id)

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        return self.store.list_sessions(user_id)

    # ---------------------------------------------------------------- history
    @staticmethod
    def cap_history(messages: list[Any], *, limit: int = DEFAULT_HISTORY_LIMIT) -> tuple[list[Any], str]:
        """Keep the newest ``limit`` messages; report what was dropped."""

        if len(messages) <= limit:
            return messages, ""
        dropped = len(messages) - limit
        return messages[-limit:], f"已裁掉较早的 {dropped} 条消息（超过 {limit} 条历史上限）"


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "OwnershipError",
    "SessionManager",
    "UserIdentityError",
    "resolve_user_id",
    "user_file_path",
    "write_user_id",
]