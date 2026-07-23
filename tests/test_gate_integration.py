"""Integration tests for the gate engine wiring (CLI + TUI + ABAP severities).

Complements ``test_gate_engine.py`` (which unit-tests the engine in isolation)
by proving the three integration points work end to end:
  * ``evaluate_all_gates`` bins results by phase and returns ordered verdicts.
  * The ABAP hygiene checks (ST22/SPAM/spool/transports) declare ADVISORY while
    the ramp-down quiesce checks (SM04/SM12/SM37) stay BLOCKING.
  * The CLI ``runbook --gate/--exceptions/--export`` flags render verdicts and
    write a Markdown exception report.
  * The TUI phase board renders a gate badge from the real engine.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from exodia.cli import app
from exodia.core.gate import GateDecision, evaluate_all_gates
from exodia.core.registry import registry
from exodia.core.result import Phase, Result
from exodia.core.severity import Severity

runner = CliRunner()


# --------------------------------------------------------------------------- #
# evaluate_all_gates — per-phase binning
# --------------------------------------------------------------------------- #


def _block(name: str, phase: Phase) -> Result:
    r = Result.fail(name, "blocking finding")
    r.severity = Severity.BLOCKING
    r.phase = phase
    return r


def _adv(name: str, phase: Phase) -> Result:
    r = Result.fail(name, "advisory finding")
    r.severity = Severity.ADVISORY
    r.phase = phase
    return r


def test_evaluate_all_gates_bins_by_phase_in_order() -> None:
    results = [
        _adv("post.hygiene", Phase.POST),
        _block("prep.backup", Phase.PREPARATION),
        Result.ok("rampdown.locks"),  # UNCLASSIFIED, ok
    ]
    results[2].phase = Phase.RAMP_DOWN
    verdicts = evaluate_all_gates(results)
    # Ordered by phase: Preparation, Ramp-Down, Post.
    phases = [v.phase for v in verdicts]
    assert phases == [Phase.PREPARATION, Phase.RAMP_DOWN, Phase.POST]


def test_evaluate_all_gates_decisions_per_phase() -> None:
    results = [
        _block("prep.backup", Phase.PREPARATION),
        _adv("post.hygiene", Phase.POST),
    ]
    verdicts = {v.phase: v for v in evaluate_all_gates(results)}
    assert verdicts[Phase.PREPARATION].decision is GateDecision.NO_GO
    # An advisory-only phase is GO — advisories never block.
    assert verdicts[Phase.POST].decision is GateDecision.GO


def test_evaluate_all_gates_empty() -> None:
    assert evaluate_all_gates([]) == []


# --------------------------------------------------------------------------- #
# ABAP severity reclassification (COP model: hygiene = advisory)
# --------------------------------------------------------------------------- #


def test_abap_hygiene_checks_are_advisory() -> None:
    registry.discover()
    hygiene = [
        "abap.readiness.short-dumps",
        "abap.readiness.spam-status",
        "abap.readiness.spool-requests",
        "abap.readiness.transport-requests",
    ]
    for name in hygiene:
        cls = registry.get_check(name)
        assert cls is not None, f"missing check {name}"
        assert Severity.from_check(cls()) is Severity.ADVISORY, name


def test_abap_rampdown_quiesce_checks_stay_blocking() -> None:
    registry.discover()
    blocking = [
        "abap.readiness.active-users",
        "abap.readiness.lock-entries",
        "abap.readiness.background-jobs",
    ]
    for name in blocking:
        cls = registry.get_check(name)
        assert cls is not None, f"missing check {name}"
        assert Severity.from_check(cls()) is Severity.BLOCKING, name


# --------------------------------------------------------------------------- #
# CLI wiring — runbook --gate / --exceptions / --export
# --------------------------------------------------------------------------- #


def _a_runbook_name() -> str:
    """Pick any discovered runbook name (the wiring is runbook-agnostic)."""
    registry.discover()
    names = sorted(registry.runbooks().keys())
    assert names, "no runbooks discovered"
    # Prefer an ABAP readiness runbook if present (has phased checks).
    for n in names:
        if "cutover" in n or "readiness" in n:
            return n
    return names[0]


def test_cli_runbook_gate_flag_renders_verdicts() -> None:
    rb = _a_runbook_name()
    result = runner.invoke(app, ["runbook", rb, "--gate", "--no-emoji"])
    # No live SAP connection => checks SKIP => PENDING gates, but the section
    # must render and the command must not crash.
    assert "Gate Verdicts" in result.output


def test_cli_runbook_exceptions_flag_renders_report() -> None:
    rb = _a_runbook_name()
    result = runner.invoke(app, ["runbook", rb, "--exceptions", "--no-emoji"])
    assert "Gate Verdicts" in result.output
    assert "Exception & Advisory Report" in result.output


def test_cli_runbook_export_writes_markdown(tmp_path: Path) -> None:
    rb = _a_runbook_name()
    out = tmp_path / "exceptions.md"
    result = runner.invoke(
        app, ["runbook", rb, "--export", str(out), "--no-emoji"]
    )
    assert out.is_file(), result.output
    text = out.read_text(encoding="utf-8")
    assert "# SAP Migration Toolkit" in text
    assert "Gate Summary" in text


def test_cli_runbook_no_gate_flag_is_silent() -> None:
    rb = _a_runbook_name()
    result = runner.invoke(app, ["runbook", rb, "--no-emoji"])
    # Default behaviour unchanged: no gate section without the flag.
    assert "Gate Verdicts" not in result.output


# --------------------------------------------------------------------------- #
# TUI wiring — phase board gate badge
# --------------------------------------------------------------------------- #


def test_tui_phase_gate_badge_reflects_engine() -> None:
    from exodia.tui.app import ExodiaTUI

    app_tui = ExodiaTUI()
    r_block = _block("backup.recoverable", Phase.PREPARATION)
    r_adv = _adv("abap.st22", Phase.POST)
    app_tui._results = [r_block, r_adv]
    app_tui._name_to_phase = {
        "backup.recoverable": "preparation",
        "abap.st22": "post",
    }
    # Preparation has an open blocker -> NO-GO; Post is advisory-only -> GO.
    assert "NO-GO" in app_tui._phase_gate_badge("preparation")
    assert "GO" in app_tui._phase_gate_badge("post")
    # A phase with no results yields no badge.
    assert app_tui._phase_gate_badge("downtime") == ""
