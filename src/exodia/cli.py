"""Exodia CLI — the router. `exodia list`, `exodia run <name>`, `exodia doctor`."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .core import report
from .core.context import Context
from .core.dashboard import LiveDashboard
from .core.logging import configure
from .core.registry import registry
from .core.report import RunMeta
from .core.runner import run_action, run_checks

app = typer.Typer(
    name="exodia",
    help="Stateless executor for SAP migration operations (HANA/ASE, PI/PO Java).",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# Default location for the last-run artefact, consumed by `exodia report`.
LAST_RUN = Path(".exodia/last-run.json")


def _version_cb(value: bool) -> None:
    if value:
        console.print(f"exodia {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
    _version: bool = typer.Option(
        False, "--version", callback=_version_cb, is_eager=True, help="Show version and exit."
    ),
) -> None:
    configure(verbose=verbose)


@app.command("list")
def list_ops() -> None:
    """List all discovered checks and actions."""
    checks = registry.checks()
    actions = registry.actions()

    ct = Table(title="Checks (read-only)", expand=True)
    ct.add_column("Name", style="cyan")
    ct.add_column("Blocking")
    ct.add_column("Description")
    for name, check_cls in sorted(checks.items()):
        ct.add_row(name, "yes" if check_cls.blocking else "no", check_cls.description)
    console.print(ct)

    at = Table(title="Actions (state-changing)", expand=True)
    at.add_column("Name", style="magenta")
    at.add_column("Requires checks")
    at.add_column("Description")
    for name, action_cls in sorted(actions.items()):
        at.add_row(name, ", ".join(action_cls.requires_checks) or "-", action_cls.description)
    console.print(at)

    if not checks and not actions:
        console.print("[yellow]No operations discovered yet. Modules land under exodia.modules.[/]")


def _build_context(
    host: str | None,
    user: str | None,
    db_type: str | None,
    source: str | None,
    target: str | None,
    dry_run: bool,
    yes: bool,
    config: str | None,
) -> Context:
    if config:
        ctx = Context.from_file(config)
        # CLI flags override file values when provided.
        if host:
            ctx.host = host
        if user:
            ctx.user = user
        if db_type:
            ctx.db_type = db_type
        if source:
            ctx.source = source
        if target:
            ctx.target = target
        ctx.dry_run = dry_run
        ctx.assume_yes = yes
        return ctx
    return Context(
        host=host,
        user=user,
        db_type=db_type,
        source=source,
        target=target,
        dry_run=dry_run,
        assume_yes=yes,
    )


def _meta_from_ctx(ctx: Context, title: str) -> RunMeta:
    return RunMeta(
        title=title,
        host=ctx.host,
        sid=ctx.sid,
        db_type=ctx.db_type,
        source=ctx.source,
        target=ctx.target,
        system_type=ctx.system_type,
        dry_run=ctx.dry_run,
        exodia_version=__version__,
    )


def _resolve_pipeline(name: str, ctx: Context) -> tuple[list, str, str]:
    """Resolve an operation name to (runnable-kind, title). Returns
    ([check_or_action_objs], title, kind) where kind is 'check' or 'action'.

    Exits (code 2) if the name is unknown."""
    check_cls = registry.get_check(name)
    action_cls = registry.get_action(name)
    if check_cls is None and action_cls is None:
        console.print(f"[red]Unknown operation:[/] {name}. Try `exodia list`.")
        raise typer.Exit(2)
    if check_cls is not None:
        return [check_cls()], f"Check: {name}", "check"
    assert action_cls is not None  # nosec B101 - narrowed above
    return [action_cls()], f"Action: {name}" + (" (dry-run)" if ctx.dry_run else ""), "action"


def _prechecks_for(action_obj) -> list:  # type: ignore[no-untyped-def]
    prechecks = []
    for c in action_obj.requires_checks:
        pc_cls = registry.get_check(c)
        if pc_cls is not None:
            prechecks.append(pc_cls())
    return prechecks


@app.command("run")
def run_op(
    name: str = typer.Argument(..., help="Check or action name, e.g. 'hana.free-space'."),
    host: str | None = typer.Option(None, "--host", help="Remote host (omit for local)."),
    user: str | None = typer.Option(None, "--user", help="SSH user for remote host."),
    db_type: str | None = typer.Option(None, "--db-type", help="hana | ase | ..."),
    source: str | None = typer.Option(None, "--source"),
    target: str | None = typer.Option(None, "--target"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Dry-run (default) or execute."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation for actions."),
    config: str | None = typer.Option(None, "--config", help="YAML config (escape hatch)."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
    out: str | None = typer.Option(
        None, "--out", help="Persist the full run (meta + results) to this JSON path."
    ),
    live: bool = typer.Option(
        True, "--live/--no-live", help="Live progress view (auto-falls back to linear on non-TTY)."
    ),
    tui: bool = typer.Option(
        False, "--tui", help="Rich Textual dashboard (requires the 'tui' extra)."
    ),
) -> None:
    """Run a check or a guarded action by name."""
    ctx = _build_context(host, user, db_type, source, target, dry_run, yes, config)
    objs, title, kind = _resolve_pipeline(name, ctx)

    def pipeline(on_start, on_result):  # type: ignore[no-untyped-def]
        if kind == "check":
            return run_checks(objs, ctx, on_start=on_start, on_result=on_result)
        action = objs[0]
        prechecks = _prechecks_for(action)
        return run_action(action, prechecks, ctx, on_start=on_start, on_result=on_result)

    if tui:
        try:
            from .tui import run_dashboard
        except ImportError:
            console.print(
                "[red]The Textual dashboard needs the 'tui' extra:[/] pip install 'exodia[tui]'"
            )
            raise typer.Exit(2) from None
        results = run_dashboard(title, pipeline)
    elif as_json:
        # JSON mode: no live view (would corrupt the machine output).
        results = pipeline(None, None)
    elif live:
        dash = LiveDashboard(title, console)
        with dash.session():
            results = pipeline(dash.on_start, dash.on_result)
    else:
        results = pipeline(None, None)

    meta = _meta_from_ctx(ctx, title)

    # Always persist the last run so `exodia report` can pick it up.
    with contextlib.suppress(OSError):
        report.save_artifact(LAST_RUN, results, meta)  # non-fatal on read-only CWD
    if out:
        report.save_artifact(out, results, meta)

    if as_json:
        console.print_json(report.render_json(results))
    elif not tui:
        report.render_table(results, title, console)

    raise typer.Exit(report.exit_code(results))


@app.command("dashboard")
def dashboard(
    name: str = typer.Argument(..., help="Check or action name to run in the dashboard."),
    host: str | None = typer.Option(None, "--host", help="Remote host (omit for local)."),
    user: str | None = typer.Option(None, "--user", help="SSH user for remote host."),
    db_type: str | None = typer.Option(None, "--db-type", help="hana | ase | ..."),
    source: str | None = typer.Option(None, "--source"),
    target: str | None = typer.Option(None, "--target"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute", help="Dry-run (default) or execute."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation for actions."),
    config: str | None = typer.Option(None, "--config", help="YAML config (escape hatch)."),
) -> None:
    """Run an operation inside the rich Textual dashboard (requires 'tui' extra)."""
    try:
        from .tui import run_dashboard
    except ImportError:
        console.print(
            "[red]The Textual dashboard needs the 'tui' extra:[/] pip install 'exodia[tui]'"
        )
        raise typer.Exit(2) from None

    ctx = _build_context(host, user, db_type, source, target, dry_run, yes, config)
    objs, title, kind = _resolve_pipeline(name, ctx)

    def pipeline(on_start, on_result):  # type: ignore[no-untyped-def]
        if kind == "check":
            return run_checks(objs, ctx, on_start=on_start, on_result=on_result)
        action = objs[0]
        prechecks = _prechecks_for(action)
        return run_action(action, prechecks, ctx, on_start=on_start, on_result=on_result)

    results = run_dashboard(title, pipeline)
    meta = _meta_from_ctx(ctx, title)
    with contextlib.suppress(OSError):
        report.save_artifact(LAST_RUN, results, meta)
    raise typer.Exit(report.exit_code(results))


@app.command("report")
def report_cmd(
    source: str | None = typer.Option(
        None, "--json", "-i", help="Run artefact to read (default: .exodia/last-run.json)."
    ),
    html_out: str | None = typer.Option(None, "--html", help="Write the HTML report here."),
    md_out: str | None = typer.Option(None, "--md", help="Write the Markdown report here."),
    title: str | None = typer.Option(None, "--title", help="Override the report title."),
    stdout_format: str | None = typer.Option(
        None, "--stdout", help="Print to stdout instead of files: 'html' or 'md'."
    ),
) -> None:
    """Generate a standalone HTML + Markdown report from a run artefact.

    Reads the last run from .exodia/last-run.json by default, or a JSON file
    produced by `exodia run --out <path>`."""
    src = Path(source) if source else LAST_RUN
    if not src.exists():
        console.print(
            f"[red]No run artefact at[/] {src}. "
            "Run something first (e.g. `exodia run <name> --out run.json`)."
        )
        raise typer.Exit(2)

    results, meta = report.load_artifact(src)
    if title:
        meta.title = title

    if stdout_format:
        fmt = stdout_format.lower()
        if fmt == "html":
            console.print(report.render_html(results, meta), markup=False)
        elif fmt in ("md", "markdown"):
            console.print(report.render_markdown(results, meta), markup=False)
        else:
            console.print(f"[red]Unknown --stdout format:[/] {stdout_format} (use 'html' or 'md').")
            raise typer.Exit(2)
        return

    html_path = Path(html_out) if html_out else src.with_suffix(".html")
    md_path = Path(md_out) if md_out else src.with_suffix(".md")

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(report.render_html(results, meta), encoding="utf-8")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report.render_markdown(results, meta), encoding="utf-8")

    console.print(f"[green]✅ HTML report:[/] {html_path}")
    console.print(f"[green]✅ Markdown report:[/] {md_path}")


@app.command("doctor")
def doctor() -> None:
    """Self-check: verify Exodia's own setup and discovery."""
    checks = registry.checks()
    actions = registry.actions()
    console.print(f"[green]exodia {__version__}[/]")
    console.print(f"  discovered checks : {len(checks)}")
    console.print(f"  discovered actions: {len(actions)}")
    from .core.knowledge import _load_kb

    console.print(f"  KB error entries  : {len(_load_kb())}")
    console.print("[green]✅ core healthy[/]")


if __name__ == "__main__":
    app()
