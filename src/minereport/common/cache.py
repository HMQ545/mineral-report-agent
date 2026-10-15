"""Small on-disk cache that keeps provenance honest."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from . import config
from .provenance import utc_now_iso

_KINDS = {
    "news": config.NEWS_CACHE_DIR,
    "pdf": config.PDF_CACHE_DIR,
    "prices": config.PRICE_CACHE_DIR,
}


def _dir_for(kind: str) -> Path:
    try:
        directory = _KINDS[kind]
    except KeyError:
        raise ValueError(f"unknown cache kind {kind!r}") from None
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def cache_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def read_json(kind: str, key: str, *, max_age_seconds: float | None = None) -> dict[str, Any] | None:
    path = _dir_for(kind) / f"{key}.json"
    if not path.exists():
        return None
    if max_age_seconds is not None and time.time() - path.stat().st_mtime > max_age_seconds:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(kind: str, key: str, payload: Any) -> Path:
    path = _dir_for(kind) / f"{key}.json"
    record = {"cached_at": utc_now_iso(), "payload": payload}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_bytes(
    kind: str, key: str, suffix: str, *, max_age_seconds: float | None = None
) -> tuple[bytes, str] | None:
    path = _dir_for(kind) / f"{key}{suffix}"
    if not path.exists():
        return None
    if max_age_seconds is not None and time.time() - path.stat().st_mtime > max_age_seconds:
        return None
    meta_path = _dir_for(kind) / f"{key}.meta.json"
    cached_at = utc_now_iso()
    if meta_path.exists():
        try:
            cached_at = json.loads(meta_path.read_text(encoding="utf-8")).get("cached_at", cached_at)
        except (OSError, json.JSONDecodeError):
            pass
    return path.read_bytes(), cached_at


def write_bytes(kind: str, key: str, suffix: str, payload: bytes, **meta: Any) -> Path:
    path = _dir_for(kind) / f"{key}{suffix}"
    path.write_bytes(payload)
    meta_record = {"cached_at": utc_now_iso(), **meta}
    (_dir_for(kind) / f"{key}.meta.json").write_text(
        json.dumps(meta_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path

