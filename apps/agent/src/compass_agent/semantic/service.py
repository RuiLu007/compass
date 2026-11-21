"""Semantic tools operate on a Run pin and private, session-bound references."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select

from compass_agent.context_runtime.source_access import record_source, record_sources
from compass_agent.data_connections.connections import DataConnections
from compass_agent.models.errors import ModelError
from compass_agent.runtime.private_state import digest
from compass_agent.semantic import client
from compass_agent.semantic.entities import SemanticMetricOffer
from compass_agent.semantic.execution import (
    calculate,
    choose_connection,
    group_totals,
    missing_group_is_zero,
    parameter_feedback,
    period_comparison,
    previous_equal_window,
)
from compass_agent.tools.contracts import ToolRejected


def effective_cutoff(accepted_at, snapshot, historical_as_of=None):
    cutoff = accepted_at.astimezone(UTC)
    if snapshot is not None:
        try:
            known = datetime.fromisoformat(snapshot["knownAt"])
            if known.tzinfo is None:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ToolRejected("semantic_source_unavailable") from None
        cutoff = min(cutoff, known.astimezone(UTC))
    if historical_as_of is not None:
        try:
            selected = datetime.fromisoformat(historical_as_of)
            if selected.tzinfo is None or selected > cutoff:
                raise ValueError
        except (TypeError, ValueError):
            raise ToolRejected("invalid_parameters") from None
        return selected.astimezone(UTC)
    return cutoff


class RegistryTools:
    def __init__(self, journal, trusted):
        self.journal = journal
        self.trusted = trusted
        self.store = journal.store
        self.pins = (journal.context_binding or {}).get("registry", {}).get("pins", [])

    async def api(self, op, args=None):
        if snapshot := getattr(self.journal, "search_snapshot", None):
            from compass_agent.documents.client import verify

            if "semantic" not in snapshot["kinds"] or not await asyncio.to_thread(
                verify,
                self.store.source_settings,
                self.trusted,
                self.journal.run_id,
                kinds=["semantic"],
            ):
                raise ModelError("source_access_denied", 403)
        return await asyncio.to_thread(
            client.request,
            self.store.source_settings,
            self.trusted,
            self.journal.run_id,
            op,
            args,
            self.pins,
        )

    def put(self, kind, key, data):
        with self.store.transaction() as tx:
            session, run = self.store.lock(tx, self.journal.run_id, self.trusted)
            self.store.require_epoch(run, self.journal.epoch)
            return self.put_locked(tx, session, kind, key, data)

    def put_locked(self, tx, session, kind, key, data):
        run_id = self.journal.run_id
        ref = uuid5(run_id, kind + ":" + key)
        if not tx.get(SemanticMetricOffer, ref):
            tx.add(
                SemanticMetricOffer(
                    offer_id=ref,
                    session_id=session.id,
                    run_id=run_id,
                    owner_id=session.owner_user_id,
                    kind=kind,
                    status="active",
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                    private_payload=self.store.private.seal(run_id, kind, data),
                )
            )
        return str(ref)

    def get(self, ref, kind, *, session_scope=False):
        with self.store.transaction() as tx:
            session, _ = self.store.lock(tx, self.journal.run_id, self.trusted)
            row = tx.get(SemanticMetricOffer, UUID(ref))
            if (
                not row
                or row.kind != kind
                or row.owner_id != self.trusted.actor_user_id
                or row.session_id != session.id
                or (not session_scope and row.run_id != self.journal.run_id)
                or row.expires_at <= datetime.now(UTC)
            ):
                raise ToolRejected("semantic_reference_expired")
            return self.store.private.open(row.run_id, row.kind, row.private_payload)

    def open_result(self, ref):
        try:
            return ref, self.get(ref, "open", session_scope=True)
        except ToolRejected:
            # Older conversations exposed the database receipt and the saved-result
            # receipt separately. Only the same owner's retained session can map them.
            with self.store.transaction() as tx:
                session, _ = self.store.lock(tx, self.journal.run_id, self.trusted)
                rows = tx.scalars(
                    select(SemanticMetricOffer).where(
                        SemanticMetricOffer.session_id == session.id,
                        SemanticMetricOffer.owner_id == self.trusted.actor_user_id,
                        SemanticMetricOffer.kind == "open",
                        SemanticMetricOffer.expires_at > datetime.now(UTC),
                    )
                )
                for row in rows:
                    data = self.store.private.open(row.run_id, row.kind, row.private_payload)
                    if data.get("source_receipt") == ref:
                        return str(row.offer_id), data
            raise

    async def requested_result(self, args):
        if args.get("source_refs"):
            return None
        if args.get("result_ref"):
            ref, _ = await asyncio.to_thread(self.open_result, args["result_ref"])
            if args.get("trigger", "requested") != "requested":
                return ref
            # A model may repeat a read while handling "save my last answer".
            # That in-progress read was never a result the owner could have referred to.
            try:
                await asyncio.to_thread(self.get, ref, "open")
            except ToolRejected:
                return ref
        if args.get("trigger", "requested") == "requested":
            candidates = await asyncio.to_thread(session_results, self.journal, self.trusted)
            if not candidates:
                raise ToolRejected("semantic_reference_expired")
            chosen = await asyncio.to_thread(self.chosen_result)
            return next(
                (v["result_ref"] for v in candidates if v["result_ref"] == chosen),
                candidates[0]["result_ref"],
            )
        return None

    def approved(self, execution):
        return next(
            (
                p
                for p in self.journal.approved_proposals
                if p.get("callId") == execution.call_id
                and p.get("stepId") == str(execution.step_id)
            ),
            None,
        )

    def formal_query_started(self):
        with self.store.transaction() as tx:
            return (
                tx.scalar(
                    select(SemanticMetricOffer.offer_id)
                    .where(
                        SemanticMetricOffer.run_id == self.journal.run_id,
                        SemanticMetricOffer.kind.in_(("formal", "plan", "result")),
                    )
                    .limit(1)
                )
                is not None
            )

    def metric_ref(self, metric):
        return {
            "kind": "semantic",
            "run": str(self.journal.run_id),
            "pins": self.pins,
            "asset_id": metric["asset_id"],
            "version_id": metric["version_id"],
        }

    def record(self, metric):
        ref = self.metric_ref(metric)
        record_source(self.journal, ref)
        return ref

    def record_page(self, items):
        if items:
            record_sources(self.journal, [self.metric_ref(item) for item in items])

    async def run(self, op, args, execution):
        match op:
            case "search":
                conditions = {k: v for k, v in args.items() if k != "cursor"}
                search_key = digest({"pins": self.pins, "conditions": conditions})
                offset = 0
                if args.get("cursor"):
                    cursor = self.get(args["cursor"], "cursor")
                    if cursor["search"] != search_key:
                        raise ToolRejected("invalid_parameters")
                    offset = cursor["offset"]
                result = await self.api(op, {**conditions, "cursor": offset})
                from compass_agent.semantic.search import accept_page

                budget = (await self.api("context"))["max_search_rounds"]
                page = await asyncio.to_thread(
                    accept_page, self, execution, search_key, offset, result, budget
                )
                await asyncio.to_thread(self.record_page, page["items"])
                return page
            case "definition":
                result = await self.api(op, args)
                await asyncio.to_thread(self.record, result)
                return result
            case "resolve":
                snapshot = getattr(self.journal, "search_snapshot", None)
                cutoff = effective_cutoff(
                    self.journal.accepted_at, snapshot, args.get("historical_as_of")
                )
                intent = {k: v for k, v in args.items() if k != "historical_as_of"}
                if intent.get("parameters"):
                    intent["parameters"] = {**intent["parameters"], "as_of": cutoff.isoformat()}
                try:
                    resolved = await self.api(op, intent)
                except Exception:
                    self.put("formal", "protected", {})
                    raise
                if resolved["status"] != "no_match":
                    self.put("formal", "protected", {})
                if resolved["status"] == "needs_clarification":
                    grant = self.approved(execution)
                    picked = grant.get("choiceId") if grant else None
                    if not picked:
                        saved = await asyncio.to_thread(self.choice, intent["mention"])
                        picked = saved.get("asset_id") if saved else None
                    if not picked:
                        picked = (getattr(self.journal, "registry_selected", None) or {}).get(
                            "asset_id"
                        )
                    if picked not in [v["asset_id"] for v in resolved["options"]]:
                        raise ToolRejected("semantic_choice_required")
                    resolved = await self.api("choice", {**intent, "metric_id": picked})
                    if resolved["status"] == "resolved":
                        self.put(
                            "choice",
                            intent["mention"].strip().casefold(),
                            {"asset_id": picked, "version_id": resolved["metric"]["version_id"]},
                        )
                if resolved["status"] != "resolved":
                    return resolved
                metric = resolved["metric"]
                selected = getattr(self.journal, "registry_selected", None)
                if (
                    selected
                    and metric["asset_id"] in getattr(self.journal, "registry_family", ())
                    and metric["asset_id"] != selected["asset_id"]
                ):
                    return {"status": "use_selected_metric", "metric": selected}
                await asyncio.to_thread(self.record, metric)
                plan = {
                    "asset_id": metric["asset_id"],
                    "version_id": metric["version_id"],
                    "binding_id": resolved["binding_id"],
                }
                if "parameters" not in intent:
                    return {
                        "status": "needs_parameters",
                        "metric": metric,
                        "required": ["start", "end"],
                        "as_of": cutoff.isoformat(),
                    }
                info = await self.api("binding", plan)
                parameters = intent["parameters"]
                if feedback := parameter_feedback(info["binding"]["parameter_schema"], parameters):
                    return feedback
                adjacent = previous_equal_window(parameters)
                plan["parameters"] = parameters
                plan["timezone"] = info["metric"].get("timezone")
                plan["data_release"] = info["binding"]["access_requirements"].get("source_release")
                key = self.put("plan", digest(plan), plan)
                return {
                    "status": "resolved",
                    "query_ref": key,
                    "parameters": parameters,
                    "previous_equal_window": adjacent,
                    "business_timezone": plan["timezone"],
                    "data_release": plan["data_release"],
                    "metric": metric,
                    "manifest_hash": resolved["manifest_hash"],
                }
            case "query":
                return await self.query(args, execution)
            case "discover":
                if ref := await self.requested_result(args):
                    return await self.propose({**args, "result_ref": ref}, execution)
                return await self.api(
                    "discover",
                    {
                        k: v
                        for k, v in args.items()
                        if k in {"source_refs", "owner_scope_id", "usage_scope_id"}
                    },
                )
            case "validate" | "submit":
                return await self.api(op, args)
        raise ToolRejected("invalid_parameters")

    def choice(self, mention):
        key = uuid5(self.journal.run_id, "choice:" + mention.strip().casefold())
        try:
            return self.get(str(key), "choice")
        except ToolRejected:
            return None

    async def report_references(self, records):
        for record in records:
            metric = record.get("metric")
            if metric and record.get("binding_id"):
                await self.api(
                    "reference",
                    {
                        "asset_id": metric["asset_id"],
                        "version_id": metric["version_id"],
                        "binding_id": record["binding_id"],
                        "reference_id": str(self.journal.run_id),
                        "reference_kind": "report",
                    },
                )

    async def query(self, args, execution):
        plan = self.get(args["query_ref"], "plan")
        ids = {k: plan[k] for k in ("asset_id", "version_id", "binding_id")}
        info = await self.api("binding", ids)
        targets = await asyncio.to_thread(
            DataConnections(self.store.engine, self.store.source_settings).bindings_for_run,
            self.trusted.scope,
        )
        from compass_agent.documents.client import restrict_connections

        conn = choose_connection(
            restrict_connections(targets, getattr(self.journal, "search_snapshot", None)),
            info["binding"],
        )
        parameters = dict(args.get("parameters") or plan["parameters"])
        requested_cutoff = parameters.get("as_of")
        expected_cutoff = plan["parameters"]["as_of"]
        snapshot = getattr(self.journal, "search_snapshot", None)
        if snapshot is not None:
            effective_cutoff(self.journal.accepted_at, snapshot, expected_cutoff)
            if info["binding"]["access_requirements"].get("source_release") != snapshot.get(
                "dataRelease"
            ):
                raise ToolRejected("semantic_source_unavailable")
        if requested_cutoff and datetime.fromisoformat(requested_cutoff) != datetime.fromisoformat(
            expected_cutoff
        ):
            raise ToolRejected("semantic_rebind_parameters")
        parameters["as_of"] = expected_cutoff
        if parameters != plan["parameters"]:
            raise ToolRejected("semantic_rebind_parameters")
        if parameters["end"] <= parameters["start"]:
            raise ToolRejected("invalid_parameters")
        if args.get("operation") == "drilldown":
            parent = args.get("parent_group") or parameters.get("filters")
            filters = parameters.get("filters", {})
            if parameters.get("group_by", "total") == "total" or not parent:
                raise ToolRejected("invalid_parameters")
            if isinstance(parent, dict) and any(
                k not in filters or digest(filters[k]) != digest(v) for k, v in parent.items()
            ):
                raise ToolRejected("semantic_rebind_parameters")
        windows = [parameters]
        if args.get("operation") == "compare":
            previous = args.get("comparison") or previous_equal_window(parameters)
            if (
                not previous
                or previous["end"] <= previous["start"]
                or previous.get("group_by", "total") != parameters.get("group_by", "total")
            ):
                raise ToolRejected("invalid_parameters")
            if previous.get("filters", {}) != parameters.get("filters", {}):
                raise ToolRejected("invalid_parameters")
            if previous.get("as_of") and datetime.fromisoformat(
                previous["as_of"]
            ) != datetime.fromisoformat(expected_cutoff):
                raise ToolRejected("semantic_rebind_parameters")
            windows.append({**previous, "as_of": parameters["as_of"]})
        periods = []
        validation = []
        witness = None
        for window in windows:
            part, current_scope = await calculate(conn, info["binding"], window, execution.control)
            if witness is not None and witness != current_scope:
                raise ToolRejected("source_access_denied")
            witness = current_scope
            periods.append(part)
            if "policies" in witness:
                validation.append({"parameters": window, "digest": digest(part)})
        assert witness is not None
        rows = (
            period_comparison(*periods, empty_is_zero=missing_group_is_zero(info))
            if len(periods) == 2
            else periods[0]
        )
        await self.api("binding", ids)
        call_key = execution.call_id + str(execution.step_id)
        source = {
            **self.record(info["metric"]),
            "kind": "semantic_result",
            "binding_id": plan["binding_id"],
            "connection": str(conn.connection_id),
            "revision": conn.revision,
            "witness": witness,
        }
        if validation:
            source["receipt"] = str(uuid5(self.journal.run_id, "result:" + call_key))
        key = self.put(
            "result",
            call_key,
            {"plan": plan, "parameters": parameters, "source": source, "verification": validation},
        )
        await asyncio.to_thread(record_source, self.journal, source)
        await self.api(
            "reference",
            {**ids, "reference_id": str(self.journal.run_id), "reference_kind": "query"},
        )
        if info["metric"]["unit"] == "ratio" and any(
            "numerator" in r.get("detail", {}) for r in rows
        ):
            rows = [
                {
                    **r,
                    "numerator": r["detail"].get("numerator"),
                    "denominator": r["detail"].get("denominator"),
                }
                for r in rows
            ]
        columns = list(rows[0]) if rows else []
        return {
            "resultId": key,
            "calculation_mode": info["binding"]["mode"],
            "metric": info["metric"],
            "manifest_hash": info["manifest_hash"],
            "binding_id": plan["binding_id"],
            "binding_hash": info["binding"]["target_hash"],
            "aggregation": info["aggregation"],
            "data_release": plan["data_release"],
            "connection_revision": conn.revision,
            "columns": columns,
            "rows": [
                [r[k] if not isinstance(r[k], dict) else str(r[k]) for k in columns] for r in rows
            ],
            "unit": info["metric"]["unit"],
            "time_range": windows,
            "period_totals": group_totals(periods, windows, info),
            "as_of": parameters["as_of"],
            "authorized_scope": str(self.trusted.scope.workspace_id),
            "source_scope_hash": digest([str(conn.connection_id), conn.revision, witness]),
            "data_scope": {
                k: witness[k] for k in ("tenant_id", "all_regions", "region_ids") if k in witness
            },
        }

    def chosen_result(self):
        try:
            return self.get(str(uuid5(self.journal.run_id, "selection:open")), "selection")[
                "result_ref"
            ]
        except ToolRejected:
            return None

    async def propose(self, args, execution):
        ref, result = await asyncio.to_thread(self.open_result, args["result_ref"])
        args = {**args, "result_ref": ref}
        from compass_agent.context_runtime.source_access import allows

        if not await asyncio.to_thread(
            allows, self.store, self.trusted, result["refs"], epoch=self.journal.epoch
        ):
            raise ToolRejected("semantic_reference_expired")
        grant = self.approved(execution)
        if not grant:
            raise ToolRejected("semantic_confirmation_required")
        if grant.get("kind") == "semantic_result_choice":
            picked = grant["choiceId"]
            retained = self.get(picked, "open", session_scope=True)
            if not await asyncio.to_thread(
                allows, self.store, self.trusted, retained["refs"], epoch=self.journal.epoch
            ):
                raise ToolRejected("semantic_reference_expired")
            self.put("selection", "open", {"result_ref": picked})
            return {
                "status": "preview_required",
                "result_ref": picked,
                "proposal": retained.get("proposal", {}),
            }
        from compass_agent.schemas import SemanticResultDecision
        from compass_agent.semantic.results import decide_result

        return await asyncio.to_thread(
            decide_result,
            self.store,
            self.trusted,
            self.journal.run_id,
            SemanticResultDecision(
                result_ref=UUID(args["result_ref"]), action="save", fields=grant["fields"]
            ),
            for_tool=True,
            epoch=self.journal.epoch,
        )


def read_sources(store, trusted, refs):
    from compass_agent.semantic.execution import verify_witness

    groups = {}
    for ref in refs:
        groups.setdefault((ref["run"], tuple(ref["pins"])), []).append(ref)
    for (run_id, pins), entries in groups.items():
        from itertools import batched

        unique = {(r["asset_id"], r["version_id"], r.get("binding_id")) for r in entries}
        bindings = {}
        for chunk in batched(unique, 64, strict=False):
            items = [
                {
                    "asset_id": asset,
                    "version_id": version,
                    **({"binding_id": binding} if binding else {}),
                }
                for asset, version, binding in chunk
            ]
            data = client.request(
                store.source_settings,
                trusted,
                UUID(run_id),
                "authorize",
                {"refs": items},
                list(pins),
            )
            bindings.update((b["binding_id"], b) for b in data["bindings"])
        for entry in entries:
            if entry["kind"] == "semantic_result":
                plan = bindings.get(entry["binding_id"])
                if not plan or not asyncio.run(verify_witness(store, trusted, entry, plan)):
                    return False
    return True


def open_digest(result):
    return digest(
        {"columns": list(result.columns), "rows": result.rows, "truncated": result.truncated}
    )


def remember_open(journal, context, args, result, binding):
    if (
        args.get("op") != "query"
        or not result.source_ref
        or (not result.rows and not getattr(journal, "search_snapshot", None))
    ):
        return None
    service = RegistryTools(journal, context.trusted)
    key = str(context.step_id) + context.call_id
    ref = str(uuid5(journal.run_id, "open:" + key))
    witness = result.source_ref
    if witness.get("kind") == "transient":
        witness = {"kind": "semantic_open", "offer": ref, "run": str(journal.run_id)}
    from compass_agent.semantic.extraction import definition_from_query

    question = journal.user_question
    proposal = definition_from_query(args["sql"], result.columns, question)
    signature = digest(
        [
            str(binding.connection_id or binding.workspace_id),
            binding.revision,
            proposal.pop("query_signature"),
        ]
    )
    service.put(
        "open",
        key,
        {
            "refs": [witness],
            "columns": list(result.columns),
            "source_receipt": result.id,
            "created_at": datetime.now(UTC).isoformat(),
            "query": args,
            "connection": str(binding.connection_id or binding.workspace_id),
            "revision": binding.revision,
            "fingerprint": open_digest(result),
            "definition_hash": signature,
            "reusable": proposal["reusable"] and not result.truncated,
            "proposal": proposal,
            "question": question[:320],
        },
    )
    return ref, witness


async def verify_open(store, trusted, ref):
    import json

    from compass_agent.execution_context import current_execution
    from compass_agent.runtime.control import OwnerKey, RunRegistry
    from compass_agent.schemas import ToolCall, ToolCallFunction
    from compass_agent.tools.builtin.database_query import DatabaseResult, query_database_live

    run = store.read_run(UUID(ref["run"]), trusted)
    with store.transaction() as tx:
        row = tx.get(SemanticMetricOffer, UUID(ref["offer"]))
        if (
            not row
            or row.kind != "open"
            or row.run_id != run.id
            or row.session_id != run.session_id
            or row.owner_id != trusted.actor_user_id
        ):
            return False
        data = store.private.open(row.run_id, row.kind, row.private_payload)
    conns = DataConnections(store.engine, store.source_settings).bindings_for_run(trusted.scope)
    binding = next(
        (
            b
            for b in conns
            if str(b.connection_id or b.workspace_id) == data["connection"]
            and b.revision == data["revision"]
        ),
        None,
    )
    if not binding:
        return False
    call = ToolCall(
        id="revalidate",
        function=ToolCallFunction(name="database_query", arguments=json.dumps(data["query"])),
    )
    control = RunRegistry().register(OwnerKey.from_context(trusted), lifetime_s=6)
    # A live permission read may repeat between any two durable steps. Its local result ID
    # must not advance the Run's deterministic ID sequence or open a journal attempt.
    token = current_execution.set(None)
    try:
        result = await control.perform(lambda: query_database_live(call, binding, control))
    finally:
        current_execution.reset(token)
    return isinstance(result, DatabaseResult) and open_digest(result) == data.get("fingerprint")


def session_results(journal, trusted):
    from compass_agent.context_runtime.source_access import allows
    from compass_agent.runtime.entities import AgentRun

    service = RegistryTools(journal, trusted)
    with service.store.transaction() as tx:
        session, _ = service.store.lock(tx, journal.run_id, trusted)
        rows = tx.scalars(
            select(SemanticMetricOffer)
            .join(AgentRun, AgentRun.id == SemanticMetricOffer.run_id)
            .where(
                AgentRun.status == "completed",
                SemanticMetricOffer.session_id == session.id,
                SemanticMetricOffer.owner_id == trusted.actor_user_id,
                SemanticMetricOffer.kind == "open",
                SemanticMetricOffer.expires_at > datetime.now(UTC),
            )
            .order_by(SemanticMetricOffer.expires_at.desc())
            .limit(12)
        ).all()
        results = [
            (
                str(r.offer_id),
                r.status,
                service.store.private.open(r.run_id, "open", r.private_payload),
            )
            for r in rows
        ]
    from compass_agent.semantic.results import invitation_eligibility

    eligible = invitation_eligibility(results, service.store, trusted, journal.run_id)
    return [
        {
            "result_ref": ref,
            "columns": data["columns"],
            "created_at": data["created_at"],
            "status": status,
            "reusable": ref in eligible,
        }
        for ref, status, data in results
        if status != "saved" and allows(service.store, trusted, data["refs"], epoch=journal.epoch)
    ]
