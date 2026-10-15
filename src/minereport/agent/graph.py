"""LangGraph workflow: parse intent -> parallel fan-out -> LLM -> render."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.graph import END, StateGraph

from ..news import entities as news_entities
from ..news import ids as news_ids
from ..pdf import registry as pdf_registry
from ..price import registry as price_registry
from . import evidence as evidence_engine
from . import prompts
from .llm import LlmError, NewsDigest, RiskSection
from .mcp_client import call_tool
from .quality import run_quality_checks
from .report import render_report, write_report
from .risk import rule_based_risks
from .state import REQUIRED_DATASETS, BriefState

CN_TZ = timezone(timedelta(hours=8))

# Body fetching is bounded on three axes so one slow publisher cannot stall the
# whole daily brief (a real run once spent >10 minutes in Google News RSS alone).
BODY_FETCH_LIMIT = 8
BODY_FETCH_CONCURRENCY = 3
BODY_FETCH_TIMEOUT_SECONDS = 45.0
BODY_FETCH_BUDGET_SECONDS = 120.0

MISSING_OPTIONAL_REASON = (
    "严格 Live 模式：本次未取得该商品的真实市场报价，未使用离线模拟价格，"
    "因此无法据此量化实际销售价格与盈利影响。"
)

COMMODITY_KEYWORDS = (
    ("碳酸锂", "lithium_carbonate"),
    ("lithium carbonate", "lithium_carbonate"),
    ("锂辉石", "lithium_spodumene"),
    ("spodumene", "lithium_spodumene"),
    ("锂", "lithium_carbonate"),
    ("lithium", "lithium_carbonate"),
    ("铜", "copper"),
    ("copper", "copper"),
    ("铝", "aluminum"),
    ("aluminium", "aluminum"),
    ("aluminum", "aluminum"),
    ("镍", "nickel"),
    ("nickel", "nickel"),
    ("锌", "zinc"),
    ("zinc", "zinc"),
)


def resolve_source(query: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Newest *verified* registry entry for this query.

    Registry-driven on purpose: only human-verified sources (with a sha256
    fingerprint) are eligible, so neither the model nor the file order can decide
    which resource statement a brief quotes.
    """

    notes: list[str] = []
    source = pdf_registry.select_source(query)
    if source is None:
        notes.append("注册表中没有可用的主交付资源量来源。")
        return None, notes
    notes.append(source.get("selection_reason") or "已从注册表选择资源量来源。")
    if source.get("is_historical"):
        notes.append(
            f"资源量文档生效日期 {source.get('effective_date')}，距今 {source.get('age_days')} 天，"
            "按历史数据标注（本次未在注册表中找到更新的已验证报告）。"
        )
    return source, notes


def sanitize_optional_entry(entry: dict[str, Any], *, require_live: bool) -> dict[str, Any]:
    """Strip unusable numbers *before* analysis, not merely at render time.

    A fixture quote may exist in the repository, but in strict-live mode it must
    never reach the risk step as a number: missing really means missing.
    """

    if not require_live or entry.get("provenance") == "live":
        return entry
    cleaned = dict(entry)
    for key in ("price", "prev_close", "change_pct", "as_of", "min", "max"):
        cleaned.pop(key, None)
    cleaned["data_status"] = "DATA_MISSING"
    cleaned["reason"] = MISSING_OPTIONAL_REASON
    return cleaned


