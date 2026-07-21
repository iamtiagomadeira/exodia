"""Software component version comparison (SAP MIG task 1046).

Reads table CVERS (installed software components + release + patch level) on
both source and target over RFC and diffs them. In a cutover you must confirm
the target lands on the *same* component stack as the source; a silent mismatch
(e.g. a different S/4HANA or SAP_BASIS SP) is exactly the kind of thing this
catches before go-live instead of after.

Read-only on both sides. WARNs (does not FAIL) on a mismatch: the engineer
decides whether a delta is expected, but it is surfaced loudly with the exact
components that differ.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _rfc


class ComponentVersionsCheck(Check):
    """Compare installed software components (CVERS) source vs target."""

    name = "abap.readiness.component-versions"
    description = "Compare software component versions (CVERS) between source and target."
    title = "CVERS — Software Component Versions Check (Source vs Target)"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return _rfc.COMPARE_CONN_SPECS

    def _components(self, ctx: Context, side: str) -> dict[str, str] | None:
        """Return {COMPONENT: 'release SP<level>'} for a side, or None if unreachable."""
        if not _rfc.has_connection_params(ctx, side):
            return None
        client = _rfc.get_client(ctx, side)
        rows = _rfc.read_table(
            client,
            "CVERS",
            fields=["COMPONENT", "RELEASE", "EXTRELEASE"],
        )
        return {
            r["COMPONENT"]: f"{r.get('RELEASE', '')} SP{r.get('EXTRELEASE', '')}".strip()
            for r in rows
            if r.get("COMPONENT")
        }

    def run(self, ctx: Context) -> Result:
        if not _rfc.has_connection_params(ctx, _rfc.SOURCE):
            return Result.skip(self.name, "no source RFC connection params")
        try:
            source = self._components(ctx, _rfc.SOURCE)
            target = self._components(ctx, _rfc.TARGET)
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read CVERS: {exc}")

        if source is None:
            return Result.skip(self.name, "no source RFC connection params")
        if target is None:
            # Target not provided: just inventory the source (still useful evidence).
            return Result.ok(
                self.name,
                f"source has {len(source)} software components (no target to compare)",
                data={"source_components": source},
                facts={"Source Components": str(len(source)), "Target Components": "n/a"},
            )

        only_source = sorted(set(source) - set(target))
        only_target = sorted(set(target) - set(source))
        differing = sorted(
            comp for comp in set(source) & set(target) if source[comp] != target[comp]
        )
        deltas = {
            comp: {"source": source[comp], "target": target[comp]} for comp in differing
        }
        data = {
            "source_count": len(source),
            "target_count": len(target),
            "only_on_source": only_source,
            "only_on_target": only_target,
            "differing": deltas,
        }
        if only_source or only_target or differing:
            return Result.warn(
                self.name,
                f"component mismatch: {len(differing)} differ, "
                f"{len(only_source)} only on source, {len(only_target)} only on target",
                data=data,
                facts={
                    "Source Components": str(len(source)),
                    "Target Components": str(len(target)),
                    "Differing": ", ".join(differing) or "none",
                    "Only On Source": str(len(only_source)),
                    "Only On Target": str(len(only_target)),
                },
            )
        return Result.ok(
            self.name,
            f"all {len(source)} software components match between source and target",
            data=data,
            facts={
                "Source Components": str(len(source)),
                "Target Components": str(len(target)),
                "Differing": "none",
            },
        )
