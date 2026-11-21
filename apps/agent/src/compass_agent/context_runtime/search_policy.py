"""Prove each prerequisite of a selected, current source policy from checked operands."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from compass_agent.context_runtime.search_sample import LookupNeeded


def _section(source):
    return source["locator"].split(" · [", 1)[0], source["version"]


def _metric_named(fact, clause):
    title = next((s["title"] for s in fact.sources if s["kind"] == "metric"), "")
    # Period and display-unit modifiers often surround the published business name.
    title = re.sub(r"^(?:期末|期初|日结|月末)", "", title)
    title = re.sub(r"(?:件数|数量|次数)$", "", title)

    def compact(value):
        return re.sub(r"\s+", "", value).casefold()

    return bool(title and compact(title) in compact(clause))


def _full_month(window):
    if not window:
        return False
    start, end = map(date.fromisoformat, window)
    return start.day == end.day == 1 and 28 <= (end - start).days <= 31


def _trend(fact, clause, cutoff):
    if not fact.comparison:
        return False
    left, comparator, right = fact.comparison
    desired = next(
        (
            op
            for phrase, op in (("不低于", "ge"), ("不高于", "le"), ("高于", "gt"), ("低于", "lt"))
            if phrase in clause
        ),
        None,
    )
    if not (
        desired
        and desired == comparator
        and _metric_named(left, clause)
        and left.field == right.field == "value"
        and left.receipt_scope
        and left.receipt_scope == right.receipt_scope
        and left.window
        and right.window
        and right.window[1] == left.window[0]
    ):
        return False
    if "完整月" in clause:
        return (
            _full_month(left.window)
            and _full_month(right.window)
            and (not cutoff or date.fromisoformat(left.window[1]) == cutoff.replace(day=1))
        )
    return True


def _threshold(fact, match, clause, sections, units):
    if not fact.comparison:
        return False
    left, op, limit = fact.comparison
    unit, multiplier = units[match[2]]
    local = clause[: match.start()].rstrip()
    direction = next(
        (
            operator
            for pattern, operator in (
                (r"(?:至少|不少于|不低于|达到|满)$", "ge"),
                (r"(?:至多|最多|不得超过|不超过|不高于)$", "le"),
                (r"(?:大于|高于|超过)$", "gt"),
                (r"(?:小于|低于|不到)$", "lt"),
            )
            if re.search(pattern, local)
        ),
        None,
    )
    return (
        limit.current
        and op == limit.threshold == direction
        and direction is not None
        and left.unit == limit.unit == unit
        and limit.value == Decimal(match[1].replace(",", "")) * multiplier
        and any(
            source["kind"] == "knowledge"
            and _section(source) in sections
            and (
                re.sub(r"^\s*\d+[.、]\s*", "", source["excerpt"]) in clause
                or clause in source["excerpt"]
            )
            for source in limit.sources
        )
    )


def _shared_dimensions(facts):
    dimensions = {}
    for fact in facts:
        if not fact.receipt_scope:
            continue
        values = dict(fact.receipt_scope[0])
        if fact.authorized_source_tenant is not None:
            values["tenant"] = fact.authorized_source_tenant
        for name, value in values.items():
            # Both warehouse and selling locations refer to an approved location key.
            if name.endswith("_site"):
                name = "site"
            if name in dimensions and dimensions[name] != value:
                raise ValueError("各项资格的查询对象不一致")
            dimensions[name] = value
    return dimensions


def observation_window(event, policy):
    evidence = event.event
    if not evidence or not policy.current or not policy.policy_text:
        raise ValueError("观察窗须使用已核事件时点和现行起算条款")
    origin = re.search(r"(?:从|自)([^，。\n]{1,100}?)(?:当日|当天|之日)起", policy.text)
    duration = re.search(
        r"连续(?:观察)?\s*([1-9]\d?|[一二三四五六七八九十两])\s*个?自然日", policy.text
    )
    if not origin or not duration:
        raise ValueError("原条款须明确起点事件及连续自然日数")
    terms = []
    for names, owns_time in evidence["roles"]:
        match = next((name for name in names if name in origin[1]), None)
        if not match:
            raise ValueError("起算事件的类别与已读原文不一致")
        terms.append((origin[1].rindex(match) + len(match), owns_time))
    if not max(terms)[1]:
        raise ValueError("时点列须来自原文最后指明的起算事件")
    n = duration[1]
    days = (
        int(n)
        if n.isdecimal()
        else dict(zip("一二三四五六七八九十", range(1, 11), strict=True)).get(n, 2)
    )
    start = date.fromisoformat(evidence["day"])
    return (start.isoformat(), (start + timedelta(days=days)).isoformat())


def policy_eligibility(
    cap, rule, qualifications, offer, measure, units, sample_matches, anchor=None
):
    if not all(q.comparison and q.condition is not None for q in qualifications):
        raise ValueError("资格须由可追溯的独立比较核验")
    if not cap.rule_clauses:
        return all(q.condition for q in qualifications), ""
    sections = {_section(s) for s in cap.sources if s["kind"] == "knowledge"}
    available = list(qualifications)
    certified = []
    series = []
    cutoff = (
        datetime.fromisoformat(offer["knownAt"]).astimezone(ZoneInfo(offer["timezone"])).date()
        if offer.get("knownAt") and offer.get("timezone")
        else None
    )

    def take(matches, requirement):
        if not matches:
            raise LookupNeeded("尚缺该项制度资格的同对象、同期间比较：" + requirement)
        if len(matches) > 1:
            raise ValueError("请为该项制度资格只保留一条相符的比较：" + requirement)
        result = matches[0]
        available.remove(result)
        return result

    for clause in cap.rule_clauses:
        thresholds = list(measure.finditer(clause))
        if not thresholds:
            certified.append(take([q for q in available if _trend(q, clause, cutoff)], clause))
            continue
        related = []
        contains_ratio = any(units[m[2]][0] == "ratio" for m in thresholds)
        for match in thresholds:
            unit = units[match[2]][0]
            field = "denominator" if contains_ratio and unit == "count" else "value"
            fact = take(
                [
                    q
                    for q in available
                    if (
                        _threshold(q, match, clause, sections, units)
                        and q.comparison[0].field == field
                        and _metric_named(q.comparison[0], clause)
                    )
                ],
                f"{match[0]}，请使用正式指标的 {field} 列和原段阈值核对。",
            )
            left = fact.comparison[0]
            if not left.window or not left.receipt_scope:
                raise ValueError("资格比较缺少正式回执的期间或对象")
            if (
                cutoff
                and "已日结" in clause
                and (left.window != ((cutoff - timedelta(days=1)).isoformat(), cutoff.isoformat()))
            ):
                raise ValueError("日结资格须覆盖决策时点之前的最新完整日")
            related.append(left)
            certified.append(fact)
        if contains_ratio:
            coordinates = {(x.window, x.receipt_scope) for x in related}
            if len(coordinates) != 1:
                raise ValueError("比例与尝试数须来自同一期间和对象的已核指标")
            references = re.findall(r"见《([^》]+)》", clause) if "窗" in clause else []
            if references:
                if (
                    not anchor
                    or not any(
                        s["kind"] == "knowledge" and s["title"] in references
                        for s in anchor.sources
                    )
                    or related[0].window != anchor.window
                ):
                    raise ValueError("该观察窗须与所引制度及起算原始事件的期间一致")
                for name, value in related[0].receipt_scope[0]:
                    field = ("site" if name.endswith("_site") else name) + "_id"
                    if field in anchor.event["filters"] and anchor.event["filters"][field] != str(
                        value
                    ):
                        raise ValueError("观察窗起算事件与正式指标的对象不一致")
            series.extend(related)
    dimensions = _shared_dimensions([q.comparison[0] for q in certified])
    expansion = [
        s
        for s in re.split(r"[。\n]", rule.policy_text or "")
        if re.search(r"(?:才可|才(?:能|允许)|方可).{0,24}(?:全部|全额)", s)
    ]
    note = ""
    for clause in expansion:
        windows = {x.window for x in series}
        rule_sections = {_section(s) for s in rule.sources}
        requirements = list(measure.finditer(clause))
        if not requirements:
            raise ValueError("全额采购条件缺少可核的样本门槛")
        for match in requirements:
            candidates = [
                q for q in available if _threshold(q, match, clause, rule_sections, units)
            ]
            matches = [
                q
                for q in candidates
                if sample_matches(q.comparison[0], clause, windows, dimensions)
            ]
            if not matches:
                for q in candidates:
                    if q.comparison[0].lookup_problem:
                        raise LookupNeeded(q.comparison[0].lookup_problem)
            check = take(matches, f"{match[0]}，请核对实际样本的去重单位、标记及观察窗。")
            if check.condition:
                raise ValueError("样本门槛已达到，请继续核对全额条款的其他依据和适用额度")
            note = "全额投放的样本门槛尚未达到，沿用小额上限。"
    if available:
        raise ValueError("采购资格包含无法与适用条款对应的比较")
    return all(q.condition for q in certified), note
