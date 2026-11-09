"""Background tasks for the GUI.

Long work (detection, rendering, OCR, AI, adb) runs on Qt's global thread pool.
Tasks receive value snapshots, never widgets, and report back through signals,
which Qt delivers on the main thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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
        self._work = work

    def run(self) -> None:
        """Execute the work and emit ``succeeded`` or ``failed``."""
        try:
            result = self._work(self.signals.progress.emit)
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
    task.signals.succeeded.connect(lambda _result: _running.discard(task))
    task.signals.failed.connect(lambda _error: _running.discard(task))
    if on_progress is not None:
        task.signals.progress.connect(on_progress)
    QThreadPool.globalInstance().start(task)
    return task
