"""Read-only scene features; classification never predicts these fields."""

import json
from functools import lru_cache
from importlib.resources import files

from compass_agent.models.errors import ModelError
from compass_agent.schemas import Intent, RoutingFeatures


@lru_cache
def catalog():
    value = json.loads(files(__package__).joinpath("scenes.json").read_text())
    scenes = {row["scene_id"]: row for row in value["scenes"]}
    if len(scenes) != len(value["scenes"]):
        raise ValueError("Duplicate routing scenes")
    return value["version"], scenes


def validate_intent(value):
    intent = Intent.model_validate(value)
    scene = catalog()[1].get(intent.scene_id)
    if scene is None or scene["category_id"] != intent.category_id:
        raise ModelError("intent_invalid", 503)
    return intent


def features_for(intent):
    row = catalog()[1][intent.scene_id]
    return RoutingFeatures(**{key: row[key] for key in RoutingFeatures.model_fields})


def categories():
    return {row["category_id"]: row["category_name"] for row in catalog()[1].values()}


def descriptions():
    return {
        key: row["category_name"] + " / " + row["name"] + "：" + row["description"]
        for key, row in catalog()[1].items()
    }
