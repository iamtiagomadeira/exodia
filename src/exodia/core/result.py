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
    # Exact timing of the underlying work. ``execute`` phases of an action can
    # run for hours (SWPM restore), so an auditor needs the real start, end and
    # duration — not just when the object was created.
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None

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

    def stamp_timing(self, started_at: datetime, ended_at: datetime) -> Result:
        """Record the real start/end of the work this Result represents.

        Called by the runner/guard around each check and action phase so the
        exact wall-clock span (and duration) is preserved in evidence.
        """
        self.started_at = started_at
        self.ended_at = ended_at
        self.duration_seconds = max(0.0, (ended_at - started_at).total_seconds())
        return self

    @property
    def duration_str(self) -> str:
        """Human-readable duration, e.g. ``2h 14m 08s`` or ``850ms``."""
        return format_duration(self.duration_seconds)


def format_duration(seconds: float | None) -> str:
    """Format a span of seconds as a compact, audit-friendly string.

    ``None`` -> ``"—"``; sub-second -> ``"850ms"``; otherwise
    ``"[Nh ]Mm SSs"`` (hours only when non-zero).
    """
    if seconds is None:
        return "—"
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"
