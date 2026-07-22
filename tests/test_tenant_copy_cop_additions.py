"""Tests for the COP-derived tenant-copy additions (no real HANA):

* HANA service ports (M_SERVICES) + replication parameters (M_INIFILE_CONTENTS)
* HSR parameter config with SSL on/off, and the HANA restart action
* the Dry-Run/Mock isolation actions (users/RFCs/jobs) with backup + reverse
"""

from __future__ import annotations

from exodia.core import Context, Status
from exodia.core.registry import registry
from exodia.core.shell import CommandResult, Runner
from exodia.modules.system_copy.tenant_copy.actions.hsr_config import (
    ConfigureHsrParametersAction,
    RestartHanaAction,
)
from exodia.modules.system_copy.tenant_copy.actions.mock_run import (
    MockIsolateRfcsAction,
    MockIsolateUsersAction,
    MockStopJobsAction,
)


class ScriptedRunner(Runner):
    """Replays a canned result per call and records the SQL/argv issued."""

    def __init__(self, exit_code: int = 0, stdout: str = "", by_sql: dict | None = None) -> None:
        self.calls: list[list[str]] = []
        self._exit_code = exit_code
        self._stdout = stdout
        self._by_sql = by_sql or {}

    def run(self, argv, timeout=300, input_text=None):  # type: ignore[no-untyped-def]
        self.calls.append(argv)
        sql = argv[-1] if argv else ""
        for needle, (code, out) in self._by_sql.items():
            if needle in sql:
                return CommandResult(argv, code, out, "")
        return CommandResult(argv, self._exit_code, self._stdout, "")


def _ctx(runner: Runner, **params: object) -> Context:
    class _C(Context):
        def runner(self):  # type: ignore[override]
            return runner

    return _C(params=params)  # type: ignore[arg-type]


def _rfc_ctx(responder, **params):  # type: ignore[no-untyped-def]
    class _FakeClient:
        def __init__(self, r):
            self._r = r

        def call(self, fm, **kw):
            return self._r(fm, kw)

        def close(self):
            pass

    class _C(Context):
        def rfc_client(self, side):  # type: ignore[override]
            return _FakeClient(responder)

    return _C(params=params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Ports + replication parameters checks (hdbsql via runner)
# --------------------------------------------------------------------------- #


def _run_check(name, ctx):  # type: ignore[no-untyped-def]
    cls = registry.get_check(name)
    assert cls is not None, name
    return cls().execute(ctx)


def test_source_ports_extracted() -> None:
    # M_SERVICES rows: DATABASE, SERVICE, PORT, SQL_PORT
    out = '"HT4","indexserver","31003","31015"\n"SYSTEMDB","nameserver","31001","31013"'
    ctx = _ctx(ScriptedRunner(stdout=out), source_userstore_key="SRC")
    r = _run_check("tenant-copy.hana.source-ports", ctx)
    assert r.status is Status.PASS
    assert r.data["services"]
    assert "31015" in r.facts["SQL Ports"] or "31013" in r.facts["SQL Ports"]


def test_target_ports_read_failure_fails() -> None:
    ctx = _ctx(ScriptedRunner(exit_code=1, stdout=""), target_userstore_key="TGT")
    r = _run_check("tenant-copy.hana.target-ports", ctx)
    assert r.status is Status.FAIL


def test_source_replication_parameters_captured() -> None:
    out = (
        '"global.ini","system_replication_communication","enable_ssl","off"\n'
        '"global.ini","communication","ssl","off"\n'
        '"global.ini","persistence","log_mode","normal"'
    )
    ctx = _ctx(ScriptedRunner(stdout=out), source_userstore_key="SRC")
    r = _run_check("tenant-copy.hana.source-replication-parameters", ctx)
    assert r.status is Status.PASS
    assert r.facts["SR enable_ssl"] == "off"
    assert r.facts["log_mode"] == "normal"


# --------------------------------------------------------------------------- #
# HSR parameter config — SSL on/off
# --------------------------------------------------------------------------- #


def test_hsr_config_ssl_off_statements() -> None:
    ctx = _ctx(ScriptedRunner(stdout="ok"), target_userstore_key="TGT", ssl_mode="off")
    r = ConfigureHsrParametersAction().dry_run(ctx)
    assert r.status is Status.PASS
    joined = "\n".join(r.data["statements"])
    assert "'system_replication_communication','enable_ssl') = 'off'" in joined
    assert "'communication','listeninterface') = '.internal'" in joined
    assert r.facts["SSL Mode"] == "OFF"
    assert r.facts["Restart Required"] == "Yes"


def test_hsr_config_ssl_on_statements() -> None:
    ctx = _ctx(ScriptedRunner(stdout="ok"), target_userstore_key="TGT", ssl_mode="on")
    r = ConfigureHsrParametersAction().dry_run(ctx)
    joined = "\n".join(r.data["statements"])
    assert "'system_replication_communication','enable_ssl') = 'on'" in joined
    assert "'communication','ssl') = 'on'" in joined
    assert "'communication','listeninterface') = '.global'" in joined
    assert r.facts["SSL Mode"] == "ON"


def test_hsr_config_execute_applies_each() -> None:
    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner, target_userstore_key="TGT", ssl_mode="off")
    r = ConfigureHsrParametersAction().execute(ctx)
    assert r.status is Status.PASS
    # every statement issued via hdbsql
    assert all(call[0] == "hdbsql" for call in runner.calls)
    assert int(r.facts["Parameters Applied"]) >= 10


