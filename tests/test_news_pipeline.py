"""News pipeline closure, stable news_id and as_of semantics."""

from __future__ import annotations

import pytest

from minereport.news import entities, ids, server as news_server


def _item(url, title, *, snippet="", source="Test Media", provider="direct-feed:test",
          published="2026-10-13T00:00:00+00:00"):
    return {
        "url": url,
        "title": title,
        "snippet": snippet,
        "source": source,
        "provider": provider,
        "published_at": published,
    }


def _search(monkeypatch, items, query="Pilbara 锂矿", days=7):
    monkeypatch.setattr(
        news_server.sources,
        "search_queries",
        lambda queries, days=None, timeout=30.0: (list(items), [f"stub -> {len(items)} item(s)"]),
    )
    return news_server.search(query, days)


def test_pipeline_is_closed_and_explains_every_item(monkeypatch):
    items = [
        _item("https://www.mining.com/a", "Pilbara Minerals lifts spodumene output"),
        _item("https://www.mining.com/a", "Pilbara Minerals lifts spodumene output"),          # exact dup
        _item("https://www.mining.com/b", "PLS Group quarterly activities report released"),
        _item("https://www.mining.com/c", "Lithium price outlook worsens", snippet="lithium price falls"),
        _item("https://www.mining.com/d", "Spodumene market tightens", snippet="spodumene demand rises"),
        _item("https://news.google.com/rss/articles/CBMiXxY?oc=5", "Lithium supply grows",
              snippet="lithium supply", provider="google-news-rss"),
        _item("https://www.mining.com/f", "Perth weather forecast"),
        _item("https://www.mining.com/g", "Copper mine expansion approved"),
    ]
    payload = _search(monkeypatch, items)["data"]
    pipeline = payload["pipeline"]

    assert pipeline["fetched"] == 8
    assert pipeline["duplicate_removed"] == 1
    assert pipeline["unique"] == 7
    assert (pipeline["company_candidates"], pipeline["industry_candidates"], pipeline["irrelevant"]) == (2, 3, 2)
    assert pipeline["fetched"] - pipeline["duplicate_removed"] == pipeline["unique"]
    assert (
        pipeline["company_candidates"] + pipeline["industry_candidates"] + pipeline["irrelevant"]
        == pipeline["unique"]
    )
    assert pipeline["company_displayed"] == 2 and pipeline["industry_displayed"] == 3
    assert pipeline["not_displayed_by_limit"] == 0
    # legacy stats keys must keep the closed (post-dedupe) semantics
    assert payload["stats"]["raw"] == 8
    assert payload["stats"]["company"] == 2
    assert payload["stats"]["excluded_irrelevant"] == 2


def test_display_caps_are_reported_not_hidden(monkeypatch):
    companies = [
        _item(f"https://www.mining.com/c{i}", f"Pilbara Minerals news {i}") for i in range(25)
    ]
    industries = [
        _item(f"https://www.mining.com/i{i}", f"Lithium price update {i}", snippet="lithium price")
        for i in range(15)
    ]
    pipeline = _search(monkeypatch, companies + industries)["data"]["pipeline"]

    assert pipeline["company_candidates"] == 25 and pipeline["company_displayed"] == 20
    assert pipeline["industry_candidates"] == 15 and pipeline["industry_displayed"] == 12
    assert pipeline["not_displayed_by_limit"] == 8, "undisplayed items must be counted, not silently dropped"
    assert pipeline["unique"] == 40


def test_as_of_is_the_newest_displayed_publication(monkeypatch):
    items = [
        _item("https://www.mining.com/a", "Pilbara Minerals quarterly report released",
              published="2026-10-13T23:15:02+00:00"),
        _item("https://www.mining.com/c", "Lithium price outlook", snippet="lithium price",
              published="2026-10-15T06:30:00+00:00"),
    ]
    envelope = _search(monkeypatch, items)

    assert envelope["as_of"] == "2026-10-15T06:30:00+00:00", "as_of must cover industry items too"
    assert envelope["data"]["company_as_of"] == "2026-10-13T23:15:02+00:00"
    assert envelope["data"]["industry_as_of"] == "2026-10-15T06:30:00+00:00"


def test_undated_items_are_counted_and_never_guess_a_time(monkeypatch):
    items = [
        _item("https://www.mining.com/a", "Pilbara Minerals lifts output", published=""),
        _item("https://www.mining.com/c", "Lithium price outlook", snippet="lithium price",
              published="2026-10-11T00:00:00+00:00"),
    ]
    data = _search(monkeypatch, items)["data"]
    assert data["pipeline"]["undated"] == 1
    assert data["company_as_of"] is None
    assert data["pipeline"]["fetched"] == 2


def test_news_id_is_stable_across_runs_reordering_and_reclassification(monkeypatch):
    company_source = _item("https://www.mining.com/a", "Pilbara Minerals lifts output")
    industry_source = _item("https://www.mining.com/c", "Lithium price outlook", snippet="lithium price")

    first = _search(monkeypatch, [company_source, industry_source])["data"]
    second = _search(monkeypatch, [industry_source, company_source])["data"]

    first_by_title = {item["title"]: item["news_id"] for item in first["company_items"] + first["industry_items"]}
    second_by_title = {item["title"]: item["news_id"] for item in second["company_items"] + second["industry_items"]}
    assert first_by_title == second_by_title

    # The very same story re-classified from industry background to company news
    # keeps its id: evidence keys must not depend on the bucket it landed in.
    promoted = dict(industry_source, title="Pilbara Minerals lithium price outlook", snippet="lithium price")
    third = _search(monkeypatch, [promoted])["data"]
    assert third["company_items"][0]["news_id"] == ids.news_identity(promoted)[0]


def test_tracking_params_do_not_change_the_id():
    plain = {"url": "https://www.mining.com/a/", "title": "T", "source": "mining.com"}
    tracked = {"url": "https://www.mining.com/a/?utm_source=rss&oc=5", "title": "T", "source": "mining.com"}
    assert ids.news_identity(plain)[0] == ids.news_identity(tracked)[0]


def test_google_redirect_gets_a_degraded_id_and_no_fabricated_url():
    google = {"url": "https://news.google.com/rss/articles/CBMiXxY?oc=5", "title": "PLS shares fall", "source": "Kalkine"}
    news_id, kind = ids.news_identity(google)
    assert kind == "degraded" and news_id.startswith("nid-x-")
    assert "news.google.com" in news_id or "mining.com" not in news_id
    assert ids.normalize_url(google["url"]).startswith("https://news.google.com/rss/articles/CBMiXxY")
    # same article fetched again -> same id, still no invented target
    assert ids.news_identity(google) == (news_id, kind)


def test_display_numbers_are_not_evidence_keys(monkeypatch):
    items = [
        _item("https://www.mining.com/a", "Pilbara Minerals lifts output"),
        _item("https://www.mining.com/b", "PLS Group report released"),
        _item("https://www.mining.com/c", "Lithium price outlook", snippet="lithium price"),
    ]
    data = _search(monkeypatch, items)["data"]
    company_ids = [item["news_id"] for item in data["company_items"]]
    industry_ids = [item["news_id"] for item in data["industry_items"]]
    assert len(set(company_ids)) == len(company_ids)
    assert len(set(industry_ids)) == len(industry_ids)
    assert not set(company_ids) & set(industry_ids)
    # "C1"/"I1" only exist as render-time labels, never inside the payload
    assert all(not item["news_id"].startswith(("C", "I")) for item in data["company_items"])