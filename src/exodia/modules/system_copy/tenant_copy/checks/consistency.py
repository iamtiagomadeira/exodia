"""HANA database consistency checks for tenant copy (SAP Note 1785060 / 1977584).

Read-only technical consistency checks for the HANA database, from the SAP
consistency-check framework:

* ``table-consistency`` — ``CALL CHECK_TABLE_CONSISTENCY('CHECK', NULL, NULL)``:
  the logical layer (row/column store, indices, dictionary). An empty result
  set means clean; any rows are inconsistencies (SCHEMA_NAME, TABLE_NAME,
  ERROR_CODE, ERROR_MESSAGE). SAP Note 1785060.
* ``catalog-consistency`` — ``CALL CHECK_CATALOG('CHECK', NULL, NULL)``: the
  catalog / metadata layer.

Both use the CHECK action ONLY — never REPAIR (that is invasive and done under
SAP support guidance). Source and target variants are exposed:

* run on the SOURCE during Preparation to capture a baseline — pre-existing
  inconsistencies must not be blamed on the copy;
* run on the TARGET during Post-Activities to prove the copied tenant is clean.

Note: the persistence layer (data volumes / logs) is checked with ``hdbpersdiag``
on a recovered copy offline — that is a documented manual step, not a safe live
read-only check, so it is intentionally not automated here.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from . import _common as c


def _tenant_key(ctx: Context, side: str) -> str | None:
    """The tenant hdbuserstore key for a side, or None if not provided."""
    if side == c.SOURCE:
        val = ctx.get("source_tenant_key") or ctx.get("tenant_key")
    else:
        val = ctx.get("target_tenant_key") or ctx.get("tenant_key")
    return str(val) if val else None


class _ConsistencyCheck(Check):
    """Shared logic: run a HANA CHECK_* procedure and report inconsistencies.

    Subclasses set ``proc`` (the procedure name), ``side`` and ``phase``. The
    check connects to the tenant via its hdbuserstore key and runs
    ``CALL <proc>('CHECK', NULL, NULL)``. An empty result set is a clean PASS;
    any returned rows are inconsistencies and FAIL (blocking on the target).
    """

    proc = "CHECK_TABLE_CONSISTENCY"
    side = c.SOURCE
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        key = ParamSpec(
            f"{self.side}_tenant_key",
            f"{self.side.capitalize()} tenant hdbuserstore key",
            help=f"hdbsql -U key connecting to the {self.side} tenant to run the "
            "consistency check.",
        )
        return [key]

    def run(self, ctx: Context) -> Result:
        key = _tenant_key(ctx, self.side)
        if not key:
            return Result.skip(
                self.name,
                f"no {self.side} tenant key ({self.side}_tenant_key) — cannot run "
                f"{self.proc}",
            )
        sql = f"CALL {self.proc}('CHECK', NULL, NULL)"
        cr = ctx.runner().run(
            ["hdbsql", "-U", str(key), "-x", "-a", "-j", sql],
            timeout=int(ctx.get("consistency_timeout", 3600)),
        )
        if not cr.ok:
            return Result.fail(
                self.name,
                f"could not run {self.proc} on the {self.side} tenant",
                detail=cr.stderr or cr.stdout,
                facts={"Side": self.side.capitalize(), "Procedure": self.proc, "Ran": "No"},
                sap_note="1785060",
            )
        rows = [r for r in c.parse_hdbsql_rows(cr.stdout) if r and any(f.strip() for f in r)]
        # An empty result set = no inconsistencies found.
        if not rows:
            return Result.ok(
                self.name,
                f"{self.side} {self.proc}: no inconsistencies found",
                data={"side": self.side, "procedure": self.proc, "errors": 0},
                facts={"Side": self.side.capitalize(), "Procedure": self.proc, "Inconsistencies": "0"},
            )
        # Rows returned = inconsistencies. Surface the first few for context.
        sample = "; ".join(", ".join(f.strip() for f in r) for r in rows[:3])
        return Result.fail(
            self.name,
            f"{self.side} {self.proc}: {len(rows)} inconsistency row(s) reported — "
            f"{sample}{'…' if len(rows) > 3 else ''}",
            data={"side": self.side, "procedure": self.proc, "errors": len(rows), "rows": rows[:50]},
            facts={
                "Side": self.side.capitalize(),
                "Procedure": self.proc,
                "Inconsistencies": str(len(rows)),
            },
            sap_note="1785060",
        )


# --------------------------------------------------------------------------- #
# Table consistency (CHECK_TABLE_CONSISTENCY) — SAP Note 1785060
# --------------------------------------------------------------------------- #


class SourceTableConsistencyCheck(_ConsistencyCheck):
    """Source HANA table consistency baseline (CHECK_TABLE_CONSISTENCY)."""

    name = "tenant-copy.hana.source-table-consistency"
    description = "Source HANA table consistency baseline (CHECK_TABLE_CONSISTENCY)."
    title = "Source HANA Table Consistency (CHECK_TABLE_CONSISTENCY)"
    proc = "CHECK_TABLE_CONSISTENCY"
    side = c.SOURCE
    phase = Phase.PREPARATION


class TargetTableConsistencyCheck(_ConsistencyCheck):
    """Target HANA table consistency after the copy (CHECK_TABLE_CONSISTENCY)."""

    name = "tenant-copy.hana.target-table-consistency"
    description = "Target HANA table consistency after copy (CHECK_TABLE_CONSISTENCY)."
    title = "Target HANA Table Consistency (CHECK_TABLE_CONSISTENCY)"
    proc = "CHECK_TABLE_CONSISTENCY"
    side = c.TARGET
    phase = Phase.POST
    blocking = True


# --------------------------------------------------------------------------- #
# Catalog consistency (CHECK_CATALOG)
# --------------------------------------------------------------------------- #


class SourceCatalogConsistencyCheck(_ConsistencyCheck):
    """Source HANA catalog consistency baseline (CHECK_CATALOG)."""

    name = "tenant-copy.hana.source-catalog-consistency"
    description = "Source HANA catalog/metadata consistency baseline (CHECK_CATALOG)."
    title = "Source HANA Catalog Consistency (CHECK_CATALOG)"
    proc = "CHECK_CATALOG"
    side = c.SOURCE
    phase = Phase.PREPARATION


class TargetCatalogConsistencyCheck(_ConsistencyCheck):
    """Target HANA catalog consistency after the copy (CHECK_CATALOG)."""

    name = "tenant-copy.hana.target-catalog-consistency"
    description = "Target HANA catalog/metadata consistency after copy (CHECK_CATALOG)."
    title = "Target HANA Catalog Consistency (CHECK_CATALOG)"
    proc = "CHECK_CATALOG"
    side = c.TARGET
    phase = Phase.POST
    blocking = True
