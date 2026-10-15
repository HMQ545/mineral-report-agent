"""Filesystem and environment configuration shared by every process."""

from __future__ import annotations

import os
from pathlib import Path

_COMMON_DIR = Path(__file__).resolve().parent
SRC_DIR = _COMMON_DIR.parent.parent
PROJECT_ROOT = SRC_DIR.parent


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


DATA_DIR = _path_from_env("MINEREPORT_DATA_DIR", PROJECT_ROOT / "data")
FIXTURES_DIR = DATA_DIR / "fixtures"
SOURCES_DIR = DATA_DIR / "sources"
CACHE_DIR = DATA_DIR / "cache"
PDF_CACHE_DIR = CACHE_DIR / "pdf"
NEWS_CACHE_DIR = CACHE_DIR / "news"
PRICE_CACHE_DIR = CACHE_DIR / "prices"
REPORTS_DIR = _path_from_env("MINEREPORT_REPORTS_DIR", PROJECT_ROOT / "reports")


def ensure_dirs() -> None:
    for directory in (
        DATA_DIR,
        FIXTURES_DIR,
        SOURCES_DIR,
        CACHE_DIR,
        PDF_CACHE_DIR,
        NEWS_CACHE_DIR,
        PRICE_CACHE_DIR,
        REPORTS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


ensure_dirs()

