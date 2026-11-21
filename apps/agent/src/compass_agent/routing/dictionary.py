"""Read-only scene features; classification never predicts these fields."""

import json
from functools import lru_cache
from importlib.resources import files

from compass_agent.models.errors import ModelError
from compass_agent.prompt_assets import prompt_text
from compass_agent.schemas import Intent, RoutingFeatures

CATALOG_VERSION = "2c16fee116a7536b"


@lru_cache
def catalog():
    value = json.loads(
        files("compass_agent").joinpath("prompts/support/routing/scenes.json").read_text()
    )
    scenes = {
        key: {
            field: item for field, item in row.items() if field not in {"description", "examples"}
        }
        for key, row in value["scenes"].items()
    }
    if any(key != row["scene_id"] for key, row in scenes.items()):
        raise ValueError("Routing scene ID mismatch")
    return CATALOG_VERSION, scenes


def intent_for_scene(scene_id: str) -> Intent:
    scene = catalog()[1].get(scene_id)
    if scene is None:
        raise ModelError("intent_invalid", 503)
    return Intent(category_id=scene["category_id"], scene_id=scene_id)


def features_for(intent):
    row = catalog()[1][intent.scene_id]
    return RoutingFeatures(**{key: row[key] for key in RoutingFeatures.model_fields})


def descriptions():
    document = json.loads(prompt_text("support/routing/scenes.json"))
    return {
        key: row["description"].rstrip("\n")
        + ("\n识别示例：\n" + "\n".join(row["examples"]) if row.get("examples") else "")
        for key, row in document["scenes"].items()
    }
