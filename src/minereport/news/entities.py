"""Company entity resolution so news relevance is not a place-name guess.

The bug this module exists to prevent: "Pilbara" is a *region*. Rio Tinto's iron
ore business and a gold explorer called Pilbara Gold both match it, so a naive
keyword filter pulled them into a Pilbara Minerals brief. Relevance now requires
explicit company evidence (official name, ticker or project name), and anything
matching a known confusable entity is dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from ..common import config
from . import ids

REGISTRY_PATH = config.SOURCES_DIR / "entities.json"

COMPANY = "company"
INDUSTRY = "industry"
IRRELEVANT = "irrelevant"

# --- where an item was published (source_type) -------------------------------
OFFICIAL = "official"
MEDIA = "media"
RSS = "rss"
UNKNOWN = "unknown"

MEDIA_DOMAINS = ("mining.com", "australianmining.com.au")

# --- how well its content was verified (evidence) ----------------------------
EVIDENCE_OFFICIAL_DISCLOSURE = "official_disclosure"
EVIDENCE_FULL_ARTICLE = "full_article"
EVIDENCE_RSS_SUMMARY = "rss_summary"
EVIDENCE_TITLE_ONLY = "title_only"
EVIDENCE_UNVERIFIABLE = "unverifiable"

EVIDENCE_LEVELS = (
    EVIDENCE_OFFICIAL_DISCLOSURE,
    EVIDENCE_FULL_ARTICLE,
    EVIDENCE_RSS_SUMMARY,
    EVIDENCE_TITLE_ONLY,
    EVIDENCE_UNVERIFIABLE,
)

EVIDENCE_LABELS = {
    EVIDENCE_OFFICIAL_DISCLOSURE: "官方公告",
    EVIDENCE_FULL_ARTICLE: "完整正文",
    EVIDENCE_RSS_SUMMARY: "RSS 摘要",
    EVIDENCE_TITLE_ONLY: "仅标题",
    EVIDENCE_UNVERIFIABLE: "无法核实",
}

# --- how the body fetch went -------------------------------------------------
FETCH_SUCCESS = "success"
FETCH_FAILED = "failed"
FETCH_UNRESOLVABLE = "unresolvable_redirect"
FETCH_NOT_ATTEMPTED = "not_attempted"
FETCH_TIMEOUT = "timeout"
FETCH_BUDGET_EXHAUSTED = "budget_exhausted"

FETCH_STATUSES = (
    FETCH_SUCCESS,
    FETCH_FAILED,
    FETCH_UNRESOLVABLE,
    FETCH_NOT_ATTEMPTED,
    FETCH_TIMEOUT,
    FETCH_BUDGET_EXHAUSTED,
)

_PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _norm(text: str) -> str:
    return _PUNCT_RE.sub(" ", (text or "").lower()).strip()


@dataclass(frozen=True)
class Entity:
    key: str
    name: str
    aliases: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    match_keywords: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    industry_terms: tuple[str, ...] = ()
    industry_context_terms: tuple[str, ...] = ()
    search_queries: tuple[str, ...] = ()
    industry_query: str = ""
    official_domains: tuple[str, ...] = ()

    def company_evidence(self, text: str) -> list[str]:
        """Explicit company/project mentions only - never a bare place name."""

        haystack = _norm(text)
        hits = [
            phrase
            for phrase in (*self.aliases, *self.tickers, *self.projects)
            if phrase and _norm(phrase) in haystack
        ]
        # A ticker must appear as a standalone token, otherwise "pls" inside a
        # word would match; aliases are phrases and are safe as substrings.
        return hits

    def exclusions(self, text: str) -> list[str]:
        haystack = _norm(text)
        return [term for term in self.exclude_terms if _norm(term) in haystack]

    def industry_evidence(self, text: str) -> tuple[list[str], list[str]]:
        haystack = _norm(text)
        terms = [term for term in self.industry_terms if _norm(term) in haystack]
        context = [term for term in self.industry_context_terms if _norm(term) in haystack]
        return terms, context


@lru_cache(maxsize=1)
def _payload() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"entities": []}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _load() -> tuple[Entity, ...]:
    payload = _payload()
    entities = []
    for raw in payload.get("entities", []):
        entities.append(
            Entity(
                key=raw["key"],
                name=raw["name"],
                aliases=tuple(raw.get("aliases", ())),
                tickers=tuple(raw.get("tickers", ())),
                projects=tuple(raw.get("projects", ())),
                match_keywords=tuple(raw.get("match_keywords", ())),
                exclude_terms=tuple(raw.get("exclude_terms", ())),
                industry_terms=tuple(raw.get("industry_terms", ())),
                industry_context_terms=tuple(raw.get("industry_context_terms", ())),
                search_queries=tuple(raw.get("search_queries", ())),
                industry_query=raw.get("industry_query", ""),
                official_domains=tuple(raw.get("official_domains", ())),
            )
        )
    return tuple(entities)


def reload_entities() -> None:
    _load.cache_clear()
    _payload.cache_clear()


def official_domains(entity: Entity | None = None) -> tuple[str, ...]:
    """Exchange/regulator hosts plus this company's own hosts."""

    global_domains = tuple(_payload().get("official_domains", ()))
    own = tuple(getattr(entity, "official_domains", ()) or ())
    return tuple(dict.fromkeys([*own, *global_domains]))


