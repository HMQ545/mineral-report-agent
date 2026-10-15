"""Live end-to-end checks against real external sources.

Run with: ``uv run pytest -m live``.
These tests never fall back to fixtures: when a real source fails they fail.
"""

from __future__ import annotations

import asyncio
import re

from pathlib import Path

import pytest

from minereport.news import server as news_server
from minereport.pdf import registry as pdf_registry
from minereport.pdf import server as pdf_server
from minereport.price import server as price_server

pytestmark = pytest.mark.live

# Explicit budgets: these tests must fail loudly rather than hang forever when an
# upstream site is slow (Google News RSS once took 655s for a single search).
NEWS_SEARCH_BUDGET = 300.0
ARTICLE_FETCH_BUDGET = 60.0


async def _bounded(coro, seconds: float, label: str):
    """Await a live call under a hard budget; a timeout is a failure, not a skip."""

    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError as exc:  # noqa: BLE001 - re-raised with context
        raise asyncio.TimeoutError(f"{label} 超过 {seconds:.0f}s 预算") from exc

async def _mcp_news_call(tool: str, arguments: dict) -> dict:
    """Call a mining-news-mcp tool through a real stdio MCP session."""

    import json
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "minereport.news", "--transport", "stdio"]
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    return json.loads(result.content[0].text)


def _article_sample_urls() -> list[str]:
    import json

    from minereport.common import config

    path = config.SOURCES_DIR / "article_samples.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [sample["url"] for sample in payload["samples"]]


@pytest.mark.anyio
async def test_live_news_search_returns_real_items():
    result = await _bounded(
        asyncio.to_thread(news_server.search, "Pilbara lithium", 7),
        NEWS_SEARCH_BUDGET,
        "news.search (direct)",
    )
    assert result["provenance"] == "live", result.get("notes")
    items = result["data"]["items"]
    assert items, "expected at least one recent news item"
    for item in items:
        assert item["url"].startswith("http")
        assert item["title"]
        assert item["news_id"], "every item needs a stable news id"
        assert item["evidence"] in {
            "official_disclosure",
            "full_article",
            "rss_summary",
            "title_only",
            "unverifiable",
        }
        assert item["source_type"] in {"official", "media", "rss", "unknown"}
        assert item["fetch_status"] in {
            "success",
            "failed",
            "unresolvable_redirect",
            "not_attempted",
            "timeout",
            "budget_exhausted",
        }


@pytest.mark.anyio
async def test_live_news_search_via_mcp_client():
    """Test A - the search tool: titles, urls, sources, recency, dedupe.

    It deliberately makes no assumption that any item can be fetched in full.
    """

    payload = await _bounded(
        _mcp_news_call("search", {"query": "给我生成一份关于 Pilbara 锂矿的今日简报", "days": 7}),
        NEWS_SEARCH_BUDGET,
        "search via MCP",
    )
    assert payload["provenance"] == "live", payload.get("notes")
    data = payload["data"]
    assert data["entity"]["name"] == "Pilbara Minerals Limited"

    items = data["items"]
    assert items, "expected at least one news item"

    for item in items:
        assert item["title"].strip()
        assert item["url"].startswith("http")
        assert item.get("published_at"), "every item must carry a publish time"
        assert item["relevance"] in {"company", "industry"}

    titles = [item["title"] for item in items]
    assert len(titles) == len(set(titles)), "search results must be de-duplicated"

    for item in data["company_items"]:
        assert item["relevance_evidence"].get("company_evidence"), item["title"]

    # --- pipeline closure and as_of semantics -------------------------------
    pipeline = data["pipeline"]
    assert pipeline["fetched"] - pipeline["duplicate_removed"] == pipeline["unique"]
    assert (
        pipeline["company_candidates"] + pipeline["industry_candidates"] + pipeline["irrelevant"]
        == pipeline["unique"]
    )

    published = sorted(item["published_at"] for item in items if item.get("published_at"))
    assert payload["as_of"] == published[-1], "as_of must be the newest displayed publication"
    assert all(item["news_id"] for item in items)
    assert len({item["news_id"] for item in items}) == len(items), "ids must be unique per story"

    print(
        f"\nsearch via MCP: raw={data['stats']['raw']} company={data['stats']['company']} "
        f"industry={data['stats']['industry']} direct_url={data['stats'].get('direct_url')}"
    )


