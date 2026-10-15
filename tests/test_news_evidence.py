"""Evidence grading: source attribute vs content verification."""

from __future__ import annotations

from minereport.news import entities

ENTITY = entities.resolve_entity("Pilbara 锂矿")
BODY = "Pilbara Minerals Limited operates Pilgangoora in Western Australia. " * 10


def _assess(item, fetch=None):
    return entities.assess_evidence(item, entity=ENTITY, fetch=fetch)


def test_official_disclosure_needs_domain_body_marker_and_entity():
    item = {
        "url": "https://www.pls.com/investors/asx-announcement-quarterly.pdf",
        "title": "ASX Announcement: Pilbara Minerals quarterly activities report",
        "provider": "direct-feed:pls.com",
    }
    out = _assess(item, {"status": "success", "chars": len(BODY), "text": BODY})
    assert out["source_type"] == entities.OFFICIAL
    assert out["evidence"] == entities.EVIDENCE_OFFICIAL_DISCLOSURE
    assert out["official_verified"] is True
    assert "pls.com" in out["evidence_reason"]


def test_official_domain_alone_is_not_a_disclosure():
    item = {
        "url": "https://www.pls.com/about/community",
        "title": "Community programs",
        "provider": "direct-feed:pls.com",
    }
    out = _assess(item, {"status": "success", "chars": len(BODY), "text": BODY})
    assert out["source_type"] == entities.OFFICIAL
    assert out["evidence"] == entities.EVIDENCE_FULL_ARTICLE
    assert out["official_verified"] is False


def test_official_domain_without_body_stays_at_summary_level():
    item = {
        "url": "https://announcements.asx.com.au/pls/2026-release.pdf",
        "title": "ASX Announcement",
        "snippet": "Pilbara Minerals release",
        "provider": "rss",
    }
    out = _assess(item, {"status": "failed", "error": "403"})
    assert out["source_type"] == entities.OFFICIAL
    assert out["evidence"] == entities.EVIDENCE_RSS_SUMMARY
    assert out["official_verified"] is False
    assert out["fetch_status"] == entities.FETCH_FAILED


def test_media_body_is_full_article():
    item = {"url": "https://www.mining.com/x/", "title": "Pilbara output", "provider": "direct-feed:mining.com"}
    out = _assess(item, {"status": "success", "chars": len(BODY), "text": BODY})
    assert out["source_type"] == entities.MEDIA
    assert out["evidence"] == entities.EVIDENCE_FULL_ARTICLE


def test_short_body_is_not_a_full_article():
    item = {"url": "https://www.mining.com/x/", "title": "Pilbara output", "snippet": "summary text",
            "provider": "direct-feed:mining.com"}
    out = _assess(item, {"status": "success", "chars": 40, "text": "too short"})
    assert out["evidence"] == entities.EVIDENCE_RSS_SUMMARY


def test_title_only_and_unverifiable():
    title_only = _assess({"url": "https://www.mining.com/x/", "title": "Prices fall",
                          "provider": "direct-feed:mining.com"})
    assert title_only["evidence"] == entities.EVIDENCE_TITLE_ONLY

    nothing = _assess({"url": "", "title": "", "provider": ""})
    assert nothing["evidence"] == entities.EVIDENCE_UNVERIFIABLE
    assert nothing["source_type"] == entities.UNKNOWN


def test_google_redirect_is_rss_and_never_fabricates_a_body():
    item = {"url": "https://news.google.com/rss/articles/CBMiXxY?oc=5", "title": "PLS falls",
            "provider": "google-news-rss"}
    out = _assess(item, {"status": entities.FETCH_UNRESOLVABLE, "error": "UNRESOLVABLE_REDIRECT"})
    assert out["source_type"] == entities.RSS
    assert out["evidence"] == entities.EVIDENCE_TITLE_ONLY
    assert out["fetch_status"] == entities.FETCH_UNRESOLVABLE
    assert out["official_verified"] is False


def test_every_level_has_a_chinese_label_and_a_reason():
    for level in entities.EVIDENCE_LEVELS:
        assert entities.EVIDENCE_LABELS[level]

    item = {"url": "https://www.mining.com/x/", "title": "T", "snippet": "S",
            "provider": "direct-feed:mining.com"}
    out = _assess(item)
    assert out["evidence_reason"]
    assert out["fetch_status"] == entities.FETCH_NOT_ATTEMPTED
    assert out["evidence"] == entities.EVIDENCE_RSS_SUMMARY