"""Tests for the severity-gate engine (COP-driven gate model).

Covers the three pillars from ``COP_model.md``:
  * Severity — the three intrinsic roles + backward-compat derivation from the
    legacy ``blocking: bool`` flag, and stamping onto results via Check.execute.
  * GatePolicy — per-engagement reclassification, forbid_override (supervised
    mode), and the overridable allow-list.
  * evaluate_gate — GO / NO-GO / GO_WITH_OVERRIDE / PENDING decisions, with the
    core COP rule that advisory failures never block.
  * ExceptionReport — the exportable advisory artifact (Markdown + terminal),
    advisory selection, and the override audit trail.
"""

from __future__ import annotations

from exodia.core.base import Check
from exodia.core.context import Context
from exodia.core.gate import (
    GateDecision,
    GatePolicy,
    Override,
    evaluate_gate,
)
from exodia.core.gate_report import ExceptionReport
from exodia.core.result import Phase, Result, Side
from exodia.core.severity import Severity

# --------------------------------------------------------------------------- #
# Severity
# --------------------------------------------------------------------------- #


def test_severity_gates_only_blocking() -> None:
    assert Severity.BLOCKING.gates is True
    assert Severity.ADVISORY.gates is False
    assert Severity.INFO.gates is False


def test_severity_rank_order() -> None:
    assert Severity.BLOCKING.rank > Severity.ADVISORY.rank > Severity.INFO.rank


def test_from_check_prefers_explicit_severity() -> None:
    class C:
        severity = Severity.INFO
        blocking = True  # explicit severity must win over the legacy flag

    assert Severity.from_check(C()) is Severity.INFO


def test_from_check_derives_from_legacy_blocking_true() -> None:
    class C:
        blocking = True

    assert Severity.from_check(C()) is Severity.BLOCKING


def test_from_check_derives_from_legacy_blocking_false() -> None:
    class C:
        blocking = False

    # A non-blocking FAIL was always, semantically, advisory.
    assert Severity.from_check(C()) is Severity.ADVISORY


def test_from_check_accepts_string_severity() -> None:
    class C:
        severity = "blocking"

    assert Severity.from_check(C()) is Severity.BLOCKING


def test_coerce_parses_and_rejects() -> None:
    assert Severity.coerce("advisory") is Severity.ADVISORY
    assert Severity.coerce("nonsense") is None
    assert Severity.coerce(42) is None


# --------------------------------------------------------------------------- #
# Severity stamping via Check.execute (backward compat with real checks)
# --------------------------------------------------------------------------- #


class _BlockingLegacyCheck(Check):
    name = "test.legacy-blocking"
    blocking = True
    phase = Phase.PREPARATION

    def run(self, ctx: Context) -> Result:
        return Result.fail(self.name, "boom")


class _AdvisoryNewCheck(Check):
    name = "test.new-advisory"
    severity = Severity.ADVISORY
    side = Side.SOURCE
    responsible = "customer"
    phase = Phase.POST

    def run(self, ctx: Context) -> Result:
        return Result.fail(self.name, "hygiene finding")


def test_execute_stamps_derived_severity_from_legacy_flag() -> None:
    res = _BlockingLegacyCheck().execute(Context())
    assert res.severity is Severity.BLOCKING


def test_execute_stamps_declared_severity_and_axes() -> None:
    res = _AdvisoryNewCheck().execute(Context())
    assert res.severity is Severity.ADVISORY
    assert res.side is Side.SOURCE
    assert res.responsible == "customer"
    assert res.phase is Phase.POST


# --------------------------------------------------------------------------- #
# GatePolicy
# --------------------------------------------------------------------------- #


def _adv(name: str, phase: Phase = Phase.PREPARATION) -> Result:
    r = Result.fail(name, "advisory finding")
    r.severity = Severity.ADVISORY
    r.phase = phase
    return r


