"""Guarded action: headless SWPM import (target, R3load/JLoad).

``export-import.swpm.import`` orchestrates SWPM/sapinst in unattended mode to run
the import into the freshly installed TARGET (R3load for ABAP, JLoad for AS Java),
then monitors ``import_monitor.log`` for failed/retried packages. Thin orchestrator
— R3load/JLoad do the certified import; Exodia launches sapinst (guarded) and
reads the logs.

It requires the target import-space guard-rail to have passed first.
"""

from __future__ import annotations

from exodia.core.result import Phase

from ._swpm_load import _SwpmLoadAction


class SwpmImportAction(_SwpmLoadAction):
    """Orchestrate a headless SWPM import on the target (R3load/JLoad), guarded."""

    name = "export-import.swpm.import"
    description = "Orchestrate SWPM sapinst unattended for the import into the target (R3load/JLoad)."
    title = "SWPM Import — R3load/JLoad (target)"
    phase = Phase.DOWNTIME
    direction = "import"
    monitor_log_key = "import_monitor_log"
    requires_checks = [
        "export-import.target.import-space",
    ]
