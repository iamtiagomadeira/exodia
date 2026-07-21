"""Logged-on users readiness (SAP MIG ramp-down — SM04 / AL08).

Before a takeover, no interactive users should still be logged on to the source
(only the migration/service user). A user session still open at cutover means
someone can post data into a system that is about to be frozen and copied.

Reads the live user list via TH_USER_LIST. Blocking: any logged-on user other
than the allow-listed migration users FAILs. The allow-list defaults to the
common technical users and can be extended via the ``allowed_users`` param.
"""

from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec

from . import _rfc

_DEFAULT_ALLOWED = {"DDIC", "SAP*", "TMSADM", "SAPJSF", "SOLMAN"}


class ActiveUsersCheck(Check):
    """No interactive users logged on to the source at takeover (SM04 / TH_USER_LIST)."""

    name = "abap.readiness.active-users"
    description = "No interactive users logged on at takeover (SM04 / TH_USER_LIST)."
    blocking = True

    def parameters(self) -> list[ParamSpec]:
        return [
            *_rfc.SOURCE_CONN_SPECS,
            ParamSpec(
                "allowed_users",
                "Comma-separated users allowed to stay logged on",
                help="Technical/migration users to ignore (default: DDIC, SAP*, TMSADM, ...).",
            ),
        ]

    def run(self, ctx: Context) -> Result:
        side = _rfc.SOURCE
        if not _rfc.has_connection_params(ctx, side):
            return Result.skip(
                self.name, "no source RFC connection params (set source_ashost + credentials)"
            )
        allowed = set(_DEFAULT_ALLOWED)
        extra = ctx.get("allowed_users")
        if extra:
            allowed |= {u.strip().upper() for u in str(extra).split(",") if u.strip()}
        try:
            client = _rfc.get_client(ctx, side)
            res = client.call("TH_USER_LIST")
        except _rfc.RfcError as exc:
            return Result.fail(self.name, f"could not read logged-on users: {exc}")

        sessions = res.get("USRLIST", []) or []
        users = sorted({s.get("BNAME", "").upper() for s in sessions if s.get("BNAME")})
        unexpected = [u for u in users if u not in allowed]
        data = {
            "logged_on": users,
            "session_count": len(sessions),
            "unexpected": unexpected,
        }
        if unexpected:
            return Result.fail(
                self.name,
                f"{len(unexpected)} unexpected user(s) still logged on: {', '.join(unexpected)}",
                data=data,
            )
        return Result.ok(
            self.name,
            f"no unexpected users logged on ({len(users)} technical session(s))",
            data=data,
        )
