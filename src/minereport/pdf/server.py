"""mineral-pdf-mcp: ``extract_resources`` over registered public reports."""

from __future__ import annotations

import argparse
import hashlib
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..common.cli import add_transport_args, run_server
from ..common.fixtures import FixtureNotFoundError
from ..common.health import register_health
from ..common.http import ResponseTooLargeError, UrlNotAllowedError
from ..common.provenance import Provenance, envelope, error_envelope
from . import registry
from .extractor import parse_pdf, summarise_categories
from .safety import NotAPdfError, PdfInputError, assert_pdf, resolve_pdf_bytes

SERVICE = "mineral-pdf-mcp"
DEFAULT_PORT = 8102
MAX_PDF_BYTES = 25 * 1024 * 1024

INSTRUCTIONS = (
    "Extracts Measured/Indicated/Inferred mineral resources and Proved/Probable ore "
    "reserves from public NI 43-101 / JORC PDF statements, keeping page-level evidence. "
    "Resources and reserves are never mixed and numbers are never invented."
)


def _allowed_hosts() -> tuple[str, ...] | None:
    raw = os.environ.get("MINEREPORT_PDF_ALLOWED_HOSTS", "").strip()
    if not raw:
        return None
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def list_sources() -> dict[str, Any]:
    """List the human-verified report sources this server knows about."""

    sources = [
        {
            "key": source.get("key"),
            "role": source.get("role"),
            "standard": source.get("standard"),
            "reporter_entity": source.get("reporter_entity"),
            "project_name": source.get("project_name"),
            "report_date": source.get("report_date"),
            "url": source.get("url"),
            "sha256": source.get("sha256"),
            "hosting_note": source.get("hosting_note"),
        }
        for source in registry.all_sources()
    ]
    return envelope(
        {"registry_path": str(registry.REGISTRY_PATH), "sources": sources},
        provenance=Provenance.LIVE,
        source="pdf source registry",
        source_url=None,
        notes=["注册表中每个条目都经过 verify_sources.py 校验。"],
    )


