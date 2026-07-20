"""Interactive menu — the operator-friendly front door to Exodia.

Goal: an admin should never have to hand-craft a long command line or a YAML
file. ``exodia menu`` walks them through it:

    1. pick a methodology (grouped from discovered operations)
    2. pick an operation within it (checks first — they're safe — then actions)
    3. answer only the fields that operation declares (with defaults + choices)
    4. review, confirm, run — actions keep the guarded dry-run -> confirm flow

The prompting is abstracted behind a small ``Prompter`` protocol so the wizard
logic is unit-testable with a scripted fake, with the real Typer/Rich prompts
used at runtime.

Operation grouping is derived from the dotted name: the methodology is the first
segment (``tenant-copy`` from ``tenant-copy.hana.copy-tenant``). This means any
future module is picked up automatically with zero menu wiring — same philosophy
as the auto-discovery registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .base import Action, Check
from .context import Context
from .params import ParamKind, ParamSpec, dedupe
from .registry import Registry

_FIELD_KEYS = {"host", "user", "db_type", "source", "target", "sid", "system_type"}


class Prompter(Protocol):
    """Minimal I/O surface the wizard needs; real impl wraps Typer/Rich."""

    def choose(self, title: str, options: list[str]) -> int:
        """Show numbered options; return the chosen 0-based index."""
        ...

    def ask(self, prompt: str, default: str | None, secret: bool) -> str:
        """Ask for a free-text value; return the answer (possibly empty)."""
        ...

    def confirm(self, prompt: str, default: bool = False) -> bool:
        """Yes/no confirmation."""
        ...

    def note(self, message: str) -> None:
        """Show an informational line."""
        ...


@dataclass(frozen=True)
class Operation:
    """A discovered operation, normalised for the menu."""

    name: str
    kind: str  # "check" | "action"
    description: str
    methodology: str


def discover_operations(registry: Registry) -> list[Operation]:
    """Flatten the registry into menu operations, sorted by name."""
    ops: list[Operation] = []
    for name, check_cls in registry.checks().items():
        ops.append(Operation(name, "check", check_cls.description, _methodology(name)))
    for name, action_cls in registry.actions().items():
        ops.append(Operation(name, "action", action_cls.description, _methodology(name)))
    return sorted(ops, key=lambda o: (o.methodology, o.kind != "check", o.name))


def _methodology(name: str) -> str:
    """First dotted segment is the methodology group (e.g. 'tenant-copy')."""
    return name.split(".", 1)[0] if "." in name else name


def methodologies(ops: list[Operation]) -> list[str]:
    """Distinct methodology groups, in stable order."""
    out: list[str] = []
    for op in ops:
        if op.methodology not in out:
            out.append(op.methodology)
    return out


def _pretty(methodology: str) -> str:
    return methodology.replace("-", " ").replace("_", " ").title()


def collect_params(
    specs: list[ParamSpec], prompter: Prompter
) -> tuple[dict[str, str], dict[str, str]]:
    """Prompt for each spec. Returns (field_values, param_values).

    Empty answers to optional fields are dropped so defaults resolve naturally.
    Required-but-empty answers are re-prompted once, then kept as-is (the
    operation's own validation will surface a clean error downstream).
    """
    fields: dict[str, str] = {}
    params: dict[str, str] = {}
    for spec in dedupe(specs):
        value = _ask_one(spec, prompter)
        if value == "" and not spec.required:
            continue
        if spec.kind is ParamKind.FIELD or spec.key in _FIELD_KEYS:
            fields[spec.key] = value
        else:
            params[spec.key] = value
    return fields, params


def _ask_one(spec: ParamSpec, prompter: Prompter) -> str:
    if spec.help:
        prompter.note(f"  ⓘ {spec.help}")
    if spec.choices:
        idx = prompter.choose(spec.prompt, list(spec.choices))
        return spec.choices[idx]
    prompt = spec.prompt + (" *" if spec.required else "")
    answer = prompter.ask(prompt, spec.default, spec.secret).strip()
    if answer == "" and spec.required:
        prompter.note("  ⚠️  this field is required")
        answer = prompter.ask(prompt, spec.default, spec.secret).strip()
    return answer


def build_context(
    fields: dict[str, str],
    params: dict[str, str],
    *,
    execute: bool,
    assume_yes: bool,
) -> Context:
    """Assemble a Context from collected field + param values."""
    kwargs: dict[str, object] = {k: v for k, v in fields.items() if v != ""}
    kwargs["params"] = params
    kwargs["dry_run"] = not execute
    kwargs["assume_yes"] = assume_yes
    return Context(**kwargs)  # type: ignore[arg-type]


def spec_for(op: Operation, registry: Registry) -> list[ParamSpec]:
    """Return the declared parameters for an operation instance."""
    cls: type[Check] | type[Action] | None = (
        registry.get_check(op.name)
        if op.kind == "check"
        else registry.get_action(op.name)
    )
    if cls is None:
        return []
    return list(cls().parameters())
