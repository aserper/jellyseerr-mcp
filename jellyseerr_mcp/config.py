from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass
class AppConfig:
    jellyseerr_url: str
    jellyseerr_api_key: str
    timeout: float = 15.0
    # Transport
    transport: str = "stdio"  # one of: stdio, sse, streamable-http
    host: str = "127.0.0.1"
    port: int = 8000
    mount_path: str = "/"
    # Auth (HTTP transports)
    auth_enabled: bool = False
    auth_issuer_url: str | None = None
    auth_resource_server_url: str | None = None
    auth_required_scopes: list[str] | None = None
    auth_bearer_tokens: list[str] | None = None


def load_config() -> AppConfig:
    load_dotenv()

    url = os.getenv("JELLYSEERR_URL", "").strip()
    api_key = os.getenv("JELLYSEERR_API_KEY", "").strip()
    timeout_str: Optional[str] = os.getenv("JELLYSEERR_TIMEOUT")

    if not url or not api_key:
        raise RuntimeError(
            "Missing configuration. Please set JELLYSEERR_URL and JELLYSEERR_API_KEY (tip: copy .env.example)."
        )

    # Optional: transport + HTTP server config
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    host = os.getenv("FASTMCP_HOST", "127.0.0.1").strip()
    port_str = os.getenv("FASTMCP_PORT", "8000").strip()
    mount_path = os.getenv("FASTMCP_MOUNT_PATH", "/").strip() or "/"

    # Optional: auth config
    auth_enabled = os.getenv("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    auth_issuer_url = os.getenv("AUTH_ISSUER_URL")
    auth_resource_server_url = os.getenv("AUTH_RESOURCE_SERVER_URL")
    scopes = os.getenv("AUTH_REQUIRED_SCOPES")
    tokens = os.getenv("AUTH_BEARER_TOKENS")

    try:
        timeout = float(timeout_str) if timeout_str else 15.0
    except ValueError:
        timeout = 15.0
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    return AppConfig(
        jellyseerr_url=url.rstrip("/"),
        jellyseerr_api_key=api_key,
        timeout=timeout,
        transport=transport,
        host=host,
        port=port,
        mount_path=mount_path,
        auth_enabled=auth_enabled,
        auth_issuer_url=(auth_issuer_url.strip() if auth_issuer_url else None),
        auth_resource_server_url=(auth_resource_server_url.strip() if auth_resource_server_url else None),
        auth_required_scopes=([s.strip() for s in scopes.split(",") if s.strip()] if scopes else None),
        auth_bearer_tokens=([t.strip() for t in tokens.split(",") if t.strip()] if tokens else None),
    )
