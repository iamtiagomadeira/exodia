"""Batch input session readiness (SAP MIG ramp-down — SM35 / APQI).

Pending or in-process batch input sessions at takeover are unfinished data loads
that would be lost (or replayed against a frozen system). A cutover drains SM35
before the copy. This check reads the batch input queue table (APQI) over RFC.

Read-only. WARNs when open sessions remain (they are usually reviewed, not an
automatic hard stop), with the exact count so the operator can clear them.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc

# APQI.QSTATE codes for sessions that are not yet finished: created/new,
# generated, error, and in-process. Finished sessions are removed/marked so we
# count anything that is not a clean 'F' (finished).
_OPEN_STATES = {"", "C", "N", "G", "E", "R", "B"}


class BatchInputSessionsCheck(Check):
    """No open batch input sessions on the source pre-takeover (SM35 / APQI)."""

    name = "abap.readiness.batch-input-sessions"
    description = "No open batch input sessions at takeover (SM35 / APQI)."
    title = "SM35 — Batch Input Sessions Check"
    phase = Phase.RAMP_DOWN
    blocking = False

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
            rows = _rfc.read_table(client, "APQI", fields=["GROUPID", "QSTATE"])
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read batch input queue (APQI): {exc}")

        total = len(rows)
        open_sessions = [r for r in rows if r.get("QSTATE", "").upper() in _OPEN_STATES]
        data = {"total_sessions": total, "open_sessions": len(open_sessions)}
        if open_sessions:
            return Result.warn(
                self.name,
                f"{len(open_sessions)} open batch input session(s) of {total} — "
                "process or delete them before takeover",
                data=data,
                facts={"Open Sessions": str(len(open_sessions)), "Total Sessions": str(total)},
            )
        return Result.ok(
            self.name,
            f"no open batch input sessions ({total} total, all finished)",
            data=data,
            facts={"Open Sessions": "0", "Total Sessions": str(total)},
        )
