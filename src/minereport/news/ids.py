"""Stable, cross-report identifiers for news items.

A display number (``C1``/``I2``) changes whenever ordering changes, so it can
never be an evidence key. ``news_id`` is derived from a *normalised* URL - or,
for aggregator links whose real target cannot be recovered, from title+source -
so the same story keeps the same id across runs, reports and classifications.

``hash()`` is never used: it is salted per process and would not survive a
restart.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Parameters that never change which article a URL points at.
TRACKING_PARAMS = {
    "oc",
    "ref",
    "source",
    "fbclid",
    "gclid",
    "yclid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "spm",
    "cmpid",
    "hl",
    "gl",
    "ceid",
}
TRACKING_PREFIXES = ("utm_",)

# Hosts that only ever serve a JavaScript redirect: the target is *not*
# recoverable, so we must not guess it.
UNRESOLVABLE_HOSTS = ("news.google.com", "news.url.google.com")

_PUNCT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _norm_text(text: Any) -> str:
    return _PUNCT_RE.sub(" ", str(text or "").lower()).strip()


def is_unresolvable(url: str) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return any(host == blocked or host.endswith("." + blocked) for blocked in UNRESOLVABLE_HOSTS)


def normalize_url(url: str) -> str:
    """Lowercase scheme/host, drop tracking params and fragments, keep the path.

    Google News paths are opaque base64 tokens and are deliberately preserved
    verbatim: rewriting them would invent a URL that does not exist.
    """

    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    if not parts.scheme or not parts.netloc:
        return raw
    host = (parts.hostname or "").lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
        and not any(key.lower().startswith(prefix) for prefix in TRACKING_PREFIXES)
    ]
    query = "&".join(f"{key}={value}" for key, value in sorted(kept))
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), query, ""))


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def news_identity(item: Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(news_id, kind)`` where kind is ``url``, ``degraded`` or ``unknown``."""

    url = normalize_url(str(item.get("url") or ""))
    if url.lower().startswith(("http://", "https://")) and not is_unresolvable(url):
        return f"nid-{_digest('url:' + url)}", "url"
    title = _norm_text(item.get("title"))
    source = _norm_text(item.get("source") or item.get("provider"))
    if not title:
        # Neither a usable URL nor a title: no honest identifier exists.
        return "", "unknown"
    return f"nid-x-{_digest(f'degraded:{title}|{source}')}", "degraded"


__all__ = [
    "TRACKING_PARAMS",
    "UNRESOLVABLE_HOSTS",
    "is_unresolvable",
    "news_identity",
    "normalize_url",
]