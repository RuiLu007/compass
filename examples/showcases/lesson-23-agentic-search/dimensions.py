"""Publish business filter dictionaries read from the classroom's restricted identity."""

import hashlib
import json
from pathlib import Path

import psycopg

# This explicit column list is the public business dictionary of the classroom.
QUERIES = {
    "region": (
        "经营地区",
        "core.geo_area",
        "SELECT geo_name,geo_id FROM core.geo_area WHERE geo_id=region_id ORDER BY geo_id",
    ),
    "channel": (
        "销售渠道",
        "core.channel",
        "SELECT channel_name,channel_id FROM core.channel ORDER BY channel_id",
    ),
    "site": ("经营地点", "core.site", "SELECT site_name,site_id FROM core.site ORDER BY site_id"),
}

# Business names accompany the authorized column's published enum. Keeping the full domain
# distinguishes product views from search, page views and checkout in independent questions.
EVENT_NAMES = {
    "PAGE_VIEW": "页面浏览",
    "SEARCH": "搜索",
    "PRODUCT_VIEW": "商品浏览",
    "ADD_CART": "加入购物车",
    "CHECKOUT_SUBMITTED": "提交结账",
    "CHECKOUT_REJECTED": "结账被拒",
}


SOURCE_DOMAINS = {
    "behavior_event_type": (
        "行为事件类型",
        "事件类型",
        "core.behavior_event.event_type",
        EVENT_NAMES,
    ),
    "inventory_document_type": (
        "库存单据类型",
        "单据类型",
        "core.inventory_document.doc_type",
        {"OPENING": "期初单", "PURCHASE": "采购单", "TRANSFER": "调拨单", "DISPOSAL": "处置单"},
    ),
    "inventory_movement_type": (
        "库存流水类型",
        "流水类型",
        "core.inventory_movement.movement_type",
        {
            "OPENING": "期初入账",
            "RECEIPT": "实际收货",
            "RESERVE": "锁库预留",
            "RELEASE": "释放预留",
            "SHIP": "发货出库",
            "RETURN_RECEIVE": "退货收件",
            "RESTOCK": "退货返库",
            "SCRAP": "报废",
            "TRANSFER_OUT": "调拨出库",
            "TRANSIT_IN": "转入在途",
            "TRANSIT_OUT": "转出在途",
            "TRANSFER_IN": "调拨入库",
            "DAMAGE": "损毁",
            "RTO_RECEIPT": "拒收退回入库",
        },
    ),
}


def dictionary_fields(frozen):
    connection = json.loads((Path(frozen) / "connection.json").read_text())
    out = {}
    with psycopg.connect(
        host=connection["host"],
        port=connection["port"],
        dbname=connection["database_name"],
        user=connection["role"],
        password=connection["password"],
    ) as db:
        for dimension, (title, table, query) in QUERIES.items():
            rows = db.execute(query).fetchall()
            summary = "；".join(f"{name} = {key}" for name, key in rows)
            field = (
                "site（销售地点/日结地点）或 fulfillment_site（履约地点）"
                if dimension == "site"
                else dimension
            )
            summary = f"正式指标 filters.{field} 使用整数业务键。{summary}。"
            name = title + "筛选值域"
            aliases = [n for n, _ in rows]
            if dimension == "channel":
                aliases.extend(n.removeprefix("微信") for n, _ in rows if n.startswith("微信"))
            if dimension == "site":
                aliases.extend(n.removeprefix("行野") for n, _ in rows)
            out["dimension." + dimension] = dict(
                kind="entity",
                code="dimension." + dimension,
                name=name,
                summary=summary,
                aliases=aliases,
                domain="retail",
                responsible="零售数据负责人",
                grain="每个授权业务键一项",
                unit="业务键",
                source_position=table,
                source_digest=hashlib.sha256(summary.encode()).hexdigest(),
            )
        for dimension, (title, noun, position, names) in SOURCE_DOMAINS.items():
            table, column = position.rsplit(".", 1)
            domain = db.execute(
                """SELECT col_description(attrelid,attnum) FROM pg_attribute
                   WHERE attrelid=%s::regclass AND attname=%s
                     AND has_column_privilege(session_user,attrelid,attname,'SELECT')""",
                (table, column),
            ).fetchone()
            codes = (domain[0] if domain else "").split()
            if not codes or set(codes) != set(names):
                raise ValueError("Review the frozen source domain before publishing: " + position)
            summary = position + "。" + "。".join(f"{names[c]} = {c}" for c in codes) + "。"
            code = "dimension." + dimension
            out[code] = dict(
                kind="entity",
                code=code,
                name=title + "值域",
                summary=summary,
                aliases=[*names.values(), noun],
                domain="retail",
                responsible="零售数据负责人",
                grain=f"每个{title}一项",
                unit=noun + "代码",
                source_position=position,
                source_digest=hashlib.sha256(summary.encode()).hexdigest(),
            )
    return out
