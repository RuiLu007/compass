# Compass PromptOps Platform：总体架构、数据模型与 API 设计

针对 Compass，我建议把 PromptOps 建设为一个**独立的、面向企业 Agent 的 Prompt 配置管理与发布平台**，而不是单纯的 Prompt 编辑器。

它需要同时服务两类使用者：

- **开发者与运营管理人员**：编辑、调试、评测、审核、发布和回滚 Prompt。
- **Agent 应用与 Context Runtime**：拉取版本、同步本地、解析发布配置、固定 Run 版本。

我建议采用：

> **PromptOps = Prompt Registry + Workspace + Evaluation + Release Management + Runtime Distribution**

但这五项是逻辑能力，不代表五个微服务。本期可以使用一个 Python 后端、PostgreSQL、现有对象存储和一个 Web 管理界面实现。

最重要的架构决策有三个：

1. **PromptOps 负责配置资产的生命周期，Context Runtime 负责运行时上下文生命周期。** 不在 PromptOps 内重复实现 Context Pipeline。
2. **以不可变 Release Bundle 为发布单位，而不是逐个发布互相依赖的 Prompt。** 保证一个 Agent 的多个分支和策略版本一致。
3. **支持远端集中管理与本地开发工作区双模式。** 开发者可以把 Prompt 拉到本地，与 Python 代码一起调优，再将修改提交到平台。

---

# 一、PromptOps 平台的能力边界

## 1. 平台应该管理哪些资产？

建议将 PromptOps 的资产分为四类。

| 资产                  | 说明                         | 示例                            |
| ------------------- | -------------------------- | ----------------------------- |
| **PromptTemplate**  | 静态或动态 Prompt 模板            | `gmv.sql.generator`           |
| **ContextPolicy**   | Context Pipeline 的策略配置     | `gmv.analysis.context_policy` |
| **PromptRelease**   | 一组相互兼容的 Prompt 与 Policy 版本 | `compass-gmv-r12`             |
| **EvaluationSuite** | Prompt/Policy 的测试集与发布门禁    | `gmv-sql-regression`          |

这里要强调：ContextPolicy 由 Context Runtime 执行，但可以由 PromptOps 平台统一管理、版本化和发布。

因为一次 Prompt 调优可能需要同时调整：

- Prompt 内容；
- Prompt 变量；
- Context 收集策略；
- 历史窗口；
- 压缩阈值；
- 输出契约。

如果它们分开发布，可能导致运行时配置不一致。

## 2. 哪些能力不属于 PromptOps？

| 不属于 PromptOps        | 所属系统                        |
| -------------------- | --------------------------- |
| 实时收集 ContextItem     | Context Runtime             |
| 上下文选择、排序、压缩          | Context Runtime             |
| 模型调用和 Token Usage 采集 | LLM Runtime / Agent Runtime |
| Run/Step 状态持久化       | Agent Runtime               |
| 业务指标定义和 Schema 管理    | Semantic Layer              |
| 工具权限和执行              | Tool Dispatcher             |
| 长期 Memory 存储         | Memory Store                |

PromptOps 可以展示这些系统提供的评测和观测数据，但不应该成为它们的执行引擎。

---

# 二、总体架构

## PromptOps Platform

(独立平台)

**Workspace**

编辑 / Diff / 调试

**Registry**

资产 / 不可变版本

**Evaluation**

测试 / 评测 / 回归

**Release**

审核 / 发布 / 回滚

**Distribution API**

精确版本获取 · Manifest · ETag · 增量同步

HTTPS / 鉴权 / 发布版本

**Developer Workspace**

Pull / Checkout / Diff / Push

**Context Runtime**

PromptSync / Loader / RunConfigSnapshot

PostgreSQL · Object Storage · Audit Log

建议平台后端使用模块化单体架构。管理 API、发布 API 和运行时分发 API 在代码中分组，部署初期可以共用同一个服务。

---

# 三、核心领域数据模型

我建议先建立七个核心实体：

1. `PromptProject`
2. `PromptAsset`
3. `PromptVersion`
4. `PromptWorkspace`
5. `ReleaseBundle`
6. `ReleasePointer`
7. `EvaluationRun`

另外增加 `AuditEvent` 作为发布治理的基础记录。

