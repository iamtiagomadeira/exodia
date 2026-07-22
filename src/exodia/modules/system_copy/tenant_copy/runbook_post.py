"""Post-copy validation runbook for HANA tenant copy (Post-Activities phase).

Groups the read-only checks that prove the copied tenant is healthy on the
target after the reconnect: system/secure_communication, source-vs-target data
consistency (top tables), and the HANA technical consistency checks
(CHECK_TABLE_CONSISTENCY + CHECK_CATALOG per SAP Note 1785060 / 1977584).

Run this on the target once the replica is finalized and the SAP system is
reconnected. Read-only throughout — safe to re-run.
"""

from __future__ import annotations

from exodia.core.runbook import Runbook


class TenantCopyPostValidationRunbook(Runbook):
    """Read-only post-copy validation sweep for the target tenant."""

    name = "tenant-copy.hana.post-validation"
    description = (
        "Read-only post-copy validation on the target: secure_communication, "
        "data consistency (top tables), and HANA table + catalog consistency "
        "(CHECK_TABLE_CONSISTENCY / CHECK_CATALOG)."
    )
    stop_on_blocking = False
    steps = [
        "tenant-copy.hana.secure-communication",
        "tenant-copy.hana.data-consistency",
        "tenant-copy.hana.target-table-consistency",
        "tenant-copy.hana.target-catalog-consistency",
    ]
