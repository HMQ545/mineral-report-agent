"""Intent / turn taxonomy plus the slot contract for every business intent."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Intent(str, Enum):
    DAILY_REPORT = "daily_report"
    NEWS_SEARCH = "news_search"
    RESOURCE_QUERY = "resource_query"
    PRICE_QUERY = "price_query"
    TREND_QUERY = "trend_query"
    GENERAL_CHAT = "general_chat"
    UNKNOWN = "unknown"


class TurnAction(str, Enum):
    """What the current user turn means for task management."""

    NEW_TASK = "new_task"
    CONTINUE_TASK = "continue_task"
    SLOT_COMPLETION = "slot_completion"
    GENERAL_CHAT = "general_chat"
    CANCEL_TASK = "cancel_task"


BUSINESS_INTENTS = (
    Intent.DAILY_REPORT,
    Intent.NEWS_SEARCH,
    Intent.RESOURCE_QUERY,
    Intent.PRICE_QUERY,
    Intent.TREND_QUERY,
)

# Keyed by intent: slots that must be present before a task can run.
REQUIRED_SLOTS: dict[Intent, tuple[str, ...]] = {
    Intent.DAILY_REPORT: ("company", "commodity"),
    Intent.NEWS_SEARCH: ("query",),
    Intent.RESOURCE_QUERY: ("company",),
    Intent.PRICE_QUERY: ("commodity", "date"),
    Intent.TREND_QUERY: ("commodity", "days"),
    Intent.GENERAL_CHAT: (),
    Intent.UNKNOWN: (),
}

# Rule-based defaults applied only when the user did not supply the slot.
DEFAULT_SLOTS: dict[Intent, dict[str, Any]] = {
    Intent.DAILY_REPORT: {"days": 7},
    Intent.NEWS_SEARCH: {"days": 7},
    Intent.TREND_QUERY: {"days": 30},
}

SLOT_LABELS = {
    "company": "公司",
    "project": "项目",
    "commodity": "商品",
    "date": "日期",
    "days": "天数",
    "query": "检索关键词",
    "pdf_ref": "资源量报告",
    "report_date": "报告日期",
}

# Intent changes that may reuse the same task_id (goal refinement only).
COMPATIBLE_INTENT_TRANSITIONS: dict[Intent, tuple[Intent, ...]] = {
    Intent.PRICE_QUERY: (Intent.TREND_QUERY,),
    Intent.TREND_QUERY: (Intent.PRICE_QUERY,),
    Intent.NEWS_SEARCH: (Intent.NEWS_SEARCH,),
    Intent.RESOURCE_QUERY: (Intent.RESOURCE_QUERY,),
    Intent.DAILY_REPORT: (Intent.DAILY_REPORT,),
}


def is_compatible_transition(current: Intent, requested: Intent) -> bool:
    """May ``requested`` reuse the task that is currently on ``current``?"""

    if current == requested:
        return True
    return requested in COMPATIBLE_INTENT_TRANSITIONS.get(current, ())


class IntentResult(BaseModel):
    """Result of one turn-classification pass (rules and/or model)."""

    intent: Intent = Intent.UNKNOWN
    action: TurnAction = TurnAction.NEW_TASK
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    source: str = Field(default="rules", description="rules | model | rules+model | rules-fallback")
    also_cancel: bool = Field(default=False, description="用户在同一轮里表达了取消，但同时还有别的诉求")


class Slots(BaseModel):
    """Typed slot container; business validation lives in ``validator.py``."""

    company: str | None = None
    project: str | None = None
    commodity: str | None = None
    date: str | None = None
    days: int | None = None
    query: str | None = None
    pdf_ref: str | None = None
    report_date: str | None = None

    @field_validator("days")
    @classmethod
    def _days_range(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not 1 <= value <= 4000:
            raise ValueError("days must be between 1 and 4000")
        return value

    @field_validator("date", "report_date")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc
        if parsed > date.today():
            raise ValueError(f"date must not be in the future: {value}")
        return value

    def given(self) -> dict[str, Any]:
        """Only the slots the caller actually populated."""

        return {key: value for key, value in self.model_dump().items() if value is not None}


class SlotResolution(BaseModel):
    """Outcome of validation + defaulting for one intent."""

    slots: Slots = Field(default_factory=Slots)
    missing: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return not self.missing and not self.errors


__all__ = [
    "BUSINESS_INTENTS",
    "COMPATIBLE_INTENT_TRANSITIONS",
    "DEFAULT_SLOTS",
    "REQUIRED_SLOTS",
    "SLOT_LABELS",
    "Intent",
    "IntentResult",
    "SlotResolution",
    "Slots",
    "TurnAction",
    "is_compatible_transition",
]