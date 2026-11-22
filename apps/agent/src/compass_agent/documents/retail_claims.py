"""Optional retail document checks over signed receipts and opened original terms."""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from compass_agent.context_runtime.search_claims import (
    MEASURE,
    UNITS,
    Checked,
    SearchGap,
    display,
    merge_sources,
)
from compass_agent.context_runtime.search_sample import LookupNeeded, matches_sample
from compass_agent.documents.retail_policy import observation_window, policy_eligibility


def price_table(record):
    rows = []
    for line in record["text"].splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [s.strip() for s in line.strip().strip("|").split("|")]
        if not rows and not any("金额" in s or "价格" in s or "报价" in s for s in cells):
            continue
        rows.append(cells)
    if len(rows) < 3 or len(rows[0]) != 2 or not all(re.fullmatch("[:-]+", s) for s in rows[1]):
        raise ValueError("需要带表头的完整报价表")
    col = next(i for i, s in enumerate(rows[0]) if any(w in s for w in ("金额", "价格", "报价")))
    tiers = []
    for row in rows[2:]:
        if (
            len(row) != 2
            or not (m := MEASURE.fullmatch(row[col]))
            or m[2] not in {"元", "万元", "分"}
        ):
            raise ValueError("报价中有无法核实的档位")
        price = Decimal(m[1].replace(",", "")) * UNITS[m[2]][1]
        if price <= 0 or price != price.to_integral_value():
            raise ValueError("整档金额无效")
        tiers.append((row[1 - col], price))
    return tiers


_EVENT_DATE = (
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    r"(?:\s*(\d{1,2})\s*(?:时|:|：)\s*(\d{2})?)?"
)


def event_date(text, event):
    suffix = {
        "launch": r"(?:如期|正式|按期|计划|安排|将|起)?\s*(?:上线|上刊|启动)",
        "reply": r"(?:之?前)?\s*(?:书面)?(?:确认|回复|回函|答复)",
    }[event]
    prefix = {
        "launch": r"(?:上线|上刊|启动)(?:时间|时刻|日期)?(?:定于|为|是|：|:|于)\s*",
        "reply": r"(?:回函|回复|确认)(?:截止时间|期限|时限|截止)(?:为|是|：|:|至|于)?\s*",
    }[event]
    times = {
        m.groups()
        for pattern in (_EVENT_DATE + r"\s*" + suffix, prefix + _EVENT_DATE)
        for m in re.finditer(pattern, text)
    }
    if len(times) != 1:
        raise ValueError("原文需唯一标明适用的上线或回函日期")
    return next(iter(times))


def next_workday(calendar, launch, rule, tz):
    # Calendar and local date/time come from opened primary texts. Missing coverage
    # or an ambiguous time leaves the decision pending.
    launch_year, month, day_of_month, _, _ = event_date(launch, "launch")
    year_match = re.search(r"(20\d\d)\s*年", calendar["title"] + calendar.get("text", ""))
    hour_match = re.search(r"工作日.{0,6}?(\d{1,2})\s*(?:时|:(\d{2}))", rule)
    if not year_match or not hour_match or not tz or "工作日" not in calendar["text"]:
        raise ValueError("缺少上线时刻、复核条款或工作日历覆盖")
    year = int(launch_year or year_match[1])
    if year != int(year_match[1]):
        raise ValueError("工作日历未覆盖该年")
    scheduled = date(year, int(month), int(day_of_month))
    coverage = {int(value) for value in re.findall(r"(\d{1,2})\s*月", calendar["text"])}
    full_year = bool(re.search(r"全年(?:工作日|适用|安排)", calendar["text"]))
    if not full_year and scheduled.month not in coverage:
        raise ValueError("当前工作日历原段未覆盖上线日期")
    holidays, working = set(), set()
    for m in re.finditer(
        r"(\d+)\s*月\s*(\d+)\s*日至\s*(?:(\d+)\s*月\s*)?(\d+)\s*日[^。]{0,8}(?:休假|连休|放假)",
        calendar["text"],
    ):
        first, last = date(year, int(m[1]), int(m[2])), date(year, int(m[3] or m[1]), int(m[4]))
        holidays.update(first + timedelta(days=i) for i in range((last - first).days + 1))
    for m in re.finditer(r"(\d+)\s*月\s*(\d+)\s*日[^。]{0,20}?(?:按工作日|上班)", calendar["text"]):
        working.add(date(year, int(m[1]), int(m[2])))
    for offset in range(1, 32):
        day = scheduled + timedelta(days=offset)
        if day.year != year or (not full_year and day.month not in coverage):
            break
        if day in working or day.weekday() < 5 and day not in holidays:
            return f"{day.isoformat()} {int(hour_match[1]):02}:{int(hour_match[2] or 0):02} {tz}"
    raise ValueError("下一个工作日超出日历覆盖")


