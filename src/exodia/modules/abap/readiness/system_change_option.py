"""System change option readiness (SAP MIG — SE06 / TADIR / T000).

Before a cutover the migration team records whether the system is set to
"modifiable" or "not modifiable" (SE06 global setting) and the software-component
change options. On the target after a copy this is often deliberately locked
down. This check reads the global setting so it is captured as evidence and can
be compared source vs target.

Read-only. Never a hard block — it is a recorded fact / parity signal.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc


class SystemChangeOptionCheck(Check):
    """Global system change option (SE06) captured for the migration record."""

    name = "abap.readiness.system-change-option"
    description = "Global system change option / modifiability (SE06)."
    title = "SE06 — Global System Change Option Check"
    phase = Phase.PREPARATION
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
            # TCESYST holds the global SE06 change flag (GLOBAL: 'X'=modifiable).
            rows = _rfc.read_table(client, "TCESYST", fields=["GLOBAL"])
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read system change option: {exc}")

        flag = rows[0].get("GLOBAL", "") if rows else ""
        modifiable = flag.upper() == "X"
        state = "Modifiable" if modifiable else "Not modifiable"
        return Result.ok(
            self.name,
            f"global system change option: {state}",
            data={"global": flag, "modifiable": modifiable},
            facts={"System Change Option": state},
        )
