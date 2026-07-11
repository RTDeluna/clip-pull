import asyncio
import logging

from background_tasks import track_task, _background_tasks


def test_track_task_holds_reference_until_done():
    async def run():
        async def noop():
            await asyncio.sleep(0.05)

        task = asyncio.create_task(noop())
        track_task(task)
        assert task in _background_tasks
        await task
        assert task not in _background_tasks

    asyncio.run(run())


def test_track_task_logs_unhandled_exceptions(caplog):
    async def run():
        async def boom():
            raise RuntimeError("background task exploded")

        task = asyncio.create_task(boom())
        track_task(task)
        with caplog.at_level(logging.ERROR, logger="clippull"):
            try:
                await task
            except RuntimeError:
                pass  # awaiting re-raises it here; track_task's callback still ran first
            await asyncio.sleep(0)  # let the done_callback run

    asyncio.run(run())
    assert "Unhandled exception in background task" in caplog.text
    assert "background task exploded" in caplog.text


def test_track_task_does_not_log_for_cancelled_tasks(caplog):
    async def run():
        async def sleeps_forever():
            await asyncio.sleep(10)

        task = asyncio.create_task(sleeps_forever())
        track_task(task)
        await asyncio.sleep(0)
        task.cancel()
        with caplog.at_level(logging.ERROR, logger="clippull"):
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)

    asyncio.run(run())
    assert "Unhandled exception in background task" not in caplog.text
