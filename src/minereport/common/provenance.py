"""The four-level provenance contract used by every tool response."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Provenance(str, Enum):
    """Where the bytes in a response actually came from."""

    LIVE = "live"
    CACHED = "cached"
    FIXTURE = "fixture"
    SYNTHETIC = "synthetic"


# Business-level availability, deliberately separate from provenance: a dataset
# can be honestly `live` and still be unusable (`DATA_MISSING`), and a fixture is
# `OK` in demo mode. Only these two values exist - there is no fifth provenance.
DATA_STATUS_OK = "OK"
DATA_STATUS_MISSING = "DATA_MISSING"

PROVENANCE_LABELS: dict[Provenance, str] = {
    Provenance.LIVE: "实时获取",
    Provenance.CACHED: "本地缓存",
    Provenance.FIXTURE: "离线样例（fixture）",
    Provenance.SYNTHETIC: "合成数据（synthetic）",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def envelope(
    data: Any,
    *,
    provenance: Provenance,
    source: str,
    source_url: str | None = None,
    as_of: str | None = None,
    retrieved_at: str | None = None,
    notes: list[str] | None = None,
    error: str | None = None,
    data_status: str = DATA_STATUS_OK,
    **extra: Any,
) -> dict[str, Any]:
    """Build the uniform response envelope every MCP tool returns."""

    payload: dict[str, Any] = {
        "data": data,
        "provenance": provenance.value,
        "provenance_label": PROVENANCE_LABELS[provenance],
        "source": source,
        "source_url": source_url,
        "as_of": as_of,
        "retrieved_at": retrieved_at or utc_now_iso(),
        "degraded": provenance is not Provenance.LIVE,
        "data_status": data_status,
        "notes": list(notes or []),
    }
    if error:
        payload["error"] = error
    payload.update(extra)
    return payload


def error_envelope(
    message: str,
    *,
    source: str,
    source_url: str | None = None,
    code: str = "ERROR",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """A failed call is never dressed up as data."""

    return envelope(
        None,
        provenance=Provenance.LIVE,
        source=source,
        source_url=source_url,
        notes=notes,
        error=message,
        data_status=DATA_STATUS_MISSING,
        ok=False,
        error_code=code,
    )


def is_live(payload: dict[str, Any]) -> bool:
    return payload.get("provenance") == Provenance.LIVE.value and not payload.get("error")

