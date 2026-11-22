# 第 23 课 · 代码设计与实现入口

[主方案](TECH_DESIGN.md) · [向量专题](VECTOR_SEARCH.md) · [结论与引用](EVIDENCE.md)

以下片段来自当前工作区。按模型调用、来源约束、交付核验三个时机阅读。投放复议和两周 GMV 比较使用同一组通用工具，各自在工作区绑定中选择数据、文档版本和交付格式。

## 1. Run 接受时固定资料版本

Agent 接收 Web 签发的来源快照。保存前比对租户、工作区、使用者、Run 身份和时点。复议均使用固定发布；GMV 资料包另带向量配置与比较简报格式。

实现入口为 [client.py](../../../apps/agent/src/compass_agent/documents/client.py)。

```python client.py · 现有实现
class SearchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenantId: UUID
    workspaceId: UUID
    runId: UUID
    actorUserId: UUID
    knownAt: AwareDatetime
    timezone: str
    collectionDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    connectionId: UUID
    connectionRevision: int = Field(ge=1)
    dataRelease: str = Field(min_length=1, max_length=240)
    kinds: list[Kind] = Field(max_length=5)
    skillEnabled: bool
    reportFormat: Literal["comparison_brief"] | None = None
    vectorIndex: Literal["text-embedding-3-small:1024:v1"] | None = None
    scenePack: Literal["retail"] | None = None
    maxToolCalls: int | None = Field(default=None, ge=0, le=400)
```

## 2. 候选排序之前圈定可读版本

Web 的 readable 检查固定快照与当前访问范围、审批、发布日期和适用业务日。向量查询的 SQL 也只接收这些文档的授权标识，词频统计的总体同样来自此集合。

实现入口为 [search.ts](../../../apps/web/src/server/modules/documents/search.ts)。

```typescript search.ts · 现有实现
export function visibleDocuments(c: Corpus, args: SearchArgs) {
  const date = args.businessDate ?? businessDay(c.pin);
  return c.collection.documents.filter(
    (d) => readable(c, d, date) && (!args.kind || args.kind === d.kind),
  );
}
```

## 3. 在 Markdown 自然段边界切片

一个章节可以包含多段。目标长度 600 字符，超长的整段或表格独立成片，ID 由章节号和片号组成。发布时另算 sectionHash；正文有变化时生成新的资料摘要。

实现入口为 [publish.py](../../../examples/showcases/lesson-23-east-gmv-rag/publish.py)。

```python publish.py · 现有实现
def chunks(body):
    blocks = re.split(r"^## (.+)$", body, flags=re.M)
    for index, (heading, text) in enumerate(zip(blocks[1::2], blocks[2::2], strict=True), 1):
        part = ""
        number = 1
        for paragraph in re.split(r"\n\s*\n", text.strip()):
            if part and len(part) + len(paragraph) + 2 > 600:
                yield f"s{index}-{number}", heading, part
                number += 1
                part = ""
            part = (part + "\n\n" + paragraph).strip()
        if part:
            yield f"s{index}-{number}", heading, part
```

## 4. 写入和检索使用同一向量空间

发布器每批最多送入 32 段；查询阶段发送当前检索文本。Agent 从现有加密 OpenAI Provider 连接取得凭据，在服务端完成调用并检查返回模型、顺序、数量、维度和有限数值。

实现入口为 [embeddings.py](../../../apps/agent/src/compass_agent/documents/embeddings.py)。

```python embeddings.py · 现有实现
MODEL = "text-embedding-3-small"
DIMENSIONS = 1024
INDEX = f"{MODEL}:{DIMENSIONS}:v1"
```

## 5. 合并两路召回

BM25 取可见段落中评分为正的前十名。pgvector 返回按余弦距离排序的前十名。下面的 i 从零起算，第一个结果的贡献为 1/61。精确文号优先，同一条款有多个适用版本时保留对应版本，最多三段返回给模型。

实现入口为 [search.ts](../../../apps/web/src/server/modules/documents/search.ts)。

```typescript search.ts · 现有实现
const fused = new Map<string, number>();
for (const list of [ranked.slice(0, 10).map(key), semantic]) {
  list.forEach((k, i) => fused.set(k, (fused.get(k) ?? 0) + 1 / (61 + i)));
}
```

## 6. 正式取值后的独立明细

普通正式指标题使用 query_metric。需要另查货池流水或采购单时，Run 快照必须开放 detail，而且已接受的调查目标要包含 detail。SQL 回执和正式指标保留各自的来源类型。

实现入口为 [search_control.py](../../../apps/agent/src/compass_agent/context_runtime/search_control.py)。

```python search_control.py · 现有实现
def independent_detail(self):
    return "detail" in self.pin["kinds"] and any(
        t.source == "detail" for t in self.targets.values()
    )
```

## 7. 从查询格计算两期差异

每格包含当轮来源、从零起的行号和列名。leaf 复查回执与列来源，金额先统一单位。经营判断、行动及待查事项另列，引用打开的原文或具体查询行。

实现入口为 [comparison_brief.py](../../../apps/agent/src/compass_agent/context_runtime/comparison_brief.py)。

