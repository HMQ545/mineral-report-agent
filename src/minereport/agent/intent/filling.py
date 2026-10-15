"""Slot filling: user input beats task context beats rule defaults."""

from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Any

from .schemas import DEFAULT_SLOTS, Intent

CN_TZ = timezone(timedelta(hours=8))


def default_report_date() -> str:
    return date.today().isoformat()


def fill_slots(
    intent: Intent,
    extracted: dict[str, Any] | None,
    inherited: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge slots and record where each value came from.

    Precedence: this turn's explicit input > context of the task being continued
    > rule defaults. Values explicitly set to ``None`` never erase an inherited
    value (that only happens through an explicit cancel/new-task path).
    """

    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}

    for key, value in (inherited or {}).items():
        if value is not None:
            merged[key] = value
            sources[key] = "task-context"

    for key, value in (extracted or {}).items():
        if value is not None:
            merged[key] = value
            sources[key] = "user-input"

    for key, value in DEFAULT_SLOTS.get(intent, {}).items():
        if merged.get(key) is None:
            merged[key] = value
            sources[key] = "default"

    if intent in {Intent.DAILY_REPORT, Intent.NEWS_SEARCH} and not merged.get("report_date"):
        # Not a silent business value: it is the retrieval day, recorded as such.
        merged["report_date"] = default_report_date()
        sources["report_date"] = "default(retrieval-day)"

    return merged, sources


__all__ = ["default_report_date", "fill_slots"]