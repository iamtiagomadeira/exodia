"""Preparation-phase guard-rail checks for the Export/Import (R3load/JLoad) copy.

These are the read-only guard-rails the export/import method was missing before
the source export is allowed to start. Every check is read-only; blocking ones
FAIL the prepare pipeline so the cutover cannot proceed on a bad footing.

* ``export-import.source.db-consistency`` (BLOCK) — the source database has no
  inconsistent tables before the export (DB02 / DBACOCKPIT consistency).
* ``export-import.migration-key`` (BLOCK) — a valid SAP migration key is present
  for a heterogeneous (OS/DB) system copy (SAP Note 82478).
* ``export-import.unicode-compatibility`` (BLOCK) — the source→target code page /
  Unicode combination is supported (SAP Notes 43853 / 745030).
* ``export-import.target.import-space`` (BLOCK) — the target DB data filesystem
  and the import staging directory have room + are writable.
* ``export-import.r3load.table-splitting-plan`` (n-block) — a table/package
  splitting plan (R3ta / str_splitter) exists for large tables (SAP Note 1043380).

References (cite by number only): 784118, 1738258, 82478, 43853, 745030, 1043380.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from .. import _r3load as r


def _run(ctx: Context, argv: list[str], timeout: int = 120):  # type: ignore[no-untyped-def]
    return ctx.runner().run(argv, timeout=timeout)


def _avail_gb(cr) -> float | None:  # type: ignore[no-untyped-def]
    """Parse ``df -BG --output=avail <path>`` output into available GB."""
    if not cr.ok:
        return None
    lines = [ln.strip() for ln in cr.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    try:
        return float(lines[-1].rstrip("G"))
    except ValueError:
        return None


class SourceDbConsistencyCheck(Check):
    """Source DB has no inconsistent tables before the export (DB02/DBACOCKPIT).

    Exporting an inconsistent source silently propagates the corruption into the
    target, where it is far harder to diagnose. This confirms a clean baseline.
    The consistency-report path is supplied by the operator (a saved DB02 / DBA
    check output); its presence + a zero inconsistency count is the gate.
    """

    name = "export-import.source.db-consistency"
    description = "Source database has no inconsistent tables before export (DB02/DBACOCKPIT)."
    title = "Source DB Consistency (DB02 / DBACOCKPIT)"
    phase = Phase.PREPARATION
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [
            ParamSpec(
                "db_consistency_report", "DB consistency report path",
                help="Saved DB02/DBACOCKPIT consistency output to inspect for inconsistent tables.",
            )
        ]

    def run(self, ctx: Context) -> Result:
        report = ctx.get("db_consistency_report")
        if not report:
            return Result.skip(
                self.name,
                "no db_consistency_report given — run DB02/DBACOCKPIT consistency "
                "on the source and point db_consistency_report at the saved output",
            )
        exists = _run(ctx, ["test", "-f", str(report)])
        if not exists.ok:
            return Result.fail(
                self.name,
                f"consistency report not found at {report}",
                data={"report": str(report)},
                sap_note="784118",
            )
        # Count lines flagged inconsistent/corrupt. A clean report has none.
        cr = _run(
            ctx,
            ["grep", "-ic", "-e", "inconsistent", "-e", "corrupt", "-e", "missing index", str(report)],
        )
        # grep -c prints the count on stdout (exit 1 when zero matches).
        raw = (cr.stdout or "0").strip().splitlines()[0] if cr.stdout.strip() else "0"
        try:
            hits = int(raw)
        except ValueError:
            hits = 0
        if hits > 0:
            return Result.fail(
                self.name,
                f"source DB consistency report flags {hits} inconsistency line(s) — "
                "resolve them before exporting (an inconsistent source corrupts the target)",
                data={"report": str(report), "inconsistencies": hits},
                facts={"Inconsistencies": str(hits)},
                sap_note="784118",
            )
        return Result.ok(
            self.name,
            f"source DB consistency report at {report} shows no inconsistencies",
            data={"report": str(report), "inconsistencies": 0},
            facts={"Inconsistencies": "0"},
        )


class MigrationKeyCheck(Check):
    """A valid SAP migration key must be present for a heterogeneous copy.

    R3load refuses a heterogeneous (OS/DB-changing) export/import without a valid
    migration key from SAP. For a homogeneous copy the key is not required, so
    the check PASSes with a note. The key is a license token, not a password —
    it is safe to reference by shape (never printed in full).
    """

    name = "export-import.migration-key"
    description = "SAP migration key present/valid for a heterogeneous system copy."
    title = "SAP Migration Key (heterogeneous copy)"
    phase = Phase.PREPARATION
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [r.SOURCE_DB, r.TARGET_DB, r.MIGRATION_KEY]

    def run(self, ctx: Context) -> Result:
        source_db = ctx.get("source_db")
        target_db = ctx.get("target_db") or ctx.db_type
        heterogeneous = r.is_heterogeneous(source_db, target_db)
        key = ctx.get("migration_key")
        if not heterogeneous:
            return Result.ok(
                self.name,
                f"homogeneous copy (source_db={source_db or '?'} == target_db={target_db or '?'}) "
                "— no migration key required",
                data={"heterogeneous": False, "source_db": source_db, "target_db": target_db},
                facts={"Heterogeneous": "No", "Migration Key": "not required"},
            )
        if not key:
            return Result.fail(
                self.name,
                "heterogeneous copy requires an SAP migration key but none given "
                "(set migration_key) — request it from SAP for the OS/DB migration",
                data={"heterogeneous": True, "source_db": source_db, "target_db": target_db},
                facts={"Heterogeneous": "Yes", "Migration Key": "MISSING"},
                sap_note="82478",
            )
        if not r.is_valid_migration_key(key):
            return Result.fail(
                self.name,
                "migration_key is present but malformed (expected a ~24-char "
                "alphanumeric token) — re-copy it exactly from the SAP key request",
                data={"heterogeneous": True, "key_valid": False},
                facts={"Heterogeneous": "Yes", "Migration Key": "invalid shape"},
                sap_note="82478",
            )
        return Result.ok(
            self.name,
            "valid SAP migration key present for the heterogeneous system copy",
            # Never echo the key itself — only its presence/validity.
            data={"heterogeneous": True, "key_valid": True},
            facts={"Heterogeneous": "Yes", "Migration Key": "valid"},
        )


class UnicodeCompatibilityCheck(Check):
    """Source→target code page / Unicode combination must be supported.

    Modern NetWeaver targets are Unicode-only. A non-Unicode source into a
    Unicode target is a *Unicode conversion* — supported, but it MUST be flagged
    (nametab / SPUMG preparation, extra R3load options). A Unicode source into a
    non-Unicode target is NOT supported and is blocked.
    """

    name = "export-import.unicode-compatibility"
    description = "Source→target code page / Unicode combination is supported."
    title = "Unicode / Code Page Compatibility"
    phase = Phase.PREPARATION
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [r.SOURCE_UNICODE, r.TARGET_UNICODE]

    def run(self, ctx: Context) -> Result:
        src_uc = r.as_bool(ctx.get("source_unicode"), default=True)
        tgt_uc = r.as_bool(ctx.get("target_unicode"), default=True)
        facts = {
            "Source Unicode": "Yes" if src_uc else "No",
            "Target Unicode": "Yes" if tgt_uc else "No",
        }
        # Unicode -> non-Unicode is a downgrade R3load will not perform.
        if src_uc and not tgt_uc:
            return Result.fail(
                self.name,
                "Unicode source into a non-Unicode target is NOT supported — the "
                "target NetWeaver release must be Unicode",
                data={"source_unicode": src_uc, "target_unicode": tgt_uc},
                facts=facts,
                sap_note="43853",
            )
        # non-Unicode -> Unicode is a conversion: supported but must be prepared.
        if not src_uc and tgt_uc:
            return Result.warn(
                self.name,
                "non-Unicode source into a Unicode target is a UNICODE CONVERSION — "
                "ensure SPUMG/nametab preparation is complete and the export uses the "
                "Unicode conversion options (SAP Note 745030)",
                data={"source_unicode": src_uc, "target_unicode": tgt_uc, "conversion": True},
                facts={**facts, "Mode": "Unicode conversion"},
                sap_note="745030",
            )
        return Result.ok(
            self.name,
            f"code page compatible (source Unicode={src_uc}, target Unicode={tgt_uc})",
            data={"source_unicode": src_uc, "target_unicode": tgt_uc, "conversion": False},
            facts={**facts, "Mode": "no conversion"},
        )


class TargetImportSpaceCheck(Check):
    """The target DB data filesystem + import staging dir must have room and be writable."""

    name = "export-import.target.import-space"
    description = "Target DB data filesystem and import staging directory have space + are writable."
    title = "Target Import Space & I/O"
    phase = Phase.PREPARATION
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [r.TARGET_DATA_DIR, r.IMPORT_DIR, r.IMPORT_SIZE_GB]

    def run(self, ctx: Context) -> Result:
        data_dir = ctx.get("target_data_dir")
        import_dir = ctx.get("import_dir") or "/export"
        if not data_dir:
            return Result.skip(
                self.name,
                "no target_data_dir given — set it to the target DB data filesystem "
                "so import free space can be checked",
            )
        # 1. Import staging dir must be readable/writable for R3load.
        writable = _run(ctx, ["test", "-w", str(import_dir)])
        if not writable.ok:
            return Result.fail(
                self.name,
                f"import staging directory {import_dir} is not writable — R3load needs "
                "write access there for task/log files",
                data={"import_dir": str(import_dir)},
                sap_note="784118",
            )
        # 2. Target data filesystem free space vs expected imported size.
        cr = _run(ctx, ["df", "-BG", "--output=avail", str(data_dir)])
        avail = _avail_gb(cr)
        if avail is None:
            return Result.warn(
                self.name,
                f"could not read free space for the target data dir {data_dir}",
                detail=cr.stderr or cr.stdout,
                data={"target_data_dir": str(data_dir)},
            )
        raw = ctx.get("import_size_gb")
        if raw is None:
            return Result.ok(
                self.name,
                f"{avail:.0f}G free at {data_dir}; import staging {import_dir} writable "
                "(no expected size given to compare)",
                data={"target_data_dir": str(data_dir), "avail_gb": avail},
                facts={"Free (target data)": f"{avail:.0f}G"},
            )
        try:
            needed = float(raw)
        except (TypeError, ValueError):
            return Result.warn(self.name, f"invalid import_size_gb: {raw!r}")
        # A DB import inflates over the raw dump (indexes, fillfactor): keep 30% headroom.
        required = needed * 1.3
        if avail < required:
            return Result.fail(
                self.name,
                f"{avail:.0f}G free at {data_dir} < ~{required:.0f}G needed "
                f"(import {needed:.0f}G + 30% for indexes/headroom)",
                data={"target_data_dir": str(data_dir), "avail_gb": avail, "required_gb": required},
                facts={"Free (target data)": f"{avail:.0f}G", "Needed": f"~{required:.0f}G"},
                sap_note="784118",
            )
        return Result.ok(
            self.name,
            f"{avail:.0f}G free at {data_dir} ≥ ~{required:.0f}G needed; "
            f"import staging {import_dir} writable",
            data={"target_data_dir": str(data_dir), "avail_gb": avail, "required_gb": required},
            facts={"Free (target data)": f"{avail:.0f}G", "Needed": f"~{required:.0f}G"},
        )


class TableSplittingPlanCheck(Check):
    """A table/package splitting plan (R3ta / str_splitter) exists for large tables.

    For large tables, splitting the export into parallel R3load packages is what
    makes the downtime window fit. This confirms the split output (WHR/STR files)
    is present so the export can run in parallel — non-blocking (a small system
    may not need splitting), but a strong recommendation.
    """

    name = "export-import.r3load.table-splitting-plan"
    description = "Table/package splitting plan (R3ta/str_splitter) prepared for large tables."
    title = "R3load Table-Splitting Plan (R3ta / str_splitter)"
    phase = Phase.PREPARATION
    blocking = False

    def parameters(self) -> list[ParamSpec]:
        return [r.SPLIT_DIR]

    def run(self, ctx: Context) -> Result:
        split_dir = ctx.get("split_dir")
        if not split_dir:
            return Result.warn(
                self.name,
                "no split_dir given — if the source has large tables, prepare a "
                "table-splitting plan (R3ta/str_splitter) and point split_dir at its "
                "output to parallelise the export (SAP Note 1043380)",
                sap_note="1043380",
            )
        exists = _run(ctx, ["test", "-d", str(split_dir)])
        if not exists.ok:
            return Result.warn(
                self.name,
                f"split_dir {split_dir} not found — no table-splitting plan prepared",
                data={"split_dir": str(split_dir)},
                sap_note="1043380",
            )
        # Count the WHR/STR split artefacts (str_splitter output).
        cr = _run(
            ctx,
            ["sh", "-c", f"ls -1 {str(split_dir)}/*.WHR {str(split_dir)}/*.STR 2>/dev/null | wc -l"],
        )
        try:
            n = int((cr.stdout or "0").strip().splitlines()[0]) if cr.stdout.strip() else 0
        except (ValueError, IndexError):
            n = 0
        if n == 0:
            return Result.warn(
                self.name,
                f"split_dir {split_dir} present but no WHR/STR split files found — "
                "run R3ta / str_splitter to produce the splitting plan for large tables",
                data={"split_dir": str(split_dir), "split_files": 0},
                sap_note="1043380",
            )
        return Result.ok(
            self.name,
            f"table-splitting plan present: {n} split artefact(s) under {split_dir}",
            data={"split_dir": str(split_dir), "split_files": n},
            facts={"Split Files": str(n)},
        )
