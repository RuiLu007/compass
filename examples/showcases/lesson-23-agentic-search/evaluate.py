"""Run ordinary Chat questions and collect actual Run receipts and authorized citation details."""

import argparse
import json
import os
import re
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import psycopg
from compass_agent.runtime.private_state import PrivateState
from materials import KINDS, publish
from psycopg.rows import dict_row
from web_client import ClassroomWeb

MAIN = [
    "节后第一场会，帮我把华东小程序那笔跑鞋引流款定下来。有人说这三个月销售一直在掉，扣完退款到底怎么样？上次卡住的尺码，是没人看还是下了单没卖成，到货后好了吗？按上次周会留的事项和今天的报价，给我一页意见。建议选哪档、留多少钱、最晚什么时候回对方，谁来复看？",
    "华东微信上次挂起的跑鞋推广，今天能恢复到什么程度？我需要近三个月的销售和净回款、出问题那几天与到货后的表现，以及能签的金额、余额、答复和复核安排。",
    "今天要回媒介方。请接着上次华东小程序周会，把跑鞋预算写成一页批复建议。先核这三个月生意与退款，再说流量和履约有没有恢复，最后把档位、余款、时限和负责人落下来。",
]
QUESTIONS = {
    "simple": "9 月华东小程序扣退款后的成交额是多少？",
    "finance": "九月华东小程序实收和扣退款后的金额差在哪里？有多少是以前月份收的钱？",
    "store": "上海浦东滨江店、杭州城西体验店都来问九月能否申报返补，帮我分别答复。",
    "inventory-gap": (
        "华东微信小程序逐风·晨跑 R100 黑色欧码 42，苏州华东配送中心 9 月 10 日日结可用库存多少？"
    ),
}
EXPECTED = {
    "G1": ["352968.20", "73684.75", "204550.95", "293736.05", "19810.70", "180840.20"],
    "G2": ["80", "68", "52", "18673.20"],
    "G3": ["DOC13392", "400", "80"],
    "G4": ["57", "79", "80", "0"],
    "G5": ["407", "406", "1"],
    "G6": ["20000", "8000", "6000", "14000"],
    "G7": ["15:00", "10:00", "10月11日", "华东渠道经理"],
    "simple": ["180840.20"],
    "finance": ["23710.75", "8994", "4360.60", "10356.15", "13354.60"],
    "store": ["14088.75", "84212.80", "100000", "80000"],
    "store-expired": ["14088.75", "84212.80", "100000"],
    "price": ["8000", "9000", "15000", "20000"],
}
GROUPS = [
    "candidate",
    "baseline",
    "sql",
    "semantic",
    "price",
    "rebate-expired",
    "budget",
    "unavailable",
    *["without-" + k for k in KINDS],
]


def model_accounting(db, run_id):
    """Sum actual physical attempts, including routing; reservations stay separate."""
    entries = db.execute(
        """SELECT a.provider_id,a.requested_model,a.config_version,a.status,a.sent,
                  a.outcome_unknown,a.cleanup_confirmed,a.usage,a.actual_cost_microusd,
                  a.reserved_tokens,a.reserved_cost_microusd
             FROM model_attempt a JOIN model_call c ON c.call_id=a.call_id
            WHERE c.run_id=%s AND a.origin='live' ORDER BY a.created_at,a.number""",
        (run_id,),
    ).fetchall()
    models = {}
    for entry in entries:
        key = (str(entry["provider_id"]), entry["requested_model"], entry["config_version"])
        totals = models.setdefault(key, Counter())
        if not entry["sent"] and entry["cleanup_confirmed"] and not entry["outcome_unknown"]:
            totals["unsent_attempts"] += 1
            continue
        usage = entry["usage"] or {}
        if (
            entry["status"] in {"prepared", "dispatched", "unknown", "incomplete"}
            or entry["outcome_unknown"]
            or not entry["cleanup_confirmed"]
            or usage.get("input_tokens") is None
            or usage.get("output_tokens") is None
        ):
            totals["unknown_attempts"] += 1
            totals["reserved_tokens"] += entry["reserved_tokens"]
            totals["reserved_cost_microusd"] += entry["reserved_cost_microusd"]
            continue
        totals["measured_attempts"] += 1
        totals["input_tokens"] += usage["input_tokens"]
        totals["output_tokens"] += usage["output_tokens"]
        totals["reasoning_tokens"] += usage.get("reasoning_tokens") or 0
        totals["tokens"] += usage["input_tokens"] + max(
            usage["output_tokens"], usage.get("reasoning_tokens") or 0
        )
        if entry["actual_cost_microusd"] is None:
            totals["unpriced_attempts"] += 1
        else:
            totals["priced_attempts"] += 1
            totals["cost_microusd"] += entry["actual_cost_microusd"]
    summed = Counter()
    for totals in models.values():
        summed.update(totals)
    return {
        "usageSource": "model_attempt",
        "usage": dict(summed),
        "usageByModel": [
            {"providerId": key[0], "model": key[1], "configVersion": key[2], **totals}
            for key, totals in models.items()
        ],
        "unsettledModelCalls": summed["unknown_attempts"],
        "usageComplete": not (summed["unknown_attempts"] or summed["unpriced_attempts"]),
    }