def extract_resources(pdf_url: str = "", refresh: bool = False) -> dict[str, Any]:
    """Extract resource/reserve statements from a PDF URL, file or fixture.

    ``refresh=True`` forces a fresh download so that a strict live run never
    silently answers from a local cache.
    """

    reference = (pdf_url or "").strip()
    if not reference:
        default = registry.default_source(role="primary-deliverable")
        if not default:
            return error_envelope(
                "no pdf_url given and no primary-deliverable source is registered",
                source="pdf registry",
                code="NO_DEFAULT_SOURCE",
            )
        reference = str(default["url"])

    try:
        resolved = resolve_pdf_bytes(
            reference,
            allow_hosts=_allowed_hosts(),
            max_bytes=MAX_PDF_BYTES,
            refresh=refresh,
        )
    except UrlNotAllowedError as exc:
        return error_envelope(
            f"URL rejected by download policy: {exc}",
            source="pdf download policy",
            source_url=reference,
            code="URL_NOT_ALLOWED",
        )
    except ResponseTooLargeError as exc:
        return error_envelope(
            f"PDF exceeds the {MAX_PDF_BYTES} byte limit: {exc}",
            source="pdf download policy",
            source_url=reference,
            code="TOO_LARGE",
        )
    except FixtureNotFoundError as exc:
        return error_envelope(
            f"fixture not found: {exc}",
            source="repository fixture",
            source_url=reference,
            code="FIXTURE_NOT_FOUND",
        )
    except PdfInputError as exc:
        return error_envelope(
            f"invalid input: {exc}",
            source="pdf input",
            source_url=reference,
            code="INVALID_INPUT",
        )
    except Exception as exc:  # noqa: BLE001
        return error_envelope(
            f"download failed: {type(exc).__name__}: {exc}",
            source="pdf download",
            source_url=reference,
            code="DOWNLOAD_FAILED",
        )

    data = resolved["data"]
    assert isinstance(data, bytes)
    try:
        sha256 = assert_pdf(data)
    except NotAPdfError as exc:
        return error_envelope(
            f"not a PDF: {exc}",
            source="pdf validation",
            source_url=str(resolved["source_url"]),
            code="NOT_A_PDF",
            notes=list(resolved["notes"]),  # type: ignore[arg-type]
        )

    parsed = parse_pdf(data)
    registered = registry.lookup(str(resolved["source_url"])) or next(
        (item for item in registry.all_sources() if item.get("sha256") == sha256), None
    )

    notes = list(resolved["notes"])  # type: ignore[arg-type]
    if registered:
        notes.append(
            f"命中源注册表条目 {registered.get('key')}（role={registered.get('role')}）"
        )
        if registered.get("hosting_note"):
            notes.append(str(registered["hosting_note"]))
    else:
        notes.append("该 URL 不在源注册表中，元数据完全来自文档自身解析结果。")

    metadata = registered or {}
    categories = parsed["categories"]
    summary_rows = [row for row in categories if row.get("statement_scope") == "project_summary"]
    detail_rows = [row for row in categories if row.get("statement_scope") == "appendix_detail"]

    def dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A summary figure must appear once; the first evidence page is kept."""

        seen: set[tuple] = set()
        unique: list[dict[str, Any]] = []
        for row in rows:
            key = (row.get("category"), row.get("tonnes"), row.get("grade"), row.get("contained"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    resources = dedupe([row for row in summary_rows if row["group"] == "MineralResource"])
    reserves = dedupe([row for row in summary_rows if row["group"] == "OreReserve"])

    # Appendix tables often end with their own "total" rows that repeat the
    # headline figures verbatim (verified on the real Pilbara release: p56
    # repeats Indicated 315.2 and Inferred 76.6). Those duplicates are dropped
    # from the detail list so no figure is ever shown twice.
    summary_keys = {
        (row.get("category"), row.get("tonnes"), row.get("grade")) for row in (resources + reserves)
    }

    def detail_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            row
            for row in rows
            if (row.get("category"), row.get("tonnes"), row.get("grade")) not in summary_keys
        ]

    detail_candidates = [row for row in detail_rows if row["group"] == "MineralResource"]
    resource_details = detail_only(detail_candidates)
    reserve_candidates = [row for row in detail_rows if row["group"] == "OreReserve"]
    reserve_details = detail_only(reserve_candidates)
    duplicates_removed = (
        len(detail_candidates) - len(resource_details) + len(reserve_candidates) - len(reserve_details)
    )

    payload = {
        "document": {
            "input": reference,
            "resolved_url": resolved["source_url"],
            "sha256": sha256,
            "bytes": len(data),
            "page_count": parsed["page_count"],
        },
        "standards": parsed["standards"],
        "standard_evidence": parsed["standard_evidence"],
        "standard_mentions": parsed["standard_mentions"],
        "confidence": parsed["confidence"],
        "reporter_entity": metadata.get("reporter_entity") or parsed["reporter_entity"],
        "project_name": metadata.get("project_name") or parsed["project_name"],
        "report_date": metadata.get("report_date") or parsed["report_date"],
        "effective_date": (
            metadata.get("effective_date")
            or metadata.get("report_date")
            or parsed.get("report_date")
        ),
        "announced_date": metadata.get("announced_date") or metadata.get("document_date"),
        "fetched_at": metadata.get("fetched_at") or metadata.get("verified_at"),
        "age_days": registry.source_metadata(metadata).get("age_days") if metadata else None,
        "is_historical": bool(registry.source_metadata(metadata).get("is_historical")) if metadata else False,
        "selection_reason": metadata.get("selection_reason"),
        "commodity": metadata.get("commodity") or parsed["commodity"],
        "counts": {
            "mineral_resource_rows": len(resources),
            "ore_reserve_rows": len(reserves),
            "appendix_resource_rows": len(resource_details),
            "appendix_duplicate_rows_removed": duplicates_removed,
            "appendix_reserve_rows": len(reserve_details),
        },
        "primary_pages": parsed.get("primary_pages", {}),
        "mineral_resources": resources,
        "ore_reserves": reserves,
        "appendix": {
            "note": (
                "附录明细按矿区/矿体拆分，是上表汇总的构成部分，**不可与汇总值相加**；"
                f"已剔除 {duplicates_removed} 行与主表完全相同的总计行。"
            ),
            "mineral_resources": resource_details,
            "ore_reserves": reserve_details,
        },
        "disclosure_note": (
            "资源量/储量为该文档披露口径（生效日期 "
            f"{payload_report_date(metadata, parsed)}），并非当前最新披露；"
            "本次未在已验证注册表中找到更新的报告，因此按历史数据标注；"
            "如需最新数据请以公司最新公告为准。"
        ),
        "categories": categories,
        "summary": summarise_categories(categories),
        "registry": (
            {
                "key": metadata.get("key"),
                "role": metadata.get("role"),
                "hosting_note": metadata.get("hosting_note"),
                "verified_at": metadata.get("verified_at"),
            }
            if registered
            else None
        ),
        "contact_sheet": parsed["notes"],
    }

    provenance = resolved["provenance"]
    assert isinstance(provenance, Provenance)
    return envelope(
        payload,
        provenance=provenance,
        source=str(resolved["source"]),
        source_url=str(resolved["source_url"]),
        as_of=payload["effective_date"] or payload["report_date"],
        notes=notes + parsed["notes"],
        sha256=sha256,
        sha256_verified=bool(registered) and registered.get("sha256") == sha256,
    )


def payload_report_date(metadata: dict[str, Any], parsed: dict[str, Any]) -> str:
    return str(
        metadata.get("effective_date")
        or metadata.get("report_date")
        or parsed.get("report_date")
        or "未知"
    )


def create_server(
    host: str = "127.0.0.1", port: int = DEFAULT_PORT, log_level: str = "INFO"
) -> FastMCP:
    mcp = FastMCP(SERVICE, instructions=INSTRUCTIONS, host=host, port=port, log_level=log_level)
    mcp.tool()(extract_resources)
    mcp.tool()(list_sources)
    register_health(mcp, service=SERVICE, version=__version__)
    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="minereport-pdf", description=INSTRUCTIONS)
    add_transport_args(parser, DEFAULT_PORT)
    args = parser.parse_args(argv)
    run_server(create_server(args.host, args.port, args.log_level), args)


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "SERVICE",
    "create_server",
    "extract_resources",
    "list_sources",
    "main",
]

