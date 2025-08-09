# Jellyseerr MCP Server

An MCP (Model Context Protocol) server for Jellyseerr that exposes Jellyseerr API functionality as MCP tools usable by LLM clients. It includes colorful, emoji-forward logging and clear console output.

## Features
- Exposes key Jellyseerr endpoints as MCP tools (search, request, get request status, etc.)
- Async HTTP client with robust error handling and timeouts
- Colorful, structured logging via Rich with emoji indicators
- Configuration via environment variables (`.env` supported)

## Requirements
- Python 3.10+
- Packages in `requirements.txt`

## Setup
1. Create and activate a virtualenv.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set your values.

```
JELLYSEERR_URL=https://your-jellyseerr.example.com
JELLYSEERR_API_KEY=your_api_key_here
JELLYSEERR_TIMEOUT=15
```

## Running the MCP server
This server supports stdio (default) and optional HTTP transports.

Stdio (recommended for MCP clients):

```
python -m jellyseerr_mcp
```

You should see colorful logs indicating the server is ready over stdio.

HTTP (SSE) with Bearer token auth (for tools that prefer HTTP + OAuth-style auth):

```
FASTMCP_HOST=127.0.0.1 FASTMCP_PORT=8797 MCP_TRANSPORT=sse \
AUTH_ENABLED=true AUTH_ISSUER_URL=http://localhost:8797 \
AUTH_RESOURCE_SERVER_URL=http://localhost:8797 \
AUTH_BEARER_TOKENS=devtoken123 python -m jellyseerr_mcp
```

Then connect your MCP client to `http://127.0.0.1:8797` and pass `Authorization: Bearer devtoken123`.

## Exposed tools (initial set)
- `search_media(query: str)` — Search Jellyseerr for media by query.
- `request_media(media_id: int, media_type: str)` — Create a media request.
- `get_request(request_id: int)` — Fetch a request’s details/status.
- `ping()` — Liveness check with server/transport info.

More tools can be added easily — see `jellyseerr_mcp/server.py`.

## Notes
- The previous FastAPI stub has been replaced with a proper MCP server scaffold.
- HTTP transport (SSE) is available with optional bearer token auth. Full OAuth 2.0 flows require an external issuer or a provider implementation — tell me your preferred OAuth provider and I’ll wire it in.
