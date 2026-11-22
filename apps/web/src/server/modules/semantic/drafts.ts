import { randomUUID } from "node:crypto";
import { z } from "zod";
import { draftBody, type Finding } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import { ensureShare, personal, scope, semanticAccess, type Context } from "./access";
import { digest, latest, published, targetPartition, version, type Version } from "./catalog";

export async function sources(c: Context) {
  const ids = c.scopes.filter((s) => s.manage || s.use).map((s) => s.id);
  const { rows } = await c.db.query<{
    source_id: string;
    locator: string;
    source_version: string;
    source_kind: string;
    material: { excerpt?: string; priority?: string; responsible?: string };
    owner_scope_id: string | null;
  }>(
    "SELECT source_id,locator,source_version,source_kind,material,owner_scope_id FROM web.semantic_source WHERE owner_scope_id=ANY($1::uuid[]) OR (owner_scope_id IS NULL AND source_kind='package') ORDER BY locator,source_version",
    [ids],
  );
  return rows;
}
export async function dependencies(c: Context, ids: string[], owner: string, asset: string) {
  if (!ids.length) return;
  const readable = await published(c);
  for (const id of ids) {
    const other = readable.find((v) => v.asset_id === id);
    if (
      !other ||
      id === asset ||
      (scope(c, owner).kind === "platform" && scope(c, other.usage_scope_id).kind !== "platform")
    )
      reject("validation_failed", "依赖指标不可用或引用自身。");
  }
  const { rowCount } = await c.db.query(
    `WITH RECURSIVE edges AS (SELECT a.asset_id,rel.to_asset_id FROM web.semantic_assets a
    JOIN LATERAL (SELECT version_id FROM web.semantic_versions WHERE asset_id=a.asset_id ORDER BY version_no DESC LIMIT 1) v ON true
    JOIN web.semantic_relations rel ON rel.from_version_id=v.version_id AND rel.kind='depends_on'),
    walk(id) AS (SELECT unnest($1::uuid[]) UNION SELECT e.to_asset_id FROM walk w JOIN edges e ON e.asset_id=w.id)
    SELECT 1 FROM walk WHERE id=$2`,
    [ids, asset],
  );
  if (rowCount) reject("validation_failed", "依赖关系存在环。");
}
export async function saveDraft(c: Context, raw: unknown, pathDraft?: string, managed = false) {
  const f = draftBody.parse(raw);
  if (!c.can("draft")) reject("forbidden");
  const newest = f.asset_id ? await latest(c, f.asset_id, true) : null;
  let previous = newest;
  // A rollback can make an older version the active starting point for the next revision.
  if (newest?.lifecycle === "published" && newest.digest !== f.expected_digest) {
    const live = (await published(c)).find((v) => v.asset_id === newest.asset_id);
    if (live?.digest === f.expected_digest && live) previous = await version(c, live.version_id);
  }
  const owner = previous?.owner_scope_id ?? f.owner_scope_id ?? (await personal(c));
  if (previous && f.owner_scope_id && f.owner_scope_id !== owner) reject("forbidden");
  const usage =
    f.usage_scope_id ??
    previous?.usage_scope_id ??
    (f.kind === "term" && scope(c, owner).kind === "personal"
      ? (await targetPartition(c, owner, true, true)).id
      : owner);
  ensureShare(c, owner, usage, f.kind);
  if (pathDraft && previous?.draft_id !== pathDraft) reject("conflict");
  if (
    previous &&
    (previous.digest !== f.expected_digest ||
      previous.kind !== f.kind ||
      previous.code !== f.code ||
      previous.domain !== f.domain)
  )
    reject("conflict");
  if (
    f.source_id &&
    !(await sources(c)).some(
      (s) =>
        s.source_id === f.source_id &&
        (!s.owner_scope_id || s.owner_scope_id === usage || s.owner_scope_id === owner),
    )
  )
    reject("forbidden", "来源不在授权范围内。");
  if (f.derived_from && !(await published(c)).some((v) => v.version_id === f.derived_from))
    reject("not_found");
  const asset = previous?.asset_id ?? randomUUID();
  const lineage = previous
    ? (
        await c.db.query<{ to_asset_id: string }>(
          "SELECT to_asset_id FROM web.semantic_relations WHERE from_version_id=$1 AND kind='derived_from'",
          [previous.version_id],
        )
      ).rows.map((row) => row.to_asset_id)
    : [];
  const inputs = f.dependencies.filter((id) => !lineage.includes(id));
  const targetIds = [...new Set([...inputs, ...(f.links ?? []).map((link) => link.target_id)])];
  if (targetIds.length) {
    const checked = await c.db.query(
      "SELECT asset_id FROM web.semantic_assets WHERE asset_id=ANY($1::uuid[]) AND lifecycle<>'archived' ORDER BY asset_id FOR SHARE",
      [targetIds],
    );
    if (checked.rowCount !== targetIds.length)
      reject("validation_failed", "关联资产已移除或不存在。");
  }
  await dependencies(c, inputs, owner, asset);
  let draft = randomUUID() as string;
  if (previous) {
    const {
      rows: [head],
    } = await c.db.query(
      "SELECT open_draft_id,lifecycle FROM web.semantic_assets WHERE asset_id=$1",
      [asset],
    );
    if (!managed && ["business", "data", "approved"].includes(head.lifecycle))
      reject("conflict", "请先退回待审版本再编辑。");
    draft = head.open_draft_id ?? (head.lifecycle === "draft" ? previous.draft_id : draft);
  } else {
    await c.db.query(
      "INSERT INTO web.semantic_assets(asset_id,owner_scope_id,kind,code,domain,responsible_ref,creator_user_id) VALUES ($1,$2,$3,$4,$5,$6,$7)",
      [asset, owner, f.kind, f.code, f.domain, f.responsible, c.actor.userId],
    );
  }
  const linkKind = { entity: "entity_join", term: "denotes", metric: "derived_from" }[f.kind];
  const readable = f.links ? await published(c) : [];
  const links = (f.links ?? []).map(({ target_id, ...rule }) => {
    const target = readable.find((item) => item.asset_id === target_id);
    if (
      !target ||
      (scope(c, owner).kind === "platform" && scope(c, target.usage_scope_id).kind !== "platform")
    )
      reject("validation_failed", "请选择当前可用的关联对象。");
    if (f.kind === "metric" && target.kind !== "entity")
      reject("validation_failed", "请选择计算所用的来源实体。");
    if (f.kind === "term" && target.kind === "term")
      reject("validation_failed", "术语须指向实体或指标。");
    if (
      f.kind === "entity" &&
      (target.kind !== "entity" ||
        !rule.local?.length ||
        rule.remote?.length !== rule.local.length ||
        !rule.cardinality ||
        !rule.use)
    )
      reject("validation_failed", "请补齐关系的连接键、基数与用途。");
    return {
      id: target_id,
      role:
        f.kind === "entity"
          ? [target.code, rule.local?.join(","), rule.remote?.join(",")].join("/")
          : target.code,
      rule: f.kind === "entity" ? { ...rule, target: target.code.replace(/^entity\./, "") } : {},
    };
  });
  if (new Set(links.map((l) => l.id + "/" + l.role)).size !== links.length)
    reject("validation_failed", "关联对象重复。");
  if (
    managed &&
    f.kind === "term" &&
    (!(f.question ?? previous?.definition.question) ||
      !(f.counterexample ?? previous?.definition.counterexample))
  )
    reject("validation_failed", "术语须填写示例问题与易混场景。");
  if (managed && f.kind === "entity") {
    const keys = f.primary_key ?? previous?.definition.primary_key;
    const fields = f.fields ?? previous?.definition.fields;
    if (
      !f.grain ||
      !(f.table ?? previous?.definition.table) ||
      !Array.isArray(keys) ||
      !keys.length ||
      !fields ||
      typeof fields !== "object" ||
      keys.some((k) => !Object.hasOwn(fields, k))
    )
      reject("validation_failed", "实体须填写数据表、粒度和字段字典，主键必须出现在字典中。");
    for (const link of f.links ?? []) {
      const remote = readable.find((v) => v.asset_id === link.target_id)?.definition.fields;
      if (
        link.local?.some((k) => !Object.hasOwn(fields, k)) ||
        !remote ||
        typeof remote !== "object" ||
        link.remote?.some((k) => !Object.hasOwn(remote, k))
      )
        reject("validation_failed", "连接键必须出现在两端实体的字段字典中。");
    }
  }
  const vid = randomUUID();
  const def = {
    ...previous?.definition,
    ...(f.initial_context && { initial_context: f.initial_context }),
    ...(f.kind === "metric" && f.metric_details),
    formula: f.formula,
    event_time: f.event_time,
    timezone: f.timezone,
    aggregation: f.aggregation,
    dimensions: f.dimensions,
    filters: f.filters,
    responsible: f.responsible,
    ...(f.kind === "term" && {
      question: f.question ?? previous?.definition.question,
      counterexample: f.counterexample ?? previous?.definition.counterexample,
    }),
    ...(f.kind === "entity" && {
      table: f.table ?? previous?.definition.table,
      primary_key: f.primary_key ?? previous?.definition.primary_key,
      fields: f.fields ?? previous?.definition.fields,
      visible_time: f.visible_time ?? previous?.definition.visible_time,
      ...(f.links && { joins: links.map((l) => l.rule) }),
    }),
    ...(f.derived_from ? { derived_from: f.derived_from } : {}),
  };
  const hash = digest({
    asset,
    vid,
    usage,
    def,
    name: f.name,
    summary: f.summary,
    grain: f.grain,
    unit: f.unit,
    source: f.source_id ?? null,
    position: f.source_position,
    aliases: f.aliases,
    dependencies: f.dependencies,
  });
  await c.db.query(
    `INSERT INTO web.semantic_versions(version_id,asset_id,version_no,parent_version_id,draft_id,usage_scope_id,editor_user_id,name,summary,grain,unit,schema_version,definition,source_id,source_position,digest)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,1,$12,$13,$14,$15)`,
    [
      vid,
      asset,
      (newest?.version_no ?? 0) + 1,
      previous?.version_id ?? null,
      draft,
      usage,
      c.actor.userId,
      f.name,
      f.summary,
      f.grain,
      f.unit,
      def,
      f.source_id ?? null,
      f.source_position,
      hash,
    ],
  );
  for (const alias of new Set([f.name, ...f.aliases])) {
    await c.db.query(
      "INSERT INTO web.semantic_aliases VALUES ($1,$2,$3,'zh-CN',$4,$5) ON CONFLICT DO NOTHING",
      [vid, asset, usage, normalize(alias), alias],
    );
  }
  if (previous) {
    await c.db.query(
      `INSERT INTO web.semantic_relations(from_version_id,kind,to_asset_id,scope_id,role,rule)
       SELECT $1,kind,to_asset_id,scope_id,role,rule FROM web.semantic_relations
       WHERE from_version_id=$2 AND kind IN ('entity_join','denotes','derived_from')
       AND ($3::text IS NULL OR kind<>$3 OR scope_id<>$4 OR NOT (to_asset_id=ANY($5::uuid[])))`,
      [
        vid,
        previous.version_id,
        f.links ? linkKind : null,
        previous.usage_scope_id,
        readable.map((v) => v.asset_id),
      ],
    );
  }
  for (const l of links)
    await c.db.query("INSERT INTO web.semantic_relations VALUES ($1,$2,$3,$4,$5,$6)", [
      vid,
      linkKind,
      l.id,
      usage,
      l.role,
      l.rule,
    ]);
  for (const dep of new Set(inputs))
    await c.db.query(
      "INSERT INTO web.semantic_relations VALUES ($1,'depends_on',$2,$3,'input','{}')",
      [vid, dep, usage],
    );
  await c.db.query(
    "UPDATE web.semantic_assets SET open_draft_id=$2,lifecycle='draft' WHERE asset_id=$1",
    [asset, draft],
  );
  return { asset_id: asset, version_id: vid, draft_id: draft, digest: hash, state: "draft" };
}
export const normalize = (value: string) =>
  value.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();

