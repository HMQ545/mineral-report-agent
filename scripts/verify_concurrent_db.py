"""Several real OS processes hammer the same state/business SQLite files.

Evidence for the "no lock crash, no silent overwrite, no corruption" requirement:
the workers are separate processes (multiprocessing spawn), not threads inside one
interpreter, and they exercise the app's own Store / SessionManager code path.

    uv run --no-sync python scripts/verify_concurrent_db.py [processes] [writes]

Post-conditions checked:
* every task_id created by every worker is still present (nothing overwritten)
* the stored task set equals the union exactly (no ghosts, no duplicates)
* PRAGMA integrity_check == 'ok' on both databases
* a contended effect step is claimed by exactly one worker
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from multiprocessing import get_context
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class Ping(TypedDict, total=False):
    n: int


def _worker(index: int, session_id: str, writes: int, result_path: str) -> None:
    """One independent process: same session writes + same-step claim + checkpoints."""

    asyncio.run(_worker_async(index, session_id, writes, result_path))


async def _worker_async(index: int, session_id: str, writes: int, result_path: str) -> None:
    sys.path.insert(0, str(SRC))
    from minereport.agent.session.manager import SessionManager
    from minereport.agent.session.store import Store, StoreBusyError, effect_key
    from minereport.agent.task.manager import TaskManager
    from langgraph.graph import END, START, StateGraph

    result: dict = {"index": index, "task_ids": [], "retries": 0, "error": None,
                    "claimed": False, "checkpoints": 0, "journal_mode": None}

    async def ping(state: Ping) -> Ping:
        return {"n": (state.get("n") or 0) + 1}

    try:
        store = Store()
        manager = TaskManager(store)
        store.ensure_session(session_id, "u-concurrent")

        for i in range(writes):
            for attempt in range(4):
                try:
                    task = manager.create(
                        session_id=session_id, user_id="u-concurrent",
                        intent="trend_query", slots={"commodity": "copper", "n": f"{index}-{i}"},
                        missing=["days"], description=f"p{index}-{i}",
                    )
                    result["task_ids"].append(task["task_id"])
                    break
                except StoreBusyError:
                    result["retries"] += 1
                    await asyncio.sleep(0.05 + 0.05 * attempt)
            else:
                raise RuntimeError("task write kept failing with StoreBusyError")

        key = effect_key("shared-task", "shared-step", "get_trend", {"commodity": "copper"})
        try:
            claimed, _ = store.claim_step(key, session_id=session_id,
                                          request_id=f"r-{index}", tool_call_id=f"tc-{index}")
            result["claimed"] = bool(claimed)
        except StoreBusyError:
            result["retries"] += 1  # somebody else holds the step; not a data problem

        graph = StateGraph(Ping)
        graph.add_node("ping", ping)
        graph.add_edge(START, "ping")
        graph.add_edge("ping", END)
        session_manager = SessionManager(store, persist="sqlite")
        saver = await session_manager.checkpointer()
        cursor = await saver.conn.execute("PRAGMA journal_mode")
        result["journal_mode"] = (await cursor.fetchone())[0]
        app = graph.compile(checkpointer=saver)
        for j in range(5):
            state = await app.ainvoke({"n": 0}, config={"configurable": {"thread_id": f"t-{index}-{j}"}})
            assert state["n"] == 1, state
            result["checkpoints"] += 1
        await session_manager.aclose()
    except Exception as exc:  # noqa: BLE001 - reported by the parent
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        Path(result_path).write_text(json.dumps(result), encoding="utf-8")


async def _main() -> int:
    processes = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    writes = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    workdir = Path(tempfile.mkdtemp(prefix="minereport-concurrent-"))
    business_db = workdir / "agent_business.sqlite"
    state_db = workdir / "agent_state.sqlite"
    os.environ["MINEREPORT_BUSINESS_DB"] = str(business_db)
    os.environ["MINEREPORT_STATE_DB"] = str(state_db)

    session_id = str(uuid.uuid4())
    print(f"workdir    : {workdir}")
    print(f"session_id : {session_id}")
    print(f"processes  : {processes}   writes/process: {writes}")

    ctx = get_context("spawn")
    jobs = []
    for index in range(processes):
        path = workdir / f"worker-{index}.json"
        job = ctx.Process(target=_worker, args=(index, session_id, writes, str(path)))
        job.start()
        jobs.append((job, path))
    for job, _ in jobs:
        job.join(60)
        if job.is_alive():
            job.terminate()
            print("FAIL: a worker did not finish within 60s")
            return 1

    results = [json.loads(path.read_text(encoding="utf-8")) for _, path in jobs]
    expected = [tid for item in results for tid in item["task_ids"]]

    from minereport.agent.session.store import Store, state_db_path, business_db_path
    store = Store(business_db)
    stored = {task["task_id"] for task in store.list_tasks(session_id, user_id="u-concurrent")}

    def integrity(path: Path) -> str:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()

    failures: list[str] = []
    for item in results:
        print(f"  worker {item['index']}: tasks={len(item['task_ids'])} retries={item['retries']} "
              f"claimed={item['claimed']} checkpoints={item['checkpoints']} "
              f"journal={item['journal_mode']} error={item['error']}")
        if item["error"]:
            failures.append(f"worker {item['index']} raised {item['error']}")
        if item["journal_mode"] != "wal":
            failures.append(f"worker {item['index']} checkpointer journal={item['journal_mode']} (expected wal)")

    if set(expected) != stored:
        missing = set(expected) - stored
        ghost = stored - set(expected)
        failures.append(f"task set mismatch: missing={len(missing)} ghost={len(ghost)}")
    if len(expected) != len(set(expected)):
        failures.append("a worker produced duplicate task_ids")
    if len(stored) != processes * writes:
        failures.append(f"expected {processes * writes} stored tasks, found {len(stored)}")
    winners = sum(1 for item in results if item["claimed"])
    if winners != 1:
        failures.append(f"contended step claimed by {winners} workers (expected exactly 1)")
    for label, path in (("business", business_db_path()), ("state", state_db_path())):
        status = integrity(path)
        print(f"  integrity_check {label:8s}: {status}")
        if status != "ok":
            failures.append(f"{label} database integrity_check = {status}")

    print()
    if failures:
        print("FAIL")
        for line in failures:
            print("  - " + line)
        return 1
    print(f"PASS: {processes} processes wrote {len(expected)} tasks + {sum(i['checkpoints'] for i in results)} "
          f"checkpoints concurrently; no lost/overwritten rows, both databases intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))