"""Execution context — carries connection + parameters into checks/actions.

Stateless by design: a Context is built per-invocation from CLI args and an
optional config file, passed down, and discarded. No persistence, no memory.

The model doubles as the **config-file schema**: ``Context.from_file()`` loads a
YAML file, validates it against these fields, and rejects unknown keys so typos
surface immediately instead of being silently ignored. See ``exodia.config.yaml``
in the repo root for a fully commented example.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .shell import Runner, SSHRunner

DbType = Literal["hana", "ase", "oracle", "db2", "maxdb", "sybase"]
SystemType = Literal["abap", "java", "dual", "pipo", "solman"]


class ConfigError(ValueError):
    """Raised when a config file fails schema validation, with a readable message."""


class Context(BaseModel):
    """Everything a check or action needs to run. Immutable-ish per run.

    This is also the config-file schema: every field below can be set in a YAML
    file passed via ``--config``. Unknown keys are rejected.
    """

    # Reject unknown keys so a typo in a config file is a hard error, not a
    # silently-ignored setting.
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # Target selection
    host: str | None = Field(None, description="Remote host; omit to run locally.")
    user: str | None = Field(None, description="SSH user (required when host is set).")
    port: int = Field(22, ge=1, le=65535, description="SSH port.")
    key_filename: str | None = Field(None, description="Path to the SSH private key.")
    known_hosts: str | None = Field(None, description="Path to a known_hosts file.")

    # SAP / DB parameters
    db_type: DbType | None = Field(None, description="Database platform.")
    sid: str | None = Field(None, description="SAP System ID, e.g. PRD.")
    source: str | None = Field(None, description="Source system/host identifier.")
    target: str | None = Field(None, description="Target system/host identifier.")
    system_type: SystemType | None = Field(
        None, description="SAP stack: abap, java, dual, pipo, or solman."
    )

    # Behaviour flags
    dry_run: bool = Field(
        True, description="SAFE DEFAULT: nothing executes unless explicitly disabled."
    )
    assume_yes: bool = Field(False, description="Skip confirmation prompts (careful).")
    skip_checks: list[str] = Field(
        default_factory=list, description="Check names to skip."
    )

    # Free-form overrides (the escape hatch — pre/post hooks, custom params).
    params: dict[str, Any] = Field(
        default_factory=dict, description="Free-form key/value overrides for checks."
    )
    pre_hooks: list[str] = Field(default_factory=list, description="Commands run before.")
    post_hooks: list[str] = Field(default_factory=list, description="Commands run after.")

    @field_validator("sid")
    @classmethod
    def _normalise_sid(cls, v: str | None) -> str | None:
        """Uppercase the SID by SAP convention. Deep sanity (length, charset,
        instance pairing) is owned by the dedicated ``*.sid-instance-sanity``
        check, which validates against the live system — the schema only
        normalises here so it doesn't duplicate (and fight) that check."""
        return v.strip().upper() if v else v

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
        """Load and validate a context from a YAML config file.

        Raises ``ConfigError`` with a readable, line-oriented message when the
        file has unknown keys or invalid values — so misconfiguration fails
        loudly at load time rather than mid-migration.
        """
        import yaml

        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        try:
            data = yaml.safe_load(p.read_text()) or {}
        except yaml.YAMLError as exc:  # malformed YAML
            raise ConfigError(f"{p}: invalid YAML — {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{p}: top-level config must be a mapping, got {type(data).__name__}")
        try:
            return cls(**data)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(x) for x in e['loc']) or '(root)'}: {e['msg']}"
                for e in exc.errors()
            )
            raise ConfigError(f"{p}: {problems}") from exc