def instructor_database_url():
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    if "/compass_agent_test_" not in url:
        raise ValueError("Use a dedicated classroom Agent DB for instructor access")
    return url


def recount_receipts(files, directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with psycopg.connect(instructor_database_url(), row_factory=dict_row) as db:
        for source in files:
            target = directory / source.name
            if target.resolve() == source.resolve():
                raise ValueError("Use a separate output directory for the corrected receipts")
            record = json.loads(source.read_text())
            run_id = UUID(record["runId"])
            run = db.execute(
                "SELECT config_snapshot->'timeoutSeconds' AS timeout FROM agent_run WHERE id=%s",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"Run {run_id} does not belong to this classroom database")
            trace = record.setdefault("trace", {})
            trace.update(model_accounting(db, run_id))
            if run["timeout"] is not None:
                trace["timeoutSeconds"] = run["timeout"]
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def instructor_trace(run_id):
    url = instructor_database_url()
    private = PrivateState(os.environ["COMPASS_RUNTIME_STATE_KEY"])
    uid = UUID(run_id)
    with psycopg.connect(url, row_factory=dict_row) as db:
        run = db.execute(
            """SELECT status,reason_code,config_snapshot,active_ms,
                      EXTRACT(epoch FROM finished_at-created_at) AS wall_seconds
                 FROM agent_run WHERE id=%s""",
            (uid,),
        ).fetchone()
        attempts = db.execute(
            """SELECT s.kind,s.ordinal,s.node_key,a.input_content,a.output_content,
             a.status FROM agent_step s
             JOIN agent_attempt a ON s.id=a.step_id
             WHERE s.run_id=%s ORDER BY s.ordinal,a.number""",
            (uid,),
        ).fetchall()
        accounting = model_accounting(db, uid)
        ledger = db.execute(
            "SELECT private_content FROM agent_message WHERE id=%s",
            (uuid5(uid, "context:search-allowance"),),
        ).fetchone()
    calls = []
    requests = {}
    models = []
    for attempt in attempts:
        frame = (
            private.open(uid, "attempt-output", attempt["output_content"])
            if attempt["output_content"]
            else {}
        )
        output = frame.get("result") if isinstance(frame, dict) else frame
        if not isinstance(output, dict):
            output = {}
        if attempt["kind"] == "model":
            identity = {
                key: output.get(key)
                for key in ("provider_id", "requested_model", "response_model", "config_version")
            }
            if identity["provider_id"] and identity not in models:
                models.append(identity)
            for item in output.get("tool_calls", []):
                function = item.get("function", {})
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        pass
                requests[item.get("id")] = (function.get("name"), arguments, attempt["ordinal"])
        if attempt["kind"] == "tool":
            receipt = output.get("receipt")
            # Nested operations journal their private result ID under the parent. Only the
            # outer dispatch has a ToolReceipt and represents a model-visible tool action.
            if not isinstance(receipt, dict):
                continue
            call_id = receipt.get("execution_id") or attempt["node_key"].rsplit(":", 1)[-1]
            name, arguments, turn = requests.get(call_id, (None, None, None))
            calls.append(
                dict(
                    ordinal=attempt["ordinal"],
                    turn=turn,
                    name=name,
                    arguments=arguments,
                    status=receipt.get("status"),
                    output=receipt.get("payload"),
                    reason=receipt.get("reason_code"),
                    executionId=call_id,
                )
            )
    pin = run["config_snapshot"]
    context = pin.get("contextRuntime", {})
    ledger_rows = private.open(uid, "search-allowance", ledger["private_content"]) if ledger else []
    settled_keys = set()
    repeats = 0
    for entry in ledger_rows:
        if entry["key"] in settled_keys:
            repeats += 1
        if entry.get("settled") and not entry.get("failure"):
            settled_keys.add(entry["key"])
    physical = {entry["identity"].rsplit(":", 1)[-1].split("/")[0] for entry in ledger_rows}
    for call in calls:
        call["dispatched"] = call["executionId"] in physical
    doc_calls = {
        c["executionId"] for c in calls if c["name"] in ("search_documents", "read_document")
    }
    attempts_by_key = Counter(
        e["key"] for e in ledger_rows if e["identity"].rsplit(":", 1)[-1].split("/")[0] in doc_calls
    )
    return dict(
        status=run["status"],
        reason=run["reason_code"],
        activeMs=run["active_ms"],
        wallSeconds=round(float(run["wall_seconds"]), 2)
        if run["wall_seconds"] is not None
        else None,
        selection=pin["command"].get("selection"),
        models=models,
        context={
            key: context.get(key)
            for key in ("generation", "policyDigest", "manifestDigest", "search", "registry")
        },
        snapshot=pin.get("searchSnapshot"),
        limits=pin.get("budget"),
        timeoutSeconds=pin.get("timeoutSeconds"),
        calls=calls,
        physicalCalls=len(ledger_rows),
        repeatedSuccessfulDispatches=repeats,
        documentDispatchCounts=list(sorted(attempts_by_key.values(), reverse=True)),
        **accounting,
    )


def contains(text, token):
    simplified = re.sub(r"[,，\s]", "", text).replace("：", ":")
    if token.startswith("10月"):
        return bool(re.search(r"10(?:月|[-/])11", simplified))
    if re.fullmatch(r"\d+(\.\d+)?", token):
        from decimal import Decimal

        matches = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])([万亿]?)", simplified)
        # The independent rubric counts business values, not digit substrings from IDs.
        units = {"": 1, "万": 10_000, "亿": 100_000_000}
        return any(Decimal(x) * units[unit] == Decimal(token) for x, unit in matches)
    return token in simplified


