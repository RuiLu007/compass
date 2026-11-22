"""Read immutable prompt generations using the caller's transaction."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from compass_agent.promptops.catalog import canonical_hash, digest_text
from compass_agent.promptops.entities import PromptGeneration, PromptHead, PromptVersion
from compass_agent.promptops.errors import PromptError
from compass_agent.promptops.layout import MEMBERS, asset_path, dumps, extract, replace_member


def current_bodies(engine, paths):
    """Bind auxiliary requests to one publication before a response or SSE starts."""
    from compass_agent.context_runtime.pipeline import exact

    with Session(engine) as tx, tx.begin():
        generation = PromptReader.pin_current(tx)
        info = PromptReader.describe(tx, generation)
        binding = {
            "generation": generation,
            "policyVersion": info.policy_version,
            "policyDigest": info.policy_digest,
        }
        return {p.path: p.body.rstrip("\n") for p in exact(tx, binding, paths)}


@dataclass(frozen=True)
class VersionedPrompt:
    path: str
    version: int
    sha256: str
    body: str
    asset_path: str | None = None
    asset_sha256: str | None = None
    source_members: tuple[dict, ...] = ()

    def receipt(self) -> dict:
        value = {
            "path": self.path,
            "version": self.version,
            "sha256": self.sha256,
            "assetPath": self.asset_path or self.path,
            "assetSha256": self.asset_sha256 or self.sha256,
        }
        if self.source_members:
            value["sourceMembers"] = [dict(member) for member in self.source_members]
        return value


@dataclass(frozen=True)
class GenerationInfo:
    id: int
    policy_version: str
    policy_digest: str
    manifest_digest: str


class PromptReader:
    @staticmethod
    def pin_current(session: Session) -> int:
        value = session.scalar(select(PromptHead.generation).where(PromptHead.id == 1))
        if value is None:
            raise PromptError("baseline_unavailable", "请先整体发布初始提示词。", 503)
        return value

    @staticmethod
    def _generation(session: Session, generation: int) -> PromptGeneration:
        row = session.get(PromptGeneration, generation)
        if (
            row is None
            or not isinstance(row.manifest, dict)
            or not all(
                isinstance(path, str) and type(number) is int and number > 0
                for path, number in row.manifest.items()
            )
            or row.manifest_digest != canonical_hash(row.manifest)
        ):
            raise PromptError("generation_unavailable", "任务绑定的提示词清单不可用。", 503)
        return row

    @classmethod
    def describe(cls, session: Session, generation: int) -> GenerationInfo:
        row = cls._generation(session, generation)
        return GenerationInfo(row.id, row.policy_version, row.policy_digest, row.manifest_digest)

    @classmethod
    def published_paths(cls, session: Session, generation: int) -> frozenset[str]:
        manifest = cls._generation(session, generation).manifest
        return frozenset(manifest) | frozenset(p for p in MEMBERS if asset_path(p) in manifest)

    @classmethod
    def load_exact(
        cls, session: Session, generation: int, paths: Sequence[str]
    ) -> tuple[VersionedPrompt, ...]:
        manifest = cls._generation(session, generation).manifest
        physical = set()
        legacy_tools = {}
        for path in paths:
            if path in manifest:
                physical.add(path)
            elif asset_path(path) in manifest:
                physical.add(asset_path(path))
            elif path.startswith("tools/") or path == "support/routing/scenes.json":
                members = [p for p in MEMBERS if asset_path(p) == path and p in manifest]
                if not members:
                    raise PromptError("missing_prompt", "任务绑定的提示词清单缺少所需文件。", 503)
                legacy_tools[path] = members
                physical.update(members)
            else:
                raise PromptError("missing_prompt", "任务绑定的提示词清单缺少所需文件。", 503)
        if physical.difference(manifest):
            raise PromptError("missing_prompt", "任务绑定的提示词清单缺少所需文件。", 503)
        keys = {(p, manifest[p]) for p in physical}
        if not keys:
            return ()
        rows = session.scalars(
            select(PromptVersion).where(tuple_(PromptVersion.path, PromptVersion.version).in_(keys))
        )
        values = {}
        for row in rows:
            if digest_text(row.body) != row.sha256:
                raise PromptError("corrupt_prompt", "已发布的提示词未通过完整性校验。", 503)
            values[row.path] = VersionedPrompt(row.path, row.version, row.sha256, row.body)
        if len(values) != len(keys):
            raise PromptError("incomplete_generation", "历史提示词版本不完整。", 503)
        result = []
        for path in paths:
            if path in values:
                result.append(values[path])
            elif path in legacy_tools:
                # Old Runs retain their original descriptions and parameter annotations.
                from compass_agent.prompt_assets import factory_text
                from compass_agent.promptops.structured import parse

                document = parse(factory_text(path))
                if path == "support/routing/scenes.json":
                    for scene in document["scenes"].values():
                        scene.pop("sop", None)
                for member in legacy_tools[path]:
                    body = values[member].body
                    replace_member(
                        document, member, body.rstrip("\n") if path.startswith("tools/") else body
                    )
                body = dumps(document)
                primary = next(
                    (
                        p
                        for p in legacy_tools[path]
                        if MEMBERS[p]["keys"] == ["function", "description"]
                    ),
                    legacy_tools[path][0],
                )
                source = values[primary]
                result.append(
                    VersionedPrompt(
                        path,
                        source.version,
                        digest_text(body),
                        body,
                        source.path,
                        source.sha256,
                        tuple(values[p].receipt() for p in legacy_tools[path]),
                    )
                )
            else:
                source = values[asset_path(path)]
                try:
                    body = extract(path, source.body, runtime=True)
                except (KeyError, ValueError, TypeError) as exc:
                    raise PromptError(
                        "invalid_asset", "任务绑定的结构化提示词缺少所需内容。", 503
                    ) from exc
                result.append(
                    VersionedPrompt(
                        path, source.version, digest_text(body), body, source.path, source.sha256
                    )
                )
        return tuple(result)
