"""Shared plumbing for the ABAP readiness checks — the RFC counterpart of the
tenant-copy ``_common`` helpers.

Design goals (mirroring the HANA side):

* **Read-only.** Only ever calls read function modules (RFC_READ_TABLE,
  RFC_SYSTEM_INFO, TH_SERVER_LIST, ...). Nothing here mutates a system.
* **Testable without a real SAP.** All RFC traffic goes through a small
  ``RfcClient`` protocol. Production uses ``PyRfcClient`` (lazy-imports pyrfc so
  the dependency is optional and the module still imports on a box without the
  SAP NW RFC SDK). Tests inject a fake client via a Context subclass, exactly
  like ``FakeRunner`` on the hdbsql side.
* **Source vs target.** A cutover compares the on-prem source against the ECS
  target. Connection params are namespaced per side (``source_*`` / ``target_*``)
  with a bare fallback, so one Context can address either system.

The SAP NW RFC SDK (and pyrfc) require an S-user download and a service user
with S_RFC authorisations on the target client. That is an operational
prerequisite, documented in the module help — not something this code can or
should bundle.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from exodia.core.params import ParamSpec

if TYPE_CHECKING:
    from exodia.core.context import Context

# Which side of a migration a check addresses.
SOURCE = "source"
TARGET = "target"


# --------------------------------------------------------------------------- #
# RFC client abstraction (production = pyrfc; tests = fake)
# --------------------------------------------------------------------------- #


@runtime_checkable
class RfcClient(Protocol):
    """Minimal read-only RFC surface the checks depend on.

    Just enough to call a function module and read a table. Kept tiny so a fake
    is trivial and so we never accidentally reach for a write-capable call.
    """

    def call(self, function_module: str, **kwargs: Any) -> dict[str, Any]:
        """Invoke an RFC-enabled function module, returning its export/table params."""
        ...

    def close(self) -> None:  # pragma: no cover - trivial
        """Release the connection."""
        ...


class RfcError(RuntimeError):
    """Raised when an RFC connection or call fails, with a readable message."""


class PyRfcClient:
    """Production ``RfcClient`` backed by pyrfc.

    pyrfc is imported lazily inside ``__init__`` so that:
      * the module imports fine on a machine without the SAP NW RFC SDK, and
      * ``exodia list`` / discovery never fails just because the SDK is absent.
    A check that actually needs a live connection surfaces a clean, actionable
    error (install the SDK / provide credentials) instead of an import error.
    """

    def __init__(self, conn_params: dict[str, Any]) -> None:
        try:
            from pyrfc import Connection  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001 - any import/SDK failure
            raise RfcError(
                "pyrfc / SAP NW RFC SDK not available — install the SDK and "
                "`pip install pyrfc` on the host that runs the ABAP checks"
            ) from exc
        try:
            self._conn = Connection(**conn_params)
        except Exception as exc:  # noqa: BLE001 - connect/auth failure
            raise RfcError(f"RFC connect failed: {exc}") from exc

    def call(self, function_module: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return dict(self._conn.call(function_module, **kwargs))
        except Exception as exc:  # noqa: BLE001 - RFC call failure
            raise RfcError(f"RFC call {function_module} failed: {exc}") from exc

    def close(self) -> None:  # pragma: no cover - thin wrapper
        with contextlib.suppress(Exception):
            self._conn.close()


# --------------------------------------------------------------------------- #
# Connection params + client factory
# --------------------------------------------------------------------------- #


def side_key(side: str, name: str) -> str:
    """Build the param name for a given side, e.g. ('source','client')."""
    return f"{side}_{name}"


def _side_param(ctx: Context, side: str, name: str, default: Any = None) -> Any:
    """Read a side-namespaced param, falling back to the bare name then default."""
    val = ctx.get(side_key(side, name))
    if val is None:
        val = ctx.get(name)
    return default if val is None else val


def conn_params(ctx: Context, side: str) -> dict[str, Any]:
    """Assemble pyrfc connection kwargs for one side from Context params.

    Supports the two common shapes:
      * direct application server: ashost + sysnr
      * load-balanced / message server: mshost + msserv + group (+ r3name)
    Credentials come from params; passwords are marked secret in the ParamSpecs
    so the wizard never echoes them, and are never logged here.
    """
    p: dict[str, Any] = {}
    ashost = _side_param(ctx, side, "ashost")
    if ashost:
        p["ashost"] = ashost
        p["sysnr"] = str(_side_param(ctx, side, "sysnr", "00"))
    else:
        mshost = _side_param(ctx, side, "mshost")
        if mshost:
            p["mshost"] = mshost
            p["msserv"] = str(_side_param(ctx, side, "msserv", "3600"))
            p["group"] = _side_param(ctx, side, "group", "PUBLIC")
            r3name = _side_param(ctx, side, "r3name")
            if r3name:
                p["r3name"] = r3name
    p["client"] = str(_side_param(ctx, side, "client", "000"))
    user = _side_param(ctx, side, "rfc_user")
    if user:
        p["user"] = user
    passwd = _side_param(ctx, side, "rfc_password")
    if passwd:
        p["passwd"] = passwd
    lang = _side_param(ctx, side, "lang", "EN")
    if lang:
        p["lang"] = lang
    return p


def get_client(ctx: Context, side: str) -> RfcClient:
    """Return an RfcClient for the given side.

    A test injects a fake by overriding ``Context.rfc_client``; production falls
    back to a real ``PyRfcClient`` built from the side's connection params.
    """
    factory = getattr(ctx, "rfc_client", None)
    if callable(factory):
        return factory(side)  # type: ignore[no-any-return]
    return PyRfcClient(conn_params(ctx, side))


def has_connection_params(ctx: Context, side: str) -> bool:
    """True when enough params exist to attempt a connection to ``side``."""
    p = conn_params(ctx, side)
    return bool(p.get("ashost") or p.get("mshost"))


# --------------------------------------------------------------------------- #
# RFC_READ_TABLE helper — the workhorse for reading ABAP tables read-only
# --------------------------------------------------------------------------- #


def read_table(
    client: RfcClient,
    table: str,
    *,
    fields: list[str] | None = None,
    where: str | None = None,
    rowcount: int = 0,
) -> list[dict[str, str]]:
    """Read an ABAP table via RFC_READ_TABLE, returning a list of field dicts.

    RFC_READ_TABLE returns fixed-width DATA rows plus a FIELDS descriptor giving
    each field's OFFSET/LENGTH; we slice every row accordingly so callers get
    clean ``{field: value}`` dicts instead of raw fixed-width strings.
    """
    options = [{"TEXT": where}] if where else []
    field_spec = [{"FIELDNAME": f} for f in (fields or [])]
    res = client.call(
        "RFC_READ_TABLE",
        QUERY_TABLE=table,
        DELIMITER="",
        OPTIONS=options,
        FIELDS=field_spec,
        ROWCOUNT=rowcount,
    )
    field_defs = res.get("FIELDS", [])
    rows_out: list[dict[str, str]] = []
    for row in res.get("DATA", []):
        raw = row.get("WA", "")
        record: dict[str, str] = {}
        for fd in field_defs:
            name = fd.get("FIELDNAME", "")
            offset = int(fd.get("OFFSET", 0))
            length = int(fd.get("LENGTH", 0))
            record[name] = raw[offset : offset + length].strip()
        rows_out.append(record)
    return rows_out


# --------------------------------------------------------------------------- #
# Parameter specs — declared by checks so the interactive menu can prompt.
# --------------------------------------------------------------------------- #

SOURCE_ASHOST = ParamSpec(
    "source_ashost", "Source application server host",
    help="Hostname of the on-prem source ABAP AS (ashost). Blank for load balancing.",
)
SOURCE_SYSNR = ParamSpec(
    "source_sysnr", "Source instance number", default="00",
    help="Two-digit system number of the source AS.",
)
SOURCE_CLIENT = ParamSpec(
    "source_client", "Source client", default="000",
    help="ABAP client to log on to on the source (e.g. 000).",
)
TARGET_ASHOST = ParamSpec(
    "target_ashost", "Target application server host",
    help="Hostname of the ECS/HEC target ABAP AS (ashost).",
)
TARGET_SYSNR = ParamSpec(
    "target_sysnr", "Target instance number", default="00",
    help="Two-digit system number of the target AS.",
)
TARGET_CLIENT = ParamSpec(
    "target_client", "Target client", default="000",
    help="ABAP client to log on to on the target (e.g. 000).",
)
RFC_USER = ParamSpec(
    "rfc_user", "RFC service user",
    help="Service user with S_RFC authorisations for the readiness function modules.",
)
RFC_PASSWORD = ParamSpec(
    "rfc_password", "RFC service user password", secret=True,
    help="Password for the RFC service user (never echoed or logged).",
)

#: Connection set for a single-system readiness check (defaults to the source).
SOURCE_CONN_SPECS: list[ParamSpec] = [
    SOURCE_ASHOST,
    SOURCE_SYSNR,
    SOURCE_CLIENT,
    RFC_USER,
    RFC_PASSWORD,
]

#: Full set for a source-vs-target comparison check.
COMPARE_CONN_SPECS: list[ParamSpec] = [
    SOURCE_ASHOST,
    SOURCE_SYSNR,
    SOURCE_CLIENT,
    TARGET_ASHOST,
    TARGET_SYSNR,
    TARGET_CLIENT,
    RFC_USER,
    RFC_PASSWORD,
]
