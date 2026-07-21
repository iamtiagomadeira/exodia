"""hdbuserstore key discovery — list the secure-store keys available on a host.

The secure user store (``hdbsql -U <KEY>``) is SAP's password-free way to reach
HANA. Rather than make the operator remember/type a key name, Exodia can run
``hdbuserstore LIST`` and offer the discovered keys as a menu dropdown. The
password never leaves the store — this only reads the key *names* + their
host:port/user metadata, never a secret.

Output of ``hdbuserstore LIST`` looks like::

    DATA FILE       : /home/prdadm/.hdb/.../SSFS_HDB.DAT
    KEY SRCSYS
      ENV : src-host:30013
      USER: SYSTEM
    KEY SRCTEN
      ENV : src-host:30015@PRD
      USER: SAPABAP1

We parse each ``KEY <name>`` block into a small record so the menu can show
``SRCSYS  (src-host:30013, SYSTEM)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from exodia.core.context import Context

_KEY_RE = re.compile(r"^\s*KEY\s+(\S+)\s*$")
_ENV_RE = re.compile(r"^\s*ENV\s*:\s*(.+?)\s*$")
_USER_RE = re.compile(r"^\s*USER\s*:\s*(.+?)\s*$")


@dataclass(frozen=True)
class HdbKey:
    """One hdbuserstore key: its name and (optional) connection metadata."""

    name: str
    env: str = ""
    user: str = ""

    @property
    def label(self) -> str:
        """Human dropdown label, e.g. 'SRCSYS  (src-host:30013, SYSTEM)'."""
        meta = ", ".join(p for p in (self.env, self.user) if p)
        return f"{self.name}  ({meta})" if meta else self.name


def parse_hdbuserstore_list(stdout: str) -> list[HdbKey]:
    """Parse ``hdbuserstore LIST`` output into HdbKey records (name + env + user)."""
    keys: list[HdbKey] = []
    name: str | None = None
    env = ""
    user = ""

    def _flush() -> None:
        nonlocal name, env, user
        if name:
            keys.append(HdbKey(name=name, env=env, user=user))
        name, env, user = None, "", ""

    for line in stdout.splitlines():
        m = _KEY_RE.match(line)
        if m:
            _flush()
            name = m.group(1)
            continue
        if name:
            em = _ENV_RE.match(line)
            if em:
                env = em.group(1)
                continue
            um = _USER_RE.match(line)
            if um:
                user = um.group(1)
    _flush()
    return keys


def discover_hdb_keys(ctx: Context) -> list[HdbKey]:
    """Run ``hdbuserstore LIST`` through the context runner and return the keys.

    Returns an empty list if the command can't run (no hdbuserstore on PATH,
    SSH not reachable) — the caller then falls back to a free-text prompt.
    """
    try:
        cr = ctx.runner().run(["hdbuserstore", "LIST"], timeout=int(ctx.get("os_timeout", 30)))
    except Exception:  # noqa: BLE001 - discovery is best-effort
        return []
    if not cr.ok:
        return []
    return parse_hdbuserstore_list(cr.stdout)
