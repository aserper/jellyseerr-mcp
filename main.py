from __future__ import annotations

import asyncio

from jellyseerr_mcp.server import main as run_mcp


if __name__ == "__main__":
    # Entry point for MCP stdio server
    asyncio.run(run_mcp())