def _block(name: str, phase: Phase = Phase.PREPARATION) -> Result:
    r = Result.fail(name, "blocking finding")
    r.severity = Severity.BLOCKING
    r.phase = phase
    return r


def test_policy_reclassifies_advisory_to_blocking() -> None:
    pol = GatePolicy(reclassify={"abap.st22": Severity.BLOCKING})
    assert pol.severity_of(_adv("abap.st22")) is Severity.BLOCKING


def test_policy_default_keeps_intrinsic_severity() -> None:
    pol = GatePolicy()
    assert pol.severity_of(_adv("abap.st22")) is Severity.ADVISORY


def test_policy_forbid_override_supervised_mode() -> None:
    pol = GatePolicy(forbid_override=True)
    assert pol.may_override("anything") is False


def test_policy_overridable_allow_list() -> None:
    pol = GatePolicy(overridable=["backup.recoverable"])
    assert pol.may_override("backup.recoverable") is True
    assert pol.may_override("hsr.sync") is False


def test_policy_default_allows_any_override() -> None:
    assert GatePolicy().may_override("whatever") is True


def test_policy_from_context_parses_gate_block() -> None:
    ctx = Context(
        params={
            "gate": {
                "reclassify": {"abap.st22": "blocking", "bad": "nonsense"},
                "forbid_override": True,
                "overridable": ["x"],
            }
        }
    )
    pol = GatePolicy.from_context(ctx)
    assert pol.reclassify == {"abap.st22": Severity.BLOCKING}  # bad entry dropped
    assert pol.forbid_override is True
    assert pol.overridable == ["x"]


def test_policy_from_context_empty_is_default() -> None:
    pol = GatePolicy.from_context(Context())
    assert pol.reclassify == {}
    assert pol.forbid_override is False


# --------------------------------------------------------------------------- #
# evaluate_gate
# --------------------------------------------------------------------------- #


def test_gate_go_when_clean() -> None:
    v = evaluate_gate(Phase.PREPARATION, [Result.ok("a"), Result.ok("b")])
    assert v.decision is GateDecision.GO
    assert v.passed == 2
    assert v.blocking_open == []


def test_gate_no_go_on_open_blocker() -> None:
    v = evaluate_gate(Phase.PREPARATION, [_block("backup"), Result.ok("b")])
    assert v.decision is GateDecision.NO_GO
    assert v.blocking_open == ["backup"]


def test_gate_advisory_fail_never_blocks() -> None:
    # The core COP rule: an advisory FAIL does NOT produce a NO-GO.
    v = evaluate_gate(Phase.POST, [_adv("abap.st22"), Result.ok("b")])
    assert v.decision is GateDecision.GO
    assert v.advisories == ["abap.st22"]
    assert v.blocking_open == []


def test_gate_go_with_override() -> None:
    ov = [Override(check="backup", reason="verified out-of-band", who="TM")]
    v = evaluate_gate(Phase.PREPARATION, [_block("backup")], overrides=ov)
    assert v.decision is GateDecision.GO_WITH_OVERRIDE
    assert v.decision.is_go is True
    assert len(v.overrides) == 1
    assert v.blocking_open == []


def test_gate_pending_when_all_skipped() -> None:
    v = evaluate_gate(Phase.PREPARATION, [Result.skip("a", "n/a")])
    assert v.decision is GateDecision.PENDING
    assert v.total_graded == 0


def test_gate_policy_reclassify_forces_no_go() -> None:
    # A customer who wants ST22 treated as blocking flips an advisory to NO-GO.
    pol = GatePolicy(reclassify={"abap.st22": Severity.BLOCKING})
    v = evaluate_gate(Phase.PREPARATION, [_adv("abap.st22")], policy=pol)
    assert v.decision is GateDecision.NO_GO
    assert v.blocking_open == ["abap.st22"]


