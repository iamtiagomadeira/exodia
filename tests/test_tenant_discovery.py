"""Tests for target-tenant discovery + selection logic (no real HANA).

Covers M_DATABASES parsing, SYSTEMDB exclusion, and the resolve-mode decision
(none / single / multiple) that drives the interactive selection.
"""

from __future__ import annotations

from exodia.modules.system_copy.tenant_copy.discovery import (
    TenantInfo,
    _parse_tenants,
    resolve_target_tenant,
    selectable_tenants,
)


def test_parse_tenants_reads_name_and_status() -> None:
    out = '"SYSTEMDB","YES"\n"QAS","YES"\n"DEV","NO"\n2 rows selected'
    ts = _parse_tenants(out)
    assert [t.name for t in ts] == ["SYSTEMDB", "QAS", "DEV"]
    assert ts[1].active_status == "YES"
    assert ts[0].is_system is True
    assert ts[1].is_system is False


def test_systemdb_is_not_selectable() -> None:
    ts = _parse_tenants('"SYSTEMDB","YES"\n"QAS","YES"')
    sel = selectable_tenants(ts)
    assert [t.name for t in sel] == ["QAS"]


def test_resolve_none_when_only_systemdb() -> None:
    ts = _parse_tenants('"SYSTEMDB","YES"')
    mode, candidates = resolve_target_tenant(ts)
    assert mode == "none"
    assert candidates == []


def test_resolve_single_when_one_tenant() -> None:
    ts = _parse_tenants('"SYSTEMDB","YES"\n"QAS","YES"')
    mode, candidates = resolve_target_tenant(ts)
    assert mode == "single"
    assert len(candidates) == 1
    assert candidates[0].name == "QAS"


def test_resolve_multiple_when_several_tenants() -> None:
    ts = _parse_tenants('"SYSTEMDB","YES"\n"QAS","YES"\n"DEV","NO"\n"TST","YES"')
    mode, candidates = resolve_target_tenant(ts)
    assert mode == "multiple"
    assert {t.name for t in candidates} == {"QAS", "DEV", "TST"}


def test_tenant_label_is_human_readable() -> None:
    t = TenantInfo(name="QAS", active_status="YES")
    assert t.label == "QAS (yes)"


def test_empty_list_resolves_none() -> None:
    mode, candidates = resolve_target_tenant([])
    assert mode == "none"
    assert candidates == []
