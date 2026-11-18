"""Real Lesson 18 HTTP/SQL/MCP/Sandbox fixture with deterministic model output."""

import json
import os
from typing import Any, cast

from compass_agent import main
from compass_agent.api.runs import RunContext, get_agent_executor
from compass_agent.data_connections.transport import AuthorizedDatabaseBinding as RetailBinding
from compass_agent.runtime.context import compass_definition
from compass_agent.runtime.service import RunService
from compass_agent.schemas import TenantScope, ToolCall, ToolCallFunction
from fastapi import Request
from pydantic import SecretStr

from .query_fixtures import provider_for
from .test_agent_run import FakeModels, FakeRouting, model_result, selected_route


def call(name, identifier, arguments):
    return ToolCall(id=identifier, function=ToolCallFunction(name=name, arguments=arguments))


CODE = """
import matplotlib.pyplot as plt
baseline = sum(int(row['paidCent']) for row in data['rows'])
current = sum(int(row['comparePaidCent']) for row in data['rows'])
result = {'baseline': baseline, 'current': current, 'delta': current - baseline}
plt.bar(['Baseline', 'Current'], [baseline / 100, current / 100])
plt.ylabel('CNY')
plt.savefig('/workspace/chart.png')
"""


class ToolModels(FakeModels):
    async def run(self, prepared, value, **kwargs):
        question = next(m.content for m in value.messages if m.role == "user") or ""
        observations = {
            m.tool_call_id: json.loads(m.content or "{}")
            for m in value.messages
            if m.role == "tool"
        }
        chart_first = "图表后审批" in question and "python" not in observations
        if ("MCP" in question or "审批" in question) and not chart_first:
            remote = "write" if "审批" in question else "slow" if "取消" in question else "add"
            definition = next(t for t in value.tools if f"/ {remote}:" in t.function.description)
            if "remote" in observations:
                return model_result(text="MCP 计算结果是 5。")
            return model_result(
                tool_calls=(call(definition.function.name, "remote", '{"left":2,"right":3}'),),
                token="fixture",
                finish="tool_calls",
            )
        if "query" not in observations:
            calls = (
                call(
                    "retail_sales",
                    "query",
                    json.dumps(
                        {
                            "dimension": "product",
                            "period": {"start": "2025-11-17", "end": "2025-11-24"},
                            "compareTo": {"start": "2025-11-24", "end": "2025-12-01"},
                            "channelCode": "POS",
                        }
                    ),
                ),
            )
            if "并行" in question:
                calls = (*calls, calls[0].model_copy(update={"id": "query-second"}))
            return model_result(tool_calls=calls, token="query", finish="tool_calls")
        if "python" not in observations:
            code = "import time\ntime.sleep(120)\nresult = 1" if "取消" in question else CODE
            return model_result(
                tool_calls=(
                    call(
                        "python_analysis",
                        "python",
                        json.dumps(
                            {"source_query_id": observations["query"]["queryId"], "code": code}
                        ),
                    ),
                ),
                token="python",
                finish="tool_calls",
            )
        result = observations["python"]
        if "result" not in result:
            return model_result(text="Python 分析未完成：" + json.dumps(result))
        data = result["result"]
        return model_result(
            text=f"Python 分析：实付金额从 {data['baseline'] / 100:.2f} 元"
            f"变为 {data['current'] / 100:.2f} 元，变化 {data['delta'] / 100:.2f} 元。"
        )


def build_executor(tool_loader):
    dsn = os.environ["COMPASS_WORKFLOW_E2E_RETAIL_DSN"]
    from urllib.parse import urlsplit

    def binding(scope):
        if not isinstance(scope, TenantScope):
            return None
        return RetailBinding(
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            role=urlsplit(dsn).username or "",
            dsn=SecretStr(dsn),
        )

    return RunService(
        cast(Any, ToolModels([])),
        cast(Any, FakeRouting(selected_route())),
        tools_provider=provider_for(binding),
        tool_loader=tool_loader,
        definition=compass_definition(),
    )


async def executor(context: RunContext, request: Request):
    return build_executor(request.app.state.tool_loader)


app = main.app
app.dependency_overrides[get_agent_executor] = executor
main.build_agent_executor = lambda settings, engine, app_state, model_service, **kwargs: (
    build_executor(app_state.tool_loader)
)
