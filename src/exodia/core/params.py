"""Parameter specifications — the metadata that powers the interactive menu.

A ``ParamSpec`` describes ONE input a check or action needs: its key (the name
read via ``ctx.get(...)`` or a first-class Context field), a human prompt, an
optional default, whether it is required, whether it is a secret (never echoed),
and an optional fixed set of choices.

Checks and Actions expose their inputs by overriding ``parameters()``. The menu
wizard reads these specs to prompt the operator field-by-field — so no one ever
has to hand-craft a giant command line or a YAML file. Modules that declare no
specs still work: the wizard simply offers the common connection fields and the
free-form escape hatch.

This is pure metadata: importing it has no side effects and needs no runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ParamKind(str, Enum):
    """Where a parameter lands on the Context."""

    #: a first-class Context field (host, user, db_type, source, target, ...)
    FIELD = "field"
    #: a free-form entry under Context.params (read via ctx.get(...))
    PARAM = "param"


@dataclass(frozen=True)
class ParamSpec:
    """Describes a single operator-supplied input."""

    key: str
    prompt: str
    default: str | None = None
    required: bool = False
    secret: bool = False
    kind: ParamKind = ParamKind.PARAM
    choices: tuple[str, ...] = field(default_factory=tuple)
    help: str = ""

    def with_default(self, default: str | None) -> ParamSpec:
        """Return a copy with a different default (used to chain env/config)."""
        return ParamSpec(
            key=self.key,
            prompt=self.prompt,
            default=default,
            required=self.required,
            secret=self.secret,
            kind=self.kind,
            choices=self.choices,
            help=self.help,
        )


# --------------------------------------------------------------------------- #
# Shared, reusable specs so modules don't re-declare the common connection set.
# --------------------------------------------------------------------------- #

HOST = ParamSpec(
    "host", "Remote host (blank = run locally)", kind=ParamKind.FIELD,
    help="Hostname/IP of the target; leave blank to run on this machine.",
)
USER = ParamSpec(
    "user", "SSH user for the remote host", kind=ParamKind.FIELD,
    help="Required only when a remote host is given.",
)
DB_TYPE = ParamSpec(
    "db_type", "Database type", default="hana", kind=ParamKind.FIELD,
    choices=("hana", "ase"),
)


def dedupe(specs: list[ParamSpec]) -> list[ParamSpec]:
    """Collapse specs sharing a key (first occurrence wins), preserving order."""
    seen: set[str] = set()
    out: list[ParamSpec] = []
    for s in specs:
        if s.key in seen:
            continue
        seen.add(s.key)
        out.append(s)
    return out
