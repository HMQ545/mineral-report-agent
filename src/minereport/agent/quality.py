"""Pre-render data-quality gate.

Nothing here trusts prose: counts are re-added, dates are re-parsed, scopes are
re-checked and every numeric risk claim is re-computed against the evidence
registry. Safe problems are fixed deterministically; the rest are reported, and
in strict-live mode the hard ones join ``live_gate_failures`` so a report that
looks complete but is wrong can never ship silently.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..common.provenance import DATA_STATUS_MISSING
from ..news import entities
from ..news import ids as news_ids
from . import evidence as evidence_engine

HISTORICAL_AFTER_DAYS = 540
FUTURE_SKEW = timedelta(days=1)

PRICE_NUMBER_KEYS = ("price", "prev_close", "change_pct", "min", "max", "first_close", "last_close")
RESERVES_MINERAL_CATEGORIES = {"Measured", "Indicated", "Inferred"}
RESERVES_ORE_CATEGORIES = {"Proved", "Probable"}


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _numeric_keys(entry: dict[str, Any]) -> list[str]:
    return [key for key in PRICE_NUMBER_KEYS if entry.get(key) is not None]


def run_quality_checks(state: dict[str, Any]) -> dict[str, Any]:
    """Run every check and return the report consumed by the renderer."""

    require_live = bool(state.get("require_live"))
    issues: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    news_envelope = dict(state.get("news") or {})
    news_data = dict(news_envelope.get("data") or {})

    def issue(code: str, message: str, severity: str = "error", fixed: bool = False) -> None:
        issues.append({"code": code, "message": message, "severity": severity, "fixed": fixed})

    def record(name: str, ok: bool, detail: str = "", severity: str = "warn") -> None:
        if ok:
            checks.append({"name": name, "status": "PASS", "detail": ""})
        else:
            checks.append({"name": name, "status": "FAIL" if severity == "error" else "WARN", "detail": detail})

    # 1 ---------------------------------------------------------- pipeline closure
    pipeline = dict(news_data.get("pipeline") or {})
    if pipeline:
        fetched = int(pipeline.get("fetched") or 0)
        removed = int(pipeline.get("duplicate_removed") or 0)
        unique = int(pipeline.get("unique") or 0)
        classified = (
            int(pipeline.get("company_candidates") or 0)
            + int(pipeline.get("industry_candidates") or 0)
            + int(pipeline.get("irrelevant") or 0)
        )
        ok = True
        if fetched - removed != unique:
            pipeline["unique"] = fetched - removed
            issue(
                "news_pipeline_stage_mismatch",
                f"去重阶段不闭合：fetched({fetched}) - duplicate_removed({removed}) != unique({unique})，已按 fetched-removed 修正",
                "warn",
                fixed=True,
            )
            unique = pipeline["unique"]
            ok = False
        if classified != unique:
            ok = False
            issue(
                "news_pipeline_classification_mismatch",
                f"互斥分类不闭合：公司+行业+无关={classified} != unique={unique}",
                "error",
            )
        displayed = int(pipeline.get("company_displayed") or 0) + int(pipeline.get("industry_displayed") or 0)
        expected_displayed = int(pipeline.get("company_candidates") or 0) + int(pipeline.get("industry_candidates") or 0) - int(
            pipeline.get("not_displayed_by_limit") or 0
        )
        if displayed != expected_displayed:
            ok = False
            issue(
                "news_pipeline_display_mismatch",
                f"展示计数不闭合：公司+行业展示={displayed} != 候选-未展示={expected_displayed}",
                "error",
            )
        record(
            "新闻统计闭合",
            ok,
            "公司+行业+无关 之和必须等于去重后条数",
            severity="error",
        )
        news_data["pipeline"] = pipeline

    # 2/3 ------------------------------------------- publication dates and as_of
    displayed_items: list[dict[str, Any]] = list(news_data.get("company_items") or []) + list(
        news_data.get("industry_items") or []
    )
    now = datetime.now(timezone.utc)
    bad_dates: list[str] = []
    stamps: list[datetime] = []
    for item in displayed_items:
        parsed = _parse(item.get("published_at"))
        if item.get("published_at") and (parsed is None or parsed > now + FUTURE_SKEW):
            bad_dates.append(str(item.get("news_id") or item.get("title") or "?"))
            item["published_at"] = None
            item["published_at_note"] = "发布时间无法解析或位于未来，已忽略"
            continue
        if parsed is not None:
            stamps.append(parsed.astimezone(timezone.utc))
    record("新闻发布时间有效", not bad_dates, f"无法解析/未来时间：{bad_dates}")
    for entry in bad_dates:
        diagnostics.append(f"新闻发布时间无效，已从 as_of 计算中剔除：{entry}")

    expected_as_of = max(stamps).isoformat(timespec="seconds") if stamps else None
    if news_envelope.get("as_of") != expected_as_of:
        issue(
            "news_as_of_recomputed",
            f"news.as_of 已按本次展示条目重算：{news_envelope.get('as_of')} -> {expected_as_of}",
            "warn",
            fixed=True,
        )
        news_envelope["as_of"] = expected_as_of
    company_stamps = [s for item, s in zip(
        news_data.get("company_items") or [], [_parse(i.get("published_at")) for i in (news_data.get("company_items") or [])]
    ) if s]
    record(
        "news.as_of 语义一致",
        news_envelope.get("as_of") == expected_as_of,
        "as_of 必须等于展示条目中最新的 published_at",
    )

    # 4 ------------------------------------------------------ evidence completeness
    unknown_ids: list[str] = []
    fixed_evidence = 0
    for item in displayed_items:
        if not item.get("news_id"):
            repaired, kind = news_ids.news_identity(item)
            if repaired:
                item["news_id"] = repaired
                item["news_id_kind"] = kind
                fixed_evidence += 1
            else:
                unknown_ids.append(str(item.get("title") or "?"))
                continue
        if (
            item.get("source_type") not in {entities.OFFICIAL, entities.MEDIA, entities.RSS, entities.UNKNOWN}
            or item.get("evidence") not in entities.EVIDENCE_LEVELS
            or item.get("fetch_status") not in entities.FETCH_STATUSES
        ):
            item.update(entities.assess_evidence(item, entity=None))
            fixed_evidence += 1
    if fixed_evidence:
        issue("news_evidence_completed", f"{fixed_evidence} 条新闻补齐了证据字段", "warn", fixed=True)
    record("新闻证据字段完整", not unknown_ids, f"缺少 news_id：{unknown_ids}", severity="error")

    # 5 ------------------------------------------- strict-live: no synthetic numbers
    leaked: list[str] = []
    if require_live:
        for name, envelope in (("price", state.get("price") or {}), ("trend", state.get("trend") or {})):
            if envelope and envelope.get("provenance") != "live" and _numeric_keys(envelope.get("data") or {}):
                leaked.append(f"{name}({envelope.get('provenance')})")
        for entry in state.get("optional_market") or []:
            if entry.get("provenance") != "live" and (_numeric_keys(entry) or entry.get("as_of")):
                leaked.append(f"optional_market.{entry.get('commodity')}({entry.get('provenance')})")
    record("严格 Live 无模拟数值", not leaked, f"非 live 数据仍带数值：{leaked}", severity="error")
    if leaked:
        issue("strict_live_synthetic_numbers", f"严格 Live 模式混入非 live 数值：{leaked}", "error")

    # 6 ------------------------------------------------ DATA_MISSING used as a number
    misused: list[str] = []
    for name, entry in (("price", state.get("price") or {}), ("trend", state.get("trend") or {})):
        if entry.get("data_status") == DATA_STATUS_MISSING and _numeric_keys(entry.get("data") or {}):
            misused.append(name)
    for entry in state.get("optional_market") or []:
        if entry.get("data_status") == DATA_STATUS_MISSING and _numeric_keys(entry):
            misused.append(f"optional_market.{entry.get('commodity')}")
    record("DATA_MISSING 未被当作数值", not misused, f"缺失数据仍带数值：{misused}", severity="error")
    if misused:
        issue("data_missing_used_as_value", f"DATA_MISSING 数据仍带数值字段：{misused}", "error")

    # 7 ----------------------------------------------------------- PDF dates/age
    reserves_envelope = state.get("reserves") or {}
    reserves = reserves_envelope.get("data") or {}
    if reserves:
        missing_dates = [key for key in ("effective_date", "announced_date", "fetched_at") if not reserves.get(key)]
        age_days = reserves.get("age_days")
        if missing_dates:
            issue("pdf_dates_incomplete", f"资源量文档缺少日期字段：{missing_dates}", "warn")
            unverified.append({"dataset": "reserves", "reason": f"缺少日期字段 {missing_dates}"})
        if isinstance(age_days, (int, float)) and age_days > HISTORICAL_AFTER_DAYS and not reserves.get("is_historical"):
            reserves["is_historical"] = True
            issue(
                "pdf_age_flagged",
                f"资源量文档已 {int(age_days)} 天（>{HISTORICAL_AFTER_DAYS}），标记为历史数据",
                "warn",
                fixed=True,
            )
        record("PDF 三日期明确", not missing_dates, f"缺少：{missing_dates}")

    # 8 ------------------------------------------- resources vs reserves separation
    scope_errors: list[str] = []
    for row in reserves.get("mineral_resources") or []:
        if row.get("category") not in RESERVES_MINERAL_CATEGORIES or row.get("group") not in {None, "MineralResource"}:
            scope_errors.append(f"资源量行分类异常：{row.get('category')}/{row.get('group')}")
    for row in reserves.get("ore_reserves") or []:
        if row.get("category") not in RESERVES_ORE_CATEGORIES or row.get("group") not in {None, "OreReserve"}:
            scope_errors.append(f"储量行分类异常：{row.get('category')}/{row.get('group')}")
    record("资源量与储量未混用", not scope_errors, "; ".join(scope_errors[:3]), severity="error")
    if scope_errors:
        issue("resources_reserves_mixed", "; ".join(scope_errors[:3]), "error")

    # 9 ---------------------------------------------------- appendix not re-summed
    primary = [
        row
        for row in (reserves.get("mineral_resources") or [])
        if row.get("statement_scope", "project_summary") == "project_summary"
    ]
    duplicate_categories = [row.get("category") for row in primary]
    duplicated = sorted({value for value in duplicate_categories if duplicate_categories.count(value) > 1})
    record("主表未重复汇总", not duplicated, f"主表分类重复：{duplicated}", severity="error")
    if duplicated:
        issue("resource_summary_duplicated", f"主表出现重复分类（疑似汇总与附录混排）：{duplicated}", "error")

    # 10 ---------------------------------------------------------- price scope wording
    scope_issue = ""
    price_data = ((state.get("price") or {}).get("data") or {})
    if state.get("commodity") == "lithium_carbonate" and price_data and not price_data.get("downstream_note"):
        scope_issue = "碳酸锂参考价缺少 downstream_note 口径说明"
    for entry in state.get("optional_market") or []:
        if entry.get("data_status") == DATA_STATUS_MISSING and not entry.get("reason"):
            scope_issue = f"{entry.get('commodity')} 缺失但未给出原因"
    record("行情口径标注完整", not scope_issue, scope_issue)

    # 11 ------------------------------------------------------------- claims traceable
    index = state.get("evidence_index") or evidence_engine.build_evidence_index(state, news_items=displayed_items)
    claims = list(state.get("claims") or [])
    claim_issues = evidence_engine.validate_claims(index, claims, require_live=require_live)
    for item in claim_issues:
        issue(item["code"], item["message"], item.get("severity", "error"))
    record(
        "风险结论可追溯到证据",
        not claim_issues,
        "; ".join(f"{item['code']}: {item['message']}" for item in claim_issues[:3]),
        severity="error" if any(item.get("severity") == "error" for item in claim_issues) else "warn",
    )

    errors = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] != "error"]
    news_envelope["data"] = news_data
    return {
        "checks": checks,
        "issues": issues,
        "errors": errors,
        "warnings": warnings,
        "unverified": unverified,
        "diagnostics": diagnostics,
        "news": news_envelope,
        "claims_checked": len(claims),
        "as_of": news_envelope.get("as_of"),
        "company_as_of": max(company_stamps).isoformat(timespec="seconds") if company_stamps else None,
    }


__all__ = ["HISTORICAL_AFTER_DAYS", "run_quality_checks"]