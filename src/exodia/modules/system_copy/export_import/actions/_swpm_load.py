"""Shared base for the guarded SWPM export/import actions (thin orchestrator).

Both ``export-import.swpm.export`` (source) and ``export-import.swpm.import``
(target) drive SWPM/sapinst in unattended mode over an existing
``inifile.params`` and then MONITOR the R3load migration-monitor log. They share
everything except which product id / inifile / monitor log they use, so the
common flow lives here and each concrete action only names its direction.

Hard rules (inherited from core, restated so they are never lost):
  * Commands are always ``argv: list[str]`` — never ``shell=True``.
  * Secrets (instkey.pkey / passwords) never land in argv, env, or a Result.
  * DRY-RUN NEVER invokes sapinst/R3load — it only describes the command +
    param file that WOULD run.
  * Errors PAUSE (FAIL) — sapinst is never killed.

References (cite by number only): 2230669 (SWPM / product IDs), 950619 (system
copy inifile), 784118 (R3load / migration monitor).
"""

from __future__ import annotations

from pathlib import Path

from exodia.core import Context, Result
from exodia.core.base import Action
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from ...backup_restore.actions.swpm.planner import (
    InifileError,
    build_plan,
    gui_url,
    parse_progress,
    validate_inifile,
)
from .. import _r3load as r


