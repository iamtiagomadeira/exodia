"""Report rendering — turn Results into human tables, machine JSON, or
standalone HTML/Markdown artefacts for clients and managers."""

from __future__ import annotations

import html
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .result import Result, Status

_STYLE = {
    Status.PASS: "green",
    Status.WARN: "yellow",
    Status.FAIL: "red",
    Status.SKIP: "dim",
    Status.ERROR: "bold red",
}
_ICON = {
    Status.PASS: "✅",
    Status.WARN: "⚠️ ",
    Status.FAIL: "❌",
    Status.SKIP: "⏭️ ",
    Status.ERROR: "💥",
}

# Sober, SAP-friendly palette for the HTML artefact (no garish emojis).
_HTML_BADGE = {
    Status.PASS: ("PASS", "#1f7a3d", "#e6f4ea"),
    Status.WARN: ("WARN", "#8a6100", "#fdf3d8"),
    Status.FAIL: ("FAIL", "#b3261e", "#fce8e6"),
    Status.SKIP: ("SKIP", "#5f6368", "#f1f3f4"),
    Status.ERROR: ("ERROR", "#8c1d18", "#f9dedc"),
}


@dataclass
class RunMeta:
    """Metadata describing a run — surfaced in report headers."""

    title: str = "Exodia Run"
    host: str | None = None
    sid: str | None = None
    db_type: str | None = None
    source: str | None = None
    target: str | None = None
    system_type: str | None = None
    dry_run: bool = True
    exodia_version: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


# --------------------------------------------------------------------------- #
# Rich table (interactive terminal)                                           #
# --------------------------------------------------------------------------- #
def render_table(results: list[Result], title: str, console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title=title, expand=True)
    table.add_column("", width=3)
    table.add_column("Check / Phase", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Summary")
    for r in results:
        table.add_row(
            _ICON.get(r.status, "?"),
            r.name,
            f"[{_STYLE[r.status]}]{r.status.value.upper()}[/]",
            r.summary,
        )
    console.print(table)

    # Remediation panels for anything that failed and carries KB guidance.
    for r in results:
        if r.status.is_blocking and (r.cause or r.fix):
            body = ""
            if r.cause:
                body += f"[bold]Cause:[/] {r.cause}\n"
            if r.fix:
                body += "[bold]Fix:[/]\n" + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(r.fix))
            if r.sap_note:
                body += f"\n[bold]SAP Note:[/] {r.sap_note}"
            console.print(Panel(body, title=f"🔧 {r.name}", border_style="red"))


def render_json(results: list[Result]) -> str:
    return json.dumps([r.model_dump(mode="json") for r in results], indent=2, default=str)


def worst_status(results: list[Result]) -> Status:
    order = [Status.PASS, Status.SKIP, Status.WARN, Status.FAIL, Status.ERROR]
    worst = Status.PASS
    for r in results:
        if order.index(r.status) > order.index(worst):
            worst = r.status
    return worst


def exit_code(results: list[Result]) -> int:
    """0 if nothing blocking, 1 otherwise — for CI/automation."""
    return 1 if any(r.status.is_blocking for r in results) else 0


def status_counts(results: list[Result]) -> dict[Status, int]:
    """Count results per status (all five statuses present, defaulting to 0)."""
    counter = Counter(r.status for r in results)
    return {s: counter.get(s, 0) for s in Status}


# --------------------------------------------------------------------------- #
# Run artefact persistence (.exodia/last-run.json and --out)                  #
# --------------------------------------------------------------------------- #
def render_run_json(results: list[Result], meta: RunMeta) -> str:
    """Serialise a full run (meta + results) for later `exodia report`."""
    payload = {
        "meta": asdict(meta),
        "results": [r.model_dump(mode="json") for r in results],
    }
    return json.dumps(payload, indent=2, default=str)