@pytest.mark.anyio
async def test_live_fetch_article_via_mcp_client_returns_real_body():
    """Test B - the fetch_article tool: a real body from a real HTTP fetch.

    Candidates come from the live search result first, then from the verified
    sample registry, so this does not depend on what the last seven days of RSS
    happened to contain. Failure accounting:
      * external unavailability (403/429/timeout/gone) -> try the next candidate,
        and skip only when *every* candidate is unreachable;
      * extraction that returns too little text -> a parser defect, so the test
        fails instead of skipping.
    """

    unreachable: list[str] = []
    too_small: list[str] = []

    candidates: list[str] = []
    try:
        search = await _mcp_news_call("search", {"query": "lithium market", "days": 7})
        candidates += [item["url"] for item in (search["data"].get("direct_url_items") or [])]
    except Exception as exc:  # noqa: BLE001 - recorded, never hidden
        unreachable.append(f"search failed: {type(exc).__name__}: {exc}")
    candidates += _article_sample_urls()

    seen: set[str] = set()
    ordered = [url for url in candidates if not (url in seen or seen.add(url))]
    assert ordered, "no candidate article URLs available"

    for url in ordered:
        try:
            payload = await _bounded(
                _mcp_news_call("fetch_article", {"url": url, "refresh": True}),
                ARTICLE_FETCH_BUDGET,
                f"fetch_article({url})",
            )
        except asyncio.TimeoutError as exc:
            unreachable.append(f"{url} [TIMEOUT] {exc}")
            continue
        if payload.get("error"):
            unreachable.append(f"{url} [{payload.get('error_code')}] {str(payload['error'])[:90]}")
            continue

        assert payload["provenance"] == "live", f"{url} answered from cache, not a live fetch"
        data = payload["data"]
        text = (data.get("text") or "").strip()
        title = (data.get("title") or "").strip()

        problems = []
        if data.get("word_count", 0) < 60:
            problems.append(f"word_count={data.get('word_count')}")
        if len(text) < 400:
            problems.append(f"chars={len(text)}")
        if text.lower() == title.lower():
            problems.append("body equals the title")
        if text.count(". ") < 3 and text.count("。") < 3:
            problems.append("body has fewer than three sentences")
        if problems:
            too_small.append(f"{url} -> {', '.join(problems)}")
            continue

        assert data["url"] == url
        assert data["site"], "the publisher site must be recorded"
        print(
            f"\nfetch_article via MCP: {url}\n"
            f"  provenance={payload['provenance']} title={title[:70]!r}\n"
            f"  site={data['site']} word_count={data['word_count']} chars={len(text)} "
            f"retrieved_at={payload['retrieved_at']}\n"
            f"  text head: {' '.join(text.split())[:150]}"
        )
        return

    if too_small:
        pytest.fail(
            "article extraction returned too little text (parser defect): "
            + "; ".join(too_small)
            + ((" | also unreachable: " + "; ".join(unreachable)) if unreachable else "")
        )
    pytest.skip("all candidate article URLs were unavailable externally: " + "; ".join(unreachable))

