"""Summarize local Chat receipts, keeping different prompt versions and experiments separate."""

import argparse
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def summarize(files):
    groups = defaultdict(list)
    receipts = {}
    for path in sorted(files):
        row = json.loads(path.read_text())
        if not all(key in row for key in ("runId", "case", "group")):
            continue
        rank = (
            row.get("trace", {}).get("usageSource") == "model_attempt",
            bool(row.get("instructorAssessment")),
            bool(row.get("verdict")),
            path.stat().st_mtime_ns,
        )
        if row["runId"] not in receipts or receipts[row["runId"]][0] < rank:
            receipts[row["runId"]] = (rank, row)
    for _, row in receipts.values():
        trace = row.get("trace", {})
        context = trace.get("context", {})
        snapshot = trace.get("snapshot") or {}
        config = {
            "promptManifest": context.get("manifestDigest"),
            "policy": context.get("policyDigest"),
            "semanticReleases": (context.get("registry") or {}).get("pins"),
            "modelSelection": trace.get("selection"),
            "budget": trace.get("limits"),
            "timeoutSeconds": trace.get("timeoutSeconds"),
            "source": {
                k: snapshot.get(k)
                for k in (
                    "tenantId",
                    "workspaceId",
                    "knownAt",
                    "timezone",
                    "dataRelease",
                    "connectionId",
                    "connectionRevision",
                    "collectionDigest",
                    "maxToolCalls",
                    "skillEnabled",
                    "scenePack",
                    "kinds",
                )
            },
        }
        signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        groups[
            (
                row["group"],
                row["case"],
                context.get("generation"),
                context.get("manifestDigest"),
                signature,
            )
        ].append(row)
    for (group, case, generation, digest, signature), rows in groups.items():
        traces = [row["trace"] for row in rows if "trace" in row]
        durations = [row["wallSeconds"] for row in rows if row.get("wallSeconds") is not None]
        calls = [t["physicalCalls"] for t in traces if "physicalCalls" in t]
        metering = [t for t in traces if t.get("usageSource") == "model_attempt"]
        usage = [t["usage"] for t in metering]
        models = defaultdict(Counter)
        for t in metering:
            for entry in t.get("usageByModel", []):
                key = (entry["providerId"], entry["model"], entry["configVersion"])
                models[key].update(
                    {
                        k: v
                        for k, v in entry.items()
                        if k not in {"providerId", "model", "configVersion"}
                    }
                )
        cost = [u["cost_microusd"] for u in usage if "cost_microusd" in u]
        tokens = [u["tokens"] for u in usage if "tokens" in u]
        reasons = Counter(t["reason"] for t in traces if t.get("reason"))
        statuses = Counter(row.get("status", "accepted") for row in rows)
        by_tool = Counter(
            (c.get("name") or c.get("tool") or "unidentified")
            for t in traces
            for c in t.get("calls", [])
            if c.get("dispatched")
        )
        missing = Counter(k for r in rows for k, vals in r.get("missingValues", {}).items() if vals)
        hard = Counter(
            k for row in rows for k, passed in row.get("hardChecks", {}).items() if not passed
        )
        full = sum(
            (row.get("instructorAssessment") or {}).get("status", row.get("verdict")) == "passed"
            and all(row.get("hardChecks", {}).values())
            for row in rows
        )
        versions = {
            (
                m.get("provider_id"),
                m.get("requested_model"),
                m.get("response_model"),
                m.get("config_version"),
            )
            for t in traces
            for m in t.get("models", [])
        }
        yield dict(
            group=group,
            case=case,
            generation=generation,
            manifestDigest=digest,
            conditionsSha256=signature,
            submitted=len(rows),
            fullPasses=full,
            fullPassRate=round(full / len(rows), 4),
            observedModels=[
                dict(providerId=p, requestedModel=q, responseModel=a, configVersion=v)
                for p, q, a, v in sorted(versions, key=str)
            ],
            status=dict(statuses),
            verdicts=dict(Counter(row.get("verdict", "awaiting_collection") for row in rows)),
            runIds=[row["runId"] for row in rows],
            recordedRunCosts=len(cost),
            fullyMeteredRuns=sum(t.get("usageComplete") is True for t in metering),
            unverifiedLedgerRuns=len(rows) - len(metering),
            unknownAttempts=sum(u.get("unknown_attempts", 0) for u in usage),
            unpricedAttempts=sum(u.get("unpriced_attempts", 0) for u in usage),
            reservedTokens=sum(u.get("reserved_tokens", 0) for u in usage),
            reservedCostUsd=round(sum(u.get("reserved_cost_microusd", 0) for u in usage) / 1e6, 6),
            usageByModel=[
                {"providerId": k[0], "model": k[1], "configVersion": k[2], **v}
                for k, v in sorted(models.items())
            ],
            attributedCostUsd=round(sum(cost) / 1e6, 6) if cost else None,
            meteredRuns=len(tokens),
            attributedTokens=sum(tokens) if tokens else None,
            dispatches=sum(calls) if calls else None,
            slowestSeconds=max(durations, default=None),
            medianSeconds=round(statistics.median(durations), 2) if durations else None,
            repeatedSuccessfulDispatches=sum(
                t.get("repeatedSuccessfulDispatches", 0) for t in traces
            ),
            countsByDispatchedTool=dict(by_tool),
            reasons=dict(reasons),
            missingGroups=dict(missing),
            failedHardChecks=dict(hard),
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("directory", nargs="+", type=Path)
    options = p.parse_args()
    files = [f for directory in options.directory for f in directory.glob("*.json")]
    print(json.dumps(list(summarize(files)), ensure_ascii=False, indent=2))
