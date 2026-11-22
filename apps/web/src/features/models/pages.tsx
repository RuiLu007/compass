"use client";
import { useCallback, useEffect, useState } from "react";
import { Plus, Search, ArrowUpRight, Server, RefreshCw } from "lucide-react";
import type { PagePayload } from "@/contracts/page";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { AppLink } from "@/components/shell/app-link";
import { Modal } from "@/components/ui/dialog";
import { UiPageHeading } from "@/components/ui/page-heading";
import { ProviderEditor } from "./provider-editor";
import { ObservabilityPanel } from "./observability-panel";
import { ReliabilityPanel } from "./reliability-panel";
import { ModelPlayground } from "./playground";
import type { AvailableModels, ModelChangePage, Provider, ProviderPage } from "./types";
import { modelStatus, capabilityNames } from "./types";

export function ModelsPages({ payload }: { payload: PagePayload }) {
  return payload.page.id === "platform-models" ? (
    <PlatformModels />
  ) : (
    <WorkspaceModels payload={payload} />
  );
}
function WorkspaceModels({ payload }: { payload: PagePayload }) {
  const api = useDocumentSession();
  const [data, setData] = useState<AvailableModels | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    void api
      .request<AvailableModels>(
        `models/available?${new URLSearchParams({ ...payload.session.scope, view: "module" })}`,
      )
      .then((result) => {
        if (live) {
          setData(result);
          setError("");
        }
      })
      .catch(() => {
        if (live) setError("暂时无法加载可用模型。");
      });
    const remove = api.onClear(() => setData(null));
    return () => {
      live = false;
      remove();
    };
  }, [api, payload.session.scope, attempt]);
  return (
    <section className="model-page">
      <UiPageHeading title="工作区可用模型">
        {payload.session.isPlatformAdmin && (
          <AppLink className="button secondary" href="/platform/models">
            平台模型管理
            <ArrowUpRight size={16} />
          </AppLink>
        )}
      </UiPageHeading>
      {error ? (
        <div role="alert" className="panel">
          <p>{error}</p>
          <button className="secondary" onClick={() => setAttempt((value) => value + 1)}>
            重新加载
          </button>
        </div>
      ) : !data ? (
        <p role="status">正在加载模型…</p>
      ) : data.items.length ? (
        <div className="model-summary-grid">
          {data.items.map((model) => (
            <article className="panel model-summary" key={model.id}>
              <Server size={22} />
              <h3>{model.alias}</h3>
              <p className="muted">{model.api_model}</p>
              <span className="status">Chat 可用</span>
              <div className="model-capability-pills">
                {Object.entries(capabilityNames)
                  .filter(
                    ([key]) =>
                      model.capabilities[key as keyof typeof capabilityNames].state === "supported",
                  )
                  .map(([key, label]) => (
                    <span key={key}>{label}</span>
                  ))}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel model-empty">
          <h3>暂无可用模型</h3>
          <p>请联系平台管理员配置并开放模型。</p>
        </div>
      )}
    </section>
  );
}
function PlatformModels() {
  const api = useDocumentSession();
  const [data, setData] = useState<ProviderPage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [draftQ, setDraftQ] = useState("");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Provider | "new" | null>(null);
  const [testing, setTesting] = useState<Provider | null>(null);
  const [changes, setChanges] = useState<{ provider: Provider; data: ModelChangePage } | null>(
    null,
  );
  const [refresh, setRefresh] = useState(0);
  const reload = useCallback(() => setRefresh((value) => value + 1), []);
  useEffect(() => {
    let live = true;
    void api
      .request<ProviderPage>(
        `platform/models?${new URLSearchParams({ page: String(page), page_size: "20", q, ...(kind ? { kind } : {}) })}`,
      )
      .then((result) => {
        if (live) {
          setData(result);
          setError("");
        }
      })
      .catch(() => {
        if (live) setError("模型配置暂时不可用，请重新加载。");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [api, q, kind, page, refresh]);
  useEffect(
    () =>
      api.onClear(() => {
        setData(null);
        setEditing(null);
        setTesting(null);
        setChanges(null);
      }),
    [api],
  );
  async function showChanges(provider: Provider) {
    try {
      setChanges({
        provider,
        data: await api.request<ModelChangePage>(
          `platform/models/${provider.id}/changes?page=1&page_size=50`,
        ),
      });
    } catch {
      setError("变更记录暂时不可用，请重试。");
    }
  }
  if (testing)
    return (
      <ModelPlayground
        provider={testing}
        onClose={() => {
          setTesting(null);
          reload();
        }}
      />
    );
  return (
    <section className="model-page">
      <UiPageHeading title="模型 Provider">
        <button onClick={() => setEditing("new")}>
          <Plus size={17} />
          添加 Provider
        </button>
      </UiPageHeading>
      <div className="model-notice">
        配置面向所有租户生效。支持 LiteLLM 的生成模型协议；平台控制分析 Chat
        的可用模型，决策模型在管理端测试。
      </div>
      <ObservabilityPanel providers={data?.items ?? []} />
      <ReliabilityPanel />
      <section className="panel model-list-panel">
        <form
          className="model-filters"
          data-editor-ignore
          onSubmit={(event) => {
            event.preventDefault();
            setQ(draftQ);
            setPage(1);
          }}
        >
          <label className="model-search">
            <Search size={16} />
            <input
              aria-label="搜索模型 Provider"
              placeholder="搜索模型名称或别称"
              value={draftQ}
              onChange={(event) => setDraftQ(event.target.value)}
              maxLength={100}
            />
          </label>
          <select
            aria-label="模型类型"
            value={kind}
            onChange={(event) => {
              setKind(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部类型</option>
            <option value="llm">LLM</option>
            <option value="decision">决策模型</option>
          </select>
          <button className="secondary" type="submit">
            搜索
          </button>
          <button type="button" className="icon-button" aria-label="刷新模型列表" onClick={reload}>
            <RefreshCw size={17} />
          </button>
        </form>
        {error && (
          <p role="alert" className="form-error">
            {error}
            <button className="secondary compact" onClick={reload}>
              重新加载
            </button>
          </p>
        )}
        {loading ? (
          <div className="model-empty" role="status">
            正在加载模型配置…
          </div>
        ) : data?.items.length ? (
          <>
            <div className="table-wrap">
              <table className="model-table">
                <thead>
                  <tr>
                    <th>模型 / 别称</th>
                    <th>类型与连接</th>
                    <th>状态</th>
                    <th>Chat 可见</th>
                    <th>最近测试</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((provider) => (
                    <tr key={provider.id}>
                      <td>
                        <strong>{provider.alias}</strong>
                        <small>{provider.api_model}</small>
                        <small>
                          {provider.vendor} · v{provider.version}
                        </small>
                      </td>
                      <td>
                        <span className="status status-muted">
                          {provider.kind === "llm" ? "LLM" : "决策模型"}
                        </span>
                        <small className="model-url" title={provider.base_url}>
                          {provider.base_url}
                        </small>
                        <small>
                          {provider.has_api_key
                            ? "密钥已配置"
                            : provider.has_connection_params
                              ? "原生认证参数已配置"
                              : "使用 SDK 原生认证"}
                        </small>
                      </td>
                      <td>
                        <span className={`status ${provider.enabled ? "" : "status-muted"}`}>
                          {provider.enabled ? "已启用" : "已停用"}
                        </span>
                      </td>
                      <td>
                        {provider.chat_visible && provider.enabled
                          ? "已开放"
                          : provider.chat_supported
                            ? "未开放"
                            : "仅管理端"}
                      </td>
                      <td>
                        {provider.latest_test ? (
                          <>
                            <span>
                              {modelStatus[provider.latest_test.status] ??
                                provider.latest_test.status}
                              {provider.latest_test.stale ? " · 已过期" : ""}
                            </span>
                            <small>
                              绑定版本 {provider.latest_test.binding_version} ·{" "}
                              {provider.latest_test.capabilities
                                .map(
                                  (name) =>
                                    capabilityNames[name as keyof typeof capabilityNames] ?? name,
                                )
                                .join("、") || "基础调用"}
                            </small>
                            <small>
                              {new Date(provider.latest_test.started_at).toLocaleString("zh-CN")}
                            </small>
                          </>
                        ) : (
                          <span className="muted">尚未测试</span>
                        )}
                      </td>
                      <td>
                        <div className="model-row-actions">
                          <button
                            className="secondary compact"
                            onClick={() => setEditing(provider)}
                          >
                            编辑
                          </button>
                          <button
                            className="secondary compact"
                            onClick={() => setTesting(provider)}
                          >
                            测试
                          </button>
                          <button className="model-link" onClick={() => void showChanges(provider)}>
                            变更记录
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="model-pagination">
              <span>
                共 {data.total} 个 Provider · 第 {page} 页
              </span>
              <div className="actions">
                <button
                  className="secondary compact"
                  disabled={page <= 1}
                  onClick={() => setPage((value) => value - 1)}
                >
                  上一页
                </button>
                <button
                  className="secondary compact"
                  disabled={page * data.page_size >= data.total}
                  onClick={() => setPage((value) => value + 1)}
                >
                  下一页
                </button>
              </div>
            </div>
          </>
        ) : (
          !error && (
            <div className="model-empty">
              <Server size={32} />
              <h3>{q || kind ? "没有匹配的模型" : "添加第一个模型 Provider"}</h3>
              <p>
                {q || kind
                  ? "调整搜索条件后重试。"
                  : "从模型目录选择模型，配置连接信息并验证能力。"}
              </p>
              {!q && !kind && <button onClick={() => setEditing("new")}>添加 Provider</button>}
            </div>
          )
        )}
      </section>
      {editing && (
        <ProviderEditor
          provider={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
      {changes && (
        <Modal
          open
          title={`${changes.provider.alias} · 变更记录`}
          description="仅记录配置字段、版本与操作者，不包含凭据或模型输入输出。"
          onClose={() => setChanges(null)}
        >
          <div className="model-changes">
            {changes.data.items.length ? (
              changes.data.items.map((item) => (
                <article key={item.operation_id}>
                  <strong>
                    版本 {item.before_version ?? "新建"} → {item.after_version}
                  </strong>
                  <p>{item.changed_fields.join("、")}</p>
                  <small className="muted">
                    {new Date(item.created_at).toLocaleString("zh-CN")} · {item.actor_user_id}
                  </small>
                </article>
              ))
            ) : (
              <p>暂无变更记录。</p>
            )}
          </div>
        </Modal>
      )}
    </section>
  );
}