def disclosure_markers() -> tuple[str, ...]:
    return tuple(_payload().get("disclosure_markers", ()))


def host_of(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(str(url or "")).hostname or "").lower()


def host_matches(host: str, domains: Iterable[str]) -> str | None:
    for domain in domains:
        candidate = str(domain).strip().lstrip(".").lower()
        if candidate and (host == candidate or host.endswith("." + candidate)):
            return candidate
    return None


def source_type_for(item: dict[str, Any], entity: Entity | None = None) -> tuple[str, str | None]:
    """``(source_type, matched_official_domain)`` - where it was published only.

    A domain hit deliberately does *not* imply the item is a formal disclosure;
    that question is answered by :func:`assess_evidence`.
    """

    url = str(item.get("url") or "")
    host = host_of(url)
    provider = str(item.get("provider") or "")
    if host:
        matched = host_matches(host, official_domains(entity))
        if matched:
            return OFFICIAL, matched
    if ids.is_unresolvable(url) or provider in {"google-news-rss", "bing-news-rss"}:
        return RSS, None
    if provider.startswith("direct-feed:"):
        return MEDIA, None
    if host_matches(host, MEDIA_DOMAINS):
        return MEDIA, None
    if host:
        return MEDIA, None
    return UNKNOWN, None


def assess_evidence(
    item: dict[str, Any],
    *,
    entity: Entity | None = None,
    fetch: dict[str, Any] | None = None,
    min_chars: int | None = None,
) -> dict[str, Any]:
    """Deterministic evidence matrix: source attribute vs content verification.

    ``official_disclosure`` needs all four: official host, a real body, a
    disclosure marker and a matching company entity. A plain "about us" page on
    an official domain therefore stays at whatever level its content supports.
    """

    if min_chars is None:
        from .sources import ARTICLE_MIN_CHARS as _MIN

        min_chars = _MIN

    fetch = dict(fetch or {})
    status = str(fetch.get("status") or item.get("fetch_status") or FETCH_NOT_ATTEMPTED)
    if status not in FETCH_STATUSES:
        status = FETCH_FAILED
    chars = int(fetch.get("chars") or 0)
    body = str(fetch.get("text") or "")
    title = str(item.get("title") or "").strip()
    snippet = str(item.get("snippet") or "").strip()

    source_type, official_domain = source_type_for(item, entity)

    haystack = _norm(" ".join([title, snippet, str(item.get("url") or ""), body[:2000]]))
    markers = [marker for marker in disclosure_markers() if _norm(marker) and _norm(marker) in haystack]
    entity_hit = bool(entity) and bool(
        entity.company_evidence(" ".join([title, snippet, body[:2000]]))
    )
    body_chars = max(chars, len(body))
    valid_body = status == FETCH_SUCCESS and body_chars >= int(min_chars)

    reason_suffix = ""
    if source_type == OFFICIAL and not valid_body:
        reason_suffix = "；官方域名但未取得正文，不能据此认定正式披露"
    elif source_type == OFFICIAL and not markers:
        reason_suffix = "；官方域名但缺少披露标记"

    if source_type == OFFICIAL and valid_body and markers and entity_hit:
        return {
            "source_type": source_type,
            "official_domain": official_domain,
            "evidence": EVIDENCE_OFFICIAL_DISCLOSURE,
            "official_verified": True,
            "fetch_status": status,
            "evidence_reason": (
                f"官方域名 {official_domain} + 正文 {body_chars} 字符 + 披露标记「{markers[0]}」+ 实体匹配"
            ),
        }

    if valid_body:
        return {
            "source_type": source_type,
            "official_domain": official_domain,
            "evidence": EVIDENCE_FULL_ARTICLE,
            "official_verified": False,
            "fetch_status": status,
            "evidence_reason": f"抓取成功且正文 {body_chars} 字符（≥{min_chars}）{reason_suffix}",
        }

    if snippet:
        return {
            "source_type": source_type,
            "official_domain": official_domain,
            "evidence": EVIDENCE_RSS_SUMMARY,
            "official_verified": False,
            "fetch_status": status,
            "evidence_reason": f"仅有 RSS 标题与摘要{reason_suffix}",
        }

    if title:
        return {
            "source_type": source_type,
            "official_domain": official_domain,
            "evidence": EVIDENCE_TITLE_ONLY,
            "official_verified": False,
            "fetch_status": status,
            "evidence_reason": f"仅有标题{reason_suffix}",
        }

    return {
        "source_type": source_type,
        "official_domain": official_domain,
        "evidence": EVIDENCE_UNVERIFIABLE,
        "official_verified": False,
        "fetch_status": status,
        "evidence_reason": "标题与摘要皆缺失，无法核实内容",
    }