def test_live_pilbara_pdf_matches_registry_and_separates_resources_from_reserves():
    source = pdf_registry.default_source(role="primary-deliverable")
    assert source, "the primary-deliverable source must be registered"

    result = pdf_server.extract_resources(source["url"], refresh=True)
    assert result["provenance"] == "live", result.get("error") or result.get("notes")
    assert result["sha256_verified"] is True

    data = result["data"]
    assert data["reporter_entity"] == "Pilbara Minerals Limited"
    assert "JORC" in data["standards"]
    assert data["counts"]["mineral_resource_rows"] > 0
    assert data["counts"]["ore_reserve_rows"] > 0

    for row in data["mineral_resources"]:
        assert row["group"] == "MineralResource"
        assert row["page"] >= 1 and row["quote"]
    for row in data["ore_reserves"]:
        assert row["group"] == "OreReserve"


def test_live_ni43101_source_is_reachable_and_declares_the_standard():
    source = pdf_registry.default_source(role="standard-compatibility")
    assert source
    result = pdf_server.extract_resources(source["url"], refresh=True)
    assert result["provenance"] == "live"
    assert "NI 43-101" in result["data"]["standards"]


def test_live_price_supports_historical_lookup():
    latest = price_server.get_price("copper")
    assert latest["provenance"] == "live"

    trend = price_server.get_trend("copper", 30)
    assert trend["provenance"] == "live"
    points = trend["data"]["points"]
    assert len(points) >= 15

    dates = [point["date"] for point in points]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))

    historical = price_server.get_price("copper", dates[-3])
    assert historical["provenance"] == "live"
    assert historical["data"]["as_of"] == dates[-3]
    assert historical["data"]["quote_basis"].startswith("daily close")


def test_live_non_trading_day_returns_not_found_instead_of_latest_price():
    result = price_server.get_price("copper", "2026-09-05")
    assert result.get("error_code") == "NOT_FOUND"
    assert result["data"] is None


def test_live_lithium_carbonate_is_flagged_as_a_downstream_reference():
    result = price_server.get_price("lithium_carbonate")
    assert result["provenance"] == "live"
    assert any("结算价" in note for note in result["notes"])


@pytest.mark.anyio
async def test_live_agent_run_passes_the_strict_gate(tmp_path: Path):
    from minereport.agent.llm import StubBackend
    from minereport.agent.main import run_agent
    from minereport.agent.mcp_client import open_tools

    tools = await open_tools()
    result = await run_agent(
        "给我生成一份关于 Pilbara 锂矿的今日简报",
        require_live=True,
        out_dir=tmp_path,
        tools=tools,
        backend=StubBackend(),
    )
    assert result["live_gate_failures"] == [], result["live_gate_failures"]
    report_path = Path(result["report_path"])
    assert report_path.exists()
    provenance = result["provenance_map"]
    for dataset in ("news", "reserves", "price"):
        assert provenance[dataset]["provenance"] == "live"

    markdown = report_path.read_text(encoding="utf-8")
    # acceptance is about the artefact, not only about exit codes
    assert "DATA_MISSING" in markdown, "strict-live report must mask non-live values"
    assert "1,180 USD/t" not in markdown, "the spodumene fixture price must not appear"
    optional_section = markdown.split("### 4.1 其他相关市场")[1].split("## 5.")[0]
    spodumene_rows = [line for line in optional_section.splitlines() if "锂辉石精矿" in line]
    assert spodumene_rows, "the unavailable commodity must still be listed in section 4.1"
    assert all("DATA_MISSING" in line for line in spodumene_rows)
    assert "### 1.1 公司直接相关新闻" in markdown
    assert "### 1.2 锂行业背景新闻" in markdown
    assert "Rio Tinto" not in markdown
    assert "Pilbara Gold" not in markdown

    # --- v2 data-quality guarantees -----------------------------------------
    assert "闭合校验" in markdown and "不一致 ✗" not in markdown, "news statistics must close"
    assert "证据：" in markdown and "news_id：" in markdown, "news must show evidence level + stable id"
    optional_block = markdown.split("### 4.1")[1].split("## 5.")[0]
    risk_block = markdown.split("## 5.")[1].split("## 6.")[0]
    assert "离线样例" not in optional_block, "an unused fixture must not be shown as a price"
    assert "离线样例" not in risk_block, "the risk section must not claim a fixture was used"
    assert "未使用离线模拟价格" in optional_block + risk_block
    assert "data_status" in markdown
    assert "effective_date" in markdown and "announced_date" in markdown and "fetched_at" in markdown
    assert "历史数据" in markdown, "the 2023 statement must be flagged as historical"
    assert "结论—证据对照" in markdown, "numeric risk claims must be traceable"
    assert "质量校验·" in markdown
    # this test injects the stub backend on purpose; the real model is covered by
    # test_live_deepseek_*_returns_chinese_and_valid_schema
    assert "### 5.2 综合判断（" in markdown
    assert "## 8. 运行诊断" in markdown



