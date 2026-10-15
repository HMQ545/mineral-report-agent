"""Stage 1: intent recognition, slot extraction, filling and validation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from minereport.agent.intent.classifier import classify_by_rules
from minereport.agent.intent.extractor import extract_slots
from minereport.agent.intent.filling import fill_slots
from minereport.agent.intent.schemas import Intent, TurnAction
from minereport.agent.intent.validator import validate_slots


def _classify(text: str, pending=None):
    slots, _ = extract_slots(text)
    return classify_by_rules(text, pending_tasks=pending or [], extracted=slots)


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("给我生成一份关于 Pilbara 锂矿的今日简报", Intent.DAILY_REPORT),
        ("查一下 Pilbara 最近的新闻", Intent.NEWS_SEARCH),
        ("Pilbara 有多少矿产资源？", Intent.RESOURCE_QUERY),
        ("碳酸锂今天什么价格", Intent.PRICE_QUERY),
        ("帮我查一下最近 30 天的铜价走势", Intent.TREND_QUERY),
        ("你好", Intent.GENERAL_CHAT),
        ("Python 的 asyncio 是什么？", Intent.GENERAL_CHAT),
    ],
)
def test_seven_intents_are_recognised(text, intent):
    assert _classify(text).intent == intent


def test_ambiguous_lithium_price_needs_a_product():
    """“锂价”is a price query but the product is ambiguous -> ask, do not guess."""

    result = _classify("帮我查一下锂价")
    assert result.intent == Intent.PRICE_QUERY

    slots, notes = extract_slots("帮我查一下锂价")
    assert "commodity" not in slots
    assert any("歧义" in note for note in notes)

    resolution = validate_slots(Intent.PRICE_QUERY, slots)
    assert "commodity" in resolution.missing


def test_explicit_products_beat_the_bare_lithium_token():
    for text, expected in (
        ("碳酸锂最近 30 天走势", "lithium_carbonate"),
        ("锂辉石精矿最近一个月走势", "lithium_spodumene"),
        ("LME 铜价走势", "copper"),
    ):
        slots, _ = extract_slots(text)
        assert slots["commodity"] == expected


def test_relative_and_absolute_time_slots():
    slots, _ = extract_slots("碳酸锂最近30天走势")
    assert slots["days"] == 30

    slots, _ = extract_slots("碳酸锂最近一个月走势")
    assert slots["days"] == 30

    slots, _ = extract_slots("昨天的铜价")
    expected = (date.today() - timedelta(days=1)).isoformat()
    assert slots["date"] == expected

    slots, _ = extract_slots("查一下 2026-09-01 的铜价")
    assert slots["date"] == "2026-09-01"


def test_company_and_project_resolution_from_registry():
    slots, _ = extract_slots("Pilgangoora 的资源量")
    assert slots["company"] == "Pilbara Minerals Limited"
    assert slots["project"].lower() == "pilgangoora"


def test_cancel_continue_and_restart_rules():
    assert _classify("先不查了").action == TurnAction.CANCEL_TASK
    assert _classify("继续刚才的锂价查询").action == TurnAction.CONTINUE_TASK
    assert _classify("重新查一次铜价").action == TurnAction.NEW_TASK


def test_cancel_plus_another_request_keeps_both():
    result = _classify("先不查了，解释一下 Python 协程")
    assert result.action == TurnAction.GENERAL_CHAT
    assert result.also_cancel is True


def test_slot_completion_is_detected_when_a_task_is_waiting():
    pending = [{"task_id": "t1", "intent": "trend_query", "status": "waiting_for_slots"}]
    result = _classify("碳酸锂，最近 30 天", pending=pending)
    assert result.action == TurnAction.SLOT_COMPLETION


def test_fill_slots_precedence_and_defaults():
    inherited = {"commodity": "copper", "days": 7}
    extracted = {"days": 30}
    merged, sources = fill_slots(Intent.TREND_QUERY, extracted, inherited)
    assert merged["commodity"] == "copper" and sources["commodity"] == "task-context"
    assert merged["days"] == 30 and sources["days"] == "user-input"

    merged, sources = fill_slots(Intent.TREND_QUERY, {}, {})
    assert merged["days"] == 30 and sources["days"] == "default"


def test_validation_rejects_untrusted_sources_but_keeps_good_slots():
    resolution = validate_slots(Intent.TREND_QUERY, {"commodity": "lithium_carbonate", "days": 0})
    assert any(err.startswith("days") for err in resolution.errors)
    assert resolution.slots.commodity == "lithium_carbonate"
    assert "days" in resolution.missing

    resolution = validate_slots(Intent.RESOURCE_QUERY, {"company": "ACME Mining Ltd"})
    assert any("可信实体注册表" in err for err in resolution.errors)

    resolution = validate_slots(Intent.RESOURCE_QUERY, {"company": "Pilbara Minerals Limited", "pdf_ref": "https://evil.example/x.pdf"})
    assert any("可信资源注册表" in err for err in resolution.errors)


def test_validation_accepts_registry_backed_values():
    resolution = validate_slots(
        Intent.RESOURCE_QUERY,
        {"company": "pilbara minerals limited", "project": "Pilgangoora"},
    )
    assert resolution.errors == []
    assert resolution.slots.company == "Pilbara Minerals Limited"
    assert resolution.is_complete