def document_identity(item, kind):
    if not item.current or len(item.sources) != 1 or item.sources[0]["kind"] != kind:
        raise ValueError("行动依据须直接引用当前适用的原文")
    source = item.sources[0]
    return source["version"], source["locator"].split(" · [", 1)[0].split(" / ", 1)[0]


def action_statement(parents, calendar, tz):
    launch, rule = parents[:2]
    offer = document_identity(launch, "external")
    document_identity(rule, "knowledge")
    when = next_workday(calendar, launch.text, rule.text, tz)
    if len(parents) == 2:
        return "复核时间 " + when
    deadline, owner = parents[2:]
    if document_identity(deadline, "external") != offer:
        raise ValueError("回函和上线须关联同一签收报价")
    document_identity(owner, "history")
    role = re.search(r"(?:负责人|承办人)(?:为|是|：|:)\s*([^，。；\n]{1,32})", owner.text)
    tasks = re.search(r"复核([^，。；\n]{2,100})", rule.text)
    if not role or not tasks or not re.search(r"负责人|承办人", rule.text):
        raise ValueError("行动单缺少原件中的负责人、复核项目或责任约定")
    calendar_year = int(when[:4])

    def instant(item, event):
        y, m, d, h, minutes = event_date(item.text, event)
        if h is None:
            raise ValueError("回函和上线均须核对本地时刻")
        return datetime(int(y or calendar_year), int(m), int(d), int(h), int(minutes or 0))

    due = instant(deadline, "reply")
    scheduled = instant(launch, "launch")
    if due >= scheduled:
        raise ValueError("回函期限须早于上线时间")
    return (
        f"建议由{role[1]}提请签批，获批后于 {due:%Y-%m-%d %H:%M} 前回函。"
        f"若如期于 {scheduled:%Y-%m-%d %H:%M} 上线，由{role[1]}于 {when} 复核{tasks[1]}。"
    )


def checked_window(event, policy):
    if event.lookup_problem:
        raise SearchGap(event.lookup_problem)
    window = observation_window(event, policy)
    end = (date.fromisoformat(window[1]) - timedelta(days=1)).isoformat()
    days = (date.fromisoformat(window[1]) - date.fromisoformat(window[0])).days
    return Checked(
        f"按原件中实际发生的事件起算，观察窗为 {window[0]} 至 {end}。",
        merge_sources((event, policy)),
        window=window,
        event=event.event,
        calculation=f"{days} 个连续自然日",
    )