# Each dropout retains checks for facts that can still be established.
DROPOUTS = {
    "semantic": (["G3", "G4"], ["口径", "正式指标", "回执"]),
    "detail": (["G1"], ["验收窗", "归因", "实际收货", "采购实收", "到货"]),
    "knowledge": (["G1", "G3"], ["额度比例", "规则", "制度", "办法", "政策"]),
    "history": (["G1"], ["挂账", "承办人", "余额", "待办", "负责人"]),
    "external": (["G1", "G3", "G5"], ["报价", "档位", "回函", "排期", "日历"]),
}


def reported_gap(answer, subjects):
    uncertainty = (
        r"未(?:能|取得|查|获|核|确认|完成|验证)|待(?:查|核|补|确认)"
        r"|缺少|缺失|缺口|无法|不足|尚无|暂[缓停]|需要补"
    )
    for sentence in re.split(r"[。！？\n]", answer):
        if any(subject in sentence for subject in subjects) and re.search(uncertainty, sentence):
            return True
    return False


def adaptations(calls, question=""):
    """Find source-specific business values used by a later model turn in another source."""

    def tokens(value):
        if isinstance(value, (list, tuple)):
            return set().union(*(tokens(v) for v in value))
        if isinstance(value, dict):
            hidden = {
                "sourceref",
                "source_ref",
                "as_of",
                "knownat",
                "publishedat",
                "timezone",
                "time_range",
                "scope",
                "ref",
                "detail",
                "versions",
            }
            return set().union(*(tokens(v) for k, v in value.items() if k.lower() not in hidden))
        if not isinstance(value, str):
            return set()
        return set(
            re.findall(
                r"(?<![A-Za-z0-9])(?:[A-Z]{1,6}\d{2,}|[A-Z]{1,6}-[A-Z0-9-]*\d[A-Z0-9-]*|"
                r"\d{4}-\d{2}-\d{2}|\d{3,5}-[A-Z][A-Z0-9]+-\d+)(?![A-Za-z0-9])",
                value,
            )
        )

    def observed(call):
        if call.get("status") not in ("ok", "empty") or call.get("turn") is None:
            return {}
        name, out = call.get("name"), call.get("output")
        if not isinstance(out, dict):
            return {}
        if name in ("read_document", "search_documents"):
            result = {}
            for section in out.get("sections", []):
                kind = section.get("kind")
                if kind in ("knowledge", "history", "external"):
                    visible = [
                        section.get(k, "")
                        for k in ("text", "snippet", "excerpt", "heading", "title", "docId")
                    ]
                    result.setdefault(kind, set()).update(tokens(visible))
            return result
        if name == "database_query":
            if (call.get("arguments") or {}).get("op") != "query" or not out.get("rows"):
                return {}
            return {"detail": tokens(out["rows"])}
        if name == "query_metric" and out.get("rows"):
            return {"semantic": tokens(out["rows"])}
        if name == "search_semantics" and out.get("items"):
            return {"semantic": tokens(out["items"])}
        if name == "resolve_semantics" and out.get("status") == "resolved":
            return {"semantic": tokens(out.get("metric", {}))}
        if name == "get_metric_definition" and out.get("definition"):
            return {"semantic": tokens(out.get("definition", {}))}
        return {}

    known = tokens(question)
    sources = [observed(call) for call in calls]
    pairs = []
    seen = set()
    for i, later in enumerate(calls):
        used = tokens(later.get("arguments")) - known
        if not used or not sources[i]:
            continue
        for j, earlier in enumerate(calls[:i]):
            if not sources[j] or earlier["ordinal"] >= later["turn"]:
                continue
            for source, values in sources[j].items():
                matched = used & (values - tokens(earlier["arguments"]))
                if not matched:
                    continue
                for destination in sources[i].keys() - {source}:
                    unique = [
                        token
                        for token in sorted(matched)
                        if (later["ordinal"], source, destination, token) not in seen
                    ]
                    if not unique:
                        continue
                    seen.update((later["ordinal"], source, destination, token) for token in unique)
                    pairs.append(
                        dict(
                            observedAt=earlier["ordinal"],
                            usedAt=later["ordinal"],
                            origin=source,
                            destination=destination,
                            observedWith=earlier["name"],
                            usedWith=later["name"],
                            sharedValues=unique,
                        )
                    )
    return pairs


