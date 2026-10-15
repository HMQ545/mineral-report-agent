"""Connect the agent to the three MCP servers (stdio locally, HTTP in Docker)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

SERVER_SPECS: dict[str, dict[str, Any]] = {
    "news": {"module": "minereport.news", "port": 8101, "env": "MINEREPORT_NEWS_URL"},
    "pdf": {"module": "minereport.pdf", "port": 8102, "env": "MINEREPORT_PDF_URL"},
    "price": {"module": "minereport.price", "port": 8103, "env": "MINEREPORT_PRICE_URL"},
}


def build_connections() -> dict[str, dict[str, Any]]:
    """HTTP when a URL is configured, otherwise launch the server over stdio."""

    connections: dict[str, dict[str, Any]] = {}
    for name, spec in SERVER_SPECS.items():
        url = os.environ.get(spec["env"], "").strip()
        if url:
            connections[name] = {"url": url, "transport": "streamable_http"}
        else:
            connections[name] = {
                "command": sys.executable,
                "args": ["-m", spec["module"], "--transport", "stdio"],
                "transport": "stdio",
            }
    return connections


def coerce_json(result: Any) -> dict[str, Any]:
    """MCP tool results arrive as text or a list of content blocks."""

    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return {"raw": result}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                return coerce_json(item["text"])
            if isinstance(item, str):
                return coerce_json(item)
        return {"raw": result}
    return {"raw": result}


def resolve_tool(tools: dict[str, Any], name: str) -> Any | None:
    """Tool names may be plain or prefixed with the server name."""

    if name in tools:
        return tools[name]
    for key, tool in tools.items():
        if key.endswith(f"__{name}") or key.endswith(f"_{name}") or key.split(".")[-1] == name:
            return tool
    return None


async def open_tools(*, timeout: float = 90.0) -> dict[str, Any]:
    """Return ``{tool_name: tool}`` once every server answers."""

    from langchain_mcp_adapters.client import MultiServerMCPClient

    connections = build_connections()
    client = MultiServerMCPClient(connections)

    delay = 0.5
    deadline = asyncio.get_event_loop().time() + timeout
    last_error: Exception | None = None
    while True:
        try:
            tools = await client.get_tools()
            if tools:
                return {tool.name: tool for tool in tools}
        except Exception as exc:  # noqa: BLE001 - retried, then surfaced
            last_error = exc
        if asyncio.get_event_loop().time() >= deadline:
            raise RuntimeError(
                f"MCP servers not ready after {timeout}s "
                f"(connections={list(connections)}); last error: {last_error}"
            ) from last_error
        await asyncio.sleep(delay)
        delay = min(delay * 2, 5.0)


async def call_tool(tools: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = resolve_tool(tools, name)
    if tool is None:
        raise KeyError(f"tool {name!r} not found; available: {sorted(tools)}")
    raw = await tool.ainvoke(arguments)
    return coerce_json(raw)


__all__ = ["SERVER_SPECS", "build_connections", "call_tool", "coerce_json", "open_tools", "resolve_tool"]

