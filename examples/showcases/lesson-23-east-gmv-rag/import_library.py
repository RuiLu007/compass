"""Adopt the published classroom files and vectors without overwriting later edits."""

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
from publish import CORPUS, INDEX, TENANT, WORKSPACE, collection, encode, sha


def main():
    release = collection()
    digest = sha(encode(release))
    root = Path(os.environ["COMPASS_DOCUMENTS_ROOT"])
    binding = json.loads((root / "bindings" / TENANT / f"{WORKSPACE}.json").read_bytes())
    if binding["collectionDigest"] != digest:
        raise ValueError("Publish this corpus in the target workspace first")
    base_id = uuid5(NAMESPACE_URL, f"compass/{TENANT}/{WORKSPACE}/east-gmv-classroom")
    with psycopg.connect(os.environ["WEB_MIGRATION_DATABASE_URL"]) as db:
        db.execute("SET LOCAL ROLE compass_web_owner")
        db.execute(
            """INSERT INTO web.knowledge_base (id,tenant_id,workspace_id,name,description)
               VALUES (%s,%s,%s,'华东经营分析资料库','支付口径、渠道活动、库存与门店经营记录')
               ON CONFLICT (id) DO NOTHING""",
            (base_id, TENANT, WORKSPACE),
        )
        count = 0
        for path, doc in zip(sorted(CORPUS.glob("*.md")), release["documents"], strict=True):
            content = path.read_bytes()
            file_id = uuid5(base_id, doc["docId"] + "/" + doc["version"])
            inserted = db.execute(
                """INSERT INTO web.knowledge_file
                   (id,base_id,name,format,original,char_count,status,source_id,source_version,origin_digest)
                   VALUES (%s,%s,%s,'md',%s,%s,'ready',%s,%s,%s) ON CONFLICT (id) DO NOTHING
                   RETURNING id""",
                (
                    file_id,
                    base_id,
                    path.name,
                    content,
                    len(content.decode()),
                    doc["docId"],
                    doc["version"],
                    digest,
                ),
            ).fetchone()
            if not inserted:
                continue
            for ordinal, section in enumerate(doc["sections"], 1):
                result = db.execute(
                    """INSERT INTO web.knowledge_chunk
                       (file_id,ordinal,section_id,heading,body,section_hash,embedding)
                       SELECT %s,%s,section_id,%s,%s,section_hash,embedding
                       FROM web.document_vectors
                       WHERE tenant_id=%s AND collection_digest=%s
                       AND index_profile=%s AND doc_id=%s
                       AND version=%s AND section_id=%s AND section_hash=%s""",
                    (
                        file_id,
                        ordinal,
                        section["heading"],
                        section["text"],
                        TENANT,
                        digest,
                        INDEX,
                        doc["docId"],
                        doc["version"],
                        section["sectionId"],
                        section["sectionHash"],
                    ),
                )
                if result.rowcount != 1:
                    raise ValueError("Published vector or section checksum missing")
                count += 1
        print(f"Imported {count} chunks into {base_id}")


if __name__ == "__main__":
    main()
