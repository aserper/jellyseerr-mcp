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


def load_config() -> AppConfig:
    load_dotenv()

    url = os.getenv("JELLYSEERR_URL", "").strip()
    api_key = os.getenv("JELLYSEERR_API_KEY", "").strip()
    timeout_str: Optional[str] = os.getenv("JELLYSEERR_TIMEOUT")

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
    )
