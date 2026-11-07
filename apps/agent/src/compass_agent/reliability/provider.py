"""Fixed HTTP scripts exercising the production SDK adapter and transport."""

import asyncio
import json

import httpx


class ScriptedStream(httpx.AsyncByteStream):
    def __init__(self, scenario):
        self.scenario = scenario

    async def __aiter__(self):
        texts = (
            ['{"sales": "invalid"}']
            if self.scenario == "schema_invalid"
            else ["正在分析销售数据。", "本次调用已完成校验。", "可以提交完整结果。"]
        )
        for text in texts:
            await asyncio.sleep(0.12)
            payload = {
                "id": "simulation",
                "model": "deepseek-v4-pro",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
            }
            yield ("data: " + json.dumps(payload) + "\n\n").encode()
            if self.scenario == "interrupted":
                raise httpx.ReadError("simulated disconnect")
        payload = {
            "id": "simulation",
            "model": "deepseek-v4-pro",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }
        yield ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode()

    async def aclose(self):
        pass


class SimulationTransport(httpx.MockTransport):
    def __init__(self, script):
        super().__init__(script.handle)
        self.script = script

    async def aclose(self):
        if self.script.scenario == "cleanup_failed":
            raise RuntimeError("simulated resource close failure")


class ScriptedProvider:
    def __init__(self, scenario, on_event=None):
        self.scenario = scenario
        self.requests = 0
        self.on_event = on_event

    def transport(self):
        return SimulationTransport(self)

    async def handle(self, request):
        self.requests += 1
        if self.on_event:
            await self.on_event(f"Attempt {self.requests}：供应商收到推理请求")
        await asyncio.sleep(0.1)
        if self.scenario == "authentication":
            if self.on_event:
                await self.on_event(f"Attempt {self.requests}：返回 401，认证失败")
            return httpx.Response(401, json={"error": {"message": "simulated rejection"}})
        if self.scenario in {"rate_limit", "unavailable", "exhausted"} and (
            self.requests == 1 or self.scenario == "exhausted"
        ):
            status = 503 if self.scenario == "unavailable" else 429
            if self.on_event:
                await self.on_event(f"Attempt {self.requests}：返回 {status}")
            return httpx.Response(
                status,
                json={"error": {"message": "simulated transient rejection"}},
            )
        if self.scenario == "timeout":
            await asyncio.sleep(10)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=ScriptedStream(self.scenario)
        )
