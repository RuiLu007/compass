"""Resolve only previously issued, Run-scoped paragraph references."""

import asyncio
import json
from datetime import datetime
from hashlib import sha256
from time import monotonic
from uuid import UUID, uuid5

from compass_agent.context_runtime.source_access import assistant_message, record_sources
from compass_agent.documents.embeddings import INDEX, EmbeddingUnavailable, embed
from compass_agent.runtime.private_state import digest
from compass_agent.semantic.client import read_document_operation
from compass_agent.tools.contracts import ToolRejected


def reference_id(run_id, source):
    return str(uuid5(run_id, "document:" + digest(source)))


class DocumentTools:
    def __init__(self, journal, trusted):
        self.journal = journal
        self.trusted = trusted
        self.store = journal.store
        self.snapshot = journal.search_snapshot
        if not hasattr(journal, "document_pin_lock"):
            journal.document_pin_lock = asyncio.Lock()

    async def pin(self):
        if self.snapshot is not None:
            return
        from compass_agent.documents.client import accept

        async with self.journal.document_pin_lock:
            if self.snapshot is not None:
                return

            async def read():
                return await asyncio.to_thread(
                    accept,
                    self.store.source_settings,
                    self.trusted,
                    self.journal.run_id,
                    protocol=2,
                )

            self.snapshot = await self.journal.local_read(
                "documents/pin", {"protocol": 2}, read, kind="control", yield_boundary=False
            )

    def lookup(self, ref):
        with self.store.transaction() as tx:
            _, run = self.store.lock(tx, self.journal.run_id, self.trusted)
            self.store.require_epoch(run, self.journal.epoch)
            message = assistant_message(tx, run.id)
            return next(
                (
                    r
                    for r in message.source_refs or []
                    if r.get("kind") == "document"
                    and r["runId"] == str(run.id)
                    and reference_id(run.id, r) == str(ref)
                ),
                None,
            )

    async def run(self, op, args, execution):
        started = monotonic()
        if execution.run_id != self.journal.run_id or execution.trusted != self.trusted:
            raise ToolRejected("tool_scope_denied")
        await self.pin()
        assert self.snapshot is not None
        if op == "read":
            source = await asyncio.to_thread(self.lookup, args["ref"])
            if not source:
                raise ToolRejected("document_unavailable")
            args = {"sourceRef": source, "offset": args["offset"]}
        if (
            op == "search"
            and not self.snapshot.get("emptyReason")
            and self.snapshot.get("vectorIndex") == INDEX
        ):
            try:
                query = " ".join(filter(None, (args["query"], args.get("entity"))))
                vectors = await embed(self.store.engine, self.store.source_settings, [query])
                args = {**args, "queryVector": vectors[0]}
            except EmbeddingUnavailable:
                pass  # The Web owner can still serve the authorized lexical search.
        result = await read_document_operation(
            self.store.source_settings, self.trusted, self.journal.run_id, op, args
        )
        execution.require_running()
        rows = self.checked_rows(op, result)
        if not rows and op == "read":
            raise ToolRejected("document_unavailable")
        await asyncio.to_thread(record_sources, self.journal, [r["sourceRef"] for r in rows])
        for r in rows:
            r["ref"] = reference_id(execution.run_id, r["sourceRef"])
        return {
            **result,
            "callId": execution.call_id,
            "elapsedMs": round((monotonic() - started) * 1000),
            "truncated": op == "search",
            "retryable": False,
        }

    def checked_rows(self, op, result):
        assert self.snapshot is not None
        try:
            rows = result["sections"]
            if (
                not isinstance(rows, list)
                or len(rows) > (3 if op == "search" else 1)
                or result["status"] != ("ok" if rows else "empty")
                or result["collectionDigest"] != self.snapshot["collectionDigest"]
                or datetime.fromisoformat(result["knownAt"])
                != datetime.fromisoformat(self.snapshot["knownAt"])
            ):
                raise ValueError
            for row in rows:
                source = row["sourceRef"]
                if (
                    source["kind"] != "document"
                    or (
                        self.snapshot.get("protocol") != 2
                        and row["kind"] not in self.snapshot["kinds"]
                    )
                    or source["runId"] != str(self.journal.run_id)
                    or source["collectionDigest"] != self.snapshot["collectionDigest"]
                    or source["tenant"] != self.snapshot["tenantId"]
                    or source["workspace"] != self.snapshot["workspaceId"]
                    or any(
                        source[k] != row[k]
                        for k in ("docId", "version", "sectionId", "sectionHash")
                    )
                ):
                    raise ValueError
                if op == "read" and sha256(row["text"].encode()).hexdigest() != row["sectionHash"]:
                    raise ValueError
            return rows
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ToolRejected("document_unavailable") from None


def verify_documents(store, trusted, refs):
    from compass_agent.documents.client import verified_access

    groups: dict[str, list] = {}
    for ref in refs:
        if (ref.get("tenant"), ref.get("workspace")) != (
            str(trusted.scope.tenant_id),
            str(trusted.scope.workspace_id),
        ):
            return False
        groups.setdefault(ref["runId"], []).append(ref)
    for identifier, group in groups.items():
        documents = [r for r in group if r["kind"] == "document"]
        kinds = {r["sourceKind"] for r in group if r["kind"] == "search_source"}
        signature = None
        for chunk in _verification_pages(documents):
            current = verified_access(
                store.source_settings, trusted, UUID(identifier), refs=chunk, kinds=kinds
            )
            if current is None or (signature is not None and signature != current):
                return False
            signature = current
    return True


def _verification_pages(refs):
    # Leave room in Web's 64 KiB request for actor, scope, Run, kinds and wrappers.
    # Empty reference sets still recheck the class-only dependency.
    page, size = [], 0
    for ref in refs:
        length = len(json.dumps(ref, ensure_ascii=False, separators=(",", ":")).encode()) + 1
        if len(page) == 64 or size + length > 60 * 1024:
            yield page
            page, size = [], 0
        page.append(ref)
        size += length
    yield page