def test_live_deepseek_news_digest_returns_chinese_and_valid_schema():
    """Integration test for the structured-output fix (was: 400 tool_choice)."""

    import asyncio
    import json
    import os

    from dotenv import load_dotenv

    from minereport.agent.llm import DeepSeekBackend, NewsDigest

    load_dotenv(r"D:\Python\mineral-report-agent\.env", override=False)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.fail("DEEPSEEK_API_KEY not configured; the real-LLM gate cannot be verified")

    backend = DeepSeekBackend(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=os.environ["DEEPSEEK_API_KEY"],
        api_base=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    payload = {
        "company_items": [
            {
                "title": "Pilbara Minerals lifts spodumene output at Pilgangoora",
                "url": "https://example.com/a",
                "source": "Example",
                "published_at": "2026-10-14",
                "snippet": "Pilbara Minerals reported higher spodumene concentrate production at Pilgangoora.",
            }
        ],
        "industry_items": [],
    }
    user = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    digest = asyncio.run(
        backend.structured(NewsDigest, "你是矿业分析师，只根据给定材料总结，不得编造。", user)
    )

    assert digest.items, "the model must return at least one analysed item"
    item = digest.items[0]
    assert item.sentiment in {"positive", "neutral", "negative"}
    assert re.search(r"[\u4e00-\u9fff]", item.summary), "summary must be in Chinese"
    assert re.search(r"[\u4e00-\u9fff]", digest.overall), "overall must be in Chinese"


def test_live_deepseek_risk_section_returns_chinese_and_valid_schema():
    """Real model, real production prompt, real claim-repair pipeline.

    Bullet *count* is the model's choice, so it is only bounded (non-empty); the
    hard contract is that its output survives ``repair_claims`` + the claim
    validator with no outstanding issue.
    """

    import asyncio
    import json
    import os

    from dotenv import load_dotenv

    from minereport.agent import evidence as engine
    from minereport.agent import prompts
    from minereport.agent.llm import DeepSeekBackend, RiskSection

    load_dotenv(r"D:\Python\mineral-report-agent\.env", override=False)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.fail("DEEPSEEK_API_KEY not configured; the real-LLM gate cannot be verified")

    backend = DeepSeekBackend(
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=os.environ["DEEPSEEK_API_KEY"],
        api_base=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )

    index = {
        engine.PRICE_TREND_001: {
            "kind": "trend",
            "data_status": "OK",
            "commodity": "lithium_carbonate",
            "display_name": "碳酸锂（广期所主力连续）",
            "requested_days": 60,
            "change_pct": -34.2671,
            "first_close": 153500.0,
            "last_close": 100900.0,
            "min": 100900.0,
            "max": 161000.0,
            "unit": "CNY/t",
            "coverage": {"start": "2026-08-17", "end": "2026-10-14", "trading_days": 37},
        },
        "nid-live-test-1": {
            "kind": "news",
            "data_status": "OK",
            "news_id": "nid-live-test-1",
            "title": "Pilbara Minerals quarterly activities report delayed",
            "source": "Kalkine",
            "url": "https://example.invalid/news/live-test-1",
            "published_at": "2026-10-13T23:15:02+00:00",
            "evidence": "rss_summary",
            "source_type": "rss",
            "fetch_status": "not_attempted",
        },
    }
    state = {"require_live": True, "commodity": "lithium_carbonate", "news_digest": {"items": []}}
    rule_claims = engine.rule_claims(state, index)
    assert rule_claims, "the deterministic claim for the price trend must exist"

    user = prompts.RISK_USER_TEMPLATE.format(
        topic="Pilgangoora Operation（Pilbara Minerals Limited）",
        commodity="lithium_carbonate",
        generated_at="2026-10-15T00:00:00+08:00",
        rules=json.dumps(rule_claims, ensure_ascii=False, indent=1),
        evidence_index=json.dumps(index, ensure_ascii=False, indent=1),
        evidence=json.dumps({engine.PRICE_TREND_001: index[engine.PRICE_TREND_001]}, ensure_ascii=False, indent=1),
    )
    section = asyncio.run(backend.structured(RiskSection, prompts.RISK_SYSTEM, user))

    assert section.bullets, "the model must return at least one bullet"
    assert len(section.bullets) <= 8, f"unexpectedly many bullets: {len(section.bullets)}"
    assert all(re.search(r"[\u4e00-\u9fff]", bullet) for bullet in section.bullets)
    assert re.search(r"[\u4e00-\u9fff]", section.judgement)

    model_claims = [claim.model_dump() for claim in section.claims]
    kept, repairs = engine.repair_claims(
        index, rule_claims + model_claims, require_live=True
    )
    issues = engine.validate_claims(index, kept, require_live=True)
    assert kept, "the rule-derived claim must survive the repair step"
    assert issues == [], f"real model output still violates the gate: {issues} (repairs={repairs})"


def test_live_news_search_is_entity_filtered():
    result = news_server.search("Pilbara 锂矿 今日简报", 7)
    assert result["provenance"] == "live"
    data = result["data"]
    assert data["entity"]["name"] == "Pilbara Minerals Limited"

    confusable = ("rio tinto", "pilbara gold", "iron ore", "asx:pgl")
    for item in data["company_items"]:
        text = f"{item['title']} {item.get('snippet', '')}".lower()
        assert not any(term in text for term in confusable), item["title"]
        assert item["relevance_evidence"].get("company_evidence"), item["title"]

    assert data["stats"]["excluded_irrelevant"] >= 0
    if not data["company_items"]:
        assert any("暂无可验证的公司直接相关新闻" in note for note in result["notes"])


@pytest.mark.anyio
async def test_live_conversational_intent_classification_uses_deepseek():
    """The conversational layer with the real model (not the stub)."""

    import os
    from unittest.mock import patch

    from dotenv import load_dotenv

    from minereport.agent.intent.classifier import classify
    from minereport.agent.intent.extractor import extract_slots
    from minereport.agent.llm import build_backend

    load_dotenv(r"D:\Python\mineral-report-agent\.env", override=False)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.fail("DEEPSEEK_API_KEY not configured; the real-LLM gate cannot be verified")

    # Force the model path even for inputs the rules already handle.
    with patch(
        "minereport.agent.intent.classifier.classify_by_rules",
        side_effect=lambda *a, **k: __import__(
            "minereport.agent.intent.schemas", fromlist=["IntentResult"]
        ).IntentResult(confidence=0.1),
    ):
        backend, _ = build_backend("deepseek", require_real=True)
        slots, _ = extract_slots("帮我看看最近一周铜价的走势")
        verdict = await classify("帮我看看最近一周铜价的走势", backend=backend, pending_tasks=[], extracted=slots)

    assert verdict.source == "rules+model", f"expected a real model verdict, got {verdict.source}"
    assert verdict.intent.value in {"trend_query", "price_query", "general_chat", "unknown"}
    assert verdict.action.value in {"new_task", "slot_completion", "continue_task", "general_chat", "cancel_task"}
    print(f"\nlive intent verdict: {verdict.intent.value} / {verdict.action.value} ({verdict.reason[:60]})")