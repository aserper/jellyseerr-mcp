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

```
python -m jellyseerr_mcp
```

You should see colorful logs indicating the server is ready on stdio. The server communicates via stdin/stdout, making it compatible with Claude Desktop and other MCP clients.

## Exposed tools (initial set)
- `search_media(query: str)` — Search Jellyseerr for media by query.
- `request_media(media_id: int, media_type: str)` — Create a media request.
- `get_request(request_id: int)` — Fetch a request’s details/status.
- `ping()` — Liveness check with server/transport info.

More tools can be added easily — see `jellyseerr_mcp/server.py`.
