"""Boot every server listed in a generated MCP config and list its tools.

Evidence for the delivery item "mcp-config.json is directly usable by Claude
Desktop / Cursor": the client is emulated exactly, i.e. each server is started
with the ``command`` + ``args`` from the file, over stdio.

    uv run --no-sync python scripts/verify_mcp_config.py [config.json]
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIGS = ("mcp-config.json", "mcp-config.stdio.local.json", "mcp-config.stdio.example.json")

# Tool names the brief requires; extra tools are fine, missing ones are not.
REQUIRED = {
    "mining-news-mcp": {"search", "fetch_article"},
    "mineral-pdf-mcp": {"extract_resources"},
    "lme-price-mcp": {"get_price", "get_trend"},
}


async def probe(name: str, spec: dict) -> tuple[str, list[str], str | None]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    if "url" in spec:  # HTTP transport: leave it to the client, not this checker
        return name, [], "url-based server (skipped: start the HTTP service instead)"

    params = StdioServerParameters(command=spec["command"], args=list(spec.get("args") or []))
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
        return name, sorted(tool.name for tool in listed.tools), None
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return name, [], f"{type(exc).__name__}: {exc}"


def resolve(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    for candidate in DEFAULT_CONFIGS:
        path = ROOT / candidate
        if path.is_file():
            return path
    raise SystemExit(f"none of {DEFAULT_CONFIGS} found in {ROOT}")


async def main() -> int:
    config_path = resolve(sys.argv[1] if len(sys.argv) > 1 else None)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    servers = config.get("mcpServers") or {}
    if not servers:
        print(f"FAIL: {config_path} has no mcpServers")
        return 1

    if any("<PROJECT_PATH>" in json.dumps(spec) for spec in servers.values()):
        print(f"config : {config_path}")
        print("SKIP: this is the portable template with the <PROJECT_PATH> placeholder.")
        print("      Run `uv run python scripts/generate_mcp_config.py` first, then check mcp-config.json.")
        return 2

    print(f"config : {config_path}")
    failures: list[str] = []
    for name, spec in servers.items():
        probed, tools, error = await probe(name, spec)
        if error:
            print(f"  {probed:18s} FAIL {error}")
            failures.append(f"{probed}: {error}")
            continue
        print(f"  {probed:18s} OK   tools={','.join(tools)}")
        missing = REQUIRED.get(probed, set()) - set(tools)
        if missing:
            failures.append(f"{probed}: missing required tools {sorted(missing)}")

    print()
    if failures:
        print("FAIL")
        for line in failures:
            print("  - " + line)
        return 1
    print(f"PASS: all {len(servers)} servers booted from {config_path.name} and exposed the required tools.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))