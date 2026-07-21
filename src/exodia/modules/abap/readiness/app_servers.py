"""Application server + logon group inventory (SAP MIG tasks ~1052, 1065).

Reads the list of active application server instances (SM51 / TH_SERVER_LIST)
and the configured logon groups (SMLG backing table RZLLICLASS). A cutover plan
records these on the source so the target can be verified to match after the
copy; capturing them as structured evidence removes the manual screenshotting.

Read-only. FAILs only if the source cannot be reached; otherwise it inventories
what it finds (an empty logon-group set is a WARN, since most systems have at
least one).
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc


class AppServersCheck(Check):
    """Inventory active application servers and logon groups on the source."""

    name = "abap.readiness.app-servers"
    description = "Inventory active app servers (SM51) and logon groups (SMLG)."
    title = "SM51 — Application Servers Check"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.SOURCE_CONN_SPECS

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(
                self.name, "no source RFC connection params (set source_ashost + credentials)"
            )
        try:
            client = _rfc.get_client(ctx, side)
            servers_res = client.call("TH_SERVER_LIST")
            groups = _rfc.read_table(
                client, "RZLLICLASS", fields=["CLASSNAME", "APPLSERVER"]
            )
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read app-server topology: {exc}")

        servers = [
            {
                "name": row.get("NAME", ""),
                "host": row.get("HOST", ""),
                "services": row.get("SERVICES", ""),
            }
            for row in (servers_res.get("LIST", []) or [])
        ]
        logon_groups = sorted({g["CLASSNAME"] for g in groups if g.get("CLASSNAME")})
        data = {
            "app_servers": servers,
            "app_server_count": len(servers),
            "logon_groups": logon_groups,
        }
        if not servers:
            return Result.warn(
                self.name, "no active application servers returned by TH_SERVER_LIST", data=data,
                facts={"App Servers": "0", "Logon Groups": str(len(logon_groups))},
            )
        if not logon_groups:
            return Result.warn(
                self.name,
                f"{len(servers)} app server(s) but no logon groups configured (SMLG empty)",
                data=data,
                facts={"App Servers": str(len(servers)), "Logon Groups": "0"},
            )
        return Result.ok(
            self.name,
            f"{len(servers)} app server(s), {len(logon_groups)} logon group(s): "
            f"{', '.join(logon_groups)}",
            data=data,
            facts={
                "App Servers": str(len(servers)),
                "Logon Groups": ", ".join(logon_groups) or "none",
            },
        )
