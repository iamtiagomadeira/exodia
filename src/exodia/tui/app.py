"""Textual dashboard app for Exodia (optional extra `tui`).

Renders a live pipeline view: a table of checks/actions, a scrollable log, and
a status bar with the current phase. The pipeline runs on a worker thread so
the UI stays responsive; runner hooks post updates back onto the app.
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Log, Static

from exodia.core.result import Result, Status

_STATUS_TEXT = {
    Status.PASS: "PASS",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.SKIP: "SKIP",
    Status.ERROR: "ERROR",
}


class DashboardApp(App[int]):
    """Live Textual dashboard driving a pipeline runner.

    `pipeline` is a callable that receives ``on_start`` and ``on_result`` hooks
    and returns the final ``list[Result]`` (its exit code drives the app result).
    """

    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; background: $boost; color: $text; padding: 0 1; }
    #body { height: 1fr; }
    #checks { width: 60%; border: round $primary; }
    #log { width: 40%; border: round $secondary; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        title: str,
        pipeline: Callable[
            [Callable[[str], None], Callable[[str, Result], None]], list[Result]
        ],
    ) -> None:
        super().__init__()
        self._title = title
        self._pipeline = pipeline
        self._results: list[Result] = []
        self._rows: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(f"⏳ {self._title} — starting…", id="status")
        with Horizontal(id="body"):
            with Vertical(id="checks"):
                yield DataTable(id="table")
            with Vertical(id="log"):
                yield Log(id="logview")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns("Check / Phase", "Status", "Summary")
        self.run_worker(self._run, thread=True, exclusive=True)

    # -- worker ------------------------------------------------------------- #
    def _run(self) -> None:
        def on_start(name: str) -> None:
            self.call_from_thread(self._mark_running, name)

        def on_result(name: str, result: Result) -> None:
            self.call_from_thread(self._mark_done, name, result)

        results = self._pipeline(on_start, on_result)
        self.call_from_thread(self._finish, results)

    # -- UI updates (main thread) ------------------------------------------ #
    def _mark_running(self, name: str) -> None:
        table = self.query_one("#table", DataTable)
        if name not in self._rows:
            self._rows[name] = table.add_row(name, "running", "", key=name)
        else:
            table.update_cell(name, "Status", "running")
        self.query_one("#status", Static).update(f"▶ {self._title} — running {name}")
        self.query_one("#logview", Log).write_line(f"▶ {name} …")

    def _mark_done(self, name: str, result: Result) -> None:
        table = self.query_one("#table", DataTable)
        label = _STATUS_TEXT.get(result.status, "?")
        if name not in self._rows:
            self._rows[name] = table.add_row(name, label, result.summary, key=name)
        else:
            table.update_cell(name, "Status", label)
            table.update_cell(name, "Summary", result.summary)
        self.query_one("#logview", Log).write_line(f"{label}: {name} — {result.summary}")

    def _finish(self, results: list[Result]) -> None:
        self._results = results
        blocking = any(r.status.is_blocking for r in results)
        verdict = "FAIL" if blocking else "OK"
        self.query_one("#status", Static).update(
            f"✔ {self._title} — done: {verdict} ({len(results)} results). Press q to quit."
        )


def run_dashboard(
    title: str,
    pipeline: Callable[[Callable[[str], None], Callable[[str, Result], None]], list[Result]],
) -> list[Result]:
    """Run the Textual dashboard and return the collected results."""
    app = DashboardApp(title, pipeline)
    app.run()
    return app._results
