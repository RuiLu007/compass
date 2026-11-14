import asyncio

import pytest
from compass_agent.tools.contracts import PhaseCanceled, ToolRejected
from compass_agent.workflows.script import WorkflowError, execute_script

from .workflow_fixture import PERIODS, SOURCE


@pytest.mark.parametrize("source", [SOURCE, SOURCE.replace("() => agent", "agent")])
def test_runner_chains_and_overlaps_independent_children(source):
    async def run():
        branch_started = asyncio.Event()
        trace = []

        async def dispatch(profile: str, _value: object) -> dict:
            trace.append(profile)
            if profile == "channel_compare":
                return {"status": "ok", "channelCode": "POS", "evidenceRef": "lead"}
            if profile == "product_breakdown":
                await asyncio.wait_for(branch_started.wait(), 2)
            else:
                branch_started.set()
            return {"status": "ok", "evidenceRef": profile}

        result = await execute_script(source, PERIODS, dispatch, remaining=3)
        assert trace[0] == "channel_compare"
        assert set(trace[1:]) == {"product_breakdown", "scope_review"}
        assert result == {
            "status": "ok",
            "evidenceRefs": ["lead", "product_breakdown", "scope_review"],
        }

    asyncio.run(run())


@pytest.mark.parametrize(
    "source",
    [
        "import fs from 'node:fs';",
        "return await import('node:fs');",
        "return process.env;",
        "while (true) {}",
        "return await Promise.all([agent('x', {}), agent('x', {}), agent('x', {})]);",
    ],
)
def test_runner_rejects_ambient_access_timeouts_and_excess_dispatch(source):
    async def run():
        async def dispatch(_profile: str, _value: object) -> dict:
            await asyncio.sleep(5)
            return {}

        with pytest.raises(WorkflowError):
            await asyncio.wait_for(execute_script(source, PERIODS, dispatch, remaining=1), 4)

    asyncio.run(run())


def test_runner_cancellation_waits_for_child_cleanup():
    async def run():
        started = asyncio.Event()
        settled = asyncio.Event()

        async def dispatch(_profile: str, _value: object) -> dict:
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                settled.set()
            return {}

        execution = asyncio.create_task(
            execute_script(
                "return await agent('channel_compare', args);",
                PERIODS,
                dispatch,
                remaining=15,
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        execution.cancel()
        with pytest.raises(PhaseCanceled) as stopped:
            await execution
        assert stopped.value.cleanup_confirmed and settled.is_set()

    asyncio.run(run())


def test_runner_reports_syntax_rejection_before_child_dispatch():
    async def run():
        async def dispatch(_profile, _value):
            pytest.fail("Invalid script dispatched a child")

        with pytest.raises(ToolRejected, match="^invalid_workflow_script$"):
            await execute_script(
                'await agent("sub_agent", args); const value = ;',
                PERIODS,
                dispatch,
                remaining=3,
            )

    asyncio.run(run())
