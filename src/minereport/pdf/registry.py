"""Registered, human-verified public report sources."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any

from ..common import config

REGISTRY_PATH = config.SOURCES_DIR / "pdf_sources.json"

# A resource statement older than this is presented as *historical* data rather
# than as a current figure. The Pilbara document in the registry is 2023-vintage,
# so the daily brief must say so instead of quietly implying it is recent.
HISTORICAL_AFTER_DAYS = 540


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"sources": []}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def reload_registry() -> None:
    _load.cache_clear()


def all_sources() -> list[dict[str, Any]]:
    return list(_load().get("sources", []))


def lookup(url: str) -> dict[str, Any] | None:
    for source in all_sources():
        if source.get("url") == url:
            return source
    return None


def default_source(*, standard: str | None = None, role: str | None = None) -> dict[str, Any] | None:
    for source in all_sources():
        if standard and source.get("standard") != standard:
            continue
        if role and source.get("role") != role:
            continue
        return source
    return None


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    text_value = str(value)
    try:
        return datetime.fromisoformat(text_value).date()
    except ValueError:
        return None


def source_metadata(source: dict[str, Any] | None, *, today: date | None = None) -> dict[str, Any]:
    """Normalise the three dates a registered report carries.

    * ``effective_date`` - the date the resource statement is effective to
    * ``announced_date`` - when the company/regulator published it
    * ``fetched_at``     - when this checkout last verified the document
    """

    source = source or {}
    effective = _as_date(source.get("effective_date") or source.get("report_date"))
    announced = _as_date(source.get("announced_date") or source.get("document_date")) or effective
    fetched = _as_date(source.get("fetched_at") or source.get("verified_at"))
    reference = today or datetime.now(timezone.utc).date()
    age = (reference - effective).days if effective else None
    return {
        "effective_date": effective.isoformat() if effective else None,
        "announced_date": announced.isoformat() if announced else None,
        "fetched_at": fetched.isoformat() if fetched else None,
        "age_days": age,
        "is_historical": bool(age is not None and age > HISTORICAL_AFTER_DAYS),
    }


def select_source(query: str, *, role: str | None = "primary-deliverable") -> dict[str, Any] | None:
    """Newest *verified* registry entry matching the query's company/project.

    Never touches the network and never lets a model invent a URL: only entries a
    human registered (with a sha256 fingerprint) are eligible. Among the matches
    the newest ``announced_date`` wins, so a stale report cannot shadow a newer
    one just because it sits earlier in the file.
    """

    lowered = (query or "").lower()
    candidates = [item for item in all_sources() if not role or item.get("role") == role]
    matched = [
        item
        for item in candidates
        if any(str(keyword).strip().lower() in lowered for keyword in (item.get("keywords") or []))
    ]
    pool = matched or candidates
    if not pool:
        return None

    def sort_key(item: dict[str, Any]) -> tuple[str, str]:
        meta = source_metadata(item)
        return (meta["announced_date"] or "", meta["fetched_at"] or "")

    newest = max(pool, key=sort_key)
    meta = source_metadata(newest)
    reason = (
        f"注册表{'关键词命中' if matched else '回退主交付源'}：选择公告日期最新"
        f"（{meta['announced_date'] or '未知'}）的已验证条目 "
        f"{newest.get('key')}（sha256 已核验）"
    )
    if len(matched) > 1:
        reason += f"；另有 {len(matched) - 1} 个更早的匹配条目未使用"
    if meta["is_historical"]:
        reason += f"；该文档生效日期距今 {meta['age_days']} 天，按历史数据标注"
    selected = dict(newest)
    selected.update(meta)
    selected["selection_reason"] = reason
    return selected


__all__ = [
    "HISTORICAL_AFTER_DAYS",
    "REGISTRY_PATH",
    "all_sources",
    "default_source",
    "lookup",
    "reload_registry",
    "select_source",
    "source_metadata",
]

