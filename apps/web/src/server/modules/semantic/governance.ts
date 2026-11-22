import { z } from "zod";
import { id, releaseBody, reviewBody, text } from "../../../contracts/semantic";
import { reject } from "../../platform/errors";
import { scope, type Context } from "./access";
import type { ScopeView } from "../../../contracts/semantic";
import {
  digest,
  latest,
  published,
  releases,
  targetPartition,
  version,
  type Manifest,
  type Version,
} from "./catalog";
import { findings } from "./drafts";

export const bindingQuery = `SELECT binding_id,version_id,source_ref,mode,target_ref,target_hash,parameter_schema,result_schema,access_requirements,validation_receipt
  FROM web.execution_bindings WHERE version_id=$1 AND tenant_id=$2 AND workspace_id=$3`;
export async function subject(c: Context, v: Version, binding?: string, writing = false) {
  let owner = c.scopes.find((s) => s.id === v.owner_scope_id);
  // A submitted personal revision can enter the responsible audience's review queue.
  // This authority applies only to this version, never to the author's other drafts.
  if (!owner) {
    const partition = c.scopes.find((s) => s.kind === "tenant");
    const audience = c.scopes.find((s) => s.id === v.usage_scope_id);
    const reviewer = v.owner_scope_id === v.usage_scope_id ? partition : audience;
    if (!partition || !reviewer || !(reviewer.business || reviewer.data || reviewer.release))
      reject("not_found");
    const submitted = await c.db.query(
      `SELECT FROM web.semantic_scopes s JOIN web.semantic_reviews r ON r.scope_id=$2
       WHERE s.scope_id=$1 AND s.kind='personal' AND s.tenant_id=$3
       AND r.version_id=$4 AND r.step='submit' LIMIT 1`,
      [v.owner_scope_id, partition.id, c.target.tenantId, v.version_id],
    );
    if (!submitted.rowCount) reject("not_found");
    owner = {
      id: v.owner_scope_id,
      kind: "personal",
      label: "个人",
      use: false,
      manage: false,
      share: false,
      business: false,
      data: false,
      release: false,
    } satisfies ScopeView;
  }
  const isLocal = !!binding && owner.kind === "platform";
  const partition =
    owner.kind === "personal" && !owner.manage
      ? scope(c, c.scopes.find((s) => s.kind === "tenant")!.id)
      : await targetPartition(c, v.owner_scope_id, isLocal, writing);
  const authority = isLocal ? partition : owner;
  const {
    rows: [b],
  } = binding
    ? await c.db.query(bindingQuery + " AND binding_id=$4", [
        v.version_id,
        c.target.tenantId,
        c.target.workspaceId,
        binding,
      ])
    : { rows: [] };
  if (binding && !b) reject("not_found");
  const hash = digest({ version: v.digest, partition: partition.id, binding: b ?? null });
  const { rows: history } = await c.db.query<{ step: string; actor_id: string; note: string }>(
    "SELECT step,actor_id,note FROM web.semantic_reviews WHERE scope_id=$1 AND version_id=$2 AND digest=$3 ORDER BY created_at,review_id",
    [partition.id, v.version_id, hash],
  );
  const last = history.at(-1)?.step;
  const state =
    (
      { submit: "business", business: "data", data: "approved", return: "returned" } as Record<
        string,
        string
      >
    )[last ?? ""] ?? "draft";
  return { authority, partition, b, hash, history, state, isLocal };
}
export function duty(
  c: Context,
  v: Version,
  sub: Awaited<ReturnType<typeof subject>>,
  action: "business" | "data" | "release",
) {
  // A personal draft enters the nominated reviewers' queue only after submission.
  if (sub.authority.kind === "personal") {
    const reviewers =
      v.owner_scope_id === v.usage_scope_id
        ? sub.partition
        : c.scopes.find((s) => s.id === v.usage_scope_id);
    return reviewers?.[action] ?? false;
  }
  return sub.authority[action];
}
export async function inspect(c: Context, v: Version, binding?: string) {
  const sub = await subject(c, v, binding);
  const step = sub.state === "approved" ? "release" : sub.state;
  const canReview =
    (step === "business" || step === "data" || step === "release") && duty(c, v, sub, step);
  if (!sub.authority.manage && !canReview) reject("not_found");
  const {
    rows: [head],
  } = await c.db.query(
    "SELECT generation,stable_id,gray_id FROM web.semantic_release_head WHERE partition_id=$1",
    [sub.partition.id],
  );
  const actions: string[] = [];
  const submittedBy = sub.history.findLast((h) => h.step === "submit")?.actor_id;
  const independent = ![
    v.editor,
    v.creator,
    submittedBy,
    sub.b?.validation_receipt?.author,
  ].includes(c.actor.userId);
  if (sub.authority.manage && ["draft", "returned"].includes(sub.state))
    actions.push("edit", "submit");
  if (sub.state === "business" && duty(c, v, sub, "business") && independent)
    actions.push("business", "return");
  if (
    sub.state === "data" &&
    duty(c, v, sub, "data") &&
    independent &&
    sub.history.at(-1)?.actor_id !== c.actor.userId
  )
    actions.push("data", "return");
  if (sub.state === "approved" && duty(c, v, sub, "release")) actions.push("release");
  const impact = await c.db.query<{ kind: string; reference_id: string }>(
    "SELECT kind,reference_id FROM web.semantic_reference WHERE workspace_id=$1 AND version_id=$2",
    [c.target.workspaceId, v.version_id],
  );
  const live = await published(c);
  const released = !binding && live.some((m) => m.version_id === v.version_id);

  if (released && sub.authority.manage) actions.push("revise");
  const dependent = live.filter((m) => m.dependencies.includes(v.asset_id)).map((m) => m.name);
  const { rows: bindings } = await c.db.query(bindingQuery, [
    v.version_id,
    c.target.tenantId,
    c.target.workspaceId,
  ]);
  return {
    ...v,
    editable: sub.authority.manage,
    actions,
    state: released ? "published" : sub.state,
    generation: head?.generation ?? 0,
    release_id: head?.stable_id ?? null,
    binding_id: sub.b?.binding_id ?? null,
    history: sub.history,
    checks: await findings(c, v),
    impact: { references: impact.rows, dependent, external: "请补充核对未登记的外部查询和报表。" },
    bindings: bindings.map((b) => ({
      binding_id: b.binding_id,
      mode: b.mode,
      source_ref: b.source_ref,
      target_ref: b.target_ref,
      hash: b.target_hash,
    })),
  };
}
export async function review(c: Context, id: string, action: string, raw: unknown) {
  const f = reviewBody.parse(raw);
  const v = await version(c, id);
  const latestVersion = await latest(c, v.asset_id, true);
  if (v.digest !== f.expected_digest || latestVersion.version_id !== v.version_id)
    reject("conflict");
  const sub = await subject(c, v, f.binding_id, true);
  const view = await inspect(c, v, f.binding_id);
  if (!view.actions.includes(action)) reject("forbidden", "当前审批步骤或处理人不符合要求。");
  if (action === "return" && !f.note.trim()) reject("validation_failed", "请填写退回原因。");
  if (action !== "return") {
    const errs = view.checks.filter((r) => r.severity === "error");
    if (errs.length)
      reject("validation_failed", errs.map((i) => i.field + "：" + i.message).join("；"));
    if (
      sub.b &&
      (!sub.b.target_hash ||
        !Object.keys(sub.b.parameter_schema?.properties ?? {}).length ||
        !Object.keys(sub.b.result_schema?.properties ?? {}).length ||
        !sub.b.access_requirements?.protocol)
    )
      reject("validation_failed", "执行绑定缺少参数、结果或来源授权契约。");
    if (action === "data" && sub.b?.validation_receipt?.requires_trial) {
      const trial = await c.db.query(
        "SELECT FROM web.semantic_event WHERE workspace_id=$1 AND action='trial' AND receipt->>'approval_digest'=$2 AND receipt->>'matched'='true' LIMIT 1",
        [c.target.workspaceId, sub.hash],
      );
      if (!trial.rowCount) reject("validation_failed", "请使用独立预期完成该绑定的试算。");
    }
    if (action === "data" && sub.b && !f.note.trim())
      reject("validation_failed", "请记录试算及数据授权核验结果。");
  }
  await c.db.query(
    "INSERT INTO web.semantic_reviews(scope_id,version_id,digest,step,actor_id,note) VALUES ($1,$2,$3,$4,$5,$6)",
    [sub.partition.id, id, sub.hash, action, c.actor.userId, f.note],
  );
  const state = (
    { submit: "business", business: "data", data: "approved", return: "returned" } as Record<
      string,
      string
    >
  )[action];
  if (!sub.isLocal)
    await c.db.query("UPDATE web.semantic_assets SET lifecycle=$2 WHERE asset_id=$1", [
      v.asset_id,
      state,
    ]);
  return { asset_id: v.asset_id, version_id: id, state };
}
export async function lockHead(c: Context, partition: string, expected?: number) {
  await c.db.query(
    "INSERT INTO web.semantic_release_head(partition_id) VALUES ($1) ON CONFLICT DO NOTHING",
    [partition],
  );
  const {
    rows: [h],
  } = await c.db.query("SELECT * FROM web.semantic_release_head WHERE partition_id=$1 FOR UPDATE", [
    partition,
  ]);
  if (expected !== undefined && h.generation !== expected)
    reject("conflict", "发布状态已更新，请刷新并核对目标范围。");
  return h;
}
export async function advance(
  c: Context,
  partition: string,
  gen: number,
  manifest: Manifest,
  audience: string | null,
) {
  const {
    rows: [r],
  } = await c.db.query(
    "INSERT INTO web.semantic_release(partition_id,generation,manifest,digest,audience_id) VALUES ($1,$2,$3,$4,$5) RETURNING release_id",
    [partition, gen, manifest, digest(manifest), audience],
  );
  await c.db.query(
    audience
      ? "UPDATE web.semantic_release_head SET generation=$2,gray_id=$3,audience_id=$4 WHERE partition_id=$1"
      : "UPDATE web.semantic_release_head SET generation=$2,stable_id=$3,gray_id=NULL,audience_id=NULL WHERE partition_id=$1",
    audience ? [partition, gen, r.release_id, audience] : [partition, gen, r.release_id],
  );
  return { release_id: r.release_id as string, generation: gen };
}
// Update the definition in every active channel without promoting a different gray change.
export async function changeDefinition(c: Context, v: Version, remove = false) {
  const partition = await targetPartition(c, v.owner_scope_id, false, true);
  const head = await lockHead(c, partition.id);
  const dependencies: Record<string, string> = {};
  if (!remove) {
    const errors = (await findings(c, v)).filter((f) => f.severity === "error");
    if (errors.length) reject("validation_failed", errors.map((f) => f.message).join("；"));
    const { rows } = await c.db.query<{ to_asset_id: string }>(
      "SELECT to_asset_id FROM web.semantic_relations WHERE from_version_id=$1 AND kind='depends_on'",
      [v.version_id],
    );
    const visible = rows.length ? await published(c) : [];
    for (const row of rows) {
      const asset = visible.find((a) => a.asset_id === row.to_asset_id);
      if (!asset) reject("validation_failed", "请先发布依赖的指标。");
      dependencies[asset.asset_id] = asset.version_id;
    }
  }
  async function updateChannel(
    previous: string | null,
    generation: number,
    audience: string | null,
  ) {
    const {
      rows: [base],
    } = await c.db.query("SELECT manifest FROM web.semantic_release WHERE release_id=$1", [
      previous,
    ]);
    const manifest: Manifest = { assets: base?.manifest.assets ?? {} };
    if (remove) delete manifest.assets[v.asset_id];
    else manifest.assets[v.asset_id] = { version: v.version_id, dependencies };
    return advance(c, partition.id, generation, manifest, audience);
  }
  const stable = await updateChannel(head.stable_id, head.generation + 1, null);
  if (head.gray_id) await updateChannel(head.gray_id, head.generation + 2, head.audience_id);
  const state = remove ? "archived" : "published";
  await c.db.query(
    "UPDATE web.semantic_assets SET lifecycle=$2,open_draft_id=NULL WHERE asset_id=$1",
    [v.asset_id, state],
  );
  return {
    ...stable,
    asset_id: v.asset_id,
    version_id: v.version_id,
    previous_release_id: head.stable_id,
    state,
  };
}
export async function release(c: Context, raw: unknown) {
  const f = releaseBody.parse(raw);
  if (!f.note.trim() || (f.mode === "gray") !== !!f.audience_id)
    reject("validation_failed", "请填写理由和对应的发布受众。");
  const v = await version(c, f.version_id);
  if (
    (await latest(c, v.asset_id, true)).version_id !== v.version_id ||
    v.digest !== f.expected_digest
  )
    reject("conflict");
  const sub = await subject(c, v, f.binding_id, true);
  if (!duty(c, v, sub, "release") || sub.state !== "approved") reject("forbidden");
  if (f.audience_id && !["department", "job", "workspace"].includes(scope(c, f.audience_id).kind))
    reject("validation_failed");
  const h = await lockHead(c, sub.partition.id, f.expected_generation);
  if (f.audience_id && !h.stable_id) reject("validation_failed", "请先发布稳定版本。");
  const baseId = f.mode === "gray" && h.audience_id === f.audience_id ? h.gray_id : h.stable_id;
  const {
    rows: [base],
  } = await c.db.query("SELECT manifest FROM web.semantic_release WHERE release_id=$1", [baseId]);
  const manifest: Manifest = { assets: base?.manifest.assets ?? {} };
  const visible = await published(c);
  if (
    sub.isLocal &&
    !visible.some((p) => p.asset_id === v.asset_id && p.version_id === v.version_id)
  )
    reject("validation_failed", "请先发布对应的公共定义。");
  const deps: Record<string, string> = {};
  for (const d of v.dependencies) {
    const target = visible.find((x) => x.asset_id === d);
    if (!target) reject("validation_failed", "请先发布关联定义。");
    deps[d] = target.version_id;
  }
  manifest.assets[v.asset_id] = {
    version: v.version_id,
    attachment: sub.isLocal,
    dependencies: deps,
    ...(sub.b ? { binding: sub.b.binding_id, hash: sub.b.target_hash } : {}),
  };
  const receipt = await advance(
    c,
    sub.partition.id,
    h.generation + 1,
    manifest,
    f.audience_id ?? null,
  );
  if (!sub.isLocal)
    await c.db.query(
      "UPDATE web.semantic_assets SET open_draft_id=NULL,lifecycle='published' WHERE asset_id=$1",
      [v.asset_id],
    );
  return {
    ...receipt,
    asset_id: v.asset_id,
    version_id: v.version_id,
    previous_release_id: baseId,
  };
}
export async function restore(
  c: Context,
  releaseId: string,
  action: "rollback" | "revoke",
  raw: unknown,
) {
  const f = z
    .strictObject({
      request_key: id,
      expected_generation: z.number().int().nonnegative(),
      note: text,
    })
    .parse(raw);
  const {
    rows: [r],
  } = await c.db.query(
    "SELECT r.*,x.release_id AS revoked FROM web.semantic_release r LEFT JOIN web.semantic_revocation x USING(release_id) WHERE r.release_id=$1",
    [releaseId],
  );
  if (!r || !scope(c, r.partition_id).release) reject("not_found");
  if (r.revoked) reject("conflict");
  const h = await lockHead(c, r.partition_id, f.expected_generation);
  if (action === "revoke") {
    await c.db.query(
      "INSERT INTO web.semantic_revocation(release_id,actor_id,reason) VALUES ($1,$2,$3)",
      [releaseId, c.actor.userId, f.note],
    );
    await c.db.query(
      "UPDATE web.semantic_release_head SET generation=generation+1,stable_id=CASE WHEN stable_id=$2 THEN NULL ELSE stable_id END,audience_id=CASE WHEN gray_id=$2 THEN NULL ELSE audience_id END,gray_id=CASE WHEN gray_id=$2 THEN NULL ELSE gray_id END WHERE partition_id=$1",
      [r.partition_id, releaseId],
    );
    return { release_id: releaseId, generation: h.generation + 1, state: "revoked" };
  }
  const allowed = new Set((await releases(c)).map((r) => r.partition));
  if (!allowed.has(r.partition_id) && !scope(c, r.partition_id).release) reject("forbidden");
  const restored = await advance(
    c,
    r.partition_id,
    h.generation + 1,
    { assets: r.manifest.assets },
    r.audience_id,
  );
  return {
    ...restored,
    restored_from: releaseId,
    previous_release_id: r.audience_id ? (h.gray_id ?? h.stable_id) : h.stable_id,
  };
}
