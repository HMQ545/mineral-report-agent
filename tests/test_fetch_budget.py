"""Body fetching: bounded concurrency, timeout and an overall time budget."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from minereport.agent import graph as graph_module
from minereport.agent.llm import StubBackend
from minereport.agent.main import run_agent
from minereport.news import entities
from minereport.news import ids as news_ids
from minereport.pdf import server as pdf_server
from minereport.price import server as price_server

QUERY = "给我生成一份关于 Pilbara 锂矿的今日简报"


class FakeTool:
    def __init__(self, func):
        self._func = func

    async def ainvoke(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._func(arguments)
        if inspect.isawaitable(result):
            result = await result
        return result


def _news_item(index: int, *, google: bool = False, snippet: bool = True) -> dict[str, Any]:
    if google:
        url = f"https://news.google.com/rss/articles/CBMiXx{index}?oc=5"
        provider = "google-news-rss"
    else:
        url = f"https://www.mining.com/pilbara-minerals-{index}/"
        provider = "direct-feed:mining.com"
    item: dict[str, Any] = {
        "url": url,
        "title": f"Pilbara Minerals update {index}",
        "source": "mining.com",
        "provider": provider,
        "published_at": "2026-10-14T00:00:00+00:00",
        "relevance": "company",
        "relevance_evidence": {"company_evidence": ["pilbara minerals"]},
    }
    if snippet:
        item["snippet"] = "Pilbara Minerals update"
    news_id, kind = news_ids.news_identity(item)
    item.update(news_id=news_id, news_id_kind=kind)
    item.update(entities.assess_evidence(item))
    return item


def _article_envelope(url: str, body: str) -> dict[str, Any]:
    return {
        "data": {"url": url, "title": "t", "text": body, "word_count": len(body.split())},
        "provenance": "live",
        "source": "public-http",
        "source_url": url,
        "as_of": "2026-10-14T00:00:00+00:00",
        "retrieved_at": "2026-10-15T00:00:00+00:00",
        "degraded": False,
        "data_status": "OK",
        "notes": [],
    }


def build_tools(items: list[dict[str, Any]], fetch_impl):
    async def search(arguments):
        return {
            "data": {
                "company_items": items,
                "industry_items": [],
                "items": items,
                "pipeline": {
                    "fetched": len(items),
                    "duplicate_removed": 0,
                    "unique": len(items),
                    "company_candidates": len(items),
                    "industry_candidates": 0,
                    "irrelevant": 0,
                    "company_displayed": len(items),
                    "industry_displayed": 0,
                    "not_displayed_by_limit": 0,
                    "undated": 0,
                    "body_attempted": 0,
                    "body_succeeded": 0,
                    "body_failed": 0,
                    "body_budget_exhausted": 0,
                    "body_elapsed_s": 0.0,
                },
                "company_as_of": "2026-10-14T00:00:00+00:00",
                "industry_as_of": None,
            },
            "provenance": "live",
            "source": "public-rss",
            "source_url": "https://news.google.com/rss/search",
            "as_of": "2026-10-14T00:00:00+00:00",
            "retrieved_at": "2026-10-15T00:00:00+00:00",
            "degraded": False,
            "data_status": "OK",
            "notes": [],
        }

    async def fetch_article(arguments):
        return await fetch_impl(arguments["url"])

    return {
        "search": FakeTool(search),
        "fetch_article": FakeTool(fetch_article),
        "extract_resources": FakeTool(
            lambda _args: pdf_server.extract_resources("fixture://pdf/synthetic_jorc_statement.pdf")
        ),
        "get_price": FakeTool(lambda _args: price_server.get_price("lithium_spodumene")),
        "get_trend": FakeTool(lambda _args: price_server.get_trend("lithium_spodumene", 30)),
    }


async def _run(tmp_path, items, fetch_impl):
    return await run_agent(
        QUERY,
        days=7,
        require_live=False,
        out_dir=tmp_path,
        tools=build_tools(items, fetch_impl),
        backend=StubBackend(),
    )


@pytest.mark.anyio
async def test_body_fetch_is_bounded_at_three_concurrent_requests(tmp_path):
    active = 0
    peak = 0
    body = "Pilbara Minerals reported Pilgangoora output. " * 30

    async def fetch(url):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.15)
        finally:
            active -= 1
        return _article_envelope(url, body)

    items = [_news_item(i) for i in range(8)] + [_news_item(9, google=True)]
    result = await _run(tmp_path, items, fetch)
    pipeline = result["news_pipeline"]

    assert pipeline["body_attempted"] == 8, "the 8-item cap and the google exemption must both hold"
    assert peak == graph_module.BODY_FETCH_CONCURRENCY == 3
    assert pipeline["body_succeeded"] == 8
    assert pipeline["body_failed"] == 0

    fetched = {item["news_id"] for item in result["company_news"] if item["evidence"] == "full_article"}
    assert len(fetched) == 8
    google = [item for item in result["company_news"] if "news.google.com" in item["url"]][0]
    assert google["fetch_status"] == "not_attempted"
    assert google["evidence"] != "full_article"


@pytest.mark.anyio
async def test_overall_budget_stops_further_fetches(tmp_path, monkeypatch):
    """A tight budget must bound the stage, not merely make it slower.

    Honest semantics: a call that already started is truncated at the deadline
    (classified ``timeout``), and everything still queued is never attempted
    (``budget_exhausted``). Nothing may run past the budget.
    """

    monkeypatch.setattr(graph_module, "BODY_FETCH_BUDGET_SECONDS", 0.05)
    body = "Pilbara Minerals reported Pilgangoora output. " * 30

    async def fetch(url):
        await asyncio.sleep(0.3)
        return _article_envelope(url, body)

    items = [_news_item(i) for i in range(8)]
    result = await _run(tmp_path, items, fetch)
    pipeline = result["news_pipeline"]

    assert pipeline["body_succeeded"] == 0
    assert pipeline["body_attempted"] + pipeline["body_budget_exhausted"] == 8, "every candidate is accounted for"
    # Whether a queued item is skipped or truncated depends on scheduler timing;
    # test_exhausted_budget_never_starts_a_request pins the skipping deterministically.
    assert pipeline["body_elapsed_s"] < 0.3, "the stage must not outlive its budget"
    assert all(item["evidence"] != "full_article" for item in result["company_news"])
    assert all(item["fetch_status"] in {"timeout", "budget_exhausted"} for item in result["company_news"])


@pytest.mark.anyio
async def test_exhausted_budget_never_starts_a_request(tmp_path, monkeypatch):
    """Deterministic form of the same rule: nothing is fetched once time is up."""

    monkeypatch.setattr(graph_module, "BODY_FETCH_BUDGET_SECONDS", -1.0)
    started: list[str] = []

    async def fetch(url):
        started.append(url)
        return _article_envelope(url, "body " * 100)

    items = [_news_item(i) for i in range(8)]
    result = await _run(tmp_path, items, fetch)
    pipeline = result["news_pipeline"]

    assert started == [], "no request may start after the budget is gone"
    assert pipeline["body_attempted"] == 0
    assert pipeline["body_budget_exhausted"] == 8
    assert all(item["fetch_status"] == "budget_exhausted" for item in result["company_news"])
    assert all(item["evidence"] != "full_article" for item in result["company_news"])


@pytest.mark.anyio
async def test_timeout_is_classified_and_downgrades_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_module, "BODY_FETCH_TIMEOUT_SECONDS", 0.05)

    async def fetch(url):
        await asyncio.sleep(0.4)
        return _article_envelope(url, "body " * 100)

    items = [_news_item(i, snippet=(i < 4)) for i in range(6)]
    result = await _run(tmp_path, items, fetch)
    pipeline = result["news_pipeline"]

    assert pipeline["body_succeeded"] == 0
    assert pipeline["body_failed"] == 6
    assert all(item["fetch_status"] == "timeout" for item in result["company_news"])
    with_snippet = [item for item in result["company_news"] if item["title"].endswith(("0", "1", "2", "3"))]
    assert all(item["evidence"] == "rss_summary" for item in with_snippet)
    without_snippet = [item for item in result["company_news"] if item not in with_snippet]
    assert all(item["evidence"] == "title_only" for item in without_snippet)
    assert any("超时" in error for error in result["errors"])


@pytest.mark.anyio
async def test_fetch_failure_is_recorded_with_its_reason(tmp_path):
    async def fetch(url):
        raise RuntimeError("connection reset")

    items = [_news_item(i) for i in range(2)]
    result = await _run(tmp_path, items, fetch)

    assert result["news_pipeline"]["body_failed"] == 2
    assert all(item["fetch_status"] == "failed" for item in result["company_news"])
    fetch_errors = [error for error in result["errors"] if "fetch_article" in error]
    assert len(fetch_errors) == 2, result["errors"]
    assert all("connection reset" in error for error in fetch_errors)
    assert all(item["evidence"] == "rss_summary" for item in result["company_news"])