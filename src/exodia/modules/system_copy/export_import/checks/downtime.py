"""Downtime-phase monitor checks for the Export/Import (R3load/JLoad) copy.

These checks READ the R3load / migration-monitor logs (never invoke R3load) to
give progress, failed-package detection, and post-import integrity. They are the
observability half of the thin orchestrator — the ``swpm.export`` / ``swpm.import``
actions drive sapinst; these checks watch what R3load writes.

* ``export-import.r3load.export-monitor`` (BLOCK) — parse ``export_monitor.log``
  for per-package progress, durations and errors on the source.
* ``export-import.r3load.import-monitor`` (BLOCK) — parse ``import_monitor.log``
  for per-package progress, failed/retried packages on the target.
* ``export-import.import-integrity`` (BLOCK) — confirm ALL packages finished OK
  (no 'E'/error in the *_monitor logs, all ``*.TSK`` tasks ``ok``).

References (cite by number only): 784118 (R3load/system copy tools), 1738258 (SWPM).
"""

from __future__ import annotations

from pathlib import Path

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from .. import _r3load as r


def _read_remote_or_local(ctx: Context, path: str) -> str | None:
    """Read a log file locally (Path) or over the runner (SSHRunner) — read-only.

    Local runs read the file directly; remote runs ``cat`` it through the runner
    (argv list, never shell=True). Returns None when the file is absent/unreadable.
    """
    if not ctx.is_remote:
        p = Path(path)
        if not p.is_file():
            return None
        try:
            return p.read_text(errors="replace")
        except OSError:
            return None
    cr = ctx.runner().run(["cat", path], timeout=int(ctx.get("monitor_timeout", 120)))
    return cr.stdout if cr.ok else None


class ExportMonitorCheck(Check):
    """Monitor the R3load export by parsing ``export_monitor.log`` (read-only).

    Reports how many packages have finished, which failed, and per-package
    durations. A failed package (rc != 0 / 'with error') FAILs the check so the
    export is not treated as clean. While packages are still running with no
    failures, the check WARNs (in-progress) rather than passing prematurely.
    """

    name = "export-import.r3load.export-monitor"
    description = "Monitor the R3load export via export_monitor.log (packages, durations, errors)."
    title = "R3load Export Monitor (export_monitor.log)"
    phase = Phase.DOWNTIME
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [r.EXPORT_MONITOR_LOG]

    def run(self, ctx: Context) -> Result:
        return _monitor_run(self, ctx, ctx.get("export_monitor_log"), "export")


class ImportMonitorCheck(Check):
    """Monitor the R3load import by parsing ``import_monitor.log`` (read-only).

    Same shape as the export monitor but for the target import: reports finished
    / failed / retried packages and durations. A failed package FAILs the check.
    """

    name = "export-import.r3load.import-monitor"
    description = "Monitor the R3load import via import_monitor.log (packages, retries, errors)."
    title = "R3load Import Monitor (import_monitor.log)"
    phase = Phase.DOWNTIME
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [r.IMPORT_MONITOR_LOG]

    def run(self, ctx: Context) -> Result:
        return _monitor_run(self, ctx, ctx.get("import_monitor_log"), "import")


def _monitor_run(check: Check, ctx: Context, log_path: object, kind: str) -> Result:
    """Shared body for the export/import monitors — pure parse of the monitor log."""
    if not log_path:
        return Result.skip(
            check.name,
            f"no {kind}_monitor_log given — point it at the migration monitor's "
            f"{kind}_monitor.log to track per-package progress",
        )
    text = _read_remote_or_local(ctx, str(log_path))
    if text is None:
        return Result.fail(
            check.name,
            f"{kind}_monitor.log not found/readable at {log_path}",
            data={"log": str(log_path)},
            sap_note="784118",
        )
    report = r.parse_monitor_log(text)
    pct = report.progress_pct
    facts = {
        "Completed": str(len(report.completed)),
        "Failed": str(len(report.failed)),
        "Running": str(report.running),
        "Progress": f"{pct:.0f}%" if pct is not None else "n/a",
    }
    data = {
        "log": str(log_path),
        "completed": report.completed,
        "failed": report.failed,
        "running": report.running,
        "durations": report.durations,
        "progress_pct": pct,
    }
    if report.failed:
        return Result.fail(
            check.name,
            f"{kind} has {len(report.failed)} failed package(s): "
            f"{', '.join(report.failed[:10])}"
            f"{'…' if len(report.failed) > 10 else ''} — inspect the R3load *.log for each",
            detail="\n".join(report.error_lines[:20]),
            data=data,
            facts=facts,
            sap_note="784118",
        )
    if report.running > 0 or not report.completed:
        return Result.warn(
            check.name,
            f"{kind} in progress: {len(report.completed)} package(s) done"
            f"{f', {report.running} running' if report.running else ''}"
            f"{f' ({pct:.0f}%)' if pct is not None else ''} — no failures so far",
            data=data,
            facts=facts,
        )
    return Result.ok(
        check.name,
        f"{kind} progressing cleanly: {len(report.completed)} package(s) finished, "
        "no failures",
        data=data,
        facts=facts,
    )


