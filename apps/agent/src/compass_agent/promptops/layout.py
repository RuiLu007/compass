"""Canonical asset members and the explicit read bridge for previous publications."""

import json
from importlib.resources import files

_LAYOUT = json.loads(files(__package__).joinpath("legacy_layout.json").read_text())
MEMBERS: dict[str, dict] = _LAYOUT["members"]
LEGACY_POLICY_DIGESTS = frozenset(_LAYOUT["policy_digests"])


def asset_path(path: str) -> str:
    return MEMBERS.get(path, {}).get("path", path)


def extract(path: str, body: str, *, runtime=False) -> str:
    if path not in MEMBERS:
        return body
    value = json.loads(body)
    keys = MEMBERS[path]["keys"]
    for key in keys[:-1]:
        value = value[key]
    text = value[keys[-1]]
    if not isinstance(text, str):
        raise ValueError("asset member must be text")
    if runtime and asset_path(path) == "support/routing/scenes.json" and value.get("examples"):
        text += "\n识别示例：\n" + "\n".join(value["examples"])
    return text


def replace_member(document: dict, path: str, body: str) -> None:
    keys = MEMBERS[path]["keys"]
    node = document
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = body


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
