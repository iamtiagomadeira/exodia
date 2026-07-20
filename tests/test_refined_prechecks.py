"""Tests for the refined pre-checks (TIA-72).

Deepens the scaffold checks of export/import, HSR and Solution Manager with
real SAP logic: kernel-dir resolution of the load tool, export consistency via
keydb.xml, live replication status, backup-catalog probe, and managed-system
RFC connectivity. All checks are read-only, so a FakeRunner replays canned
command output and we assert on the structured Result.
"""

from __future__ import annotations

from exodia.core import Context, Status
from exodia.core.shell import CommandResult, Runner
from exodia.modules.solution_manager.checks.preconditions import (
    ManagedSystemConnectivityCheck,
)
from exodia.modules.system_copy.export_import.checks.preconditions import (
    ExportConsistencyCheck,
    LoadToolForStackCheck,
)
from exodia.modules.system_copy.hsr.checks.preconditions import (
    DataBackupExistsCheck,
    ReplicationStatusCheck,
)


class ScriptedRunner(Runner):
    """Return a CommandResult chosen by matching a substring in the argv.

    ``rules`` is an ordered list of (needle, CommandResult). The first needle
    found in the joined argv wins; otherwise a default OK/!OK result is used.
    """

    def __init__(
        self,
        rules: list[tuple[str, CommandResult]] | None = None,
        default_exit: int = 0,
        default_stdout: str = "",
    ) -> None:
        self.calls: list[list[str]] = []
        self._rules = list(rules or [])
        self._default_exit = default_exit
        self._default_stdout = default_stdout

    def run(
        self, argv: list[str], timeout: int = 300, input_text: str | None = None
    ) -> CommandResult:
        self.calls.append(argv)
        joined = " ".join(argv)
        for needle, result in self._rules:
            if needle in joined:
                return result
        return CommandResult(argv, self._default_exit, self._default_stdout, "")


