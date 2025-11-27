from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import JellyseerrClient
from .config import load_config
from .logging_setup import setup_logging


async def main() -> None:
    logger = setup_logging()
    logger.info("🚀 Starting Jellyseerr MCP server…")

    config = load_config()
    client = JellyseerrClient(config)

    mcp = FastMCP("jellyseerr")

    @mcp.tool(description="Simple liveness check.")
    async def ping() -> Any:
        logger.info("🏓 Ping received")
        return {
            "ok": True,
            "service": "jellyseerr-mcp",
        }

    @mcp.tool(description="Search Jellyseerr for media by text query.")
    async def search_media(query: str) -> Any:
        logger.info(f"🔎 Searching media for query: [bold cyan]{query}[/]")
        data = await client.search_media(query)
        logger.info("✅ Search complete")
        return data

    @mcp.tool(description="Create a media request in Jellyseerr.")
    async def request_media(media_id: int, media_type: str) -> Any:
        logger.info(f"📥 Requesting media id={media_id} type={media_type}")
        data = await client.request_media(media_id=media_id, media_type=media_type)
        logger.info("✅ Request created")
        return data

    @mcp.tool(description="Get Jellyseerr request details/status by id.")
    async def get_request(request_id: int) -> Any:
        logger.info(f"📄 Fetching request #{request_id}")
        data = await client.get_request(request_id=request_id)
        logger.info("✅ Request fetched")
        return data

    @mcp.tool(description="(Advanced) Low-level tool to call any Jellyseerr endpoint. Use with caution.")
    async def raw_request(method: str, endpoint: str, params: dict | None = None, body: dict | None = None) -> Any:
        logger.info(f"🛠️ Raw request {method.upper()} {endpoint}")

        allowed_methods = {"GET", "POST", "PUT", "DELETE"}
        if method.upper() not in allowed_methods:
            raise ValueError(f"Unsupported method: {method}. Must be one of {allowed_methods}")

        data = await client.request(method=method, endpoint=endpoint, params=params, json=body)
        logger.info("✅ Raw request complete")
        return data

    try:
        # Run on stdio by default (mcp.run() with no arguments defaults to stdio)
        await mcp.run()
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
