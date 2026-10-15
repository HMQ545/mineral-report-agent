"""Stage 4/5: conversational routing, slot filling, task switching, chat."""

from __future__ import annotations

import uuid

import pytest

from minereport.agent.conversation import AMBIGUOUS_COMMODITY_QUESTION, run_turn
from minereport.agent.llm import StubBackend
from minereport.agent.session.manager import SessionManager, resolve_user_id
from minereport.agent.session.store import Store
from minereport.agent.task.manager import TaskManager

MCP_CALLS: list[tuple[str, dict]] = []


class FakeTool:
    def __init__(self, name: str, payload: dict):
        self.name = name
        self.payload = payload

    async def ainvoke(self, arguments):
        MCP_CALLS.append((self.name, arguments))
        return self.payload


def build_tools() -> dict:
    return {
        "search": FakeTool("search", {"provenance": "live", "source": "rss", "data": {
            "items": [], "company_items": [], "industry_items": [], "stats": {"excluded_irrelevant": 2}}}),
        "fetch_article": FakeTool("fetch_article", {"provenance": "live", "data": {"url": "u", "text": "body", "word_count": 100}}),
        "extract_resources": FakeTool("extract_resources", {"provenance": "live", "data": {
            "reporter_entity": "Pilbara Minerals Limited", "project_name": "Pilgangoora Operation",
            "report_date": "2023-06-30", "counts": {"mineral_resource_rows": 3, "ore_reserve_rows": 2},
            "mineral_resources": [{"category": "Indicated", "tonnes": 315.2, "tonnes_unit": "Mt", "grade": 1.15, "grade_unit": "%"}]}}),
        "list_sources": FakeTool("list_sources", {"provenance": "live", "data": {"sources": []}}),
        "get_price": FakeTool("get_price", {"provenance": "live", "data": {
            "display_name": "碳酸锂", "price": 100900, "unit": "CNY/t", "as_of": "2026-10-14"}}),
        "get_trend": FakeTool("get_trend", {"provenance": "live", "data": {
            "display_name": "碳酸锂", "change_pct": -5.0,
            "coverage": {"start": "2026-09-14", "end": "2026-10-14", "trading_days": 23}}}),
    }


@pytest.fixture()
def chat(tmp_path, monkeypatch):
    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "chat_state.sqlite"))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "chat_business.sqlite"))
    store = Store()
    user_id, _ = resolve_user_id(None, store=store)
    manager = SessionManager(store, persist="memory")
    session_id = manager.ensure_session(None, user_id)
    tools = build_tools()

    async def turn(text: str, *, request_id: str | None = None, session: str | None = None):
        MCP_CALLS.clear()
        return await run_turn(
            store=store,
            session_manager=manager,
            tools=tools,
            backend=StubBackend(),
            user_id=user_id,
            session_id=session or session_id,
            request_id=request_id or str(uuid.uuid4()),
            text=text,
        )

    return {"turn": turn, "store": store, "manager": manager, "user_id": user_id,
            "session_id": session_id, "tasks": TaskManager(store)}


@pytest.mark.anyio
async def test_scenario_a_general_chat_uses_no_mcp(chat):
    result = await chat["turn"]("你好")
    assert result["intent"] == "general_chat"
    assert MCP_CALLS == [], "ordinary chat must not call any MCP tool"
    assert result["reply"]


@pytest.mark.anyio
async def test_scenario_b_trend_query_calls_get_trend(chat):
    result = await chat["turn"]("帮我查一下最近 30 天的铜价走势")
    assert result["intent"] == "trend_query"
    assert [call[0] for call in MCP_CALLS] == ["get_trend"]
    assert MCP_CALLS[0][1]["days"] == 30
    assert result["task_status"] == "completed"


@pytest.mark.anyio
async def test_scenario_c_slot_completion_keeps_the_same_task(chat):
    first = await chat["turn"]("帮我查一下锂价")
    assert first["intent"] == "price_query"
    assert first["reply"].startswith(AMBIGUOUS_COMMODITY_QUESTION)
    assert first["task_status"] == "waiting_for_slots"
    assert MCP_CALLS == []

    second = await chat["turn"]("碳酸锂，最近 30 天")
    assert second["task_id"] == first["task_id"], "slot completion must reuse the task"
    assert [call[0] for call in MCP_CALLS] == ["get_trend"], "price_query -> trend_query is a compatible upgrade"


@pytest.mark.anyio
async def test_scenario_d_and_e_switching_between_business_and_chat(chat):
    business = await chat["turn"]("Pilbara 有多少矿产资源？")
    assert business["intent"] == "resource_query"
    assert [call[0] for call in MCP_CALLS] == ["extract_resources"]

    chatty = await chat["turn"]("Python 的 asyncio 是什么？")
    assert chatty["intent"] == "general_chat"
    assert MCP_CALLS == [], "chat must not reuse the previous business route"

    again = await chat["turn"]("昨天的碳酸锂价格是多少？")
    assert again["intent"] == "price_query"
    assert [call[0] for call in MCP_CALLS] == ["get_price"]
    assert again["task_id"] != business["task_id"], "a new query creates a new task"


@pytest.mark.anyio
async def test_scenario_f_cancel_then_chat(chat):
    first = await chat["turn"]("帮我查一下锂价")
    assert first["task_status"] == "waiting_for_slots"

    switched = await chat["turn"]("先不查了，解释一下 MCP 协议")
    assert switched["intent"] == "general_chat"
    assert MCP_CALLS == []

    pending = chat["tasks"].resumable(chat["session_id"], chat["user_id"])
    assert pending == [], "explicit cancel must close the task"

    plain = await chat["turn"]("先不查了")
    assert plain["task_status"] == "cancelled"


