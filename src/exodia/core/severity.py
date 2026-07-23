"""Severity — the three gate roles a check can carry.

The real Cutover Plan (see ``COP_model.md``) does **not** block on system
hygiene. It blocks only what makes the copy fail technically or lose data;
everything else is a documented exception the customer acknowledges. That gives
three roles:

* **BLOCKING**  — stops the copy or risks data loss (no recoverable backup, HSR
  not in SYNC before takeover, missing migration key, insufficient target
  space). A blocking FAIL is a NO-GO for the phase gate. Overridable only as a
  *conscious, audited* decision.
* **ADVISORY**  — system hygiene / go-live quality that does NOT fail the copy
  (ST22 dumps, SE80 inactive objects, SPAM queue, mount-point >80%). Never
  blocks; instead it feeds the exportable exception report the customer signs
  off.
* **INFO**      — context only (recorded baselines, versions). Never a gate;
  display-only on the panel.

Two design rules from the COP:

1. **Severity is intrinsic to the check** (declared on the class), but the
   **gate policy is per-engagement** (config can reclassify a check for a given
   customer). This module owns the intrinsic default; :mod:`exodia.core.gate`
   owns the per-engagement policy.
2. **Backward compatibility.** Checks predating this model declared only
   ``blocking: bool``. ``Severity.from_check`` derives the role from that flag
   so every existing check keeps working with zero edits: ``blocking=True`` ->
   BLOCKING, otherwise ADVISORY (a plain FAIL that doesn't halt the pipeline is,
   by definition, advisory).
"""

from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    """The gate role of a check — how a FAIL is treated at a phase gate."""

    BLOCKING = "blocking"
    ADVISORY = "advisory"
    INFO = "info"

    @property
    def icon(self) -> str:
        """Traffic-light glyph for the panel/report."""
        return {
            Severity.BLOCKING: "🔴",
            Severity.ADVISORY: "🟡",
            Severity.INFO: "⚪",
        }[self]

    @property
    def label(self) -> str:
        """Human-readable role name for reports."""
        return {
            Severity.BLOCKING: "Blocking",
            Severity.ADVISORY: "Advisory",
            Severity.INFO: "Info",
        }[self]

    @property
    def rank(self) -> int:
        """Ordering used when the worst role wins (higher = more severe)."""
        return {Severity.INFO: 0, Severity.ADVISORY: 1, Severity.BLOCKING: 2}[self]

    @property
    def gates(self) -> bool:
        """True when a FAIL in this role can produce a NO-GO at a phase gate.

        Only BLOCKING gates. ADVISORY and INFO never block the copy — they feed
        the exception report instead.
        """
        return self is Severity.BLOCKING

    @classmethod
    def from_check(cls, check: object) -> Severity:
        """Derive the intrinsic severity of a check.

        Preference order:
        1. An explicit ``severity`` attribute (new model) if present.
        2. Fall back to the legacy ``blocking: bool`` flag: True -> BLOCKING,
           False -> ADVISORY. This keeps every pre-existing check working with
           no edits — a non-blocking FAIL was always, semantically, advisory.
        """
        declared = getattr(check, "severity", None)
        if isinstance(declared, Severity):
            return declared
        if isinstance(declared, str):
            try:
                return cls(declared)
            except ValueError:
                pass
        return cls.BLOCKING if getattr(check, "blocking", False) else cls.ADVISORY

    @classmethod
    def coerce(cls, value: object) -> Severity | None:
        """Best-effort parse of a config value into a Severity (or None)."""
        if isinstance(value, Severity):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError:
                return None
        return None
