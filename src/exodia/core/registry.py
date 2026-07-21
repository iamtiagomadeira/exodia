"""Auto-discovery registry — the 'Head' that finds the 'Limbs'.

Walks exodia.modules, imports every submodule, and collects Check/Action
subclasses. Drop a new module in and it appears in `exodia list` with no
central wiring. This is the plugability backbone.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from .base import Action, Check
from .logging import get_logger

if TYPE_CHECKING:
    from .runbook import Runbook

log = get_logger()


class Registry:
    """Discovers and indexes all checks and actions under exodia.modules."""

    def __init__(self) -> None:
        self._checks: dict[str, type[Check]] = {}
        self._actions: dict[str, type[Action]] = {}
        self._runbooks: dict[str, type[Runbook]] = {}
        self._discovered = False

    def discover(self) -> None:
        if self._discovered:
            return
        # Imported lazily to avoid a circular import: runbook.py imports the
        # registry singleton at module load, so the registry must not import
        # Runbook at its own module top.
        import exodia.core.checks as core_checks_pkg
        import exodia.modules as modules_pkg

        from .runbook import Runbook

        packages = [
            (modules_pkg.__path__, "exodia.modules."),
            (core_checks_pkg.__path__, "exodia.core.checks."),
        ]
        for path, prefix in packages:
            for mod in pkgutil.walk_packages(path, prefix=prefix):
                try:
                    importlib.import_module(mod.name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not import %s: %s", mod.name, exc)

        for cls in _all_subclasses(Check):
            name = getattr(cls, "name", "")
            if name:
                self._checks[name] = cls
        for cls in _all_subclasses(Action):
            name = getattr(cls, "name", "")
            if name:
                self._actions[name] = cls
        for cls in _all_subclasses(Runbook):
            name = getattr(cls, "name", "")
            if name:
                self._runbooks[name] = cls
        self._discovered = True

    def checks(self) -> dict[str, type[Check]]:
        self.discover()
        return dict(self._checks)

    def actions(self) -> dict[str, type[Action]]:
        self.discover()
        return dict(self._actions)

    def runbooks(self) -> dict[str, type[Runbook]]:
        self.discover()
        return dict(self._runbooks)

    def get_check(self, name: str) -> type[Check] | None:
        self.discover()
        return self._checks.get(name)

    def get_action(self, name: str) -> type[Action] | None:
        self.discover()
        return self._actions.get(name)

    def get_runbook(self, name: str) -> type[Runbook] | None:
        self.discover()
        return self._runbooks.get(name)


def _all_subclasses(cls: type) -> set[type]:
    subs = set(cls.__subclasses__())
    for sub in list(subs):
        subs |= _all_subclasses(sub)
    # Exclude abstract intermediates without a name.
    return {s for s in subs if not getattr(s, "__abstractmethods__", None)}


# Module-level singleton.
registry = Registry()
