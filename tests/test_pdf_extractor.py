"""PDF extraction: standards, resource/reserve separation and evidence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from minereport.common import config
from minereport.common.models import ResourceCategory
from minereport.pdf.extractor import parse_pdf

FIXTURES = config.FIXTURES_DIR / "pdf"


def _parse(name: str) -> dict:
    return parse_pdf((FIXTURES / name).read_bytes())


def test_jorc_fixture_declares_standard_and_keeps_evidence():
    result = _parse("synthetic_jorc_statement.pdf")
    assert result["standards"] == ["JORC"]
    assert result["confidence"] == "high"
    assert result["standard_evidence"], "a declaration quote is required"
    for row in result["categories"]:
        assert row["page"] >= 1
        assert row["quote"], "every row must carry its literal source line"


def test_ni43101_fixture_declares_standard():
    result = _parse("synthetic_ni43101_statement.pdf")
    assert result["standards"] == ["NI 43-101"]


def test_resources_and_reserves_are_separated():
    result = _parse("synthetic_jorc_statement.pdf")
    resources = [row for row in result["categories"] if row["group"] == "MineralResource"]
    reserves = [row for row in result["categories"] if row["group"] == "OreReserve"]
    assert {row["category"] for row in resources} == {"Measured", "Indicated", "Inferred"}
    assert {row["category"] for row in reserves} == {"Proved", "Probable"}


def test_jorc_fixture_numbers_match_the_source_lines():
    result = _parse("synthetic_jorc_statement.pdf")
    by_category = {
        row["category"]: row
        for row in result["categories"]
        if row["group"] == "MineralResource"
    }
    assert by_category["Measured"]["tonnes"] == 22.1
    assert by_category["Indicated"]["tonnes"] == 315.2
    assert by_category["Inferred"]["tonnes"] == 76.6
    assert by_category["Indicated"]["grade"] == 1.15


def test_prose_mentions_are_not_filed_as_data_rows():
    result = _parse("synthetic_jorc_statement.pdf")
    for row in result["categories"]:
        assert row["quote"].strip().split()[0] in {
            "Measured",
            "Indicated",
            "Inferred",
            "Proved",
            "Probable",
        }
        assert "estimate as outlined" not in row["quote"]


def test_model_rejects_cross_group_category():
    with pytest.raises(ValidationError):
        ResourceCategory(category="Inferred", group="OreReserve", tonnes=1.0)
    with pytest.raises(ValidationError):
        ResourceCategory(category="Proved", group="MineralResource", tonnes=1.0)


def test_empty_document_produces_low_confidence_without_invention():
    from minereport.common.pdfgen import build_text_pdf

    data = build_text_pdf(["Nothing relevant here.", "Just prose."])
    result = parse_pdf(data)
    assert result["standards"] == []
    assert result["categories"] == []
    assert result["confidence"] == "low"
    assert result["notes"], "the absence of data must be explained, not hidden"

