"""Tests for the Context config-file schema (TIA-55)."""

from __future__ import annotations

from pathlib import Path

import pytest

from exodia.core.context import ConfigError, Context

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_shipped_example_config_is_valid() -> None:
    """The committed exodia.config.yaml must load cleanly."""
    example = REPO_ROOT / "exodia.config.yaml"
    assert example.is_file(), "example config file is missing"
    ctx = Context.from_file(example)
    assert ctx.dry_run is True


def test_from_file_valid(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        "db_type: hana\nsid: prd\nsystem_type: abap\nport: 2200\n",
    )
    ctx = Context.from_file(cfg)
    assert ctx.db_type == "hana"
    assert ctx.sid == "PRD"  # normalised to upper-case
    assert ctx.system_type == "abap"
    assert ctx.port == 2200


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "hostt: typo.example.com\n")  # typo: hostt
    with pytest.raises(ConfigError) as exc:
        Context.from_file(cfg)
    assert "hostt" in str(exc.value)


def test_invalid_db_type_is_rejected(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "db_type: postgres\n")  # not a supported platform
    with pytest.raises(ConfigError) as exc:
        Context.from_file(cfg)
    assert "db_type" in str(exc.value)


def test_sid_is_normalised_to_upper(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "sid: prd\n")
    assert Context.from_file(cfg).sid == "PRD"


def test_sid_deep_validation_is_left_to_the_check(tmp_path: Path) -> None:
    # The schema normalises but does NOT reject odd SIDs — the dedicated
    # *.sid-instance-sanity check owns deep validation against the live system.
    cfg = _write(tmp_path, "sid: TOOLONG\n")
    assert Context.from_file(cfg).sid == "TOOLONG"


def test_out_of_range_port_is_rejected(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "port: 99999\n")
    with pytest.raises(ConfigError) as exc:
        Context.from_file(cfg)
    assert "port" in str(exc.value)


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        Context.from_file(tmp_path / "nope.yaml")


def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "db_type: [unclosed\n")
    with pytest.raises(ConfigError):
        Context.from_file(cfg)


def test_non_mapping_top_level_raises(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="mapping"):
        Context.from_file(cfg)
