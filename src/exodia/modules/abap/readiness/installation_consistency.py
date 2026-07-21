"""Installation consistency check (SAP MIG — SICK / SM28).

SICK (the installation check) verifies core consistency: kernel vs database
release, character set, critical structure lengths. A cutover records a clean
SICK on the source before copy and re-runs it on the target after. This check
reads the installation-check results over RFC.

Read-only. FAILs when SICK reports errors (a genuine consistency problem), else
records a clean result as evidence.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc


class InstallationConsistencyCheck(Check):
    """SICK / SM28 installation consistency check (kernel vs DB, charset, ...)."""

    name = "abap.readiness.installation-consistency"
    description = "Installation consistency (SICK / SM28): no reported errors."
    title = "SICK/SM28 — Installation Consistency Check"
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
            # OCS_GET_INSTALLED_COMPS / installation check FM surfaces errors as
            # a message table; we use the generic installation check reader.
            res = client.call("SUSR_CHECK_INSTALLATION_CONSISTENCY")
        except _rfc.RfcError:
            # Fall back to a clean SKIP when the FM isn't available on this rev —
            # SICK is not exposed identically on every release.
            return Result.skip(
                self.name,
                "installation consistency FM not available on this release; run SICK manually",
            )

        messages = res.get("ET_MESSAGES", []) or res.get("MESSAGES", []) or []
        errors = [
            m for m in messages if str(m.get("TYPE", "")).upper() in ("E", "A")
        ]
        if errors:
            return Result.fail(
                self.name,
                f"SICK reports {len(errors)} consistency error(s)",
                data={"errors": errors, "message_count": len(messages)},
                facts={"Consistency Errors": str(len(errors))},
            )
        return Result.ok(
            self.name,
            "installation consistency check reports no errors",
            data={"message_count": len(messages)},
            facts={"Consistency Errors": "0"},
        )
