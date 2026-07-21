"""OS-level system checks for SAP MIG (kernel, OS, CPU, timezone).

These read facts from the operating system (not RFC) via the context runner —
SSH when a remote host is set, local otherwise. They capture what a Basis
engineer collects from `disp+work -version`, `cat /etc/os-release`, `lscpu` and
`timedatectl` on the source/target during a migration.

Each is a per-side variant (source / target) so it runs where it has access in
an air-gapped engagement. All read-only. The timezone check is target-only (the
COP only cares that the target timezone is correct).
"""

from __future__ import annotations

import re

from exodia.core import Check, Context, Result
from exodia.core.params import ParamKind, ParamSpec
from exodia.core.result import Phase

_HOST = ParamSpec(
    "host", "Remote host (blank = local)", kind=ParamKind.FIELD,
    help="Host to read OS facts from over SSH; blank reads locally.",
)
_USER = ParamSpec(
    "user", "SSH user", kind=ParamKind.FIELD,
    help="SSH user (typically <sid>adm) for the remote host.",
)


class _OsCheck(Check):
    """Base: an OS-level check tagged for a side (source/target), via the runner."""

    side = "source"
    phase = Phase.PREPARATION

    def parameters(self) -> list[ParamSpec]:
        return [_HOST, _USER]


# --------------------------------------------------------------------------- #
# Kernel release (disp+work -version)
# --------------------------------------------------------------------------- #


class _KernelReleaseCheck(_OsCheck):
    def run(self, ctx: Context) -> Result:
        cr = ctx.runner().run(["disp+work", "-version"], timeout=int(ctx.get("os_timeout", 60)))
        if not cr.ok:
            return Result.fail(
                self.name,
                f"could not read {self.side} kernel version (disp+work -version)",
                detail=cr.stderr or cr.stdout,
                facts={"Side": self.side.capitalize(), "Readable": "No"},
            )
        text = cr.stdout
        rel = _grep1(text, r"kernel release\s+(\S+)")
        patch = _grep1(text, r"(?:patch number|sup pkg lvl|patchlevel)\s+(\S+)")
        unicode_flag = "Unicode" if "unicode" in text.lower() else "n/a"
        return Result.ok(
            self.name,
            f"{self.side} kernel release {rel or '?'} (patch {patch or '?'})",
            data={"side": self.side, "kernel_release": rel, "patch": patch},
            facts={
                "Side": self.side.capitalize(),
                "Kernel Release": rel or "?",
                "Patch Level": patch or "?",
                "Unicode": unicode_flag,
            },
        )


class SourceKernelReleaseCheck(_KernelReleaseCheck):
    name = "abap.readiness.source-kernel-release"
    description = "Source kernel release + patch level (disp+work -version)."
    title = "Source Kernel Release (disp+work -version)"
    side = "source"


class TargetKernelReleaseCheck(_KernelReleaseCheck):
    name = "abap.readiness.target-kernel-release"
    description = "Target kernel release + patch level (disp+work -version)."
    title = "Target Kernel Release (disp+work -version)"
    side = "target"


# --------------------------------------------------------------------------- #
# OS release (cat /etc/os-release)
# --------------------------------------------------------------------------- #


class _OsReleaseCheck(_OsCheck):
    def run(self, ctx: Context) -> Result:
        cr = ctx.runner().run(["cat", "/etc/os-release"], timeout=int(ctx.get("os_timeout", 60)))
        if not cr.ok:
            return Result.fail(
                self.name,
                f"could not read {self.side} OS release (/etc/os-release)",
                detail=cr.stderr or cr.stdout,
                facts={"Side": self.side.capitalize(), "Readable": "No"},
            )
        pretty = _grep1(cr.stdout, r'PRETTY_NAME="?([^"\n]+)"?')
        version = _grep1(cr.stdout, r'VERSION_ID="?([^"\n]+)"?')
        return Result.ok(
            self.name,
            f"{self.side} OS: {pretty or '?'}",
            data={"side": self.side, "os": pretty, "version_id": version},
            facts={
                "Side": self.side.capitalize(),
                "OS": pretty or "?",
                "Version": version or "?",
            },
        )