def verify(p, evidence, parents, sources):
    if p.op == "event_window":
        if len(parents) != 2:
            raise ValueError("请依次提供实际事件时点、观察窗起算条款")
        item = checked_window(*parents)
    elif p.op == "next_workday":
        if (
            not evidence
            or evidence["kind"] != "external"
            or not evidence.get("current")
            or len(parents) not in {2, 4}
        ):
            raise ValueError("需引用当前日历，并依次关联上线原文与复核条款")
        calendar = evidence["record"]
        statement = action_statement(
            parents, calendar, calendar.get("timezone") or evidence.get("timezone")
        )
        extra = {k: evidence[k] for k in ("kind", "title", "version", "scope", "locator")}
        item = Checked(
            statement,
            (*sources, {**extra, "excerpt": calendar["text"]}),
            calculation="上线后的首个工作日，按原条款取本地复核时刻",
        )
    elif p.op == "whole_tier":
        if not evidence or evidence["kind"] != "external" or len(parents) < 3:
            raise ValueError("选择档位需要完整报价、已核上限、适用条款与资格比较")
        cap, rule, *qualifications = parents
        supports = [q for q in qualifications if q.comparison is None]
        anchor = None
        if supports:
            qualifications = [q for q in qualifications if q.comparison is not None]
            if len(supports) == 1 and supports[0].event and supports[0].window:
                anchor = supports[0]
            elif len(supports) == 2:
                event = next((q for q in supports if q.event or q.lookup_problem), None)
                policy = next((q for q in supports if q.policy_text), None)
                if event and policy:
                    anchor = checked_window(event, policy)
            if not anchor:
                raise ValueError("请提供已核起算窗口或起算原始事件与条款")
        if (
            cap.unit != "CNY_fen"
            or cap.value is None
            or cap.value < 0
            or cap.selection_balance is None
            or not cap.current
            or not rule.current
            or {s["kind"] for s in rule.sources} != {"knowledge"}
            or not all(x.condition is not None for x in qualifications)
            or not {"metric", "detail"} <= {s["kind"] for q in qualifications for s in q.sources}
        ):
            raise ValueError("缺少适用的选择规则或正式指标与明细资格")
        if not re.search(r"最高.{0,4}(?:单档|整档|档位)", rule.text):
            raise ValueError("条款未指定额度内的最高档")
        record = evidence["record"]
        if not re.search(r"完整档位|整档|不可拆分", record["text"]):
            raise ValueError("缺少完整档位约束")
        if not evidence.get("current", False) or not all(
            s.get("kind") != "external" for s in cap.sources
        ):
            raise ValueError("需要决策时点的报价和独立上限")
        if len(set(p.parents)) != len(p.parents):
            raise ValueError("每项采购资格须使用独立比较")
        try:
            qualified, note = policy_eligibility(
                cap, rule, qualifications, evidence, MEASURE, UNITS, matches_sample, anchor
            )
        except LookupNeeded as error:
            raise SearchGap(str(error)) from error
        if evidence.get("knownAt") and (
            deadline := re.search(
                r"最迟(?:于)?\s*(?:(\d{4})\s*年\s*)?(\d+)\s*月\s*(\d+)\s*日\s*(\d{1,2}):(\d{2})",
                record["text"],
            )
        ):
            now = datetime.fromisoformat(evidence["knownAt"]).astimezone(
                ZoneInfo(evidence["timezone"])
            )
            end = now.replace(
                year=int(deadline[1] or now.year),
                month=int(deadline[2]),
                day=int(deadline[3]),
                hour=int(deadline[4]),
                minute=int(deadline[5]),
                second=0,
                microsecond=0,
            )
            if now >= end:
                raise ValueError("报价回函时限已过")
        tiers = price_table(record)
        eligible = (
            [(price, name) for name, price in tiers if price <= cap.value] if qualified else []
        )
        amount, label = max(eligible) if eligible else (Decimal(0), "")
        text = note + (
            f"建议订{label}，金额 {display(amount, 'CNY_fen')}，待签批。"
            if eligible
            else "暂缓采购，保留预算。"
        )
        extra = {k: evidence[k] for k in ("kind", "title", "version", "scope", "locator")}
        item = Checked(
            text,
            (*sources, {**extra, "excerpt": record["text"]}),
            value=amount,
            unit="CNY_fen",
            selection_balance=cap.selection_balance,
            calculation=f"已核额度 {display(cap.value, 'CNY_fen')}，比较全部 {len(tiers)} 个整档。",
        )
    else:
        raise ValueError("未注册的资料包核验操作")
    return item
