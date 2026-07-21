"""Runbook — an ordered, named bundle of checks with an aggregate verdict.

A single check answers one question ("are the queues drained?"). A *runbook*
answers the operational question a migration consultant actually has: "is this
system ready for takeover, right now?". It runs a curated, ordered sequence of
checks in one shot, against the live system, and rolls their individual results
up into one readiness verdict.

Design principles (deliberately aligned with Exodia's stateless core):

* **The system is the source of truth, not a cache.** A runbook re-reads the
  live SAP system every time it runs. Running it five times gives five honest
  snapshots — it never remembers "queues were empty last time" and skips the
  work. That would be dangerous in a migration: a queue can refill between runs.
* **History lives in evidence bundles, not mutable state.** Each run writes a
  sealed, tamper-evident bundle (manifest + event trail). ``exodia history`` and
  ``exodia status`` read those bundles to show *what was run and when* — the
  audit clock, persisted — without ever pretending a live condition still holds.
* **Idempotent by construction.** Because every step is a read-only check that
  re-observes reality, a runbook is safe to re-run as often as you like. The
  GUI/CLI reflects the *current* state each time, which is exactly what a
  cutover operator needs on the day.

A Runbook is discovered by the registry just like Check/Action: subclass it,
give it a ``name`` and a ``steps`` list, drop it under ``exodia.modules``.
"""

from __future__ import annotations

from .params import ParamSpec, dedupe
from .registry import registry
from .result import Result, Status

# Aggregate verdict ranking: the worst individual status wins, with the usual
# migration semantics (a single blocking FAIL sinks the whole readiness verdict).
_SEVERITY = {
    Status.PASS: 0,
    Status.SKIP: 1,
    Status.WARN: 2,
    Status.FAIL: 3,
    Status.ERROR: 4,
}


class Runbook:
    """An ordered, named sequence of checks producing an aggregate verdict.

    Subclasses set ``name``, ``description`` and ``steps`` (an ordered list of
    check names). ``stop_on_blocking`` mirrors the check pipeline: when True
    (the default), the first blocking FAIL halts the remaining steps — matching
    ``run_checks`` — so a hard blocker is surfaced immediately. Set it False to
    always run every step (a full readiness sweep that reports every problem at
    once, even the blocking ones).
    """

    #: unique dotted name, e.g. "abap.cutover-readiness"
    name: str = ""
    #: human description
    description: str = ""
    #: ordered list of check names to run
    steps: list[str] = []
    #: stop at the first blocking FAIL (True) or always run every step (False)
    stop_on_blocking: bool = False

    def parameters(self) -> list[ParamSpec]:
        """Union of the parameters of every step check, de-duplicated by key.

        Lets the wizard prompt once for the combined set of inputs the whole
        runbook needs (e.g. one set of source connection params shared by all
        the ABAP readiness checks) instead of asking per check.
        """
        collected: list[ParamSpec] = []
        for step in self.steps:
            check_cls = registry.get_check(step)
            if check_cls is None:
                continue
            collected.extend(check_cls().parameters())
        return dedupe(collected)

    def resolve_steps(self) -> list[tuple[str, type | None]]:
        """Return each step name paired with its resolved check class (or None)."""
        return [(name, registry.get_check(name)) for name in self.steps]

    @staticmethod
    def aggregate(results: list[Result]) -> Status:
        """Roll individual results up into one verdict: the worst status wins.

        An empty run, or one where every step was skipped, is reported as SKIP —
        "we checked nothing" (or "everything was skipped") must never look like
        "everything is fine".
        """
        graded = [r for r in results if r.status is not Status.SKIP]
        if not graded:
            return Status.SKIP
        return max((r.status for r in graded), key=lambda s: _SEVERITY[s])

    @classmethod
    def verdict_result(cls, name: str, results: list[Result]) -> Result:
        """Build a synthetic Result representing the aggregate readiness verdict.

        Carries a per-status tally in ``data`` so the UI can render a readiness
        board (how many passed / warned / failed) without re-counting.
        """
        status = cls.aggregate(results)
        tally = {s.value: sum(1 for r in results if r.status is s) for s in Status}
        tally = {k: v for k, v in tally.items() if v}
        blocking = [r.name for r in results if r.status.is_blocking]
        if status is Status.PASS:
            summary = f"READY — all {len(results)} step(s) passed"
        elif status is Status.WARN:
            summary = f"READY WITH WARNINGS — {tally.get('warn', 0)} warning(s), no blockers"
        elif status.is_blocking:
            summary = (
                f"NOT READY — {len(blocking)} blocking issue(s): {', '.join(blocking)}"
            )
        else:
            summary = "INCONCLUSIVE — no steps produced a graded result"
        return Result(
            name=f"{name}.verdict",
            status=status,
            summary=summary,
            data={"tally": tally, "blocking": blocking, "steps": len(results)},
        )