class _SwpmLoadAction(Action):
    """Common guarded orchestration for a headless SWPM export OR import.

    Subclasses set :attr:`direction` ("export"/"import"), the inifile/product-id
    param keys, and the migration-monitor log key. The four Action phase methods
    are implemented once here.
    """

    #: "export" (source) or "import" (target).
    direction: str = "export"
    #: param key holding the migration monitor log for this direction.
    monitor_log_key: str = "export_monitor_log"

    destructive = True
    phase = Phase.DOWNTIME
    requires_checks: list[str] = []

    # --- parameter resolution -------------------------------------------------

    def parameters(self) -> list[ParamSpec]:
        return [
            r.STACK,
            r.SAPINST_PATH,
            r.INIFILE,
            r.PRODUCT_ID,
            r.EXPORT_DIR if self.direction == "export" else r.IMPORT_DIR,
            ParamSpec(self.monitor_log_key, f"{self.direction}_monitor.log path"),
            ParamSpec("sapinst_log", "sapinst_dev.log path (optional)"),
        ]

    @staticmethod
    def _inifile(ctx: Context) -> str | None:
        val = ctx.get("inifile")
        return str(val) if val else None

    @staticmethod
    def _product_id(ctx: Context) -> str:
        return str(ctx.get("product_id", ""))

    @staticmethod
    def _sapinst_path(ctx: Context) -> str:
        return str(ctx.get("sapinst_path", "/usr/sap/SWPM/sapinst"))

    @staticmethod
    def _start_guiserver(ctx: Context) -> bool:
        return bool(ctx.get("start_guiserver", True))

    def _monitor_log(self, ctx: Context) -> str | None:
        val = ctx.get(self.monitor_log_key)
        return str(val) if val else None

    def _build_plan(self, ctx: Context) -> object:
        return build_plan(
            sapinst_path=self._sapinst_path(ctx),
            inifile_path=self._inifile(ctx) or "",
            product_id=self._product_id(ctx),
            start_guiserver=self._start_guiserver(ctx),
        )

    # --- shared prepare (validate inifile, secret-free) -----------------------

    def _prepare(self, ctx: Context, phase: str) -> Result:
        product_id = self._product_id(ctx)
        if not product_id:
            return Result.fail(
                phase,
                f"no product_id for the {self.direction} (set params.product_id; "
                "see SAP Note 2230669)",
                sap_note="2230669",
            )
        try:
            info = validate_inifile(self._inifile(ctx))
        except InifileError as exc:
            return Result.fail(phase, str(exc), sap_note="950619")
        return Result.ok(
            phase,
            f"inifile validated for {self.direction} ({len(info.keys_found)} key(s) present)",
            data={
                "inifile": info.path,
                "keys_found": info.keys_found,
                # Confirm the secret's PRESENCE only — never read its value.
                "instkey_pkey_present": info.has_secret_pkey,
                "product_id": product_id,
                "direction": self.direction,
            },
        )

    # --- Action phase methods -------------------------------------------------

    def dry_run(self, ctx: Context) -> Result:
        phase = f"{self.name}.dry-run"
        prep = self._prepare(ctx, phase)
        if prep.status.is_blocking:
            return prep
        plan = self._build_plan(ctx)
        guiserver_on = self._start_guiserver(ctx)
        detail_lines = [
            "sub-phase 1 prepare_inifile: validate existing inifile.params (done above)",
            f"sub-phase 2 run_sapinst ({self.direction}, headless): {plan.display}",  # type: ignore[attr-defined]
            "  strategy: launched detached (setsid/nohup) so the load survives the session",
            f"  R3load/JLoad does the certified {self.direction}; Exodia only orchestrates + monitors",
            (
                f"sub-phase 3 monitor: parse {self.monitor_log_key} "
                f"({self.direction}_monitor.log) for per-package progress/errors; "
                "'waiting for input' => WARN (GUI handoff); error => FAIL (pause, never kill)"
            ),
        ]
        if guiserver_on:
            detail_lines.append(f"  observer-mode GUI handoff URL: {gui_url(ctx.host)}")
        return Result.ok(
            phase,
            f"would run headless SWPM {self.direction} for "
            f"{self._product_id(ctx)}; nothing executed",
            detail="\n".join(detail_lines),
            data={
                "direction": self.direction,
                "argv": plan.argv,  # type: ignore[attr-defined]
                "env": plan.env,  # type: ignore[attr-defined]
                "observer_mode": guiserver_on,
                "gui_url": gui_url(ctx.host) if guiserver_on else None,
                "inifile": prep.data.get("inifile"),
                "instkey_pkey_present": prep.data.get("instkey_pkey_present"),
                "monitor_log": self._monitor_log(ctx),
            },
            sap_note="2230669",
        )

    def execute(self, ctx: Context) -> Result:
        phase = f"{self.name}.execute"
        prep = self._prepare(ctx, phase)
        if prep.status.is_blocking:
            return prep
        plan = self._build_plan(ctx)
        runner = ctx.runner()
        # SAPINST_* exported inline via `env` in the argv list (never shell=True).
        env_argv = [f"{k}={v}" for k, v in plan.env.items()]  # type: ignore[attr-defined]
        launch_argv = ["setsid", "nohup", "env", *env_argv, *plan.argv]  # type: ignore[attr-defined]

        self._emit_phase(f"swpm {self.direction}", plan.display)  # type: ignore[attr-defined]
        self._emit_log(f"$ launching sapinst for {self.direction} (detached)")
        cr = runner.run(launch_argv, timeout=int(ctx.get("launch_timeout", 300)))
        if not cr.ok:
            return Result.fail(
                phase,
                f"sapinst failed to launch for {self.direction} (exit {cr.exit_code}) — "
                "run paused, not killed",
                detail=cr.stderr or cr.stdout,
                data={"direction": self.direction, "exit_code": cr.exit_code, "argv": launch_argv},
                sap_note="2230669",
            )
        report = parse_progress(cr.stdout + "\n" + cr.stderr)
        self._emit_log(f"sapinst state: {report.state.value}")
        if report.state.value == "error":
            return Result.fail(
                phase,
                f"sapinst reported an error launching the {self.direction} — "
                "run PAUSED (not killed)",
                detail=report.detail,
                data={"direction": self.direction, "state": report.state.value},
                sap_note="2230669",
            )
        return Result.warn(
            phase,
            f"sapinst launched for the {self.direction} and running headless — "
            f"monitor via {self.monitor_log_key} or the GUI",
            detail=f"Observer-mode GUI: {gui_url(ctx.host)}",
            data={
                "direction": self.direction,
                "state": report.state.value,
                "gui_url": gui_url(ctx.host),
                "argv": launch_argv,
            },
        )

    def verify(self, ctx: Context) -> Result:
        """Verify by parsing the migration-monitor log for this direction."""
        phase = f"{self.name}.verify"
        log_path = self._monitor_log(ctx)
        if not log_path:
            return Result.warn(
                phase,
                f"no {self.monitor_log_key} to verify the {self.direction} — "
                f"set it to the migration monitor's {self.direction}_monitor.log",
            )
        text = self._read_log(ctx, log_path)
        if text is None:
            return Result.warn(
                phase,
                f"{self.direction}_monitor.log not available yet at {log_path} — "
                "the load may still be starting (check the GUI for a handoff)",
                data={"gui_url": gui_url(ctx.host)},
            )
        report = r.parse_monitor_log(text)
        pct = report.progress_pct
        facts = {
            "Completed": str(len(report.completed)),
            "Failed": str(len(report.failed)),
            "Progress": f"{pct:.0f}%" if pct is not None else "n/a",
        }
        data = {
            "direction": self.direction,
            "completed": report.completed,
            "failed": report.failed,
            "progress_pct": pct,
        }
        if report.failed:
            return Result.fail(
                phase,
                f"{self.direction} has {len(report.failed)} failed package(s): "
                f"{', '.join(report.failed[:10])} — run PAUSED, inspect the R3load logs",
                detail="\n".join(report.error_lines[:20]),
                data=data,
                facts=facts,
                sap_note="784118",
            )
        if report.running > 0 or not report.completed:
            return Result.warn(
                phase,
                f"{self.direction} still in progress "
                f"({len(report.completed)} package(s) done"
                f"{f', {pct:.0f}%' if pct is not None else ''}) — no failures so far",
                data=data,
                facts=facts,
            )
        return Result.ok(
            phase,
            f"{self.direction} completed: {len(report.completed)} package(s) finished, "
            "no failures",
            data=data,
            facts=facts,
        )

    def rollback(self, ctx: Context) -> Result:
        return Result.skip(
            f"{self.name}.rollback",
            f"no automatic rollback for a SWPM {self.direction} — clean up the "
            f"partial {self.direction} directory and re-run per the migration "
            "runbook (SAP Note 2230669)",
            sap_note="2230669",
        )

    # --- helpers --------------------------------------------------------------

    def _read_log(self, ctx: Context, log_path: str) -> str | None:
        """Read the monitor log locally or via the runner (read-only)."""
        if not ctx.is_remote:
            path = Path(log_path)
            if not path.is_file():
                return None
            try:
                return path.read_text(errors="replace")
            except OSError:
                return None
        cr = ctx.runner().run(["cat", log_path], timeout=int(ctx.get("monitor_timeout", 120)))
        return cr.stdout if cr.ok else None
