"""RSS aggregation and article text extraction (no synthetic news, ever).

Two kinds of real source are combined:

* Google News RSS - best recency for a topical query, but its item links are
  JS redirects, so article bodies cannot be extracted from them.
* Direct publisher feeds - real article URLs, so the agent can actually read
  the body text.

Everything else (Bing News RSS) is a documented fallback.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

from lxml import html as lxml_html

from ..common import cache
from ..common.http import DEFAULT_TIMEOUT, UrlNotAllowedError, assert_url_allowed, get_text

BING_NEWS_RSS = "https://www.bing.com/news/search"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"

DIRECT_FEEDS: tuple[tuple[str, str], ...] = (
    ("mining.com", "https://www.mining.com/feed/"),
    ("australianmining.com.au", "https://www.australianmining.com.au/feed/"),
)

# Hosts that only ever serve a JavaScript redirect, never article text.
UNRESOLVABLE_HOSTS = ("news.google.com", "news.url.google.com")

ARTICLE_MIN_CHARS = 200
_GOOGLE_HOST_HINT = "Google News 链接是 JS 跳转页，无法解析原文正文"
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "svg",
    "iframe",
)


class UnresolvableRedirectError(ValueError):
    """Raised for aggregator links whose target cannot be recovered."""


def _text_of(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return html.unescape(node.text).strip()


def _parse_pubdate(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def unwrap_bing_url(url: str) -> str:
    """Bing wraps the publisher URL inside an apiclick redirect."""

    parsed = urlparse(url)
    if "bing.com" not in (parsed.hostname or ""):
        return url
    target = parse_qs(parsed.query).get("url", [])
    return target[0] if target and target[0].startswith("http") else url


def parse_rss(xml_text: str, *, source_name: str) -> list[dict[str, str]]:
    """Normalise an RSS document into item dicts (pure function, unit tested)."""

    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []
    for node in root.iter("item"):
        link = _text_of(node.find("link"))
        if source_name == "bing-news-rss":
            link = unwrap_bing_url(link)
        publisher = _text_of(node.find("source"))
        description = _text_of(node.find("description"))
        snippet = re.sub(r"<[^>]+>", " ", description)
        snippet = re.sub(r"\s+", " ", html.unescape(snippet)).strip()
        published = _parse_pubdate(_text_of(node.find("pubDate")))
        items.append(
            {
                "title": _text_of(node.find("title")),
                "url": link,
                "source": publisher or source_name,
                "provider": source_name,
                "published_at": published.isoformat(timespec="seconds") if published else "",
                "snippet": snippet,
            }
        )
    return items


def tokenize(query: str) -> list[str]:
    return [token.lower() for token in re.split(r"[\s,，、]+", query) if len(token) > 1]


def _within_window(item: dict[str, str], cutoff: datetime) -> bool:
    published = item.get("published_at")
    if not published:
        return True
    try:
        return datetime.fromisoformat(published) >= cutoff
    except ValueError:
        return True


def select_items(
    items: list[dict[str, str]],
    *,
    query: str,
    days: int,
    now: datetime | None = None,
) -> tuple[list[dict[str, str]], str]:
    """Prefer items matching *every* query token, then fall back to any token."""

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(days, 1))
    tokens = tokenize(query)

    window = [item for item in items if item.get("url") and _within_window(item, cutoff)]
    if not tokens:
        return window, "no-token-filter"

    def haystack(item: dict[str, str]) -> str:
        return f"{item.get('title', '')} {item.get('snippet', '')}".lower()

    strict = [item for item in window if all(token in haystack(item) for token in tokens)]
    if strict:
        return strict, "all-tokens"
    relaxed = [item for item in window if any(token in haystack(item) for token in tokens)]
    return relaxed, "any-token"


def search_google(query: str, *, days: int, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, str]]:
    windowed = query if "when:" in query.lower() else f"{query} when:{max(days, 1)}d"
    xml_text = get_text(
        GOOGLE_NEWS_RSS,
        params={"q": windowed, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        timeout=timeout,
    )
    items = parse_rss(xml_text, source_name="google-news-rss")
    selected, _ = select_items(items, query=query, days=days)
    return selected


def search_direct_feeds(query: str, *, days: int, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, str]]:
    """Publisher feeds give real article URLs, which is what bodies need."""

    collected: list[dict[str, str]] = []
    failures: list[str] = []
    for name, url in DIRECT_FEEDS:
        try:
            xml_text = get_text(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - one dead feed must not kill the search
            failures.append(f"{name}: {type(exc).__name__}")
            continue
        items = parse_rss(xml_text, source_name=f"direct-feed:{name}")
        selected, _ = select_items(items, query=query, days=days)
        collected.extend(selected)
    if not collected and failures and len(failures) == len(DIRECT_FEEDS):
        raise RuntimeError("all direct feeds failed: " + "; ".join(failures))
    return collected


def search_bing(query: str, *, days: int, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, str]]:
    xml_text = get_text(BING_NEWS_RSS, params={"q": query, "format": "RSS"}, timeout=timeout)
    items = parse_rss(xml_text, source_name="bing-news-rss")
    selected, _ = select_items(items, query=query, days=days)
    return selected


def search_queries(
    queries: Sequence[str],
    *,
    days: int,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[list[dict[str, str]], list[str]]:
    """Run several precise queries across Google News *and* the publisher feeds.

    Entity-aware searching needs more than one query ("Pilbara Minerals",
    "Pilgangoora", the ticker), and each item remembers which query found it.
    """

    collected: list[dict[str, str]] = []
    notes: list[str] = []
    for query in queries:
        for name, func in (("google-news-rss", search_google), ("direct-feeds", search_direct_feeds)):
            try:
                found = func(query, days=days, timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - one dead source must not kill the search
                notes.append(f"{name}({query!r}) failed: {type(exc).__name__}: {exc}")
                continue
            notes.append(f"{name}({query!r}) -> {len(found)} item(s)")
            for item in found:
                tagged = dict(item)
                tagged["matched_query"] = query
                collected.append(tagged)
    return collected, notes

def _first_xpath(tree: object, paths: tuple[str, ...]) -> str:
    for path in paths:
        found = tree.xpath(path)  # type: ignore[attr-defined]
        if found and str(found[0]).strip():
            return re.sub(r"\s+", " ", str(found[0])).strip()
    return ""


# Container class/id markers that mean "this is chrome, not the article body".
_NOISE_MARKERS = re.compile(
    r"related|classified|sidebar|aside|comment|newsletter|promo|advert|sponsor|"
    r"breadcrumb|footer|header|nav|menu|widget|recommend|teaser|subscribe|paywall",
    re.IGNORECASE,
)

# Content-ish containers, used in addition to <article>/<main>/<body>. Some sites
# (e.g. australianmining.com.au) put the body in div.entry-content while the page
# also carries a dozen <article> teaser cards - picking the first <article> there
# yields only teaser text.
_CONTENT_XPATH = (
    "//article | //main | "
    "//div[contains(@class,'entry-content') or contains(@class,'content-inner') or "
    "contains(@class,'post-content') or contains(@class,'article-body') or "
    "contains(@class,'article__body') or contains(@class,'story-body') or "
    "contains(@class,'post-body') or contains(@class,'articleBody')]"
)


def _is_noise(node: object) -> bool:
    marker = f"{node.get('class', '')} {node.get('id', '')}"  # type: ignore[attr-defined]
    return bool(_NOISE_MARKERS.search(marker))


def _paragraphs(container: object, min_chars: int = 40) -> list[str]:
    out: list[str] = []
    for node in container.xpath(".//p"):  # type: ignore[attr-defined]
        if _is_noise(node):
            continue
        text = re.sub(r"\s+", " ", node.text_content()).strip()
        if len(text) >= min_chars:
            out.append(text)
    return out


def extract_article(document: str, url: str) -> dict[str, object]:
    """Pull a readable body out of an HTML document (pure function, unit tested).

    Candidate containers are scored by how much article-like paragraph text they
    hold, which is what makes this work on sites whose first <article> element is
    a teaser card rather than the story.
    """

    tree = lxml_html.fromstring(document)
    for tag in _DROP_TAGS:
        for node in tree.xpath(f"//{tag}"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    # Score only specific content containers. <body> is deliberately excluded
    # here: it swallows teaser cards and related links, so it is used purely as a
    # last-resort fallback when no specific container holds enough text.
    best: list[str] = []
    for container in tree.xpath(_CONTENT_XPATH):
        if _is_noise(container):
            continue
        paragraphs = _paragraphs(container)
        if sum(len(item) for item in paragraphs) > sum(len(item) for item in best):
            best = paragraphs

    text = "\n\n".join(best)
    if len(text) < ARTICLE_MIN_CHARS:
        for body in tree.xpath("//body"):
            paragraphs = _paragraphs(body)
            candidate_text = "\n\n".join(paragraphs)
            if len(candidate_text) > len(text):
                text = candidate_text
            break
        if len(text) < ARTICLE_MIN_CHARS:
            for body in tree.xpath("//body"):
                raw = re.sub(r"\s+", " ", body.text_content()).strip()
                if len(raw) > len(text):
                    text = raw
                break

    title = _first_xpath(
        tree,
        (
            '//meta[@property="og:title"]/@content',
            '//meta[@name="twitter:title"]/@content',
            "//title/text()",
            "//h1/text()",
        ),
    )
    published = _first_xpath(
        tree,
        (
            '//meta[@property="article:published_time"]/@content',
            '//meta[@name="pubdate"]/@content',
            "//time/@datetime",
        ),
    )
    site = _first_xpath(tree, ('//meta[@property="og:site_name"]/@content',)) or (
        urlparse(url).hostname or ""
    )

    return {
        "url": url,
        "title": title,
        "site": site,
        "published_at": published,
        "text": text,
        "word_count": len(text.split()),
    }

def fetch_article(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    refresh: bool = False,
) -> tuple[dict[str, object], bool]:
    """Return ``(article, from_cache)``. Raises on failure; never fabricates.

    ``refresh=True`` skips the on-disk cache so a strict live check really hits
    the network (the same contract the PDF tool uses for its documents).
    """

    assert_url_allowed(url)
    host = (urlparse(url).hostname or "").lower()
    if any(host == blocked or host.endswith("." + blocked) for blocked in UNRESOLVABLE_HOSTS):
        raise UnresolvableRedirectError(f"{_GOOGLE_HOST_HINT}: {url}")

    key = cache.cache_key(url)
    cached = None if refresh else cache.read_json("news", f"article-{key}")
    if cached and cached.get("payload"):
        return cached["payload"], True

    document = get_text(url, timeout=timeout, max_redirects=3)
    article = extract_article(document, url)
    cache.write_json("news", f"article-{key}", article)
    return article, False


__all__ = [
    "BING_NEWS_RSS",
    "DIRECT_FEEDS",
    "GOOGLE_NEWS_RSS",
    "UNRESOLVABLE_HOSTS",
    "UnresolvableRedirectError",
    "UrlNotAllowedError",
    "extract_article",
    "fetch_article",
    "parse_rss",
    "search_bing",
    "search_queries",
    "search_direct_feeds",
    "search_google",
    "select_items",
    "tokenize",
    "unwrap_bing_url",
]
