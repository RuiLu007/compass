"""Follow approved field and aggregate projections back through read-only SQL sources."""

import re
from collections import defaultdict

from pglast import ast, parse_sql
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Visitor


def display_columns(query, available, dictionary):
    """Label simple source projections using column comments opened in this Run."""
    if not query or not dictionary:
        return {}
    relations = defaultdict(set)

    class Relations(Visitor):
        def visit_RangeVar(self, ancestors, node):
            name = node.alias.aliasname if node.alias else node.relname
            relations[name].add(f"{node.schemaname}.{node.relname}" if node.schemaname else None)

        def visit_RangeSubselect(self, ancestors, node):
            if node.alias:
                relations[node.alias.aliasname].add(None)

    try:
        Relations()(parse_sql(query))
    except ParseError:
        return {}
    labels = {}
    prefixes = {"min": "最小值", "max": "最大值", "sum": "合计", "avg": "平均值", "count": "计数"}
    for key, expression in available.items():
        wrapped = re.fullmatch(r"(sum|min|max|avg|count)\((DISTINCT )?([\w]+\.[\w]+)\)", expression)
        field = wrapped[3] if wrapped else expression
        match = re.fullmatch(r"(\w+)\.(\w+)", field)
        if not match or len(tables := relations.get(match[1], set())) != 1 or None in tables:
            continue
        entry = dictionary.get(f"{next(iter(tables))}.{match[2]}")
        if not entry:
            continue
        description = re.split(r"[；;。\n]", entry["description"], maxsplit=1)[0].strip()
        if not 1 <= len(description) <= 64:
            continue
        if wrapped:
            prefix = ("去重" if wrapped[2] else "") + prefixes[wrapped[1]]
            description = f"{prefix}（{description}）"
        labels[key] = (description, entry["source"])
    return labels


def _physical_field(label):
    unit = ""
    if label.endswith(("_fen", "_cent")):
        unit = "CNY_fen"
    elif label.endswith(("_qty", "_quantity", "_units")):
        unit = "piece"
    elif label.endswith("_orders"):
        unit = "order"
    return unit


class _BaseStar(dict):
    """A nested SELECT with one base.* plus explicitly named expressions."""

    def __init__(self, base, explicit):
        super().__init__()
        self.base = base
        self.explicit = explicit

    def __contains__(self, key):
        return key not in self.explicit or super().__contains__(key)

    def get(self, key, default=None):
        if key in self.explicit:
            return super().get(key, default)
        return f"{self.base}.{key}", _physical_field(key)


def source_columns(query, columns):
    try:
        stmts = parse_sql(query)
    except (ValueError, ParseError):
        return {}, {}
    if len(stmts) != 1 or not isinstance(stmts[0].stmt, ast.SelectStmt):
        return {}, {}
    mapped = _select(stmts[0].stmt, {}, columns, depth=0)
    return {key: column for key, (column, _) in mapped.items()}, {
        key: unit for key, (_, unit) in mapped.items() if unit
    }


def _select(stmt, inherited, labels=None, *, depth):
    if (
        depth > 6
        or not isinstance(stmt, ast.SelectStmt)
        or stmt.larg
        or not stmt.fromClause
        or not stmt.targetList
    ):
        return {}
    ctes = dict(inherited)
    if stmt.withClause:
        if stmt.withClause.recursive:
            return {}
        for cte in stmt.withClause.ctes or ():
            if not isinstance(cte, ast.CommonTableExpr) or not cte.ctename:
                return {}
            labels_for_cte = [s.sval for s in cte.aliascolnames or ()]
            # Inline calendar windows may constrain real records. Literal VALUES
            # contribute no attributable columns of their own.
            ctes[cte.ctename] = _select(cte.ctequery, ctes, labels_for_cte or None, depth=depth + 1)
    sources = {}

    def add(node):
        if isinstance(node, ast.JoinExpr):
            return not node.alias and add(node.larg) and add(node.rarg)
        if (
            not isinstance(node, (ast.RangeSubselect, ast.RangeVar))
            or node.alias
            and node.alias.colnames
        ):
            return False
        if isinstance(node, ast.RangeSubselect):
            if not node.alias:
                return False
            name = node.alias.aliasname
            fields = _select(node.subquery, ctes, depth=depth + 1)
        else:
            name = node.alias.aliasname if node.alias else node.relname
            fields = ctes.get(node.relname) if node.schemaname is None else None
        if name in sources:
            return False
        sources[name] = fields
        return True

    if not all(add(n) for n in stmt.fromClause):
        return {}
    targets = stmt.targetList
    if labels is not None and len(targets) != len(labels):
        return {}
    mapped = {}
    if labels is None and len(sources) == 1:
        base, fields = next(iter(sources.items()))
        stars = [
            tuple(t.val.fields)
            for t in targets
            if isinstance(t.val, ast.ColumnRef)
            and t.val.fields
            and isinstance(t.val.fields[-1], ast.A_Star)
        ]
        if (
            fields is None
            and len(stars) == 1
            and (len(stars[0]) == 1 or len(stars[0]) == 2 and stars[0][0].sval == base)
            and all(t.name or isinstance(t.val, ast.ColumnRef) for t in targets)
        ):
            mapped = _BaseStar(
                base, {t.name or getattr(t.val.fields[-1], "sval", None) for t in targets}
            )
    for i, target in enumerate(targets):
        if not isinstance(target, ast.ResTarget):
            continue
        expr = target.val
        label = labels[i] if labels is not None else target.name
        if not label and isinstance(expr, ast.ColumnRef) and expr.fields:
            label = getattr(expr.fields[-1], "sval", None)
        if not label or labels and labels.count(label) != 1:
            continue
        if checked := _expression(expr, sources, depth=0):
            mapped[label] = checked
    return mapped


