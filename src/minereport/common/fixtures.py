"""Access to the repository's fixed offline samples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config


class FixtureNotFoundError(FileNotFoundError):
    """Raised when a fixture key does not resolve to a repository file."""


def _safe_join(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    root_resolved = root.resolve()
    if root_resolved != candidate and root_resolved not in candidate.parents:
        raise FixtureNotFoundError(f"fixture path escapes {root_resolved}: {relative!r}")
    return candidate


def resolve_fixture(relative: str) -> Path:
    """Resolve ``news/x`` (or ``fixture://news/x``) to a file inside data/fixtures."""

    cleaned = relative
    for prefix in ("fixture://", "fixtures://"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.strip("/\\")

    root = config.FIXTURES_DIR
    candidates = [_safe_join(root, cleaned)]
    if not Path(cleaned).suffix:
        candidates += [_safe_join(root, cleaned + ".json"), _safe_join(root, cleaned + ".pdf")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FixtureNotFoundError(
        f"fixture {relative!r} not found under {root} (tried: {[str(c) for c in candidates]})"
    )


def load_fixture_json(relative: str) -> Any:
    return json.loads(resolve_fixture(relative).read_text(encoding="utf-8"))


def load_fixture_bytes(relative: str) -> bytes:
    return resolve_fixture(relative).read_bytes()


def is_inside_data_dir(path: Path) -> bool:
    root = config.DATA_DIR.resolve()
    resolved = path.resolve()
    return resolved == root or root in resolved.parents

