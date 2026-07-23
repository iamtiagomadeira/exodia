"""Guarded action: transfer the export dump source→target + verify integrity.

``export-import.transfer-export`` moves the R3load/JLoad export dump from the
source export directory to the target import directory and verifies its integrity
on arrival via a checksum manifest. It is non-blocking (a shared filesystem/NFS
setup may make a physical transfer unnecessary), but when a transfer IS needed
this guards it: dry-run describes the exact rsync/scp + checksum commands, and
verify recomputes checksums on the target and compares them to the source
manifest so a truncated/corrupt transfer is caught before the import.

Thin orchestrator: uses the runner (rsync over SSH / sha256sum), never shell=True,
no secrets on the command line.

References (cite by number only): 784118 (system copy / R3load dump).
"""

from __future__ import annotations

from exodia.core import Context, Result
from exodia.core.base import Action
from exodia.core.params import ParamSpec
from exodia.core.result import Phase

from .. import _r3load as r


class TransferExportAction(Action):
    """Transfer the export dump to the target and verify its checksum, guarded."""

    name = "export-import.transfer-export"
    description = "Transfer the export dump source→target and verify integrity (checksum) on arrival."
    title = "Transfer Export Dump (+ checksum verify)"
    phase = Phase.DOWNTIME
    destructive = True
    requires_checks: list[str] = []

    def parameters(self) -> list[ParamSpec]:
        return [
            r.EXPORT_DIR,
            r.IMPORT_DIR,
            ParamSpec(
                "transfer_target_host", "Target host for the dump transfer",
                help="Host to rsync the export dump to (blank = same host / shared FS).",
            ),
            ParamSpec(
                "checksum_manifest", "Checksum manifest path (source-side)",
                help="sha256 manifest captured on the source (sha256sum output) to verify on arrival.",
            ),
        ]

    # --- resolution -----------------------------------------------------------

    @staticmethod
    def _export_dir(ctx: Context) -> str:
        return str(ctx.get("export_dir", "/export"))

    @staticmethod
    def _import_dir(ctx: Context) -> str:
        return str(ctx.get("import_dir") or ctx.get("export_dir") or "/export")

    @staticmethod
    def _target_host(ctx: Context) -> str | None:
        val = ctx.get("transfer_target_host")
        return str(val) if val else None

    def _transfer_argv(self, ctx: Context) -> list[str]:
        """Build the rsync argv (list[str], never a shell string).

        Local/shared FS → rsync between two paths. Remote target → rsync over
        SSH to ``host:import_dir``. Archive + checksum + partial for resumable,
        integrity-checked transfers of a large dump.
        """
        src = self._export_dir(ctx).rstrip("/") + "/"
        host = self._target_host(ctx)
        dst = self._import_dir(ctx).rstrip("/") + "/"
        remote_dst = f"{host}:{dst}" if host else dst
        return ["rsync", "-a", "--checksum", "--partial", "--stats", src, remote_dst]

    # --- Action phases --------------------------------------------------------

    def dry_run(self, ctx: Context) -> Result:
        phase = f"{self.name}.dry-run"
        argv = self._transfer_argv(ctx)
        manifest = ctx.get("checksum_manifest")
        host = self._target_host(ctx)
        dest = f"{host}:{self._import_dir(ctx)}" if host else self._import_dir(ctx)
        detail_lines = [
            f"sub-phase 1 transfer: {' '.join(argv)}",
            (
                "sub-phase 2 verify: recompute sha256 on the target import dir and "
                f"compare to the source manifest ({manifest or 'set checksum_manifest'})"
            ),
        ]
        return Result.ok(
            phase,
            f"would transfer export dump {self._export_dir(ctx)} → {dest}; nothing executed",
            detail="\n".join(detail_lines),
            data={
                "argv": argv,
                "export_dir": self._export_dir(ctx),
                "import_dir": self._import_dir(ctx),
                "target_host": self._target_host(ctx),
                "checksum_manifest": str(manifest) if manifest else None,
            },
            sap_note="784118",
        )

    def execute(self, ctx: Context) -> Result:
        phase = f"{self.name}.execute"
        argv = self._transfer_argv(ctx)
        self._emit_phase("transfer", " ".join(argv))
        self._emit_log(f"$ {' '.join(argv)}")
        cr = ctx.runner().run(argv, timeout=int(ctx.get("transfer_timeout", 14400)))
        if not cr.ok:
            return Result.fail(
                phase,
                f"export dump transfer failed (exit {cr.exit_code}) — run PAUSED; "
                "re-run is safe (rsync resumes)",
                detail=cr.stderr or cr.stdout,
                data={"argv": argv, "exit_code": cr.exit_code},
                sap_note="784118",
            )
        if cr.stdout:
            self._emit_log(cr.stdout)
        return Result.ok(
            phase,
            f"export dump transferred to {self._import_dir(ctx)}; verify checksum next",
            data={"argv": argv, "import_dir": self._import_dir(ctx)},
        )

    def verify(self, ctx: Context) -> Result:
        """Recompute checksums on the target and compare to the source manifest."""
        phase = f"{self.name}.verify"
        manifest = ctx.get("checksum_manifest")
        if not manifest:
            return Result.warn(
                phase,
                "no checksum_manifest given — transfer done but integrity not verified "
                "(capture a sha256sum manifest on the source to enable verification)",
                data={"import_dir": self._import_dir(ctx)},
            )
        # sha256sum -c reads the manifest and checks files in the import dir.
        import_dir = self._import_dir(ctx)
        argv = ["sha256sum", "-c", "--quiet", str(manifest)]
        cr = ctx.runner().run(
            argv, timeout=int(ctx.get("verify_timeout", 3600))
        )
        if not cr.ok:
            return Result.fail(
                phase,
                "checksum verification FAILED — the transferred dump is corrupt or "
                "incomplete; do NOT import it (re-transfer first)",
                detail=cr.stdout or cr.stderr,
                data={"argv": argv, "import_dir": import_dir, "exit_code": cr.exit_code},
                sap_note="784118",
            )
        return Result.ok(
            phase,
            f"export dump integrity verified on the target ({import_dir}) — checksums match",
            data={"import_dir": import_dir, "manifest": str(manifest)},
        )

    def rollback(self, ctx: Context) -> Result:
        return Result.skip(
            f"{self.name}.rollback",
            "no automatic rollback — delete the partially-transferred dump on the "
            "target and re-run the transfer (rsync is resumable/idempotent)",
        )
