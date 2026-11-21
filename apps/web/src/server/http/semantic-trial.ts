import { z } from "zod";
import { id, queryParams } from "../../contracts/semantic";
import { execute } from "../composition";
import { boundedJson, fetchAgent } from "../agent-transport";
import { reject, ApplicationError } from "../platform/errors";
import { bindIdentity } from "../modules/identity/infrastructure/pg-identity";
import { semanticAccess, type Target } from "../modules/semantic/access";
import { inspect, subject } from "../modules/semantic/governance";
import { version } from "../modules/semantic/catalog";
import { idempotent } from "../modules/semantic/drafts";
import { jsonResponse } from "./responses";

export async function trial(token: string, target: Target, raw: unknown, signal: AbortSignal) {
  const f = z
    .strictObject({
      request_key: id,
      version_id: id,
      binding_id: id,
      parameters: queryParams,
      expected: z
        .array(
          z.strictObject({
            group_key: z.string().max(160).nullable(),
            value: z.string().max(100).nullable(),
            status: z.string().min(1).max(60),
          }),
        )
        .min(1)
        .max(50),
    })
    .parse(raw);
  const load = async (db: Parameters<typeof bindIdentity>[0]) => {
    const c = await semanticAccess(db, await bindIdentity(db).requireActor(token), target);
    const v = await version(c, f.version_id);
    const view = await inspect(c, v, f.binding_id);
    if (!c.can("query") || !(view.editable || view.actions.includes("data"))) reject("forbidden");
    const sub = await subject(c, v, f.binding_id);
    const {
      rows: [b],
    } = await db.query(
      "SELECT sql_text FROM web.execution_bindings WHERE binding_id=$1 AND version_id=$2 AND workspace_id=$3 AND tenant_id=$4",
      [f.binding_id, f.version_id, target.workspaceId, target.tenantId],
    );
    if (!b) reject("not_found");
    return { c, hash: sub.hash, binding: { ...sub.b, sql_text: b.sql_text } };
  };
  const before = await execute(token, "query", async ({ db, models }) => {
    const { hash, binding } = await load(db);
    const context = await models.authorize(token, { ...target, kind: "tenant", purpose: "chat" });
    return { hash, binding, context };
  });
  const secret = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!secret) reject("unavailable");
  const r = await fetchAgent(
    {
      path: "/v1/semantic/trial",
      context: before.value.context,
      method: "POST",
      input: {
        binding: before.value.binding,
        parameters: f.parameters,
        expected: f.expected,
      },
    },
    secret,
    AbortSignal.any([signal, AbortSignal.timeout(13000)]),
  );
  if (!r.ok) {
    await r.body?.cancel();
    reject(
      r.status === 422 ? "validation_failed" : "unavailable",
      "试算未通过，请核对来源、期间及权限。",
    );
  }
  const result = z
    .strictObject({
      matched: z.boolean(),
      rows: z.number().int().nonnegative(),
      target_hash: z.string(),
    })
    .parse(await boundedJson(r, () => new ApplicationError("unavailable", "试算响应无效")));
  if (!result.matched) reject("validation_failed", "试算结果与独立预期不一致。");
  const after = await execute(token, "command", async ({ db }) => {
    const { c, hash, binding } = await load(db);
    if (before.value.hash !== hash || result.target_hash !== binding.target_hash)
      reject("conflict");
    return idempotent(c, f.request_key, "trial", f, async () => ({
      version_id: f.version_id,
      binding_id: f.binding_id,
      approval_digest: hash,
      matched: true,
      rows: result.rows,
    }));
  });
  return jsonResponse(after.value, after.viewRevision);
}
