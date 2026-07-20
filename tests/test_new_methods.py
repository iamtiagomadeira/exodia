"""Tests for the new System Copy method modules and Solution Manager checks.

Covers export/import (SWPM/R3load/JLoad), HSR (version, log_mode, ports), and
Solution Manager post-copy (PCA, SLD/LMDB). All checks are read-only, so a
FakeRunner replays canned command output and we assert on the structured Result.
"""

from __future__ import annotations

from exodia.core import Context, Status
from exodia.core.shell import CommandResult, Runner
from exodia.modules.solution_manager.checks.preconditions import (
    LmdbReachableCheck,
    NoStaleSourceRegistrationCheck,
    PcaTaskListAvailableCheck,
    SldReachableCheck,
)
from exodia.modules.system_copy.export_import.checks.preconditions import (
    DbClientReachableCheck,
    ExportDirSpaceCheck,
    LoadToolForStackCheck,
    SwpmPresentCheck,
)
from exodia.modules.system_copy.hsr.checks.preconditions import (
    DistinctHostsCheck,
    LogModeNormalCheck,
    ReplicationPortsReachableCheck,
    VersionCompatibilityCheck,
)


class FakeRunner(Runner):
    """Replays a canned result (optionally different per call)."""

    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        results: list[CommandResult] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self._results = list(results or [])

    def run(
        self, argv: list[str], timeout: int = 300, input_text: str | None = None
    ) -> CommandResult:
        self.calls.append(argv)
        if self._results:
            return self._results.pop(0)
        return CommandResult(argv, self._exit_code, self._stdout, self._stderr)


def _ctx(runner: Runner, **kw: object) -> Context:
    class _FakeCtx(Context):
        def runner(self) -> Runner:  # type: ignore[override]
            return runner

    return _FakeCtx(**kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# export/import
# --------------------------------------------------------------------------- #


def test_swpm_present_pass() -> None:
    ctx = _ctx(FakeRunner(exit_code=0), params={"swpm_path": "/usr/sap/SWPM"})
    assert SwpmPresentCheck().run(ctx).status is Status.PASS


def test_swpm_present_fail() -> None:
    ctx = _ctx(FakeRunner(exit_code=1), params={"swpm_path": "/nope"})
    assert SwpmPresentCheck().run(ctx).status is Status.FAIL


def test_load_tool_java_needs_jload() -> None:
    # command -v jload.sh fails -> Java has no load tool
    ctx = _ctx(FakeRunner(exit_code=1), params={"stack": "java"})
    res = LoadToolForStackCheck().run(ctx)
    assert res.status is Status.FAIL
    assert "jload.sh" in res.data["missing"]


def test_load_tool_abap_ok() -> None:
    ctx = _ctx(FakeRunner(exit_code=0), params={"stack": "abap"})
    assert LoadToolForStackCheck().run(ctx).status is Status.PASS


def test_export_dir_space_insufficient() -> None:
    # df reports 50G avail; need 100G * 1.2 = 120G -> FAIL
    df = CommandResult(["df"], 0, "Avail\n50G\n", "")
    ctx = _ctx(FakeRunner(results=[df]), params={"export_dir": "/export", "export_size_gb": 100})
    res = ExportDirSpaceCheck().run(ctx)
    assert res.status is Status.FAIL


def test_export_dir_space_ok() -> None:
    df = CommandResult(["df"], 0, "Avail\n500G\n", "")
    ctx = _ctx(FakeRunner(results=[df]), params={"export_dir": "/export", "export_size_gb": 100})
    assert ExportDirSpaceCheck().run(ctx).status is Status.PASS


def test_db_client_skips_without_db_type() -> None:
    ctx = _ctx(FakeRunner(exit_code=0))
    assert DbClientReachableCheck().run(ctx).status is Status.SKIP


def test_db_client_warns_when_missing() -> None:
    ctx = _ctx(FakeRunner(exit_code=1), db_type="hana")
    assert DbClientReachableCheck().run(ctx).status is Status.WARN


# --------------------------------------------------------------------------- #
# HSR
# --------------------------------------------------------------------------- #


def test_hsr_version_secondary_lower_fails() -> None:
    primary = CommandResult(["hdbsql"], 0, '"2.00.067.00.1"\n', "")
    secondary = CommandResult(["hdbsql"], 0, '"2.00.059.00.1"\n', "")
    ctx = _ctx(FakeRunner(results=[primary, secondary]))
    res = VersionCompatibilityCheck().run(ctx)
    assert res.status is Status.FAIL


def test_hsr_version_secondary_equal_ok() -> None:
    v = '"2.00.067.00.1"\n'
    ctx = _ctx(
        FakeRunner(results=[CommandResult(["x"], 0, v, ""), CommandResult(["x"], 0, v, "")])
    )
    assert VersionCompatibilityCheck().run(ctx).status is Status.PASS


def test_hsr_log_mode_overwrite_fails() -> None:
    cr = CommandResult(["hdbsql"], 0, '"overwrite"\n', "")
    ctx = _ctx(FakeRunner(results=[cr]))
    assert LogModeNormalCheck().run(ctx).status is Status.FAIL


def test_hsr_log_mode_normal_ok() -> None:
    cr = CommandResult(["hdbsql"], 0, '"normal"\n', "")
    ctx = _ctx(FakeRunner(results=[cr]))
    assert LogModeNormalCheck().run(ctx).status is Status.PASS


def test_hsr_ports_unreachable_fails() -> None:
    ctx = _ctx(FakeRunner(exit_code=1), params={"secondary_host": "sec01", "instance": "00"})
    assert ReplicationPortsReachableCheck().run(ctx).status is Status.FAIL


def test_hsr_distinct_hosts_same_fails() -> None:
    ctx = _ctx(FakeRunner(), host="node1", params={"secondary_host": "node1"})
    assert DistinctHostsCheck().run(ctx).status is Status.FAIL


def test_hsr_distinct_hosts_different_ok() -> None:
    ctx = _ctx(FakeRunner(), host="node1", params={"secondary_host": "node2"})
    assert DistinctHostsCheck().run(ctx).status is Status.PASS


# --------------------------------------------------------------------------- #
# Solution Manager
# --------------------------------------------------------------------------- #


def test_pca_skips_without_sid() -> None:
    ctx = _ctx(FakeRunner(exit_code=0))
    assert PcaTaskListAvailableCheck().run(ctx).status is Status.SKIP


def test_pca_ok_with_toolchain() -> None:
    ctx = _ctx(FakeRunner(exit_code=0), sid="SOL")
    assert PcaTaskListAvailableCheck().run(ctx).status is Status.PASS


def test_sld_unreachable_fails() -> None:
    ctx = _ctx(FakeRunner(exit_code=1), params={"sld_host": "sld01", "sld_port": "50000"})
    assert SldReachableCheck().run(ctx).status is Status.FAIL


def test_sld_reachable_ok() -> None:
    ctx = _ctx(FakeRunner(exit_code=0), params={"sld_host": "sld01"})
    assert SldReachableCheck().run(ctx).status is Status.PASS


def test_lmdb_skips_without_host() -> None:
    ctx = _ctx(FakeRunner(exit_code=0))
    assert LmdbReachableCheck().run(ctx).status is Status.SKIP


def test_no_stale_registration_always_warns() -> None:
    ctx = _ctx(FakeRunner(), sid="SOL")
    assert NoStaleSourceRegistrationCheck().run(ctx).status is Status.WARN
