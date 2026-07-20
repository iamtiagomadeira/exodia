"""Tests for the migration timing / UI features.

Covers the audit clock requirement: exact start, end and duration must be
capturable at every layer — Result, runner/base, evidence bundle, history —
plus the monitor's live stopwatch and progress bar.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from rich.console import Console

from exodia.core.base import Action, Check
from exodia.core.context import Context
from exodia.core.evidence import EvidenceBundle, list_bundles
from exodia.core.monitor import RichMonitor
from exodia.core.result import Result, format_duration
from exodia.core.runner import run_action, run_checks

# -- format_duration ------------------------------------------------------- #


def test_format_duration_none() -> None:
    assert format_duration(None) == "—"


def test_format_duration_subsecond() -> None:
    assert format_duration(0.85) == "850ms"


def test_format_duration_seconds() -> None:
    assert format_duration(42) == "42s"


def test_format_duration_minutes() -> None:
    assert format_duration(125) == "2m 05s"


def test_format_duration_hours() -> None:
    assert format_duration(2 * 3600 + 14 * 60 + 8) == "2h 14m 08s"


# -- Result.stamp_timing --------------------------------------------------- #


def test_stamp_timing_sets_span_and_duration() -> None:
    start = datetime(2026, 7, 20, 14, 0, 0, tzinfo=UTC)
    end = start + timedelta(minutes=3, seconds=30)
    r = Result.ok("t", "done").stamp_timing(start, end)
    assert r.started_at == start
    assert r.ended_at == end
    assert r.duration_seconds == 210.0
    assert r.duration_str == "3m 30s"


def test_stamp_timing_never_negative() -> None:
    start = datetime(2026, 7, 20, 14, 0, 0, tzinfo=UTC)
    end = start - timedelta(seconds=5)  # clock skew
    r = Result.ok("t").stamp_timing(start, end)
    assert r.duration_seconds == 0.0


# -- runner / base timing -------------------------------------------------- #


class _SlowCheck(Check):
    name = "t.slow"
    description = "sleeps a touch"

    def run(self, ctx: Context) -> Result:
        return Result.ok(self.name, "fine")


def test_check_execute_stamps_timing() -> None:
    ctx = Context(sid="ABC")
    results = run_checks([_SlowCheck()], ctx)
    assert len(results) == 1
    r = results[0]
    assert r.started_at is not None
    assert r.ended_at is not None
    assert r.duration_seconds is not None
    assert r.ended_at >= r.started_at


class _NoopAction(Action):
    name = "t.noop"
    description = "does nothing"

    def dry_run(self, ctx: Context) -> Result:
        return Result.ok(f"{self.name}.dry-run", "would do nothing")

    def execute(self, ctx: Context) -> Result:
        return Result.ok(f"{self.name}.execute", "did nothing")

    def verify(self, ctx: Context) -> Result:
        return Result.ok(f"{self.name}.verify", "verified")


def test_action_phases_are_timed() -> None:
    ctx = Context(sid="ABC", dry_run=False, assume_yes=True)
    results = run_action(_NoopAction(), [], ctx)
    # every phase result carries a timing stamp
    assert all(r.started_at is not None and r.ended_at is not None for r in results)
    assert any(r.name.endswith(".execute") for r in results)


# -- evidence timing ------------------------------------------------------- #


def test_bundle_records_operation_timing(tmp_path: Path) -> None:
    ctx = Context(sid="PRD")
    b = EvidenceBundle("system-copy.tenant-copy.hana", ctx, root=tmp_path)
    b.open()
    start = datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC)
    r1 = Result.ok("a", "x").stamp_timing(start, start + timedelta(minutes=5))
    r2 = Result.ok("b", "y").stamp_timing(
        start + timedelta(minutes=5), start + timedelta(hours=2)
    )
    b.add_results([r1, r2])
    bundle_dir = b.close()

    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    assert manifest["operation_started"] == start.isoformat()
    assert manifest["operation_ended"] == (start + timedelta(hours=2)).isoformat()
    assert manifest["duration_seconds"] == 7200.0
    assert manifest["duration_str"] == "2h 00m 00s"


def test_report_md_has_started_ended_duration(tmp_path: Path) -> None:
    ctx = Context(sid="PRD")
    b = EvidenceBundle("system-copy.hsr", ctx, root=tmp_path)
    b.open()
    start = datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC)
    b.add_results([Result.ok("a", "x").stamp_timing(start, start + timedelta(minutes=90))])
    bundle_dir = b.close()
    md = (bundle_dir / "report.md").read_text()
    assert "**Started (UTC):**" in md
    assert "**Ended (UTC):**" in md
    assert "**Duration:** 1h 30m 00s" in md
    assert "| Duration |" in md  # results table has a duration column


def test_run_jsonl_carries_result_timing(tmp_path: Path) -> None:
    ctx = Context(sid="PRD")
    b = EvidenceBundle("system-copy.hsr", ctx, root=tmp_path)
    b.open()
    start = datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC)
    b.add_results([Result.ok("a", "x").stamp_timing(start, start + timedelta(seconds=30))])
    b.close()
    events = [
        json.loads(line)
        for line in (b.dir / "run.jsonl").read_text().splitlines()
        if line.strip()
    ]
    result_events = [e for e in events if e.get("kind") == "result"]
    assert result_events
    assert result_events[0]["duration_seconds"] == 30.0
    assert result_events[0]["started_at"] == start.isoformat()


# -- history / list_bundles ------------------------------------------------ #


def test_list_bundles_orders_newest_first(tmp_path: Path) -> None:
    for i, sid in enumerate(["AAA", "BBB"]):
        ctx = Context(sid=sid)
        b = EvidenceBundle("system-copy.tenant-copy.hana", ctx, root=tmp_path)
        b.open()
        start = datetime(2026, 7, 20, 10 + i, 0, 0, tzinfo=UTC)
        b.add_results([Result.ok("a", "x").stamp_timing(start, start + timedelta(minutes=10))])
        b.close()
    rows = list_bundles(tmp_path)
    assert len(rows) == 2
    # newest (BBB, started 11:00) first
    assert rows[0]["sid"] == "BBB"
    assert rows[0]["duration_str"] == "10m 00s"
    assert rows[1]["sid"] == "AAA"


def test_list_bundles_empty_root(tmp_path: Path) -> None:
    assert list_bundles(tmp_path / "does-not-exist") == []


# -- monitor: stopwatch + progress ---------------------------------------- #


def _console() -> Console:
    return Console(file=StringIO(), width=100, force_terminal=True)


def test_monitor_renders_start_and_elapsed() -> None:
    console = _console()
    m = RichMonitor("SWPM restore", console=console)
    with m:
        m.phase("execute")
    out = console.file.getvalue()  # type: ignore[attr-defined]
    assert "started" in out
    assert "elapsed" in out


def test_monitor_progress_clamps_and_renders() -> None:
    console = _console()
    m = RichMonitor("restore", console=console)
    with m:
        m.phase("execute")
        m.progress(150, "recovering")  # clamps to 100
        assert m._percent == 100.0
        m.progress(-5)
        assert m._percent == 0.0
        m.progress(None)
        assert m._percent is None


def test_new_phase_resets_progress() -> None:
    m = RichMonitor("t", console=_console())
    m.progress(50)
    assert m._percent == 50.0
    m.phase("next")
    assert m._percent is None
