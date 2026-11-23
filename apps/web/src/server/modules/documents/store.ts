import "server-only";
import { randomUUID } from "node:crypto";
import { link, mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import {
  binding,
  collection,
  hash,
  pinSchema,
  type Binding,
  type Collection,
  type Pin,
} from "./contracts";
import { reject } from "../../platform/errors";

export type Subject = {
  actor_user_id: string;
  tenant_id: string;
  workspace_id: string;
  run_id: string;
};
function directory(key: "COMPASS_DOCUMENTS_ROOT" | "COMPASS_DOCUMENTS_PIN_DIR") {
  const value = process.env[key];
  if (!value) reject("unavailable");
  return value;
}
async function json(path: string) {
  return JSON.parse(await readFile(path, "utf8"));
}
export async function currentBinding(
  s: Pick<Subject, "tenant_id" | "workspace_id">,
): Promise<Binding | undefined> {
  const root = process.env.COMPASS_DOCUMENTS_ROOT;
  if (!root) return undefined;
  let value: unknown;
  try {
    value = await json(join(root, "bindings", s.tenant_id, s.workspace_id + ".json"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
  const item = binding.safeParse(value);
  if (
    !item.success ||
    item.data.tenantId !== s.tenant_id ||
    item.data.workspaceId !== s.workspace_id
  )
    reject("unavailable");
  return item.data;
}
function snapshotPath(digest: string) {
  return join(directory("COMPASS_DOCUMENTS_PIN_DIR"), "collections", digest + ".json");
}
export async function loadCollection(
  pin: Pick<Pin, "tenantId" | "collectionDigest">,
  signal?: AbortSignal,
) {
  const root = process.env.COMPASS_DOCUMENTS_ROOT;
  let data: Buffer | undefined;
  if (root) {
    try {
      data = await readFile(join(root, "collections", pin.collectionDigest + ".json"), { signal });
      if (data.length > 8 * 1024 * 1024) reject("unavailable");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
  data ??= await readFile(snapshotPath(pin.collectionDigest), { signal });
  if (hash(data) !== pin.collectionDigest) reject("unavailable");
  const parsed = collection.safeParse(JSON.parse(data.toString("utf8")));
  if (!parsed.success || parsed.data.tenantId !== pin.tenantId) reject("unavailable");
  return parsed.data;
}
const pinPath = (s: Subject) =>
  join(directory("COMPASS_DOCUMENTS_PIN_DIR"), s.tenant_id, s.workspace_id, s.run_id + ".json");
export async function readPin(s: Subject) {
  const pin = pinSchema.safeParse(await json(pinPath(s)));
  if (
    !pin.success ||
    pin.data.actorUserId !== s.actor_user_id ||
    pin.data.runId !== s.run_id ||
    pin.data.tenantId !== s.tenant_id ||
    pin.data.workspaceId !== s.workspace_id
  )
    reject("forbidden");
  return pin.data;
}
export async function acceptPin(
  s: Subject,
  b: Binding,
  managed?: Collection,
  options: Pick<Pin, "protocol" | "emptyReason"> = {},
) {
  try {
    return await readPin(s);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  const proposal = pinSchema.parse({
    ...binding.omit({ policies: true }).strip().parse(b),
    runId: s.run_id,
    actorUserId: s.actor_user_id,
    ...options,
    knownAt: new Date(Math.min(Date.parse(b.knownAt), Date.now())).toISOString(),
  });
  if (managed) {
    const path = snapshotPath(b.collectionDigest);
    await mkdir(dirname(path), { recursive: true, mode: 0o700 });
    const temp = path + "." + randomUUID();
    await writeFile(temp, JSON.stringify(managed), { flag: "wx", mode: 0o600 });
    try {
      await link(temp, path);
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e;
    } finally {
      await unlink(temp);
    }
  }
  await loadCollection(proposal);
  const path = pinPath(s);
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temp = path + "." + randomUUID();
  await writeFile(temp, JSON.stringify(proposal), { flag: "wx", mode: 0o600 });
  try {
    await link(temp, path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  } finally {
    await unlink(temp);
  }
  return readPin(s);
}
