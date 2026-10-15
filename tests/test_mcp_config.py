"""Portability of the MCP config: the template, the generator, and real startup.

The point of these tests is that a public checkout must never contain somebody's
absolute path, and that whatever the generator produces is still a config that
actually boots all three MCP servers over stdio.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_mcp_config.py"
EXAMPLE = ROOT / "mcp-config.stdio.example.json"
EXPECTED_SERVERS = {"mining-news-mcp", "mineral-pdf-mcp", "lme-price-mcp"}

# Deliberately awkward paths: plain, with spaces, and with non-ASCII characters.
PROJECT_PATHS = [
    r"C:\work\other-project",
    r"D:\My Projects\mineral report agent",
    r"E:\中文目录\矿权日报",
]


def _run_generator(tmp_path: Path, *extra: str) -> Path:
    out = tmp_path / "mcp-config.stdio.local.json"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), *extra],
        check=True,
        capture_output=True,
        text=True,
    )
    return out


def test_example_template_has_no_machine_specific_path():
    raw = EXAMPLE.read_text(encoding="utf-8")
    assert "<PROJECT_PATH>" in raw
    assert not re.search(r"[A-Za-z]:[\\/]", raw), "template must not embed an absolute path"
    assert "Users" not in raw


def test_example_template_keeps_the_original_wiring():
    servers = json.loads(EXAMPLE.read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == EXPECTED_SERVERS
    for spec in servers.values():
        assert spec["command"] == "uv"
        assert "url" not in spec  # stdio transport, not HTTP
        args = spec["args"]
        assert args[args.index("--transport") + 1] == "stdio"
        assert args[args.index("--directory") + 1] == "<PROJECT_PATH>"


@pytest.mark.parametrize("project_path", PROJECT_PATHS)
def test_generator_embeds_the_requested_project_path(tmp_path: Path, project_path: str):
    config = json.loads(_run_generator(tmp_path, "--project-root", project_path).read_text(encoding="utf-8"))
    assert set(config["mcpServers"]) == EXPECTED_SERVERS
    for name, spec in config["mcpServers"].items():
        args = spec["args"]
        assert args[args.index("--directory") + 1] == project_path, name
        assert args[args.index("--transport") + 1] == "stdio", name
        assert spec["command"] == "uv", name


def test_generated_json_escapes_windows_backslashes(tmp_path: Path):
    project_path = r"D:\My Projects\mineral report agent"
    out = _run_generator(tmp_path, "--project-root", project_path)
    raw = out.read_text(encoding="utf-8")

    assert "\\\\" in raw, "backslashes must be escaped in the JSON text"
    reloaded = json.loads(raw)  # valid JSON is the whole point
    args = reloaded["mcpServers"]["mining-news-mcp"]["args"]
    assert args[args.index("--directory") + 1] == project_path


def test_generator_defaults_to_this_checkout(tmp_path: Path):
    config = json.loads(_run_generator(tmp_path).read_text(encoding="utf-8"))
    args = config["mcpServers"]["mineral-pdf-mcp"]["args"]
    assert args[args.index("--directory") + 1] == str(ROOT)


def test_generator_rejects_a_template_without_the_placeholder(tmp_path: Path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"mcpServers": {}}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--template", str(broken), "--out", str(tmp_path / "x.json")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "placeholder" in (result.stderr + result.stdout)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("module", "expected_tools"),
    [
        ("minereport.news", {"search", "fetch_article"}),
        ("minereport.pdf", {"extract_resources", "list_sources"}),
        ("minereport.price", {"get_price", "get_trend", "list_commodities"}),
    ],
)
async def test_each_mcp_server_starts_over_stdio(module: str, expected_tools: set[str]):
    """The generator can only be useful if these servers still boot."""

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module, "--transport", "stdio"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()

    names = {tool.name for tool in listed.tools}
    assert expected_tools <= names, f"{module} exposed {sorted(names)}"


def test_default_run_writes_the_checklist_filename_too(tmp_path: Path, monkeypatch):
    """The delivery checklist asks for ``mcp-config.json``.

    A default run must produce it next to ``mcp-config.stdio.local.json``, with
    identical, direct-to-client content - while staying out of git.
    """

    env = dict(os.environ, MINEREPORT_MCP_CONFIG_DIR=str(tmp_path))
    subprocess.run([sys.executable, str(SCRIPT)], check=True, capture_output=True, text=True, env=env)

    local = tmp_path / "mcp-config.stdio.local.json"
    deliverable = tmp_path / "mcp-config.json"
    assert local.is_file() and deliverable.is_file()

    text = deliverable.read_text(encoding="utf-8")
    assert text == local.read_text(encoding="utf-8")
    config = json.loads(text)
    assert set(config["mcpServers"]) == EXPECTED_SERVERS
    for spec in config["mcpServers"].values():
        assert spec["command"] == "uv"
        assert spec["args"][spec["args"].index("--directory") + 1] == str(ROOT)
