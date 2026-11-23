"""Print one ordinary chat message containing synthetic handoff records."""

import argparse


def make_message(case: str, batch: int) -> str:
    routes = ("门店自提", "到店配送", "邮寄到家", "直营网点")
    checks = (
        "当班同事核过交接双方的签名，记录已经归档。",
        "抽查时找到了原始签收页，流水号与交接登记一致。",
        "班组复核了当日登记时间，交接联现存放在门店档案柜。",
        "复查发现备注换过行，纸质交接联与系统登记的内容一致。",
    )
    subjects = ("配送签收", "外箱换标", "库位盘点", "包装抽验")
    rows = []
    for index in range(152):
        serial = (batch - 1) * 152 + index + 1
        rows.append(
            f"巡检 {serial:03d}｜{routes[index % 4]}｜{subjects[(index // 4) % 4]}｜"
            f"{checks[(index // 16) % 4]}"
        )
    if case == "selection":
        rows[63] = (
            "南仓调拨 W-18｜5 月 13 日，承运人已签收 72 箱，余下 14 箱待补签。"
            if batch == 1
            else "南仓调拨 W-19｜5 月 14 日，余下 14 箱的签收联已归档，本批 86 箱已全部签收。"
        )
    else:
        rows[63] = (
            "退款批次 R-06｜5 月 6 日至 12 日，共提交 18,420.75 元；提交口径含银行处理中款项。"
            if batch == 1
            else "银行回执 B-06｜5 月 6 日至 12 日，已入账 18,105.95 元。差额仅一笔，"
            "原单 R-071，314.80 元，状态为银行处理中；陈岚下个工作日向银行核回执。"
        )
    return (
        f"这是第 {batch} 批门店交接原始记录。请帮我列出这批需要跟进的事项，最多两句。\n\n"
        + "\n".join(rows)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=["selection", "compression"])
    parser.add_argument("batch", type=int, choices=[1, 2])
    args = parser.parse_args()
    print(make_message(args.case, args.batch))
