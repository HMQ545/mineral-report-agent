"""Top-level conversational agent: routing, slots, tasks, MCP dispatch, chat.

The daily-report workflow (``graph.build_graph``) is reused unchanged through an
explicit input/output adapter, invoked imperatively so no assumption is made that
``AgentState`` is a superset of ``BriefState``.

No ``interrupt()`` is used: a task that misses slots is stored as
``waiting_for_slots`` and resumed on a later turn through the TaskManager, which
keeps ``thread_id = session_id`` free of multiple paused execution sites.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.graph import END, StateGraph

from ..common.provenance import utc_now_iso
from ..pdf import registry as pdf_registry
from .effects import EffectFailedError, StepBusyError, call_tool_once
from .session.store import SessionBusyError, StoreBusyError
from .graph import build_graph as build_brief_graph
from .graph import collect_provenance, live_gate_failures
from .intent.classifier import classify
from .intent.extractor import extract_slots
from .intent.filling import fill_slots
from .intent.schemas import SLOT_LABELS, Intent, TurnAction
from .intent.validator import validate_slots
from .llm import LlmError
from .nodes.chat import CHAT_SYSTEM, chat_prompt
from .state import AgentState, error_entry
from .task.manager import TaskManager
from .task.matcher import TaskMatcher

CN_TZ = timezone(timedelta(hours=8))
AMBIGUOUS_COMMODITY_QUESTION = "你想查碳酸锂还是锂辉石精矿？"


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _error(code: str, message: str) -> dict[str, Any]:
    return error_entry(code, message)


def build_conversation_graph(*, store: Any, tools: dict[str, Any], backend: Any, session_manager: Any, checkpointer: Any):
    task_manager = TaskManager(store)
    matcher = TaskMatcher(store, backend=backend)
    brief_graph = build_brief_graph(tools, backend)

    # ------------------------------------------------------------------ nodes
    async def session_manager_node(state: AgentState) -> dict[str, Any]:
        store.assert_owner(state["session_id"], state["user_id"])
        pending = task_manager.resumable(state["session_id"], state["user_id"])
        return {
            "pending_tasks": [
                {
                    "task_id": task["task_id"],
                    "intent": task["intent"],
                    "status": task["status"],
                    "slots": task["slots"],
                    "missing_slots": task["missing_slots"],
                    "description": task.get("description", ""),
                    "created_at": task["created_at"],
                }
                for task in pending
            ],
            # Reset every per-turn field: the checkpointer keeps the thread alive,
            # so a previous task must never leak into the next turn.
            "task_id": "",
            "intent": "",
            "turn_action": "",
            "intent_reason": "",
            "matcher_status": "",
            "reply": "",
            "task_status": "",
            "suspended": False,
            "also_cancel": False,
            "extracted_slots": {},
            "slots": {},
            "slot_sources": {},
            "missing_slots": [],
            "resume_candidates": [],
            "cancelled_ids": [],
            "intent_notes": [],
            "report_path": "",
            "report_markdown": "",
            "live_gate_failures": [],
        }

    async def intent_recognition(state: AgentState) -> dict[str, Any]:
        text = state["query"]
        extracted, extract_notes = extract_slots(text)
        result = await classify(
            text,
            backend=backend,
            pending_tasks=state.get("pending_tasks") or [],
            extracted=extracted,
        )
        return {
            "extracted_slots": extracted,
            "intent": result.intent.value,
            "turn_action": result.action.value,
            "intent_confidence": result.confidence,
            "intent_reason": result.reason,
            "also_cancel": result.also_cancel,
            "intent_notes": list(extract_notes) + [f"意图来源：{result.source}"],
        }

    async def slot_extraction(state: AgentState) -> dict[str, Any]:
        extracted, notes = extract_slots(state["query"])
        return {"extracted_slots": extracted, "intent_notes": (state.get("intent_notes") or []) + notes}

    async def task_matcher(state: AgentState) -> dict[str, Any]:
        action = state.get("turn_action")
        if action not in {TurnAction.CONTINUE_TASK.value, TurnAction.SLOT_COMPLETION.value}:
            return {}
        outcome = await matcher.match(
            state["query"],
            user_id=state["user_id"],
            session_id=state["session_id"],
            requested_intent=state.get("intent"),
            extracted=state.get("extracted_slots") or {},
        )
        payload: dict[str, Any] = {"matcher_status": outcome.status}
        if outcome.is_unique:
            task = outcome.task or {}
            payload["task_id"] = task.get("task_id")
            payload["task_status"] = task.get("status")
            payload["intent"] = task.get("intent", state.get("intent"))
            payload["resume_candidates"] = []
        elif outcome.status == "ambiguous":
            payload["resume_candidates"] = outcome.candidates
            payload["reply"] = _candidate_question(outcome.candidates)
        else:
            payload["reply"] = f"没有可恢复的任务：{outcome.reason}"
        return payload

    async def slot_filling(state: AgentState) -> dict[str, Any]:
        requested = Intent(state.get("intent") or Intent.UNKNOWN.value)
        inherited: dict[str, Any] = {}
        base = requested
        task_id = state.get("task_id")
        if task_id:
            task = task_manager.get(task_id) or {}
            inherited = task.get("slots") or {}
            base = Intent(task.get("intent") or requested.value)

        merged, sources = fill_slots(base, state.get("extracted_slots") or {}, inherited)
        # Explicit, compatible goal refinement only (see the compatibility matrix):
        #   * a duration asks for a trend  (price_query -> trend_query)
        #   * a single date asks for a price (trend_query -> price_query)
        from .intent.schemas import is_compatible_transition

        intent = base
        query = state["query"]
        wants_trend = merged.get("days") or any(word in query for word in ("走势", "趋势"))
        wants_price = merged.get("date") and not merged.get("days")
        if base == Intent.PRICE_QUERY and wants_trend and is_compatible_transition(base, Intent.TREND_QUERY):
            intent = Intent.TREND_QUERY
            sources["intent"] = "compatible-upgrade(price→trend)"
            merged, extra = fill_slots(intent, state.get("extracted_slots") or {}, merged)
            sources.update(extra)
        elif base == Intent.TREND_QUERY and wants_price and is_compatible_transition(base, Intent.PRICE_QUERY):
            intent = Intent.PRICE_QUERY
            sources["intent"] = "compatible-narrowing(trend→price)"
        elif is_compatible_transition(base, requested):
            intent = requested
        return {"slots": merged, "slot_sources": sources, "intent": intent.value}

    async def slot_validation(state: AgentState) -> dict[str, Any]:
        intent = Intent(state.get("intent") or Intent.UNKNOWN.value)
        resolution = validate_slots(intent, state.get("slots") or {})
        updates: dict[str, Any] = {
            "slots": resolution.slots.model_dump(exclude_none=True),
            "missing_slots": resolution.missing,
        }
        if resolution.errors:
            updates["errors"] = [_error("slot-invalid", err) for err in resolution.errors]
        return updates

    async def task_manager_node(state: AgentState) -> dict[str, Any]:
        intent = state.get("intent") or Intent.UNKNOWN.value
        slots = state.get("slots") or {}
        missing = state.get("missing_slots") or []
        task_id = state.get("task_id")

        if intent in {Intent.GENERAL_CHAT.value, Intent.UNKNOWN.value}:
            return {}

        try:
            if task_id:
                task_manager.resume(task_id)
                task = task_manager.update(
                    task_id,
                    intent=intent,
                    status="waiting_for_slots" if missing else "running",
                    suspended=False,
                    slots=slots,
                    missing=missing,
                    slot_sources=state.get("slot_sources") or {},
                )
            else:
                task = task_manager.create(
                    session_id=state["session_id"],
                    user_id=state["user_id"],
                    intent=intent,
                    slots=slots,
                    missing=missing,
                    description=state["query"][:120],
                )
        except SessionBusyError as exc:
            # The business database is momentarily busy: report it instead of
            # letting the whole conversation die.
            return {
                "errors": [_error("store-busy", str(exc))],
                "reply": "数据库暂时繁忙，本次请求未写入。请稍后重试（已有会话与任务未受影响）。",
                "task_status": "failed",
            }
        return {"task_id": task["task_id"], "task_status": task["status"], "suspended": task["suspended"]}

    def route(state: AgentState) -> str:
        action = state.get("turn_action")
        if action == TurnAction.CANCEL_TASK.value or state.get("also_cancel"):
            return "cancel"
        if action == TurnAction.GENERAL_CHAT.value:
            return "chat"
        if state.get("matcher_status") == "ambiguous" or (
            not state.get("task_id") and action in {TurnAction.CONTINUE_TASK.value, TurnAction.SLOT_COMPLETION.value}
        ):
            return "clarify"
        if state.get("intent") == Intent.UNKNOWN.value:
            return "clarify"
        if state.get("missing_slots"):
            return "clarify"
        if state.get("errors"):
            return "clarify"
        intent = state.get("intent")
        if intent == Intent.DAILY_REPORT.value:
            return "daily_report"
        if intent == Intent.NEWS_SEARCH.value:
            return "news"
        if intent == Intent.RESOURCE_QUERY.value:
            return "resource"
        if intent == Intent.PRICE_QUERY.value:
            return "price"
        if intent == Intent.TREND_QUERY.value:
            return "trend"
        return "clarify"

    async def clarify(state: AgentState) -> dict[str, Any]:
        if state.get("reply"):
            return {"reply": state["reply"], "task_status": "waiting_for_slots", "suspended": True}
        missing = state.get("missing_slots") or []
        notes = " ".join(state.get("intent_notes") or [])
        if "commodity" in missing and ("歧义" in notes or "锂" in state["query"]):
            question = AMBIGUOUS_COMMODITY_QUESTION
        elif missing:
            labels = "、".join(SLOT_LABELS.get(slot, slot) for slot in missing)
            question = f"还需要你补充：{labels}。请直接说明，我会继续这个任务。"
        else:
            question = "我还不能确定你的目标，可以说明一下要查新闻、资源量还是行情吗？"
        if state.get("intent") == Intent.UNKNOWN.value:
            question = "无法可靠判断你的需求：" + question
        return {"reply": question, "task_status": "waiting_for_slots", "suspended": True}

    async def cancel(state: AgentState) -> dict[str, Any]:
        cancelled: list[str] = []
        task_id = state.get("task_id")
        if task_id:
            task_manager.cancel(task_id, "用户取消")
            cancelled.append(task_id)
        else:
            for task in task_manager.resumable(state["session_id"], state["user_id"]):
                task_manager.cancel(task["task_id"], "用户取消")
                cancelled.append(task["task_id"])
        reply = f"已取消 {len(cancelled)} 个未完成任务。" if cancelled else "当前没有可取消的任务。"
        return {"reply": reply, "task_status": "cancelled", "cancelled_ids": cancelled}

    async def chat(state: AgentState) -> dict[str, Any]:
        recent = [
            f"{getattr(m, 'type', 'user')}: {getattr(m, 'content', m)}"
            for m in (state.get("messages") or [])[-6:]
        ]
        prompt = chat_prompt(state["query"], history_summary=state.get("history_summary") or "", recent=recent)
        reply = await backend.text(CHAT_SYSTEM, prompt, stage="general_chat")
        return {"reply": reply, "task_status": "chat"}

    def _sources_for(slots: dict[str, Any]) -> dict[str, Any] | None:
        company = (slots.get("company") or "").lower()
        for source in pdf_registry.all_sources():
            if source.get("role") != "primary-deliverable" and not company:
                continue
            if company and (
                company in (source.get("reporter_entity") or "").lower()
                or any(keyword in company for keyword in source.get("keywords", []))
            ):
                return source
        return pdf_registry.default_source(role="primary-deliverable")

    async def daily_report(state: AgentState) -> dict[str, Any]:
        slots = state.get("slots") or {}
        source = _sources_for(slots)
        brief_input: dict[str, Any] = {
            "query": state["query"],
            "days": slots.get("days") or 7,
            "require_live": state.get("require_live", False),
            "llm_mode": backend.mode,
            "out_dir": state.get("out_dir"),
            "commodity": slots.get("commodity"),
            "reserve_source": source,
            "topic": (source or {}).get("project_name") or state["query"][:40],
            "news_query": f"{((source or {}).get('keywords') or ['lithium'])[0].title()} lithium",
            "retrieved_at": utc_now_iso(),
            "errors": [],
        }
        try:
            brief_result = await brief_graph.ainvoke(brief_input)
        except LlmError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the task record
            return {
                "errors": [_error("daily-report", f"{type(exc).__name__}: {exc}")],
                "reply": f"日报生成失败：{type(exc).__name__}: {exc}",
                "task_status": "failed",
            }
        failures = live_gate_failures(brief_result)
        return {
            "report_path": brief_result.get("report_path", ""),
            "report_markdown": brief_result.get("report_markdown", ""),
            "provenance_map": collect_provenance(brief_result),
            "live_gate_failures": failures,
            "errors": [_error("brief", message) for message in (brief_result.get("errors") or [])],
            "reply": f"日报已生成：{brief_result.get('report_path')}"
            + (f"（Live 校验未通过的项：{'; '.join(failures)}）" if failures else ""),
            "task_status": "completed",
        }

    async def run_news(state: AgentState) -> dict[str, Any]:
        slots = state.get("slots") or {}
        arguments = {"query": slots.get("query") or state["query"], "days": slots.get("days") or 7}
        return await _single_tool(state, "search", arguments, step_id="news_search.search", summarise=_summarise_news)

    async def run_resource(state: AgentState) -> dict[str, Any]:
        slots = state.get("slots") or {}
        arguments: dict[str, Any] = {}
        if slots.get("pdf_ref"):
            arguments["pdf_url"] = slots["pdf_ref"]
        if state.get("require_live"):
            arguments["refresh"] = True
        return await _single_tool(
            state, "extract_resources", arguments, step_id="resource_query.extract_resources", summarise=_summarise_resources
        )

    async def run_price(state: AgentState) -> dict[str, Any]:
        slots = state.get("slots") or {}
        arguments = {"commodity": slots.get("commodity"), "date": slots.get("date")}
        return await _single_tool(state, "get_price", arguments, step_id="price_query.get_price", summarise=_summarise_price)

    async def run_trend(state: AgentState) -> dict[str, Any]:
        slots = state.get("slots") or {}
        arguments = {"commodity": slots.get("commodity"), "days": slots.get("days") or 30}
        return await _single_tool(state, "get_trend", arguments, step_id="trend_query.get_trend", summarise=_summarise_trend)

    async def _single_tool(state: AgentState, name: str, arguments: dict[str, Any], *, step_id: str, summarise) -> dict[str, Any]:
        task_id = state.get("task_id") or f"adhoc-{state['session_id']}"
        try:
            envelope = await call_tool_once(
                store,
                tools,
                name,
                arguments,
                session_id=state["session_id"],
                task_id=task_id,
                request_id=state["request_id"],
                step_id=step_id,
            )
        except StepBusyError as exc:
            return {"reply": f"该步骤正在执行中，请稍后重试（{exc}）", "errors": [_error("step-busy", str(exc))]}
        except EffectFailedError as exc:
            return {"reply": f"调用失败：{exc}", "errors": [_error("effect-failed", str(exc))], "task_status": "failed"}
        except Exception as exc:  # noqa: BLE001
            return {
                "reply": f"{name} 调用失败：{type(exc).__name__}: {exc}",
                "errors": [_error(f"{name}-failed", f"{type(exc).__name__}: {exc}")],
                "task_status": "failed",
            }
        return {
            "tool_results": [
                {
                    "tool_call_id": str(uuid.uuid4()),
                    "tool": name,
                    "step_id": step_id,
                    "task_id": task_id,
                    "arguments": arguments,
                }
            ],
            "reply": summarise(envelope, arguments),
            "provenance_map": {"last_tool": {"provenance": envelope.get("provenance"), "source": envelope.get("source")}},
            "task_status": "completed",
        }

    async def finish(state: AgentState) -> dict[str, Any]:
        errors = list(state.get("errors") or [])
        status = state.get("task_status") or ""
        task_id = state.get("task_id")
        reply = state.get("reply") or "（没有生成回复）"
        try:
            if task_id and status in {"completed", "failed"}:
                if status == "completed":
                    task_manager.complete(task_id, {"reply": state.get("reply", "")})
                else:
                    message = "; ".join(item.get("message", "") for item in (state.get("errors") or []))
                    task_manager.fail(task_id, message or "unknown failure")
            elif task_id and status == "waiting_for_slots":
                task_manager.update(task_id, suspended=bool(state.get("suspended", True)))
            store.finish_request(
                state["session_id"], state["request_id"], status="completed", task_id=task_id, reply=reply
            )
        except SessionBusyError as exc:
            # Never crash the REPL on a lock conflict; the task record keeps its
            # previous state and the user can simply retry.
            errors.append(_error("store-busy", str(exc)))
            reply = "数据库暂时繁忙，任务状态已保留。请重试刚才的输入。"
        return {
            "messages": [{"role": "assistant", "content": reply}],
            "reply": reply,
            "errors": errors,
        }

    graph = StateGraph(AgentState)
    graph.add_node("session_manager", session_manager_node)
    graph.add_node("intent_recognition", intent_recognition)
    graph.add_node("slot_extraction", slot_extraction)
    graph.add_node("task_matcher", task_matcher)
    graph.add_node("slot_filling", slot_filling)
    graph.add_node("slot_validation", slot_validation)
    graph.add_node("task_manager", task_manager_node)
    graph.add_node("clarify", clarify)
    graph.add_node("chat", chat)
    graph.add_node("cancel", cancel)
    graph.add_node("daily_report", daily_report)
    graph.add_node("news", run_news)
    graph.add_node("resource", run_resource)
    graph.add_node("price", run_price)
    graph.add_node("trend", run_trend)
    graph.add_node("finish", finish)

    graph.set_entry_point("session_manager")
    graph.add_edge("session_manager", "intent_recognition")
    graph.add_edge("intent_recognition", "slot_extraction")
    graph.add_edge("slot_extraction", "task_matcher")
    graph.add_edge("task_matcher", "slot_filling")
    graph.add_edge("slot_filling", "slot_validation")
    graph.add_edge("slot_validation", "task_manager")
    graph.add_conditional_edges(
        "task_manager",
        route,
        {
            "cancel": "cancel",
            "chat": "chat",
            "clarify": "clarify",
            "daily_report": "daily_report",
            "news": "news",
            "resource": "resource",
            "price": "price",
            "trend": "trend",
        },
    )
    for node in ("cancel", "chat", "clarify", "daily_report", "news", "resource", "price", "trend"):
        graph.add_edge(node, "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


def _candidate_question(candidates: list[dict[str, Any]]) -> str:
    lines = ["有多个未完成任务可能匹配，请确认要继续哪一个："]
    for index, task in enumerate(candidates[:5], start=1):
        missing = "、".join(SLOT_LABELS.get(slot, slot) for slot in (task.get("missing_slots") or [])) or "无"
        lines.append(
            f"{index}. task_id={task['task_id']}（意图 {task['intent']}，缺失槽位：{missing}，创建于 {task.get('created_at')}）"
        )
    lines.append("回复对应的 task_id 或直接补充参数即可。")
    return "\n".join(lines)


def _summarise_news(envelope: dict[str, Any], _args: dict[str, Any]) -> str:
    if envelope.get("error"):
        return f"新闻查询失败：{envelope['error']}"
    data = envelope.get("data") or {}
    stats = data.get("stats") or {}
    company = data.get("company_items") or []
    industry = data.get("industry_items") or []
    lines = [
        f"新闻检索完成（provenance={envelope.get('provenance')}）：公司直接相关 {len(company)} 条，行业背景 {len(industry)} 条，过滤无关 {stats.get('excluded_irrelevant', 0)} 条。"
    ]
    for item in company[:3] or industry[:3]:
        lines.append(f"- {item['title']}（{item.get('source')}，{item.get('published_at')}）")
    if not company:
        lines.append("检索期内暂无可验证的公司直接相关新闻。")
    return "\n".join(lines)


def _summarise_resources(envelope: dict[str, Any], _args: dict[str, Any]) -> str:
    if envelope.get("error"):
        return f"资源量查询失败：{envelope['error']}"
    data = envelope.get("data") or {}
    counts = data.get("counts") or {}
    lines = [
        f"资源量解析完成（provenance={envelope.get('provenance')}）：{data.get('reporter_entity')} / {data.get('project_name')}"
    ]
    for row in (data.get("mineral_resources") or [])[:4]:
        lines.append(f"- {row['category']}: {row.get('tonnes')}{row.get('tonnes_unit') or ''} @ {row.get('grade')}{row.get('grade_unit') or ''}")
    lines.append(f"（主表 {counts.get('mineral_resource_rows', 0)} 行；披露日期 {data.get('report_date')}）")
    return "\n".join(lines)


def _summarise_price(envelope: dict[str, Any], args: dict[str, Any]) -> str:
    if envelope.get("error"):
        return f"行情查询失败：{envelope['error']}"
    data = envelope.get("data") or {}
    return (
        f"{data.get('display_name')}：{data.get('price')} {data.get('unit')}"
        f"（{data.get('as_of')}，provenance={envelope.get('provenance')}，参数 {json.dumps(args, ensure_ascii=False)}）"
    )


def _summarise_trend(envelope: dict[str, Any], args: dict[str, Any]) -> str:
    if envelope.get("error"):
        return f"走势查询失败：{envelope['error']}"
    data = envelope.get("data") or {}
    coverage = data.get("coverage") or {}
    return (
        f"{data.get('display_name')} 近 {args.get('days')} 天走势："
        f"{coverage.get('start')} ~ {coverage.get('end')}，{coverage.get('trading_days')} 个交易日，"
        f"区间涨跌 {data.get('change_pct')}%（provenance={envelope.get('provenance')}）"
    )


async def run_turn(
    *,
    store: Any,
    session_manager: Any,
    tools: dict[str, Any],
    backend: Any,
    user_id: str,
    session_id: str,
    request_id: str,
    text: str,
    require_live: bool = False,
    out_dir: Any = None,
    persist: str = "sqlite",
) -> dict[str, Any]:
    """One conversational turn: request dedupe, graph invocation, history."""

    task_manager = TaskManager(store)

    replay = task_manager.replay(session_id, request_id)
    if replay:
        return {
            "reply": replay.get("reply") or "",
            "task_id": replay.get("task_id") or "",
            "task_status": "replayed",
            "replayed": True,
            "intent": "",
            "slots": {},
            "missing_slots": [],
        }

    task_manager.begin_request(session_id, request_id, "")

    graph = build_conversation_graph(
        store=store,
        tools=tools,
        backend=backend,
        session_manager=session_manager,
        checkpointer=await session_manager.checkpointer(),
    )
    state: dict[str, Any] = {
        "query": text,
        "user_id": user_id,
        "session_id": session_id,
        "request_id": request_id,
        "require_live": require_live,
        "llm_mode": backend.mode,
        "persist": persist,
        "out_dir": str(out_dir) if out_dir else "",
        "messages": [{"role": "user", "content": text}],
        "retrieved_at": utc_now_iso(),
        "errors": [],
        "tool_results": [],
    }
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": session_id}})
    result["replayed"] = False
    return result


__all__ = ["AMBIGUOUS_COMMODITY_QUESTION", "build_conversation_graph", "run_turn"]