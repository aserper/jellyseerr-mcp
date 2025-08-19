from __future__ import annotations

import asyncio
import sys

from jellyseerr_mcp.server import main as run_mcp

MIN_PYTHON_VERSION = (3, 10)

def _check_python_version() -> None:
    if sys.version_info < MIN_PYTHON_VERSION:
        sys.exit(
            f"Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]} or higher is required."
        )

if __name__ == "__main__":
    _check_python_version()
    # Entry point for MCP stdio server
    asyncio.run(run_mcp())
