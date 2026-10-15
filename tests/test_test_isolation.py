"""Regression tests for test isolation itself.

Splitting the checkpointer database from the business database briefly let some
tests fall back to the repository's real ``data/sessions/`` files, which polluted
later runs. These tests make that impossible to reintroduce.
"""

from __future__ import annotations

import sqlite3

from minereport.agent.session.store import Store, business_db_path, state_db_path
from minereport.agent.task.manager import TaskManager
from minereport.common import config


def _real_sessions_snapshot() -> dict:
    directory = config.DATA_DIR / "sessions"
    snapshot: dict = {"files": {}, "rows": {}}
    if not directory.exists():
        return snapshot
    for path in sorted(directory.glob("*.sqlite")):
        stat = path.stat()
        snapshot["files"][path.name] = (stat.st_size, stat.st_mtime_ns)
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        for table in ("sessions", "tasks", "requests", "effects"):
            try:
                snapshot["rows"][f"{path.name}:{table}"] = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            except sqlite3.Error:
                pass
        connection.close()
    return snapshot


def test_databases_point_into_tmp_path(tmp_path):
    assert business_db_path().parent == tmp_path
    assert state_db_path().parent == tmp_path
    assert business_db_path() != state_db_path()


def test_offline_test_never_writes_the_real_sessions_directory():
    before = _real_sessions_snapshot()

    store = Store()
    store.ensure_session("s-isolated", "u-isolated")
    manager = TaskManager(store)
    task = manager.create(session_id="s-isolated", user_id="u-isolated", intent="trend_query",
                          slots={"commodity": "copper"}, missing=["days"])
    assert manager.resumable("s-isolated", "u-isolated")[0]["task_id"] == task["task_id"]

    assert before == _real_sessions_snapshot(), (
        "an offline test must not create or modify files under data/sessions"
    )


def test_repeated_execution_is_stable():
    """The same flow twice in a row must not accumulate rows."""

    store = Store()
    store.ensure_session("s-repeat", "u1")
    manager = TaskManager(store)

    first = manager.create(session_id="s-repeat", user_id="u1", intent="price_query",
                           slots={"commodity": "copper"}, missing=["date"])
    assert len(manager.resumable("s-repeat", "u1")) == 1
    assert [task["task_id"] for task in manager.resumable("s-repeat", "u1")] == [first["task_id"]]

    manager.cancel(first["task_id"], "done")
    assert manager.resumable("s-repeat", "u1") == []

    again = Store()                                   # a second store on the same file
    assert TaskManager(again).resumable("s-repeat", "u1") == []
    assert len(again.list_tasks("s-repeat", user_id="u1")) == 1


def test_order_of_operations_does_not_change_the_final_state(tmp_path):
    def scenario(create_b_first: bool) -> list[str]:
        # each ordering gets its own database file so the two runs are comparable
        store = Store(tmp_path / f"order-{create_b_first}.sqlite")
        store.ensure_session("s-order", "u1")
        manager = TaskManager(store)

        def create(intent: str, commodity: str):
            return manager.create(session_id="s-order", user_id="u1", intent=intent,
                                  slots={"commodity": commodity}, missing=["days"])

        if create_b_first:
            b = create("trend_query", "copper")
            a = create("price_query", "aluminum")
        else:
            a = create("price_query", "aluminum")
            b = create("trend_query", "copper")
        manager.cancel(b["task_id"], "cancelled in both orders")
        return sorted(f"{task['intent']}:{task['status']}" for task in manager.all_for_session("s-order", "u1"))

    assert scenario(False) == scenario(True)