def test_gate_mixed_blocker_and_advisory() -> None:
    v = evaluate_gate(
        Phase.PREPARATION,
        [_block("hsr.sync"), _adv("abap.st22"), Result.ok("space")],
    )
    assert v.decision is GateDecision.NO_GO
    assert v.blocking_open == ["hsr.sync"]
    assert v.advisories == ["abap.st22"]
    assert v.passed == 1


def test_gate_warn_is_advisory_not_blocking() -> None:
    r = Result.warn("timezone", "minor drift")
    r.severity = Severity.BLOCKING  # even a blocking-role WARN is not is_blocking
    v = evaluate_gate(Phase.PREPARATION, [r])
    # WARN is not a blocking status, so it feeds advisories, never a NO-GO.
    assert v.decision is GateDecision.GO
    assert v.advisories == ["timezone"]


def test_verdict_one_line_and_summary() -> None:
    v = evaluate_gate(Phase.PREPARATION, [_block("backup")])
    assert "NO-GO" in v.summary.upper() or "NO_GO" in v.summary.upper()
    assert isinstance(v.one_line(), str)


# --------------------------------------------------------------------------- #
# Override
# --------------------------------------------------------------------------- #


def test_override_requires_reason_and_who() -> None:
    o = Override(check="backup", reason="verified elsewhere", who="TM")
    assert o.check == "backup"
    assert "backup" in o.one_line()
    assert "TM" in o.one_line()


# --------------------------------------------------------------------------- #
# ExceptionReport
# --------------------------------------------------------------------------- #


def _sample_report() -> ExceptionReport:
    r_block = _block("backup.recoverable")
    r_block.side = Side.SOURCE
    r_block.responsible = "migration-team"
    r_adv = _adv("abap.st22-dumps")
    r_adv.side = Side.SOURCE
    r_adv.responsible = "customer"
    r_adv_post = _adv("abap.se80-inactive", phase=Phase.POST)
    r_ok = Result.ok("hana.target-space")
    ov = [Override(check="backup.recoverable", reason="verified out-of-band", who="TM")]
    vp = evaluate_gate(Phase.PREPARATION, [r_block, r_adv, r_ok], overrides=ov)
    vpost = evaluate_gate(Phase.POST, [r_adv_post])
    return ExceptionReport(
        [r_block, r_adv, r_adv_post, r_ok],
        [vp, vpost],
        overrides=ov,
        system="GENERIC-COPY",
        method="Export/Import",
    )


def test_report_advisories_excludes_blocking() -> None:
    rep = _sample_report()
    names = [r.name for r in rep._advisories()]
    assert "abap.st22-dumps" in names
    assert "abap.se80-inactive" in names
    assert "backup.recoverable" not in names  # blocking never an advisory


def test_report_markdown_contains_key_sections() -> None:
    md = _sample_report().to_markdown()
    assert "# SAP Migration Toolkit" in md
    assert "## Gate Summary" in md
    assert "## Advisories" in md
    assert "## Override Audit Trail" in md
    assert "abap.st22-dumps" in md
    assert "verified out-of-band" in md  # the override reason is on file


def test_report_markdown_shows_go_with_override() -> None:
    md = _sample_report().to_markdown()
    assert "GO WITH OVERRIDE" in md


def test_report_write_markdown(tmp_path: object) -> None:
    from pathlib import Path

    target = Path(str(tmp_path)) / "sub" / "report.md"
    path = _sample_report().write_markdown(str(target))
    assert Path(path).is_file()
    assert "Advisories" in Path(path).read_text(encoding="utf-8")


def test_report_terminal_renders_without_error() -> None:
    import io

    from rich.console import Console

    rep = _sample_report()
    # no_emoji path (CI / enterprise terminals) must not raise.
    rep.render_terminal(console=Console(file=io.StringIO(), force_terminal=False), no_emoji=True)


def test_report_no_advisories_message() -> None:
    v = evaluate_gate(Phase.PREPARATION, [Result.ok("a")])
    rep = ExceptionReport([Result.ok("a")], [v])
    md = rep.to_markdown()
    assert "No advisories" in md
