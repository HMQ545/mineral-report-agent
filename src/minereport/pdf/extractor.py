"""Extraction of mineral resource / ore reserve statements from PDF text.

Everything is evidence based: a row is only reported together with the page it
came from and the literal line it was read from. Nothing is invented.
"""

from __future__ import annotations

import io
import re
from typing import Any

import pdfplumber

MAX_PAGES = 160

CATEGORY_NAMES = {
    "measured": "Measured",
    "indicated": "Indicated",
    "inferred": "Inferred",
    "proved": "Proved",
    "probable": "Probable",
}

GROUP_FOR_CATEGORY = {
    "Measured": "MineralResource",
    "Indicated": "MineralResource",
    "Inferred": "MineralResource",
    "Proved": "OreReserve",
    "Probable": "OreReserve",
}

ROW_RE = re.compile(
    r"^\s*(Measured|Indicated|Inferred|Proved|Probable|MEASURED|INDICATED|INFERRED|PROVED|PROBABLE)\b(.*)$"
)
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# A data row is: a category label followed by nothing but numbers.
NUMERIC_ONLY_RE = re.compile(r"^\s*\d[\d,]*(?:\.\d+)?(?:\s+\d[\d,]*(?:\.\d+)?)*\s*$")
FOOTNOTE_RE = re.compile(r"[\s*\u2020\u2021]*(?:\(?[a-z]\)?)?\s*$")
PAREN_UNIT_RE = re.compile(r"\(([^()]{1,12})\)")
VALID_UNIT_RE = re.compile(r"^[A-Za-z%][A-Za-z0-9%./ ]{0,9}$")

# Standards are only declared by an explicit statement, never by a bare
# mention: the Pilbara release references other companies' NI 43-101 reports
# inside its industry comparison appendix.
JORC_DECLARATIONS = (
    re.compile(r"JORC Code\s*\(?\s*2012", re.I),
    re.compile(r"JORC\s*2012\s+(?:Ore Reserve|Mineral Resource|Code)", re.I),
    re.compile(r"in accordance with the JORC Code", re.I),
    re.compile(r"Australasian Code for Reporting of (?:Exploration Results,\s*)?Mineral Resources", re.I),
    re.compile(r"reported (?:in|under) compliance with the JORC", re.I),
)
NI43101_DECLARATIONS = (
    re.compile(r"NI\s*43[\s-]?101\s+Technical Report", re.I),
    re.compile(r"National Instrument\s*43[\s-]?101", re.I),
    re.compile(r"prepared in accordance with NI\s*43[\s-]?101", re.I),
    re.compile(r"in accordance with NI\s*43[\s-]?101", re.I),
    re.compile(r"Standards of Disclosure for Mineral Projects", re.I),
    re.compile(r"Form\s*43[\s-]?101F1", re.I),
)
DECLARATION_PAGE_LIMIT = 8

ENTITY_RE = re.compile(
    r"([A-Z][A-Za-z0-9&.,'’\-]*(?:\s+[A-Z][A-Za-z0-9&.,'’\-]*){0,4}\s+"
    r"(?:Limited|Ltd\.?|Inc\.?|Incorporated|Corporation|Corp\.?|Pty\.?\s+Ltd\.?))"
)
PROJECT_RE = re.compile(
    r"([A-Z][A-Za-z0-9'’\-]+(?:\s+[A-Z][A-Za-z0-9'’\-]+){0,3}\s+"
    r"(?:Operation|Project|Mine|Deposit|Property|Complex))"
)
REPORT_DATE_RE = re.compile(
    r"(?:as at|dated|effective)\s+(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", re.IGNORECASE
)
DATE_RE = re.compile(r"\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b")
COMMODITY_PATTERNS = (
    (re.compile(r"Li\s*2\s*O", re.I), "Li2O"),
    (re.compile(r"\bspodumene\b", re.I), "Spodumene"),
    (re.compile(r"Ta\s*2\s*O\s*5", re.I), "Ta2O5"),
)


def _normalise_units(units: list[str]) -> list[str]:
    return [unit.strip() for unit in units if VALID_UNIT_RE.match(unit.strip())]


