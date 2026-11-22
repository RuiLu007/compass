"""Extract a parameterized definition from one already authorized query."""

import re
from hashlib import sha256

from pglast import ast, parse_sql
from pglast.stream import RawStream
from pglast.visitors import Visitor


def date_literal(node):
    while isinstance(node, ast.TypeCast):
        node = node.arg
    if isinstance(node, ast.A_Const):
        raw = getattr(node.val, "sval", "")
        if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", raw):
            return raw
    return None


class QueryFields(Visitor):
    def __init__(self):
        super().__init__()
        self.aggregates: list[str] = []
        self.time_fields: list[str] = []
        self.parameters: list[str] = []
        self.index = 0
        self.conditions: list[str] = []

    def visit_FuncCall(self, ancestors, node):
        name = node.funcname[-1].sval.lower()
        if (
            name in {"sum", "avg", "count", "min", "max", "stddev", "variance"}
            and name not in self.aggregates
        ):
            self.aggregates.append(name)

    def visit_A_Expr(self, ancestors, node):
        for field, value in ((node.lexpr, node.rexpr), (node.rexpr, node.lexpr)):
            if isinstance(field, ast.ColumnRef):
                bounds = value if isinstance(value, tuple) else (value,)
                dates = [raw[:35] for literal in bounds if (raw := date_literal(literal))]
                if dates:
                    self.parameters.extend(dates)
                    self.time_fields.append(RawStream()(field))
                elif isinstance(value, (ast.A_Const, ast.TypeCast)):
                    self.conditions.append(RawStream()(field))
                    literal = value.arg if isinstance(value, ast.TypeCast) else value
                    if isinstance(literal, ast.A_Const) and isinstance(literal.val, ast.Integer):
                        self.parameters.append(f"{RawStream()(field)} = {literal.val.ival}"[:100])

    def visit_A_Const(self, ancestors, node):
        self.index += 1
        return ast.ParamRef(number=self.index)


def projection_formula(statement):
    if statement.larg is not None and statement.rarg is not None:
        op = statement.op.name.removeprefix("SETOP_") + (" ALL" if statement.all else "")
        return f"({projection_formula(statement.larg)}) {op} ({projection_formula(statement.rarg)})"
    return ", ".join(
        RawStream()(target.val)
        for target in statement.targetList or ()
        if isinstance(target, ast.ResTarget)
    )


def receipt_proposal(data):
    if recommendation := data.get("recommendation"):
        return recommendation
    proposal = data.get("proposal", {})
    fields = proposal.get("fields", {})
    query = (data.get("query") or {}).get("sql")
    if fields.get("formula") == "" and query:
        # Older set queries stored their signature and parameters but an empty projection.
        repaired = definition_from_query(query, data["columns"], data.get("question", ""))
        return {**proposal, "fields": {**fields, "formula": repaired["fields"]["formula"]}}
    return proposal


def definition_from_query(query, columns, question):
    statement = parse_sql(query)[0].stmt
    if not isinstance(statement, ast.SelectStmt):
        raise ValueError("Only a read query can supply a reusable metric")
    # Keep the identity of fixed business conditions while making the date examples reusable.
    canonical = RawStream()(statement)
    signature = sha256(
        re.sub(r"'\d{4}-\d{2}-\d{2}(?:[ T][^']*)?'", "'<date>'", canonical).encode()
    ).hexdigest()
    groups = []
    targets = statement.targetList or ()
    for group in statement.groupClause or ():
        if isinstance(group, ast.A_Const) and isinstance(group.val, ast.Integer):
            position = group.val.ival
            if isinstance(position, int) and 1 <= position <= len(targets):
                target = targets[position - 1]
                if isinstance(target, ast.ResTarget):
                    group = target.val
        groups.append(group)
    visitor = QueryFields()
    visitor(statement)
    labels = [str(col)[:100] for col in columns][:16]
    groups = [RawStream()(group) for group in groups]
    aggregates = visitor.aggregates
    name = (labels[-1] if labels else "")[:120]
    fields = {
        "name": name,
        "summary": ("按" + "、".join(groups) + "统计" + name if groups else name + "汇总")
        if aggregates
        else question[:600],
        "formula": projection_formula(statement)[:1800],
        "grain": "、".join(groups)[:150]
        if groups
        else ("当前筛选范围汇总" if aggregates else "查询明细"),
        "aggregation": "、".join(aggregates),
        "event_time": "、".join(dict.fromkeys(visitor.time_fields))[:150],
        "dimensions": groups[:16],
        "filters": list(dict.fromkeys(visitor.conditions))[:16],
        "unit": "",
    }
    return {
        "query_signature": signature,
        "fields": fields,
        "parameter_examples": list(dict.fromkeys(visitor.parameters))[:8],
        "missing": [k for k in ("event_time", "unit", "aggregation") if not fields[k]]
        + ["domain", "responsible"],
    }
