"""Application ownership tests around preparation, cancellation and metadata commits."""

import asyncio
import hashlib
import threading
import time
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from compass_agent.llm.continuation import canonical, restore
from compass_agent.llm.contracts import ContinuationBinding, GenerationOutput
from compass_agent.llm.errors import RuntimeFailure
from compass_agent.llm.policy import CallPolicy
from compass_agent.llm.runtime import Runtime
from compass_agent.models.errors import ModelError
from compass_agent.models.secrets import ModelContinuationCodec
from compass_agent.schemas import ChatMessage

from .test_models import TEST, create, generate, invoke
from .test_models import system as system
from .test_runtime import Adapter


def check_cancel_after_prepare_commit(system):
    repo, upstream, service = system
    _, saved = create(repo, alias="Lifecycle " + uuid4().hex)
    call_id = uuid4()
    committed, release = threading.Event(), threading.Event()
    original = repo.begin

    def delayed(*args):
        result = original(*args)
        committed.set()
        assert release.wait(1)
        return result

    async def scenario():
        with patch.object(repo, "begin", side_effect=delayed):
            task = asyncio.create_task(
                service.prepare(call_id, saved.provider_id, generate(), TEST)
            )
            async with asyncio.timeout(5):
                while not committed.is_set():
                    await asyncio.sleep(0.005)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert repo.call(call_id, TEST).status == "canceled"
    assert not repo.call(call_id, TEST).upstream_outcome_unknown
    assert not upstream.requests


def test_cancel_after_prepare_commit_records_without_dispatch(system):
    check_cancel_after_prepare_commit(system)


def check_finish_idempotency(system):
    repo, _, service = system
    _, saved = create(repo, alias="Lifecycle " + uuid4().hex)
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert repo.finish(result) is True
    assert (
        repo.finish(result.model_copy(update={"status": "failed", "error_code": "internal_error"}))
        is False
    )
    assert repo.call(result.call_id, TEST).status == "completed"
    assert repo.finish(result.model_copy(update={"call_id": uuid4()})) is False


def test_finish_only_accepts_same_existing_terminal_record(system):
    check_finish_idempotency(system)


def test_decryption_failure_after_commit_is_recorded_without_dispatch(system):
    repo, upstream, service = system
    _, saved = create(repo)

    async def scenario():
        req = generate()
        prepared = await service.prepare(uuid4(), saved.provider_id, req, TEST)
        with patch.object(repo.secrets, "open_key", side_effect=ModelError("unavailable", 503)):
            return await service.run(prepared, req)

    result = asyncio.run(scenario())
    assert result.status == "failed" and result.error_code == "unavailable"
    assert repo.call(result.call_id, TEST).status == "failed"
    assert not upstream.requests and not result.upstream_outcome_unknown


def test_preparation_budget_is_not_reset_at_runtime_entry(system):
    repo, upstream, service = system
    _, saved = create(repo)

    async def scenario():
        req = generate()
        prepared = await service.prepare(uuid4(), saved.provider_id, req, TEST)
        start = asyncio.get_running_loop().time() - 2
        prepared = replace(
            prepared,
            policy=replace(prepared.policy, started_monotonic=start, deadline_monotonic=start + 1),
        )
        return await service.run(prepared, req)

    result = asyncio.run(scenario())
    assert result.error_code == "model_timeout" and result.status == "failed"
    assert not upstream.requests and not result.upstream_outcome_unknown


def test_prepare_timeout_closes_a_late_committed_record_without_dispatch(system):
    repo, upstream, service = system
    _, saved = create(repo)
    call_id = uuid4()
    begin = repo.begin

    def delayed(*args):
        result = begin(*args)
        time.sleep(0.06)
        return result

    def short_policy(start, end):
        return CallPolicy(start, start + 0.02)

    with (
        patch("compass_agent.models.service.CallPolicy", side_effect=short_policy),
        patch.object(repo, "begin", side_effect=delayed),
        pytest.raises(ModelError, match="model_timeout"),
    ):
        asyncio.run(service.prepare(call_id, saved.provider_id, generate(), TEST))
    record = repo.call(call_id, TEST)
    assert record.status == "failed" and record.error_code == "model_timeout"
    assert not record.upstream_outcome_unknown and not upstream.requests


def test_combined_http_output_limit_is_enforced_before_recording_success(system):
    repo, _, service = system
    _, saved = create(repo)

    async def large_output(req, session):
        session.resources.sent = True
        session.progress.normal_eof = True
        session.progress.finish_reason = "tool_calls"
        return GenerationOutput(text="x" * 1048576, continuation_token="x" * 1048576)

    service.runtime = Runtime({"litellm_chat": Adapter(large_output)})
    result = asyncio.run(invoke(service, saved.provider_id, generate()))
    assert result.status == "failed" and result.error_code == "response_too_large"
    assert not result.text and result.continuation_token is None
    assert len(result.model_dump_json().encode()) < 2097152
    assert repo.call(result.call_id, TEST).status == "failed"


def test_cancellation_during_prepare_timeout_settlement_takes_priority(system):
    repo, upstream, service = system
    _, saved = create(repo)
    begin = repo.begin
    call_id = uuid4()
    release = threading.Event()

    def delayed(*args):
        result = begin(*args)
        assert release.wait(1)
        return result

    async def scenario():
        from compass_agent.models.service import settle

        settling = asyncio.Event()

        async def observed_settle(task, seconds):
            settling.set()
            return await settle(task, seconds)

        with (
            patch(
                "compass_agent.models.service.CallPolicy",
                side_effect=lambda start, end: CallPolicy(start, start + 0.02),
            ),
            patch.object(repo, "begin", side_effect=delayed),
            patch("compass_agent.models.service.settle", side_effect=observed_settle),
        ):
            task = asyncio.create_task(
                service.prepare(call_id, saved.provider_id, generate(), TEST)
            )
            await asyncio.wait_for(settling.wait(), 1)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert repo.call(call_id, TEST).status == "canceled"
    assert not upstream.requests


def test_v1_token_payload_is_accepted_and_expired_token_rejected(system):
    repo, _, _ = system
    _, saved = create(repo)
    binding = ContinuationBinding(
        actor=TEST.actor_user_id, scope=TEST.scope, provider=saved.provider_id, binding_version=1
    )
    messages = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="tool response"),
    ]
    visible = [m.model_dump(exclude_none=True) for m in messages]
    # V1 serialized UUIDs as strings and used Fernet's timestamp without a payload expiry.
    old_payload = {
        "binding": binding.model_dump(mode="json"),
        "visible_count": 2,
        "visible_digest": hashlib.sha256(canonical(visible)).hexdigest(),
        "private_assistants": {"1": "private protocol state"},
        "allowed_tool_call_ids": ["tool-1"],
    }
    token = repo.secrets.seal("tool_continuation", old_payload)
    req = generate().model_copy(
        update={
            "messages": [
                *messages,
                ChatMessage(role="tool", content="done", tool_call_id="tool-1"),
            ],
            "continuation_token": token,
        }
    )
    restored, _ = restore(binding, req, ModelContinuationCodec(repo.secrets))
    assert restored[1]["reasoning_content"] == "private protocol state"
    expired = repo.secrets.cipher.encrypt_at_time(
        canonical({"purpose": "tool_continuation", "value": old_payload}), int(time.time()) - 901
    ).decode()
    with pytest.raises(RuntimeFailure, match="continuation_invalid"):
        ModelContinuationCodec(repo.secrets).open(expired)
