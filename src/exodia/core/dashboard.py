"""Live dashboard — a Rich `Live` view of a running pipeline, with a graceful
non-TTY fallback (linear line output) for CI, logs and detached sessions.

The Textual-based rich UI lives in `exodia.tui` (optional extra); this module
is always available because Rich is a core dependency.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .result import Result, Status

_ICON = {
    Status.PASS: ("✓", "green"),
    Status.WARN: ("!", "yellow"),
    Status.FAIL: ("✗", "red"),
    Status.SKIP: ("–", "dim"),
    Status.ERROR: ("✗", "bold red"),
}


class LiveDashboard:
    """Tracks per-item progress and renders it live (TTY) or linearly (non-TTY).

    Use via :meth:`session` as a context manager, wiring ``on_start`` and
    ``on_result`` into the runner::

        with LiveDashboard(title, console).session() as dash:
            run_checks(checks, ctx, on_start=dash.on_start, on_result=dash.on_result)
    """

    def __init__(self, title: str, console: Console | None = None) -> None:
        self.title = title
        self.console = console or Console()
        # Order-preserving item state: name -> (status | None running, elapsed)
        self._order: list[str] = []
        self._status: dict[str, Status | None] = {}
        self._summary: dict[str, str] = {}
        self._started_at: dict[str, float] = {}
        self._elapsed: dict[str, float] = {}
        self._active: str | None = None
        self._live: Live | None = None

    # -- runner hooks ------------------------------------------------------- #
    def on_start(self, name: str) -> None:
        if name not in self._status:
            self._order.append(name)
        self._status[name] = None  # running
        self._started_at[name] = time.monotonic()
        self._active = name
        if self._live is not None:
            self._live.update(self._render())
        elif not self._is_tty:
            self.console.print(f"[cyan]▶[/] {name} …")

    def on_result(self, name: str, result: Result) -> None:
        self._status[name] = result.status
        self._summary[name] = result.summary
        started = self._started_at.get(name, time.monotonic())
        self._elapsed[name] = time.monotonic() - started
        if self._active == name:
            self._active = None
        if self._live is not None:
            self._live.update(self._render())
        elif not self._is_tty:
            icon, style = _ICON.get(result.status, ("?", "white"))
            self.console.print(
                f"[{style}]{icon}[/] {name} "
                f"[{style}]{result.status.value.upper()}[/] "
                f"[dim]({self._elapsed[name]:.1f}s)[/] {result.summary}"
            )

    # -- rendering ---------------------------------------------------------- #
    @property
    def _is_tty(self) -> bool:
        return self.console.is_terminal

    def _render(self) -> Table:
        table = Table(title=self.title, expand=True)
        table.add_column("", width=3)
        table.add_column("Check / Phase", style="cyan", no_wrap=True)
        table.add_column("Status")
        table.add_column("Time", justify="right", width=8)
        table.add_column("Summary")
        for name in self._order:
            status = self._status.get(name)
            if status is None:
                marker: Text | Spinner = Spinner("dots", style="cyan")
                status_cell = Text("running", style="cyan")
                elapsed = time.monotonic() - self._started_at.get(name, time.monotonic())
            else:
                icon, style = _ICON.get(status, ("?", "white"))
                marker = Text(icon, style=style)
                status_cell = Text(status.value.upper(), style=style)
                elapsed = self._elapsed.get(name, 0.0)
            table.add_row(
                marker,
                name,
                status_cell,
                f"{elapsed:.1f}s",
                self._summary.get(name, ""),
            )
        return table

    @contextmanager
    def session(self) -> Iterator[LiveDashboard]:
        """Context manager. On a TTY, drives a live-updating table; otherwise
        yields immediately (hooks emit linear line output instead)."""
        if not self._is_tty:
            yield self
            return
        with Live(self._render(), console=self.console, refresh_per_second=12) as live:
            self._live = live
            try:
                yield self
            finally:
                live.update(self._render())
                self._live = None