export async function saveResultDraft(c: Context, raw: unknown) {
  const f = z
    .strictObject({
      fields: draftBody
        .omit({ asset_id: true, expected_digest: true, derived_from: true })
        .extend({ kind: z.literal("metric").default("metric") }),
      result_ref: z.uuid(),
      fingerprint: z.string().regex(/^[a-f0-9]{64}$/),
      definition_hash: z
        .string()
        .regex(/^[a-f0-9]{64}$/)
        .optional(),
      columns: z.array(z.string().max(160)).max(64),
      captured_at: z.iso.datetime({ offset: true }),
    })
    .parse(raw);
  return idempotent(c, f.fields.request_key, "result-draft", f, async () => {
    const owner = f.fields.owner_scope_id ?? (await personal(c));
    ensureShare(c, owner, f.fields.usage_scope_id ?? owner);
    const locator = "run-result/" + f.result_ref;
    const {
      rows: [src],
    } = await c.db.query(
      "INSERT INTO web.semantic_source(locator,source_version,digest,owner_scope_id,source_kind,material) VALUES($1,$2,$3,$4,'query_receipt',$5) RETURNING source_id",
      [
        locator,
        f.captured_at,
        f.fingerprint,
        owner,
        { columns: f.columns, captured_at: f.captured_at, definition_hash: f.definition_hash },
      ],
    );
    return saveDraft(c, {
      ...f.fields,
      owner_scope_id: owner,
      source_id: src.source_id,
      source_position: locator,
    });
  });
}
export function validateDefinition(v: Version): Finding[] {
  const findings: Finding[] = [];
  const missing = (field: string, val: unknown) => {
    if (typeof val !== "string" || !val.trim())
      findings.push({
        field,
        message: "未填写" + field,
        suggestion: "补齐口径定义后重新校验。",
        severity: "error",
      });
  };
  missing("来源", v.source_id);
  missing("来源位置", v.source_position);
  missing("业务含义", v.summary);
  missing("负责人", v.definition.responsible ?? v.responsible);
  if (v.kind === "metric") {
    for (const [k, val] of Object.entries({
      粒度: v.grain,
      单位: v.unit,
      公式: v.definition.formula,
      业务时间: v.definition.event_time,
      聚合规则: v.definition.aggregation,
      时区: v.definition.timezone,
    }))
      missing(k, val);
    const formula = String(v.definition.formula ?? "");
    const invalidOperator =
      /(?:^|[\s(])(?:insert\s+into|update\s+\S+\s+set|delete\s+from|drop\s+(?:table|view))\b/i;
    const malformed =
      /(?:[+*/=]|(?<!\w)-)\s*$/.test(formula) || /\/\s*0(?:\.0+)?(?:\s*$|\s*[),])/.test(formula);
    if (invalidOperator.test(formula) || malformed) {
      findings.push({
        field: "公式",
        severity: "error",
        message: "公式包含写操作、缺失运算项或固定零分母。",
        suggestion: "填写可核对的只读计算表达式，为比率指定分母规则。",
      });
    }
    try {
      if (v.definition.timezone)
        new Intl.DateTimeFormat("en", { timeZone: String(v.definition.timezone) });
    } catch {
      findings.push({
        field: "时区",
        severity: "error",
        message: "无法识别该时区。",
        suggestion: "选择有效的 IANA 时区。",
      });
    }
  }
  return findings;
}
export async function findings(c: Context, v: Version) {
  const items = validateDefinition(v);
  const visible = await published(c);
  if (visible.some((p) => p.asset_id !== v.asset_id && normalize(p.name) === normalize(v.name)))
    items.push({
      field: "名称",
      severity: "warning",
      message: "可见范围存在同名口径。",
      suggestion: "说明计算或适用范围的差异。",
    });
  return items;
}
export async function discover(c: Context, ids: string[], owner: string, usage: string) {
  ensureShare(c, owner, usage);
  const available = await sources(c);
  return ids.map((id) => {
    const source = available.find((s) => s.source_id === id);
    if (!source) reject("not_found");
    return {
      source_ref: id,
      position: source.locator,
      material: source.material.excerpt ?? "",
      source_version: source.source_version,
    };
  });
}
export async function idempotent<T>(
  c: Context,
  key: string,
  action: string,
  input: unknown,
  work: () => Promise<T>,
) {
  const hash = digest({ action, input });
  await c.db.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [
    `${c.actor.userId}/${c.target.workspaceId}/${key}`,
  ]);
  const {
    rows: [prior],
  } = await c.db.query(
    "SELECT request_digest,receipt FROM web.semantic_event WHERE actor_id=$1 AND workspace_id=$2 AND request_key=$3",
    [c.actor.userId, c.target.workspaceId, key],
  );
  if (prior) {
    c.scopes = (await semanticAccess(c.db, c.actor, c.target)).scopes;
    if (prior.request_digest !== hash) reject("conflict", "此请求编号已用于不同内容。");
    const fields = input as Record<string, unknown>;
    const vid = prior.receipt.version_id;
    if (vid) {
      const v = await version(c, vid);
      if (action.startsWith("drafts") || action === "result-draft")
        ensureShare(c, v.owner_scope_id, v.usage_scope_id, v.kind);
      else if (action.startsWith("revisions/") || action === "releases" || action === "trial") {
        const { subject, duty } = await import("./governance");
        const sub = await subject(
          c,
          v,
          typeof fields.binding_id === "string" ? fields.binding_id : undefined,
        );
        const step = action.split("/").at(-1);
        if (step === "submit" && !sub.authority.manage) reject("forbidden");
        if ((step === "business" || step === "data") && !duty(c, v, sub, step)) reject("forbidden");
        if (step === "return" && !(duty(c, v, sub, "business") || duty(c, v, sub, "data")))
          reject("forbidden");
        if (step === "releases" && !duty(c, v, sub, "release")) reject("forbidden");
        if (
          step === "trial" &&
          !(c.can("query") && (sub.authority.manage || duty(c, v, sub, "data")))
        )
          reject("forbidden");
      } else if (action === "bindings") {
        const owner = scope(c, v.owner_scope_id);
        const authority =
          owner.kind === "platform" ? await targetPartition(c, owner.id, true) : owner;
        if (!authority.manage || !c.can("query") || !c.can("govern")) reject("forbidden");
      } else if (!c.scopes.find((s) => s.id === v.owner_scope_id)?.manage) reject("forbidden");
    }
    if (
      ["scopes", "grants", "asset-grants"].includes(action) &&
      !(c.tenantAdmin && c.can("govern"))
    )
      reject("forbidden");
    if (
      action === "sources" &&
      !(await sources(c)).some(
        (s) =>
          s.source_id === prior.receipt.source_id &&
          s.owner_scope_id &&
          scope(c, s.owner_scope_id).manage,
      )
    )
      reject("forbidden");
    if (action.startsWith("releases/")) {
      const {
        rows: [r],
      } = await c.db.query("SELECT partition_id FROM web.semantic_release WHERE release_id=$1", [
        prior.receipt.release_id,
      ]);
      if (!r || !scope(c, r.partition_id).release) reject("forbidden");
    }
    return prior.receipt as T;
  }
  const result = await work();
  const note = z.object({ note: z.string().max(2400).optional() }).safeParse(input);
  await c.db.query(
    "INSERT INTO web.semantic_event(actor_id,workspace_id,request_key,action,request_digest,receipt,note) VALUES ($1,$2,$3,$4,$5,$6,$7)",
    [
      c.actor.userId,
      c.target.workspaceId,
      key,
      action,
      hash,
      result,
      note.success ? (note.data.note ?? "") : "",
    ],
  );
  return result;
}
