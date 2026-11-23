"""Retain code receipts together with their authorized query input."""

import hashlib
import json

from compass_agent.semantic.evidence import result as query_evidence


def calculation(args, payload, refs):
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    tables = list(query_evidence(args, payload, refs))
    if tables:
        for item in tables:
            yield {**item, "parentResultIds": [args["source_query_id"]], "code": args["code"]}
        return
    yield {
        "kind": "artifact",
        "title": "代码执行结果",
        "version": fingerprint,
        "locator": fingerprint,
        "sourceRefs": refs,
        "parentResultIds": [args["source_query_id"]],
        "record": {
            "artifactId": fingerprint,
            "summary": json.dumps(payload, ensure_ascii=False),
            "code": args["code"],
            "inputResultId": args["source_query_id"],
        },
    }
