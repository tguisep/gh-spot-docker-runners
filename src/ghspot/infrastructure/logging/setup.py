"""Structured logging.

Pretty output when a human is watching, JSON when the daemon is under systemd and its output
is going to the journal. Nothing here decides *what* to log — the reconciler reports facts and
this decides how they are rendered.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_REDACT = ("token", "secret", "password", "jit", "authorization")


def configure(level: str = "INFO", output: str = "auto") -> None:
    """Set up structlog and the standard library logger it wraps.

    ``output`` is ``console``, ``json``, or ``auto`` to choose by whether stderr is a terminal.
    """
    use_console = output == "console" or (output == "auto" and sys.stderr.isatty())

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.dev.ConsoleRenderer() if use_console else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level.upper())
    # httpx logs every request at INFO, which at a 15s poll interval is pure noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _redact(_logger: object, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    """Never let a credential reach the log, whoever passed it.

    The JIT config blob is a working credential for one runner. It is threaded through the
    provisioning path, so a careless `log.debug(spec=...)` would print it.
    """
    for key, value in list(event.items()):
        if any(word in key.casefold() for word in _REDACT):
            event[key] = "***"
        elif isinstance(value, str) and len(value) > 200 and key not in {"event", "error"}:
            event[key] = f"{value[:60]}… ({len(value)} chars)"
    return event


def get_logger(name: str = "ghspot") -> Any:
    return structlog.get_logger(name)
