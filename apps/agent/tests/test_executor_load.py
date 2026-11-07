"""Repeatable 100-client/1,000-call local HTTP-provider load acceptance."""

import asyncio
import json
import statistics
from pathlib import Path

from compass_agent.llm.admission import ExecutionGate
from compass_agent.llm.executor import ReliableExecutor
from compass_agent.llm.litellm_chat import LiteLLMChatAdapter
from compass_agent.llm.policy import LogicalPolicy
from compass_agent.llm.runtime import Runtime
from compass_agent.models.secrets import ModelContinuationCodec, SecretStore
from compass_agent.reliability.provider import ScriptedProvider
from cryptography.fernet import Fernet

from .test_runtime import request


def test_hundred_clients_thousand_calls_remain_bounded_and_independent():
    async def run():
        gate = ExecutionGate(8, 32)
        codec = ModelContinuationCodec(SecretStore(Fernet.generate_key().decode()))
        results, latencies = [], []
        loop = asyncio.get_running_loop()
        started = loop.time()

        async def client(number):
            for index in range(10):
                scenario = ["success", "rate_limit", "authentication", "interrupted"][
                    (number + index) % 4
                ]
                provider = ScriptedProvider(scenario)
                runtime = Runtime({"litellm_chat": LiteLLMChatAdapter(codec, provider.transport)})
                invocation = request(10, stream=True)
                chunks = []

                async def emit(event, chunks=chunks):
                    if event.type == "text_delta":
                        chunks.append(event.text)

                call_started = loop.time()
                result = await ReliableExecutor(runtime, gate).execute(
                    invocation, LogicalPolicy(max_attempts=2), emit
                )
                latencies.append((loop.time() - call_started) * 1000)
                assert result.call_id == invocation.context.call_id
                assert result.execution is not None
                assert len(result.execution.attempts) == provider.requests <= 2
                if result.error and result.error.code == "runtime_overloaded":
                    assert not chunks and provider.requests == 0
                elif scenario in {"success", "rate_limit"}:
                    assert result.status == "completed"
                    assert result.output.kind == "generate"
                    assert result.output.text == "".join(chunks)
                    assert provider.requests == (2 if scenario == "rate_limit" else 1)
                elif scenario == "interrupted":
                    assert result.status == "incomplete" and chunks and provider.requests == 1
                else:
                    assert result.error is not None
                    assert (
                        result.error.code == "model_authentication_failed"
                        and provider.requests == 1
                    )
                results.append(result)
                await asyncio.sleep(0.005)

        await asyncio.gather(*(client(n) for n in range(100)))
        assert len(results) == 1000
        assert len({r.call_id for r in results}) == 1000
        assert gate.peak_active <= 8 and gate.peak_inflight <= 32
        assert gate.inflight == gate.active == gate.quarantined == 0
        assert gate.snapshot()["available"] == 8
        assert any(r.status == "completed" for r in results)
        report = dict(
            clients=100,
            calls=1000,
            duration_s=loop.time() - started,
            completed=sum(r.status == "completed" for r in results),
            overloaded=sum(bool(r.error and r.error.code == "runtime_overloaded") for r in results),
            attempts=sum(len(r.execution.attempts) for r in results if r.execution is not None),
            p50_ms=statistics.median(latencies),
            p95_ms=sorted(latencies)[949],
            capacity=gate.snapshot(),
        )
        output = Path(__file__).resolve().parents[3] / "work/991"
        output.mkdir(parents=True, exist_ok=True)
        (output / "load-report.json").write_text(json.dumps(report, indent=2))

    asyncio.run(run())
