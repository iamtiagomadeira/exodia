"""System copy based on HANA System Replication (HSR).

The HSR method sets up the target as a replication secondary of the source, lets
it catch up, then takes it over as an independent system. Grounded in SAP HSR
requirements:

* **Same major HANA version** — the secondary must run the same or a compatible
  (equal/higher within the allowed window) revision as the primary; SAP does not
  support replicating to a lower revision.
* **Replication ports reachable** — the primary opens ports 4<nn>01-4<nn>07
  (nn = instance) to the secondary; the network path must be open.
* **log_mode = normal** — system replication requires the primary to run in
  ``log_mode=normal`` (not overwrite) so logs can be shipped.
* **Distinct SIDs / hosts** — primary and secondary must be different hosts
  (and normally share the SID for a homogeneous copy).

Every check is read-only.
"""

from __future__ import annotations

import re

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:\.\d+)*)")

# --------------------------------------------------------------------------- #
# Parameter specs
# --------------------------------------------------------------------------- #

PRIMARY_KEY = ParamSpec(
    "primary_userstore_key",
    "Primary SYSTEMDB hdbuserstore key",
    default="SYSTEMDB",
    help="hdbsql -U key for the PRIMARY (source) SYSTEMDB.",
)
SECONDARY_KEY = ParamSpec(
    "secondary_userstore_key",
    "Secondary SYSTEMDB hdbuserstore key",
    default="SYSTEMDB",
    help="hdbsql -U key for the SECONDARY (target) SYSTEMDB.",
)
SECONDARY_HOST = ParamSpec(
    "secondary_host",
    "Secondary host",
    help="Target host that becomes the replication secondary.",
)
INSTANCE = ParamSpec(
    "instance",
    "HANA instance number",
    default="00",
    help="Two digits; replication ports 4<nn>01-07 are derived from it.",
)


def _run(ctx: Context, argv: list[str], timeout: int = 60):  # type: ignore[no-untyped-def]
    return ctx.runner().run(argv, timeout=timeout)


def _hdbsql(key: str, stmt: str) -> list[str]:
    return ["hdbsql", "-U", str(key), "-x", "-a", "-j", stmt]


def _parse_version(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    m = _VERSION_RE.search(text)
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


class VersionCompatibilityCheck(Check):
    """Primary and secondary must run compatible HANA revisions.

    SAP requires the secondary revision >= primary revision (never lower).
    """

    name = "hsr.version-compatibility"
    description = "Secondary HANA revision is compatible with the primary."
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [PRIMARY_KEY, SECONDARY_KEY]

    def _version(self, ctx: Context, key: str) -> tuple[int, ...] | None:
        cr = _run(ctx, _hdbsql(key, "SELECT VERSION FROM M_DATABASE"))
        return _parse_version(cr.stdout) if cr.ok else None

    def run(self, ctx: Context) -> Result:
        pkey = ctx.get("primary_userstore_key") or "SYSTEMDB"
        skey = ctx.get("secondary_userstore_key") or "SYSTEMDB"
        pv = self._version(ctx, pkey)
        sv = self._version(ctx, skey)
        if pv is None or sv is None:
            return Result.skip(
                self.name,
                "could not read version from one/both systems (keys reachable?)",
                data={"primary": pv, "secondary": sv},
            )
        if sv < pv:
            return Result.fail(
                self.name,
                f"secondary {sv} is LOWER than primary {pv} — HSR does not support "
                "replicating to a lower revision; upgrade the secondary first",
                data={"primary": list(pv), "secondary": list(sv)},
            )
        return Result.ok(
            self.name,
            f"secondary {sv} is compatible with primary {pv}",
            data={"primary": list(pv), "secondary": list(sv)},
        )


class LogModeNormalCheck(Check):
    """The primary must run in log_mode=normal for replication to ship logs."""

    name = "hsr.log-mode-normal"
    description = "Primary runs in log_mode=normal (required for HSR)."
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [PRIMARY_KEY]

    def run(self, ctx: Context) -> Result:
        key = ctx.get("primary_userstore_key") or "SYSTEMDB"
        stmt = (
            "SELECT VALUE FROM M_INIFILE_CONTENTS WHERE FILE_NAME='global.ini' "
            "AND KEY='log_mode'"
        )
        cr = _run(ctx, _hdbsql(key, stmt))
        if not cr.ok:
            return Result.skip(
                self.name,
                "could not read log_mode from primary global.ini",
                detail=cr.stderr or cr.stdout,
            )
        value = cr.stdout.strip().strip('"').lower()
        if "normal" not in value:
            return Result.fail(
                self.name,
                f"primary log_mode is '{value or 'unknown'}' — set log_mode=normal "
                "and take a full data backup before enabling replication",
                data={"log_mode": value},
            )
        return Result.ok(self.name, "primary log_mode=normal", data={"log_mode": value})


class ReplicationPortsReachableCheck(Check):
    """The secondary must reach the primary's system-replication ports."""

    name = "hsr.replication-ports-reachable"
    description = "Secondary can reach the primary replication ports."
    blocking = False

    def parameters(self) -> list[ParamSpec]:
        return [SECONDARY_HOST, INSTANCE]

    def run(self, ctx: Context) -> Result:
        host = ctx.get("secondary_host") or ctx.host
        inst = str(ctx.get("instance") or "00").zfill(2)
        if not host:
            return Result.skip(
                self.name, "no secondary_host/host given; cannot probe ports"
            )
        # HSR uses 4<nn>01..4<nn>07; probe the first as a representative.
        port = int(f"4{inst}01")
        cr = _run(ctx, ["nc", "-z", "-w", "5", str(host), str(port)])
        if not cr.ok:
            return Result.fail(
                self.name,
                f"cannot reach {host}:{port} — open replication ports 4{inst}01-07 "
                "between primary and secondary",
                data={"host": host, "port": port},
            )
        return Result.ok(
            self.name,
            f"replication port {host}:{port} reachable",
            data={"host": host, "port": port},
        )


class DistinctHostsCheck(Check):
    """Primary and secondary must be different hosts."""

    name = "hsr.distinct-hosts"
    description = "Primary and secondary are different hosts."
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [SECONDARY_HOST]

    def run(self, ctx: Context) -> Result:
        secondary = ctx.get("secondary_host")
        primary = ctx.host
        if not secondary:
            return Result.skip(self.name, "secondary_host not provided")
        if primary and secondary and primary.strip().lower() == secondary.strip().lower():
            return Result.fail(
                self.name,
                f"primary and secondary are the same host ({primary}) — HSR requires "
                "two distinct hosts",
                data={"primary": primary, "secondary": secondary},
            )
        return Result.ok(
            self.name,
            f"primary ({primary or '?'}) and secondary ({secondary}) are distinct",
            data={"primary": primary, "secondary": secondary},
        )
