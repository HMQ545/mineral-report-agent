"""Pre-render quality gate: every check, fix and escalation."""

from __future__ import annotations

from minereport.agent.quality import run_quality_checks

CHECK_NAMES = {
    "新闻统计闭合",
    "新闻发布时间有效",
    "news.as_of 语义一致",
    "新闻证据字段完整",
    "严格 Live 无模拟数值",
    "DATA_MISSING 未被当作数值",
    "PDF 三日期明确",
    "资源量与储量未混用",
    "主表未重复汇总",
    "行情口径标注完整",
    "风险结论可追溯到证据",
}


def _codes(report):
    return [item["code"] for item in report["issues"]]


def test_every_check_is_reported_by_name(brief_state):
    report = run_quality_checks(brief_state())
    assert {check["name"] for check in report["checks"]} == CHECK_NAMES
    assert report["errors"] == []


def test_pipeline_stage_break_is_repaired(brief_state):
    state = brief_state()
    state["news"]["data"]["pipeline"]["unique"] = 999
    report = run_quality_checks(state)
    assert "news_pipeline_stage_mismatch" in _codes(report)
    assert report["news"]["data"]["pipeline"]["unique"] == 48
    assert any(item["fixed"] for item in report["issues"])


def test_pipeline_classification_break_is_a_hard_error(brief_state):
    state = brief_state()
    state["news"]["data"]["pipeline"]["company_candidates"] = 99
    report = run_quality_checks(state)
    assert "news_pipeline_classification_mismatch" in _codes(report)
    assert report["errors"]


def test_invalid_publication_dates_are_dropped_and_as_of_recomputed(brief_state):
    state = brief_state()
    state["news"]["data"]["company_items"][0]["published_at"] = "2099-01-01T00:00:00+00:00"
    state["news"]["as_of"] = "2099-01-01T00:00:00+00:00"
    report = run_quality_checks(state)
    assert report["news"]["data"]["company_items"][0]["published_at"] is None
    assert report["news"]["as_of"] == "2026-10-15T06:30:00+00:00"
    assert report["diagnostics"]


def test_missing_news_id_is_repaired(brief_state):
    state = brief_state()
    del state["news"]["data"]["company_items"][0]["news_id"]
    report = run_quality_checks(state)
    repaired = report["news"]["data"]["company_items"][0]["news_id"]
    assert repaired.startswith("nid-")
    assert [c for c in report["checks"] if c["name"] == "新闻证据字段完整"][0]["status"] == "PASS"


def test_displayed_items_without_any_id_are_reported(brief_state):
    state = brief_state()
    state["news"]["data"]["company_items"][0].update(news_id="", title="", url="")
    report = run_quality_checks(state)
    assert [c for c in report["checks"] if c["name"] == "新闻证据字段完整"][0]["status"] == "FAIL"


def test_strict_live_synthetic_numbers_are_a_hard_error(brief_state):
    state = brief_state()
    state["price"] = dict(state["price"], provenance="fixture")
    report = run_quality_checks(state)
    assert "strict_live_synthetic_numbers" in _codes(report)
    assert [c for c in report["checks"] if c["name"] == "严格 Live 无模拟数值"][0]["status"] == "FAIL"


def test_demo_mode_may_carry_fixture_numbers(brief_state):
    state = brief_state(require_live=False)
    state["price"] = dict(state["price"], provenance="fixture")
    report = run_quality_checks(state)
    assert "strict_live_synthetic_numbers" not in _codes(report)


def test_data_missing_with_numbers_is_a_hard_error(brief_state):
    state = brief_state()
    state["optional_market"] = [
        {
            "commodity": "lithium_spodumene",
            "display_name": "锂辉石精矿 SC6.0 CIF",
            "data_status": "DATA_MISSING",
            "price": 1180.0,
            "unit": "USD/t",
        }
    ]
    report = run_quality_checks(state)
    assert "data_missing_used_as_value" in _codes(report)


def test_pdf_dates_missing_are_recorded_as_unverified(brief_state):
    state = brief_state()
    state["reserves"]["data"].pop("announced_date")
    report = run_quality_checks(state)
    assert "pdf_dates_incomplete" in _codes(report)
    assert any(item["dataset"] == "reserves" for item in report["unverified"])


def test_historical_pdf_age_is_flagged(brief_state):
    state = brief_state()
    state["reserves"]["data"]["is_historical"] = False
    report = run_quality_checks(state)
    assert "pdf_age_flagged" in _codes(report)
    assert state["reserves"]["data"]["is_historical"] is True
    assert [c for c in report["checks"] if c["name"] == "PDF 三日期明确"][0]["status"] == "PASS"


def test_resources_and_reserves_must_not_be_mixed(brief_state):
    state = brief_state()
    state["reserves"]["data"]["ore_reserves"][0]["category"] = "Inferred"
    report = run_quality_checks(state)
    assert "resources_reserves_mixed" in _codes(report)


def test_duplicate_primary_resource_rows_are_rejected(brief_state):
    state = brief_state()
    rows = state["reserves"]["data"]["mineral_resources"]
    rows.append(dict(rows[1]))
    report = run_quality_checks(state)
    assert "resource_summary_duplicated" in _codes(report)


def test_claim_issues_flow_into_the_report(brief_state):
    state = brief_state()
    state["claims"] = [
        {
            "claim_id": "risk_bad",
            "claim": "价格下跌",
            "value": -1.0,
            "unit": "%",
            "evidence_ids": ["price_trend_404"],
        }
    ]
    report = run_quality_checks(state)
    assert "claim_evidence_missing" in _codes(report)
    assert [c for c in report["checks"] if c["name"] == "风险结论可追溯到证据"][0]["status"] == "FAIL"