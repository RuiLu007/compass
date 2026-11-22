export type LibraryScope = { tenantId: string; workspaceId: string };
export type Base = {
  tenantId?: string;
  workspaceId?: string;
  tenantName?: string;
  workspaceName?: string;
  canManage?: boolean;
  id: string;
  name: string;
  description: string;
  files: number;
  bytes: number;
  revision: number;
};
export type Doc = {
  id: string;
  name: string;
  format: string;
  bytes: number;
  chars: number;
  uploaded: string;
  status: "ready" | "processing" | "failed";
  error: string;
  revision: number;
};
export type Chunk = {
  id: string;
  ordinal: number;
  heading: string;
  body: string;
  enabled: boolean;
  revision: number;
};
export type Dialog =
  | { type: "base"; item?: Base }
  | { type: "rename"; item: Doc }
  | { type: "delete"; item: Base | Doc }
  | { type: "chunk"; item: Chunk }
  | { type: "upload" };

export const status = { ready: "已入库", processing: "待完成索引", failed: "处理失败" };
export function size(bytes: number) {
  return bytes >= 1048576
    ? (bytes / 1048576).toFixed(1) + " MB"
    : bytes >= 1024
      ? (bytes / 1024).toFixed(1) + " KB"
      : bytes + " B";
}
