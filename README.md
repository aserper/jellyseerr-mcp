![Jellyseerr MCP Server](header.png)

# Jellyseerr MCP Server

[![Tests](https://img.shields.io/github/actions/workflow/status/aserper/jellyseerr-mcp/test.yml?style=for-the-badge&logo=github&label=Tests)](https://github.com/aserper/jellyseerr-mcp/actions/workflows/test.yml)
[![Supported Python versions](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](README.md#license)
[![GHCR](https://img.shields.io/badge/ghcr.io-jellyseerr--mcp-blue?style=for-the-badge&logo=docker&logoColor=white)](https://github.com/aserper/jellyseerr-mcp/pkgs/container/jellyseerr-mcp)

[![GitHub stars](https://img.shields.io/github/stars/aserper/jellyseerr-mcp.svg?style=social)](https://github.com/aserper/jellyseerr-mcp)
[![GitHub forks](https://img.shields.io/github/forks/aserper/jellyseerr-mcp.svg?style=social)](https://github.com/aserper/jellyseerr-mcp/fork)

An MCP (Model Context Protocol) server for Jellyseerr that exposes Jellyseerr API functionality as MCP tools usable by LLM clients. It includes colorful, emoji-forward logging and clear console output.

## Features
- Exposes key Jellyseerr endpoints as MCP tools (search, request, get request status, etc.)
- Synchronous HTTP client with robust error handling and timeouts
- Colorful, structured logging via Rich with emoji indicators
- Configuration via environment variables (`.env` supported)
- Non-blocking stdio server compatible with multi-mcp configurations

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

## Docker

You can run the server using Docker by either pulling the pre-built image from the GitHub Container Registry (GHCR) or building it yourself.

### Pull from GHCR

```bash
docker pull ghcr.io/aserper/jellyseerr-mcp:latest
```

### Build Locally

```bash
docker build -t jellyseerr-mcp .
```

### Run

```bash
# If you pulled from GHCR:
docker run --rm -it \
  -e JELLYSEERR_URL="https://your-jellyseerr.com" \
  -e JELLYSEERR_API_KEY="your_api_key" \
  ghcr.io/aserper/jellyseerr-mcp:latest

# If you built locally:
docker run --rm -it \
  -e JELLYSEERR_URL="https://your-jellyseerr.com" \
  -e JELLYSEERR_API_KEY="your_api_key" \
  jellyseerr-mcp
```

### Multi-MCP Configuration

The server is designed to work seamlessly with multi-mcp configurations. Example `mcp.json`:

```json
{
  "mcpServers": {
    "jellyseerr": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "jellyseerr_mcp"],
      "env": {
        "JELLYSEERR_URL": "https://your-jellyseerr.example.com",
        "JELLYSEERR_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

## Exposed tools (initial set)
- `search_media(query: str)` — Search Jellyseerr for media by query.
- `request_media(media_id: int, media_type: str)` — Create a media request.
- `get_request(request_id: int)` — Fetch a request’s details/status.
- `ping()` — Liveness check with server/transport info.

More tools can be added easily — see `jellyseerr_mcp/server.py`.
