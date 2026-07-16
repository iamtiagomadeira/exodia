"""Core result types shared by every check and action.

Everything in Exodia returns a `Result`. Checks return one; actions return one
per phase. Results are structured (Pydantic) so they can be rendered to a table,
serialised to JSON for CI, or aggregated into a report — never bare strings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Outcome of a check or action phase."""

    PASS = "pass"  # nosec B105 - status enum value, not a password
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"  # unexpected exception, distinct from a clean FAIL

    @property
    def is_blocking(self) -> bool:
        """FAIL and ERROR block a prepare pipeline; WARN/SKIP/PASS do not."""
        return self in (Status.FAIL, Status.ERROR)


class Result(BaseModel):
    """Structured outcome of a single check or action phase."""

    name: str
    status: Status
    summary: str = ""
    detail: str = ""
    # Optional remediation surfaced from the troubleshooting KB.
    cause: str | None = None
    fix: list[str] = Field(default_factory=list)
    sap_note: str | None = None
    # Free-form structured data (e.g. measured free space, versions).
    data: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def ok(cls, name: str, summary: str = "", **kw: object) -> Result:
        return cls(name=name, status=Status.PASS, summary=summary, **kw)  # type: ignore[arg-type]

    @classmethod
    def warn(cls, name: str, summary: str, **kw: object) -> Result:
        return cls(name=name, status=Status.WARN, summary=summary, **kw)  # type: ignore[arg-type]

    @classmethod
    def fail(cls, name: str, summary: str, **kw: object) -> Result:
        return cls(name=name, status=Status.FAIL, summary=summary, **kw)  # type: ignore[arg-type]

    @classmethod
    def skip(cls, name: str, summary: str, **kw: object) -> Result:
        return cls(name=name, status=Status.SKIP, summary=summary, **kw)  # type: ignore[arg-type]

    @classmethod
    def error(cls, name: str, summary: str, **kw: object) -> Result:
        return cls(name=name, status=Status.ERROR, summary=summary, **kw)  # type: ignore[arg-type]
