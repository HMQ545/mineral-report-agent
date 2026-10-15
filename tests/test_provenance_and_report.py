"""Provenance contract and the no-masquerade guarantee in the report."""

from __future__ import annotations

from minereport.agent.report import provenance_badge, render_report
from minereport.common.provenance import Provenance, envelope, error_envelope


def test_envelope_marks_everything_non_live_as_degraded():
    for provenance in (Provenance.CACHED, Provenance.FIXTURE, Provenance.SYNTHETIC):
        payload = envelope({"x": 1}, provenance=provenance, source="test")
        assert payload["degraded"] is True
        assert payload["provenance"] == provenance.value

    live = envelope({"x": 1}, provenance=Provenance.LIVE, source="test")
    assert live["degraded"] is False


def test_error_envelope_has_no_data():
    payload = error_envelope("boom", source="test", code="X")
    assert payload["data"] is None
    assert payload["error"] == "boom"
    assert payload["error_code"] == "X"


def test_badges_only_appear_for_non_live():
    assert provenance_badge({"provenance": "live"}) == ""
    assert "非实时数据" in provenance_badge({"provenance": "fixture"})
    assert "非实时数据" in provenance_badge({"provenance": "synthetic"})


def _state_with(*, provenance: str) -> dict:
    dataset = envelope(
        {"items": [], "query": "q", "days": 7},
        provenance=Provenance(provenance),
        source="unit-test-source",
        source_url="https://example.invalid/x",
    )
    return {
        "query": "q",
        "topic": "Unit Test Topic",
        "commodity": "lithium_carbonate",
        "days": 7,
        "generated_at": "2026-10-14T00:00:00+08:00",
        "llm_mode": "stub",
        "require_live": False,
        "news": dataset,
        "reserves": dict(dataset, data=None),
        "price": dataset,
        "trend": dataset,
        "news_digest": {"items": [], "overall": ""},
        "risks": [],
        "errors": [],
        "unverified": [],
        "articles": [],
        "optional_market": [],
    }


def test_fixture_state_is_never_presented_as_live():
    markdown = render_report(_state_with(provenance="fixture"))
    assert "| news | `fixture` |" in markdown
    assert "非实时数据" in markdown
    provenance_section = markdown.split("## 0.")[1].split("## 1.")[0]
    assert "| news | `live` |" not in provenance_section


def test_live_state_sections_carry_no_degraded_badge():
    markdown = render_report(_state_with(provenance="live"))
    assert "非实时数据" not in markdown
    assert "| news | `live` |" in markdown


def test_missing_reserves_is_stated_explicitly():
    state = _state_with(provenance="live")
    state["reserves"] = None
    markdown = render_report(state)
    assert "未获取到可验证的矿产资源量数据" in markdown

