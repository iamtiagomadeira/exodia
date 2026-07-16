"""Structured logging — the single biggest gap in the internal predecessor.

Every check and action logs through here: structured, levelled, and never
leaking secrets. Rich handler for humans; JSON option for CI/automation.
"""

from __future__ import annotations

import logging
import re
import sys

from rich.logging import RichHandler

# Patterns whose values must never reach a log line. Kept deliberately broad:
# a false redaction is harmless, a leaked SAP credential is not.
_SECRET_PATTERNS = [
    # key=value / key: value forms (password, passphrase, key phrase, token, ...)
    re.compile(
        r"(?i)(password|passwd|pwd|secret|key[_-]?phrase|pass[_-]?phrase|token|api[_-]?key)"
        r"\s*[=:]\s*\S+"
    ),
    # command-line password flags: -p / -P / --password <value> (ASE isql -P, etc.)
    re.compile(r"(?i)(-p|--password|--pwd)\s+\S+"),
]


class RedactingFilter(logging.Filter):
    """Redacts secret-looking substrings from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in _SECRET_PATTERNS:
            msg = pat.sub(lambda m: m.group(0).split(m.group(1))[0] + m.group(1) + "=***", msg)
        record.msg = msg
        record.args = ()
        return True


def get_logger(name: str = "exodia", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = RichHandler(rich_tracebacks=True, show_path=False, console=None)
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def configure(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("exodia").setLevel(level)
    # Keep paramiko quiet unless debugging.
    logging.getLogger("paramiko").setLevel(logging.WARNING if not verbose else logging.INFO)


# Ensure a sane default even if configure() is never called.
if not logging.getLogger("exodia").handlers:
    get_logger()
    if sys.stderr.isatty():
        configure(verbose=False)
