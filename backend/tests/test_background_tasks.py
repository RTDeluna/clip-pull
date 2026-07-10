import asyncio

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