## 1. PromptProject：应用与命名空间

用于隔离不同应用和团队。

```
class PromptProject(BaseModel):    project_id: str    name: str    owner_team: str    environment_names: list[str]    created_at: datetime    updated_at: datetime
```

例如：

```
project: compass-dataagent

assets:
  common.base
  common.tool_rules
  gmv.planner
  gmv.sql
  gmv.analysis
  gmv.reporter
  gmv.context_policy
```

不要把每个业务租户都建成一个 PromptOps Project。

**应用项目、配置作用域和数据租户是三个不同维度。**

一个 Prompt 可以是平台级默认资产，也可以被特定业务部门的配置覆盖。运行时需要根据租户配置解析有效发布版本。

## 2. PromptAsset：逻辑资产身份

PromptAsset 不直接保存可变正文，而是标识一个长期存在的 Prompt。

| 字段            | 类型        | 说明                                            |
| ------------- | --------- | --------------------------------------------- |
| `asset_id`    | UUID      | 资产 ID                                         |
| `project_id`  | UUID      | 所属项目                                          |
| `asset_key`   | TEXT      | 稳定业务名称                                        |
| `asset_type`  | ENUM      | `prompt` / `context_policy` / `output_schema` |
| `description` | TEXT      | 用途                                            |
| `owner_team`  | TEXT      | 负责人                                           |
| `status`      | ENUM      | active / deprecated                           |
| `created_at`  | TIMESTAMP | 创建时间                                          |

例如：

```
{
  "asset_key": "gmv.sql.generator",
  "asset_type": "prompt",
  "description": "GMV 分析 SQL 生成阶段指令"
}
```

## 3. PromptVersion：不可变内容版本

这是最核心的实体。

```
class PromptVersion(BaseModel):    version_id: str    asset_id: str    version_number: int    content: str    role: Literal["system", "developer"]    template_engine: str    variables_schema: dict    dependencies: list["AssetReference"]    content_hash: str    created_by: str    created_at: datetime
```

建议一个版本发布后绝不修改正文。任何修改生成新版本。

### 静态 Prompt 示例

```
{
  "asset_key": "common.tool_rules",
  "content": "只允许使用当前授权的工具。",
  "variables_schema": {}
}
```

### 动态 Prompt 示例

```
{
  "asset_key": "gmv.sql.generator",
  "content": "请分析{{region}}的{{metric_name}}，使用{{metric_definition}}作为权威口径。",
  "variables_schema": {
    "type": "object",
    "properties": {
      "region": {"type": "string"},
      "metric_name": {"type": "string"},
      "metric_definition": {"type": "string"}
    },
    "required": [
      "region",
      "metric_name",
      "metric_definition"
    ]
  }
}
```

变量 Schema 应在保存、评测和运行时渲染三个环节进行校验。

---

# 四、Prompt Workspace：支持本地和平台联合开发

这是针对你之前强调的需求专门设计的能力。

开发者应能够：

```
compass prompt pull --env dev

compass prompt checkout gmv.sql.generator

# 本地编辑 Prompt，并运行 Agent 测试

compass prompt diff

compass prompt push --create-candidate
```

这里建议明确区分：

| 对象                       | 是否可修改 | 作用      |
| ------------------------ | ----- | ------- |
| Remote Published Version | 否     | 生产运行    |
| Remote Draft             | 是     | 平台在线编辑  |
| Local Workspace          | 是     | 与代码一起调试 |
| Local Release Cache      | 否     | 精确版本加载  |

本地 Workspace 应记录：

```
project: compass-dataagent
base_release: compass-r12
base_release_hash: sha256:...

workspace:
  gmv.sql.generator:
    base_version: 7
    local_path: prompts/gmv/sql.md
```

`push` 时平台需要检查基线版本是否变化。

如果开发者基于 v7 调优，但平台已经发布 v8，应提供 Diff 和冲突处理，而不是直接覆盖 v8。

---

# 五、ReleaseBundle：保证多个 Prompt 一致发布

对于企业 Agent，这是比单 Prompt 版本更重要的对象。

一个 GMV Agent 可能使用：

- Planner Prompt；
- SQL Prompt；
- Analysis Prompt；
- Report Prompt；
- Tool Rules；
- ContextPolicy；
- Output Schema。

