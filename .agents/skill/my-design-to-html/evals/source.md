# 报表导出技术方案（评测输入）

## 需求依据

这是一个仅供 Skill 评测使用的合成方案。用户需要将已筛选的经营数据导出为 CSV，避免一次读取全部记录。当前方案尚未实现。

## 目标与设计

ExportService 接收已授权的查询条件和列选择，校验列名后调用只读 Repository，以每批最多 500 行的方式读取。CsvWriter 每批写出并清空缓冲区。只交付 CSV 格式；允许的列为 date、region、revenue。吞吐量与端到端耗时尚未设定目标，不要求新增任务队列。

查询权限由既有 API 校验，ExportService 只使用校验后的过滤条件。非法列在开始读取前返回错误。Controller 返回文件流，Repository 提供批次迭代器，CsvWriter 负责表头与行编码。核心行为是逐批读取、写出、清空，保持固定批次大小。

变更：新增 export/service.py 组织导出，调整 report/api.py 增加 CSV 下载入口；其他业务文件保持现状。

## 代码设计

下面是核心设计示意：

```python
import csv
import io
from collections.abc import AsyncIterator

ALLOWED_COLUMNS = {"date", "region", "revenue"}
BATCH_SIZE = 500


async def export_report(repo, filters, columns) -> AsyncIterator[bytes]:
    if not columns:
        raise ValueError("Select at least one column")
    if len(set(columns)) != len(columns):
        raise ValueError("Duplicate columns")
    if set(columns) - ALLOWED_COLUMNS:
        raise ValueError("Unsupported columns")

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    yield buffer.getvalue().encode("utf-8")
    buffer.seek(0)
    buffer.truncate(0)

    async for rows in repo.iter_batches(filters, size=BATCH_SIZE):
        for row in rows:
            writer.writerow({column: row[column] for column in columns})
        yield buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)
```

## 验收标准

评测输入中的工作流章节：检查非法列、空结果与多批次结果。此段应从默认阅读版省略。

## 部署与兼容

沿用既有应用启动流程。本节用于验证内容裁剪，默认不进入阅读版。

## 评审结论

尚未评审。生成阅读版不改变此状态。
