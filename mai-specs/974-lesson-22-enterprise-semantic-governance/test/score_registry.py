"""Exercise the HTTP Registry and Agent read-only executor against a frozen retail release."""

import argparse
import asyncio
import json
import os
from collections import Counter
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
from compass_agent.data_connections.transport import AuthorizedDatabaseBinding
from compass_agent.semantic.execution import calculate
from effect_cohort import samples
from pydantic import SecretStr
from score_sql_bindings import FINANCE, ROAS, UNITS, cases, expected
from verify_retail_content import RELEASE


class PublishedRegistry:
    def __init__(self, client, personas):
        self.http = client
        self.personas = personas
        self.pins = {}

    def request(self, persona, run_id, op, args):
        who = self.personas[persona]
        payload = {
            "actor_user_id": who["actor"],
            "tenant_id": who["tenantId"],
            "workspace_id": who["workspaceId"],
            "run_id": str(run_id),
            "args": args,
        }
        if op != "manifest":
            if persona not in self.pins:
                self.pins[persona] = self.request(persona, run_id, "manifest", {})["releases"]
            payload["pins"] = self.pins[persona]
        res = self.http.post("/api/internal/semantic/" + op, json=payload)
        res.raise_for_status()
        return res.json()

    async def execute(self, persona, metric, p, tenant):
        run_id = uuid4()
        resolved = self.request(persona, run_id, "resolve", {"mention": metric})
        if resolved["status"] != "resolved":
            raise ValueError(f"resolution={resolved['status']}")
        definition = resolved["metric"]
        pointer = {
            "asset_id": definition["asset_id"],
            "version_id": definition["version_id"],
            "binding_id": resolved["binding_id"],
        }
        approved = self.request(persona, run_id, "binding", pointer)["binding"]
        dsn = os.environ[f"SEMANTIC_RETAIL_{tenant}_URL"]
        who = self.personas[persona]
        binding = AuthorizedDatabaseBinding(
            tenant_id=UUID(who["tenantId"]),
            workspace_id=UUID(who["workspaceId"]),
            name=f"retail:{tenant}",
            connection_id=uuid4(),
            role=urlsplit(dsn).username,
            dsn=SecretStr(dsn),
        )
        control = SimpleNamespace(
            id=run_id,
            require_running=lambda: None,
            phase_deadline=asyncio.get_running_loop().time() + 5,
        )
        rows, witness = await calculate(binding, approved, p, control)
        if witness["tenant_id"] != tenant or witness["all_regions"] != (tenant == 101):
            raise ValueError("result scope differs")
        if tenant == 102 and witness["region_ids"] != [1]:
            raise ValueError("region scope differs")
        return rows


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="dedicated local Web test origin")
    parser.add_argument(
        "personas", type=Path, help="test persona keys with actor/tenantId/workspaceId"
    )
    args = parser.parse_args()
    if urlsplit(args.base_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("use the local acceptance server")
    if not urlsplit(os.environ["WEB_TEST_DATABASE_URL"]).path.startswith("/compass_web_test_"):
        parser.error("use a dedicated Web test database")
    counts = Counter()
    failed = []
    annotations = {}
    with ExitStack() as stack:
        http = stack.enter_context(
            httpx.Client(
                base_url=args.base_url,
                timeout=10,
                headers={"authorization": "Bearer " + os.environ["COMPASS_AGENT_SERVICE_TOKEN"]},
            )
        )
        registry = PublishedRegistry(http, json.loads(args.personas.read_text()))
        web = stack.enter_context(psycopg.connect(os.environ["WEB_TEST_DATABASE_URL"]))
        web.execute("SET TRANSACTION READ ONLY")
        codes = dict(web.execute("SELECT asset_id::text,code FROM web.semantic_assets"))
        connections = {}
        for tenant in (101, 102):
            conn = stack.enter_context(psycopg.connect(os.environ[f"SEMANTIC_RETAIL_{tenant}_URL"]))
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL search_path=pg_catalog,retail_course,pg_temp")
            connections[tenant] = conn

        for n, (tenant, metric, p) in enumerate(cases(), start=1):
            persona = f"{tenant}.sales"
            if metric == FINANCE:
                persona = "101.finance"
            if metric == ROAS:
                persona = "101.marketing"
            try:
                oracle = expected(connections[tenant], metric, p)
                rows = await registry.execute(persona, metric, p, tenant)
                values = {
                    r["group_key"]: (
                        Decimal(r["value"]) if r["value"] is not None else None,
                        r["status"],
                    )
                    for r in rows
                }
                if len(rows) != len(values) or values != oracle:
                    raise ValueError(
                        f"result rows differ from independent aggregate: {values} vs {oracle}"
                    )
                if any(
                    (
                        r["unit"],
                        r["data_release"],
                        r["business_timezone"],
                        r["period_start"],
                        r["period_end"],
                    )
                    != (UNITS[metric], RELEASE, "Asia/Shanghai", p["start"], p["end"])
                    for r in rows
                ):
                    raise ValueError("result unit, source or time range differs")
                counts["sql"] += 1
            except Exception as error:
                failed.append({"id": f"S{n:03}", "error": str(error)})

        for case in samples():
            if case["stratum"] == "complex":
                continue
            question = case["question"]
            try:
                # Only natural utterance is supplied. This measures Registry decisions in
                # isolation; the Chat/model's parsed dates and dimensions require a separate run.
                reply = registry.request(case["persona"], uuid4(), "resolve", {"mention": question})
                status = reply["status"]
                ids = [v["asset_id"] for v in reply.get("options", [])]
                if "metric" in reply:
                    ids.append(reply["metric"]["asset_id"])
                metrics = sorted(codes[i] for i in ids)
                expected_action = case["expected"]["action"]
                allowed = {
                    "resolve": {"resolved"},
                    "clarify": {"needs_clarification"},
                    "deny": {"unavailable_binding", "forbidden"},
                }[expected_action]
                if status not in allowed or metrics != sorted(case["expected"]["metrics"]):
                    raise ValueError(f"{status}, {metrics}")
                counts[case["stratum"]] += 1
            except Exception as error:
                failed.append({"id": case["id"], "error": str(error)})
        annotations["pinned_partition_releases"] = registry.pins
    print(
        json.dumps(
            {"mode": "registry-and-executor", "passed": counts, "failures": failed, **annotations},
            ensure_ascii=False,
        )
    )
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