def _constant(node):
    if isinstance(node, ast.A_Const):
        return getattr(node.val, "sval", getattr(node.val, "fval", getattr(node.val, "ival", None)))
    return None


def _group_category(node, sources, depth):
    """Only expose named partitions whose branch tests depend on actual table fields."""
    if node.arg or not node.args or len(node.args) > 12:
        return None
    labels = [_constant(node.defresult)]
    for branch in node.args:
        pred = branch.expr
        if isinstance(pred, ast.NullTest):
            field = pred.arg
        elif isinstance(pred, ast.A_Expr):
            names = pred.name or ()
            if len(names) != 1 or getattr(names[0], "sval", None) not in {
                "<",
                "<=",
                "=",
                ">=",
                ">",
                "<>",
            }:
                return None
            rhs = pred.rexpr.arg if isinstance(pred.rexpr, ast.TypeCast) else pred.rexpr
            if _constant(rhs) is None:
                return None
            field = pred.lexpr
        else:
            return None
        if not _expression(field, sources, depth=depth + 1):
            return None
        labels.append(_constant(branch.result))
    if not all(isinstance(s, str) and 0 < len(s) <= 64 for s in labels) or len(set(labels)) != len(
        labels
    ):
        return None
    return RawStream()(node), ""


def _expression(node, sources, *, depth):
    if depth > 10:
        return None
    if isinstance(node, ast.CaseExpr):
        return _group_category(node, sources, depth)
    if isinstance(node, ast.ColumnRef):
        parts = [s.sval for s in node.fields or () if isinstance(s, ast.String)]
        if len(parts) not in (1, 2):
            return None
        label = parts[-1]
        if not isinstance(label, str):
            return None
        if len(parts) == 2:
            if parts[0] not in sources:
                return None
            choices = [sources[parts[0]]]
        else:
            choices = [fields for fields in sources.values() if fields is None or label in fields]
        if not choices:
            return None
        # The successful database execution establishes which base relation owns
        # an unqualified non-ambiguous column. Derived columns require explicit lineage.
        if any(fields is not None for fields in choices):
            return choices[0].get(label) if len(choices) == 1 and choices[0] is not None else None
        return RawStream()(node), _physical_field(label)
    if isinstance(node, ast.TypeCast):
        origin = _expression(node.arg, sources, depth=depth + 1)
        if node.typeName is None:
            return None
        names = [part.sval for part in node.typeName.names or () if isinstance(part, ast.String)]
        if not origin or not names or (len(names) > 1 and names[:-1] != ["pg_catalog"]):
            return None
        if names[-1] not in {
            "numeric",
            "int2",
            "int4",
            "int8",
            "date",
            "text",
            "timestamp",
            "timestamptz",
        }:
            return None
        return RawStream()(node), origin[1] if names[-1] in {
            "numeric",
            "int2",
            "int4",
            "int8",
        } else ""
    if isinstance(node, ast.A_Expr):
        names = node.name or ()
        sign = getattr(names[-1], "sval", "") if names else ""
        left = _expression(node.lexpr, sources, depth=depth + 1)
        if (
            sign == "/"
            and left
            and left[1] == "CNY_fen"
            and str(_constant(node.rexpr)) in {"100", "100.0", "100.00"}
        ):
            return left[0] + " / 100", "CNY_yuan"
        right = _expression(node.rexpr, sources, depth=depth + 1)
        if sign in {"+", "-"} and left and right and left[1] and left[1] == right[1]:
            return f"({left[0]} {sign} {right[0]})", left[1]
        return None
    if not isinstance(node, ast.FuncCall) or node.over:
        return None
    names = [x.sval for x in node.funcname or () if isinstance(x, ast.String)]
    if len(names) != 1 and (len(names) != 2 or names[0] != "pg_catalog"):
        return None
    name = names[-1]
    args = list(node.args or ())
    if name == "count" and node.agg_star:
        return (
            (RawStream()(node), "count")
            if any(fields is None or fields for fields in sources.values())
            else None
        )
    if name in {"sum", "min", "max", "avg", "count"} and len(args) == 1:
        origin = _expression(args[0], sources, depth=depth + 1)
        if origin:
            name += "(DISTINCT " if node.agg_distinct else "("
            expression = name + origin[0] + ")"
            if node.agg_filter:
                expression += " FILTER (WHERE " + RawStream()(node.agg_filter) + ")"
            return expression, "count" if names[-1] == "count" else origin[1]
    if (
        name == "round"
        and len(args) in {1, 2}
        and (len(args) == 1 or _constant(args[1]) in {0, 1, 2})
    ):
        return _expression(args[0], sources, depth=depth + 1)
    if name in {"date_trunc", "timezone", "to_char"} and len(args) == 2:
        pos = 0 if name == "to_char" else 1
        origin = _expression(args[pos], sources, depth=depth + 1)
        fmt = _constant(args[1 - pos])
        if (
            origin
            and isinstance(fmt, str)
            and fmt in {"day", "month", "year", "YYYY-MM", "YYYY-MM-DD", "Asia/Shanghai", "UTC"}
        ):
            return RawStream()(node), ""
    return None
