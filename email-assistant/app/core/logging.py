"""Structured logging.

JSON in production (Cloud Run / Cloud Logging picks it up automatically),
human-readable console output in development.
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import get_settings

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.is_production:
        # Cloud Logging expects the level under "severity".
        processors.append(_rename_level_to_severity)
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _quieten_parser_libraries(level)
    _configured = True


# Parsers that warn per-file about input we already handle.  A batch over a
# real mailbox printed "invalid pdf header" forty times in a row, which reads
# as a fault and is not one — every outcome is recorded on the document row
# and reported by `extract --problems`.  Errors still come through.
NOISY_LIBRARIES = ("pypdf", "openpyxl")


def _quieten_parser_libraries(level: int) -> None:
    # Never quieter than the configured level, so LOG_LEVEL=DEBUG still shows
    # everything these libraries have to say.
    floor = level if level < logging.INFO else logging.ERROR
    for name in NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(floor)


def _rename_level_to_severity(_logger, _name, event_dict):  # type: ignore[no-untyped-def]
    if "level" in event_dict:
        event_dict["severity"] = event_dict.pop("level").upper()
    return event_dict


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)
