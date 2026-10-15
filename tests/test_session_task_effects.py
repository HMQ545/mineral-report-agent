"""Stage 2/3: store, session identity, tasks, requests, effects, TaskMatcher."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from minereport.agent.effects import EffectFailedError, StepBusyError, call_tool_once
from minereport.agent.session.manager import (
    SessionManager,
    UserIdentityError,
    read_user_id,
    resolve_user_id,
)
from minereport.agent.session.store import OwnershipError, Store, effect_key
from minereport.agent.task.manager import TaskManager
from minereport.agent.task.matcher import TaskMatcher


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "state.sqlite"))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "business.sqlite"))
    return Store()


class FakeTool:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def ainvoke(self, arguments):
        self.calls += 1
        return self.payload


# --------------------------------------------------------------------- store
def test_session_ownership_is_enforced(store):
    store.ensure_session("s1", "u1")
    with pytest.raises(OwnershipError):
        store.assert_owner("s1", "u2")
    with pytest.raises(OwnershipError):
        store.ensure_session("s1", "u2")


def test_task_lifecycle_and_resumable_filter(store):
    store.ensure_session("s1", "u1")
    task = store.create_task(
        session_id="s1", user_id="u1", intent="trend_query",
        slots={"commodity": "copper"}, missing=["days"], status="waiting_for_slots",
    )
    assert task["status"] == "waiting_for_slots"
    assert len(store.list_resumable("s1", "u1")) == 1

    store.update_task(task["task_id"], status="cancelled")
    assert store.list_resumable("s1", "u1") == []


def test_effect_claim_lease_and_result_restore(store):
    key = effect_key("task-1", "trend_query.get_trend", "get_trend", {"commodity": "copper", "days": 30})
    claimed, _ = store.claim_step(key, session_id="s1", request_id="r1", tool_call_id="tc1")
    assert claimed

    claimed_again, record = store.claim_step(key, session_id="s1", request_id="r2", tool_call_id="tc2")
    assert claimed_again is False and record["status"] == "running"

    store.complete_step(key, result={"ok": True}, tool_call_id="tc1")
    claimed_after, record = store.claim_step(key, session_id="s1", request_id="r3", tool_call_id="tc3")
    assert claimed_after is False
    assert store.restore_result(record) == {"ok": True}


def test_large_result_is_file_backed_and_verified(store, tmp_path):
    key = effect_key("task-big", "step-x", "tool-x", {"n": 1})
    store.claim_step(key, session_id="s1", request_id="r1", tool_call_id="tc1")
    big = {"blob": "x" * 300_000}
    record = store.complete_step(key, result=big, tool_call_id="tc1")
    assert record["result_ref"] and record["result_json"] is None
    assert store.restore_result(record)["blob"] == big["blob"]

    Path(record["result_ref"]).write_text("{\"tampered\": true}", encoding="utf-8")
    assert store.restore_result(record) is None, "a tampered result must not be reused"


def test_stale_running_lease_can_be_reclaimed(store):
    key = effect_key("task-stale", "step-y", "tool-y", {"n": 2})
    claimed, record = store.claim_step(
        key, session_id="s1", request_id="r1", tool_call_id="tc1", lease_seconds=-1
    )
    assert claimed
    reclaimed, again = store.claim_step(key, session_id="s1", request_id="r2", tool_call_id="tc2")
    assert reclaimed is True and int(again["attempts"]) == 2


def test_two_connections_cannot_claim_the_same_step(tmp_path):
    """Two Store objects = two processes; the DB lease must arbitrate."""

    db = tmp_path / "shared.sqlite"
    first, second = Store(db), Store(db)
    key = effect_key("task-two", "step-z", "tool-z", {})

    results = [
        first.claim_step(key, session_id="s1", request_id="r1", tool_call_id="tc-a")[0],
        second.claim_step(key, session_id="s1", request_id="r2", tool_call_id="tc-b")[0],
    ]
    assert results.count(True) == 1, results


def test_request_records_and_attempt_cap(store):
    store.ensure_session("s1", "u1")
    new, _ = store.begin_request("s1", "req-1", "task-1")
    assert new is True
    replay, record = store.begin_request("s1", "req-1", "task-1")
    assert replay is False and record["request_id"] == "req-1"

    key = effect_key("task-cap", "step-cap", "tool-cap", {})
    for attempt in range(3):
        claimed, record = store.claim_step(
            key, session_id="s1", request_id=f"r{attempt}", tool_call_id=f"tc{attempt}",
            lease_seconds=-1, max_attempts=2,
        )
        if claimed:
            store.fail_step(key, error="boom")
    assert int(record["attempts"]) >= 2
    claimed, _ = store.claim_step(key, session_id="s1", request_id="r9", tool_call_id="tc9")
    assert claimed is False, "attempt cap must stop endless retries"


# ------------------------------------------------------------------- user_id
def test_user_id_is_created_then_reused(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "state.sqlite"))
    store = Store()

    first, notes = resolve_user_id(None, store=store)
    assert first and notes
    second, _ = resolve_user_id(None, store=store)
    assert second == first
    same, _ = resolve_user_id(first, store=store)
    assert same == first

    with pytest.raises(UserIdentityError):
        resolve_user_id("11111111-1111-1111-1111-111111111111", store=store)

    switched, notes = resolve_user_id(
        "22222222-2222-2222-2222-222222222222", switch=True, store=store
    )
    assert switched.endswith("2222")
    assert read_user_id() == switched


def test_identity_and_tasks_survive_process_restart(tmp_path, monkeypatch):
    db = tmp_path / "state.sqlite"
    business_db = tmp_path / "business.sqlite"
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(db))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(business_db))

    store_a = Store()
    user_id, _ = resolve_user_id(None, store=store_a)
    session_id = SessionManager(store_a).ensure_session(None, user_id)
    task = TaskManager(store_a).create(
        session_id=session_id, user_id=user_id, intent="trend_query",
        slots={"commodity": "copper"}, missing=["days"],
    )
    store_a.close()

    # Simulated restart: brand-new Store/SessionManager objects on the same files.
    store_b = Store(business_db)
    assert read_user_id() == user_id
    recovered_user, _ = resolve_user_id(None, store=store_b)
    assert recovered_user == user_id
    store_b.assert_owner(session_id, user_id)
    recovered = TaskManager(store_b).resumable(session_id, user_id)
    assert [t["task_id"] for t in recovered] == [task["task_id"]]
    assert recovered[0]["missing_slots"] == ["days"]


def test_sessions_are_isolated_between_users(store):
    store.ensure_session("s1", "u1")
    store.ensure_session("s2", "u2")
    assert [s["session_id"] for s in store.list_sessions("u1")] == ["s1"]
    with pytest.raises(OwnershipError):
        store.assert_owner("s2", "u1")


# -------------------------------------------------------------------- effects
@pytest.mark.anyio
async def test_call_tool_once_reuses_the_recorded_result(store, monkeypatch):
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(store.path))
    tool = FakeTool({"data": {"points": [1, 2, 3]}})
    tools = {"get_trend": tool}
    kwargs = dict(
        session_id="s1", task_id="task-1", request_id="r1", step_id="trend_query.get_trend"
    )
    first = await call_tool_once(store, tools, "get_trend", {"commodity": "copper"}, **kwargs)
    second = await call_tool_once(
        store, tools, "get_trend", {"commodity": "copper"},
        session_id="s1", task_id="task-1", request_id="r2", step_id="trend_query.get_trend",
    )
    assert first == second
    assert tool.calls == 1, "a completed step must never be re-executed"


@pytest.mark.anyio
async def test_call_tool_once_reports_busy_and_failures(store, monkeypatch):
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(store.path))

    class SlowTool:
        def __init__(self):
            self.started = asyncio.Event()

        async def ainvoke(self, arguments):
            self.started.set()
            await asyncio.sleep(0.3)
            return {"data": "ok"}

    slow = SlowTool()
    task = asyncio.create_task(
        call_tool_once(store, {"t": slow}, "t", {"a": 1}, session_id="s1", task_id="tk", request_id="r1", step_id="st")
    )
    await slow.started.wait()
    with pytest.raises(StepBusyError):
        await call_tool_once(store, {"t": slow}, "t", {"a": 1}, session_id="s1", task_id="tk", request_id="r2", step_id="st")
    await task

    class BrokenTool:
        async def ainvoke(self, arguments):
            raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        await call_tool_once(store, {"b": BrokenTool()}, "b", {}, session_id="s1", task_id="tk", request_id="r3", step_id="st2")

    record = store.get_step(effect_key("tk", "st2", "b", {}))
    assert record["status"] == "failed" and "network down" in record["error"]


# -------------------------------------------------------------------- matcher
@pytest.fixture()
def seeded(store):
    store.ensure_session("s1", "u1")
    manager = TaskManager(store)
    price = manager.create(session_id="s1", user_id="u1", intent="price_query",
                           slots={"commodity": "lithium_carbonate"}, missing=["date"],
                           description="锂价查询")
    resource = manager.create(session_id="s1", user_id="u1", intent="resource_query",
                              slots={"company": "Pilbara Minerals Limited"}, missing=["project"],
                              description="Pilbara 资源量查询")
    copper = manager.create(session_id="s1", user_id="u1", intent="trend_query",
                            slots={"commodity": "copper"}, missing=["days"],
                            description="铜价走势")
    return store, manager, matcher_for(store), price, resource, copper


def matcher_for(store):
    return TaskMatcher(store)


@pytest.mark.anyio
async def test_matcher_cases(seeded):
    store, manager, matcher, price, resource, copper = seeded

    outcome = await matcher.match("碳酸锂，最近 30 天", user_id="u1", session_id="s1",
                                  requested_intent="trend_query",
                                  extracted={"commodity": "lithium_carbonate", "days": 30})
    assert outcome.is_unique and outcome.task["task_id"] == price["task_id"]

    outcome = await matcher.match("Pilbara 的项目是 Pilgangoora", user_id="u1", session_id="s1",
                                  requested_intent="resource_query",
                                  extracted={"company": "Pilbara Minerals Limited"})
    assert outcome.is_unique and outcome.task["task_id"] == resource["task_id"]

    outcome = await matcher.match("帮我找一下之前的那个查询", user_id="u1", session_id="s1")
    assert outcome.status == "ambiguous" and len(outcome.candidates) >= 2

    outcome = await matcher.match("碳酸锂和铜的走势", user_id="u1", session_id="s1",
                                  requested_intent="trend_query")
    assert outcome.status == "ambiguous"

    manager.cancel(copper["task_id"], "user cancel")
    outcome = await matcher.match("继续铜的查询", user_id="u1", session_id="s1",
                                  requested_intent="trend_query")
    # The copper task is cancelled and the remaining tasks are unrelated, so the
    # matcher must refuse rather than silently resume a different task.
    assert outcome.status == "none"
    assert "不会改用其它任务代替" in outcome.reason

    outcome = await matcher.match("继续", user_id="u1", session_id="s1", explicit_task_id=price["task_id"])
    assert outcome.is_unique and outcome.task["task_id"] == price["task_id"]

    outcome = await matcher.match(
        "继续", user_id="u1", session_id="s1",
        explicit_task_id="11111111-2222-3333-4444-555555555555",
    )
    assert outcome.status == "none"

    manager.complete(resource["task_id"], {"ok": True})
    outcome = await matcher.match("继续 Pilbara 资源量", user_id="u1", session_id="s1",
                                  requested_intent="resource_query")
    assert outcome.status in {"none", "ambiguous"}
    assert outcome.task is None or outcome.task["task_id"] != resource["task_id"]

# ------------------------------------------------- cross-process / concurrency
@pytest.mark.anyio
async def test_completed_step_is_reused_across_processes(tmp_path, monkeypatch):
    """Two ``Store`` objects on one file = two processes.

    Process B must restore the result process A persisted instead of calling the
    MCP tool a second time (the tool-idempotency key is DB-level, not in-memory).
    """

    db = tmp_path / "cross-process.sqlite"
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(db))
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "cross-state.sqlite"))

    process_a, process_b = Store(db), Store(db)
    tool_a = FakeTool({"data": {"points": [1, 2, 3]}})
    tool_b = FakeTool({"data": {"points": [9, 9, 9]}})
    args = {"commodity": "copper", "days": 30}

    first = await call_tool_once(
        process_a, {"get_trend": tool_a}, "get_trend", args,
        session_id="s1", task_id="task-x", request_id="r-a", step_id="trend_query.get_trend",
    )
    second = await call_tool_once(
        process_b, {"get_trend": tool_b}, "get_trend", args,
        session_id="s1", task_id="task-x", request_id="r-b", step_id="trend_query.get_trend",
    )

    assert first == second == {"data": {"points": [1, 2, 3]}}
    assert tool_a.calls == 1 and tool_b.calls == 0, "the second process must reuse the stored result"


@pytest.mark.anyio
async def test_a_slow_tool_call_does_not_block_other_sessions(tmp_path, monkeypatch):
    """No SQLite write transaction may be held across an MCP call.

    While one session waits on a slow MCP tool, a different session must still be
    able to write to the very same database file.
    """

    import time

    db = tmp_path / "parallel.sqlite"
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(db))
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "parallel-state.sqlite"))
    slow_store, other_store = Store(db), Store(db)

    class SlowTool:
        def __init__(self):
            self.started = asyncio.Event()

        async def ainvoke(self, arguments):
            self.started.set()
            await asyncio.sleep(0.6)
            return {"data": "slow"}

    slow = SlowTool()
    in_flight = asyncio.create_task(
        call_tool_once(
            slow_store, {"get_trend": slow}, "get_trend", {"commodity": "copper"},
            session_id="s-slow", task_id="task-slow", request_id="r-slow",
            step_id="trend_query.get_trend",
        )
    )
    await asyncio.wait_for(slow.started.wait(), timeout=5)

    started_at = time.perf_counter()
    other_store.ensure_session("s-other", "u-other")          # a write while the slow call is in flight
    quick = await call_tool_once(
        other_store, {"get_price": FakeTool({"data": {"price": 1}})}, "get_price",
        {"commodity": "copper"},
        session_id="s-other", task_id="task-other", request_id="r-other",
        step_id="price_query.get_price",
    )
    elapsed = time.perf_counter() - started_at

    assert quick["data"]["price"] == 1
    assert elapsed < 0.5, f"another session waited {elapsed:.2f}s behind an in-flight MCP call"
    await in_flight
    record = other_store.get_step(
        effect_key("task-other", "price_query.get_price", "get_price", {"commodity": "copper"})
    )
    assert record["status"] == "completed"