def _role_map(units: list[str]) -> list[str]:
    """Give each numeric column a role using its unit (position independent)."""

    roles: list[str] = []
    mass_seen = 0
    for unit in units:
        low = unit.lower()
        if low in {"mt", "kt", "t", "dmt", "million tonnes", "million t"}:
            mass_seen += 1
            roles.append("tonnes" if mass_seen == 1 else "contained")
        elif low.startswith("%"):
            roles.append("grade")
        else:
            roles.append("extra")
    return roles


def _page_lines(page: Any) -> list[str]:
    text = page.extract_text() or ""
    return [line.rstrip() for line in text.splitlines()]


def _scan_patterns(
    patterns: tuple[re.Pattern[str], ...], pages: list[str], offset: int = 0
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for index, text in enumerate(pages):
        flat = re.sub(r"\s+", " ", text)
        for pattern in patterns:
            match = pattern.search(flat)
            if not match:
                continue
            hits.append(
                {
                    "page": offset + index + 1,
                    "matched": match.group(0),
                    "quote": flat[max(0, match.start() - 110) : match.end() + 110].strip(),
                }
            )
            break
    return hits


def _detect_standards(pages: list[str]) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Declarations only, and only in the front matter.

    Anything further inside the document is recorded as a mere mention: the
    Pilbara release cites other companies' NI 43-101 reports in an industry
    comparison appendix, which is not a declaration about itself.
    """

    front = pages[:DECLARATION_PAGE_LIMIT]
    rest = pages[DECLARATION_PAGE_LIMIT:]
    standards: list[str] = []
    evidence: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []

    for label, patterns in (("JORC", JORC_DECLARATIONS), ("NI 43-101", NI43101_DECLARATIONS)):
        front_hits = _scan_patterns(patterns, front)
        if front_hits:
            standards.append(label)
            evidence.append({**front_hits[0], "scope": "front matter declaration"})
        for hit in _scan_patterns(patterns, rest, offset=DECLARATION_PAGE_LIMIT):
            mentions.append({**hit, "standard": label, "scope": "mention outside front matter"})
    return standards, evidence, mentions


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _best_match(pattern: re.Pattern[str], text: str) -> str | None:
    """Most frequent match wins; longer spelling breaks ties."""

    found = [match.strip() for match in pattern.findall(text)]
    if not found:
        return None
    counts: dict[str, int] = {}
    for item in found:
        counts[item] = counts.get(item, 0) + 1
    return sorted(counts, key=lambda item: (counts[item], len(item)), reverse=True)[0]


def _assign_statement_scope(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Split headline statement rows from appendix breakdown rows.

    Real releases repeat the resource table in appendices broken down by deposit.
    Those rows are NOT additive to the headline table, so the daily brief must
    show the project-level summary only and mark the rest as appendix detail.
    The headline statement is the earliest page carrying rows for that group.
    """

    primary_pages: dict[str, int] = {}
    for group in ("MineralResource", "OreReserve"):
        pages = [row["page"] for row in rows if row["group"] == group]
        if pages:
            primary_pages[group] = min(pages)

    for row in rows:
        primary = primary_pages.get(row["group"])
        heading = (row.get("heading") or "").lower()
        in_appendix = "appendix" in heading
        if primary is not None and row["page"] == primary and not in_appendix:
            row["statement_scope"] = "project_summary"
        else:
            row["statement_scope"] = "appendix_detail"
        row["related_primary_page"] = primary
    return rows, primary_pages


def parse_pdf(pdf_bytes: bytes, *, max_pages: int = MAX_PAGES) -> dict[str, Any]:
    """Parse a PDF and return the structured, evidence-backed result."""

    rows: list[dict[str, Any]] = []
    pages_text: list[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        page_count = len(document.pages)
        for page_index, page in enumerate(document.pages[:max_pages], start=1):
            lines = _page_lines(page)
            pages_text.append("\n".join(lines))
            heading = ""
            units: list[str] = []

            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                low = stripped.lower()
                if ("mineral resource" in low or "ore reserve" in low) and len(stripped) < 200:
                    heading = stripped

                candidates = _normalise_units(PAREN_UNIT_RE.findall(stripped))
                if len(candidates) >= 2:
                    units = candidates

                match = ROW_RE.match(stripped)
                if not match:
                    continue
                category = CATEGORY_NAMES[match.group(1).lower()]
                remainder = FOOTNOTE_RE.sub("", match.group(2))
                if not NUMERIC_ONLY_RE.match(remainder):
                    # Prose such as "Inferred Mineral Resource estimate as outlined
                    # in Table 1" is not a data row and must never be filed as one.
                    continue
                numbers = NUMBER_RE.findall(remainder)
                if len(numbers) < 2:
                    continue
                values = [float(number.replace(",", "")) for number in numbers]
                # Units are only trustworthy when the header has exactly one unit
                # per numeric column; otherwise the table has a different shape
                # and attaching units would mislabel the numbers.
                aligned = len(units) == len(values)
                roles = _role_map(units) if aligned else []

                columns: list[dict[str, Any]] = []
                for index, value in enumerate(values):
                    role = roles[index] if index < len(roles) else (
                        "tonnes" if index == 0 else "grade" if index == 1 else "extra"
                    )
                    columns.append(
                        {
                            "index": index,
                            "role": role,
                            "unit": units[index] if aligned else None,
                            "value": value,
                        }
                    )

                def first_of(role: str, field: str) -> Any:
                    for column in columns:
                        if column["role"] == role:
                            return column[field]
                    return None

                rows.append(
                    {
                        "category": category,
                        "group": GROUP_FOR_CATEGORY[category],
                        "tonnes": first_of("tonnes", "value"),
                        "tonnes_unit": first_of("tonnes", "unit"),
                        "grade": first_of("grade", "value"),
                        "grade_unit": first_of("grade", "unit"),
                        "contained": first_of("contained", "value"),
                        "contained_unit": first_of("contained", "unit"),
                        "columns": columns,
                        "page": page_index,
                        "quote": stripped,
                        "heading": heading,
                        "confidence": "high" if heading else "medium",
                    }
                )

    rows, primary_pages = _assign_statement_scope(rows)

    full_text = "\n".join(pages_text)
    standards, standard_evidence, standard_mentions = _detect_standards(pages_text)
    first_page = pages_text[0] if pages_text else ""
    flattened_first = re.sub(r"\s+", " ", first_page)

    commodity = None
    for pattern, name in COMMODITY_PATTERNS:
        if pattern.search(full_text):
            commodity = name
            break

    report_date = _first_match(REPORT_DATE_RE, flattened_first) or _first_match(DATE_RE, flattened_first)
    entity = _best_match(ENTITY_RE, flattened_first)
    project = _best_match(PROJECT_RE, flattened_first) or _best_match(PROJECT_RE, re.sub(r"\s+", " ", full_text))

    if standards and rows:
        confidence = "high"
    elif standards or rows:
        confidence = "medium"
    else:
        confidence = "low"

    notes: list[str] = []
    if not standards:
        notes.append("未找到 NI 43-101 / JORC 的显式声明，未对报告标准下结论。")
    if not rows:
        notes.append("未在文本层提取到 Measured/Indicated/Inferred/Proved/Probable 的数值行（可能为扫描件或仅有图片表格）。")

    scope_counts = {
        "project_summary": sum(1 for row in rows if row["statement_scope"] == "project_summary"),
        "appendix_detail": sum(1 for row in rows if row["statement_scope"] == "appendix_detail"),
    }

    return {
        "page_count": page_count,
        "primary_pages": primary_pages,
        "scope_counts": scope_counts,
        "pages_parsed": len(pages_text),
        "standards": standards,
        "standard_evidence": standard_evidence,
        "standard_mentions": standard_mentions,
        "confidence": confidence,
        "report_date": report_date,
        "reporter_entity": entity,
        "project_name": project,
        "commodity": commodity,
        "categories": rows,
        "notes": notes,
    }


def summarise_categories(categories: list[dict[str, Any]]) -> str:
    """Small human readable summary used in logs and the report."""

    if not categories:
        return "no resource/reserve rows extracted"
    parts = []
    for row in categories:
        tonnes = row.get("tonnes")
        grade = row.get("grade")
        unit = row.get("tonnes_unit") or "Mt"
        parts.append(f"{row['category']} {tonnes}{unit} @ {grade}{row.get('grade_unit') or '%'}")
    return "; ".join(parts)


__all__ = ["GROUP_FOR_CATEGORY", "parse_pdf", "summarise_categories"]

