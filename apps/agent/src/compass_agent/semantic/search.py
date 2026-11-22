"""Account for distinct searches while replaying the original authorized tool message."""

import json
from uuid import uuid5

from sqlalchemy import func, select

from compass_agent.runtime.private_state import digest
from compass_agent.semantic.entities import SemanticMetricOffer
from compass_agent.tools.contracts import ToolRejected

MAX_SEARCH_ROUNDS = 8


def accept_page(service, execution, search_key, offset, result):
    run_id = service.journal.run_id
    call = f"{execution.step_id}:{execution.call_id}"
    request = digest([search_key, offset])
    authority = digest(result)
    stamp = digest(
        {"search": search_key, "offset": offset, "ids": [m["asset_id"] for m in result["items"]]}
    )
    with service.store.transaction() as tx:
        session, run = service.store.lock(tx, run_id, service.trusted)
        service.store.require_epoch(run, service.journal.epoch)
        row = tx.get(SemanticMetricOffer, uuid5(run_id, "search_call:" + call))
        if row is not None:
            saved = service.store.private.open(run_id, "search_call", row.private_payload)
            if saved["request"] != request:
                raise ToolRejected("invalid_parameters")
            if saved["authority"] is not None and saved["authority"] != authority:
                raise ToolRejected("source_access_denied")
            return json.loads(saved["response"])
        used = tx.scalar(
            select(func.count())
            .select_from(SemanticMetricOffer)
            .where(SemanticMetricOffer.run_id == run_id, SemanticMetricOffer.kind == "search")
        )
        repeated = tx.get(SemanticMetricOffer, uuid5(run_id, "search:" + stamp)) is not None
        if repeated or used >= MAX_SEARCH_ROUNDS:
            response = {"status": "budget_exhausted", "items": []}
            authority = None
        else:
            response = dict(result)
            service.put_locked(tx, session, "search", stamp, {})
            if result.get("cursor") is not None:
                page = {"search": search_key, "offset": result["cursor"]}
                response["cursor"] = service.put_locked(tx, session, "cursor", digest(page), page)
        service.put_locked(
            tx,
            session,
            "search_call",
            call,
            {
                "request": request,
                "authority": authority,
                "response": json.dumps(response, ensure_ascii=False, separators=(",", ":")),
            },
        )
        return response
