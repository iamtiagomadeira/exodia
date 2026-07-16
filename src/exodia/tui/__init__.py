"""Optional Textual-based dashboard (extra `tui`).

Everything here imports `textual` lazily / at module import time; the CLI only
imports this module *inside* the command function, so users without the extra
installed are never forced to have textual present.
"""

from __future__ import annotations

from .app import DashboardApp, run_dashboard

__all__ = ["DashboardApp", "run_dashboard"]
