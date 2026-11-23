"""Build existing delivery formats from the accepted answer and registered result handles."""

import csv
import io

from compass_agent.context_runtime.delivery import Delivery, _csv_cell
from compass_agent.schemas import DashboardDocumentV1, DashboardWidget


def build_artifact(candidate, evidence, body, node_key, as_of):
    spec = candidate.artifact
    if spec is None:
        return None
    widgets = []
    for identifier in spec.evidence_ids:
        item = evidence.get(identifier)
        if not item:
            raise ValueError("附件来源须属于当前执行域。")
        if spec.format == "markdown":
            continue  # The accepted body already contains the checked statements and citations.
        if item["kind"] != "query":
            raise ValueError("附件数据须引用当前执行域的查询结果。")
        record = item["record"]
        rows = record["rows"]
        if rows and isinstance(rows[0], dict):
            rows = [[row[col] for col in record["columns"]] for row in rows]
        widgets.append(
            DashboardWidget(
                kind="table",
                title=item["title"],
                factRef=identifier,
                columns=record["columns"],
                rows=rows,
            )
        )
    if spec.format == "markdown":
        value = Delivery(
            node_key,
            "report",
            "report.md",
            "text/markdown",
            ("# " + spec.title + "\n\n" + body + "\n").encode(),
        )
    elif spec.format == "csv":
        if not widgets:
            raise ValueError("CSV 附件需选择查询结果。")
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        for widget in widgets:
            writer.writerow([_csv_cell(widget.title)])
            writer.writerow([_csv_cell(col) for col in widget.columns])
            writer.writerows([_csv_cell(cell) for cell in row] for row in widget.rows)
        value = Delivery(
            node_key, "table", "analysis.csv", "text/csv", stream.getvalue().encode("utf-8-sig")
        )
    else:
        document = DashboardDocumentV1(
            schemaVersion=1, title=spec.title, scope="当前授权查询范围", asOf=as_of, widgets=widgets
        )
        value = Delivery(
            node_key,
            "dashboard",
            "dashboard.json",
            "application/json",
            document.model_dump_json(by_alias=True).encode(),
        )
    if len(value.data) > 512 * 1024:
        raise ValueError("附件超过 512 KiB，请减少选取的数据。")
    return value
