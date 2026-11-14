# 第 18 课：统一工具执行与独立 Sandbox

本课提供三种执行绑定：受信函数使用 `in_process`，Python 分析使用 OpenSandbox，用户配置的远端工具使用 MCP。副作用、审批、串并行是独立策略。当前正式内置工具为 todo_write、database_query 和 python_analysis，位于 `tools/builtin/<tool_name>/`，静态工具清单统一维护在 `tools/catalog.py`。

分析首页的「第 18 课 · 工具执行与 Sandbox」卡片提供「Python 分析绘图」「同轮多工具查询」「使用我的 MCP 工具」三个提示词。点击后预填问题，可编辑后发送。Python 示例需要已授权的 PostgreSQL 连接与启用的 Sandbox；MCP 示例使用当前用户已配置的连接。多工具示例按后端批次策略执行，演示并行时设置 `COMPASS_TOOL_BATCH_MODE=parallel` 后启动 Agent。

## 模块与入口

| 模块             | 职责                                                     |
| ---------------- | -------------------------------------------------------- |
| `api/`           | Agent 内部 HTTP、可信身份、Run/SSE、MCP 配置、产物读取   |
| `runtime/`       | Run 生命周期、工具装配、取消、持久化和恢复               |
| `harness/`       | 通用推理循环、执行预算和可选的核验钩子                   |
| `routing/`       | 意图、模型选择与确定性 Workflow 分流                     |
| `workflows/`     | 通用 JS 脚本引擎与历史工作流资产                         |
| `tools/`         | 不依赖上述编排模块的注册表、策略、调度、配额、结果与产物 |
| `tools/builtin/` | 通用 Todo、只读数据库查询与 Python                       |
| `tools/mcp/`     | 本人连接配置、加密凭据、协议会话、分页发现与执行         |
| `tools/sandbox/` | OpenSandbox 客户端、分配租约、计算回收、NAS 保留期核对   |

任务启动时先发现 MCP、读取获授权的数据库连接，合并当前启用的内置声明，检查 16 个工具 / 64 KiB 的总上限，再进入模型路由。超限时直接返回明确错误；可减少 MCP 所选工具。模型声明和实际执行读取同一份不可变 Run Registry。每次启动任务重新发现 MCP，当前任务不热增删工具。工具事件包含目录 ID、原调用 ID、环境、策略、耗时与清理结果；远端返回数据不进入服务日志。

`ToolBatchPolicy` 默认 `serial + stop`。`parallel` 的上限是每 Run 2、进程 4；MCP 连接另有 1～2 的上限。Todo、提交和 Workflow 等推进状态的操作按串行处理；独立查询、Python 和 MCP 可以并行。整批先验证参数、可见性和审批，任何待审项都会暂停整批。结果回填使用模型原顺序，完成事件使用真实完成顺序。清理未知会停止后续调用并保留占用。

## 本地启动

需要 Docker daemon、Python/uv 与项目依赖。命令从 Compass 根执行。新安装先配置根 `.env` 的数据库、服务令牌和模型加密密钥，并完成应用迁移及数据连接配置。

```bash
pnpm db:migrate -F agent
pnpm sandbox init
pnpm sandbox up
set -a
source work/sandbox/local.env
set +a
pnpm dev -F agent
```

`init` 生成权限为 0600 的本地服务密钥，保留已有文件。`up` 构建 Python 和 NAS 镜像、启动 OpenSandbox/NFS-Ganesha，并校验 16 个带归属标签的 Docker NFS 卷。数据端点使用宿主机 `20000～29999` 端口段，部署时为 Sandbox 预留。默认网络 `172.29.18.0/24`，NAS 内网地址 `172.29.18.2`，控制面只向宿主机 `127.0.0.1:8112` 发布。已有同名不兼容卷会明确报错。

Python 环境包含固定版本 NumPy 和 Matplotlib。每个 slot 使用独立 64 MiB ext4 文件系统，通过 NFSv4 挂载。计算容器使用非 root UID 1000、1 CPU、512 MiB、64 PIDs、去除 capabilities、no-new-privileges 和 `dns+nft` 默认拒绝出网（同时限制域名和直连 IP）。Agent 不持有 Docker socket；Docker 管理权只在 OpenSandbox 控制面与卷供应操作中使用。Ganesha 管理容器需要 mount/loop 权限，独立于执行用户代码的容器。

