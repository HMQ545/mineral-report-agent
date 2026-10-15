"""lme-price-mcp: ``get_price`` and ``get_trend``."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..common.cli import add_transport_args, run_server
from ..common.health import register_health
from ..common.provenance import Provenance, envelope, error_envelope
from . import registry, sources

SERVICE = "lme-price-mcp"
DEFAULT_PORT = 8103
SPARK_CHARS = "▁▂▃▄▅▆▇█"

INSTRUCTIONS = (
    "Returns commodity quotes and trading-day history. LME figures come from a "
    "third-party Sina feed (not the official LME API) and are labelled as such. "
    "Historical dates are looked up for real; missing dates return an error instead "
    "of the latest price."
)


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    low, high = min(values), max(values)
    if high == low:
        return SPARK_CHARS[0] * len(values)
    span = high - low
    return "".join(SPARK_CHARS[int((value - low) / span * (len(SPARK_CHARS) - 1))] for value in values)


def _parse_iso(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def validate_trend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Strictly increasing dates, no duplicates, positive closes."""

    seen: set[str] = set()
    previous: date | None = None
    max_gap = 0
    for row in rows:
        current = _parse_iso(row["date"])
        if row["date"] in seen:
            raise ValueError(f"duplicate trading date {row['date']}")
        if previous is not None:
            if current <= previous:
                raise ValueError(
                    f"trading dates are not strictly increasing: {previous} -> {current}"
                )
            max_gap = max(max_gap, (current - previous).days)
        if row["close"] is None or row["close"] <= 0:
            raise ValueError(f"invalid close on {row['date']}: {row['close']}")
        seen.add(row["date"])
        previous = current
    return {"rows": len(rows), "max_gap_days": max_gap}


def _base_quote(cfg: dict[str, Any], *, price: float, as_of: str, prev_close: float | None) -> dict[str, Any]:
    change_pct = None
    if prev_close:
        change_pct = round((price - prev_close) / prev_close * 100, 4)
    return {
        "commodity": cfg["canonical"],
        "canonical": cfg["canonical"],
        "display_name": cfg["display_name"],
        "venue": cfg["venue"],
        "venue_official": cfg.get("venue_official", False),
        "venue_note": cfg.get("venue_note", ""),
        "instrument_code": cfg.get("code"),
        "quote_type": cfg["quote_type"],
        "contract_spec": cfg["contract_spec"],
        "currency": cfg["currency"],
        "unit": cfg["unit"],
        "price": price,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "as_of": as_of,
        "source": cfg["source"],
        "source_url": cfg["source_url"],
        "downstream_note": cfg.get("downstream_note"),
    }


def _fixture_price(commodity: str, cfg: dict[str, Any]) -> dict[str, Any]:
    payload, _ = sources.fixture_quote(commodity)
    quote = payload["quote"]
    return envelope(
        _base_quote(cfg, price=quote["price"], as_of=payload["as_of"], prev_close=quote.get("prev_close")),
        provenance=Provenance.FIXTURE,
        source=cfg["source"],
        source_url=cfg["source_url"],
        as_of=payload["as_of"],
        notes=[payload["disclaimer"], cfg.get("downstream_note", "")],
        quote_basis="offline fixture",
    )


