"""Execute immutable, reviewed bindings on an independently authorized PostgreSQL session."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from uuid import UUID

import psycopg
from jsonschema import Draft202012Validator, FormatChecker
from psycopg import sql
from psycopg.types.json import Jsonb

from compass_agent.data_connections.legacy import LegacyScopeCheck
from compass_agent.data_connections.transport import read_database
from compass_agent.tools.contracts import ToolRejected


def choose_connection(bindings, plan):
    req = plan["access_requirements"]
    selected = [
        b
        for b in bindings
        if str(b.connection_id) == plan["source_ref"]
        or (b.name == plan["source_ref"])
        or (
            isinstance(b.scope_check, LegacyScopeCheck)
            and plan["source_ref"] == f"retail:{b.scope_check.tenant_id}"
            and req.get("retail_tenant_id") == b.scope_check.tenant_id
        )
    ]
    if len(selected) != 1:
        raise ToolRejected("semantic_source_unavailable")
    return selected[0]


def json_value(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def previous_equal_window(parameters):
    """Return the adjoining half-open business-date window with the same number of days."""
    try:
        start = date.fromisoformat(parameters["start"])
        end = date.fromisoformat(parameters["end"])
    except ValueError:
        raise ToolRejected("invalid_parameters") from None
    duration = end - start
    if duration.days <= 0:
        raise ToolRejected("invalid_parameters")
    if duration.days >= start.toordinal():
        return None
    return {**parameters, "start": (start - duration).isoformat(), "end": start.isoformat()}


def observed_value(row):
    if not row or row["status"] != "OK":
        return None
    try:
        value = Decimal(str(row["value"]))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def exact_sum(values):
    with localcontext() as context:
        context.prec = (
            max(value.adjusted() for value in values)
            - min(int(value.as_tuple().exponent) for value in values)
            + len(str(len(values)))
            + 2
        )
        return sum(values, Decimal(0))


def complete_period(rows, empty_is_zero):
    return bool(rows) and all(
        observed_value(r) is not None or (empty_is_zero and r["status"] == "NO_FACTS") for r in rows
    )


def missing_group_is_zero(info):
    """Only a registered complete additive money binding defines an absent group as zero."""
    props = info["binding"]["result_schema"].get("properties", {})
    return (
        str(info.get("aggregation", "")).startswith("additive")
        and info["metric"]["unit"] == "CNY_fen"
        and props.get("unit", {}).get("const") == info["metric"]["unit"]
        and "NO_FACTS" in props.get("status", {}).get("enum", [])
    )


def period_comparison(current, previous, *, empty_is_zero=False):
    """Preserve both source windows and attach each signed change to one aligned row."""
    complete = empty_is_zero and all(complete_period(p, True) for p in (current, previous))
    sides = [
        {r["group_key"]: r for r in rows if r["status"] != "NO_FACTS"}
        for rows in (current, previous)
    ]
    output = []
    for index, rows in enumerate((current, previous)):
        for row in rows:
            key = row["group_key"]
            values = [
                observed_value(side[key]) if key in side else (Decimal(0) if complete else None)
                for side in sides
            ]
            current_value, previous_value = values if row["status"] != "NO_FACTS" else [None, None]
            change = None
            if (
                (not index or key not in sides[0])
                and row["status"] != "NO_FACTS"
                and current_value is not None
                and previous_value is not None
            ):
                change = exact_sum([current_value, previous_value.copy_negate()])
            output.append(
                {
                    **row,
                    "period_side": "comparison" if index else "current",
                    "current_value": json_value(current_value),
                    "comparison_value": json_value(previous_value),
                    "current_minus_comparison": json_value(change),
                }
            )
    return output


def group_totals(periods, windows, info):
    if not str(info.get("aggregation", "")).startswith("additive"):
        return []
    totals = []
    for rows, window in zip(periods, windows, strict=True):
        if window.get("group_by", "total") == "total" or not complete_period(
            rows, missing_group_is_zero(info)
        ):
            continue
        values = [v for row in rows if (v := observed_value(row)) is not None]
        total = exact_sum(values) if values else Decimal(0)
        totals.append({"start": window["start"], "end": window["end"], "value": json_value(total)})
    return totals


def parameter_feedback(schema, parameters):
    properties = schema.get("properties", {})
    types = {
        key: rule.get("type")
        for key, rule in properties.get("filters", {}).get("properties", {}).items()
    }
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in validator.iter_errors(parameters):
        path = ".".join(map(str, error.absolute_path)) or "parameters"
        feedback = {
            "status": "invalid_parameters",
            "field": path,
            "filter_types": types,
            "group_by_options": properties.get("group_by", {}).get("enum", []),
        }
        if (
            path.startswith("filters.")
            and error.validator == "type"
            and isinstance(error.instance, str)
        ):
            feedback["repair"] = (
                "按已发布的业务实体或筛选值域核对该名称的业务键。"
                "可用 search_semantics(query=原业务名称, kinds=[entity]) 查找值域，"
                "再按绑定类型传入 filters。"
            )
        return feedback
    return None


async def source_scope(cursor, plan):
    req = plan["access_requirements"]
    if req.get("protocol") != "postgres_readonly" or req.get("identity") != "verified_session_user":
        raise ToolRejected("semantic_source_unavailable")
    table_names = req.get("required_relations", [])
    if not table_names or len(table_names) > 64:
        raise ToolRejected("semantic_source_unavailable")
    profiles = req.get("namespace_profiles", {})
    chosen = None
    for name, names in profiles.items():
        if (
            not isinstance(names, list)
            or not names
            or names[0] != "pg_catalog"
            or names[-1] != "pg_temp"
        ):
            continue
        await cursor.execute(
            "SELECT bool_and(to_regnamespace(n) IS NOT NULL AND has_schema_privilege(n,'USAGE') "
            "AND NOT has_schema_privilege(n,'CREATE')) FROM unnest(%s::text[]) n",
            (names[1:-1],),
        )
        if await cursor.fetchone() == (True,):
            chosen = name, names
            break
    if chosen is None:
        raise ToolRejected("semantic_source_unavailable")
    profile, names = chosen
    await cursor.execute(
        sql.SQL("SET LOCAL search_path TO {}").format(sql.SQL(",").join(map(sql.Identifier, names)))
    )
    # Catalog qualification and active, read-only row protection must survive every replay.
    await cursor.execute(
        """SELECT c.relname,n.nspname,pg_catalog.row_security_active(c.oid),
        has_any_column_privilege(c.oid,'SELECT'),
        has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'),
        (SELECT array_agg(pg_get_expr(p.polqual,p.polrelid) ORDER BY p.polname) FROM pg_policy p
          WHERE p.polrelid=c.oid AND p.polcmd IN ('r','*'))
        FROM unnest(%s::text[]) t JOIN pg_class c ON c.oid=to_regclass(quote_ident(t))
        JOIN pg_namespace n ON n.oid=c.relnamespace""",
        (table_names,),
    )
    tables = await cursor.fetchall()
    if len(tables) != len(table_names) or any(
        schema not in names[1:-1] or not rls or not can_read or writable
        for _, schema, rls, can_read, writable, _ in tables
    ):
        raise ToolRejected("semantic_source_unavailable")
    tenant = req.get("retail_tenant_id")
    if tenant is None:
        expected = req.get("policy_expressions")
        if not isinstance(expected, dict) or any(
            expected.get(name) != expr for name, _, _, _, _, expr in tables
        ):
            raise ToolRejected("semantic_source_unavailable")
        return {}, {"tables": table_names, "policies": expected}
    # The retail adapter accepts a single SELECT policy backed by its session-user guard.
    guard_schema = names[1] if profile == "learner" else "sec"
    for _, _, _, _, _, expressions in tables:
        if (
            not expressions
            or len(expressions) != 1
            or expressions[0]
            not in {
                f"{guard_schema}.allowed(tenant_id, NULL::bigint)",
                f"{guard_schema}.allowed(tenant_id, region_id)",
                "allowed(tenant_id, NULL::bigint)",
                "allowed(tenant_id, region_id)",
            }
        ):
            raise ToolRejected("semantic_source_unavailable")
    await cursor.execute("SELECT tenant_id,release_id FROM tenant")
    if await cursor.fetchall() != [(tenant, req["source_release"])]:
        raise ToolRejected("semantic_source_unavailable")
    guard = sql.SQL("{}.allowed").format(sql.Identifier(guard_schema))
    await cursor.execute(sql.SQL("SELECT {}(%s::smallint,-1::bigint)").format(guard), (tenant,))
    all_regions = (await cursor.fetchone()) == (True,)
    if req["scope"] == "all_regions" and not all_regions:
        raise ToolRejected("semantic_source_unavailable")
    regions = []
    if not all_regions:
        await cursor.execute(
            "SELECT DISTINCT region_id FROM order_header WHERE region_id IS NOT NULL "
            "ORDER BY region_id LIMIT 1025"
        )
        regions = [r[0] for r in await cursor.fetchall()]
        if not regions or len(regions) > 1024:
            raise ToolRejected("semantic_source_unavailable")
    attestation = {
        "tenant_id": tenant,
        "data_release": req["source_release"],
        "all_regions": all_regions,
        "region_ids": regions,
    }
    return attestation, attestation


async def calculate(binding, plan, args, control, *, check_only=False):
    if hashlib.sha256(plan["sql_text"].encode()).hexdigest() != plan["target_hash"]:
        raise ToolRejected("semantic_binding_changed")
    if not check_only and not Draft202012Validator(
        plan["parameter_schema"], format_checker=FormatChecker()
    ).is_valid(args):
        raise ToolRejected("invalid_parameters")

    async def read(cursor):
        authority, witness = await source_scope(cursor, plan)
        if check_only:
            async with psycopg.AsyncRawCursor(cursor.connection) as raw:
                await raw.execute("EXPLAIN " + plan["sql_text"], (Jsonb(args), Jsonb(authority)))
            return [], witness
        validator = Draft202012Validator(plan["result_schema"], format_checker=FormatChecker())
        async with psycopg.AsyncRawCursor(cursor.connection) as raw:
            await raw.execute(plan["sql_text"], (Jsonb(args), Jsonb(authority)))
            columns = [c.name for c in raw.description or ()]
            records = [
                {k: json_value(v) for k, v in zip(columns, row, strict=True)}
                for row in await raw.fetchmany(51)
            ]
        if len(records) > 50:
            raise ToolRejected("semantic_result_limit")
        if not all(validator.is_valid(row) for row in records):
            raise ToolRejected("semantic_result_contract")
        return records, witness

    return await read_database(binding, control, read)


async def verify_witness(store, trusted, ref, plan):
    from compass_agent.data_connections.connections import DataConnections
    from compass_agent.runtime.control import OwnerKey, RunRegistry

    current = choose_connection(
        DataConnections(store.engine, store.source_settings).bindings_for_run(trusted.scope), plan
    )
    if str(current.connection_id) != ref["connection"] or current.revision != ref["revision"]:
        return False
    control = RunRegistry().register(
        OwnerKey.from_context(trusted), run_id=UUID(ref["run"]), lifetime_s=5
    )
    if "policies" in ref["witness"]:
        from compass_agent.runtime.private_state import digest
        from compass_agent.semantic.entities import SemanticMetricOffer

        if not ref.get("receipt"):
            return False
        with store.transaction() as tx:
            row = tx.get(SemanticMetricOffer, UUID(ref["receipt"]))
            if (
                not row
                or row.run_id != UUID(ref["run"])
                or row.kind != "result"
                or row.owner_id != trusted.actor_user_id
            ):
                return False
            checks = store.private.open(row.run_id, row.kind, row.private_payload).get(
                "verification"
            )
        if not checks:
            return False
        for check in checks:
            records, witness = await control.perform(
                lambda check=check: calculate(current, plan, check["parameters"], control)
            )
            if witness != ref["witness"] or digest(records) != check["digest"]:
                return False
        return True
    _, witness = await control.perform(
        lambda: calculate(current, plan, {}, control, check_only=True)
    )
    return witness == ref["witness"]