class SourceOsReleaseCheck(_OsReleaseCheck):
    name = "abap.readiness.source-os-release"
    description = "Source OS release (/etc/os-release)."
    title = "Source OS Release (/etc/os-release)"
    side = "source"


class TargetOsReleaseCheck(_OsReleaseCheck):
    name = "abap.readiness.target-os-release"
    description = "Target OS release (/etc/os-release)."
    title = "Target OS Release (/etc/os-release)"
    side = "target"


# --------------------------------------------------------------------------- #
# CPU info (lscpu)
# --------------------------------------------------------------------------- #


class _CpuInfoCheck(_OsCheck):
    def run(self, ctx: Context) -> Result:
        cr = ctx.runner().run(["lscpu"], timeout=int(ctx.get("os_timeout", 60)))
        if not cr.ok:
            return Result.fail(
                self.name,
                f"could not read {self.side} CPU info (lscpu)",
                detail=cr.stderr or cr.stdout,
                facts={"Side": self.side.capitalize(), "Readable": "No"},
            )
        cpus = _grep1(cr.stdout, r"^CPU\(s\):\s+(\d+)")
        model = _grep1(cr.stdout, r"Model name:\s+(.+)")
        sockets = _grep1(cr.stdout, r"Socket\(s\):\s+(\d+)")
        cores_per = _grep1(cr.stdout, r"Core\(s\) per socket:\s+(\d+)")
        return Result.ok(
            self.name,
            f"{self.side} CPU: {cpus or '?'} vCPU(s), {model or 'unknown model'}",
            data={
                "side": self.side, "cpus": cpus, "model": model,
                "sockets": sockets, "cores_per_socket": cores_per,
            },
            facts={
                "Side": self.side.capitalize(),
                "CPU(s)": cpus or "?",
                "Model": (model or "?")[:60],
                "Sockets": sockets or "?",
                "Cores/Socket": cores_per or "?",
            },
        )


class SourceCpuInfoCheck(_CpuInfoCheck):
    name = "abap.readiness.source-cpu-info"
    description = "Source CPU cores + model (lscpu)."
    title = "Source CPU Cores & Type (lscpu)"
    side = "source"


class TargetCpuInfoCheck(_CpuInfoCheck):
    name = "abap.readiness.target-cpu-info"
    description = "Target CPU cores + model (lscpu)."
    title = "Target CPU Cores & Type (lscpu)"
    side = "target"


# --------------------------------------------------------------------------- #
# Timezone — target only
# --------------------------------------------------------------------------- #


class TargetTimezoneCheck(_OsCheck):
    """Capture the target OS timezone (timedatectl) — target-only per the COP."""

    name = "abap.readiness.target-timezone"
    description = "Target OS timezone (timedatectl)."
    title = "Target OS Timezone (timedatectl)"
    side = "target"

    def run(self, ctx: Context) -> Result:
        cr = ctx.runner().run(["timedatectl"], timeout=int(ctx.get("os_timeout", 60)))
        if not cr.ok:
            # Fall back to reading /etc/timezone if timedatectl is absent.
            cr = ctx.runner().run(["cat", "/etc/timezone"], timeout=int(ctx.get("os_timeout", 60)))
            if not cr.ok:
                return Result.fail(
                    self.name,
                    "could not read target timezone (timedatectl / /etc/timezone)",
                    detail=cr.stderr or cr.stdout,
                    facts={"Readable": "No"},
                )
        tz = _grep1(cr.stdout, r"Time zone:\s+(\S+)") or cr.stdout.strip().splitlines()[0].strip()
        expected = ctx.get("expected_timezone")
        data = {"timezone": tz, "expected": expected}
        if expected and tz and tz != str(expected):
            return Result.warn(
                self.name,
                f"target timezone is {tz}, expected {expected}",
                data=data,
                facts={"Timezone": tz or "?", "Expected": str(expected)},
            )
        return Result.ok(
            self.name,
            f"target timezone: {tz or '?'}",
            data=data,
            facts={"Timezone": tz or "?"},
        )


def _grep1(text: str, pattern: str) -> str | None:
    """First capture group of the first matching line, or None."""
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None
