"""Runner — orchestrates ordered check pipelines and guarded actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Action, Check
from .context import Context
from .logging import get_logger
from .result import Result

if TYPE_CHECKING:
    from .evidence import EvidenceBundle
    from .runbook import Runbook

log = get_logger()


def run_checks(
    checks: list[Check], ctx: Context, evidence: EvidenceBundle | None = None
) -> list[Result]:
    """Run an ordered list of checks. A blocking FAIL stops the pipeline early.

    If an ``evidence`` bundle is supplied, each result is recorded to it as an
    automatic audit by-product (no extra effort at the call site).
    """
    results: list[Result] = []
    for check in checks:
        if check.name in ctx.skip_checks:
            result = Result.skip(check.name, "skipped via config/--skip")
            results.append(result)
            if evidence is not None:
                evidence.add_results([result])
            continue
        result = check.execute(ctx)
        results.append(result)
        if evidence is not None:
            evidence.add_results([result])
        if check.blocking and result.status.is_blocking:
            log.warning("blocking check %s failed — stopping pipeline early", check.name)
            break
    return results


def run_action(
    action: Action,
    prechecks: list[Check],
    ctx: Context,
    evidence: EvidenceBundle | None = None,
) -> list[Result]:
    """Run pre-checks, then the guarded action flow. Aborts if a precheck blocks."""
    results = run_checks(prechecks, ctx, evidence)
    if any(r.status.is_blocking for r in results):
        aborted = Result.skip(f"{action.name}.execute", "aborted — pre-checks did not pass")
        results.append(aborted)
        if evidence is not None:
            evidence.add_results([aborted])
        return results
    phases = action.run_guarded(ctx)
    results.extend(phases)
    if evidence is not None:
        evidence.add_results(phases)
    return results


def run_runbook(
    runbook: Runbook, ctx: Context, evidence: EvidenceBundle | None = None
) -> list[Result]:
    """Run every step check of a runbook in order, then append the verdict.

    Each step re-observes the live system (no cached state), results stream into
    the evidence bundle as they land, and the run finishes with a synthetic
    aggregate verdict Result. If ``runbook.stop_on_blocking`` is True the first
    blocking FAIL halts the remaining steps (like the check pipeline); otherwise
    every step runs so the operator sees every problem at once.

    A step naming a check that isn't registered yields an ERROR result for that
    step (surfaced, never silently dropped) rather than aborting the whole run.
    """
    results: list[Result] = []
    for name, check_cls in runbook.resolve_steps():
        if check_cls is None:
            missing = Result.error(name, "unknown check — not registered")
            results.append(missing)
            if evidence is not None:
                evidence.add_results([missing])
            continue
        if name in ctx.skip_checks:
            skipped = Result.skip(name, "skipped via config/--skip")
            results.append(skipped)
            if evidence is not None:
                evidence.add_results([skipped])
            continue
        result = check_cls().execute(ctx)
        results.append(result)
        if evidence is not None:
            evidence.add_results([result])
        if runbook.stop_on_blocking and result.status.is_blocking:
            log.warning(
                "runbook %s: blocking step %s failed — stopping early",
                runbook.name,
                name,
            )
            break

    verdict = type(runbook).verdict_result(runbook.name, results)
    results.append(verdict)
    if evidence is not None:
        evidence.add_results([verdict])
    return results
