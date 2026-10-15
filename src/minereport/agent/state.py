"""LangGraph state shared by every node of the daily-brief workflow."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class BriefState(TypedDict, total=False):
    # inputs
    query: str
    days: int
    require_live: bool
    llm_mode: str

    # resolved intent
    topic: str
    news_query: str
    commodity: str
    reserve_source: dict[str, Any] | None
    intent_notes: list[str]

    # raw MCP results (each is a full envelope dict)
    news: dict[str, Any] | None
    company_news: list[dict[str, Any]]
    industry_news: list[dict[str, Any]]
    articles: list[dict[str, Any]]
    reserves: dict[str, Any] | None
    price: dict[str, Any] | None
    trend: dict[str, Any] | None
    optional_market: list[dict[str, Any]]

    # derived
    news_digest: dict[str, Any] | None
    news_pipeline: dict[str, Any]
    evidence_index: dict[str, dict[str, Any]]
    claims: list[dict[str, Any]]
    claim_issues: list[dict[str, Any]]
    quality: dict[str, Any]
    risks: list[dict[str, Any]]
    judgement: str
    llm_risks: dict[str, Any] | None
    provenance_map: dict[str, dict[str, Any]]
    unverified: list[dict[str, Any]]
    # written by three parallel nodes, so it needs a reducer
    errors: Annotated[list[str], operator.add]
    live_gate_failures: list[str]

    # plumbing
    slug: str
    out_dir: Any
    retrieved_at: str

    # output
    generated_at: str
    report_markdown: str
    report_path: str


DATASET_ORDER = ("news", "reserves", "price", "trend")
REQUIRED_DATASETS = ("news", "reserves", "price")


def blank_state(query: str, *, days: int = 7, require_live: bool = False, llm_mode: str = "auto") -> BriefState:
    return BriefState(
        query=query,
        days=days,
        require_live=require_live,
        llm_mode=llm_mode,
        intent_notes=[],
        articles=[],
        company_news=[],
        industry_news=[],
        optional_market=[],
        risks=[],
        news_pipeline={},
        evidence_index={},
        claims=[],
        claim_issues=[],
        quality={},
        provenance_map={},
        unverified=[],
        errors=[],
        live_gate_failures=[],
    )


__all__ = [
    "AgentState",
    "DATASET_ORDER",
    "REQUIRED_DATASETS",
    "BriefState",
    "blank_state",
    "error_entry",
    "merge_by_key",
]



def merge_by_key(field: str):
    """Reducer that keeps one entry per stable id (order preserving)."""

    def reducer(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
        merged: dict[str, Any] = {}
        order: list[str] = []
        for batch in (left or [], right or []):
            for item in batch:
                if isinstance(item, dict) and item.get(field):
                    key = str(item[field])
                else:
                    key = f"anon::{len(merged)}"
                if key not in merged:
                    order.append(key)
                merged[key] = item
        return [merged[key] for key in order]

    return reducer


class AgentState(TypedDict, total=False):
    """Top-level conversational state (a superset of the brief fields we map)."""

    # identifiers / turn plumbing
    user_id: str
    session_id: str
    task_id: str
    request_id: str
    query: str
    require_live: bool
    llm_mode: str
    persist: str
    out_dir: Any
    tools: Any
    backend: Any
    store: Any
    pending_tasks: list[dict[str, Any]]
    created_at: str
    generated_at: str
    retrieved_at: str

    # conversation
    messages: Annotated[list[Any], add_messages]
    history_summary: str

    # routing / intent
    turn_action: str
    intent: str
    intent_confidence: float
    intent_reason: str
    also_cancel: bool
    matcher_status: str

    # slots / task
    extracted_slots: dict[str, Any]
    slots: dict[str, Any]
    missing_slots: list[str]
    slot_sources: dict[str, str]
    intent_notes: list[str]
    task_status: str
    suspended: bool
    resume_candidates: list[dict[str, Any]]
    cancelled_ids: list[str]

    # outputs
    reply: str
    report_path: str
    report_markdown: str
    provenance_map: dict[str, dict[str, Any]]
    live_gate_failures: list[str]

    # stable-id collections (NOT operator.add: retries must not duplicate)
    tool_results: Annotated[list[dict[str, Any]], merge_by_key("tool_call_id")]
    errors: Annotated[list[dict[str, Any]], merge_by_key("error_id")]


def error_entry(code: str, message: str) -> dict[str, Any]:
    return {"error_id": f"{code}:{hash(message) & 0xFFFFFFFF:x}", "code": code, "message": message}
