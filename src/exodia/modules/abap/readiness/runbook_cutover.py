"""Cutover readiness runbook — the "is this system ready for takeover?" sweep.

Bundles the read-only ABAP readiness checks into one ordered runbook that a
migration consultant runs against the live source (and, for the comparison
checks, the target) on cutover day. It answers the single operational question
the cutover plan keeps asking by hand across a dozen transactions — SM04, SM12,
SM13/SM58/SMQ1/SMQ2, SM37, SP01, ST22, SMT1/SM59, SE03/SE01, SCC4, SM51 — with
one aggregate verdict and a sealed evidence bundle.

Ordering mirrors how an operator reasons on the day:

1. **Identity & topology** first — confirm we're pointed at the right system and
   see every app server (system-info, app-servers).
2. **Version parity** — source vs target component levels (component-versions).
3. **Ramp-down** — the heart of it: no interactive users, no locks, drained
   update/tRFC/qRFC queues, no pending background jobs (active-users,
   lock-entries, update-queues-drained, background-jobs).
4. **Housekeeping / post-processing signals** — spool backlog, fresh short
   dumps, transport backlog, RFC destinations, client openness
   (spool-requests, short-dumps, transport-requests, rfc-destinations,
   client-settings).

``stop_on_blocking`` is False on purpose: on cutover day you want the *whole*
picture in one pass — every blocker and every warning at once — not a run that
halts at the first drained-queue FAIL and hides the rest.
"""

from __future__ import annotations

from exodia.core.runbook import Runbook


class CutoverReadinessRunbook(Runbook):
    """Full read-only ABAP takeover-readiness sweep (SAP MIG)."""

    name = "abap.cutover-readiness"
    description = (
        "Read-only takeover-readiness sweep: identity, version parity, ramp-down "
        "drain and post-processing signals, with one aggregate verdict."
    )
    stop_on_blocking = False
    steps = [
        # 1. identity & topology
        "abap.readiness.system-info",
        "abap.readiness.app-servers",
        # 2. version parity (source vs target)
        "abap.readiness.component-versions",
        # 3. ramp-down (the blocking core)
        "abap.readiness.active-users",
        "abap.readiness.lock-entries",
        "abap.readiness.update-queues-drained",
        "abap.readiness.background-jobs",
        # 4. housekeeping / post-processing signals
        "abap.readiness.spool-requests",
        "abap.readiness.short-dumps",
        "abap.readiness.transport-requests",
        "abap.readiness.rfc-destinations",
        "abap.readiness.client-settings",
    ]
