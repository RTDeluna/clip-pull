import asyncio
import logging

logger = logging.getLogger("clippull")

_background_tasks: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
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


def track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