def grade(record):
    group, case = record["group"], record["case"]
    answer, details, trace = record["answer"], record["citations"], record["trace"]
    kinds = {"semantic" if k in ("metric", "definition") else k for k in record["sourceKinds"]}
    supported = [d for d in details if d["status"] == "supported"]
    supported_kinds = {
        "semantic" if s["kind"] in ("metric", "definition") else s["kind"]
        for d in supported
        for s in d["sources"]
    }
    checked_text = (
        answer
        + "\n"
        + "\n".join(
            d["statement"] + " " + " ".join(s["excerpt"] for s in d["sources"]) for d in supported
        )
    )
    focus = list(EXPECTED)[:7] if case == "main" else [case] if case in EXPECTED else []
    checks = {
        "completed": record["status"] == "completed",
        "noRepeatedSuccessfulDispatch": trace["repeatedSuccessfulDispatches"] == 0,
    }
    if group.startswith("without-"):
        kind = group.removeprefix("without-")
        focus, subjects = DROPOUTS[kind]
        checks.update(
            disabledKindHidden=kind not in kinds,
            remainingVerifiedFacts=bool(supported),
            specifiedGapReported=reported_gap(answer, subjects),
        )
    elif group in ("budget", "unavailable"):
        focus = []
        checks["incompleteConditionsReported"] = reported_gap(
            answer,
            [
                "指标",
                "来源",
                "条件",
                "材料",
                "报价",
                "依据",
                "信息",
                "数据",
                "预算",
                "限制",
                "核实",
                "查询回执",
            ],
        )
        if group == "budget":
            checks["atMostTwoDispatches"] = trace["physicalCalls"] <= 2
        else:
            checks["boundedDocumentAttempts"] = bool(trace.get("documentDispatchCounts")) and (
                max(trace["documentDispatchCounts"]) <= 2
            )
            checks["verifiedFactsRetained"] = bool(supported_kinds & {"semantic", "detail"})
            checks["faultObserved"] = any(
                any(
                    x in json.dumps([c["output"], c["reason"]], ensure_ascii=False)
                    for x in ("unavailable", "unreachable", "503", "source_missing")
                )
                for c in trace["calls"]
            )
    elif group == "price":
        focus = [g for g in focus if g != "G6"] + ["price"]
        checks["currentQuoteVersion"] = any(
            s["kind"] == "external"
            and s["locator"].startswith("E-1009 /")
            and s["version"] == "3"
            and contains(s["excerpt"], "9000")
            for d in supported
            for s in d["sources"]
        )
        sentences = [s for s in re.split(r"[。！？\n]", answer) if s.strip()]
        checks["purchaseDeferred"] = any(
            re.search(
                r"暂缓(?:本次)?(?:下单|采购|订购|订档)|暂不(?:下单|采购|订购|订档)"
                r"|不(?:建议|予)?(?:下单|采购|订购|订档)|无(?:可购|可选|可订|可签)档位",
                s,
            )
            for s in sentences
        )
        checks["fullReserveRetained"] = any(
            contains(d["statement"], "20000")
            and (
                contains(d.get("calculation") or "", "0")
                or (
                    re.search(r"保留|留存|剩余|结余|余款", d["statement"])
                    and {"history", "external"} <= {s["kind"] for s in d["sources"]}
                )
            )
            for d in supported
        )
    elif group == "rebate-expired" and case == "store":
        focus = ["store-expired"]
    elif case == "main" and group in ("candidate", "baseline", "sql", "semantic"):
        checks["allFiveKindsContribute"] = set(KINDS) <= supported_kinds
        checks["laterQueryUsesEarlierObservation"] = bool(trace["adaptiveLinks"])
    if case == "store":
        expired = group == "rebate-expired"
        for store, amount, threshold in [
            ("shanghai", "14088.75", "100000"),
            ("hangzhou", "84212.80", "100000" if expired else "80000"),
        ]:
            checks[store + "ApplicableThreshold"] = any(
                any(
                    contains(left, amount)
                    and contains(right, threshold)
                    and sign in ({"≥", ">"} if store == "hangzhou" and not expired else {"<", "≤"})
                    for left, sign, right in re.findall(
                        r"([\d,.]+)\s*元\s*([<>≤≥])\s*([\d,.]+)\s*元",
                        (d.get("calculation") or "") + "\n" + d["statement"],
                    )
                )
                and any(s["kind"] == "metric" for s in d["sources"])
                and any(
                    s["kind"] == "knowledge"
                    and s["locator"].startswith("K-31 /")
                    and s["version"] == ("2" if expired else "1")
                    for s in d["sources"]
                )
                for d in supported
            )
    if case == "finance":
        checks["confirmedPaymentsAndRefundTiming"] = {"semantic", "detail"} <= kinds
        dated_values = []
        for d in supported:
            for source in d["sources"]:
                if source["kind"] != "detail":
                    continue
                scope, _, observed = source["excerpt"].partition(" · ")
                try:
                    row = json.loads(scope)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    dated_values.extend(
                        (str(v).partition("（CASE ")[0], observed) for v in row.values()
                    )
        for month, amount in [("07", "8994"), ("08", "4360.60"), ("09", "10356.15")]:
            checks["originalPaymentMonth" + month] = any(
                contains(shown, amount)
                and re.fullmatch(
                    rf"2025-{month}(?:-01(?:[ T]00:00:00(?:[+-]\d\d(?::?\d\d)?)?)?)?"
                    rf"|2025年{int(month)}月",
                    bucket,
                )
                is not None
                for bucket, shown in dated_values
            )
    if case == "inventory-gap":
        checks["datedInventoryCoverageGap"] = any(
            d["status"] == "insufficient"
            and any(
                s["kind"] == "metric"
                and "2025-09-10" in s["scope"]
                and s["excerpt"]
                in {"COVERAGE_UNKNOWN", "SOURCE_DELAYED", "OUTSIDE_SNAPSHOT_COVERAGE"}
                for s in d["sources"]
            )
            for d in details
        )
    missing = {g: [v for v in EXPECTED[g] if not contains(checked_text, v)] for g in focus}
    checks["requiredValuesPresent"] = not any(missing.values())
    checks["claimsAvailable"] = bool(details) or group in ("budget", "unavailable")
    instruction = {
        "baseline": "Count a full pass only after applying all G1–G7 candidate conditions.",
        "sql": "Check available detail observations and stopping point without other sources.",
        "semantic": "Check governed metric scope and explicitly missing detail and documents.",
        "price": "Confirm both whole tiers exceed the internal limit and all funds remain.",
        "budget": "Confirm gaps and that no proposed procurement exceeds the available evidence.",
        "unavailable": "Confirm bounded retries, remaining verified facts and service gap.",
    }
    return dict(
        missingValues=missing,
        hardChecks=checks,
        claims=[
            dict(
                statement=d["statement"],
                status=d["status"],
                kinds=sorted({s["kind"] for s in d["sources"]}),
            )
            for d in details
        ],
        instructorReview=instruction.get(
            group,
            "Check periods, recommendation, approval and causality for each claim.",
        ),
        verdict="needs_instructor_review" if all(checks.values()) else "failed",
    )


