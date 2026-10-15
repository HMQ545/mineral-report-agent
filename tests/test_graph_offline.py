"""Offline end-to-end run of the LangGraph workflow (fixtures + stub LLM)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from minereport.agent.llm import StubBackend
from minereport.agent.main import run_agent
from minereport.news import server as news_server
from minereport.pdf import server as pdf_server
from minereport.price import server as price_server


class FakeTool:
    """Mimics a LangChain tool: ``await tool.ainvoke({...})``."""

    def __init__(self, name: str, func):
        self.name = name
        self._func = func

    async def ainvoke(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._func(arguments)


def build_fixture_tools() -> dict[str, FakeTool]:
    def search(args):
        return news_server._fixture_search(
            args.get("query", ""), args.get("days", 7), "offline test"
        )

    def fetch_article(args):
        url = args.get("url", "")
        return {
            "data": {
                "url": url,
                "title": "[offline] fixture article",
                "site": "example.invalid",
                "published_at": "2026-10-13T00:00:00+00:00",
                "text": "offline fixture body " * 40,
                "word_count": 120,
            },
            "provenance": "fixture",
            "degraded": True,
            "source": "repository fixture",
            "source_url": url,
            "as_of": "2026-10-13",
            "retrieved_at": "2026-10-14T00:00:00+00:00",
            "notes": [],
        }

    def extract_resources(_args):
        # Deliberately ignore the URL the graph resolved: the offline suite must
        # never reach the network, so this pins the synthetic fixture.
        return pdf_server.extract_resources("fixture://pdf/synthetic_jorc_statement.pdf")

    def get_price(_args):
        return price_server.get_price("lithium_spodumene")

    def get_trend(_args):
        return price_server.get_trend("lithium_spodumene", 30)

    return {
        "search": FakeTool("search", search),
        "fetch_article": FakeTool("fetch_article", fetch_article),
        "extract_resources": FakeTool("extract_resources", extract_resources),
        "get_price": FakeTool("get_price", get_price),
        "get_trend": FakeTool("get_trend", get_trend),
    }


@pytest.mark.anyio
async def test_offline_workflow_produces_a_complete_brief(tmp_path: Path):
    result = await run_agent(
        "给我生成一份关于 Pilbara 锂矿的今日简报",
        days=7,
        require_live=False,
        out_dir=tmp_path,
        tools=build_fixture_tools(),
        backend=StubBackend(),
    )

    markdown = result["report_markdown"]
    for heading in (
        "## 0. 数据来源与可信度",
        "## 1. 新闻摘要",
        "## 2. 矿产资源量",
        "## 3. 矿石储量",
        "## 4. 价格走势",
        "## 5. 风险提示",
        "## 6. 引用源链接",
    ):
        assert heading in markdown, heading

    assert result["report_path"]
    assert Path(result["report_path"]).exists()

    provenance = result["provenance_map"]
    assert provenance["news"]["provenance"] == "fixture"
    assert provenance["reserves"]["provenance"] == "fixture"
    assert provenance["price"]["provenance"] == "fixture"
    assert "非实时数据" in markdown


@pytest.mark.anyio
async def test_require_live_fails_when_only_fixtures_are_available(tmp_path: Path):
    result = await run_agent(
        "给我生成一份关于 Pilbara 锂矿的今日简报",
        days=7,
        require_live=True,
        out_dir=tmp_path,
        tools=build_fixture_tools(),
        backend=StubBackend(),
    )
    failures = result["live_gate_failures"]
    assert failures, "a fixture-only run must fail the strict live gate"
    assert any("news" in failure for failure in failures)
    assert any("reserves" in failure for failure in failures)