这些资产可能存在兼容关系。

因此建议以 ReleaseBundle 发布：

```
{
  "release_id": "compass-gmv-r12",
  "project": "compass-dataagent",
  "assets": {
    "common.base": "v4",
    "gmv.planner": "v3",
    "gmv.sql": "v7",
    "gmv.analysis": "v5",
    "gmv.reporter": "v2",
    "gmv.context_policy": "v3"
  },
  "runtime_contract": {
    "min_context_runtime": "1.2",
    "required_strategies": [
      "priority_window@1",
      "tool_result_projection@1"
    ]
  }
}
```

发布时应把所有引用解析为不可变版本，并计算整个 Bundle 的内容哈希。

**运行时不应该在同一个 Run 中分别读取各个 Prompt 的 latest。**

否则一个长任务可能在不同阶段拿到不兼容的配置。

---

# 六、ReleasePointer：解决在线生效

发布指针用于确定：

> 某个环境、某个业务场景、某个配置作用域，目前使用哪个 Release？

建议：

```
class ReleasePointer(BaseModel):    project_id: str    environment: str    scope_type: Literal[        "platform", "tenant", "department"    ]    scope_id: str    scenario: str    release_id: str    generation: int    updated_at: datetime
```

例如：

| 环境         | 作用域              | 场景           | Release |
| ---------- | ---------------- | ------------ | ------- |
| production | platform         | gmv_analysis | r12     |
| production | sales_department | gmv_analysis | r13     |
| staging    | platform         | gmv_analysis | r14     |

这里要定义清楚配置继承关系。

例如：

```
平台默认发布
    ↓
部门覆盖
    ↓
租户覆盖
```

但不建议支持任意字段的深层合并。

M1 可以采用**最具体作用域的完整 Release 覆盖**，避免出现无法预测的 Prompt 混合。

---

# 七、Evaluation：测试与发布门禁

PromptOps 平台不需要自己实现完整的 Agent Runtime，但应该提供统一评测接口。

建议至少支持：

| 评测类型      | 示例                   |
| --------- | -------------------- |
| 模板校验      | 所有必需变量能否正确渲染         |
| 依赖校验      | Prompt 引用是否存在、是否循环引用 |
| Schema 校验 | 输出契约是否兼容             |
| SQL 任务评测  | 是否使用正确 GMV 口径        |
| 安全回归      | 是否诱导跨租户数据访问          |
| 成本评测      | Token、费用、延迟变化        |
| 版本对比      | v7 与 v8 的任务成功率差异     |

评测执行可以委托 Compass 的测试环境或独立 Eval Worker。

PromptOps 负责创建评测任务、保存结果、展示 Diff 和执行发布门禁。

不需要在平台里再造一套 AgentExecutor。

---

# 八、对外 API：按使用者划分

我建议分为三组 API：

1. **Management API**：面向管理平台和开发工具。
2. **Runtime Distribution API**：面向 Context Runtime。
3. **Evaluation / Release API**：面向 CI、测试和发布流程。

## 1. Management API

| Method | Endpoint                             | 作用      |
| ------ | ------------------------------------ | ------- |
| GET    | `/v1/projects`                       | 查询项目    |
| POST   | `/v1/projects`                       | 创建项目    |
| GET    | `/v1/projects/{id}/assets`           | 列出资产    |
| POST   | `/v1/projects/{id}/assets`           | 创建资产    |
| GET    | `/v1/assets/{id}/versions`           | 查询版本历史  |
| GET    | `/v1/assets/{id}/versions/{version}` | 获取精确版本  |
| POST   | `/v1/assets/{id}/versions`           | 创建不可变版本 |
| GET    | `/v1/assets/{id}/diff`               | 比较版本    |
| POST   | `/v1/workspaces`                     | 创建远端工作区 |
| PATCH  | `/v1/workspaces/{id}`                | 修改草稿    |
| POST   | `/v1/workspaces/{id}/submit`         | 提交候选版本  |

## 2. Runtime Distribution API

这是 Context Runtime 最核心的对接接口。

