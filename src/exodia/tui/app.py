"""ExodiaTUI — the Textual application: a grid cockpit over the live registry.

Run it with ``exodia tui`` (or ``python -m exodia.tui``). It discovers the same
checks / actions / runbooks the CLI uses and lays them out in a keyboard-driven
grid. Read-only operations (checks, runbooks) run for real in a worker thread
and stream into the log + results table; state-changing actions are shown but
deferred to the guarded CLI flow.
"""

from __future__ import annotations

from datetime import UTC, datetime

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    DataTable,
    Footer,
    RichLog,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

from .. import __version__
from ..core.context import Context
from ..core.evidence import EvidenceBundle
from ..core.menu import (
    checks_in,
    discover_operations,
    families,
    methodologies_in_family,
    pretty,
    runbooks_in,
)
from ..core.registry import registry
from ..core.result import Result, Status, format_duration
from ..core.runner import run_checks, run_runbook

# ASCII wordmark — pure typography of the tool's own name (no third-party art).
_WORDMARK = r""" ███████ ██   ██  ██████  ██████  ██  █████
 ██       ██ ██  ██    ██ ██   ██ ██ ██   ██
 █████     ███   ██    ██ ██   ██ ██ ███████
 ██       ██ ██  ██    ██ ██   ██ ██ ██   ██
 ███████ ██   ██  ██████  ██████  ██ ██   ██"""

_STATUS_ICON = {
    Status.PASS: "✅",
    Status.WARN: "⚠️",
    Status.FAIL: "❌",
    Status.SKIP: "⏭️",
    Status.ERROR: "💥",
}
_STATUS_CLASS = {
    Status.PASS: "pass",
    Status.WARN: "warn",
    Status.FAIL: "fail",
    Status.SKIP: "skip",
    Status.ERROR: "err",
}


