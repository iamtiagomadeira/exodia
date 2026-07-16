"""Tests for report rendering (HTML/Markdown/JSON) and the live dashboard."""

from __future__ import annotations

import io
import json

from rich.console import Console

from exodia.core.dashboard import LiveDashboard
from exodia.core.report import (
    RunMeta,
    load_artifact,
    render_html,
    render_markdown,
    render_run_json,
    save_artifact,
    status_counts,
)
from exodia.core.result import Result, Status
from exodia.core.runner import run_checks


def _mixed_results() -> list[Result]:
    return [
        Result.ok("core.free-space", "42 GB free"),
        Result.warn("hana.version", "minor version drift"),
        Result.fail(
            "hana.recover",
            "log backup 1247 missing",
            cause="A required log backup is not available in the catalog.",
            fix=["Locate the missing log backup file", "Restore from the archive location"],
            sap_note="1642148",
        ),
        Result.skip("pipo.postcopy", "skipped via config"),
    ]


def _meta() -> RunMeta:
    return RunMeta(
        title="Migration Prepare — PRD",
        host="sapprd01",
        sid="PRD",
        db_type="hana",
        system_type="abap",
        source="/backup/prd",
        target="sapprd02",
        dry_run=True,
        exodia_version="0.1.0",
    )


# --------------------------------------------------------------------------- #
# Markdown                                                                    #
# --------------------------------------------------------------------------- #
def test_markdown_has_summary_and_table() -> None:
    md = render_markdown(_mixed_results(), _meta())
    assert "# Migration Prepare — PRD" in md
    assert "1 pass" in md
    assert "1 warn" in md
    assert "1 fail" in md
    # Table header + a data row.
    assert "| Check / Phase | Status | Summary |" in md
    assert "| hana.recover | FAIL |" in md


def test_markdown_includes_remediation() -> None:
    md = render_markdown(_mixed_results(), _meta())
    assert "## Remediation" in md
    assert "### hana.recover" in md
    assert "log backup" in md
    assert "SAP Note:** 1642148" in md
    assert "Restore from the archive location" in md


def test_markdown_metadata_present() -> None:
    md = render_markdown(_mixed_results(), _meta())
    assert "sapprd01" in md
    assert "PRD" in md
    assert "hana" in md
    assert "dry-run" in md


# --------------------------------------------------------------------------- #
# HTML                                                                        #
# --------------------------------------------------------------------------- #
def test_html_is_self_contained() -> None:
    html = render_html(_mixed_results(), _meta())
    assert "<!DOCTYPE html>" in html
    assert "<style>" in html
    # No external resources of any kind.
    assert "http://" not in html
    assert "https://" not in html
    assert "src=" not in html
    assert "<link" not in html
    assert "<script" not in html


def test_html_contains_results_and_badges() -> None:
    html = render_html(_mixed_results(), _meta())
    assert "hana.recover" in html
    assert "FAIL" in html
    assert "PASS" in html
    assert 'class="badge"' in html
    # Metadata rendered.
    assert "sapprd01" in html
    assert "Migration Prepare — PRD" in html


def test_html_shows_remediation_for_failures() -> None:
    html = render_html(_mixed_results(), _meta())
    assert "Remediation" in html
    assert "1642148" in html
    assert "Restore from the archive location" in html


def test_html_escapes_untrusted_text() -> None:
    results = [Result.fail("x.evil", "<script>alert(1)</script>", cause="a & b <c>")]
    html = render_html(results, _meta())
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b &lt;c&gt;" in html


# --------------------------------------------------------------------------- #
# Artefact round-trip                                                         #
# --------------------------------------------------------------------------- #
def test_run_json_roundtrip(tmp_path) -> None:
    results = _mixed_results()
    meta = _meta()
    path = tmp_path / "run.json"
    save_artifact(path, results, meta)

    loaded_results, loaded_meta = load_artifact(path)
    assert len(loaded_results) == len(results)
    assert loaded_results[2].name == "hana.recover"
    assert loaded_results[2].sap_note == "1642148"
    assert loaded_meta.sid == "PRD"
    assert loaded_meta.host == "sapprd01"


def test_load_bare_results_list(tmp_path) -> None:
    """A file that is just a JSON list of results (from `run --json` piped)."""
    results = _mixed_results()
    path = tmp_path / "bare.json"
    path.write_text(
        json.dumps([r.model_dump(mode="json") for r in results], default=str), encoding="utf-8"
    )
    loaded_results, loaded_meta = load_artifact(path)
    assert len(loaded_results) == len(results)
    assert isinstance(loaded_meta, RunMeta)


def test_render_run_json_has_envelope() -> None:
    payload = json.loads(render_run_json(_mixed_results(), _meta()))
    assert "meta" in payload
    assert "results" in payload
    assert payload["meta"]["sid"] == "PRD"


def test_status_counts() -> None:
    counts = status_counts(_mixed_results())
    assert counts[Status.PASS] == 1
    assert counts[Status.WARN] == 1
    assert counts[Status.FAIL] == 1
    assert counts[Status.SKIP] == 1
    assert counts[Status.ERROR] == 0


# --------------------------------------------------------------------------- #
# Live dashboard — non-TTY fallback                                           #
# --------------------------------------------------------------------------- #
def test_dashboard_non_tty_fallback_linear_output() -> None:
    """On a non-TTY console the dashboard must emit linear output and not blow up."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    dash = LiveDashboard("Test run", console)
    assert dash._is_tty is False

    with dash.session():
        dash.on_start("check.a")
        dash.on_result("check.a", Result.ok("check.a", "all good"))
        dash.on_start("check.b")
        dash.on_result("check.b", Result.fail("check.b", "boom"))

    out = buf.getvalue()
    assert "check.a" in out
    assert "check.b" in out
    assert "PASS" in out
    assert "FAIL" in out


def test_dashboard_hooks_wire_into_runner() -> None:
    """The dashboard hooks integrate with the runner without breaking it."""
    from exodia.core.context import Context
    from exodia.core.registry import registry

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    dash = LiveDashboard("Free space", console)

    ctx = Context(params={"path": "/", "min_gb": 0})
    check = registry.get_check("core.free-space")
    assert check is not None
    with dash.session():
        results = run_checks([check()], ctx, on_start=dash.on_start, on_result=dash.on_result)

    assert len(results) == 1
    assert results[0].status is Status.PASS
    assert "core.free-space" in buf.getvalue()


# --------------------------------------------------------------------------- #
# Textual app — import/instantiate only (skip if extra missing)              #
# --------------------------------------------------------------------------- #
def test_textual_app_imports_and_instantiates() -> None:
    import importlib.util

    if importlib.util.find_spec("textual") is None:
        import pytest

        pytest.skip("textual extra not installed")

    from exodia.tui import DashboardApp

    def pipeline(on_start, on_result):  # noqa: ANN001
        on_start("x")
        r = Result.ok("x", "done")
        on_result("x", r)
        return [r]

    app = DashboardApp("Test", pipeline)
    assert app is not None
