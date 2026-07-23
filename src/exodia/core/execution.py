"""Phased execution engine — check -> gate -> action -> gate, per phase.

The readiness runbooks (``Runbook`` + ``run_runbook``) answer *"is the system
ready?"* by chaining read-only checks into one verdict. This module answers the
next question — *"walk the cutover, phase by phase, and only proceed when the
gate says GO"* — by chaining **checks AND guarded actions**, with the gate
engine deciding between phases whether it is safe to advance.

Design (mirrors how a migration lead actually runs a go-live):

1. Phases run in cutover order: Preparation -> Ramp-Down -> Downtime -> Post.
2. Within a phase: run the phase's checks first (evidence), then evaluate the
   **phase gate** over those results using the engagement's ``GatePolicy`` +
   any audited ``Override`` s.
3. The gate is the guard-rail: if it is **NO-GO**, the phase's state-changing
   actions do NOT run and the plan stops (a blocking finding is open and
   un-overridden). GO / GO_WITH_OVERRIDE lets the actions run; PENDING (nothing
   graded) is treated as "not proven safe" and also halts, never silently
   proceeds.
4. Actions run through the base ``Action.run_guarded`` flow, so **dry-run is the
   default**: nothing changes a system unless the Context explicitly opts in
   (``dry_run=False`` + confirmation). The engine never bypasses that guard.

The engine is deliberately thin: it does not re-implement gate logic (that lives
in :mod:`exodia.core.gate`) nor action safety (that lives in
:class:`exodia.core.base.Action`). It orchestrates the two so a whole cutover can
be driven — and audited — as one gated pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .base import Action, Check
from .gate import GatePolicy, GateVerdict, Override, evaluate_gate
from .logging import get_logger
from .result import Phase, Result
from .runner import run_checks

if TYPE_CHECKING:
    from .context import Context
    from .evidence import EvidenceBundle

log = get_logger()

#: cutover order — the phases the engine walks, in sequence.
PHASE_ORDER: list[Phase] = [
    Phase.PREPARATION,
    Phase.RAMP_DOWN,
    Phase.DOWNTIME,
    Phase.POST,
]


@dataclass
class PhaseStep:
    """One phase of an execution plan: its checks and its guarded actions.

    ``checks`` are read-only evidence gathered *before* the gate. ``actions`` are
    the state-changing steps that only run if the gate opens.
    """

    phase: Phase
    checks: list[Check] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)


@dataclass
class PhaseOutcome:
    """What happened in one phase: check results, the gate verdict, action results."""

    phase: Phase
    check_results: list[Result]
    gate: GateVerdict
    action_results: list[Result]
    #: True when the gate opened and the actions were allowed to run.
    proceeded: bool


@dataclass
class ExecutionReport:
    """The full result of walking an execution plan."""

    outcomes: list[PhaseOutcome]
    #: True when every walked phase's gate opened (the plan ran to completion).
    completed: bool
    #: the phase the plan halted on (NO-GO / PENDING), or None if it completed.
    halted_on: Phase | None

    def all_results(self) -> list[Result]:
        """Every check + action result across all phases, in order."""
        out: list[Result] = []
        for o in self.outcomes:
            out.extend(o.check_results)
            out.extend(o.action_results)
        return out

    def gate_verdicts(self) -> list[GateVerdict]:
        return [o.gate for o in self.outcomes]


class ExecutionPlan:
    """An ordered, gated cutover plan: phases of checks + guarded actions.

    Build one by declaring the steps per phase, then :meth:`run` it against a
    Context. The engine walks phases in cutover order, gating each transition.
    """

    def __init__(self, name: str, steps: list[PhaseStep]) -> None:
        self.name = name
        # keep only the phases that have something to do, in cutover order
        by_phase = {s.phase: s for s in steps}
        self.steps: list[PhaseStep] = [
            by_phase[p] for p in PHASE_ORDER if p in by_phase
        ]

    def run(
        self,
        ctx: Context,
        *,
        policy: GatePolicy | None = None,
        overrides: list[Override] | None = None,
        evidence: EvidenceBundle | None = None,
    ) -> ExecutionReport:
        """Walk the plan phase by phase, gating each transition.

        For each phase: run checks -> evaluate the gate -> run actions only if the
        gate opens. Stop at the first phase whose gate does not open (NO-GO or
        PENDING). Actions run guarded, so dry-run is still the default.
        """
        policy = policy or GatePolicy.from_context(ctx)
        overrides = list(overrides or [])
        outcomes: list[PhaseOutcome] = []
        halted_on: Phase | None = None

        for step in self.steps:
            check_results = run_checks(step.checks, ctx, evidence)
            gate = evaluate_gate(step.phase, check_results, policy, overrides)

            if not gate.decision.is_go:
                # gate closed (NO-GO) or nothing proven (PENDING): do NOT run
                # this phase's state-changing actions, and stop the plan.
                log.warning(
                    "plan %s: phase %s gate = %s — halting before actions",
                    self.name,
                    step.phase.value,
                    gate.decision.value,
                )
                outcomes.append(
                    PhaseOutcome(
                        phase=step.phase,
                        check_results=check_results,
                        gate=gate,
                        action_results=[],
                        proceeded=False,
                    )
                )
                halted_on = step.phase
                break

            action_results: list[Result] = []
            for action in step.actions:
                phase_results = action.run_guarded(ctx)
                action_results.extend(phase_results)
                if evidence is not None:
                    evidence.add_results(phase_results)
                # a blocking failure inside a guarded action stops the phase:
                # we must not run later actions on a broken step.
                if any(r.status.is_blocking for r in phase_results):
                    log.warning(
                        "plan %s: action %s failed in phase %s — stopping phase",
                        self.name,
                        action.name,
                        step.phase.value,
                    )
                    halted_on = step.phase
                    outcomes.append(
                        PhaseOutcome(
                            phase=step.phase,
                            check_results=check_results,
                            gate=gate,
                            action_results=action_results,
                            proceeded=True,
                        )
                    )
                    return ExecutionReport(
                        outcomes=outcomes, completed=False, halted_on=halted_on
                    )

            outcomes.append(
                PhaseOutcome(
                    phase=step.phase,
                    check_results=check_results,
                    gate=gate,
                    action_results=action_results,
                    proceeded=True,
                )
            )

        completed = halted_on is None
        return ExecutionReport(
            outcomes=outcomes, completed=completed, halted_on=halted_on
        )


def summarize(report: ExecutionReport) -> str:
    """A one-line-per-phase human summary of an execution walk."""
    lines: list[str] = []
    for o in report.outcomes:
        icon = o.gate.decision.icon
        detail = "ran actions" if o.proceeded else "halted (no actions)"
        lines.append(
            f"{icon} {o.phase.value}: gate={o.gate.decision.value} "
            f"({len(o.check_results)} checks, {len(o.action_results)} action results) "
            f"— {detail}"
        )
    if report.completed:
        lines.append("PLAN COMPLETE — every phase gate opened.")
    else:
        lines.append(f"PLAN HALTED at phase: {report.halted_on.value if report.halted_on else '?'}")
    return "\n".join(lines)
