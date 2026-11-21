"""Publish a checksummed document edition and atomically move this classroom's Run pointer."""

import argparse
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from uuid import UUID

from freeze import RELEASE, ROOT, K

WORKSPACE = "99500000-0000-4000-8000-000000000020"
TENANT = "99500000-0000-4000-8000-000000000010"
OFFER_GROUP = "97300000-0000-4000-8000-000000000023"
MATERIALS = ROOT / "mai-specs/973-lesson-23-agentic-search/spec/materials"
ORIGINALS = (
    (
        "history/east-budget-minutes",
        "H-0912",
        "1",
        "华东渠道周会",
        "2025-09-12T18:00",
        "2025-09-12",
        None,
    ),
    (
        "knowledge/restart-policy-v1",
        "K-17",
        "1",
        "行野运动集团渠道运营",
        "2025-01-02T09:00",
        "2025-01-02",
        "2025-10-01",
    ),
    (
        "knowledge/restart-policy-v2",
        "K-17",
        "2",
        "行野运动集团渠道运营与履约",
        "2025-09-25T17:00",
        "2025-10-01",
        None,
    ),
    (
        "knowledge/recovery-handbook",
        "K-08",
        "1",
        "行野运动集团履约运营",
        "2025-08-01T09:00",
        "2025-08-01",
        None,
    ),
    (
        "knowledge/store-rebate",
        "K-31",
        "1",
        "行野运动集团零售运营",
        "2025-08-29T16:00",
        "2025-08-29",
        None,
    ),
    (
        "external/media-offer",
        "E-1009",
        "2",
        "江海运动媒体",
        "2025-10-09T08:30",
        "2025-10-09",
        "2025-10-10",
    ),
    (
        "external/work-calendar",
        "E-CAL-2025",
        "1",
        "国务院办公厅",
        "2024-11-12T00:00",
        "2025-01-01",
        "2026-01-01",
    ),
)
ALIASES = {
    "几成": "比例 额度",
    "留着": "预留",
    "一档": "档位",
    "多少钱": "金额",
    "媒介方": "媒体",
    "周六": "星期六",
    "上班": "工作日",
}
KINDS = ["semantic", "detail", "knowledge", "history", "external"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        f.write(value)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, path)


def publish(
    root,
    *,
    workspace=WORKSPACE,
    tenant=TENANT,
    group=OFFER_GROUP,
    revision=1,
    exclude=(),
    withdraw=(),
    variant="default",
    skill=True,
    limit=None,
    connection=None,
):
    for identifier in (workspace, tenant, group, connection or workspace):
        UUID(identifier)
    docs, policies = [], {}
    for file, code, version, issuer, published, start, end in ORIGINALS:
        kind = file.split("/")[0]
        body = unicodedata.normalize("NFC", (MATERIALS / (file + ".md")).read_text())
        if variant == "price" and code == "E-1009":
            version, published = "3", "2025-10-09T08:45"
            body = (
                body.replace("修订 2", "修订 3")
                .replace("08:30", "08:45")
                .replace("6,000 元", "9,000 元")
            )
        if variant == "rebate-expired" and code == "K-31":
            version, published = "2", "2025-08-29T17:00"
            body = (
                body.replace("第 1 版", "第 2 版")
                .replace("16:00", "17:00")
                .replace("9 月 1 日至 30 日", "8 月 1 日至 31 日")
            )
        parts = re.split(r"^## (.+)$", body, flags=re.M)
        title, introduction = parts[0].split("\n", 1)
        sections = []
        pairs = [("发布说明", introduction), *zip(parts[1::2], parts[2::2], strict=True)]
        for index, (heading, text) in enumerate(pairs):
            text = text.strip()
            sections.append(
                dict(
                    sectionId=f"s{index}",
                    heading=heading,
                    text=text,
                    sectionHash=sha(text.encode()),
                )
            )
        audience = None if code == "E-CAL-2025" else group if code == "E-1009" else workspace
        doc = dict(
            kind=kind,
            docId=code,
            version=version,
            title=title.removeprefix("# "),
            issuer=issuer,
            publishedAt=published + ":00+08:00",
            requiresApproval=kind == "knowledge",
            approvedAt=published + ":00+08:00" if kind == "knowledge" else None,
            validFrom=start,
            validTo=end,
            audienceWorkspaceId=audience,
            aliases=[],
            sections=sections,
        )
        docs.append(doc)
        policies[code] = dict(enabled=code not in withdraw, audienceWorkspaceId=audience)
    collection = json.dumps(
        dict(schemaVersion=1, tenantId=tenant, aliases=ALIASES, documents=docs), ensure_ascii=False
    ).encode()
    digest = sha(collection)
    bundle = root / "collections" / (digest + ".json")
    if bundle.exists() and bundle.read_bytes() != collection:
        raise ValueError("Immutable collection conflict")
    atomic_json(bundle, collection)
    binding = dict(
        tenantId=tenant,
        workspaceId=workspace,
        knownAt=K.isoformat(),
        timezone="Asia/Shanghai",
        collectionDigest=digest,
        connectionId=connection or workspace,
        connectionRevision=revision,
        dataRelease=RELEASE,
        kinds=[k for k in KINDS if k not in exclude],
        skillEnabled=skill,
        scenePack="retail",
        policies=policies,
    )
    if limit is not None:
        binding["maxToolCalls"] = limit
    atomic_json(
        root / "bindings" / tenant / (workspace + ".json"),
        json.dumps(binding, ensure_ascii=False).encode(),
    )
    return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--workspace", default=WORKSPACE)
    parser.add_argument("--group", default=OFFER_GROUP)
    parser.add_argument("--connection")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--exclude", action="append", choices=KINDS, default=[])
    parser.add_argument(
        "--variant", choices=["default", "price", "rebate-expired"], default="default"
    )
    parser.add_argument("--no-skill", dest="skill", action="store_false")
    parser.add_argument(
        "--withdraw", action="append", choices=sorted({s[1] for s in ORIGINALS}), default=[]
    )
    parser.add_argument("--limit", type=int)
    print(publish(**vars(parser.parse_args())))
