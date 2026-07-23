"""Tests for the phased execution engine (check -> gate -> action -> gate)."""

from __future__ import annotations

from exodia.core.base import Action, Check
from exodia.core.context import Context
from exodia.core.execution import (
    PHASE_ORDER,
    ExecutionPlan,
    PhaseStep,
    summarize,
)
from exodia.core.gate import GateDecision, GatePolicy, Override
from exodia.core.result import Phase, Result, Severity

# --- fakes ---------------------------------------------------------------- #


class _PassCheck(Check):
    name = "fake.pass"
    phase = Phase.PREPARATION

    def run(self, ctx: Context) -> Result:
        return Result.ok(self.name, "all good")


class _BlockingFailCheck(Check):
    name = "fake.block"
    phase = Phase.PREPARATION
    severity = Severity.BLOCKING

    def run(self, ctx: Context) -> Result:
        return Result.fail(self.name, "a real blocker")


class _AdvisoryFailCheck(Check):
    name = "fake.advisory"
    phase = Phase.PREPARATION
    severity = Severity.ADVISORY

    def run(self, ctx: Context) -> Result:
        return Result.fail(self.name, "hygiene issue, not a blocker")


class _RecordingAction(Action):
    """An action that records whether its execute() actually ran."""

    name = "fake.action"
    phase = Phase.PREPARATION
    destructive = True
    executed: bool = False

    def dry_run(self, ctx: Context) -> Result:
        return Result.ok(f"{self.name}.dry-run", "would do the thing")

    def execute(self, ctx: Context) -> Result:
        type(self).executed = True
        return Result.ok(f"{self.name}.execute", "did the thing")

    def verify(self, ctx: Context) -> Result:
        return Result.ok(f"{self.name}.verify", "confirmed")


class _FailingAction(Action):
    name = "fake.failaction"
    phase = Phase.DOWNTIME
    destructive = True

    def dry_run(self, ctx: Context) -> Result:
        return Result.ok(f"{self.name}.dry-run", "would try")

    def execute(self, ctx: Context) -> Result:
        return Result.fail(f"{self.name}.execute", "boom")

    def verify(self, ctx: Context) -> Result:  # pragma: no cover - never reached
        return Result.ok(f"{self.name}.verify", "should not run")


def _reset() -> None:
    _RecordingAction.executed = False


# --- tests ---------------------------------------------------------------- #


def test_gate_go_lets_actions_run_but_dry_run_is_default() -> None:
    """A clean gate opens; actions run guarded, and dry-run is the default."""
    _reset()
    plan = ExecutionPlan(
        "t",
        [PhaseStep(Phase.PREPARATION, checks=[_PassCheck()], actions=[_RecordingAction()])],
    )
    ctx = Context()  # dry_run defaults True
    report = plan.run(ctx)

    assert report.completed is True
    assert report.halted_on is None
    outcome = report.outcomes[0]
    assert outcome.gate.decision is GateDecision.GO
    assert outcome.proceeded is True
    # dry-run is the default: execute() must NOT have fired
    assert _RecordingAction.executed is False
    # but the dry-run phase result IS present
    assert any("dry-run" in r.name for r in outcome.action_results)


def test_execute_runs_only_when_opted_in() -> None:
    """With dry_run=False + assume_yes, the guarded action actually executes."""
    _reset()
    plan = ExecutionPlan(
        "t",
        [PhaseStep(Phase.PREPARATION, checks=[_PassCheck()], actions=[_RecordingAction()])],
    )
    ctx = Context(dry_run=False, assume_yes=True)
    report = plan.run(ctx)

    assert report.completed is True
    assert _RecordingAction.executed is True
    assert any(r.name.endswith(".verify") for r in report.outcomes[0].action_results)


def test_blocking_gate_halts_before_actions() -> None:
    """A blocking-open finding => NO-GO => actions do NOT run and the plan halts."""
    _reset()
    plan = ExecutionPlan(
        "t",
        [
            PhaseStep(
                Phase.PREPARATION,
                checks=[_PassCheck(), _BlockingFailCheck()],
                actions=[_RecordingAction()],
            )
        ],
    )
    ctx = Context(dry_run=False, assume_yes=True)
    report = plan.run(ctx)

    assert report.completed is False
    assert report.halted_on is Phase.PREPARATION
    outcome = report.outcomes[0]
    assert outcome.gate.decision is GateDecision.NO_GO
    assert outcome.proceeded is False
    assert outcome.action_results == []
    # crucially: the destructive action never fired
    assert _RecordingAction.executed is False


