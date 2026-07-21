"""Tests for hdbuserstore key discovery (no real HANA)."""

from __future__ import annotations

from exodia.core import Context
from exodia.core.shell import CommandResult, Runner
from exodia.modules.system_copy.tenant_copy.hdbkeys import (
    HdbKey,
    discover_hdb_keys,
    parse_hdbuserstore_list,
)

_LIST_OUTPUT = """DATA FILE       : /home/prdadm/.hdb/host/SSFS_HDB.DAT
KEY SRCSYS
  ENV : src-host:30013
  USER: SYSTEM
KEY SRCTEN
  ENV : src-host:30015@PRD
  USER: SAPABAP1
KEY DEFAULT
  ENV : localhost:30015
  USER: SAPABAP1
"""


def test_parse_lists_all_keys_with_metadata() -> None:
    keys = parse_hdbuserstore_list(_LIST_OUTPUT)
    assert [k.name for k in keys] == ["SRCSYS", "SRCTEN", "DEFAULT"]
    assert keys[0].env == "src-host:30013"
    assert keys[0].user == "SYSTEM"
    assert keys[1].env == "src-host:30015@PRD"


def test_key_label_is_human_readable() -> None:
    assert HdbKey("SRCSYS", "src-host:30013", "SYSTEM").label == "SRCSYS  (src-host:30013, SYSTEM)"
    assert HdbKey("BARE").label == "BARE"


def test_parse_empty_output() -> None:
    assert parse_hdbuserstore_list("") == []
    assert parse_hdbuserstore_list("DATA FILE : /x/y.DAT\n") == []


class _Runner(Runner):
    def __init__(self, stdout: str, ok: bool = True) -> None:
        self._stdout = stdout
        self._ok = ok

    def run(self, argv, timeout=300, input_text=None):  # type: ignore[no-untyped-def]
        return CommandResult(argv, 0 if self._ok else 1, self._stdout, "")


def _ctx(runner: Runner) -> Context:
    class _C(Context):
        def runner(self):  # type: ignore[override]
            return runner

    return _C()  # type: ignore[call-arg]


def test_discover_returns_keys_from_runner() -> None:
    keys = discover_hdb_keys(_ctx(_Runner(_LIST_OUTPUT)))
    assert {k.name for k in keys} == {"SRCSYS", "SRCTEN", "DEFAULT"}


def test_discover_empty_when_command_fails() -> None:
    assert discover_hdb_keys(_ctx(_Runner("", ok=False))) == []
