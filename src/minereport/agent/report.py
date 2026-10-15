"""Markdown rendering of the daily brief, with provenance badges everywhere.

Layout rules learned from the real runs:
* list/table bodies are rendered in Python and injected as fragments - mixing
  Jinja control tags with Markdown newlines glued rows together;
* diagnostics never appear inside the news section;
* strict-live runs mask every non-live value instead of printing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment

from ..common import config
from ..common.provenance import PROVENANCE_LABELS
from ..news.entities import EVIDENCE_LABELS

PROVENANCE_ORDER = ("live", "cached", "fixture", "synthetic")
MISSING_COMPANY_NEWS = "检索期内暂无可验证的公司直接相关新闻。"
DATA_MISSING = "DATA_MISSING"

_ENV = Environment(
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=ChainableUndefined,
)


def provenance_badge(envelope: dict[str, Any] | None) -> str:
    if not envelope:
        return "> ⚠ 本节无数据。"
    provenance = envelope.get("provenance", "unknown")
    label = PROVENANCE_LABELS.get(provenance, provenance)  # type: ignore[arg-type]
    if provenance == "live":
        return ""
    return f"> ⚠ 数据来源：{label}，非实时数据，不得作为实时结论依据。"


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:,.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _clip(text: Any, limit: int = 120) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines if line) if lines else ""


def _table(header: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        out.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    return "\n".join(out)


def _mask(el: dict[str, Any] | None, require_live: bool) -> dict[str, Any] | None:
    """Strict-live runs must never print a fixture/synthetic value as fact."""

    if not require_live or not el:
        return el
    if el.get("provenance") == "live" and not el.get("error"):
        return el
    return None


def _clean_display_name(value: Any) -> str:
    """Fixture wording belongs to demo mode, never to a strict-live row."""

    text = str(value or "")
    for marker in ("（离线样例）", "(离线样例)", "（离线 fixture）", "(offline fixture)"):
        text = text.replace(marker, "")
    return text.strip()


def _missing_row(label: str, reason: str) -> list[str]:
    return [label, DATA_MISSING, "—", "—", "—", reason]


TEMPLATE = _ENV.from_string(
    """# {{ topic }} · 矿权日报

| 项目 | 值 |
| --- | --- |
| 查询 | `{{ query }}` |
| 生成时间（Asia/Shanghai） | {{ generated_at }} |
| 新闻检索窗口 | 近 {{ days }} 天 |
| 主商品 | `{{ commodity }}` |
| LLM 模式 | {{ llm_mode }} |
| 验收模式 | {{ live_mode_text }} |

> **数据可信度声明**：live = 本次真实获取；cached = 此前真实获取后的本地缓存；fixture = 仓库内置离线样例；synthetic = 程序合成。`as_of` 是数据自身的日期（可能早于抓取时间），`retrieved_at` 是抓取时刻；`live` 只表示"本次真实获取"，不表示"最新披露"。

## 0. 数据来源与可信度

{{ provenance_table }}

## 1. 新闻摘要

{{ news_block }}

## 2. 矿产资源量（Mineral Resources）

{{ resources_block }}

## 3. 矿石储量（Ore Reserves）

{{ ore_reserves_block }}

## 4. 价格走势

{{ price_block }}

## 5. 风险提示

### 5.1 规则触发（确定性）

{{ rules_block }}

### 5.2 综合判断（{{ llm_mode }}）

{{ judgement_block }}

{% if claims_block %}### 5.3 结论—证据对照

{{ claims_block }}
{% endif %}

## 6. 引用源链接

{{ links_block }}

## 7. 数据缺失与限制

{{ limitations_block }}

## 8. 运行诊断

{{ diagnostics_block }}

---

