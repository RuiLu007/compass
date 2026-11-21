import { z } from "zod";
import { id } from "../../contracts/semantic";
import { boundedJson, fetchAgent } from "../agent-transport";
import { execute } from "../composition";
import { bindIdentity } from "../modules/identity/infrastructure/pg-identity";
import { semanticAccess } from "../modules/semantic/access";
import { discover, sources } from "../modules/semantic/drafts";
import { inspect } from "../modules/semantic/governance";
import { version } from "../modules/semantic/catalog";
import { ApplicationError, reject } from "../platform/errors";
import { jsonResponse } from "./responses";

const candidates = z.strictObject({
  candidates: z
    .array(
      z.strictObject({
        source_ref: id,
        position: z.string().max(400),
        name: z.string().max(160),
        summary: z.string().max(1600),
        formula: z.string().max(1600),
        confidence: z.number().min(0).max(1),
        missing: z.array(z.string()).max(16),
        suggestions: z.array(z.string()).max(16),
      }),
    )
    .max(12),
});
export async function suggestions(
  token: string,
  target: { tenantId: string; workspaceId: string },
  input: unknown,
  signal: AbortSignal,
) {
  const f = z
    .strictObject({
      source_refs: z.array(id).max(8).default([]),
      owner_scope_id: id.optional(),
      usage_scope_id: id.optional(),
      version_id: id.optional(),
    })
    .parse(input);
  const load = () =>
    execute(token, "query", async ({ db, models }) => {
      const context = await models.authorize(token, { ...target, kind: "tenant", purpose: "chat" });
      const c = await semanticAccess(db, await bindIdentity(db).requireActor(token), target);
      let material,
        draft = "";
      if (f.version_id) {
        const v = await version(c, f.version_id);
        const view = await inspect(c, v);
        if (!view.editable || !v.source_id) reject("forbidden");
        material = (await sources(c))
          .filter((s) => s.source_id === v.source_id)
          .map((s) => ({
            source_ref: s.source_id,
            position: v.source_position,
            material: s.material.excerpt ?? v.summary,
            source_version: s.source_version,
          }));
        draft = JSON.stringify({
          name: v.name,
          summary: v.summary,
          grain: v.grain,
          definition: v.definition,
        }).slice(0, 4000);
      } else {
        if (!f.owner_scope_id || !f.source_refs.length) reject("validation_failed");
        material = await discover(
          c,
          f.source_refs,
          f.owner_scope_id,
          f.usage_scope_id ?? f.owner_scope_id,
        );
      }
      return {
        context,
        payload: { task: f.version_id ? "review" : "discover", sources: material, draft },
      };
    });
  const before = await load();
  const unavailable = () =>
    new ApplicationError("unavailable", "语义建议服务暂时不可用，请检查已发布提示词和模型连接。");
  const credential = process.env.COMPASS_AGENT_SERVICE_TOKEN;
  if (!credential) throw unavailable();
  const response = await fetchAgent(
    {
      path: "/v1/semantic/suggestions",
      context: before.value.context,
      method: "POST",
      input: before.value.payload,
    },
    credential,
    AbortSignal.any([signal, AbortSignal.timeout(50000)]),
  );
  if (!response.ok) {
    await response.body?.cancel();
    throw unavailable();
  }
  const parsed = candidates.safeParse(await boundedJson(response, unavailable));
  if (!parsed.success) throw unavailable();
  const after = await load();
  if (JSON.stringify(before.value.payload) !== JSON.stringify(after.value.payload))
    reject("conflict");
  return jsonResponse(parsed.data, after.viewRevision);
}
