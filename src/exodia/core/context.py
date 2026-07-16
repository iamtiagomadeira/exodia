"""Execution context — carries connection + parameters into checks/actions.

Stateless by design: a Context is built per-invocation from CLI args and an
optional config file, passed down, and discarded. No persistence, no memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .shell import Runner, SSHRunner


class Context(BaseModel):
    """Everything a check or action needs to run. Immutable-ish per run."""

    model_config = {"arbitrary_types_allowed": True}

    # Target selection
    host: str | None = None  # None => run locally
    user: str | None = None
    port: int = 22
    key_filename: str | None = None
    known_hosts: str | None = None

    # SAP / DB parameters
    db_type: str | None = None  # "hana" | "ase" | ...
    sid: str | None = None
    source: str | None = None
    target: str | None = None
    system_type: str | None = None  # "abap" | "java" | "pipo" | ...

    # Behaviour flags
    dry_run: bool = True  # SAFE DEFAULT: nothing executes unless explicitly disabled
    assume_yes: bool = False
    skip_checks: list[str] = Field(default_factory=list)

    # Free-form overrides (the escape hatch — pre/post hooks, custom params).
    params: dict[str, Any] = Field(default_factory=dict)
    pre_hooks: list[str] = Field(default_factory=list)
    post_hooks: list[str] = Field(default_factory=list)

    def runner(self) -> Runner | SSHRunner:
        """Return the right executor: local Runner or remote SSHRunner."""
        if self.host is None:
            return Runner()
        if not self.user:
            raise ValueError("remote host requires --user")
        return SSHRunner(
            host=self.host,
            user=self.user,
            port=self.port,
            key_filename=self.key_filename,
            known_hosts=self.known_hosts,
        )

    @property
    def is_remote(self) -> bool:
        return self.host is not None

    def get(self, key: str, default: Any = None) -> Any:
        """Read a param from the escape-hatch overrides."""
        return self.params.get(key, default)

    @classmethod
    def from_file(cls, path: str | Path) -> Context:
        """Load a context from a YAML config file (the escape hatch)."""
        import yaml

        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls(**data)
