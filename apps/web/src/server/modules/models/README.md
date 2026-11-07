# 模型访问授权

本模块复用 identity/access 的公开端口，在 Web 的短事务内校验平台资格或租户、有效工作区与模型使用权限。`public.ts` 是模块公开入口。

模型配置、凭据与调用元记录由 Agent 持久化；HTTP 调用在授权事务提交后进行，入口位于 `server/http/model-handler.ts` 与 `server/model-agent-client.ts`。禁止在本模块事务中执行模型网络请求。
