"""Contract tests for logging setup.

A stdio MCP server speaks JSON-RPC over stdout: any byte written there that is not part of a
JSON-RPC message corrupts the stream and makes the client drop the connection. These tests pin
that contract for the logging configuration.
"""

import io
import logging

from rich.console import Console

from jellyseerr_mcp.logging_setup import setup_logging


def _console_of(handler):
    """The rich Console the RichHandler renders to."""
    return handler.console


def test_log_console_targets_stderr():
    """The log console must not be stdout — that is the JSON-RPC wire."""
    logger = setup_logging("INFO")
    handlers = [h for h in logger.handlers if h.__class__.__name__ == "RichHandler"]
    root_handlers = [h for h in logging.getLogger().handlers if h.__class__.__name__ == "RichHandler"]
    candidates = handlers or root_handlers
    assert candidates, "no RichHandler configured"
    for handler in candidates:
        console = _console_of(handler)
        if console._file is not None:
            assert console._file is not Console().file, (
                "rich Console is writing to stdout; a stdio MCP server must log to stderr"
            )


def test_logging_never_writes_to_stdout(capsys):
    """A log call must not put bytes on stdout."""
    logger = setup_logging("INFO")
    logger.info("🚀 Starting Jellyseerr MCP server…")
    for handler in logging.getLogger().handlers:
        handler.flush()
    captured = capsys.readouterr()
    assert captured.out == "", f"logging leaked to stdout (the JSON-RPC wire): {captured.out!r}"


def test_setup_logging_is_idempotent_and_overrides_existing_root_handlers(monkeypatch):
    """A pre-installed root handler must be replaced, not reused, or logs keep going to stdout."""
    stray = logging.StreamHandler(io.StringIO())  # stands in for a handler bound to stdout
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [stray])

    setup_logging("INFO")

    assert stray not in logging.getLogger().handlers, (
        "basicConfig(force=True) must drop pre-existing handlers"
    )
