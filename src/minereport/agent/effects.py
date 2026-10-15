"""Idempotent MCP execution: request dedupe and tool-call dedupe are separate.

* request level  -> ``(session_id, request_id)``                (TaskManager)
* tool level     -> ``(task_id, step_id, tool_name, args_hash)``  (this module)

Honest limitation (also documented): the MCP call and the SQLite commit are not
atomic, so a crash between a successful MCP response and persisting it can still
cause a repeat call. The goal is traceable, resumable, repeat-minimising
behaviour - not a claim of exactly-once semantics.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from .mcp_client import call_tool
from .session.store import Store, effect_key


class StepBusyError(RuntimeError):
    """Another worker currently owns this step's lease."""


class EffectFailedError(RuntimeError):
    """The stored effect failed, or its cached result can no longer be trusted."""


async def call_tool_once(
    store: Store,
    tools: dict[str, Any],
    name: str,
    arguments: dict[str, Any],
    *,
    session_id: str,
    task_id: str,
    request_id: str,
    step_id: str,
    allow_retry: bool = True,
    lease_seconds: int = 120,
) -> dict[str, Any]:
    """Run one MCP tool at most once per ``(task, step, tool, args)`` key."""

    key = effect_key(task_id, step_id, name, arguments)
    tool_call_id = str(uuid.uuid4())

    claimed, record = await asyncio.to_thread(
        store.claim_step,
        key,
        session_id=session_id,
        request_id=request_id,
        tool_call_id=tool_call_id,
        lease_seconds=lease_seconds,
        allow_retry=allow_retry,
    )

    if not claimed:
        status = record.get("status")
        if status == "completed":
            result = await asyncio.to_thread(store.restore_result, record)
            if result is None:
                raise EffectFailedError(
                    "cached tool result is missing or its digest no longer matches; refusing to reuse it"
                )
            return result
        if status == "running":
            raise StepBusyError(
                f"step {step_id!r} of task {task_id} is already running "
                f"(lease until {record.get('lease_expires_at')})"
            )
        raise EffectFailedError(record.get("error") or f"step {step_id!r} previously failed")

    try:
        result = await call_tool(tools, name, arguments)
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        await asyncio.to_thread(store.fail_step, key, error=f"{type(exc).__name__}: {exc}")
        raise
    await asyncio.to_thread(store.complete_step, key, result=result, tool_call_id=tool_call_id)
    return result


__all__ = ["EffectFailedError", "StepBusyError", "call_tool_once"]