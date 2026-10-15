"""PDF scope: the headline summary must not be polluted by appendix detail."""

from __future__ import annotations

from pathlib import Path

import pytest

from minereport.common import config
from minereport.pdf import server as pdf_server
from minereport.pdf.extractor import _assign_statement_scope, parse_pdf

REAL_PDF = config.CACHE_DIR / "pdf" / "pilbara-2023-mineral-resource.pdf"


def _rows(spec):
    return [
        {"category": category, "group": group, "page": page, "tonnes": tonnes, "heading": ""}
        for category, group, page, tonnes in spec
    ]


def test_scope_assignment_picks_the_earliest_page_per_group():
    rows = _rows(
        [
            ("Measured", "MineralResource", 2, 22.1),
            ("Indicated", "MineralResource", 2, 315.2),
            ("Indicated", "MineralResource", 56, 131.8),
            ("Inferred", "MineralResource", 56, 20.1),
            ("Proved", "OreReserve", 3, 19.1),
            ("Probable", "OreReserve", 3, 195.1),
        ]
    )
    scoped, primary = _assign_statement_scope(rows)
    assert primary == {"MineralResource": 2, "OreReserve": 3}
    summary = [row for row in scoped if row["statement_scope"] == "project_summary"]
    appendix = [row for row in scoped if row["statement_scope"] == "appendix_detail"]
    assert [row["tonnes"] for row in summary] == [22.1, 315.2, 19.1, 195.1]
    assert [row["tonnes"] for row in appendix] == [131.8, 20.1]
    assert all(row["related_primary_page"] in (2, 3) for row in scoped)


def test_appendix_heading_always_marks_detail():
    rows = _rows([("Indicated", "MineralResource", 2, 1.0)])
    rows[0]["heading"] = "Appendix 3 - breakdown by deposit"
    scoped, _ = _assign_statement_scope(rows)
    assert scoped[0]["statement_scope"] == "appendix_detail"


def test_synthetic_fixture_has_a_single_summary_row_per_category():
    data = (config.FIXTURES_DIR / "pdf" / "synthetic_jorc_statement.pdf").read_bytes()
    parsed = parse_pdf(data)
    summary = [row for row in parsed["categories"] if row["statement_scope"] == "project_summary"]
    keys = [(row["category"], row["tonnes"]) for row in summary]
    assert len(keys) == len(set(keys)), "summary rows must be unique"


@pytest.mark.skipif(not REAL_PDF.exists(), reason="real Pilbara PDF not cached on this machine")
def test_real_pilbara_pdf_summary_is_not_duplicated():
    result = pdf_server.extract_resources()
    data = result["data"]

    assert data["counts"]["mineral_resource_rows"] == 3
    assert data["counts"]["ore_reserve_rows"] == 2
    assert data["counts"]["appendix_resource_rows"] > 0

    resources = [(row["category"], row["tonnes"]) for row in data["mineral_resources"]]
    assert resources == [("Measured", 22.1), ("Indicated", 315.2), ("Inferred", 76.6)]
    reserves = [(row["category"], row["tonnes"]) for row in data["ore_reserves"]]
    assert reserves == [("Proved", 19.1), ("Probable", 195.1)]

    # the appendix must never re-introduce the headline figures
    appendix = {(row["category"], row["tonnes"]) for row in data["appendix"]["mineral_resources"]}
    assert ("Indicated", 315.2) not in appendix
    assert ("Inferred", 76.6) not in appendix
    assert all(row["statement_scope"] == "appendix_detail" for row in data["appendix"]["mineral_resources"])
    assert data["disclosure_note"]