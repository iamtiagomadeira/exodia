# Authoring a module

Exodia is **plugable**: drop a Python file under `src/exodia/modules/` and its
checks and actions appear in `exodia list` — no central registration, no wiring.
This guide walks through adding your own.

## How discovery works

At startup `Registry.discover()` walks `exodia.modules` (and `exodia.core.checks`)
with `pkgutil.walk_packages`, imports every submodule, and collects every concrete
subclass of `Check` and `Action` that defines a non-empty `name`. That means:

- **The methodology is derived from the dotted `name` string, not the folder.**
  A check named `hsr.version-compatibility` belongs to the `hsr` method regardless
  of where the file physically lives.
- Abstract base classes (no `name`) are ignored.
- An import error in one module is logged as a warning and skipped — it never
  breaks the whole CLI.

Folders are still the convention for humans:

```
src/exodia/modules/
  system_copy/
    <method>/                # backup_restore, export_import, hsr, tenant_copy
      __init__.py
      checks/
        __init__.py
        preconditions.py      # your Check subclasses
      actions/
        __init__.py
        <action>.py           # your Action subclasses
  solution_manager/           # standalone family
  pipo/
```

## The two categories

| | `Check` | `Action` |
|---|---|---|
| Effect | **Read-only.** Never mutates the target. | **State-changing.** Guarded. |
| Must implement | `run(ctx) -> Result` | `dry_run`, `execute`, `verify` |
| Optional | `parameters()`, `blocking` | `parameters()`, `rollback()`, `requires_checks` |

This Check/Action split is the safety backbone — keep checks side-effect free so
they are safe to run anywhere, any time.

## Writing a Check

```python
from __future__ import annotations

from exodia.core import Check, Context, Result
from exodia.core.params import ParamSpec

# ParamSpecs drive the interactive menu (exodia menu). Declare the inputs your
# check needs; the wizard prompts for them field-by-field.
BACKUP_PATH = ParamSpec(
    "backup_path",
    "Backup destination directory",
    default="/hana/backup",
    help="Where the backup will be written.",
)


class BackupSpaceCheck(Check):
    # Unique, dotted name. The prefix (here 'backup-restore') is the method.
    name = "backup-restore.hana.free-space"
    description = "Enough free space at the backup destination."
    blocking = True  # a FAIL here aborts the surrounding prepare pipeline

    def parameters(self) -> list[ParamSpec]:
        return [BACKUP_PATH]

    def run(self, ctx: Context) -> Result:
        path = ctx.get("backup_path") or "/hana/backup"
        # Run commands ONLY through ctx.runner() — never subprocess directly.
        cr = ctx.runner().run(["df", "-BG", "--output=avail", path])
        if not cr.ok:
            # SKIP when you cannot determine the answer (not a failure).
            return Result.skip(self.name, f"could not read free space at {path}")
        gib = int(cr.stdout.strip().splitlines()[-1].rstrip("G"))
        if gib < 50:
            return Result.fail(self.name, f"only {gib}G free at {path} (need >= 50G)")
        return Result.ok(self.name, f"{gib}G available at {path}")
```

### Result helpers

Return exactly one `Result` from `run()`. Use the status that matches reality:

| Helper | Status | Use when |
|---|---|---|
| `Result.ok(name, summary="", **data)` | PASS | The check passed. |
| `Result.warn(name, summary, **data)` | WARN | Non-blocking concern; operator should review. |
| `Result.fail(name, summary, **data)` | FAIL | Blocking problem; must be resolved first. |
| `Result.skip(name, summary, **data)` | SKIP | Could not determine (unreachable, missing input). |
| `Result.error(name, summary, **data)` | ERROR | Unexpected exception (the framework does this for you). |

Any extra keyword args are stored as structured `data` on the result — useful for
the evidence bundle and the JSON report. **Prefer SKIP over FAIL when you simply
could not tell** — a false FAIL erodes trust in the whole run.

## Writing an Action

Actions are guarded by `run_guarded()`, which always runs `dry_run` first and
only proceeds to `execute` + `verify` when `ctx.dry_run` is false **and** the
operator confirmed (`ctx.assume_yes` / `--yes`).

```python
from __future__ import annotations

from exodia.core import Action, Context, Result


class RegisterSecondary(Action):
    name = "hsr.register-secondary"
    description = "Register the target as an HSR secondary."
    requires_checks = ["hsr.version-compatibility", "hsr.log-mode-normal"]

    def dry_run(self, ctx: Context) -> Result:
        # Describe EXACTLY what execute() would do — no side effects.
        return Result.ok(self.name, "would run hdbnsutil -sr_register ...")

    def execute(self, ctx: Context) -> Result:
        cr = ctx.runner().run(["hdbnsutil", "-sr_register", "..."])
        if not cr.ok:
            return Result.fail(self.name, f"register failed: {cr.stderr}")
        return Result.ok(self.name, "secondary registered")

    def verify(self, ctx: Context) -> Result:
        # Prove the goal was achieved (e.g. replication ACTIVE).
        cr = ctx.runner().run(["hdbnsutil", "-sr_state"])
        ok = cr.ok and "ACTIVE" in cr.stdout
        return (Result.ok if ok else Result.fail)(self.name, "replication state checked")

    def rollback(self, ctx: Context) -> Result:
        # Optional. Default is a documented-only SKIP (no auto-rollback).
        return Result.ok(self.name + ".rollback", "would run hdbnsutil -sr_unregister")
```

- `requires_checks` names checks that MUST pass before the action runs.
- Keep `dry_run` honest: it is what the operator reads before committing.
- Never `verify` after a failed `execute` — the framework already guards this.

## Parameters and the menu

`ParamSpec` is pure metadata that powers `exodia menu`. Two kinds:

- `ParamKind.FIELD` — a first-class `Context` field (`host`, `user`, `db_type`,
  `source`, `target`). Reuse the shared `HOST`, `USER`, `DB_TYPE` specs.
- `ParamKind.PARAM` (default) — a free-form entry read via `ctx.get("key")`.

Set `required=True` for mandatory inputs, `secret=True` for anything that must
never be echoed, and `choices=(...)` to constrain values. Free-form inputs and
`SOURCE_HOST`-style values are **PARAMs, not FIELDs**.

## Rules (enforced in review)

- **Commands are argument lists**, never `shell=True`. Always go through
  `ctx.runner()` so local and remote (SSH) execution both work.
- **Checks never mutate.** If it changes state, it is an Action.
- **Never log secrets.** Use `secret=True` on the ParamSpec.
- **Cite SAP Notes by number only** — never reproduce their copyrighted text.
- **Add tests** under `tests/` for every new check and action (see
  `tests/test_new_methods.py` for the `FakeRunner` pattern), and keep coverage
  above the CI floor.

## Verify your module is discovered

```bash
exodia list | grep <your-method>     # should list your checks/actions
exodia doctor                        # sanity check
exodia run <your.check.name>         # run it (dry-run is the default)
pytest -q                            # tests green
```
