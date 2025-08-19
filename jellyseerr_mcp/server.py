from __future__ import annotations

import asyncio
from typing import Any, Tuple

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from .client import JellyseerrClient
from .config import load_config
from .logging_setup import setup_logging
from .auth import build_auth

def create_server() -> Tuple[FastMCP, JellyseerrClient]:
    logger = setup_logging()
    logger.info("🚀 Starting Jellyseerr MCP server…")

    config = load_config()
    auth_settings, token_verifier = build_auth(config)

    server = FastMCP(
        "jellyseerr",
        host=config.host,
        port=config.port,
        # Use FastMCP defaults: sse at /sse, messages at /messages/
        auth=auth_settings,
        token_verifier=token_verifier,
    )

    client = JellyseerrClient(config)

    @server.tool(name="ping", description="Simple liveness check. Returns server and transport info.")
    async def ping() -> Any:  # type: ignore[override]
        logger.info("🏓 Ping received")
        return {
            "ok": True,
            "service": "jellyseerr-mcp",
            "transport": config.transport,
            "authEnabled": config.auth_enabled,
        }

    @server.tool(name="search_media", description="Search Jellyseerr for media by text query.")
    async def search_media(query: str) -> Any:  # type: ignore[override]
        logger.info(f"🔎 Searching media for query: [bold cyan]{query}[/]")
        data = await client.search_media(query)
        logger.info("✅ Search complete")
        return data

    @server.tool(name="request_media", description="Create a media request in Jellyseerr.")
    async def request_media(media_id: int, media_type: str) -> Any:  # type: ignore[override]
        logger.info(f"📥 Requesting media id={media_id} type={media_type}")
        data = await client.request_media(media_id=media_id, media_type=media_type)
        logger.info("✅ Request created")
        return data

    @server.tool(name="get_request", description="Get Jellyseerr request details/status by id.")
    async def get_request(request_id: int) -> Any:  # type: ignore[override]
        logger.info(f"📄 Fetching request #{request_id}")
        data = await client.get_request(request_id=request_id)
        logger.info("✅ Request fetched")
        return data

    @server.tool(name="raw_request", description="(Advanced) Low-level tool to call any Jellyseerr endpoint. Use with caution.")
    async def raw_request(method: str, endpoint: str, params: dict | None = None, body: dict | None = None) -> Any:  # type: ignore[override]
        logger.info(f"🛠️ Raw request {method.upper()} {endpoint}")
        
        allowed_methods = {"GET", "POST", "PUT", "DELETE"}
        if method.upper() not in allowed_methods:
            raise ValueError(f"Unsupported method: {method}. Must be one of {allowed_methods}")

        data = await client.request(method=method, endpoint=endpoint, params=params, json=body)
        logger.info("✅ Raw request complete")
        return data

    # Health endpoints for HTTP transports/direct probing by clients
    @server.custom_route("/", methods=["GET"])
    async def root(request: Request):
        return PlainTextResponse("Jellyseerr MCP Server OK")

    @server.custom_route("/health", methods=["GET"])  # common convention
    async def health(_: Request):
        return JSONResponse({"status": "ok", "service": "jellyseerr-mcp"})

    @server.custom_route("/auth-check", methods=["GET"])  # debug helper
    async def auth_check(request: Request):
        # Show whether auth middleware recognized the user
        user = getattr(request, "user", None)
        auth = isinstance(user, AuthenticatedUser)
        authz = request.headers.get("authorization")
        return JSONResponse({
            "authorized": auth,
            "authHeaderPresent": bool(authz),
            "authHeaderPrefix": (authz.split(" ")[0] if authz else None),
            "user": getattr(user, "identity", None),
            "scopes": getattr(user, "scopes", None),
        })

    return server, client


async def main() -> None:
    server, client = create_server()
    try:
        # Choose transport
        from .config import load_config as _load
        cfg = _load()
        if cfg.transport == "stdio":
            await server.run_stdio_async()
        elif cfg.transport == "sse":
            # SSE over HTTP (requires uvicorn via FastMCP)
            import anyio

            await server.run_sse_async(mount_path=cfg.mount_path)
        elif cfg.transport == "streamable-http":
            await server.run_streamable_http_async()
        else:
            raise RuntimeError(f"Unknown MCP_TRANSPORT: {cfg.transport}")
    finally:
        # After server exits, cleanup HTTP client
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