def get_price(commodity: str, date: str | None = None) -> dict[str, Any]:
    """Latest quote, or the close of an exact historical trading day."""

    cfg = registry.config_for(commodity)
    if not cfg:
        return error_envelope(
            f"unknown commodity {commodity!r}; supported: {sorted(registry.all_commodities())}",
            source="price registry",
            code="UNKNOWN_COMMODITY",
        )
    if cfg["family"] == "fixture":
        return _fixture_price(commodity, cfg)

    notes: list[str] = []
    if cfg.get("downstream_note"):
        notes.append(cfg["downstream_note"])
    if not cfg.get("venue_official", True):
        notes.append(f"{cfg.get('venue_note', '')}；source={cfg['source']}")

    snapshot: dict[str, Any] | None = None
    snapshot_error: str | None = None
    try:
        snapshot = sources.fetch_snapshot(cfg["code"])
    except Exception as exc:  # noqa: BLE001
        snapshot_error = f"{type(exc).__name__}: {exc}"

    try:
        kline, kline_cached = sources.fetch_kline(cfg)
    except Exception as exc:  # noqa: BLE001
        if snapshot and snapshot.get("last"):
            kline, kline_cached = [], False
            notes.append(f"日K 获取失败（{type(exc).__name__}），仅返回快照价。")
        else:
            cached = None
            return error_envelope(
                f"no live quote available: snapshot={snapshot_error}; history={type(exc).__name__}: {exc}",
                source=cfg["source"],
                source_url=cfg["source_url"],
                code="SOURCE_UNAVAILABLE",
            )

    if kline_cached:
        notes.append("日K 数据来自本地缓存（原始为真实抓取）。")
    if snapshot_error:
        notes.append(f"实时快照获取失败：{snapshot_error}")

    if date:
        wanted = date.strip()
        try:
            _parse_iso(wanted)
        except ValueError:
            return error_envelope(
                f"date must be YYYY-MM-DD, got {date!r}",
                source=cfg["source"],
                source_url=cfg["source_url"],
                code="BAD_DATE",
            )
        index = next((i for i, row in enumerate(kline) if row["date"] == wanted), None)
        if index is None:
            available = f"{kline[0]['date']}..{kline[-1]['date']}" if kline else "no history"
            return error_envelope(
                f"no quote for {wanted} (not a trading day or outside coverage {available}); "
                "the latest price is deliberately NOT substituted",
                source=cfg["source"],
                source_url=cfg["source_url"],
                code="NOT_FOUND",
                notes=notes,
            )
        row = kline[index]
        prev_close = kline[index - 1]["close"] if index > 0 else None
        payload = _base_quote(cfg, price=row["close"], as_of=row["date"], prev_close=prev_close)
        payload["quote_basis"] = "daily close (historical lookup)"
        payload["ohlc"] = {key: row.get(key) for key in ("open", "high", "low", "close", "settle")}
        return envelope(
            payload,
            provenance=Provenance.CACHED if kline_cached else Provenance.LIVE,
            source=cfg["source"],
            source_url=cfg["source_url"],
            as_of=row["date"],
            notes=notes,
        )

    as_of = (snapshot or {}).get("date") or (kline[-1]["date"] if kline else sources.today_cn())
    same_day = next((row for row in kline if row["date"] == as_of), None)
    cross_check = None

    if same_day is not None:
        price = same_day["close"]
        basis = "daily close (settled)"
        provenance = Provenance.CACHED if kline_cached else Provenance.LIVE
        if snapshot and snapshot.get("last"):
            cross_check = round((snapshot["last"] - price) / price * 100, 4)
    elif snapshot and snapshot.get("last"):
        price = snapshot["last"]
        basis = "intraday snapshot"
        provenance = Provenance.LIVE
    else:
        price = kline[-1]["close"]
        as_of = kline[-1]["date"]
        basis = "latest available daily close"
        provenance = Provenance.CACHED if kline_cached else Provenance.LIVE

    prev_close = None
    for row in reversed(kline):
        if row["date"] < as_of:
            prev_close = row["close"]
            break
    if prev_close is None and snapshot:
        prev_close = snapshot.get("prev_close_raw")

    payload = _base_quote(cfg, price=price, as_of=as_of, prev_close=prev_close)
    payload["quote_basis"] = basis
    payload["cross_check"] = {
        "snapshot_last": (snapshot or {}).get("last"),
        "daily_close_same_day": same_day["close"] if same_day else None,
        "snapshot_vs_daily_close_pct": cross_check,
        "raw_snapshot_fields": (snapshot or {}).get("fields"),
    }
    return envelope(
        payload,
        provenance=provenance,
        source=cfg["source"],
        source_url=cfg["source_url"],
        as_of=as_of,
        notes=notes,
    )