def _ctx(runner: Runner, **kw: object) -> Context:
    class _FakeCtx(Context):
        def runner(self) -> Runner:  # type: ignore[override]
            return runner

    return _FakeCtx(**kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# export/import — kernel-dir load tool resolution
# --------------------------------------------------------------------------- #


def test_load_tool_found_in_kernel_dir() -> None:
    # $DIR_CT_RUN resolves; test -x <kdir>/R3load succeeds.
    runner = ScriptedRunner(
        rules=[
            ("echo $DIR_CT_RUN", CommandResult(["echo"], 0, "/usr/sap/ABC/SYS/exe/run\n", "")),
            ("/usr/sap/ABC/SYS/exe/run/R3load", CommandResult(["test"], 0, "", "")),
        ]
    )
    ctx = _ctx(runner, params={"stack": "abap"})
    res = LoadToolForStackCheck().run(ctx)
    assert res.status is Status.PASS
    assert res.data["kernel_dir"] == "/usr/sap/ABC/SYS/exe/run"
    assert "R3load" in res.data["in_kernel"]


def test_load_tool_falls_back_to_path_when_no_kernel_dir() -> None:
    # $DIR_CT_RUN empty -> resolve via command -v (default OK).
    runner = ScriptedRunner(
        rules=[("echo $DIR_CT_RUN", CommandResult(["echo"], 0, "\n", ""))],
        default_exit=0,
    )
    ctx = _ctx(runner, params={"stack": "abap"})
    res = LoadToolForStackCheck().run(ctx)
    assert res.status is Status.PASS
    assert res.data["kernel_dir"] is None


def test_load_tool_missing_everywhere_fails() -> None:
    runner = ScriptedRunner(
        rules=[("echo $DIR_CT_RUN", CommandResult(["echo"], 0, "\n", ""))],
        default_exit=1,  # command -v jload.sh fails
    )
    ctx = _ctx(runner, params={"stack": "java"})
    res = LoadToolForStackCheck().run(ctx)
    assert res.status is Status.FAIL
    assert "jload.sh" in res.data["missing"]


# --------------------------------------------------------------------------- #
# export/import — export consistency
# --------------------------------------------------------------------------- #


def test_export_consistency_no_payload_fails() -> None:
    runner = ScriptedRunner(default_exit=1)  # both test -d fail
    ctx = _ctx(runner, params={"export_dir": "/export"})
    assert ExportConsistencyCheck().run(ctx).status is Status.FAIL


def test_export_consistency_no_keydb_warns() -> None:
    runner = ScriptedRunner(
        rules=[
            ("ABAP/DATA", CommandResult(["test"], 0, "", "")),  # payload exists
            ("keydb.xml && echo yes", CommandResult(["sh"], 0, "", "")),  # no 'yes'
        ]
    )
    ctx = _ctx(runner, params={"export_dir": "/export"})
    assert ExportConsistencyCheck().run(ctx).status is Status.WARN


def test_export_consistency_complete_passes() -> None:
    runner = ScriptedRunner(
        rules=[
            ("ABAP/DATA", CommandResult(["test"], 0, "", "")),
            ("keydb.xml && echo yes", CommandResult(["sh"], 0, "yes\n", "")),
            ("grep -c", CommandResult(["sh"], 0, "3\n", "")),
        ]
    )
    ctx = _ctx(runner, params={"export_dir": "/export"})
    assert ExportConsistencyCheck().run(ctx).status is Status.PASS


# --------------------------------------------------------------------------- #
# HSR — replication status + backup catalog
# --------------------------------------------------------------------------- #


def test_replication_status_active_passes() -> None:
    out = "overall system replication status: ACTIVE\nmode: PRIMARY\n"
    runner = ScriptedRunner(rules=[("systemReplicationStatus", CommandResult(["sh"], 0, out, ""))])
    ctx = _ctx(runner, params={"instance": "00"})
    res = ReplicationStatusCheck().run(ctx)
    assert res.status is Status.PASS
    assert res.data["status"] == "ACTIVE"


def test_replication_status_syncing_fails() -> None:
    out = "overall system replication status: SYNCING\n"
    runner = ScriptedRunner(rules=[("systemReplicationStatus", CommandResult(["sh"], 0, out, ""))])
    ctx = _ctx(runner, params={"instance": "00"})
    res = ReplicationStatusCheck().run(ctx)
    assert res.status is Status.FAIL
    assert res.data["status"] == "SYNCING"


def test_replication_status_empty_skips() -> None:
    runner = ScriptedRunner(default_exit=0, default_stdout="")
    ctx = _ctx(runner, params={"instance": "00"})
    assert ReplicationStatusCheck().run(ctx).status is Status.SKIP


def test_data_backup_present_passes() -> None:
    runner = ScriptedRunner(rules=[("M_BACKUP_CATALOG", CommandResult(["hdbsql"], 0, "2\n", ""))])
    ctx = _ctx(runner)
    res = DataBackupExistsCheck().run(ctx)
    assert res.status is Status.PASS
    assert res.data["backup_count"] == 2


def test_data_backup_absent_fails() -> None:
    runner = ScriptedRunner(rules=[("M_BACKUP_CATALOG", CommandResult(["hdbsql"], 0, "0\n", ""))])
    ctx = _ctx(runner)
    assert DataBackupExistsCheck().run(ctx).status is Status.FAIL


# --------------------------------------------------------------------------- #
# Solution Manager — managed-system connectivity
# --------------------------------------------------------------------------- #


def test_managed_connectivity_skips_without_host() -> None:
    ctx = _ctx(ScriptedRunner())
    assert ManagedSystemConnectivityCheck().run(ctx).status is Status.SKIP


def test_managed_connectivity_gateway_open_passes() -> None:
    # nc to gateway 3300 succeeds.
    runner = ScriptedRunner(rules=[("3300", CommandResult(["nc"], 0, "", ""))])
    ctx = _ctx(runner, params={"managed_host": "sap01", "managed_instance": "00"})
    res = ManagedSystemConnectivityCheck().run(ctx)
    assert res.status is Status.PASS
    assert res.data["gateway_port"] == 3300


def test_managed_connectivity_only_http_warns() -> None:
    # gateway 3300 closed, HTTP 8000 open.
    runner = ScriptedRunner(
        rules=[
            ("3300", CommandResult(["nc"], 1, "", "")),
            ("8000", CommandResult(["nc"], 0, "", "")),
        ]
    )
    ctx = _ctx(runner, params={"managed_host": "sap01", "managed_instance": "00"})
    assert ManagedSystemConnectivityCheck().run(ctx).status is Status.WARN


def test_managed_connectivity_all_closed_fails() -> None:
    runner = ScriptedRunner(default_exit=1)  # both nc probes fail
    ctx = _ctx(runner, params={"managed_host": "sap01", "managed_instance": "00"})
    assert ManagedSystemConnectivityCheck().run(ctx).status is Status.FAIL