课堂端到端拓扑为宿主机 Agent + Docker Sandbox/NAS，控制面和数据端口仅向宿主机回环地址发布。五应用 Compose 已透传 MCP、批次和 Sandbox 配置，默认关闭 Sandbox；将 Agent 放入容器或远端节点时，需同步配置该节点可达的 OpenSandbox 控制面与 execd 数据地址。云端沿用 SDK 和分配记录，以受控 Linux 节点、gVisor 和托管 NAS 部署；节点容量、运行时、私有网络和 NAS 配额需按云端环境配置。课堂 Docker Desktop 使用 runc。

```bash
pnpm sandbox status
pnpm sandbox logs
# 等待在途任务完成并核对未知资源后停止管理服务，保留 NAS 卷。
pnpm sandbox down
```

## Python 主链与产物

先通过 `database_query` 取得当前 Run 的 `resultId`，再调用 `python_analysis`：

```json
{
  "source_query_id": "本轮查询返回的 ID",
  "code": "import matplotlib.pyplot as plt\nvalues = [int(row['paidCent']) for row in data['rows']]\nresult = {'totalCent': sum(values)}\nplt.bar(range(len(values)), values)\nplt.savefig('/workspace/chart.png')"
}
```

`data` 来自服务端保存的完整授权查询快照。代码最多 16 KiB，输入 128 KiB，JSON 结果 64 KiB。每次执行单独创建计算容器和工作卷；计算删除确认后，新建只读取件容器读取固定文件，再确认取件容器删除。PNG 校验通过后才发布产物引用：单文件 512 KiB、单 Run 2 MiB，图片最大 2048 边长 / 100 万像素。

创建等待最多 30 秒、Python 执行最多 20 秒、取件命令最多 10 秒。完整工具生命周期最多 106 秒并截断到 Run 剩余时间。远端 TTL 120 秒只作失联兜底，正常路径主动删除。创建回包丢失后保留租约，按 allocation/phase/sequence/host 元数据核对资源，不重放代码。缺少 Sandbox ID 且暂未查到资源时，继续保留未知租约；找到原资源并确认删除后才归还占用。已知 ID 的执行可直接查询其删除结局。

前端通过 `/api/web/v1/analysis/runs/{runId}/artifacts/{artifactId}` 读取图表，每次重新完成会话与工作区授权；Agent 同时校验完整 owner 和 Run。已接收字节与 NAS 分离。为支持终态后的下载，M4 在当前 Agent 进程保留产物最多 30 分钟，最多 64 个 Run、每 Run 2 MiB；进程退出后失效。现有 M4 Run 路由要求同一任务回到原进程，第 19 课承接持久化读取。

工作卷保留 24 小时。受控只读检查入口为 `POST /v1/analysis/runs/{runId}/sandboxes/{allocationId}/inspect`，使用内部服务令牌与本人可信上下文，返回结果与已校验图表的 SHA-256；没有客户端路径参数。到期后后台固定清理器检查空目录，再释放 slot。计算未知状态持续占用容量，并进入及时核对；先核对计算，再清理到期存储。同一 OpenSandbox 节点的 Agent 副本使用相同 host_id，共享租约和配额；其他副本完成清理后，原副本从租约状态释放本地 Sandbox 占用。MCP 的未知占用独立保留。

## 配置本人 MCP

使用 Web 登录会话调用 `/api/mcp/connections?tenantId=…&workspaceId=…`。GET 列表；POST 创建；PATCH `/{id}` 修改；POST `/{id}/test` 初始化并发现工具。写请求携同源 `Origin` 和会话 `X-CSRF-Token`，actor 来自服务端会话。连接密钥加密保存，不回显。

```json
{
  "revision": 0,
  "name": "经营数据 MCP",
  "endpoint": "https://tools.example.com/mcp",
  "enabled": true,
  "required": false,
  "max_inflight": 2,
  "selected_tools": ["sales_summary"],
  "tool_policies": [{ "name": "sales_summary", "effect": "read_only", "approval": "none" }]
}
```

