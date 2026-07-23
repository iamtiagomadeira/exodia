"""Guarded action: recreate DB statistics + indexes on the target post-import.

``export-import.target.db-statistics`` runs an update-statistics pass on the
target database after the import so the optimizer has fresh, accurate stats (a
fresh import has stale/empty stats and cripples runtime performance until they
are rebuilt). Non-blocking: a system can technically run without it, but every
system copy runbook recreates stats before handing the target over.

Thin orchestrator: engine-aware command selection (BRCONNECT for Oracle/DB2,
HANA update-statistics SQL for HANA), argv lists only, no secrets on the CLI.

References (cite by number only): 588668 (BRCONNECT update statistics),
1642148 (HANA admin).
"""

from __future__ import annotations

from exodia.core import Context, Result
from exodia.core.base import Action
from exodia.core.params import DB_TYPE, ParamSpec
from exodia.core.result import Phase

from .. import _r3load as r


class TargetDbStatisticsAction(Action):
    """Recreate DB optimizer statistics on the target after the import, guarded."""

    name = "export-import.target.db-statistics"
    description = "Recreate target DB statistics/indexes after import (BRCONNECT / update statistics)."
    title = "Target DB Statistics (post-import update stats)"
    phase = Phase.POST
    destructive = True
    requires_checks: list[str] = []

    def parameters(self) -> list[ParamSpec]:
        return [
            DB_TYPE.with_default("hana"),
            r.TARGET_DB,
            ParamSpec(
                "brconnect_sid", "SID for BRCONNECT (Oracle/DB2)",
                help="SAP SID BRCONNECT operates on, e.g. 'ABC'. Used for Oracle/DB2 targets.",
            ),
            ParamSpec(
                "userstore_key", "hdbsql userstore key (HANA)",
                help="hdbsql -U key for the HANA target (update statistics). No password on the CLI.",
            ),
        ]

    # --- resolution -----------------------------------------------------------

    @staticmethod
    def _db_type(ctx: Context) -> str:
        return str(ctx.get("target_db") or ctx.db_type or "hana").lower()

    def _plan_argv(self, ctx: Context) -> tuple[list[str], str]:
        """Return (argv, human-note) for the engine's update-statistics command.

        Raises ValueError with a clear message when the engine is unsupported or a
        required parameter (SID / userstore key) is missing.
        """
        db = self._db_type(ctx)
        if db in ("oracle", "db2"):
            sid = ctx.get("brconnect_sid") or ctx.sid
            if not sid:
                raise ValueError(
                    "BRCONNECT needs a SID (set brconnect_sid or ctx.sid) for the "
                    f"{db} target update-statistics"
                )
            # BRCONNECT collects optimizer stats for all tables (-u system -c
            # unattended -f stats). argv list — never shell=True.
            argv = ["brconnect", "-u", "/", "-c", "-f", "stats", "-t", "all"]
            return argv, f"BRCONNECT update statistics on {sid} ({db})"
        if db == "hana":
            key = ctx.get("userstore_key")
            if not key:
                raise ValueError(
                    "HANA update statistics needs a userstore_key (hdbsql -U key)"
                )
            # HANA refreshes column-store statistics via the optimizer; a
            # system-wide refresh is driven through hdbsql (secret-free -U key).
            sql = "UPDATE STATISTICS"
            argv = ["hdbsql", "-U", str(key), "-x", "-a", sql]
            return argv, "HANA UPDATE STATISTICS on the target"
        if db in ("ase", "sybase"):
            # ASE uses update statistics per DB; a driver script is expected.
            raise ValueError(
                "ASE update statistics is run per-database via 'update statistics' "
                "in isql — provide it through the ASE post-copy runbook step"
            )
        raise ValueError(f"unsupported target db for statistics update: {db}")

    # --- Action phases --------------------------------------------------------

    def dry_run(self, ctx: Context) -> Result:
        phase = f"{self.name}.dry-run"
        try:
            argv, note = self._plan_argv(ctx)
        except ValueError as exc:
            return Result.fail(phase, str(exc), sap_note="588668")
        return Result.ok(
            phase,
            f"would recreate target DB statistics ({note}); nothing executed",
            detail=f"command: {' '.join(argv)}",
            data={"db_type": self._db_type(ctx), "argv": argv, "note": note},
            sap_note="588668",
        )

    def execute(self, ctx: Context) -> Result:
        phase = f"{self.name}.execute"
        try:
            argv, note = self._plan_argv(ctx)
        except ValueError as exc:
            return Result.fail(phase, str(exc), sap_note="588668")
        self._emit_phase("update statistics", note)
        self._emit_log(f"$ {' '.join(argv[:3])} … {note}")
        cr = ctx.runner().run(argv, timeout=int(ctx.get("stats_timeout", 7200)))
        if cr.stdout:
            self._emit_log(cr.stdout)
        if not cr.ok:
            return Result.fail(
                phase,
                f"update statistics failed (exit {cr.exit_code}) — the target will "
                "have poor optimizer plans until stats are rebuilt; re-run is safe",
                detail=cr.stderr or cr.stdout,
                data={"argv": argv, "exit_code": cr.exit_code},
                sap_note="588668",
            )
        return Result.ok(
            phase,
            f"target DB statistics recreated ({note})",
            data={"db_type": self._db_type(ctx), "argv": argv},
        )

    def verify(self, ctx: Context) -> Result:
        # Statistics recreation has no cheap universal verification (each engine
        # exposes stats freshness differently); the execute rc is the signal.
        return Result.skip(
            f"{self.name}.verify",
            "no generic post-check for update statistics — confirm optimizer stats "
            "freshness in DBACOCKPIT / the engine's stats view per the runbook",
        )

    def rollback(self, ctx: Context) -> Result:
        return Result.skip(
            f"{self.name}.rollback",
            "no rollback needed — recreating statistics is idempotent and safe to re-run",
        )
