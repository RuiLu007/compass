"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import type { SessionView } from "@/contracts/page";
import type { MetricView, ScopeView, Finding } from "@/contracts/semantic";
import { Suggestions, type Suggestion } from "./suggestions";
import { PolicyEditor } from "./policy";
import { BindingForm } from "./binding-form";
import { Organization } from "./organization";
import { MetricForm } from "./form";
import "./semantic.css";

type Source = { source_id: string; locator: string; source_kind: string };
type Detail = MetricView & {
  checks?: Finding[];
  feedback?: {
    id: string;
    category: string;
    note: string;
    version_id: string;
    revision_id: string | null;
  }[];
  history?: { step: string; note: string }[];
  impact?: { dependent: string[]; external: string };
  versions?: { version_id: string; version_no: number; summary: string }[];
  bindings?: { binding_id: string; target_ref: string }[];
};
type Release = {
  release_id: string;
  partition_id: string;
  audience_id: string | null;
  generation: number;
  current_generation: number;
  revoked: boolean;
  revoked_reason: string | null;
  channel: "stable" | "gray" | "history";
  publisher: string | null;
  note: string | null;
};
const labels: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  inactive: "当前未发布",
  business: "业务评审",
  data: "数据审批",
  approved: "已批准",
  returned: "已退回",
  submit: "提交评审",
  release: "发布",
  return: "退回",
  revise: "创建修订",
  edit: "编辑草稿",
};
export function SemanticPage({ session }: { session: SessionView }) {
  const api = useDocumentSession();
  const target = new URLSearchParams({
    tenantId: session.scope.tenantId!,
    workspaceId: session.scope.workspaceId!,
  }).toString();
  const [items, setItems] = useState<MetricView[]>([]),
    [scopes, setScopes] = useState<ScopeView[]>([]),
    [sources, setSources] = useState<Source[]>([]),
    [personal, setPersonal] = useState(false),
    [configure, setConfigure] = useState(false),
    [history, setHistory] = useState<Release[]>([]);
  const [suggestion, setSuggestion] = useState<Suggestion>();
  const [selected, select] = useState(() =>
      typeof window === "undefined"
        ? ""
        : (new URLSearchParams(window.location.search).get("asset") ?? ""),
    ),
    [binding, bind] = useState(""),
    [detail, show] = useState<Detail>(),
    [mode, setMode] = useState<"new" | "edit" | "derive" | "source" | "">(""),
    [notice, notify] = useState(""),
    [error, fail] = useState(""),
    [busy, working] = useState(false),
    [revision, refresh] = useState(0),
    [loading, setLoading] = useState(true);
  const [q, search] = useState(""),
    [domain, setDomain] = useState(""),
    [owner, setOwner] = useState(""),
    [state, setState] = useState(""),
    [note, setNote] = useState(""),
    [audience, setAudience] = useState(""),
    [decision, setDecision] = useState("");
  const keyRef = useRef({ body: "", key: "" });
  const read = useCallback(
    <T,>(path: string, signal: AbortSignal) =>
      api.request<T>(
        "semantic/" + path + (path.includes("?") ? "&" : "?") + target,
        "GET",
        undefined,
        { signal, retainOnNotFound: true },
      ),
    [api, target],
  );
  useEffect(() => {
    const ctl = new AbortController();
    let live = true;
    Promise.all([
      read<{ items: MetricView[] }>("assets", ctl.signal),
      read<{ items: ScopeView[]; can_create_personal: boolean; can_configure: boolean }>(
        "scopes",
        ctl.signal,
      ),
      read<{ items: Source[] }>("sources", ctl.signal),
      read<{ items: Release[] }>("releases", ctl.signal),
    ])
      .then(([m, s, src, h]) => {
        if (live) {
          setItems(m.items);
          setScopes(s.items);
          setPersonal(s.can_create_personal);
          setConfigure(s.can_configure);
          setSources(src.items);
          setHistory(h.items);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (live) {
          fail(String(e.message));
          setLoading(false);
        }
      });
    const clear = api.onClear(() => {
      setItems([]);
      setScopes([]);
      setSources([]);
      show(undefined);
      setHistory([]);
      setMode("");
      select("");
    });
    return () => {
      live = false;
      ctl.abort();
      clear();
    };
  }, [api, read, revision]);
  useEffect(() => {
    if (!selected) return;
    const ctl = new AbortController();
    let live = true;
    read<Detail>("assets/" + selected + (binding ? "?binding_id=" + binding : ""), ctl.signal)
      .then((data) => {
        if (live) show(data);
      })
      .catch((e) => {
        if (live) fail(String(e.message));
      });
    return () => {
      live = false;
      ctl.abort();
    };
  }, [selected, binding, read, revision]);
  async function write<T>(path: string, data: Record<string, unknown>) {
    const serialized = JSON.stringify([path, data]);
    if (keyRef.current.body !== serialized)
      keyRef.current = { body: serialized, key: crypto.randomUUID() };
    return api.request<T>(
      "semantic/" + path + "?" + target,
      "POST",
      { ...data, request_key: keyRef.current.key },
      { retainOnNotFound: true },
    );
  }
  const perform = async (work: () => Promise<void>) => {
    if (busy) return;
    working(true);
    fail("");
    notify("");
    try {
      await work();
      keyRef.current = { body: "", key: "" };
      refresh((i) => i + 1);
    } catch (e) {
      fail(e instanceof Error ? e.message : "提交失败，请重试。");
    } finally {
      working(false);
    }
  };
  async function apply() {
    if (!detail) return;
    await perform(async () => {
      const common = {
        expected_digest: detail.digest,
        note,
        ...(binding ? { binding_id: binding } : {}),
      };
      if (decision === "release")
        await write("releases", {
          ...common,
          version_id: detail.version_id,
          expected_generation: detail.generation,
          mode: audience ? "gray" : "stable",
          audience_id: audience || null,
        });
      else await write("revisions/" + detail.version_id + "/" + decision, common);
      notify("已完成" + labels[decision]);
      setDecision("");
      setNote("");
    });
  }
  function open(id: string) {
    show(undefined);
    select(id);
    bind("");
    setMode("");
    setDecision("");
    fail("");
    notify("");
  }
  const filtered = items.filter(
    (m) =>
      (!q ||
        [m.name, m.code, ...m.aliases].some((s) => s.toLowerCase().includes(q.toLowerCase()))) &&
      (!domain || m.domain === domain) &&
      (!owner || m.owner_scope_id === owner) &&
      (!state || state === m.state),
  );
  return (
    <section className="semantic-workspace" aria-label="指标口径">
      <div className="semantic-toolbar">
        <label>
          搜索指标
          <input
            value={q}
            onChange={(e) => search(e.target.value)}
            placeholder="名称、代码或别名"
          />
        </label>
        <label>
          业务域
          <select value={domain} onChange={(e) => setDomain(e.target.value)}>
            <option value="">全部业务域</option>
            {[...new Set(items.map((m) => m.domain))].map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        <label>
          维护归属
          <select value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">全部归属</option>
            {scopes.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          发布状态
          <select value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">全部状态</option>
            {Object.keys(labels)
              .slice(0, 6)
              .map((k) => (
                <option key={k} value={k}>
                  {labels[k]}
                </option>
              ))}
          </select>
        </label>
        {(personal || scopes.some((s) => s.manage)) && (
          <>
            <button
              className="ui-button"
              onClick={() => {
                setSuggestion(undefined);
                setMode("new");
                fail("");
              }}
            >
              新建指标
            </button>
            <button
              className="ui-button"
              data-variant="secondary"
              onClick={() => setMode("source")}
            >
              登记来源
            </button>
          </>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
      {(notice || busy || loading) && (
        <p role="status">{busy ? "正在提交…" : loading ? "正在加载指标…" : notice}</p>
      )}
      {!mode && scopes.some((s) => s.manage) && (
        <Suggestions
          key={selected}
          target={target}
          scopes={scopes}
          sources={sources}
          version={detail?.editable ? detail.version_id : undefined}
          onChoose={(s) => {
            setSuggestion(s);
            setMode(detail?.editable ? "edit" : "new");
          }}
        />
      )}
      {mode === "source" ? (
        <form
          className="ui-panel semantic-form"
          onSubmit={(e) => {
            e.preventDefault();
            const f = Object.fromEntries(new FormData(e.currentTarget));
            void perform(async () => {
              await write("sources", { ...f, owner_scope_id: f.owner_scope_id || undefined });
              setMode("");
              notify("来源已登记");
            });
          }}
        >
          <h2>登记来源</h2>
          <label>
            来源类型
            <select name="kind">
              {Object.entries({
                document: "业务文档",
                sql: "历史 SQL",
                schema: "Schema",
                bi: "BI 清单",
                report: "报表",
              }).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            来源位置
            <input name="locator" required />
          </label>
          <label>
            来源版本
            <input name="version" required defaultValue="1" />
          </label>
          <label>
            来源摘要
            <textarea name="excerpt" required />
          </label>
          <label>
            责任人
            <input name="responsible" required />
          </label>
          <label>
            优先级
            <select name="priority">
              <option>P1</option>
              <option>P0</option>
              <option>P2</option>
            </select>
          </label>
          <label>
            归属范围
            <select name="owner_scope_id">
              {personal && <option value="">仅我</option>}
              {scopes
                .filter((s) => s.manage)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
            </select>
          </label>
          <div>
            <button disabled={busy} className="ui-button">
              保存来源
            </button>{" "}
            <button
              type="button"
              className="ui-button"
              data-variant="secondary"
              onClick={() => setMode("")}
            >
              取消
            </button>
          </div>
        </form>
      ) : mode ? (
        <MetricForm
          suggestion={suggestion}
          key={mode + (detail?.version_id ?? "")}
          mode={mode}
          detail={mode === "new" ? undefined : detail}
          scopes={scopes}
          personal={personal}
          sources={sources}
          busy={busy}
          cancel={() => setMode("")}
          save={(fields) =>
            perform(async () => {
              const result = await write<{ asset_id: string }>("drafts", fields);
              open(result.asset_id);
              notify("指标已保存为草稿");
            })
          }
        />
      ) : (
        <div className="semantic-columns">
          <div className="ui-panel">
            <h2>指标列表</h2>
            {filtered.length ? (
              filtered.map((m) => (
                <button
                  key={m.asset_id}
                  className="semantic-row"
                  aria-pressed={m.asset_id === selected}
                  onClick={() => open(m.asset_id)}
                >
                  <strong>{m.name}</strong>
                  <span>
                    {m.domain} · {labels[m.state] ?? m.state} · v{m.version_no}
                  </span>
                </button>
              ))
            ) : (
              <p>{loading ? "正在读取…" : "当前筛选范围没有指标。可调整筛选或新建指标。"}</p>
            )}
          </div>
          {detail && (
            <article className="ui-panel semantic-detail">
              <h2>{detail.name}</h2>
              <p>{detail.summary}</p>
              <dl>
                {Object.entries({
                  指标代码: detail.code,
                  业务域: detail.domain,
                  维护归属: scopes.find((s) => s.id === detail.owner_scope_id)?.label,
                  发布使用范围: scopes.find((s) => s.id === detail.usage_scope_id)?.label,
                  负责人: String(detail.definition.responsible ?? detail.responsible),
                  生效状态: labels[detail.state],
                  版本: "v" + detail.version_no,
                  单位: detail.unit,
                  统计粒度: detail.grain,
                  业务时间: detail.definition.event_time,
                  公式: detail.definition.formula,
                  来源: detail.source_position,
                }).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{String(v ?? "—")}</dd>
                  </div>
                ))}
              </dl>
              {(detail.editable || binding) && detail.kind === "metric" && (
                <BindingForm
                  asset={detail}
                  items={items}
                  binding={binding}
                  save={(path, f) =>
                    perform(async () => {
                      const receipt = await write<{ binding_id?: string }>(path, f);
                      if (path === "bindings" && receipt.binding_id) bind(receipt.binding_id);
                      notify(path === "trial" ? "试算与独立预期一致" : "已登记绑定，请提交评审");
                    })
                  }
                />
              )}
              {!!detail.bindings?.length && (
                <label>
                  执行绑定
                  <select
                    value={binding}
                    onChange={(e) => {
                      bind(e.target.value);
                      setDecision("");
                    }}
                  >
                    <option value="">仅发布定义</option>
                    {detail.bindings.map((b) => (
                      <option key={b.binding_id} value={b.binding_id}>
                        {b.target_ref}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {!!detail.checks?.length && (
                <ul>
                  {detail.checks.map((f, i) => (
                    <li key={i}>
                      {f.field} · {f.message} {f.suggestion}
                    </li>
                  ))}
                </ul>
              )}
              <div className="semantic-actions">
                {detail.actions.map((a) => (
                  <button
                    key={a}
                    className="ui-button"
                    data-variant="secondary"
                    onClick={() =>
                      a === "edit" || a === "revise" ? setMode("edit") : setDecision(a)
                    }
                  >
                    {labels[a]}
                  </button>
                ))}
                {detail.state === "published" && personal && (
                  <button
                    className="ui-button"
                    data-variant="secondary"
                    onClick={() => setMode("derive")}
                  >
                    派生指标
                  </button>
                )}
              </div>
              {decision && (
                <div className="semantic-form">
                  <h3>
                    {labels[decision]} · v{detail.version_no}
                  </h3>
                  <p>
                    {detail.impact?.dependent.join("、")} {detail.impact?.external}
                  </p>
                  <label>
                    操作理由
                    <input value={note} onChange={(e) => setNote(e.target.value)} />
                  </label>
                  {decision === "release" && (
                    <label>
                      发布受众
                      <select value={audience} onChange={(e) => setAudience(e.target.value)}>
                        <option value="">全量发布</option>
                        {scopes
                          .filter((s) => ["department", "job", "workspace"].includes(s.kind))
                          .map((s) => (
                            <option value={s.id} key={s.id}>
                              {s.label}
                            </option>
                          ))}
                      </select>
                    </label>
                  )}
                  <div>
                    <button
                      className="ui-button"
                      disabled={busy || !note.trim()}
                      onClick={() => void apply()}
                    >
                      确认{labels[decision]}
                    </button>{" "}
                    <button
                      className="ui-button"
                      data-variant="secondary"
                      onClick={() => setDecision("")}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}
              {detail.state === "published" && (
                <form
                  className="semantic-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = Object.fromEntries(new FormData(e.currentTarget));
                    void perform(async () => {
                      await write("feedback", { ...f, version_id: detail.version_id });
                      notify("已关联该版本的反馈");
                    });
                  }}
                >
                  <label>
                    反馈类别
                    <select name="category">
                      <option>定义错误</option>
                      <option>解析错误</option>
                      <option>计算结果</option>
                    </select>
                  </label>
                  <label>
                    反馈说明
                    <input name="note" required maxLength={160} />
                  </label>
                  <button className="ui-button" disabled={busy}>
                    提交口径反馈
                  </button>
                </form>
              )}
              {detail.feedback?.map((item) => (
                <p key={item.id}>
                  {item.category} · {item.note}{" "}
                  {item.revision_id
                    ? "已关联修订"
                    : item.version_id !== detail.version_id &&
                      detail.editable && (
                        <button
                          className="ui-button"
                          onClick={() =>
                            void perform(async () => {
                              await write("feedback/" + item.id + "/adopt", {
                                revision_id: detail.version_id,
                              });
                              notify("反馈已关联本修订");
                            })
                          }
                        >
                          关联本修订
                        </button>
                      )}
                </p>
              ))}
              <details>
                <summary>版本与审查历史</summary>
                {detail.versions?.map((v) => (
                  <p key={v.version_id}>
                    v{v.version_no} · {v.summary}
                  </p>
                ))}
                {detail.history?.map((a, i) => (
                  <p key={i}>
                    {labels[a.step]} · {a.note}
                  </p>
                ))}
              </details>
            </article>
          )}
        </div>
      )}
      {scopes.some((s) => s.release) && (
        <PolicyEditor
          target={target}
          scopes={scopes}
          sources={sources}
          items={items}
          revision={revision}
          save={(fields) =>
            perform(async () => {
              await write("policies", fields);
              notify("策略已发布，新任务将使用本次配置");
            })
          }
        />
      )}
      {configure && (
        <Organization
          target={target}
          revision={revision}
          save={(path, f) =>
            perform(async () => {
              await write(path, f);
              notify("范围配置已更新");
            })
          }
        />
      )}
      {!!history.length && (
        <details className="ui-panel">
          <summary>发布与回滚记录</summary>
          {history.map((r) => (
            <form
              key={r.release_id}
              className="semantic-actions"
              onSubmit={(e) => {
                e.preventDefault();
                const reason = new FormData(e.currentTarget).get("note");
                const button = (e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
                const action = button?.value === "revoke" ? "revoke" : "rollback";
                void perform(async () => {
                  await write("releases/" + r.release_id + "/" + action, {
                    expected_generation: r.current_generation,
                    note: reason,
                  });
                  notify(action === "revoke" ? "已撤回选定发布" : "已回切选定版本");
                });
              }}
            >
              <span>
                {scopes.find((s) => s.id === r.partition_id)?.label} · 第 {r.generation} 次发布 ·{" "}
                {r.revoked
                  ? "已撤回"
                  : { stable: "当前稳定版", gray: "当前灰度版", history: "历史版本" }[r.channel]}
                {r.publisher && " · " + r.publisher}
                {r.audience_id &&
                  " · 受众 " + (scopes.find((s) => s.id === r.audience_id)?.label ?? "已登记范围")}
                {r.note && " · " + r.note}
                {r.revoked_reason && " · 撤回理由：" + r.revoked_reason}
              </span>
              {!r.revoked && (
                <>
                  <input aria-label="变更理由" required name="note" placeholder="变更理由" />
                  <button
                    className="ui-button"
                    value="rollback"
                    data-variant="secondary"
                    disabled={busy}
                  >
                    确认回滚
                  </button>
                  <button
                    className="ui-button"
                    value="revoke"
                    data-variant="secondary"
                    disabled={busy}
                  >
                    撤回发布
                  </button>
                </>
              )}
            </form>
          ))}
        </details>
      )}
    </section>
  );
}
