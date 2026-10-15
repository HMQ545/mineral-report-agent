"""Input resolution rules for ``extract_resources`` (SSRF + size + path safety)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..common import cache
from ..common.fixtures import FixtureNotFoundError, is_inside_data_dir, resolve_fixture
from ..common.http import MAX_DOWNLOAD_BYTES, UrlNotAllowedError, assert_url_allowed, fetch_bytes
from ..common.provenance import Provenance

PDF_MAGIC = b"%PDF-"


class PdfInputError(ValueError):
    """Raised when an input reference cannot be turned into PDF bytes safely."""


class NotAPdfError(ValueError):
    """Raised when the downloaded bytes are not a PDF."""


def _local_path_from_file_url(reference: str) -> Path:
    parsed = urlparse(reference)
    if parsed.scheme != "file":
        raise PdfInputError(f"not a file url: {reference!r}")
    raw = unquote(parsed.path)
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        raw = f"//{parsed.netloc}{raw}"
    if raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]
    path = Path(raw)
    if not path.is_file():
        raise PdfInputError(f"file not found: {path}")
    if not is_inside_data_dir(path):
        raise PdfInputError(
            f"file:// inputs are restricted to the project data directory ({path} is outside)"
        )
    return path


def resolve_pdf_bytes(
    reference: str,
    *,
    allow_hosts: tuple[str, ...] | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    refresh: bool = False,
) -> dict[str, object]:
    """Return PDF bytes plus the provenance of where they came from."""

    reference = (reference or "").strip()
    if not reference:
        raise PdfInputError("pdf_url must not be empty")

    if reference.startswith(("fixture://", "fixtures://")):
        path = resolve_fixture(reference)
        data = path.read_bytes()
        return {
            "data": data,
            "source": "repository fixture",
            "source_url": f"fixture://{path.relative_to(path.parents[1]).as_posix()}",
            "provenance": Provenance.FIXTURE,
            "notes": ["离线样例文件，非真实披露文档。"],
        }

    if reference.lower().startswith("file://"):
        path = _local_path_from_file_url(reference)
        return {
            "data": path.read_bytes(),
            "source": "local file (project data dir)",
            "source_url": reference,
            "provenance": Provenance.FIXTURE if "fixtures" in path.parts else Provenance.CACHED,
            "notes": [f"本地文件：{path}"],
        }

    assert_url_allowed(reference, allow_hosts=allow_hosts)
    key = cache.cache_key(reference)
    cached = None if refresh else cache.read_bytes("pdf", key, ".pdf")
    if cached:
        data, cached_at = cached
        return {
            "data": data,
            "source": "cache of a previous live download",
            "source_url": reference,
            "provenance": Provenance.CACHED,
            "notes": [f"命中本地缓存（原始抓取时间 {cached_at}）"],
        }

    data, final_url = fetch_bytes(reference, max_bytes=max_bytes, timeout=60.0)
    cache.write_bytes(
        "pdf",
        key,
        ".pdf",
        data,
        source_url=reference,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return {
        "data": data,
        "source": "live download",
        "source_url": final_url,
        "provenance": Provenance.LIVE,
        "notes": [],
    }


def assert_pdf(data: bytes) -> str:
    if not data:
        raise NotAPdfError("empty response body")
    if not data[:5].startswith(PDF_MAGIC):
        raise NotAPdfError(f"payload does not start with %PDF- (first bytes: {data[:8]!r})")
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "FixtureNotFoundError",
    "NotAPdfError",
    "PdfInputError",
    "UrlNotAllowedError",
    "assert_pdf",
    "resolve_pdf_bytes",
]

