"""Core result types shared by every check and action.

Everything in Exodia returns a `Result`. Checks return one; actions return one
per phase. Results are structured (Pydantic) so they can be rendered to a table,
serialised to JSON for CI, or aggregated into a report — never bare strings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

from .severity import Severity


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


class Phase(str, Enum):
    """The four macro-phases of a system-copy / migration cutover plan.

    These mirror the official ECS/HEC Cutover Plan structure so a report groups
    checks the way a migration team actually reasons about the cutover:

    * PREPARATION  — read-only readiness on source & target, no downtime.
    * RAMP_DOWN    — quiesce the source (drain queues, lock users, stop jobs).
    * DOWNTIME     — business is down; the copy/replica is created and synced.
    * POST         — post-activities on the target (verify, reattach, validation).

    ``UNCLASSIFIED`` is the default for operations that predate phase tagging.
    """

    PREPARATION = "preparation"
    RAMP_DOWN = "ramp_down"
    DOWNTIME = "downtime"
    POST = "post"
    UNCLASSIFIED = "unclassified"

    @property
    def label(self) -> str:
        """Human-readable phase title for reports."""
        return {
            Phase.PREPARATION: "Preparation Phase",
            Phase.RAMP_DOWN: "Ramp-Down Phase (Source)",
            Phase.DOWNTIME: "Downtime / Execution Phase",
            Phase.POST: "Post-Activities Phase (Target)",
            Phase.UNCLASSIFIED: "Other Checks",
        }[self]

    @property
    def order(self) -> int:
        """Cutover ordering used to sort phase groups in a report."""
        return {
            Phase.PREPARATION: 0,
            Phase.RAMP_DOWN: 1,
            Phase.DOWNTIME: 2,
            Phase.POST: 3,
            Phase.UNCLASSIFIED: 9,
        }[self]


class Side(str, Enum):
    """Which system a check/step runs against — an axis the real COP tracks.

    The Cutover Plan tags every step with a SIDE so the same finding can route
    differently depending on where it lives. Kept alongside RESPONSIBLE (a free
    string owner) to mirror the plan's routing model.
    """

    SOURCE = "source"
    TARGET = "target"
    BOTH = "both"
    ORG = "org"  # organisational / project-level, not a specific system

    @property
    def label(self) -> str:
        return {
            Side.SOURCE: "Source",
            Side.TARGET: "Target",
            Side.BOTH: "Source+Target",
            Side.ORG: "Project",
        }[self]


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
    # --- report presentation (human-readable grouping) --------------------- #
    # Which cutover macro-phase this result belongs to (drives report grouping).
    phase: Phase = Phase.UNCLASSIFIED
    # Gate role: BLOCKING fails the phase gate; ADVISORY feeds the exception
    # report; INFO is display-only. Stamped from the check's intrinsic severity
    # by ``execute`` unless a run already set it. The per-engagement GatePolicy
    # (exodia.core.gate) may later reclassify this for reporting.
    severity: Severity = Severity.ADVISORY
    # Which system this ran against (source/target/both/org) and who owns the
    # follow-up. Mirror the COP's SIDE + RESPONSIBLE routing axes; both optional.
    side: Side | None = None
    responsible: str | None = None
    # Explicit, action-oriented title for a human report, e.g.
    # "SM12 — Lock Entries Check" or "HANA Revision Compatibility". Falls back
    # to ``name`` when unset.
    title: str = ""
    # Ordered, labelled facts to show as their own columns/rows in a report,
    # e.g. {"HANA Version": "2.00.067", "Lock Entries": "0"}. This is what makes
    # a check "explicit" for the customer — the measured value, clearly labelled.
    facts: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Exact timing of the underlying work. ``execute`` phases of an action can
    # run for hours (SWPM restore), so an auditor needs the real start, end and
    # duration — not just when the object was created.
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None

    @property
    def display_title(self) -> str:
        """The human title if set, else the dotted name."""
        return self.title or self.name

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
