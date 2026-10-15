"""Structured risk claims: every number must be traceable and re-computable."""

from __future__ import annotations

from minereport.agent import evidence as engine


def _index(state):
    news = list(state.get("company_news") or []) + list(state.get("industry_news") or [])
    return engine.build_evidence_index(state, news_items=news)


def _claims(state):
    index = _index(state)
    return index, engine.rule_claims(state, index)


def test_evidence_index_exposes_prices_trend_resource_and_news(brief_state):
    index = _index(brief_state())
    assert engine.PRICE_SPOT_001 in index and engine.PRICE_TREND_001 in index
    assert engine.RESOURCE_SUMMARY_001 in index and engine.RESOURCE_INFERRED_001 in index
    assert index["nid-company-1"]["kind"] == "news"
    share = index[engine.RESOURCE_INFERRED_001]
    assert share["numerator"] == 76.6 and share["denominator"] > 0
    assert share["value"] == round(76.6 / (22.1 + 315.2 + 76.6) * 100, 2)
    assert share["as_of"] == "2023-06-30"


def test_rule_claims_are_valid_and_recomputable(brief_state):
    index, claims = _claims(brief_state())
    assert {claim["claim_id"] for claim in claims} >= {
        "risk_price_trend_001",
        "risk_price_spot_001",
        "risk_resource_inferred_001",
    }
    assert engine.validate_claims(index, claims, require_live=True) == []


def test_unknown_evidence_id_is_reported(brief_state):
    index, _ = _claims(brief_state())
    claims = [
        {
            "claim_id": "risk_x",
            "claim": "价格下跌",
            "value": -1.0,
            "unit": "%",
            "evidence_ids": ["price_trend_999"],
        }
    ]
    codes = [item["code"] for item in engine.validate_claims(index, claims, require_live=True)]
    assert "claim_evidence_missing" in codes


def test_evidence_type_mismatch_is_reported(brief_state):
    index, _ = _claims(brief_state())
    claims = [
        {
            "claim_id": "risk_type",
            "claim": "价格跌幅显著",
            "value": -1.0,
            "unit": "%",
            "evidence_ids": ["nid-company-1"],
        }
    ]
    codes = [item["code"] for item in engine.validate_claims(index, claims, require_live=True)]
    assert "claim_evidence_type_mismatch" in codes


def test_correct_calculation_passes_and_wrong_one_fails(brief_state):
    index, _ = _claims(brief_state())
    good = {
        "claim_id": "risk_good",
        "claim": "近 60 天下游参考价下跌",
        "value": -34.2671,
        "unit": "%",
        "calculation": {"method": "percentage_change", "start_value": 153500.0, "end_value": 100900.0},
        "evidence_ids": [engine.PRICE_TREND_001],
    }
    assert engine.validate_claims(index, [good], require_live=True) == []

    bad = dict(good, claim_id="risk_bad", value=-20.0)
    codes = [item["code"] for item in engine.validate_claims(index, [bad], require_live=True)]
    assert "claim_calculation_mismatch" in codes


def test_claim_without_evidence_is_reported(brief_state):
    index, _ = _claims(brief_state())
    claims = [{"claim_id": "risk_no_ev", "claim": "价格下跌", "value": -1.0, "unit": "%"}]
    codes = [item["code"] for item in engine.validate_claims(index, claims, require_live=True)]
    assert "claim_without_evidence" in codes


def test_duplicate_claim_id_is_reported(brief_state):
    index, claims = _claims(brief_state())
    duplicated = claims + [dict(claims[0])]
    codes = [item["code"] for item in engine.validate_claims(index, duplicated, require_live=True)]
    assert "claim_duplicate_id" in codes


def test_data_missing_evidence_cannot_back_a_number(brief_state):
    state = brief_state(require_live=True)
    # a non-live price in strict-live mode: the index must carry no numbers at all
    state["price"] = dict(state["price"], provenance="fixture", data={"commodity": "lithium_carbonate", "price": 1180.0})
    index = _index(state)
    assert index[engine.PRICE_SPOT_001]["data_status"] == "DATA_MISSING"
    assert "price" not in index[engine.PRICE_SPOT_001]

    claims = [
        {
            "claim_id": "risk_missing",
            "claim": "价格上涨",
            "value": 1180.0,
            "unit": "USD/t",
            "evidence_ids": [engine.PRICE_SPOT_001],
        }
    ]
    codes = [item["code"] for item in engine.validate_claims(index, claims, require_live=True)]
    assert "claim_uses_missing_data" in codes


def test_rule_claims_skip_missing_spot_price(brief_state):
    state = brief_state(require_live=True)
    state["price"] = dict(state["price"], provenance="fixture", data={"commodity": "lithium_carbonate", "price": 1180.0})
    _index_map, claims = _claims(state)
    assert "risk_price_spot_001" not in {claim["claim_id"] for claim in claims}