class ExodiaTUI(App[None]):
    """The flexible-grid migration cockpit."""

    CSS_PATH = "exodia.tcss"
    TITLE = "EXODIA"
    SUB_TITLE = "SAP migration cockpit"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "run_selected", "Run"),
        Binding("r", "run_selected", "Run", show=False),
        # vim-style + Tab panel focus movement
        Binding("tab", "focus_next", "Next panel", show=False),
        Binding("shift+tab", "focus_previous", "Prev panel", show=False),
        Binding("l", "focus_main", "→ main", show=False),
        Binding("h", "focus_sidebar", "← tree", show=False),
        Binding("f", "toggle_maximize", "Zoom panel"),
        Binding("c", "clear_log", "Clear log"),
        Binding("d", "toggle_dark", "Dark/Light"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._ops = discover_operations(registry)
        self._results: list[Result] = []
        self._started_at = datetime.now(UTC)
        # maps a Tree node's data payload -> a run target
        self._counts = {
            "checks": len(registry.checks()),
            "actions": len(registry.actions()),
            "runbooks": len(registry.runbooks()),
        }

    # -- layout ------------------------------------------------------------- #
    def compose(self) -> ComposeResult:
        c = self._counts
        header_text = (
            f"{_WORDMARK}\n\n"
            f"[b white]The stateless SAP migration toolkit[/]  "
            f"[dim]— read-only checks, guarded actions, sealed evidence.[/]\n"
            f"[cyan]Creator:[/] [b]Tiago Madeira[/]   [dim]·[/]   "
            f"[cyan]v[/]{__version__}   [dim]·[/]   "
            f"[green]{c['checks']} checks[/] · "
            f"[magenta]{c['actions']} actions[/] · "
            f"[green]{c['runbooks']} runbooks[/]"
        )
        yield Static(header_text, id="header", markup=True)

        with Horizontal(id="body"):
            with Container(id="sidebar"):
                yield Static("Operations", id="sidebar-title")
                yield self._build_tree()
            with Vertical(id="main"):
                with Container(id="detail"):
                    yield Static("Detail", classes="panel-title")
                    yield Static(
                        "[dim]Select an operation on the left. "
                        "Enter runs a read-only check or runbook.[/]",
                        id="detail-body",
                        markup=True,
                    )
                with Container(id="logpanel"):
                    yield Static("Live log", classes="panel-title")
                    yield RichLog(id="log", highlight=True, markup=True, wrap=True)
                with Container(id="resultspanel"):
                    yield Static("Results", classes="panel-title")
                    yield DataTable(id="results", zebra_stripes=True, cursor_type="row")

        yield Static(self._board_text(), id="footer-board", markup=True)
        yield Footer()

    def _build_tree(self) -> Tree[dict]:
        """Build the family → method → (runbooks / checks / actions) tree."""
        tree: Tree[dict] = Tree("migration families", id="optree")
        tree.root.expand()
        tree.show_root = False
        for fam in families(self._ops):
            fam_node = tree.root.add(f"📁 {pretty(fam)}", data={"kind": "family"})
            fam_node.expand()
            for method in methodologies_in_family(self._ops, fam):
                m_node = fam_node.add(f"📂 {pretty(method)}", data={"kind": "method"})
                # runbooks first (one-click sweeps), then checks, then actions
                for name, desc, steps in runbooks_in(registry, method):
                    m_node.add_leaf(
                        f"📋 {name}  [dim]({steps} checks)[/]",
                        data={"kind": "runbook", "name": name, "desc": desc},
                    )
                for op in checks_in(self._ops, method):
                    m_node.add_leaf(
                        f"🔍 {op.name}",
                        data={"kind": "check", "name": op.name, "desc": op.description},
                    )
                for op in (o for o in self._ops if o.methodology == method and o.kind == "action"):
                    m_node.add_leaf(
                        f"⚙️  {op.name}",
                        data={"kind": "action", "name": op.name, "desc": op.description},
                    )
        return tree

    # -- setup after mount -------------------------------------------------- #
    def on_mount(self) -> None:
        table = self.query_one("#results", DataTable)
        table.add_columns("", "operation", "status", "duration", "summary")
        self.query_one(Tree).focus()

    # -- tree selection ----------------------------------------------------- #
    def on_tree_node_selected(self, event: Tree.NodeSelected[dict]) -> None:
        self._show_detail(event.node)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[dict]) -> None:
        self._show_detail(event.node)

    def _show_detail(self, node: TreeNode[dict]) -> None:
        data = node.data or {}
        kind = data.get("kind")
        body = self.query_one("#detail-body", Static)
        if kind == "check":
            check_cls = registry.get_check(data["name"])
            blocking = getattr(check_cls, "blocking", False) if check_cls else False
            body.update(
                f"[b cyan]{data['name']}[/]  [dim](read-only check)[/]\n\n"
                f"{data.get('desc') or '—'}\n\n"
                f"blocking: [b]{'yes' if blocking else 'no'}[/]\n"
                f"[green]▶ Enter to run — safe, reads the live system.[/]"
            )
        elif kind == "runbook":
            rb_cls = registry.get_runbook(data["name"])
            steps = len(getattr(rb_cls, "steps", []) or [])
            body.update(
                f"[b green]{data['name']}[/]  [dim](runbook — {steps} checks)[/]\n\n"
                f"{data.get('desc') or '—'}\n\n"
                f"[green]▶ Enter to run the whole sweep — re-reads the live "
                f"system, writes a sealed evidence bundle.[/]"
            )
        elif kind == "action":
            act_cls = registry.get_action(data["name"])
            reqs = ", ".join(getattr(act_cls, "requires_checks", []) or []) or "—"
            body.update(
                f"[b magenta]{data['name']}[/]  [dim](state-changing action)[/]\n\n"
                f"{data.get('desc') or '—'}\n\n"
                f"requires checks: {reqs}\n"
                f"[yellow]⚠ Actions are guarded and NOT run from the TUI. "
                f"Use:[/] [b]exodia run {data['name']} --execute[/]"
            )
        else:
            body.update("[dim]Pick a check or runbook leaf, then press Enter.[/]")

    # -- running ------------------------------------------------------------ #
    def action_run_selected(self) -> None:
        tree = self.query_one(Tree)
        node = tree.cursor_node
        data = (node.data or {}) if node else {}
        kind = data.get("kind")
        if kind == "check":
            self._run_worker(kind="check", name=data["name"])
        elif kind == "runbook":
            self._run_worker(kind="runbook", name=data["name"])
        elif kind == "action":
            self.notify(
                f"'{data['name']}' is a state-changing action — run it via "
                f"`exodia run {data['name']} --execute` for the guarded flow.",
                severity="warning",
                title="Guarded action",
            )
        else:
            self.notify("Select a check or runbook first.", severity="information")

    @work(thread=True, exclusive=True)
    def _run_worker(self, *, kind: str, name: str) -> None:
        """Run a check or runbook in a worker thread, streaming into the UI.

        Read-only only: builds a minimal local Context (dry-run), runs it, and
        pushes phase/log/result events back onto the UI thread via the
        ``mon_*`` hooks (mirroring what an action's monitor would do).
        """
        self.call_from_thread(self.mon_start, f"{kind}: {name}")
        self.call_from_thread(self.mon_phase, "running", name)
        try:
            ctx = Context(dry_run=True, assume_yes=False)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self.mon_log, f"[red]could not build context: {exc}[/]")
            self.call_from_thread(self.mon_stop)
            return

        try:
            if kind == "check":
                check_cls = registry.get_check(name)
                if check_cls is None:
                    self.call_from_thread(self.mon_log, f"[red]unknown check: {name}[/]")
                    self.call_from_thread(self.mon_stop)
                    return
                self.call_from_thread(self.mon_log, f"[cyan]running check →[/] {name} …")
                results = run_checks([check_cls()], ctx)
            else:
                rb_cls = registry.get_runbook(name)
                if rb_cls is None:
                    self.call_from_thread(self.mon_log, f"[red]unknown runbook: {name}[/]")
                    self.call_from_thread(self.mon_stop)
                    return
                bundle = EvidenceBundle(name, ctx, operation="runbook").open()
                self.call_from_thread(
                    self.mon_log, f"[cyan]running runbook →[/] {name} ({len(rb_cls().steps)} steps) …"
                )
                results = run_runbook(rb_cls(), ctx, evidence=bundle)
                bundle.close(results)
                self.call_from_thread(self.mon_log, f"[dim]📁 evidence: {bundle.dir}[/]")
        except Exception as exc:  # noqa: BLE001 - surface, never crash the UI
            self.call_from_thread(self.mon_log, f"[red]run failed: {exc}[/]")
            self.call_from_thread(self.mon_stop)
            return

        for r in results:
            self.call_from_thread(self.mon_result, r)
        self.call_from_thread(self.mon_phase, "done", name)
        self.call_from_thread(self.mon_stop)

    # -- Monitor hooks (called on the UI thread) ---------------------------- #
    def mon_start(self, title: str) -> None:
        self.query_one("#log", RichLog).write(f"[b]▶ {title}[/]  [dim]started[/]")

    def mon_stop(self) -> None:
        self.query_one("#footer-board", Static).update(self._board_text())

    def mon_phase(self, name: str, detail: str = "") -> None:
        suffix = f" — {detail}" if detail else ""
        self.query_one("#log", RichLog).write(f"[cyan]▶ phase:[/] {name}{suffix}")

    def mon_progress(self, percent: float | None, detail: str = "") -> None:
        if percent is not None:
            self.query_one("#log", RichLog).write(f"[cyan]  {percent:5.1f}%[/] {detail}")

    def mon_log(self, line: str) -> None:
        self.query_one("#log", RichLog).write(line)

    def mon_result(self, result: Result) -> None:
        self._results.append(result)
        icon = _STATUS_ICON.get(result.status, "")
        klass = _STATUS_CLASS.get(result.status, "")
        table = self.query_one("#results", DataTable)
        table.add_row(
            icon,
            result.display_title,
            f"[{klass}]{result.status.value.upper()}[/]",
            result.duration_str,
            (result.summary or "")[:80],
        )
        self.query_one("#footer-board", Static).update(self._board_text())

    def mon_handoff(self, message: str, url: str | None = None) -> None:
        log = self.query_one("#log", RichLog)
        log.write(f"[b yellow]⏸ HANDOFF[/] {message}")
        if url:
            log.write(f"[underline cyan]{url}[/]")

    # -- readiness board ---------------------------------------------------- #
    def _board_text(self) -> str:
        counts = dict.fromkeys(Status, 0)
        for r in self._results:
            counts[r.status] += 1
        order = [Status.PASS, Status.WARN, Status.FAIL, Status.SKIP, Status.ERROR]
        segs = []
        for s in order:
            n = counts[s]
            if n:
                segs.append(f"[{_STATUS_CLASS[s]}]{_STATUS_ICON[s]} {n} {s.value}[/]")
        body = "  ·  ".join(segs) if segs else "[dim]no results yet — pick an op and press Enter[/]"
        elapsed = format_duration((datetime.now(UTC) - self._started_at).total_seconds())
        total = len(self._results)
        return f" [b]Readiness[/]   {body}    [dim]│ {total} result(s) · session {elapsed}[/]"

    # -- actions ------------------------------------------------------------ #
    def action_focus_main(self) -> None:
        self.query_one("#results", DataTable).focus()

    def action_focus_sidebar(self) -> None:
        self.query_one(Tree).focus()

    def action_toggle_maximize(self) -> None:
        """Zoom the focused panel to full screen (and back)."""
        if self.screen.maximized is not None:
            self.screen.minimize()
        elif self.focused is not None:
            self.screen.maximize(self.focused)

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_toggle_dark(self) -> None:
        self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"


def run_tui() -> None:
    """Entry point used by ``exodia tui``."""
    ExodiaTUI().run()
