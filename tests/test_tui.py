"""Tests for the Exodia TUI — driven headless via Textual's Pilot.

These never open a real terminal: ``App.run_test()`` mounts the app in a
virtual screen so we can assert the grid composes, the tree is populated from
the live registry, selection updates the detail panel, and running a read-only
check streams a result into the table. Textual's async test harness is used.
"""

from __future__ import annotations

import pytest
from textual.widgets import DataTable, RichLog, Static, Tree

from exodia.tui.app import ExodiaTUI

pytestmark = pytest.mark.asyncio


async def test_tui_composes_all_panels() -> None:
    """The grid mounts every named panel and the header wordmark."""
    app = ExodiaTUI()
    async with app.run_test() as pilot:
        # header widget exists (carries the wordmark + counts)
        assert app.query_one("#header", Static)
        # all the key panels exist
        for pid in ("#sidebar", "#detail", "#logpanel", "#resultspanel", "#footer-board"):
            assert app.query(pid)
        # tree, log and results widgets are present
        assert app.query_one(Tree)
        assert app.query_one("#log", RichLog)
        assert app.query_one("#results", DataTable)
        await pilot.pause()


async def test_tree_is_populated_from_registry() -> None:
    """The operations tree reflects the live registry (families/methods/ops)."""
    app = ExodiaTUI()
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        # root has at least one family child
        assert len(tree.root.children) >= 1
        # counts wired from the registry
        assert app._counts["checks"] > 0
        assert app._counts["runbooks"] > 0
        await pilot.pause()


async def test_results_table_has_headers() -> None:
    """The results DataTable is initialised with the expected columns."""
    app = ExodiaTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#results", DataTable)
        assert len(table.columns) == 5


async def test_running_a_check_streams_a_result() -> None:
    """Selecting a check leaf and pressing Enter runs it and lands a row.

    Uses the first discovered check. Runs read-only against localhost with a
    dry-run context, so it is safe in CI (it will PASS/WARN/FAIL/SKIP/ERROR —
    any structured Result is fine; we only assert a row appears).
    """
    app = ExodiaTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        # find the first check leaf in the tree and select it
        target = None
        stack = list(app.query_one(Tree).root.children)
        while stack:
            node = stack.pop(0)
            data = node.data or {}
            if data.get("kind") == "check":
                target = node
                break
            stack.extend(node.children)
        assert target is not None, "no check leaf found in tree"

        # drive the run directly (avoids depending on cursor position)
        app._run_worker(kind="check", name=target.data["name"])
        # wait for the worker thread to finish and marshal results back
        await app.workers.wait_for_complete()
        await pilot.pause()

        table = app.query_one("#results", DataTable)
        assert table.row_count >= 1, "running a check should add at least one result row"


async def test_action_leaf_does_not_execute() -> None:
    """Actions are guarded: selecting one must NOT run it, only notify."""
    app = ExodiaTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        # locate an action leaf
        target = None
        stack = list(app.query_one(Tree).root.children)
        while stack:
            node = stack.pop(0)
            data = node.data or {}
            if data.get("kind") == "action":
                target = node
                break
            stack.extend(node.children)
        if target is None:
            pytest.skip("no action registered")
        table = app.query_one("#results", DataTable)
        before = table.row_count
        # simulate selecting the action node then running
        app.query_one(Tree).select_node(target)
        app.action_run_selected()
        await pilot.pause()
        # no new result rows — actions never execute from the TUI
        assert table.row_count == before
