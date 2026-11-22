import { allowed, bindAccess } from "../access/public";
import type { Actor } from "../../platform/application-types";
import type { SqlClient } from "../../platform/db";
import { reject } from "../../platform/errors";
import type { ScopeView } from "../../../contracts/semantic";

export type Target = { tenantId: string; workspaceId: string };
type StoredScope = {
  id: string;
  kind: string;
  department_key: string | null;
  owner_user_id: string | null;
  member: boolean;
  grants: string[];
};
export async function semanticAccess(
  db: SqlClient,
  actor: Actor,
  target: Target,
  optionalContextRead = false,
) {
  const iam = bindAccess(db);
  const [access, workspace] = await Promise.all([
    iam.current(actor, target.tenantId),
    iam.workspace(target.tenantId, target.workspaceId),
  ]);
  if (workspace.status !== "active") reject("forbidden");
  const can = (cap: "read" | "draft" | "govern" | "query") =>
    allowed(access, `semantics.${cap}`, target);
  const tenantAdmin = access.role === "admin" && !!access.memberId;
  const sharedTermsOnly =
    optionalContextRead && !can("read") && allowed(access, "module.analysis", target);
  if (!sharedTermsOnly && !can("read") && !can("draft") && !can("govern")) reject("forbidden");
  const { rows } = await db.query<StoredScope>(
    `SELECT s.scope_id AS id,s.kind,s.department_key,s.owner_user_id,
    EXISTS(SELECT FROM web.semantic_scope_memberships m WHERE m.scope_id=s.scope_id AND m.tenant_member_id=$3 AND m.valid_from<=now() AND m.revoked_at IS NULL) AS member,
    ARRAY(SELECT g.capability FROM web.semantic_scope_grants g WHERE g.scope_id=s.scope_id AND g.grantee_member_id=$3 AND g.revoked_at IS NULL) AS grants
    FROM web.semantic_scopes s WHERE (s.tenant_id=$1 OR s.kind='platform')
    AND (s.workspace_id IS NULL OR s.workspace_id=$2) AND (s.kind<>'personal' OR s.owner_user_id=$4)`,
    [target.tenantId, target.workspaceId, access.memberId, actor.userId],
  );
  // Administrators may inspect published organizational definitions in this target.
  // Membership determines department grants, rollout audiences and approval duties.
  const scopes: ScopeView[] = rows
    .map((s) => {
      const personal = s.kind === "personal" && s.owner_user_id === actor.userId;
      const platform = s.kind === "platform" && actor.isPlatformAdmin;
      const local = ["tenant", "workspace"].includes(s.kind) && tenantAdmin;
      const delegated = s.grants.includes("manage");
      const manage = can("draft") && (personal || platform || local || delegated);
      const use = ["platform", "tenant", "workspace"].includes(s.kind) || personal || s.member;
      const duty = (key: string) => can("govern") && (platform || local || s.grants.includes(key));
      return {
        id: s.id,
        kind: s.kind,
        label:
          (
            {
              platform: "平台公共",
              tenant: "本租户",
              workspace: "本工作区",
              personal: "仅我",
            } as Record<string, string>
          )[s.kind] ??
          s.department_key ??
          s.kind,
        use,
        manage,
        share: platform || local || s.grants.includes("share") || personal,
        business: duty("business"),
        data: duty("data"),
        release: duty("release"),
      };
    })
    .filter(
      (s) =>
        s.use ||
        s.manage ||
        s.share ||
        s.business ||
        s.data ||
        s.release ||
        canReadOrgScope(s.kind, actor.isPlatformAdmin || tenantAdmin),
    );
  return {
    db,
    actor,
    target,
    scopes,
    can,
    tenantAdmin,
    sharedTermsOnly,
    memberId: access.memberId,
  };
}
export type Context = Awaited<ReturnType<typeof semanticAccess>>;
function canReadOrgScope(kind: string, administrator: boolean) {
  return administrator && (kind === "department" || kind === "job");
}
export function publishedScopeIds(c: Context) {
  return c.scopes
    .filter((s) => s.use || canReadOrgScope(s.kind, c.actor.isPlatformAdmin || c.tenantAdmin))
    .map((s) => s.id);
}
export function scope(c: Context, id: string) {
  const s = c.scopes.find((row) => row.id === id);
  if (!s) reject("not_found");
  return s;
}
export function ensureShare(c: Context, owner: string, usage: string, kind?: string) {
  const origin = scope(c, owner);
  if (!origin.manage) reject("forbidden");
  if (
    owner === usage ||
    (kind === "term" && origin.kind === "personal" && scope(c, usage).kind === "workspace")
  )
    return;
  if (
    origin.kind === "platform" ||
    ["platform", "personal"].includes(scope(c, usage).kind) ||
    !scope(c, usage).share
  )
    reject("forbidden");
}
export async function personal(c: Context) {
  if (!c.memberId || !c.can("draft")) reject("forbidden");
  let s = c.scopes.find((row) => row.kind === "personal");
  if (!s) {
    await c.db.query(
      "INSERT INTO web.semantic_scopes(kind,tenant_id,owner_user_id) VALUES ('personal',$1,$2) ON CONFLICT DO NOTHING",
      [c.target.tenantId, c.actor.userId],
    );
    const refresh = await semanticAccess(c.db, c.actor, c.target);
    c.scopes = refresh.scopes;
    s = c.scopes.find((row) => row.kind === "personal");
  }
  if (!s) reject("forbidden");
  return s.id;
}
