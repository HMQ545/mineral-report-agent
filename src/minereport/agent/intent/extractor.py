"""Rule-based slot extraction from Chinese natural language."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from ...news import entities
from ...pdf import registry as pdf_registry
from ...price import registry as price_registry

ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CN_DATE_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
DAYS_RES = (
    (re.compile(r"最近\s*(\d{1,4})\s*天"), 1),
    (re.compile(r"近\s*(\d{1,4})\s*天"), 1),
    (re.compile(r"(\d{1,4})\s*天(?:的|内|以来)?"), 1),
    (re.compile(r"(\d{1,4})\s*个月"), 30),
)
CN_DURATIONS = {
    "一个月": 30,
    "最近一个月": 30,
    "半个月": 15,
    "一周": 7,
    "最近一周": 7,
    "三个月": 90,
    "半年": 180,
    "一年": 365,
}
RELATIVE_DATES = {"今天": 0, "昨天": -1, "前天": -2}
# Bare "锂" is ambiguous: carbonate and spodumene are different products.
AMBIGUOUS_COMMODITY_TOKENS = ("锂价", "锂的价格", "锂价格")


def _commodity(text: str) -> tuple[str | None, bool]:
    """Return ``(canonical, ambiguous)``."""

    lowered = text.lower()
    canonical = price_registry.resolve(lowered)
    if canonical:
        return canonical, False

    # Longest registered alias wins, so "碳酸锂" beats the bare "锂".
    best: tuple[int, str] | None = None
    for alias, target in price_registry.ALIASES.items():
        if alias and alias in lowered:
            if best is None or len(alias) > best[0]:
                best = (len(alias), target)
    if best:
        return best[1], False

    for token in AMBIGUOUS_COMMODITY_TOKENS:
        if token in text:
            return None, True
    if "锂" in text:
        return None, True
    return None, False


def _company_and_project(text: str) -> tuple[str | None, str | None]:
    lowered = text.lower()
    company: str | None = None
    project: str | None = None
    for entity in entities._load():  # registry accessor
        if any(alias.lower() in lowered for alias in entity.aliases) or any(
            project.lower() in lowered for project in entity.projects
        ):
            company = entity.name
            for candidate in entity.projects:
                if candidate.lower() in lowered:
                    project = candidate
                    break
            break
    if company is None:
        for source in pdf_registry.all_sources():
            keywords = [str(k).lower() for k in source.get("keywords", [])]
            if any(keyword and keyword in lowered for keyword in keywords):
                company = source.get("reporter_entity") or None
                project = source.get("project_name") or None
                break
    return company, project


def _days(text: str) -> int | None:
    for pattern, factor in DAYS_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1)) * factor
    for token, value in CN_DURATIONS.items():
        if token in text:
            return value
    return None


def _date(text: str) -> str | None:
    match = ISO_DATE_RE.search(text)
    if match:
        return match.group(1)
    match = CN_DATE_RE.search(text)
    if match:
        today = date.today()
        return date(today.year, int(match.group(1)), int(match.group(2))).isoformat()
    for token, offset in RELATIVE_DATES.items():
        if token in text:
            return (date.today() + timedelta(days=offset)).isoformat()
    return None


def _query(text: str) -> str | None:
    """Free-text news query: strip the leading request phrasing."""

    cleaned = re.sub(r"^(帮我|请|麻烦)?\s*(查一下|查查|搜索|搜一下|看看|找一下|查询)\s*", "", text).strip()
    cleaned = re.sub(r"(的)?(新闻|消息|资讯)\s*$", "", cleaned).strip()
    return cleaned or None


def extract_commodities(text: str) -> list[str]:
    """Every registered commodity mentioned, longest alias first (deduped)."""

    lowered = text.lower()
    hits: list[tuple[int, str]] = []
    for alias, target in price_registry.ALIASES.items():
        if alias and alias in lowered and target not in [h[1] for h in hits]:
            hits.append((len(alias), target))
    hits.sort(reverse=True)
    return [target for _, target in hits]


def extract_slots(text: str) -> tuple[dict[str, Any], list[str]]:
    """Extract whatever the user stated explicitly; never guess identifiers."""

    notes: list[str] = []
    slots: dict[str, Any] = {}

    commodity, ambiguous = _commodity(text)
    if commodity:
        slots["commodity"] = commodity
    elif ambiguous:
        notes.append("商品表述存在歧义（碳酸锂 vs 锂辉石精矿），需要追问具体品种。")

    company, project = _company_and_project(text)
    if company:
        slots["company"] = company
    if project:
        slots["project"] = project

    days = _days(text)
    if days:
        slots["days"] = days
    parsed_date = _date(text)
    if parsed_date:
        slots["date"] = parsed_date

    query = _query(text)
    if query:
        slots["query"] = query
    return slots, notes


LITHIUM_FAMILY = ("lithium_carbonate", "lithium_spodumene")


def commodities_in_text(text: str) -> set[str]:
    """Registered products named in the text, with the bare "锂" expanded.

    A task created while the product was still ambiguous says only "锂价"; that
    still constrains the task to the lithium family.
    """

    found = set(extract_commodities(text))
    if not found and "锂" in text:
        found.update(LITHIUM_FAMILY)
    return found


__all__ = [
    "AMBIGUOUS_COMMODITY_TOKENS",
    "LITHIUM_FAMILY",
    "commodities_in_text",
    "extract_commodities",
    "extract_slots",
]