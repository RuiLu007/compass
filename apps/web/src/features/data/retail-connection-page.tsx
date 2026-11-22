"use client";
import { useEffect, useState, type FormEvent } from "react";
import { CircleAlert, CircleCheck, Database, Plus, RefreshCw } from "lucide-react";
import { useDocumentSession } from "@/components/shell/session-boundary";
import { Modal } from "@/components/ui/dialog";
import { UiPageHeading } from "@/components/ui/page-heading";
import type { SessionView } from "@/contracts/page";
import type { components } from "@/generated/api";
import { ApiError } from "@/client/request-scope";
import styles from "./retail-connection-page.module.css";

type Connection = components["schemas"]["DataConnectionView"];
type ConnectionList = components["schemas"]["DataConnectionList"];
type ConnectionTest = components["schemas"]["RetailConnectionTest"];
type Engine = Connection["engine"];

const engines = {
  postgresql: { label: "PostgreSQL", port: 5432, sslmode: "prefer" },
  mysql: { label: "MySQL", port: 3306, sslmode: "verify-full" },
};

const testMessages: Record<ConnectionTest["status"], string> = {
  ok: "连接可用。",
  connection_failed: "连接失败，请检查地址、账号、密码和 SSL 模式。",
  identity_mismatch: "只读身份或 Retail 租户权限不匹配，请检查发布库和账号。",
  unavailable: "连接暂时无法测试，请检查部署配置后重试。",
};

export function RetailConnectionPage({ session }: { session: SessionView }) {
  const { tenant, workspace, isPlatformAdmin } = session;
  return (
    <section className="model-page">
      {isPlatformAdmin && tenant && workspace ? (
        <Connections
          key={`${tenant.id}/${workspace.id}`}
          query={new URLSearchParams({ tenantId: tenant.id, workspaceId: workspace.id }).toString()}
        />
      ) : (
        <>
          <UiPageHeading title="数据连接" />
          <div className="panel">请联系平台管理员配置此工作区的数据连接。</div>
        </>
      )}
    </section>
  );
}