def test_carbonate_price_cannot_back_a_spodumene_sale_price_claim(brief_state):
    index, _ = _claims(brief_state())
    claims = [
        {
            "claim_id": "risk_scope",
            "claim": "Pilbara 锂辉石精矿销售价下跌 5%",
            "value": -5.0,
            "unit": "%",
            "calculation": {"method": "percentage_change", "start_value": 100900.0, "end_value": 95800.0},
            "evidence_ids": [engine.PRICE_SPOT_001],
        }
    ]
    codes = [item["code"] for item in engine.validate_claims(index, claims, require_live=True)]
    assert "claim_price_scope_mismatch" in codes


def test_operating_conclusion_on_title_only_news_is_flagged(brief_state):
    index, _ = _claims(brief_state())
    claims = [
        {
            "claim_id": "risk_overreach",
            "claim": "公司产量下降",
            "evidence_ids": ["nid-industry-1"],
        }
    ]
    issues = engine.validate_claims(index, claims, require_live=True)
    assert [item["code"] for item in issues] == ["claim_overreach"]
    assert issues[0]["severity"] == engine.SEVERITY_ERROR

    demo = engine.validate_claims(index, claims, require_live=False)
    assert demo[0]["severity"] == engine.SEVERITY_WARN


def _repair(state, claims):
    index = _index(state)
    return index, *engine.repair_claims(index, claims, require_live=bool(state.get("require_live")))


def test_repair_drops_claims_without_usable_evidence(brief_state):
    state = brief_state()
    index, kept, repairs = _repair(
        state,
        [
            {"claim_id": "a", "claim": "价格下跌", "value": -1.0, "unit": "%", "evidence_ids": []},
            {"claim_id": "b", "claim": "价格下跌", "value": -1.0, "unit": "%", "evidence_ids": ["nope"]},
        ],
    )
    assert kept == []
    assert len(repairs) == 2


def test_repair_recomputes_a_wrong_number(brief_state):
    state = brief_state()
    index, kept, repairs = _repair(
        state,
        [
            {
                "claim_id": "risk_wrong",
                "claim": "价格大跌",
                "value": -12.0,
                "unit": "%",
                "calculation": {"method": "percentage_change", "start_value": 153500.0, "end_value": 100900.0},
                "evidence_ids": [engine.PRICE_TREND_001],
            }
        ],
    )
    assert kept[0]["value"] == round((100900.0 - 153500.0) / 153500.0 * 100, 4)
    assert any("重算" in note for note in repairs)
    assert engine.validate_claims(index, kept, require_live=True) == []


def test_repair_drops_overreach_in_strict_mode_but_keeps_it_in_demo(brief_state):
    claim = {"claim_id": "risk_op", "claim": "公司产量下降", "evidence_ids": ["nid-industry-1"]}

    strict_state = brief_state(require_live=True)
    _index_map, kept, repairs = _repair(strict_state, [claim])
    assert kept == [] and any("证据不足" in note for note in repairs)

    demo_state = brief_state(require_live=False)
    _index_map2, kept_demo, repairs_demo = _repair(demo_state, [claim])
    assert [item["claim_id"] for item in kept_demo] == ["risk_op"]
    assert repairs_demo == []


def test_hedged_sentences_are_not_treated_as_overreach(brief_state):
    state = brief_state()
    index, kept, repairs = _repair(
        state,
        [
            {
                "claim_id": "risk_hedge",
                "claim": "公司未披露 Pilgangoora 产量与成本数据",
                "evidence_ids": ["nid-industry-1"],
            }
        ],
    )
    assert [item["claim_id"] for item in kept] == ["risk_hedge"], repairs


def test_repair_drops_missing_data_and_scope_confusion(brief_state):
    state = brief_state()
    state["price"] = dict(state["price"], provenance="fixture", data={"commodity": "lithium_carbonate", "price": 1180.0})
    index, kept, repairs = _repair(
        state,
        [
            {
                "claim_id": "risk_missing",
                "claim": "锂辉石价格上涨",
                "value": 1180.0,
                "unit": "USD/t",
                "evidence_ids": [engine.PRICE_SPOT_001],
            }
        ],
    )
    assert kept == []
    assert repairs
    assert engine.validate_claims(index, kept, require_live=True) == []


def test_repair_drops_a_restated_rule_claim(brief_state):
    state = brief_state()
    index, rule_claims = _claims(state)
    duplicate = dict(rule_claims[0], claim_id="risk_price_trend_002")
    _index_map, kept, repairs = _repair(state, rule_claims + [duplicate])
    assert "risk_price_trend_002" not in {item["claim_id"] for item in kept}
    assert any("重复" in note for note in repairs)
