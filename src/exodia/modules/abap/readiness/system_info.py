"""System-identity readiness checks (SAP MIG tasks ~1047-1050).

Reads the source (or target) ABAP system's kernel, database, unicode and
release information over RFC — the data a Basis engineer collects from
System > Status before a migration. Read-only; captures everything as evidence.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc


class SystemInfoCheck(Check):
    """Collect kernel / DB / unicode / release from RFC_SYSTEM_INFO.

    Never fails on the *values* (they are environment facts, not pass/fail
    conditions) — it FAILs only when the system cannot be reached at all, so a
    broken connection surfaces early. On success it records the identity block
    as structured evidence for the migration audit.
    """

    name = "abap.readiness.system-info"
    description = "Source ABAP system identity: kernel, DB, unicode, release (RFC_SYSTEM_INFO)."
    title = "System Identity & Kernel Check (RFC_SYSTEM_INFO)"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(
                self.name,
                "no source RFC connection params (set source_ashost + credentials)",
            )
        try:
            client = _rfc.get_client(ctx, side)
            res = client.call("RFC_SYSTEM_INFO")
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read system info: {exc}")

        info = res.get("RFCSI_EXPORT", {}) or {}
        identity = {
            "sid": info.get("RFCSYSID", ""),
            "host": info.get("RFCHOST", ""),
            "kernel_release": info.get("RFCKERNRL", ""),
            "kernel_patch": info.get("RFCSAPRL", ""),
            "db_system": info.get("RFCDBSYS", ""),
            "db_host": info.get("RFCDBHOST", ""),
            "unicode": info.get("RFCCHARTYP", ""),
            "opsys": info.get("RFCOPSYS", ""),
        }
        if not identity["sid"]:
            return Result.warn(
                self.name,
                "RFC_SYSTEM_INFO returned no system id — unexpected response shape",
                data=identity,
                facts={
                    "SID": identity["sid"] or "unknown",
                    "DB System": identity["db_system"],
                    "Kernel": identity["kernel_release"],
                    "Unicode": identity["unicode"],
                },
            )
        return Result.ok(
            self.name,
            f"{identity['sid']} · kernel {identity['kernel_release']} · "
            f"db {identity['db_system']} · unicode {identity['unicode']}",
            data=identity,
            facts={
                "SID": identity["sid"],
                "DB System": identity["db_system"],
                "Kernel": identity["kernel_release"],
                "Unicode": identity["unicode"],
                "Release": identity["kernel_patch"],
            },
        )
