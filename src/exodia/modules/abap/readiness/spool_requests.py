"""Spool requests readiness (SAP MIG ramp-down — SP01 / TSP01).

Reports pending/in-process spool + output requests on the source. A large
backlog of output requests at takeover is lost work (print jobs that never
went out) and points to a system that was not cleanly quiesced. Read-only.

WARNs when in-process output requests exist; a plain spool backlog is reported
as informational counts (spool data itself is not usually migration-critical,
but the engineer should know before freezing the system).
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase, Side
from exodia.core.severity import Severity

from . import _rfc

# TSP01.RQFINAL: ' ' not finished; TSP01 rows are spool requests. Output request
# processing status lives in TSP02/TST01; we keep to TSP01 for a simple backlog
# signal that RFC_READ_TABLE can serve without joins.


class SpoolRequestsCheck(Check):
    """Report spool request backlog on the source (SP01 / TSP01)."""

    name = "abap.readiness.spool-requests"
    description = "Spool request backlog at takeover (SP01 / TSP01)."
    title = "SP01 — Spool Requests Backlog Check"
    phase = Phase.RAMP_DOWN
    # A spool backlog is hygiene, not a copy-blocker — the customer decides
    # whether to drain it before freeze. ADVISORY by default (reclassifiable).
    severity = Severity.ADVISORY
    gate_side = Side.SOURCE
    responsible = "customer"

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
            rows = _rfc.read_table(
                client, "TSP01", fields=["RQIDENT", "RQFINAL"]
            )
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read spool table TSP01: {exc}")

        total = len(rows)
        unfinished = [r for r in rows if r.get("RQFINAL", "") != "Y"]
        data = {"total_spool_requests": total, "unfinished": len(unfinished)}
        if unfinished:
            return Result.warn(
                self.name,
                f"{len(unfinished)} unfinished spool request(s) of {total} total",
                data=data,
                facts={"Total Spool Requests": str(total), "Unfinished": str(len(unfinished))},
            )
        return Result.ok(
            self.name,
            f"no unfinished spool requests ({total} total, all completed)",
            data=data,
            facts={"Total Spool Requests": str(total), "Unfinished": "0"},
        )
