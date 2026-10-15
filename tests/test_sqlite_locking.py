"""Regression tests for the SQLite lock conflict (checkpointer vs business store)."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest

from minereport.agent.conversation import run_turn
from minereport.agent.llm import StubBackend
from minereport.agent.session import store as store_module
from minereport.agent.session.manager import SessionManager, resolve_user_id
from minereport.agent.session.store import Store, StoreBusyError, business_db_path, state_db_path
from minereport.agent.task.manager import TaskManager


class FakeTool:
    def __init__(self, name, payload):
        self.name, self.payload = name, payload

    async def ainvoke(self, arguments):
        return self.payload


def build_tools():
    trend = {"provenance": "live", "source": "sina", "data": {
        "display_name": "碳酸锂", "change_pct": -5.0,
        "coverage": {"start": "2026-09-14", "end": "2026-10-14", "trading_days": 23}}}
    return {name: FakeTool(name, trend) for name in ("get_trend", "get_price", "search", "extract_resources")}


def test_checkpointer_and_business_store_use_different_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "agent_state.sqlite"))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "agent_business.sqlite"))

    assert state_db_path().name == "agent_state.sqlite"
    assert business_db_path().name == "agent_business.sqlite"
    assert business_db_path() != state_db_path(), "the two stores must not share a file"


@pytest.mark.anyio
async def test_two_turn_chat_with_sqlite_persistence_does_not_lock(tmp_path, monkeypatch):
    """The reported crash: turn 2 (slot completion) hit 'database is locked'."""

    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "agent_state.sqlite"))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "agent_business.sqlite"))

    store = Store()
    user_id, _ = resolve_user_id(None, store=store)
    manager = SessionManager(store, persist="sqlite")
    session_id = manager.ensure_session(None, user_id)
    tools = build_tools()

    async def turn(text, request_id):
        return await run_turn(
            store=store, session_manager=manager, tools=tools, backend=StubBackend(),
            user_id=user_id, session_id=session_id, request_id=request_id, text=text,
        )

    first = await turn("帮我查一下锂价", str(uuid.uuid4()))
    assert first["task_status"] == "waiting_for_slots"

    second = await turn("碳酸锂", str(uuid.uuid4()))          # previously crashed here
    assert second["task_id"] == first["task_id"]
    assert second["task_status"] == "waiting_for_slots"
    assert "日期" in second["reply"]

    third = await turn("最近30天的走势", str(uuid.uuid4()))
    assert third["task_id"] == first["task_id"], "a compatible intent keeps the task"
    assert third["intent"] == "trend_query"
    assert third["task_status"] == "completed"
    await manager.aclose()

    tasks = TaskManager(store).all_for_session(session_id, user_id)
    assert len(tasks) == 1 and tasks[0]["status"] == "completed"


def test_write_retries_while_another_connection_holds_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "biz.sqlite"))
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "state.sqlite"))
    store = Store()
    store.ensure_session("s1", "u1")

    holding = threading.Event()
    released = threading.Event()

    def hold_then_release():
        holder = sqlite3.connect(str(business_db_path()), timeout=1.0, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        holding.set()
        time.sleep(0.4)
        holder.execute("COMMIT")
        released.set()
        holder.close()

    thread = threading.Thread(target=hold_then_release, daemon=True)
    thread.start()
    assert holding.wait(timeout=5), "the competing writer never took the lock"

    task = store.create_task(session_id="s1", user_id="u1", intent="trend_query",
                             slots={}, missing=["days"], status="waiting_for_slots")
    assert released.is_set(), "the store should have retried until the lock was free"
    assert store.get_task(task["task_id"]) is not None
    thread.join(timeout=5)


def test_write_raises_typed_error_when_lock_is_never_released(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "biz2.sqlite"))
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "state2.sqlite"))
    store = Store()
    store.ensure_session("s1", "u1")

    holder = sqlite3.connect(str(business_db_path()), timeout=1.0, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    with pytest.raises(StoreBusyError):
        store.create_task(session_id="s1", user_id="u1", intent="price_query",
                          slots={}, missing=["date"], status="waiting_for_slots")
    holder.execute("ROLLBACK")
    holder.close()

    # the connection is still usable and no partial row was written
    store.ensure_session("s1", "u1")
    assert store.list_tasks("s1", user_id="u1") == []


def test_legacy_combined_database_is_migrated(tmp_path, monkeypatch):
    """Old installs stored business tables inside agent_state.sqlite."""

    state_db = tmp_path / "agent_state.sqlite"
    legacy = sqlite3.connect(str(state_db))
    legacy.executescript(store_module.SCHEMA)
    legacy.execute(
        "INSERT INTO sessions(session_id,user_id,created_at,updated_at) VALUES ('s-old','u-old','t','t')"
    )
    legacy.execute(
        "INSERT INTO tasks(task_id,session_id,user_id,intent,status,suspended,slots,missing_slots,"
        " slot_sources,description,created_at,updated_at)"
        " VALUES ('t-old','s-old','u-old','trend_query','waiting_for_slots',0,'{}','[\"days\"]','{}','old','t','t')"
    )
    legacy.commit()
    legacy.close()

    monkeypatch.setenv("MINEREPORT_STATE_DB", str(state_db))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "agent_business.sqlite"))

    store = Store()                                          # triggers the migration
    migrated = store.get_task("t-old")
    assert migrated is not None and migrated["missing_slots"] == ["days"]
    assert store.get_session("s-old")["user_id"] == "u-old"

    legacy_check = sqlite3.connect(str(state_db))
    assert legacy_check.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    legacy_check.close()


def test_concurrent_writers_keep_both_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "biz3.sqlite"))
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "state3.sqlite"))
    first, second = Store(), Store()
    first.ensure_session("s1", "u1")

    created: list[str] = []
    errors: list[Exception] = []

    def writer(store: Store, commodity: str) -> None:
        try:
            task = store.create_task(session_id="s1", user_id="u1", intent="trend_query",
                                     slots={"commodity": commodity}, missing=["days"],
                                     status="waiting_for_slots")
            created.append(task["task_id"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(first, "copper")),
        threading.Thread(target=writer, args=(second, "lithium_carbonate")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(created) == 2
    stored = {task["task_id"] for task in first.list_tasks("s1", user_id="u1")}
    assert stored == set(created), "neither writer may silently overwrite the other"

@pytest.mark.anyio
async def test_checkpointer_connection_uses_wal_and_busy_timeout(tmp_path, monkeypatch):
    """Point 5 of the lock audit: the aiosqlite checkpointer connection must get
    the same pragmas as the business store.

    ``AsyncSqliteSaver.from_conn_string`` opens a plain aiosqlite connection whose
    journal mode is ``delete``; without WAL a second process/container sharing the
    state file can trip over a rollback journal.
    """

    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "business.sqlite"))

    store = Store()
    manager = SessionManager(store, persist="sqlite")
    saver = await manager.checkpointer()

    async def pragma(name: str):
        cursor = await saver.conn.execute(f"PRAGMA {name}")
        return (await cursor.fetchone())[0]

    assert await pragma("journal_mode") == "wal"
    assert await pragma("busy_timeout") == 5000
    assert await pragma("synchronous") == 1

    assert await manager.checkpointer() is saver, "the checkpointer must be created once"
    await manager.aclose()