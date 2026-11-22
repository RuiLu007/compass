from copy import deepcopy
from functools import partial
from uuid import uuid4

import pytest
from compass_agent.context_runtime.profiles import search_pack
from compass_agent.context_runtime.search_claims import SearchGap, render

from .test_search_verification import draft, metric, proposal, record

render_retail = partial(render, pack=search_pack({"scenePack": "retail"}, None))


K = "2025-10-09T01:00:00Z"
POLICY = """以下条件须同时满足。
1. 最近一个完整月的现金流净额高于前一个完整月。
2. 恢复期的尝试至少 30 次，处理缺货率至多 5%。粒度见手册。
3. 最近一个已日结的快照中，可用库存至少 100 件。
达到条件动用余额的 40%。"""
TIER = """同一地区及规格至少 20 个带活动标记的浏览会话，且有本轮支出记录，才可动用全部余额。
样本不足时沿用小额上限。
须选择额度内的最高单档。"""
OFFER = """最迟于 10 月 9 日 15:00 前书面确认。
10 月 10 日 10:00 上线，只能订一个完整档位。
| 档位 | 金额 |
| --- | ---: |
| 基础档 | 6,000 元 |
| 全量档 | 15,000 元 |"""
SQL = """WITH w(label,s,e) AS (VALUES
    ('前期','2025-09-07'::date,'2025-09-12'::date),
    ('恢复','2025-09-16'::date,'2025-09-21'::date))
    SELECT w.label, count(DISTINCT b.visit_id) FILTER (WHERE b.source_id IS NOT NULL) AS sample
    FROM w JOIN core.visits b ON b.region_id=1 AND b.sku_id=435
        AND b.event_type='VIEW' AND b.event_at>=w.s AND b.event_at<w.e
        AND b.visible_at<='2025-10-09T01:00:00Z'
    GROUP BY w.label"""


def fixture():
    docs = {
        "balance": record(
            "history", "周会", "本笔预留余额为 20,000 元。负责人为运营经理，负责回函。"
        ),
        "trial": record("knowledge", "试资", POLICY),
        "tier": record("knowledge", "选档", TIER),
        "review": record(
            "knowledge",
            "复核制度",
            "获批上线后，负责人在首个工作日上午 10 时复核缺货率、活动会话和实际支出。",
        ),
        "offer": record("external", "确认函", OFFER, knownAt=K, timezone="Asia/Shanghai"),
        "calendar": record(
            "external",
            "2025 年日历",
            "10 月 11 日虽为星期六，按工作日执行。",
            timezone="Asia/Shanghai",
        ),
    }
    facts = []

    def add(key, op, **inputs):
        facts.append(proposal(key, op, **inputs))

    for key, doc, quote, op in (
        ("balance", "balance", "20,000 元", "measure"),
        ("fraction", "trial", "40%", "measure"),
        ("attempt_min", "trial", "30 次", "measure"),
        ("rate_max", "trial", "5%", "measure"),
        ("stock_min", "trial", "100 件", "measure"),
        ("sample_min", "tier", "20 个", "measure"),
        ("tier", "tier", "最高单档", "quote"),
        ("launch", "offer", "10:00 上线", "quote"),
        ("reply", "offer", "15:00 前书面确认", "quote"),
        ("owner", "balance", "负责人为运营经理", "quote"),
        ("review", "review", "复核缺货率", "quote"),
    ):
        add(key, op, evidenceId=doc, quote=quote)
    add("cap", "multiply", parents=["balance", "fraction"])
    for key, name, value, unit, start, end, filters in (
        ("current", "现金流净额", 13000, "CNY_fen", "09-01", "10-01", {"region": 1}),
        ("previous", "现金流净额", 12000, "CNY_fen", "08-01", "09-01", {"region": 1}),
        ("rate", "处理缺货率", "0", "ratio", "09-16", "09-21", {"region": 1, "sku": 435}),
        ("stock", "期末可用库存件数", 406, "piece", "10-08", "10-09", {"sku": 435}),
    ):
        data = metric(name, value, unit)
        data["scope"] = f"{start} 至 {end} / {filters}"
        data["record"]["time_range"] = [
            {"start": "2025-" + start, "end": "2025-" + end, "filters": filters}
        ]
        if key == "rate":
            data["record"]["columns"].append("denominator")
            data["record"]["rows"][0].append(57)
            data["available"]["denominator"] = "denominator"
        docs[key] = data
        add(key, "cell", evidenceId=key, row=0, column="value")
    add("attempts", "cell", evidenceId="rate", row=0, column="denominator")
    detail = record("detail", "授权明细")
    dictionary = {
        "core.visits." + col: {
            "description": desc,
            "source": {
                "kind": "detail",
                "title": "授权数据字典",
                "version": "1",
                "scope": "当前组",
                "locator": col,
                "excerpt": desc,
            },
        }
        for col, desc in (
            ("visit_id", "浏览会话"),
            ("source_id", "活动来源"),
            ("event_type", "VIEW CLICK"),
            ("region_id", "地区"),
            ("sku_id", "规格"),
        )
    }
    detail.update(
        sql=SQL,
        knownAt=K,
        columnDictionary=dictionary,
        record={"columns": ["label", "sample"], "rows": [["前期", 0], ["恢复", 0]]},
        available={
            "label": "w.label",
            "sample": "count(DISTINCT b.visit_id) FILTER (WHERE b.source_id IS NOT NULL)",
        },
        units={"sample": "count"},
    )
    docs["samples"] = detail
    dictionary_record = record(
        "definition", "数据字典", "core.visits.event_type。浏览 = VIEW。点击 = CLICK。"
    )
    dictionary_record["record"]["kind"] = "entity"
    detail["dictionaries"] = [dictionary_record]
    add("sample", "cell", evidenceId="samples", row=1, column="sample")
    for key, left, right, op in (
        ("growth", "current", "previous", "gt"),
        ("volume", "attempts", "attempt_min", "ge"),
        ("shortage", "rate", "rate_max", "le"),
        ("inventory", "stock", "stock_min", "ge"),
        ("expand", "sample", "sample_min", "ge"),
    ):
        add(key, "compare", parents=[left, right], comparator=op)
    add(
        "decision",
        "whole_tier",
        evidenceId="offer",
        parents=["cap", "tier", "growth", "volume", "shortage", "inventory", "expand"],
    )
    add(
        "action",
        "next_workday",
        evidenceId="calendar",
        parents=["launch", "review", "reply", "owner"],
    )
    return docs, facts


