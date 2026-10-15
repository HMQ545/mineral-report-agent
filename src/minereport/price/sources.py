"""Parsers for the Sina quote endpoints (third-party venue data, not official)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..common import cache
from ..common.fixtures import load_fixture_json
from ..common.http import get_text
from . import registry

CN_TZ = timezone(timedelta(hours=8))

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "Accept": "*/*",
}

SNAPSHOT_URL = "https://hq.sinajs.cn/list={code}"
KLINE_FOREIGN_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}=/"
    "GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}"
)
KLINE_DOMESTIC_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{symbol}=/"
    "InnerFuturesNewService.getDailyKLine?symbol={symbol}"
)

KLINE_CACHE_TTL = 60 * 30


class QuoteParseError(ValueError):
    """Raised when a source payload cannot be understood (never guessed)."""


class HistoryNotFound(KeyError):
    """Raised when a requested historical trading day has no quote."""


def today_cn() -> str:
    return datetime.now(CN_TZ).date().isoformat()


def parse_snapshot(text: str, code: str) -> dict[str, Any]:
    """Parse one ``hq_str_<code>`` payload.

    Only field meanings that are stable across the two families are used;
    everything else is returned raw for audit instead of being guessed.
    """

    match = re.search(rf'hq_str_{re.escape(code)}="([^"]*)"', text)
    if not match:
        raise QuoteParseError(f"no payload for {code!r} in response")
    fields = match.group(1).split(",")
    if len(fields) < 9:
        raise QuoteParseError(f"unexpectedly short payload for {code!r}: {len(fields)} fields")

    if code.startswith("hf_"):
        last, date, name = fields[0], fields[12] if len(fields) > 12 else "", fields[13] if len(fields) > 13 else ""
        open_, high, low = fields[8], fields[4], fields[5]
        prev_close = fields[7]
        family = "sina_foreign"
    elif code.startswith("nf_"):
        last, date, name = fields[8], fields[17] if len(fields) > 17 else "", fields[0]
        open_, high, low = fields[2], fields[3], fields[4]
        prev_close = fields[5]
        family = "sina_domestic"
    else:
        raise QuoteParseError(f"unsupported Sina code prefix: {code!r}")

    def number(value: str) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "family": family,
        "code": code,
        "name": name,
        "last": number(last),
        "open": number(open_),
        "high": number(high),
        "low": number(low),
        "prev_close_raw": number(prev_close),
        "date": date,
        "fields": fields,
    }


def _extract_json_array(raw: str) -> list[dict[str, Any]]:
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise QuoteParseError("no JSON array found in k-line response")
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise QuoteParseError(f"k-line JSON is malformed: {exc}") from exc


def parse_kline(raw: str) -> list[dict[str, Any]]:
    """Normalise either Sina k-line dialect into ``[{date, open, high, low, close}]``."""

    rows = _extract_json_array(raw)
    normalised: list[dict[str, Any]] = []
    for row in rows:
        date = row.get("date") or row.get("d") or row.get("day")
        close = row.get("close") or row.get("c")
        if not date or close is None:
            continue

        def number(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        parsed = {
            "date": str(date),
            "open": number(row.get("open") or row.get("o")),
            "high": number(row.get("high") or row.get("h")),
            "low": number(row.get("low") or row.get("l")),
            "close": number(close),
            "settle": number(row.get("s")),
        }
        if parsed["close"] is None:
            continue
        normalised.append(parsed)

    normalised.sort(key=lambda item: item["date"])
    return normalised


def fetch_snapshot(code: str, *, timeout: float = 20.0) -> dict[str, Any]:
    text = get_text(
        SNAPSHOT_URL.format(code=code),
        headers=SINA_HEADERS,
        encoding="gbk",
        timeout=timeout,
    )
    return parse_snapshot(text, code)


def fetch_kline(cfg: dict[str, Any], *, timeout: float = 30.0) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(rows, from_cache)``; the cache is only used after a live failure."""

    symbol = cfg["kline_symbol"]
    template = KLINE_FOREIGN_URL if cfg["family"] == "sina_foreign" else KLINE_DOMESTIC_URL
    url = template.format(symbol=symbol)
    cache_key = f"kline-{cfg['canonical']}-{symbol}"

    try:
        raw = get_text(url, headers=SINA_HEADERS, timeout=timeout)
        rows = parse_kline(raw)
        if not rows:
            raise QuoteParseError("k-line response contained no usable rows")
        cache.write_json("prices", cache_key, rows)
        return rows, False
    except Exception:
        cached = cache.read_json("prices", cache_key, max_age_seconds=60 * 60 * 48)
        if cached and cached.get("payload"):
            return cached["payload"], True
        raise


def fixture_quote(commodity: str) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = registry.resolve(commodity)
    cfg = registry.config_for(commodity)
    if not cfg or cfg["family"] != "fixture":
        raise QuoteParseError(f"{commodity!r} is not a fixture-backed commodity")
    payload = load_fixture_json(cfg["fixture"])
    return payload, cfg


__all__ = [
    "CN_TZ",
    "HistoryNotFound",
    "QuoteParseError",
    "SINA_HEADERS",
    "fetch_kline",
    "fetch_snapshot",
    "fixture_quote",
    "parse_kline",
    "parse_snapshot",
    "today_cn",
]

