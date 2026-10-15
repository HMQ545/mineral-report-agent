"""Report rendering: strict-live masking, news grouping and Markdown hygiene."""

from __future__ import annotations

from minereport.agent.report import MISSING_COMPANY_NEWS, render_report
from minereport.common.provenance import Provenance, envelope


def _envelope(data, provenance: Provenance, source="unit-test", url=None):
    return envelope(data, provenance=provenance, source=source, source_url=url)


def _state(*, require_live: bool, news_provenance: Provenance = Provenance.LIVE):
    company = [
        {
            "title": "Pilbara Minerals lifts Pilgangoora output",
            "url": "https://example.com/company-1",
            "source": "Example",
            "published_at": "2026-10-14T00:00:00+00:00",
            "snippet": "Company item",
            "relevance": "company",
        }
    ]
    industry = [
        {
            "title": "Lithium prices fall across Asia",
            "url": "https://example.com/industry-1",
            "source": "Example",
            "published_at": "2026-10-14T00:00:00+00:00",
            "snippet": "Industry item",
            "relevance": "industry",
        }
    ]
    news_data = {
        "items": company + industry,
        "company_items": company,
        "industry_items": industry,
        "entity": {"key": "pilbara-minerals", "name": "Pilbara Minerals Limited"},
        "stats": {"raw": 10, "company": 1, "industry": 1, "excluded_irrelevant": 8},
    }
    reserves_data = {
        "reporter_entity": "Pilbara Minerals Limited",
        "project_name": "Pilgangoora Operation",
        "report_date": "2023-06-30",
        "standards": ["JORC"],
        "document": {"resolved_url": "https://example.com/report.pdf", "sha256": "a" * 64, "page_count": 56},
        "primary_pages": {"MineralResource": 2, "OreReserve": 3},
        "mineral_resources": [
            {"category": "Indicated", "group": "MineralResource", "tonnes": 315.2, "tonnes_unit": "Mt",
             "grade": 1.15, "grade_unit": "%", "contained": 3.6, "contained_unit": "Mt", "page": 2,
             "quote": "Indicated 315.2 1.15"}
        ],
        "ore_reserves": [],
        "appendix": {"mineral_resources": [], "ore_reserves": [], "note": "附录不可相加"},
        "disclosure_note": "该资源量为 2023 年披露口径，并非当前最新披露。",
    }
    price_data = {
        "display_name": "碳酸锂（广期所主力连续）",
        "venue": "GFEX",
        "venue_official": True,
        "contract_spec": "GFEX lithium carbonate",
        "price": 100900.0,
        "unit": "CNY/t",
        "prev_close": 107060.0,
        "change_pct": -5.75,
        "as_of": "2026-10-14",
        "quote_basis": "daily close (settled)",
        "source": "sina",
    }
    return {
        "query": "给我生成一份关于 Pilbara 锂矿的今日简报",
        "topic": "Pilgangoora Operation（Pilbara Minerals Limited）",
        "commodity": "lithium_carbonate",
        "days": 7,
        "generated_at": "2026-10-15T00:00:00+08:00",
        "llm_mode": "deepseek",
        "require_live": require_live,
        "news": _envelope(news_data, news_provenance, url="https://news.google.com/rss/search"),
        "reserves": _envelope(reserves_data, Provenance.LIVE, url="https://example.com/report.pdf"),
        "price": _envelope(price_data, Provenance.LIVE, url="https://hq.sinajs.cn/list=nf_LC0"),
        "trend": _envelope({"points": [], "coverage": {}}, Provenance.LIVE, url="https://hq.sinajs.cn/list=nf_LC0"),
        "news_digest": {
            "items": [{"title": "Pilbara Minerals lifts Pilgangoora output", "url": "https://example.com/company-1",
                       "summary": "产量提升。", "sentiment": "positive"}],
            "overall": "整体中性。",
            "model": "deepseek",
        },
        "risks": [],
        "llm_risks": {"bullets": ["价格下行风险"]},
        "judgement": "综合判断文本。",
        "errors": ["news.fetch_article failed once"],
        "intent_notes": [],
        "unverified": [],
        "articles": [],
        "optional_market": [
            {
                "commodity": "lithium_spodumene",
                "display_name": "锂辉石精矿 SC6.0 CIF（离线样例）",
                "price": 1180.0,
                "unit": "USD/t",
                "as_of": "2026-10-10",
                "provenance": "fixture",
                "note": "无公开真实数据源",
            }
        ],
    }


def test_strict_live_report_never_shows_fixture_prices():
    markdown = render_report(_state(require_live=True))
    assert "DATA_MISSING" in markdown
    assert "1,180" not in markdown and "1180" not in markdown
    assert "严格 Live 模式" in markdown
    assert "未使用离线模拟价格" in markdown
    # The §0 legend deliberately defines what a fixture is; the price table and
    # the risk sections must never suggest one was used.
    optional_section = markdown.split("### 4.1")[1].split("## 5.")[0]
    risk_section = markdown.split("## 5.")[1].split("## 6.")[0]
    assert "离线样例" not in optional_section
    assert "离线样例" not in risk_section


def test_demo_mode_may_show_fixture_but_labels_it():
    markdown = render_report(_state(require_live=False))
    assert "1,180" in markdown
    assert "`fixture`" in markdown


def test_strict_live_masks_non_live_news_section():
    markdown = render_report(_state(require_live=True, news_provenance=Provenance.FIXTURE))
    section = markdown.split("## 1. 新闻摘要")[1].split("## 2.")[0]
    assert "DATA_MISSING" in section


def test_company_and_industry_news_are_separate_sections():
    markdown = render_report(_state(require_live=True))
    assert "### 1.1 公司直接相关新闻" in markdown
    assert "### 1.2 锂行业背景新闻" in markdown
    company_section = markdown.split("### 1.1")[1].split("### 1.2")[0]
    assert "Pilbara Minerals lifts Pilgangoora output" in company_section
    assert "Lithium prices fall across Asia" not in company_section


def test_missing_company_news_shows_the_explicit_message():
    state = _state(require_live=True)
    state["news"]["data"]["company_items"] = []
    state["news"]["data"]["items"] = state["news"]["data"]["industry_items"]
    state["news_digest"]["items"] = []
    markdown = render_report(state)
    assert MISSING_COMPANY_NEWS in markdown


def test_links_are_markdown_and_not_duplicated():
    markdown = render_report(_state(require_live=True))
    links_section = markdown.split("## 6. 引用源链接")[1].split("## 7.")[0]
    assert "](https://" in links_section
    urls = [line.split("](")[1].rstrip(")") for line in links_section.splitlines() if "](" in line]
    assert len(urls) == len(set(urls)), "the same source must not be linked twice"


def test_news_items_do_not_glue_urls_onto_the_next_title():
    markdown = render_report(_state(require_live=True))
    section = markdown.split("## 1. 新闻摘要")[1].split("## 2.")[0]
    for line in section.splitlines():
        if line.startswith("**"):
            assert line.rstrip().endswith("**") or "）" in line or "·" in line
            assert "http" not in line, "a URL must never sit on a title line"


def test_diagnostics_are_kept_out_of_the_news_section():
    markdown = render_report(_state(require_live=True))
    news_section = markdown.split("## 1. 新闻摘要")[1].split("## 2.")[0]
    assert "news.fetch_article failed once" not in news_section
    diagnostics = markdown.split("## 8. 运行诊断")[1]
    assert "news.fetch_article failed once" in diagnostics