def test_purchase_and_action_require_each_original_and_carry_them_into_the_answer():
    data, facts = fixture()
    body, manifest = render_retail(uuid4(), draft(facts, ["decision", "action"]), data)
    assert all(
        t in body
        for t in (
            "样本门槛尚未达到",
            "6,000.00 元",
            "待签批",
            "运营经理",
            "获批后",
            "2025-10-09 15:00",
            "2025-10-10 10:00",
            "2025-10-11 10:00",
            "缺货率、活动会话和实际支出",
        )
    )
    assert len(manifest["anchors"]) == 2
    data["offer"]["record"]["text"] = OFFER.replace("6,000", "9,000")
    assert "暂缓采购" in render_retail(uuid4(), draft(facts, ["decision"]), data)[0]


@pytest.mark.parametrize(
    "change",
    [
        "missing_growth",
        "duplicate_volume",
        "no_rate",
        "wrong_amount",
        "wrong_scope",
        "wrong_month",
        "wrong_window",
        "wrong_marker",
        "wrong_event",
        "unknown_dictionary",
        "omitted_filter",
        "unrelated_filter",
        "unverified_sample",
        "sample_sufficient",
        "wrong_receipt",
        "old_policy",
        "empty_selected_window",
    ],
)
def test_rule_checks_do_not_accept_unmatched_values(change):
    data, facts = fixture()
    nodes = {f["key"]: f for f in facts}
    if change == "missing_growth":
        nodes["decision"]["parents"].remove("growth")
    elif change == "duplicate_volume":
        nodes["decision"]["parents"][-3] = "volume"
    elif change == "no_rate":
        nodes["decision"]["parents"].remove("shortage")
    elif change == "wrong_amount":
        data["current"]["title"] = data["previous"]["title"] = "支付总额"
    elif change == "wrong_scope":
        data["stock"]["record"]["time_range"][0]["filters"]["sku"] = 20
    elif change == "wrong_month":
        data["previous"]["record"]["time_range"][0]["start"] = "2025-07-01"
    elif change == "wrong_window":
        nodes["sample"]["row"] = 0
    elif change == "wrong_marker":
        data["samples"]["sql"] = SQL.replace("IS NOT NULL", "IS NULL")
    elif change == "wrong_event":
        data["samples"]["sql"] = SQL.replace("'VIEW'", "'CLICK'")
    elif change == "unknown_dictionary":
        data["samples"]["columnDictionary"]["core.visits.source_id"]["description"] = "寄件编号"
    elif change == "omitted_filter":
        data["samples"]["sql"] = SQL.replace(" AND b.sku_id=435", "")
    elif change == "unrelated_filter":
        data["samples"]["sql"] = SQL.replace(
            " AND b.sku_id=435", " AND b.sku_id=435 AND b.visit_id=8"
        )
    elif change == "unverified_sample":
        nodes["decision"]["parents"].remove("expand")
    elif change == "sample_sufficient":
        data["samples"]["record"]["rows"][1][1] = 21
    elif change == "wrong_receipt":
        data["other"] = deepcopy(data["offer"])
        data["other"]["locator"] = "其他确认函"
        nodes["reply"]["evidenceId"] = "other"
    elif change == "old_policy":
        data["review"]["current"] = False
    elif change == "empty_selected_window":
        data["samples"]["record"]["rows"][1][0] = "缺失"
    with pytest.raises(ValueError):
        render_retail(uuid4(), draft(facts, ["decision", "action"]), data)