*本报告由 minereport Agent 生成。数据来源与 provenance 全部记录在 `docs/VERIFICATION.md` 与 `data/sources/*.json`；非 live 数据一律标注，不以模拟数据冒充真实数据。*
"""
)


def _provenance_table(state: dict[str, Any]) -> str:
    rows: list[list[Any]] = []
    for dataset in ("news", "reserves", "price", "trend"):
        envelope = state.get(dataset) or {}
        if not envelope:
            rows.append([dataset, "`missing`", "—", "—", "—", "未获得响应"])
            continue
        note = "；".join(envelope.get("notes") or [])
        if envelope.get("error"):
            note = f"ERROR: {envelope['error']}"
        rows.append(
            [
                dataset,
                f"`{envelope.get('provenance', 'unknown')}`",
                envelope.get("data_status", "OK"),
                _clip(envelope.get("source", "—"), 48),
                envelope.get("as_of") or "—",
                envelope.get("retrieved_at") or "—",
                _clip(note, 120) or "—",
            ]
        )
    return _table(
        ["数据集", "provenance", "data_status", "来源", "数据日期 as_of", "获取时间 retrieved_at", "备注"],
        rows,
    )


def _news_item_lines(item: dict[str, Any], index: int, *, prefix: str) -> list[str]:
    title = item.get("title") or item.get("url") or "(无标题)"
    when = f" · {item['published_at']}" if item.get("published_at") else ""
    source = item.get("source") or item.get("provider") or "n/a"
    evidence = EVIDENCE_LABELS.get(str(item.get("evidence")), str(item.get("evidence") or "未评估"))
    news_id = item.get("news_id") or "n/a"
    lines = [f"**{prefix}{index}. {title}**{when}"]
    lines.append(
        f"  - 来源：{source} · 证据：{evidence} · 情绪：`{item.get('sentiment') or '未评估'}`"
        f" · news_id：`{news_id}`"
    )
    if item.get("summary"):
        lines.append(f"  - 要点：{item['summary']}")
    if item.get("url"):
        lines.append(f"  - 链接：[{_clip(title, 60)}]({item['url']})")
    return lines


def _news_block(state: dict[str, Any]) -> str:
    news = state.get("news") or {}
    data = news.get("data") or {}
    digest = state.get("news_digest") or {}
    require_live = bool(state.get("require_live"))

    if _mask(news, require_live) is None:
        return f"{provenance_badge(news)}\n\n{DATA_MISSING}：严格 live 模式下新闻数据不是本次真实获取。".strip()

    company = data.get("company_items") or []
    industry = data.get("industry_items") or []
    digest_by_url = {item.get("url"): item for item in (digest.get("items") or [])}

    parts: list[str] = []
    badge = provenance_badge(news)
    if badge:
        parts.extend([badge, ""])

    entity = (data.get("entity") or {}).get("name")
    if entity:
        parts.append(f"公司实体：**{entity}**（判定依据：公司正式名称 / 股票代码 / 项目名称）")
        parts.append("")
    parts.append(
        "> 证据等级：`官方公告` > `完整正文` > `RSS 摘要` > `仅标题`。证据等级表示可核验程度，"
        "不等于新闻真实性；`仅标题`/`RSS 摘要` 级记录不得作为产量、成本、停产等经营事实的依据。"
    )
    parts.append("")

    parts.append("### 1.1 公司直接相关新闻")
    parts.append("")
    if company:
        for index, item in enumerate(company, start=1):
            merged = dict(item)
            merged.update(digest_by_url.get(item.get("url")) or {})
            parts.extend(_news_item_lines(merged, index, prefix="C"))
            parts.append("")
    else:
        parts.append(MISSING_COMPANY_NEWS)
        parts.append("")

    parts.append("### 1.2 锂行业背景新闻")
    parts.append("")
    if industry:
        for index, item in enumerate(industry, start=1):
            merged = dict(item)
            merged.update(digest_by_url.get(item.get("url")) or {})
            parts.extend(_news_item_lines(merged, index, prefix="I"))
            parts.append("")
    else:
        parts.append("检索期内暂无相关的行业背景新闻。")

    if digest.get("overall"):
        parts.append("")
        parts.append(f"**整体判断（{digest.get('model', 'unknown')}）**：{digest['overall']}")

    pipeline = data.get("pipeline") or {}
    if pipeline:
        fetched = int(pipeline.get("fetched") or 0)
        removed = int(pipeline.get("duplicate_removed") or 0)
        unique = int(pipeline.get("unique") or 0)
        company_c = int(pipeline.get("company_candidates") or 0)
        industry_c = int(pipeline.get("industry_candidates") or 0)
        irrelevant = int(pipeline.get("irrelevant") or 0)
        closed = (fetched - removed == unique) and (company_c + industry_c + irrelevant == unique)
        parts.append("")
        parts.append("### 1.3 新闻处理流水")
        parts.append("")
        parts.append(
            _table(
                ["阶段", "计数", "口径"],
                [
                    ["抓取（各查询 × 各来源累加）", fetched, "原始条数，含重复"],
                    ["去重移除", removed, "按 news_id / 标题去重"],
                    ["去重后唯一", unique, "= 抓取 − 去重移除"],
                    ["公司直接相关（候选）", company_c, "互斥分类"],
                    ["行业背景（候选）", industry_c, "互斥分类"],
                    ["过滤无关", irrelevant, "互斥分类；三者之和 = 去重后唯一"],
                    ["公司展示", pipeline.get("company_displayed", 0), "上限 20 条"],
                    ["行业展示", pipeline.get("industry_displayed", 0), "上限 12 条"],
                    ["因上限未展示", pipeline.get("not_displayed_by_limit", 0), "候选 − 展示"],
                    ["无发布时间", pipeline.get("undated", 0), "不参与 as_of 计算"],
                    [
                        "正文抓取（尝试/成功/失败）",
                        f"{pipeline.get('body_attempted', 0)} / {pipeline.get('body_succeeded', 0)} / "
                        f"{pipeline.get('body_failed', 0)}",
                        f"预算耗尽 {pipeline.get('body_budget_exhausted', 0)} 篇，"
                        f"耗时 {pipeline.get('body_elapsed_s', 0)}s",
                    ],
                ],
            )
        )
        parts.append("")
        parts.append(
            f"> 闭合校验：{fetched} − {removed} = {unique}；"
            f"{company_c} + {industry_c} + {irrelevant} = {company_c + industry_c + irrelevant}"
            f"（{'一致 ✓' if closed else '不一致 ✗'}）。互斥分类只统计去重后的唯一新闻，"
            "展示上限只影响展示条数。"
        )
    return "\n".join(parts).strip()


def _reserves_header(reserves: dict[str, Any], badge: str) -> list[str]:
    document = reserves.get("document") or {}
    primary = reserves.get("primary_pages") or {}
    lines = [
        badge,
        "",
        f"- 报告主体：{reserves.get('reporter_entity')}",
        f"- 项目：{reserves.get('project_name')}",
        f"- 资源量生效日期（effective_date）：{reserves.get('effective_date') or reserves.get('report_date') or '未标注'}"
        f"（主表页码 p{primary.get('MineralResource', '?')}）",
        f"- 公告/披露日期（announced_date）：{reserves.get('announced_date') or '未标注'}",
        f"- 本次获取时间（fetched_at）：{reserves.get('fetched_at') or '未标注'}",
        "- 文档时效："
        + (
            f"距今 {int(reserves['age_days'])} 天"
            + ("（历史数据，非当前最新披露）" if reserves.get("is_historical") else "")
            if isinstance(reserves.get("age_days"), (int, float))
            else "未标注"
        )
        + (f"；来源选择：{reserves['selection_reason']}" if reserves.get("selection_reason") else ""),
        f"- 标准：{', '.join(reserves.get('standards') or []) or '未判定'}",
        f"- 文档：{document.get('resolved_url')}",
        f"- 文档 SHA-256：`{str(document.get('sha256') or '')[:16]}…`（共 {document.get('page_count')} 页）",
    ]
    for evidence in reserves.get("standard_evidence") or []:
        lines.append(f"- 标准声明证据（p{evidence.get('page')}）：{_clip(evidence.get('quote'), 160)}")
    registry_note = (reserves.get("registry") or {}).get("hosting_note")
    if registry_note:
        lines.append(f"- 托管说明：{registry_note}")
    if reserves.get("disclosure_note"):
        lines.append(f"- ⚠ 时效说明：{reserves['disclosure_note']}")
    return lines


def _category_table(rows: list[dict[str, Any]]) -> str:
    return _table(
        ["分类", "矿石量", "Li₂O 品位", "含 Li₂O 量", "页码", "原文证据"],
        [
            [
                f"{row.get('category')}（{row.get('group')}）",
                f"{_fmt(row.get('tonnes'))} {row.get('tonnes_unit') or ''}",
                f"{_fmt(row.get('grade'))} {row.get('grade_unit') or ''}",
                f"{_fmt(row.get('contained'))} {row.get('contained_unit') or ''}",
                f"p{row.get('page')}",
                _clip(row.get("quote"), 90),
            ]
            for row in rows
        ],
    )


def _resources_block(state: dict[str, Any]) -> str:
    envelope = state.get("reserves") or {}
    require_live = bool(state.get("require_live"))
    if _mask(envelope, require_live) is None:
        return f"{provenance_badge(envelope)}\n\n**{DATA_MISSING}**：严格 live 模式下未获得本次真实资源量文档。".strip()

    badge = provenance_badge(envelope)
    reserves = envelope.get("data")
    if not reserves:
        text = [badge, "", "**未获取到可验证的矿产资源量数据。** 本报告不使用其他公司或合成数据顶替。"]
        if envelope.get("error"):
            text.extend(["", f"- 失败原因：{envelope['error']}"])
        return "\n".join(text)

    rows = reserves.get("mineral_resources") or []
    lines = _reserves_header(reserves, badge)
    lines.append("")
    if rows:
        lines.append("**项目级资源量汇总（主表）**")
        lines.append("")
        lines.append(_category_table(rows))
        lines.append("")
        lines.append(
            "> 说明：Measured / Indicated / Inferred 属于**矿产资源量**，不是矿石储量；"
            "上表仅为项目级汇总，每类只出现一次。"
        )
        appendix = (reserves.get("appendix") or {}).get("mineral_resources") or []
        if appendix:
            lines.append("")
            lines.append(f"**附录明细（{len(appendix)} 行，来自 p{(reserves.get('primary_pages') or {}).get('MineralResource', '?')} 之后的分区表）**")
            lines.append("")
            lines.append(
                "> ⚠ 附录按矿区/矿体拆分，是上表汇总的构成部分，**不可与汇总值相加**。"
            )
            lines.append("")
            lines.append(_category_table(appendix))
    else:
        lines.append("该文档未提取到矿产资源量分类。")
    return "\n".join(lines)


def _ore_reserves_block(state: dict[str, Any]) -> str:
    envelope = state.get("reserves") or {}
    require_live = bool(state.get("require_live"))
    if _mask(envelope, require_live) is None:
        return f"**{DATA_MISSING}**：严格 live 模式下未获得本次真实储量文档。"
    reserves = envelope.get("data")
    if not reserves:
        return "未获取到储量数据。"
    rows = reserves.get("ore_reserves") or []
    if not rows:
        return "未提取到矿石储量分类（该报告可能只披露资源量）。"
    lines = [provenance_badge(envelope), "", "**项目级矿石储量汇总（主表）**", "", _category_table(rows), ""]
    lines.append(
        "> 说明：Proved / Probable 属于**矿石储量**，是在经济可采假设下的结果，与矿产资源量不可混用。"
    )
    appendix = (reserves.get("appendix") or {}).get("ore_reserves") or []
    if appendix:
        lines.extend(["", f"**附录明细（{len(appendix)} 行）**", "", _category_table(appendix)])
    return "\n".join(lines)


def _price_block(state: dict[str, Any]) -> str:
    require_live = bool(state.get("require_live"))
    price_envelope = state.get("price") or {}
    trend_envelope = state.get("trend") or {}
    price = (_mask(price_envelope, require_live) or {}).get("data")
    trend = (_mask(trend_envelope, require_live) or {}).get("data")

    optional_rows: list[list[Any]] = []
    for entry in state.get("optional_market") or []:
        if require_live and (entry.get("data_status") == DATA_MISSING or entry.get("provenance") != "live"):
            optional_rows.append(
                [
                    _clean_display_name(entry.get("display_name")),
                    DATA_MISSING,
                    entry.get("unit") or "—",
                    "—",
                    "—",
                    entry.get("reason")
                    or "严格 Live 模式：本次无可验证的真实报价，未使用离线模拟价格。",
                ]
            )
        else:
            optional_rows.append(
                [
                    _clean_display_name(entry.get("display_name")),
                    f"{_fmt(entry.get('price'))}",
                    entry.get("unit"),
                    entry.get("as_of") or "—",
                    f"`{entry.get('provenance')}`",
                    _clip(entry.get("note") or entry.get("reason"), 120),
                ]
            )

    if not price and not trend and not optional_rows:
        return f"{DATA_MISSING}：严格 live 模式下未获得本次真实行情。" if require_live else "未获得可用的行情数据。"

    lines: list[str] = []
    badge = provenance_badge(price_envelope) or provenance_badge(trend_envelope)
    if badge:
        lines.extend([badge, ""])
    if price:
        venue = price.get("venue")
        official = "" if price.get("venue_official") else " · 第三方转述（非交易所官方 API）"
        lines.append(f"- 标的：{price.get('display_name')}（{venue}{official}）")
        lines.append(f"- 合约/规格：{price.get('contract_spec')}")
        lines.append(
            f"- 最新报价：**{_fmt(price.get('price'))} {price.get('unit')}**"
            f"（as_of {price.get('as_of')}，{price.get('quote_basis') or 'n/a'}）"
        )
        lines.append(
            f"- 前一交易日：{_fmt(price.get('prev_close'))} {price.get('unit')} · "
            f"变动：{_fmt(price.get('change_pct'))}%"
        )
        lines.append(f"- 来源：{price.get('source')}")
        if price.get("venue_note"):
            lines.append(f"- 来源说明：{price['venue_note']}")
        if price.get("downstream_note"):
            lines.append(f"- 口径说明：{price['downstream_note']}")
    if trend:
        coverage = trend.get("coverage") or {}
        lines.extend(
            [
                "",
                f"**近 {trend.get('requested_days')} 天走势**"
                f"（{coverage.get('start')} ~ {coverage.get('end')}，{coverage.get('trading_days')} 个交易日）",
                "",
                f"`{trend.get('sparkline')}`",
                "",
                f"区间涨跌：{_fmt(trend.get('change_pct'))}% · 最低 {_fmt(trend.get('min'))} · "
                f"最高 {_fmt(trend.get('max'))} {trend.get('unit')}",
                "",
            ]
        )
        points = trend.get("points") or []
        shown = points[-15:] if len(points) > 15 else points
        lines.append(
            _table(["日期", "收盘"], [[point.get("date"), _fmt(point.get("close"))] for point in shown])
        )
    if optional_rows:
        lines.extend(
            [
                "",
                "### 4.1 其他相关市场",
                "",
                _table(["标的", "报价", "单位", "数据日期", "provenance", "说明"], optional_rows),
            ]
        )
    return "\n".join(lines)


def _rules_block(state: dict[str, Any]) -> str:
    rules = [rule for rule in (state.get("risks") or []) if rule.get("severity") != "info"]
    if not rules:
        return "未触发阈值型风险规则。"
    return _table(
        ["级别", "代码", "说明"],
        [[rule.get("severity"), f"`{rule.get('code')}`", rule.get("message")] for rule in rules],
    )


def _links_block(state: dict[str, Any]) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for label, envelope in (
        ("新闻数据源", state.get("news") or {}),
        ("资源量文档", state.get("reserves") or {}),
        ("行情数据源", state.get("price") or {}),
        ("走势数据源", state.get("trend") or {}),
    ):
        url = envelope.get("source_url")
        if url and url not in seen:
            seen.add(url)
            lines.append(f"[{label}]({url})")
    for article in state.get("articles") or []:
        url = article.get("url")
        if url and str(url).startswith("http") and url not in seen:
            seen.add(url)
            title = _clip(article.get("title") or url, 70)
            lines.append(f"[{title}]({url})")
    return _bullets(lines) or "无可用链接。"


def _limitations_block(state: dict[str, Any]) -> str:
    items: list[str] = []
    for item in state.get("unverified") or []:
        items.append(f"{item['dataset']}：{item['reason']}")
    price_data = ((state.get("price") or {}).get("data")) or {}
    if price_data.get("downstream_note"):
        items.append(str(price_data["downstream_note"]))
    items.extend(str(note) for note in ((state.get("trend") or {}).get("notes") or []))
    registry_note = (((state.get("reserves") or {}).get("data") or {}).get("registry") or {}).get(
        "hosting_note"
    )
    if registry_note:
        items.append(str(registry_note))
    disclosure = ((state.get("reserves") or {}).get("data") or {}).get("disclosure_note")
    if disclosure:
        items.append(str(disclosure))
    for item in ((state.get("quality") or {}).get("warnings") or []):
        items.append(f"质量校验（{item['severity']}）：{item['message']}")
    # de-duplicate while keeping order
    unique = list(dict.fromkeys(items))
    return _bullets(unique) if unique else "无额外限制记录。"


def _claims_block(state: dict[str, Any]) -> str:
    claims = state.get("claims") or []
    index = state.get("evidence_index") or {}
    if not claims:
        return ""
    rows: list[list[Any]] = []
    for claim in claims:
        value = claim.get("value")
        shown = "—" if value is None else f"{_fmt(value)} {claim.get('unit') or ''}".strip()
        evidence_ids = list(claim.get("evidence_ids") or [])
        links: list[str] = []
        for evidence_id in evidence_ids[:3]:
            record = index.get(evidence_id) or {}
            url = record.get("url")
            links.append(f"[{evidence_id}]({url})" if url else f"`{evidence_id}`")
        if len(evidence_ids) > 3:
            links.append(f"等 {len(evidence_ids)} 条")
        rows.append(
            [
                f"`{claim.get('claim_id')}`",
                _clip(claim.get("claim"), 80),
                shown,
                "、".join(links) or "—",
            ]
        )
    return _table(["claim_id", "结论", "数值", "证据（ID / 链接）"], rows)


def _diagnostics_block(state: dict[str, Any]) -> str:
    notes = list(state.get("errors") or [])
    intent_notes = list(state.get("intent_notes") or [])
    lines = [f"**运行备注**：{note}" for note in notes]
    lines += [f"**意图解析**：{note}" for note in intent_notes]
    pipeline = state.get("news_pipeline") or ((state.get("news") or {}).get("data") or {}).get("pipeline") or {}
    if pipeline:
        lines.append(
            "**正文抓取**："
            f"尝试 {pipeline.get('body_attempted', 0)} 篇，成功 {pipeline.get('body_succeeded', 0)} 篇，"
            f"失败 {pipeline.get('body_failed', 0)} 篇（预算耗尽 {pipeline.get('body_budget_exhausted', 0)}），"
            f"耗时 {pipeline.get('body_elapsed_s', 0)}s。"
        )
    for check in (state.get("quality") or {}).get("checks") or []:
        detail = f" — {check['detail']}" if check.get("detail") else ""
        lines.append(f"**质量校验·{check['name']}**：{check['status']}{detail}")
    return _bullets(lines) if lines else "无异常记录。"


def render_report(state: dict[str, Any]) -> str:
    llm_risks = state.get("llm_risks") or {}
    bullets = llm_risks.get("bullets") or []
    judgement = state.get("judgement") or "未生成综合判断。"
    judgement_block = _bullets(list(bullets)) + ("\n\n" + judgement if judgement else "")

    context = {
        "topic": state.get("topic", state.get("query", "")),
        "query": state.get("query", ""),
        "days": state.get("days", 7),
        "commodity": state.get("commodity", "—"),
        "generated_at": state.get("generated_at", ""),
        "llm_mode": state.get("llm_mode", "unknown"),
        "live_mode_text": (
            "require-live（严格：非 live 数据一律不写入结论）"
            if state.get("require_live")
            else "演示模式（允许降级，但全部标注）"
        ),
        "provenance_table": _provenance_table(state),
        "news_block": _news_block(state),
        "resources_block": _resources_block(state),
        "ore_reserves_block": _ore_reserves_block(state),
        "price_block": _price_block(state),
        "rules_block": _rules_block(state),
        "judgement_block": judgement_block,
        "claims_block": _claims_block(state),
        "links_block": _links_block(state),
        "limitations_block": _limitations_block(state),
        "diagnostics_block": _diagnostics_block(state),
    }
    return TEMPLATE.render(**context)


def write_report(
    markdown: str,
    *,
    topic: str,
    generated_at: str,
    slug: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    directory = Path(out_dir) if out_dir else config.REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    if not slug:
        slug = "".join(ch if ch.isalnum() else "-" for ch in topic.lower()).strip("-") or "brief"
    slug = "-".join(part for part in slug.split("-") if part)[:60] or "brief"
    date_part = generated_at.split("T")[0]
    path = directory / f"{date_part}-{slug}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


__all__ = ["provenance_badge", "render_report", "write_report"]