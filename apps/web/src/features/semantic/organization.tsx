"use client";
import { useEffect, useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";

export function Organization({
  target,
  revision,
  save,
}: {
  target: string;
  revision: number;
  save: (path: string, f: Record<string, unknown>) => Promise<void>;
}) {
  const api = useDocumentSession();
  const [data, update] = useState<{
    scopes: { scope_id: string; kind: string; name: string | null }[];
    members: { id: string; name: string }[];
  }>({ scopes: [], members: [] });
  const [error, fail] = useState("");
  useEffect(() => {
    const ac = new AbortController();
    void api
      .request<typeof data>("semantic/organization?" + target, "GET", undefined, {
        signal: ac.signal,
        retainOnNotFound: true,
      })
      .then((v) => {
        if (!ac.signal.aborted) update(v);
      })
      .catch((e) => {
        if (!ac.signal.aborted) fail(e.message);
      });
    return () => ac.abort();
  }, [api, target, revision]);
  return (
    <details className="ui-panel">
      <summary>部门、岗位与授权</summary>
      {error && <p role="alert">{error}</p>}
      <form
        className="semantic-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save("scopes", Object.fromEntries(new FormData(e.currentTarget)));
        }}
      >
        <label>
          范围类型
          <select name="kind">
            <option value="department">部门</option>
            <option value="job">岗位</option>
            <option value="tenant">本租户</option>
            <option value="workspace">本工作区</option>
          </select>
        </label>
        <label>
          部门或岗位名称
          <input name="name" maxLength={160} />
        </label>
        <button className="ui-button">建立范围</button>
      </form>
      {!!data.scopes.length && (
        <form
          className="semantic-form"
          onSubmit={(e) => {
            e.preventDefault();
            const f = Object.fromEntries(new FormData(e.currentTarget));
            void save("grants", { ...f, active: f.active === "true" });
          }}
        >
          <label>
            授权范围
            <select name="scope_id">
              {data.scopes.map((s) => (
                <option key={s.scope_id} value={s.scope_id}>
                  {s.name ?? (s.kind === "tenant" ? "本租户" : "本工作区")}
                </option>
              ))}
            </select>
          </label>
          <label>
            成员
            <select name="member_id">
              {data.members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            职责
            <select name="capability">
              {Object.entries({
                member: "使用",
                manage: "维护",
                share: "分享至本范围",
                business: "业务评审",
                data: "数据审批",
                release: "发布",
              }).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            授权状态
            <select name="active">
              <option value="true">授予</option>
              <option value="false">撤销</option>
            </select>
          </label>
          <button className="ui-button">更新授权</button>
        </form>
      )}
    </details>
  );
}