def get_trend(commodity: str, days: int = 30) -> dict[str, Any]:
    """Trading-day closes for the trailing ``days`` calendar days."""

    cfg = registry.config_for(commodity)
    if not cfg:
        return error_envelope(
            f"unknown commodity {commodity!r}; supported: {sorted(registry.all_commodities())}",
            source="price registry",
            code="UNKNOWN_COMMODITY",
        )
    days = max(1, min(int(days), 4000))

    if cfg["family"] == "fixture":
        payload, _ = sources.fixture_quote(commodity)
        trend = payload["trend"][-days:]
        closes = [point["close"] for point in trend]
        return envelope(
            {
                "commodity": cfg["canonical"],
                "display_name": cfg["display_name"],
                "venue": cfg["venue"],
                "unit": cfg["unit"],
                "contract_spec": cfg["contract_spec"],
                "source": cfg["source"],
                "source_url": cfg["source_url"],
                "points": trend,
                "first_close": closes[0] if closes else None,
                "last_close": closes[-1] if closes else None,
                "min": min(closes) if closes else None,
                "max": max(closes) if closes else None,
                "change_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 4) if len(closes) > 1 else None,
                "sparkline": _sparkline(closes),
                "coverage": {
                    "start": trend[0]["date"] if trend else None,
                    "end": trend[-1]["date"] if trend else None,
                    "trading_days": len(trend),
                },
            },
            provenance=Provenance.FIXTURE,
            source=cfg["source"],
            source_url=cfg["source_url"],
            as_of=payload["as_of"],
            notes=[payload["disclaimer"]],
        )

    try:
        kline, kline_cached = sources.fetch_kline(cfg)
    except Exception as exc:  # noqa: BLE001
        return error_envelope(
            f"history unavailable: {type(exc).__name__}: {exc}",
            source=cfg["source"],
            source_url=cfg["source_url"],
            code="SOURCE_UNAVAILABLE",
        )

    if not kline:
        return error_envelope(
            "history endpoint returned no rows",
            source=cfg["source"],
            source_url=cfg["source_url"],
            code="EMPTY_HISTORY",
        )

    stats = validate_trend(kline)
    last_date = _parse_iso(kline[-1]["date"])
    window_start = last_date - timedelta(days=days)
    window = [row for row in kline if _parse_iso(row["date"]) >= window_start]
    if not window:
        window = kline[-1:]

    closes = [row["close"] for row in window]
    weekdays = sum(
        1
        for offset in range((last_date - window_start).days + 1)
        if (window_start + timedelta(days=offset)).weekday() < 5
    )
    notes = [
        "按实际交易日返回；周末与交易所假期没有报价属于正常情况。",
        "coverage_vs_weekdays 以工作日为分母估算，未内置交易所假期日历，因此是下界。",
    ]
    if kline_cached:
        notes.append("日K 数据来自本地缓存（原始为真实抓取）。")
    if cfg.get("downstream_note"):
        notes.append(cfg["downstream_note"])

    payload = {
        "commodity": cfg["canonical"],
        "display_name": cfg["display_name"],
        "venue": cfg["venue"],
        "venue_official": cfg.get("venue_official", False),
        "venue_note": cfg.get("venue_note", ""),
        "instrument_code": cfg.get("code"),
        "contract_spec": cfg["contract_spec"],
        "currency": cfg["currency"],
        "unit": cfg["unit"],
        "source": cfg["source"],
        "source_url": cfg["source_url"],
        "requested_days": days,
        "points": [{"date": row["date"], "close": row["close"]} for row in window],
        "first_close": closes[0],
        "last_close": closes[-1],
        "min": min(closes),
        "max": max(closes),
        "change_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 4) if len(closes) > 1 else None,
        "sparkline": _sparkline(closes),
        "coverage": {
            "start": window[0]["date"],
            "end": window[-1]["date"],
            "trading_days": len(window),
            "expected_weekdays": weekdays,
            "coverage_vs_weekdays": round(len(window) / weekdays, 4) if weekdays else None,
            "max_gap_days": stats["max_gap_days"],
            "full_history_start": kline[0]["date"],
            "full_history_end": kline[-1]["date"],
        },
    }
    return envelope(
        payload,
        provenance=Provenance.CACHED if kline_cached else Provenance.LIVE,
        source=cfg["source"],
        source_url=cfg["source_url"],
        as_of=window[-1]["date"],
        notes=notes,
    )


def list_commodities() -> dict[str, Any]:
    """List the commodities this server can quote, with venue and source."""

    items = []
    for key, cfg in sorted(registry.all_commodities().items()):
        items.append(
            {
                "commodity": key,
                "display_name": cfg["display_name"],
                "venue": cfg["venue"],
                "venue_official": cfg.get("venue_official", False),
                "venue_note": cfg.get("venue_note", ""),
                "instrument_code": cfg.get("code"),
                "contract_spec": cfg["contract_spec"],
                "unit": cfg["unit"],
                "source": cfg["source"],
                "source_url": cfg["source_url"],
                "family": cfg["family"],
            }
        )
    return envelope(
        {"commodities": items},
        provenance=Provenance.LIVE,
        source="price registry",
        notes=["registry file: data/sources/price_sources.json"],
    )


def create_server(
    host: str = "127.0.0.1", port: int = DEFAULT_PORT, log_level: str = "INFO"
) -> FastMCP:
    mcp = FastMCP(SERVICE, instructions=INSTRUCTIONS, host=host, port=port, log_level=log_level)
    mcp.tool()(get_price)
    mcp.tool()(get_trend)
    mcp.tool()(list_commodities)
    register_health(mcp, service=SERVICE, version=__version__)
    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="minereport-price", description=INSTRUCTIONS)
    add_transport_args(parser, DEFAULT_PORT)
    args = parser.parse_args(argv)
    run_server(create_server(args.host, args.port, args.log_level), args)


__all__ = [
    "SERVICE",
    "create_server",
    "get_price",
    "get_trend",
    "list_commodities",
    "main",
    "validate_trend",
]

