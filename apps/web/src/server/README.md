# Web 服务端模块

`composition.ts` 为 HTTP 与 RSC 组装同一组应用用例，私有查询共用一个 PostgreSQL 只读快照。管理写入、成功审计和通知共享连接与事务；审计导出使用读写快照，提交完成后才返回 CSV。模块之间只依赖 `public.ts`；领域规则不依赖框架和数据库，应用用例通过具名存储接口调用 PostgreSQL 适配器。

| 位置          | 责任                                           |
| ------------- | ---------------------------------------------- |
| identity      | 本地账号摘要、密码校验、会话、个人偏好         |
| access        | 租户、空间、直接成员、个人权限与页面资格       |
| audit         | 追加审计、范围查询、游标和 CSV 编码            |
| models        | 平台模型管理、租户模型摘要与 Chat 的数据库授权 |
| notifications | 本人可见通知、投递去重与标已读                 |
| platform      | 错误、配置、事务与跨模块基础类型               |
| http          | 严格 DTO、固定 Origin / CSRF、响应与缓存约定   |

密码与平台资格由受管工具维护，运行凭据只有展示字段更新权限。迁移源、权限授予及恢复方法见 [web/main](../../db/main/README.md)。`src/contracts` 只公开安全 DTO 与纯权限目录，客户端不导入服务器模块。ESLint 与 [导入边界测试](../../tests/architecture/imports.test.ts)验证框架、分层及跨模块依赖。

受保护页面使用完整文档导航；服务端初始隐藏内容，客户端同版本复验成功后显示。页面失效后同步隐藏正文和弹层、终止旧请求、清空草稿与下载 URL，再加载新文档。实现入口为 [请求控制](../client/request-scope.ts) 和 [会话边界](../components/shell/session-boundary.tsx)。

模型路由在短数据库事务完成身份、租户与有效空间校验后，再由 `model-agent-client.ts` 调用 Agent HTTP API。请求使用服务凭据与服务端构造的上下文；浏览器不接触连接凭据。`contracts/model-contract.ts` 统一校验生成的 JSON 与 SSE DTO，流取消会中止上游请求。平台管理与诊断、租户模块只读、Chat 双权限分别校验；模型配置与运行记录由 Agent 持久化。
