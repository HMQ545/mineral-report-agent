"""Shared command line surface so every entry point starts identically."""

from __future__ import annotations

import argparse
from typing import Any

TRANSPORTS = ("stdio", "streamable-http")


def add_transport_args(parser: argparse.ArgumentParser, default_port: int) -> None:
    parser.add_argument(
        "--transport",
        choices=TRANSPORTS,
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host for streamable-http")
    parser.add_argument("--port", type=int, default=default_port, help="bind port for streamable-http")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )


def run_server(mcp: Any, args: argparse.Namespace) -> None:
    """Run a FastMCP app over the selected transport."""

    mcp.run(transport=args.transport)

