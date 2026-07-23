"""Guarded action: headless SWPM export (source, R3load/JLoad).

``export-import.swpm.export`` orchestrates SWPM/sapinst in unattended mode to run
the DB-independent export (R3load for ABAP, JLoad for AS Java) on the SOURCE, then
monitors ``export_monitor.log``. It is a THIN ORCHESTRATOR — R3load/JLoad do the
certified export; Exodia only launches sapinst (guarded) and reads the logs.

It requires the preparation + ramp-down guard-rails to have passed first so the
export is only allowed on a consistent, quiesced source.
"""

from __future__ import annotations

from exodia.core.result import Phase

from ._swpm_load import _SwpmLoadAction


class SwpmExportAction(_SwpmLoadAction):
    """Orchestrate a headless SWPM export on the source (R3load/JLoad), guarded."""

    name = "export-import.swpm.export"
    description = "Orchestrate SWPM sapinst unattended for the DB-independent export (R3load/JLoad)."
    title = "SWPM Export — R3load/JLoad (source)"
    phase = Phase.DOWNTIME
    direction = "export"
    monitor_log_key = "export_monitor_log"
    requires_checks = [
        "export-import.source.db-consistency",
        "export-import.migration-key",
        "export-import.unicode-compatibility",
        "export-import.source.quiesced-verify",
    ]
