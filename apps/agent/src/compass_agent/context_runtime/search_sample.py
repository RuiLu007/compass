"""Recognize scoped, distinct sample counts from signed SQL and database column comments."""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pglast import ast, enums, parse_sql
from pglast.parser import ParseError


class LookupNeeded(Exception):
    """A signed field requires another granted source before it can carry this role."""


def require_terms(evidence, target, literal):
    terms, sources = controlled_terms(evidence, target, literal)
    if not terms:
        raise LookupNeeded(
            f"明细字段 {target} 的枚举 {literal} 缺少已发布的业务词义。"
            f"请用 search_semantics 按 {literal} 检索实体值域，再用 get_metric_definition "
            "读取同时包含该物理字段、枚举值与业务名称的正式定义。"
        )
    return terms, sources


def require_column(dictionary, schema, relation, name):
    field = f"{schema}.{relation}.{name}"
    info = dictionary.get(field)
    if not info:
        raise LookupNeeded(
            f"请用 database_query 的 describe_table 读取 {schema}.{relation} 中 "
            f"{name} 的授权列说明。若已读前一页，请按 offset 继续。"
        )
    return info


def _column(node):
    if isinstance(node, ast.ColumnRef):
        parts = [part.sval for part in node.fields or () if isinstance(part, ast.String)]
        if len(parts) == 2:
            return tuple(parts)
    return None


def _literal(node):
    if isinstance(node, ast.TypeCast):
        node = node.arg
    if isinstance(node, ast.A_Const):
        for attr in ("sval", "ival", "fval"):
            if (v := getattr(node.val, attr, None)) is not None:
                return str(v)
    return None


def _join(node, relations, conditions):
    if isinstance(node, ast.JoinExpr):
        if node.quals:
            conditions.append(node.quals)
        return _join(node.larg, relations, conditions) and _join(node.rarg, relations, conditions)
    if isinstance(node, ast.RangeVar) and not (node.alias and node.alias.colnames):
        alias = node.alias.aliasname if node.alias else node.relname
        if alias not in relations:
            relations[alias] = (node.schemaname, node.relname)
            return True
    return False


def _conjuncts(node):
    if isinstance(node, ast.BoolExpr) and node.boolop == enums.BoolExprType.AND_EXPR:
        for arg in node.args or ():
            yield from _conjuncts(arg)
    else:
        yield node


def _window_values(stmt, cells):
    lookup = {}
    if not stmt.withClause:
        return lookup
    for cte in stmt.withClause.ctes or ():
        if not cte.ctequery.valuesLists or not cte.aliascolnames:
            return None
        names = [x.sval for x in cte.aliascolnames]
        keys = [
            (col[1], cells[label])
            for target, label in zip(stmt.targetList, cells, strict=True)
            if (col := _column(target.val)) and col[0] == cte.ctename and label in cells
        ]
        if not keys:
            return None
        values = [
            dict(zip(names, map(_literal, row), strict=True)) for row in cte.ctequery.valuesLists
        ]
        matching = [row for row in values if all(row.get(k) == str(v) for k, v in keys)]
        if len(matching) != 1:
            return None
        lookup.update({(cte.ctename, k): v for k, v in matching[0].items()})
    return lookup


def _date_prefix(text):
    if text is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text).isoformat()
    if re.match(r"\d{4}-\d{2}-\d{2}[ T]", text):
        when = datetime.fromisoformat(text)
        if when.hour == when.minute == when.second == 0:
            return when.date().isoformat()
    return None


def sample_proof(evidence, cells, column):
    if not evidence.get("sql"):
        return None
    try:
        return _sample_proof(evidence, cells, column)
    except (ValueError, ParseError, IndexError, KeyError, TypeError):
        return None


