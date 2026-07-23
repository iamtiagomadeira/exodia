"""Post-phase check for the Export/Import copy — source-vs-target data consistency.

``export-import.data-consistency`` (POST, BLOCKING) compares row counts between
the source and target from two count manifests (``table,count`` per line, e.g.
produced by a ``SELECT COUNT(*)`` sweep or DB02). A source table missing on the
target, or a lower target count, is a data-loss signal that FAILs the check — the
import must not have dropped records.

The BDLS / SGEN / STMS / purge post-activities are NOT implemented here: they
already exist as the cross-cutting ``abap.post.*`` block (Wave 1) and are reused
via the Phase axis. This check is specific to import row-count integrity.

References (cite by number only): 784118 (system copy / R3load).
"""

from __future__ import annotations

from pathlib import Path

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from .. import _r3load as r


class DataConsistencyCheck(Check):
    """Source-vs-target row-count consistency after the import (no data loss)."""

    name = "export-import.data-consistency"
    description = "Source-vs-target row counts match after import (no records lost)."
    title = "Data Consistency (source vs target row counts)"
    phase = Phase.POST
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [
            ParamSpec(
                "source_counts", "Source row-count manifest path",
                help="File of 'table,count' lines captured on the SOURCE before/during export.",
            ),
            ParamSpec(
                "target_counts", "Target row-count manifest path",
                help="File of 'table,count' lines captured on the TARGET after import.",
            ),
        ]

    def _read(self, ctx: Context, path: str) -> str | None:
        if not ctx.is_remote:
            p = Path(path)
            if not p.is_file():
                return None
            try:
                return p.read_text(errors="replace")
            except OSError:
                return None
        cr = ctx.runner().run(["cat", path], timeout=int(ctx.get("consistency_timeout", 120)))
        return cr.stdout if cr.ok else None

    def run(self, ctx: Context) -> Result:
        src_path = ctx.get("source_counts")
        tgt_path = ctx.get("target_counts")
        if not src_path or not tgt_path:
            return Result.skip(
                self.name,
                "no source_counts / target_counts manifests given — capture row "
                "counts on both sides ('table,count' per line) to verify no data loss",
            )
        src_text = self._read(ctx, str(src_path))
        tgt_text = self._read(ctx, str(tgt_path))
        if src_text is None or tgt_text is None:
            side = "source_counts" if src_text is None else "target_counts"
            return Result.fail(
                self.name,
                f"{side} manifest not found/readable",
                data={"source_counts": str(src_path), "target_counts": str(tgt_path)},
                sap_note="784118",
            )
        source = r.parse_count_manifest(src_text)
        target = r.parse_count_manifest(tgt_text)
        if not source:
            return Result.fail(
                self.name,
                f"source count manifest {src_path} has no usable 'table,count' rows",
                data={"source_counts": str(src_path)},
            )
        diff = r.compare_counts(source, target)
        facts = {
            "Tables Compared": str(diff.common),
            "Matches": str(diff.matches),
            "Mismatches": str(len(diff.mismatches)),
            "Missing on Target": str(len(diff.only_source)),
        }
        data = {
            "common": diff.common,
            "matches": diff.matches,
            "mismatches": diff.mismatches,
            "only_source": diff.only_source,
            "only_target": diff.only_target,
        }
        if not diff.consistent:
            parts: list[str] = []
            if diff.only_source:
                parts.append(
                    f"{len(diff.only_source)} source table(s) missing on target "
                    f"({', '.join(diff.only_source[:5])})"
                )
            if diff.mismatches:
                sample = ", ".join(
                    f"{t}: {s}->{tg}" for t, (s, tg) in list(diff.mismatches.items())[:5]
                )
                parts.append(f"{len(diff.mismatches)} row-count mismatch(es) ({sample})")
            return Result.fail(
                self.name,
                "data consistency FAILED — possible data loss: " + "; ".join(parts),
                data=data,
                facts=facts,
                sap_note="784118",
            )
        extra = (
            f" ({len(diff.only_target)} target-only table(s) ignored)"
            if diff.only_target
            else ""
        )
        return Result.ok(
            self.name,
            f"data consistent: {diff.matches}/{diff.common} table row counts match "
            f"source→target{extra}",
            data=data,
            facts=facts,
        )
