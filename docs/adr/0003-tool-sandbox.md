# 0003 · OpenSandbox 与 NFS 工作卷

日期 2026-11-11。选型已由课程负责人确定；本地实现已接入，验收结果持续登记在[实施记录](../../mai-specs/986-lesson-18-tool-execution-sandbox/IMPLEMENTATION.md)。关联 [Feature 986 技术方案](../../mai-specs/986-lesson-18-tool-execution-sandbox/design/TECH_DESIGN.md)。

## 决策

课堂采用 **OpenSandbox Docker 后端 + NFS-Ganesha**，通过 Docker Compose 部署。每次 Python Tool Call/Attempt 分配独立 Sandbox 和可写 NFS 工作卷；计算结束主动销毁，文件保留 24 小时并支持受控只读重挂载。云端沿用 OpenSandbox 接口，计算部署到专用 Linux VM 的 Docker + gVisor，存储使用同地域私网 NAS。

模块归属：`runtime` 管 Run 生命周期，`harness` 管推理循环和自主性控制，`workflows` 管业务编排，`tools` 管统一工具执行。`tools/sandbox` 封装 OpenSandbox 和分配记录，`tools/mcp` 负责远端工具；基础设施配置与卷预置脚本归 `deploy/sandbox`，HTTP 路由归 `api`。

Retail 等受信内置工具继续使用进程内绑定。副作用、审批与串并行策略分别声明；需要审批的批次在占槽和创建 Sandbox 前返回等待。

## 卷供应与生命周期

部署管理员预置独立 NFS 子目录及 Docker named volume，初始 16 个工作卷 slot。运行时通过 `pvc.claimName` 挂载已经存在的卷，Agent 无 Docker socket。Compass 在自有主库记录完整 owner、Run、Attempt、slot、Sandbox ID 和期限，控制跨副本配额与独占租用。模型不指定镜像、挂载、卷名或存储路径。[OpenSandbox named volume 接口](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/examples/docker-pvc-volume-mount.md)

计算配额、工作卷保留和已接受产物分别管理。创建响应丢失时根据 allocation_id 核对原资源，结局未知时保留占用。原 Python Sandbox 删除后由可信只读取件器检查文件；Agent 接受不可变字节后才发布 Artifact 引用。M5 继续把已接受字节与 Step 一起提交，NAS 工作文件保留不承担 Checkpoint 恢复。

## 利弊与取舍

| 选择                   | 收益                                                       | 代价与边界                                                            |
| ---------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------- |
| OpenSandbox            | 复用计算生命周期、命令与文件 SDK，支持明确的分配和回收路径 | 需要锁定兼容版本，核对删除完成语义与数据端点网络                      |
| NFS-Ganesha            | 本地 Docker 可演示 NAS，文件与计算生命周期分离             | FSAL、NFS 网络、UID/GID、配额与保留清理需配置；课堂单实例存在故障边界 |
| 预置工作卷池           | 运行期租用简单，Agent 保持普通应用权限                     | 保留文件占用 slot，容量满时拒绝；扩容由部署管理员执行                 |
| 每调用独立 Sandbox     | 并行可写目录分离，回收和文件接收边界明确                   | 每次创建及只读取件产生启动成本，输入通过授权快照传递                  |
| 云端 gVisor + 托管 NAS | 保持工具接口与 OCI 镜像，增强执行隔离并共享文件存储        | 运行时兼容、NAS I/O、节点维护与云资源费用需要实测                     |

自建 Docker 执行服务、Kubernetes/CSI 与托管 Sandbox 的比较见[主方案选型矩阵](../../mai-specs/986-lesson-18-tool-execution-sandbox/design/TECH_DESIGN.md#sandbox-技术选型)。当前采用 OpenSandbox 承接计算控制面；跨节点自动调度需求出现后再接 Kubernetes，卷供应改为 PVC/CSI。

## 验证与复评

本地已运行 OpenSandbox 与 Ganesha 的组合。验收覆盖本地独立分配、真实 NFS 挂载、计算删除后的文件摘要、重挂载授权、过期清理、双租户隔离与创建丢响应。云端另验证 gVisor、网络与磁盘限额、NAS 故障及跨副本配额。

Ganesha backing volume 使用 Docker Linux VM 内支持 FSAL 的文件系统；NFS 地址从 Docker daemon 可达。SDK 的连接关闭、终止请求受理和实际计算清理分别核对。出现硬件隔离要求、预置卷容量瓶颈或需要多节点自动调度时复评相应部分。