def controlled_terms(evidence, target, literal):
    terms, sources = [], []
    for definition in evidence.get("dictionaries", ()):
        record = definition["record"]
        if record.get("kind") != "entity" or not re.search(
            rf"(?<![\w.]){re.escape(target)}(?![A-Za-z0-9_.])", record["text"]
        ):
            continue
        for match in re.finditer(
            r"(?:^|[。；;\n])\s*([^=。；;\n]{1,80}?)\s*=\s*([A-Za-z_][\w.-]{0,63})(?=[。；;\n]|$)",
            record["text"],
        ):
            if match[2] == literal:
                term = match[1].strip()
                terms.append(term)
                source = {
                    k: definition[k] for k in ("kind", "title", "version", "scope", "locator")
                }
                sources.append({**source, "excerpt": f"{target} · {term} = {literal}"})
    return terms, sources


def dated_event(evidence, cells, column):
    raw = cells[column]
    if not isinstance(raw, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        return None
    try:
        occurred = datetime.fromisoformat(raw)
        if occurred.tzinfo:
            occurred = occurred.astimezone(ZoneInfo(evidence["timezone"]))
        stmt = parse_sql(evidence["sql"])[0].stmt
        if (
            not isinstance(stmt, ast.SelectStmt)
            or not stmt.targetList
            or stmt.withClause
            or stmt.larg
            or stmt.rarg
        ):
            return None
        selected = _column(stmt.targetList[list(cells).index(column)].val)
        relations, conditions = {}, [stmt.whereClause]
        if not selected or not all(_join(n, relations, conditions) for n in stmt.fromClause or ()):
            return None
        table = relations[selected[0]]
        dictionary = evidence.get("columnDictionary", {})
        time_field = require_column(dictionary, table[0], table[1], selected[1])
        if not re.search("时间|时刻|日期|发生日", time_field["description"]):
            return None
        roles, sources, filters = [], [time_field["source"]], {}
        for pred in (p for term in conditions if term for p in _conjuncts(term)):
            if not isinstance(pred, ast.A_Expr):
                return None
            if not pred.name or pred.name[-1].sval != "=":
                continue
            ref, literal = _column(pred.lexpr), _literal(pred.rexpr)
            if not ref or not literal:
                continue
            if literal.isdecimal():
                filters[ref[1]] = literal
                continue
            schema, name = relations[ref[0]]
            field = f"{schema}.{name}.{ref[1]}"
            info = require_column(dictionary, schema, name, ref[1])
            if literal not in info["description"].split():
                return None
            names, vocabulary = require_terms(evidence, field, literal)
            roles.append((tuple(names), relations[ref[0]] == table))
            sources.extend([info["source"], *vocabulary])
        if not roles or not any(same_table for _, same_table in roles):
            return None
        return {
            "day": occurred.date().isoformat(),
            "roles": roles,
            "filters": filters,
            "sources": sources,
        }
    except (ValueError, TypeError, ParseError, KeyError, IndexError):
        return None


def _sample_proof(evidence, cells, column):
    statements = parse_sql(evidence["sql"])
    if len(statements) != 1:
        return None
    stmt = statements[0].stmt
    if (
        not isinstance(stmt, ast.SelectStmt)
        or not stmt.targetList
        or stmt.havingClause
        or stmt.larg
        or stmt.rarg
    ):
        return None
    aggregate = stmt.targetList[list(cells).index(column)].val
    if not (
        isinstance(aggregate, ast.FuncCall)
        and aggregate.funcname
        and aggregate.funcname[-1].sval == "count"
        and aggregate.agg_distinct
        and aggregate.args
        and len(aggregate.args) == 1
        and (entity := _column(aggregate.args[0]))
    ):
        return None
    conditions = [p for p in (stmt.whereClause, aggregate.agg_filter) if p]
    relations = {}
    if not all(_join(n, relations, conditions) for n in stmt.fromClause or ()):
        return None
    values = _window_values(stmt, cells)
    if values is None:
        return None
    dictionary = evidence.get("columnDictionary", {})

    def entry(ref):
        if not ref or ref[0] not in relations:
            return None
        schema, table = relations[ref[0]]
        return dictionary.get(f"{schema}.{table}.{ref[1]}")

    fields = []
    schema, relation = relations[entity[0]]
    selected = require_column(dictionary, schema, relation, entity[1])
    markers = set()
    joins = []
    constraints = {}
    ranges = {}
    population = []
    for pred in (p for term in conditions for p in _conjuncts(term)):
        if isinstance(pred, ast.NullTest):
            f = _column(pred.arg)
            if not f or pred.nulltesttype != enums.NullTestType.IS_NOT_NULL:
                return None
            markers.add(f)
            continue
        if not isinstance(pred, ast.A_Expr) or not pred.name:
            return None
        op = pred.name[-1].sval
        left, right = _column(pred.lexpr), _column(pred.rexpr)
        if not left:
            return None
        literal = values.get(right) if right in values else _literal(pred.rexpr)
        if literal is None:
            if not right:
                return None
            if op == "=":
                joins.append((left, right))
            continue
        if op in (">=", "<") and (day := _date_prefix(literal)):
            ranges.setdefault(left, {})[op] = day
        elif op == "=" and re.fullmatch(r"-?\d+", literal):
            constraints.setdefault(left[1], set()).add(literal)
        elif op == "=":
            schema, relation = relations[left[0]]
            target = f"{schema}.{relation}.{left[1]}"
            info = require_column(dictionary, schema, relation, left[1])
            if literal not in info["description"].split():
                return None
            terms, vocabulary = require_terms(evidence, target, literal)
            fields.extend({"source": item} for item in vocabulary)
            population.append(terms)
            fields.append(info)
        elif op == "<=" and re.match(r"\d{4}-\d{2}-\d{2}", literal):
            if datetime.fromisoformat(literal) != datetime.fromisoformat(evidence["knownAt"]):
                return None
        else:
            return None
    # A marker may be described on the foreign key joined to the nullable dimension.
    equivalent = set(markers)
    for a, b in joins:
        if a in markers:
            equivalent.add(b)
        if b in markers:
            equivalent.add(a)
    marker_fields = [x for ref in equivalent if (x := entry(ref))]
    if markers and not marker_fields:
        ref = sorted(markers)[0]
        schema, relation = relations[ref[0]]
        require_column(dictionary, schema, relation, ref[1])
    if not marker_fields or not population or len(ranges) != 1:
        return None
    bounds = next(iter(ranges.values()))
    if set(bounds) != {">=", "<"}:
        return None
    tables = {f"{s}.{t}." for s, t in relations.values() if s}
    return {
        "window": (bounds[">="], bounds["<"]),
        "constraints": constraints,
        "columns": {k.rsplit(".", 1)[1] for k in dictionary if k.rsplit(".", 1)[0] + "." in tables},
        "entity": selected["description"],
        "markers": [info["description"] for info in marker_fields],
        "population": population,
        "sources": tuple(x["source"] for x in [selected, *marker_fields, *fields]),
    }


def matches_sample(fact, clause, windows, dimensions):
    if fact.receipt_scope:
        title = next((s["title"] for s in fact.sources if s["kind"] == "metric"), "")
        return (
            bool(title and title in clause and fact.field == "value" and fact.window in windows)
            and dict(fact.receipt_scope[0]).items() <= dimensions.items()
        )
    proof = fact.sample
    if not proof or fact.truncated or proof["window"] not in windows:
        return False
    if not all(any(term in clause for term in terms) for terms in proof["population"]):
        return False

    def matches(description):
        return any(
            word[start : start + 2] in clause
            for word in re.findall(r"[\u3400-\u9fff]{2,}", description)
            for start in range(len(word) - 1)
        )

    expected = {k + "_id": str(v) for k, v in dimensions.items() if k + "_id" in proof["columns"]}
    for field, values in proof["constraints"].items():
        matches_for_field = [
            str(value) for name, value in dimensions.items() if field == name + "_id"
        ]
        if len(matches_for_field) != 1 or values != {matches_for_field[0]}:
            return False
    return bool(
        expected
        and all(proof["constraints"].get(k) == {v} for k, v in expected.items())
        and matches(proof["entity"])
        and any(matches(x) for x in proof["markers"])
    )
