"""Tenant discovery — list the databases available on a HANA SYSTEMDB.

Before triggering a copy, the operator must pick which target tenant will
receive the data. Rather than trust a typed name, Exodia asks the target
SYSTEMDB what tenants actually exist (read-only, via ``M_DATABASES``) and lets
the operator confirm/select from that live list:

* exactly one candidate  -> assume it, ask for a yes/no confirmation;
* several candidates      -> present them for selection (SID + name + status);
* none                    -> nothing to target (the copy would create a new one).

This turns "hope you typed the right SID" into "the admin confirms, against the
system's own answer, that THIS tenant receives the source data".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import checks

if TYPE_CHECKING:
    from exodia.core.context import Context

from .checks import _common as c


@dataclass(frozen=True)
class TenantInfo:
    """One database as reported by a SYSTEMDB's M_DATABASES."""

    name: str
    active_status: str = ""
    # Whether it is the SYSTEMDB itself (never a tenant-copy target).
    is_system: bool = False

    @property
    def selectable(self) -> bool:
        """SYSTEMDB is never a copy target; only real tenants are selectable."""
        return not self.is_system and self.name.upper() != "SYSTEMDB"

    @property
    def label(self) -> str:
        """Human label for a menu row, e.g. 'QAS (online)'."""
        status = self.active_status or "unknown"
        return f"{self.name} ({status.lower()})"


def list_target_tenants(ctx: Context) -> list[TenantInfo]:
    """Return the tenants present on the TARGET SYSTEMDB (read-only).

    Queries ``SYS_DATABASES.M_DATABASES`` on the target via its hdbuserstore
    key. Returns an empty list when the query fails or nothing is returned so
    the caller can fall back to asking for a name.
    """
    stmt = "SELECT DATABASE_NAME, ACTIVE_STATUS FROM SYS_DATABASES.M_DATABASES"
    cr = c.run(ctx, c.hdbsql_argv(ctx, c.TARGET, stmt))
    if not cr.ok:
        return []
    return _parse_tenants(cr.stdout)


def _parse_tenants(stdout: str) -> list[TenantInfo]:
    tenants: list[TenantInfo] = []
    for row in c.parse_hdbsql_rows(stdout):
        if not row or not row[0]:
            continue
        name = row[0]
        status = row[1] if len(row) > 1 else ""
        tenants.append(
            TenantInfo(
                name=name,
                active_status=status,
                is_system=name.upper() == "SYSTEMDB",
            )
        )
    return tenants


def selectable_tenants(tenants: list[TenantInfo]) -> list[TenantInfo]:
    """Filter to the tenants that can actually be a copy target (not SYSTEMDB)."""
    return [t for t in tenants if t.selectable]


def resolve_target_tenant(
    tenants: list[TenantInfo],
) -> tuple[str, list[TenantInfo]]:
    """Decide how to resolve the target tenant from a discovered list.

    Returns ``(mode, candidates)`` where mode is one of:

    * ``"none"``     — no selectable tenant exists (copy will create a new one);
    * ``"single"``   — exactly one candidate; caller should confirm it;
    * ``"multiple"`` — several candidates; caller should let the operator pick.

    Pure decision logic (no I/O) so it is trivially unit-testable; the CLI layer
    turns the mode into the right prompt.
    """
    candidates = selectable_tenants(tenants)
    if not candidates:
        return "none", []
    if len(candidates) == 1:
        return "single", candidates
    return "multiple", candidates


# ``checks`` is imported to guarantee the tenant-copy check package (and its
# _common helpers) is importable from here without a circular import surprise.
_ = checks
