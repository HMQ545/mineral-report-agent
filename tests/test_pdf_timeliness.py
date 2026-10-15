"""PDF resource reports: three dates, newest verified entry, registry hygiene."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from minereport.agent.graph import resolve_source
from minereport.pdf import registry

SOURCES = Path(__file__).resolve().parents[1] / "data" / "sources" / "pdf_sources.json"


def test_registry_is_clean_utf8_without_corrupted_notes():
    raw = SOURCES.read_text(encoding="utf-8")
    assert "\ufffd" not in raw
    data = json.loads(raw)
    for source in data["sources"]:
        note = str(source.get("hosting_note") or "")
        assert "??" not in note, f"{source['key']} hosting_note is corrupted: {note!r}"
        assert not note.endswith("?")


def test_source_metadata_separates_the_three_dates():
    pilbara = registry.lookup("https://minedocs.com/25/Pilbara-Minerals-MR-08242023.pdf")
    meta = registry.source_metadata(pilbara, today=date(2026, 10, 15))
    assert meta["effective_date"] == "2023-06-30"
    assert meta["announced_date"] == "2023-08-24"
    assert meta["fetched_at"] == "2026-10-14"
    assert meta["age_days"] == (date(2026, 10, 15) - date(2023, 6, 30)).days
    assert meta["is_historical"] is True


def test_resolve_source_marks_the_2023_document_as_historical():
    source, notes = resolve_source("给我生成一份关于 Pilbara 锂矿的今日简报")
    assert source["key"] == "pilbara-2023-mineral-resource"
    assert source["effective_date"] == "2023-06-30"
    assert source["announced_date"] == "2023-08-24"
    assert source["is_historical"] is True
    assert any("历史数据" in note for note in notes)
    assert any("注册表" in note for note in notes)


def test_newest_verified_entry_wins_regardless_of_file_order(monkeypatch):
    older = {
        "key": "old", "role": "primary-deliverable", "keywords": ["pilgangoora"],
        "effective_date": "2020-01-01", "announced_date": "2020-02-01", "sha256": "a",
    }
    newer = {
        "key": "new", "role": "primary-deliverable", "keywords": ["pilgangoora"],
        "effective_date": "2026-01-01", "announced_date": "2026-02-01", "sha256": "b",
    }
    for order in ([older, newer], [newer, older]):
        monkeypatch.setattr(registry, "all_sources", lambda order=order: list(order))
        selected = registry.select_source("Pilgangoora 资源量")
        assert selected["key"] == "new", order
        assert selected["announced_date"] == "2026-02-01"
        assert "另" in selected["selection_reason"] or "最新" in selected["selection_reason"]


def test_unmatched_query_falls_back_to_the_primary_deliverable(monkeypatch):
    monkeypatch.setattr(registry, "all_sources", lambda: [])
    assert registry.select_source("nothing registered") is None
    source, notes = resolve_source("nothing registered")
    assert source is None and notes