def test_advisory_failure_does_not_block() -> None:
    """An ADVISORY failure never closes the gate — actions still run."""
    _reset()
    plan = ExecutionPlan(
        "t",
        [
            PhaseStep(
                Phase.PREPARATION,
                checks=[_PassCheck(), _AdvisoryFailCheck()],
                actions=[_RecordingAction()],
            )
        ],
    )
    ctx = Context(dry_run=False, assume_yes=True)
    report = plan.run(ctx)

    assert report.completed is True
    outcome = report.outcomes[0]
    assert outcome.gate.decision is GateDecision.GO
    assert outcome.proceeded is True
    assert _RecordingAction.executed is True
    # the advisory is captured for the exception report
    assert "fake.advisory" in outcome.gate.advisories


def test_override_turns_no_go_into_go_with_override() -> None:
    """An audited override of the only blocker => GO_WITH_OVERRIDE, actions run."""
    _reset()
    plan = ExecutionPlan(
        "t",
        [
            PhaseStep(
                Phase.PREPARATION,
                checks=[_BlockingFailCheck()],
                actions=[_RecordingAction()],
            )
        ],
    )
    ctx = Context(dry_run=False, assume_yes=True)
    policy = GatePolicy(overridable=["fake.block"])
    overrides = [
        Override(
            check="fake.block",
            who="migration-lead",
            reason="risk accepted, compensating control in place",
            phase=Phase.PREPARATION,
        )
    ]
    report = plan.run(ctx, policy=policy, overrides=overrides)

    outcome = report.outcomes[0]
    assert outcome.gate.decision is GateDecision.GO_WITH_OVERRIDE
    assert outcome.proceeded is True
    assert _RecordingAction.executed is True


def test_pending_gate_halts() -> None:
    """A phase where everything is skipped => PENDING => halt, never proceed."""
    _reset()
    plan = ExecutionPlan(
        "t",
        [PhaseStep(Phase.PREPARATION, checks=[_PassCheck()], actions=[_RecordingAction()])],
    )
    ctx = Context(dry_run=False, assume_yes=True, skip_checks={"fake.pass"})
    report = plan.run(ctx)

    assert report.completed is False
    assert report.halted_on is Phase.PREPARATION
    assert report.outcomes[0].gate.decision is GateDecision.PENDING
    assert _RecordingAction.executed is False


def test_phases_run_in_cutover_order() -> None:
    """Steps declared out of order are walked in canonical cutover order."""
    plan = ExecutionPlan(
        "t",
        [
            PhaseStep(Phase.POST, checks=[_PassCheck()]),
            PhaseStep(Phase.PREPARATION, checks=[_PassCheck()]),
            PhaseStep(Phase.DOWNTIME, checks=[_PassCheck()]),
        ],
    )
    ctx = Context()
    report = plan.run(ctx)
    walked = [o.phase for o in report.outcomes]
    assert walked == [Phase.PREPARATION, Phase.DOWNTIME, Phase.POST]
    # and it respects the global PHASE_ORDER ranking
    assert walked == [p for p in PHASE_ORDER if p in walked]


def test_failing_action_stops_the_phase() -> None:
    """A blocking failure inside a guarded action halts the plan mid-phase."""
    plan = ExecutionPlan(
        "t",
        [PhaseStep(Phase.DOWNTIME, checks=[_PassCheck()], actions=[_FailingAction()])],
    )
    ctx = Context(dry_run=False, assume_yes=True)
    report = plan.run(ctx)

    assert report.completed is False
    assert report.halted_on is Phase.DOWNTIME
    outcome = report.outcomes[0]
    assert outcome.proceeded is True  # gate opened, action ran
    assert any(r.status.is_blocking for r in outcome.action_results)


def test_summarize_is_human_readable() -> None:
    plan = ExecutionPlan(
        "t",
        [PhaseStep(Phase.PREPARATION, checks=[_PassCheck()])],
    )
    report = plan.run(Context())
    text = summarize(report)
    assert "preparation" in text
    assert "PLAN COMPLETE" in text
