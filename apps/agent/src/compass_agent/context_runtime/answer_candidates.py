"""Private persistence for submitted answer candidates."""

import asyncio
from uuid import uuid5

from compass_agent.context_runtime.answers import ANSWER
from compass_agent.context_runtime.source_access import save_private
from compass_agent.execution_context import current_execution, execution_path
from compass_agent.runtime.private_state import digest
from compass_agent.tools.contracts import ExecutionReceipt, ToolRejected


async def submit(args, context):
    journal = current_execution.get()
    if journal is None:
        raise ToolRejected("runtime_state_invalid")
    value = ANSWER.validate_python(args).model_dump(mode="json", by_alias=True)
    identifier = str(
        uuid5(journal.run_id, f"answer:{execution_path.get()}:{context.step_id}:{context.call_id}")
    )
    record = {
        "id": identifier,
        "digest": digest(value),
        "candidate": value,
        "executionPath": execution_path.get(),
    }

    def save():
        with journal.store.transaction() as tx:
            _, run = journal.store.lock(tx, journal.run_id)
            journal.store.require_epoch(run, journal.epoch)
            save_private(journal.store, tx, run, f"answer/v2/candidate/{identifier}", record, [])

    await asyncio.to_thread(save)
    return ExecutionReceipt(
        context.call_id,
        "ok",
        "confirmed",
        {
            "status": "candidate_received",
            "candidateId": identifier,
        },
    )
