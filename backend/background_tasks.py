import asyncio
import functools
import logging
from typing import Callable, Optional

logger = logging.getLogger("clippull")

_background_tasks: set[asyncio.Task] = set()


def _on_task_done(
    task: asyncio.Task,
    on_failure: Optional[Callable[[BaseException], None]] = None,
) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # Fire-and-forget tasks (WS broadcasts, batch-complete notifications,
        # the History auto-remove timer, etc.) have nothing else watching
        # them -- without this, a failure here was previously invisible
        # entirely (asyncio's own "Task exception was never retrieved"
        # message only goes to stderr, which no one sees in a packaged app
        # with no console window).
        logger.exception("Unhandled exception in background task", exc_info=exc)
        if on_failure is not None:
            # A caller that can identify the affected queue/history entry can
            # also surface the death to the UI (e.g. a WS error broadcast).
            # Guarded so a raising handler can't itself escape the done
            # callback and resurface as an "Exception in callback" log line.
            try:
                on_failure(exc)
            except Exception:
                logger.exception("track_task on_failure handler raised")


def track_task(
    task: asyncio.Task,
    on_failure: Optional[Callable[[BaseException], None]] = None,
) -> None:
    _background_tasks.add(task)
    if on_failure is None:
        task.add_done_callback(_on_task_done)
    else:
        task.add_done_callback(functools.partial(_on_task_done, on_failure=on_failure))
