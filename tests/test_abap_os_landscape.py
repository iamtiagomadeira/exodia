"""Tests for the OS-level and landscape readiness checks (no real SAP/OS).

* OS-level (via the context runner): kernel release (disp+work), OS release
  (/etc/os-release), CPU (lscpu), timezone (timedatectl) — source & target.
* Landscape (via RFC_READ_TABLE): RZ10 gateway ACL paths, SMLT languages, SPAM
  status, SECSTORE, RZ04 operation modes, RZ12 RFC groups, SM61 job groups.
"""

from __future__ import annotations

from typing import Any

from exodia.core import Context, Status
from exodia.core.registry import registry
from exodia.core.shell import CommandResult, Runner


def _run_check(name: str, ctx: Context) -> Any:
    cls = registry.get_check(name)
    assert cls is not None, f"{name} not discovered"
    return cls().execute(ctx)


# --------------------------------------------------------------------------- #
# OS-level checks — runner returns canned command output
# --------------------------------------------------------------------------- #


class OsRunner(Runner):
    def __init__(self, by_cmd: dict[str, str]) -> None:
        self.by_cmd = by_cmd

    def run(self, argv, timeout=300, input_text=None):  # type: ignore[no-untyped-def]
        cmd = argv[0]
        if cmd in self.by_cmd:
            return CommandResult(argv, 0, self.by_cmd[cmd], "")
        # Command not mocked -> simulate "not found" (non-zero exit).
        return CommandResult(argv, 127, "", "not mocked")


def _os_ctx(by_cmd: dict[str, str]) -> Context:
    class _C(Context):
        def runner(self):  # type: ignore[override]
            return OsRunner(by_cmd)

    return _C()  # type: ignore[call-arg]


_DISPWORK = """
disp+work information
---------------------
kernel release                785
sup pkg lvl                   200
unicode enabled version
"""

_LSCPU = """Architecture: x86_64
CPU(s):              16
Socket(s):           2
Core(s) per socket:  8
Model name:          Intel(R) Xeon(R) Platinum 8370C
"""

_OSREL = 'PRETTY_NAME="SUSE Linux Enterprise Server 15 SP5"\nVERSION_ID="15.5"\n'


def test_source_kernel_release() -> None:
    r = _run_check("abap.readiness.source-kernel-release", _os_ctx({"disp+work": _DISPWORK}))
    assert r.status is Status.PASS
    assert r.facts["Kernel Release"] == "785"
    assert r.facts["Side"] == "Source"


def test_target_kernel_release_fail_when_unreadable() -> None:
    ctx = _os_ctx({})  # disp+work not mocked -> non-zero
    r = _run_check("abap.readiness.target-kernel-release", ctx)
    assert r.status is Status.FAIL


def test_source_os_release() -> None:
    r = _run_check("abap.readiness.source-os-release", _os_ctx({"cat": _OSREL}))
    assert r.status is Status.PASS
    assert "SUSE" in r.facts["OS"]
    assert r.facts["Version"] == "15.5"


def test_target_cpu_info() -> None:
    r = _run_check("abap.readiness.target-cpu-info", _os_ctx({"lscpu": _LSCPU}))
    assert r.status is Status.PASS
    assert r.facts["CPU(s)"] == "16"
    assert "Xeon" in r.facts["Model"]


def test_target_timezone_from_timedatectl() -> None:
    tz_out = "               Local time: Mon\n                Time zone: Europe/Zurich (CET, +0100)\n"
    r = _run_check("abap.readiness.target-timezone", _os_ctx({"timedatectl": tz_out}))
    assert r.status is Status.PASS
    assert r.facts["Timezone"] == "Europe/Zurich"


def test_target_timezone_mismatch_warns() -> None:
    tz_out = "                Time zone: UTC (UTC, +0000)\n"

    class _C(Context):
        def runner(self):  # type: ignore[override]
            return OsRunner({"timedatectl": tz_out})

    ctx = _C(params={"expected_timezone": "Europe/Zurich"})
    r = _run_check("abap.readiness.target-timezone", ctx)
    assert r.status is Status.WARN


def test_timezone_is_target_only() -> None:
    # there is no source-timezone check — only the target one exists
    assert registry.get_check("abap.readiness.target-timezone") is not None
    assert registry.get_check("abap.readiness.source-timezone") is None


# --------------------------------------------------------------------------- #
# Landscape checks — RFC via a fake client
# --------------------------------------------------------------------------- #


class FakeRfcClient:
    def __init__(self, responder):  # type: ignore[no-untyped-def]
        self._r = responder

    def call(self, fm: str, **kw: Any) -> dict:
        return self._r(fm, kw)

    def close(self) -> None:
        pass


class RfcCtx(Context):
    def bind(self, responder):  # type: ignore[no-untyped-def]
        object.__setattr__(self, "_resp", responder)
        return self

    def rfc_client(self, side: str) -> FakeRfcClient:
        return FakeRfcClient(self._resp)  # type: ignore[attr-defined]


