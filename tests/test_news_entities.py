"""Entity-aware news relevance: confusable companies must not leak in."""

from __future__ import annotations

import pytest

from minereport.news import entities

ENTITY = entities.resolve_entity("给我生成一份关于 Pilbara 锂矿的今日简报")


def test_chinese_query_resolves_to_the_company_entity():
    assert ENTITY is not None
    assert ENTITY.name == "Pilbara Minerals Limited"
    assert "pilgangoora" in ENTITY.projects


@pytest.mark.parametrize(
    "title",
    [
        "Rio Tinto reports 85.7 million tonnes of Pilbara sales",
        "Rio Tinto reports strong Pilbara iron ore performance as major projects progress",
        "Pilbara Gold (ASX:PGL) Expands Terra Gold Mineralisation at Roe Hills",
        "Linkforce: Pilbara mining company sues ex-employee over alleged secret breach",
    ],
)
def test_confusable_entities_are_irrelevant(title):
    """A bare place name is never evidence of the company."""

    label, _ = entities.classify_item({"title": title}, ENTITY)
    assert label == entities.IRRELEVANT


@pytest.mark.parametrize(
    "title",
    [
        "Pilbara Minerals lifts spodumene concentrate output at Pilgangoora",
        "PLS Group Limited (ASX:PLS): How Pilgangoora Processes Spodumene",
        "Pilgangoora lithium expansion study approved",
        "Pilbara Minerals Limited signs new offtake agreement",
    ],
)
def test_company_direct_items_need_explicit_evidence(title):
    label, evidence = entities.classify_item({"title": title}, ENTITY)
    assert label == entities.COMPANY
    assert evidence.get("company_evidence")


@pytest.mark.parametrize(
    "title",
    [
        "EnergyX inks 10-year, $2B lithium offtake deal with Trafigura",
        "Lithium Triangle's vast resources face unsolved extraction hurdles",
        "Lithium prices tumble 8% as Europe moves to curb Chinese hybrid vehicle imports",
    ],
)
def test_lithium_background_items_are_industry(title):
    label, _ = entities.classify_item({"title": title}, ENTITY)
    assert label == entities.INDUSTRY


@pytest.mark.parametrize(
    "title",
    [
        "Morning Bid: Trillion dollar trade",
        "Bulk Handling Technologies tackles lime processing from end to end",
    ],
)
def test_unrelated_items_are_dropped(title):
    label, _ = entities.classify_item({"title": title}, ENTITY)
    assert label == entities.IRRELEVANT


def test_company_evidence_beats_a_confusable_mention_in_the_same_item():
    item = {
        "title": "Pilbara Minerals guides on Pilbara iron ore market context",
        "snippet": "Pilbara Minerals Limited commented on the region.",
    }
    label, _ = entities.classify_item(item, ENTITY)
    assert label == entities.COMPANY


def test_dedupe_collapses_same_url_and_same_title():
    items = [
        {"title": "PLS Group reschedules report", "url": "https://a.example/x?oc=5", "source": "Kalkine"},
        {"title": "PLS Group reschedules report", "url": "https://a.example/x", "source": "Kalkine"},
        {"title": "PLS Group reschedules report", "url": "https://b.example/y", "source": "Other"},
        {"title": "Different story", "url": "https://c.example/z", "source": "Other"},
    ]
    unique = entities.dedupe_items(items)
    assert len(unique) == 2
    assert unique[0]["url"].startswith("https://a.example/x")