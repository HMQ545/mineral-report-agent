"""Hard gate: prove every registered source is really reachable and parseable.

``--update`` records (or refreshes) sha256/size/timestamp in the registry.
Without ``--update`` any mismatch is a failure - a source that changed is a
finding, not something to silently paper over.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from datetime import date
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minereport.common import cache, config  # noqa: E402
from minereport.common.http import fetch_bytes  # noqa: E402
from minereport.pdf.extractor import parse_pdf  # noqa: E402
from minereport.pdf.safety import assert_pdf  # noqa: E402

REGISTRY = config.SOURCES_DIR / "pdf_sources.json"


def _page_texts(data: bytes, limit: int = 200) -> list[str]:
    with pdfplumber.open(io.BytesIO(data)) as document:
        return [(page.extract_text() or "") for page in document.pages[:limit]]


def verify_source(source: dict, *, update: bool, timeout: float) -> dict:
    result = {"key": source["key"], "url": source["url"], "ok": False, "problems": [], "notes": []}

    try:
        data, final_url = fetch_bytes(source["url"], max_bytes=25 * 1024 * 1024, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        result["problems"].append(f"download failed: {type(exc).__name__}: {exc}")
        return result

    result["final_url"] = final_url
    result["bytes"] = len(data)

    try:
        digest = assert_pdf(data)
    except Exception as exc:  # noqa: BLE001
        result["problems"].append(f"not a valid PDF: {exc}")
        return result

    result["sha256"] = digest
    recorded = source.get("sha256")
    if update or not recorded:
        source["sha256"] = digest
        source["bytes"] = len(data)
        source["verified_at"] = date.today().isoformat()
        result["notes"].append("sha256 recorded")
    elif recorded != digest:
        result["problems"].append(f"sha256 changed: registry={recorded[:16]}.. actual={digest[:16]}..")

    cache.write_bytes("pdf", source["key"], ".pdf", data, source_url=source["url"], sha256=digest)

    texts = _page_texts(data)
    joined = "\n".join(texts)
    for keyword in source.get("expect_keywords", []):
        if keyword.lower() not in joined.lower():
            result["problems"].append(f"expected keyword {keyword!r} not found in the document text")

    parsed = parse_pdf(data)
    result["standards"] = parsed["standards"]
    result["rows"] = len(parsed["categories"])

    expected_standard = source.get("standard")
    if expected_standard and expected_standard not in parsed["standards"]:
        result["problems"].append(
            f"expected standard {expected_standard!r} was not declared in the front matter "
            f"(found: {parsed['standards']})"
        )

    found_categories = {row["category"] for row in parsed["categories"]}
    for category in source.get("expect_categories", []):
        if category not in found_categories:
            result["problems"].append(f"expected category {category!r} was not extracted")

    if source.get("role") == "primary-deliverable":
        resources = [row for row in parsed["categories"] if row["group"] == "MineralResource"]
        reserves = [row for row in parsed["categories"] if row["group"] == "OreReserve"]
        if not resources:
            result["problems"].append("primary source produced no MineralResource rows")
        if not reserves:
            result["problems"].append("primary source produced no OreReserve rows")

    result["ok"] = not result["problems"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="record/refresh sha256 in the registry")
    parser.add_argument("--only", action="append", default=None, help="limit to these source keys")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    sources = registry["sources"]
    if args.only:
        sources = [source for source in sources if source["key"] in set(args.only)]

    failures = 0
    for source in sources:
        print(f"== {source['key']} (role={source.get('role')}, standard={source.get('standard')})")
        result = verify_source(source, update=args.update, timeout=args.timeout)
        digest = result.get("sha256", "")
        print(f"   url      : {source['url']}")
        print(f"   bytes    : {result.get('bytes')}")
        print(f"   sha256   : {digest[:32]}{'...' if digest else ''}")
        print(f"   standards: {result.get('standards')}  rows={result.get('rows')}")
        for note in result["notes"]:
            print(f"   note     : {note}")
        for problem in result["problems"]:
            print(f"   PROBLEM  : {problem}")
        print(f"   => {'PASS' if result['ok'] else 'FAIL'}")
        failures += 0 if result["ok"] else 1

    if args.update:
        REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nregistry updated: {REGISTRY}")

    print(f"\n{len(sources) - failures}/{len(sources)} sources verified")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

