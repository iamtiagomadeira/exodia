"""Tests for the `exodia cutover-plan` playbook command and the config templates.

The playbook is a read-only reference card; these tests assert it renders the
four phases in order with the safety gates flagged, and that every command it
prints references a real registered operation. Also validates the example
config templates load against the schema.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from exodia.cli import _CUTOVER_PLAN, app
from exodia.core.context import Context
from exodia.core.registry import registry

runner = CliRunner()


def test_cutover_plan_runs_and_shows_four_phases() -> None:
    result = runner.invoke(app, ["cutover-plan"])
    assert result.exit_code == 0
    for phase in (
        "Preparation Phase",
        "Ramp-Down Phase",
        "Downtime / Execution Phase",
        "Post-Activities Phase",
    ):
        assert phase in result.output


def test_cutover_plan_flags_the_gates() -> None:
    result = runner.invoke(app, ["cutover-plan"])
    # the customer-confirmation gate and the manual step must be called out
    assert "customer_confirmed=true" in result.output
    assert "MANUAL" in result.output
    assert "type the target tenant name" in result.output.lower()


def test_cutover_plan_phases_in_cutover_order() -> None:
    out = runner.invoke(app, ["cutover-plan"]).output
    order = [
        out.index("Preparation Phase"),
        out.index("Ramp-Down Phase"),
        out.index("Downtime / Execution Phase"),
        out.index("Post-Activities Phase"),
    ]
    assert order == sorted(order)


def test_cutover_plan_commands_reference_real_operations() -> None:
    """Every 'exodia run <op>' / 'exodia runbook <rb>' in the plan must resolve."""
    checks = set(registry.checks())
    actions = set(registry.actions())
    runbooks = set(registry.runbooks())
    for _phase, _sub, steps in _CUTOVER_PLAN:
        for _label, cmd, _note in steps:
            parts = cmd.split()
            if "run" in parts:
                op = parts[parts.index("run") + 1]
                assert op in actions or op in checks, f"unknown op in plan: {op}"
            elif "runbook" in parts:
                rb = parts[parts.index("runbook") + 1]
                assert rb in runbooks, f"unknown runbook in plan: {rb}"


def test_example_configs_load() -> None:
    examples = Path(__file__).resolve().parent.parent / "examples"
    for name in ("tenant-copy.yaml", "abap-ramp-down.yaml", "abap-post-activities.yaml"):
        ctx = Context.from_file(examples / name)
        assert ctx is not None
