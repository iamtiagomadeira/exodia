"""Base classes for the two operation categories: Check and Action.

Check  = read-only validation. Safe to run anywhere, any time.
Action = state-changing execution. Guarded: requires pre-checks, dry-run first,
         explicit confirmation, verify after, documented rollback.

This distinction is the safety backbone of Exodia.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .context import Context
from .knowledge import enrich
from .logging import get_logger
from .params import ParamSpec
from .result import Phase, Result, Side
from .severity import Severity

if TYPE_CHECKING:
    from .monitor import Monitor

log = get_logger()


class Check(ABC):
    """A read-only validation. Never mutates the target."""

    #: unique dotted name, e.g. "hana.free-space"
    name: str = ""
    #: human description
    description: str = ""
    #: if True, a FAIL aborts the surrounding prepare pipeline immediately.
    #: LEGACY flag — kept for backward compat. Prefer declaring ``severity``.
    #: When ``severity`` is unset, BLOCKING is derived from this (True->BLOCKING).
    blocking: bool = False
    #: intrinsic gate role (BLOCKING/ADVISORY/INFO). Declare this on new checks.
    #: When left None, it is derived from ``blocking`` for backward compatibility
    #: (see ``Severity.from_check``). BLOCKING fails a phase gate; ADVISORY feeds
    #: the exception report; INFO is display-only.
    severity: Severity | None = None
    #: which system this runs against (source/target/both/org) — COP routing axis.
    side: Side | None = None
    #: who owns the follow-up (free string, e.g. "customer" / "migration-team").
    responsible: str | None = None
    #: which cutover macro-phase this check belongs to (drives report grouping)
    phase: Phase = Phase.UNCLASSIFIED
    #: explicit, action-oriented report title, e.g. "SM12 — Lock Entries Check".
    #: Falls back to the dotted name when empty.
    title: str = ""

    @abstractmethod
    def run(self, ctx: Context) -> Result:
        """Perform the validation and return a structured Result."""
        ...

    def parameters(self) -> list[ParamSpec]:
        """Inputs this check needs. Override to drive the interactive menu.

        Default: no declared inputs. The wizard still offers the common
        connection fields and the free-form escape hatch, so undeclared
        operations keep working.
        """
        return []

    def execute(self, ctx: Context) -> Result:
        """Wrapper: runs the check, catches exceptions, enriches from KB.

        Also stamps the check's declared ``phase`` / ``title`` onto the Result
        (unless ``run`` already set them), so every check is grouped and labelled
        for the human report without each ``run`` having to repeat that metadata.
        """
        started = datetime.now(UTC)
        try:
            result = self.run(ctx)
        except Exception as exc:  # noqa: BLE001 - convert to structured ERROR
            log.exception("check %s raised", self.name)
            result = Result.error(self.name, f"unexpected error: {exc}")
        if result.phase is Phase.UNCLASSIFIED and self.phase is not Phase.UNCLASSIFIED:
            result.phase = self.phase
        if not result.title and self.title:
            result.title = self.title
        # Stamp the intrinsic gate role + routing axes onto the result unless the
        # run already set them. Severity is always derived (from ``severity`` or
        # the legacy ``blocking`` flag) so every result carries a role.
        if result.severity is Severity.ADVISORY:  # the model default = "unset"
            result.severity = Severity.from_check(self)
        if result.side is None and self.side is not None:
            result.side = self.side
        if result.responsible is None and self.responsible is not None:
            result.responsible = self.responsible
        result.stamp_timing(started, datetime.now(UTC))
        if result.status.is_blocking:
            enrich(result, ctx)
        return result


class Action(ABC):
    """A state-changing operation. Guarded by the safe-execution flow."""

    name: str = ""
    description: str = ""
    #: marks that this modifies systems (always True for real actions)
    destructive: bool = True
    #: names of checks that MUST pass before this action runs
    requires_checks: list[str] = []
    #: which cutover macro-phase this action belongs to (drives report grouping)
    phase: Phase = Phase.UNCLASSIFIED
    #: explicit, action-oriented report title; falls back to the dotted name.
    title: str = ""
    #: when True, execute() is gated behind an EXPLICIT customer confirmation
    #: (param ``customer_confirmed`` truthy) on top of the normal --yes gate.
    #: Used for irreversible customer-impacting steps like stopping the source
    #: application servers, which SAP must not do until the customer signs off.
    requires_customer_confirmation: bool = False
    #: when True this is a MANUAL attestation step — Exodia performs no system
    #: action; the operator does something off-system (e.g. emails the customer)
    #: and records that they did it (param ``attested`` truthy). Captured as
    #: evidence so the cutover record is complete.
    manual: bool = False

    @abstractmethod
    def dry_run(self, ctx: Context) -> Result:
        """Describe exactly what execute() would do, without doing it."""
        ...

    def parameters(self) -> list[ParamSpec]:
        """Inputs this action needs. Override to drive the interactive menu.

        Default: no declared inputs. The wizard still offers the common
        connection fields and the free-form escape hatch.
        """
        return []

    @abstractmethod
    def execute(self, ctx: Context) -> Result:
        """Perform the action. Only called after dry-run + confirmation."""
        ...

    @abstractmethod
    def verify(self, ctx: Context) -> Result:
        """Confirm the action achieved its goal (e.g. replica ACTIVE)."""
        ...

    def rollback(self, ctx: Context) -> Result:
        """Best-effort reversal. Default: documented-only (no auto-rollback)."""
        return Result.skip(
            f"{self.name}.rollback",
            "no automatic rollback — see runbook / SAP Note for manual steps",
        )

    # -- live monitor (optional) ------------------------------------------- #
    #: a live Monitor for long-running actions; None = no live UI (default).
    _monitor: Monitor | None = None

    def set_monitor(self, monitor: Monitor | None) -> None:
        """Attach a live monitor so execute() can stream phase/log/progress.

        Optional: when unset, the ``_emit_*`` helpers are silent no-ops, so an
        action's execute() can call them unconditionally without caring whether
        a dashboard is attached (CLI --monitor) or not (plain run, tests).
        """
        self._monitor = monitor

    def _emit_phase(self, name: str, detail: str = "") -> None:
        if self._monitor is not None:
            self._monitor.phase(name, detail)

    def _emit_log(self, line: str) -> None:
        if self._monitor is not None:
            self._monitor.log_line(line)

    def _emit_progress(self, percent: float | None, detail: str = "") -> None:
        if self._monitor is not None:
            self._monitor.progress(percent, detail)

    def run_guarded(self, ctx: Context) -> list[Result]:
        """The full safe-execution flow. Returns one Result per phase."""
        phase_results: list[Result] = []

        # Phase 2: dry-run always happens and is shown.
        dr = self._tag(self._safe(self.dry_run, ctx, f"{self.name}.dry-run"))
        phase_results.append(dr)
        if ctx.dry_run:
            return phase_results  # stop here in dry-run mode (the default)

        # Phase 3: confirmation gate (unless --yes).
        if not ctx.assume_yes:
            phase_results.append(
                self._tag(
                    Result.skip(f"{self.name}.execute", "awaiting confirmation (--yes not set)")
                )
            )
            return phase_results

        # Phase 3b: customer-confirmation gate. Irreversible customer-impacting
        # steps (e.g. stopping the source application servers) must not run until
        # the customer has explicitly signed off — set the ``customer_confirmed``
        # param truthy. Without it we stop here with a clear SKIP.
        if self.requires_customer_confirmation and not _truthy(ctx.get("customer_confirmed")):
            phase_results.append(
                self._tag(
                    Result.skip(
                        f"{self.name}.execute",
                        "awaiting CUSTOMER confirmation — this step impacts the "
                        "customer system and must not run until the customer has "
                        "signed off (set customer_confirmed=true once they have)",
                    )
                )
            )
            return phase_results

        # Phase 4: execute.
        ex = self._tag(self._safe(self.execute, ctx, f"{self.name}.execute"))
        phase_results.append(ex)
        if ex.status.is_blocking:
            enrich(ex, ctx)
            return phase_results  # do NOT verify a failed execute

        # Phase 5: verify.
        phase_results.append(self._tag(self._safe(self.verify, ctx, f"{self.name}.verify")))
        return phase_results

    def _tag(self, result: Result) -> Result:
        """Stamp this action's phase/title onto a phase result (if unset)."""
        if result.phase is Phase.UNCLASSIFIED and self.phase is not Phase.UNCLASSIFIED:
            result.phase = self.phase
        if not result.title and self.title:
            result.title = self.title
        return result

    @staticmethod
    def _safe(fn, ctx: Context, name: str) -> Result:  # type: ignore[no-untyped-def]
        started = datetime.now(UTC)
        try:
            result: Result = fn(ctx)
        except Exception as exc:  # noqa: BLE001
            log.exception("%s raised", name)
            result = Result.error(name, f"unexpected error: {exc}")
        return result.stamp_timing(started, datetime.now(UTC))


def _truthy(value: object) -> bool:
    """Interpret a config/param value as a boolean confirmation flag.

    Accepts real booleans and the usual string spellings (true/yes/y/1/on) so a
    YAML ``customer_confirmed: true`` and a CLI ``--param customer_confirmed=yes``
    both work.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "y", "1", "on")