def _news_for_evidence(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Displayed news items, joined with the digest sentiment for claim checks."""

    digest_items = (state.get("news_digest") or {}).get("items") or []
    by_url = {item.get("url"): item for item in digest_items}
    merged_items: list[dict[str, Any]] = []
    for item in list(state.get("company_news") or []) + list(state.get("industry_news") or []):
        merged = dict(item)
        digest_entry = by_url.get(item.get("url"))
        if digest_entry:
            merged["sentiment"] = digest_entry.get("sentiment") or merged.get("sentiment")
            merged["news_id"] = merged.get("news_id") or digest_entry.get("news_id")
        merged_items.append(merged)
    return merged_items


def detect_commodity(query: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    lowered = query.lower()
    for keyword, canonical in COMMODITY_KEYWORDS:
        if keyword in lowered:
            if keyword == "锂":
                notes.append("查询提到锂，默认选用碳酸锂作为下游市场参考价（非锂辉石销售价）。")
            return canonical, notes
    notes.append("查询未指明商品，默认使用 lithium_carbonate。")
    return "lithium_carbonate", notes


def build_graph(tools: dict[str, Any], backend: Any):
    """Compile the workflow. ``tools`` maps MCP tool names to LangChain tools."""

    async def parse_intent(state: BriefState) -> dict[str, Any]:
        query = state["query"]
        source, notes = resolve_source(query)
        commodity, commodity_notes = detect_commodity(query)
        notes.extend(commodity_notes)

        if source:
            keyword = (source.get("keywords") or [source.get("key", "lithium")])[0]
            news_query = f"{keyword.title()} lithium"
            topic = f"{source.get('project_name')}（{source.get('reporter_entity')}）"
            slug = f"{keyword.lower().replace(' ', '-')}-lithium-brief"
        else:
            news_query = query
            topic = query
            slug = "brief"

        return {
            "topic": topic,
            "news_query": news_query,
            "commodity": commodity,
            "reserve_source": source,
            "intent_notes": notes,
            "slug": slug,
            "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        }

    async def fetch_news(state: BriefState) -> dict[str, Any]:
        errors: list[str] = []
        try:
            result = await call_tool(
                tools, "search", {"query": state["news_query"], "days": state["days"]}
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"news.search failed: {type(exc).__name__}: {exc}")
            return {"news": None, "articles": [], "errors": errors}

        data = result.get("data") or {}
        company = data.get("company_items") or []
        industry = data.get("industry_items") or []
        items = data.get("items") or (company + industry)

        articles: list[dict[str, Any]] = []
        # Company-direct items first: those are the ones worth reading in full.
        fetchable = [
            item
            for item in (company + industry)
            if str(item.get("url", "")).startswith("http")
            and not news_ids.is_unresolvable(str(item.get("url", "")))
        ][:BODY_FETCH_LIMIT]
        if items and not fetchable:
            errors.append(
                "新闻条目均来自聚合跳转链接，无法解析原文正文；本次仅使用 RSS 标题与链接。"
            )

        entity = news_entities.resolve_entity(state.get("news_query") or "")
        started = time.monotonic()
        deadline = started + BODY_FETCH_BUDGET_SECONDS
        semaphore = asyncio.Semaphore(BODY_FETCH_CONCURRENCY)
        outcomes: dict[str, dict[str, Any]] = {}

        async def fetch_one(item: dict[str, Any]) -> None:
            url = str(item.get("url"))
            arguments: dict[str, Any] = {"url": url}
            if state.get("require_live"):
                arguments["refresh"] = True
            try:
                async with semaphore:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        outcomes[url] = {
                            "status": news_entities.FETCH_BUDGET_EXHAUSTED,
                            "error": f"整体正文抓取预算 {BODY_FETCH_BUDGET_SECONDS:.0f}s 已用尽",
                        }
                        return
                    envelope = await asyncio.wait_for(
                        call_tool(tools, "fetch_article", arguments),
                        timeout=min(BODY_FETCH_TIMEOUT_SECONDS, remaining),
                    )
            except asyncio.TimeoutError:
                outcomes[url] = {
                    "status": news_entities.FETCH_TIMEOUT,
                    "error": f"正文抓取超时（>{BODY_FETCH_TIMEOUT_SECONDS:.0f}s）",
                }
                return
            except Exception as exc:  # noqa: BLE001
                outcomes[url] = {
                    "status": news_entities.FETCH_FAILED,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                return
            if envelope.get("error"):
                message = str(envelope.get("error"))
                status = (
                    news_entities.FETCH_UNRESOLVABLE
                    if "UNRESOLVABLE" in str(envelope.get("error_code") or "").upper()
                    or "UNRESOLVABLE" in message.upper()
                    else news_entities.FETCH_FAILED
                )
                outcomes[url] = {"status": status, "error": message}
                return
            payload = envelope.get("data") or {}
            text_body = str(payload.get("text") or "")
            outcomes[url] = {
                "status": news_entities.FETCH_SUCCESS,
                "chars": len(text_body),
                "text": text_body,
                "error": None,
            }
            articles.append(payload)

        if fetchable:
            await asyncio.gather(*(fetch_one(item) for item in fetchable))

        elapsed = round(time.monotonic() - started, 2)

        def with_evidence(item: dict[str, Any]) -> dict[str, Any]:
            described = dict(item)
            fetch = outcomes.get(str(item.get("url")))
            described.update(news_entities.assess_evidence(described, entity=entity, fetch=fetch))
            if fetch and fetch.get("error"):
                described["fetch_error"] = fetch["error"]
            return described

        company = [with_evidence(item) for item in company]
        industry = [with_evidence(item) for item in industry]

        succeeded = sum(1 for item in outcomes.values() if item["status"] == news_entities.FETCH_SUCCESS)
        failed = sum(
            1
            for item in outcomes.values()
            if item["status"]
            in {news_entities.FETCH_FAILED, news_entities.FETCH_TIMEOUT, news_entities.FETCH_UNRESOLVABLE}
        )
        budget_exhausted = sum(
            1 for item in outcomes.values() if item["status"] == news_entities.FETCH_BUDGET_EXHAUSTED
        )
        # ``attempted`` counts calls that really started: succeeded + failed.
        # Skipped-because-budget items are reported separately rather than being
        # silently folded into the failure count.
        attempted = succeeded + failed
        pipeline = dict(data.get("pipeline") or {})
        pipeline.update(
            body_attempted=attempted,
            body_succeeded=succeeded,
            body_failed=failed,
            body_budget_exhausted=budget_exhausted,
            body_elapsed_s=elapsed,
        )
        for url, outcome in outcomes.items():
            if outcome.get("error"):
                errors.append(f"news.fetch_article {outcome['status']} for {url}: {outcome['error']}")

        fixed_envelope = dict(result)
        fixed_data = dict(data)
        fixed_data.update(
            items=company + industry,
            company_items=company,
            industry_items=industry,
            pipeline=pipeline,
        )
        fixed_envelope["data"] = fixed_data
        fixed_envelope["notes"] = list(result.get("notes") or []) + [
            f"正文抓取：尝试 {attempted} 篇，成功 {succeeded} 篇，"
            f"失败 {attempted - succeeded} 篇（含预算耗尽 {budget_exhausted} 篇），耗时 {elapsed}s。"
        ]
        return {
            "news": fixed_envelope,
            "company_news": company,
            "industry_news": industry,
            "articles": articles,
            "news_pipeline": pipeline,
            "errors": errors,
        }

    async def fetch_reserves(state: BriefState) -> dict[str, Any]:
        errors: list[str] = []
        source = state.get("reserve_source") or {}
        arguments: dict[str, Any] = {}
        if source.get("url"):
            arguments["pdf_url"] = source["url"]
        if state.get("require_live"):
            arguments["refresh"] = True
        try:
            result = await call_tool(tools, "extract_resources", arguments)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pdf.extract_resources failed: {type(exc).__name__}: {exc}")
            return {"reserves": None, "errors": errors}
        if result.get("error"):
            errors.append(f"pdf.extract_resources returned an error: {result['error']}")
        return {"reserves": result, "errors": errors}

    async def fetch_prices(state: BriefState) -> dict[str, Any]:
        errors: list[str] = []
        commodity = state.get("commodity", "lithium_carbonate")
        price = trend = None
        try:
            price = await call_tool(tools, "get_price", {"commodity": commodity})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"price.get_price failed: {type(exc).__name__}: {exc}")
        try:
            trend = await call_tool(tools, "get_trend", {"commodity": commodity, "days": 60})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"price.get_trend failed: {type(exc).__name__}: {exc}")

        optional: list[dict[str, Any]] = []
        for extra in ("lithium_spodumene",):
            if extra == commodity:
                continue
            try:
                result = await call_tool(tools, "get_price", {"commodity": extra})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"price.get_price({extra}) failed: {type(exc).__name__}: {exc}")
                continue
            if result.get("error"):
                errors.append(f"price.get_price({extra}) returned an error: {result['error']}")
                continue
            data = result.get("data") or {}
            optional.append(
                sanitize_optional_entry(
                    {
                        "commodity": extra,
                        "display_name": data.get("display_name", extra),
                        "price": data.get("price"),
                        "prev_close": data.get("prev_close"),
                        "change_pct": data.get("change_pct"),
                        "unit": data.get("unit"),
                        "as_of": data.get("as_of"),
                        "provenance": result.get("provenance"),
                        "data_status": result.get("data_status", "OK"),
                        "note": "；".join(result.get("notes") or [])[:160],
                    },
                    require_live=bool(state.get("require_live")),
                )
            )
        return {"price": price, "trend": trend, "optional_market": optional, "errors": errors}

    async def summarize_news(state: BriefState) -> dict[str, Any]:
        errors: list[str] = []
        items = ((state.get("news") or {}).get("data") or {}).get("items") or []
        if not items:
            return {
                "news_digest": {
                    "items": [],
                    "overall": "过去窗口内没有可用的新闻条目。",
                    "model": backend.mode,
                }
            }

        by_url = {article.get("url"): article for article in (state.get("articles") or [])}

        def enrich(item: dict[str, Any]) -> dict[str, Any]:
            article = by_url.get(item.get("url")) or {}
            return {
                "news_id": item.get("news_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "published_at": item.get("published_at"),
                "snippet": item.get("snippet"),
                "relevance": item.get("relevance"),
                "source_type": item.get("source_type"),
                "evidence": item.get("evidence"),
                "evidence_reason": item.get("evidence_reason"),
                "fetch_status": item.get("fetch_status"),
                "excerpt": (article.get("text") or "")[:800],
            }

        company_items = list(state.get("company_news") or [])
        industry_items = list(state.get("industry_news") or [])
        if not company_items and not industry_items:
            company_items = [item for item in items if item.get("relevance") == "company"][:6]
            industry_items = [item for item in items if item.get("relevance") == "industry"][:5]
        # Analyse as many items as the report displays, so no item ends up listed
        # without an analysis purely because it fell outside the model's window.
        payload_items = [enrich(item) for item in company_items[:8]]
        industry_payload = [enrich(item) for item in industry_items[:12]]

        user = prompts.NEWS_USER_TEMPLATE.format(
            topic=state.get("topic"),
            commodity=state.get("commodity"),
            today=datetime.now(CN_TZ).date().isoformat(),
            payload=json.dumps(
                {"company_items": payload_items, "industry_items": industry_payload},
                ensure_ascii=False,
                indent=1,
            ),
        )
        try:
            digest = await backend.structured(
                NewsDigest, prompts.NEWS_SYSTEM, user, stage="news_digest"
            )
            data = digest.model_dump()
            data["model"] = backend.mode

            # Re-key every analysed entry onto its source item. Models sometimes
            # drop or rewrite the url, and the report merges by url, so without
            # this the summary would silently disappear.
            sources_in_order = payload_items + industry_payload
            by_url = {item.get("url"): item for item in sources_in_order if item.get("url")}
            for index, entry in enumerate(data.get("items") or []):
                source = by_url.get(entry.get("url")) or (
                    sources_in_order[index] if index < len(sources_in_order) else None
                )
                if source:
                    entry["url"] = source.get("url") or entry.get("url") or ""
                    entry["title"] = entry.get("title") or source.get("title") or ""
                    entry["source"] = entry.get("source") or source.get("source") or ""
                    entry["published_at"] = entry.get("published_at") or source.get("published_at")
                    entry["news_id"] = source.get("news_id") or entry.get("news_id") or ""
            matched = len({entry.get("url") for entry in (data.get("items") or []) if entry.get("url")})
            if matched < len(sources_in_order):
                errors.append(
                    f"新闻分析覆盖 {matched}/{len(sources_in_order)} 条输入材料（模型未逐条返回）。"
                )
            return {"news_digest": data, "errors": errors}
        except Exception as exc:  # noqa: BLE001
            message = f"新闻分析（{backend.mode}）调用失败：{type(exc).__name__}: {exc}"
            if backend.mode == "deepseek":
                # 真实模型验收模式：直接失败，绝不静默降级为 stub / 空分析 / 固定文本
                raise LlmError(message) from exc
            errors.append(message)
            fallback = {
                "items": [
                    {
                        "title": item["title"],
                        "url": item["url"],
                        "source": item.get("source") or "",
                        "published_at": item.get("published_at"),
                        "summary": (item.get("snippet") or "")[:200],
                        "sentiment": "unknown",
                    }
                    for item in payload_items
                ],
                "overall": "模型不可用，仅列出原始条目，未做情绪与要点分析。",
                "model": "unavailable",
            }
            return {"news_digest": fallback, "errors": errors}

    async def assess_risk(state: BriefState) -> dict[str, Any]:
        errors: list[str] = []
        rules = rule_based_risks(state)
        news_items = _news_for_evidence(state)
        evidence_index = evidence_engine.build_evidence_index(state, news_items=news_items)
        evidence = {
            "price": ((state.get("price") or {}).get("data") or {}),
            "trend": {
                key: value
                for key, value in ((state.get("trend") or {}).get("data") or {}).items()
                if key in {"commodity", "change_pct", "min", "max", "first_close", "last_close", "coverage", "unit"}
            },
            "reserve_counts": ((state.get("reserves") or {}).get("data") or {}).get("counts"),
            "reserve_summary": ((state.get("reserves") or {}).get("data") or {}).get("summary"),
            "news_overall": ((state.get("news_digest") or {}).get("overall")),
            "provenance": {
                key: (state.get(key) or {}).get("provenance")
                for key in ("news", "reserves", "price")
            },
        }
        user = prompts.RISK_USER_TEMPLATE.format(
            topic=state.get("topic"),
            commodity=state.get("commodity"),
            generated_at=state.get("generated_at", ""),
            rules=json.dumps(rules, ensure_ascii=False, indent=1),
            evidence_index=json.dumps(evidence_index, ensure_ascii=False, indent=1, default=str),
            evidence=json.dumps(evidence, ensure_ascii=False, indent=1, default=str),
        )
        model_claims: list[dict[str, Any]] = []
        try:
            section = await backend.structured(
                RiskSection, prompts.RISK_SYSTEM, user, stage="risk_section"
            )
            bullets = section.bullets
            judgement = section.judgement
            model_claims = [claim.model_dump() for claim in section.claims]
        except Exception as exc:  # noqa: BLE001
            message = f"风险分析（{backend.mode}）调用失败：{type(exc).__name__}: {exc}"
            if backend.mode == "deepseek":
                raise LlmError(message) from exc
            errors.append(message)
            bullets = [rule["message"] for rule in rules[:5]]
            judgement = "模型不可用，本节仅列出确定性规则触发结果，未生成综合判断。"
            model_claims = []
        claims = evidence_engine.rule_claims(state, evidence_index)
        seen_ids = {claim["claim_id"] for claim in claims}
        for claim in model_claims:
            claim_id = str(claim.get("claim_id") or "")
            if claim_id and claim_id in seen_ids:
                continue
            seen_ids.add(claim_id)
            claims.append(claim)
        claims, claim_repairs = evidence_engine.repair_claims(
            evidence_index, claims, require_live=bool(state.get("require_live"))
        )
        errors.extend(claim_repairs)
        claim_issues = evidence_engine.validate_claims(
            evidence_index, claims, require_live=bool(state.get("require_live"))
        )
        return {
            "risks": rules,
            "llm_risks": {"bullets": bullets, "model": backend.mode},
            "judgement": judgement,
            "claims": claims,
            "claim_issues": claim_issues,
            "evidence_index": evidence_index,
            "errors": errors,
        }

    async def validate_quality(state: BriefState) -> dict[str, Any]:
        report = run_quality_checks(dict(state))
        updates: dict[str, Any] = {
            "quality": report,
            "unverified": report.get("unverified") or [],
        }
        if report.get("news"):
            updates["news"] = report["news"]
        notes = list(report.get("diagnostics") or [])
        notes += [
            f"质量校验（{item['severity']}）：{item['code']} - {item['message']}"
            for item in (report.get("issues") or [])
        ]
        if notes:
            updates["errors"] = notes
        return updates

    async def render(state: BriefState) -> dict[str, Any]:
        markdown = render_report(dict(state))
        path = write_report(
            markdown,
            topic=state.get("topic", "brief"),
            generated_at=state.get("generated_at") or datetime.now(CN_TZ).isoformat(timespec="seconds"),
            slug=state.get("slug") or "brief",
            out_dir=state.get("out_dir"),
        )
        return {"report_markdown": markdown, "report_path": str(path)}

    graph = StateGraph(BriefState)
    graph.add_node("parse_intent", parse_intent)
    graph.add_node("fetch_news", fetch_news)
    graph.add_node("fetch_reserves", fetch_reserves)
    graph.add_node("fetch_prices", fetch_prices)
    graph.add_node("summarize_news", summarize_news)
    graph.add_node("assess_risk", assess_risk)
    graph.add_node("validate_quality", validate_quality)
    graph.add_node("render", render)

    graph.set_entry_point("parse_intent")
    for node in ("fetch_news", "fetch_reserves", "fetch_prices"):
        graph.add_edge("parse_intent", node)
    graph.add_edge(["fetch_news", "fetch_reserves", "fetch_prices"], "summarize_news")
    graph.add_edge("summarize_news", "assess_risk")
    graph.add_edge("assess_risk", "validate_quality")
    graph.add_edge("validate_quality", "render")
    graph.add_edge("render", END)
    return graph.compile()


def collect_provenance(state: dict[str, Any]) -> dict[str, Any]:
    provenance_map: dict[str, Any] = {}
    for dataset in ("news", "reserves", "price", "trend"):
        envelope = state.get(dataset)
        provenance_map[dataset] = {
            "provenance": (envelope or {}).get("provenance"),
            "source": (envelope or {}).get("source"),
            "source_url": (envelope or {}).get("source_url"),
            "as_of": (envelope or {}).get("as_of"),
            "data_status": (envelope or {}).get("data_status"),
            "error": (envelope or {}).get("error"),
        }
    return provenance_map


def live_gate_failures(state: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for dataset in REQUIRED_DATASETS:
        envelope = state.get(dataset) or {}
        if envelope.get("error"):
            failures.append(f"{dataset}: {envelope['error']}")
            continue
        if not envelope:
            failures.append(f"{dataset}: no response from the MCP server")
            continue
        if envelope.get("provenance") != "live":
            failures.append(
                f"{dataset}: provenance={envelope.get('provenance')} (source={envelope.get('source')})"
            )
    for issue in ((state.get("quality") or {}).get("errors") or []):
        failures.append(f"quality.{issue['code']}: {issue['message']}")
    return failures


__all__ = [
    "BODY_FETCH_BUDGET_SECONDS",
    "BODY_FETCH_CONCURRENCY",
    "BODY_FETCH_LIMIT",
    "BODY_FETCH_TIMEOUT_SECONDS",
    "build_graph",
    "collect_provenance",
    "detect_commodity",
    "live_gate_failures",
    "resolve_source",
    "sanitize_optional_entry",
]

