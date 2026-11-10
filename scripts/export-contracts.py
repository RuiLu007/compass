import json
import sys
from pathlib import Path

from compass_agent import schemas
from compass_agent.main import app
from compass_agent.schemas import (
    RUN_STREAM_LIMITS,
    RUN_STREAM_PROTOCOL,
    RUN_STREAM_VERSION,
    AnalysisCancelAck,
    DemoOverview,
    RunPacket,
)
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
models = [
    value
    for value in vars(schemas).values()
    if isinstance(value, type)
    and issubclass(value, BaseModel)
    and value.__module__ == schemas.__name__
    and value.__name__ != "StrictModel"
]
_, model_contracts = models_json_schema([(model, "validation") for model in models])
model_contracts["$schema"] = "https://json-schema.org/draft/2020-12/schema"
model_contracts["$id"] = "https://compass.local/contracts/model-contracts.schema.json"
openapi = app.openapi()
# SSE events and internal envelopes must have generated TS types even when their
# endpoint returns StreamingResponse rather than a JSON response_model.
openapi.setdefault("components", {}).setdefault("schemas", {}).update(
    json.loads(json.dumps(model_contracts["$defs"]).replace("#/$defs/", "#/components/schemas/"))
)
_, run_contracts = models_json_schema(
    [(RunPacket, "validation"), (AnalysisCancelAck, "validation")],
    by_alias=True,
)
run_stream = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://compass.local/contracts/run-stream.schema.json",
    "$ref": "#/$defs/RunPacket",
    "$defs": run_contracts["$defs"],
}
run_stream_limits = "".join(
    (
        f"export const RUN_STREAM_PROTOCOL = {json.dumps(RUN_STREAM_PROTOCOL)} as const;\n",
        f"export const RUN_STREAM_VERSION = {RUN_STREAM_VERSION} as const;\n",
        "export const RUN_STREAM_LIMITS = {\n",
        *(f"  {key}: {json.dumps(value)},\n" for key, value in RUN_STREAM_LIMITS.items()),
        "} as const;\n",
    )
)
for name, schema in [
    ("openapi.json", openapi),
    ("demo-overview.schema.json", DemoOverview.model_json_schema()),
    ("model-contracts.schema.json", model_contracts),
    ("run-stream.schema.json", run_stream),
]:
    (output / name).write_text(
        json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
(output / "run-stream-limits.ts").write_text(run_stream_limits)
