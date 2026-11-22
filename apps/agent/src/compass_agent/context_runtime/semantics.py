"""A scoped, immutable catalog of approved metric definitions."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from compass_agent.models.errors import ModelError


def business_period(question: str, as_of: date):
    """Resolve explicit dates or one complete calendar period against Run admission time."""
    values = []
    for value in re.findall(r"(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])", question):
        try:
            values.append(date.fromisoformat(value))
        except ValueError:
            return None
    if values:
        return min(values), max(values)
    week_start = as_of - timedelta(days=as_of.weekday())
    if "上周" in question:
        return week_start - timedelta(days=7), week_start - timedelta(days=1)
    if "本周" in question:
        return week_start, as_of
    if "上月" in question or "上个月" in question:
        last_month = as_of.replace(day=1) - timedelta(days=1)
        return last_month.replace(day=1), last_month
    if "本月" in question:
        return as_of.replace(day=1), as_of
    return None


def report_variables(question, as_of, scope_profile):
    period = business_period(question, as_of)
    from compass_agent.context_runtime.profiles import ASSEMBLY

    match = re.search(
        r"(?:单位(?:为|是|[：:])\s*([\w%％/·]{1,16})(?=[，。；、\s]|$)|以\s*([\w%％/·]{1,16}?)\s*为单位)",
        question,
    )
    return {
        "period": (period[0].isoformat() + " 至 " + period[1].isoformat())
        if period
        else "本次任务的统计期间（范围待核对）",
        "unit": (match[1] or match[2]) if match else "已核实口径的计量单位",
        "audience": ASSEMBLY["scopeProfiles"][scope_profile]["audience"],
    }


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric_id: str = Field(alias="metricId", min_length=1, max_length=96)
    name: str = Field(min_length=1, max_length=128)
    aliases: tuple[str, ...] = ()
    definition: str = Field(min_length=1, max_length=2048)
    tenant_id: UUID = Field(alias="tenantId")
    workspace_id: UUID = Field(alias="workspaceId")
    object: str
    grain: str
    unit: str
    aggregation: str
    dimensions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    numerator: str | None = None
    denominator: str | None = None
    source: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=96)
    valid_from: date | None = Field(None, alias="validFrom")
    valid_until: date | None = Field(None, alias="validUntil")

    @property
    def required_metrics(self):
        return tuple(
            dict.fromkeys(d for d in (*self.dependencies, self.numerator, self.denominator) if d)
        )

    @model_validator(mode="after")
    def dates(self):
        if any(
            not s.strip() for s in (self.name, self.object, self.unit, self.grain, *self.aliases)
        ):
            raise ValueError("blank metric label")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("invalid metric interval")
        return self


class Dictionary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str
    metrics: tuple[Metric, ...] = Field(max_length=20000)

    @model_validator(mode="after")
    def validate_graph(self):
        graph = {(m.tenant_id, m.workspace_id, m.metric_id): m for m in self.metrics}
        if len(self.metrics) != len(graph):
            raise ValueError("duplicate metric")
        edges = {key: [(*key[:2], dep) for dep in m.required_metrics] for key, m in graph.items()}
        if any(node not in graph for deps in edges.values() for node in deps):
            raise ValueError("unknown metric dependency")
        try:
            tuple(TopologicalSorter(edges).static_order())
        except CycleError as exc:
            raise ValueError("cyclic metric dependency") from exc
        return self

    def digest(self):
        value = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
        return hashlib.sha256(value).hexdigest()

    def match(self, question, scope, as_of, *, allowed_sources: set[str]):
        scoped = [
            m
            for m in self.metrics
            if m.tenant_id == scope.tenant_id
            and m.workspace_id == scope.workspace_id
            and m.source in allowed_sources
        ]
        start, end = business_period(question, as_of) or (as_of, as_of)
        usable = {
            m.metric_id: m
            for m in scoped
            if (not m.valid_from or m.valid_from <= start)
            and (not m.valid_until or m.valid_until >= end)
        }
        text = normalize(question)
        query_terms = terms(text)
        named, ranked = {}, {}
        excluded = []
        for m in usable.values():
            labels = {normalize(word) for word in (m.name, *m.aliases)}
            matches = {label for label in labels if label in text}
            if matches:
                named[m.metric_id] = matches
            else:
                score = len(
                    query_terms & terms(" ".join((m.name, m.object, m.definition, *m.dimensions)))
                )
                if score >= 2:
                    ranked[m.metric_id] = score
        candidates = (
            sorted(named)
            if named
            else sorted(k for k, score in ranked.items() if score == max(ranked.values()))
        )
        chosen, records = [], {}
        for key in candidates[:20]:
            metric = usable[key]
            if any(
                "按" + grain in text and normalize(metric.grain) != grain
                for grain in ("天", "周", "月", "季", "年")
            ):
                excluded.append((key, "grain"))
                continue
            requested_unit = re.search(r"(?:单位为|单位是|单位[:：])\s*([\w%元笔人数件单]+)", text)
            if requested_unit and not requested_unit.group(1).startswith(normalize(metric.unit)):
                excluded.append((key, "unit"))
                continue
            closure, todo = {}, [key]
            while todo:
                current = todo.pop()
                if current in closure:
                    continue
                if current not in usable or len(closure) >= 40:
                    break
                closure[current] = usable[current]
                todo.extend(usable[current].required_metrics)
            else:
                if len(records.keys() | closure.keys()) <= 40:
                    records.update(closure)
                    chosen.append(key)
                    continue
            excluded.append((key, "unavailable_dependency"))
        shared_label = any(
            named.get(a, set()) & named.get(b, set())
            for i, a in enumerate(chosen)
            for b in chosen[i + 1 :]
        )
        status = (
            "no_match"
            if not chosen
            else "unique"
            if len(chosen) == 1
            else "ambiguous"
            if shared_label or not named
            else "multiple"
        )
        return MetricMatch(status, tuple(chosen), tuple(records.values()), tuple(excluded))

    def recall(self, question, scope, as_of, *, allowed_sources):
        return self.match(question, scope, as_of, allowed_sources=allowed_sources).records


@dataclass(frozen=True, slots=True)
class MetricMatch:
    status: str
    candidates: tuple[str, ...]
    records: tuple[Metric, ...]
    excluded: tuple[tuple[str, str], ...]


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


def terms(text):
    words = re.findall(r"[a-z0-9_]+|[㐀-鿿]+", normalize(text))
    return {
        part
        for word in words
        for part in (
            (word,) if word.isascii() else (word[i : i + 2] for i in range(max(1, len(word) - 1)))
        )
    }


class MetricCatalog:
    """A directory may retain earlier content-addressed policies alongside current.json."""

    def __init__(self, path=""):
        self.active = None
        self.by_digest = {}
        if path:
            target = Path(path)
            paths = sorted(target.glob("*.json")) if target.is_dir() else [target]
            current = target / "current.json" if target.is_dir() else target
            if current not in paths:
                raise ValueError("active metric dictionary missing")
            for item in paths:
                catalog = Dictionary.model_validate_json(item.read_bytes())
                digest = catalog.digest()
                self.by_digest[digest] = catalog
                if item == current:
                    self.active = digest

    def resolve(self, digest):
        if digest is None:
            return None
        if digest not in self.by_digest:
            raise ModelError("context_dictionary_unavailable", 503)
        return self.by_digest[digest]
