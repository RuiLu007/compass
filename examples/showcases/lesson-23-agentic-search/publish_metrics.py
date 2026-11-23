"""Prepare trusted bindings as drafts, run the real trial and perform distinct teaching reviews."""

import argparse
import json
import subprocess
from uuid import NAMESPACE_URL, uuid5

from configure import fixture_db
from dimensions import dictionary_fields
from materials import ROOT, TENANT, WORKSPACE, K
from psycopg.types.json import Jsonb
from web_client import ClassroomWeb

TRIALS = {
    "sales.paid_gmv": ("2025-09-01", "2025-10-01", {"region": 1, "channel": 4}, "20455095"),
    "sales.net_gmv": ("2025-09-01", "2025-10-01", {"region": 1, "channel": 4}, "18084020"),
    "channel.reported_paid_amount": (
        "2025-09-16",
        "2025-09-21",
        {"region": 1, "channel": 4, "sku": 435},
        "1867320",
    ),
    "inventory.closing_available_qty": ("2025-10-08", "2025-10-09", {"site": 7, "sku": 435}, "406"),
    "fulfillment.allocation_insufficient_rate": (
        "2025-09-16",
        "2025-09-21",
        {"region": 1, "channel": 4, "fulfillment_site": 7, "sku": 435},
        "0",
    ),
}

EXTRA_ALIASES = {
    "sales.paid_gmv": ["成交额"],
    "sales.net_gmv": ["扣退款的成交额", "扣退款后的成交额"],
    "channel.reported_paid_amount": ["商品日报实付"],
    "inventory.closing_available_qty": ["上个日结可用件数"],
}


def key(*parts):
    return str(uuid5(NAMESPACE_URL, "/".join(("compass/retail-fulfillment-v1", *map(str, parts)))))


def artifact():
    js = """
    import {loadFulfillmentMetric, loadMonthlyCashBindings}
      from './apps/web/scripts/retail-semantic-seed.mjs';
    console.log(JSON.stringify({
      ...await loadFulfillmentMetric(),
      monthly: await loadMonthlyCashBindings()
    }));
    """
    return json.loads(subprocess.check_output(["node", "--input-type=module", "-e", js], cwd=ROOT))


