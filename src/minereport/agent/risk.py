"""Rule-based risk signals. Deterministic, auditable, never LLM-invented."""

from __future__ import annotations

from typing import Any

SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


def primary_resource_rows(reserves: dict[str, Any] | None) -> tuple[list[dict[str, Any]], int | None]:
    """Rows of the headline statement only.

    Real reports repeat the resource table in appendices broken down by deposit.
    Those breakdown rows are not additive to the headline table, so summing all
    of them overstates the totals (a real DeepSeek run flagged this). We keep
    only the rows from the earliest page that carries a resource table, which is
    where the primary statement is published, and say so in the payload.
    """

    if not reserves or reserves.get("error"):
        return [], None
    rows = [
        row
        for row in ((reserves.get("data") or {}).get("mineral_resources") or [])
        if row.get("tonnes") is not None
        and row.get("statement_scope", "project_summary") == "project_summary"
    ]
    if not rows:
        return [], None
    first_page = min(row.get("page") or 0 for row in rows)
    return [row for row in rows if (row.get("page") or 0) == first_page], first_page


def resource_totals(reserves: dict[str, Any] | None) -> dict[str, float]:
    totals: dict[str, float] = {}
    rows, _ = primary_resource_rows(reserves)
    for row in rows:
        unit = row.get("tonnes_unit") or "unit"
        totals[unit] = totals.get(unit, 0.0) + float(row["tonnes"])
    return totals


def inferred_share(reserves: dict[str, Any] | None) -> tuple[float | None, int | None]:
    rows, page = primary_resource_rows(reserves)
    totals: dict[str, float] = {}
    for row in rows:
        unit = row.get("tonnes_unit") or "unit"
        totals[unit] = totals.get(unit, 0.0) + float(row["tonnes"])
    if not totals:
        return None, page
    unit = max(totals, key=lambda key: totals[key])
    if totals[unit] <= 0:
        return None, page
    inferred = sum(
        float(row.get("tonnes") or 0.0)
        for row in rows
        if row.get("category") == "Inferred" and (row.get("tonnes_unit") or "unit") == unit
    )
    return round(inferred / totals[unit] * 100, 2), page


def rule_based_risks(state: dict[str, Any]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []

    def add(severity: str, code: str, message: str) -> None:
        risks.append({"severity": severity, "code": code, "message": message})

    # --- price momentum -------------------------------------------------
    trend = state.get("trend") or {}
    if trend and not trend.get("error"):
        change = (trend.get("data") or {}).get("change_pct")
        if change is not None:
            window = (trend.get("data") or {}).get("requested_days")
            if change <= -15:
                add("high", "price_drawdown", f"近 {window} 天下游参考价下跌 {change:.2f}%，价格风险显著。")
            elif change <= -7:
                add("medium", "price_pullback", f"近 {window} 天下游参考价回落 {change:.2f}%。")
            elif change >= 15:
                add("medium", "price_rally", f"近 {window} 天下游参考价上涨 {change:.2f}%，需关注回调风险。")

    price = state.get("price") or {}
    if price and not price.get("error"):
        daily = (price.get("data") or {}).get("change_pct")
        if daily is not None and abs(daily) >= 5:
            add("medium", "price_daily_move", f"最近交易日环比变动 {daily:.2f}%，短期波动偏大。")

    # --- news sentiment -------------------------------------------------
    digest = state.get("news_digest") or {}
    items = digest.get("items") or []
    if items:
        negative = [item for item in items if item.get("sentiment") == "negative"]
        ratio = len(negative) / len(items)
        if ratio >= 0.5:
            add("medium", "news_negative", f"{len(negative)}/{len(items)} 条新闻为负面口径。")
        elif ratio == 0 and str(digest.get("model", "")).startswith("stub"):
            add("info", "news_stub", "新闻情绪判定来自 stub 后端，未经过真实模型分析。")

    # --- resource quality ----------------------------------------------
    share, page = inferred_share(state.get("reserves"))
    if share is not None:
        if share > 40:
            add(
                "medium",
                "inferred_share",
                f"主表（p{page}）Inferred 资源量占 {share:.1f}%，地质置信度偏低。",
            )
        else:
            add(
                "info",
                "inferred_share",
                f"主表（p{page}）Inferred 资源量占 {share:.1f}%，未超过 40% 阈值。"
                "（仅统计主表，附录按矿区拆分的明细行不计入，避免重复加总。）",
            )

    reserves = state.get("reserves")
    if not reserves or reserves.get("error"):
        add("high", "reserves_missing", "未能获取可验证的官方资源量/储量数据，资源结论不成立。")

    # --- provenance / data quality -------------------------------------
    for dataset in ("news", "reserves", "price"):
        envelope = state.get(dataset) or {}
        provenance = envelope.get("provenance")
        if provenance and provenance != "live":
            add(
                "high",
                f"{dataset}_not_live",
                f"{dataset} 数据来源为 {provenance}，不得作为实时结论依据。",
            )

    optional = state.get("optional_market") or []
    for entry in optional:
        if entry.get("data_status") == "DATA_MISSING" or entry.get("provenance") not in {None, "live"}:
            if state.get("require_live"):
                add(
                    "medium",
                    "spodumene_unavailable",
                    "锂辉石精矿暂无本次可验证的真实市场报价，严格 Live 模式未使用离线模拟价格，"
                    "因此无法据此量化 Pilgangoora 的实际销售价格及盈利影响。",
                )
            else:
                add(
                    "medium",
                    "spodumene_unavailable",
                    f"{entry.get('display_name') or '该商品'}使用的是仓库内置离线样例报价（fixture），"
                    "仅用于演示，不代表实际销售价格。",
                )

    if price and not price.get("error") and not (price.get("data") or {}).get("venue_official", True):
        add(
            "info",
            "third_party_feed",
            "LME 价格为第三方行情转述（新浪外盘），不是 LME 官方 API 数据。",
        )

    if (state.get("commodity") == "lithium_carbonate") and "spodumene" in (state.get("topic") or "").lower():
        add("info", "downstream_proxy", "碳酸锂为下游参考价，不能代表锂辉石精矿销售价。")

    risks.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], 9))
    return risks


__all__ = ["SEVERITY_ORDER", "inferred_share", "primary_resource_rows", "resource_totals", "rule_based_risks"]