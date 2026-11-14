"""Bounded private protocol to the QuickJS workflow worker."""

import asyncio
import json
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from uuid import UUID

from compass_agent.execution_context import current_execution, execution_scope
from compass_agent.tools.contracts import ExecutionError as WorkflowError
from compass_agent.tools.contracts import PhaseCanceled, ResourceUnconfirmed, ToolRejected

FRAME_BYTES = 64 * 1024
RUNNER = Path(__file__).resolve().parents[3] / "workflow-runner" / "run.mjs"


async def execute_script(
    source: str,
    args: dict,
    dispatch: Callable[[str, object], Coroutine[Any, Any, dict]] | None = None,
    *,
    remaining: float,
    group_event: Callable[[int, str], None] | None = None,
    checkpoint: Callable[[UUID], None] | None = None,
    validate_result: Callable[[object], None] | None = None,
    parent_model_step: UUID | None = None,
    child_dispatch: Callable[[str, object, int | None, int], Coroutine[Any, Any, dict]]
    | None = None,
    grouped_dispatch: Callable[[str, object, int | None], Coroutine[Any, Any, dict]] | None = None,
) -> object:
    journal = current_execution.get()
    workflow = (
        await asyncio.to_thread(journal.workflow, source, args, parent_model_step=parent_model_step)
        if journal
        else None
    )
    if checkpoint and workflow:
        checkpoint(workflow["stepId"])
    if workflow and workflow["saved"] is not None:
        saved = workflow["saved"]
        if code := saved.get("rejection"):
            raise ToolRejected(code)
        return saved["result"]
    node = shutil.which("node")
    if not node or not RUNNER.is_file():
        raise WorkflowError("runner_unavailable")
    process = await asyncio.create_subprocess_exec(
        node,
        "--max-old-space-size=64",
        str(RUNNER),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=RUNNER.parent,
        env={},
        limit=FRAME_BYTES,
    )
    assert process.stdin is not None and process.stdout is not None
    writer = process.stdin
    reader = process.stdout
    pending: dict[asyncio.Task, int] = {}
    read: asyncio.Task | None = None
    seen: set[int] = set()
    groups: dict[int, str] = {}
    canceled = False
    cleanup_confirmed = True
    delivered: set[int] = set()
    completed: dict[int, object] = {}
    script_rejection: str | None = None

    async def send(message: dict) -> None:
        line = json.dumps(message, ensure_ascii=False, allow_nan=False).encode()
        if len(line) >= FRAME_BYTES:
            raise WorkflowError("workflow_frame_limit")
        writer.write(line + b"\n")
        await writer.drain()

    try:
        await send(
            {
                "type": "start",
                "source": source,
                "args": args,
                "timeoutMs": int(remaining * 1000),
                **({"replay": workflow["clock"]} if workflow else {}),
            }
        )
        read = asyncio.create_task(reader.readline())
        while True:
            done, _ = await asyncio.wait({read, *pending}, return_when=asyncio.FIRST_COMPLETED)
            for child in tuple(pending):
                if child in done:
                    child_id = pending.pop(child)
                    completed[child_id] = child.result()
            order = workflow["deliveryOrder"] if workflow else []
            while completed:
                expected = next(
                    (identifier for identifier in order if identifier not in delivered), None
                )
                identifier = expected if expected is not None else next(iter(completed))
                if identifier not in completed:
                    break
                await send(
                    {"type": "result", "requestId": identifier, "value": completed.pop(identifier)}
                )
                delivered.add(identifier)
            if read not in done:
                continue
            line = read.result()
            if not line or len(line) >= FRAME_BYTES:
                raise WorkflowError("invalid_workflow_frame")
            frame = json.loads(line)
            if not isinstance(frame, dict):
                raise WorkflowError("invalid_workflow_frame")
            if frame.get("type") == "done" and set(frame) == {"type", "result"} and not pending:
                result = frame["result"]
                if validate_result:
                    validate_result(result)
                if journal and workflow:
                    await journal.workflow_result(workflow, args, result)
                return result
            if frame.get("type") == "error":
                if not seen:
                    script_rejection = (
                        "invalid_workflow_script"
                        if frame.get("code") == "invalid_workflow_script"
                        else "workflow_execution_failed"
                    )
                    break
                raise WorkflowError("script_failed")
            if frame.get("type") == "group" and set(frame) == {"type", "groupId", "status"}:
                identifier, status = frame["groupId"], frame["status"]
                if (
                    type(identifier) is not int
                    or not 1 <= identifier <= 3
                    or status not in {"running", "completed", "failed"}
                ):
                    raise WorkflowError("invalid_workflow_group")
                if (status == "running" and identifier in groups) or (
                    status != "running" and groups.get(identifier) != "running"
                ):
                    raise WorkflowError("invalid_workflow_group")
                groups[identifier] = status
                if group_event:
                    group_event(identifier, status)
                read = asyncio.create_task(reader.readline())
                continue
            if frame.get("type") != "agent" or set(frame) != {
                "type",
                "requestId",
                "profile",
                "input",
                "groupId",
            }:
                raise WorkflowError("invalid_workflow_frame")
            request_id, profile = frame["requestId"], frame["profile"]
            if (
                type(request_id) is not int
                or request_id < 1
                or request_id in seen
                or not isinstance(profile, str)
                or len(profile) > 32
            ):
                raise WorkflowError("invalid_child_request")
            if len(seen) >= 3 or len(pending) >= 2:
                raise WorkflowError("workflow_child_limit")
            seen.add(request_id)
            group_id = frame["groupId"]
            if group_id is not None and (
                type(group_id) is not int or groups.get(group_id) != "running"
            ):
                raise WorkflowError("invalid_workflow_group")

            async def invoke(
                profile=profile, frame=frame, group_id=group_id, request_id=request_id
            ):
                prepared = None
                if journal:
                    assert workflow is not None
                    workflow_parent = journal.parent.set(workflow["stepId"])
                    try:
                        prepared = await journal.host_begin(
                            workflow["key"],
                            request_id,
                            {"profile": profile, "input": frame["input"], "groupId": group_id},
                        )
                    finally:
                        journal.parent.reset(workflow_parent)
                with execution_scope(
                    f"host:{workflow['key'] if workflow else 'volatile'}:{request_id}"
                ):
                    parent = journal.parent.set(prepared[0]) if journal and prepared else None
                    try:
                        if child_dispatch:
                            value = await child_dispatch(
                                profile, frame["input"], group_id, request_id
                            )
                        elif grouped_dispatch:
                            value = await grouped_dispatch(profile, frame["input"], group_id)
                        else:
                            assert dispatch is not None
                            value = await dispatch(profile, frame["input"])
                        if journal:
                            await journal.host_result(prepared, value)
                        return value
                    finally:
                        if journal and parent is not None:
                            journal.parent.reset(parent)

            invocation = invoke()
            pending[asyncio.create_task(invocation)] = request_id
            read = asyncio.create_task(reader.readline())
    except asyncio.CancelledError as error:
        canceled = True
        if isinstance(error, PhaseCanceled):
            cleanup_confirmed = error.cleanup_confirmed
    except (ValueError, OSError, RecursionError):
        raise WorkflowError("invalid_workflow_frame") from None
    finally:
        tasks = [*pending, *([read] if read is not None else [])]
        for task in tasks:
            if not task.done():
                task.cancel()
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        writer.close()
        outcomes = await asyncio.gather(*tasks, process.wait(), return_exceptions=True)
        if not cleanup_confirmed or any(
            isinstance(item, ResourceUnconfirmed)
            or (isinstance(item, PhaseCanceled) and not item.cleanup_confirmed)
            for item in outcomes
        ):
            raise ResourceUnconfirmed("workflow cleanup unconfirmed")
    if canceled:
        raise PhaseCanceled(cleanup_confirmed=True)
    if script_rejection:
        if journal and workflow:
            await journal.workflow_result(workflow, args, None, rejection=script_rejection)
        raise ToolRejected(script_rejection)
    raise WorkflowError("script_failed")
