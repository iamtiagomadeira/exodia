"""Ramp-down guard-rail for the Export/Import method — source must be quiesced.

Before the R3load/JLoad export runs, the source must be fully quiet. The export
is a point-in-time snapshot: any in-flight update, held lock, or undrained qRFC/
tRFC queue at export time is either lost or exported in an inconsistent state.

``export-import.source.quiesced-verify`` (RAMP_DOWN, BLOCKING) confirms, over
RFC (read-only), that:

* SM12 — no enqueue lock entries are held (ENQUEUE_READ),
* SM13 — no pending update records (VBDATA),
* SMQ1/SMQ2 — inbound/outbound qRFC queues are empty,
* SM58 — no stuck transactional RFC calls (ARFCSSTATE).

This is the export/import counterpart of the cross-cutting
``abap.readiness.update-queues-drained``; it additionally asserts SM12 locks and
is scoped/named for this method so the export action can require it directly.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase
from exodia.modules.abap.readiness import _rfc


class SourceQuiescedVerifyCheck(Check):
    """Source is fully quiesced before the export (SM12/SM13/SMQ1-2/SM58 all zero)."""

    name = "export-import.source.quiesced-verify"
    description = "Source quiesced before export (0 locks SM12, 0 updates SM13, drained qRFC/tRFC)."
    title = "SM12/SM13/SMQ — Source Quiesced Before Export"
    phase = Phase.RAMP_DOWN
    blocking = True

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
            # SM12 — held enqueue locks.
            locks = client.call("ENQUEUE_READ", GCLIENT="", GNAME="", GARG="", GUNAME="")
            # SM13 — pending update records (VBDATA).
            vbdata = _rfc.read_table(client, "VBDATA", fields=["SKEY"], rowcount=1)
            # SM58 — stuck transactional RFC calls (ARFCSSTATE).
            arfc = _rfc.read_table(client, "ARFCSSTATE", fields=["ARFCIPID"], rowcount=1)
            # SMQ1/SMQ2 — qRFC outbound/inbound queues.
            outbound = client.call("TRFC_QOUT_LIST")
            inbound = client.call("TRFC_QIN_GET_CURRENT_QUEUES")
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read source quiesce state: {exc}")

        n_locks = len(locks.get("ENQ", []) or [])
        n_updates = len(vbdata)
        n_trfc = len(arfc)
        n_out = len(outbound.get("QVIEW", []) or [])
        n_in = len(inbound.get("QVIEW", []) or [])
        data = {
            "lock_entries": n_locks,
            "pending_updates": n_updates,
            "stuck_trfc": n_trfc,
            "qrfc_outbound": n_out,
            "qrfc_inbound": n_in,
        }
        facts = {
            "Lock Entries (SM12)": str(n_locks),
            "Pending Updates (SM13)": str(n_updates),
            "Stuck tRFC (SM58)": str(n_trfc),
            "qRFC Outbound (SMQ1)": str(n_out),
            "qRFC Inbound (SMQ2)": str(n_in),
        }
        total = n_locks + n_updates + n_trfc + n_out + n_in
        if total:
            return Result.fail(
                self.name,
                f"source NOT quiesced: {n_locks} locks, {n_updates} updates, "
                f"{n_trfc} tRFC, {n_out} qRFC-out, {n_in} qRFC-in still pending — "
                "the export would capture an inconsistent snapshot",
                data=data,
                facts=facts,
            )
        return Result.ok(
            self.name,
            "source fully quiesced — no locks, updates, tRFC or qRFC entries; safe to export",
            data=data,
            facts=facts,
        )
