from __future__ import annotations

import logging
import os
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    level_name = level or os.getenv("LOG_LEVEL", "INFO")
    log_level = getattr(logging, level_name.upper(), logging.INFO)

    console = Console()
    handler = RichHandler(console=console, show_time=True, show_path=False, rich_tracebacks=True)

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        handlers=[handler],
    )

    logger = logging.getLogger("jellyseerr_mcp")
    logger.setLevel(log_level)
    logger.debug("🧪 Debug logging enabled")
    return logger
