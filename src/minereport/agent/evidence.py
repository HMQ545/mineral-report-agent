"""Deterministic evidence registry and structured risk-claim validation.

Every number that appears in a risk conclusion has to be traceable to an entry
in this registry. The model may only cite ids that exist here, and the checks
below re-compute what can be re-computed instead of trusting the model's prose.
"""

from __future__ import annotations

from typing import Any

PRICE_SPOT_001 = "price_spot_001"
PRICE_TREND_001 = "price_trend_001"
RESOURCE_SUMMARY_001 = "resource_summary_001"
RESOURCE_INFERRED_001 = "resource_inferred_001"

CALC_PERCENTAGE_CHANGE = "percentage_change"
CALC_SHARE = "share"

# Text markers that mean "this conclusion is about production/cost/curtailment",
# i.e. operating facts that a share-price story cannot establish.
OPERATING_MARKERS = ("产量", "成本", "停产", "减产", "扩产", "盈利", "margin", "production", "cost")

# Phrasing that already says "we cannot tell" must not be flagged as overreach -
# that sentence is exactly what the brief is supposed to write.
HEDGE_MARKERS = (
    "未披露",
    "未提供",
    "未提及",
    "无公开",
    "缺乏",
    "尚无法确认",
    "无法确认",
    "无法核实",
    "不能据此",
    "暂无",
)

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _envelope_usable(envelope: dict[str, Any] | None, require_live: bool) -> bool:
    if not envelope or envelope.get("error"):
        return False
    if require_live and envelope.get("provenance") != "live":
        return False
    return envelope.get("data_status", "OK") != "DATA_MISSING"


