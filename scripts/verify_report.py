"""Check a generated brief against the acceptance criteria for this project.

    uv run python scripts/verify_report.py [reports/<file>.md]

Exits non-zero and prints the failing checks, so "tests pass" can never stand in
for "the artefact is actually correct".
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CONFUSABLE_ENTITIES = ("Rio Tinto", "Pilbara Gold", "ASX:PGL", "iron ore")
FIXTURE_PRICE_TEXT = "1,180 USD/t"


def _section(md: str, start: str, end: str) -> str:
    if start not in md:
        return ""
    tail = md.split(start, 1)[1]
    return tail.split(end, 1)[0] if end in tail else tail


def check(md: str) -> list[tuple[str, bool, str]]:
    company = _section(md, "### 1.1", "### 1.2")
    industry = _section(md, "### 1.2", "## 2.")
    resources = _section(md, "## 2.", "## 3.")
    reserves = _section(md, "## 3.", "## 4.")
    optional = _section(md, "### 4.1", "## 5.")
    links = _section(md, "## 6.", "## 7.")
    news_section = _section(md, "## 1.", "## 2.")
    risk_section = _section(md, "## 5.", "## 6.")
    diagnostics = _section(md, "## 8.", "---")

    # Only the headline table counts as "the summary"; the appendix is checked
    # separately, otherwise its breakdown rows look like duplicates.
    summary_block = _section(resources, "项目级资源量汇总", "附录明细") or resources
    reserve_block = _section(reserves, "项目级矿石储量汇总", "附录明细") or reserves

    def rows_for(block: str, categories: tuple[str, ...]) -> dict[str, int]:
        return {
            category: sum(
                1
                for line in block.splitlines()
                if re.match(rf"\|\s*{category}\b", line.strip())
            )
            for category in categories
        }

    resource_row_counts = rows_for(summary_block, ("Measured", "Indicated", "Inferred"))
    reserve_row_counts = rows_for(reserve_block, ("Proved", "Probable"))
    rows_with_315 = [line for line in resources.splitlines() if "315.2" in line]
    rows_with_76 = [line for line in resources.splitlines() if "76.6" in line]
    link_urls = re.findall(r"\]\((https?://[^)]+)\)", links)

    return [
        ("no 'Thinking mode' 400 text", "Thinking mode does not support" not in md, ""),
        ("news split into company/industry", bool(company) and bool(industry), "1.1/1.2 sections missing"),
        (
            "company section has no confusable entity",
            not any(term in company for term in CONFUSABLE_ENTITIES),
            f"found one of {CONFUSABLE_ENTITIES} in 1.1",
        ),
        (
            "company items carry an explicit company mention",
            ("Pilbara Minerals" in company) or ("PLS" in company) or ("Pilgangoora" in company),
            "no company/project evidence in 1.1",
        ),
        (
            "headline summary rows are unique",
            all(count == 1 for count in resource_row_counts.values()),
            f"resource row counts: {resource_row_counts}",
        ),
        (
            "315.2 Mt and 76.6 Mt appear in exactly one row",
            len(rows_with_315) == 1 and len(rows_with_76) == 1,
            f"rows with 315.2={len(rows_with_315)}, rows with 76.6={len(rows_with_76)}",
        ),
        (
            "reserve rows are unique",
            all(count == 1 for count in reserve_row_counts.values()),
            f"reserve row counts: {reserve_row_counts}",
        ),
        (
            "appendix detail is separated and labelled",
            "附录明细" in resources and "不可与汇总值相加" in resources,
            "appendix rows are not separated from the summary",
        ),
        (
            "disclosure date caveat present",
            ("并非当前最新披露" in md) or ("不是当前最新" in md),
            "the 2023 statement is not flagged as non-current",
        ),
        (
            "strict-live masks the unavailable commodity",
            "DATA_MISSING" in optional and FIXTURE_PRICE_TEXT not in md,
            "a fixture price leaked into a strict-live report",
        ),
        (
            "news pipeline closes in the report",
            ("闭合校验" in md and "不一致 ✗" not in md),
            "the news stage counts do not add up",
        ),
        (
            "news items carry an evidence level and a stable id",
            ("证据：" in news_section and "news_id：" in news_section),
            "section 1 lacks 证据 / news_id labels",
        ),
        (
            "strict-live never calls a fixture a used price",
            ("离线样例" not in optional and "离线样例" not in risk_section),
            "fixture wording leaked into section 4.1 or 5",
        ),
        (
            "resource report shows its three dates",
            all(key in resources for key in ("effective_date", "announced_date", "fetched_at")),
            "section 2 is missing effective/announced/fetched dates",
        ),
        (
            "risk claims link back to evidence",
            (
                "结论—证据对照" in md
                and "claim_id" in md
                and re.search(r"`(price_|resource_|nid-)", md) is not None
            ),
            "structured claims are missing or unlinked",
        ),
        (
            "pre-render quality checks are reported",
            "质量校验·" in diagnostics,
            "section 8 does not list the quality checks",
        ),
        (
            "news summaries are Chinese",
            bool(re.search(r"要点：[^\n]*[\u4e00-\u9fff]", md)) or "未评估" in md,
            "no Chinese news summary found",
        ),
        (
            "risk judgement is Chinese",
            bool(re.search(r"### 5\.2[\s\S]{0,4000}[\u4e00-\u9fff]", md)),
            "no Chinese judgement found",
        ),
        ("markdown links are well formed", "](" in md and not re.search(r"\]\(\s*\)", md), "empty link target"),
        ("source links are unique", len(link_urls) == len(set(link_urls)), "duplicate source links"),
        (
            "diagnostics never appear inside the news section",
            "运行诊断" in md and "LLM step failed" not in news_section and "调用失败" not in news_section,
            "a model/runtime error leaked into section 1",
        ),
        ("diagnostics section exists", bool(diagnostics), "section 8 missing"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", default=None, help="markdown file to check")
    args = parser.parse_args(argv)

    path = Path(args.report) if args.report else max(
        (ROOT / "reports").glob("*.md"), key=lambda item: item.stat().st_mtime
    )
    md = path.read_text(encoding="utf-8")
    results = check(md)

    print(f"report: {path}")
    failures = 0
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  <- {detail}" if not ok else ""))
        failures += 0 if ok else 1
    print(f"\n{len(results) - failures}/{len(results)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())