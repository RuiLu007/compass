"""Export the course scene catalog without questions or answer keys."""

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

APP = Path(__file__).resolve().parents[1]
SOURCE = APP.parents[1] / "mai-specs/992-lesson-12-multi-model-routing/spec/task-scenarios.xlsx"
OUTPUT = APP / "src/compass_agent/routing/scenes.json"
CATEGORIES = {
    "经营与渠道": "business_channel",
    "营销与会员": "marketing_members",
    "电商与产品": "commerce_product",
    "交易与履约": "transactions_fulfillment",
    "商品与库存": "product_inventory",
    "区域与门店": "regions_stores",
    "数据与语义": "data_semantics",
    "报告与协作": "reports_collaboration",
    "平台运行": "platform_operations",
    "输入边界": "input_boundary",
}


def export(source):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(source) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = [
                "".join(x.itertext())
                for x in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)
            ]
        rows = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        scenes = []
        for row in rows.findall(".//m:sheetData/m:row", ns):
            values = []
            for cell in row.findall("m:c", ns):
                value = cell.find("m:v", ns)
                if cell.get("t") == "s":
                    values.append(shared[int(value.text)])
                elif cell.get("t") == "inlineStr":
                    values.append("".join(cell.find("m:is", ns).itertext()))
                else:
                    values.append(value.text if value is not None else "")
            if not values or not values[0].startswith("SC-"):
                continue
            scene, category, title, _, task, difficulty = values[:6]
            mode = {
                "SC-33": "clarify",
                "SC-35": "clarify",
                "SC-36": "handoff",
                "SC-37": "refuse",
                "SC-38": "handoff",
                "SC-39": "refuse",
                "SC-40": "clarify",
            }.get(scene, "answer")
            scenes.append(
                dict(
                    scene_id=scene,
                    category_id=CATEGORIES[category],
                    category_name=category,
                    name=title,
                    task_type=task,
                    difficulty=difficulty if difficulty in {"L1", "L2", "L3"} else "undetermined",
                    response_mode=mode,
                    required_capabilities=[],
                    description=values[6] + "；" + values[7],
                )
            )
    if len(scenes) != 40 or len({x["scene_id"] for x in scenes}) != 40:
        raise ValueError("Expected forty unique course scenes")
    content = json.dumps(scenes, ensure_ascii=False, sort_keys=True).encode()
    return (
        json.dumps(
            dict(version=hashlib.sha256(content).hexdigest()[:16], scenes=scenes),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = export(args.source)
    if args.check:
        if OUTPUT.read_text() != result:
            raise SystemExit("Routing dictionary drift; re-export the scene catalog")
    else:
        OUTPUT.write_text(result)