def build_evidence_index(state: dict[str, Any], *, news_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Deterministic ``id -> evidence`` map the model (and the gate) reason from."""

    require_live = bool(state.get("require_live"))
    index: dict[str, dict[str, Any]] = {}

    price_envelope = state.get("price") or {}
    price = (price_envelope.get("data") or {}) if price_envelope else {}
    if price and _envelope_usable(price_envelope, require_live):
        index[PRICE_SPOT_001] = {
            "kind": "price",
            "data_status": price_envelope.get("data_status", "OK"),
            "commodity": price.get("commodity"),
            "display_name": price.get("display_name"),
            "price": _num(price.get("price")),
            "prev_close": _num(price.get("prev_close")),
            "change_pct": _num(price.get("change_pct")),
            "unit": price.get("unit"),
            "as_of": price.get("as_of"),
            "source": price.get("source"),
            "venue": price.get("venue"),
            "quote_basis": price.get("quote_basis"),
        }
    elif price:
        index[PRICE_SPOT_001] = {
            "kind": "price",
            "data_status": "DATA_MISSING",
            "commodity": price.get("commodity"),
            "display_name": price.get("display_name"),
            "unit": price.get("unit"),
            "as_of": price.get("as_of"),
            "source": price.get("source"),
        }

    trend_envelope = state.get("trend") or {}
    trend = (trend_envelope.get("data") or {}) if trend_envelope else {}
    if trend and _envelope_usable(trend_envelope, require_live):
        index[PRICE_TREND_001] = {
            "kind": "trend",
            "data_status": trend_envelope.get("data_status", "OK"),
            "commodity": trend.get("commodity"),
            "display_name": trend.get("display_name"),
            "requested_days": trend.get("requested_days"),
            "change_pct": _num(trend.get("change_pct")),
            "first_close": _num(trend.get("first_close")),
            "last_close": _num(trend.get("last_close")),
            "min": _num(trend.get("min")),
            "max": _num(trend.get("max")),
            "unit": trend.get("unit"),
            "coverage": trend.get("coverage") or {},
        }

    reserves_envelope = state.get("reserves") or {}
    reserves = (reserves_envelope.get("data") or {}) if reserves_envelope else {}
    if reserves and _envelope_usable(reserves_envelope, require_live):
        rows = [
            row
            for row in (reserves.get("mineral_resources") or [])
            if row.get("statement_scope", "project_summary") == "project_summary"
        ]
        index[RESOURCE_SUMMARY_001] = {
            "kind": "resource_summary",
            "data_status": reserves_envelope.get("data_status", "OK"),
            "reporter_entity": reserves.get("reporter_entity"),
            "project_name": reserves.get("project_name"),
            "effective_date": reserves.get("effective_date") or reserves.get("report_date"),
            "announced_date": reserves.get("announced_date"),
            "fetched_at": reserves.get("fetched_at"),
            "page": reserves.get("primary_pages", {}).get("MineralResource"),
            "rows": [
                {
                    "category": row.get("category"),
                    "group": row.get("group"),
                    "tonnes": _num(row.get("tonnes")),
                    "tonnes_unit": row.get("tonnes_unit"),
                    "grade": _num(row.get("grade")),
                    "grade_unit": row.get("grade_unit"),
                    "contained": _num(row.get("contained")),
                    "contained_unit": row.get("contained_unit"),
                    "page": row.get("page"),
                }
                for row in rows
            ],
        }

        shares: dict[str, float] = {}
        for row in rows:
            unit = row.get("tonnes_unit") or "unit"
            shares[unit] = shares.get(unit, 0.0) + float(_num(row.get("tonnes")) or 0.0)
        if shares:
            unit = max(shares, key=lambda key: shares[key])
            if shares[unit] > 0:
                inferred = sum(
                    float(_num(row.get("tonnes")) or 0.0)
                    for row in rows
                    if row.get("category") == "Inferred" and (row.get("tonnes_unit") or "unit") == unit
                )
                index[RESOURCE_INFERRED_001] = {
                    "kind": "resource_share",
                    "data_status": reserves_envelope.get("data_status", "OK"),
                    "value": round(inferred / shares[unit] * 100, 2),
                    "unit": "%",
                    "numerator": inferred,
                    "denominator": shares[unit],
                    "denominator_unit": unit,
                    "page": reserves.get("primary_pages", {}).get("MineralResource"),
                    "as_of": reserves.get("effective_date") or reserves.get("report_date"),
                }

    for item in news_items:
        news_id = item.get("news_id")
        if not news_id:
            continue
        index[news_id] = {
            "kind": "news",
            "data_status": "OK",
            "news_id": news_id,
            "title": item.get("title"),
            "source": item.get("source"),
            "url": item.get("url"),
            "published_at": item.get("published_at"),
            "evidence": item.get("evidence"),
            "source_type": item.get("source_type"),
            "fetch_status": item.get("fetch_status"),
            "sentiment": item.get("sentiment"),
            "relevance": item.get("relevance"),
        }
    return index


def rule_claims(state: dict[str, Any], index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic claims for the rule-triggered risks (never model-invented)."""

    claims: list[dict[str, Any]] = []

    def add(claim_id: str, claim: str, value: float | None, unit: str, calculation: dict[str, Any], evidence_ids: list[str]) -> None:
        claims.append(
            {
                "claim_id": claim_id,
                "claim": claim,
                "value": value,
                "unit": unit,
                "calculation": calculation,
                "evidence_ids": evidence_ids,
                "source": "rule",
            }
        )

    trend = index.get(PRICE_TREND_001) or {}
    if trend.get("change_pct") is not None:
        add(
            "risk_price_trend_001",
            f"近 {trend.get('requested_days')} 天下游参考价区间涨跌 {trend.get('change_pct')}%",
            trend.get("change_pct"),
            "%",
            {
                "method": CALC_PERCENTAGE_CHANGE,
                "start_value": trend.get("first_close"),
                "end_value": trend.get("last_close"),
            },
            [PRICE_TREND_001],
        )

    spot = index.get(PRICE_SPOT_001) or {}
    if spot.get("change_pct") is not None and spot.get("data_status") == "OK":
        add(
            "risk_price_spot_001",
            f"最近交易日环比变动 {spot.get('change_pct')}%",
            spot.get("change_pct"),
            "%",
            {
                "method": CALC_PERCENTAGE_CHANGE,
                "start_value": spot.get("prev_close"),
                "end_value": spot.get("price"),
            },
            [PRICE_SPOT_001],
        )

    share = index.get(RESOURCE_INFERRED_001) or {}
    if share.get("value") is not None:
        add(
            "risk_resource_inferred_001",
            f"主表 Inferred 资源量占 {share.get('value')}%",
            share.get("value"),
            "%",
            {
                "method": CALC_SHARE,
                "numerator": share.get("numerator"),
                "denominator": share.get("denominator"),
            },
            [RESOURCE_INFERRED_001],
        )

    digest = state.get("news_digest") or {}
    items = digest.get("items") or []
    negative_ids = [
        item.get("news_id")
        for item in items
        if item.get("sentiment") == "negative" and item.get("news_id") in index
    ]
    if items and negative_ids:
        add(
            "risk_news_sentiment_001",
            f"{len(negative_ids)}/{len(items)} 条新闻为负面口径",
            round(len(negative_ids) / len(items) * 100, 2),
            "%",
            {"method": CALC_SHARE, "numerator": len(negative_ids), "denominator": len(items)},
            negative_ids[:3],
        )
    return claims


def _matches(claim_text: str, markers: tuple[str, ...]) -> bool:
    lowered = claim_text.lower()
    return any(marker.lower() in lowered for marker in markers)


def validate_claims(
    index: dict[str, dict[str, Any]],
    claims: list[dict[str, Any]],
    *,
    require_live: bool,
) -> list[dict[str, Any]]:
    """Structural validation of model/rule claims. Returns quality issues."""

    issues: list[dict[str, Any]] = []
    seen: set[str] = set()

    def issue(code: str, message: str, severity: str = SEVERITY_ERROR) -> None:
        issues.append({"code": code, "message": message, "severity": severity})

    for claim in claims:
        claim_id = str(claim.get("claim_id") or "").strip()
        text = str(claim.get("claim") or "").strip()
        evidence_ids = [str(item) for item in (claim.get("evidence_ids") or []) if item]
        value = _num(claim.get("value"))
        calculation = claim.get("calculation") or {}

        if not claim_id:
            issue("claim_without_id", f"风险结论缺少 claim_id：{text[:60] or '(空)'}")
            continue
        if claim_id in seen:
            issue("claim_duplicate_id", f"claim_id 重复：{claim_id}")
            continue
        seen.add(claim_id)

        if not evidence_ids:
            issue("claim_without_evidence", f"{claim_id} 未引用任何证据 ID")
            continue

        known = [item for item in evidence_ids if item in index]
        unknown = [item for item in evidence_ids if item not in index]
        if unknown:
            issue("claim_evidence_missing", f"{claim_id} 引用了不存在的证据 ID：{unknown}")

        kinds = {index[item].get("kind") for item in known}
        missing_evidence = [
            item for item in known if index[item].get("data_status") == "DATA_MISSING"
        ]
        if value is not None and missing_evidence:
            issue(
                "claim_uses_missing_data",
                f"{claim_id} 用 DATA_MISSING 证据 {missing_evidence} 给出了数值 {value}",
            )

        if value is not None and "price" not in kinds and "trend" not in kinds and "resource_share" not in kinds:
            if _matches(text, ("涨跌", "跌幅", "涨幅", "价格", "报价", "占比")):
                issue(
                    "claim_evidence_type_mismatch",
                    f"{claim_id} 是数值型结论（{text[:40]}），但证据类型为 {sorted(k for k in kinds if k)}",
                )

        if _matches(text, ("锂辉石", "spodumene", "销售价", "售价")) and any(
            index[item].get("kind") == "price" and index[item].get("commodity") == "lithium_carbonate"
            for item in known
        ):
            issue(
                "claim_price_scope_mismatch",
                f"{claim_id} 用碳酸锂参考价支撑锂辉石销售价结论（口径混用）",
            )

        method = str(calculation.get("method") or "")
        if method == CALC_PERCENTAGE_CHANGE:
            start, end = _num(calculation.get("start_value")), _num(calculation.get("end_value"))
            if start and end and value is not None:
                expected = (end - start) / start * 100
                if abs(expected - value) > 0.05:
                    issue(
                        "claim_calculation_mismatch",
                        f"{claim_id} 涨跌幅自相矛盾：声明 {value}%，按 start={start}/end={end} 应为 {expected:.4f}%",
                    )
        elif method == CALC_SHARE:
            numerator, denominator = _num(calculation.get("numerator")), _num(calculation.get("denominator"))
            if numerator is not None and denominator and value is not None:
                expected = numerator / denominator * 100
                if abs(expected - value) > 0.05:
                    issue(
                        "claim_calculation_mismatch",
                        f"{claim_id} 占比自相矛盾：声明 {value}%，按 {numerator}/{denominator} 应为 {expected:.4f}%",
                    )

        if _matches(text, OPERATING_MARKERS) and not _matches(text, HEDGE_MARKERS):
            weak = [
                item
                for item in known
                if index[item].get("kind") == "news"
                and index[item].get("evidence") in {"title_only", "rss_summary", "unverifiable"}
            ]
            if weak and not any(index[item].get("kind") != "news" for item in known):
                issue(
                    "claim_overreach",
                    f"{claim_id} 依据 {'/'.join(sorted({index[i].get('evidence') or '' for i in weak}))} 级新闻推断经营事实（{text[:40]}）",
                    SEVERITY_WARN if not require_live else SEVERITY_ERROR,
                )

    return issues


def repair_claims(
    index: dict[str, dict[str, Any]],
    claims: list[dict[str, Any]],
    *,
    require_live: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Deterministically fix or drop claims the model got wrong.

    Returns ``(kept_claims, repairs)``. Repairs are reported, never silent:
    a claim that cannot be traced to real evidence is removed from the report
    instead of being printed as a definitive conclusion.
    """

    kept: list[dict[str, Any]] = []
    repairs: list[str] = []
    seen: set[str] = set()
    # (value, unit, frozenset(evidence)) of what we already accepted, so a model
    # claim that merely restates a rule-derived one is dropped instead of printed
    # twice in the same table.
    accepted: set[tuple[Any, str, frozenset[str]]] = set()

    for claim in claims:
        claim_id = str(claim.get("claim_id") or "").strip()
        text_value = str(claim.get("claim") or "").strip()
        value = _num(claim.get("value"))
        calculation = dict(claim.get("calculation") or {})
        evidence_ids = [str(item) for item in (claim.get("evidence_ids") or []) if item]

        if not claim_id:
            repairs.append(f"剔除缺少 claim_id 的风险结论：{text_value[:40] or '(空)'}")
            continue
        if claim_id in seen:
            repairs.append(f"剔除重复 claim_id 的风险结论：{claim_id}")
            continue

        known = [item for item in evidence_ids if item in index]
        if not known:
            repairs.append(f"剔除无有效证据的风险结论：{claim_id}（引用 {evidence_ids or '空'}）")
            continue

        missing = [item for item in known if index[item].get("data_status") == "DATA_MISSING"]
        if value is not None and missing:
            repairs.append(f"剔除引用缺失数据的风险结论：{claim_id}（{missing} 为 DATA_MISSING）")
            continue

        if _matches(text_value, ("锂辉石", "spodumene", "销售价", "售价")) and any(
            index[item].get("kind") == "price" and index[item].get("commodity") == "lithium_carbonate"
            for item in known
        ):
            repairs.append(f"剔除价格口径混用的风险结论：{claim_id}（碳酸锂参考价 ≠ 锂辉石销售价）")
            continue

        kinds = {index[item].get("kind") for item in known}
        if value is not None and not ({"price", "trend", "resource_share"} & kinds) and _matches(
            text_value, ("涨跌", "跌幅", "涨幅", "价格", "报价", "占比")
        ):
            repairs.append(f"剔除缺少数值型证据的风险结论：{claim_id}")
            continue

        if (
            require_live
            and _matches(text_value, OPERATING_MARKERS)
            and not _matches(text_value, HEDGE_MARKERS)
            and all(index[item].get("kind") == "news" for item in known)
        ):
            weak = [
                item
                for item in known
                if index[item].get("evidence") in {"title_only", "rss_summary", "unverifiable"}
            ]
            if weak:
                levels = "/".join(sorted({str(index[item].get("evidence")) for item in weak}))
                repairs.append(f"剔除证据不足的经营类结论：{claim_id}（仅有 {levels} 级新闻）")
                continue

        method = str(calculation.get("method") or "")
        if value is not None and method == CALC_PERCENTAGE_CHANGE:
            start, end = _num(calculation.get("start_value")), _num(calculation.get("end_value"))
            if start and end:
                expected = (end - start) / start * 100
                if abs(expected - value) > 0.05:
                    repairs.append(
                        f"按证据重算风险结论数值：{claim_id} {value} → {round(expected, 4)}"
                    )
                    value = round(expected, 4)
        elif value is not None and method == CALC_SHARE:
            numerator, denominator = _num(calculation.get("numerator")), _num(calculation.get("denominator"))
            if numerator is not None and denominator:
                expected = numerator / denominator * 100
                if abs(expected - value) > 0.05:
                    repairs.append(
                        f"按证据重算风险结论数值：{claim_id} {value} → {round(expected, 4)}"
                    )
                    value = round(expected, 4)

        signature = (
            None if value is None else round(float(value), 4),
            str(claim.get("unit") or ""),
            frozenset(known),
        )
        if signature in accepted:
            repairs.append(f"剔除与已有结论重复的风险结论：{claim_id}")
            continue

        seen.add(claim_id)
        accepted.add(signature)
        updated = dict(claim)
        updated["value"] = value
        updated["verified"] = True
        kept.append(updated)

    return kept, repairs


__all__ = [
    "CALC_PERCENTAGE_CHANGE",
    "CALC_SHARE",
    "PRICE_SPOT_001",
    "PRICE_TREND_001",
    "RESOURCE_INFERRED_001",
    "RESOURCE_SUMMARY_001",
    "SEVERITY_ERROR",
    "SEVERITY_WARN",
    "build_evidence_index",
    "repair_claims",
    "rule_claims",
    "validate_claims",
]