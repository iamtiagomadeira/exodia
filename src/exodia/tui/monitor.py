"""TextualMonitor — the Textual implementation of the core ``Monitor`` protocol.

``core/monitor.py`` defines a ``Monitor`` protocol (start/stop/phase/progress/
log_line/result/handoff) that action ``execute()`` calls into so a long-running
operation streams its progress instead of freezing the screen. The CLI ships a
``RichMonitor``; this is the promised Textual twin (TIA-66).

It is deliberately thin: it does not render anything itself. It forwards each
event to the running :class:`~exodia.tui.app.ExodiaTUI` on the UI thread via
``app.call_from_thread`` (checks/runbooks run in a Textual worker thread, so we
must not touch widgets directly). The app owns the widgets; the monitor is just
the adapter that lets the existing runner/guard code talk to them unchanged.
"""

from __future__ import annotations

import contextlib
from types import TracebackType
from typing import TYPE_CHECKING

from ..core.result import Result

if TYPE_CHECKING:
    from .app import ExodiaTUI


class TextualMonitor:
    """Adapter: forwards ``Monitor`` events to the Textual app on the UI thread.

    Implements the same surface as ``RichMonitor`` so ``run_action`` /
    ``action.set_monitor`` work with zero changes. Because a run happens in a
    Textual worker thread, every widget mutation is marshalled back to the UI
    thread with ``app.call_from_thread``.
    """

    def __init__(self, app: ExodiaTUI, title: str) -> None:
        self._app = app
        self.title = title

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        self._safe(self._app.mon_start, self.title)

    def stop(self) -> None:
        self._safe(self._app.mon_stop)

    def __enter__(self) -> TextualMonitor:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    # -- updates ------------------------------------------------------------ #
    def phase(self, name: str, detail: str = "") -> None:
        self._safe(self._app.mon_phase, name, detail)

    def progress(self, percent: float | None, detail: str = "") -> None:
        self._safe(self._app.mon_progress, percent, detail)

    def log_line(self, line: str) -> None:
        self._safe(self._app.mon_log, line)

    def result(self, result: Result) -> None:
        self._safe(self._app.mon_result, result)

    def handoff(self, message: str, url: str | None = None) -> None:
        self._safe(self._app.mon_handoff, message, url)

    # -- internal ----------------------------------------------------------- #
    def _safe(self, fn, *args) -> None:  # type: ignore[no-untyped-def]
        """Marshal a UI mutation onto the app thread; ignore if app is gone.

        ``call_from_thread`` raises if the app has already exited (e.g. the
        user quit mid-run). A monitor event arriving after shutdown is not an
        error worth crashing the worker for, so it is swallowed.
        """
        # A monitor event arriving after the app exits (user quit mid-run)
        # would make call_from_thread raise; that is not worth crashing the
        # worker thread for, so it is suppressed.
        with contextlib.suppress(Exception):
            self._app.call_from_thread(fn, *args)
