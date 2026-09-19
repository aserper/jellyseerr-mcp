from __future__ import annotations

import logging
import os
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    level_name = level or os.getenv("LOG_LEVEL", "INFO")
    log_level = getattr(logging, level_name.upper(), logging.INFO)

    # stdio MCP servers speak JSON-RPC over stdout: ANY other byte written there corrupts the
    # protocol stream and makes the client drop the connection. rich.Console() defaults to
    # sys.stdout, so logging must be pinned to stderr explicitly.
    console = Console(stderr=True)
    handler = RichHandler(console=console, show_time=True, show_path=False, rich_tracebacks=True)

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[handler],
        force=True,
    )

    logger = logging.getLogger("jellyseerr_mcp")
    logger.setLevel(log_level)
    logger.debug("🧪 Debug logging enabled")
    return logger
