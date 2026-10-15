"""Pydantic models for tool payloads and report state."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Group = Literal["MineralResource", "OreReserve"]
Category = Literal["Measured", "Indicated", "Inferred", "Proved", "Probable"]

RESOURCE_CATEGORIES = {"Measured", "Indicated", "Inferred"}
RESERVE_CATEGORIES = {"Proved", "Probable"}

GROUP_FOR_CATEGORY: dict[str, Group] = {
    "Measured": "MineralResource",
    "Indicated": "MineralResource",
    "Inferred": "MineralResource",
    "Proved": "OreReserve",
    "Probable": "OreReserve",
}


class ResourceCategory(BaseModel):
    """One row of a mineral resource / ore reserve statement."""

    category: str
    group: Group
    tonnes: float | None = None
    tonnes_unit: str | None = None
    grade: float | None = None
    grade_unit: str | None = None
    contained: float | None = None
    contained_unit: str | None = None
    page: int | None = None
    quote: str = ""
    confidence: Literal["high", "medium", "low"] = "low"

    def model_post_init(self, __context: Any) -> None:
        expected = GROUP_FOR_CATEGORY.get(self.category)
        if expected and self.group != expected:
            raise ValueError(
                f"category {self.category!r} belongs to group {expected!r}, not {self.group!r}"
            )


class NewsItem(BaseModel):
    title: str
    url: str
    source: str = ""
    published_at: str | None = None
    snippet: str = ""


class Article(BaseModel):
    url: str
    title: str = ""
    site: str = ""
    published_at: str | None = None
    text: str = ""
    word_count: int = 0


class PriceQuote(BaseModel):
    commodity: str
    canonical: str
    display_name: str
    venue: str
    venue_official: bool = True
    venue_note: str = ""
    instrument_code: str
    quote_type: str
    contract_spec: str
    currency: str
    unit: str
    price: float
    prev_close: float | None = None
    change_pct: float | None = None
    as_of: str | None = None
    source: str
    source_url: str


class TrendPoint(BaseModel):
    date: str
    close: float


class TrendCoverage(BaseModel):
    start: str | None = None
    end: str | None = None
    trading_days: int = 0
    expected_days: int = 0
    coverage_ratio: float = 0.0
    max_gap_days: int = 0