创建前将 origin 加入 Agent 的 `COMPASS_MCP_ALLOWED_ORIGINS`。课堂 HTTP 夹具单独使用 `COMPASS_MCP_LOCAL_FIXTURE_ORIGINS`，共享部署保持为空。客户端校验 origin 与 DNS、固定连接 IP、拒绝重定向和元数据地址，仅支持 Streamable HTTP。每人每工作区最多 4 个连接；发现并发 2、单连接 8 秒、总计 10 秒，最多读取 256 个目录条目 / 512 KiB。应用所需声明最多 16 个 / 64 KiB，不截断目录。

每轮使用冻结映射，实际调用前后检查配置 revision，在途调用监听清单变化并轮询配置撤销。外部返回只接收 64 KiB 内的文本与 `structuredContent`，有 output schema 时复验；二进制与资源 URI 不自动访问。未显式登记的工具采用 `unknown + required`，远端 readOnlyHint 不赋予本地权限。远端取消无法确认执行结局时，任务进入清理未确认状态，保留隔离额度并且不自动重试。

本项目沿用 MCP SDK 2.3.0，兼容测试固定握手协议 2025-11-25；OpenSandbox SDK 1.2.0。镜像 digest 和 Python 库版本见 `deploy/sandbox/`。

## 验证入口

```bash
pnpm test -F agent
pnpm test -F web
pnpm contracts:check
pnpm check
pnpm build
# 显式配置独立 AGENT_TEST_*、AGENT_RETAIL_TEST_* 和 Sandbox API key 后执行：
pnpm test:integration -F agent
# 仅在独占的课堂 Sandbox 环境中启用控制面重启、NAS 暂停故障用例：
COMPASS_SANDBOX_FAULT_TESTS=true pnpm test:integration -F agent
# 真实 HTTP/SQL/MCP/Sandbox 的浏览器夹具：
# tests.tool_e2e_app 在 8128、tests.tool_mcp_fixture 在 8129；模型返回由夹具固定。
# WEB_TEST_* 指向独立教学测试库，AGENT_API_URL 指向 8128。
WEB_TEST_TOOL_RUNTIME=true pnpm test:e2e -F web -- tool-runtime.spec.ts
```

浏览器验收使用真实 Web 登录、BFF、Agent、PostgreSQL、MCP 协议、Docker 和 NFS，模型生成环节用固定响应保证故障与审批路径可复现。执行前按 `apps/web/scripts/test-setup.mjs --fixture teaching` 的要求供应测试库；Agent 零售夹具由 `tests.workflow_fixture` 创建并限定在测试库。当前运行结果登记在 [本课实施记录](../mai-specs/986-lesson-18-tool-execution-sandbox/IMPLEMENTATION.md)。

浏览器验收的两个本地服务从仓库根启动，分别在独立终端执行（继承同一套测试环境）：

```bash
# Agent DATABASE_URL 指向已迁移的 AGENT_TEST_DATABASE_URL；启用 Sandbox 并导入 local.env。
# COMPASS_WORKFLOW_E2E_RETAIL_DSN 指向独立的 compass_retail_test_* 夹具库。
PYTHONPATH=apps/agent uv run --locked uvicorn tests.tool_mcp_fixture:app --host 127.0.0.1 --port 8129
COMPASS_MCP_LOCAL_FIXTURE_ORIGINS=http://127.0.0.1:8129 PYTHONPATH=apps/agent uv run --locked uvicorn tests.tool_e2e_app:app --host 127.0.0.1 --port 8128
# 第三个终端：WEB_TEST_DATABASE_URL / WEB_TEST_MIGRATION_DATABASE_URL / WEB_TEST_ADMIN_URL
# 和 WEB_TEST_PASSWORD 指向隔离教学账户；WEB_TEST_PORT 使用空闲端口。
pnpm test:setup -F web -- --fixture teaching
AGENT_API_URL=http://127.0.0.1:8128 WEB_TEST_TOOL_RUNTIME=true pnpm test:e2e -F web -- tool-runtime.spec.ts
```

未设置 Sandbox 凭据时，集成测试会显式跳过真实容器项；该结果不计入本课容器验收。故障测试会暂停并恢复名为 `compass-sandbox` 的课堂管理服务，执行期间保持该环境独占。
