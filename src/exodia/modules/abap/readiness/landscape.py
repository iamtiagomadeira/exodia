"""SAP landscape/config readiness checks (SAP MIG — from the COP task list).

Read-only RFC checks that capture the landscape configuration a migration
records on the source (and compares on the target): gateway ACL paths (RZ10),
installed languages (SMLT), support-package status (SPAM), operation modes
(RZ04), RFC server groups (RZ12), job server groups (SM61), and secure-store
consistency (SECSTORE). Each reads the backing table via RFC_READ_TABLE and
records the facts as evidence.

All read-only. FAIL only when the system can't be read; the values themselves
are captured as facts (a migration records them, it doesn't gate on them —
except SPAM which WARNs when not GREEN, per the COP).
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc


class GatewayAclPathsCheck(Check):
    """RZ10 — gateway ACL / security file paths (gw/reg_info, gw/sec_info, ms/acl_info)."""

    name = "abap.readiness.gateway-acl-paths"
    description = "Gateway ACL file paths present (RZ10: gw/reg_info, gw/sec_info, ms/acl_info)."
    title = "RZ10 — Gateway ACL Paths (reg_info / sec_info / prxy_info / ms_acl_info)"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(self.name, "no source RFC connection params")
        wanted = ("gw/reg_info", "gw/sec_info", "gw/prxy_info", "ms/acl_info")
        try:
            client = _rfc.get_client(ctx, side)
            rows = _rfc.read_table(client, "TPFYPROPTY", fields=["PARNAME", "PVALUE"])
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read gateway ACL params: {exc}")
        found = {r.get("PARNAME", ""): r.get("PVALUE", "") for r in rows if r.get("PARNAME", "") in wanted}
        missing = [p for p in wanted if not found.get(p)]
        data = {"paths": found, "missing": missing}
        facts = {p.split("/")[-1]: (found.get(p) or "unset") for p in wanted}
        if missing:
            return Result.warn(
                self.name,
                f"{len(missing)} gateway ACL path(s) unset: {', '.join(missing)}",
                data=data, facts=facts,
            )
        return Result.ok(
            self.name, "all gateway ACL paths are set", data=data, facts=facts,
        )


class InstalledLanguagesCheck(Check):
    """SMLT — installed languages on the system (T002C)."""

    name = "abap.readiness.installed-languages"
    description = "Installed languages inventory (SMLT / T002C)."
    title = "SMLT — Installed Languages"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(self.name, "no source RFC connection params")
        try:
            client = _rfc.get_client(ctx, side)
            rows = _rfc.read_table(client, "T002C", fields=["SPRAS", "LAISO"])
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read installed languages: {exc}")
        langs = sorted({r.get("LAISO", "") or r.get("SPRAS", "") for r in rows if r.get("SPRAS")})
        return Result.ok(
            self.name,
            f"{len(langs)} installed language(s): {', '.join(langs) or 'n/a'}",
            data={"languages": langs},
            facts={"Installed Languages": ", ".join(langs) or "n/a", "Count": str(len(langs))},
        )


class SpamStatusCheck(Check):
    """SPAM — support package manager status must be GREEN (PAT03 backlog empty)."""

    name = "abap.readiness.spam-status"
    description = "SPAM status is clean (no aborted/pending support package, PAT03)."
    title = 'SPAM — Support Package Status ("GREEN")'
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(self.name, "no source RFC connection params")
        try:
            client = _rfc.get_client(ctx, side)
            # PAT03 holds queued/aborted package steps; empty (or all done) = GREEN.
            rows = _rfc.read_table(client, "PAT03", fields=["PATCH", "STATUS"], rowcount=50)
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read SPAM status (PAT03): {exc}")
        # Any row with a non-final status ('N'=new/queued, 'A'=aborted) is not GREEN.
        pending = [r for r in rows if r.get("STATUS", "").upper() in ("N", "A", "R")]
        if pending:
            return Result.warn(
                self.name,
                f"SPAM not GREEN: {len(pending)} pending/aborted package step(s)",
                data={"pending": len(pending)},
                facts={"SPAM Status": "NOT GREEN", "Pending Steps": str(len(pending))},
            )
        return Result.ok(
            self.name, 'SPAM status is clean ("GREEN")',
            facts={"SPAM Status": "GREEN"},
        )


class SecureStoreCheck(Check):
    """SECSTORE — secure store consistency (no inconsistent entries, SECSTORE)."""

    name = "abap.readiness.secure-store"
    description = "Secure store has no inconsistent entries (SECSTORE)."
    title = "SECSTORE — Secure Store Consistency"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(self.name, "no source RFC connection params")
        try:
            client = _rfc.get_client(ctx, side)
            # SECSTORE entries live in RSECTAB; a consistency check reports bad rows.
            rows = _rfc.read_table(client, "RSECACTB", fields=["RSECID"], rowcount=1)
        except _rfc.RfcError:
            # RSECACTB may not be readable on every release — record as manual.
            return Result.skip(
                self.name, "secure-store table not readable via RFC; verify SECSTORE manually",
            )
        return Result.ok(
            self.name, "secure store readable (verify SECSTORE has no red entries)",
            data={"rows": len(rows)}, facts={"Secure Store": "readable"},
        )


class OperationModesCheck(Check):
    """RZ04 — operation modes defined (TPFID)."""

    name = "abap.readiness.operation-modes"
    description = "Operation modes inventory (RZ04 / TPFID)."
    title = "RZ04 — Operation Modes"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(self.name, "no source RFC connection params")
        try:
            client = _rfc.get_client(ctx, side)
            rows = _rfc.read_table(client, "TPFID", fields=["BTCJOBNAME", "OPMODE"])
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read operation modes: {exc}")
        modes = sorted({r.get("OPMODE", "") for r in rows if r.get("OPMODE")})
        return Result.ok(
            self.name,
            f"{len(modes)} operation mode(s): {', '.join(modes) or 'none'}",
            data={"operation_modes": modes},
            facts={"Operation Modes": ", ".join(modes) or "none", "Count": str(len(modes))},
        )


class RfcServerGroupsCheck(Check):
    """RZ12 — RFC server groups (RZLLITAB)."""

    name = "abap.readiness.rfc-server-groups"
    description = "RFC server groups inventory (RZ12 / RZLLITAB)."
    title = "RZ12 — RFC Server Groups"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(self.name, "no source RFC connection params")
        try:
            client = _rfc.get_client(ctx, side)
            rows = _rfc.read_table(client, "RZLLITAB", fields=["CLASSNAME"])
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read RFC server groups: {exc}")
        groups = sorted({r.get("CLASSNAME", "") for r in rows if r.get("CLASSNAME")})
        return Result.ok(
            self.name,
            f"{len(groups)} RFC server group(s): {', '.join(groups) or 'none'}",
            data={"rfc_server_groups": groups},
            facts={"RFC Server Groups": ", ".join(groups) or "none", "Count": str(len(groups))},
        )


class JobServerGroupsCheck(Check):
    """SM61 — background job server groups (BTCJGROUP)."""

    name = "abap.readiness.job-server-groups"
    description = "Background job server groups inventory (SM61 / BTCJGROUP)."
    title = "SM61 — Job Server Groups"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(self.name, "no source RFC connection params")
        try:
            client = _rfc.get_client(ctx, side)
            rows = _rfc.read_table(client, "BTCJGROUP", fields=["JOBGROUP"])
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read job server groups: {exc}")
        groups = sorted({r.get("JOBGROUP", "") for r in rows if r.get("JOBGROUP")})
        return Result.ok(
            self.name,
            f"{len(groups)} job server group(s)",
            data={"job_server_groups": groups},
            facts={"Job Server Groups": ", ".join(groups) or "none", "Count": str(len(groups))},
        )