@pytest.mark.anyio
async def test_two_pending_tasks_can_be_resumed_independently(chat):
    """Two waiting tasks in one session, a chat detour, then resume each one."""

    lithium = await chat["turn"]("帮我查一下锂价")
    aluminium = await chat["turn"]("帮我查一下铝的价格")
    assert lithium["task_id"] != aluminium["task_id"]
    assert lithium["task_status"] == "waiting_for_slots"
    assert aluminium["task_status"] == "waiting_for_slots"
    assert MCP_CALLS == []

    chat_only = await chat["turn"]("帮我解释一下什么是 MCP 协议")
    assert chat_only["intent"] == "general_chat" and MCP_CALLS == []
    pending = chat["tasks"].resumable(chat["session_id"], chat["user_id"])
    assert {task["task_id"] for task in pending} == {lithium["task_id"], aluminium["task_id"]}

    resumed = await chat["turn"]("碳酸锂，最近 30 天")
    assert resumed["task_id"] == lithium["task_id"], "slot completion must pick the matching task"
    assert [call[0] for call in MCP_CALLS] == ["get_trend"]

    other = await chat["turn"](f"继续任务 {aluminium['task_id']}")
    assert other["task_id"] == aluminium["task_id"], "the other pending task is still resumable"
    assert other["task_status"] == "waiting_for_slots"
    assert MCP_CALLS == [], "the aluminium task still misses its date"

    still_pending = {task["task_id"] for task in chat["tasks"].resumable(chat["session_id"], chat["user_id"])}
    assert still_pending == {aluminium["task_id"]}


@pytest.mark.anyio
async def test_completed_task_cannot_be_resumed(chat):
    done = await chat["turn"]("Pilbara 有多少矿产资源？")
    assert done["task_status"] == "completed"

    result = await chat["turn"](f"继续任务 {done['task_id']}")
    assert not result.get("task_id")
    assert "不可" in result["reply"] or "没有可恢复" in result["reply"]



@pytest.mark.anyio
async def test_request_id_is_idempotent_but_text_repetition_is_not(chat):
    rid = str(uuid.uuid4())
    first = await chat["turn"]("帮我查一下最近 30 天的铜价走势", request_id=rid)
    assert [call[0] for call in MCP_CALLS] == ["get_trend"]

    replay = await chat["turn"]("帮我查一下最近 30 天的铜价走势", request_id=rid)
    assert replay.get("replayed") is True
    assert MCP_CALLS == [], "a replayed request must not execute tools again"
    assert replay["task_id"] == first["task_id"]

    fresh = await chat["turn"]("帮我查一下最近 30 天的铜价走势")
    assert fresh.get("replayed") is False
    assert fresh["task_id"] != first["task_id"], "same text with a new request_id is a new task"


@pytest.mark.anyio
async def test_tool_step_is_not_repeated_across_requests(chat):
    first = await chat["turn"]("铜最近 30 天走势")
    assert [call[0] for call in MCP_CALLS] == ["get_trend"]

    # A brand-new task with the same step and args would normally call the tool
    # again; asking for the same *task's* step twice must not.
    task_id = first["task_id"]
    import asyncio
    from minereport.agent.effects import call_tool_once

    tools = build_tools()
    MCP_CALLS.clear()
    envelope = await call_tool_once(
        chat["store"], tools, "get_trend", {"commodity": "copper", "days": 30},
        session_id=chat["session_id"], task_id=task_id, request_id="r-x", step_id="trend_query.get_trend",
    )
    assert envelope["provenance"] == "live"
    assert MCP_CALLS == [], "an already completed step must be served from the effect record"


@pytest.mark.anyio
async def test_only_the_needed_tool_is_called_per_intent(chat):
    cases = {
        "Pilbara 的资源量是多少？": ["extract_resources"],
        "昨天的碳酸锂价格": ["get_price"],
        "碳酸锂最近 30 天走势": ["get_trend"],
        "查一下 Pilbara 最新的新闻": ["search"],
    }
    for text, expected in cases.items():
        await chat["turn"](text)
        assert [call[0] for call in MCP_CALLS] == expected, text


@pytest.mark.anyio
async def test_sessions_are_isolated(chat):
    other_session = chat["manager"].ensure_session(None, chat["user_id"])
    first = await chat["turn"]("帮我查一下锂价")
    assert first["task_status"] == "waiting_for_slots"

    result = await chat["turn"]("碳酸锂，最近 30 天", session=other_session)
    assert str(result.get("task_id") or "") != first["task_id"]
    assert MCP_CALLS == []

    pending = chat["tasks"].resumable(chat["session_id"], chat["user_id"])
    assert [task["task_id"] for task in pending] == [first["task_id"]]
    assert chat["tasks"].resumable(other_session, chat["user_id"]) == []


@pytest.mark.anyio
async def test_busy_database_at_finish_degrades_instead_of_crashing(chat, monkeypatch):
    """A lock conflict must never kill the multi-turn REPL (the reported crash).

    The turn has to come back with a readable reply, keep the task record intact
    and surface the conflict in ``errors`` so the user can simply retry.
    """

    from minereport.agent.session.store import StoreBusyError

    def always_busy(*args, **kwargs):
        raise StoreBusyError("business database is busy after retries: database is locked")

    monkeypatch.setattr(chat["store"], "finish_request", always_busy)

    result = await chat["turn"]("帮我查一下最近 30 天的铜价走势")

    assert "繁忙" in result["reply"], result["reply"]
    assert any(item.get("code") == "store-busy" for item in result["errors"]), result["errors"]
    assert result["task_status"] == "completed"

    tasks = chat["tasks"].all_for_session(chat["session_id"], chat["user_id"])
    assert len(tasks) == 1 and tasks[0]["status"] == "completed", tasks