| Method | Endpoint                           | 作用                     |
| ------ | ---------------------------------- | ---------------------- |
| GET    | `/v1/runtime/resolve`              | 解析当前环境、作用域和场景的发布指针     |
| GET    | `/v1/runtime/releases/{id}`        | 获取不可变 Release Manifest |
| GET    | `/v1/runtime/releases/{id}/bundle` | 下载完整发布包                |
| GET    | `/v1/runtime/changes`              | 增量查询发布变化               |
| GET    | `/v1/runtime/health`               | 分发服务健康检查               |

运行时建议优先使用两个 API：

```
GET /v1/runtime/resolve?project=compass-dataagent&environment=production&scope_id=sales-east&scenario=gmv_analysis
```

返回：

```
{
  "release_id": "compass-gmv-r13",
  "bundle_hash": "sha256:...",
  "generation": 18
}
```

然后：

```
GET /v1/runtime/releases/compass-gmv-r13/bundle
```

返回不可变发布包，供本地缓存。

分发 API 应支持 `ETag`、条件请求、内容哈希和短期缓存。下载失败时，已有 Run 可以继续使用已验证的精确版本缓存；新 Run 是否允许使用缓存指针必须由明确的发布一致性策略决定。

## 3. Evaluation / Release API

| Method | Endpoint                     | 作用          |
| ------ | ---------------------------- | ----------- |
| POST   | `/v1/evaluations`            | 创建评测        |
| GET    | `/v1/evaluations/{id}`       | 查询评测结果      |
| POST   | `/v1/releases/candidates`    | 构建候选 Bundle |
| POST   | `/v1/releases/{id}/validate` | 验证依赖和兼容性    |
| POST   | `/v1/releases/{id}/approve`  | 审批          |
| POST   | `/v1/releases/{id}/publish`  | 发布          |
| POST   | `/v1/releases/{id}/rollback` | 回滚指定发布      |
| GET    | `/v1/releases/{id}/audit`    | 查询发布审计      |

生产发布应采用乐观并发控制，要求提交预期的 `generation`，防止两个发布操作互相覆盖。

---

# 九、PostgreSQL 表结构建议

M1 不需要复杂的事件溯源数据库。核心可以先用以下表：

