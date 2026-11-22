import type { RefObject } from "react";
import type { ScopeView } from "@/contracts/semantic";

export function SourceForm({
  id,
  initialFocus,
  personal,
  scopes,
  busy,
  save,
}: {
  id: string;
  initialFocus: RefObject<HTMLInputElement | null>;
  personal: boolean;
  scopes: ScopeView[];
  busy: boolean;
  save: (fields: Record<string, unknown>) => void;
}) {
  return (
    <form
      id={id}
      className="semantic-editor"
      onSubmit={(event) => {
        event.preventDefault();
        const fields = Object.fromEntries(new FormData(event.currentTarget));
        save({ ...fields, owner_scope_id: fields.owner_scope_id || undefined });
      }}
    >
      <fieldset disabled={busy}>
        <legend>来源信息</legend>
        <div className="semantic-field-grid">
          <label className="semantic-wide">
            <span>来源位置</span>
            <input
              ref={initialFocus}
              name="locator"
              required
              maxLength={160}
              placeholder="文档链接、文件路径或报表位置"
            />
          </label>
          <label>
            来源类型
            <select name="kind">
              {Object.entries({
                document: "业务文档",
                sql: "历史 SQL",
                schema: "Schema",
                bi: "BI 清单",
                report: "报表",
              }).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>来源版本</span>
            <input name="version" required maxLength={160} defaultValue="1" />
          </label>
          <label className="semantic-wide">
            <span>来源摘要</span>
            <textarea name="excerpt" required maxLength={2400} rows={4} />
          </label>
        </div>
      </fieldset>
      <fieldset disabled={busy}>
        <legend>维护信息</legend>
        <div className="semantic-field-grid">
          <label>
            <span>责任人</span>
            <input name="responsible" required maxLength={160} />
          </label>
          <label>
            优先级
            <select name="priority" defaultValue="P1">
              <option>P0</option>
              <option>P1</option>
              <option>P2</option>
            </select>
          </label>
          <label>
            归属范围
            <select
              name="owner_scope_id"
              defaultValue={
                personal ? (scopes.find((s) => s.kind === "personal")?.id ?? "") : undefined
              }
            >
              {personal && !scopes.some((s) => s.kind === "personal") && (
                <option value="">仅我</option>
              )}
              {scopes
                .filter((s) => s.manage)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
            </select>
          </label>
        </div>
      </fieldset>
    </form>
  );
}
