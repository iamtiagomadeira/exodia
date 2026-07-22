"""Exodia TUI — a flexible, grid-based terminal cockpit for SAP migrations.

This is the Textual front-end foreseen by ``core/monitor.py`` (TIA-66): a
mission-control layout that puts the whole toolkit on one screen —

  ┌───────────────────────────────────────────────────────────────┐
  │  EXODIA wordmark · tagline · version                (header)   │
  ├───────────────┬───────────────────────────────────────────────┤
  │ operations    │  detail  (selected op: kind, phase, params)    │
  │ tree          │                                                │
  │ (families →   ├───────────────────────────────────────────────┤
  │  methods →    │  live log tail  (native output while running)  │
  │  runbooks/    │                                                │
  │  checks/      ├───────────────────────────────────────────────┤
  │  actions)     │  results table  (status · name · duration)     │
  ├───────────────┴───────────────────────────────────────────────┤
  │  readiness board: PASS · WARN · FAIL · SKIP · elapsed (footer) │
  └───────────────────────────────────────────────────────────────┘

The layout is a real CSS grid: panels are keyed widgets that keep their
identity across resizes, focus moves with arrows / hjkl / Tab, and any panel
can be zoomed to full screen. Everything is wired to the live registry — the
same 91 checks / 25 actions / 7 runbooks the CLI runs — so the TUI is not a
mock: pressing Enter on a read-only check or runbook runs it for real and
streams results into the log + table via :class:`TextualMonitor`.

Design rules honoured from the rest of the codebase:

* **Read-only by default.** The TUI runs checks and runbooks (always safe).
  State-changing *actions* are shown but NOT executed from the TUI — they carry
  the guarded dry-run→confirm flow that belongs on the CLI. Selecting an action
  shows its plan/metadata; a clear notice points to ``exodia run ... --execute``.
* **The system is the source of truth.** Every run re-reads the live system;
  nothing is cached.
* **Same discovery backbone.** No hand-wired menu: the tree is built from
  ``core.menu`` families/methodologies, so new modules appear automatically.
"""

from __future__ import annotations

from .app import ExodiaTUI, run_tui
from .monitor import TextualMonitor

__all__ = ["ExodiaTUI", "TextualMonitor", "run_tui"]