```python comparison_brief.py · 现有实现
before, after = cells
if (
    before.value is None
    or after.value is None
    or before.status != "supported"
    or after.status != "supported"
    or before.unit != after.unit
):
    raise ValueError(f"{pair.label} 需要两格同单位的有效数值")
delta = after.value - before.value
percent = (
    "基期为零" if not before.value else f"{delta / before.value * Decimal(100):.2f}%"
)
```

## 8. 预算、无进展和可发表范围

恢复后重建已签收批次与工具额度。两个连续批次都没有取得新结果的可行分支触发停搜判断。generate 使用既有来源。在正式签批路径，服务端重开草稿依赖复算整档与剩余额度。在比较简报路径，服务端复算数值表。

实现入口为 [search_control.py](../../../apps/agent/src/compass_agent/context_runtime/search_control.py)。

```python search_control.py · 现有实现
if remaining(budget)["toolCalls"] <= 0:
    self.stop_reason = "budget_exhausted"
elif self.targets and not viable:
    self.stop_reason = "blocked"
elif viable and all(self.stalled.get(k, 0) >= 2 for k in viable):
    self.stop_reason = "blocked" if viable.issubset(failed) else "no_progress"
```

## 9. 签发引用与回看

| 时机               | 现有入口                                                | 检查结果                                               |
| ------------------ | ------------------------------------------------------- | ------------------------------------------------------ |
| 签收一批工具结果   | `context_runtime/search_verification.py::verify_batch`  | 将来源、可用字段、行数或段落定位写入 Run 私有记录      |
| 核验断言或经营简报 | `verify_draft`、`comparison_delivery`                   | 按固定格式重开回执和当前权限；可修复的问题反馈给主模型 |
| 编号及存储         | `search_claims.py::format_checked`、`publish_citations` | 记录 Markdown 正文偏移、摘要、消息修订及来源详情       |
| 回看已发表答案     | `read_citations`、Web 的 `CitedAnswer`                  | 重新核验正文、修订和可读范围，合法角标打开相应详情     |

华东 GMV 案例的工作区上限是 52 次工具调用。完整记录含 10 次混合检索、15 次原文读取和 9 次实际 SQL 查询，另有发现连接、列出表和读取表结构。这里的调用次数是单次运行结果，模型可以为下一个问题选择不同路径。

## 10. 投放复议的正式指标与撤权

履约锁库缺货率由正式查询回执带回分子、分母和覆盖状态。57 次尝试、0 次短缺得到 0%；零尝试或覆盖不齐将保留相应状态。整档采购使用纪要余额、当期有效制度、完整报价及复投资格复算；换价后用同一笔余额重算。学员可在[零售数据与验收](RETAIL_CASE.md)中对照反事实变体。

来源权限在取回回执、写入交付和重新打开历史答案时重新检查。搜索来源集超出运行允许的数量时，受保护的输出保留阻断标记。下面是 source_access.py 中的实际分支。

```python source_access.py · 已去重来源的计数
    counted = sum(not (r.get("kind") == "semantic" and r.get("default_term")) for r in refs)
    if counted > (MAX_SEARCH_REFS if search else 64):
        refs = [{"kind": "source_set_blocked" if search else "transient"}]
    message.source_refs = refs
```

## 11. 工作区资料管理如何接入检索

管理页的 retrieval-drawer.tsx 提交关键词至 knowledge/catalog/search 或 knowledge/bases/search。knowledge-handler.ts 在生成查询向量前后复核权限，modules/documents/retrieval.ts 使用已存向量和 pgvector 余弦距离返回前十个片段。向量生成复用 indexing.ts，Agent 端允许受信的平台管理与工作区知识管理上下文。检索测试呈现余弦分数及分段详情。

平台管理员使用只读接口 `GET /api/web/v1/knowledge/catalog` 浏览各租户的知识库，服务端按当前登录身份复核平台权限。目录携带每个库的租户、工作区及可维护状态。文件列表、原文件、分段通过同一目录读取，服务端校验库与文件的归属关系。创建使用当前工作区，编辑使用目录返回的归属工作区。

Web 页面使用 knowledge/bases 管理当前工作区的知识库。文件导入分两次请求，第一次保存原始字节，第二次取得处理权、解析正文并生成索引。PDF 使用 unpdf 的文本层提取，Markdown 与 TXT 使用 UTF-8 解码。数据库事务结束后才调用 Agent，索引完成时再次检查身份、工作区、文件和处理批次。请求中断后可从文档列表重试。单文件上限 5 MB，文本 PDF 最多 300 页，每份文件最多生成 256 个分段。

| 操作         | 实现位置                                                            | 对检索的影响                                        |
| ------------ | ------------------------------------------------------------------- | --------------------------------------------------- |
| 上传及编辑   | Web 的 http/knowledge-handler.ts 与 modules/documents/indexing.ts   | 已提交的正文和 1024 维向量一并可见                  |
| 生成向量     | Agent 的 models/knowledge-embeddings 路由与 documents/embeddings.py | Web 传入文本，Agent 使用其管理的 OpenAI Provider    |
| 固定可读内容 | Web 的 modules/documents/library.ts 与 store.ts                     | 沿用已有文号、时间与受众，新 Run 按摘要固定可读章节 |
| 回读及停用   | Web 的 http/documents-handler.ts                                    | 当前启用的哈希同时约束词法、向量、引用和相邻段落    |