_SRC = {"source_ashost": "src-host", "source_client": "000"}


def _read_table_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        return {"FIELDS": [], "DATA": []}
    names = list(rows[0].keys())
    width = 40
    fields = [{"FIELDNAME": n, "OFFSET": i * width, "LENGTH": width} for i, n in enumerate(names)]
    data = [{"WA": "".join(str(r.get(n, "")).ljust(width) for n in names)} for r in rows]
    return {"FIELDS": fields, "DATA": data}


def test_gateway_acl_paths_all_set() -> None:
    rows = _read_table_rows([
        {"PARNAME": "gw/reg_info", "PVALUE": "/x/reginfo"},
        {"PARNAME": "gw/sec_info", "PVALUE": "/x/secinfo"},
        {"PARNAME": "gw/prxy_info", "PVALUE": "/x/prxyinfo"},
        {"PARNAME": "ms/acl_info", "PVALUE": "/x/ms_acl"},
    ])
    ctx = RfcCtx(params=_SRC).bind(lambda fm, kw: rows)
    r = _run_check("abap.readiness.gateway-acl-paths", ctx)
    assert r.status is Status.PASS


def test_gateway_acl_paths_missing_warns() -> None:
    rows = _read_table_rows([{"PARNAME": "gw/reg_info", "PVALUE": "/x/reginfo"}])
    ctx = RfcCtx(params=_SRC).bind(lambda fm, kw: rows)
    r = _run_check("abap.readiness.gateway-acl-paths", ctx)
    assert r.status is Status.WARN


def test_installed_languages() -> None:
    rows = _read_table_rows([{"SPRAS": "E", "LAISO": "EN"}, {"SPRAS": "D", "LAISO": "DE"}])
    ctx = RfcCtx(params=_SRC).bind(lambda fm, kw: rows)
    r = _run_check("abap.readiness.installed-languages", ctx)
    assert r.status is Status.PASS
    assert "EN" in r.facts["Installed Languages"]


def test_spam_status_green() -> None:
    ctx = RfcCtx(params=_SRC).bind(lambda fm, kw: _read_table_rows([]))
    r = _run_check("abap.readiness.spam-status", ctx)
    assert r.status is Status.PASS
    assert r.facts["SPAM Status"] == "GREEN"


def test_spam_status_not_green_warns() -> None:
    rows = _read_table_rows([{"PATCH": "SAPKB75001", "STATUS": "A"}])
    ctx = RfcCtx(params=_SRC).bind(lambda fm, kw: rows)
    r = _run_check("abap.readiness.spam-status", ctx)
    assert r.status is Status.WARN


def test_operation_modes() -> None:
    rows = _read_table_rows([{"BTCJOBNAME": "x", "OPMODE": "DAY"}, {"BTCJOBNAME": "y", "OPMODE": "NIGHT"}])
    ctx = RfcCtx(params=_SRC).bind(lambda fm, kw: rows)
    r = _run_check("abap.readiness.operation-modes", ctx)
    assert r.status is Status.PASS
    assert r.data["operation_modes"] == ["DAY", "NIGHT"]


def test_rfc_and_job_server_groups() -> None:
    ctx_rfc = RfcCtx(params=_SRC).bind(lambda fm, kw: _read_table_rows([{"CLASSNAME": "parallel_generators"}]))
    r1 = _run_check("abap.readiness.rfc-server-groups", ctx_rfc)
    assert r1.status is Status.PASS
    ctx_job = RfcCtx(params=_SRC).bind(lambda fm, kw: _read_table_rows([{"JOBGROUP": "GRP1"}]))
    r2 = _run_check("abap.readiness.job-server-groups", ctx_job)
    assert r2.status is Status.PASS


# --------------------------------------------------------------------------- #
# Runbook wiring
# --------------------------------------------------------------------------- #


def test_target_os_validation_runbook() -> None:
    rb = registry.get_runbook("abap.target-os-validation")
    assert rb is not None
    steps = rb().steps
    assert steps == [
        "abap.readiness.target-kernel-release",
        "abap.readiness.target-os-release",
        "abap.readiness.target-cpu-info",
        "abap.readiness.target-timezone",
    ]


def test_pre_migration_includes_landscape_checks() -> None:
    rb = registry.get_runbook("abap.pre-migration-checks")()
    for step in (
        "abap.readiness.source-kernel-release",
        "abap.readiness.source-os-release",
        "abap.readiness.source-cpu-info",
        "abap.readiness.gateway-acl-paths",
        "abap.readiness.installed-languages",
        "abap.readiness.spam-status",
        "abap.readiness.operation-modes",
        "abap.readiness.rfc-server-groups",
        "abap.readiness.job-server-groups",
    ):
        assert step in rb.steps, f"{step} missing from pre-migration runbook"
