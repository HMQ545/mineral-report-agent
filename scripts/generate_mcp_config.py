"""Generate a machine-specific MCP config from the portable example template.

The repository only ships ``mcp-config.stdio.example.json``, which contains the
``<PROJECT_PATH>`` placeholder - so a public checkout never carries somebody
else's absolute path. This script replaces the placeholder with the real path of
this checkout and writes ``mcp-config.stdio.local.json`` (git-ignored).

    uv run python scripts/generate_mcp_config.py                  # -> mcp-config.stdio.local.json + mcp-config.json
    uv run python scripts/generate_mcp_config.py --stdout          # print, do not write
    uv run python scripts/generate_mcp_config.py --cursor          # also merge into Cursor's user config
    uv run python scripts/generate_mcp_config.py --claude          # also merge into Claude Desktop's config
    uv run python scripts/generate_mcp_config.py --project-root <path> --out <file>

The rendered text is parsed back with ``json.loads`` before it is written, so a
Windows path is always escaped correctly or the command fails loudly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "mcp-config.stdio.example.json"
PLACEHOLDER = "<PROJECT_PATH>"
DEFAULT_OUTPUT = "mcp-config.stdio.local.json"
# The delivery checklist asks for a file literally called ``mcp-config.json``.
# The default run writes both names with identical content; both are git-ignored
# so a public checkout still never carries somebody else's absolute path.
DELIVERABLE_OUTPUT = "mcp-config.json"

# Servers must keep their name, command and transport: this script only swaps
# the path, it never rewrites the wiring.
EXPECTED_SERVERS = ("mining-news-mcp", "mineral-pdf-mcp", "lme-price-mcp")


def render(project_root: Path, template: Path | None = None) -> dict:
    """Return the config dict with ``<PROJECT_PATH>`` replaced by ``project_root``."""

    template = template or TEMPLATE
    if not template.is_file():
        raise SystemExit(f"template not found: {template}")
    raw = template.read_text(encoding="utf-8")
    if PLACEHOLDER not in raw:
        raise SystemExit(f"{template.name} does not contain the {PLACEHOLDER} placeholder")

    # json.dumps gives exactly the escaping a JSON string literal needs
    # (backslashes doubled, quotes escaped), so Windows paths survive.
    escaped = json.dumps(str(project_root))[1:-1]
    rendered = raw.replace(PLACEHOLDER, escaped)

    try:
        config = json.loads(rendered)
    except json.JSONDecodeError as exc:  # pragma: no cover - guard rail
        raise SystemExit(f"rendered config is not valid JSON: {exc}") from exc

    servers = config.get("mcpServers") or {}
    missing = [name for name in EXPECTED_SERVERS if name not in servers]
    if missing:
        raise SystemExit(f"template is missing expected servers: {missing}")
    for name, spec in servers.items():
        args = spec.get("args") or []
        if "--directory" not in args or args[args.index("--directory") + 1] != str(project_root):
            raise SystemExit(f"{name}: project path was not substituted correctly")
        if "--transport" in args and args[args.index("--transport") + 1] != "stdio":
            raise SystemExit(f"{name}: transport changed unexpectedly")
    return config


def default_output_dir() -> Path:
    """Where the generated configs go (overridable for tests/CI)."""

    override = os.environ.get("MINEREPORT_MCP_CONFIG_DIR")
    return Path(override).expanduser() if override else PROJECT_ROOT


def cursor_config_path() -> Path:
    override = os.environ.get("MINEREPORT_CURSOR_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("USERPROFILE", Path.home())) / ".cursor" / "mcp.json"


def claude_config_path() -> Path:
    override = os.environ.get("MINEREPORT_CLAUDE_CONFIG")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "Claude" / "claude_desktop_config.json"


def merge_into(config: dict, path: Path) -> Path:
    """Merge our servers into an existing client config, preserving the rest."""

    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    servers = existing.setdefault("mcpServers", {})
    servers.update(config["mcpServers"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        default=None,
        help="path to embed (default: the repository containing this script)",
    )
    parser.add_argument("--out", default=None, help=f"output file (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--template", default=None, help="override the example template")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--cursor", action="store_true", help="merge into Cursor's user config")
    parser.add_argument("--claude", action="store_true", help="merge into Claude Desktop's config")
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else PROJECT_ROOT
    template = Path(args.template).expanduser() if args.template else None
    config = render(project_root, template)
    text = json.dumps(config, ensure_ascii=False, indent=2)

    print(f"project root : {project_root}", file=sys.stderr)
    print(f"template     : {(template or TEMPLATE)}", file=sys.stderr)

    if args.stdout:
        print(text)
        return 0

    if args.out:
        outputs = [Path(args.out).expanduser()]
    else:
        directory = default_output_dir()
        outputs = [directory / DEFAULT_OUTPUT, directory / DELIVERABLE_OUTPUT]
    for out in outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote        : {out}", file=sys.stderr)

    for flag, path in (("--cursor", cursor_config_path()), ("--claude", claude_config_path())):
        if getattr(args, flag.lstrip("-")):
            print(f"merged into  : {merge_into(config, path)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())