def resolve_entity(query: str) -> Entity | None:
    """Which registered company does this user query refer to?"""

    haystack = _norm(query)
    best: tuple[int, Entity] | None = None
    for entity in _load():
        keywords = [k for k in entity.match_keywords if _norm(k) and _norm(k) in haystack]
        if not keywords:
            continue
        score = max(len(_norm(k)) for k in keywords)
        if best is None or score > best[0]:
            best = (score, entity)
    return best[1] if best else None


def classify_item(item: dict[str, Any], entity: Entity | None) -> tuple[str, dict[str, Any]]:
    """Return ``(label, evidence)`` for a news item.

    Order matters: explicit company evidence wins, then a known confusable
    entity disqualifies the item, then industry relevance is scored.
    """

    if entity is None:
        return INDUSTRY, {"reason": "no entity registered for this query"}

    text = f"{item.get('title', '')} {item.get('snippet', '')}"

    company_hits = entity.company_evidence(text)
    if company_hits:
        return COMPANY, {"company_evidence": company_hits}

    exclusions = entity.exclusions(text)
    if exclusions:
        return IRRELEVANT, {"excluded_by": exclusions}

    terms, context = entity.industry_evidence(text)
    if len(terms) >= 2 or (terms and context):
        return INDUSTRY, {"industry_terms": terms, "context_terms": context[:4]}

    return IRRELEVANT, {"reason": "no company evidence and insufficient industry relevance"}


def dedupe_key(item: dict[str, Any]) -> str:
    """Deduplicate on URL *and* title so the same story from two feeds collapses."""

    url = (item.get("url") or "").strip().lower()
    url = re.sub(r"[?&](utm_[^&]*|oc|hl|gl|ceid)=[^&]*", "", url)
    title = _norm(item.get("title", ""))
    source = _norm(item.get("source", ""))
    return f"{url}|{title[:70]}|{source[:20]}"


def dedupe_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the same story found by several queries/feeds.

    The stable ``news_id`` is the primary key; a normalised title is the second
    guard so a Google-redirect copy and a publisher-feed copy of one story do not
    both survive.
    """

    seen: set[str] = set()
    titles: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        news_id, _kind = ids.news_identity(item)
        title_key = _norm(item.get("title", ""))[:70]
        keys = [key for key in (news_id, f"title:{title_key}" if title_key else "") if key]
        if any(key in seen for key in keys):
            continue
        if title_key and title_key in titles:
            continue
        seen.update(keys)
        if title_key:
            titles.add(title_key)
        unique.append(item)
    return unique


__all__ = [
    "COMPANY",
    "INDUSTRY",
    "IRRELEVANT",
    "EVIDENCE_FULL_ARTICLE",
    "EVIDENCE_LABELS",
    "EVIDENCE_LEVELS",
    "EVIDENCE_OFFICIAL_DISCLOSURE",
    "EVIDENCE_RSS_SUMMARY",
    "EVIDENCE_TITLE_ONLY",
    "EVIDENCE_UNVERIFIABLE",
    "FETCH_BUDGET_EXHAUSTED",
    "FETCH_FAILED",
    "FETCH_NOT_ATTEMPTED",
    "FETCH_STATUSES",
    "FETCH_SUCCESS",
    "FETCH_TIMEOUT",
    "FETCH_UNRESOLVABLE",
    "MEDIA",
    "OFFICIAL",
    "RSS",
    "UNKNOWN",
    "assess_evidence",
    "disclosure_markers",
    "official_domains",
    "source_type_for",
    "REGISTRY_PATH",
    "Entity",
    "classify_item",
    "dedupe_items",
    "dedupe_key",
    "reload_entities",
    "resolve_entity",
]