class ImportIntegrityCheck(Check):
    """Post-import integrity: every package finished OK and every task is ``ok``.

    Combines two sources of truth: the ``import_monitor.log`` (no failed package,
    at least one completed) AND the R3load ``*.TSK`` task files (no ``err``/``xeq``
    remaining). A single failed package or a leftover pending/failed task FAILs —
    the import must be provably complete before the target is trusted.
    """

    name = "export-import.import-integrity"
    description = "Post-import integrity: all packages +++/OK, no 'E' in *_monitor logs / *.TSK."
    title = "Import Integrity (packages OK, tasks ok)"
    phase = Phase.DOWNTIME
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [
            r.IMPORT_MONITOR_LOG,
            ParamSpec(
                "tsk_dir", "R3load *.TSK task directory",
                help="Directory holding the R3load *.TSK task files to verify all tasks are 'ok'.",
            ),
        ]

    def run(self, ctx: Context) -> Result:
        log_path = ctx.get("import_monitor_log")
        tsk_dir = ctx.get("tsk_dir")
        if not log_path and not tsk_dir:
            return Result.skip(
                self.name,
                "no import_monitor_log or tsk_dir given — provide at least one to "
                "verify post-import integrity",
            )
        data: dict[str, object] = {}
        facts: dict[str, str] = {}
        problems: list[str] = []

        # 1. Monitor log: no failed package, at least one completed.
        if log_path:
            text = _read_remote_or_local(ctx, str(log_path))
            if text is None:
                problems.append(f"import_monitor.log not readable at {log_path}")
            else:
                report = r.parse_monitor_log(text)
                data["completed"] = report.completed
                data["failed"] = report.failed
                facts["Packages OK"] = str(len(report.completed))
                facts["Packages Failed"] = str(len(report.failed))
                if report.failed:
                    problems.append(
                        f"{len(report.failed)} failed package(s): "
                        f"{', '.join(report.failed[:10])}"
                    )
                elif not report.completed:
                    problems.append("no completed packages found in import_monitor.log")

        # 2. TSK task files: every task 'ok', none 'err'/'xeq'.
        if tsk_dir:
            tsk = self._aggregate_tsk(ctx, str(tsk_dir))
            data["tsk"] = {"ok": tsk.ok, "err": tsk.err, "xeq": tsk.xeq}
            facts["Tasks OK"] = str(tsk.ok)
            if tsk.err:
                problems.append(f"{tsk.err} R3load task(s) in error (err) under {tsk_dir}")
            if tsk.xeq:
                problems.append(f"{tsk.xeq} R3load task(s) still pending (xeq) under {tsk_dir}")
            if tsk.total == 0:
                problems.append(f"no *.TSK task entries found under {tsk_dir}")

        if problems:
            return Result.fail(
                self.name,
                "import integrity check FAILED: " + "; ".join(problems),
                data=data,
                facts=facts,
                sap_note="784118",
            )
        return Result.ok(
            self.name,
            "import integrity verified: all packages finished OK and all R3load tasks are 'ok'",
            data=data,
            facts=facts,
        )

    def _aggregate_tsk(self, ctx: Context, tsk_dir: str) -> r.TskReport:
        """Read + parse every *.TSK under a directory into one aggregate report."""
        agg = r.TskReport()
        # List *.TSK files (argv list, never shell=True on the remote path).
        if ctx.is_remote:
            cr = ctx.runner().run(
                ["sh", "-c", f"cat {tsk_dir}/*.TSK 2>/dev/null"],
                timeout=int(ctx.get("monitor_timeout", 120)),
            )
            text = cr.stdout if cr.ok else ""
            part = r.parse_tsk(text)
            agg.ok += part.ok
            agg.err += part.err
            agg.xeq += part.xeq
            return agg
        d = Path(tsk_dir)
        if not d.is_dir():
            return agg
        for tsk_file in sorted(d.glob("*.TSK")):
            try:
                part = r.parse_tsk(tsk_file.read_text(errors="replace"))
            except OSError:
                continue
            agg.ok += part.ok
            agg.err += part.err
            agg.xeq += part.xeq
        return agg
