from __future__ import annotations

import logging
import os
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    level_name = level or os.getenv("LOG_LEVEL", "INFO")
    log_level = getattr(logging, level_name.upper(), logging.INFO)

    # stdout is the MCP protocol stream. Replace existing handlers too, so an
    # earlier stdout handler cannot leak diagnostic output onto the wire.
    console = Console(stderr=True)
    handler = RichHandler(console=console, show_time=True, show_path=False, rich_tracebacks=False)

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[handler],
        force=True,
    )

    logger = logging.getLogger("jellyseerr_mcp")
    logger.setLevel(log_level)
    # HTTPX INFO/DEBUG records include URLs containing SAB/Hydra query API keys.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logger.debug("🧪 Debug logging enabled")
    return logger
