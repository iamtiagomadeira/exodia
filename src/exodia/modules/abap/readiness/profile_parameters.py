"""Profile parameter parity check (SAP MIG parity — RZ10/RZ11 / TPFYPROPTY).

A cutover compares key instance/default profile parameters between the source
and the target so the copied system behaves like the original where it must,
and differs only where the target environment legitimately requires it (hosts,
memory sizing). This check reads a curated set of parameters from both sides via
the standard parameter table and diffs them.

Read-only. Reports differing parameters as a WARN (they are usually expected on
a new host, but the engineer must eyeball them), never a hard block — the point
is visibility, not a gate.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc

# Parameters that matter for functional parity across a copy. Host/memory params
# are expected to differ on a new target and are intentionally NOT in this set;
# these are the ones where a silent drift would change behaviour.
_PARITY_PARAMS = (
    "login/system_client",
    "login/no_automatic_user_sapstar",
    "rdisp/max_wprun_time",
    "abap/heap_area_total",
    "zcsa/system_language",
    "install/codepage/appl_server",
    "transport/systemtype",
)


class ProfileParameterParityCheck(Check):
    """Compare key profile parameters source vs target (RZ10/RZ11 parity)."""

    name = "abap.readiness.profile-parameter-parity"
    description = "Key profile parameters compared source vs target (RZ10/RZ11)."
    title = "RZ10/RZ11 — Profile Parameter Parity Check (Source vs Target)"
    phase = Phase.PREPARATION
    blocking = False

    def parameters(self) -> list[ParamSpec]:
        return _rfc.COMPARE_CONN_SPECS

    def _read_params(self, ctx: Context, side: str) -> dict[str, str] | None:
        """Read the curated parameter set from one side's active configuration.

        Uses RFC_READ_TABLE against TPFYPROPTY (parameter -> current value). A
        None return means the side could not be read.
        """
        if not _rfc.has_connection_params(ctx, side):
            return None
        try:
            client = _rfc.get_client(ctx, side)
            rows = _rfc.read_table(
                client, "TPFYPROPTY", fields=["PARNAME", "PVALUE"]
            )
        except _rfc.RfcError:
            return None
        wanted = set(_PARITY_PARAMS)
        return {
            r.get("PARNAME", ""): r.get("PVALUE", "")
            for r in rows
            if r.get("PARNAME", "") in wanted
        }

    def run(self, ctx: Context) -> Result:
        if not _rfc.has_connection_params(ctx, _rfc.SOURCE):
            return Result.skip(
                self.name, "no source RFC connection params (set source_ashost + credentials)"
            )
        src = self._read_params(ctx, _rfc.SOURCE)
        tgt = self._read_params(ctx, _rfc.TARGET)
        if src is None:
            return Result.fail(self.name, "could not read source profile parameters")
        if tgt is None:
            # Source-only inventory (target not reachable this run).
            return Result.ok(
                self.name,
                f"read {len(src)} source profile parameter(s); no target to compare",
                data={"source_params": src},
                facts={"Source Parameters": str(len(src)), "Target": "not compared"},
            )
        differing = {
            k: {"source": src.get(k), "target": tgt.get(k)}
            for k in set(src) | set(tgt)
            if src.get(k) != tgt.get(k)
        }
        data = {"source_params": src, "target_params": tgt, "differing": differing}
        if differing:
            names = ", ".join(sorted(differing))
            return Result.warn(
                self.name,
                f"{len(differing)} profile parameter(s) differ between source and target: {names}",
                data=data,
                facts={
                    "Parameters Compared": str(len(set(src) | set(tgt))),
                    "Differing": str(len(differing)),
                    "Differing Names": names,
                },
            )
        return Result.ok(
            self.name,
            f"all {len(src)} compared profile parameters match between source and target",
            data=data,
            facts={"Parameters Compared": str(len(src)), "Differing": "0"},
        )
