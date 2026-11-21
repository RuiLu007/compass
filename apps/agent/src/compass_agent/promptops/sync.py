"""Explicit, transactional source upgrade; immutable historical publications are retained."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from compass_agent.promptops import catalog, service
from compass_agent.promptops.entities import PromptFile, PromptGeneration, PromptVersion, now
from compass_agent.promptops.errors import PromptError
from compass_agent.promptops.layout import MEMBERS, dumps, replace_member
from compass_agent.promptops.structured import parse, refresh_tool_schema
from compass_agent.promptops.template import validate_template


def plan(tx, pointer):
    previous = service.active(tx, pointer)
    before = previous.manifest if previous else {}
    assets = catalog.bundled()
    rows = {row.path: row for row in tx.scalars(select(PromptFile))}
    updates = {}
    for path, source in assets.items():
        row = rows.get(path)
        online = service.version_row(tx, path, before[path]).body if path in before else None
        if row and online is not None:
            baseline = row.last_import_body
            if baseline is not None and source != baseline and online not in {source, baseline}:
                raise PromptError(
                    "source_conflict", f"{path} 的源码与已发布内容均有修改，请先合并。", 409
                )
            published = online if baseline is None or online != baseline else source
            pending = row.draft_body
        else:
            published, pending = source, row.draft_body if row else None
            if path.endswith(".json"):
                document = parse(source)
                draft_members = {}
                for legacy, member in MEMBERS.items():
                    if member["path"] != path or legacy not in rows:
                        continue
                    old = rows[legacy]
                    if legacy in before:
                        body = service.version_row(tx, legacy, before[legacy]).body
                        if old.last_import_body is None or body != old.last_import_body:
                            replace_member(document, legacy, body)
                    if old.draft_body is not None:
                        draft_members[legacy] = old.draft_body
                published = dumps(document)
                if draft_members:
                    for legacy, body in draft_members.items():
                        replace_member(document, legacy, body)
                    pending = dumps(document)
        if path.startswith("tools/"):
            published = refresh_tool_schema(path, published)
            if pending is not None:
                pending = refresh_tool_schema(path, pending)
        validate_template(path, published)
        if pending is not None and path.endswith(".json"):
            validate_template(path, pending)
        updates[path] = (source, published, pending)
    return previous, before, rows, updates


def synchronize(tx: Session, actor: UUID | None = None) -> dict:
    actor = actor or UUID(int=0)
    pointer = service.head(tx, lock=True)
    previous, before, rows, updates = plan(tx, pointer)
    manifest = {
        path: number
        for path, number in before.items()
        if path not in MEMBERS and path not in updates
    }
    changed = []
    for path, (source, body, pending) in updates.items():
        row = rows.get(path)
        if row is None:
            row = service.new_file(tx, path, "bundled", actor)
            rows[path] = row
        current = service.version_row(tx, path, before[path]).body if path in before else None
        number = before.get(path)
        if current != body:
            row.max_version += 1
            number = row.max_version
            tx.add(
                PromptVersion(
                    path=path,
                    version=number,
                    body=body,
                    byte_count=len(body.encode()),
                    sha256=catalog.digest_text(body),
                    created_by=actor,
                    note="源码同步与资产聚合",
                )
            )
            row.active_epoch += 1
            changed.append(path)
        manifest[path] = number
        if (row.last_import_body, row.draft_body) != (source, pending) or current != body:
            row.edit_rev += 1
            row.updated_by, row.updated_at = actor, now()
        row.origin = "bundled"
        row.last_import_body, row.last_import_hash = source, catalog.digest_text(source)
        row.draft_body = pending
        row.draft_base_version = number if pending is not None else None
        row.draft_base_epoch = row.active_epoch if pending is not None else None
    retired = set(before).intersection(MEMBERS)
    for path in retired:
        row = rows[path]
        row.draft_body = row.draft_base_version = row.draft_base_epoch = None
        row.edit_rev += 1
        row.active_epoch += 1
    policy = catalog.current_policy()
    create = (
        previous is None
        or manifest != before
        or policy != (previous.policy_version, previous.policy_digest)
    )
    if create:
        generation = PromptGeneration(
            parent_id=pointer.generation,
            manifest=manifest,
            manifest_digest=catalog.canonical_hash(manifest),
            policy_version=policy[0],
            policy_digest=policy[1],
            action="all",
            snapshot_no=pointer.last_snapshot_no + 1,
            created_by=actor,
            note="结构化资产升级：场景目录、工具契约和成组指令",
        )
        if tx.get_bind().dialect.name == "sqlite":
            generation.id = (pointer.generation or 0) + 1
        tx.add(generation)
        tx.flush()
        pointer.generation = generation.id
        pointer.last_snapshot_no += 1
    tx.flush()
    return {
        "generation": pointer.generation,
        "snapshot_no": pointer.last_snapshot_no,
        "assets": len(manifest),
        "retired": len(retired),
        "changed": changed,
        "drafts": [p for p, (_, _, pending) in updates.items() if pending is not None],
        "created": create,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="保存备份后事务提交；省略时仅预览")
    parser.add_argument(
        "--reset-runtime-policy", action="store_true", help="停止服务后允许新策略注册"
    )
    parser.add_argument("--backup-dir", default="work/promptops-assets-upgrade")
    parser.add_argument(
        "--write-sources", action="store_true", help="在工程根目录重新导出工具 JSON 的代码 Schema"
    )
    args = parser.parse_args()
    if args.write_sources:
        root = Path("apps/agent/src/compass_agent/prompts")
        if not root.is_dir():
            raise PromptError("source_unavailable", "请在 Compass 工程根目录导出工具声明。")
        for path, body in catalog.bundled().items():
            if path.startswith("tools/"):
                (root / path).write_text(body)
    from compass_agent.database import get_engine

    with Session(get_engine()) as tx, tx.begin():
        pointer = service.head(tx, lock=True)
        _, before, _, updates = plan(tx, pointer)
        summary = {
            "previous_generation": pointer.generation,
            "source_assets": len(updates),
            "retired": len(set(before).intersection(MEMBERS)),
            "drafts": [p for p, (_, _, body) in updates.items() if body is not None],
        }
        if not args.apply:
            print(dumps(summary))
            return
        if args.reset_runtime_policy:
            from compass_agent.runtime.deployment import MODE_LOCK

            tx.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MODE_LOCK})
            active = tx.scalar(
                text(
                    "SELECT count(*) FROM agent_run_job "
                    "WHERE status IN ('leased','reclaiming') AND lease_expires_at > now()"
                )
            )
            if active:
                raise PromptError(
                    "active_workers", "仍有有效执行租约，请先停止受管服务并等待租约释放。", 409
                )
        target = Path(args.backup_dir) / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = {}
        for model in (PromptFile, PromptVersion, PromptGeneration):
            backup[model.__tablename__] = [
                dict(row) for row in tx.execute(select(model.__table__)).mappings()
            ]
        backup["head"] = summary
        target.write_text(json.dumps(backup, default=str, ensure_ascii=False, indent=2))
        target.chmod(0o600)
        result = synchronize(tx)
        if args.reset_runtime_policy:
            tx.execute(
                text(
                    "UPDATE agent_runtime_control SET policy_sha256 = NULL WHERE key = 'execution'"
                )
            )
        print(dumps({**result, "backup": str(target)}))


if __name__ == "__main__":
    main()
