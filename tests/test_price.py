"""Price parsing, registry resolution and history validation (offline)."""

from __future__ import annotations

import pytest

from minereport.price import registry, server, sources

FOREIGN_SNAPSHOT = (
    'var hq_str_hf_CAD="14608.760,,14612.000,14614.500,14695.000,14560.000,18:51:18,'
    '14578.000,14560.000,0,1,2,2026-10-14,伦铜,6416";'
)
DOMESTIC_SNAPSHOT = (
    'var hq_str_nf_LC0="碳酸锂连续,153333,106000.000,106700.000,100220.000,100900.000,100900.000,'
    '100940.000,100900.000,102800.000,105480.000,281,4,427300.000,339357,,碳酸锂,2026-10-14,1";'
)
FOREIGN_KLINE = (
    "/*<script>location.href='//sina.com';</script>*/\n"
    'var _CAD=([{"date":"2026-10-12","open":"14000","high":"14100","low":"13900","close":"14050"},'
    '{"date":"2026-10-13","open":"14050","high":"14100","low":"14000","close":"14080"}]);'
)
DOMESTIC_KLINE = (
    'var _LC0=([{"d":"2026-10-12","o":"101000","h":"102000","l":"100000","c":"101500"},'
    '{"d":"2026-10-13","o":"101500","h":"102500","l":"101000","c":"102000"}]);'
)


def test_resolve_aliases():
    assert registry.resolve("CU") == "copper"
    assert registry.resolve("伦铜") == "copper"
    assert registry.resolve("碳酸锂") == "lithium_carbonate"
    assert registry.resolve("lithium spodumene") == "lithium_spodumene"
    assert registry.resolve("unobtainium") is None


def test_parse_snapshot_foreign_fields():
    parsed = sources.parse_snapshot(FOREIGN_SNAPSHOT, "hf_CAD")
    assert parsed["family"] == "sina_foreign"
    assert parsed["last"] == 14608.76
    assert parsed["date"] == "2026-10-14"
    assert parsed["name"] == "伦铜"


def test_parse_snapshot_domestic_fields():
    parsed = sources.parse_snapshot(DOMESTIC_SNAPSHOT, "nf_LC0")
    assert parsed["family"] == "sina_domestic"
    assert parsed["last"] == 100900.0
    assert parsed["open"] == 106000.0
    assert parsed["date"] == "2026-10-14"


def test_parse_snapshot_missing_payload_raises():
    with pytest.raises(sources.QuoteParseError):
        sources.parse_snapshot('var hq_str_other="1,2";', "hf_CAD")


def test_parse_kline_both_dialects():
    foreign = sources.parse_kline(FOREIGN_KLINE)
    domestic = sources.parse_kline(DOMESTIC_KLINE)
    assert [row["date"] for row in foreign] == ["2026-10-12", "2026-10-13"]
    assert foreign[-1]["close"] == 14080.0
    assert [row["close"] for row in domestic] == [101500.0, 102000.0]


def test_parse_kline_sorts_by_date():
    raw = 'var _X=([{"date":"2026-10-13","close":"2"},{"date":"2026-10-12","close":"1"}]);'
    rows = sources.parse_kline(raw)
    assert [row["date"] for row in rows] == ["2026-10-12", "2026-10-13"]


def test_validate_trend_rejects_non_increasing_dates():
    with pytest.raises(ValueError):
        server.validate_trend(
            [{"date": "2026-10-13", "close": 1.0}, {"date": "2026-10-13", "close": 2.0}]
        )


def test_validate_trend_rejects_bad_close():
    with pytest.raises(ValueError):
        server.validate_trend([{"date": "2026-10-13", "close": 0.0}])


def test_validate_trend_reports_gap():
    stats = server.validate_trend(
        [{"date": "2026-10-01", "close": 1.0}, {"date": "2026-10-08", "close": 2.0}]
    )
    assert stats["max_gap_days"] == 7


def test_unknown_commodity_returns_error_envelope():
    result = server.get_price("unobtainium")
    assert result["error_code"] == "UNKNOWN_COMMODITY"
    assert result["data"] is None


def test_spodumene_is_fixture_and_marked_degraded():
    price = server.get_price("lithium_spodumene")
    assert price["provenance"] == "fixture"
    assert price["degraded"] is True
    assert any("离线样例" in note or "fixture" in note.lower() for note in price["notes"])

    trend = server.get_trend("lithium_spodumene", 30)
    assert trend["provenance"] == "fixture"
    assert trend["data"]["points"]

