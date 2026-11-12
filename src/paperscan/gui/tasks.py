"""Background tasks for the GUI.

Long work (detection, rendering, OCR, AI, adb) runs on Qt's global thread pool.
Tasks receive value snapshots, never widgets, and report back through signals,
which Qt delivers on the main thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import CancelledError
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, SignalInstance

log = logging.getLogger(__name__)

#: Tasks whose results have not been delivered yet. Holding them keeps their
#: signal objects alive until the queued signal reaches the main thread.
_running: set[Task] = set()


class TaskSignals(QObject):
    """Signals of one task (QRunnable cannot own signals itself)."""

    succeeded = Signal(object)
    failed = Signal(object)  # the raised Exception
    progress = Signal(str, float)


class Task(QRunnable):
    """Run ``work(report_progress)`` in the thread pool and emit its result."""

    def __init__(self, work: Callable[[Callable[[str, float], None]], Any]) -> None:
        """Wrap ``work``; it receives a progress callback ``(message, fraction)``."""
        super().__init__()
        self.signals = TaskSignals()
        self._work: Callable[[Callable[[str, float], None]], Any] | None = work

    def run(self) -> None:
        """Execute the work and emit ``succeeded`` or ``failed``.

        The work is dropped first: Qt keeps the finished runnable, and with it anything
        the work captured (often whole images) would stay in memory.
        """
        work, self._work = self._work, None
        if work is None:
            return
        try:
            result = work(self.signals.progress.emit)
        except CancelledError as exc:  # the user stopped it: worth a line, not a traceback
            log.info("Background task cancelled: %s", exc)
            self._emit(self.signals.failed, exc)
            return
        except Exception as exc:
            log.exception("Background task failed: %s", exc)
            self._emit(self.signals.failed, exc)
            return
        self._emit(self.signals.succeeded, result)

    @staticmethod
    def _emit(signal: SignalInstance, value: object) -> None:
        """Deliver the outcome; if the app is shutting down and its receivers are gone, drop it."""
        try:
            signal.emit(value)
        except RuntimeError as exc:
            log.debug("Task result dropped during shutdown: %s", exc)


def start_task(
    work: Callable[[Callable[[str, float], None]], Any],
    on_success: Callable[[Any], None],
    on_failure: Callable[[Exception], None],
    on_progress: Callable[[str, float], None] | None = None,
) -> Task:
    """Start ``work`` in the background and connect its outcome handlers."""
    task = Task(work)
    task.setAutoDelete(False)
    _running.add(task)
    task.signals.succeeded.connect(on_success)
    task.signals.failed.connect(on_failure)
    task.signals.succeeded.connect(lambda _result: _release(task))
    task.signals.failed.connect(lambda _error: _release(task))
    if on_progress is not None:
        task.signals.progress.connect(on_progress)
    QThreadPool.globalInstance().start(task)
    return task


def _release(task: Task) -> None:
    """Forget a task whose outcome was delivered.

    Deleting its signal object drops the connected handlers, which may hold the
    caller's data and refer back to the task.
    """
    _running.discard(task)
    task.signals.deleteLater()