def test_restart_hana_stop_then_start() -> None:
    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner)
    r = RestartHanaAction().execute(ctx)
    assert r.status is Status.PASS
    assert runner.calls[0] == ["HDB", "stop"]
    assert runner.calls[1] == ["HDB", "start"]


def test_restart_hana_stop_failure() -> None:
    runner = ScriptedRunner(exit_code=1, stdout="")
    ctx = _ctx(runner)
    r = RestartHanaAction().execute(ctx)
    assert r.status is Status.FAIL


# --------------------------------------------------------------------------- #
# Mock-run isolation (backup + reverse)
# --------------------------------------------------------------------------- #


def test_mock_isolate_users_backs_up_then_locks() -> None:
    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner, tenant_key="TEN", abap_schema="SAPABAP1")
    r = MockIsolateUsersAction().execute(ctx)
    assert r.status is Status.PASS
    sqls = [c[-1] for c in runner.calls]
    assert any("CREATE TABLE" in s and "BKP_USR02" in s for s in sqls)
    assert any("UPDATE USR02 SET UFLAG = '65'" in s and "DDIC" in s for s in sqls)


def test_mock_isolate_users_spares_extra() -> None:
    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner, tenant_key="TEN", keep_unlocked="JSMITH,MARY")
    r = MockIsolateUsersAction().dry_run(ctx)
    assert "JSMITH" in r.facts["Spared"] and "DDIC" in r.facts["Spared"]


def test_mock_isolate_rfcs_neutralises_prefixes() -> None:
    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner, tenant_key="TEN")
    r = MockIsolateRfcsAction().execute(ctx)
    assert r.status is Status.PASS
    sqls = " ".join(c[-1] for c in runner.calls)
    assert "'G=', 'G=#'" in sqls and "'H=', 'H=#'" in sqls
    assert "SAPGUI_QUEUE" in sqls  # spared for X=


def test_mock_stop_jobs_sets_status_z() -> None:
    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner, tenant_key="TEN")
    r = MockStopJobsAction().execute(ctx)
    assert r.status is Status.PASS
    sqls = " ".join(c[-1] for c in runner.calls)
    assert "UPDATE TBTCO SET STATUS = 'Z'" in sqls
    assert "RDDIMPDP%" in sqls  # spared


def test_mock_rollback_restores_from_backup() -> None:
    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner, tenant_key="TEN", abap_schema="SAPABAP1")
    r = MockIsolateUsersAction().rollback(ctx)
    assert r.status is Status.PASS
    sqls = " ".join(c[-1] for c in runner.calls)
    assert "TRUNCATE TABLE" in sqls and "USR02" in sqls
    assert "INSERT INTO" in sqls and "BKP_USR02" in sqls