function Connections({ query }: { query: string }) {
  const api = useDocumentSession();
  const [connections, setConnections] = useState<Connection[]>();
  const [editing, setEditing] = useState<Connection | "new">();
  const [attempt, setAttempt] = useState(0);
  const [loadingError, setLoadingError] = useState("");
  const [notice, setNotice] = useState("");
  const [testing, setTesting] = useState<string>();
  const [results, setResults] = useState<Record<string, ConnectionTest>>({});
  const listPath = `data/connections?${query}`;

  useEffect(() => {
    const controller = new AbortController();
    void api
      .request<ConnectionList>(listPath, "GET", undefined, { signal: controller.signal })
      .then(({ items }) => {
        if (!controller.signal.aborted) {
          setConnections(items);
          setLoadingError("");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadingError("暂时无法加载数据连接。");
      });
    const clear = api.onClear(() => {
      setConnections(undefined);
      setEditing(undefined);
      setResults({});
    });
    return () => {
      controller.abort();
      clear();
    };
  }, [api, listPath, attempt]);

  async function test(connection: Connection) {
    setTesting(connection.id);
    setNotice("");
    setResults((previous) => {
      const next = { ...previous };
      delete next[connection.id];
      return next;
    });
    try {
      const result = await api.request<ConnectionTest>(
        `data/connections/${connection.id}/test?${query}`,
        "POST",
        {},
        { retainOnNotFound: true },
      );
      if (result.revision !== connection.revision) {
        setNotice("配置已变化，请重新加载后测试。");
        return;
      }
      setResults((previous) => ({ ...previous, [connection.id]: result }));
    } catch (error) {
      setNotice(error instanceof ApiError ? error.message : "测试失败，请重试。");
    } finally {
      setTesting(undefined);
    }
  }

  const reload = () => {
    setNotice("");
    setAttempt((value) => value + 1);
  };

  return (
    <>
      <UiPageHeading title="数据连接">
        {connections && !loadingError && (
          <button className="secondary" onClick={reload} disabled={!!testing}>
            <RefreshCw size={16} aria-hidden="true" /> 重新加载
          </button>
        )}
        <button onClick={() => setEditing("new")} disabled={!connections || !!loadingError}>
          <Plus size={17} aria-hidden="true" /> 新增连接
        </button>
      </UiPageHeading>
      {notice && <p role="status">{notice}</p>}
      {loadingError ? (
        <div className="panel">
          <p role="alert">{loadingError}</p>
          <button className="secondary" onClick={reload}>
            重新加载
          </button>
        </div>
      ) : !connections ? (
        <p role="status">正在加载数据连接…</p>
      ) : connections.length === 0 ? (
        <div className="panel model-empty">
          <h3>当前工作区暂无数据连接</h3>
          <p>新增连接后，可从列表测试连接。</p>
        </div>
      ) : (
        <div className={styles.cards}>
          {connections.map((connection) => {
            const isRetail = connection.retail_tenant_id != null;
            const savedResult = results[connection.id];
            const result = savedResult?.revision === connection.revision ? savedResult : undefined;
            const endpoint = `${connection.host}:${connection.port}/${connection.database_name}`;
            return (
              <article className={`panel model-summary ${styles.card}`} key={connection.id}>
                <div className={styles.cardHeader}>
                  <span className={styles.cardIcon}>
                    <Database size={20} aria-hidden="true" />
                  </span>
                  <div className={styles.badges}>
                    <span className="status status-muted">{engines[connection.engine].label}</span>
                    {isRetail && <span className="status">默认 Retail</span>}
                  </div>
                </div>
                <div className={styles.identity}>
                  <h3>{connection.name}</h3>
                  <p className="muted" title={endpoint}>
                    {endpoint}
                  </p>
                </div>
                {result ? (
                  <p
                    className={`${styles.feedback} ${result.status === "ok" ? styles.success : styles.failure}`}
                    role={result.status === "ok" ? "status" : "alert"}
                  >
                    {result.status === "ok" ? (
                      <CircleCheck size={14} aria-hidden="true" />
                    ) : (
                      <CircleAlert size={14} aria-hidden="true" />
                    )}
                    <span>
                      {result.status === "ok" && isRetail
                        ? "连接可用。只读身份和 Retail 租户权限验证通过。"
                        : testMessages[result.status]}
                    </span>
                  </p>
                ) : (
                  <p className={`${styles.feedback} muted`}>
                    {testing === connection.id ? "正在检查连接…" : "尚未测试"}
                  </p>
                )}
                <footer className={`actions ${styles.cardActions}`}>
                  <button className="secondary" onClick={() => setEditing(connection)}>
                    编辑
                  </button>
                  <button
                    className="secondary"
                    onClick={() => void test(connection)}
                    disabled={!!testing}
                  >
                    {testing === connection.id ? "正在测试…" : "测试连接"}
                  </button>
                </footer>
              </article>
            );
          })}
        </div>
      )}
      {editing && (
        <ConnectionEditor
          connection={editing === "new" ? undefined : editing}
          canCreateRetail={!connections?.some((item) => item.retail_tenant_id != null)}
          query={query}
          onClose={() => setEditing(undefined)}
          onSave={(saved) => {
            setConnections((previous) => {
              if (!previous?.some((item) => item.id === saved.id))
                return saved.retail_tenant_id == null
                  ? [...(previous ?? []), saved]
                  : [saved, ...(previous ?? [])];
              return previous.map((item) => (item.id === saved.id ? saved : item));
            });
            setEditing(undefined);
            setNotice("配置已保存。");
          }}
        />
      )}
    </>
  );
}

function ConnectionEditor({
  connection,
  canCreateRetail,
  query,
  onClose,
  onSave,
}: {
  connection?: Connection;
  canCreateRetail: boolean;
  query: string;
  onClose: () => void;
  onSave: (connection: Connection) => void;
}) {
  const api = useDocumentSession();
  const [engine, setEngine] = useState<Engine>(connection?.engine ?? "postgresql");
  const [retail, setRetail] = useState(connection?.retail_tenant_id != null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const login = {
      host: String(fields.get("host")).trim(),
      port: Number(fields.get("port")),
      database_name: String(fields.get("database_name")).trim(),
      role: String(fields.get("role")).trim(),
      sslmode: String(fields.get("sslmode")),
    };
    const password = String(fields.get("password"));
    if (
      !password &&
      (!connection ||
        Object.entries(login).some(
          ([key, value]) => connection[key as keyof typeof login] !== value,
        ))
    ) {
      setError("首次配置或修改连接目标时，请输入密码。");
      return;
    }
    const resource = connection ? `connections/${connection.id}` : "connections";
    setSaving(true);
    setError("");
    try {
      const result = await api.request<Connection>(
        `data/${resource}?${query}`,
        connection ? "PATCH" : "POST",
        {
          ...login,
          name: String(fields.get("name")).trim(),
          engine,
          password: password || null,
          revision: connection?.revision ?? 0,
          retail_tenant_id: retail ? Number(fields.get("retail_tenant_id")) : null,
        },
        { retainOnNotFound: true },
      );
      onSave(result);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "保存失败，请重试。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={connection ? "编辑连接" : "新增连接"}
      description="填写数据库地址和登录信息，保存后可测试连接。"
      open
      onClose={() => {
        if (!saving) onClose();
      }}
    >
      <form className="model-form" onSubmit={save} onChange={() => setError("")}>
        <fieldset
          disabled={saving}
          className="model-form-grid"
          style={{ border: 0, margin: 0, padding: 0 }}
        >
          <label>
            连接名称
            <input name="name" defaultValue={connection?.name} maxLength={80} required />
          </label>
          <label>
            数据库类型
            <select
              value={engine}
              disabled={!!connection}
              onChange={(event) => {
                setEngine(event.target.value as Engine);
                setRetail(false);
              }}
            >
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
            </select>
          </label>
          <label>
            主机
            <input name="host" defaultValue={connection?.host} maxLength={253} required />
          </label>
          <label>
            端口
            <input
              key={engine}
              name="port"
              type="number"
              defaultValue={connection?.port ?? engines[engine].port}
              min={1}
              max={65535}
              required
            />
          </label>
          <label>
            数据库
            <input
              name="database_name"
              defaultValue={connection?.database_name}
              maxLength={64}
              required
            />
          </label>
          <label>
            账号
            <input name="role" defaultValue={connection?.role} maxLength={128} required />
          </label>
          <label>
            密码
            <input
              name="password"
              type="password"
              autoComplete="new-password"
              maxLength={1024}
              aria-describedby="connection-password-hint"
              required={!connection}
            />
            <span id="connection-password-hint" className="muted model-help">
              {connection ? "已配置。保留原密码时留空。" : "请输入数据库账号的密码。"}
            </span>
          </label>
          <label>
            SSL 模式
            <select
              key={engine}
              name="sslmode"
              defaultValue={connection?.sslmode ?? engines[engine].sslmode}
            >
              {engine === "postgresql" && (
                <>
                  <option value="prefer">优先使用 SSL</option>
                  <option value="require">要求 SSL</option>
                </>
              )}
              <option value="verify-full">验证证书和主机名</option>
              <option value="disable">禁用 SSL</option>
            </select>
          </label>
          {!connection && canCreateRetail && engine === "postgresql" && (
            <label className="model-check">
              <input
                type="checkbox"
                checked={retail}
                onChange={(event) => setRetail(event.target.checked)}
              />{" "}
              用于 Retail 分析
            </label>
          )}
          {retail && (
            <label>
              Retail 租户 ID
              <input
                name="retail_tenant_id"
                type="number"
                defaultValue={connection?.retail_tenant_id ?? undefined}
                min={1}
                max={Number.MAX_SAFE_INTEGER}
                required
              />
            </label>
          )}
        </fieldset>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button type="submit" disabled={saving}>
            {saving ? "正在保存…" : "保存配置"}
          </button>
          <button type="button" className="secondary" disabled={saving} onClick={onClose}>
            取消
          </button>
        </div>
      </form>
    </Modal>
  );
}