def save_artifact(path: str | Path, results: list[Result], meta: RunMeta) -> Path:
    """Write a run artefact to disk, creating parent directories."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_run_json(results, meta), encoding="utf-8")
    return target


def load_artifact(path: str | Path) -> tuple[list[Result], RunMeta]:
    """Load a run artefact. Accepts both the meta+results envelope and a
    bare list of results (as produced by `exodia run --json` piped to a file)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "results" in raw:
        meta_data = raw.get("meta") or {}
        results_data = raw["results"]
        meta = RunMeta(**{k: v for k, v in meta_data.items() if k in RunMeta.__dataclass_fields__})
    else:
        results_data = raw
        meta = RunMeta()
    results = [Result.model_validate(item) for item in results_data]
    return results, meta


# --------------------------------------------------------------------------- #
# Markdown artefact                                                           #
# --------------------------------------------------------------------------- #
def render_markdown(results: list[Result], meta: RunMeta) -> str:
    counts = status_counts(results)
    lines: list[str] = []
    lines.append(f"# {meta.title}")
    lines.append("")
    lines.append(
        f"**Result:** {worst_status(results).value.upper()} · "
        f"{counts[Status.PASS]} pass · {counts[Status.WARN]} warn · "
        f"{counts[Status.FAIL]} fail · {counts[Status.SKIP]} skip · "
        f"{counts[Status.ERROR]} error"
    )
    lines.append("")

    # Metadata block.
    meta_rows = [
        ("Timestamp", meta.timestamp),
        ("Host", meta.host or "local"),
        ("SID", meta.sid or "—"),
        ("DB type", meta.db_type or "—"),
        ("System type", meta.system_type or "—"),
        ("Source", meta.source or "—"),
        ("Target", meta.target or "—"),
        ("Mode", "dry-run" if meta.dry_run else "execute"),
        ("Exodia", meta.exodia_version or "—"),
    ]
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    for label, value in meta_rows:
        lines.append(f"| {label} | {value} |")
    lines.append("")

    # Results table.
    lines.append("## Results")
    lines.append("")
    lines.append("| Check / Phase | Status | Summary |")
    lines.append("| --- | --- | --- |")
    for r in results:
        summary = r.summary.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.name} | {r.status.value.upper()} | {summary} |")
    lines.append("")

    # Failure details with remediation.
    failures = [r for r in results if r.status.is_blocking and (r.cause or r.fix or r.sap_note)]
    if failures:
        lines.append("## Remediation")
        lines.append("")
        for r in failures:
            lines.append(f"### {r.name}")
            lines.append("")
            if r.cause:
                lines.append(f"- **Cause:** {r.cause}")
            if r.fix:
                lines.append("- **Fix:**")
                for i, step in enumerate(r.fix, 1):
                    lines.append(f"  {i}. {step}")
            if r.sap_note:
                lines.append(f"- **SAP Note:** {r.sap_note}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# HTML artefact (self-contained, inline CSS, client/manager-facing)           #
# --------------------------------------------------------------------------- #
def _badge(status: Status) -> str:
    label, fg, bg = _HTML_BADGE[status]
    return (
        f'<span class="badge" style="color:{fg};background:{bg};'
        f'border:1px solid {fg}33">{label}</span>'
    )


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def render_html(results: list[Result], meta: RunMeta) -> str:
    counts = status_counts(results)
    worst = worst_status(results)

    summary_cards = "".join(
        f'<div class="card"><div class="num">{counts[s]}</div>'
        f'<div class="lbl">{_HTML_BADGE[s][0]}</div></div>'
        for s in (Status.PASS, Status.WARN, Status.FAIL, Status.SKIP, Status.ERROR)
    )

    meta_rows = [
        ("Timestamp", meta.timestamp),
        ("Host", meta.host or "local"),
        ("SID", meta.sid or "—"),
        ("DB type", meta.db_type or "—"),
        ("System type", meta.system_type or "—"),
        ("Source", meta.source or "—"),
        ("Target", meta.target or "—"),
        ("Mode", "dry-run" if meta.dry_run else "execute"),
        ("Exodia", meta.exodia_version or "—"),
    ]
    meta_html = "".join(
        f"<tr><th>{_esc(label)}</th><td>{_esc(str(value))}</td></tr>" for label, value in meta_rows
    )

    result_rows = "".join(
        f"<tr><td class='name'>{_esc(r.name)}</td>"
        f"<td>{_badge(r.status)}</td>"
        f"<td>{_esc(r.summary)}</td></tr>"
        for r in results
    )

    failures = [r for r in results if r.status.is_blocking and (r.cause or r.fix or r.sap_note)]
    remediation_html = ""
    if failures:
        blocks = []
        for r in failures:
            parts = [f"<h3>{_esc(r.name)}</h3>"]
            if r.cause:
                parts.append(f"<p><strong>Cause:</strong> {_esc(r.cause)}</p>")
            if r.fix:
                steps = "".join(f"<li>{_esc(s)}</li>" for s in r.fix)
                parts.append(f"<p><strong>Fix:</strong></p><ol>{steps}</ol>")
            if r.sap_note:
                parts.append(f"<p><strong>SAP Note:</strong> {_esc(r.sap_note)}</p>")
            blocks.append(f"<div class='remediation'>{''.join(parts)}</div>")
        remediation_html = (
            "<section><h2>Remediation</h2>" + "".join(blocks) + "</section>"
        )

    worst_label, worst_fg, worst_bg = _HTML_BADGE[worst]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(meta.title)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #202124; background: #f5f6f8; margin: 0; padding: 32px;
    line-height: 1.5; font-size: 15px;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; background: #fff;
    border: 1px solid #e0e0e0; border-radius: 10px; overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  header {{ padding: 28px 32px; border-bottom: 1px solid #eceff1; }}
  header h1 {{ margin: 0 0 6px; font-size: 22px; font-weight: 600; }}
  .verdict {{ display: inline-block; padding: 3px 12px; border-radius: 999px;
    font-size: 13px; font-weight: 600; color: {worst_fg}; background: {worst_bg};
    border: 1px solid {worst_fg}33; }}
  section {{ padding: 24px 32px; }}
  section + section {{ border-top: 1px solid #eceff1; }}
  h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .04em;
    color: #5f6368; margin: 0 0 16px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .card {{ flex: 1; min-width: 90px; text-align: center; padding: 16px 8px;
    background: #fafafa; border: 1px solid #eceff1; border-radius: 8px; }}
  .card .num {{ font-size: 26px; font-weight: 700; }}
  .card .lbl {{ font-size: 11px; letter-spacing: .06em; color: #5f6368;
    margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 9px 12px; vertical-align: top; }}
  .meta th {{ width: 150px; color: #5f6368; font-weight: 500; }}
  .meta tr:nth-child(odd) {{ background: #fafafa; }}
  .results th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
    color: #5f6368; border-bottom: 2px solid #eceff1; }}
  .results td {{ border-bottom: 1px solid #f1f3f4; }}
  .results td.name {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px; white-space: nowrap; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px;
    font-size: 12px; font-weight: 600; letter-spacing: .03em; }}
  .remediation {{ background: #fffaf9; border: 1px solid #f5d9d6;
    border-radius: 8px; padding: 12px 18px; margin-bottom: 14px; }}
  .remediation h3 {{ margin: 4px 0 8px; font-size: 14px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  .remediation ol {{ margin: 6px 0 0; padding-left: 22px; }}
  footer {{ padding: 16px 32px; color: #9aa0a6; font-size: 12px;
    border-top: 1px solid #eceff1; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>{_esc(meta.title)}</h1>
      <span class="verdict">{worst_label}</span>
    </header>
    <section>
      <h2>Summary</h2>
      <div class="cards">{summary_cards}</div>
    </section>
    <section>
      <h2>Run metadata</h2>
      <table class="meta"><tbody>{meta_html}</tbody></table>
    </section>
    <section>
      <h2>Results</h2>
      <table class="results">
        <thead><tr><th>Check / Phase</th><th>Status</th><th>Summary</th></tr></thead>
        <tbody>{result_rows}</tbody>
      </table>
    </section>
    {remediation_html}
    <footer>Generated by Exodia {_esc(meta.exodia_version or "")} · {_esc(meta.timestamp)}</footer>
  </div>
</body>
</html>
"""
