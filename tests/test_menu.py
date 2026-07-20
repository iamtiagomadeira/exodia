"""Tests for the interactive menu wizard logic (Prompter-abstracted, no real I/O).

A scripted FakePrompter replays canned answers so the whole wizard flow is
unit-testable: methodology grouping, parameter collection (fields vs params,
required re-prompt, choices, defaults), and Context assembly.
"""

from __future__ import annotations

from exodia.core.context import Context
from exodia.core.menu import (
    build_context,
    collect_params,
    discover_operations,
    methodologies,
    spec_for,
)
from exodia.core.params import ParamKind, ParamSpec
from exodia.core.registry import registry


class FakePrompter:
    """Replays scripted answers; records what it was asked."""

    def __init__(
        self,
        choices: list[int] | None = None,
        answers: list[str] | None = None,
        confirms: list[bool] | None = None,
    ) -> None:
        self._choices = list(choices or [])
        self._answers = list(answers or [])
        self._confirms = list(confirms or [])
        self.notes: list[str] = []

    def choose(self, title: str, options: list[str]) -> int:
        return self._choices.pop(0)

    def ask(self, prompt: str, default: str | None, secret: bool) -> str:
        if self._answers:
            return self._answers.pop(0)
        return default or ""

    def confirm(self, prompt: str, default: bool = False) -> bool:
        return self._confirms.pop(0) if self._confirms else default

    def note(self, message: str) -> None:
        self.notes.append(message)


# --------------------------------------------------------------------------- #
# Discovery + grouping
# --------------------------------------------------------------------------- #


def test_discover_operations_includes_tenant_copy() -> None:
    ops = discover_operations(registry)
    names = {o.name for o in ops}
    assert "tenant-copy.hana.copy-tenant" in names
    assert "tenant-copy.hana.source-tenant-online" in names


def test_methodologies_grouping() -> None:
    ops = discover_operations(registry)
    groups = methodologies(ops)
    assert "tenant-copy" in groups
    assert "backup-restore" in groups


def test_checks_sort_before_actions_in_group() -> None:
    ops = discover_operations(registry)
    tc = [o for o in ops if o.methodology == "tenant-copy"]
    kinds = [o.kind for o in tc]
    # all checks come before any action within a methodology
    assert kinds.index("action") > max(
        (i for i, k in enumerate(kinds) if k == "check"), default=-1
    )


# --------------------------------------------------------------------------- #
# Parameter collection
# --------------------------------------------------------------------------- #


def test_collect_params_routes_fields_vs_params() -> None:
    specs = [
        ParamSpec("source", "Source", kind=ParamKind.FIELD, required=True),
        ParamSpec("target", "Target", kind=ParamKind.FIELD, required=True),
        ParamSpec("copy_method", "Method", default="replication"),
        ParamSpec("source_host", "Host"),
    ]
    prompter = FakePrompter(answers=["PRD", "QAS", "replication", "customer-hana"])
    fields, params = collect_params(specs, prompter)
    assert fields == {"source": "PRD", "target": "QAS"}
    assert params == {"copy_method": "replication", "source_host": "customer-hana"}


def test_collect_params_drops_empty_optional() -> None:
    specs = [
        ParamSpec("source_host", "Host"),  # optional, answered empty
        ParamSpec("copy_method", "Method", default="replication"),
    ]
    prompter = FakePrompter(answers=["", "replication"])
    fields, params = collect_params(specs, prompter)
    assert "source_host" not in params
    assert params["copy_method"] == "replication"


def test_collect_params_choices_use_choose() -> None:
    specs = [ParamSpec("copy_method", "Method", choices=("replication", "backup"))]
    prompter = FakePrompter(choices=[1])  # pick "backup"
    _, params = collect_params(specs, prompter)
    assert params["copy_method"] == "backup"


def test_collect_params_required_reprompt() -> None:
    specs = [ParamSpec("source", "Source", kind=ParamKind.FIELD, required=True)]
    # first answer empty -> re-prompt -> "PRD"
    prompter = FakePrompter(answers=["", "PRD"])
    fields, _ = collect_params(specs, prompter)
    assert fields["source"] == "PRD"
    assert any("required" in n for n in prompter.notes)


# --------------------------------------------------------------------------- #
# Context assembly
# --------------------------------------------------------------------------- #


def test_build_context_dry_run_default() -> None:
    ctx = build_context(
        {"source": "PRD", "target": "QAS"},
        {"copy_method": "replication"},
        execute=False,
        assume_yes=False,
    )
    assert isinstance(ctx, Context)
    assert ctx.dry_run is True
    assert ctx.source == "PRD"
    assert ctx.get("copy_method") == "replication"


def test_build_context_execute_sets_flags() -> None:
    ctx = build_context({}, {}, execute=True, assume_yes=True)
    assert ctx.dry_run is False
    assert ctx.assume_yes is True


# --------------------------------------------------------------------------- #
# spec_for: operations expose their declared parameters
# --------------------------------------------------------------------------- #


def test_spec_for_action_returns_declared_params() -> None:
    ops = discover_operations(registry)
    copy = next(o for o in ops if o.name == "tenant-copy.hana.copy-tenant")
    specs = spec_for(copy, registry)
    keys = {s.key for s in specs}
    assert {"source", "target", "copy_method", "source_host"} <= keys


def test_spec_for_undeclared_check_is_empty_but_safe() -> None:
    ops = discover_operations(registry)
    # any check without an override returns [] and must not raise
    for o in ops:
        if o.kind == "check":
            assert isinstance(spec_for(o, registry), list)
