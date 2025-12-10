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
    # Auth config for SSE
    auth_issuer_url: Optional[str] = None
    auth_resource_server_url: Optional[str] = None
    auth_required_scopes: Optional[list[str]] = None


def load_config() -> AppConfig:
    load_dotenv()

    url = os.getenv("JELLYSEERR_URL", "").strip()
    api_key = os.getenv("JELLYSEERR_API_KEY", "").strip()
    timeout_str: Optional[str] = os.getenv("JELLYSEERR_TIMEOUT")

    auth_issuer_url = os.getenv("MCP_AUTH_ISSUER_URL")
    auth_resource_server_url = os.getenv("MCP_AUTH_RESOURCE_SERVER_URL")
    auth_scopes_str = os.getenv("MCP_AUTH_REQUIRED_SCOPES")
    auth_required_scopes = auth_scopes_str.split(",") if auth_scopes_str else None

    if not url or not api_key:
        raise RuntimeError(
            "Missing configuration. Please set JELLYSEERR_URL and JELLYSEERR_API_KEY (tip: copy .env.example)."
        )

    try:
        timeout = float(timeout_str) if timeout_str else 15.0
    except ValueError:
        timeout = 15.0

    return AppConfig(
        jellyseerr_url=url.rstrip("/"),
        jellyseerr_api_key=api_key,
        timeout=timeout,
        auth_issuer_url=auth_issuer_url,
        auth_resource_server_url=auth_resource_server_url,
        auth_required_scopes=auth_required_scopes,
    )
