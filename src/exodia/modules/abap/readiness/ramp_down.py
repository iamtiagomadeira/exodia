"""Ramp-down drain readiness (SAP MIG tasks ~2014-2019).

Before a takeover the source must be quiet: no pending update requests, no
stuck transactional RFCs, and empty inbound/outbound qRFC queues. A cutover
plan checks these by hand across SM13 / SM58 / SMQ1 / SMQ2. This check reads
the underlying tables/queues over RFC and reports a single drained / not-drained
verdict with the exact counts.

Blocking: a non-empty queue at takeover risks data loss, so a positive count
FAILs (and blocks the surrounding prepare pipeline).
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc


class UpdateQueuesDrainedCheck(Check):
    """Source update records + tRFC + qRFC queues must all be empty pre-takeover."""

    name = "abap.readiness.update-queues-drained"
    description = "Source has no pending updates / tRFC / qRFC entries (ready for takeover)."
    title = "SM13/SM58/SMQ1/SMQ2 — Update & Queue Drain Check"
    phase = Phase.RAMP_DOWN
    blocking = True

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
            # Pending update records (SM13 backing table VBDATA).
            vbdata = _rfc.read_table(client, "VBDATA", fields=["SKEY"], rowcount=1)
            # Stuck transactional RFC calls (SM58 backing table ARFCSSTATE).
            arfc = _rfc.read_table(client, "ARFCSSTATE", fields=["ARFCIPID"], rowcount=1)
            # qRFC inbound/outbound queues (SMQ1/SMQ2) via the standard reader FM.
            outbound = client.call("TRFC_QOUT_LIST")
            inbound = client.call("TRFC_QIN_GET_CURRENT_QUEUES")
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read ramp-down state: {exc}")

        n_updates = len(vbdata)
        n_trfc = len(arfc)
        n_out = len(outbound.get("QVIEW", []) or [])
        n_in = len(inbound.get("QVIEW", []) or [])
        data = {
            "pending_updates": n_updates,
            "stuck_trfc": n_trfc,
            "qrfc_outbound": n_out,
            "qrfc_inbound": n_in,
        }
        total = n_updates + n_trfc + n_out + n_in
        if total:
            return Result.fail(
                self.name,
                f"source not drained: {n_updates} updates, {n_trfc} tRFC, "
                f"{n_out} qRFC-out, {n_in} qRFC-in still pending",
                data=data,
                facts={
                    "Pending Updates": str(n_updates),
                    "Stuck tRFC": str(n_trfc),
                    "qRFC Outbound": str(n_out),
                    "qRFC Inbound": str(n_in),
                },
            )
        return Result.ok(
            self.name,
            "source drained: no pending updates, tRFC or qRFC entries",
            data=data,
            facts={
                "Pending Updates": "0",
                "Stuck tRFC": "0",
                "qRFC Outbound": "0",
                "qRFC Inbound": "0",
            },
        )
