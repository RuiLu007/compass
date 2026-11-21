import "server-only";
import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { bindAccess, allowed } from "../modules/access/public";
import { bindIdentity } from "../modules/identity/infrastructure/pg-identity";
import { withTransaction } from "../platform/db";
import { reject } from "../platform/errors";
import { readJson, jsonResponse, errorResponse } from "./responses";
import { acceptPin, readPin, loadCollection, currentBinding } from "../modules/documents/store";
import { hash, sourceRef, searchArgs } from "../modules/documents/contracts";
import { searchDocuments, readDocument } from "../modules/documents/search";

const envelope = z.strictObject({
  actor_user_id: z.uuid(),
  tenant_id: z.uuid(),
  workspace_id: z.uuid(),
  run_id: z.uuid(),
  args: z.record(z.string(), z.unknown()).default({}),
});
const readArgs = z.strictObject({ sourceRef, offset: z.number().int().min(-1).max(1).default(0) });
const verifyArgs = z.strictObject({
  refs: z.array(sourceRef).max(64).default([]),
  kinds: z
    .array(z.enum(["semantic", "detail", "knowledge", "history", "external"]))
    .max(5)
    .default([]),
});
export async function internalDocuments(request: Request, operation: string) {
  try {
    const token = process.env.COMPASS_AGENT_SERVICE_TOKEN;
    const expected = Buffer.from("Bearer " + token),
      actual = Buffer.from(request.headers.get("authorization") ?? "");
    if (!token || expected.length !== actual.length || !timingSafeEqual(expected, actual))
      reject("forbidden");
    const op = z.enum(["pin", "search", "read", "verify"]).parse(operation);
    const s = envelope.parse(await readJson(request));
    return jsonResponse(
      await withTransaction("query", async (db) => {
        request.signal.throwIfAborted();
        const user = await bindIdentity(db).user(s.actor_user_id);
        if (user.status !== "active") reject("forbidden");
        const service = bindAccess(db);
        // Every reader, including a platform administrator, needs an active tenant membership.
        const access = await service.current(
          { userId: user.id, isPlatformAdmin: false, expiresAt: "", sessionId: "runtime" },
          s.tenant_id,
        );
        const workspace = await service.workspace(s.tenant_id, s.workspace_id);
        if (
          workspace.status !== "active" ||
          !access.memberId ||
          !allowed(access, "module.analysis", {
            tenantId: s.tenant_id,
            workspaceId: s.workspace_id,
          })
        )
          reject("forbidden");
        request.signal.throwIfAborted();
        const current = await currentBinding(s);
        if (op === "pin") {
          z.strictObject({}).parse(s.args);
          return acceptPin(s, current);
        }
        const pin = await readPin(s);
        const verification = op === "verify" ? verifyArgs.parse(s.args) : undefined;
        const active = await db.query<{ id: string }>(
          "SELECT id FROM web.workspace WHERE tenant_id=$1 AND status='active' AND id=ANY($2::uuid[])",
          [s.tenant_id, [...access.workspacePermissions.keys()]],
        );
        const activeIds = new Set(active.rows.map((r) => r.id));
        let accessDigest: string | undefined;
        if (verification) {
          if (!verification.kinds.every((k) => current.kinds.includes(k) && pin.kinds.includes(k)))
            return { allowed: false };
          accessDigest = hash(
            JSON.stringify([
              access.authzVersion,
              access.memberId,
              access.role,
              [...access.tenantPermissions].sort(),
              [...activeIds]
                .sort()
                .map((id) => [id, [...(access.workspacePermissions.get(id) ?? [])].sort()]),
              [...current.kinds].sort(),
              Object.entries(current.policies).sort(([a], [b]) => a.localeCompare(b)),
            ]),
          );
          if (!verification.refs.length) return { allowed: true, accessDigest };
        }
        const c = {
          access: {
            ...access,
            workspacePermissions: new Map(
              [...access.workspacePermissions].filter(([id]) => activeIds.has(id)),
            ),
          },
          current,
          pin,
          collection: await loadCollection(pin, request.signal),
        };
        if (verification)
          return {
            allowed: verification.refs.every((ref) => readDocument(c, ref).length === 1),
            accessDigest,
          };
        const sections =
          op === "search"
            ? searchDocuments(c, searchArgs.parse(s.args))
            : (() => {
                const a = readArgs.parse(s.args);
                return readDocument(c, a.sourceRef, a.offset);
              })();
        return {
          status: sections.length ? "ok" : "empty",
          knownAt: pin.knownAt,
          collectionDigest: pin.collectionDigest,
          sections,
        };
      }),
    );
  } catch (error) {
    return errorResponse(error);
  }
}
