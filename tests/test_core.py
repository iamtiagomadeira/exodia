"""Smoke tests for the Exodia core — real execution, no mocks needed for these."""

from __future__ import annotations

from exodia.core import Context, Result, Status
from exodia.core.knowledge import enrich, lookup
from exodia.core.registry import registry
from exodia.core.report import exit_code, worst_status
from exodia.core.runner import run_checks


def test_result_helpers() -> None:
    assert Result.ok("x").status is Status.PASS
    assert Result.fail("x", "bad").status.is_blocking
    assert not Result.warn("x", "meh").status.is_blocking


def test_registry_discovers_free_space() -> None:
    checks = registry.checks()
    assert "core.free-space" in checks


def test_free_space_check_runs_locally() -> None:
    """Runs the real df-based check against the local root filesystem."""
    ctx = Context(params={"path": "/", "min_gb": 0})  # 0 GB threshold always passes
    check = registry.get_check("core.free-space")
    assert check is not None
    results = run_checks([check()], ctx)
    assert len(results) == 1
    assert results[0].status is Status.PASS
    assert results[0].data["avail_gb"] >= 0


def test_free_space_blocking_fail_stops_pipeline() -> None:
    ctx = Context(params={"path": "/", "min_gb": 10**9})  # impossible threshold
    check = registry.get_check("core.free-space")
    assert check is not None
    results = run_checks([check()], ctx)
    assert results[0].status is Status.FAIL


def test_kb_lookup_hana_log_backup() -> None:
    entry = lookup("recovery could not be completed: log backup 1247 missing")
    assert entry is not None
    assert entry.sap_note == "1642148"


def test_kb_enrich_attaches_fix() -> None:
    r = Result.fail("hana.recover", "log backup 1247 missing")
    enrich(r)
    assert r.sap_note == "1642148"
    assert r.fix


def test_exit_code_and_worst_status() -> None:
    good = [Result.ok("a"), Result.warn("b", "m")]
    bad = [Result.ok("a"), Result.fail("b", "x")]
    assert exit_code(good) == 0
    assert exit_code(bad) == 1
    assert worst_status(bad) is Status.FAIL
