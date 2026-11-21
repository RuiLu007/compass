import type { DocumentSession } from "@/client/request-scope";
import { ApiError } from "@/client/request-scope";
import type { components } from "@/generated/api";

type S = components["schemas"];
export type Entry = S["PromptFileEntry"];
export type FileView = S["PromptFileDetail"];
export type Target = Pick<S["PromptReleaseTarget"], "action"> &
  Partial<Pick<S["PromptReleaseTarget"], "path" | "snapshot_no">>;
export type Receipt = S["PromptReceipt"];
export type ImportChoice = Pick<S["PromptImportChoice"], "path" | "choice"> &
  Partial<Pick<S["PromptImportChoice"], "body">>;
export type ImportRow = S["PromptImportEntry"];
export type Snapshot = S["PromptSnapshotEntry"];
export type ReleasePreview = S["PromptReleasePreview"];
export type Change = S["PromptChange"];

type Results = {
  tree: S["PromptTree"];
  file: FileView;
  history: S["PromptHistory"];
  snapshots: S["PromptSnapshots"];
  snapshot: S["PromptSnapshot"];
  commands: Receipt;
  "render-preview": S["PromptRendered"];
  "import-preview": S["PromptImportPreview"];
  "release-preview": S["PromptReleasePreview"];
};
type Preview = "render-preview" | "import-preview" | "release-preview";
type Write = "file-draft" | "draft-from-version" | "import" | "releases";
export function promptClient(session: DocumentSession) {
  const pending = new Map<string, string>();
  function get<K extends Exclude<keyof Results, Preview>>(
    kind: K,
    params: Record<string, string> = {},
    signal?: AbortSignal,
  ) {
    return session.request<Results[K]>(
      "platform/promptops/" + kind + "?" + new URLSearchParams(params),
      "GET",
      undefined,
      { signal, retainOnNotFound: true },
    );
  }
  function preview<K extends Preview>(kind: K, body: unknown, signal?: AbortSignal) {
    return session.request<Results[K]>("platform/promptops/" + kind, "POST", body, {
      signal,
      retainOnNotFound: true,
    });
  }
  async function write(kind: Write, body: Record<string, unknown>) {
    const key = kind + JSON.stringify(body);
    let id = pending.get(key);
    if (id) {
      try {
        const receipt = await get("commands", { request_id: id });
        pending.delete(key);
        return receipt;
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
    } else {
      id = crypto.randomUUID();
      pending.set(key, id);
    }
    try {
      const receipt = await session.request<Receipt>(
        "platform/promptops/" + kind,
        "POST",
        { ...body, request_id: id },
        { retainOnNotFound: true },
      );
      pending.delete(key);
      return receipt;
    } catch (error) {
      if (error instanceof ApiError && error.status < 500) {
        pending.delete(key);
        throw error;
      }
      throw new Error("提交结果待核对。重试相同操作可读取原回执。");
    }
  }
  return { get, preview, write };
}
export type Ops = ReturnType<typeof promptClient>;
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "读取失败，请重试。";
export const versionLabel = (v: number | null | undefined) => (v ? "v" + v : "未生效");
export const snapshotLabel = (n: number) => "S" + String(n).padStart(3, "0");
