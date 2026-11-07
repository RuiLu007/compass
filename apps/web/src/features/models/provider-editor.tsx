"use client";
import { useEffect, useRef, useState } from "react";
import { Modal } from "@/components/ui/dialog";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { ApiError } from "@/client/request-scope";
import type { CatalogItem, CatalogPage, Provider, SavedProvider } from "./types";

export function ProviderEditor({
  provider,
  onClose,
  onSaved,
}: {
  provider?: Provider;
  onClose: () => void;
  onSaved: () => void;
}) {
  const api = useDocumentSession();
  const [catalog, setCatalog] = useState<CatalogPage | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<CatalogItem | null>(null);
  const [catalogId, setCatalogId] = useState(provider?.catalog_id ?? "");
  const [model, setModel] = useState(provider?.api_model ?? "");
  const [alias, setAlias] = useState(provider?.alias ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [key, setKey] = useState("");
  const [manual, setManual] = useState(false);
  const [connectionParams, setConnectionParams] = useState("");
  const [enabled, setEnabled] = useState(provider?.enabled ?? false);
  const [chatVisible, setChatVisible] = useState(provider?.chat_visible ?? false);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [busy, setBusy] = useState(false);
  const [unknownOperation, setUnknownOperation] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const locked = useRef(false);
  const retryOperation = useRef<string | null>(null);
  useEffect(
    () =>
      api.onClear(() => {
        setKey("");
        setConnectionParams("");
        setAlias("");
        setBaseUrl("");
        setModel("");
        setSelected(null);
        setCatalog(null);
      }),
    [api],
  );
  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      setCatalogError("");
      void api
        .request<CatalogPage>(
          `platform/models/catalog?${new URLSearchParams({ q: query, page: "1", page_size: "100" })}`,
        )
        .then((value) => {
          if (live) setCatalog(value);
        })
        .catch(() => {
          if (live) setCatalogError("模型目录暂时不可用，请稍后重新搜索。");
        });
    }, 180);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [api, query]);
  const currentCatalog = selected ?? catalog?.items.find((item) => item.id === catalogId);
  const decision = manual
    ? ["aliyun", "typesafe", "systemone"].includes(catalogId.trim().split("/")[0])
    : (currentCatalog?.kind ?? provider?.kind) === "decision";
  const chatAllowed =
    !decision &&
    (manual
      ? Boolean(catalogId.trim())
      : (currentCatalog?.chat_supported ??
        (provider?.catalog_id === catalogId && provider.chat_supported)));
  function chooseModel(id: string) {
    const item = catalog?.items.find((value) => value.id === id);
    if (!item || !item.adapter_supported) return;
    setCatalogId(item.id);
    setSelected(item);
    setModel(item.api_model);
    setBaseUrl(item.base_url ?? "");
    setChatVisible(false);
    if (!alias) setAlias(item.api_model);
    setDirty(true);
    api.setDirty(true);
  }
  const close = () => {
    if (busy || (dirty && !window.confirm("放弃尚未保存的模型配置？"))) return;
    setKey("");
    setConnectionParams("");
    api.setDirty(false);
    onClose();
  };
  async function checkSave(operationId: string) {
    try {
      await api.request<SavedProvider>(
        `platform/models/operations/${operationId}`,
        "GET",
        undefined,
        { retainOnNotFound: true },
      );
      api.setDirty(false);
      setKey("");
      onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        retryOperation.current = operationId;
        setUnknownOperation(null);
        setError("尚未找到这次保存记录。请确认配置并重新输入 API Key 后再保存。");
      } else setError("暂时无法确认保存结果，请稍后再次核对。");
    }
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (locked.current || unknownOperation) return;
    if (!catalogId || !model.trim() || !alias.trim()) {
      setError("请填写模型标识、API 模型名和显示别称。");
      return;
    }
    let params: Record<string, unknown> | undefined;
    if (!decision && connectionParams.trim()) {
      try {
        const value: unknown = JSON.parse(connectionParams);
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
        params = value as Record<string, unknown>;
      } catch {
        setError("供应商连接参数必须是 JSON 对象。");
        return;
      }
    }
    if (
      provider &&
      (provider.enabled !== enabled || provider.chat_visible !== chatVisible) &&
      !window.confirm("此变更会影响所有租户后续可用的模型，确认保存？")
    )
      return;
    locked.current = true;
    setBusy(true);
    setError("");
    const operationId = retryOperation.current ?? crypto.randomUUID();
    try {
      await api.request<SavedProvider>(
        provider ? `platform/models/${provider.id}` : "platform/models",
        provider ? "PATCH" : "POST",
        {
          operation_id: operationId,
          ...(provider ? { expected_version: provider.version } : {}),
          catalog_id: catalogId.trim(),
          api_model: model.trim(),
          alias: alias.trim(),
          base_url: baseUrl.trim(),
          ...(key.trim() ? { api_key: key.trim() } : {}),
          ...(decision
            ? provider?.has_connection_params
              ? { connection_params: {} }
              : {}
            : params
              ? { connection_params: params }
              : {}),
          enabled,
          chat_visible: Boolean(chatAllowed && chatVisible),
        },
      );
      setKey("");
      setConnectionParams("");
      api.setDirty(false);
      onSaved();
    } catch (cause) {
      setKey("");
      setConnectionParams("");
      if (!(cause instanceof ApiError) || cause.status >= 500) {
        setUnknownOperation(operationId);
        setError("连接中断，保存结果待确认。请先核对结果，避免重复保存。");
      } else
        setError(
          cause.status === 409
            ? "配置已被其他管理员更新。请关闭表单并刷新列表后再编辑。"
            : cause.message,
        );
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  return (
    <Modal
      title={provider ? "编辑模型 Provider" : "添加模型 Provider"}
      description="平台统一配置，向各租户提供模型。API Key 仅写入，不会回显。"
      open
      onClose={close}
      wide
    >
      <form
        onSubmit={save}
        className="model-form"
        data-editor
        onChange={() => {
          setDirty(true);
          api.setDirty(true);
        }}
      >
        <fieldset disabled={busy || Boolean(unknownOperation)}>
          <label className="model-check">
            <input
              type="checkbox"
              checked={manual}
              onChange={(e) => {
                setManual(e.target.checked);
                setSelected(null);
              }}
            />
            手动输入模型标识
          </label>
          {manual ? (
            <label>
              模型标识
              <input
                value={catalogId}
                aria-label="模型标识"
                aria-describedby="model-identifier-help"
                onChange={(e) => {
                  const id = e.target.value;
                  setCatalogId(id);
                  setModel(id.includes("/") ? id.slice(id.indexOf("/") + 1) : id);
                }}
                maxLength={200}
                required
                placeholder="如：anthropic/claude-sonnet-4-5"
              />
              <span className="muted model-help" id="model-identifier-help">
                生成模型填写 provider/model；决策模型填写 typesafe/model、aliyun/model 或
                systemone/model。
              </span>
            </label>
          ) : (
            <>
              <label>
                搜索模型目录
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索供应商或模型名称"
                  autoFocus
                  maxLength={100}
                />
              </label>
              {catalogError && (
                <p role="alert" className="form-error">
                  {catalogError}
                </p>
              )}
              <label>
                模型目录
                <select
                  aria-label="模型目录"
                  value={catalogId}
                  required
                  onChange={(e) => chooseModel(e.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      return;
                    }
                    if (
                      !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) ||
                      event.altKey
                    )
                      return;
                    event.preventDefault();
                    const options = catalog?.items.filter((item) => item.adapter_supported) ?? [];
                    const index = options.findIndex((item) => item.id === catalogId);
                    const next =
                      event.key === "Home"
                        ? 0
                        : event.key === "End"
                          ? options.length - 1
                          : event.key === "ArrowDown"
                            ? Math.min(index + 1, options.length - 1)
                            : Math.max(index - 1, 0);
                    if (options[next]) chooseModel(options[next].id);
                  }}
                >
                  <option value="">请选择模型</option>
                  {catalogId && !catalog?.items.some((item) => item.id === catalogId) && (
                    <option value={catalogId}>{model}（当前配置）</option>
                  )}
                  {catalog?.items.map((item) => (
                    <option key={item.id} value={item.id} disabled={!item.adapter_supported}>
                      {item.vendor} · {item.api_model}
                      {item.adapter_supported ? "" : " · 暂不支持此适配器"}
                    </option>
                  ))}
                </select>
              </label>
              <p className="muted model-help">
                {catalog
                  ? `LiteLLM ${catalog.sdk_version} 与 System One 预设 · 匹配 ${catalog.total} 个模型，搜索可缩小范围。`
                  : "正在加载模型目录…"}{" "}
                方向键选择，Enter 确认。
              </p>
            </>
          )}
          <div className="model-form-grid">
            <label>
              API 模型名
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                maxLength={200}
                required
              />
            </label>
            <label>
              显示别称
              <input
                value={alias}
                onChange={(e) => setAlias(e.target.value)}
                maxLength={80}
                required
                placeholder="如：DeepSeek 旗舰"
              />
            </label>
          </div>
          <label>
            Base URL
            <input
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              maxLength={2048}
              required={decision}
              placeholder={decision ? "如：https://api.typesafe.ai/v1" : "留空使用供应商默认端点"}
            />
          </label>
          {decision && (
            <p className="muted model-help">
              填写服务的 API Base URL，调用时追加 /systemone。阿里云使用工作区 /compatible-mode/v1。
            </p>
          )}
          <label>
            API Key
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              autoComplete="new-password"
              maxLength={4096}
              required={decision && !provider?.has_api_key}
              placeholder={
                provider?.has_api_key
                  ? "已配置；留空保留现有凭据"
                  : decision
                    ? "必填，使用服务的 Bearer API Key"
                    : "可留空使用供应商原生认证"
              }
            />
          </label>
          <p className="muted model-help">
            编辑已有配置时，留空会保留现有凭据。关闭表单或提交后会清空本次输入。
          </p>
          {!decision && (
            <details>
              <summary>供应商连接参数{provider?.has_connection_params ? "（已配置）" : ""}</summary>
              <label>
                供应商连接参数（JSON）
                <textarea
                  value={connectionParams}
                  onChange={(e) => setConnectionParams(e.target.value)}
                  rows={5}
                  maxLength={32768}
                  spellCheck={false}
                  placeholder={
                    '如：{"api_version":"2024-10-21"} 或 {"aws_region_name":"us-east-1"}'
                  }
                />
              </label>
              <p className="muted model-help">
                按 LiteLLM 的供应商文档填写。参数加密保存，不回显；留空保留，填写 {"{}"} 清空。
              </p>
            </details>
          )}
          <label className="model-check">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            启用此 Provider
          </label>
          <label className="model-check">
            <input
              type="checkbox"
              checked={Boolean(chatAllowed && chatVisible)}
              disabled={!chatAllowed}
              onChange={(e) => setChatVisible(e.target.checked)}
            />
            允许出现在分析 Chat 主模型列表
          </label>
          {!chatAllowed && (
            <p className="muted model-help">
              选择生成模型后可开放给分析 Chat；决策模型在管理端测试。
            </p>
          )}
        </fieldset>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <div className="actions model-form-actions">
          <button type="button" className="secondary" onClick={close} disabled={busy}>
            取消
          </button>
          {unknownOperation ? (
            <button type="button" onClick={() => void checkSave(unknownOperation)}>
              核对保存结果
            </button>
          ) : (
            <button type="submit" disabled={busy}>
              {busy ? "正在保存…" : "保存配置"}
            </button>
          )}
        </div>
      </form>
    </Modal>
  );
}
