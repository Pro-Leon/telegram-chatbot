"""Structured logging configuration for production observability.

Provides JSON structured logging for production and human-readable format
for development. Configurable via STRUCTURED_LOGGING env var.
"""

import json
import logging
import sys
import time
from typing import Any

_SENSITIVE_PATTERNS = (
    "api_key",
    "token",
    "password",
    "secret",
    "session",
    "authorization",
    "dsn",
)


class StructuredFormatter(logging.Formatter):
    """JSON log formatter for production use."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


class HumanFormatter(logging.Formatter):
    """Human-readable log formatter for development."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        msg = f"{ts} [{record.name}] {record.levelname}: {record.getMessage()}"
        if record.exc_info and record.exc_info[0] is not None:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


def setup_logging(structured: bool = False) -> None:
    """Configure root logging with the appropriate formatter."""
    handler = logging.StreamHandler(sys.stdout)
    if structured:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(HumanFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def sanitize_log_value(value: Any) -> Any:
    """Return a safe representation of a value for logging.

    Redacts values for keys matching known sensitive patterns.
    """
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if any(p in k.lower() for p in _SENSITIVE_PATTERNS) else v
            for k, v in value.items()
        }
    return value
