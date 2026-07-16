"""Runner — orchestrates ordered check pipelines and guarded actions."""

from __future__ import annotations

from collections.abc import Callable

from .base import Action, Check
from .context import Context
from .logging import get_logger
from .result import Result

log = get_logger()

# Live-progress hooks. Both are optional and default to no-ops, so callers
# (CLI, dashboard, tests) that don't care about progress are unaffected.
#   on_start(name)          -> a check/phase is about to run
#   on_result(name, result) -> a check/phase finished with this Result
OnStart = Callable[[str], None]
OnResult = Callable[[str, Result], None]


def _noop_start(name: str) -> None:  # pragma: no cover - trivial
    pass


def _noop_result(name: str, result: Result) -> None:  # pragma: no cover - trivial
    pass


def run_checks(
    checks: list[Check],
    ctx: Context,
    *,
    on_start: OnStart | None = None,
    on_result: OnResult | None = None,
) -> list[Result]:
    """Run an ordered list of checks. A blocking FAIL stops the pipeline early."""
    on_start = on_start or _noop_start
    on_result = on_result or _noop_result
    results: list[Result] = []
    for check in checks:
        if check.name in ctx.skip_checks:
            on_start(check.name)
            skipped = Result.skip(check.name, "skipped via config/--skip")
            results.append(skipped)
            on_result(check.name, skipped)
            continue
        on_start(check.name)
        result = check.execute(ctx)
        results.append(result)
        on_result(check.name, result)
        if check.blocking and result.status.is_blocking:
            log.warning("blocking check %s failed — stopping pipeline early", check.name)
            break
    return results


def run_action(
    action: Action,
    prechecks: list[Check],
    ctx: Context,
    *,
    on_start: OnStart | None = None,
    on_result: OnResult | None = None,
) -> list[Result]:
    """Run pre-checks, then the guarded action flow. Aborts if a precheck blocks."""
    on_start = on_start or _noop_start
    on_result = on_result or _noop_result
    results = run_checks(prechecks, ctx, on_start=on_start, on_result=on_result)
    if any(r.status.is_blocking for r in results):
        aborted = Result.skip(f"{action.name}.execute", "aborted — pre-checks did not pass")
        results.append(aborted)
        on_result(f"{action.name}.execute", aborted)
        return results
    on_start(action.name)
    phase_results = action.run_guarded(ctx)
    results.extend(phase_results)
    for r in phase_results:
        on_result(r.name, r)
    return results