def test_mock_actions_skip_without_tenant_key() -> None:
    ctx = _ctx(ScriptedRunner(stdout="ok"), abap_schema="SAPABAP1")
    r = MockIsolateUsersAction().execute(ctx)
    assert r.status is Status.SKIP


def test_mock_actions_are_downtime_phase_and_discovered() -> None:
    from exodia.core.result import Phase

    for name, cls in [
        ("tenant-copy.hana.mock-isolate-users", MockIsolateUsersAction),
        ("tenant-copy.hana.mock-isolate-rfcs", MockIsolateRfcsAction),
        ("tenant-copy.hana.mock-stop-jobs", MockStopJobsAction),
    ]:
        assert registry.get_action(name) is not None
        assert cls.phase is Phase.DOWNTIME


# --------------------------------------------------------------------------- #
# Post-copy reconnect + cleanup actions
# --------------------------------------------------------------------------- #


def test_reconnect_verify_ok() -> None:
    from exodia.modules.system_copy.tenant_copy.actions.post_reconnect import (
        ReconnectVerifyAction,
    )

    runner = ScriptedRunner(stdout="1")
    ctx = _ctx(runner, userstore_key="DEFAULT")
    r = ReconnectVerifyAction().execute(ctx)
    assert r.status is Status.PASS
    assert runner.calls[0][0] == "hdbsql"
    assert ["R3trans", "-x"] in runner.calls


def test_reconnect_verify_db_fail() -> None:
    from exodia.modules.system_copy.tenant_copy.actions.post_reconnect import (
        ReconnectVerifyAction,
    )

    runner = ScriptedRunner(exit_code=1, stdout="")
    ctx = _ctx(runner, userstore_key="DEFAULT")
    r = ReconnectVerifyAction().execute(ctx)
    assert r.status is Status.FAIL


def test_reconnect_verify_r3trans_warn() -> None:
    from exodia.modules.system_copy.tenant_copy.actions.post_reconnect import (
        ReconnectVerifyAction,
    )

    # DB connect ok, R3trans -x returns non-zero
    runner = ScriptedRunner(by_sql={"SELECT 1 FROM DUMMY": (0, "1")}, exit_code=12)
    ctx = _ctx(runner, userstore_key="DEFAULT")
    r = ReconnectVerifyAction().execute(ctx)
    assert r.status is Status.WARN


def test_delete_abap_dict_data_clears_tables() -> None:
    from exodia.modules.system_copy.tenant_copy.actions.post_reconnect import (
        DeleteAbapDictDataAction,
    )

    runner = ScriptedRunner(stdout="ok")
    ctx = _ctx(runner, tenant_key="TEN", abap_schema="SAPABAP1")
    r = DeleteAbapDictDataAction().execute(ctx)
    assert r.status is Status.PASS
    sqls = " ".join(c[-1] for c in runner.calls)
    assert "DELETE FROM SAPABAP1.PAHI" in sqls
    assert "DELETE FROM SAPABAP1.DDLOG" in sqls
    assert int(r.facts["Tables Cleared"]) == 12


def test_delete_abap_dict_data_skip_without_key() -> None:
    from exodia.modules.system_copy.tenant_copy.actions.post_reconnect import (
        DeleteAbapDictDataAction,
    )

    r = DeleteAbapDictDataAction().execute(_ctx(ScriptedRunner(stdout="ok")))
    assert r.status is Status.SKIP


# --------------------------------------------------------------------------- #
# Post-copy validation checks
# --------------------------------------------------------------------------- #


def test_secure_communication_on_pass() -> None:
    ctx = _ctx(ScriptedRunner(stdout='"ON"'), target_userstore_key="TGT")
    r = _run_check("tenant-copy.hana.secure-communication", ctx)
    assert r.status is Status.PASS
    assert r.facts["secure_communication"] == "ON"


def test_secure_communication_off_fails() -> None:
    ctx = _ctx(ScriptedRunner(stdout='"OFF"'), target_userstore_key="TGT")
    r = _run_check("tenant-copy.hana.secure-communication", ctx)
    assert r.status is Status.FAIL


