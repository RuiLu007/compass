"""Publish the classroom's Markdown as an immutable, OpenAI-indexed Web collection."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from uuid import UUID

import psycopg
import yaml
from compass_agent.database import get_engine
from compass_agent.documents.embeddings import INDEX, EmbeddingUnavailable, embed
from compass_agent.settings import get_settings

CORPUS = Path(__file__).parent / "corpus"
TENANT = "99500000-0000-4000-8000-000000000010"
WORKSPACE = "99500000-0000-4000-8000-000000000021"
KNOWN = "2025-11-04T12:00:00+08:00"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, path)


def chunks(body):
    blocks = re.split(r"^## (.+)$", body, flags=re.M)
    for index, (heading, text) in enumerate(zip(blocks[1::2], blocks[2::2], strict=True), 1):
        part = ""
        number = 1
        for paragraph in re.split(r"\n\s*\n", text.strip()):
            if part and len(part) + len(paragraph) + 2 > 600:
                yield f"s{index}-{number}", heading, part
                number += 1
                part = ""
            part = (part + "\n\n" + paragraph).strip()
        if part:
            yield f"s{index}-{number}", heading, part


def collection():
    docs = []
    for p in sorted(CORPUS.glob("*.md")):
        _, front, body = unicodedata.normalize("NFC", p.read_text()).split("---\n", 2)
        m = yaml.safe_load(front)
        recorded = m["archived_at"]
        history = m["kind"] in {"incident_runbook", "incident_record", "data_handover"}
        edition = dict(
            docId=m["id"],
            title=m["title"],
            issuer=m["owner"],
            version=str(m["version"]),
            # The collection is bound exclusively to East. All authorized classroom
            # analysts in that workspace can read these synthetic teaching materials.
            audienceWorkspaceId=None,
            aliases=[],
            validFrom=m["valid_from"].isoformat(),
            validTo=m["valid_to"].isoformat() if m.get("valid_to") else None,
            kind="history" if history else "knowledge",
            approvedAt=None if history else recorded,
            requiresApproval=not history,
            publishedAt=recorded,
            sections=[
                dict(sectionId=key, heading=title, text=text, sectionHash=sha(text.encode()))
                for key, title, text in chunks(body)
            ],
        )
        if not edition["sections"]:
            raise ValueError(f"Missing sections in {p.name}")
        docs.append(edition)
    if len(docs) != 12 or len({d["docId"] for d in docs}) != len(docs):
        raise ValueError("Expected twelve distinct source documents")
    return dict(schemaVersion=1, tenantId=TENANT, documents=docs, aliases={})


async def publish(args):
    root = args.root
    obj = collection()
    blob = encode(obj)
    digest = sha(blob)
    path = root / "collections" / (digest + ".json")
    if path.exists() and path.read_bytes() != blob:
        raise ValueError("Collection digest conflict")
    atomic(path, blob)
    sections = [(d, s) for d in obj["documents"] for s in d["sections"]]
    print(f"{len(obj['documents'])} documents, {len(sections)} chunks, {digest}", flush=True)
    if args.prepare_only:
        return
    if not args.connection or args.revision < 1:
        raise ValueError("Supply the registered database connection and its revision")
    binding = root / "bindings" / TENANT / (WORKSPACE + ".json")
    binding.parent.mkdir(parents=True, exist_ok=True)
    with binding.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = json.loads(binding.read_bytes()) if binding.exists() else None
        if current and (
            current["connectionId"] not in (args.connection, args.expected_connection)
            or current["dataRelease"] != "east-gmv-october-2025-v1"
        ):
            raise ValueError("Workspace already has another source binding")
        with psycopg.connect(os.environ["WEB_MIGRATION_DATABASE_URL"]) as db:
            db.execute("SET LOCAL ROLE compass_web_owner")
            count = db.execute(
                "SELECT count(*) FROM web.document_vectors WHERE tenant_id=%s "
                "AND collection_digest=%s AND index_profile=%s",
                (TENANT, digest, INDEX),
            ).fetchone()[0]
            if count and count != len(sections):
                raise ValueError("Incomplete existing vector index")
            if not count:
                settings, engine = get_settings(), get_engine()
                for start in range(0, len(sections), 32):
                    batch = sections[start : start + 32]
                    vectors = await embed(
                        engine,
                        settings,
                        [f"{d['title']}\n{s['heading']}\n{s['text']}" for d, s in batch],
                    )
                    with db.cursor() as cur:
                        cur.executemany(
                            """INSERT INTO web.document_vectors
                              (tenant_id,collection_digest,index_profile,doc_id,
                               version,section_id,section_hash,embedding)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::public.vector)""",
                            [
                                (
                                    TENANT,
                                    digest,
                                    INDEX,
                                    d["docId"],
                                    d["version"],
                                    s["sectionId"],
                                    s["sectionHash"],
                                    json.dumps(v),
                                )
                                for (d, s), v in zip(batch, vectors, strict=True)
                            ],
                        )
                    print(f"Embedded {start + len(batch)}/{len(sections)}", flush=True)
        metadata = dict(
            tenantId=TENANT,
            workspaceId=WORKSPACE,
            collectionDigest=digest,
            connectionId=args.connection,
            connectionRevision=args.revision,
            dataRelease="east-gmv-october-2025-v1",
            knownAt=KNOWN,
            timezone="Asia/Shanghai",
            kinds=["knowledge", "history", "detail"],
            policies={
                d["docId"]: dict(enabled=True, audienceWorkspaceId=None) for d in obj["documents"]
            },
            maxToolCalls=52,
            reportFormat="comparison_brief",
            vectorIndex=INDEX,
            skillEnabled=True,
        )
        if current:
            for code, policy in current["policies"].items():
                if code in metadata["policies"]:
                    metadata["policies"][code] = policy
        atomic(binding, encode(metadata))
        print("Published complete index and workspace binding")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--connection", type=lambda s: str(UUID(s)), default=None)
    parser.add_argument("--expected-connection", type=lambda s: str(UUID(s)))
    parser.add_argument("--revision", type=int, default=1)
    try:
        asyncio.run(publish(parser.parse_args()))
    except EmbeddingUnavailable as exc:
        raise SystemExit(f"Index was not published: {exc}") from None