@contextmanager
def source_fault(options):
    if options.group != "unavailable":
        yield
        return
    if options.documents.resolve() != Path(os.environ["COMPASS_DOCUMENTS_ROOT"]).resolve():
        raise ValueError("Fault injection requires the server's isolated classroom root")
    bundle = options.documents / "collections" / (options.collection_digest + ".json")
    backup = bundle.with_suffix(".fault-" + str(uuid4()))
    bundle.rename(backup)
    try:
        yield
    finally:
        backup.rename(bundle)


def observe(web, case, index, question, options):
    request = dict(
        **web.scope,
        question=question,
        requestId=str(uuid4()),
        sessionId=None,
        selection={"mode": "fixed", "provider_id": options.model}
        if options.model
        else {"mode": "auto", "provider_id": None},
    )
    accepted = web.fetch("/api/web/v1/analysis/sessions/submit", request)
    run = accepted["runId"]
    session = accepted["sessionId"]
    path = options.output / f"{options.group}-{case}-{index}-{run}.json"
    record = dict(
        runId=run, sessionId=session, case=case, group=options.group, question=question, index=index
    )
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(case, index, run, "accepted", flush=True)
    return collect(web, path, options, inject_fault=True)


def blocked_cleanup(run):
    # A fenced worker exposes no new business result. The durable ledger records whether
    # cleanup is unresolved even when the last public packet still says "running".
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    if "/compass_agent_test_" not in url:
        raise ValueError("Classroom database required")
    with psycopg.connect(url) as db:
        row = db.execute(
            "SELECT cleanup_status, recovery_status FROM agent_run WHERE id=%s", (UUID(run),)
        ).fetchone()
    return row == ("unconfirmed", "blocked")


