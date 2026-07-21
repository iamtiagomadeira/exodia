"""Target OS-validation runbook (SAP MIG — target host facts).

Groups the OS-level target checks — kernel release, OS release, CPU cores/type
and timezone — into one read-only sweep to run against the target host (over
SSH). Mirrors the "TARGET - Preparation / OS Information" section of the COP.
"""

from __future__ import annotations

from exodia.core.runbook import Runbook


class TargetOsValidationRunbook(Runbook):
    """Target OS facts: kernel, OS release, CPU, timezone (run on the target host)."""

    name = "abap.target-os-validation"
    description = (
        "Target OS validation: kernel release, OS release, CPU cores/type and "
        "timezone (run against the target host over SSH)."
    )
    stop_on_blocking = False
    steps = [
        "abap.readiness.target-kernel-release",
        "abap.readiness.target-os-release",
        "abap.readiness.target-cpu-info",
        "abap.readiness.target-timezone",
    ]
