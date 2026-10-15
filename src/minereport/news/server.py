"""mining-news-mcp: ``search`` and ``fetch_article``.

``search`` is entity-aware: for a question about a registered company it runs
several precise queries (official name, project, ticker) plus one industry
background query, then labels every item as ``company`` / ``industry`` /
``irrelevant``. A bare place name is never accepted as company evidence, which is
what kept Rio Tinto iron-ore stories out of a Pilbara Minerals brief.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..common.cli import add_transport_args, run_server
from ..common.fixtures import load_fixture_json
from ..common.health import register_health
from ..common.http import UrlNotAllowedError
from ..common.provenance import Provenance, envelope, error_envelope
from . import entities, ids, sources

SERVICE = "mining-news-mcp"
DEFAULT_PORT = 8101
FIXTURE_KEY = "news/pilbara_lithium"

INSTRUCTIONS = (
    "Aggregates mining/lithium news from public RSS feeds and extracts article text. "
    "Google News RSS provides recency, publisher feeds provide real article URLs. "
    "Items are classified as company / industry / irrelevant using an entity registry "
    "(official name, ticker, project names), never a bare place name. Every response "
    "carries provenance: live, cached or fixture."
)


def _fixture_search(query: str, days: int, reason: str) -> dict[str, Any]:
    payload = load_fixture_json(FIXTURE_KEY)
    items = payload.get("items", [])[: payload.get("limit", 10)]
    pipeline = _empty_pipeline(len(items))
    pipeline.update(
        fetched=len(items),
        unique=len(items),
        industry_candidates=len(items),
        industry_displayed=len(items),
    )
    return envelope(
        {
            "query": query,
            "days": days,
            "items": items,
            "company_items": [],
            "industry_items": items,
            "pipeline": pipeline,
            "company_as_of": None,
            "industry_as_of": _max_published(items),
        },
        provenance=Provenance.FIXTURE,
        source=payload.get("source", "repository fixture"),
        source_url=payload.get("source_url"),
        as_of=payload.get("as_of"),
        notes=[
            reason,
            payload.get("disclaimer", "离线样例数据，非实时新闻。"),
        ],
    )


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return entities.dedupe_items(items)


def _describe(item: dict[str, Any], entity: entities.Entity | None) -> dict[str, Any]:
    """Attach the stable id and the initial (pre-fetch) evidence assessment."""

    described = dict(item)
    news_id, kind = ids.news_identity(described)
    described["news_id"] = news_id
    described["news_id_kind"] = kind
    described.update(entities.assess_evidence(described, entity=entity))
    return described


def _max_published(items: Sequence[dict[str, Any]]) -> str | None:
    """Newest real publication time among *these* items, in UTC.

    Deliberately never the fetch time: ``retrieved_at`` already carries that.
    """

    stamps: list[datetime] = []
    for item in items:
        raw = item.get("published_at")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        stamps.append(parsed.astimezone(timezone.utc))
    if not stamps:
        return None
    return max(stamps).isoformat(timespec="seconds")


def _empty_pipeline(fetched: int = 0) -> dict[str, Any]:
    return {
        "fetched": fetched,
        "duplicate_removed": 0,
        "unique": 0,
        "company_candidates": 0,
        "industry_candidates": 0,
        "irrelevant": 0,
        "company_displayed": 0,
        "industry_displayed": 0,
        "not_displayed_by_limit": 0,
        "undated": 0,
        "body_attempted": 0,
        "body_succeeded": 0,
        "body_failed": 0,
        "body_budget_exhausted": 0,
        "body_elapsed_s": 0.0,
    }


def search(query: str, days: int = 3) -> dict[str, Any]:
    """Search real news feeds for ``query`` within the last ``days`` days."""

    days = max(1, min(int(days), 90))
    entity = entities.resolve_entity(query)

    if entity and entity.search_queries:
        queries = list(entity.search_queries)
        if entity.industry_query:
            queries.append(entity.industry_query)
    else:
        queries = [query]

    raw_items, notes = sources.search_queries(queries, days=days)
    if not raw_items:
        # Bing RSS as a last resort, still entity-classified below.
        try:
            bing = sources.search_bing(query, days=days)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"bing-news-rss failed: {type(exc).__name__}: {exc}")
            bing = []
        else:
            notes.append(f"bing-news-rss -> {len(bing)} item(s)")
        raw_items = [dict(item, matched_query=query) for item in bing]
        queries = [query]

    live_sources_ok = any("failed" not in note for note in notes) or bool(raw_items)

    # Stage 1/2: fetch count, then de-duplicate *before* classifying, so the
    # mutually-exclusive classes below always add up to ``unique``.
    fetched = len(raw_items)
    unique_items = _dedupe(raw_items)
    duplicate_removed = fetched - len(unique_items)

    company_candidates: list[dict[str, Any]] = []
    industry_candidates: list[dict[str, Any]] = []
    irrelevant = 0
    for item in unique_items:
        label, evidence = entities.classify_item(item, entity)
        enriched = _describe(item, entity)
        enriched["relevance"] = label
        enriched["relevance_evidence"] = evidence
        if label == entities.COMPANY:
            company_candidates.append(enriched)
        elif label == entities.INDUSTRY:
            industry_candidates.append(enriched)
        else:
            irrelevant += 1

    def by_date(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda item: item.get("published_at") or "", reverse=True)

    def is_direct(url: str) -> bool:
        return str(url).startswith("http") and "news.google.com" not in str(url)

    company = by_date(company_candidates)[:20]

    deduped_industry = by_date(industry_candidates)
    direct_industry = [item for item in deduped_industry if is_direct(item.get("url", ""))]
    # Publisher-feed items carry real article URLs, which is what body fetching
    # needs; keep a few of them visible instead of letting date sorting push them
    # out behind Google redirect links.
    industry = (direct_industry[:3] + [item for item in deduped_industry if item not in direct_industry[:3]])[:12]

    direct_url_items = [
        item for item in (company + industry) if is_direct(item.get("url", "")) and item.get("relevance") == "industry"
    ][:8]

    displayed = company + industry
    not_displayed = (len(company_candidates) - len(company)) + (
        len(industry_candidates) - len(industry)
    )
    pipeline = _empty_pipeline(fetched)
    pipeline.update(
        duplicate_removed=duplicate_removed,
        unique=len(unique_items),
        company_candidates=len(company_candidates),
        industry_candidates=len(industry_candidates),
        irrelevant=irrelevant,
        company_displayed=len(company),
        industry_displayed=len(industry),
        not_displayed_by_limit=not_displayed,
        undated=sum(1 for item in displayed if not item.get("published_at")),
    )

    payload = {
        "query": query,
        "days": days,
        "entity": {"key": entity.key, "name": entity.name, "projects": list(entity.projects)} if entity else None,
        "items": displayed,
        "company_items": company,
        "industry_items": industry,
        "direct_url_items": direct_url_items,
        "queries_used": queries,
        "pipeline": pipeline,
        "company_as_of": _max_published(company),
        "industry_as_of": _max_published(industry),
        "stats": {
            "raw": fetched,
            "company": len(company),
            "industry": len(industry),
            "excluded_irrelevant": irrelevant,
            "direct_url": len(direct_url_items),
        },
    }

    if company or industry:
        notes.append(
            f"处理流水：抓取 {fetched} 条 → 去重移除 {duplicate_removed} 条 → 唯一 {len(unique_items)} 条"
            f"（公司直接相关 {len(company_candidates)}、行业背景 {len(industry_candidates)}、"
            f"过滤无关 {irrelevant}）；展示 公司 {len(company)} / 行业 {len(industry)}，"
            f"因展示上限未展示 {not_displayed} 条。"
        )
        if not company:
            notes.append("检索期内暂无可验证的公司直接相关新闻。")
        return envelope(
            payload,
            provenance=Provenance.LIVE,
            source="public-rss:google-news-rss+direct-feeds",
            source_url=sources.GOOGLE_NEWS_RSS,
            as_of=_max_published(displayed),
            notes=notes,
        )

    if live_sources_ok:
        notes.append("实时数据源可用但窗口内无匹配新闻；不填充任何模拟新闻。")
        notes.append("检索期内暂无可验证的公司直接相关新闻。")
        payload.setdefault("pipeline", _empty_pipeline(0))
        return envelope(
            payload,
            provenance=Provenance.LIVE,
            source="public-rss",
            source_url=None,
            notes=notes,
        )

    return _fixture_search(query, days, "所有实时 RSS 数据源均失败：" + " | ".join(notes))


def fetch_article(url: str, refresh: bool = False) -> dict[str, Any]:
    """Fetch and extract the body text of a single article URL.

    ``refresh=True`` forces a real network fetch instead of answering from the
    local cache, so a live check always proves the current path works.
    """

    try:
        article, from_cache = sources.fetch_article(url, refresh=refresh)
    except UrlNotAllowedError as exc:
        return error_envelope(
            f"URL rejected by SSRF policy: {exc}",
            source="public-http",
            source_url=url,
            code="URL_NOT_ALLOWED",
        )
    except sources.UnresolvableRedirectError as exc:
        return error_envelope(
            str(exc),
            source="public-http",
            source_url=url,
            code="UNRESOLVABLE_REDIRECT",
        )
    except Exception as exc:  # noqa: BLE001
        return error_envelope(
            f"article fetch failed: {type(exc).__name__}: {exc}",
            source="public-http",
            source_url=url,
            code="FETCH_FAILED",
        )

    return envelope(
        article,
        provenance=Provenance.CACHED if from_cache else Provenance.LIVE,
        source="public-http",
        source_url=url,
        as_of=article.get("published_at") or None,
        notes=["命中本地缓存（此前为真实抓取）"] if from_cache else [],
    )


def create_server(
    host: str = "127.0.0.1", port: int = DEFAULT_PORT, log_level: str = "INFO"
) -> FastMCP:
    mcp = FastMCP(SERVICE, instructions=INSTRUCTIONS, host=host, port=port, log_level=log_level)
    mcp.tool()(search)
    mcp.tool()(fetch_article)
    register_health(mcp, service=SERVICE, version=__version__)
    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="minereport-news", description=INSTRUCTIONS)
    add_transport_args(parser, DEFAULT_PORT)
    args = parser.parse_args(argv)
    run_server(create_server(args.host, args.port, args.log_level), args)


__all__ = ["SERVICE", "create_server", "fetch_article", "main", "search"]