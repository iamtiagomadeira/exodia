"""Tests for the Export/Import (R3load/JLoad) system-copy method — Wave 2.

Covers the thin-orchestrator actions (SWPM export/import, dump transfer, target
DB statistics) and the read-only guard-rail / monitor checks that the method was
missing, plus the pure log/manifest parsers in ``_r3load``.

Hard invariants proven here (Exodia safety contract):

* registry auto-discovers every export-import op by name and each lands in the
  right lifecycle Phase (PREPARATION / RAMP_DOWN / DOWNTIME / POST);
* dry-run runs NOTHING (the FakeRunner records zero calls) — sapinst/rsync are
  never actually invoked in the default (dry-run) mode;
* every action command is argv (list[str]), never a shell string, and no secret
  is placed on the command line;
* the pure parsers (monitor log, TSK, count manifest) are deterministic and
  treat a failed package / row-count drop as a data-loss signal.

Everything is exercised with a FakeRunner or a tmp_path — no subprocess, no DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exodia.core import Context, Status
from exodia.core.registry import registry
from exodia.core.result import Phase
from exodia.core.shell import CommandResult, Runner
from exodia.modules.system_copy.export_import import _r3load as r
from exodia.modules.system_copy.export_import.actions.db_statistics import (
    TargetDbStatisticsAction,
)
from exodia.modules.system_copy.export_import.actions.swpm_export import (
    SwpmExportAction,
)
from exodia.modules.system_copy.export_import.actions.swpm_import import (
    SwpmImportAction,
)
from exodia.modules.system_copy.export_import.actions.transfer_export import (
    TransferExportAction,
)
from exodia.modules.system_copy.export_import.checks.downtime import (
    ExportMonitorCheck,
    ImportIntegrityCheck,
    ImportMonitorCheck,
)
from exodia.modules.system_copy.export_import.checks.post import DataConsistencyCheck
from exodia.modules.system_copy.export_import.checks.preparation import (
    MigrationKeyCheck,
    UnicodeCompatibilityCheck,
)


class FakeRunner(Runner):
    """Records argv calls (+ any stdin) and replays a canned result."""

    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        results: list[CommandResult] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self._results = list(results or [])

    def run(
        self, argv: list[str], timeout: int = 300, input_text: str | None = None
    ) -> CommandResult:
        self.calls.append(argv)
        self.inputs.append(input_text)
        if self._results:
            return self._results.pop(0)
        return CommandResult(argv, self._exit_code, self._stdout, self._stderr)


def _ctx(runner: Runner, **kw: object) -> Context:
    class _FakeCtx(Context):
        def runner(self) -> Runner:  # type: ignore[override]
            return runner

    return _FakeCtx(**kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Discovery + phase placement + argv-only metadata
# --------------------------------------------------------------------------- #

_EI_ACTIONS = [
    "export-import.swpm.export",
    "export-import.swpm.import",
    "export-import.transfer-export",
    "export-import.target.db-statistics",
]
_EI_CHECKS = [
    "export-import.source.db-consistency",
    "export-import.migration-key",
    "export-import.unicode-compatibility",
    "export-import.target.import-space",
    "export-import.r3load.table-splitting-plan",
    "export-import.source.quiesced-verify",
    "export-import.r3load.export-monitor",
    "export-import.r3load.import-monitor",
    "export-import.import-integrity",
    "export-import.data-consistency",
]


def test_all_export_import_actions_discovered() -> None:
    actions = registry.actions()
    for name in _EI_ACTIONS:
        assert name in actions, f"{name} not auto-discovered"


def test_all_export_import_checks_discovered() -> None:
    checks = registry.checks()
    for name in _EI_CHECKS:
        assert name in checks, f"{name} not auto-discovered"


def test_export_import_had_zero_actions_now_four() -> None:
    ei_actions = [n for n in registry.actions() if n.startswith("export-import.")]
    assert len(ei_actions) == 4


def test_phase_placement_matches_lifecycle() -> None:
    expected = {
        "export-import.migration-key": Phase.PREPARATION,
        "export-import.unicode-compatibility": Phase.PREPARATION,
        "export-import.target.import-space": Phase.PREPARATION,
        "export-import.source.db-consistency": Phase.PREPARATION,
        "export-import.r3load.table-splitting-plan": Phase.PREPARATION,
        "export-import.source.quiesced-verify": Phase.RAMP_DOWN,
        "export-import.r3load.export-monitor": Phase.DOWNTIME,
        "export-import.r3load.import-monitor": Phase.DOWNTIME,
        "export-import.import-integrity": Phase.DOWNTIME,
        "export-import.swpm.export": Phase.DOWNTIME,
        "export-import.swpm.import": Phase.DOWNTIME,
        "export-import.transfer-export": Phase.DOWNTIME,
        "export-import.target.db-statistics": Phase.POST,
        "export-import.data-consistency": Phase.POST,
    }
    for name, phase in expected.items():
        cls = registry.get_check(name) or registry.get_action(name)
        assert cls is not None, name
        assert cls.phase is phase, f"{name} expected {phase}, got {cls.phase}"


def test_action_required_checks_resolve() -> None:
    for name in _EI_ACTIONS:
        cls = registry.get_action(name)
        assert cls is not None
        for rc in cls.requires_checks:
            assert registry.get_check(rc) is not None, f"{name} -> {rc}"


def test_import_requires_import_space_guardrail() -> None:
    assert "export-import.target.import-space" in SwpmImportAction.requires_checks


# --------------------------------------------------------------------------- #
# Pure parsers — deterministic, no runner
# --------------------------------------------------------------------------- #


def test_parse_monitor_log_completed_failed_and_progress() -> None:
    log = (
        "'SAPAPPL0' export rc == 0\n"
        "'SAPAPPL1' export took 0:12:31, rc == 0\n"
        "'SAPSSEXC' export with error, rc == 2\n"
        "2 jobs running\n"
    )
    report = r.parse_monitor_log(log)
    assert report.completed == ["SAPAPPL0", "SAPAPPL1"]
    assert report.failed == ["SAPSSEXC"]
    assert report.running == 2
    assert report.durations["SAPAPPL1"] == "0:12:31"
    assert report.has_errors is True
    # 2 completed of 3 seen -> 66%.
    assert report.progress_pct == pytest.approx(66.6, abs=0.5)


def test_parse_monitor_log_failure_wins_over_success_same_pkg() -> None:
    # A package that errored then printed a stray rc==0 must stay failed.
    log = "'SAPAPPL0' import with error, rc == 2\n'SAPAPPL0' import rc == 0\n"
    report = r.parse_monitor_log(log)
    assert report.failed == ["SAPAPPL0"]
    assert report.completed == []


def test_parse_tsk_counts_statuses() -> None:
    tsk = (
        "D SAPAPPL0 T ok\n"
        "P SAPAPPL0 I ok\n"
        "D SAPSSEXC T err\n"
        "D SAPPOOL  T xeq\n"
        "# comment line\n"
    )
    rep = r.parse_tsk(tsk)
    assert (rep.ok, rep.err, rep.xeq) == (2, 1, 1)
    assert rep.total == 4
    assert rep.all_ok is False


def test_parse_tsk_all_ok_true_only_when_clean() -> None:
    rep = r.parse_tsk("D A T ok\nP B I ok\n")
    assert rep.all_ok is True


def test_parse_count_manifest_tolerant() -> None:
    counts = r.parse_count_manifest("MARA,1234\nVBAK 56\n# skip\nbad-line\nBSEG;9\n")
    assert counts == {"MARA": 1234, "VBAK": 56, "BSEG": 9}


def test_compare_counts_flags_loss_and_mismatch() -> None:
    src = {"MARA": 100, "VBAK": 50, "T000": 3}
    tgt = {"MARA": 100, "VBAK": 49}  # VBAK short, T000 missing on target
    diff = r.compare_counts(src, tgt)
    assert diff.consistent is False
    assert "T000" in diff.only_source
    assert diff.mismatches["VBAK"] == (50, 49)


def test_compare_counts_consistent_when_matches() -> None:
    src = {"MARA": 100, "VBAK": 50}
    tgt = {"MARA": 100, "VBAK": 50, "EXTRA": 9}  # target-only is fine
    diff = r.compare_counts(src, tgt)
    assert diff.consistent is True
    assert diff.only_target == ["EXTRA"]


def test_is_heterogeneous_and_migration_key_shape() -> None:
    assert r.is_heterogeneous("oracle", "hana") is True
    assert r.is_heterogeneous("hana", "hana") is False
    assert r.is_heterogeneous(None, "hana") is False
    assert r.is_valid_migration_key("ABCD1234EFGH5678IJKL9012") is True
    assert r.is_valid_migration_key("short") is False
    assert r.is_valid_migration_key(None) is False


# --------------------------------------------------------------------------- #
# Dry-run: nothing executes; argv only; no secret on the command line
# --------------------------------------------------------------------------- #


def test_swpm_export_dry_run_runs_nothing(tmp_path: Path) -> None:
    # A minimal valid inifile so _prepare passes validation; the point is that
    # dry-run DESCRIBES the sapinst plan without ever invoking sapinst.
    ini = tmp_path / "inifile.params"
    ini.write_text(
        "SAPINST.CD.PACKAGE = /media\n"
        "NW_System.Type = ABAP\n"
        "SAPINST_EXECUTE_PRODUCT_ID = NW_Export\n"
    )
    runner = FakeRunner()
    ctx = _ctx(
        runner,
        params={
            "sapinst_path": "/usr/sap/SWPM/sapinst",
            "product_id": "NW_Export",
            "inifile": str(ini),
        },
    )
    res = SwpmExportAction().dry_run(ctx)
    assert res.status is Status.PASS
    assert runner.calls == []  # sapinst never invoked in dry-run


def test_transfer_export_dry_run_runs_nothing_and_argv_is_list() -> None:
    runner = FakeRunner()
    ctx = _ctx(
        runner,
        params={
            "export_dir": "/export",
            "import_dir": "/import",
            "transfer_target_host": "host1",
        },
    )
    res = TransferExportAction().dry_run(ctx)
    assert res.status is Status.PASS
    assert runner.calls == []
    argv = res.data["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "rsync"
    assert "host1:/import/" in argv[-1]


def test_db_statistics_dry_run_hana_argv_has_no_password() -> None:
    runner = FakeRunner()
    ctx = _ctx(runner, params={"target_db": "hana", "userstore_key": "EXODIAKEY"})
    res = TargetDbStatisticsAction().dry_run(ctx)
    assert res.status is Status.PASS
    assert runner.calls == []
    argv = res.data["argv"]
    # userstore key is a -U reference, never a password literal.
    assert "-U" in argv
    assert not any("PASS" in str(a).upper() for a in argv)


def test_db_statistics_dry_run_hana_missing_key_fails() -> None:
    runner = FakeRunner()
    ctx = _ctx(runner, params={"target_db": "hana"})
    res = TargetDbStatisticsAction().dry_run(ctx)
    assert res.status is Status.FAIL


# --------------------------------------------------------------------------- #
# Checks — pure preparation guard-rails (no runner needed)
# --------------------------------------------------------------------------- #


def test_migration_key_homogeneous_passes_without_key() -> None:
    runner = FakeRunner()
    ctx = _ctx(runner, params={"source_db": "hana", "target_db": "hana"})
    res = MigrationKeyCheck().run(ctx)
    assert res.status is Status.PASS


def test_migration_key_heterogeneous_without_key_fails() -> None:
    runner = FakeRunner()
    ctx = _ctx(runner, params={"source_db": "oracle", "target_db": "hana"})
    res = MigrationKeyCheck().run(ctx)
    assert res.status is Status.FAIL


def test_unicode_source_into_non_unicode_target_blocked() -> None:
    runner = FakeRunner()
    ctx = _ctx(runner, params={"source_unicode": "true", "target_unicode": "false"})
    res = UnicodeCompatibilityCheck().run(ctx)
    assert res.status is Status.FAIL


def test_unicode_conversion_warns_not_blocks() -> None:
    runner = FakeRunner()
    ctx = _ctx(runner, params={"source_unicode": "false", "target_unicode": "true"})
    res = UnicodeCompatibilityCheck().run(ctx)
    assert res.status is Status.WARN


# --------------------------------------------------------------------------- #
# Monitor / integrity checks — read a real log from tmp_path (local runner)
# --------------------------------------------------------------------------- #


def test_export_monitor_fails_on_failed_package(tmp_path: Path) -> None:
    log = tmp_path / "export_monitor.log"
    log.write_text("'SAPAPPL0' export rc == 0\n'SAPSSEXC' export with error, rc == 2\n")
    runner = FakeRunner()
    ctx = _ctx(runner, params={"export_monitor_log": str(log)})
    res = ExportMonitorCheck().run(ctx)
    assert res.status is Status.FAIL
    assert "SAPSSEXC" in res.summary


def test_export_monitor_warns_while_in_progress(tmp_path: Path) -> None:
    log = tmp_path / "export_monitor.log"
    log.write_text("'SAPAPPL0' export rc == 0\n3 jobs running\n")
    runner = FakeRunner()
    ctx = _ctx(runner, params={"export_monitor_log": str(log)})
    res = ExportMonitorCheck().run(ctx)
    assert res.status is Status.WARN


def test_import_monitor_ok_when_all_clean(tmp_path: Path) -> None:
    log = tmp_path / "import_monitor.log"
    log.write_text("'SAPAPPL0' import rc == 0\n'SAPAPPL1' import rc == 0\n")
    runner = FakeRunner()
    ctx = _ctx(runner, params={"import_monitor_log": str(log)})
    res = ImportMonitorCheck().run(ctx)
    assert res.status is Status.PASS


def test_import_monitor_skips_without_log() -> None:
    runner = FakeRunner()
    ctx = _ctx(runner, params={})
    res = ImportMonitorCheck().run(ctx)
    assert res.status is Status.SKIP


def test_import_integrity_ok_with_clean_log_and_tsk(tmp_path: Path) -> None:
    log = tmp_path / "import_monitor.log"
    log.write_text("'SAPAPPL0' import rc == 0\n")
    tsk = tmp_path / "SAPAPPL0.TSK"
    tsk.write_text("D SAPAPPL0 T ok\nP SAPAPPL0 I ok\n")
    runner = FakeRunner()
    ctx = _ctx(
        runner,
        params={"import_monitor_log": str(log), "tsk_dir": str(tmp_path)},
    )
    res = ImportIntegrityCheck().run(ctx)
    assert res.status is Status.PASS


def test_import_integrity_fails_on_err_task(tmp_path: Path) -> None:
    log = tmp_path / "import_monitor.log"
    log.write_text("'SAPAPPL0' import rc == 0\n")
    tsk = tmp_path / "SAPAPPL0.TSK"
    tsk.write_text("D SAPAPPL0 T ok\nP SAPAPPL0 I err\n")
    runner = FakeRunner()
    ctx = _ctx(
        runner,
        params={"import_monitor_log": str(log), "tsk_dir": str(tmp_path)},
    )
    res = ImportIntegrityCheck().run(ctx)
    assert res.status is Status.FAIL


# --------------------------------------------------------------------------- #
# Post — data-consistency detects row loss
# --------------------------------------------------------------------------- #


def test_data_consistency_ok_when_counts_match(tmp_path: Path) -> None:
    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text("MARA,100\nVBAK,50\n")
    tgt.write_text("MARA,100\nVBAK,50\n")
    runner = FakeRunner()
    ctx = _ctx(runner, params={"source_counts": str(src), "target_counts": str(tgt)})
    res = DataConsistencyCheck().run(ctx)
    assert res.status is Status.PASS


def test_data_consistency_fails_on_row_loss(tmp_path: Path) -> None:
    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text("MARA,100\nVBAK,50\n")
    tgt.write_text("MARA,100\nVBAK,49\n")
    runner = FakeRunner()
    ctx = _ctx(runner, params={"source_counts": str(src), "target_counts": str(tgt)})
    res = DataConsistencyCheck().run(ctx)
    assert res.status is Status.FAIL