| 表                   | 主键或唯一约束                                       | 主要字段                                     |
| ------------------- | --------------------------------------------- | ---------------------------------------- |
| `prompt_projects`   | `project_id`                                  | name、owner_team                          |
| `prompt_assets`     | `asset_id`；\`project_id + asset_key\` 唯一      | type、description、status                  |
| `prompt_versions`   | `version_id`；\`asset_id + version_number\` 唯一 | content、variables_schema、hash            |
| `prompt_workspaces` | `workspace_id`                                | project、base_release、owner、status        |
| `workspace_changes` | `workspace_id + asset_id`                     | draft_content、base_version               |
| `release_bundles`   | `release_id`                                  | manifest、bundle_hash、compatibility       |
| `release_pointers`  | `project + env + scope + scenario` 唯一         | release_id、generation                    |
| `evaluation_runs`   | `evaluation_id`                               | candidate_release、dataset、status、results |
| `audit_events`      | `event_id`                                    | actor、action、resource、before、after       |

其中有两个重要约束。

### 1. 不可变版本

`prompt_versions` 和 `release_bundles` 的发布内容必须不可变。

建议通过数据库写入权限、应用服务约束和必要的触发器共同保证，而不只是靠 UI 禁止编辑。

### 2. 发布指针可变

`release_pointers` 是少数允许更新的生产配置表。

每次变更都要记录：

- 原 Release；
- 新 Release；
- 操作人；
- 审批记录；
- 评测结果；
- 时间；
- 发布原因。

这样才能支持可靠回滚和审计。

---

# 十、完整发布与运行时同步时序

Agent RuntimeContext RuntimeRegistryEval WorkerPromptOpsDeveloperAgent RuntimeContext RuntimeRegistryEval WorkerPromptOpsDeveloper#chatgpt-mermaid-\_r_doa\_{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;fill:rgb(13, 13, 13);}@keyframes edge-animation-frame{from{stroke-dashoffset:0;}}@keyframes dash{to{stroke-dashoffset:0;}}#chatgpt-mermaid-\_r_doa\_ .edge-animation-slow{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 50s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_doa\_ .edge-animation-fast{stroke-dasharray:9,5!important;stroke-dashoffset:900;animation:dash 20s linear infinite;stroke-linecap:round;}#chatgpt-mermaid-\_r_doa\_ .error-icon{fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_doa\_ .error-text{fill:rgb(13, 13, 13);stroke:rgb(13, 13, 13);}#chatgpt-mermaid-\_r_doa\_ .edge-thickness-normal{stroke-width:1px;}#chatgpt-mermaid-\_r_doa\_ .edge-thickness-thick{stroke-width:3.5px;}#chatgpt-mermaid-\_r_doa\_ .edge-pattern-solid{stroke-dasharray:0;}#chatgpt-mermaid-\_r_doa\_ .edge-thickness-invisible{stroke-width:0;fill:none;}#chatgpt-mermaid-\_r_doa\_ .edge-pattern-dashed{stroke-dasharray:3;}#chatgpt-mermaid-\_r_doa\_ .edge-pattern-dotted{stroke-dasharray:2;}#chatgpt-mermaid-\_r_doa\_ .marker{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ .marker.cross{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ svg{font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";font-size:16px;}#chatgpt-mermaid-\_r_doa\_ p{margin:0;}#chatgpt-mermaid-\_r_doa\_ .actor{stroke:rgb(83, 154, 248);fill:rgb(222, 234, 251);stroke-width:1;}#chatgpt-mermaid-\_r_doa\_ rect.actor.outer-path[data-look="neo"]{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_doa\_ rect.note[data-look="neo"]{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_doa\_ text.actor>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_doa\_ .actor-line{stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ .innerArc{stroke-width:1.5;stroke-dasharray:none;}#chatgpt-mermaid-\_r_doa\_ .messageLine0{stroke-width:1.5;stroke-dasharray:none;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ .messageLine1{stroke-width:1.5;stroke-dasharray:2,2;stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ [id$="-arrowhead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ .sequenceNumber{fill:#707070;}#chatgpt-mermaid-\_r_doa\_ [id$="-sequencenumber"]{fill:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ [id$="-crosshead"] path{fill:rgb(143, 143, 143);stroke:rgb(143, 143, 143);}#chatgpt-mermaid-\_r_doa\_ .messageText{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_doa\_ .labelBox{stroke:rgba(0, 0, 0, 0.1);fill:rgb(252, 252, 252);filter:none;}#chatgpt-mermaid-\_r_doa\_ .labelText,#chatgpt-mermaid-\_r_doa\_ .labelText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_doa\_ .loopText,#chatgpt-mermaid-\_r_doa\_ .loopText>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_doa\_ .sectionTitle,#chatgpt-mermaid-\_r_doa\_ .sectionTitle>tspan{fill:rgb(13, 13, 13);stroke:none;}#chatgpt-mermaid-\_r_doa\_ .loopLine{stroke-width:2px;stroke-dasharray:2,2;stroke:rgba(0, 0, 0, 0.1);fill:rgba(0, 0, 0, 0.1);}#chatgpt-mermaid-\_r_doa\_ .note{stroke:rgb(107, 198, 127);fill:rgb(243, 243, 243);}#chatgpt-mermaid-\_r_doa\_ .noteText,#chatgpt-mermaid-\_r_doa\_ .noteText>tspan{fill:rgb(13, 13, 13);stroke:none;font-weight:normal;}#chatgpt-mermaid-\_r_doa\_ .activation0{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_doa\_ .activation1{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_doa\_ .activation2{fill:rgb(243, 243, 243);stroke:hsl(0, 0%, 85.2941176471%);}#chatgpt-mermaid-\_r_doa\_ .actorPopupMenu{position:absolute;}#chatgpt-mermaid-\_r_doa\_ .actorPopupMenuPanel{position:absolute;fill:rgb(222, 234, 251);box-shadow:0px 8px 16px 0px rgba(0,0,0,0.2);filter:drop-shadow(3px 5px 2px rgb(0 0 0 / 0.4));}#chatgpt-mermaid-\_r_doa\_ .actor-man circle,#chatgpt-mermaid-\_r_doa\_ line{fill:rgb(222, 234, 251);stroke-width:2px;}#chatgpt-mermaid-\_r_doa\_ g rect.rect{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_doa\_ .node .neo-node{stroke:rgb(83, 154, 248);}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].node rect,#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].cluster rect,#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].node polygon{stroke:url(#chatgpt-mermaid-\_r_doa\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].swimlane.cluster rect{filter:none;}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].node path{stroke:url(#chatgpt-mermaid-\_r_doa\_-gradient);stroke-width:1px;}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].node .outer-path{filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].node .neo-line path{stroke:rgb(83, 154, 248);filter:none;}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].node circle{stroke:url(#chatgpt-mermaid-\_r_doa\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].node circle .state-start{fill:#000000;}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].icon-shape .icon{fill:url(#chatgpt-mermaid-\_r_doa\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_doa\_ [data-look="neo"].icon-shape .icon-neo path{stroke:url(#chatgpt-mermaid-\_r_doa\_-gradient);filter:drop-shadow( 1px 2px 2px rgba(185,185,185,1));}#chatgpt-mermaid-\_r_doa\_ :root{--mermaid-font-family:-apple-system-body,ui-sans-serif,-apple-system,system-ui,"Segoe UI",Helvetica,"Apple Color Emoji",Arial,sans-serif,"Segoe UI Emoji","Segoe UI Symbol";}提交 Prompt / Policy 修改创建不可变版本构建候选 Release执行回归评测质量 / Token / 安全结果审核与发布门禁原子切换 ReleasePointer查询发布变化新 Release ID + Hash下载精确 Bundle不可变配置包校验并更新本地镜像新 Run 请求固定 Release + ContextPolicy保存 RunConfigSnapshot

需要注意：本地同步完成与发布指针切换并不要求同一时刻发生。

Worker 可以提前下载，也可以在新 Run 创建时按需下载。真正需要保证的是：Run 固定的 Release ID 能被精确解析、校验和加载。

---

# 十一、权限模型

PromptOps 的权限不等同于业务数据权限。

建议至少区分：

| 角色            | 权限                  |
| ------------- | ------------------- |
| Viewer        | 查看允许范围内的 Prompt 和版本 |
| Editor        | 编辑草稿、提交候选           |
| Evaluator     | 发起评测、查看结果           |
| Approver      | 审批候选发布              |
| Publisher     | 执行发布和回滚             |
| RuntimeClient | 只读指定项目、环境和作用域的发布配置  |

生产 RuntimeClient 不应具有创建版本、修改草稿或发布权限。

平台还需要防止通过 Prompt 模板、变量示例、测试数据和评测日志泄露租户业务信息。

特别是本地开发工作区，不能把真实生产凭据或敏感业务数据打包进 Prompt 资产。

---

# 十二、分阶段落地

**M1：Registry + Distribution**

Prompt 资产、不可变版本、ReleaseBundle、发布指针、运行时精确下载、本地镜像和 CLI Pull/Checkout/Diff。

---

**M2：Workspace + Evaluation + Release**

Web 编辑、版本 Diff、候选发布、回归评测、审批、发布、回滚和审计。

---

**M3：实验与持续优化**

多版本 A/B Test、租户灰度、效果趋势、Prompt 与 ContextPolicy 联合优化、自动化回归和异常回滚。

# 十三、最终推荐

我建议 Compass PromptOps 的第一版**不要建设完整的 LLMOps 平台**，而是把最关键的资产和发布机制做扎实：

**核心数据对象：**

`PromptAsset → PromptVersion → ReleaseBundle → ReleasePointer`

**开发协作：**

`Local Workspace ↔ Remote Workspace → Evaluation → Release`

**运行时消费：**

`Resolve → Download → Verify → Local Cache → RunConfigSnapshot`

**关键设计原则：**

- Prompt 与 ContextPolicy 统一版本化，但执行职责分离。
- 静态和动态 Prompt 使用统一模板模型。
- 生产版本不可变，发布指针可变。
- 一个 Release 固定所有依赖版本。
- 运行时通过 API 获取配置，不直接连接 PromptOps 数据库。
- 本地开发可以自由调优，生产只使用已批准版本。
- Prompt 发布无需重新部署应用，但新算法和不兼容契约仍需部署代码。

这样既能满足企业 Agent 多分支 Prompt 的集中管理与热更新，也能和我们前面设计的 Context Runtime、Context Pipeline、Strategy Registry 自然衔接。
