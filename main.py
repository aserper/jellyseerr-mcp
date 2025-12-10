from __future__ import annotations

import asyncio
import os
import sys

from jellyseerr_mcp.server import run as run_mcp

MIN_PYTHON_VERSION = (3, 10)

def _check_python_version() -> None:
    if sys.version_info < MIN_PYTHON_VERSION:
        sys.exit(
            f"Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]} or higher is required."
        )

if __name__ == "__main__":
    _check_python_version()
    # Entry point for MCP stdio server
    import argparse

    parser = argparse.ArgumentParser(description="Jellyseerr MCP Server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse"],
        help="Transport protocol to use (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8000)),
        help="Port to serve SSE on (default: $PORT or 8000)",
    )
    args = parser.parse_args()

    asyncio.run(run_mcp(transport=args.transport, port=args.port))
