"""Application ownership of HTTP root executions, including routing and simulation."""

import asyncio
import logging

from compass_agent.models.errors import ModelError


class ExecutionTasks:
    def __init__(self):
        self.tasks = set()
        self.closing = False

    def create(self, coroutine):
        if self.closing:
            coroutine.close()
            raise ModelError("unavailable", 503)
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def close(self):
        self.closing = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=4.5)
            for task in pending:
                task.cancel()
                task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            if pending:
                logging.getLogger(__name__).warning(
                    "Model shutdown left %d owned tasks", len(pending)
                )


def root_task(request, coroutine):
    return request.app.state.model_tasks.create(coroutine)
