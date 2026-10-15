"""News parsing and relevance selection (offline, pure functions)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from minereport.news import sources

BING_RSS = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel>
  <item>
    <title>Pilbara lithium output rises</title>
    <link>https://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3A%2F%2Fexample.com%2Fpilbara</link>
    <description>Pilbara lithium concentrate shipments increased.</description>
    <pubDate>Tue, 13 Oct 2026 08:00:00 GMT</pubDate>
    <source url="https://example.com">Example News</source>
  </item>
</channel></rss>
"""

GOOGLE_RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel>
  <item>
    <title>Pilbara lithium project update</title>
    <link>https://news.google.com/rss/articles/ABC123?oc=5</link>
    <description>Pilbara lithium project update Example</description>
    <pubDate>Wed, 14 Oct 2026 06:00:00 GMT</pubDate>
    <source url="https://example.com">Example</source>
  </item>
  <item>
    <title>Iron ore shipments steady</title>
    <link>https://news.google.com/rss/articles/DEF456?oc=5</link>
    <description>Iron ore only.</description>
    <pubDate>Wed, 14 Oct 2026 05:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

ARTICLE_HTML = (
    """<!doctype html><html><head>
<title>Pilbara lithium output rises</title>
<meta property="og:site_name" content="Example News">
<meta property="article:published_time" content="2026-10-13T08:00:00Z">
</head><body>
<nav>menu menu</nav>
<article>
<p>"""
    + ("Pilbara Minerals lifted spodumene concentrate output in the quarter. " * 4)
    + """</p>
<p>"""
    + ("The company reiterated its production guidance for the year ahead. " * 4)
    + """</p>
</article>
<footer>footer</footer>
</body></html>
"""
)


def test_unwrap_bing_url_returns_publisher_url():
    wrapped = (
        "https://www.bing.com/news/apiclick.aspx?ref=FexRss"
        "&url=https%3A%2F%2Fexample.com%2Fpilbara&c=1"
    )
    assert sources.unwrap_bing_url(wrapped) == "https://example.com/pilbara"


def test_parse_rss_normalises_items():
    items = sources.parse_rss(BING_RSS, source_name="bing-news-rss")
    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://example.com/pilbara"
    assert item["source"] == "Example News"
    assert item["provider"] == "bing-news-rss"
    assert item["published_at"].startswith("2026-10-13")


def test_select_items_prefers_all_token_matches():
    items = sources.parse_rss(GOOGLE_RSS, source_name="google-news-rss")
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    selected, mode = sources.select_items(items, query="Pilbara lithium", days=7, now=now)
    assert mode == "all-tokens"
    assert [item["title"] for item in selected] == ["Pilbara lithium project update"]


def test_select_items_falls_back_to_any_token():
    items = sources.parse_rss(GOOGLE_RSS, source_name="google-news-rss")
    now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    selected, mode = sources.select_items(items, query="Pilbara copper", days=7, now=now)
    assert mode == "any-token"
    assert len(selected) == 1


def test_select_items_drops_stale_entries():
    items = sources.parse_rss(GOOGLE_RSS, source_name="google-news-rss")
    later = datetime(2026, 12, 1, tzinfo=timezone.utc)
    selected, _ = sources.select_items(items, query="Pilbara lithium", days=7, now=later)
    assert selected == []


def test_extract_article_strips_chrome_and_keeps_body():
    article = sources.extract_article(ARTICLE_HTML, "https://example.com/pilbara")
    assert article["title"] == "Pilbara lithium output rises"
    assert article["site"] == "Example News"
    assert "menu menu" not in article["text"]
    assert "spodumene concentrate output" in article["text"]
    assert article["word_count"] > 30


def test_fetch_article_refuses_google_redirect(monkeypatch):
    called = {"n": 0}

    def fake_get_text(*args, **kwargs):  # pragma: no cover - must not be called
        called["n"] += 1
        return ARTICLE_HTML

    monkeypatch.setattr(sources, "get_text", fake_get_text)
    with pytest.raises(sources.UnresolvableRedirectError):
        sources.fetch_article("https://news.google.com/rss/articles/ABC123?oc=5")
    assert called["n"] == 0


def test_fetch_article_rejects_private_host():
    with pytest.raises(sources.UrlNotAllowedError):
        sources.fetch_article("http://127.0.0.1/secret")



TEASER_PAGE_HTML = """<html><body>
<article class="card"><p>Short teaser paragraph that is long enough to qualify as a paragraph but is not the story body at all.</p></article>
<article class="card"><p>Another teaser card paragraph that is also long enough to be picked up by a naive extractor.</p></article>
<div class="entry-content"><div class="content-inner">
<p>The company confirmed that the first real body paragraph contains the actual reporting about the event that happened today.</p>
<p>The second real body paragraph continues the story with additional detail and a quotation from a senior executive.</p>
<p>A third paragraph closes the article with market context and the outlook for the coming quarters.</p>
</div></div>
</body></html>"""


def test_extract_article_prefers_the_real_body_over_teaser_cards():
    """australianmining.com.au keeps the story in div.entry-content and ships 15 teaser <article> tags."""

    article = sources.extract_article(TEASER_PAGE_HTML, "https://example.com/story")
    assert "first real body paragraph" in article["text"]
    assert "second real body paragraph" in article["text"]
    assert "teaser" not in article["text"].lower()
    assert article["word_count"] >= 40


def test_fetch_article_refresh_bypasses_the_cache(monkeypatch):
    calls = {"n": 0}

    def fake_get_text(*args, **kwargs):
        calls["n"] += 1
        return TEASER_PAGE_HTML

    monkeypatch.setattr(sources, "get_text", fake_get_text)
    url = "https://example.com/refresh-check"
    sources.fetch_article(url, refresh=True)
    sources.fetch_article(url, refresh=True)
    assert calls["n"] == 2, "refresh=True must always hit the network"