def stage_candidate(conn, version, candidate, author):
    found = conn.execute(
        "SELECT binding_id,target_hash FROM web.execution_bindings "
        "WHERE version_id=%s AND workspace_id=%s",
        (version, WORKSPACE),
    ).fetchall()
    if found:
        if len(found) != 1 or found[0][1] != candidate["target_hash"]:
            raise ValueError("Unexpected existing binding for the same draft")
        return str(found[0][0])
    return str(
        conn.execute(
            """INSERT INTO web.execution_bindings(
        version_id,tenant_id,workspace_id,source_ref,mode,target_ref,
        sql_text,target_hash,parameter_schema,result_schema,access_requirements,validation_receipt)
        VALUES(%s,%s,%s,'retail:101','trusted_sql',%s,%s,%s,%s,%s,%s,%s) RETURNING binding_id""",
            (
                version,
                TENANT,
                WORKSPACE,
                candidate["target_ref"],
                candidate["sql"],
                candidate["target_hash"],
                Jsonb(candidate["parameter_schema"]),
                Jsonb(candidate["result_schema"]),
                Jsonb({**candidate["access"], "retail_tenant_id": 101}),
                Jsonb({"requires_trial": True, "author": author}),
            ),
        ).fetchone()[0]
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", required=True, help="Verified frozen source directory")
    dimensions = dictionary_fields(parser.parse_args().frozen)
    admin = ClassroomWeb("teaching.admin")
    reviewers = {
        "submit": admin,
        "business": ClassroomWeb("teaching.viewer"),
        "data": ClassroomWeb("teaching.auditor"),
    }
    views = {item["code"]: item for item in admin.semantic("assets")["items"]}
    tenant = next(s["id"] for s in admin.semantic("scopes")["items"] if s["kind"] == "tenant")
    art = artifact()
    metric, binding, monthly = art["metric"], art["binding"], art["monthly"]
    sources = [
        "channel_product_daily",
        "asset_period_status",
        "sku",
        "spu",
        "payment",
        "refund",
        "order_header",
        "source_watermark",
        "assortment",
        "inventory_snapshot",
    ]
    case_codes = [
        *dimensions,
        *("entity." + name for name in sources),
        *TRIALS,
        "term.sales_amount",
    ]
    for code in case_codes:
        v = views.get(code)
        dictionary_changed = (
            code in dimensions
            and v is not None
            and (
                v["summary"] != dimensions[code]["summary"]
                or set(v["aliases"]) != {dimensions[code]["name"], *dimensions[code]["aliases"]}
            )
        )
        with fixture_db() as conn:
            if (
                v
                and v["state"] == "published"
                and not dictionary_changed
                and set(EXTRA_ALIASES.get(code, [])).issubset(v["aliases"])
                and (code not in monthly or "month" in v["definition"]["dimensions"])
            ):
                print(code, "already_published", flush=True)
                continue
            if code in dimensions and (
                v is None or dictionary_changed and v["state"] == "published"
            ):
                fields = dimensions[code].copy()
                fingerprint = fields.pop("source_digest")
                source = admin.semantic(
                    "sources",
                    dict(
                        request_key=key(code, fingerprint, "source"),
                        kind="schema",
                        locator=fields["source_position"],
                        version=fingerprint,
                        excerpt=fields["summary"],
                        priority="P0",
                        responsible=fields["responsible"],
                        owner_scope_id=tenant,
                    ),
                )
                v = admin.semantic(
                    "drafts",
                    dict(
                        request_key=key(
                            code, fingerprint, v["version_id"] if v else "new", "draft"
                        ),
                        source_id=source["source_id"],
                        owner_scope_id=tenant,
                        **(
                            {"asset_id": v["asset_id"], "expected_digest": v["digest"]} if v else {}
                        ),
                        **fields,
                    ),
                )
            elif v is None:
                if code != metric["code"]:
                    raise ValueError("Install the prerequisite retail semantic pack: " + code)
                source = admin.semantic(
                    "sources",
                    dict(
                        request_key=key(code, "source"),
                        kind="sql",
                        locator="retail-fulfillment/allocation-rate.sql",
                        version=binding["target_hash"],
                        excerpt=metric["summary"],
                        responsible=metric["responsible"],
                        priority="P0",
                        owner_scope_id=tenant,
                    ),
                )
                fields = {
                    k: metric[k]
                    for k in (
                        "code",
                        "domain",
                        "name",
                        "summary",
                        "grain",
                        "unit",
                        "responsible",
                        "aliases",
                    )
                }
                v = admin.semantic(
                    "drafts",
                    dict(
                        request_key=key(code, "draft"),
                        kind="metric",
                        source_id=source["source_id"],
                        source_position="allocation-rate.sql",
                        owner_scope_id=tenant,
                        dependencies=[
                            views["entity." + n]["asset_id"]
                            for n in ("order_header", "source_watermark")
                        ],
                        **metric["definition"],
                        **fields,
                    ),
                )
            elif code in EXTRA_ALIASES and (
                not set(EXTRA_ALIASES[code]).issubset(v["aliases"])
                or code in monthly
                and "month" not in v["definition"]["dimensions"]
            ):
                # Reference the shipped v1 content in a new definition revision. Keep all existing
                # field semantics and bind its independently approved version to the same SQL.
                fields = {
                    k: v[k]
                    for k in (
                        "code",
                        "domain",
                        "kind",
                        "name",
                        "summary",
                        "grain",
                        "unit",
                        "source_id",
                        "source_position",
                        "owner_scope_id",
                        "usage_scope_id",
                        "asset_id",
                    )
                }
                fields.update(
                    {
                        k: v["definition"][k]
                        for k in (
                            "formula",
                            "event_time",
                            "timezone",
                            "aggregation",
                            "dimensions",
                            "filters",
                        )
                    }
                )
                if code in monthly:
                    fields["dimensions"] = list(dict.fromkeys([*fields["dimensions"], "month"]))
                v = admin.semantic(
                    "drafts",
                    dict(
                        request_key=key(code, "cash-month" if code in monthly else "aliases"),
                        expected_digest=v["digest"],
                        dependencies=v["dependencies"],
                        aliases=list(dict.fromkeys([*v["aliases"], *EXTRA_ALIASES[code]])),
                        responsible=v["responsible"],
                        **fields,
                    ),
                )
            if code == metric["code"] or code in monthly:
                bid = stage_candidate(
                    conn,
                    v["version_id"],
                    monthly.get(code, binding),
                    "99500000-0000-4000-8000-000000000002",
                )
            else:
                row = conn.execute(
                    "SELECT binding_id FROM web.execution_bindings "
                    "WHERE tenant_id=%s AND workspace_id=%s AND version_id=%s",
                    (TENANT, WORKSPACE, v["version_id"]),
                ).fetchone()
                bid = str(row[0]) if row else None
                if code in TRIALS and not bid:
                    # A previous attempt may have saved the draft before staging its binding.
                    conn.execute(
                        """INSERT INTO web.execution_bindings(
        version_id,tenant_id,workspace_id,source_ref,mode,target_ref,sql_text,target_hash,
                        parameter_schema,result_schema,access_requirements,validation_receipt)
                        SELECT %s,b.tenant_id,b.workspace_id,b.source_ref,b.mode,b.target_ref,
                               b.sql_text,b.target_hash,b.parameter_schema,b.result_schema,
                        b.access_requirements,jsonb_build_object('requires_trial',true,
                            'author','99500000-0000-4000-8000-000000000002')
                        FROM web.execution_bindings b JOIN web.semantic_versions v
                          ON v.parent_version_id=b.version_id
                        WHERE v.version_id=%s AND b.tenant_id=%s AND b.workspace_id=%s""",
                        (v["version_id"], v["version_id"], TENANT, WORKSPACE),
                    )
                    bid = str(
                        conn.execute(
                            "SELECT binding_id FROM web.execution_bindings WHERE version_id=%s",
                            (v["version_id"],),
                        ).fetchone()[0]
                    )
        base = dict(expected_digest=v["digest"])
        if bid:
            base["binding_id"] = bid
        if code in TRIALS:
            start, end, filters, value = TRIALS[code]
            expected_rows = [dict(group_key="total", value=value, status="OK")]
            group_by = "total"
            if code in monthly:
                start = "2025-07-01"
                group_by = "month"
                values = (
                    ["35296820", "7368475", "20455095"]
                    if code == "sales.paid_gmv"
                    else ["29373605", "1981070", "18084020"]
                )
                expected_rows = [
                    dict(group_key="2025-" + m, value=v0, status="OK")
                    for m, v0 in zip(["07", "08", "09"], values, strict=True)
                ]
            admin.semantic(
                "trial",
                dict(
                    request_key=key(v["version_id"], "trial"),
                    version_id=v["version_id"],
                    binding_id=bid,
                    parameters=dict(
                        start=start,
                        end=end,
                        as_of=K.isoformat(),
                        filters=filters,
                        group_by=group_by,
                    ),
                    expected=expected_rows,
                ),
            )
        notes = {
            "submit": "提交本版定义及受限执行绑定。",
            "business": "课堂业务评审已核粒度、单位、期间和适用部门。",
            "data": "课堂数据评审核对来源、授权和独立预期。",
            "release": "发布已审课堂修订。",
        }
        for step, reviewer in reviewers.items():
            reviewer.semantic(
                "revisions/" + v["version_id"] + "/" + step,
                dict(request_key=key(v["version_id"], step), note=notes[step], **base),
            )
        with fixture_db() as conn:
            generation = conn.execute(
                "SELECT generation FROM web.semantic_release_head WHERE partition_id=%s", (tenant,)
            ).fetchone()
        res = admin.semantic(
            "releases",
            dict(
                request_key=key(v["version_id"], "release"),
                note=notes["release"],
                version_id=v["version_id"],
                expected_generation=generation[0] if generation else 0,
                **base,
            ),
        )
        print(code, "published", res["release_id"], flush=True)

    # Current asset permissions are independent of the older scope memberships.
    # Grant only this classroom's published inputs to its dedicated analyst.
    directory = admin.semantic("asset-grants")
    grant_codes = set(case_codes) - {"term.sales_amount"}  # Shared terms use their own read policy.
    assets = sorted(item["id"] for item in directory["assets"] if item["code"] in grant_codes)
    if len(assets) != len(grant_codes):
        raise ValueError("Classroom asset publication is incomplete")
    admin.semantic(
        "asset-grants",
        dict(
            request_key=key("classroom-analyst-assets", *assets),
            mode="member",
            target_ids=["99500000-0000-4000-8000-000000000031"],
            asset_ids=assets,
            active=True,
        ),
    )
    print("Classroom analyst asset permissions verified", flush=True)


if __name__ == "__main__":
    main()
