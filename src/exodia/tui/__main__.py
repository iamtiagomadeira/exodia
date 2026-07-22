"""Enable ``python -m exodia.tui`` to launch the cockpit."""

from __future__ import annotations

from .app import run_tui

if __name__ == "__main__":
    run_tui()
