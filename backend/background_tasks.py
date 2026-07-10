import asyncio

_background_tasks: set[asyncio.Task] = set()


def track_task(task: asyncio.Task) -> None:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