def test_data_consistency_match_pass() -> None:
    rows = '"MSEG","1000000"\n"BSEG","900000"'
    ctx = _ctx(
        ScriptedRunner(stdout=rows),
        source_tenant_key="SRC",
        target_tenant_key="TGT",
    )
    r = _run_check("tenant-copy.hana.data-consistency", ctx)
    assert r.status is Status.PASS


def test_data_consistency_skip_without_keys() -> None:
    ctx = _ctx(ScriptedRunner(stdout=""))
    r = _run_check("tenant-copy.hana.data-consistency", ctx)
    assert r.status is Status.SKIP


# --------------------------------------------------------------------------- #
# HANA consistency checks — CHECK_TABLE_CONSISTENCY / CHECK_CATALOG (1785060)
# --------------------------------------------------------------------------- #


def test_table_consistency_clean_pass() -> None:
    # empty result set = no inconsistencies
    ctx = _ctx(ScriptedRunner(stdout=""), target_tenant_key="TGT")
    r = _run_check("tenant-copy.hana.target-table-consistency", ctx)
    assert r.status is Status.PASS
    assert r.facts["Inconsistencies"] == "0"
    # ran the CHECK action (never REPAIR)
    assert any("CHECK_TABLE_CONSISTENCY('CHECK'" in c[-1] for c in ctx.runner().calls)


def test_table_consistency_inconsistencies_fail() -> None:
    rows = '"SAPABAP1","MSEG","","0","8003","index inconsistency"'
    ctx = _ctx(ScriptedRunner(stdout=rows), target_tenant_key="TGT")
    r = _run_check("tenant-copy.hana.target-table-consistency", ctx)
    assert r.status is Status.FAIL
    assert r.data["errors"] >= 1
    assert r.sap_note == "1785060"


def test_table_consistency_is_blocking_on_target() -> None:
    cls = registry.get_check("tenant-copy.hana.target-table-consistency")
    assert cls is not None and cls.blocking is True


def test_table_consistency_skip_without_key() -> None:
    ctx = _ctx(ScriptedRunner(stdout=""))
    r = _run_check("tenant-copy.hana.target-table-consistency", ctx)
    assert r.status is Status.SKIP


def test_catalog_consistency_clean_pass() -> None:
    ctx = _ctx(ScriptedRunner(stdout=""), target_tenant_key="TGT")
    r = _run_check("tenant-copy.hana.target-catalog-consistency", ctx)
    assert r.status is Status.PASS
    assert any("CHECK_CATALOG('CHECK'" in c[-1] for c in ctx.runner().calls)


def test_source_consistency_is_preparation_phase() -> None:
    from exodia.core.result import Phase

    src_tbl = registry.get_check("tenant-copy.hana.source-table-consistency")
    src_cat = registry.get_check("tenant-copy.hana.source-catalog-consistency")
    assert src_tbl is not None and src_tbl.phase is Phase.PREPARATION
    assert src_cat is not None and src_cat.phase is Phase.PREPARATION


def test_source_consistency_uses_source_key() -> None:
    ctx = _ctx(ScriptedRunner(stdout=""), source_tenant_key="SRC")
    r = _run_check("tenant-copy.hana.source-table-consistency", ctx)
    assert r.status is Status.PASS
    # connected with the SOURCE key
    assert any("SRC" in c for c in ctx.runner().calls[0])


def test_consistency_read_failure_fails() -> None:
    ctx = _ctx(ScriptedRunner(exit_code=1, stdout=""), target_tenant_key="TGT")
    r = _run_check("tenant-copy.hana.target-table-consistency", ctx)
    assert r.status is Status.FAIL
    assert r.facts["Ran"] == "No"


def test_post_validation_runbook_wired() -> None:
    rb = registry.get_runbook("tenant-copy.hana.post-validation")
    assert rb is not None
    steps = rb().steps
    assert "tenant-copy.hana.target-table-consistency" in steps
    assert "tenant-copy.hana.target-catalog-consistency" in steps
    for s in steps:
        assert registry.get_check(s) is not None, f"unresolved {s}"


def test_source_consistency_in_readiness_source_runbook() -> None:
    rb = registry.get_runbook("tenant-copy.hana.readiness-source")()
    assert "tenant-copy.hana.source-table-consistency" in rb.steps
    assert "tenant-copy.hana.source-catalog-consistency" in rb.steps
