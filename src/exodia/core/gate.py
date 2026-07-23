"""Gate engine — severity policy, audited overrides, and go/no-go verdicts.

This module turns a list of :class:`~exodia.core.result.Result` into a
**phase gate verdict** the way the real Cutover Plan does (see ``COP_model.md``):

* **Gate at the boundaries, advise in between.** A gate is a discrete GO / NO-GO
  decision with explicit criteria — not a per-phase traffic light. Only
  :class:`~exodia.core.severity.Severity.BLOCKING` results can produce a NO-GO.
  ADVISORY results never block; they accumulate into the exception report.

* **Severity is intrinsic; policy is per-engagement.** A check declares its
  default severity, but a :class:`GatePolicy` (loaded from the engagement's
  config) can reclassify it — e.g. one customer wants ST22 dumps treated as
  blocking, another as advisory. The check code never changes.

* **Override = a conscious, audited decision.** A blocking NO-GO can be overridden
  so the operator is never *trapped* by the tool at 3am — but every override
  records *who / what / when / why*. The override log **is** the handover
  exception artifact the operator has to produce anyway (evidence-by-default).

* **The tool is a co-pilot, not a lock.** It renders a verdict and refuses to
  auto-advance a runbook on a blocking-open, but the operator can acknowledge and
  proceed. A *supervised* policy (junior executes a senior's plan) can forbid or
  restrict overrides via config.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .result import Phase, Result, Status
from .severity import Severity


class GateDecision(str, Enum):
    """The verdict of a phase gate."""

    GO = "go"  # nothing blocking open — safe to advance
    NO_GO = "no_go"  # at least one blocking issue open and not overridden
    GO_WITH_OVERRIDE = "go_with_override"  # blocking issue(s) consciously overridden
    PENDING = "pending"  # nothing graded yet / all skipped — not decided

    @property
    def icon(self) -> str:
        return {
            GateDecision.GO: "✅",
            GateDecision.NO_GO: "🔴",
            GateDecision.GO_WITH_OVERRIDE: "⚠️",
            GateDecision.PENDING: "⏳",
        }[self]

    @property
    def is_go(self) -> bool:
        """True when the gate permits advancing (clean or overridden)."""
        return self in (GateDecision.GO, GateDecision.GO_WITH_OVERRIDE)


class Override(BaseModel):
    """An audited decision to proceed past a blocking finding.

    This is the atomic unit of the handover exception log: it answers *who*
    decided to ignore *what*, *when*, and *why*. Serialisable so it lands in the
    evidence bundle and the exportable exception report verbatim.
    """

    check: str  # the check name being overridden
    reason: str  # WHY — the conscious justification (required, non-empty)
    who: str  # WHO — operator identity / initials
    when: datetime = Field(default_factory=lambda: datetime.now(UTC))
    phase: Phase = Phase.UNCLASSIFIED

    def one_line(self) -> str:
        ts = self.when.strftime("%Y-%m-%d %H:%M UTC")
        return f"[{ts}] {self.who} overrode {self.check}: {self.reason}"


class GatePolicy(BaseModel):
    """Per-engagement gate policy — reclassification + override rules.

    Loaded from the engagement config (a ``gate:`` block). All fields optional;
    an empty policy reproduces the intrinsic severities and permits overrides
    (expert mode), which is the safe default for a senior-operated tool.
    """

    # Reclassify specific checks for THIS engagement: {check_name: severity}.
    # e.g. {"abap.st22-dumps": "blocking"} for a compliance-strict customer.
    reclassify: dict[str, Severity] = Field(default_factory=dict)
    # When True (supervised mode), blocking findings CANNOT be overridden — a
    # junior executing a senior's plan must escalate instead of proceeding.
    forbid_override: bool = False
    # Optional allow-list: only these check names may be overridden even in
    # expert mode. Empty = any blocking check may be overridden (with audit).
    overridable: list[str] = Field(default_factory=list)

    def severity_of(self, result: Result) -> Severity:
        """Effective severity for a result under this policy.

        Per-engagement reclassification wins over the result's intrinsic
        severity. This is the *only* place the two axes (intrinsic vs policy)
        are reconciled.
        """
        override = self.reclassify.get(result.name)
        if override is not None:
            return override
        return result.severity

    def may_override(self, check_name: str) -> bool:
        """Whether a given blocking check is allowed to be overridden."""
        if self.forbid_override:
            return False
        if self.overridable:
            return check_name in self.overridable
        return True

    @classmethod
    def from_context(cls, ctx: Any) -> GatePolicy:
        """Build a policy from a Context's ``gate`` param block (or empty).

        Reads ``ctx.params['gate']`` (a mapping) so the policy travels in the
        existing config/escape-hatch without a schema change. Unknown/missing =>
        default expert-mode policy.
        """
        raw: dict[str, object] = {}
        try:
            got = ctx.get("gate") or {}
            if isinstance(got, dict):
                raw = got
        except Exception:  # noqa: BLE001 - any non-Context is treated as empty
            raw = {}
        if not raw:
            return cls()
        # Coerce reclassify values into Severity, dropping unparseable entries.
        reclassify_raw = raw.get("reclassify") or {}
        reclassify: dict[str, Severity] = {}
        if isinstance(reclassify_raw, dict):
            for name, val in reclassify_raw.items():
                sev = Severity.coerce(val)
                if sev is not None:
                    reclassify[str(name)] = sev
        overridable_raw = raw.get("overridable") or []
        overridable = [str(x) for x in overridable_raw] if isinstance(overridable_raw, list) else []
        return cls(
            reclassify=reclassify,
            forbid_override=bool(raw.get("forbid_override", False)),
            overridable=overridable,
        )


class GateVerdict(BaseModel):
    """The computed decision for one phase gate.

    Carries everything the panel/report needs without recomputation: the
    decision, the open blockers, the acknowledged advisories, the applied
    overrides, and a per-role tally.
    """

    phase: Phase
    decision: GateDecision
    blocking_open: list[str] = Field(default_factory=list)
    advisories: list[str] = Field(default_factory=list)
    overrides: list[Override] = Field(default_factory=list)
    passed: int = 0
    total_graded: int = 0

    @property
    def summary(self) -> str:
        d = self.decision
        if d is GateDecision.GO:
            return f"GO — {self.passed}/{self.total_graded} passed, no blockers"
        if d is GateDecision.GO_WITH_OVERRIDE:
            n = len(self.overrides)
            return (
                f"GO WITH OVERRIDE — {n} blocking issue(s) consciously overridden; "
                f"{len(self.advisories)} advisory(ies) noted"
            )
        if d is GateDecision.NO_GO:
            return (
                f"NO-GO — {len(self.blocking_open)} blocking issue(s) open: "
                f"{', '.join(self.blocking_open)}"
            )
        return "PENDING — nothing graded yet"

    def one_line(self) -> str:
        """Compact panel line, e.g. ``Preparation ⚠️ GO WITH OVERRIDE 12/14``."""
        return (
            f"{self.phase.label} {self.decision.icon} "
            f"{self.decision.value.upper().replace('_', ' ')} "
            f"{self.passed}/{self.total_graded}"
        )


def evaluate_gate(
    phase: Phase,
    results: list[Result],
    policy: GatePolicy | None = None,
    overrides: list[Override] | None = None,
) -> GateVerdict:
    """Compute the go/no-go verdict for one phase.

    Args:
        phase: the phase these results belong to.
        results: the graded results for this phase.
        policy: per-engagement reclassification/override rules (default: empty).
        overrides: audited overrides already recorded for this run.

    Logic (mirrors the COP):
    * A result is a *blocking-open* iff its effective severity gates AND its
      status is blocking (FAIL/ERROR) AND it has not been overridden.
    * ADVISORY failures never block — they land in ``advisories`` for the
      exception report.
    * Decision: NO-GO if any blocking-open remain; GO_WITH_OVERRIDE if blockers
      existed but every one was overridden; GO if clean; PENDING if nothing was
      graded.
    """
    policy = policy or GatePolicy()
    overrides = list(overrides or [])
    overridden_names = {o.check for o in overrides}

    blocking_open: list[str] = []
    advisories: list[str] = []
    applied_overrides: list[Override] = []
    passed = 0
    graded = 0

    for r in results:
        if r.status is Status.SKIP:
            continue
        graded += 1
        if r.status is Status.PASS:
            passed += 1
            continue
        # A non-pass, non-skip result (WARN/FAIL/ERROR): route by effective role.
        eff = policy.severity_of(r)
        if eff.gates and r.status.is_blocking:
            if r.name in overridden_names:
                applied_overrides.extend(o for o in overrides if o.check == r.name)
            else:
                blocking_open.append(r.name)
        else:
            # ADVISORY/INFO failure or a WARN — feeds the exception report.
            advisories.append(r.name)

    if graded == 0:
        decision = GateDecision.PENDING
    elif blocking_open:
        decision = GateDecision.NO_GO
    elif applied_overrides:
        decision = GateDecision.GO_WITH_OVERRIDE
    else:
        decision = GateDecision.GO

    return GateVerdict(
        phase=phase,
        decision=decision,
        blocking_open=blocking_open,
        advisories=advisories,
        overrides=applied_overrides,
        passed=passed,
        total_graded=graded,
    )


def evaluate_all_gates(
    results: list[Result],
    policy: GatePolicy | None = None,
    overrides: list[Override] | None = None,
) -> list[GateVerdict]:
    """Group results by phase and return one verdict per phase, in phase order.

    Convenience wrapper used by the CLI/TUI: it bins results by their
    ``Phase`` and calls :func:`evaluate_gate` on each bin. Phases with no
    results are omitted. Verdicts come back sorted by ``Phase.order`` so the
    caller can render gates in cutover sequence
    (Preparation -> Ramp-Down -> Downtime -> Post-Activities).
    """
    policy = policy or GatePolicy()
    by_phase: dict[Phase, list[Result]] = {}
    for r in results:
        by_phase.setdefault(r.phase, []).append(r)
    verdicts = [
        evaluate_gate(phase, phase_results, policy=policy, overrides=overrides)
        for phase, phase_results in by_phase.items()
    ]
    return sorted(verdicts, key=lambda v: v.phase.order)
