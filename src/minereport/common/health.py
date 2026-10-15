"""``/healthz`` endpoint that proves MCP initialisation and tool discovery."""

from __future__ import annotations

import inspect
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from .provenance import utc_now_iso


def register_health(mcp: Any, *, service: str, version: str) -> None:
    """Expose ``GET /healthz`` on the same HTTP app that serves MCP."""

    started_at = time.time()

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> JSONResponse:  # pragma: no cover - needs a live server
        tools: list[str] = []
        error: str | None = None
        try:
            listed = mcp.list_tools()
            if inspect.isawaitable(listed):
                listed = await listed
            tools = [getattr(tool, "name", str(tool)) for tool in listed]
        except Exception as exc:  # noqa: BLE001 - surfaced in the payload, never swallowed
            error = f"{type(exc).__name__}: {exc}"

        ready = error is None and bool(tools)
        payload = {
            "service": service,
            "status": "ok" if ready else "starting",
            "ready": ready,
            "tools": tools,
            "tool_count": len(tools),
            "version": version,
            "mcp_initialized": error is None,
            "uptime_s": round(time.time() - started_at, 3),
            "checked_at": utc_now_iso(),
        }
        if error:
            payload["error"] = error
        return JSONResponse(payload, status_code=200 if ready else 503)