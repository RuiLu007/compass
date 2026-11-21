import "server-only";
export { createAccessService } from "./application/service";
export type { AccessStore, AccessContext } from "./application/ports";
export { allowed, requirePermission } from "./domain/access-policy";
export type { CurrentAccess, Scope } from "./domain/access-policy";
export { bindAccess } from "./infrastructure/pg-access";
