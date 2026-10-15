"""Shared pytest fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolated_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point both SQLite databases at a per-test temporary directory.

    The business store and the LangGraph checkpointer were split into two files;
    a test that only overrode one of them used to fall back to the repository's
    real ``data/sessions/`` database and pollute later tests. This fixture makes
    that impossible: individual tests may still override the variables.
    """

    monkeypatch.setenv("MINEREPORT_STATE_DB", str(tmp_path / "agent_state.sqlite"))
    monkeypatch.setenv("MINEREPORT_BUSINESS_DB", str(tmp_path / "agent_business.sqlite"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def project_root() -> Path:
    return ROOT


@pytest.fixture
def brief_state():
    """Factory for a realistic strict-live brief state (unit-level)."""

    def make(**overrides):
        price_data = {
            "commodity": "lithium_carbonate",
            "display_name": "碳酸锂（广期所主力连续）",
            "price": 98820.0,
            "prev_close": 100900.0,
            "change_pct": -2.0614,
            "unit": "CNY/t",
            "as_of": "2026-10-15",
            "quote_basis": "intraday snapshot",
            "downstream_note": "下游市场参考价，不是 Pilbara 锂辉石精矿的实际销售结算价",
            "venue": "GFEX",
            "venue_official": True,
            "source": "sina:hq.sinajs.cn",
        }
        trend_data = {
            "commodity": "lithium_carbonate",
            "requested_days": 60,
            "change_pct": -34.2671,
            "first_close": 153500.0,
            "last_close": 100900.0,
            "min": 100900.0,
            "max": 161000.0,
            "unit": "CNY/t",
            "coverage": {"start": "2026-08-17", "end": "2026-10-14", "trading_days": 37},
        }
        reserves_data = {
            "reporter_entity": "Pilbara Minerals Limited",
            "project_name": "Pilgangoora Operation",
            "report_date": "2023-06-30",
            "effective_date": "2023-06-30",
            "announced_date": "2023-08-24",
            "fetched_at": "2026-10-15",
            "age_days": 938,
            "is_historical": True,
            "primary_pages": {"MineralResource": 2},
            "mineral_resources": [
                {"category": "Measured", "group": "MineralResource", "tonnes": 22.1,
                 "tonnes_unit": "Mt", "page": 2, "statement_scope": "project_summary"},
                {"category": "Indicated", "group": "MineralResource", "tonnes": 315.2,
                 "tonnes_unit": "Mt", "page": 2, "statement_scope": "project_summary"},
                {"category": "Inferred", "group": "MineralResource", "tonnes": 76.6,
                 "tonnes_unit": "Mt", "page": 2, "statement_scope": "project_summary"},
            ],
            "ore_reserves": [
                {"category": "Proved", "group": "OreReserve", "tonnes": 19.1, "tonnes_unit": "Mt", "page": 3},
            ],
        }
        news_items = [
            {
                "news_id": "nid-company-1",
                "title": "Pilbara Minerals lifts spodumene output",
                "url": "https://www.mining.com/company-1",
                "source": "mining.com",
                "published_at": "2026-10-13T23:15:02+00:00",
                "relevance": "company",
                "source_type": "media",
                "evidence": "full_article",
                "evidence_reason": "抓取成功",
                "fetch_status": "success",
                "sentiment": "negative",
            },
            {
                "news_id": "nid-industry-1",
                "title": "Lithium price outlook",
                "url": "https://www.mining.com/industry-1",
                "source": "mining.com",
                "published_at": "2026-10-15T06:30:00+00:00",
                "relevance": "industry",
                "source_type": "media",
                "evidence": "title_only",
                "evidence_reason": "仅有标题",
                "fetch_status": "not_attempted",
                "sentiment": "negative",
            },
        ]

        def envelope(payload, provenance="live", **extra):
            base = {
                "data": payload,
                "provenance": provenance,
                "source": "test",
                "source_url": "https://example.invalid/source",
                "as_of": "2026-10-15",
                "retrieved_at": "2026-10-15T00:00:00+00:00",
                "degraded": provenance != "live",
                "data_status": "OK",
                "notes": [],
            }
            base.update(extra)
            return base

        state = {
            "query": "给我生成一份关于 Pilbara 锂矿的今日简报",
            "topic": "Pilgangoora Operation（Pilbara Minerals Limited）",
            "commodity": "lithium_carbonate",
            "days": 7,
            "generated_at": "2026-10-15T16:00:00+08:00",
            "llm_mode": "deepseek",
            "require_live": True,
            "news": envelope(
                {
                    "company_items": [news_items[0]],
                    "industry_items": [news_items[1]],
                    "items": news_items,
                    "pipeline": {
                        "fetched": 63,
                        "duplicate_removed": 15,
                        "unique": 48,
                        "company_candidates": 7,
                        "industry_candidates": 12,
                        "irrelevant": 29,
                        "company_displayed": 7,
                        "industry_displayed": 12,
                        "not_displayed_by_limit": 0,
                        "undated": 0,
                        "body_attempted": 4,
                        "body_succeeded": 1,
                        "body_failed": 3,
                        "body_budget_exhausted": 0,
                        "body_elapsed_s": 4.2,
                    },
                },
                source_url="https://news.google.com/rss/search",
                as_of="2026-10-15T06:30:00+00:00",
            ),
            "company_news": [news_items[0]],
            "industry_news": [news_items[1]],
            "news_digest": {"items": news_items, "overall": "整体偏弱。", "model": "deepseek"},
            "articles": [],
            "reserves": envelope(reserves_data),
            "price": envelope(price_data),
            "trend": envelope(trend_data),
            "optional_market": [],
            "risks": [],
            "llm_risks": {"bullets": [], "model": "deepseek"},
            "judgement": "综合判断。",
            "claims": [],
            "claim_issues": [],
            "evidence_index": {},
            "quality": {},
            "news_pipeline": {},
            "unverified": [],
            "errors": [],
            "intent_notes": [],
            "provenance_map": {},
            "live_gate_failures": [],
        }
        state.update(overrides)
        return state

    return make
