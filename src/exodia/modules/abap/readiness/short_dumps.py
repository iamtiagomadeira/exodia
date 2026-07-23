"""ABAP short dumps readiness (SAP MIG tasks ~4021, 4022 — ST22 / SNAP).

Reports ABAP runtime errors (short dumps) recorded on the source within a
recent window. A cutover plan reviews ST22 before and after the copy: a burst
of fresh dumps just before takeover, or new dumps right after, is a red flag
that something is unhealthy. Read-only.

WARNs when dumps exist inside the lookback window (default: today). The window
is configurable via ``dumps_since`` (YYYYMMDD); older historical dumps are
ignored so the signal stays about *current* health, not lifetime totals.
"""

from __future__ import annotations

from datetime import UTC, datetime

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase, Side
from exodia.core.severity import Severity

from . import _rfc


class ShortDumpsCheck(Check):
    """Report recent ABAP short dumps on the source (ST22 / SNAP)."""

    name = "abap.readiness.short-dumps"
    description = "Recent ABAP short dumps at takeover (ST22 / SNAP)."
    title = "ST22 — ABAP Short Dumps Check"
    phase = Phase.POST
    # Hygiene, not a copy-blocker: runtime dumps are a go-live quality signal the
    # customer decides to clean or accept — they do NOT fail a system copy. So
    # ADVISORY by default (a COP can reclassify to blocking for a strict
    # engagement via the gate policy). This is the ST22 case from COP_model.md.
    severity = Severity.ADVISORY
    gate_side = Side.SOURCE
    responsible = "customer"

    def parameters(self) -> list[ParamSpec]:
        return [
            *_rfc.SOURCE_CONN_SPECS,
            ParamSpec(
                "dumps_since",
                "Only count dumps on/after this date (YYYYMMDD)",
                help="Lookback boundary for short dumps. Defaults to today (UTC).",
            ),
        ]

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(
                self.name, "no source RFC connection params (set source_ashost + credentials)"
            )
        since = str(ctx.get("dumps_since") or datetime.now(UTC).strftime("%Y%m%d"))
        try:
            client = _rfc.get_client(ctx, side)
            # SNAP holds short-dump headers; DATUM is the date (YYYYMMDD).
            rows = _rfc.read_table(
                client,
                "SNAP",
                fields=["DATUM", "UNAME", "AHOST"],
                where=f"DATUM >= '{since}' AND SEQNO = '000'",
            )
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read short-dump table SNAP: {exc}")

        by_user: dict[str, int] = {}
        for r in rows:
            u = r.get("UNAME", "?")
            by_user[u] = by_user.get(u, 0) + 1
        data = {
            "since": since,
            "dump_count": len(rows),
            "by_user": dict(sorted(by_user.items(), key=lambda kv: -kv[1])),
        }
        if rows:
            return Result.warn(
                self.name,
                f"{len(rows)} ABAP short dump(s) since {since}",
                data=data,
                facts={"Dumps Since": since, "Dump Count": str(len(rows))},
            )
        return Result.ok(
            self.name, f"no ABAP short dumps since {since}", data=data,
            facts={"Dumps Since": since, "Dump Count": "0"},
        )