def with_event_window(explicit):
    data, facts = fixture()
    data["trial"]["record"]["text"] = POLICY.replace("粒度见手册", "观察窗见《入库作业》")
    policy = record("knowledge", "入库作业", "自采购单在负责仓实际收货当天起，连续观察五个自然日。")
    data["window"] = policy
    event = record("detail", "受限明细")
    event.update(
        record={"columns": ["actual_time"], "rows": [["2025-09-15T16:30:00+00:00"]]},
        available={"actual_time": "m.occurred_at"},
        timezone="Asia/Shanghai",
        sql="""SELECT m.occurred_at FROM core.moves m JOIN core.orders d ON m.parent_id=d.id
            WHERE d.kind='ORDER' AND m.event='RECEIVED' AND m.sku_id=435""",
    )
    event["columnDictionary"] = {}
    for col, description in (
        ("core.moves.occurred_at", "发生时刻"),
        ("core.orders.kind", "ORDER DEFER"),
        ("core.moves.event", "RECEIVED TRANSFER"),
    ):
        source = {
            "kind": "detail",
            "title": "数据库字典",
            "version": "1",
            "scope": "授权仓",
            "locator": col,
            "excerpt": description,
        }
        event["columnDictionary"][col] = {"description": description, "source": source}
    definitions = []
    for col, summary in (
        ("core.orders.kind", "采购单 = ORDER。取消单 = DEFER。"),
        ("core.moves.event", "实际收货 = RECEIVED。调仓入库 = TRANSFER。"),
    ):
        definition = record("definition", col, col + "。" + summary)
        definition["record"]["kind"] = "entity"
        definitions.append(definition)
    event["dictionaries"] = definitions
    data["event"] = event
    k = next(i for i, p in enumerate(facts) if p["key"] == "decision")
    branches = [
        proposal("event", "cell", evidenceId="event", row=0, column="actual_time"),
        proposal("rule_window", "quote", evidenceId="window", quote="实际收货"),
    ]
    if explicit:
        branches.append(proposal("anchored", "event_window", parents=["event", "rule_window"]))
    facts[k]["parents"] += ["anchored"] if explicit else ["event", "rule_window"]
    facts[k:k] = branches
    return data, facts


@pytest.mark.parametrize("explicit", [True, False])
def test_both_shapes_bind_receipt_and_policy_to_selected_window(explicit):
    data, facts = with_event_window(explicit)
    body, manifest = render_retail(uuid4(), draft(facts, ["decision"]), data)
    assert "6,000.00 元" in body
    sources = next(iter(manifest["details"].values()))["sources"]
    assert any(s["title"] == "入库作业" for s in sources)
    assert any("实际收货 = RECEIVED" in s["excerpt"] for s in sources)
    data["event"]["sql"] = data["event"]["sql"].replace("'RECEIVED'", "'TRANSFER'")
    with pytest.raises(ValueError):
        render_retail(uuid4(), draft(facts, ["decision"]), data)


@pytest.mark.parametrize("change", ["missing_event", "prior_period", "wrong_sku", "other_policy"])
def test_mismatched_receipt_does_not_qualify(change):
    data, facts = with_event_window(False)
    if change == "missing_event":
        next(p for p in facts if p["key"] == "decision")["parents"].remove("event")
    elif change == "prior_period":
        data["event"]["record"]["rows"][0][0] = "2025-09-15T12:00:00+00:00"
    elif change == "wrong_sku":
        data["event"]["sql"] = data["event"]["sql"].replace("sku_id=435", "sku_id=65")
    else:
        data["window"]["title"] = "其他手册"
    with pytest.raises(ValueError):
        render_retail(uuid4(), draft(facts, ["decision"]), data)


@pytest.mark.parametrize("explicit", [True, False])
def test_missing_dictionary_guides_a_new_lookup_with_actual_field_and_value(explicit):
    data, facts = with_event_window(explicit)
    event = data["event"]
    definitions = event["dictionaries"]
    event["dictionaries"] = [definitions[1]]
    with pytest.raises(SearchGap) as missing:
        render_retail(uuid4(), draft(facts, ["decision"]), data)
    assert all(
        token in str(missing.value)
        for token in (
            "core.orders.kind",
            "ORDER",
            "search_semantics",
            "get_metric_definition",
        )
    )
    event["dictionaries"] = definitions
    del event["columnDictionary"]["core.moves.occurred_at"]
    with pytest.raises(SearchGap, match=r"describe_table.*core\.moves.*occurred_at"):
        render_retail(uuid4(), draft(facts, ["decision"]), data)


def test_missing_population_dictionary_reports_only_when_used_as_eligibility():
    data, facts = fixture()
    data["samples"]["dictionaries"] = []
    with pytest.raises(SearchGap, match=r"core\.visits\.event_type.*VIEW"):
        render_retail(uuid4(), draft(facts, ["decision"]), data)
    # The same accepted SQL count remains a source-backed raw observation.
    count = next(p for p in facts if p["key"] == "sample")
    _, manifest = render_retail(uuid4(), draft([count], ["sample"]), data)
    assert all(d["status"] == "supported" for d in manifest["details"].values())