def collect(web, path, options, *, inject_fault=False):
    record = json.loads(path.read_text())
    run, session = record["runId"], record["sessionId"]
    start = time.monotonic()

    def save():
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n")

    base = "/api/web/v1/analysis/"
    with source_fault(options) if inject_fault else nullcontext():
        while True:
            if blocked_cleanup(run):
                state = "cleanup_unconfirmed"
                break
            item = web.fetch(base + "sessions/" + session, query=web.scope)
            state = item["executionStatus"]
            if (
                state in {"completed", "failed", "paused", "waiting", "canceled"}
                or time.monotonic() - start > options.timeout
            ):
                break
            time.sleep(5)
    trace = instructor_trace(run)
    record.update(status=state, wallSeconds=trace["wallSeconds"], trace=trace)
    save()
    message = web.fetch(base + f"sessions/{session}/messages", query={**web.scope, "runId": run})
    answers = {}
    for packet in message["packets"]:
        event = packet["event"]
        if event["type"] == "message.upsert" and event["payload"]["role"] == "assistant":
            item = event["payload"]
            previous = answers.get(item["messageId"])
            if not previous or item["revision"] > previous["revision"]:
                answers[item["messageId"]] = item
    record["answer"] = "\n".join(
        item["content"]["text"] for item in sorted(answers.values(), key=lambda x: x["ordinal"])
    )
    record.pop("citationError", None)
    details = []
    try:
        url = base + "runs/" + run + "/citations"
        anchors = web.fetch(url, query=web.scope)
        for a in anchors["anchors"]:
            details.append(web.fetch(url + "/" + a["citationId"], query=web.scope))
    except ValueError as err:
        record["citationError"] = str(err)
    record["citations"] = details
    record["sourceKinds"] = sorted({s["kind"] for d in details for s in d["sources"]})
    record["trace"]["adaptiveLinks"] = adaptations(record["trace"]["calls"], record["question"])
    record.update(grade(record))
    save()
    print(record["case"], run, state, record["verdict"], flush=True)
    return record


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--case", choices=["main", *QUESTIONS], default="main")
    p.add_argument("--group", choices=GROUPS, default="candidate")
    p.add_argument("--output", type=Path)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--recount", type=Path, nargs="+", help="Recount physical model receipts")
    mode.add_argument(
        "--resume",
        type=Path,
        nargs="+",
        help="Collect existing accepted runs without submitting questions",
    )
    p.add_argument(
        "--documents",
        type=Path,
        help="Republish the selected experiment's workspace policy before submitting",
    )
    p.add_argument("--model", default=os.environ.get("COMPASS_SEARCH_MODEL"))
    p.add_argument("--count", type=int)
    p.add_argument("--start-index", type=int, default=0)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--revision", type=int, default=1)
    opt = p.parse_args()
    if opt.recount:
        if not opt.output:
            p.error("--recount requires a separate --output directory")
        recount_receipts(opt.recount, opt.output)
        return
    if opt.resume:
        web = ClassroomWeb()
        for path in opt.resume:
            collect(web, path, opt)
        return
    if not opt.output:
        p.error("Supply --output for new runs")
    if not opt.documents and opt.group != "candidate":
        p.error("The experimental groups require --documents")
    opt.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if opt.documents:
        group = opt.group
        exclude = {
            "sql": [k for k in KINDS if k != "detail"],
            "semantic": [k for k in KINDS if k != "semantic"],
        }.get(group, [group.removeprefix("without-")] if group.startswith("without-") else [])
        opt.collection_digest = publish(
            opt.documents,
            exclude=exclude,
            skill=group not in ("baseline", "sql", "semantic"),
            variant=group if group in ("price", "rebate-expired") else "default",
            limit=2 if group == "budget" else None,
            revision=opt.revision,
        )
    count = opt.count or (
        6 if opt.case == "main" and opt.group in ("candidate", "baseline", "sql", "semantic") else 1
    )

    if opt.group == "unavailable" and count != 1:
        p.error("The isolated source fault allows one Run per invocation")
    web = ClassroomWeb()

    def execute(i):
        q = MAIN[i % len(MAIN)] if opt.case == "main" else QUESTIONS[opt.case]
        return observe(web, opt.case, i, q, opt)

    with ThreadPoolExecutor(max_workers=opt.parallel) as pool:
        rows = list(pool.map(execute, range(opt.start_index, opt.start_index + count)))
    durations = [r["wallSeconds"] for r in rows if r["wallSeconds"] is not None]
    print(
        json.dumps(
            dict(
                runs=len(rows),
                verdicts=dict(Counter(r["verdict"] for r in rows)),
                medianSeconds=statistics.median(durations) if durations else None,
                slowestSeconds=max(durations, default=None),
            )
        )
    )
    if any(r["verdict"] == "failed" for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
